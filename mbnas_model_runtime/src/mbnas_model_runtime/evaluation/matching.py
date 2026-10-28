"""Deterministic one-to-one greedy matching (the fixed recall / gt-tp-fn-fp rule).

Rule ``greedy_conf_desc_iou_v1``: keep predictions with ``score >= conf``; order them by score descending, ties by
the original prediction index (stable); each prediction is matched to the **unmatched** ground truth of the **same
class** with the highest IoU (ties -> lowest GT index) if that IoU ``>= iou``. Unmatched kept predictions are false
positives, unmatched ground truths false negatives. Boxes are ``[x1, y1, x2, y2]`` in the same pixel space.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

MATCHING_RULE = "greedy_conf_desc_iou_v1"


@dataclass(frozen=True)
class MatchResult:
    tp: list[int]  # prediction indices (original order) that matched
    fp: list[int]  # kept prediction indices that did not match
    fn: list[int]  # GT indices without a match
    pairs: list[tuple[int, int, float]] = field(default_factory=list)  # (pred, gt, iou)
    kept: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """IoU of two xyxy boxes (0 for empty union)."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(iw, 0.0) * max(ih, 0.0)
    union = max(a[2] - a[0], 0.0) * max(a[3] - a[1], 0.0) + max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0) - inter
    return inter / union if union > 0 else 0.0


def iou_matrix(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Any:
    """``[len(a), len(b)]`` IoU matrix (numpy, float64); same arithmetic as :func:`iou`."""
    import numpy as np

    A = np.asarray(a, dtype=np.float64).reshape(-1, 4)
    B = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    iw = np.minimum(A[:, None, 2], B[None, :, 2]) - np.maximum(A[:, None, 0], B[None, :, 0])
    ih = np.minimum(A[:, None, 3], B[None, :, 3]) - np.maximum(A[:, None, 1], B[None, :, 1])
    inter = np.clip(iw, 0, None) * np.clip(ih, 0, None)
    area_a = np.clip(A[:, 2] - A[:, 0], 0, None) * np.clip(A[:, 3] - A[:, 1], 0, None)
    area_b = np.clip(B[:, 2] - B[:, 0], 0, None) * np.clip(B[:, 3] - B[:, 1], 0, None)
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1.0), 0.0)


def match(pred_boxes: Sequence[Sequence[float]], pred_scores: Sequence[float], pred_classes: Sequence[int],
          gt_boxes: Sequence[Sequence[float]], gt_classes: Sequence[int], *, iou_thr: float = 0.5,
          conf_thr: float = 0.25) -> MatchResult:
    """Apply :data:`MATCHING_RULE` to one image (vectorised IoU, sequential greedy assignment)."""
    import numpy as np

    keep = [i for i, s in enumerate(pred_scores) if s >= conf_thr]
    order = sorted(keep, key=lambda i: (-float(pred_scores[i]), i))
    ious = iou_matrix([pred_boxes[i] for i in order], gt_boxes) if order and len(gt_boxes) else None
    gcls = np.asarray([int(c) for c in gt_classes], dtype=np.int64)
    free = np.ones(len(gt_boxes), dtype=bool)
    tp, fp, pairs = [], [], []
    for row, p in enumerate(order):
        cand = free & (gcls == int(pred_classes[p])) if len(gt_boxes) else free
        if ious is not None and cand.any():
            masked = np.where(cand, ious[row], -1.0)
            best = int(np.argmax(masked))  # first maximum -> lowest GT index on ties
            if masked[best] >= iou_thr:
                free[best] = False
                tp.append(p)
                pairs.append((p, best, float(masked[best])))
                continue
        fp.append(p)
    fn = [int(g) for g in np.flatnonzero(free)]
    return MatchResult(sorted(tp), sorted(fp), fn, pairs, len(keep))


def macro_recall(per_class_tp: dict[int, int], per_class_gt: dict[int, int]) -> tuple[float | None, dict[str, Any]]:
    """Mean over classes with GT > 0 of TP_c / GT_c; ``None`` when no class has ground truth."""
    per = {str(c): per_class_tp.get(c, 0) / n for c, n in sorted(per_class_gt.items()) if n > 0}
    return (sum(per.values()) / len(per) if per else None), per
