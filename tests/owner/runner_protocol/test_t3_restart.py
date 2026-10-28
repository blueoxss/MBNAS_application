"""T3-19 / T3-20 (design A3 sections 5.5, 6.2, F3/F4): runner restart replay.

* T3-19 (test_subprocess.py:87/kill-9 port): a real ``mbnas-runner run`` process is SIGKILLed mid-job (its child dies
  with it, PDEATHSIG); the core app is served by an in-process uvicorn on an ephemeral 127.0.0.1 port so the runner
  process talks real HTTP. A new runner daemon with the same work root replays the journal: started-without-result ->
  FAILED/CHILD_EXIT_NO_RESULT (retryable) -> attempt 2 resumes from the registered checkpoint.
* T3-20: the result commit reached the core but its response did not reach the runner (or the request never left);
  a new daemon replays result.json with the same digest, gets the stored or first answer, acks once.
SYNTHETIC stub handlers throughout."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import harness
import httpx
import pytest
import runner_sim as rs

from mbnas_core.db import repo

pytestmark = [pytest.mark.runner_protocol, pytest.mark.slow]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as fh:
        return fh.read().split(")")[-1].split()[0] != "Z"


def _children(pid: int) -> list[int]:
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                if int(Path(f"/proc/{p}/stat").read_text().rsplit(")", 1)[-1].split()[1]) == pid:
                    out.append(int(p))
            except (OSError, ValueError, IndexError):
                pass
    return out


@pytest.fixture
def live_core(core_app, core):
    """The same core app served by uvicorn on an ephemeral loopback port (started and stopped by this fixture)."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(core_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    harness.wait_for(lambda: server.started, timeout=20)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=20)


