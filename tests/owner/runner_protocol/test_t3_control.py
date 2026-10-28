"""T3-06..09, T3-15, T3-28 (design A3 section 14): heartbeat control flags, pause/resume and cancel through the real
runner daemon (job child = real ``mbnas_runner.child`` subprocess with the SYNTHETIC stub handlers), the cancel race,
heartbeat after reclaim, runner shutdown. Ports of tests/owner/worker/test_subprocess.py:141/172 keep their
assertions; only the executor changed (runner daemon over HTTP instead of the owner worker on the DB)."""
from __future__ import annotations

import json
import os
import threading
import time

import harness
import pytest
from harness import auth

from mbnas_contracts.schemas.jobs import JobStatus
from mbnas_core.db import repo

pytestmark = [pytest.mark.runner_protocol, pytest.mark.slow]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as fh:
        return fh.read().split(")")[-1].split()[0] != "Z"


def _run_in_thread(daemon, **kw) -> threading.Thread:
    t = threading.Thread(target=daemon.run, kwargs=kw, daemon=True)
    t.start()
    return t


def test_heartbeat_returns_pause_then_null_after_withdrawal(core, runner_sim, daemon_factory, base_proto, db):
    """T3-06 / F13: PAUSE_REQUESTED -> control.action "pause" (stop file written by the runner's apply_control);
    resume while PAUSE_REQUESTED -> action null -> the runner deletes the stop file."""
    from mbnas_runner.daemon import _Live

    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    runner_sim.start(lease)
    d = daemon_factory()  # only its apply_control is used: the runner's materialisation of the flags
    live, stop = _Live(), runner_sim.stop_path(lease)
    hb = runner_sim.heartbeat(lease)
    assert hb.status_code == 200 and hb.json()["control"] == {"action": None, "run_status": "RUNNING",
                                                              "stop_requested": False, "prune": None}
    assert core.post(f"/api/v1/runs/{run_id}/pause", headers=auth(), json={"reason": "t3"}).json()["status"] == \
        "PAUSE_REQUESTED"
    ctl = runner_sim.heartbeat(lease).json()["control"]
    assert ctl["action"] == "pause" and ctl["run_status"] == "PAUSE_REQUESTED" and ctl["stop_requested"] is True
    d.apply_control(ctl, live, stop)
    assert json.loads(stop.read_text())["action"] == "pause"
    assert core.post(f"/api/v1/runs/{run_id}/resume", headers=auth()).status_code == 200
    ctl = runner_sim.heartbeat(lease).json()["control"]
    assert ctl["action"] is None and ctl["run_status"] == "RUNNING"
    d.apply_control(ctl, live, stop)
    assert not stop.exists()
    assert repo.get_job(db, lease.job_id)["status"] == "RUNNING"


