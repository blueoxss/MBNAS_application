"""Latency operating point (console.benchmark.postprocess) and NMS runtime record — CPU checks, plus one REAL GPU
check in a fresh child interpreter (like the worker child) that the CUDA torchvision NMS path is active."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mbnas_model_runtime.evaluation import evaluator as ev
from mbnas_model_runtime.evaluation.latency import postprocess_runtime, resolved_postprocess, scope_label



def test_scope_label_and_resolution_defaults() -> None:
    assert resolved_postprocess({}) == {"conf": 0.25, "iou": 0.7, "max_det": 300}
    assert resolved_postprocess({"postprocess": {"conf": 0.4}}) == {"conf": 0.4, "iou": 0.7, "max_det": 300}
    assert scope_label(resolved_postprocess({})) == "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"
    assert scope_label({"conf": 0.5, "iou": 0.45, "max_det": 100}) == "forward_and_postprocess(conf=0.5,iou=0.45,max_det=100)"


def test_evaluation_hash_includes_the_latency_scope_label() -> None:
    base = ev.EvalConfig((64, 64), 8, "FP16", "A100")
    a = ev.EvalConfig((64, 64), 8, "FP16", "A100", latency_scope=scope_label(resolved_postprocess({})))
    b = ev.EvalConfig((64, 64), 8, "FP16", "A100", latency_scope=scope_label({"conf": 0.5, "iou": 0.7, "max_det": 300}))
    hashes = {ev.evaluation_hash(c, "sha256:s") for c in (base, a, b)}
    assert len(hashes) == 3  # a different operating point is a different (non-comparable) evaluation condition
    assert a.signature("sha256:s")["latency_scope"] == "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"
    assert "latency_scope" not in base.signature("sha256:s")  # older configs keep their historical hash
    assert a.predict_cfg()["conf"] == 0.001 and a.predict_cfg()["max_det"] == 300  # AP floor stays separate


def test_postprocess_runtime_rules(monkeypatch) -> None:
    assert postprocess_runtime({"nms": False}, "cuda:0") == "no_nms"
    assert postprocess_runtime({"nms": True, "nms_impl": "python_loop (cpu)"}, "cuda:0") == "python_loop"
    monkeypatch.delitem(sys.modules, "torchvision", raising=False)
    assert postprocess_runtime({"nms": True, "nms_impl": "ultralytics.utils.nms"}, "cuda:0") == "python_loop"
    monkeypatch.setitem(sys.modules, "torchvision", object())
    assert postprocess_runtime({"nms": True, "nms_impl": "ultralytics.utils.nms"}, "cuda:0") == "torchvision_cuda_nms"
    assert postprocess_runtime({"nms": True}, "cpu") == "python_loop"


def test_every_adapter_latency_fns_takes_the_operating_point() -> None:
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter
    from mbnas_model_runtime.adapters.rfdetr_adapter import RfdetrAdapter
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    for cls in (UltralyticsAdapter, EtriHybridAdapter, RfdetrAdapter):
        param = inspect.signature(cls.latency_fns).parameters.get("postprocess")
        assert param is not None and param.default is None, cls.__name__


def test_panels_are_labelled_with_their_split(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from PIL import Image

    from mbnas_model_runtime.evaluation.images import render_fixed_samples
    from mbnas_model_runtime.evaluation.matching import match

    img = tmp_path / "a.png"
    Image.new("RGB", (32, 32)).save(img)
    sample = SimpleNamespace(sample_id="a", image=str(img), width=32, height=32, boxes=[(0, 0.5, 0.5, 0.5, 0.5)])
    manifest = SimpleNamespace(samples=[sample])
    pred = {"sample_id": "a", "boxes_xyxy": [[8.0, 8.0, 24.0, 24.0]], "scores": [0.9], "classes": [0]}
    result = {"predictions": {"a": pred}, "matches": {"a": match(pred["boxes_xyxy"], pred["scores"], pred["classes"],
                                                                  [[8.0, 8.0, 24.0, 24.0]], [0], iou_thr=0.5, conf_thr=0.25)},
              "split_hash": "sha256:final", "evaluation_hash": "sha256:e"}
    arts = render_fixed_samples(result, manifest, tmp_path / "run" / "img", tmp_path / "run", checkpoint_sha256="c",
                                iou_thr=0.5, conf_thr=0.25, n=2, split="final_eval")
    assert len(arts) == 4 and {a["matching"]["split"] for a in arts} == {"final_eval"}
    m = arts[0]["matching"]
    assert m["sample_id"] == "a" and m["checkpoint_sha256"] == "c" and {"tp", "fp", "fn", "gt"} <= set(m["counts"])
    from mbnas_contracts.schemas.records import Artifact

    Artifact.model_validate({"kind": "gt", "url": "/x", "matching": m})


def test_rfdetr_native_housekeeping_keeps_only_the_referenced_copy(tmp_path: Path) -> None:
    from mbnas_model_runtime.adapters.rfdetr_adapter import native_housekeeping
    from mbnas_contracts.util.atomic import sha256_file

    native, ckpt = tmp_path / "native", tmp_path / "ckpt"
    native.mkdir()
    ckpt.mkdir()
    for name in ("last.ckpt", "last_ema.pth", "checkpoint_best_ema.pth", "checkpoint_best_total.pth", "metrics.csv",
                 "training_config.json"):
        (native / name).write_bytes(b"n" * 7)
    ref = ckpt / "native_e0002.ckpt"
    ref.write_bytes(b"r" * 9)
    bad = native_housekeeping(native, {"path": str(ref), "sha256": "0" * 64})
    assert bad["deleted"] == [] and "mismatch" in bad["skipped_reason"]  # never delete without a verified source
    rec = native_housekeeping(native, {"path": str(ref), "sha256": sha256_file(ref)})
    assert sorted(Path(d["path"]).name for d in rec["deleted"]) == [
        "checkpoint_best_ema.pth", "checkpoint_best_total.pth", "last.ckpt", "last_ema.pth"]
    assert rec["bytes_freed"] == 28 and ref.exists() and rec["kept_reference"]["path"] == str(ref)
    assert sorted(p.name for p in native.iterdir()) == ["housekeeping.jsonl", "metrics.csv", "training_config.json"]
    lines = (native / "housekeeping.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[-1])["bytes_freed"] == 28


CHILD_SCRIPT = r"""
import json, sys
assert "torchvision" not in sys.modules, "precondition: a fresh child has not imported torchvision"
import torch
from ultralytics.nn.tasks import DetectionModel
from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter
from mbnas_runner.evaluation.handlers import measure_latency
model = DetectionModel("yolo11n.yaml", nc=10, verbose=False).cuda().eval()
before = "torchvision" in sys.modules
lat = measure_latency(UltralyticsAdapter("YOLO11"), model, (640, 640), "FP16", "cuda:0", sys.argv[1],
                      {"warmup": 2, "repeats": 3, "postprocess": {"conf": 0.25, "iou": 0.7, "max_det": 300}},
                      ["forward_only", "forward_and_postprocess", "end_to_end"])
