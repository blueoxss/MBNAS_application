"""Sandbox worker of the explicit structure checks: ``python -I -B -m mbnas_core.structure.check_worker``.

Protocol (see :mod:`mbnas_core.structure.sandbox`): one JSON object on stdin ``{"op", "payload", "limits",
"parent_pid"}``; one JSON object on stdout:

* ``{"ok": true, "result": <StructureReport document>}``
* ``{"ok": false, "request_error": {"code", "message", "status"}}`` - the same request error the in-process service
  raises (e.g. ``MODEL_NOT_FOUND``);
* ``{"ok": false, "failure": {"code", "cause"}}`` - ``STRUCTURE_CHECK_RESOURCE_LIMIT`` (an allocation refused under
  ``RLIMIT_AS``) or ``STRUCTURE_RUNTIME_UNAVAILABLE``.

The limits are applied first (rlimits, ``PR_SET_PDEATHSIG``), before the payload is interpreted or torch/ultralytics
is imported. Operations (all CPU, all through the unchanged in-process services of
:mod:`mbnas_core.console.model_services`):

* ``check_structure`` ``{genotype, input_size, class_count}`` -> ``model_services.check_genotype_inprocess``
* ``native_structure`` ``{arch, nc, input_size}`` -> ``model_services.native_structure_for_arch``
* ``registry_yaml`` ``{model_id, input_size}`` -> ``model_services.check_registry_yaml_safe``

An allocation failure inside the model build is re-raised past the service's own ``except Exception`` boundary
(:class:`_ResourceLimit` is a ``BaseException``) so it is reported as a resource-limit failure instead of a generic
"check did not complete" document.
"""
from __future__ import annotations

import json
import os
import resource
import signal
import sys
from typing import Any

MAX_INPUT_BYTES = 4 << 20
PR_SET_PDEATHSIG = 1


class _ResourceLimit(BaseException):
    pass


def _apply_limits(limits: dict[str, Any], parent_pid: int | None) -> None:
    def setlim(which: int, value: int) -> None:
        _, hard = resource.getrlimit(which)
        soft = int(value) if hard == resource.RLIM_INFINITY else min(int(value), hard)
        new_hard = soft + 5 if which == resource.RLIMIT_CPU else soft  # CPU: SIGXCPU at soft, SIGKILL at hard
        if hard != resource.RLIM_INFINITY:
            new_hard = min(new_hard, hard)
        resource.setrlimit(which, (soft, new_hard))

    setlim(resource.RLIMIT_CORE, 0)
    setlim(resource.RLIMIT_CPU, int(limits["cpu_s"]))
    setlim(resource.RLIMIT_AS, int(limits["as_bytes"]))
    setlim(resource.RLIMIT_NOFILE, int(limits["nofile"]))
    setlim(resource.RLIMIT_FSIZE, int(limits["fsize_bytes"]))
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except (OSError, AttributeError):
        pass
    if parent_pid is not None and os.getppid() != parent_pid:
        os._exit(3)  # the API process died before PDEATHSIG was armed


def _is_alloc_failure(exc: BaseException) -> bool:
    if isinstance(exc, MemoryError):
        return True
    text = str(exc).lower()
    return isinstance(exc, RuntimeError) and ("allocate" in text or "out of memory" in text or "not enough memory" in text)


ALLOC_MARKERS = ("can't allocate memory", "cannot allocate memory", "defaultcpuallocator", "out of memory",
                 "not enough memory", "memoryerror", "could not create a primitive", "std::bad_alloc",
                 "failed to map segment")  # dlopen of a shared object refused under RLIMIT_AS


def _alloc_failure_in(doc: Any) -> str | None:
    """The first diagnostic of a non-PASS document whose cause is an allocation failure (the services record build/
    forward exceptions as diagnostics instead of raising them)."""
    if not isinstance(doc, dict) or doc.get("status") == "PASS":
        return None
    for d in doc.get("diagnostics") or []:
        cause = str((d or {}).get("cause") or "").lower()
        if any(marker in cause for marker in ALLOC_MARKERS):
            return str(d.get("code") or "allocation failure")
    return None


