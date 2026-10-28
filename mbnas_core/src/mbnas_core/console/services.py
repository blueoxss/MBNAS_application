"""Process-wide providers used by the API routes (injectable for tests).

* ``registry()``: baseline registry entries from :func:`mbnas_core.registry.load_registry` (``settings.output_root``)
  — if that call fails, the bundled package data ``mbnas_core/registry/model_registry.example.json`` *unchanged*
  (every entry UNVERIFIED) plus a health warning. Cached for ``registry_ttl_s``.
* ``devices()``: validation devices from NVML (``provenance.env_lock.validation_devices``), cached briefly;
  ``MBNAS_CORE_DEVICE_SOURCE=runners``: the registered runners' devices instead. With the default ``local``
  source a host without a GPU (empty NVML list) uses the registered CPU runners' ``cpu`` entry.
* ``versions()``: installed distribution versions (``importlib.metadata``, no torch import in the API process)
  plus the recorded ``environment.lock.json`` summary.
* ``source()``: source-tree hash (``mbnas_contracts.util.source_hash``) of the installed ``mbnas_core``,
  ``mbnas_contracts`` and ``mbnas_model_runtime`` package directories, computed once per process (the API's
  commit id).
"""
from __future__ import annotations

import json
import logging
import platform
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from mbnas_core import registry as core_registry
from mbnas_core.config import Settings
from mbnas_model_runtime.datasets.manifest import DatasetService
from mbnas_core.protocol.validator import ValidationContext, validate
from mbnas_model_runtime.provenance import env_lock
from mbnas_contracts.util import source_hash

log = logging.getLogger(__name__)

REGISTRY_SOURCE = "mbnas_core.registry.load_registry"
EXAMPLE_REGISTRY = resources.files("mbnas_core.registry").joinpath("model_registry.example.json")
"""Bundled example registry (package data)."""
MBNAS_DISTRIBUTIONS = ("mbnas-contracts", "mbnas-model-runtime", "mbnas-core", "mbnas-runner", "mbnas-server")
SOURCE_PACKAGES = ("mbnas_core", "mbnas_contracts", "mbnas_model_runtime")
"""Installed packages whose directories form the core's source-tree hash."""


def _entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "registry" in value:
        value = value["registry"]
    if isinstance(value, dict):
        value = list(value.values())
    out = []
    for item in value or []:
        out.append(item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item))
    return out


