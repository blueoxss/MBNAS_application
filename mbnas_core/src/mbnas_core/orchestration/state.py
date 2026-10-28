"""User-requested run transitions (pause / resume / cancel) — the API side of the run state machine.

::

    QUEUED|RUNNING|RESUMING --pause--> PAUSE_REQUESTED --(worker: running jobs stopped at a safe epoch
                                       boundary with a full-state checkpoint, or finished)--> PAUSED
                                       (no running job: straight to PAUSED)
    PAUSED --resume--> RESUMING --(protocol hash integrity, re-validation of dataset manifests/registry/devices,
                       full-state checkpoints of queued trainings, arm budgets)--> RUNNING | back to PAUSED
    PAUSE_REQUESTED --resume--> RUNNING (pause withdrawn; the worker deletes its stop request)
    any non-terminal --cancel--> CANCEL_REQUESTED --(worker killed/cleaned its children)--> CANCELLED

Repeating the request a state already satisfies is an idempotent no-op (200); anything else is a 409
``INVALID_TRANSITION``. A failed resume check returns the run to PAUSED and raises ``RESUME_BLOCKED`` with the
per-check details; per-candidate problems (missing/corrupt full state, exhausted budget) only take that candidate
out (RESUME_BLOCKED / ELIGIBLE_BUDGET_EXHAUSTED) and do not block the run.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mbnas_core.db import repo
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_contracts.schemas.jobs import TRAINING_KINDS
from mbnas_core.scheduling import costs, jobs, planner
from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.orchestration import engine

PAUSABLE = ("QUEUED", "RUNNING", "RESUMING")
TRAINING = tuple(k.value for k in TRAINING_KINDS)


class TransitionError(Exception):
    """Rejected transition; the API maps it to an Error body with ``status``."""

    def __init__(self, code: str, message: str, *, status: int = 409, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.errors = code, message, status, errors or []


def _run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = repo.get_run(conn, run_id)
    if run is None:
        raise TransitionError("RUN_NOT_FOUND", f"run {run_id} not found", status=404)
    return run


def _invalid(run: dict[str, Any], action: str) -> TransitionError:
    return TransitionError("INVALID_TRANSITION", f"cannot {action} a run in status {run['status']}",
                           errors=[{"field": "status", "message": run["status"], "code": "INVALID_TRANSITION"}])


def request_pause(conn: sqlite3.Connection, run_id: str, reason: str | None = None) -> dict[str, Any]:
    """Pause at the next safe boundary (inside the caller's tx)."""
    run = _run(conn, run_id)
    if run["status"] in ("PAUSE_REQUESTED", "PAUSED"):
        return run
    if run["status"] not in PAUSABLE:
        raise _invalid(run, "pause")
    engine.log(conn, run_id, "pause requested", reason=reason)
    run = engine.set_run_status(conn, run_id, "PAUSE_REQUESTED", reason_code="PAUSE_REQUESTED_BY_USER")
    return engine.settle_run(conn, run_id)


def request_cancel(conn: sqlite3.Connection, run_id: str, reason: str | None = None) -> dict[str, Any]:
    """Cancel the run: queued jobs now, running children by their worker (inside the caller's tx)."""
    run = _run(conn, run_id)
    if run["status"] in ("CANCEL_REQUESTED", "CANCELLED"):
        return run
    if run["terminal"]:
        raise _invalid(run, "cancel")
    queued = repo.cancel_queued_jobs(conn, run_id)
    active = [j["id"] for j in jobs.open_jobs(conn, run_id, jobs.ACTIVE_STATUSES)]
    cancel = {"requested_at": utcnow_iso(), "scope": "run", "reason": reason, "state": "REQUESTED",
              "queued_jobs_cancelled": queued, "active_jobs": active,
              "kill_policy": "worker sends SIGTERM to the job child, SIGKILL after the grace period; "
                             "partial attempt time is billed as failed cost",
              "cleanup": "checkpoints and artifacts are kept for audit; nothing is deleted"}
    repo.update_run_fields(conn, run_id, cancel=cancel)
    engine.set_run_status(conn, run_id, "CANCEL_REQUESTED", reason_code="CANCELLED_BY_USER")
    return engine.settle_run(conn, run_id)


@dataclass
class ResumeReport:
    """Outcome of the read-only resume checks (computed outside the write transaction)."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    blocked_jobs: list[dict[str, Any]] = field(default_factory=list)
    exhausted_jobs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c["passed"] for c in self.checks)


def _verify_full_state(conn: sqlite3.Connection, run: dict[str, Any], job: dict[str, Any]) -> str | None:
    """Reason the queued training job cannot resume from full state, or None.

    A checkpoint without ``artifact_locations`` rows (owner worker) is checked on this host's disk as before. A
    checkpoint registered by a remote runner is checked at record level (location state, digest/size agreement with
    the checkpoints row); its bytes are hashed only when a core/server store copy exists on this host. The runner's
    handler verifies the file again before resuming (``RESUME_BLOCKED``)."""
    ck = repo.latest_checkpoint(conn, job["candidate_id"], "training_full")
    ctx = engine.RunContext(run)
    cand = repo.get_candidate(conn, job["candidate_id"])
    idx = next((i for i, s in enumerate(ctx.stages_for(cand)) if s.id == job["rung"]), 0)
    if ck is None:
        return "FULL_STATE_MISSING" if idx > 0 else None
    if ck.get("state") == "PRUNED":
        return "FULL_STATE_PRUNED"
    locations = [dict(r) for r in conn.execute(
        "SELECT * FROM artifact_locations WHERE object_kind = 'checkpoint' AND object_id = ? ORDER BY created_at, id",
        (ck["id"],))]
    if not locations:  # owner-local row (registered by the owner worker): the file is on this host
        if not os.path.isfile(ck["path"]):
            return "FULL_STATE_FILE_MISSING"
        if os.path.getsize(ck["path"]) != ck["bytes"] or sha256_file(ck["path"]) != ck["sha256"].removeprefix("sha256:"):
            return "FULL_STATE_HASH_MISMATCH"
    else:  # remote payload -> record-level check, plus the bytes when a local store copy exists
        states = {loc["state"] for loc in locations}
        if states <= {"PRUNED"}:
            return "FULL_STATE_PRUNED"
        if not states & {"PRESENT", "TRANSFERRING"}:
            return "FULL_STATE_HASH_MISMATCH" if "LOAD_FAILED" in states else "FULL_STATE_FILE_MISSING"
        want = ck["sha256"].removeprefix("sha256:")
        if any(loc["sha256"].removeprefix("sha256:") != want or int(loc["bytes"]) != int(ck["bytes"]) for loc in locations):
            return "FULL_STATE_HASH_MISMATCH"
        for loc in locations:
            uri = loc.get("uri")
            if loc["store"] in ("core_local", "server_store") and loc["state"] == "PRESENT" and uri and os.path.isfile(uri):
                if os.path.getsize(uri) != ck["bytes"] or sha256_file(uri) != want:
                    return "FULL_STATE_HASH_MISMATCH"
    expected = (ck.get("hashes") or {}).get("protocol")
    if expected and expected != run["protocol_hash"]:
        return "FULL_STATE_PROTOCOL_MISMATCH"
    return None


def resume_checks(conn: sqlite3.Connection, run: dict[str, Any],
                  revalidate: Callable[[dict[str, Any]], tuple[bool, str | None, list[dict[str, Any]]]]) -> ResumeReport:
    """Hash integrity, re-validation (``revalidate(resolved) -> (valid, hash, errors)``), full state, budget."""
    report = ResumeReport()
    stored = protocol_hash(run["resolved_protocol"])
    report.checks.append({"name": "protocol_hash_integrity", "passed": stored == run["protocol_hash"],
                          "value": stored, "expected": run["protocol_hash"]})
    valid, new_hash, errors = revalidate(run["resolved_protocol"])
    report.checks.append({"name": "revalidation", "passed": bool(valid and new_hash == run["protocol_hash"]),
                          "value": new_hash, "expected": run["protocol_hash"], "errors": errors[:20]})
    budget = run.get("budget") or {}
    for job in jobs.open_jobs(conn, run["id"], ("QUEUED",)):
        if job["kind"] not in TRAINING or not job.get("candidate_id"):
            continue
        reason = _verify_full_state(conn, run, job)
        if reason:
            report.blocked_jobs.append({"job_id": job["id"], "candidate_id": job["candidate_id"], "reason_code": reason})
            continue
        cand = repo.get_candidate(conn, job["candidate_id"])
        cap = budget.get("search_per_arm_seed_s")
        if cand["arm"] in engine.SEARCH_ARMS and cap is not None:
            if cap - costs.billed_seconds(conn, run["id"], cand["arm"], cand.get("search_seed")) <= 0:
                report.exhausted_jobs.append({"job_id": job["id"], "candidate_id": cand["id"],
                                              "reason_code": "BUDGET_EXHAUSTED"})
    report.checks.append({"name": "full_state_checkpoints", "passed": True, "blocked": report.blocked_jobs})
    report.checks.append({"name": "budget", "passed": True, "exhausted": report.exhausted_jobs})
    return report


def request_resume(conn: sqlite3.Connection, run_id: str,
                   report: ResumeReport | None) -> tuple[dict[str, Any], TransitionError | None]:
    """Apply a resume (inside the caller's tx). ``report`` is required when the run is PAUSED.

    Returns ``(run, blocked)``: a failed check is *returned* (not raised) so the caller commits the
    RESUMING -> PAUSED record before reporting 409 RESUME_BLOCKED.
    """
    run = _run(conn, run_id)
    status = run["status"]
    if status in PAUSABLE:
        return run, None
    if status == "PAUSE_REQUESTED":
        engine.log(conn, run_id, "pause request withdrawn by resume")
        return engine.set_run_status(conn, run_id, "RUNNING"), None
    if status != "PAUSED":
        raise _invalid(run, "resume")
    if report is None:
        raise TransitionError("RESUME_CHECKS_REQUIRED", "resume checks were not computed")
    engine.set_run_status(conn, run_id, "RESUMING")
    engine.log(conn, run_id, "resume checks", checks=report.checks)
    if not report.ok:
        run = engine.set_run_status(conn, run_id, "PAUSED", reason_code="RESUME_BLOCKED")
        return run, TransitionError("RESUME_BLOCKED", "resume checks failed; the run stays PAUSED",
                                    errors=[c for c in report.checks if not c["passed"]])
    for item in report.blocked_jobs:
        job = repo.get_job(conn, item["job_id"])
        engine.abandon_queued_job(conn, job, candidate_status="RESUME_BLOCKED", rung_state=planner.FAILED,
                                  reason_code=item["reason_code"])
    for item in report.exhausted_jobs:
        job = repo.get_job(conn, item["job_id"])
        engine.abandon_queued_job(conn, job, candidate_status="ELIGIBLE_BUDGET_EXHAUSTED",
                                  rung_state=planner.NOT_ADMITTED, reason_code=item["reason_code"])
    for cand in repo.list_candidates(conn, run_id):
        if cand["candidate_status"] == "PAUSED":
            engine.set_candidate(conn, cand["id"], "QUEUED", cand.get("current_rung"))
    engine.set_run_status(conn, run_id, "RUNNING")
    return engine.settle_run(conn, run_id), None
