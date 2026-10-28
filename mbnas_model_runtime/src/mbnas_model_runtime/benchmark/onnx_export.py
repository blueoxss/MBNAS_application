"""ONNX export per backend with a uniform :class:`ExportReport` (path, sha256, opset, IO shapes, flags, semantics).

* :func:`export_ultralytics` runs the native ultralytics ``Exporter`` on an evaluated ``DetectionModel`` /
  ``RTDETRDetectionModel`` (``format='onnx'``, opset 17, ``dynamic=False``, FP32 graph (no ``quantize``), batch 1,
  ``nms=False``, ``simplify=False`` because onnxslim is not installed) on CPU. For the call the module's ``pt_path``
  points at the output dir (nothing is written next to source weights) and a missing ``task`` (modules built directly
  from a YAML) is filled by ultralytics' ``guess_model_task``; both attributes are restored afterwards.
* :func:`export_rfdetr` calls ``RFDETR.export(output_dir, format='onnx', opset_version=17, batch_size=1,
  dynamic_batch=False, shape=(R, R))`` (R = the model resolution).
* :func:`export_torch` runs ``torch.onnx.export`` for plain modules (ETRI controlled hybrid), static batch-1 shape.
Failures never raise: the report has ``ok=False`` and the exact error.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from mbnas_model_runtime.benchmark.capability import ONNX_OPSET
from mbnas_contracts.util.atomic import sha256_file


@dataclass
class ExportReport:
    ok: bool
    backend: str
    method: str
    path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    opset: int | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    seconds: float | None = None
    error: str | None = None
    output_semantics: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def onnx_io(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    """Graph inputs/outputs (name, shape with -1 for symbolic dims, elem type) and the default-domain opset."""
    import onnx

    model = onnx.load(str(path), load_external_data=False)

    def desc(values: Any) -> list[dict[str, Any]]:
        return [{"name": v.name, "shape": [d.dim_value if d.HasField("dim_value") else -1
                                           for d in v.type.tensor_type.shape.dim],
                 "elem_type": onnx.TensorProto.DataType.Name(v.type.tensor_type.elem_type)} for v in values]

    opset = next((o.version for o in model.opset_import if o.domain in ("", "ai.onnx")), None)
    return desc(model.graph.input), desc(model.graph.output), opset


def _finish(report: ExportReport, path: Path, t0: float) -> ExportReport:
    report.inputs, report.outputs, report.opset = onnx_io(path)
    report.path, report.sha256, report.bytes = str(path), sha256_file(path), path.stat().st_size
    report.seconds = round(time.perf_counter() - t0, 3)
    report.ok = True
    return report


def export_ultralytics(module: Any, out_dir: str | Path, *, imgsz: int, stem: str, opset: int = ONNX_OPSET,
                       output_semantics: str | None = None) -> ExportReport:
    """Native ultralytics ONNX export of an in-memory detection module (see module docstring)."""
    from ultralytics import __version__ as ul_version
    from ultralytics.engine.exporter import Exporter

    from ultralytics.nn.tasks import guess_model_task

    flags = {"format": "onnx", "imgsz": imgsz, "batch": 1, "opset": opset, "simplify": False, "dynamic": False,
             "nms": False, "device": "cpu", "verbose": False}
    report = ExportReport(ok=False, backend="ultralytics", method=f"ultralytics {ul_version} Exporter",
                          flags={**flags, "graph_precision": "FP32"}, output_semantics=output_semantics)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    saved = {k: getattr(module, k) for k in ("pt_path", "task") if k in vars(module)}
    module.pt_path = str(out / f"{stem}.pt")  # Exporter writes <pt_path stem>.onnx
    if not getattr(module, "task", None):
        module.task = guess_model_task(module)
    try:
        path = Path(Exporter(overrides=flags)(model=module))
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        report.error = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        for key in ("pt_path", "task"):
            if key in saved:
                setattr(module, key, saved[key])
            elif key in vars(module):
                delattr(module, key)
    return _finish(report, path, t0)


def export_torch(module: Any, out_dir: str | Path, *, input_shape: Sequence[int], stem: str,
                 output_names: Sequence[str], input_name: str = "images", opset: int = ONNX_OPSET,
                 output_semantics: str | None = None) -> ExportReport:
    """``torch.onnx.export`` with a static batch-1 input on the module's device (eval mode).

    The ``nn.MultiheadAttention`` / ``TransformerEncoderLayer`` inference fast path is switched off for the export:
    in eval mode under ``no_grad`` it dispatches to ``aten::_transformer_encoder_layer_fwd``, which has no ONNX
    symbolic (the ETRI hybrid export failed with ``UnsupportedOperatorError`` on the benchmark path). The slow path
    is mathematically the same graph of exportable ops. The previous setting is restored afterwards.
    """
    import torch

    flags = {"input_shape": list(input_shape), "opset": opset, "dynamic_axes": None, "do_constant_folding": True,
             "input_names": [input_name], "output_names": list(output_names)}
    report = ExportReport(ok=False, backend="torch", method=f"torch {torch.__version__} torch.onnx.export",
                          flags=flags, output_semantics=output_semantics)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}.onnx"
    t0 = time.perf_counter()
    fastpath = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)
    try:
        device = next(module.parameters()).device
        x = torch.zeros(*input_shape, device=device)
        module.eval()
        with torch.no_grad():
            torch.onnx.export(module, (x,), str(path), opset_version=opset, input_names=[input_name],
                              output_names=list(output_names), do_constant_folding=True, dynamo=False)
    except Exception as exc:  # noqa: BLE001
        report.error = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        torch.backends.mha.set_fastpath_enabled(fastpath)
    return _finish(report, path, t0)


def export_rfdetr(model: Any, out_dir: str | Path, *, stem: str = "inference_model", opset: int = ONNX_OPSET,
                  resolution: int | None = None, output_semantics: str | None = None) -> ExportReport:
    """rfdetr's native ONNX export of an ``RFDETR`` instance (batch 1, static square shape ``resolution``).

    ``RFDETR.export`` deep-copies the live LWDETR (the evaluated module is moved to CPU for the copy and restored to
    its device afterwards), switches the copy to ``forward_export`` and traces it with ``torch.onnx.export``; the
    graph has one input ``input[1, 3, R, R]`` and outputs ``dets[1, Q, 4]`` (cxcywh normalised) and
    ``labels[1, Q, C]`` (logits). ``resolution`` defaults to the model's own ``model.resolution``.
    """
    from importlib.metadata import version

    res = int(resolution if resolution is not None else model.model.resolution)
    flags = {"format": "onnx", "opset_version": opset, "batch_size": 1, "dynamic_batch": False,
             "shape": [res, res], "output_name": stem, "graph_precision": "FP32"}
    report = ExportReport(ok=False, backend="rfdetr", method=f"rfdetr {version('rfdetr')} RFDETR.export",
                          flags=flags, output_semantics=output_semantics)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        result = model.export(output_dir=str(out), format="onnx", opset_version=opset, batch_size=1,
                              dynamic_batch=False, shape=(res, res), verbose=False, output_name=stem)
    except Exception as exc:  # noqa: BLE001
        report.error = f"{type(exc).__name__}: {exc}"
        return report
    candidates = [Path(result)] if isinstance(result, (str, Path)) and str(result).endswith(".onnx") else \
        sorted(out.glob("*.onnx"), key=lambda p: p.stat().st_mtime)
    if not candidates or not candidates[-1].is_file():
        report.error = f"rfdetr export produced no .onnx file in {out}"
        return report
    return _finish(report, candidates[-1], t0)
