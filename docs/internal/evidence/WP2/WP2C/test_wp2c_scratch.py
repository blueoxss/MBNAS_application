"""SCRATCH self-tests of the WP-2C runner against a stdlib fake core (development only; the T3 tests are WP-2D)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
APP = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
sys.path.insert(0, str(HERE))
from fake_core import FakeCore, envelope  # noqa: E402

from mbnas_runner.client import CoreClient, RunnerConfigError, check_core_url  # noqa: E402
from mbnas_runner.config import RunnerSettings  # noqa: E402
from mbnas_runner.daemon import RunnerDaemon  # noqa: E402
from mbnas_runner.journal import AttemptJournal, Stage, payload_digest  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "stub_handlers")
    monkeypatch.setenv("PYTHONPATH", str(APP / "tests" / "_shared"))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.05")
    return tmp_path


def settings(tmp_path: Path, url: str, **kw) -> RunnerSettings:
    tok = tmp_path / "token"
    tok.write_text("tok-test\n")
    return RunnerSettings(core_url=url, token_file=str(tok), runner_id="runner-A", work_root=str(tmp_path / "work"),
                          request_backoff_s=0.05, poll_s=0.1, kill_grace_s=2, **kw)


def paths(core: FakeCore) -> list[str]:
    assert not core.invalid, core.invalid
    return [p.rsplit("/", 1)[-1] for p, _ in core.calls]


def test_happy_path_claim_start_heartbeat_progress_result_ack(env):
    core = FakeCore([envelope(target=3)], heartbeat_s=0.2)
    os.environ["MBNAS_STUB_EPOCH_S"] = "0.5"
    try:
        d = RunnerDaemon(settings(env, core.url))
        assert d.run(once=True) == 0
    finally:
        core.stop()
    seq = paths(core)
    assert seq[:3] == ["register", "claim", "start"], seq
    for name in ("heartbeat", "progress", "checkpoints", "artifacts", "result", "ack", "deregister"):
        assert name in seq, (name, seq)
    assert seq.index("artifacts") < seq.index("result") < seq.index("ack")
    reg = core.calls[0][1]
    assert reg["runtime"]["envelope_version"] == 1 and "operator_files_sha256" in reg["runtime"]
    assert [c["epoch"] for c in core.checkpoints] == [1, 2, 3]
    assert all(len(c["sha256"]) == 64 and not c["relpath"].startswith("/") for c in core.checkpoints)
    (key, res), = core.results.items()
    body = res["body"]
    assert body["result"]["status"] == "SUCCEEDED" and body["cancel_observed"] is False
    assert body["runner_shutting_down"] is False and body["started_at"]
    assert body["artifact_ids"] and all(a.get("artifact_id") for a in body["result"]["outputs"]["evaluation"]["artifacts"])
    writes = [b for p, b in core.calls if "/jobs/" in p]
    assert all(b["runner_id"] == "runner-A" and b["attempt"] == 1 and b["ticket"] == "tkt-job-1" for b in writes)
    assert not core.invalid, core.invalid
    assert body["payload_digest"].startswith("sha256:") and not core.invalid_responses
    j = AttemptJournal.create(env / "work", "job-1", 1)
    assert j.stage == Stage.CLOSED and j.acked and j.result["payload_digest"] == payload_digest(j.result)
    prog = [b for b in core.progress]
    stamps = [p for p, _ in core.calls if p.endswith("/progress")]
    assert prog and [m for m in prog[-1]["messages"] if m["type"] == "progress"][-1]["epoch"] == 3
    seqs = [(b["seq_start"], len(b["messages"])) for b in prog]
    assert all(a + n == b for (a, n), (b, _) in zip(seqs, seqs[1:])) and seqs[0][0] == 1
    spec = json.loads(j.spec_path.read_text())
    assert spec["params"]["stop_request_path"].endswith("/runs/run-x/control/job-1.stop")
    assert (env / "work" / "runs" / "run-x" / "protocol" / "resolved.json").is_file()
    assert len(stamps) <= 3 + 2  # <= 1/s over ~1.5 s of epochs (+ final flush)


def _run_with_control(env, action: str, *, epochs: int = 6):
    core = FakeCore([envelope(target=epochs)], heartbeat_s=0.2)
    os.environ["MBNAS_STUB_EPOCH_S"] = "0.4"
    d = RunnerDaemon(settings(env, core.url))
    stop = env / "work" / "runs" / "run-x" / "control" / "job-1.stop"
    seen = {}

    def flip():
        while not core.checkpoints:
            time.sleep(0.05)
        core.control = {"action": action}
        for _ in range(100):
            if stop.exists():
                seen["content"] = json.loads(stop.read_text())
                break
            time.sleep(0.02)

    t = threading.Thread(target=flip, daemon=True)
    t.start()
    try:
        d.run(once=True)
    finally:
        core.stop()
    t.join(5)
    return core, seen, stop


def test_pause_control_writes_stop_file_and_commits_paused(env):
    core, seen, stop = _run_with_control(env, "pause")
    assert seen["content"]["action"] == "pause"
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] == "CANCELLED" and body["result"]["reason_code"] in __import__("mbnas_contracts.jobs_protocol", fromlist=["x"]).PAUSE_REASONS
    assert body["cancel_observed"] is False and not stop.exists()


def test_cancel_control_terminates_child_and_reports_cancel_observed(env):
    core, seen, _ = _run_with_control(env, "cancel")
    assert seen["content"]["action"] == "cancel"
    body = next(iter(core.results.values()))["body"]
    assert body["cancel_observed"] is True


def test_pause_withdrawal_deletes_stop_file(env, tmp_path):
    from mbnas_runner.daemon import _Live

    core = FakeCore([])
    d = RunnerDaemon(settings(env, core.url))
    live, stop = _Live(), tmp_path / "c" / "j.stop"
    d.apply_control({"action": "pause"}, live, stop)
    assert json.loads(stop.read_text())["action"] == "pause"
    d.apply_control({"action": None}, live, stop)
    assert not stop.exists()
    d.apply_control({"action": "cancel"}, live, stop)
    d.apply_control({"action": None}, live, stop)  # a cancel is never withdrawn
    assert stop.exists() and live.cancel_requested
    core.stop()


def test_shutdown_reports_runner_shutting_down(env):
    core = FakeCore([envelope(target=6)], heartbeat_s=0.2)
    os.environ["MBNAS_STUB_EPOCH_S"] = "0.4"
    d = RunnerDaemon(settings(env, core.url))

    def stop_soon():
        while not core.checkpoints:
            time.sleep(0.05)
        d.stopping = True

    threading.Thread(target=stop_soon, daemon=True).start()
    try:
        d.run(once=True)
    finally:
        core.stop()
    body = next(iter(core.results.values()))["body"]
    assert body["runner_shutting_down"] is True and body["cancel_observed"] is False
    assert paths(core)[-1] == "deregister"


def test_lease_lost_heartbeat_stops_child_and_discards_result(env):
    core = FakeCore([envelope(target=8)], heartbeat_s=0.2)
    os.environ["MBNAS_STUB_EPOCH_S"] = "0.4"
    d = RunnerDaemon(settings(env, core.url))

    def lose():
        while not core.checkpoints:
            time.sleep(0.05)
        core.lost.add("job-1")

    threading.Thread(target=lose, daemon=True).start()
    t0 = time.monotonic()
    try:
        d.run(once=True)
    finally:
        core.stop()
    assert len(core.checkpoints) < 8  # the child was stopped before finishing its 8 epochs
    j = AttemptJournal.create(env / "work", "job-1", 1)
    assert j.discarded["code"] == "LEASE_LOST" and not j.acked and not core.results


def test_lease_expiry_watchdog_kills_child_without_renewal(env):
    core = FakeCore([envelope(target=20)], heartbeat_s=30, lease_ttl_s=1)  # no beat before the lease runs out
    os.environ["MBNAS_STUB_EPOCH_S"] = "0.3"
    d = RunnerDaemon(settings(env, core.url))
    t0 = time.monotonic()
    try:
        d.run(once=True)
    finally:
        core.stop()
    assert time.monotonic() - t0 < 6
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] != "SUCCEEDED"


def test_commit_failed_is_retried_with_the_same_digest(env):
    core = FakeCore([envelope(target=1)])
    core.fail_result_once = True
    try:
        RunnerDaemon(settings(env, core.url)).run(once=True)
    finally:
        core.stop()
    results = [b for p, b in core.calls if p.endswith("/result")]
    assert len(results) == 2 and results[0]["payload_digest"] == results[1]["payload_digest"]
    assert ("job-1", 1) in core.acked


def test_restart_replay_started_without_result_sends_child_exit_no_result(env):
    core = FakeCore([])
    s = settings(env, core.url)
    e = envelope()
    core.leased["job-1"] = {**e, "_state": "RUNNING"}
    j = AttemptJournal.create(s.work_root, "job-1", 1)
    j.record_claim({"envelope": e, "ticket": "tkt-job-1", "server_time": "2026-10-26T00:00:00Z"})
    j.record_started({"started_at": "2026-10-26T00:00:00.000000Z", "lease_expires_at": None})
    try:
        d = RunnerDaemon(s)
        d.register()
        done = d.replay()
    finally:
        core.stop()
    assert done == [("job-1", 1, "closed")]
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["reason_code"] == "CHILD_EXIT_NO_RESULT" and body["result"]["outputs"]["retryable"] is True
    assert body["started_at"] == "2026-10-26T00:00:00.000000Z" and j.acked


def test_restart_replay_resends_frozen_result_then_acks(env):
    core = FakeCore([])
    s = settings(env, core.url)
    e = envelope()
    core.leased["job-1"] = {**e, "_state": "RUNNING"}
    j = AttemptJournal.create(s.work_root, "job-1", 1)
    j.record_claim({"envelope": e, "ticket": "tkt-job-1", "server_time": "2026-10-26T00:00:00Z"})
    j.record_started({"started_at": "2026-10-26T00:00:00.000000Z"})
    res = {"job_id": "job-1", "status": "FAILED", "attempt": 1, "exit_code": -9, "reason_code": "CHILD_EXIT_NO_RESULT",
           "message": "m", "outputs": {"retryable": True}, "gpu_seconds": None, "started_at": None, "finished_at": None}
    frozen = j.record_result({"result": res, "started_at": "2026-10-26T00:00:00.000000Z",
                              "finished_at": "2026-10-26T00:00:01.000000Z", "cancel_observed": False,
                              "runner_shutting_down": False, "artifact_ids": [], "device_uuid": None})
    again = j.record_result({"result": "different"})  # result.json is immutable
    assert again == frozen
    try:
        d = RunnerDaemon(s)
        d.register()
        d.replay()
        # response present, ack lost: a second replay only acks
        j2 = AttemptJournal.create(s.work_root, "job-2", 1)
        e2 = envelope("job-2")
        core.leased["job-2"] = {**e2, "_state": "DONE"}
        j2.record_claim({"envelope": e2, "ticket": "tkt-job-2", "server_time": "2026-10-26T00:00:00Z"})
        j2.record_started({"started_at": "x"})
        j2.record_result({"result": res, "started_at": "x", "finished_at": "y"})
        j2.record_response({"outcome": "SUCCEEDED"})
        d.replay()
    finally:
        core.stop()
    sent = [b for p, b in core.calls if p.endswith("/result")]
    assert len(sent) == 1 and sent[0]["payload_digest"] == frozen["payload_digest"]
    assert j.acked and j2.acked and ("job-2", 1) in core.acked


def test_restart_replay_claimed_without_start_runs_or_discards(env):
    core = FakeCore([])
    s = settings(env, core.url)
    e = envelope(target=1)
    j = AttemptJournal.create(s.work_root, "job-1", 1)
    j.record_claim({"envelope": e, "ticket": "tkt-job-1", "server_time": "2026-10-26T00:00:00Z"})  # core no longer leases it -> start 409 -> discarded
    try:
        d = RunnerDaemon(s)
        d.register()
        d.replay()
    finally:
        core.stop()
    assert j.discarded["code"] == "LEASE_LOST"


def test_core_url_insecure_refused():
    with pytest.raises(RunnerConfigError) as exc:
        check_core_url("http://10.1.2.3:8700")
    assert exc.value.code == "CORE_URL_INSECURE"
    for ok in ("http://127.0.0.1:8700", "http://localhost:8700", "http://[::1]:8700", "https://core.example:8700"):
        check_core_url(ok)
    with pytest.raises(RunnerConfigError):
        CoreClient(RunnerSettings(core_url="http://192.168.0.5:8700", token="t"))


def test_cli_exit_code_for_insecure_url(env, monkeypatch):
    import subprocess
    e = dict(os.environ, MBNAS_RUNNER_CORE_URL="http://10.9.9.9:8700", MBNAS_RUNNER_TOKEN="x")
    p = subprocess.run([sys.executable, "-B", "-m", "mbnas_runner", "run", "--once"], capture_output=True, text=True,
                       env=e, timeout=120)
    assert p.returncode == 2 and "CORE_URL_INSECURE" in p.stderr


def test_checkpoint_tamper_rejected(tmp_path):
    from mbnas_contracts.util.atomic import sha256_file
    from mbnas_runner.reports import verify_checkpoint

    f = tmp_path / "run" / "candidates" / "c" / "full.pt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"abc")
    good = {"path": str(f), "bytes": 3, "kind": "training_full", "sha256": sha256_file(f), "epoch": 1}
    rep, det = verify_checkpoint(good, tmp_path / "run", "c", "L0")
    assert rep and rep["relpath"] == "candidates/c/full.pt"
    rep, det = verify_checkpoint({**good, "sha256": "0" * 64}, tmp_path / "run", "c", "L0")
    assert rep is None and "sha256 mismatch" in det["problem"]
    rep, det = verify_checkpoint({**good}, tmp_path / "other", "c", "L0")
    assert rep is None


def test_dataset_mounts_remap(tmp_path):
    from mbnas_runner.training.context import JobInputError, local_data_path

    (tmp_path / "local" / "train").mkdir(parents=True)
    s = RunnerSettings(dataset_mounts=json.dumps({"/srv/data": str(tmp_path / "local")}), token="t")
    assert str(tmp_path / "local") in s.dataset_roots
    assert local_data_path("/srv/data/train", s) == (str(tmp_path / "local" / "train"), True)
    with pytest.raises(JobInputError) as exc:
        local_data_path("/elsewhere/train", s)
    assert exc.value.code == "DATASET_UNAVAILABLE"
    with pytest.raises(JobInputError) as exc:
        local_data_path("/srv/data/val", s)
    assert exc.value.code == "DATASET_UNAVAILABLE"
    assert local_data_path("/x/y", RunnerSettings(token="t")) == ("/x/y", False)


def test_native_spec_from_envelope_for_search_candidate(tmp_path):
    from mbnas_runner.envelope import spec_from_envelope

    e = envelope()
    e["candidate"]["genotype"] = {"yaml": {"nc": 1, "backbone": [], "head": []}, "family": "yolo11"}
    e["resume"]["live_latest"] = {"id": "ck-1", "path": "/w/ck", "sha256": "a" * 64, "epoch": 2, "rung": "L0",
                                  "hashes": {}, "state": "PRESENT"}
    spec = spec_from_envelope(e, tmp_path, native=True)
    p = spec.params
    assert p["arch"]["model_id"] == "run-x.ga0.g1.c001" and p["arch"]["backend"] == "ultralytics"
    assert p["resume_from"]["id"] == "ck-1" and "state" not in p["resume_from"]
    assert p["run_dir"] == str(tmp_path / "runs" / "run-x") and p["stage_epochs_added"] == 3
    b = envelope()
    b["candidate"]["arm"] = "Baseline"
    b["job"]["spec"]["params"]["arch"] = {"model_id": "yolo11n", "backend": "ultralytics"}
    b["job"]["spec"]["params"]["candidate"]["baseline_id"] = "yolo11n"
    assert spec_from_envelope(b, tmp_path, native=True).params["arch"]["model_id"] == "yolo11n"


def test_invalid_envelope_spec_is_started_and_committed_as_failure(env):
    e = envelope(target=3)
    e["job"]["spec"]["fixed_horizon"] = 2
    core = FakeCore([e])
    try:
        RunnerDaemon(settings(env, core.url)).run(once=True)
    finally:
        core.stop()
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["reason_code"] == "ENVELOPE_INVALID" and "start" in paths(core)
