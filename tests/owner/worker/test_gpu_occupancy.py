"""S7-F1: the GPU lock / worker must cover a job's full GPU occupancy (SYNTHETIC: fake NVML, stub handlers, tmp lock).

A SIGKILLed child's CUDA context stays listed by NVML ~150 ms after the process died (set-7 live measurement).
* the worker waits (bounded, logged) before leasing and before spawning until NVML lists no compute pid other than
  its own child (fake NVML listing a stale pid);
* the worker hands the inherited GPU-lock descriptor to its child (``pass_fds``);
* ``worker.gpu_guard`` (dev_stack's ``--gpu-lock`` wrapper) keeps the lock while an orphaned descendant of a
  SIGKILLed worker is alive and while a dead pid is still listed by NVML;
* ``dev_stack.py --restart worker`` waits for the old pids to leave NVML; ``dev_stack.py ... start`` parses.
"""
from __future__ import annotations

import fcntl
import importlib.util
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import harness
import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_contracts.util.time import parse_iso, utcnow_iso
from mbnas_runner import gpu, gpu_guard
from mbnas_core.owner_worker import main as worker_main
from mbnas_core.owner_worker.main import Worker, WorkerConfig

SERVER = Path(__file__).resolve().parents[3]  # TARGET_ROOT (WP-1 layout)


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


@pytest.fixture
def run_id(client, make_dataset, tmp_roots, stub):
    ds = make_dataset("occ", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=(), baselines=("yolo11n",), targets=(1,))
    rid, _ = harness.validate_and_run(client, proto)
    return rid


def _lock_free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


# -- worker ------------------------------------------------------------------------------------------------------
# WP-2 §1.7 (WP-2V): the NVML wait before leasing/spawning moved from the owner worker to the runner daemon. The three
# NVML-wait tests that stood here were moved, assertions kept, to tests/public/runner/test_runner_daemon_unit.py
# (test_tick_waits_for_stale_pid_before_leasing, test_execute_waits_before_spawning_..., test_wait_is_bounded_...);
# the owner-side replacement (owner-mode wait_gpu_clear never calls NVML, main.py does not import mbnas_runner) is
# tests/owner/runner_protocol/test_t3_claim.py::test_owner_mode_wait_gpu_clear_never_calls_nvml.


def test_own_child_and_own_process_are_not_waited_for(api_settings):
    w = Worker(api_settings, WorkerConfig(gpu_clear_timeout_s=5), worker_id="w-own:1")
    try:
        w._child_pid = 4_000_001
        w.gpu_pids_fn = lambda i: [4_000_001, os.getpid(), os.getppid()]
        t0 = time.monotonic()
        out = w.wait_gpu_clear()
        assert out["cleared"] and time.monotonic() - t0 < 0.5
    finally:
        w.conn.close()


def test_child_inherits_the_gpu_lock_descriptor(api_settings, tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o664)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.set_inheritable(fd, True)
    monkeypatch.setenv(worker_main.LOCK_FD_ENV, str(fd))
    captured: dict = {}

    class _P:
        pid = 1

    def popen(argv, **kw):
        captured.update(kw)
        return _P()

    monkeypatch.setattr(worker_main.subprocess, "Popen", popen)
    w = Worker(api_settings, WorkerConfig(), worker_id="w-fd:1")
    try:
        w._spawn({"id": "job-x"}, tmp_path / "stop", tmp_path / "log")
    finally:
        w.conn.close()
        os.close(fd)
    assert captured["pass_fds"] == (fd,) and captured["close_fds"] is True
    monkeypatch.setenv(worker_main.LOCK_FD_ENV, "987")  # not open in this process: nothing passed
    assert worker_main._lock_fds() == ()


# -- guardian ----------------------------------------------------------------------------------------------------
def test_guardian_waits_for_dead_or_own_pids_in_nvml(capsys):
    dead = _dead_pid()
    live_foreign = os.getppid()
    fake = FakeNvml([dead, live_foreign], clear_after_s=0.3, rest=[live_foreign])
    t0 = time.monotonic()
    out = gpu_guard.wait_stale_clear(0, set(), timeout_s=5, pids_fn=fake)
    assert out["cleared"] and time.monotonic() - t0 >= 0.3  # waited for the dead pid, not for the live foreign one
    assert gpu_guard.stale_pids(0, {live_foreign}, pids_fn=lambda i: [live_foreign]) == [live_foreign]
    assert gpu_guard.stale_pids(0, set(), pids_fn=lambda i: None) is None
    assert "waiting up to 5.0 s" in capsys.readouterr().err


WORKER_SCRIPT = textwrap.dedent('''
    import os, subprocess, sys, time
    fd = int(os.environ["MBNAS_GPU_LOCK_FD"])
    with open(sys.argv[1], "w") as fh:
        fh.write(os.readlink(f"/proc/self/fd/{fd}") + "\\n")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2.0)"], pass_fds=(fd,))
        fh.write(str(child.pid) + "\\n")
    time.sleep(60)
''')


