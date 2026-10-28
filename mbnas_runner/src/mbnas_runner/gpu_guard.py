"""GPU-lock guardian: ``python -m mbnas_runner.gpu_guard --lock <path> [--gpu-index N] -- <worker argv>``.

Replaces ``flock -w 3600 <lock> <worker>`` in ``scripts/dev_stack.py`` (S7-F1). ``flock(1)`` releases the lock the
moment the worker exits; a SIGKILLed worker's training child dies with it (PDEATHSIG) but NVML keeps listing the
child's CUDA context for ~150 ms more, so another lock user could start on a device that is still being freed.
The guardian holds the lock for the job's full GPU occupancy:

1. take ``flock(LOCK_EX)`` on ``--lock`` (wait up to ``--wait-s``, like ``flock -w``; exit 1 on timeout);
2. wait (bounded, logged) until NVML lists no *dead or zombie* compute pid (a context left by the previous holder);
3. become a child subreaper and run the worker with the lock descriptor inherited (``MBNAS_GPU_LOCK_FD``; the
   worker passes it on to its training child), forwarding SIGTERM/SIGINT/SIGHUP to the worker;
4. after the worker exits, reap every orphaned descendant (the training child is reparented to the guardian;
   SIGKILL after ``--clear-timeout-s``), then wait (bounded, logged) until none of their pids — and no dead or
   zombie pid — is listed by NVML; only then close the lock and exit with the worker's status.

A live compute process that is not a descendant (a non-lock user of the GPU) is reported but never waited for.
Every step is logged to stderr with a UTC timestamp (``<log_dir>/dev_stack/worker.log`` under dev_stack).
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import os
import signal
import subprocess
import sys
import time
from typing import Any

from mbnas_contracts.util.time import utcnow_iso
from mbnas_runner import gpu

LOCK_FD_ENV = "MBNAS_GPU_LOCK_FD"
LOCK_PATH_ENV = "MBNAS_GPU_LOCK_PATH"
_PR_SET_CHILD_SUBREAPER = 36


def _log(msg: str) -> None:
    print(f"{utcnow_iso()} gpu_guard[{os.getpid()}]: {msg}", file=sys.stderr, flush=True)


def acquire(path: str, wait_s: float, poll_s: float = 0.2) -> int | None:
    """Open ``path`` and take an exclusive flock within ``wait_s``; the descriptor, or None on timeout."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    deadline = time.monotonic() + wait_s
    logged = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            if not logged:
                _log(f"waiting up to {wait_s:.0f} s for {path}")
                logged = True
            time.sleep(poll_s)


def stale_pids(index: int, own: set[int] | None = None, pids_fn: Any = None) -> list[int] | None:
    """NVML compute pids that are dead, zombie or in ``own`` (descendants of this guardian); None without NVML."""
    listed = (pids_fn or gpu.compute_pids)(index)
    if listed is None:
        return None
    return [p for p in listed if p in (own or set()) or gpu.proc_state(p) in (None, "Z")]


def wait_stale_clear(index: int, own: set[int], timeout_s: float, pids_fn: Any = None) -> dict[str, Any]:
    return gpu.wait_clear(index, timeout_s=timeout_s, pids_fn=lambda i: stale_pids(i, own, pids_fn), log=_log)


def _children(pid: int) -> set[int]:
    """Direct children of ``pid`` (from /proc; includes zombies)."""
    out: set[int] = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as fh:
                if int(fh.read().rsplit(")", 1)[-1].split()[1]) == pid:
                    out.add(int(name))
        except (OSError, ValueError, IndexError):
            continue
    return out


def descendants(pid: int) -> set[int]:
    seen: set[int] = set()
    todo = [pid]
    while todo:
        for c in _children(todo.pop()):
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return seen


def reap_orphans(timeout_s: float) -> set[int]:
    """Reap every remaining child of this (subreaper) process; SIGKILL survivors after ``timeout_s``."""
    reaped: set[int] = set()
    deadline = time.monotonic() + timeout_s
    killed = False
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return reaped
        if pid:
            reaped.add(pid)
            _log(f"reaped orphaned descendant {pid}")
            continue
        if not killed and time.monotonic() >= deadline:
            for c in _children(os.getpid()):
                _log(f"descendant {c} still alive {timeout_s:.0f} s after the worker exited: SIGKILL")
                try:
                    os.kill(c, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            killed = True
        time.sleep(0.02)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--wait-s", type=float, default=3600.0, help="like flock -w (exit 1 on timeout)")
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--clear-timeout-s", type=float, default=60.0,
                        help="bound for reaping descendants and for their contexts to leave NVML")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        parser.error("missing worker command after --")
    lock = os.path.abspath(args.lock)
    fd = acquire(lock, args.wait_s)
    if fd is None:
        _log(f"timed out after {args.wait_s:.0f} s waiting for {lock}")
        return 1
    _log(f"lock {lock} acquired (fd {fd})")
    wait_stale_clear(args.gpu_index, set(), args.clear_timeout_s)
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_CHILD_SUBREAPER, 1)
    except OSError:
        pass
    os.set_inheritable(fd, True)
    env = {**os.environ, LOCK_FD_ENV: str(fd), LOCK_PATH_ENV: lock}
    proc = subprocess.Popen(cmd, env=env, pass_fds=(fd,))
    _log(f"worker started pid {proc.pid}: {' '.join(cmd)}")
    tree: set[int] = set()

    def _forward(signum: int, _frame: Any) -> None:
        if proc.poll() is None:
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _forward)
    while True:  # remember the worker's descendants (its training child) while they exist
        tree |= descendants(proc.pid)
        try:
            rc = proc.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            continue
    tree |= _children(os.getpid())  # orphans already reparented to us
    _log(f"worker {proc.pid} exited with {rc}; descendants seen {sorted(tree)}")
    tree |= reap_orphans(args.clear_timeout_s)
    out = wait_stale_clear(args.gpu_index, tree | {proc.pid}, args.clear_timeout_s)
    _log(f"device clear={out['cleared']} after {out['waited_s']:.3f} s; releasing {lock}")
    os.close(fd)
    return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    sys.exit(main())
