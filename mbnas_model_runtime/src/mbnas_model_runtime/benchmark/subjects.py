"""Benchmark subjects: an adapter + its evaluated PyTorch module + how exported-runtime outputs map back.

The adapter's own ``predict`` / ``latency_fns`` drive every runtime: for ONNX Runtime and TensorRT the module is
replaced by :class:`RuntimeModule`, which runs the engine on the adapter-preprocessed CUDA tensor and returns the
outputs in the structure the PyTorch module returns (first output for ultralytics heads, a name->tensor dict for the
ETRI hybrid). Every other attribute (head flags such as YOLO26 ``end2end``, ``mbnas_meta``) is read from the PyTorch
module, so preprocessing, decoding/NMS and scaling to source pixels are byte-identical code paths across runtimes.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mbnas_model_runtime.benchmark.capability import BACKENDS
from mbnas_model_runtime.benchmark.onnx_export import ExportReport, export_torch, export_ultralytics
from mbnas_contracts.util.atomic import sha256_file

ULTRA_HEADS = {"YOLO11": "native", "YOLO26": "e2e_one2one_nms_false", "RTDETR": "native"}


class SubjectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeModule:
    """Duck-typed stand-in for the PyTorch module inside ``adapter.predict`` / ``adapter.latency_fns``."""

    def __init__(self, runner: Callable[[Any], list[Any]], reference: Any, unpack: Callable[[list[Any]], Any],
                 device: Any) -> None:
        import torch

        self._runner, self._reference, self._unpack = runner, reference, unpack
        self._anchor = torch.zeros(1, device=device)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reference, name)

    def parameters(self) -> Any:
        yield self._anchor

    def __call__(self, x: Any) -> Any:
        return self._unpack(self._runner(x.float().contiguous()))


@dataclass
class BenchSubject:
    """What is benchmarked; ``export(out_dir)`` writes the ONNX for this subject.

    ``model`` is what the adapter's ``predict`` / ``latency_fns`` take (an ``nn.Module`` for ultralytics/ETRI, an
    ``RFDETR`` instance for RF-DETR). ``runtime_view(runner)`` (optional) returns that object with only the network
    forward replaced by an exported runtime; without it a :class:`RuntimeModule` around ``model`` is used.
    ``raw_view`` (optional) maps a raw forward output to a name->tensor dict so raw outputs of PyTorch and the exported
    runtimes are compared tensor by tensor. ``net`` (optional) is the ``nn.Module`` whose parameters are counted.
    """

    name: str
    backend: str
    adapter: Any
    model: Any
    imgsz: int
    device: str
    unpack: Callable[[list[Any]], Any]
    export: Callable[[Path], ExportReport]
    source: dict[str, Any] = field(default_factory=dict)
    postprocess: dict[str, Any] = field(default_factory=dict)
    runtime_view: Callable[[Any], Any] | None = None
    raw_view: Callable[[Any], Any] | None = None
    net: Any = None
    postprocess_impl: str | None = None
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "backend": self.backend, "imgsz": self.imgsz, "source": self.source,
                "postprocess": self.postprocess,
                "params": sum(p.numel() for p in (self.net if self.net is not None else self.model).parameters())}


def _first(outputs: list[Any]) -> Any:
    return outputs[0]


def verify_official_weights(path: str | Path, weights_dir: str | Path) -> dict[str, Any]:
    """Match ``path`` against ``weights_manifest.json`` (bytes + sha256); raises SubjectError otherwise."""
    p = Path(path)
    manifest_path = Path(weights_dir) / "weights_manifest.json"
    entry = json.loads(manifest_path.read_text()).get(p.name) if manifest_path.is_file() else None
    if not entry or not p.is_file():
        hint = (f"; {manifest_path} does not exist - create it with "
                f"`python scripts/download_weights.py --weights-dir {weights_dir}` (records bytes + sha256 of the "
                "official checkpoints; it downloads the pinned release files it has no manifest entry for)"
                ) if not manifest_path.is_file() else (
                f"; record it with `python scripts/download_weights.py --weights-dir {weights_dir} --only {p.name}`")
        raise SubjectError("WEIGHTS_UNVERIFIED", f"{p} has no entry in {manifest_path} or is missing{hint}")
    digest = sha256_file(p)
    if digest != entry["sha256"] or p.stat().st_size != entry["bytes"]:
        raise SubjectError("WEIGHTS_UNVERIFIED", f"{p}: sha256/bytes differ from the weights manifest")
    return {"path": str(p), "sha256": digest, "bytes": entry["bytes"], "source_url": entry.get("source_url"),
            "manifest": str(manifest_path)}


def ultralytics_from_weights(path: str | Path, *, family: str, imgsz: int, device: str,
                             weights_dir: str | Path) -> BenchSubject:
    """Official ultralytics checkpoint (verified against the weights manifest), fused, eval, on ``device``."""
    from ultralytics.nn.tasks import load_checkpoint

    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    if family not in ULTRA_HEADS:
        raise SubjectError("INVALID_SUBJECT", f"ultralytics family must be one of {sorted(ULTRA_HEADS)}")
    evidence = verify_official_weights(path, weights_dir)
    model, _ = load_checkpoint(str(path), device="cpu", fuse=False)
    head_pin = ULTRA_HEADS[family]
    head = model.model[-1]
    if getattr(head, "one2one_cv2", None) is not None:
        head.end2end = head_pin == "e2e_one2one_nms_false"
    reference = copy.deepcopy(model)
    model = model.fuse(verbose=False).to(device).eval()
    adapter = UltralyticsAdapter(family, "rtdetr" if family == "RTDETR" else "detect")
    semantics = BACKENDS["ultralytics"]["output_semantics"][family]
    return BenchSubject(
        name=Path(path).stem, backend="ultralytics", adapter=adapter, model=model, imgsz=imgsz, device=device,
        unpack=_first,
        export=lambda out: export_ultralytics(reference, out, imgsz=imgsz, stem=Path(path).stem,
                                              output_semantics=semantics),
        source={"kind": "official_weights", "family": family, "inference_head": head_pin, **evidence},
        postprocess=adapter.postprocess_description(model))


def ultralytics_from_snapshot(eval_ckpt: str | Path, *, family: str, imgsz: int, device: str) -> BenchSubject:
    """MBNAS rung-end FP32 EMA evaluation snapshot through the ultralytics adapter's own loader."""
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    adapter = UltralyticsAdapter(family, "rtdetr" if family == "RTDETR" else "detect")
    cpu_model = adapter.load_eval_model(eval_ckpt, "cpu")
    model = copy.deepcopy(cpu_model).to(device).eval()
    meta = getattr(cpu_model, "mbnas_meta", {})
    return BenchSubject(
        name=Path(eval_ckpt).stem, backend="ultralytics", adapter=adapter, model=model, imgsz=imgsz, device=device,
        unpack=_first,
        export=lambda out: export_ultralytics(cpu_model, out, imgsz=imgsz, stem=Path(eval_ckpt).stem,
                                              output_semantics=BACKENDS["ultralytics"]["output_semantics"].get(family)),
        source={"kind": "eval_snapshot", "family": family, "path": str(eval_ckpt), "sha256": sha256_file(eval_ckpt),
                "inference_head": meta.get("inference_head")},
        postprocess=adapter.postprocess_description(model))


