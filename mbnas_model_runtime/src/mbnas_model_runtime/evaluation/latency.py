"""Measured PyTorch latency on the host GPU (``latency_source = measured``).

``measure(fns, warmup, repeats, device)`` times each scope's callable (from ``adapter.latency_fns``) with
``torch.cuda.synchronize()`` before and after every repeat and ``time.perf_counter`` in between, batch 1, after
``warmup`` untimed calls. Scopes:

* ``forward_only`` — model forward on an already preprocessed device tensor;
* ``forward_and_postprocess`` (default for fitness) — forward + the adapter's native decoding/NMS + scaling back to
  source pixels;
* ``end_to_end`` — adds preprocessing from the decoded uint8 image (letterbox/resize, normalise, host->device copy).

FP16 means ``torch.autocast(float16)`` over FP32 weights; FP32 means no autocast. The manifest records device
uuid/name/driver/CUDA/torch/cuDNN, power limit and SM clocks via nvidia-ml-py, precision, batch, shape, warmup and
repeats. This is a PyTorch measurement; TensorRT engines are measured by the benchmark module.

NVML numbers devices in PCI bus order and ignores ``CUDA_VISIBLE_DEVICES``, so torch's CUDA index is not an NVML index
(a runner child sees its GPU as ``cuda:0`` whatever its NVML index). :func:`gpu_manifest` and :class:`NvmlSampler`
therefore ask NVML for the device with torch's uuid (:func:`torch_device_uuid`, :func:`nvml_device_handle`); torch's
index is used as the NVML index only when that lookup is unavailable, with a logged warning and
``nvml_lookup = "index"`` in the record (``"uuid"`` otherwise).

Each record also carries ``preprocess_ms`` (``preprocess_ms_method = derived``: p50(end_to_end) - p50(
forward_and_postprocess) from the same round-robin repeats), ``throughput_fps = None`` with the note
``not measured (batch 1 only)`` (``fps_batch1 = 1000/p50`` is a batch-1 rate, not throughput), and
``device_sampling``: NVML power draw (W) and SM clock (MHz) sampled after the warm-up, after every repeat round
(outside the timed window) and after the repeats, as min/max/mean. NVML power is the driver's averaged reading.

Operating point: the post-processing inside ``forward_and_postprocess`` (and ``end_to_end``) runs at the resolved
``console.benchmark.postprocess`` (:func:`resolved_postprocess`: conf 0.25 / iou 0.7 / max_det 300 by default) — not at
the AP evaluation floor (conf 0.001), which only the evaluator uses. The evaluation row's ``latency_scope`` is
:func:`scope_label` (e.g. ``forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)``). :func:`ensure_torchvision`
imports torchvision before any NMS runs (ultralytics only uses ``torchvision.ops.nms`` when torchvision is already in
``sys.modules``; otherwise it falls back to a Python loop); :func:`postprocess_runtime` records which NMS ran
(``torchvision_cuda_nms`` | ``python_loop`` | ``no_nms`` for heads without NMS).
"""
from __future__ import annotations

import logging
import statistics
import sys
import time
from typing import Any, Callable, Mapping

from mbnas_model_runtime.adapters.base import DEFAULT_LATENCY_SCOPE, LATENCY_SCOPES
from mbnas_contracts.constants import DEFAULT_POSTPROCESS

_log = logging.getLogger(__name__)

LATENCY_VERSION = "mbnas-latency/4"  # /3: scopes interleaved round-robin per repeat; /4: derived
#                                      preprocess_ms, explicit throughput_fps=null, NVML power/clock sampling
THROUGHPUT_NOTE = "not measured (batch 1 only)"
PREPROCESS_DEFINITION = ("p50(end_to_end) - p50(forward_and_postprocess), both timed in the same round-robin repeats; "
                         "preprocessing is not timed on its own, timing noise can make it slightly negative")
POSTPROCESS_RUNTIMES = ("torchvision_cuda_nms", "python_loop", "no_nms")


def resolved_postprocess(benchmark: Mapping[str, Any] | None) -> dict[str, Any]:
    """Latency operating point of a resolved ``console.benchmark`` (defaults fill keys an older protocol lacks)."""
    pp = dict((benchmark or {}).get("postprocess") or {})
    out = {k: pp.get(k, v) for k, v in DEFAULT_POSTPROCESS.items()}
    return {"conf": float(out["conf"]), "iou": float(out["iou"]), "max_det": int(out["max_det"])}


