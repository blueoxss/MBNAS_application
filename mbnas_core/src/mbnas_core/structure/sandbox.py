"""Subprocess sandbox of the explicit structure checks.

A check builds an untrusted model description with torch/ultralytics; it never runs inside the API process. The
parent (:class:`StructureSandbox`) starts

    [unshare -rn] <python> -I -B -m mbnas_core.structure.check_worker

with ``start_new_session=True`` (setsid: its own process group), a minimal environment (``PATH``, a private
``HOME``/``TMPDIR``/``YOLO_CONFIG_DIR`` temp directory, ``ULTRALYTICS_SAFE_LOAD=1``, ``YOLO_AUTOINSTALL=false``,
``YOLO_OFFLINE=1``, ``CUDA_VISIBLE_DEVICES=""``; no ``MBNAS_*`` variable, so no token/key ever reaches it) and the
temp directory as cwd. It writes one JSON request on stdin: ``{"op", "payload", "limits", "parent_pid"}``. The worker
applies the limits *before* it parses the payload or imports a model runtime: ``RLIMIT_CPU``/``AS``/``NOFILE``/
``FSIZE``/``CORE`` from :class:`~mbnas_core.config.CoreSettings` and ``PR_SET_PDEATHSIG=SIGKILL`` (exiting at once if
the parent already died). Doing this in the worker instead of a ``preexec_fn`` keeps ``fork`` safe in the threaded
API process; nothing untrusted is read before the limits hold.

The parent enforces the wall timeout (``killpg(SIGKILL)`` of the whole group), reads at most
``structure_check_max_output_bytes`` (1 MiB) of stdout and maps every failure to a code:

* ``STRUCTURE_CHECK_TIMEOUT`` - the wall timeout expired;
* ``STRUCTURE_CHECK_RESOURCE_LIMIT`` - CPU limit signal (``SIGXCPU``), an allocation refused under ``RLIMIT_AS``
  (reported by the worker), a file-size signal (``SIGXFSZ``) or a result larger than the output cap;
* ``STRUCTURE_RUNTIME_UNAVAILABLE`` - the worker could not start, crashed, or printed no valid result.

Only one sandboxed check runs at a time in a process (``BoundedSemaphore(1)``); a caller that cannot get it within
``structure_check_queue_timeout_s`` gets :class:`SandboxBusy`. ``unshare -rn`` (new user + network namespace: no
network) is used when ``structure_check_unshare`` is true and the host allows it; the isolation level actually used
is returned with every result (``sandbox.isolation``).

The sandbox is bound per request by the console router dependency (:func:`bind`); code that runs without a bound
sandbox (the worker itself, the PROPOSE_GENERATION child, library callers) checks in-process.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("mbnas_core.structure.sandbox")

WORKER_MODULE = "mbnas_core.structure.check_worker"
TIMEOUT = "STRUCTURE_CHECK_TIMEOUT"
RUNTIME_UNAVAILABLE = "STRUCTURE_RUNTIME_UNAVAILABLE"
RESOURCE_LIMIT = "STRUCTURE_CHECK_RESOURCE_LIMIT"
FAILURE_CODES = (TIMEOUT, RUNTIME_UNAVAILABLE, RESOURCE_LIMIT)
SAFE_ENV = {"ULTRALYTICS_SAFE_LOAD": "1", "YOLO_AUTOINSTALL": "false", "YOLO_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": ""}
_SEMAPHORE = threading.BoundedSemaphore(1)
_current: ContextVar["StructureSandbox | None"] = ContextVar("mbnas_structure_sandbox", default=None)
_unshare_ok: bool | None = None


class SandboxFailure(Exception):
    """The sandboxed check did not produce a result; ``code`` is one of :data:`FAILURE_CODES`."""

    def __init__(self, code: str, cause: str, sandbox: dict[str, Any]) -> None:
        super().__init__(cause)
        self.code, self.cause, self.sandbox = code, cause, sandbox


class SandboxBusy(Exception):
    """Another sandboxed check holds the global semaphore for longer than the queue timeout."""


class SandboxRequestError(Exception):
    """The worker rejected the request the way the in-process service would (``StructureRequestError`` fields)."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Limits:
    cpu_s: int
    as_bytes: int
    nofile: int
    fsize_bytes: int
    wall_s: float
    queue_s: float
    max_output_bytes: int
    unshare: bool

    def worker_view(self) -> dict[str, int]:
        return {"cpu_s": self.cpu_s, "as_bytes": self.as_bytes, "nofile": self.nofile, "fsize_bytes": self.fsize_bytes,
                "core": 0}


def bind(sandbox: "StructureSandbox | None") -> None:
    """Bind the sandbox of the current request (context variable)."""
    _current.set(sandbox)


def active() -> "StructureSandbox | None":
    return _current.get()


def for_settings(settings: Any) -> "StructureSandbox | None":
    """The sandbox described by ``settings`` (None when ``structure_sandbox`` is false)."""
    if not getattr(settings, "structure_sandbox", False):
        return None
    return StructureSandbox(Limits(
        cpu_s=int(settings.structure_check_cpu_s), as_bytes=int(settings.structure_check_as_bytes),
        nofile=int(settings.structure_check_nofile), fsize_bytes=int(settings.structure_check_fsize_bytes),
        wall_s=float(settings.structure_check_timeout_s), queue_s=float(settings.structure_check_queue_timeout_s),
        max_output_bytes=int(settings.structure_check_max_output_bytes), unshare=bool(settings.structure_check_unshare)))


