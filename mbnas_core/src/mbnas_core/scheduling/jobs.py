"""Job creation and lease life cycle on top of ``db.repo``.

* :func:`ensure_job` queues a job once per idempotent key ``run|kind|candidate|rung|extra``; asking again
  returns the existing job unchanged (so replaying a result can never create a duplicate CUDA job).
* A job is leased by exactly one worker (``repo.acquire_lease`` is one atomic ``UPDATE ... RETURNING``);
  :func:`start_attempt` moves LEASED -> RUNNING and stamps ``started_at`` with the start of *this* attempt
  (the cost ledger bills each attempt from its own start).
* :func:`requeue` puts a job the worker still owns back to QUEUED (retry after a failure, or a pause taken at a
  safe epoch boundary; a pause does not consume a retry, so ``max_attempts`` grows by one).
* :func:`reclaim` requeues jobs whose lease expired (worker crash) and returns ``(before, after)`` pairs so the
  caller can bill the lost attempt (``heartbeat_at - started_at``) as failed cost.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from mbnas_core.db import repo
from mbnas_contracts.schemas.jobs import JobKind, JobSpec, TRAINING_KINDS
from mbnas_contracts.util.time import utcnow_iso

ACTIVE_STATUSES = ("LEASED", "RUNNING")
OPEN_STATUSES = ("QUEUED", "LEASED", "RUNNING")
GPU_KINDS: frozenset[str] = frozenset({k.value for k in TRAINING_KINDS} | {JobKind.EVALUATE.value,
                                                                           JobKind.BENCHMARK.value})
PRIORITY = {JobKind.PROPOSE_GENERATION.value: 10, JobKind.EVALUATE.value: 40, JobKind.BENCHMARK.value: 60,
            JobKind.BASELINE_TRAIN.value: 100, JobKind.CANDIDATE_TRAIN.value: 100, JobKind.FINAL_RETRAIN.value: 200}


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def gpus_for(kind: str) -> int:
    """GPUs a job of ``kind`` occupies (one candidate per GPU; proposals are CPU-only)."""
    return 1 if kind in GPU_KINDS else 0


def job_key(run_id: str, kind: str, candidate_id: str | None = None, rung: str | None = None,
            extra: str | None = None) -> str:
    """Idempotent job key (run, kind, candidate, rung[, extra])."""
    return "|".join([run_id, kind, candidate_id or "-", rung or "-", extra or "-"])


def ensure_job(conn: sqlite3.Connection, *, run_id: str, kind: str, protocol_hash: str,
               candidate_id: str | None = None, rung: str | None = None, cumulative_target: int | None = None,
               fixed_horizon: int | None = None, arm: str | None = None, search_seed: int | None = None,
               training_seed: int | None = None, params: dict[str, Any] | None = None, key_extra: str | None = None,
               max_attempts: int = 3, now: str | None = None) -> tuple[dict[str, Any], bool]:
    """Queue the job for this key unless it already exists; returns ``(job, created)``."""
    key = job_key(run_id, kind, candidate_id, rung, key_extra)
    existing = conn.execute("SELECT id FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
    if existing is not None:
        return repo.get_job(conn, existing[0]), False  # type: ignore[return-value]
    job_id = f"job-{uuid.uuid4().hex[:16]}"
    spec = JobSpec(job_id=job_id, run_id=run_id, kind=JobKind(kind), protocol_hash=protocol_hash,
                   candidate_id=candidate_id, rung=rung, cumulative_target=cumulative_target,
                   fixed_horizon=fixed_horizon, arm=arm, search_seed=search_seed, training_seed=training_seed,
                   params=params or {})
    return repo.enqueue_job(conn, id=job_id, run_id=run_id, kind=kind, spec=spec.model_dump(mode="json"),
                            idempotency_key=key, candidate_id=candidate_id, rung=rung,
                            cumulative_target=cumulative_target, fixed_horizon=fixed_horizon,
                            priority=PRIORITY.get(kind, 100), max_attempts=max_attempts, now=now)


def start_attempt(conn: sqlite3.Connection, job_id: str, worker_id: str, now: str | None = None, *,
                  attempt: int | None = None) -> bool:
    """LEASED -> RUNNING for the lease owner; ``started_at`` = start of this attempt. ``attempt`` additionally
    fences on the lease epoch; None keeps the owner-worker behaviour."""
    now = now or utcnow_iso()
    a_sql, a_arg = ("", ()) if attempt is None else (" AND attempt = ?", (int(attempt),))
    cur = conn.execute("UPDATE jobs SET status = 'RUNNING', started_at = ?, finished_at = NULL, updated_at = ? "
                       "WHERE id = ? AND lease_owner = ? AND status = 'LEASED'" + a_sql,
                       (now, now, job_id, worker_id, *a_arg))
    return cur.rowcount == 1


def requeue(conn: sqlite3.Connection, job_id: str, worker_id: str, *, error: dict[str, Any],
            pause: bool = False, now: str | None = None, attempt: int | None = None) -> bool:
    """Return an owned job to QUEUED (retry, or pause at a safe boundary when ``pause``); ``attempt`` fences on the
    lease epoch."""
    now = now or utcnow_iso()
    a_sql, a_arg = ("", ()) if attempt is None else (" AND attempt = ?", (int(attempt),))
    cur = conn.execute(
        "UPDATE jobs SET status = 'QUEUED', lease_owner = NULL, lease_expires_at = NULL, error_json = ?, "
        "max_attempts = max_attempts + ?, updated_at = ? "
        "WHERE id = ? AND lease_owner = ? AND status IN ('LEASED', 'RUNNING')" + a_sql,
        (_dump(error), 1 if pause else 0, now, job_id, worker_id, *a_arg))
    return cur.rowcount == 1


def reclaim(conn: sqlite3.Connection, now: str | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Requeue/fail expired leases (inside the caller's tx); returns ``(job_before, job_after)`` pairs."""
    now = now or utcnow_iso()
    before = {r[0]: repo.get_job(conn, r[0]) for r in conn.execute(
        "SELECT id FROM jobs WHERE status IN ('LEASED', 'RUNNING') AND lease_expires_at < ?", (now,)).fetchall()}
    after = repo.reclaim_expired_leases(conn, now)
    return [(before[j["id"]], j) for j in after if j["id"] in before]  # type: ignore[misc]


