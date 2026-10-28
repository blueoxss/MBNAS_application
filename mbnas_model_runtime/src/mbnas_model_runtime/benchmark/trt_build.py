"""TensorRT engine build from ONNX with the installed TensorRT python API (10.x semantics, explicit batch).

:func:`build_engine` parses the ONNX (``OnnxParser.parse_from_file``), builds a fixed batch-1 engine (inputs with
symbolic dims get a min=opt=max optimisation profile at the requested static shape), FP16 flag when requested
(TF32 left at the TensorRT default and recorded), workspace pool limit, builder optimisation level, and a timing
cache file under the output root (loaded when present, saved after the build). The serialized engine is written
atomically and its SHA-256 recorded with the full builder configuration. If ``tensorrt`` cannot be imported or the
build fails the report has ``status="BLOCKED"`` and the exact error; nothing is substituted.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from mbnas_contracts.util.atomic import atomic_write_bytes, sha256_file
from mbnas_contracts.util.canon import sha256_hex

DEFAULT_WORKSPACE = 4 << 30
DEFAULT_OPT_LEVEL = 3


@dataclass
class TrtBuildReport:
    status: str
    engine_path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    seconds: float | None = None
    tensorrt_version: str | None = None
    precision: str | None = None
    builder_config: dict[str, Any] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)
    timing_cache: dict[str, Any] = field(default_factory=dict)
    onnx_sha256: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _io(network: Any) -> dict[str, Any]:
    return {"inputs": [{"name": network.get_input(i).name, "shape": list(network.get_input(i).shape),
                        "dtype": str(network.get_input(i).dtype)} for i in range(network.num_inputs)],
            "outputs": [{"name": network.get_output(i).name, "shape": list(network.get_output(i).shape),
                         "dtype": str(network.get_output(i).dtype)} for i in range(network.num_outputs)],
            "layers": network.num_layers}


def build_engine(onnx_path: str | Path, engine_path: str | Path, *, precision: str = "FP16",
                 timing_cache_path: str | Path | None = None, workspace_bytes: int = DEFAULT_WORKSPACE,
                 opt_level: int = DEFAULT_OPT_LEVEL, static_shape: Sequence[int] | None = None) -> TrtBuildReport:
    """Build and serialize one engine; see module docstring. ``precision`` is ``FP16`` or ``FP32``."""
    report = TrtBuildReport(status="BLOCKED", precision=precision, onnx_sha256=sha256_file(onnx_path))
    try:
        import tensorrt as trt
    except Exception as exc:  # noqa: BLE001
        report.error = f"tensorrt import failed: {type(exc).__name__}: {exc}"
        return report
    report.tensorrt_version = trt.__version__
    t0 = time.perf_counter()
    try:
        logger = trt.Logger(trt.Logger.WARNING)
        builder = trt.Builder(logger)
        network = builder.create_network(0)  # TensorRT 10: explicit batch is the only mode
        parser = trt.OnnxParser(network, logger)
        if not parser.parse_from_file(str(onnx_path)):
            errors = [parser.get_error(i).desc() for i in range(parser.num_errors)]
            raise RuntimeError(f"ONNX parse failed: {errors}")
        report.network = _io(network)
        config = builder.create_builder_config()
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, int(workspace_bytes))
        config.builder_optimization_level = int(opt_level)
        if precision == "FP16":
            if not builder.platform_has_fast_fp16:
                raise RuntimeError("platform reports no fast FP16")
            config.set_flag(trt.BuilderFlag.FP16)
        elif precision != "FP32":
            raise ValueError(f"unsupported precision {precision!r} (FP16 | FP32)")
        dynamic = [i for i in range(network.num_inputs) if any(d < 0 for d in network.get_input(i).shape)]
        if dynamic:
            if static_shape is None:
                raise ValueError("ONNX has symbolic input dims; pass static_shape for the batch-1 profile")
            profile = builder.create_optimization_profile()
            for i in dynamic:
                shape = tuple(int(v) for v in static_shape)
                profile.set_shape(network.get_input(i).name, shape, shape, shape)
            config.add_optimization_profile(profile)
        cache_blob = b""
        if timing_cache_path is not None and Path(timing_cache_path).is_file():
            cache_blob = Path(timing_cache_path).read_bytes()
        cache = config.create_timing_cache(cache_blob)
        config.set_timing_cache(cache, ignore_mismatch=False)
        report.builder_config = {
            "api": "builder.build_serialized_network", "explicit_batch": True, "batch": 1,
            "flags": {"FP16": config.get_flag(trt.BuilderFlag.FP16), "TF32": config.get_flag(trt.BuilderFlag.TF32)},
            "workspace_bytes": int(workspace_bytes), "builder_optimization_level": int(opt_level),
            "optimization_profile": list(static_shape) if dynamic and static_shape else None,
            "device": _device_name(), "platform_has_fast_fp16": bool(builder.platform_has_fast_fp16)}
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise RuntimeError("build_serialized_network returned None (see TensorRT log)")
        data = bytes(serialized)
        atomic_write_bytes(engine_path, data)
        report.timing_cache = {"path": str(timing_cache_path) if timing_cache_path else None,
                               "loaded_bytes": len(cache_blob)}
        if timing_cache_path is not None:
            blob = bytes(config.get_timing_cache().serialize())
            atomic_write_bytes(timing_cache_path, blob)
            report.timing_cache.update({"saved_bytes": len(blob), "sha256": sha256_hex(blob)})
    except Exception as exc:  # noqa: BLE001 - exact error recorded
        report.error = f"{type(exc).__name__}: {exc}"
        report.seconds = round(time.perf_counter() - t0, 3)
        return report
    report.status, report.engine_path = "BUILT", str(engine_path)
    report.sha256, report.bytes = sha256_hex(data), len(data)
    report.seconds = round(time.perf_counter() - t0, 3)
    return report


def _device_name() -> str | None:
    try:
        import torch

        return torch.cuda.get_device_name(torch.cuda.current_device())
    except Exception:  # noqa: BLE001
        return None