def etri_from_snapshot(eval_ckpt: str | Path, *, device: str) -> BenchSubject:
    """ETRI controlled hybrid evaluation snapshot; ONNX via torch.onnx.export with named dict outputs."""
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter

    adapter = EtriHybridAdapter()
    model = adapter.load_eval_model(eval_ckpt, device)
    imgsz = int(model.mbnas_meta["model_kwargs"]["imgsz"])
    names = ["pred_logits", "pred_boxes"]
    return BenchSubject(
        name=Path(eval_ckpt).stem, backend="etri_hybrid", adapter=adapter, model=model, imgsz=imgsz, device=device,
        unpack=lambda outs: dict(zip(names, outs)),
        export=lambda out: export_torch(model, out, input_shape=(1, 3, imgsz, imgsz), stem=Path(eval_ckpt).stem,
                                        output_names=names,
                                        output_semantics=BACKENDS["etri_hybrid"]["output_semantics"]),
        source={"kind": "eval_snapshot", "family": "etri_hybrid", "path": str(eval_ckpt),
                "sha256": sha256_file(eval_ckpt)},
        postprocess=adapter.postprocess_description(model))


# -- RF-DETR -----------------------------------------------------------------------------------------------------------
RFDETR_POSTPROCESS_IMPL = ("rfdetr.models.postprocess.PostProcess (top-k num_select over sigmoid(query x class), "
                           "cxcywh -> xyxy scaled to source pixels, no NMS) + score threshold in RFDETR.predict")


