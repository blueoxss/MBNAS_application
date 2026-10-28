"""Common evaluator: COCO AP@[.50:.95] / AP50 (pycocotools) and fixed-rule recall on one split manifest.

* Ground truth comes from the split manifest (normalised YOLO boxes -> **source-image pixels**); predictions come from
  ``adapter.predict`` in the common format (xyxy source pixels, score, class) at ``conf_floor`` with at most
  ``max_det`` boxes per image, highest scores first.
* AP: ``COCOeval(iouType="bbox")`` with IoU thresholds .50:.05:.95 and ``maxDets=[1, 10, max_det]`` (dense aerial
  scenes need more than COCO's 100). A model that outputs no box at all gets a *measured* AP of 0.0
  (``no_predictions=True``); a split with no ground-truth box gives ``ap=None`` (invalid, never 0).
* Recall: :mod:`.matching` rule at IoU 0.50 and score >= 0.25, per class TP/GT, macro mean over classes with GT.
* ``evaluation_hash`` = canonical SHA-256 of the evaluator *conditions* (version, split hash, IoU thresholds, recall
  IoU/conf, class aggregation, max_det, conf floor, resolved input shape, eval batch, precision, device class and,
  when set, the latency scope label with its post-processing operating point). It excludes candidate/checkpoint
  identity, so two evaluations are comparable iff their hashes are equal.
"""
from __future__ import annotations

import contextlib
import io
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from mbnas_model_runtime.datasets.coco_convert import manifest_to_coco
from mbnas_model_runtime.datasets.manifest import Manifest
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_model_runtime.evaluation.matching import MATCHING_RULE, MatchResult, macro_recall, match

EVALUATOR_VERSION = "mbnas-evaluator/1"
IOU_THRESHOLDS = tuple(round(0.5 + 0.05 * i, 2) for i in range(10))


@dataclass(frozen=True)
class EvalConfig:
    input_shape: tuple[int, int]
    batch: int
    precision: str
    device_class: str
    conf_floor: float = 0.001
    max_det: int = 300
    recall_iou: float = 0.5
    recall_conf: float = 0.25
    class_aggregation: str = "macro"
    extra: dict[str, Any] = field(default_factory=dict)  # adapter postprocess overrides (not part of the hash)
    latency_scope: str | None = None  # e.g. forward_and_postprocess(conf=0.25,iou=0.7,max_det=300); hashed when set

    def signature(self, split_hash: str) -> dict[str, Any]:
        sig = {"evaluator_version": EVALUATOR_VERSION, "split_hash": split_hash, "iou_thresholds": list(IOU_THRESHOLDS),
               "recall_iou": self.recall_iou, "recall_conf": self.recall_conf,
               "class_aggregation": self.class_aggregation, "matching_rule": MATCHING_RULE, "max_det": self.max_det,
               "conf_floor": self.conf_floor, "resolved_input_shape": [int(v) for v in self.input_shape],
               "batch": int(self.batch), "precision": self.precision, "device_class": self.device_class}
        if self.latency_scope is not None:  # the latency operating point is part of the comparable conditions
            sig["latency_scope"] = self.latency_scope
        return sig

    def predict_cfg(self) -> dict[str, Any]:
        return {"conf": self.conf_floor, "max_det": self.max_det, "shape": list(self.input_shape),
                "batch": self.batch, "precision": self.precision, **self.extra}


def evaluation_hash(cfg: EvalConfig, split_hash: str) -> str:
    return canonical_sha256(cfg.signature(split_hash))


def metric_definitions(cfg: EvalConfig) -> dict[str, Any]:
    return {"ap": "COCO AP@[.50:.95] (pycocotools, maxDets[2]=%d), fraction 0-1" % cfg.max_det,
            "ap_50": "COCO AP@.50, fraction 0-1",
            "recall": f"recall at IoU {cfg.recall_iou}, score >= {cfg.recall_conf}, {cfg.class_aggregation} over classes "
                      f"with GT, rule {MATCHING_RULE}",
            "coordinates": "source image pixels", "evaluator_version": EVALUATOR_VERSION}


