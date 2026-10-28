"""``GET /health``, ``GET /protocols/presets``, ``GET /registry`` (bodies projected onto the public contract 1.5.0)."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from mbnas_core.db import repo
from mbnas_core.orchestration.views import worker_stale
from mbnas_core.protocol.presets import preset_summaries, presets
from mbnas_contracts.schemas.api import Health, PresetList, RegistryList
from mbnas_contracts.util.time import utcnow, utcnow_iso
from mbnas_core.console.deps import contract_json, db, services
from mbnas_core.console.services import AppServices

router = APIRouter()


def _alive_workers(conn: sqlite3.Connection, heartbeat_s: int) -> list[dict[str, Any]]:
    now = utcnow()
    return [w for w in repo.list_workers(conn) if w["status"] != "offline" and not worker_stale(w, heartbeat_s, now)]


def _capabilities(entries: list[dict[str, Any]], svc: AppServices, source: str) -> dict[str, Any]:
    def per(key: str) -> dict[str, Any]:
        return {e["id"]: (e.get("capabilities") or {}).get(key, "UNVERIFIED") for e in entries}

    from mbnas_model_runtime.benchmark.capability import device_targets
    from mbnas_core.search.mixed_pool.bridge import PROBE_CHECKED, probe_mixed_pool

    probe = probe_mixed_pool()
    # vendored pool (DD-A12): no external root; ``checked`` names what the torch-free probe verified
    search_space = {"variant": "etri_mixed" if probe["available"] else "unavailable", "root": None,
                    "reason": probe["reason"], "checked": PROBE_CHECKED,
                    "fallback_policy": "opt-in: console.search.allow_native_fallback=true resolves to native_fallback; "
                                       "otherwise SEARCH_SPACE_UNAVAILABLE"}
    return {"full_state_resume": per("full_state_resume"), "export_onnx": per("export_onnx"),
            "export_trt": per("export_trt"), "devices": per("devices"), "adapter_class": per("adapter_class"),
            "device_targets": device_targets(svc.devices()), "search_space": search_space,
            "sse": True, "idempotency": True,
            "signed_artifact_urls": svc.settings.artifact_signing_key is not None,
            "execution_modes": ["PILOT", "REGISTERED_SEARCH"], "registry_source": source}


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services)):
    entries, source, warning = svc.registry()
    workers = _alive_workers(conn, svc.settings.heartbeat_s)
    warnings = [w for w in [warning, None if workers else "no live worker heartbeat (jobs will stay QUEUED)"] if w]
    by_uuid = {w.get("device_uuid"): w for w in workers}
    devices = [{**d, "worker_id": (by_uuid.get(d.get("uuid")) or {}).get("id")} for d in svc.devices()]
    src = svc.source()
    body = {"status": "ok" if not warnings else "degraded", "warnings": warnings,
            "capabilities": _capabilities(entries, svc, source), "versions": svc.versions(), "devices": devices,
            "registry": entries, "commit_sha": src.get("sha256") or "unknown",
            "source_tree": {k: src.get(k) for k in ("algorithm", "sha256", "files")}, "server_time": utcnow_iso(),
            "allowlists": {"dataset_roots": svc.settings.dataset_roots, "output_roots": svc.settings.output_roots},
            "presets": preset_summaries(), "worker_count": len(workers)}
    return contract_json("Health", Health, body)


@router.get("/protocols/presets")
def list_presets(svc: AppServices = Depends(services)):
    # example console.paths come from the core settings (preset_data_root / preset_output_root) or placeholders
    return contract_json("PresetList", PresetList, {"presets": presets(svc.settings)})


@router.get("/registry")
def registry(svc: AppServices = Depends(services)):
    """Contract 1.5.0 ``RegistryList`` (source as a live|example label; the DD-26 unreadable-root diagnostic is owner-only:
    ``GET /core/v1/owner/registry``)."""
    entries, source, warning = svc.registry()
    return contract_json("RegistryList", RegistryList, {"registry": entries, "source": source, "warning": warning})