RFDETR_TF32_NOTE = ("rfdetr calls torch.set_float32_matmul_precision('high') when it is imported, so the RF-DETR "
                    "pytorch:FP32 reference runs FP32 matmuls as TF32 (rfdetr's own inference setting; see device.tf32 "
                    "and float32_matmul_precision in the record). TensorRT FP32 engines also allow TF32 (builder "
                    "flag).")


def runner_output_names(runner: Any) -> list[str]:
    """Output tensor names of an :class:`OrtRunner` / :class:`TrtRunner` in the order ``runner(x)`` returns them."""
    if hasattr(runner, "output_names"):
        return list(runner.output_names)
    return [o[0] for o in runner.outputs]


def rfdetr_unpack(names: list[str]) -> Callable[[list[Any]], Any]:
    """Runtime outputs -> ``(dets, labels)``, the tuple LWDETR's export forward returns (by name, not position)."""
    missing = [n for n in ("dets", "labels") if n not in names]
    if missing:
        raise SubjectError("EXPORT_OUTPUTS_UNEXPECTED", f"RF-DETR graph outputs {names} lack {missing}")
    i_dets, i_labels = names.index("dets"), names.index("labels")
    return lambda outs: (outs[i_dets], outs[i_labels])


def rfdetr_raw_view(out: Any) -> Any:
    """``{'pred_boxes', 'pred_logits'}`` from the PyTorch dict output or the exported ``(dets, labels)`` tuple."""
    if isinstance(out, dict):
        return {"pred_boxes": out["pred_boxes"], "pred_logits": out["pred_logits"]}
    if isinstance(out, (list, tuple)) and len(out) >= 2:
        return {"pred_boxes": out[0], "pred_logits": out[1]}
    return out


def rfdetr_runtime_view(rf: Any, runner: Any, device: str) -> Any:
    """Shallow copy of the ``RFDETR`` instance whose ``model.model`` (LWDETR) is the exported runtime.

    ``RFDETR.predict`` and the adapter's ``latency_fns`` read the network from ``rf.model.model`` and keep using
    the shared ``rf.model.postprocess`` / resolution / means / stds / class names, so preprocessing and decoding are the
    same objects for PyTorch, ONNX Runtime and TensorRT. The original instance is not modified.
    """
    net = RuntimeModule(runner, rf.model.model, rfdetr_unpack(runner_output_names(runner)), device)
    ctx = copy.copy(rf.model)
    ctx.model = net
    view = copy.copy(rf)
    view.model = ctx
    return view


def verify_rfdetr_official_weights(path: str | Path, class_name: str) -> dict[str, Any]:
    """Official RF-DETR checkpoint: file name must be the class's published file and its MD5 must equal the digest
    published in ``rfdetr.assets.model_weights.ModelWeights``; the SHA-256 is recorded. Raises SubjectError."""
    import hashlib

    from rfdetr.assets.model_weights import ModelWeights

    from mbnas_model_runtime.benchmark.capability import RFDETR_WEIGHTS

    p = Path(path)
    if class_name not in RFDETR_WEIGHTS:
        raise SubjectError("INVALID_SUBJECT", f"RF-DETR class must be one of {sorted(RFDETR_WEIGHTS)}")
    if p.name != RFDETR_WEIGHTS[class_name]:
        raise SubjectError("WEIGHTS_UNVERIFIED", f"{p.name} is not the official {class_name} file "
                                                 f"{RFDETR_WEIGHTS[class_name]}")
    if not p.is_file():
        raise SubjectError("WEIGHTS_UNVERIFIED", f"{p} is missing")
    published = ModelWeights.get_md5(p.name)
    md5 = hashlib.md5()  # noqa: S324 - compared with the digest rfdetr publishes
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
    observed = md5.hexdigest()
    if not published or observed != published:
        raise SubjectError("WEIGHTS_UNVERIFIED", f"{p}: md5 {observed} != published {published}")
    return {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size, "md5": observed,
            "published_md5": published, "source_url": ModelWeights.get_url(p.name),
            "digest_basis": "published MD5 (rfdetr ModelWeights); sha256 recorded here"}


