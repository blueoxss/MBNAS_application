"""Gateway host inputs for the run-creating relays (DD-A46; WP-3 contract section 1.3, DD-A34, DD-A37).

In a split deployment the customer's data and output root exist on the gateway host only. ``POST /protocols/validate``
therefore carries what the gateway measured (:mod:`mbnas_server.api.validate_prep`: ``path_checks``, ``dataset_yaml``,
``dataset_manifests``), and the core re-validates with the same inputs when a run is created; without them the core
checks its own filesystem and answers 409 ``STALE_VALIDATION`` (evidence/WP7/7B/split_output_root_probe.txt).

DD-A46: the same inputs are attached to the relayed ``POST /runs``, ``POST /training-runs`` and ``POST
/runs/{id}/final-retrain`` bodies, and to ``POST /runs/{id}/resume`` (a PAUSED run is re-validated before it resumes)
(field names exactly those of the validate payload, :data:`BODY_FIELDS`). The core documents them as "the host inputs
the gateway measured for THIS request" (``routes_runs.check_fresh``: the freshness check must not be blind to data
changed since validation), so :func:`for_request` measures again whenever the protocol of the request is known
(``POST /runs`` and ``/training-runs`` carry it; a final retrain / resume uses the run's protocol from the core's run
view). The inputs of every measurement are cached under
``<manifest_cache_root>/gateway_inputs/<hex>.json``, keyed by the protocol hash (validate: the hash core answered);
that cache entry is attached when the protocol's paths are not available to measure (e.g. a run view without
``resolved_protocol``). The DatasetService manifest cache keeps a re-measurement cheap (an unchanged split is not
re-hashed).

Nothing is attached when ``dataset_roots`` is empty: the owner all-in-one profile, where the core checks its own paths
(the same rule as validate). The cache is an optimisation and never fails a request: unreadable, oversized or
malformed entries are ignored (and measured again), write errors are logged. At most :data:`MAX_ENTRIES` entries are
kept (oldest removed first).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from mbnas_server.api import validate_prep
from mbnas_server.config import GatewaySettings

log = logging.getLogger("mbnas_server.api.gateway_inputs")

BODY_FIELDS = ("path_checks", "dataset_yaml", "dataset_manifests")
"""The host-input fields of the validate payload (core ``protocol.gateway_inputs.BODY_FIELDS``)."""
CACHE_DIRNAME = "gateway_inputs"
CACHE_FORMAT = "mbnas.gateway-inputs-cache/1"
MAX_ENTRY_BYTES = 16 << 20
MAX_ENTRIES = 256
_HASH = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


def enabled(settings: GatewaySettings) -> bool:
    """Host inputs are measured (and attached) only when the gateway has dataset allow-roots."""
    return bool(settings.dataset_roots)


def inputs_of(body: Any) -> dict[str, Any] | None:
    """The :data:`BODY_FIELDS` present in a (prepared) request body, or None."""
    if not isinstance(body, dict):
        return None
    out = {k: body[k] for k in BODY_FIELDS if k in body}
    return out or None


def measure(settings: GatewaySettings, protocol: Any) -> dict[str, Any] | None:
    """Measure the host inputs of ``protocol`` on this host now (what validate adds), or None when there is nothing to
    measure (no dataset roots, no ``console.paths``)."""
    if not enabled(settings) or not isinstance(protocol, dict):
        return None
    return inputs_of(validate_prep.prepare(settings, {"protocol": protocol}))


def _hex(protocol_hash: Any) -> str | None:
    m = _HASH.fullmatch(protocol_hash) if isinstance(protocol_hash, str) else None
    return m.group(1) if m else None


class InputsCache:
    """``<root>/gateway_inputs/<protocol hash hex>.json`` = ``{format, protocol_hash, stored_at, inputs}``."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.dir = Path(os.fspath(root)) / CACHE_DIRNAME

    @classmethod
    def from_settings(cls, settings: GatewaySettings) -> "InputsCache":
        return cls(settings.manifest_cache_root)

    def _path(self, protocol_hash: Any) -> Path | None:
        h = _hex(protocol_hash)
        return None if h is None else self.dir / f"{h}.json"

    def get(self, protocol_hash: Any) -> dict[str, Any] | None:
        """The cached inputs of ``protocol_hash`` (None when absent or unusable)."""
        path = self._path(protocol_hash)
        if path is None:
            return None
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_ENTRY_BYTES + 1)
            if len(data) > MAX_ENTRY_BYTES:
                raise ValueError("entry too large")
            doc = json.loads(data.decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("gateway inputs cache: entry ignored (%s)", type(exc).__name__)
            return None
        if not isinstance(doc, dict) or doc.get("format") != CACHE_FORMAT or _hex(doc.get("protocol_hash")) != _hex(
                protocol_hash):
            return None
        return inputs_of(doc.get("inputs"))

    def put(self, protocol_hash: Any, inputs: dict[str, Any] | None) -> bool:
        """Store ``inputs`` for ``protocol_hash`` atomically; False when nothing was written."""
        path = self._path(protocol_hash)
        inputs = inputs_of(inputs)
        if path is None or inputs is None:
            return False
        doc = {"format": CACHE_FORMAT, "protocol_hash": f"sha256:{_hex(protocol_hash)}",
               "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "inputs": inputs}
        tmp: str | None = None
        try:
            data = json.dumps(doc, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(data) > MAX_ENTRY_BYTES:
                log.warning("gateway inputs cache: entry for %s not stored (%d bytes)", path.stem, len(data))
                return False
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            tmp = None
        except (OSError, ValueError) as exc:
            log.warning("gateway inputs cache: write failed (%s)", type(exc).__name__)
            return False
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        self._trim()
        return True

    def _trim(self) -> None:
        try:
            entries = sorted((p for p in self.dir.glob("*.json") if _HASH.fullmatch(p.stem)),
                             key=lambda p: p.stat().st_mtime)
            for p in entries[:-MAX_ENTRIES]:
                p.unlink()
        except OSError:  # concurrent trim / removal: nothing to do
            pass


def for_request(settings: GatewaySettings, cache: InputsCache, *, protocol_hash: Any = None,
                protocol: Any = None) -> dict[str, Any] | None:
    """Host inputs to attach to a run-creating relay (DD-A46): a measurement of ``protocol`` on this host now (cached
    under ``protocol_hash``), else the cache entry of ``protocol_hash`` (validate time or an earlier request), else
    None."""
    if not enabled(settings):
        return None
    measured = measure(settings, protocol)
    if measured is not None:
        if protocol_hash is not None:
            cache.put(protocol_hash, measured)
        return measured
    return cache.get(protocol_hash) if protocol_hash is not None else None


def attach(body: dict[str, Any], inputs: dict[str, Any] | None) -> dict[str, Any]:
    """``body`` with ``inputs`` added; host inputs a caller put in the body are replaced (only this gateway measures
    its host)."""
    out = {k: v for k, v in body.items() if k not in BODY_FIELDS}
    if inputs:
        out.update(inputs)
    return out