def test_guardian_holds_the_lock_until_orphans_are_gone(tmp_path):
    lock, info, log = tmp_path / "gpu.lock", tmp_path / "info.txt", tmp_path / "guard.log"
    script = tmp_path / "fake_worker.py"
    script.write_text(WORKER_SCRIPT)
    guard = subprocess.Popen([sys.executable, "-m", "mbnas_runner.gpu_guard", "--lock", str(lock), "--wait-s",
                              "10", "--clear-timeout-s", "20", "--", sys.executable, str(script), str(info)],
                             stderr=open(log, "w"), start_new_session=True)
    try:
        harness.wait_for(lambda: info.is_file() and len(info.read_text().splitlines()) == 2, timeout=20)
        locked_path, grandchild = info.read_text().splitlines()
        assert locked_path == str(lock) and not _lock_free(lock)
        worker_pid = next(int(p) for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == guard.pid)
        os.kill(worker_pid, signal.SIGKILL)
        killed_at = time.monotonic()
        time.sleep(0.5)
        assert gpu.proc_state(int(grandchild)) not in (None, "Z")  # the orphan still runs (reparented to the guard)
        assert not _lock_free(lock), "lock free while a descendant of the killed worker is still alive"
        assert guard.wait(timeout=30) == 128 + signal.SIGKILL
        released_at = time.monotonic()
        assert gpu.proc_state(int(grandchild)) is None and _lock_free(lock)
        assert released_at - killed_at >= 1.0  # held until the 2 s orphan finished, not until the worker died
    finally:
        if guard.poll() is None:
            os.killpg(guard.pid, signal.SIGKILL)
            guard.wait()
    text = log.read_text()
    assert f"reaped orphaned descendant {grandchild}" in text and "releasing" in text


def _ppid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[1])
    except (OSError, ValueError, IndexError):
        return -1


def test_guardian_times_out_like_flock_w(tmp_path):
    lock = tmp_path / "gpu.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o664)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rc = subprocess.run([sys.executable, "-m", "mbnas_runner.gpu_guard", "--lock", str(lock), "--wait-s",
                             "0.5", "--", sys.executable, "-c", "raise SystemExit(7)"], capture_output=True, text=True,
                            timeout=30)
    finally:
        os.close(fd)
    assert rc.returncode == 1 and "timed out" in rc.stderr
    ok = subprocess.run([sys.executable, "-m", "mbnas_runner.gpu_guard", "--lock", str(lock), "--wait-s", "5",
                         "--", sys.executable, "-c", "raise SystemExit(7)"], capture_output=True, text=True, timeout=30)
    assert ok.returncode == 7 and "acquired" in ok.stderr and "releasing" in ok.stderr


# -- dev_stack ---------------------------------------------------------------------------------------------------
@pytest.fixture
def dev_stack():
    spec = importlib.util.spec_from_file_location("dev_stack_under_test", SERVER / "scripts" / "dev" / "dev_stack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dev_stack_accepts_optional_start_and_runs_the_worker_under_the_guardian(dev_stack):
    p = dev_stack.build_parser()
    args = p.parse_args(["--env-file", "/x/.env", "--gpu-lock", "/x/gpu.lock", "start"])
    assert args.action == "start" and args.gpu_lock == "/x/gpu.lock"
    assert p.parse_args(["--gpu-lock", "/x/gpu.lock"]).action is None
    with pytest.raises(SystemExit):
        p.parse_args(["stop"])
    with pytest.raises(SystemExit):
        dev_stack.main(["--stop", "start"])
    assert "[--gpu-lock /abs/gpu.lock] [start]" in dev_stack.__doc__

    class S:
        worker_python = "/py"
    argv = dev_stack._argv("worker", S(), p.parse_args(["--gpu-lock", "/x/gpu.lock", "--gpu-index", "0"]))
    assert argv[:3] == ["/py", "-m", "mbnas_runner.gpu_guard"] and "--lock" in argv and "/x/gpu.lock" in argv
    assert argv[argv.index("--") + 1:] == ["/py", "-m", "mbnas_core.owner_worker", "--gpu-index", "0"]


def test_dev_stack_restart_waits_for_old_pids_to_leave_nvml(dev_stack, capsys):
    fake = FakeNvml([111, 222], clear_after_s=0.3, rest=[222])
    t0 = time.monotonic()
    out = dev_stack.wait_pids_leave_nvml({111, 333}, 0, timeout_s=5, pids_fn=fake)
    assert out["cleared"] and time.monotonic() - t0 >= 0.3  # 222 is not an old pid: not waited for
    assert "[111]" in capsys.readouterr().err
    out = dev_stack.wait_pids_leave_nvml({111}, 0, timeout_s=0.2, pids_fn=lambda i: [111])
    assert not out["cleared"] and out["foreign"] == [111]
