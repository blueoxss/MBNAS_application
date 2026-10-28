"""NVML (nvidia-ml-py) device identity and live samples for the worker row; every probe degrades to None.

``index`` is an NVML index: NVML enumerates in PCI bus order and ignores ``CUDA_VISIBLE_DEVICES``. The runner daemon
starts its job child with ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` and ``CUDA_VISIBLE_DEVICES=<gpu_index>``, so CUDA enumerates
in the same order and the child's ``cuda:0`` is the device :func:`device_info` describes (the uuid the runner
registers).
"""
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {"init": None}


def _nvml() -> Any | None:
    with _lock:
        if _state["init"] is None:
            try:
                import pynvml

                pynvml.nvmlInit()
                _state["init"] = pynvml
            except Exception:  # noqa: BLE001 - NVML missing/unusable: samples become None
                _state["init"] = False
        return _state["init"] or None


def device_info(index: int) -> dict[str, Any]:
    """``{id, index, uuid, name, memory_total_mb}`` of GPU ``index`` (uuid/name None without NVML)."""
    info: dict[str, Any] = {"id": f"host-gpu-{index}", "index": index, "uuid": None, "name": None,
                            "memory_total_mb": None}
    nv = _nvml()
    if nv is None:
        return info
    try:
        h = nv.nvmlDeviceGetHandleByIndex(index)
        info.update(uuid=nv.nvmlDeviceGetUUID(h), name=nv.nvmlDeviceGetName(h),
                    memory_total_mb=nv.nvmlDeviceGetMemoryInfo(h).total // (1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    return info


def sample(index: int) -> dict[str, Any]:
    """Live ``utilization_percent``, ``vram_mb``, ``power_w``, ``sm_clock_mhz`` (None when unreadable)."""
    out: dict[str, Any] = {"utilization_percent": None, "vram_mb": None, "power_w": None, "sm_clock_mhz": None}
    nv = _nvml()
    if nv is None:
        return out
    probes = {
        "utilization_percent": lambda h: float(nv.nvmlDeviceGetUtilizationRates(h).gpu),
        "vram_mb": lambda h: nv.nvmlDeviceGetMemoryInfo(h).used / (1024 * 1024),
        "power_w": lambda h: nv.nvmlDeviceGetPowerUsage(h) / 1000.0,
        "sm_clock_mhz": lambda h: float(nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM)),
    }
    try:
        handle = nv.nvmlDeviceGetHandleByIndex(index)
    except Exception:  # noqa: BLE001
        return out
    for key, probe in probes.items():
        try:
            out[key] = probe(handle)
        except Exception:  # noqa: BLE001
            pass
    return out


# -- device occupancy (S7-F1) ------------------------------------------------------------------------------------
def compute_pids(index: int) -> list[int] | None:
    """pids NVML lists as compute processes on GPU ``index``; None when NVML is unavailable (nothing to wait on).

    A killed CUDA process stays listed until the driver has torn its context down (measured ~150 ms after it
    became a zombie), so "the process is gone" and "the device is free" are different moments."""
    nv = _nvml()
    if nv is None:
        return None
    try:
        handle = nv.nvmlDeviceGetHandleByIndex(index)
        procs = nv.nvmlDeviceGetComputeRunningProcesses(handle)
    except Exception:  # noqa: BLE001
        return None
    return sorted({int(p.pid) for p in procs})


def proc_state(pid: int) -> str | None:
    """Single-letter /proc state of ``pid`` (``Z`` zombie) or None when the pid no longer exists."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        return None


def wait_clear(index: int, *, exclude: Any = (), timeout_s: float = 15.0, poll_s: float = 0.05,
               pids_fn: Any = None, log: Any = None, clock: Any = time.monotonic, sleep: Any = time.sleep,
               ) -> dict[str, Any]:
    """Wait (bounded by ``timeout_s``) until NVML lists no compute pid on GPU ``index`` outside ``exclude``.

    ``exclude`` is a collection or a zero-argument callable returning one (re-read every poll, e.g. the worker's
    own pid and its current child). ``log(message)`` is called once when a wait starts and once when it ends.
    Returns ``{"cleared": bool, "waited_s": float, "foreign": [pids still listed], "nvml": bool}``; without NVML the
    device counts as clear (``nvml: False``)."""
    pids_fn = pids_fn or compute_pids
    start = clock()
    first: list[int] | None = None
    while True:
        listed = pids_fn(index)
        if listed is None:
            return {"cleared": True, "waited_s": 0.0, "foreign": [], "nvml": False}
        skip = set(exclude() if callable(exclude) else exclude)
        foreign = sorted(p for p in listed if p not in skip)
        waited = clock() - start
        if not foreign:
            if first and log:
                log(f"gpu {index}: compute pids {first} left NVML after {waited:.3f} s")
            return {"cleared": True, "waited_s": waited, "foreign": [], "nvml": True}
        if first is None:
            first = foreign
            if log:
                log(f"gpu {index}: waiting up to {timeout_s:.1f} s for foreign compute pids {foreign} "
                    f"(states {[proc_state(p) or 'gone' for p in foreign]}) to leave NVML")
        if waited >= timeout_s:
            if log:
                log(f"gpu {index}: compute pids {foreign} still listed after {waited:.3f} s; giving up the wait")
            return {"cleared": False, "waited_s": waited, "foreign": foreign, "nvml": True}
        sleep(poll_s)
