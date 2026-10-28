"""Settlement of one finished attempt (R5): the body of the former ``Worker._settle_job`` (owner_worker/main.py),
extracted so the owner worker and the remote result endpoint (E8) run the same code inside their own transaction.

``settle_job(conn, job, result, exit_code, cancelled, stopping, started, finished, runner_id, device_uuid)``:

* ``cancelled`` -> the result is replaced by ``CANCELLED/RUN_CANCELLED`` (with ``keep_discarded`` the replaced result
  is kept as ``error.discarded_result``, DD-A18 option A for remote results);
* no result -> ``WORKER_SHUTDOWN`` (``stopping``) or ``CHILD_EXIT_NO_RESULT``, both retryable;
* SUCCEEDED -> ``finish_job`` + ``succeeded`` ledger row (inline timings) + ``engine.job_finished``;
* BLOCKED -> failed ledger row + ``finish_job(FAILED)`` + ``job_finished`` (final, never retried);
* CANCELLED with a pause reason -> ``requeue(pause=True)`` + ``paused`` row + ``job_requeued(paused=True)`` + settle;
* retryable FAILED with attempts left (or ``stopping``) -> ``requeue(pause=stopping)`` + ``job_requeued`` + settle;
* anything else -> ``finish_job(status)`` + ``job_finished``.

``runner_id`` replaces the worker's own id (lease owner), ``device_uuid`` its device, ``stopping`` its shutdown flag.
``attempt`` (keyword) fences the job writes on the lease epoch (R1); None keeps the owner-worker behaviour.
Must run inside the caller's transaction. Returns the final JobResult that was recorded.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from mbnas_contracts.jobs_protocol import PAUSE_REASONS
from mbnas_contracts.schemas.jobs import JobResult, JobStatus
from mbnas_core.db import repo
from mbnas_core.orchestration import engine
from mbnas_core.scheduling import costs, jobs

RETRYABLE_REASONS = frozenset({"CHILD_EXCEPTION", "CHILD_EXIT_NO_RESULT", "LEASE_EXPIRED", "WORKER_SHUTDOWN"})
# PAUSE_REASONS (contract) requeue the job without consuming a retry; everything else is final unless retryable.


def inline_timings(outputs: dict[str, Any] | None) -> dict[str, Any] | None:
    """``evaluation.timings`` of a succeeded training/EVALUATE result (inline latency profiling is billed apart,
    see ``scheduling.costs``); falls back to the top-level ``timings`` of the training handler."""
    outputs = outputs or {}
    evaluation = outputs.get("evaluation")
    timings = evaluation.get("timings") if isinstance(evaluation, dict) else None
    if not isinstance(timings, dict):
        timings = outputs.get("timings") if isinstance(evaluation, dict) else None
    return timings if isinstance(timings, dict) else None


def settle_job(conn: sqlite3.Connection, job: dict[str, Any], result: JobResult | None, exit_code: int | None,
               cancelled: bool, stopping: bool, started: str | None, finished: str, runner_id: str,
               device_uuid: str | None, *, attempt: int | None = None, keep_discarded: bool = False) -> JobResult:
    """See module docstring."""
    dev = device_uuid
    discarded: JobResult | None = None
    if cancelled:
        discarded = result
        result = JobResult(job_id=job["id"], status=JobStatus.CANCELLED, attempt=max(1, job["attempt"]),
                           reason_code="RUN_CANCELLED", exit_code=exit_code)
    elif result is None:
        reason = "WORKER_SHUTDOWN" if stopping else "CHILD_EXIT_NO_RESULT"
        result = JobResult(job_id=job["id"], status=JobStatus.FAILED, attempt=max(1, job["attempt"]),
                           reason_code=reason, exit_code=exit_code,
                           message=f"child exited with code {exit_code} without a result",
                           outputs={"retryable": True})
    result = result.model_copy(update={"exit_code": exit_code, "finished_at": finished})
    body = result.model_dump(mode="json")
    if keep_discarded and discarded is not None:
        body["discarded_result"] = discarded.model_dump(mode="json")
    if result.status == JobStatus.SUCCEEDED:
        repo.finish_job(conn, job["id"], runner_id, "SUCCEEDED", result=body, now=finished, attempt=attempt)
        costs.record_attempt(conn, job, outcome="succeeded", started_at=started, finished_at=finished, device_uuid=dev,
                             inline_timings=inline_timings(result.outputs))
        engine.job_finished(conn, repo.get_job(conn, job["id"]), result)
        return result
    if result.status == JobStatus.BLOCKED:  # final: the target cannot run this job (never retried, no proxy)
        costs.record_attempt(conn, job, outcome="failed", started_at=started, finished_at=finished,
                             device_uuid=dev, reason_code=result.reason_code)
        repo.finish_job(conn, job["id"], runner_id, "FAILED", error=body, now=finished, attempt=attempt)
        engine.job_finished(conn, repo.get_job(conn, job["id"]), result)
        return result
    if result.status == JobStatus.CANCELLED and result.reason_code in PAUSE_REASONS:
        jobs.requeue(conn, job["id"], runner_id, error=body, pause=True, now=finished, attempt=attempt)
        costs.record_attempt(conn, job, outcome="paused", started_at=started, finished_at=finished, device_uuid=dev)
        engine.job_requeued(conn, repo.get_job(conn, job["id"]), paused=True, reason_code=result.reason_code or "")
        engine.settle_run(conn, job["run_id"])
        return result
    retryable = bool(result.outputs.get("retryable")) or result.reason_code in RETRYABLE_REASONS
    outcome = "cancelled" if result.status == JobStatus.CANCELLED else "failed"
    costs.record_attempt(conn, job, outcome=outcome, started_at=started, finished_at=finished, device_uuid=dev,
                         reason_code=result.reason_code)
    if result.status == JobStatus.FAILED and retryable and (stopping or job["attempt"] < job["max_attempts"]):
        jobs.requeue(conn, job["id"], runner_id, error=body, pause=stopping, now=finished, attempt=attempt)
        engine.job_requeued(conn, repo.get_job(conn, job["id"]), paused=False, reason_code=result.reason_code or "")
        engine.settle_run(conn, job["run_id"])
        return result
    repo.finish_job(conn, job["id"], runner_id, result.status.value, error=body, now=finished, attempt=attempt)
    engine.job_finished(conn, repo.get_job(conn, job["id"]), result)
    return result
