"""REAL GPU: the ultralytics adapter's predict path (fixed-shape letterbox/stretch, native decode/NMS, scaling back to
source pixels) reproduces the native ultralytics predictor on official COCO weights (geometry/head-pinning check).

For each native detection with conf >= 0.5 there must be an adapter detection of the same class with IoU >= 0.8.
YOLO26 is compared with native ``nms=False`` (the pinned one-to-one head).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu, pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA GPU required")]

WEIGHTS = Path(os.environ["MBNAS_TEST_WEIGHTS_DIR"])  # A4: host weights dir of this test run (conftest_common default)
CASES = [("yolo11n", "YOLO11", "detect", "native", {}), ("yolo26n", "YOLO26", "detect", "e2e_one2one_nms_false",
                                                          {"nms": False}),
         ("rtdetr-l", "Hybrid", "rtdetr", "native", {})]


@pytest.mark.parametrize("stem,family,variant,head,native_kw", CASES)
def test_adapter_predict_matches_native(tmp_path: Path, stem, family, variant, head, native_kw) -> None:
    import ultralytics
    from ultralytics import RTDETR, YOLO
    from ultralytics.nn.tasks import load_checkpoint

    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter
    from mbnas_model_runtime.evaluation.matching import iou
    from mbnas_model_runtime import checkpoint as ck

    weights = WEIGHTS / f"{stem}.pt"
    if not weights.is_file():
        pytest.skip(f"{weights} missing")
    image = str(Path(ultralytics.__file__).parent / "assets" / "bus.jpg")
    model, _ = load_checkpoint(str(weights))
    meta = {"yaml": {k: v for k, v in model.yaml.items() if k != "yaml_file"}, "nc": 80,
            "names": {str(k): v for k, v in model.names.items()}, "variant": variant, "family": family,
            "inference_head": head}
    snap = ck.eval_snapshot_path(tmp_path, 0)
    ck.save_eval_snapshot(snap, model.float().state_dict(), model_meta=meta, epochs_done=0, hashes={})
    adapter = UltralyticsAdapter(family, variant)
    ours = adapter.predict(adapter.load_eval_model(snap, "cuda:0"), [("bus", image)],
                           {"conf": 0.001, "iou": 0.7, "max_det": 300, "shape": [640, 640], "batch": 1,
                            "precision": "FP32"})[0]
    native = (RTDETR if variant == "rtdetr" else YOLO)(str(weights)).predict(image, imgsz=640, conf=0.5,
                                                                             device=0, verbose=False, **native_kw)[0]
    nb = native.boxes
    assert len(nb) >= 3
    for box, cls in zip(nb.xyxy.tolist(), nb.cls.int().tolist()):
        best = max((iou(box, b) for b, c in zip(ours["boxes_xyxy"], ours["classes"]) if c == cls), default=0.0)
        assert best >= 0.8, f"{stem}: native box {box} class {cls} best IoU {best:.3f}"
