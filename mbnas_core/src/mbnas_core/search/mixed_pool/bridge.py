"""Bridge between the search space and the vendored ETRI mixed operator pool (DD-A12; WP-1 contract section 6.4).

Replaces the former bridge to an external ``ETRI_NAS`` checkout: nothing is put on ``sys.path``, no ``.git`` is read
and no environment gate (``ETRI_NAS_MIXED``) is set. The pool consists of three vendored files whose installed bytes
are the mixed-pool identity hashed into ``mbnas-mixed-space/3``:

* ``mbnas_model_runtime/operators/etri_mixed_blocks.py`` - NASPatchMerge / NASWindowTransformer / NASGlobalTransformer,
  the ultralytics ``parse_model`` extension (installed through :func:`mbnas_model_runtime.operators.ensure_registered`)
  and ``audit_model``;
* ``mbnas_core/search/mixed_pool/seed_variants.py`` - ``make_mixed_seed_variants`` / ``audit_config``;
* ``mbnas_core/search/mixed_pool/runtime_constants.py`` - the effective ``REPEAT_MODULES``.

Contract:

* ``load_etri_mixed(root=None)`` registers the operators once per process and returns :class:`EtriMixed`; ``root`` is
  accepted and ignored (callers keep their signature; there is no external root any more). Any failure raises
  :class:`EtriUnavailable` with the exact reason; a vendored module without ``__file__`` (zip/pyc-only install)
  raises :class:`SearchSpaceUnavailable` because its bytes cannot be hashed. The search space falls back to the
  native operator pool ONLY when the protocol opts in (``console.search.allow_native_fallback: true``); otherwise
  ``SearchSpace.create`` raises :class:`SearchSpaceUnavailable` (code SEARCH_SPACE_UNAVAILABLE).
* ``files`` (``provenance()['files_sha256']``) = ``{package-relative path: sha256}`` of the three files above, read
  from the installed files at load time. ``provenance()['vendor_origin']`` records the eight ``yolo_ga_runtime``
  files (and their sha256) that formed the ``mbnas-mixed-space/2`` identity; it is provenance, never hashed.
* :func:`probe_mixed_pool` is the torch-free check the validator and ``/health`` use: the pool already loaded in this
  process, else :func:`mbnas_model_runtime.operators.probe` (ultralytics/torch installed and the vendored operator
  modules present; nothing is executed). ``root`` is always ``None``.
* :func:`ensure_mixed_registered` is the old register hook name; it is
  :func:`mbnas_model_runtime.operators.ensure_registered` (registration + capabilities).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

PROBE_CHECKED = "vendored operators importable + ultralytics installed"  # == mbnas_model_runtime.operators.PROBE_CHECKED
ETRI_NAS_HEAD = "18c481517d4393f5d7818aba5b37d721e15628c5"
VENDOR_ORIGIN_FILES: dict[str, str] = {  # the eight executed yolo_ga_runtime modules of mbnas-mixed-space/2
    "yolo_ga_runtime/__init__.py": "ef0224c2bab7101623cd7a5c0919aa7ea00615e613ef67ca7cf529224314b63a",
    "yolo_ga_runtime/crossover_v22.py": "3222dfbdd816c67888ff9a49fe38cb8e054cf5bc3810620e6914c0db88e4056e",
    "yolo_ga_runtime/ga_hooks.py": "3ce290d9772c9855ae741629ce9d1f2d42f90f9e2a7be2f7d0fc1819a7b01f57",
    "yolo_ga_runtime/mixed_blocks.py": "4233dd324e755440d1bc223241fe2e0c95ca37f71e4c3aac8513778491496e70",
    "yolo_ga_runtime/mixed_hooks.py": "297511c927e1c6e192d9ebebcebce2b7977d33c00cc39aaf7dacce580cb9deb7",
    "yolo_ga_runtime/mixed_search_space.py": "adb6f41302a8ee8b526533edff9b52c916479c93ed08e63173b43241012e5da7",
    "yolo_ga_runtime/pipeline.py": "0551ae0b25b76980c1a834c36f371384ff67801da34eb8d65b07a232a1115593",
    "yolo_ga_runtime/runtime.py": "f538c50f415cf0626d70a8be5b3d25550077983451202e2244a8d7423fd045e1",
}
FUNCTIONS: tuple[str, ...] = (
    "mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants",
    "mbnas_core.search.mixed_pool.seed_variants.audit_config",
    "mbnas_model_runtime.operators.etri_mixed_blocks.register_ultralytics_blocks",
    "mbnas_model_runtime.operators.etri_mixed_blocks.audit_model",
)


class EtriUnavailable(RuntimeError):
    """The vendored mixed pool could not be imported or registered (message = exact reason)."""


class SearchSpaceUnavailable(EtriUnavailable):
    """The mixed pool is unavailable and the protocol did not opt in to the native fallback
    (``console.search.allow_native_fallback``). Error code: SEARCH_SPACE_UNAVAILABLE."""

    code = "SEARCH_SPACE_UNAVAILABLE"


@dataclass(frozen=True)
class EtriMixed:
    """Handles into the vendored mixed pool plus provenance (file hashes, registration capabilities)."""

    files: dict[str, str]
    capabilities: dict[str, Any]
    make_seed_variants: Callable[[dict[str, Any]], dict[str, dict[str, Any]]] = field(repr=False)
    audit_config: Callable[[dict[str, Any]], dict[str, Any]] = field(repr=False)
    audit_model: Callable[[Any], dict[str, Any]] = field(repr=False)
    root: str | None = None
    head: str | None = None

    def provenance(self) -> dict[str, Any]:
        """JSON-safe provenance record (no callables); ``describe()['etri']`` of the search space."""
        return {"root": self.root, "git_head": self.head, "files_sha256": dict(self.files),
                "vendor_origin": {**VENDOR_ORIGIN_FILES, "etri_nas_head": ETRI_NAS_HEAD,
                                  "space_version_before": "mbnas-mixed-space/2"},
                "registration": dict(self.capabilities), "env": {"gate": None}, "functions": list(FUNCTIONS)}


_LOADED: EtriMixed | None = None


def _vendored_file(module: ModuleType) -> tuple[str, str]:
    """``(package-relative path, sha256 of the installed bytes)`` of a vendored module."""
    file = getattr(module, "__file__", None)
    if not file or not str(file).endswith(".py"):
        raise SearchSpaceUnavailable(f"vendored mixed-pool module {module.__name__} has no source file ({file!r}); "
                                     "its content hash (mbnas-mixed-space/3) cannot be computed")
    rel = module.__name__.replace(".", "/") + ".py"
    try:
        return rel, hashlib.sha256(Path(file).read_bytes()).hexdigest()
    except OSError as exc:
        raise SearchSpaceUnavailable(f"vendored mixed-pool module {rel} is not readable "
                                     f"({type(exc).__name__}: {exc})") from exc


def load_etri_mixed(root: str | os.PathLike[str] | None = None) -> EtriMixed:
    """Register the vendored pool once per process (see module docstring); raises EtriUnavailable."""
    global _LOADED
    del root  # accepted for caller compatibility; the pool is vendored (no external root)
    if _LOADED is not None:
        return _LOADED
    try:
        from mbnas_model_runtime import operators
        from mbnas_model_runtime.operators import etri_mixed_blocks
        from mbnas_core.search.mixed_pool import runtime_constants, seed_variants

        capabilities = operators.ensure_registered()
    except Exception as exc:  # noqa: BLE001 - every import/registration failure must surface as one reason
        raise EtriUnavailable(f"{type(exc).__name__}: {exc}") from exc
    files = dict(sorted(_vendored_file(m) for m in (etri_mixed_blocks, seed_variants, runtime_constants)))
    from ultralytics.nn import tasks

    unresolved = [name for name in operators.MIXED_MODULES if not isinstance(getattr(tasks, name, None), type)]
    if unresolved or not getattr(tasks.parse_model, "_nas_mixed_installed", False):
        raise EtriUnavailable(f"registration did not expose {unresolved or 'patched parse_model'} on ultralytics.nn.tasks")
    _LOADED = EtriMixed(files=files, capabilities=dict(capabilities),
                        make_seed_variants=seed_variants.make_mixed_seed_variants,
                        audit_config=seed_variants.audit_config, audit_model=etri_mixed_blocks.audit_model)
    return _LOADED


def probe_mixed_pool(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Torch-free availability check of the vendored mixed pool (validator / ``/health``); never executes it.

    ``variant`` is ``etri_mixed`` when the pool is already loaded in this process or the operator probe
    (:func:`mbnas_model_runtime.operators.probe`) finds the vendored operators installable; else ``unavailable`` with
    the exact ``reason``. A pool that is present but fails to register later is still caught by
    ``SearchSpace.create`` (SEARCH_SPACE_UNAVAILABLE without the opt-in). ``root`` is accepted for caller compatibility
    and ignored (always reported as ``None``).
    """
    del root
    out: dict[str, Any] = {"root": None, "checked": PROBE_CHECKED}
    if _LOADED is not None:
        return {**out, "available": True, "variant": "etri_mixed", "reason": None}
    try:
        from mbnas_model_runtime import operators

        info = operators.probe()
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report the cause, never break /health or validation
        return {**out, "available": False, "variant": "unavailable",
                "reason": f"vendored operator package not importable ({type(exc).__name__}: {exc})"}
    if info.get("available"):
        return {**out, "available": True, "variant": "etri_mixed", "reason": None}
    return {**out, "available": False, "variant": "unavailable",
            "reason": info.get("reason") or "vendored mixed-pool operators unavailable"}


def ensure_mixed_registered(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Register the custom NAS modules with ultralytics' parser; returns the registration capabilities
    (= :func:`mbnas_model_runtime.operators.ensure_registered`)."""
    del root
    from mbnas_model_runtime.operators import ensure_registered

    return ensure_registered()