def scope_label(pp: Mapping[str, Any], scope: str = DEFAULT_LATENCY_SCOPE) -> str:
    """``forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)`` — the cohort/latency_scope string of a row."""
    return f"{scope}(conf={float(pp['conf']):g},iou={float(pp['iou']):g},max_det={int(pp['max_det'])})"


def ensure_torchvision() -> str | None:
    """Import torchvision (explicitly, before any NMS) and return its version; None when it is not installed."""
    try:
        import torchvision
    except ImportError:
        return None
    return str(torchvision.__version__)


def postprocess_runtime(description: Mapping[str, Any], device: str) -> str:
    """Which NMS implementation the adapter's post-processing used on ``device`` (see module docstring).

    ``description`` is ``adapter.postprocess_description(model)``: ``nms`` False -> ``no_nms``; ``nms_impl``
    ``python_loop*`` (adapter-owned pure-Torch loop) -> ``python_loop``; otherwise the ultralytics rule: CUDA
    tensors and torchvision imported -> ``torchvision_cuda_nms``, else ``python_loop`` (``TorchNMS``).
    """
    if not description.get("nms"):
        return "no_nms"
    if str(description.get("nms_impl", "")).startswith("python_loop"):
        return "python_loop"
    return "torchvision_cuda_nms" if "torchvision" in sys.modules and str(device).startswith("cuda") else "python_loop"


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy 'linear' method) of a non-empty list."""
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def stats(samples_ms: list[float]) -> dict[str, float]:
    return {"p50_ms": _percentile(samples_ms, 0.50), "p95_ms": _percentile(samples_ms, 0.95),
            "mean_ms": statistics.fmean(samples_ms), "min_ms": min(samples_ms), "max_ms": max(samples_ms),
            "n": len(samples_ms)}


def torch_device_uuid(index: int, *, torch_module: Any = None) -> str | None:
    """NVML-form uuid (``GPU-<8-4-4-4-12>``) of the CUDA device torch numbers ``index`` (``CUDA_VISIBLE_DEVICES`` and
    ``CUDA_DEVICE_ORDER`` applied), or None when torch cannot tell (no such device, a torch without ``uuid``).
    ``torch_module`` replaces the torch module (tests)."""
    try:
        if torch_module is None:
            import torch as torch_module
        raw = str(torch_module.cuda.get_device_properties(index).uuid)
    except Exception:  # noqa: BLE001 - unknown is reported as None, never guessed
        return None
    return raw if raw.startswith("GPU-") else f"GPU-{raw}"


def nvml_device_handle(nvml: Any, index: int, uuid: str | None) -> tuple[Any, dict[str, Any]]:
    """``(NVML handle, {"nvml_lookup": ...})`` of the CUDA device torch numbers ``index`` with uuid ``uuid`` (see the
    module docstring): ``nvmlDeviceGetHandleByUUID(uuid)`` -> ``"uuid"``. Only when that lookup is unavailable (torch
    gave no uuid, this nvidia-ml-py lacks the call, or NVML does not know the uuid) is ``index`` used as the NVML index:
    a warning is logged and the record says ``"index"`` with ``nvml_lookup_warning`` (the facts then describe the right
    device only when no ``CUDA_VISIBLE_DEVICES`` remap applies and CUDA enumerates in PCI bus order). An NVML error of
    the index lookup itself propagates (callers record it)."""
    by_uuid = getattr(nvml, "nvmlDeviceGetHandleByUUID", None)
    if not uuid:
        why = f"torch reported no uuid for CUDA device {index}"
    elif by_uuid is None:
        why = "this nvidia-ml-py has no nvmlDeviceGetHandleByUUID"
    else:
        try:
            return by_uuid(uuid), {"nvml_lookup": "uuid"}
        except Exception as exc:  # noqa: BLE001 - e.g. NVMLError_NotFound: fall back below, never silently
            why = f"nvmlDeviceGetHandleByUUID({uuid}) failed: {type(exc).__name__}: {exc}"
    _log.warning("NVML lookup of CUDA device %d by uuid unavailable (%s); using %d as the NVML index",
                 index, why, index)
    return nvml.nvmlDeviceGetHandleByIndex(index), {"nvml_lookup": "index", "nvml_lookup_warning": why}


def gpu_manifest(device: str, *, nvml: Any = None, torch_module: Any = None) -> dict[str, Any]:
    """Device facts at measurement time (nvidia-ml-py); fields that cannot be read are recorded as ``None``. The NVML
    facts are those of the device with torch's uuid (:func:`nvml_device_handle`). ``nvml`` / ``torch_module`` replace
    the pynvml / torch modules (tests)."""
    torch = torch_module
    if torch is None:
        import torch

    idx = torch.device(device).index or 0
    out: dict[str, Any] = {"torch": torch.__version__, "cuda_runtime": torch.version.cuda,
                           "cudnn": torch.backends.cudnn.version(), "torch_device_name": torch.cuda.get_device_name(idx),
                           "cuda_index": idx}
    uuid = torch_device_uuid(idx, torch_module=torch)
    try:
        if nvml is None:
            import pynvml as nvml

        nvml.nvmlInit()
        try:
            h, lookup = nvml_device_handle(nvml, idx, uuid)
            out.update(lookup)

            def safe(fn: Callable[[], Any]) -> Any:
                try:
                    return fn()
                except nvml.NVMLError:
                    return None

            out.update({
                "uuid": safe(lambda: nvml.nvmlDeviceGetUUID(h)), "name": safe(lambda: nvml.nvmlDeviceGetName(h)),
                "driver": safe(nvml.nvmlSystemGetDriverVersion),
                "power_limit_w": safe(lambda: nvml.nvmlDeviceGetPowerManagementLimit(h) / 1000.0),
                "sm_clock_mhz": safe(lambda: nvml.nvmlDeviceGetClockInfo(h, nvml.NVML_CLOCK_SM)),
                "sm_clock_max_mhz": safe(lambda: nvml.nvmlDeviceGetMaxClockInfo(h, nvml.NVML_CLOCK_SM)),
                "mem_clock_mhz": safe(lambda: nvml.nvmlDeviceGetClockInfo(h, nvml.NVML_CLOCK_MEM)),
                "temperature_c": safe(lambda: nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)),
                "utilization_percent": safe(lambda: nvml.nvmlDeviceGetUtilizationRates(h).gpu),
                "persistence_mode": safe(lambda: nvml.nvmlDeviceGetPersistenceMode(h)),
            })
        finally:
            nvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001 - NVML missing is recorded, not fatal
        out["nvml_error"] = f"{type(exc).__name__}: {exc}"
    return out


def preprocess_and_throughput(scopes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Derived ``preprocess_ms`` (labelled) and the explicit ``throughput_fps = None`` of one latency record."""
    e2e, fpp = scopes.get("end_to_end"), scopes.get("forward_and_postprocess")
    if e2e and fpp and e2e.get("p50_ms") is not None and fpp.get("p50_ms") is not None:
        pre: dict[str, Any] = {"preprocess_ms": float(e2e["p50_ms"]) - float(fpp["p50_ms"]),
                               "preprocess_ms_method": "derived", "preprocess_ms_definition": PREPROCESS_DEFINITION}
    else:
        pre = {"preprocess_ms": None, "preprocess_ms_method": "not available",
               "preprocess_ms_definition": "end_to_end and forward_and_postprocess were not both measured"}
    return {**pre, "throughput_fps": None, "throughput_fps_note": THROUGHPUT_NOTE}


