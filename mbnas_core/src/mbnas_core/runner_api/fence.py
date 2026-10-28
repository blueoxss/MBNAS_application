"""Lease fencing of job-scoped runner writes (R1, design P3): ``(job_id, runner_id, attempt)``.

:func:`fenced_job` reads the job row and answers 409 ``LEASE_LOST`` unless ``lease_owner == runner_id``, ``attempt``
equals the request's attempt and the status is one of ``statuses`` (default LEASED/RUNNING). The SQL writes that follow
(``repo.renew_lease/touch_job/finish_job``, ``jobs.start_attempt/requeue``) carry the same ``attempt`` condition, so a
stale attempt can never write even between this read and the write.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from mbnas_contracts.errors import ApiError
from mbnas_core.db import repo
from mbnas_core.scheduling import jobs

LEASE_LOST = "LEASE_LOST"


def owns(job: dict[str, Any] | None, runner_id: str, attempt: int,
         statuses: tuple[str, ...] = jobs.ACTIVE_STATUSES) -> bool:
    """True when ``runner_id`` holds ``job`` at ``attempt`` in one of ``statuses``."""
    return (job is not None and job.get("lease_owner") == runner_id and int(job.get("attempt") or 0) == int(attempt)
            and job.get("status") in statuses)


def lease_lost(job_id: str, attempt: int) -> ApiError:
    return ApiError(409, LEASE_LOST, f"lease of {job_id} attempt {attempt} is not held by this runner")


def fenced_job(conn: sqlite3.Connection, job_id: str, runner_id: str, attempt: int,
               statuses: tuple[str, ...] = jobs.ACTIVE_STATUSES) -> dict[str, Any]:
    """The job row when fenced (see module docstring); 404 ``JOB_NOT_FOUND`` / 409 ``LEASE_LOST`` otherwise."""
    job = repo.get_job(conn, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", f"job {job_id} not found")
    if not owns(job, runner_id, attempt, statuses):
        raise lease_lost(job_id, attempt)
    return job
