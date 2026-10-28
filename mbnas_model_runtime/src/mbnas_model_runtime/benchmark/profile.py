"""Latency profile for PyTorch vs ONNX Runtime vs TensorRT at batch 1, with the scope definitions of
:mod:`mbnas_server.evaluation.latency` (``forward_only``, ``forward_and_postprocess``, ``end_to_end``).

The adapter's ``latency_fns`` builds the per-scope callables (identical preprocessing/postprocessing code for every
runtime; the exported runtimes replace only the forward), and :func:`evaluation.latency.measure` times them with
``torch.cuda.synchronize`` around every repeat. :func:`device_manifest` records driver/CUDA/cuDNN/TensorRT/ONNX
Runtime versions, device uuid/name, power limit, SM clocks and the TF32 switches. ``fps_batch1 = 1000 / p50_ms`` is
the batch-1 rate, not a throughput measurement.
"""
from __future__ import annotations

from typing import Any

from mbnas_model_runtime.adapters.base import LATENCY_SCOPES


def device_manifest(device: str) -> dict[str, Any]:
    import torch

    from mbnas_model_runtime.benchmark.capability import host_runtime
    from mbnas_model_runtime.evaluation.latency import gpu_manifest

    return {**gpu_manifest(device), "host_runtime": host_runtime(),
            "tf32": {"cuda_matmul_allow_tf32": torch.backends.cuda.matmul.allow_tf32,
                     "cudnn_allow_tf32": torch.backends.cudnn.allow_tf32,
                     "float32_matmul_precision": torch.get_float32_matmul_precision()},
            "cudnn_benchmark": torch.backends.cudnn.benchmark}


def _detach(out: Any) -> Any:
    import torch

    if isinstance(out, torch.Tensor):
        return out.detach().clone()
    if isinstance(out, dict):
        return {k: _detach(v) for k, v in out.items()}
    if isinstance(out, (list, tuple)):
        return [_detach(v) for v in out]
    return out


#: ``latency_fns`` keyword names that carry the latency operating point, in order of preference.
POSTPROCESS_KWARGS = ("postprocess", "operating_point")


def latency_postprocess_kwargs(adapter: Any, op: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(kwargs for adapter.latency_fns, provenance)`` for the operating point ``op``.

    The point is passed only when the adapter's ``latency_fns`` declares a parameter for it; otherwise the latency
    scopes run with the adapter's built-in postprocess defaults and the provenance says so (never claimed applied).
    """
    import inspect

    point = {k: op[k] for k in ("conf", "iou", "max_det")}
    try:
        params = inspect.signature(adapter.latency_fns).parameters
    except (TypeError, ValueError):
        params = {}
    for name in POSTPROCESS_KWARGS:
        if name in params:
            return {name: point}, {"applied_by_adapter": True, "latency_fns_parameter": name}
    return {}, {"applied_by_adapter": False,
                "note": f"{type(adapter).__name__}.latency_fns takes no operating point; latency scopes used the "
                        f"adapter's built-in postprocess defaults"}


def profile_runtime(adapter: Any, module: Any, *, imgsz: int, precision: str, device: str, sample_image: str,
                    warmup: int, repeats: int,
                    extra_kwargs: dict[str, Any] | None = None) -> tuple[dict[str, Any], Any]:
    """Time every latency scope for one runtime (``module`` = PyTorch model or RuntimeModule).

    Returns ``(latency_record, raw_forward_output)``; the raw output of one ``forward_only`` call on the sample image
    is cloned so runtimes can be compared element-wise.
    """
    from mbnas_model_runtime.evaluation.latency import measure

    fns = adapter.latency_fns(module, (imgsz, imgsz), precision, device, sample_image, **(extra_kwargs or {}))
    raw = _detach(fns["forward_only"]())
    res = measure(fns, warmup=warmup, repeats=repeats, device=device, scopes=LATENCY_SCOPES)
    for scope in res["scopes"].values():
        scope["fps_batch1"] = round(1000.0 / scope["p50_ms"], 2) if scope["p50_ms"] > 0 else None
        scope.pop("samples_ms", None)
    return res, raw


def latency_table(runtimes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat rows ``{runtime, precision, scope, p50_ms, p95_ms, mean_ms, fps_batch1}`` for reports."""
    rows = []
    for key, rec in runtimes.items():
        lat = rec.get("latency") or {}
        for scope, st in (lat.get("scopes") or {}).items():
            rows.append({"runtime": rec["runtime"], "precision": rec["precision"], "key": key, "scope": scope,
                         "p50_ms": round(st["p50_ms"], 4), "p95_ms": round(st["p95_ms"], 4),
                         "mean_ms": round(st["mean_ms"], 4), "fps_batch1": st.get("fps_batch1")})
    return rows
