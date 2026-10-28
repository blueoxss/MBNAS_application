"""Response decoration that depends on the request (artifact URLs) or on mutable state (candidate_status).

Stored evaluation rows are immutable; the served view overlays the *current* mutable ``candidate_status`` and
renders each artifact from the registry with a fresh signed URL (``artifacts.registry.artifact_view``).

WP-5B locations (:mod:`mbnas_core.artifacts.locations`): a runner-registered artifact is shown with its effective
state (NOT_READY - so no signed URL - until the gateway verified its bytes in the store; LOAD_FAILED / MISSING when the
gateway reported that), and a runner-registered checkpoint with the state of its recorded locations instead of a
core-local file check (:func:`artifact_overlay`, :func:`checkpoint_state`).
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from mbnas_core.artifacts import locations
from mbnas_core.artifacts.registry import artifact_view
from mbnas_core.db import repo
from mbnas_core.console.services import AppServices


def signing_key(svc: AppServices) -> bytes | None:
    key = svc.settings.artifact_signing_key
    return None if key is None else key.get_secret_value().encode("utf-8")


def checkpoint_state(path: str | None, pruned_paths: set[str], located: dict[str, str] | None = None) -> str | None:
    """S10-F4: PRESENT (file exists), PRUNED (gone; the run's checkpoints table records the retention deletion),
    MISSING (gone without a record) or None (no checkpoint_path). A view overlay; stored rows are not rewritten.
    ``located``: ``{checkpoint path: state}`` of the runner-registered checkpoints (recorded locations win over a
    core-local file check, which cannot see a runner's disk)."""
    if not path:
        return None
    if located and path in located:
        return located[path]
    if os.path.isfile(path):
        return "PRESENT"
    return "PRUNED" if path in pruned_paths else "MISSING"


def artifact_overlay(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    """``{artifact id: effective state}`` of the run's runner-registered artifacts whose public state differs from the
    registry state (:func:`mbnas_core.artifacts.locations.effective_states`)."""
    return locations.effective_states(conn, run_id)


def render_artifact(row: dict[str, Any], overlay: dict[str, str], *, base_url: str, key: bytes | None,
                    ttl_s: int) -> dict[str, Any]:
    """``Artifact`` view of a registry row with its effective state (only a READY object gets a signed URL)."""
    if row.get("id") in overlay:
        row = {**row, "state": overlay[row["id"]]}
    return artifact_view(row, base_url=base_url, key=key, ttl_s=ttl_s)


def evaluation_views(conn: sqlite3.Connection, run_id: str, records: list[dict[str, Any]], *, base_url: str,
                     svc: AppServices) -> list[dict[str, Any]]:
    """Contract CandidateEvaluation views of evaluation records (oldest first)."""
    cands = {c["id"]: c for c in repo.list_candidates(conn, run_id)}
    by_eval: dict[str, list[dict[str, Any]]] = {}
    for art in repo.list_artifacts(conn, run_id):
        if art.get("evaluation_id"):
            by_eval.setdefault(art["evaluation_id"], []).append(art)
    key, ttl = signing_key(svc), svc.settings.signed_url_ttl_s
    checkpoints = repo.list_checkpoints(conn, run_id)
    pruned = {c["path"] for c in checkpoints if c.get("state") == "PRUNED"}
    by_id = locations.checkpoint_states(conn, run_id)
    located = {c["path"]: by_id[c["id"]] for c in checkpoints if c["id"] in by_id}
    overlay = artifact_overlay(conn, run_id)
    out = []
    for rec in records:
        row = dict(rec["row"])
        row.update(status=rec["status"], revision=rec["revision"], immutable=bool(rec["immutable"]))
        cand = cands.get(row["candidate_id"])
        if cand is not None:
            row["candidate_status"] = cand["candidate_status"]
        row["checkpoint_state"] = checkpoint_state(row.get("checkpoint_path"), pruned, located)
        arts = by_eval.get(row["id"])
        if arts:
            row["artifacts"] = [render_artifact(a, overlay, base_url=base_url, key=key, ttl_s=ttl) for a in arts]
        out.append(row)
    return out
