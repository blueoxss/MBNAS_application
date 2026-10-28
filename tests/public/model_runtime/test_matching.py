"""Deterministic one-to-one greedy matching on hand-built boxes."""
from __future__ import annotations

import itertools

from mbnas_model_runtime.evaluation.matching import iou, macro_recall, match

GT = [[0, 0, 10, 10], [20, 20, 30, 30]]


def test_iou_basics() -> None:
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0.0
    assert abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 50 / 150) < 1e-12
    assert iou([0, 0, 0, 0], [0, 0, 0, 0]) == 0.0


def test_one_to_one_and_score_order() -> None:
    preds = [[0, 0, 10, 10], [1, 1, 10, 10]]  # both overlap GT0; higher score wins, the other is FP
    m = match(preds, [0.6, 0.9], [0, 0], GT, [0, 0])
    assert m.tp == [1] and m.fp == [0] and m.fn == [1] and m.pairs[0][:2] == (1, 0)


def test_equal_scores_break_ties_by_index_and_are_permutation_stable() -> None:
    preds = [[0, 0, 10, 10], [0, 0, 10, 10], [20, 20, 30, 30]]
    m = match(preds, [0.5, 0.5, 0.5], [0, 0, 0], GT, [0, 0])
    assert m.tp == [0, 2] and m.fp == [1]
    for perm in itertools.permutations(range(3)):  # same boxes in another order: same counts
        p = [preds[i] for i in perm]
        r = match(p, [0.5] * 3, [0] * 3, GT, [0, 0])
        assert (len(r.tp), len(r.fp), len(r.fn)) == (2, 1, 0)


def test_class_iou_and_conf_thresholds() -> None:
    assert match([[0, 0, 10, 10]], [0.9], [1], GT, [0, 0]).tp == []  # wrong class
    half = [[0, 0, 10, 5]]  # IoU exactly 0.5 with GT0 -> matches (>=)
    assert match(half, [0.9], [0], GT, [0, 0]).tp == [0]
    assert match([[0, 0, 10, 4.9]], [0.9], [0], GT, [0, 0]).tp == []
    low = match([[0, 0, 10, 10]], [0.2], [0], GT, [0, 0])  # below conf 0.25: neither TP nor FP
    assert low.tp == [] and low.fp == [] and low.kept == 0 and low.fn == [0, 1]


def test_best_iou_unmatched_gt_is_chosen() -> None:
    gts = [[0, 0, 10, 10], [2, 0, 12, 10]]
    m = match([[2, 0, 12, 10], [0, 0, 10, 10]], [0.9, 0.8], [0, 0], gts, [0, 0])
    assert sorted((p, g) for p, g, _ in m.pairs) == [(0, 1), (1, 0)]


def test_macro_recall() -> None:
    r, per = macro_recall({0: 1, 1: 0}, {0: 2, 1: 4, 2: 0})
    assert per == {"0": 0.5, "1": 0.0} and r == 0.25
    assert macro_recall({}, {0: 0}) == (None, {})


def _reference(pb, ps, pc, gb, gc, thr=0.5, conf=0.25):
    """Scalar transcription of the rule (oracle for the vectorised implementation)."""
    order = sorted((i for i, s in enumerate(ps) if s >= conf), key=lambda i: (-ps[i], i))
    matched, tp, fp = {}, [], []
    for p in order:
        best, best_iou = -1, -1.0
        for g, b in enumerate(gb):
            if g not in matched and gc[g] == pc[p] and iou(pb[p], b) > best_iou:
                best, best_iou = g, iou(pb[p], b)
        if best >= 0 and best_iou >= thr:
            matched[best] = p
            tp.append(p)
        else:
            fp.append(p)
    return sorted(tp), sorted(fp), [g for g in range(len(gb)) if g not in matched]


def test_vectorised_match_equals_scalar_reference() -> None:
    import random

    rng = random.Random(0)

    def box():
        x, y = rng.uniform(0, 90), rng.uniform(0, 90)
        return [x, y, x + rng.uniform(1, 15), y + rng.uniform(1, 15)]

    for _ in range(200):
        gb = [box() for _ in range(rng.randint(0, 12))]
        gc = [rng.randint(0, 2) for _ in gb]
        pb = [box() for _ in range(rng.randint(0, 30))]
        ps = [round(rng.random(), 1) for _ in pb]
        pc = [rng.randint(0, 2) for _ in pb]
        r = match(pb, ps, pc, gb, gc)
        assert (r.tp, r.fp, r.fn) == _reference(pb, ps, pc, gb, gc)