def count_jobs(conn: sqlite3.Connection, run_id: str, statuses: tuple[str, ...]) -> int:
    """Number of jobs of a run in ``statuses``."""
    marks = ", ".join("?" for _ in statuses)
    return int(conn.execute(f"SELECT COUNT(*) FROM jobs WHERE run_id = ? AND status IN ({marks})",
                            (run_id, *statuses)).fetchone()[0])


def open_jobs(conn: sqlite3.Connection, run_id: str, statuses: tuple[str, ...] = OPEN_STATUSES) -> list[dict[str, Any]]:
    """Jobs of a run in ``statuses`` (default: not finished), oldest first."""
    marks = ", ".join("?" for _ in statuses)
    ids = conn.execute(f"SELECT id FROM jobs WHERE run_id = ? AND status IN ({marks}) ORDER BY created_at, id",
                       (run_id, *statuses)).fetchall()
    return [repo.get_job(conn, r[0]) for r in ids]  # type: ignore[misc]


def cancel_job(conn: sqlite3.Connection, job_id: str, reason_code: str, now: str | None = None) -> bool:
    """Cancel one QUEUED job (e.g. its candidate became RESUME_BLOCKED or ran out of budget)."""
    now = now or utcnow_iso()
    cur = conn.execute("UPDATE jobs SET status = 'CANCELLED', error_json = ?, finished_at = ?, updated_at = ? "
                       "WHERE id = ? AND status = 'QUEUED'",
                       (_dump({"reason_code": reason_code}), now, now, job_id))
    return cur.rowcount == 1
