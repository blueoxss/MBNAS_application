"""Capability probes for registry entries (run by ``scripts/probe_registry.py``; results are JSON evidence).

Per entry: config reference (package file sha256 + canonical sha256 of the parsed config), official weights
(manifest sha256 vs observed sha256 / RF-DETR published MD5), CPU build (parameter counts for the native class count
and for nc=1, structural hash), pretrained weight load, and optionally a CPU ONNX export. Nothing is skipped silently:
every entry gets a record, failures carry the exception text.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_model_runtime.registry.specs import ModelSpec

PROBE_SCHEMA = "mbnas.registry_probe/1"
RFDETR_WEIGHTS_SUBDIR = "rfdetr"


def _site_packages() -> Path:
    import ultralytics

    return Path(ultralytics.__file__).resolve().parents[1]


def _attempt(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    t0 = time.time()
    try:
        out = fn()
        out.setdefault("ok", True)
    except Exception as exc:  # noqa: BLE001 - probes record failures as evidence
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=4)}
    out["seconds"] = round(time.time() - t0, 3)
    return out


def versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for pkg in ("ultralytics", "rfdetr", "torch", "torchvision", "pytorch_lightning", "onnx"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            out[pkg] = None
    return out


def config_ref(spec: ModelSpec) -> dict[str, Any]:
    """Locate and hash the configuration source of ``spec``."""
    if spec.backend == "ultralytics":
        import yaml

        path = _site_packages() / spec.config_file
        return {"path": str(path), "package_path": spec.config_file, "file_sha256": sha256_file(path),
                "canonical_sha256": canonical_sha256(yaml.safe_load(path.read_text())), "model_yaml": spec.config}
    if spec.backend == "rfdetr":
        import rfdetr
        import rfdetr.config as rcfg

        cfg = getattr(rcfg, f"{spec.config}Config")()
        dump = json.loads(cfg.model_dump_json())
        path = Path(rfdetr.__file__).resolve().parent / "config.py"
        return {"path": str(path), "package_path": spec.config_file, "file_sha256": sha256_file(path),
                "config_class": type(cfg).__name__, "canonical_sha256": canonical_sha256(dump),
                "resolution": dump.get("resolution"), "patch_size": dump.get("patch_size"),
                "num_windows": dump.get("num_windows")}
    import mbnas_model_runtime.adapters.etri_hybrid as eh

    base = Path(eh.__file__).resolve().parent
    files = {f: sha256_file(base / f) for f in ("model.py", "boxes.py", "loss.py", "data.py", "postprocess.py")}
    return {"path": str(base / "model.py"), "package_path": spec.config_file, "file_sha256": files["model.py"],
            "module_sha256": files, "canonical_sha256": canonical_sha256(spec.config),
            "notebook_blob_sha1": eh.NOTEBOOK_BLOB_SHA1, "notebook_cells": eh.NOTEBOOK_CELLS}


def _rfdetr_published_md5(filename: str) -> tuple[str | None, str | None]:
    from rfdetr.assets.model_weights import ModelWeights

    for item in ModelWeights:
        asset = item.value
        if getattr(asset, "filename", None) == filename:
            return asset.url, asset.md5_hash
    return None, None


def weights_ref(spec: ModelSpec, weights_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Official weights evidence: VERIFIED / MISSING / MISMATCH / NOT_APPLICABLE."""
    if spec.weights_file is None:
        return {"status": "NOT_APPLICABLE", "reason": "no official pretrained weights for this model"}
    if spec.backend == "rfdetr":
        path = weights_dir / RFDETR_WEIGHTS_SUBDIR / spec.weights_file
        url, md5 = _rfdetr_published_md5(spec.weights_file)
        if not path.is_file():
            return {"status": "MISSING", "filename": spec.weights_file, "path": str(path), "source_url": url,
                    "published_md5": md5}
        observed_md5 = hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 - comparing to the published digest
        ok = md5 is not None and observed_md5 == md5
        return {"status": "VERIFIED" if ok else "MISMATCH", "filename": spec.weights_file, "path": str(path),
                "source_url": url, "published_md5": md5, "observed_md5": observed_md5, "sha256": sha256_file(path),
                "bytes": path.stat().st_size, "digest_basis": "published MD5 (rfdetr ModelWeights); sha256 recorded here"}
    entry = manifest.get(spec.weights_file)
    path = weights_dir / spec.weights_file
    if entry is None or not path.is_file():
        return {"status": "MISSING", "filename": spec.weights_file, "path": str(path),
                "manifest_entry": entry is not None}
    observed = sha256_file(path)
    ok = observed == entry.get("sha256") and path.stat().st_size == entry.get("bytes")
    return {"status": "VERIFIED" if ok else "MISMATCH", "filename": spec.weights_file, "path": str(path),
            "source_url": entry.get("source_url"), "sha256": observed, "manifest_sha256": entry.get("sha256"),
            "bytes": path.stat().st_size, "digest_basis": entry.get("digest_verification")}


