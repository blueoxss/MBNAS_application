"""Core sweeper (WP-2 contract section 1.3, design 5.4/5.6/6.1, DD-A18 "lease reclaim is the core sweeper's"):
a background thread that runs :func:`sweep_once` every ``heartbeat_s`` and once immediately at core start.

One pass, each step in its own short transaction:

1. **reclaim** expired leases - the body of the former ``Worker.reclaim`` unchanged: ``jobs.reclaim`` -> a RUNNING
   attempt is billed ``lost`` from ``started_at`` to ``heartbeat_at`` (``LEASE_EXPIRED``) -> FAILED jobs get
   ``engine.job_finished(LEASE_EXPIRED_MAX_ATTEMPTS)``, the others ``engine.job_requeued`` + ``engine.settle_run`` ->
   ``costs.refresh_run_costs``; afterwards retention for the failed jobs' candidates (own tx, never fatal; a remote
   run's policy becomes prune requests for its runners, DD-A66);
2. ``repo.prune_workers`` (rows older than a day);
3. ``runners`` ONLINE/REGISTERED whose ``last_seen_at`` is older than the liveness window (3 x heartbeat_s + 5 s) ->
   ``LOST``;
4. ``idempotent_responses`` rows still ``PROCESSING`` after 60 s are deleted (their transaction rolled back, so the
   result can be committed again; design 7.2).

The owner worker still calls its own ``reclaim`` before leasing; both serialise on ``BEGIN IMMEDIATE`` so a lease is
never billed twice (design D4).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from mbnas_contracts.schemas.jobs import JobResult, JobStatus
from mbnas_contracts.util.time import iso_after, utcnow_iso
from mbnas_core.config import CoreSettings
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine, retention
from mbnas_core.scheduling import costs, jobs

log = logging.getLogger("mbnas_core.runner_api.sweeper")
WORKER_ROW_RETENTION_S = 24 * 3600
PROCESSING_ORPHAN_S = 60


def reclaim(conn: sqlite3.Connection, settings: CoreSettings, now: str | None = None) -> list[str]:
    """Requeue (or fail) jobs whose lease expired at ``now`` (see module docstring); returns their ids."""
    with tx(conn):
        pairs = jobs.reclaim(conn, now)
        for before, after in pairs:
            if before["status"] == "RUNNING":  # a LEASED-only attempt never started work: nothing to bill
                costs.record_attempt(conn, before, outcome="lost", started_at=before.get("started_at"),
                                     finished_at=before.get("heartbeat_at") or utcnow_iso(),
                                     reason_code="LEASE_EXPIRED")
            if after["status"] == "FAILED":
                result = JobResult(job_id=after["id"], status=JobStatus.FAILED, attempt=max(1, after["attempt"]),
                                   reason_code="LEASE_EXPIRED_MAX_ATTEMPTS", message="lease expired, no attempts left")
                engine.job_finished(conn, after, result)
            else:
                engine.job_requeued(conn, after, paused=False, reason_code="LEASE_EXPIRED")
                engine.settle_run(conn, after["run_id"])
            costs.refresh_run_costs(conn, after["run_id"])
    for _, after in pairs:
        if after["status"] == "FAILED":
            apply_retention(conn, settings, after)
    return [after["id"] for _, after in pairs]


def apply_retention(conn: sqlite3.Connection, settings: CoreSettings, job: dict[str, Any]) -> dict[str, Any] | None:
    """Retention for the job's candidate in its own transaction (remote runs: the run's own policy through the prune
    round trip, DD-A66 - ``remote_retention_policy`` is passed for compatibility and ignored; see retention)."""
    if not job.get("candidate_id"):
        return None
    try:
        with tx(conn):
            run = repo.get_run(conn, job["run_id"])
            report = retention.apply_for_candidate(conn, run, job["candidate_id"],
                                                   default_policy=settings.full_state_retention,
                                                   remote_policy=settings.remote_retention_policy)
            if report["pruned"]:
                engine.log(conn, job["run_id"], f"retention {report['policy']}: pruned "
                           f"{len(report['pruned'])} full-state payload(s) of {job['candidate_id']}",
                           candidate_id=job["candidate_id"], policy=report["policy"],
                           bytes_freed=report["bytes_freed"])
        return report
    except Exception as exc:  # noqa: BLE001 - housekeeping must never fail a finished job; recorded instead
        with tx(conn):
            engine.log(conn, job["run_id"], f"retention failed for {job['candidate_id']}: "
                       f"{type(exc).__name__}: {exc}"[:500], level="error", candidate_id=job["candidate_id"])
        return None


def mark_lost_runners(conn: sqlite3.Connection, heartbeat_s: float, now: str) -> int:
    """ONLINE/REGISTERED runners not seen within 3 x heartbeat_s + 5 s become LOST; returns how many."""
    cutoff = iso_after(now, -(3 * heartbeat_s + 5))
    with tx(conn):
        return conn.execute("UPDATE runners SET status = 'LOST', updated_at = ? WHERE status IN ('ONLINE') "
                            "AND last_seen_at IS NOT NULL AND last_seen_at < ?", (now, cutoff)).rowcount


def clear_processing_orphans(conn: sqlite3.Connection, now: str, older_than_s: float = PROCESSING_ORPHAN_S) -> int:
    """Delete ``PROCESSING`` idempotency rows older than ``older_than_s`` (their commit never finished)."""
    with tx(conn):
        return conn.execute("DELETE FROM idempotent_responses WHERE state = 'PROCESSING' AND processing_since < ?",
                            (iso_after(now, -older_than_s),)).rowcount


def sweep_once(conn: sqlite3.Connection, settings: CoreSettings, now: str | None = None) -> dict[str, Any]:
    """One sweeper pass (see module docstring); ``now`` is injectable for tests."""
    now = now or utcnow_iso()
    reclaimed = reclaim(conn, settings, now)
    with tx(conn):
        pruned = repo.prune_workers(conn, iso_after(now, -WORKER_ROW_RETENTION_S))
    lost = mark_lost_runners(conn, settings.heartbeat_s, now)
    orphans = clear_processing_orphans(conn, now)
    return {"reclaimed": reclaimed, "workers_pruned": pruned, "runners_lost": lost, "processing_cleared": orphans}


class Sweeper:
    """Background thread: :func:`sweep_once` at start, then every ``interval_s`` (default ``heartbeat_s``)."""

    def __init__(self, settings: CoreSettings, interval_s: float | None = None, on_sweep: Any = None) -> None:
        self.settings = settings
        self.interval_s = float(interval_s or settings.heartbeat_s)
        self.on_sweep = on_sweep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: dict[str, Any] | None = None

    def run_once(self) -> dict[str, Any]:
        conn = connect(self.settings, init=False)
        try:
            self.last = sweep_once(conn, self.settings)
        finally:
            conn.close()
        if self.on_sweep is not None:
            self.on_sweep(self.last)
        return self.last

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep sweeping; the next pass retries
                log.exception("sweeper pass failed")

    def start(self) -> "Sweeper":
        try:
            self.run_once()  # design 5.6: once immediately at core start
        except Exception:  # noqa: BLE001
            log.exception("initial sweeper pass failed")
        self._thread = threading.Thread(target=self._loop, name="mbnas-core-sweeper", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
