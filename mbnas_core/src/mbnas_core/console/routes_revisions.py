"""Configuration revisions of a candidate (F5d; contract 1.5.0 ``listConfigRevisions`` / ``getConfigRevisionYaml``).

* ``GET /runs/{run_id}/candidates/{candidate_id}/config-revisions`` - ``ModelConfigRevisionList`` (oldest first) through
  the public projection; 404 RUN_NOT_FOUND / CANDIDATE_NOT_FOUND.
* ``GET /runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml`` - the stored bytes unchanged
  (``application/x-yaml``, ``X-Artifact-Sha256`` = hex sha256 of the body = the revision ``source_sha256``); 404
  NOT_FOUND (no such revision of this candidate) or ARTIFACT_NOT_READY (core holds no bytes of this revision: the gateway
  serves an upload revision from its store by ``artifact_id``); 409 LOAD_FAILED when the stored bytes no longer match
  ``source_sha256`` (never served).
"""
from __future__ import annotations

import hashlib
import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from mbnas_contracts.errors import ApiError
from mbnas_contracts.schemas.api import ModelConfigRevisionList
from mbnas_core.console.deps import contract_json, db, get_run_or_404
from mbnas_core.db import repo
from mbnas_core.orchestration import config_revisions

router = APIRouter()


def _candidate_or_404(conn: sqlite3.Connection, run_id: str, candidate_id: str) -> dict:
    get_run_or_404(conn, run_id)
    cand = repo.get_candidate(conn, candidate_id)
    if cand is None or cand["run_id"] != run_id:
        raise ApiError(404, "CANDIDATE_NOT_FOUND", f"candidate {candidate_id} not found in run {run_id}")
    return cand


@router.get("/runs/{run_id}/candidates/{candidate_id}/config-revisions")
def list_config_revisions(run_id: str, candidate_id: str, conn: sqlite3.Connection = Depends(db)):
    _candidate_or_404(conn, run_id, candidate_id)
    rows = config_revisions.list_revisions(conn, run_id, candidate_id)
    return contract_json("ModelConfigRevisionList", ModelConfigRevisionList,
                         {"run_id": run_id, "candidate_id": candidate_id,
                          "revisions": [config_revisions.public_view(r) for r in rows]}, run_id=run_id)


@router.get("/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml")
def config_revision_yaml(run_id: str, candidate_id: str, revision_id: str, conn: sqlite3.Connection = Depends(db)):
    _candidate_or_404(conn, run_id, candidate_id)
    row = config_revisions.get_revision(conn, run_id, candidate_id, revision_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"configuration revision {revision_id} not found for candidate {candidate_id}")
    text = row.get("text")
    if not isinstance(text, str):
        raise ApiError(404, "ARTIFACT_NOT_READY",
                       "core holds no bytes of this revision (an uploaded original is served from the upload store)")
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    if f"sha256:{digest}" != row["source_sha256"]:
        raise ApiError(409, "LOAD_FAILED", "stored configuration bytes no longer match the revision source_sha256")
    return Response(data, media_type="application/x-yaml", headers={
        "X-Artifact-Sha256": digest, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="{candidate_id}_{revision_id}.yaml"'})