def _ultralytics_build(spec: ModelSpec) -> dict[str, Any]:
    import torch

    from mbnas_model_runtime.adapters.common import structural_hash
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    cls = UltralyticsAdapter(spec.family, spec.variant)._model_class()
    out: dict[str, Any] = {"model_class": f"{cls.__module__}.{cls.__name__}"}
    for nc in (80, 1):
        torch.manual_seed(0)
        m = cls(spec.config, nc=nc, verbose=False)
        out[f"params_nc{nc}"] = int(sum(p.numel() for p in m.parameters()))
        if nc == 1:
            out["structural_hash_nc1"] = structural_hash(m)
            out["stride"] = [float(s) for s in m.stride.tolist()] if hasattr(m, "stride") else None
            head = m.model[-1]
            out["head"] = type(head).__name__
            out["end2end_capable"] = getattr(head, "one2one_cv2", None) is not None
    return out


def _ultralytics_pretrained(spec: ModelSpec, weights: dict[str, Any]) -> dict[str, Any]:
    if weights.get("status") != "VERIFIED":
        return {"ok": False, "error": f"weights {weights.get('status')}"}
    from ultralytics.nn.tasks import load_checkpoint

    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    w, ckpt = load_checkpoint(weights["path"])
    m = UltralyticsAdapter(spec.family, spec.variant)._model_class()(spec.config, nc=1, verbose=False)
    sd, csd = m.state_dict(), w.float().state_dict()
    transferred = sum(1 for k, v in csd.items() if k in sd and sd[k].shape == v.shape)
    return {"checkpoint_nc": int(getattr(w, "nc", None) or w.yaml.get("nc", -1)), "transferred_tensors": transferred,
            "model_tensors": len(sd), "checkpoint_version": (ckpt or {}).get("version")}


def _rfdetr_build(spec: ModelSpec) -> dict[str, Any]:
    import rfdetr
    import torch

    from mbnas_model_runtime.adapters.common import structural_hash

    cls = getattr(rfdetr, spec.config)
    torch.manual_seed(0)
    scratch = cls(pretrain_weights=None, num_classes=1, device="cpu")
    net = scratch.model.model
    return {"model_class": f"{type(net).__module__}.{type(net).__name__}",
            "params_nc1": int(sum(p.numel() for p in net.parameters())), "structural_hash_nc1": structural_hash(net),
            "resolution": int(scratch.model_config.resolution), "scratch_init": "pretrain_weights=None"}


def _rfdetr_pretrained(spec: ModelSpec, weights: dict[str, Any]) -> dict[str, Any]:
    if weights.get("status") != "VERIFIED":
        return {"ok": False, "error": f"weights {weights.get('status')}"}
    import rfdetr

    cls = getattr(rfdetr, spec.config)
    m = cls(pretrain_weights=weights["path"], device="cpu")
    net = m.model.model
    return {"params_native": int(sum(p.numel() for p in net.parameters())),
            "num_classes": int(getattr(m.model_config, "num_classes", -1)), "loaded_from": weights["path"]}