import torchvision
calls = []
original = torchvision.ops.nms
def counting_nms(boxes, scores, iou):
    calls.append(str(boxes.device))
    return original(boxes, scores, iou)
torchvision.ops.nms = counting_nms  # ultralytics looks the function up at call time
fns = UltralyticsAdapter("YOLO11").latency_fns(model, (640, 640), "FP16", "cuda:0", sys.argv[1],
                                                postprocess={"conf": 0.0, "iou": 0.7, "max_det": 300})
fns["forward_and_postprocess"]()
print(json.dumps({"before": before, "runtime": lat["postprocess_runtime"], "scope": lat["latency_scope"],
                  "point": lat["operating_point"], "torchvision": lat["torchvision"],
                  "p50": lat["scopes"]["forward_and_postprocess"]["p50_ms"], "nms_calls": calls}))
"""


@pytest.mark.gpu
def test_cuda_torchvision_nms_is_active_in_a_fresh_worker_child(tmp_path: Path) -> None:
    """REAL GPU, fresh interpreter like ``worker.child``: torchvision is imported before NMS, CUDA NMS is used."""
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA GPU required")
    from PIL import Image

    image = tmp_path / "sample.jpg"
    Image.new("RGB", (640, 480), (90, 120, 150)).save(image)
    proc = subprocess.run([sys.executable, "-B", "-c", CHILD_SCRIPT, str(image)],
                          capture_output=True, text=True, timeout=600, cwd=str(tmp_path),
                          env={"PATH": "/usr/bin:/bin", "CUDA_VISIBLE_DEVICES": "0", "YOLO_OFFLINE": "1",
                               "YOLO_CONFIG_DIR": str(tmp_path / "ycfg"), "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["before"] is False  # the child had not imported torchvision before the latency path
    assert out["runtime"] == "torchvision_cuda_nms" and out["torchvision"]
    assert out["scope"] == "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"
    assert out["point"] == {"conf": 0.25, "iou": 0.7, "max_det": 300} and out["p50"] > 0
    assert out["nms_calls"] and set(out["nms_calls"]) == {"cuda:0"}  # torchvision.ops.nms really ran on CUDA tensors