def _summary(values: list[float]) -> dict[str, Any] | None:
    return ({"min": min(values), "max": max(values), "mean": statistics.fmean(values), "n": len(values)}
            if values else None)


class NvmlSampler:
    """Power draw / SM clock samples of one GPU via nvidia-ml-py; failures are recorded, never raised.

    ``index`` is torch's CUDA index of the measured device and ``uuid`` its torch uuid (:func:`torch_device_uuid`);
    the NVML device is looked up by ``uuid`` (:func:`nvml_device_handle`: by ``index``, with a logged warning, only
    when that is unavailable). ``nvml`` is the pynvml module (injectable for tests). Call :meth:`sample` outside the
    timed window only.
    """

    METHOD = ("nvmlDeviceGetPowerUsage / nvmlDeviceGetClockInfo(SM) sampled after the warm-up, after every repeat "
              "round (outside the timed window) and after the repeats")

    def __init__(self, index: int, nvml: Any = None, *, uuid: str | None = None) -> None:
        self.index, self.uuid, self.nvml, self.handle, self.error = index, uuid, nvml, None, None
        self.lookup: dict[str, Any] = {}
        self.nvml_index: int | None = index
        self.points: list[tuple[str, float | None, int | None]] = []
        try:
            if self.nvml is None:
                import pynvml

                self.nvml = pynvml
            self.nvml.nvmlInit()
            self.handle, self.lookup = nvml_device_handle(self.nvml, index, uuid)
        except Exception as exc:  # noqa: BLE001 - NVML missing is recorded, not fatal
            self.error, self.handle = f"{type(exc).__name__}: {exc}", None
            return
        if self.lookup.get("nvml_lookup") == "uuid":  # the NVML index of the device found by uuid (None: unreadable)
            try:
                self.nvml_index = int(self.nvml.nvmlDeviceGetIndex(self.handle))
            except Exception:  # noqa: BLE001
                self.nvml_index = None

    def sample(self, label: str) -> None:
        if self.handle is None:
            return
        try:
            power = self.nvml.nvmlDeviceGetPowerUsage(self.handle) / 1000.0
            clock = self.nvml.nvmlDeviceGetClockInfo(self.handle, self.nvml.NVML_CLOCK_SM)
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self.points.append((label, power, clock))

    def summary(self) -> dict[str, Any]:
        power = [p for _, p, _ in self.points if p is not None]
        clock = [c for _, _, c in self.points if c is not None]

        def at(label: str) -> dict[str, Any] | None:
            hit = [(p, c) for lab, p, c in self.points if lab == label]
            return {"power_draw_w": hit[-1][0], "sm_clock_mhz": hit[-1][1]} if hit else None

        return {"power_draw_w": _summary(power), "sm_clock_mhz": _summary(clock), "after_warmup": at("after_warmup"),
                "after_repeats": at("after_repeats"), "method": self.METHOD, "nvml_index": self.nvml_index,
                "cuda_index": self.index, **self.lookup, "error": self.error}

    def close(self) -> None:
        if self.nvml is not None and self.handle is not None:
            try:
                self.nvml.nvmlShutdown()
            except Exception:  # noqa: BLE001
                pass