def _etri_build(spec: ModelSpec) -> dict[str, Any]:
    from mbnas_model_runtime.adapters.base import ArchSpec
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter

    rep = EtriHybridAdapter().build(ArchSpec(spec.id, spec.family, spec.backend, spec.config), 1, "scratch", 0)
    return {"model_class": spec.import_class, "params_nc1": rep.params, "structural_hash_nc1": rep.architecture_hash,
            "module_counts": rep.module_counts, "notes": rep.notes}


def _onnx_export(spec: ModelSpec, workdir: Path) -> dict[str, Any]:
    """CPU ONNX export of a randomly initialised model (structure/operator support evidence only)."""
    import onnx

    workdir.mkdir(parents=True, exist_ok=True)
    if spec.backend == "ultralytics":
        from ultralytics import RTDETR, YOLO

        cwd = os.getcwd()
        os.chdir(workdir)
        try:
            model = (RTDETR if spec.variant == "rtdetr" else YOLO)(spec.config)
            kwargs = {"nms": False} if spec.family == "YOLO26" else {}
            path = Path(model.export(format="onnx", imgsz=640, simplify=False, device="cpu", **kwargs)).resolve()
        finally:
            os.chdir(cwd)
    elif spec.backend == "rfdetr":
        import rfdetr

        m = getattr(rfdetr, spec.config)(pretrain_weights=None, num_classes=1, device="cpu")
        m.export(output_dir=str(workdir), format="onnx")
        path = sorted(workdir.glob("*.onnx"))[0]
    else:
        import torch

        from mbnas_model_runtime.adapters.etri_hybrid.model import make_detector

        net = make_detector(**{k: v for k, v in spec.config.items()}, num_classes=1).eval()
        path = workdir / "etri.onnx"
        torch.onnx.export(net, torch.zeros(1, 3, 384, 384), str(path), opset_version=17, input_names=["images"],
                          output_names=["pred_logits", "pred_boxes"], dynamo=False)
    model = onnx.load(str(path))
    shapes = {o.name: [d.dim_value or d.dim_param for d in o.type.tensor_type.shape.dim] for o in model.graph.output}
    return {"opset": max(i.version for i in model.opset_import if i.domain in ("", "ai.onnx")), "outputs": shapes,
            "bytes": path.stat().st_size, "sha256": sha256_file(path), "note": "random-init structure export (CPU)"}


def probe_spec(spec: ModelSpec, *, weights_dir: Path, manifest: dict[str, Any], export_onnx: bool) -> dict[str, Any]:
    """Probe one entry; never raises (failures are recorded)."""
    from mbnas_contracts.util.time import utcnow_iso

    rec: dict[str, Any] = {"id": spec.id, "backend": spec.backend, "probed_at": utcnow_iso()}
    rec["config_ref"] = _attempt(lambda: config_ref(spec))
    rec["weights"] = weights_ref(spec, weights_dir, manifest)
    if spec.backend == "ultralytics":
        rec["build"] = _attempt(lambda: _ultralytics_build(spec))
        rec["pretrained_load"] = _attempt(lambda: _ultralytics_pretrained(spec, rec["weights"]))
    elif spec.backend == "rfdetr":
        rec["build"] = _attempt(lambda: _rfdetr_build(spec))
        rec["pretrained_load"] = _attempt(lambda: _rfdetr_pretrained(spec, rec["weights"]))
    else:
        rec["build"] = _attempt(lambda: _etri_build(spec))
        rec["pretrained_load"] = {"ok": None, "reason": "scratch-only model (no official weights)"}
    if export_onnx:
        with tempfile.TemporaryDirectory(prefix=f"onnx-{spec.id}-") as tmp:
            rec["onnx_export"] = _attempt(lambda: _onnx_export(spec, Path(tmp)))
    return rec
