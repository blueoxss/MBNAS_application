"""Owner audit routes (``/core/v1/owner/*``, principal scope ``admin``; WP-4 section 2, DD-A3, DD-A19).

The console mount (``/core/v1/console/*``) answers only the public projection, whatever the principal kind. The raw,
owner-only payloads live here and nowhere else:

* ``GET /core/v1/owner/runs/{run_id}/candidates/{candidate_id}/audit`` - the stored candidate row as recorded: ``audit``
  (config estimate rules, ETRI audit model output, admission estimator internals, proposal job link, RNG seed
  derivations, file paths), ``lineage`` (crossover cut/mask, mutation / repair events, prior draw, tournament
  selection, parent rows, elitism flag, notes, space / prior hashes), ``blocks`` (old_name / before / after /
  repair_reason / slot_kind), identity and hashes, the raw evaluation rows (incl. superseded history), the checkpoint
  rows with their paths and the configuration revisions (metadata; the bytes via the console ``.../yaml`` route);
  404 RUN_NOT_FOUND / CANDIDATE_NOT_FOUND.
* ``GET /core/v1/owner/runs/{run_id}/export`` - the raw (``owner`` profile) export bundle
  (:func:`mbnas_core.export.bundle.bundle_files`), ``X-Bundle-Sha256``; 404 RUN_NOT_FOUND.
* ``GET /core/v1/owner/registry`` - the raw registry entries (evidence paths, probe file, build traces), the raw
  ``source`` / ``warning`` and the DD-26 diagnostic ``registry_roots_skipped`` (``[{path, reason}]`` of the registry
  roots this process cannot read), which contract 1.5.0 removed from the public ``RegistryList``.

The WP-2 owner reads ``/core/v1/owner/{runners,principals}`` stay in :mod:`mbnas_core.runner_api.routes`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from mbnas_contracts.errors import ApiError
from mbnas_contracts.util.canon import sha256_hex
from mbnas_core.console.deps import db, get_run_or_404, services
from mbnas_core.console.services import AppServices
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.export.bundle import build_zip, bundle_files
from mbnas_core.orchestration import config_revisions
from mbnas_core.runner_api.auth import require_principal

router = APIRouter(dependencies=[Depends(require_principal("admin"))])
CANDIDATE_KEYS = ("id", "run_id", "name", "arm", "family", "generation", "search_seed", "training_seed",
                  "architecture_hash", "architecture_path", "architecture_yaml", "parents", "parent_evaluation_ids",
                  "lineage", "blocks", "candidate_status", "current_rung", "audit", "created_at", "updated_at")


def _recorded_lock() -> dict[str, Any] | None:
    from mbnas_model_runtime.provenance import env_lock

    try:
        return json.loads(Path(env_lock.DEFAULT_LOCK_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/runs/{run_id}/candidates/{candidate_id}/audit")
def candidate_audit(run_id: str, candidate_id: str, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    with tx(conn, immediate=False):
        get_run_or_404(conn, run_id)
        cand = repo.get_candidate(conn, candidate_id)
        if cand is None or cand["run_id"] != run_id:
            raise ApiError(404, "CANDIDATE_NOT_FOUND", f"candidate {candidate_id} not found in run {run_id}")
        evaluations = [{**e["row"], "status": e["status"], "revision": e["revision"], "immutable": bool(e["immutable"]),
                        "created_at": e["created_at"]}
                       for e in repo.list_evaluations(conn, run_id, candidate_id=candidate_id, include_superseded=True)]
        checkpoints = repo.list_checkpoints(conn, run_id, candidate_id=candidate_id)
        revisions = [{k: v for k, v in r.items() if k != "text"} | {"has_bytes": isinstance(r.get("text"), str)}
                     for r in config_revisions.list_revisions(conn, run_id, candidate_id)]
    body = {k: cand.get(k) for k in CANDIDATE_KEYS}
    body.update(candidate_id=cand["id"], evaluations=evaluations, checkpoints=checkpoints, config_revisions=revisions)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/registry")
def owner_registry(svc: AppServices = Depends(services)) -> JSONResponse:
    from mbnas_core.registry import roots_skipped

    entries, source, warning = svc.registry()
    try:
        skipped = roots_skipped(svc.settings.output_root)
    except Exception as exc:  # noqa: BLE001 - a diagnostic never breaks the route (DD-26 convention)
        skipped = [{"path": svc.settings.output_root, "reason": f"{type(exc).__name__}: {exc}"}]
    return JSONResponse({"registry": entries, "source": source, "warning": warning, "registry_roots_skipped": skipped},
                        headers={"Cache-Control": "no-store"})


@router.get("/runs/{run_id}/export")
def owner_export(run_id: str, conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services)) -> Response:
    with tx(conn, immediate=False):
        get_run_or_404(conn, run_id)
        src = svc.source()
        files = bundle_files(conn, run_id, manifest_dir=f"{svc.settings.output_root}/manifests",
                             environment={"api_process": svc.versions(), "recorded_environment_lock": _recorded_lock()},
                             provenance={"source_tree": {k: src.get(k) for k in ("algorithm", "sha256", "files")},
                                         "bundle_format": "mbnas.export/1", "profile": "owner"},
                             profile="owner")
    data = build_zip(files)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{run_id}-owner-export.zip"', "X-Bundle-Sha256": sha256_hex(data),
        "Cache-Control": "no-store"})