def test_pause_at_epoch_boundary_then_resume_over_http(core, daemon_factory, base_proto, db, monkeypatch):
    """T3-07 (test_subprocess.py:141): pause -> the child stops at an epoch boundary -> job QUEUED with
    PAUSED_AT_SAFE_BOUNDARY, max_attempts 4, candidate and run PAUSED -> resume -> attempt 2 SUCCEEDED, epochs 1..6
    exactly once, resumed from the paused epoch, ledger [train/paused, train/succeeded]."""
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    run_id, _ = harness.validate_and_run(core, base_proto(targets=(6,)))
    d = daemon_factory()
    t = _run_in_thread(d, max_jobs=2)
    try:
        harness.wait_for(lambda: repo.list_checkpoints(db, run_id), timeout=60)
        r = core.post(f"/api/v1/runs/{run_id}/pause", headers=auth(), json={"reason": "test"})
        assert r.json()["status"] == "PAUSE_REQUESTED"
        harness.wait_for(lambda: repo.get_run(db, run_id)["status"] == "PAUSED", timeout=60)
        job = repo.list_jobs(db, run_id)[0]
        assert job["status"] == "QUEUED" and job["error"]["reason_code"] == "PAUSED_AT_SAFE_BOUNDARY"
        paused_at = max(c["epoch"] for c in repo.list_checkpoints(db, run_id))
        assert 1 <= paused_at < 6
        assert repo.get_candidate(db, job["candidate_id"])["candidate_status"] == "PAUSED"
        r = core.post(f"/api/v1/runs/{run_id}/resume", headers=auth())
        assert r.status_code == 200, r.text
        harness.wait_for(lambda: repo.get_run(db, run_id)["status"] == "COMPLETED", timeout=90)
    finally:
        d.stopping = True
        t.join(timeout=60)
    assert not t.is_alive()
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "SUCCEEDED" and job["attempt"] == 2 and job["max_attempts"] == 4
    epochs = [c["epoch"] for c in repo.list_checkpoints(db, run_id) if c["kind"] == "training_full"]
    assert epochs == list(range(1, 7))  # every epoch exactly once across the pause
    ev = [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    assert ev[0]["row"]["raw_metrics"]["child_env"]["resumed_from_epoch"] == paused_at
    ledger = repo.list_costs(db, run_id)
    assert [(r["kind"], r["meta"]["outcome"]) for r in ledger] == [("train", "paused"), ("train", "succeeded")]


def test_cancel_control_terminates_child_and_commits_cancelled(core, daemon_factory, base_proto, db, monkeypatch,
                                                               tmp_path):
    """T3-08 (test_subprocess.py:172): cancel -> heartbeat control "cancel" -> the runner terminates the child and
    commits; job CANCELLED, ledger [failed/RUN_CANCELLED], run CANCELLED, cancel.state CANCELLED, child gone."""
    hang = tmp_path / "hang"
    hang.write_text("1")
    monkeypatch.setenv("MBNAS_STUB_HANG_FILE", str(hang))
    run_id, _ = harness.validate_and_run(core, base_proto())
    d = daemon_factory()
    t = _run_in_thread(d, max_jobs=1)
    try:
        harness.wait_for(lambda: repo.list_checkpoints(db, run_id), timeout=60)
        child_pid = harness.wait_for(lambda: d._child_pid, timeout=10)
        assert _alive(child_pid)
        r = core.post(f"/api/v1/runs/{run_id}/cancel", headers=auth(), json={"reason": "test"})
        assert r.json()["status"] == "CANCEL_REQUESTED" and r.json()["cancel"]["active_jobs"]
        harness.wait_for(lambda: repo.get_run(db, run_id)["status"] == "CANCELLED", timeout=60)
        harness.wait_for(lambda: not _alive(child_pid), timeout=15)
    finally:
        d.stopping = True
        t.join(timeout=60)
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "CANCELLED"
    ledger = repo.list_costs(db, run_id)
    assert [(r["kind"], r["meta"]["reason_code"]) for r in ledger] == [("failed", "RUN_CANCELLED")]
    run = repo.get_run(db, run_id)
    assert run["cancel"]["state"] == "CANCELLED" and run["costs"]["failed_gpu_seconds"] > 0


def test_succeeded_result_after_cancel_request_is_recorded_cancelled(core, runner_sim, base_proto, db):
    """T3-09 / F11 / DD-A18 option A: a SUCCEEDED result committed after the run was cancelled is accepted as
    CANCELLED; no EVALUATED evaluation; the original result is kept as error.discarded_result."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    out = runner_sim.run_attempt(lease, commit=False)
    assert out["result"].status == JobStatus.SUCCEEDED
    assert core.post(f"/api/v1/runs/{run_id}/cancel", headers=auth(), json={"reason": "race"}).status_code == 200
    r = runner_sim.result(lease, runner_sim.commit_body(lease, out["result"], artifact_ids=out["artifact_ids"]))
    assert r.status_code == 200 and r.json()["outcome"] == "CANCELLED", r.text
    job = repo.get_job(db, lease.job_id)
    assert job["status"] == "CANCELLED" and job["error"]["reason_code"] == "RUN_CANCELLED"
    assert job["error"]["discarded_result"]["status"] == "SUCCEEDED"
    assert not [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    assert repo.get_run(db, run_id)["status"] == "CANCELLED"
    assert [(c["kind"], c["meta"]["reason_code"]) for c in repo.list_costs(db, run_id)] == [("failed", "RUN_CANCELLED")]


def test_heartbeat_after_reclaim_is_409_lease_lost(core, runner_sim, daemon_factory, base_proto, sweeper, db):
    """T3-15 / F9: after the sweeper reclaimed the lease, the runner's heartbeat is 409 LEASE_LOST and the daemon's
    heartbeat loop records lease_lost (the monitor then terminates the child)."""
    from mbnas_runner.client import JobFence
    from mbnas_runner.daemon import _Live

    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    runner_sim.start(lease)
    assert sweeper(after_s=3600)["reclaimed"] == [lease.job_id]
    r = runner_sim.heartbeat(lease)
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST"
    d = daemon_factory()
    d.register()
    live, done = _Live(), threading.Event()
    beat = threading.Thread(target=d._heartbeat, args=(JobFence.from_claim(lease.claim), live,
                                                         runner_sim.stop_path(lease), done), daemon=True)
    t0 = time.monotonic()
    beat.start()
    beat.join(timeout=10)
    done.set()
    assert not beat.is_alive() and live.lease_lost and time.monotonic() - t0 < 5
    assert repo.get_job(db, lease.job_id)["status"] == "QUEUED"


def test_runner_shutdown_requeues_without_consuming_retry(core, daemon_factory, base_proto, db, monkeypatch):
    """T3-28 / F14: SIGTERM-equivalent shutdown of the runner during a job -> result with runner_shutting_down ->
    failed/WORKER_SHUTDOWN, job QUEUED, max_attempts + 1; the runner deregisters (OFFLINE)."""
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    run_id, _ = harness.validate_and_run(core, base_proto(targets=(6,)))
    max_before = repo.list_jobs(db, run_id)[0]["max_attempts"]
    d = daemon_factory()
    t = _run_in_thread(d)
    try:
        harness.wait_for(lambda: repo.list_checkpoints(db, run_id), timeout=60)
        d.stopping = True  # what the SIGTERM handler sets (install_signals)
        t.join(timeout=60)
    finally:
        d.stopping = True
    assert not t.is_alive()
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "QUEUED" and job["error"]["reason_code"] == "WORKER_SHUTDOWN"
    assert job["max_attempts"] == max_before + 1
    assert [(r["kind"], r["meta"]["reason_code"]) for r in repo.list_costs(db, run_id)] == [("failed", "WORKER_SHUTDOWN")]
    row = db.execute("SELECT status FROM runners WHERE id = 'R1'").fetchone()
    assert row["status"] == "OFFLINE"
