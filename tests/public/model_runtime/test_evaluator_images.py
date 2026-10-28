"""Common evaluator (pycocotools AP + fixed recall), evaluation_hash semantics, gt/tp/fn/fp panels, latency stats."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mbnas_model_runtime.datasets.manifest import build_manifest
from mbnas_model_runtime.evaluation import evaluator as ev
from mbnas_model_runtime.evaluation.images import fixed_sample_ids, render_fixed_samples
from mbnas_model_runtime.evaluation.latency import _percentile, stats

# 2026-10-21 (D4): the AP path imports pycocotools (ml extras); skip instead of failing where it is absent.
needs_pycocotools = pytest.mark.skipif(importlib.util.find_spec("pycocotools") is None, reason="needs pycocotools (ml extras)")

LABEL = "0 0.5 0.5 0.5 0.5\n0 0.2 0.2 0.2 0.2\n"


@pytest.fixture
def manifest(make_dataset):
    paths = make_dataset("e", counts={"val": 3}, label=LABEL)
    return build_manifest(paths["search_val_root"], names={0: "drone"})


def cfg(**kw) -> ev.EvalConfig:
    return ev.EvalConfig(input_shape=(640, 640), batch=8, precision="FP16", device_class="A100", **kw)


def perfect(manifest) -> list[dict]:
    out = []
    for s in manifest.samples:
        boxes, classes = ev.gt_xyxy(s)
        out.append({"sample_id": s.sample_id, "boxes_xyxy": boxes, "scores": [0.9] * len(boxes), "classes": classes})
    return out


@needs_pycocotools
def test_perfect_predictions(manifest) -> None:
    res = ev.evaluate_predictions(perfect(manifest), manifest, cfg())
    assert res["ap"] == pytest.approx(1.0) and res["ap_50"] == pytest.approx(1.0) and res["recall"] == 1.0
    assert res["counts"]["tp"] == res["counts"]["gt"] == 6 and res["counts"]["fp"] == 0
    assert all(p["recall"] == 1.0 for p in res["per_sample"])


@needs_pycocotools
def test_no_predictions_is_measured_zero_and_missing_samples_fail(manifest) -> None:
    empty = [{"sample_id": s.sample_id, "boxes_xyxy": [], "scores": [], "classes": []} for s in manifest.samples]
    res = ev.evaluate_predictions(empty, manifest, cfg())
    assert res["ap"] == 0.0 and res["no_predictions"] is True and res["recall"] == 0.0
    with pytest.raises(ValueError, match="missing"):
        ev.evaluate_predictions(empty[:1], manifest, cfg())


@needs_pycocotools
def test_no_ground_truth_is_invalid(make_dataset) -> None:
    paths = make_dataset("bg", counts={"val": 2}, label="")
    m = build_manifest(paths["search_val_root"], names={0: "drone"})
    preds = [{"sample_id": s.sample_id, "boxes_xyxy": [[0, 0, 4, 4]], "scores": [0.9], "classes": [0]}
             for s in m.samples]
    res = ev.evaluate_predictions(preds, m, cfg())
    assert res["ap"] is None and res["recall"] is None and res["invalid_reason"] == "NO_GROUND_TRUTH"


@needs_pycocotools
def test_evaluation_hash_is_condition_signature(manifest) -> None:
    h = ev.evaluation_hash(cfg(), manifest.split_hash)
    assert h == ev.evaluate_predictions(perfect(manifest), manifest, cfg())["evaluation_hash"]
    assert h != ev.evaluation_hash(cfg(max_det=100), manifest.split_hash)
    assert h != ev.evaluation_hash(ev.EvalConfig((640, 640), 8, "FP32", "A100"), manifest.split_hash)
    assert h != ev.evaluation_hash(cfg(), "sha256:other")
    assert h == ev.evaluation_hash(cfg(extra={"iou": 0.6}), manifest.split_hash)  # adapter postprocess not in hash


@needs_pycocotools
def test_max_det_keeps_highest_scores(manifest) -> None:
    preds = perfect(manifest)
    preds[0] = {**preds[0], "boxes_xyxy": preds[0]["boxes_xyxy"] + [[0, 0, 1, 1]] * 5,
                "scores": preds[0]["scores"] + [0.01] * 5, "classes": preds[0]["classes"] + [0] * 5}
    res = ev.evaluate_predictions(preds, manifest, cfg(max_det=2))
    assert res["predictions"][manifest.samples[0].sample_id]["scores"] == [0.9, 0.9]


@needs_pycocotools
def test_panels(manifest, tmp_path: Path) -> None:
    preds = perfect(manifest)
    preds[0]["boxes_xyxy"][0] = [0, 0, 1, 1]  # one miss -> 1 FN + 1 FP on the first sample
    res = ev.evaluate_predictions(preds, manifest, cfg())
    arts = render_fixed_samples(res, manifest, tmp_path / "run" / "imgs", tmp_path / "run", checkpoint_sha256="abc",
                                iou_thr=0.5, conf_thr=0.25, n=2)
    assert len(arts) == 8 and [a["kind"] for a in arts[:4]] == ["gt", "tp", "fn", "fp"]
    assert {a["sample_id"] for a in arts} == set(fixed_sample_ids(manifest, 2))
    first = arts[:4]
    assert first[0]["matching"]["counts"] == {"gt": 2, "tp": 1, "fp": 1, "fn": 1, "kept": 2, "matched_gt": 1}
    assert first[0]["matching"]["sample_recall"] == 0.5 and first[0]["relpath"].startswith("imgs/")
    assert all(Path(a["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and len(a["sha256"]) == 64 for a in arts)


def test_latency_stats() -> None:
    xs = [float(i) for i in range(1, 101)]
    assert _percentile(xs, 0.5) == pytest.approx(50.5) and _percentile(xs, 0.95) == pytest.approx(95.05)
    s = stats([3.0, 1.0, 2.0])
    assert s["p50_ms"] == 2.0 and s["mean_ms"] == 2.0 and s["n"] == 3