def unshare_available() -> bool:
    """``unshare -rn true`` succeeds on this host (probed once per process)."""
    global _unshare_ok
    if _unshare_ok is None:
        exe = shutil.which("unshare")
        try:
            _unshare_ok = bool(exe) and subprocess.run([exe, "-rn", "true"], stdin=subprocess.DEVNULL,  # type: ignore[list-item]
                                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                       timeout=10, env={"PATH": "/usr/bin:/bin"}).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _unshare_ok = False
    return bool(_unshare_ok)


def worker_env(tmp: str) -> dict[str, str]:
    """Minimal worker environment: no inherited variable except ``PATH`` (never an ``MBNAS_*`` secret)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "TMPDIR": tmp, "YOLO_CONFIG_DIR": tmp,
            "MPLCONFIGDIR": tmp, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "4", **SAFE_ENV}


class StructureSandbox:
    """Parent side of one sandbox configuration (see module docstring)."""

    def __init__(self, limits: Limits, python: str | None = None) -> None:
        self.limits = limits
        self.python = python or sys.executable

    def argv(self) -> tuple[list[str], str]:
        base = [self.python, "-I", "-B", "-m", WORKER_MODULE]
        if self.limits.unshare and unshare_available():
            return [shutil.which("unshare") or "unshare", "-rn", *base], "unshare-rn+setsid+rlimits"
        return base, "setsid+rlimits"

    def run(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run ``op`` in the worker; returns its result document with ``sandbox`` metadata attached.

        Raises :class:`SandboxBusy`, :class:`SandboxRequestError` or :class:`SandboxFailure`.
        """
        if not _SEMAPHORE.acquire(timeout=self.limits.queue_s):
            raise SandboxBusy("another structure check is running; retry later")
        try:
            return self._run(op, payload)
        finally:
            _SEMAPHORE.release()

    def _run(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        argv, isolation = self.argv()
        meta: dict[str, Any] = {"isolation": isolation, "limits": self.limits.worker_view(),
                                "wall_timeout_s": self.limits.wall_s}
        request = json.dumps({"op": op, "payload": payload, "limits": self.limits.worker_view(),
                              "parent_pid": os.getpid()}).encode("utf-8")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="mbnas-structure-") as tmp:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=tmp, env=worker_env(tmp), start_new_session=True, close_fds=True)
            except OSError as exc:
                raise SandboxFailure(RUNTIME_UNAVAILABLE, f"sandbox worker could not start ({type(exc).__name__})", meta) from exc
            out = bytearray()
            err = bytearray()
            overflow = threading.Event()

            def read_out() -> None:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        return
                    if len(out) + len(chunk) > self.limits.max_output_bytes:
                        overflow.set()
                        _kill_group(proc)
                        return
                    out.extend(chunk)

            def read_err() -> None:
                assert proc.stderr is not None
                while True:
                    chunk = proc.stderr.read(65536)
                    if not chunk:
                        return
                    err.extend(chunk)
                    del err[:-16384]

            readers = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
            for t in readers:
                t.start()
            try:
                assert proc.stdin is not None
                proc.stdin.write(request)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            timed_out = False
            try:
                proc.wait(timeout=self.limits.wall_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                proc.wait()
            _kill_group(proc)  # anything the worker left in its group
            for t in readers:
                t.join(timeout=5)
        meta.update(elapsed_s=round(time.monotonic() - started, 3), exit_code=proc.returncode)
        if timed_out:
            raise SandboxFailure(TIMEOUT, f"structure check exceeded the {self.limits.wall_s:g} s wall limit", meta)
        if overflow.is_set():
            raise SandboxFailure(RESOURCE_LIMIT, f"structure check result exceeded {self.limits.max_output_bytes} bytes", meta)
        rc = proc.returncode
        if rc is not None and rc < 0:
            sig = -rc
            name = signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else str(sig)
            code = RESOURCE_LIMIT if sig in (signal.SIGXCPU, signal.SIGXFSZ) else RUNTIME_UNAVAILABLE
            log.warning("structure sandbox worker killed by %s; stderr tail: %s", name, bytes(err[-2000:]).decode("utf-8", "replace"))
            raise SandboxFailure(code, f"structure check worker terminated by {name}", meta)
        try:
            reply = json.loads(bytes(out).decode("utf-8"))
            if not isinstance(reply, dict):
                raise ValueError("reply is not an object")
        except (UnicodeDecodeError, ValueError):
            log.warning("structure sandbox worker exit %s without a valid reply; stderr tail: %s", rc,
                        bytes(err[-2000:]).decode("utf-8", "replace"))
            raise SandboxFailure(RUNTIME_UNAVAILABLE, f"structure check worker exited ({rc}) without a result", meta)
        if reply.get("ok") is True and isinstance(reply.get("result"), dict):
            doc = reply["result"]
            doc["sandbox"] = meta
            return doc
        if isinstance(reply.get("request_error"), dict):
            e = reply["request_error"]
            raise SandboxRequestError(str(e.get("code")), str(e.get("message")), int(e.get("status") or 422))
        failure = reply.get("failure") if isinstance(reply.get("failure"), dict) else {}
        code = failure.get("code") if failure.get("code") in FAILURE_CODES else RUNTIME_UNAVAILABLE
        raise SandboxFailure(code, str(failure.get("cause") or "structure check worker failed")[:300], meta)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
