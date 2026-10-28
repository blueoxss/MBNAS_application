"""One benchmark run for one subject on the host GPU: export -> engines -> per-runtime accuracy + latency -> record.

Runtime matrix (batch 1, static shape): ``pytorch:FP32`` (reference; no autocast), ``pytorch:FP16`` (autocast over
FP32 weights), ``onnxruntime:FP32`` (CUDA EP, FP32 ONNX), ``tensorrt:FP16`` and ``tensorrt:FP32`` engines built from
the same ONNX. Every runtime is scored by the common evaluator on the same manifest and timed per latency scope; a
runtime that cannot be built or run is recorded with its exact error and never filled from another runtime.

``torchvision`` is imported before any measurement: ultralytics' ``non_max_suppression`` uses the CUDA
``torchvision.ops.nms`` only when ``torchvision`` is already in ``sys.modules`` and otherwise a Python-loop NMS, so
the postprocess implementation (recorded as ``postprocess_runtime``) would otherwise depend on import order.

Every record carries ``postprocess_runtime`` (torchvision version, ultralytics NMS implementation, the subject's own
decoding implementation) and ``postprocess_operating_point``: the latency operating point ``{conf, iou, max_det}``
(job ``params.postprocess`` = protocol ``benchmark.postprocess``, default 0.25 / 0.7 / 300 from contract v1.2.0) with
whether the adapter's ``latency_fns`` applied it, and the accuracy operating point of the common evaluator (conf floor
0.001, max_det 300, adapter postprocess) used for the AP / recall deltas.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Sequence

from mbnas_model_runtime.benchmark import accuracy as acc
from mbnas_contracts.constants import DEFAULT_POSTPROCESS
from mbnas_model_runtime.benchmark.profile import device_manifest, latency_postprocess_kwargs, latency_table, profile_runtime
from mbnas_model_runtime.benchmark.subjects import BenchSubject, RuntimeModule
from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.time import utcnow_iso

SCHEMA = "mbnas-benchmark/1"
REFERENCE = "pytorch:FP32"
DEFAULT_RUNTIMES: tuple[str, ...] = ("pytorch:FP32", "pytorch:FP16", "onnxruntime:FP32", "tensorrt:FP16",
                                     "tensorrt:FP32")
LIMITATIONS = [
    "Measured on the host GPU only (latency_source=measured, device host-gpu-0). Engines are device-specific and are "
    "not evidence for Jetson or any other target; those targets are BLOCKED.",
    "Batch 1, static input shape; fps_batch1 = 1000/p50 is a batch-1 rate, not a throughput measurement.",
    "pytorch:FP16 is autocast over FP32 weights; tensorrt:FP16 is a TensorRT FP16-flag engine (TF32 flag recorded).",
    "Accuracy deltas compare runtimes of the same weights on one split with the common evaluator; absolute AP is "
    "not a model-quality claim.",
    "raw_output_max_abs_diff_vs_reference is a diagnostic only: for top-k heads (RT-DETR, YOLO26 end2end) query "
    "order and the class-index column can differ between runtimes.",
    "Accuracy deltas use the evaluator operating point (conf floor 0.001, max_det 300); latency uses "
    "postprocess_operating_point.latency only where postprocess_operating_point.latency.applied_by_adapter is true.",
]


class PostprocessError(ValueError):
    """Invalid ``postprocess`` operating point (conf/iou in [0, 1], max_det >= 1 integer)."""


def resolve_postprocess(requested: Any = None) -> dict[str, Any]:
    """Latency operating point ``{conf, iou, max_det, source}``: requested keys over the contract defaults."""
    if requested is None:
        return {**DEFAULT_POSTPROCESS, "source": "default (contract v1.2.0 benchmark.postprocess)"}
    if not isinstance(requested, dict):
        raise PostprocessError(f"postprocess must be an object, got {type(requested).__name__}")
    unknown = sorted(set(requested) - set(DEFAULT_POSTPROCESS))
    if unknown:
        raise PostprocessError(f"postprocess has unknown keys {unknown} (allowed conf, iou, max_det)")
    out = {**DEFAULT_POSTPROCESS, **requested}
    for key in ("conf", "iou"):
        v = out[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
            raise PostprocessError(f"postprocess.{key} must be a number in [0, 1], got {v!r}")
        out[key] = float(v)
    if isinstance(out["max_det"], bool) or not isinstance(out["max_det"], int) or out["max_det"] < 1:
        raise PostprocessError(f"postprocess.max_det must be an integer >= 1, got {out['max_det']!r}")
    out["source"] = "job params.postprocess (protocol benchmark.postprocess)"
    return out


def split_record(manifest: Any) -> dict[str, Any]:
    """The record's ``split`` block: ``roots`` (every image root of the manifest, :class:`~mbnas_model_runtime.datasets.
    manifest.Manifest` ``roots``), ``root`` (the single root, or None for a multi-root manifest: the
    ``mbnas-benchmark/1`` key that record readers such as ``scripts/runner/bench_host_gpu.py`` render), ``split_hash``
    and the image count. (The manifest model has no ``root`` attribute.)"""
    roots = [str(r) for r in manifest.roots]
    return {"roots": roots, "root": roots[0] if len(roots) == 1 else None, "split_hash": manifest.split_hash,
            "images": len(manifest.samples)}


def _module_for(key: str, subject: BenchSubject, onnx_path: str | None, engines: dict[str, Any],
                device_index: int) -> tuple[Any, dict[str, Any]]:
    runtime, _ = key.split(":")
    if runtime == "pytorch":
        net = subject.net if subject.net is not None else subject.model
        return subject.model, {"runtime": "pytorch", "module": type(net).__name__}
    if onnx_path is None:
        raise RuntimeError("ONNX export failed; runtime not available")
    if runtime == "onnxruntime":
        from mbnas_model_runtime.benchmark.onnx_runtime import OrtRunner

        runner: Any = OrtRunner(onnx_path, device_index)
    else:
        from mbnas_model_runtime.benchmark.trt_runtime import TrtRunner

        rep = engines[key.split(":")[1]]
        if rep.status != "BUILT":
            raise RuntimeError(f"TensorRT engine {rep.status}: {rep.error}")
        runner = TrtRunner(rep.engine_path, device_index, expect_sha256=rep.sha256)
    if subject.runtime_view is not None:
        return subject.runtime_view(runner), runner.describe()
    return RuntimeModule(runner, subject.model, subject.unpack, subject.device), runner.describe()


def run_benchmark(subject: BenchSubject, *, out_dir: str | Path, manifest: Any, sample_image: str,
                  device: str = "cuda:0", runtimes: Sequence[str] = DEFAULT_RUNTIMES, warmup: int = 20,
                  repeats: int = 100, timing_cache: str | Path | None = None, postprocess: Any = None,
                  log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Run the matrix for ``subject`` and write ``<out_dir>/benchmark.json``; returns the record.

    ``postprocess`` is the latency operating point (``{conf, iou, max_det}``, see :func:`resolve_postprocess`).
    """
    import sys

    import torch
    import torchvision

    from mbnas_model_runtime.benchmark.trt_build import build_engine

    t_start = time.perf_counter()
    op = resolve_postprocess(postprocess)
    lat_kwargs, lat_applied = latency_postprocess_kwargs(subject.adapter, op)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dev = torch.device(device)
    if torch.device(subject.device) != dev:
        raise ValueError(f"subject was loaded on {subject.device}, benchmark device is {device}")
    record: dict[str, Any] = {"schema": SCHEMA, "started_at": utcnow_iso(), "subject": subject.describe(),
                              "device": device_manifest(device), "batch": 1, "input_shape": [1, 3, subject.imgsz,
                                                                                               subject.imgsz],
                              "split": split_record(manifest),
                              "sample_image": sample_image, "warmup": warmup, "repeats": repeats,
                              "latency_source": "measured", "reference": REFERENCE,
                              "limitations": [*LIMITATIONS, *subject.notes],
                              "postprocess_runtime": {
                                  "torchvision": torchvision.__version__,
                                  "ultralytics_nms": "torchvision.ops.nms" if "torchvision" in sys.modules
                                  else "ultralytics TorchNMS python loop",
                                  "backend": subject.backend,
                                  "implementation": subject.postprocess_impl or subject.postprocess.get("head"),
                                  "device": "same CUDA device as the forward for every runtime (torch ops on the "
                                            "runtime's output tensors)"},
                              "postprocess_operating_point": {
                                  "latency": {**op, **lat_applied},
                                  "accuracy": {**acc.accuracy_operating_point(),
                                               "adapter_postprocess": subject.postprocess}}}
    log("export onnx")
    export = subject.export(out / "onnx")
    record["export"] = export.as_dict()
    engines: dict[str, Any] = {}
    for prec in sorted({k.split(":")[1] for k in runtimes if k.startswith("tensorrt:")}):
        if not export.ok:
            break
        log(f"build tensorrt {prec}")
        engines[prec] = build_engine(export.path, out / "engines" / f"{subject.name}_{prec.lower()}.engine",
                                     precision=prec, timing_cache_path=timing_cache,
                                     static_shape=(1, 3, subject.imgsz, subject.imgsz))
    record["engines"] = {k: v.as_dict() for k, v in engines.items()}
    results: dict[str, dict[str, Any]] = {}
    raw_ref = None
    for key in runtimes:
        runtime, prec = key.split(":")
        entry: dict[str, Any] = {"runtime": runtime, "precision": prec}
        try:
            module, desc = _module_for(key, subject, export.path if export.ok else None, engines, dev.index or 0)
            entry["describe"] = desc
            call_prec = prec if runtime == "pytorch" else "FP32"  # exported runtimes carry their own precision
            log(f"{key}: accuracy")
            entry["accuracy"] = acc.evaluate_runtime(subject.adapter, module, manifest, imgsz=subject.imgsz,
                                                     precision=call_prec)
            log(f"{key}: latency")
            entry["latency"], raw = profile_runtime(subject.adapter, module, imgsz=subject.imgsz, precision=call_prec,
                                                    device=device, sample_image=sample_image, warmup=warmup,
                                                    repeats=repeats, extra_kwargs=lat_kwargs)
            entry["latency"]["postprocess_operating_point"] = {**op, **lat_applied}
            if subject.raw_view is not None:
                raw = subject.raw_view(raw)
            if key == REFERENCE:
                raw_ref = raw
            entry["raw_output_max_abs_diff_vs_reference"] = acc.max_abs_diff(raw, raw_ref) if raw_ref is not None \
                else None
            entry["status"] = "MEASURED"
            del module
        except Exception as exc:  # noqa: BLE001 - per-runtime failure recorded verbatim
            entry.update({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
        torch.cuda.empty_cache()
        results[key] = entry
    ref = results.get(REFERENCE, {}).get("accuracy")
    for entry in results.values():
        if ref and entry.get("accuracy"):
            entry["accuracy_delta_vs_reference"] = acc.deltas(entry["accuracy"], ref)
            if ref.get("class_agnostic") and entry["accuracy"].get("class_agnostic"):
                entry["accuracy_class_agnostic_delta_vs_reference"] = acc.deltas(entry["accuracy"]["class_agnostic"],
                                                                                 ref["class_agnostic"])
    record.update({"runtimes": results, "latency_table": latency_table({k: v for k, v in results.items()
                                                                        if v.get("status") == "MEASURED"}),
                   "finished_at": utcnow_iso(), "seconds_wall": round(time.perf_counter() - t_start, 2)})
    record["device_after"] = device_manifest(device)
    atomic_write_json(out / "benchmark.json", record)
    return record
