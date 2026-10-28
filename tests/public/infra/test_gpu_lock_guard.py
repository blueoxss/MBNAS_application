"""S2-F10: a test marked ``gpu`` runs only while this pytest process tree holds the host GPU lock.

The set-2 iteration-1 fix pass ran 2 GPU tests without ``flock gpu.lock`` (``gpu_lock_deviation.log``), and nothing
in the test configuration stopped it. Each case below starts an inner pytest session that loads the project's real
``tests/conftest.py`` as a plugin (``-p conftest``). The session runs a tiny file with one ``gpu``-marked test, which
does no GPU work and only checks that the lock is held, and one plain test. The lock is a temporary file named by
``$PYTEST_GPU_LOCK``. Evidence type: unit.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]
INNER = '''
import fcntl, os, pytest


def _lock_is_held(path):
    fd = os.open(path, os.O_RDWR)  # a new open file description conflicts with every other holder
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


@pytest.mark.gpu
def test_gpu_marked():
    assert _lock_is_held(os.environ["PYTEST_GPU_LOCK"]), "gpu test ran without the GPU lock"


def test_plain():
    pass
'''


def _held_by_someone(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


@pytest.fixture
def inner(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    gpu: requires the host GPU\n")
    (tmp_path / "test_inner.py").write_text(INNER)
    lock = tmp_path / "gpu.lock"
    lock.touch()
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_GPU_LOCK"}
    env.update({"PYTEST_GPU_LOCK": str(lock), "PYTHONPATH": os.pathsep.join([str(TESTS), env.get("PYTHONPATH", "")])})
    cmd = [sys.executable, "-m", "pytest", "-p", "conftest", "-p", "no:cacheprovider", "-q", "-rA", "test_inner.py"]

    def run(wrapper: list[str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([*(wrapper or []), *cmd], cwd=tmp_path, env=env, capture_output=True, text=True,
                              timeout=180)

    return lock, run


def test_gpu_test_is_refused_while_another_process_holds_the_lock(inner) -> None:
    lock, run = inner
    holder = subprocess.Popen(["flock", str(lock), "sleep", "120"], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not _held_by_someone(lock):
            assert time.monotonic() < deadline, "the holder did not take the lock"
            time.sleep(0.05)
        proc = run()
    finally:
        holder.kill()
        holder.wait()
    out = proc.stdout
    assert "PASSED test_inner.py::test_plain" in out, out
    assert "PASSED test_inner.py::test_gpu_marked" not in out, f"gpu test shared the GPU lock holder's GPU:\n{out}"
    assert "ERROR test_inner.py::test_gpu_marked" in out and str(holder.pid) in out and "flock -w" in out, out
    assert proc.returncode != 0


def test_gpu_test_takes_a_free_lock_for_the_session_and_releases_it(inner) -> None:
    lock, run = inner
    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASSED test_inner.py::test_gpu_marked" in proc.stdout, proc.stdout
    assert not _held_by_someone(lock), "the session kept the lock after it ended"


@pytest.mark.parametrize("wrapper", [["flock", "-w", "30"], ["flock", "-o", "-w", "30"]], ids=["flock", "flock-o"])
def test_gpu_test_runs_under_the_flock_wrapper_without_deadlock(inner, wrapper) -> None:
    lock, run = inner
    proc = run([*wrapper, str(lock)])  # the wrapper (an ancestor) holds the lock; -o: the fd is not inherited
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASSED test_inner.py::test_gpu_marked" in proc.stdout, proc.stdout


CUDA_INNER = '''
import sys, types
import pytest

state = {"initialised": False}
sys.modules["torch"] = types.SimpleNamespace(cuda=types.SimpleNamespace(is_initialized=lambda: state["initialised"]))


def test_plain_test_that_initialises_cuda():
    state["initialised"] = True  # what torch does on the first CUDA call


def test_plain_test_after_it():
    pass


@pytest.mark.gpu
def test_gpu_test_may_initialise_cuda():
    pass
'''


def test_a_plain_test_that_initialises_cuda_is_an_error(inner, tmp_path: Path) -> None:
    """A stand-in ``torch`` module (no real CUDA) whose ``cuda.is_initialized()`` turns true during a plain test."""
    lock, run = inner
    (tmp_path / "test_inner.py").write_text(CUDA_INNER)
    proc = run()
    out = proc.stdout
    assert "ERROR test_inner.py::test_plain_test_that_initialises_cuda" in out and "not marked gpu" in out, out
    assert "PASSED test_inner.py::test_plain_test_after_it" in out, out  # CUDA was already up: not blamed again
    assert "PASSED test_inner.py::test_gpu_test_may_initialise_cuda" in out, out
