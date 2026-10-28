"""GPU lock guard and CUDA-initialisation check for the owner and public test suites (A12, WP-1 contract §9).

Moved unchanged from the old root ``tests/conftest.py`` (fix pass S2-F10). A test marked ``gpu`` runs only while this
pytest process tree holds the host GPU lock: either the run is wrapped in ``flock -w 3600 <lock> pytest ...`` (the lock
is on an inherited fd, or is held by an ancestor process), or the first gpu test takes the free lock non-blockingly and
keeps it until the session ends. While another process holds the lock, every gpu test errors at setup instead of
sharing the GPU. A test that initialises CUDA in-process without the ``gpu`` marker errors too, because both
``-m "not gpu"`` and this guard would miss it.

Lock path: ``$PYTEST_GPU_LOCK`` (not ``MBNAS_*``, which ``clean_env`` removes). WP-1 default:
``$MBNAS_TEST_ROOT/locks/gpu.lock`` (``tests/_shared/conftest_common.py`` sets ``MBNAS_TEST_ROOT`` and creates the
``locks`` directory). This module is a self-contained pytest plugin: both root conftests (``tests/owner``,
``tests/public``) re-export its hooks, and ``tests/public/infra/test_gpu_lock_guard.py`` loads it alone with
``-p gpu_guard``.
"""
from __future__ import annotations

import fcntl as _fcntl
import os
import sys as _sys
from pathlib import Path

import pytest

_DEFAULT_TEST_ROOT = os.environ.get("MBNAS_TEST_ROOT") or str(Path(__file__).resolve().parents[2] / "runtime" / "test_root")
GPU_LOCK_PATH = os.environ.get("PYTEST_GPU_LOCK", f"{_DEFAULT_TEST_ROOT}/locks/gpu.lock")


def _ancestor_pids() -> list[int]:
    pids, pid = [], os.getpid()
    while pid > 0 and pid not in pids:
        pids.append(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return pids


def gpu_lock_holders(path: str) -> list[int]:
    """pids holding a flock(2) lock on ``path``, read from /proc/locks (blocked waiters, ``->`` rows, excluded)."""
    try:
        st, rows = os.stat(path), Path("/proc/locks").read_text().splitlines()
    except OSError:
        return []
    out = []
    for row in rows:
        f = row.split()
        if len(f) >= 6 and f[1] == "FLOCK":
            major, minor, ino = f[5].split(":")
            if int(ino) == st.st_ino and (int(major, 16), int(minor, 16)) == (os.major(st.st_dev), os.minor(st.st_dev)):
                out.append(int(f[4]))
    return out


class _GpuLockGuard:
    def __init__(self, path: str) -> None:
        self.path, self.fd, self.how = path, None, None

    def _inherited(self) -> str | None:
        real = os.path.realpath(self.path)
        for name in os.listdir("/proc/self/fd"):
            try:
                if os.readlink(f"/proc/self/fd/{name}") != real:
                    continue
                _fcntl.flock(int(name), _fcntl.LOCK_EX | _fcntl.LOCK_NB)  # no-op when this description holds it
            except OSError:
                continue
            return f"inherited fd {name}"
        return None

    def ensure(self) -> str:
        if self.how:
            return self.how
        holders = gpu_lock_holders(self.path)
        ancestors = [p for p in holders if p in _ancestor_pids()]
        self.how = self._inherited() or (f"held by ancestor pid {ancestors[0]}" if ancestors else None)
        if self.how:
            return self.how
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o664)
        except OSError as exc:
            raise RuntimeError(f"cannot open the GPU lock {self.path}: {exc}; set PYTEST_GPU_LOCK") from None
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            who = []
            for pid in gpu_lock_holders(self.path) or holders:
                try:
                    cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                except OSError:
                    cmd = "?"
                who.append(f"pid {pid} ({cmd.strip()[:120]})")
            raise RuntimeError(f"GPU lock {self.path} is held by {', '.join(who) or 'another process'}; a gpu test "
                               f"must not share the GPU. Wait, or run: flock -w 3600 {self.path} "
                               f"{_sys.executable} -m pytest ...") from None
        self.fd, self.how = fd, f"taken by this pytest session (pid {os.getpid()})"
        return self.how

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)  # closing the only descriptor releases the flock
        self.fd, self.how = None, None


_GPU_GUARD = _GpuLockGuard(GPU_LOCK_PATH)
_CUDA_BEFORE = pytest.StashKey[bool]()


def _cuda_initialised() -> bool:
    torch = _sys.modules.get("torch")
    return bool(torch is not None and torch.cuda.is_initialized())


def pytest_runtest_setup(item: pytest.Item) -> None:  # runs after skipif/skip markers, before fixtures
    item.stash[_CUDA_BEFORE] = _cuda_initialised()
    if item.get_closest_marker("gpu") is not None:
        try:
            _GPU_GUARD.ensure()
        except RuntimeError as exc:
            pytest.fail(str(exc), pytrace=False)


@pytest.hookimpl(trylast=True)  # after the runner has torn the fixtures down, so a failure here leaks nothing
def pytest_runtest_teardown(item: pytest.Item) -> None:
    if item.get_closest_marker("gpu") is None and not item.stash.get(_CUDA_BEFORE, True) and _cuda_initialised():
        pytest.fail(f"{item.nodeid} initialised CUDA but is not marked gpu, so it ran GPU work outside the GPU-lock "
                    "guard; add @pytest.mark.gpu", pytrace=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    _GPU_GUARD.release()