def load_registry(settings: Settings) -> tuple[list[dict[str, Any]], str, str | None]:
    """``(entries, source, warning)`` per the module docstring."""
    try:
        return _entries(core_registry.load_registry(settings.output_root)), REGISTRY_SOURCE, None
    except Exception as exc:  # noqa: BLE001 - surfaced as a health warning, never hidden
        why = f"{REGISTRY_SOURCE} failed: {type(exc).__name__}: {exc}"
    doc = json.loads(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    return _entries(doc), str(EXAMPLE_REGISTRY), f"{why}; serving the example registry (all UNVERIFIED)"


def installed_versions() -> dict[str, Any]:
    """Distribution versions + python, without importing heavy frameworks."""
    pkgs: dict[str, str | None] = {}
    for name in dict.fromkeys((*env_lock.DISTRIBUTIONS, *MBNAS_DISTRIBUTIONS)):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    lock: dict[str, Any] = {"path": str(env_lock.DEFAULT_LOCK_PATH), "present": False}
    try:
        doc = json.loads(Path(env_lock.DEFAULT_LOCK_PATH).read_text(encoding="utf-8"))
        lock.update(present=True, generated_at=doc.get("generated_at"), torch_build=doc.get("torch_build"),
                    nvidia=doc.get("nvidia"))
    except (OSError, ValueError):
        pass
    return {"python": platform.python_version(), "packages": pkgs, "environment_lock": lock,
            "api": "mbnas-core " + (pkgs.get("mbnas-core") or "unknown")}


def source_tree() -> dict[str, Any]:
    """Source-tree hash of the installed :data:`SOURCE_PACKAGES` directories (``source()`` provider)."""
    return source_hash.compute_source_tree_hash(source_hash.package_roots(SOURCE_PACKAGES))


class _Cached:
    def __init__(self, fn: Callable[[], Any], ttl_s: float | None) -> None:
        self.fn, self.ttl_s = fn, ttl_s
        self.value: Any = None
        self.at: float | None = None
        self.lock = threading.Lock()

    def __call__(self) -> Any:
        with self.lock:
            stale = self.at is None or (self.ttl_s is not None and time.monotonic() - self.at > self.ttl_s)
            if stale:
                self.value, self.at = self.fn(), time.monotonic()
            return self.value


@dataclass
class AppServices:
    """Providers + tunables of one API process (tests replace providers with static data)."""

    settings: Settings
    registry_fn: Callable[[], tuple[list[dict[str, Any]], str, str | None]] | None = None
    devices_fn: Callable[[], list[dict[str, Any]]] | None = None
    versions_fn: Callable[[], dict[str, Any]] | None = None
    source_fn: Callable[[], dict[str, Any]] | None = None
    sse_heartbeat_s: float = 15.0
    sse_poll_s: float = 0.5
    registry_ttl_s: float = 300.0
    snapshot_event_limit: int = 200
    _cache: dict[str, _Cached] = field(default_factory=dict)

    def __post_init__(self) -> None:
        s = self.settings
        self._cache = {
            "registry": _Cached(self.registry_fn or (lambda: load_registry(s)), self.registry_ttl_s),
            "devices": _Cached(self.devices_fn or self._default_devices_fn(), 30.0),
            "versions": _Cached(self.versions_fn or installed_versions, None),
            "source": _Cached(self.source_fn or source_tree, None),
        }

    def _default_devices_fn(self) -> Callable[[], list[dict[str, Any]]]:
        """``MBNAS_CORE_DEVICE_SOURCE``: ``local`` = NVML of this host (default), ``runners`` = registered runners.

        With ``local``, a core host without a GPU has an empty NVML list, so every training validation would be
        DEVICE_UNAVAILABLE even with a CPU runner registered. When the local list is empty, the
        registered CPU runners' ``cpu`` entry (:func:`mbnas_core.protocol.validator.devices_from_runners`) is
        used instead: explicit training validates on it and an evaluation-ON search is refused with
        EVALUATION_REQUIRES_CUDA. A non-empty local list is returned unchanged, and GPU runner entries are never merged
        into ``local`` (that inventory is ``MBNAS_CORE_DEVICE_SOURCE=runners``)."""
        settings = self.settings

        def from_runners() -> list[dict[str, Any]]:
            from mbnas_core.db.core import connect
            from mbnas_core.protocol.validator import devices_from_runners

            conn = connect(settings, init=False)
            try:
                return devices_from_runners(conn)
            finally:
                conn.close()

        if getattr(settings, "device_source", "local") == "runners":
            return from_runners

        def local() -> list[dict[str, Any]]:
            devices = env_lock.validation_devices()
            if devices:
                return devices
            try:
                cpu = [d for d in from_runners() if isinstance(d, dict) and d.get("kind") == "cpu"]
            except (sqlite3.Error, OSError) as exc:  # no runner inventory readable: the empty local list stands
                log.warning("no local GPU and the registered runners could not be read (%s); validation devices: none",
                            type(exc).__name__)
                return devices
            return [*devices, *cpu]

        return local

    def registry(self) -> tuple[list[dict[str, Any]], str, str | None]:
        return self._cache["registry"]()

    def registry_lookup(self, entry_id: str) -> dict[str, Any] | None:
        return next((e for e in self.registry()[0] if e.get("id") == entry_id), None)

    def devices(self) -> list[dict[str, Any]]:
        return self._cache["devices"]()

    def versions(self) -> dict[str, Any]:
        return self._cache["versions"]()

    def source(self) -> dict[str, Any]:
        return self._cache["source"]()

    def validation_context(self, gateway: Any = None, *, local_fallback: bool = False) -> ValidationContext:
        """``gateway``: host-side inputs of a validate request (``protocol.gateway_inputs.GatewayInputs``) or None;
        ``local_fallback``: paths the gateway did not measure are checked on this host (explicit training)."""
        return ValidationContext(settings=self.settings, registry_lookup=self.registry_lookup, devices=self.devices(),
                                 dataset_service=DatasetService.from_settings(self.settings), gateway=gateway,
                                 gateway_local_fallback=local_fallback)

    def revalidate(self, resolved: dict[str, Any], gateway: Any = None, *,
                   local_fallback: bool = False) -> tuple[bool, str | None, list[dict[str, Any]]]:
        """Re-run the validator on a stored resolved protocol (fixed point when nothing changed)."""
        result = validate(resolved, self.validation_context(gateway, local_fallback=local_fallback))
        return result.valid, result.protocol_hash, result.errors
