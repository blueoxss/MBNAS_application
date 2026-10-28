"""UNIT tests of the runner's remote-queue modules (``mbnas_runner.client`` / ``journal`` / ``daemon``) against a stdlib
fake core (``tests/_shared/fake_core.py``) - WP-2 contract section 4: unit level only, NOT integration evidence (the T3
matrix against the real core app is tests/owner/runner_protocol; the real two-process run is
scripts/dev/wp2_two_process_smoke.py). Job children are real ``python -m mbnas_runner.child`` processes with the
SYNTHETIC stub handlers. Ported from the WP-2C scratch self-tests (docs/internal/evidence/WP2/WP2C/test_wp2c_scratch.py)
plus client/journal units and the runner side of the S7-F1 GPU occupancy wait (moved from the owner worker,
tests/owner/worker/test_gpu_occupancy.py)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from fake_core import FakeCore, envelope

from mbnas_contracts.jobs_protocol import PAUSE_REASONS
from mbnas_contracts.runner_protocol import compute_payload_digest
from mbnas_contracts.schemas.jobs import JobResult
from mbnas_runner import gpu
from mbnas_runner.client import CoreClient, CoreError, CoreUnavailable, JobFence, RunnerConfigError, check_core_url
from mbnas_runner.config import RunnerSettings
from mbnas_runner.daemon import RunnerDaemon, _Live
from mbnas_runner.journal import AttemptJournal, Stage, payload_digest

SHARED = Path(__file__).resolve().parents[2] / "_shared"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "stub_handlers")
    monkeypatch.setenv("PYTHONPATH", str(SHARED))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.05")
    return tmp_path


def rsettings(tmp_path: Path, url: str, **kw) -> RunnerSettings:
    tok = tmp_path / "token"
    tok.write_text("tok-test\n")
    return RunnerSettings(core_url=url, token_file=str(tok), runner_id="runner-A", work_root=str(tmp_path / "work"),
                          request_backoff_s=0.05, poll_s=0.1, kill_grace_s=2, **kw)


def paths(core: FakeCore) -> list[str]:
    assert not core.invalid, core.invalid
    return [p.rsplit("/", 1)[-1] for p, _ in core.calls]


# -- daemon over the fake core ---------------------------------------------------------------------------------------
def test_happy_path_claim_start_heartbeat_progress_result_ack(env, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.5")
    core = FakeCore([envelope(target=3)], heartbeat_s=0.2)
    try:
        assert RunnerDaemon(rsettings(env, core.url)).run(once=True) == 0
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
    (_, res), = core.results.items()
    body = res["body"]
    assert body["result"]["status"] == "SUCCEEDED" and body["cancel_observed"] is False
    assert body["runner_shutting_down"] is False and body["started_at"]
    assert body["artifact_ids"] and all(a.get("artifact_id") for a in body["result"]["outputs"]["evaluation"]["artifacts"])
    writes = [b for p, b in core.calls if "/jobs/" in p]
    assert all(b["runner_id"] == "runner-A" and b["attempt"] == 1 and b["ticket"] == "tkt-job-1" for b in writes)
    assert body["payload_digest"].startswith("sha256:") and not core.invalid_responses
    j = AttemptJournal.create(env / "work", "job-1", 1)
    assert j.stage == Stage.CLOSED and j.acked and j.result["payload_digest"] == payload_digest(j.result)
    prog = list(core.progress)
    stamps = [p for p, _ in core.calls if p.endswith("/progress")]
    assert prog and [m for m in prog[-1]["messages"] if m["type"] == "progress"][-1]["epoch"] == 3
    seqs = [(b["seq_start"], len(b["messages"])) for b in prog]
    assert all(a + n == b for (a, n), (b, _) in zip(seqs, seqs[1:])) and seqs[0][0] == 1
    spec = json.loads(j.spec_path.read_text())
    assert spec["params"]["stop_request_path"].endswith("/runs/run-x/control/job-1.stop")
    assert (env / "work" / "runs" / "run-x" / "protocol" / "resolved.json").is_file()
    assert len(stamps) <= 3 + 2  # <= 1/s over ~1.5 s of epochs (+ final flush)


def _run_with_control(env, monkeypatch, action: str, *, epochs: int = 6):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    core = FakeCore([envelope(target=epochs)], heartbeat_s=0.2)
    d = RunnerDaemon(rsettings(env, core.url))
    stop = env / "work" / "runs" / "run-x" / "control" / "job-1.stop"
    seen: dict = {}

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


def test_pause_control_writes_stop_file_and_commits_paused(env, monkeypatch):
    core, seen, stop = _run_with_control(env, monkeypatch, "pause")
    assert seen["content"]["action"] == "pause"
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] == "CANCELLED" and body["result"]["reason_code"] in PAUSE_REASONS
    assert body["cancel_observed"] is False and not stop.exists()


def test_cancel_control_terminates_child_and_reports_cancel_observed(env, monkeypatch):
    core, seen, _ = _run_with_control(env, monkeypatch, "cancel")
    assert seen["content"]["action"] == "cancel"
    assert next(iter(core.results.values()))["body"]["cancel_observed"] is True


def test_pause_withdrawal_deletes_stop_file_and_cancel_is_never_withdrawn(env, tmp_path):
    core = FakeCore([])
    try:
        d = RunnerDaemon(rsettings(env, core.url))
        live, stop = _Live(), tmp_path / "c" / "j.stop"
        d.apply_control({"action": "pause"}, live, stop)
        assert json.loads(stop.read_text())["action"] == "pause"
        d.apply_control({"action": None}, live, stop)
        assert not stop.exists()
        d.apply_control({"action": "cancel"}, live, stop)
        d.apply_control({"action": None}, live, stop)
        assert stop.exists() and live.cancel_requested
    finally:
        core.stop()


def test_shutdown_reports_runner_shutting_down(env, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    core = FakeCore([envelope(target=6)], heartbeat_s=0.2)
    d = RunnerDaemon(rsettings(env, core.url))

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
    assert body["result"]["reason_code"] == "WORKER_SHUTDOWN" and body["result"]["outputs"]["retryable"] is True
    assert paths(core)[-1] == "deregister"


def test_lease_lost_heartbeat_stops_child_and_discards_result(env, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    core = FakeCore([envelope(target=8)], heartbeat_s=0.2)
    d = RunnerDaemon(rsettings(env, core.url))

    def lose():
        while not core.checkpoints:
            time.sleep(0.05)
        core.lost.add("job-1")

    threading.Thread(target=lose, daemon=True).start()
    try:
        d.run(once=True)
    finally:
        core.stop()
    assert len(core.checkpoints) < 8  # the child was stopped before finishing its 8 epochs
    j = AttemptJournal.create(env / "work", "job-1", 1)
    assert j.discarded["code"] == "LEASE_LOST" and not j.acked and not core.results


def test_lease_expiry_watchdog_kills_child_without_renewal(env, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.3")
    core = FakeCore([envelope(target=20)], heartbeat_s=30, lease_ttl_s=1)  # no beat before the lease runs out
    d = RunnerDaemon(rsettings(env, core.url))
    t0 = time.monotonic()
    try:
        d.run(once=True)
    finally:
        core.stop()
    assert time.monotonic() - t0 < 6
    assert next(iter(core.results.values()))["body"]["result"]["status"] != "SUCCEEDED"


def test_commit_failed_is_retried_with_the_same_digest(env):
    core = FakeCore([envelope(target=1)])
    core.fail_result_once = True
    try:
        RunnerDaemon(rsettings(env, core.url)).run(once=True)
    finally:
        core.stop()
    results = [b for p, b in core.calls if p.endswith("/result")]
    assert len(results) == 2 and results[0]["payload_digest"] == results[1]["payload_digest"]
    assert ("job-1", 1) in core.acked


def _journal_with(s, e, job_id="job-1"):
    j = AttemptJournal.create(s.work_root, job_id, 1)
    j.record_claim({"envelope": e, "ticket": f"tkt-{job_id}", "server_time": "2026-10-26T00:00:00Z"})
    return j


def test_restart_replay_started_without_result_sends_child_exit_no_result(env):
    core = FakeCore([])
    s = rsettings(env, core.url)
    e = envelope()
    core.leased["job-1"] = {**e, "_state": "RUNNING"}
    j = _journal_with(s, e)
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
    s = rsettings(env, core.url)
    e = envelope()
    core.leased["job-1"] = {**e, "_state": "RUNNING"}
    j = _journal_with(s, e)
    j.record_started({"started_at": "2026-10-26T00:00:00.000000Z"})
    res = {"job_id": "job-1", "status": "FAILED", "attempt": 1, "exit_code": -9, "reason_code": "CHILD_EXIT_NO_RESULT",
           "message": "m", "outputs": {"retryable": True}, "gpu_seconds": None, "started_at": None, "finished_at": None}
    frozen = j.record_result({"result": res, "started_at": "2026-10-26T00:00:00.000000Z",
                              "finished_at": "2026-10-26T00:00:01.000000Z", "cancel_observed": False,
                              "runner_shutting_down": False, "artifact_ids": [], "device_uuid": None})
    assert j.record_result({"result": "different"}) == frozen  # result.json is immutable
    try:
        d = RunnerDaemon(s)
        d.register()
        d.replay()
        e2 = envelope("job-2")  # response present, ack lost: a replay only acks
        core.leased["job-2"] = {**e2, "_state": "DONE"}
        j2 = _journal_with(s, e2, "job-2")
        j2.record_started({"started_at": "x"})
        j2.record_result({"result": res, "started_at": "x", "finished_at": "y"})
        j2.record_response({"outcome": "SUCCEEDED"})
        d.replay()
    finally:
        core.stop()
    sent = [b for p, b in core.calls if p.endswith("/result")]
    assert len(sent) == 1 and sent[0]["payload_digest"] == frozen["payload_digest"]
    assert j.acked and j2.acked and ("job-2", 1) in core.acked


def test_restart_replay_claimed_without_start_is_discarded_when_the_lease_is_gone(env):
    core = FakeCore([])
    s = rsettings(env, core.url)
    j = _journal_with(s, envelope(target=1))  # the core no longer leases it -> start 409 -> discarded
    try:
        d = RunnerDaemon(s)
        d.register()
        d.replay()
    finally:
        core.stop()
    assert j.discarded["code"] == "LEASE_LOST"


def test_invalid_envelope_spec_is_started_and_committed_as_failure(env):
    e = envelope(target=3)
    e["job"]["spec"]["fixed_horizon"] = 2
    core = FakeCore([e])
    try:
        RunnerDaemon(rsettings(env, core.url)).run(once=True)
    finally:
        core.stop()
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["reason_code"] == "ENVELOPE_INVALID" and "start" in paths(core)


# -- client units ----------------------------------------------------------------------------------------------------
def test_core_url_insecure_refused():
    with pytest.raises(RunnerConfigError) as exc:
        check_core_url("http://10.1.2.3:8700")
    assert exc.value.code == "CORE_URL_INSECURE"
    for ok in ("http://127.0.0.1:8700", "http://localhost:8700", "http://[::1]:8700", "https://core.example:8700"):
        check_core_url(ok)
    with pytest.raises(RunnerConfigError):
        CoreClient(RunnerSettings(core_url="http://192.168.0.5:8700", token="t"))


def test_cli_exit_code_for_insecure_url(env):
    e = dict(os.environ, MBNAS_RUNNER_CORE_URL="http://10.9.9.9:8700", MBNAS_RUNNER_TOKEN="x")
    p = subprocess.run([sys.executable, "-B", "-m", "mbnas_runner", "run", "--once"], capture_output=True, text=True,
                       env=e, timeout=120)
    assert p.returncode == 2 and "CORE_URL_INSECURE" in p.stderr


def test_client_retries_5xx_and_processing_but_raises_4xx_at_once(tmp_path):
    sleeps: list[float] = []
    answers = {"/core/v1/runner/claim": [httpx.Response(500, json={"code": "COMMIT_FAILED", "message": "x"}),
                                         httpx.Response(202, json={"code": "PROCESSING"}, headers={"Retry-After": "2"}),
                                         httpx.Response(204)],
               "/core/v1/runner/jobs/j/start": [httpx.Response(409, json={"code": "LEASE_LOST", "message": "gone"})]}
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content or b"{}")))
        assert request.headers["Authorization"] == "Bearer t" and request.headers.get("X-Request-ID")
        return answers[request.url.path].pop(0)

    c = CoreClient(RunnerSettings(core_url="http://127.0.0.1:8700", token="t", runner_id="R"),
                   transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert c.claim(["BASELINE_TRAIN"]) is None
    assert sleeps[1] == 2.0  # Retry-After honoured
    with pytest.raises(CoreError) as exc:
        c.start(JobFence("j", 3, "tkt"))
    assert exc.value.status == 409 and exc.value.code == "LEASE_LOST"
    assert seen[-1][1] == {"runner_id": "R", "attempt": 3, "ticket": "tkt", "device_uuid": None}
    always = CoreClient(RunnerSettings(core_url="http://127.0.0.1:8700", token="t", runner_id="R", request_retries=2),
                        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"code": "X"})),
                        sleep=lambda s: None)
    with pytest.raises(CoreUnavailable):
        always.claim(["BENCHMARK"])


def test_journal_digest_matches_the_contract_digest_and_stages_advance(tmp_path):
    j = AttemptJournal.create(tmp_path, "job-9", 2)
    j.record_claim({"envelope": envelope("job-9", attempt=2), "ticket": "t", "server_time": "2026-10-26T00:00:00Z"})
    assert j.stage == Stage.CLAIMED
    j.record_started({"started_at": "2026-10-26T00:00:00.000000Z"})
    assert j.stage == Stage.STARTED
    res = JobResult(job_id="job-9", status="SUCCEEDED", attempt=2, outputs={"x": 1})
    commit = j.record_result({"result": res.model_dump(mode="json"), "started_at": "2026-10-26T00:00:00.000000Z",
                              "finished_at": "2026-10-26T00:00:05.000000Z", "cancel_observed": False,
                              "runner_shutting_down": False, "artifact_ids": ["art-1"], "device_uuid": None})
    assert commit["payload_digest"] == compute_payload_digest(res, "2026-10-26T00:00:00.000000Z",
                                                              "2026-10-26T00:00:05.000000Z", False, ["art-1"])
    assert j.stage == Stage.RESULT
    j.record_response({"outcome": "SUCCEEDED"})
    assert j.stage == Stage.RESPONDED
    j.record_acked()
    assert j.stage == Stage.CLOSED
    with pytest.raises(ValueError):
        AttemptJournal.create(tmp_path, "../escape", 1)


def test_checkpoint_tamper_rejected(tmp_path):
    from mbnas_contracts.util.atomic import sha256_file
    from mbnas_runner.reports import verify_checkpoint

    f = tmp_path / "run" / "candidates" / "c" / "full.pt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"abc")
    good = {"path": str(f), "bytes": 3, "kind": "training_full", "sha256": sha256_file(f), "epoch": 1}
    rep, _ = verify_checkpoint(good, tmp_path / "run", "c", "L0")
    assert rep and rep["relpath"] == "candidates/c/full.pt"
    rep, det = verify_checkpoint({**good, "sha256": "0" * 64}, tmp_path / "run", "c", "L0")
    assert rep is None and "sha256 mismatch" in det["problem"]
    rep, _ = verify_checkpoint({**good}, tmp_path / "other", "c", "L0")
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
    p = spec_from_envelope(e, tmp_path, native=True).params
    assert p["arch"]["model_id"] == "run-x.ga0.g1.c001" and p["arch"]["backend"] == "ultralytics"
    assert p["resume_from"]["id"] == "ck-1" and "state" not in p["resume_from"]
    assert p["run_dir"] == str(tmp_path / "runs" / "run-x") and p["stage_epochs_added"] == 3
    b = envelope()
    b["candidate"]["arm"] = "Baseline"
    b["job"]["spec"]["params"]["arch"] = {"model_id": "yolo11n", "backend": "ultralytics"}
    b["job"]["spec"]["params"]["candidate"]["baseline_id"] = "yolo11n"
    assert spec_from_envelope(b, tmp_path, native=True).params["arch"]["model_id"] == "yolo11n"


# -- S7-F1 GPU occupancy wait, now the runner daemon's (port of tests/owner/worker/test_gpu_occupancy.py) -------------
def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class FakeNvml:
    """NVML stand-in: lists ``stale`` for ``clear_after_s`` seconds after the first query, then ``rest``."""

    def __init__(self, stale: list[int], clear_after_s: float, rest: list[int] | None = None) -> None:
        self.stale, self.clear_after_s, self.rest = stale, clear_after_s, rest or []
        self.t0: float | None = None
        self.calls = 0

    def __call__(self, index: int) -> list[int]:
        self.calls += 1
        if self.t0 is None:
            self.t0 = time.monotonic()
        return list(self.stale if time.monotonic() - self.t0 < self.clear_after_s else self.rest)

    def listing_now(self) -> list[int]:
        return list(self.stale if self.t0 is None or time.monotonic() - self.t0 < self.clear_after_s else self.rest)


def _gpu_daemon(s, logs: list[str]) -> RunnerDaemon:
    d = RunnerDaemon(s, log=logs.append)
    d.cpu_only = False  # a GPU runner (the occupancy wait is skipped on CPU-only runners)
    return d


def test_tick_waits_for_stale_pid_before_claiming(env, monkeypatch):
    """test_gpu_occupancy.py::test_tick_waits_for_stale_pid_before_leasing, runner side: claim only after NVML no longer
    lists the dead pid."""
    stale = _dead_pid()
    fake = FakeNvml([stale], clear_after_s=0.4)
    monkeypatch.setattr(gpu, "compute_pids", fake)
    core = FakeCore([])
    logs: list[str] = []
    try:
        d = _gpu_daemon(rsettings(env, core.url, gpu_clear_timeout_s=10), logs)
        d.register()
        seen: list[tuple[list[int], float]] = []
        real = d.client.claim

        def spy(kinds):
            seen.append((fake.listing_now(), time.monotonic()))
            return real(kinds)

        d.client.claim = spy
        t0 = time.monotonic()
        assert d.tick() is False  # nothing queued
    finally:
        core.stop()
    assert seen and seen[0][0] == [] and seen[0][1] - t0 >= 0.4 - 0.05  # claimed only after the pid left NVML
    assert fake.calls >= 2 and any(str(stale) in m and "waiting up to" in m for m in logs)


def test_execute_waits_before_starting_and_spawning(env, monkeypatch):
    """test_gpu_occupancy.py::test_execute_waits_before_spawning_and_logs_to_the_run, runner side: after the claim the
    daemon waits for the device before POST /start (the wait is not billed) and before spawning the child; the wait is
    logged by the runner (see TEST_ADAPTATIONS: the run log is the core's and is written only after /start)."""
    stale = _dead_pid()
    core = FakeCore([envelope(target=1)])
    logs: list[str] = []
    try:
        d = _gpu_daemon(rsettings(env, core.url, gpu_clear_timeout_s=10), logs)
        d.register()
        claim = d.client.claim(d.kinds)
        claimed_at = time.monotonic()
        fake = FakeNvml([stale], clear_after_s=0.4)
        monkeypatch.setattr(gpu, "compute_pids", fake)
        starts: list[tuple[list[int], float]] = []
        real_start = d.client.start

        def start(fence, **kw):
            starts.append((fake.listing_now(), time.monotonic()))
            return real_start(fence, **kw)

        d.client.start = start
        spawned: list[list[int]] = []

        def spawn(argv, env, log):
            spawned.append(fake.listing_now())
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=open(log, "a", encoding="utf-8"), env=env, text=True, bufsize=1)

        d._spawn_fn = spawn
        d.execute(claim)
    finally:
        core.stop()
    assert spawned == [[]], "child spawned while NVML still listed a stale compute pid"
    assert starts and starts[0][0] == [] and starts[0][1] - claimed_at >= 0.35  # waiting is not billed
    assert any(str(stale) in m and "waiting up to" in m for m in logs)
    assert next(iter(core.results.values()))["body"]["result"]["status"] == "SUCCEEDED"


