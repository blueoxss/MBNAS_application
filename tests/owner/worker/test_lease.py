"""Lease atomicity with two worker ids, expiry reclaim with lost-work billing, attempts exhaustion (synthetic)."""
from __future__ import annotations

import threading

import harness
import pytest

from mbnas_core.console.projection import public_ids
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.scheduling import jobs
from mbnas_contracts.util.time import iso_after, parse_iso, utcnow_iso


@pytest.fixture
def run_id(client, protocol):
    rid, _ = harness.validate_and_run(client, protocol)
    return rid


def test_two_workers_never_lease_the_same_job(api_settings, run_id):
    conn = connect(api_settings, init=False)
    with tx(conn):
        for i in range(40):
            jobs.ensure_job(conn, run_id=run_id, kind="BENCHMARK", protocol_hash="sha256:x", key_extra=f"bench-{i}")
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id = ?", (run_id,)).fetchone()[0]
    conn.close()
    leased: dict[str, list[str]] = {"w-a": [], "w-b": []}
    barrier = threading.Barrier(2)

    def worker(wid: str) -> None:
        c = connect(api_settings, init=False)
        barrier.wait()
        while (job := repo.acquire_lease(c, wid, utcnow_iso(), 60)) is not None:
            leased[wid].append(job["id"])
        c.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in leased]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    a, b = set(leased["w-a"]), set(leased["w-b"])
    assert not (a & b) and len(a | b) == total == len(leased["w-a"]) + len(leased["w-b"])
    conn = connect(api_settings, init=False)
    rows = conn.execute("SELECT lease_owner, attempt, status FROM jobs WHERE run_id = ?", (run_id,)).fetchall()
    conn.close()
    assert {r["attempt"] for r in rows} == {1} and {r["status"] for r in rows} == {"LEASED"}


def test_expired_lease_is_reclaimed_and_lost_work_billed(api_settings, run_id, inline_worker):
    conn = connect(api_settings, init=False)
    t0 = utcnow_iso()
    job = repo.acquire_lease(conn, "crashed:1", t0, 5, kinds=["BASELINE_TRAIN"])
    assert jobs.start_attempt(conn, job["id"], "crashed:1", now=t0)
    assert repo.renew_lease(conn, job["id"], "crashed:1", iso_after(t0, 3), 0.001)  # last heartbeat at t0+3s
    assert repo.acquire_lease(conn, "other:2", t0, 60, kinds=["BASELINE_TRAIN"]) is None  # still owned
    reclaimed = inline_worker.reclaim(now=iso_after(t0, 10))
    assert reclaimed == [job["id"]]
    after = repo.get_job(conn, job["id"])
    assert after["status"] == "QUEUED" and after["lease_owner"] is None and after["error"]["reason_code"] == "LEASE_EXPIRED"
    ledger = repo.list_costs(conn, run_id)
    assert len(ledger) == 1 and ledger[0]["kind"] == "failed" and ledger[0]["meta"]["outcome"] == "lost"
    assert ledger[0]["gpu_seconds"] == pytest.approx(3.0, abs=0.01) and ledger[0]["attempt"] == 1
    assert repo.get_run(conn, run_id)["costs"]["failed_gpu_seconds"] == pytest.approx(3.0, abs=0.01)
    again = repo.acquire_lease(conn, "other:2", iso_after(t0, 11), 60, kinds=["BASELINE_TRAIN"])
    assert again["id"] == job["id"] and again["attempt"] == 2
    conn.close()


def test_attempts_exhausted_fails_job_and_candidate(api_settings, run_id, inline_worker):
    conn = connect(api_settings, init=False)
    for attempt in range(1, 4):
        now = utcnow_iso()
        job = repo.acquire_lease(conn, f"crash:{attempt}", now, 0.001, kinds=["BASELINE_TRAIN"])
        assert job is not None and job["attempt"] == attempt
        jobs.start_attempt(conn, job["id"], f"crash:{attempt}", now=now)
        inline_worker.reclaim(now=iso_after(now, 1))
    final = repo.get_job(conn, job["id"])
    assert final["status"] == "FAILED" and final["error"]["reason_code"] == "LEASE_EXPIRED_MAX_ATTEMPTS"
    assert repo.get_candidate(conn, final["candidate_id"])["candidate_status"] == "TRAIN_FAILED"
    assert len([r for r in repo.list_costs(conn, run_id) if r["kind"] == "failed"]) == 3
    conn.close()


