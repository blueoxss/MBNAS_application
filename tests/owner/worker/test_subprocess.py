"""Real worker/child processes (SYNTHETIC stub handlers, no GPU work): child protocol + env hygiene, crash/retry
resume, kill -9 of the worker with lease reclaim by a new worker, pause at an epoch boundary + resume, cancel."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.owner_worker.main import Worker, WorkerConfig

pytestmark = pytest.mark.slow


@pytest.fixture
def base_proto(make_dataset, tmp_roots):
    ds = make_dataset("w", counts={"train": 4, "val": 2, "test": 2})

    def build(targets=(2,)):
        return harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=(), baselines=("yolo11n",),
                                      targets=targets)
    return build


def _env(settings, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update({"MBNAS_CORE_DB_PATH": settings.db_path, "MBNAS_CORE_OUTPUT_ROOT": settings.output_root,  # DD-A13
                "MBNAS_CORE_OUTPUT_ROOTS": json.dumps(settings.output_roots),
                "MBNAS_CORE_DATASET_ROOTS": json.dumps(settings.dataset_roots), "MBNAS_CORE_LEASE_TTL_S": "2",
                "MBNAS_CORE_HEARTBEAT_S": "1", "PYTHONPATH": str(harness.HERE), "MBNAS_JOB_HANDLERS_MODULE": "stub_handlers",
                "MBNAS_STUB_EPOCH_S": "0.05"})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _spawn_worker(settings, log: Path, **extra) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "mbnas_core.owner_worker", "--poll-s", "0.1", "--kill-grace-s", "2",
                             # WP-2 §1.7: the default CLI is owner mode (PROPOSE_GENERATION only); these tests drive the
                             # stub-handler executor over every kind, so the kinds are named explicitly
                             "--kinds", "PROPOSE_GENERATION,BASELINE_TRAIN,CANDIDATE_TRAIN,FINAL_RETRAIN,EVALUATE,BENCHMARK"],
                            env=_env(settings, **extra), stdout=open(log, "a"), stderr=subprocess.STDOUT)


def _q(settings, fn):
    conn = connect(settings, init=False)
    try:
        return fn(conn)
    finally:
        conn.close()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as fh:  # a zombie is dead for our purposes
        return fh.read().split(")")[-1].split()[0] != "Z"


def test_child_protocol_env_hygiene_and_logs(client, base_proto, api_settings, monkeypatch):
    pytest.importorskip("torchvision", reason="the child imports torchvision before the handler (2026-10-15: not installed here)")
    harness.stub_env(monkeypatch)
    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "must-not-reach-children")  # DD-A13 name of the core token
    run_id, _ = harness.validate_and_run(client, base_proto())
    worker = Worker(api_settings, WorkerConfig(gpu_index=0, poll_s=0.05, kill_grace_s=2), worker_id="w-proc:1")
    assert harness.drain(worker) == 1
    worker.conn.close()
    ev = _q(api_settings, lambda c: [e for e in repo.list_evaluations(c, run_id) if e["status"] == "EVALUATED"])
    env = ev[0]["row"]["raw_metrics"]["child_env"]
    assert env == {"CUDA_VISIBLE_DEVICES": "0", "api_token_present": False, "signing_key_present": False,
                   "resumed_from_epoch": 0,
                   "torchvision_imported": True}  # GPU job kinds import torchvision before the handler (CUDA NMS)
    out = Path(base_proto()["console"]["paths"]["output_root"]) / run_id / "logs"
    logs = list(out.glob("*.stderr.log"))
    assert logs and "library noise on stdout" in logs[0].read_text()
    cks = _q(api_settings, lambda c: repo.list_checkpoints(c, run_id))
    assert [c["epoch"] for c in cks if c["kind"] == "training_full"] == [1, 2]
    assert _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "COMPLETED"


def test_crashed_child_is_retried_and_resumes_from_checkpoint(client, base_proto, api_settings, monkeypatch, tmp_path):
    harness.stub_env(monkeypatch)
    monkeypatch.setenv("MBNAS_STUB_CRASH_ONCE", str(tmp_path / "crashed.marker"))
    run_id, _ = harness.validate_and_run(client, base_proto())
    worker = Worker(api_settings, WorkerConfig(poll_s=0.05, kill_grace_s=2), worker_id="w-proc:2")
    assert harness.drain(worker) == 2  # attempt 1 crashes (exit 137, no result), attempt 2 succeeds
    worker.conn.close()
    job = _q(api_settings, lambda c: repo.list_jobs(c, run_id))[0]
    assert job["status"] == "SUCCEEDED" and job["attempt"] == 2 and job["result"]["exit_code"] == 0
    ledger = _q(api_settings, lambda c: repo.list_costs(c, run_id))
    assert [(r["attempt"], r["kind"], r["meta"]["reason_code"]) for r in ledger] == [
        (1, "failed", "CHILD_EXIT_NO_RESULT"), (2, "retry", None)]
    cks = _q(api_settings, lambda c: repo.list_checkpoints(c, run_id))
    assert [c["epoch"] for c in cks if c["kind"] == "training_full"] == [1, 2]  # epoch 1 not trained twice
    ev = _q(api_settings, lambda c: [e for e in repo.list_evaluations(c, run_id) if e["status"] == "EVALUATED"])
    assert len(ev) == 1 and ev[0]["row"]["raw_metrics"]["child_env"]["resumed_from_epoch"] == 1


def test_worker_kill9_lease_reclaimed_once_by_new_worker(client, base_proto, api_settings, monkeypatch, tmp_path):
    harness.stub_env(monkeypatch)
    hang = tmp_path / "hang"
    hang.write_text("1")
    run_id, _ = harness.validate_and_run(client, base_proto())
    first = _spawn_worker(api_settings, tmp_path / "w1.log", MBNAS_STUB_HANG_FILE=hang)
    try:
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.list_checkpoints(c, run_id)), timeout=30)
        child_pid = harness.wait_for(lambda: _q(api_settings, lambda c: next(
            (w["row"].get("child_pid") for w in repo.list_workers(c) if w["row"].get("child_pid")), None)), timeout=10)
        assert _alive(child_pid)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        harness.wait_for(lambda: not _alive(child_pid), timeout=10)  # PDEATHSIG: no orphaned GPU job
    finally:
        if first.poll() is None:
            first.kill()
    hang.unlink()
    second = _spawn_worker(api_settings, tmp_path / "w2.log")
    try:
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "COMPLETED", timeout=60)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=20)
    jobs = _q(api_settings, lambda c: repo.list_jobs(c, run_id))
    assert len(jobs) == 1 and jobs[0]["status"] == "SUCCEEDED" and jobs[0]["attempt"] == 2
    ledger = _q(api_settings, lambda c: repo.list_costs(c, run_id))
    lost = [r for r in ledger if r["meta"]["outcome"] == "lost"]
    assert len(lost) == 1 and lost[0]["kind"] == "failed" and lost[0]["meta"]["reason_code"] == "LEASE_EXPIRED"
    cks = _q(api_settings, lambda c: repo.list_checkpoints(c, run_id))
    assert [c["epoch"] for c in cks if c["kind"] == "training_full"] == [1, 2]
    evs = _q(api_settings, lambda c: [e for e in repo.list_evaluations(c, run_id) if e["status"] == "EVALUATED"])
    assert len(evs) == 1 and evs[0]["row"]["raw_metrics"]["child_env"]["resumed_from_epoch"] == 1
    assert _q(api_settings, lambda c: {w["status"] for w in repo.list_workers(c)}) >= {"offline"}


def test_pause_at_epoch_boundary_then_resume(client, base_proto, api_settings, monkeypatch, tmp_path):
    harness.stub_env(monkeypatch)
    run_id, _ = harness.validate_and_run(client, base_proto(targets=(6,)))
    worker = _spawn_worker(api_settings, tmp_path / "w.log", MBNAS_STUB_EPOCH_S="0.4")
    try:
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.list_checkpoints(c, run_id)), timeout=30)
        r = client.post(f"/api/v1/runs/{run_id}/pause", headers=auth(), json={"reason": "test"})
        assert r.json()["status"] == "PAUSE_REQUESTED"
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "PAUSED", timeout=30)
        job = _q(api_settings, lambda c: repo.list_jobs(c, run_id))[0]
        assert job["status"] == "QUEUED" and job["error"]["reason_code"] == "PAUSED_AT_SAFE_BOUNDARY"
        paused_at = max(c["epoch"] for c in _q(api_settings, lambda c: repo.list_checkpoints(c, run_id)))
        assert 1 <= paused_at < 6
        cand = _q(api_settings, lambda c: repo.get_candidate(c, job["candidate_id"]))
        assert cand["candidate_status"] == "PAUSED"
        r = client.post(f"/api/v1/runs/{run_id}/resume", headers=auth())
        assert r.status_code == 200, r.text
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "COMPLETED", timeout=60)
    finally:
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=20)
    job = _q(api_settings, lambda c: repo.list_jobs(c, run_id))[0]
    assert job["status"] == "SUCCEEDED" and job["attempt"] == 2 and job["max_attempts"] == 4
    epochs = [c["epoch"] for c in _q(api_settings, lambda c: repo.list_checkpoints(c, run_id)) if c["kind"] == "training_full"]
    assert epochs == list(range(1, 7))  # every epoch exactly once across the pause
    ev = _q(api_settings, lambda c: [e for e in repo.list_evaluations(c, run_id) if e["status"] == "EVALUATED"])
    assert ev[0]["row"]["raw_metrics"]["child_env"]["resumed_from_epoch"] == paused_at
    ledger = _q(api_settings, lambda c: repo.list_costs(c, run_id))
    assert [(r["kind"], r["meta"]["outcome"]) for r in ledger] == [("train", "paused"), ("train", "succeeded")]


def test_cancel_terminates_running_child(client, base_proto, api_settings, monkeypatch, tmp_path):
    harness.stub_env(monkeypatch)
    hang = tmp_path / "hang"
    hang.write_text("1")
    run_id, _ = harness.validate_and_run(client, base_proto())
    worker = _spawn_worker(api_settings, tmp_path / "w.log", MBNAS_STUB_HANG_FILE=hang)
    try:
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.list_checkpoints(c, run_id)), timeout=30)
        child_pid = harness.wait_for(lambda: _q(api_settings, lambda c: next(
            (w["row"].get("child_pid") for w in repo.list_workers(c) if w["row"].get("child_pid")), None)), timeout=10)
        r = client.post(f"/api/v1/runs/{run_id}/cancel", headers=auth(), json={"reason": "test"})
        assert r.json()["status"] == "CANCEL_REQUESTED" and r.json()["cancel"]["active_jobs"]
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "CANCELLED", timeout=30)
        harness.wait_for(lambda: not _alive(child_pid), timeout=10)
    finally:
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=20)
    job = _q(api_settings, lambda c: repo.list_jobs(c, run_id))[0]
    assert job["status"] == "CANCELLED"
    ledger = _q(api_settings, lambda c: repo.list_costs(c, run_id))
    assert [(r["kind"], r["meta"]["reason_code"]) for r in ledger] == [("failed", "RUN_CANCELLED")]
    run = _q(api_settings, lambda c: repo.get_run(c, run_id))
    assert run["cancel"]["state"] == "CANCELLED" and run["costs"]["failed_gpu_seconds"] > 0
