"""Live state of running pipelines: driver STATUS.json / pipeline.log, process liveness (/proc), GPU usage (NVML).

Read-only: ``/proc/<pid>/stat`` is read, nothing is signalled; NVML is only queried (no clocks/power/contexts are set).
Every failure degrades to ``None`` / ``False`` (the console must work on a host without NVML).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from mbnas_server.archive.parsers import read_json, tail_lines

try:
    _CLK_TCK = os.sysconf("SC_CLK_TCK")
except (ValueError, OSError, AttributeError):  # pragma: no cover - non-POSIX
    _CLK_TCK = 100


def _boot_time() -> float | None:
    try:
        with open("/proc/stat", "r") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def pid_alive(pid: Any, started_after: float | None = None, started_before: float | None = None) -> bool:
    """True when ``/proc/<pid>`` exists and is not a zombie. Guards against pid reuse: when given, the process start time must
    not precede ``started_after`` (the launcher start) nor follow ``started_before`` (when the record was written)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            raw = fh.read()
        rest = raw.rsplit(")", 1)[1].split()
        state, start_ticks = rest[0], int(rest[19])
    except (OSError, IndexError, ValueError):
        return False
    if state in ("Z", "X"):
        return False
    btime = _boot_time()
    if btime is not None and (started_after is not None or started_before is not None):
        started = btime + start_ticks / _CLK_TCK
        if started_after is not None and started < started_after - 5:
            return False
        if started_before is not None and started > started_before + 120:
            return False
    return True


_nvml_lock = threading.Lock()
_nvml_cache: dict[str, Any] = {"t": 0.0, "value": None}
NVML_TTL_S = 1.0


def gpu_usage() -> list[dict[str, Any]] | None:
    """[{index, name, used_mib, total_mib, util_percent}] from NVML, or None when NVML is unavailable. Cached for 1 s."""
    with _nvml_lock:
        now = time.monotonic()
        if now - _nvml_cache["t"] < NVML_TTL_S and _nvml_cache["t"] > 0:
            return _nvml_cache["value"]
        value: list[dict[str, Any]] | None
        try:
            import pynvml  # type: ignore[import-not-found]

            pynvml.nvmlInit()
            try:
                value = []
                for i in range(pynvml.nvmlDeviceGetCount()):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    try:
                        util = float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
                    except Exception:  # noqa: BLE001 - util is optional
                        util = None
                    name = pynvml.nvmlDeviceGetName(h)
                    value.append({"index": i, "name": name.decode() if isinstance(name, bytes) else str(name),
                                  "used_mib": round(mem.used / (1 << 20), 1), "total_mib": round(mem.total / (1 << 20), 1),
                                  "util_percent": util})
            finally:
                pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001 - no NVML / no driver / no GPU: report null
            value = None
        _nvml_cache.update(t=now, value=value)
        return value


def driver_state(path: str) -> dict[str, Any]:
    """{path, status_json, pipeline_log_tail[20]} of one configured driver directory (fixed file names only)."""
    status, _ = read_json(os.path.join(path, "STATUS.json"))
    tail, _ = tail_lines(os.path.join(path, "pipeline.log"), 20, max_chars=500)
    return {"path": path, "status_json": status if isinstance(status, dict) else None, "pipeline_log_tail": tail}