def _preload_runtime() -> None:
    """Import the CPU model runtime under the limits *before* the services run: the services turn an ImportError into
    a ``STRUCTURE_RUNTIME_UNAVAILABLE`` diagnostic without its cause, so a shared object refused under ``RLIMIT_AS``
    ("failed to map segment") would otherwise be misreported as a missing runtime instead of a resource limit."""
    import torch  # noqa: F401
    import ultralytics  # noqa: F401


def _guard_allocations() -> None:
    """Let allocation failures inside the CPU build escape the services' ``except Exception`` boundaries."""
    from mbnas_core.search import structure

    original = structure.inspect_genotype

    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if _is_alloc_failure(exc):
                raise _ResourceLimit(f"{type(exc).__name__}") from exc
            raise

    structure.inspect_genotype = guarded


def _run(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    from mbnas_core.console import model_services as ms

    if op == "check_structure":
        from mbnas_model_runtime.genotype import Genotype

        return ms.check_genotype_inprocess(Genotype.from_json(payload["genotype"]), int(payload["input_size"]),
                                           payload.get("class_count"))
    if op == "native_structure":
        return ms.native_structure_for_arch(payload["arch"], int(payload["nc"]), payload.get("input_size"))
    if op == "registry_yaml":
        return ms.check_registry_yaml_safe(str(payload["model_id"]), int(payload["input_size"]))
    raise ValueError(f"unknown op {op!r}")


_REPLY_FD: int | None = None


def _isolate_stdout() -> None:
    """Keep the reply channel clean: libraries that print (ultralytics logs to stdout) write to stderr instead; the
    one JSON reply goes to the saved original stdout descriptor."""
    global _REPLY_FD
    sys.stdout.flush()
    _REPLY_FD = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def _emit(obj: dict[str, Any]) -> int:
    data = json.dumps(obj, allow_nan=False, default=str).encode("utf-8")
    if _REPLY_FD is None:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        return 0
    view = memoryview(data)
    while view:
        view = view[os.write(_REPLY_FD, view):]
    return 0


def main() -> int:
    _isolate_stdout()
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT", "cause": "request too large"}})
    request = json.loads(raw.decode("utf-8"))
    _apply_limits(request["limits"], request.get("parent_pid"))
    try:
        _preload_runtime()
        _guard_allocations()
        from mbnas_core.console.model_services import StructureRequestError

        try:
            doc = _run(str(request["op"]), dict(request.get("payload") or {}))
        except StructureRequestError as exc:
            return _emit({"ok": False, "request_error": {"code": exc.code, "message": exc.message, "status": exc.status}})
        alloc = _alloc_failure_in(doc)
        if alloc:  # an allocation refused under RLIMIT_AS is a sandbox limit, never a structural FAIL of the model
            return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT",
                                                   "cause": f"allocation refused under the sandbox memory limit ({alloc})"}})
        return _emit({"ok": True, "result": doc})
    except _ResourceLimit as exc:
        return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT",
                                               "cause": f"allocation refused under the sandbox memory limit ({exc})"}})
    except MemoryError:
        return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT",
                                               "cause": "allocation refused under the sandbox memory limit (MemoryError)"}})
    except (ImportError, OSError) as exc:
        if _is_alloc_failure(exc) or any(m in str(exc).lower() for m in ALLOC_MARKERS):
            return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT",
                                                   "cause": f"runtime import refused under the sandbox memory limit ({type(exc).__name__})"}})
        return _emit({"ok": False, "failure": {"code": "STRUCTURE_RUNTIME_UNAVAILABLE",
                                               "cause": f"structure runtime unavailable ({type(exc).__name__})"}})
    except Exception as exc:  # noqa: BLE001 - a failure is data; never a traceback with server paths
        if _is_alloc_failure(exc):
            return _emit({"ok": False, "failure": {"code": "STRUCTURE_CHECK_RESOURCE_LIMIT",
                                                   "cause": f"allocation refused under the sandbox memory limit ({type(exc).__name__})"}})
        return _emit({"ok": False, "failure": {"code": "STRUCTURE_RUNTIME_UNAVAILABLE",
                                               "cause": f"structure check worker failed ({type(exc).__name__})"}})


if __name__ == "__main__":
    raise SystemExit(main())