def measure(fns: Mapping[str, Callable[[], Any]], *, warmup: int, repeats: int, device: str,
            scopes: tuple[str, ...] = LATENCY_SCOPES) -> dict[str, Any]:
    """Time every requested scope; returns ``{scopes: {scope: stats}, warmup, repeats, timer, version}``."""
    import torch

    if warmup < 0 or repeats < 1:
        raise ValueError("warmup must be >= 0 and repeats >= 1")
    unknown = [s for s in scopes if s not in fns]
    if unknown:
        raise ValueError(f"adapter provides no latency callable for scopes {unknown}")
    dev = torch.device(device)
    if dev.type != "cuda":
        raise ValueError("latency is measured on a CUDA device only (measured source)")
    # Warm every scope first, then interleave the scopes round-robin inside each repeat. A transient disturbance
    # (clock ramp, another process's CPU burst) then lands on all scopes alike instead of on one scope's block,
    # so the per-scope statistics stay comparable (forward_only <= forward_and_postprocess <= end_to_end).
    for scope in scopes:
        for _ in range(warmup):
            fns[scope]()
    torch.cuda.synchronize(dev)
    index = dev.index or 0
    sampler = NvmlSampler(index, uuid=torch_device_uuid(index, torch_module=torch))  # CUDA is initialised by now
    try:
        sampler.sample("after_warmup")
        samples: dict[str, list[float]] = {scope: [] for scope in scopes}
        for _ in range(repeats):
            for scope in scopes:
                torch.cuda.synchronize(dev)
                t0 = time.perf_counter()
                fns[scope]()
                torch.cuda.synchronize(dev)
                samples[scope].append((time.perf_counter() - t0) * 1000.0)
            sampler.sample("repeat")  # outside the timed window
        sampler.sample("after_repeats")
        device_sampling = sampler.summary()
    finally:
        sampler.close()
    results: dict[str, Any] = {scope: {**stats(samples[scope]), "samples_ms": [round(s, 4) for s in samples[scope]]}
                               for scope in scopes}
    return {"version": LATENCY_VERSION, "scopes": results, "warmup": warmup, "repeats": repeats,
            "timer": "time.perf_counter with torch.cuda.synchronize around each repeat; scopes interleaved "
                     "round-robin per repeat after per-scope warmup", "batch": 1,
            **preprocess_and_throughput(results), "device_sampling": device_sampling,
            "latency_source": "measured", "runtime": "pytorch"}
