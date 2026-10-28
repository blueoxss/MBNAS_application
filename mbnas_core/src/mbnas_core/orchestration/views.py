"""Contract-shaped payloads built from DB rows (shared by the API responses and the events the engine emits).

Rows keep JSON nulls; callers that send data to clients pass these through ``api.contract.prune`` so properties the
contract declares non-nullable are omitted instead of null.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from mbnas_contracts.util.canon import CANONICALIZATION_ID
from mbnas_contracts.util.time import parse_iso, utcnow

RUN_FIELDS = ("namespace", "execution_mode", "purpose", "preset", "created_at", "updated_at", "reason_code",
              "source_commit")


def run_payload(run: dict[str, Any], *, include_protocol: bool = False) -> dict[str, Any]:
    """Contract ``Run`` (``costs`` only holds measured keys; ``resolved_protocol`` on request).

    ``kind`` is SEARCH or FINAL_RETRAIN; a FINAL_RETRAIN child also carries ``parent_run_id`` and its frozen
    ``finalists`` (each with the selection rule that chose it).
    """
    out: dict[str, Any] = {"id": run["id"], "status": run["status"], "protocol_hash": run["protocol_hash"],
                           "terminal": bool(run["terminal"]), "costs": dict(run.get("costs") or {}),
                           "budget": run.get("budget") or {},
                           "protocol_canonicalization": f"{CANONICALIZATION_ID} sha256",
                           "kind": run.get("kind") or "SEARCH", "parent_run_id": run.get("parent_run_id")}
    out.update({k: run.get(k) for k in RUN_FIELDS})
    if out["kind"] == "FINAL_RETRAIN":
        out["finalists"] = list(run.get("finalists") or [])
    if run.get("cancel"):
        out["cancel"] = run["cancel"]
    if include_protocol:
        out["resolved_protocol"] = run["resolved_protocol"]
    return out


def worker_stale(row: dict[str, Any], heartbeat_s: float, now: datetime | None = None) -> bool:
    """True when a worker row has not been refreshed within the liveness window (3 heartbeats + 5 s).

    A worker that was killed (SIGKILL, power loss) never writes ``offline``; this is how readers tell it apart from
    a live one. The same window decides ``/health`` ``worker_count``.
    """
    beat = row.get("heartbeat_at")
    if not beat:
        return True
    return ((now or utcnow()) - parse_iso(beat)).total_seconds() > 3 * heartbeat_s + 5


def worker_payload(row: dict[str, Any], *, heartbeat_s: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Contract ``Worker`` from a ``workers`` row (live fields live in ``row_json``).

    With ``heartbeat_s`` (API readers) a non-offline row whose heartbeat is stale is reported as ``status: "lost"``
    instead of its last self-reported status (a dead worker cannot report ``offline`` itself).
    """
    out = dict(row.get("row") or {})
    out.update({"id": row["id"], "status": row["status"], "device": row["device"], "device_uuid": row.get("device_uuid"),
                "device_name": row.get("device_name"), "job_id": row.get("job_id"), "run_id": row.get("run_id"),
                "candidate_id": row.get("candidate_id"), "heartbeat_at": row.get("heartbeat_at")})
    if heartbeat_s is not None and row["status"] != "offline" and worker_stale(row, heartbeat_s, now):
        out["status"] = "lost"
    return out


def generation_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Contract ``Generation`` from a ``generations`` row."""
    return dict(row["row"])


def lineage_view(lineage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Stored candidate lineage -> contract ``CandidateDetail.lineage`` (contract 1.2.0 types ``template`` as a string).

    The search writer (``search.lineage.lineage_for``) stores ``template`` as ``{id, family, variant}``; final
    candidates copy it from their source. The view serves the template id as ``template`` and keeps the stored object
    unchanged under ``template_detail`` (``lineage`` allows additional properties). Stored rows are never rewritten.
    """
    if not isinstance(lineage, dict):
        return lineage
    template = lineage.get("template")
    if not isinstance(template, dict):
        return lineage
    tid = template.get("id")
    out = {k: v for k, v in lineage.items() if k != "template"}
    if isinstance(tid, str) and tid:
        out["template"] = tid
    out["template_detail"] = template
    return out