def test_wait_is_bounded_logged_and_a_live_foreign_pid_is_waited_for_once(env, monkeypatch):
    """test_gpu_occupancy.py::test_wait_is_bounded_logged_and_a_live_foreign_pid_is_waited_for_once, runner side
    (assertions unchanged): bounded, logged, a live foreign pid is remembered after a full wait, a dead one is not."""
    stale = _dead_pid()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    core = FakeCore([])
    logs: list[str] = []
    try:
        d = _gpu_daemon(rsettings(env, core.url, gpu_clear_timeout_s=0.3), logs)
        monkeypatch.setattr(gpu, "compute_pids", lambda i: [stale, live.pid])
        t0 = time.monotonic()
        out = d.wait_gpu_clear()
        assert not out["cleared"] and 0.3 <= time.monotonic() - t0 < 2 and set(out["foreign"]) == {stale, live.pid}
        text = "\n".join(logs)
        assert "waiting up to 0.3 s" in text and "giving up the wait" in text and str(stale) in text
        # the live foreign pid is remembered; the dead (stale) pid is still waited for
        monkeypatch.setattr(gpu, "compute_pids", lambda i: [live.pid])
        t1 = time.monotonic()
        assert d.wait_gpu_clear()["cleared"] and time.monotonic() - t1 < 0.2
        monkeypatch.setattr(gpu, "compute_pids", lambda i: [stale])
        t2 = time.monotonic()
        assert not d.wait_gpu_clear()["cleared"] and time.monotonic() - t2 >= 0.3
    finally:
        live.kill()
        live.wait()
        core.stop()