def test_runner_restart_without_result_reports_child_exit_no_result_and_resumes(live_core, core, tokens, daemon_factory,
                                                                              base_proto, db, tmp_path):
    hang = tmp_path / "hang"
    hang.write_text("1")
    run_id, _ = harness.validate_and_run(core, base_proto())
    work = tmp_path / "daemon-R1"  # the work root daemon_factory("R1") uses: the restarted daemon sees this journal
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_") or k.startswith("MBNAS_TEST_")}
    env.update(MBNAS_RUNNER_CORE_URL=live_core, MBNAS_RUNNER_TOKEN=tokens["R1"], MBNAS_RUNNER_RUNNER_ID="R1",
               MBNAS_RUNNER_WORK_ROOT=str(work), MBNAS_RUNNER_OUTPUT_ROOTS=str(work),
               MBNAS_RUNNER_DATASET_ROOTS=str(work / "data"), MBNAS_RUNNER_LOG_DIR=str(work / "logs"),
               MBNAS_RUNNER_WEIGHTS_DIR=str(work / "weights"), MBNAS_RUNNER_POLL_S="0.1",
               MBNAS_RUNNER_KILL_GRACE_S="2", MBNAS_HOME=str(tmp_path / "home"), CUDA_VISIBLE_DEVICES="",
               PYTHONPATH=str(harness.HERE), MBNAS_JOB_HANDLERS_MODULE="stub_handlers", MBNAS_STUB_EPOCH_S="0.05",
               MBNAS_STUB_HANG_FILE=str(hang), PYTHONDONTWRITEBYTECODE="1")
    first = subprocess.Popen([sys.executable, "-B", "-m", "mbnas_runner", "run"], env=env,
                             stdout=open(tmp_path / "r1.log", "a"), stderr=subprocess.STDOUT)
    try:
        harness.wait_for(lambda: repo.list_checkpoints(db, run_id), timeout=60)
        child = harness.wait_for(lambda: _children(first.pid), timeout=10)[0]
        assert _alive(child)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        harness.wait_for(lambda: not _alive(child), timeout=10)  # PDEATHSIG: no orphaned job child
    finally:
        if first.poll() is None:
            first.kill()
    hang.unlink()
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "RUNNING" and job["attempt"] == 1 and job["lease_owner"] == "R1"
    d = daemon_factory("R1")
    assert Path(d.settings.work_root) == work
    d.run(max_jobs=1)
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "SUCCEEDED" and job["attempt"] == 2 and job["result"]["exit_code"] == 0
    ledger = repo.list_costs(db, run_id)
    assert [(r["attempt"], r["kind"], r["meta"]["reason_code"]) for r in ledger] == [
        (1, "failed", "CHILD_EXIT_NO_RESULT"), (2, "retry", None)]
    cks = repo.list_checkpoints(db, run_id)
    assert [c["epoch"] for c in cks if c["kind"] == "training_full"] == [1, 2]  # epoch 1 not trained twice
    ev = [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    assert len(ev) == 1 and ev[0]["row"]["raw_metrics"]["child_env"]["resumed_from_epoch"] == 1
    assert repo.get_run(db, run_id)["status"] == "COMPLETED"


class _Drop(httpx.BaseTransport):
    """Transport wrapper: requests whose path ends with a suffix in ``rules`` are lost; ``before`` = never delivered,
    ``after`` = delivered and answered by the core, but the answer is lost on the way back."""

    def __init__(self, inner: httpx.BaseTransport, rules: dict[str, str]) -> None:
        self.inner, self.rules = inner, rules

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        mode = next((m for suffix, m in self.rules.items() if request.url.path.endswith(suffix)), None)
        if mode == "before":
            raise httpx.ConnectError("synthetic: request lost", request=request)
        resp = self.inner.handle_request(request)
        if mode == "after":
            resp.read()
            raise httpx.ReadError("synthetic: response lost", request=request)
        return resp


@pytest.mark.parametrize("lost", ["after", "before"])
def test_runner_journal_replays_result_then_ack(core, tokens, base_proto, db, tmp_path, stub, lost):
    """T3-20 / F4 (+F1): ``after`` = committed but the answer was lost; ``before`` = the commit never arrived. Either
    way a restarted daemon resends the frozen result.json (same digest), the job is committed exactly once and acked."""
    from mbnas_runner.client import CoreClient
    from mbnas_runner.daemon import RunnerDaemon
    from mbnas_runner.journal import AttemptJournal, Stage

    run_id, _ = harness.validate_and_run(core, base_proto())
    settings = rs.daemon_settings(tmp_path / "w", tokens["R1"], "R1", request_retries=0)
    flaky = CoreClient(settings, transport=_Drop(core._transport, {"/result": lost, "/ack": "before"}))
    first = RunnerDaemon(settings, client=flaky)
    first.run(once=True)
    job = repo.list_jobs(db, run_id)[0]
    assert [p.name for p in (tmp_path / "w" / "journal" / job["id"]).iterdir()] == ["a1"]
    j = AttemptJournal.create(settings.work_root, job["id"], 1)
    assert j.stage == Stage.RESULT, j.stage
    frozen = j.result["payload_digest"]
    assert job["status"] == ("SUCCEEDED" if lost == "after" else "RUNNING")
    second = rs.make_daemon(core, settings)
    second.register()
    done = second.replay()
    assert done == [(job["id"], 1, "closed")]
    j2 = AttemptJournal.create(settings.work_root, job["id"], 1)
    assert j2.acked and j2.result["payload_digest"] == frozen and j2.response["outcome"] == "SUCCEEDED"
    assert repo.get_job(db, job["id"])["status"] == "SUCCEEDED"
    assert [(c["kind"], c["meta"]["outcome"]) for c in repo.list_costs(db, run_id)] == [("train", "succeeded")]
    assert len([e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]) == 1
    row = db.execute("SELECT payload_digest, acked_at, state FROM idempotent_responses WHERE job_id = ?",
                     (job["id"],)).fetchone()
    assert row["state"] == "DONE" and row["acked_at"] and row["payload_digest"] == frozen