def test_leased_but_never_started_attempt_is_not_billed(api_settings, run_id, inline_worker):
    conn = connect(api_settings, init=False)
    now = utcnow_iso()
    job = repo.acquire_lease(conn, "crash-before-start:1", now, 0.001, kinds=["BASELINE_TRAIN"])
    assert inline_worker.reclaim(now=iso_after(now, 1)) == [job["id"]]
    assert repo.get_job(conn, job["id"])["status"] == "QUEUED"
    assert repo.list_costs(conn, run_id) == [] and "failed_gpu_seconds" not in repo.get_run(conn, run_id)["costs"]
    conn.close()


def test_idle_worker_heartbeat_keeps_health_live(api_settings, client):
    """Regression (integration smoke): an idle worker must refresh its row, else /health reports 0 live workers."""
    import time

    from mbnas_core.owner_worker.main import Worker

    w = Worker(api_settings, worker_id="idle-worker:1")
    try:
        assert w.idle_heartbeat(force=True)
        assert client.get("/api/v1/health", headers=harness.auth()).json()["worker_count"] == 1
        with tx(w.conn):  # age the row as if the worker had been idle for 10 minutes without refreshing
            w.conn.execute("UPDATE workers SET heartbeat_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", w.id))
        assert client.get("/api/v1/health", headers=harness.auth()).json()["worker_count"] == 0
        assert not w.idle_heartbeat()  # throttled: refreshed less than heartbeat_s ago
        w._idle_beat_at = time.monotonic() - api_settings.heartbeat_s - 1
        assert w.idle_heartbeat()
        health = client.get("/api/v1/health", headers=harness.auth()).json()
        assert health["worker_count"] == 1 and not any("heartbeat" in m for m in health["warnings"])
    finally:
        w.conn.close()


def test_lost_attempt_billed_to_last_progress_before_first_heartbeat(api_settings, run_id, inline_worker):
    """Regression (real crash smoke): a worker killed before its first lease heartbeat billed 0 s of lost work.
    Child progress is evidence of life and ends the billed window when it is later than the last heartbeat."""
    conn = connect(api_settings, init=False)
    t0 = utcnow_iso()
    job = repo.acquire_lease(conn, "crashed:1", t0, 5, kinds=["BASELINE_TRAIN"])
    assert jobs.start_attempt(conn, job["id"], "crashed:1", now=t0)
    with tx(conn):
        assert repo.touch_job(conn, job["id"], "crashed:1", iso_after(t0, 4))   # progress at t0+4s
        assert not repo.touch_job(conn, job["id"], "crashed:1", iso_after(t0, 2))  # never moves backwards
        assert not repo.touch_job(conn, job["id"], "other:2", iso_after(t0, 6))   # only the lease owner
        lease = repo.get_job(conn, job["id"])["lease_expires_at"]
    assert lease == iso_after(t0, 5)  # liveness evidence does not extend the lease
    assert inline_worker.reclaim(now=iso_after(t0, 10)) == [job["id"]]
    ledger = repo.list_costs(conn, run_id)
    assert len(ledger) == 1 and ledger[0]["kind"] == "failed" and ledger[0]["meta"]["outcome"] == "lost"
    assert ledger[0]["gpu_seconds"] == pytest.approx(4.0, abs=0.01)
    conn.close()