def gt_xyxy(sample: Any) -> tuple[list[list[float]], list[int]]:
    """Manifest boxes of one sample in source pixels."""
    w, h = sample.width, sample.height
    boxes = [[(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h]
             for _, cx, cy, bw, bh in sample.boxes]
    return boxes, [int(b[0]) for b in sample.boxes]


def _top(pred: Mapping[str, Any], max_det: int) -> dict[str, Any]:
    order = sorted(range(len(pred["scores"])), key=lambda i: (-float(pred["scores"][i]), i))[:max_det]
    return {"sample_id": pred["sample_id"], "boxes_xyxy": [list(map(float, pred["boxes_xyxy"][i])) for i in order],
            "scores": [float(pred["scores"][i]) for i in order], "classes": [int(pred["classes"][i]) for i in order]}


def _coco_ap(gt: dict[str, Any], preds: Sequence[Mapping[str, Any]], image_ids: Mapping[str, int],
             max_det: int) -> dict[str, Any]:
    import numpy as np
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    results = []
    for p in preds:
        for (x1, y1, x2, y2), s, c in zip(p["boxes_xyxy"], p["scores"], p["classes"]):
            results.append({"image_id": image_ids[p["sample_id"]], "category_id": int(c),
                            "bbox": [x1, y1, x2 - x1, y2 - y1], "score": float(s)})
    if not gt["annotations"]:
        return {"ap": None, "ap_50": None, "reason": "NO_GROUND_TRUTH"}
    if not results:
        return {"ap": 0.0, "ap_50": 0.0, "no_predictions": True}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        coco_gt = COCO()
        coco_gt.dataset = gt
        coco_gt.createIndex()
        coco_dt = coco_gt.loadRes(results)
        ev = COCOeval(coco_gt, coco_dt, iouType="bbox")
        ev.params.iouThrs = np.array(IOU_THRESHOLDS)
        ev.params.maxDets = [1, 10, max_det]
        ev.evaluate()
        ev.accumulate()
    # COCOeval.summarize() hard-codes maxDets=100 for AP@[.5:.95]; read the accumulated arrays at maxDets[2] instead.
    prec = ev.eval["precision"]  # [T iou, R recall, K class, A area(all,s,m,l), M maxDets]
    rec = ev.eval["recall"]  # [T, K, A, M]

    def mean(x) -> float | None:  # noqa: ANN001
        valid = x[x > -1]
        return float(valid.mean()) if valid.size else None

    t50, t75 = IOU_THRESHOLDS.index(0.5), IOU_THRESHOLDS.index(0.75)
    stats = {"ap": mean(prec[:, :, :, 0, 2]), "ap_50": mean(prec[t50, :, :, 0, 2]), "ap_75": mean(prec[t75, :, :, 0, 2]),
             "ap_small": mean(prec[:, :, :, 1, 2]), "ap_medium": mean(prec[:, :, :, 2, 2]),
             "ap_large": mean(prec[:, :, :, 3, 2]), "ar": mean(rec[:, :, 0, 2]), "max_det": max_det}
    per_class = {str(cat): mean(prec[:, :, k, 0, 2]) for k, cat in enumerate(ev.params.catIds)}
    return {"ap": stats["ap"], "ap_50": stats["ap_50"], "coco_stats": stats, "per_class_ap": per_class}


def evaluate_predictions(preds: Sequence[Mapping[str, Any]], manifest: Manifest, cfg: EvalConfig) -> dict[str, Any]:
    """Metrics of common-format predictions against ``manifest`` (see module docstring)."""
    by_id = {p["sample_id"]: _top(p, cfg.max_det) for p in preds}
    missing = [s.sample_id for s in manifest.samples if s.sample_id not in by_id]
    if missing:
        raise ValueError(f"predictions missing for {len(missing)} samples, e.g. {missing[:3]}")
    gt = manifest_to_coco(manifest, category_id_offset=0)
    image_ids = {img["sample_id"]: img["id"] for img in gt["images"]}
    coco = _coco_ap(gt, list(by_id.values()), image_ids, cfg.max_det)
    tp_c: dict[int, int] = defaultdict(int)
    gt_c: dict[int, int] = defaultdict(int)
    per_sample, matches = [], {}
    totals = {"images": len(manifest.samples), "gt": 0, "tp": 0, "fp": 0, "fn": 0, "predictions": 0, "kept": 0}
    for s in manifest.samples:
        p = by_id[s.sample_id]
        boxes, classes = gt_xyxy(s)
        m: MatchResult = match(p["boxes_xyxy"], p["scores"], p["classes"], boxes, classes,
                               iou_thr=cfg.recall_iou, conf_thr=cfg.recall_conf)
        matches[s.sample_id] = m
        for c in classes:
            gt_c[c] += 1
        for pi, gi, _ in m.pairs:
            tp_c[classes[gi]] += 1
        n_gt = len(boxes)
        totals.update({"gt": totals["gt"] + n_gt, "tp": totals["tp"] + len(m.tp), "fp": totals["fp"] + len(m.fp),
                       "fn": totals["fn"] + len(m.fn), "predictions": totals["predictions"] + len(p["scores"]),
                       "kept": totals["kept"] + m.kept})
        per_sample.append({"sample_id": s.sample_id, "gt": n_gt, "tp": len(m.tp), "fp": len(m.fp), "fn": len(m.fn),
                           "kept": m.kept, "recall": (len(m.tp) / n_gt) if n_gt else None,
                           "precision": (len(m.tp) / m.kept) if m.kept else None})
    recall, per_class_recall = macro_recall(dict(tp_c), dict(gt_c))
    return {"ap": coco["ap"], "ap_50": coco["ap_50"], "recall": recall, "per_class_recall": per_class_recall,
            "per_class_ap": coco.get("per_class_ap"), "coco_stats": coco.get("coco_stats"),
            "no_predictions": bool(coco.get("no_predictions", False)), "invalid_reason": coco.get("reason"),
            "counts": totals, "per_sample": per_sample, "split_hash": manifest.split_hash,
            "evaluation_hash": evaluation_hash(cfg, manifest.split_hash), "signature": cfg.signature(manifest.split_hash),
            "metric_definitions": metric_definitions(cfg), "matches": matches, "predictions": by_id}


def evaluate(adapter: Any, model: Any, manifest: Manifest, cfg: EvalConfig) -> dict[str, Any]:
    """Predict every manifest sample with ``adapter.predict`` and score it; adds ``timings.predict_s``."""
    t0 = time.perf_counter()
    preds = adapter.predict(model, [(s.sample_id, s.image) for s in manifest.samples], cfg.predict_cfg())
    t1 = time.perf_counter()
    out = evaluate_predictions(preds, manifest, cfg)
    out["timings"] = {"predict_s": round(t1 - t0, 4), "score_s": round(time.perf_counter() - t1, 4)}
    return out


def public(result: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe subset of an evaluation result (drops in-memory match objects and raw predictions)."""
    return {k: v for k, v in result.items() if k not in ("matches", "predictions")}


def config_dict(cfg: EvalConfig) -> dict[str, Any]:
    return asdict(cfg)