def class_for_rfdetr_weights(path: str | Path) -> str:
    from mbnas_model_runtime.benchmark.capability import RFDETR_WEIGHTS

    by_file = {v: k for k, v in RFDETR_WEIGHTS.items()}
    if Path(path).name not in by_file:
        raise SubjectError("INVALID_SUBJECT", f"{Path(path).name} is not an official RF-DETR checkpoint "
                                              f"({sorted(by_file)}); pass subject.class_name")
    return by_file[Path(path).name]


def _rfdetr_subject(adapter: Any, rf: Any, *, name: str, device: str, source: dict[str, Any]) -> BenchSubject:
    from mbnas_model_runtime.benchmark.onnx_export import export_rfdetr

    rf.model.model.to(device).eval()
    res = int(rf.model.resolution)
    semantics = BACKENDS["rfdetr"]["output_semantics"]
    return BenchSubject(
        name=name, backend="rfdetr", adapter=adapter, model=rf, imgsz=res, device=device,
        unpack=lambda outs: tuple(outs),
        export=lambda out: export_rfdetr(rf, out, stem=name, resolution=res, output_semantics=semantics),
        source={**source, "class_name": adapter.class_name, "resolution": res,
                "num_select": getattr(rf.model.postprocess, "num_select", None)},
        postprocess=adapter.postprocess_description(rf),
        runtime_view=lambda runner: rfdetr_runtime_view(rf, runner, device), raw_view=rfdetr_raw_view,
        net=rf.model.model, postprocess_impl=RFDETR_POSTPROCESS_IMPL, notes=[RFDETR_TF32_NOTE])


def rfdetr_from_weights(path: str | Path, *, device: str, class_name: str | None = None) -> BenchSubject:
    """Official RF-DETR checkpoint (published-MD5 verified) at the class's default resolution, eval, on ``device``."""
    import os

    from mbnas_model_runtime.adapters.rfdetr_adapter import RfdetrAdapter, default_rf_home
    from mbnas_model_runtime.benchmark.capability import RFDETR_RESOLUTION

    class_name = class_name or class_for_rfdetr_weights(path)
    evidence = verify_rfdetr_official_weights(path, class_name)
    if "RF_HOME" not in os.environ:  # setdefault, without computing the default when RF_HOME is set
        os.environ["RF_HOME"] = default_rf_home()
    adapter = RfdetrAdapter(class_name)
    rf = adapter._cls()(pretrain_weights=str(path), device=device)
    res = int(rf.model.resolution)
    if res != RFDETR_RESOLUTION[class_name]:
        raise SubjectError("INVALID_SUBJECT",
                           f"{class_name} resolution {res} != expected {RFDETR_RESOLUTION[class_name]}")
    return _rfdetr_subject(adapter, rf, name=Path(path).stem, device=device,
                           source={"kind": "official_weights", "family": "rfdetr", **evidence})


def rfdetr_from_snapshot(eval_ckpt: str | Path, *, device: str) -> BenchSubject:
    """MBNAS RF-DETR rung-end FP32 EMA evaluation snapshot through the adapter's own loader (trained resolution)."""
    from mbnas_model_runtime.adapters.rfdetr_adapter import RfdetrAdapter
    from mbnas_model_runtime import checkpoint as ck

    meta = ck.load_eval_snapshot(eval_ckpt)["model_meta"]
    adapter = RfdetrAdapter(str(meta["class_name"]))
    rf = adapter.load_eval_model(eval_ckpt, device)
    return _rfdetr_subject(adapter, rf, name=Path(eval_ckpt).stem, device=device,
                           source={"kind": "eval_snapshot", "family": "rfdetr", "path": str(eval_ckpt),
                                   "sha256": sha256_file(eval_ckpt)})