def test_killed_worker_is_reported_lost_not_busy(api_settings, client, run_id):
    """Regression (real crash smoke): a SIGKILLed worker never writes 'offline'; its row stayed 'busy' in snapshots.
    Readers now report a stale non-offline worker as 'lost', with the same window /health uses."""
    conn = connect(api_settings, init=False)
    with tx(conn):
        repo.upsert_worker(conn, id="ghost:1", status="busy", device="host-gpu-0", device_uuid="GPU-x", device_name="x",
                           job_id="job-x", run_id=run_id, candidate_id="c", heartbeat_at="2000-01-01T00:00:00Z",
                           row={"id": "ghost:1", "status": "busy"})
        repo.upsert_worker(conn, id="live:2", status="busy", device="host-gpu-0", device_uuid="GPU-y", device_name="y",
                           job_id="job-y", run_id=run_id, candidate_id="d", heartbeat_at=utcnow_iso(),
                           row={"id": "live:2", "status": "busy"})
    conn.close()
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=harness.auth()).json()
    status = {w["id"]: w["status"] for w in snap["workers"]}
    # contract 1.5.0 (value rule ids): a worker id is served as its opaque public id, never as the raw name:pid
    ids = public_ids()
    assert not [i for i in status if ":" in i], status
    assert status[ids.worker_id("ghost:1")] == "lost" and status[ids.worker_id("live:2")] == "busy"
    assert client.get("/api/v1/health", headers=harness.auth()).json()["worker_count"] == 1


def test_lost_lease_after_reclaim_by_other_worker_bills_the_attempt_once(api_settings, run_id, inline_worker):
    """S7-F2 / S6-F5 (two worker ids): worker A's attempt runs t0..t0+20 s; its lease expires at the last heartbeat
    (t0+3 s) and worker B reclaims it (lost work billed t0..t0+3). When A's child finishes, A finds the lease lost:
    the existing failed row is extended to the full attempt, never a second row for the same physical seconds."""
    from mbnas_contracts.schemas.jobs import JobResult, JobStatus
    from mbnas_core.owner_worker.main import _Live

    a = inline_worker
    b = harness.make_inline_worker(api_settings, "w-b:2")
    conn = connect(api_settings, init=False)
    try:
        t0 = iso_after(utcnow_iso(), -20)
        job = repo.acquire_lease(a.conn, a.id, t0, 5, kinds=["BASELINE_TRAIN"])
        assert jobs.start_attempt(a.conn, job["id"], a.id, now=t0)
        assert repo.renew_lease(a.conn, job["id"], a.id, iso_after(t0, 3), 0.001)
        assert b.reclaim(now=iso_after(t0, 10)) == [job["id"]]
        (reclaimed,) = repo.list_costs(conn, run_id)
        assert reclaimed["gpu_seconds"] == pytest.approx(3.0, abs=0.01)
        result = JobResult(job_id=job["id"], status=JobStatus.SUCCEEDED, attempt=1, exit_code=0,
                           outputs={"epochs_done": 1, "evaluation": {"timings": {"val_s": 1.0, "profile_s": 2.0}}})
        a._finalize(repo.get_job(a.conn, job["id"]) | {"attempt": 1}, result, 0, False, _Live(started_at=t0))
        rows = repo.list_costs(conn, run_id)
        mine = [r for r in rows if r["job_id"] == job["id"] and r["attempt"] == 1]
        assert len(mine) == 1 and mine[0]["id"] == reclaimed["id"] and mine[0]["kind"] == "failed"
        row = mine[0]
        interval = (parse_iso(row["finished_at"]) - parse_iso(t0)).total_seconds()
        assert interval == pytest.approx(20.0, abs=2.0) and row["started_at"] == t0
        assert row["gpu_seconds"] == pytest.approx(interval, abs=1e-6)  # = the attempt interval (1 GPU)
        assert row["meta"]["outcome"] == "lost" and row["meta"]["reason_code"] == "LEASE_LOST"
        assert row["meta"]["reclaimed_reason_code"] == "LEASE_EXPIRED"
        assert row["meta"]["reclaimed_gpu_seconds"] == pytest.approx(3.0, abs=0.01)
        costs = repo.get_run(conn, run_id)["costs"]
        assert costs["actual_gpu_seconds"] == pytest.approx(interval, abs=1e-6)
        assert costs["failed_gpu_seconds"] == pytest.approx(interval, abs=1e-6)
        # a second lost-lease finalize of the same attempt (or an earlier finish) never shrinks or duplicates it
        a._finalize(repo.get_job(a.conn, job["id"]) | {"attempt": 1}, None, 0, False,
                    _Live(started_at=t0))
        again = [r for r in repo.list_costs(conn, run_id) if r["job_id"] == job["id"] and r["attempt"] == 1]
        assert len(again) == 1 and again[0]["gpu_seconds"] >= row["gpu_seconds"]
    finally:
        conn.close()
        b.conn.close()


