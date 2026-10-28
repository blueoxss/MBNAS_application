"""What each backend's export path supports, what this host can run, and which device targets are measurable.

Every entry is a declaration backed by the installed code (see STAGE_TRAINER_AUDIT A.16/B.6 and PLAN §8.9); the
``host_runtime()`` probe reports the actual importable versions. Device targets: only ``host-gpu-0`` (the GPU at NVML
index 0 of the host that runs this code) is measurable here; any other target (Jetson, other GPUs) is BLOCKED with a
reason and never receives a proxy latency. The host GPU's display name is the product name read on this host
(:func:`host_gpu_name`: NVML, else an already initialised torch CUDA context, else ``"unknown"``), never a fixed product
name. :data:`DECLARED_TARGETS` resolves it on first
access (PEP 562 module attribute), so importing this module probes nothing.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any

from mbnas_contracts.constants import DEFAULT_POSTPROCESS  # re-exported: single definition in contracts (contract §4)

SUPPORTED_TARGETS: tuple[str, ...] = ("host-gpu-0",)
#: Display name of a host GPU whose product name cannot be read here (no NVML, no CUDA context in this process).
UNKNOWN_DEVICE_NAME = "unknown"
#: Every deployment target MBNAS knows about: id -> display name / kind / aliases. ``name: None`` = a host GPU
#: named by :func:`host_gpu_name` (see :func:`declared_targets`).
_TARGET_DECLARATIONS: dict[str, dict[str, Any]] = {
    "host-gpu-0": {"name": None, "kind": "host_gpu", "aliases": ["cuda:0"]},
    "jetson-orin-nx": {"name": "NVIDIA Jetson Orin NX", "kind": "jetson", "aliases": ["Orin NX", "orin-nx"]},
    "jetson-orin-nano": {"name": "NVIDIA Jetson Orin Nano", "kind": "jetson", "aliases": ["Orin Nano", "orin-nano"]},
    "jetson-agx-orin": {"name": "NVIDIA Jetson AGX Orin", "kind": "jetson", "aliases": ["AGX Orin", "agx-orin"]},
}
_RESOLVED: dict[str, dict[str, Any]] = {}  # declared_targets() fills it in place (held references stay current)
_RESOLVED_LOCK = threading.Lock()
_resolved_once = False


def _nvml_device_name(index: int, nvml: Any = None) -> str | None:
    """``nvmlDeviceGetName`` of NVML device ``index`` (paired nvmlInit/nvmlShutdown; NVML counts them), or None."""
    try:
        if nvml is None:
            import pynvml as nvml  # nvidia-ml-py
        nvml.nvmlInit()
    except Exception:  # noqa: BLE001 - no NVML library / driver: the next source is tried
        return None
    try:
        raw = nvml.nvmlDeviceGetName(nvml.nvmlDeviceGetHandleByIndex(index))
        name = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        return name.strip() or None
    except Exception:  # noqa: BLE001 - no such index / unreadable
        return None
    finally:
        try:
            nvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


def _torch_device_name(index: int) -> str | None:
    """torch's name of CUDA device ``index``, only when this process already initialised CUDA (never initialises it)
    and no ``CUDA_VISIBLE_DEVICES`` remap can make the torch index differ from the NVML index."""
    torch = sys.modules.get("torch")
    if torch is None or os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
        return None
    try:
        if not torch.cuda.is_initialized():
            return None
        return str(torch.cuda.get_device_name(index)).strip() or None
    except Exception:  # noqa: BLE001
        return None


def host_gpu_name(index: int = 0, *, nvml: Any = None) -> str:
    """Product name of this host's GPU ``index`` (the NVML index of the ``host-gpu-<index>`` ids of
    :func:`mbnas_model_runtime.provenance.env_lock.host_gpu_inventory`): NVML first, then torch (see
    :func:`_torch_device_name`), else :data:`UNKNOWN_DEVICE_NAME`. Never raises and never initialises CUDA (the core
    and the gateway reach this through ``/health`` and the registry). ``nvml`` replaces ``pynvml`` (tests)."""
    return _nvml_device_name(index, nvml) or _torch_device_name(index) or UNKNOWN_DEVICE_NAME


def declared_targets(*, refresh: bool = False, nvml: Any = None) -> dict[str, dict[str, Any]]:
    """Every declared deployment target, id -> ``{name, kind, aliases}``, with each host GPU named by
    :func:`host_gpu_name` (probed once per process; ``refresh=True`` probes again, ``nvml`` is passed through). Only
    SUPPORTED_TARGETS can be measured on this host; the others are listed by the registry and ``/health`` as
    UNAVAILABLE with a reason. The same dict object is returned every time (updated in place on refresh)."""
    global _resolved_once
    with _RESOLVED_LOCK:
        if refresh or not _resolved_once:
            fresh: dict[str, dict[str, Any]] = {}
            for tid, decl in _TARGET_DECLARATIONS.items():
                entry = {**decl, "aliases": list(decl["aliases"])}
                if decl["kind"] == "host_gpu":
                    entry["name"] = host_gpu_name(int(tid.rsplit("-", 1)[1]), nvml=nvml)
                fresh[tid] = entry
            _RESOLVED.clear()
            _RESOLVED.update(fresh)
            _resolved_once = True
    return _RESOLVED


def __getattr__(name: str) -> Any:
    """``DECLARED_TARGETS`` = :func:`declared_targets` (resolved on first access; readers such as the core registry keep
    importing the name)."""
    if name == "DECLARED_TARGETS":
        return declared_targets()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


ONNX_OPSET = 17
#: Export/benchmark resolution of the official RF-DETR checkpoints (rfdetr 1.11.2 ``RFDETR*Config.resolution``).
RFDETR_RESOLUTION: dict[str, int] = {"RFDETRNano": 384, "RFDETRSmall": 512, "RFDETRMedium": 576, "RFDETRLarge": 704}
#: Official checkpoint file per RF-DETR class (the published MD5 comes from ``rfdetr.assets.model_weights``).
RFDETR_WEIGHTS: dict[str, str] = {"RFDETRNano": "rf-detr-nano.pth", "RFDETRSmall": "rf-detr-small.pth",
                                  "RFDETRMedium": "rf-detr-medium.pth", "RFDETRLarge": "rf-detr-large-2026.pth"}
#: ``DEFAULT_POSTPROCESS`` (imported above) is the latency operating point used when the protocol/job gives no
#: ``benchmark.postprocess`` (contract v1.2.0); it is defined once, in ``mbnas_contracts.constants``.

BACKENDS: dict[str, dict[str, Any]] = {
    "ultralytics": {
        "export_onnx": {"supported": True, "method": "ultralytics Exporter(format='onnx') on the evaluated module",
                        "opset": ONNX_OPSET, "dynamic": False, "half": False, "batch": 1,
                        "simplify": False, "simplify_reason": "onnxslim is not installed (PLAN §8.9)",
                        "nms": False, "nms_reason": "raw head exported; decode/NMS runs in the adapter postprocess so "
                                                    "PyTorch/ORT/TRT share one postprocess"},
        "output_semantics": {
            "YOLO11": "output0[1, 4+nc, anchors]: xywh in input pixels + per-class sigmoid scores (one-to-many); "
                      "adapter postprocess = NMS + scale to source pixels",
            "YOLO26": "output0[1, 300, 6]: one-to-one end2end head (nms=False): xyxy input pixels, score, class; "
                      "adapter postprocess = confidence filter + scale (no NMS)",
            "RTDETR": "output0[1, 300, 6]: cx, cy, w, h normalised, score, class (top-k queries, no NMS)"},
        "export_trt": {"supported": True, "method": "TensorRT python API from the exported ONNX (benchmark.trt_build)"},
        "runtimes": ["pytorch", "onnxruntime", "tensorrt"],
    },
    "etri_hybrid": {
        "export_onnx": {"supported": True, "method": "torch.onnx.export of the evaluated ControlledDetector",
                        "opset": ONNX_OPSET, "dynamic": False, "batch": 1,
                        "outputs": ["pred_logits", "pred_boxes"]},
        "output_semantics": "pred_logits[1, tokens, nc+1] (softmax, background last), pred_boxes[1, tokens, 4] "
                            "cxcywh normalised; adapter postprocess = softmax, class NMS, restore to source pixels",
        "export_trt": {"supported": True, "method": "TensorRT python API from the exported ONNX"},
        "runtimes": ["pytorch", "onnxruntime", "tensorrt"],
    },
    "rfdetr": {
        "export_onnx": {"supported": True,
                        "method": "rfdetr RFDETR.export(format='onnx', opset_version=17, batch_size=1, "
                                  "dynamic_batch=False, shape=(R, R)) on a deepcopy of the evaluated LWDETR",
                        "opset": ONNX_OPSET, "dynamic": False, "batch": 1, "simplify": "not offered by rfdetr 1.11.2",
                        "resolution_by_class": "RFDETR_RESOLUTION (nano 384, small 512, medium 576, large 704); "
                                               "eval snapshots export at their trained resolution",
                        "outputs": ["dets", "labels"]},
        "output_semantics": "dets[1, 300, 4] cxcywh normalised + labels[1, 300, C] logits (no NMS); the adapter "
                            "postprocess (rfdetr PostProcess: top-k num_select over sigmoid(query x class), boxes "
                            "scaled to source pixels, then the conf threshold) runs on these outputs for every runtime",
        "export_trt": {"supported": True, "method": "TensorRT python API from the exported ONNX"},
        "runtimes": ["pytorch", "onnxruntime", "tensorrt"],
        "accuracy_delta": "adapters/rfdetr_adapter.py predict(): the ONNX Runtime / TensorRT forward replaces "
                          "LWDETR inside RFDETR.predict (same resize/normalise preprocessing, same PostProcess and "
                          "threshold), scored by the common evaluator against pytorch:FP32",
    },
}


def host_runtime() -> dict[str, Any]:
    """Importable versions on this host (each failure recorded as the exact import error).

    ``rfdetr`` is never imported here: importing it calls ``torch.set_float32_matmul_precision("high")`` (TF32
    matmuls) process-wide, which would change the conditions of every other benchmark in the process. Its installed
    version is read from the distribution metadata and ``imported`` says whether this process already loaded it.
    """
    import importlib.util
    import sys
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, Any] = {}
    for name in ("torch", "torchvision", "onnx", "onnxruntime", "tensorrt", "ultralytics"):
        try:
            mod = __import__(name)
            out[name] = {"available": True, "version": getattr(mod, "__version__", None)}
        except Exception as exc:  # noqa: BLE001 - recorded verbatim
            out[name] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        out["rfdetr"] = {"available": importlib.util.find_spec("rfdetr") is not None, "version": version("rfdetr"),
                         "imported": "rfdetr" in sys.modules}
    except PackageNotFoundError as exc:
        out["rfdetr"] = {"available": False, "error": f"PackageNotFoundError: {exc}"}
    if out["onnxruntime"]["available"]:
        import onnxruntime

        out["onnxruntime"]["providers"] = onnxruntime.get_available_providers()
    if out["torch"]["available"]:
        import torch

        out["torch"].update({"cuda": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
                             "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]})
    return out


def target_status(device: str) -> dict[str, Any]:
    """``AVAILABLE`` for the host GPU, else ``BLOCKED`` with the reason (no proxy numbers are produced)."""
    if device in SUPPORTED_TARGETS:
        return {"device": device, "status": "AVAILABLE", "kind": "host_gpu"}
    return {"device": device, "status": "BLOCKED", "reason": (
        f"target '{device}' is not attached to this host; only {list(SUPPORTED_TARGETS)} can be built and measured "
        "here. Engines built on the host GPU are not deployable to other GPUs/Jetson, and no proxy latency is "
        "substituted.")}


def declared_target_for(name: Any) -> str | None:
    """Declared target id whose id/name/alias equals ``name`` (case-insensitive), else None."""
    if not isinstance(name, str):
        return None
    low = name.strip().lower()
    for tid, t in declared_targets().items():
        names = {tid, *(a.lower() for a in t["aliases"])}
        if t["name"] and t["name"] != UNKNOWN_DEVICE_NAME:  # "unknown" is a placeholder, never a device name
            names.add(t["name"].lower())
        if low in names:
            return tid
    return None


def device_targets(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every declared target with ``available`` against the live ``inventory`` (validation devices) + a reason; a
    host GPU present in the inventory is named by its inventory entry (the live NVML name)."""
    present = {d.get("id"): d for d in inventory}
    out = []
    for tid, t in declared_targets().items():
        if tid in SUPPORTED_TARGETS and tid in present:
            out.append({"id": tid, "name": present[tid].get("name") or t["name"], "kind": t["kind"],
                        "available": True, "reason": None, "status": "AVAILABLE"})
        elif tid in SUPPORTED_TARGETS:
            out.append({"id": tid, "name": t["name"], "kind": t["kind"], "available": False, "status": "UNAVAILABLE",
                        "reason": f"{tid} is a measurable target but is not in the live device inventory "
                                  "(no NVML device / CUDA_VISIBLE_DEVICES hides it)"})
        else:
            out.append({"id": tid, "name": t["name"], "kind": t["kind"], "available": False, "status": "UNAVAILABLE",
                        "reason": target_status(tid)["reason"]})
    return out


def capability() -> dict[str, Any]:
    return {"backends": BACKENDS, "targets": {t: target_status(t) for t in (*SUPPORTED_TARGETS, "jetson-orin-nx")},
            "host": host_runtime()}