def test_lost_lease_without_reclaim_row_appends_one_lost_row(api_settings, run_id, inline_worker):
    """The owner's lost-lease path still bills a LEASED-only reclaim (nothing billed by the reclaimer) once."""
    from mbnas_core.owner_worker.main import _Live

    a = inline_worker
    conn = connect(api_settings, init=False)
    try:
        t0 = iso_after(utcnow_iso(), -8)
        job = repo.acquire_lease(a.conn, a.id, t0, 0.001, kinds=["BASELINE_TRAIN"])
        c = harness.make_inline_worker(api_settings, "w-c:3")
        c.reclaim(now=iso_after(t0, 1))  # LEASED only: not billed
        c.conn.close()
        assert repo.list_costs(conn, run_id) == []
        a._finalize(job | {"attempt": 1}, None, 0, False, _Live(started_at=t0))
        rows = [r for r in repo.list_costs(conn, run_id) if r["job_id"] == job["id"]]
        assert len(rows) == 1 and rows[0]["meta"]["reason_code"] == "LEASE_LOST"
        assert rows[0]["gpu_seconds"] == pytest.approx(8.0, abs=2.0)
    finally:
        conn.close()


def test_snapshot_lists_only_live_recently_lost_or_attached_workers(api_settings, client, run_id):
    """S7-F4: historical worker rows (offline, or long-dead workers of other runs) are not in a run's snapshot;
    worker startup ages out rows older than a day."""
    from mbnas_core.owner_worker.main import WORKER_ROW_RETENTION_S, Worker

    hb = api_settings.heartbeat_s
    now = utcnow_iso()
    rows = {  # id: (status, run_id, heartbeat age in seconds)
        "live:1": ("busy", run_id, 0), "idle:2": ("idle", None, 0),
        "recent-lost:3": ("busy", "run-other", 4 * hb + 6),          # stale (lost) but inside the window
        "old-lost:4": ("busy", "run-other", 30 * hb),                 # stale beyond the window, other run
        "old-offline:5": ("offline", None, 30 * hb),
        "recent-offline:6": ("offline", None, 1),                     # cleanly stopped: not alive, not lost
        "attached-dead:7": ("busy", run_id, 3600),                    # died on this run: always shown
        "ancient:8": ("offline", None, WORKER_ROW_RETENTION_S + 60),
        "ancient-busy:9": ("busy", "run-other", WORKER_ROW_RETENTION_S + 60),
    }
    conn = connect(api_settings, init=False)
    with tx(conn):
        for wid, (status, rid, age) in rows.items():
            repo.upsert_worker(conn, id=wid, status=status, device="host-gpu-0", run_id=rid,
                               heartbeat_at=iso_after(now, -age), row={"id": wid, "status": status})
    conn.close()
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=harness.auth()).json()
    status = {w["id"]: w["status"] for w in snap["workers"]}
    expected = {"live:1": "busy", "idle:2": "idle", "recent-lost:3": "lost", "attached-dead:7": "lost"}
    assert status == {public_ids().worker_id(k): v for k, v in expected.items()}  # contract 1.5.0: opaque public ids
    w = Worker(api_settings, worker_id="new:10")
    try:
        assert w.prune_worker_rows() == 2
        left = {r["id"] for r in repo.list_workers(w.conn)}
    finally:
        w.conn.close()
    assert left == set(rows) - {"ancient:8", "ancient-busy:9"}
