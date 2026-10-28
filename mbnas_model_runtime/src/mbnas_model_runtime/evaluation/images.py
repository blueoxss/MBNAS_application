"""gt / tp / fn / fp diagnostic panels for fixed samples of one evaluation.

Sample choice is pre-registered, never "best looking": :func:`fixed_sample_ids` takes the first ``n`` sample ids of
the split manifest in sorted order (rule ``first_n_sorted_sample_ids``). For each sample four PNGs are rendered on
the source image (optionally downscaled to ``max_side``) from the evaluator's own matching result:

* ``gt`` — every ground-truth box; ``tp`` — matched predictions (score shown); ``fn`` — unmatched ground truth;
  ``fp`` — unmatched predictions with score >= the recall confidence.

Each panel carries a text header (kind, split, sample id, counts) so it does not rely on colour alone. Files are written
atomically and described as artifact dicts (kind, relpath relative to the run dir, sample_id, split_hash, sha256,
bytes, mime_type, matching meta incl. per-image counts and sample recall/precision; sample recall is ``None``/N/A
when the image has no ground truth).
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from mbnas_contracts.util.atomic import atomic_write_bytes, sha256_file
from mbnas_model_runtime.evaluation.evaluator import gt_xyxy
from mbnas_model_runtime.evaluation.matching import MATCHING_RULE, MatchResult

SAMPLE_RULE = "first_n_sorted_sample_ids"
KINDS = ("gt", "tp", "fn", "fp")
COLOURS = {"gt": (40, 200, 70), "tp": (40, 120, 255), "fn": (235, 50, 50), "fp": (255, 150, 0)}


def fixed_sample_ids(manifest: Any, n: int = 2) -> list[str]:
    return sorted(s.sample_id for s in manifest.samples)[:n]


def _panel(image: Any, kind: str, boxes: Sequence[Sequence[float]], labels: Sequence[str], scale: float,
           header: str) -> bytes:
    from PIL import ImageDraw

    im = image.copy()
    draw = ImageDraw.Draw(im)
    width = max(1, round(max(im.size) / 640))
    for (x1, y1, x2, y2), text in zip(boxes, labels):
        box = [x1 * scale, y1 * scale, x2 * scale, y2 * scale]
        draw.rectangle(box, outline=COLOURS[kind], width=width)
        if text:
            draw.text((box[0] + 1, max(box[1] - 11, 0)), text, fill=COLOURS[kind])
    draw.rectangle([0, 0, im.size[0], 14], fill=(0, 0, 0))
    draw.text((3, 1), header, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def render_sample(sample: Any, pred: Mapping[str, Any], m: MatchResult, out_dir: str | Path, run_dir: str | Path, *,
                  split_hash: str, evaluation_hash: str, checkpoint_sha256: str | None, iou_thr: float,
                  conf_thr: float, max_side: int = 1600, split: str = "search_val") -> list[dict[str, Any]]:
    """Write the four panels of one sample; returns artifact dicts."""
    from PIL import Image

    gt_boxes, _ = gt_xyxy(sample)
    with Image.open(sample.image) as loaded:
        image = loaded.convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    if scale < 1.0:
        image = image.resize((round(image.size[0] * scale), round(image.size[1] * scale)))
    matched_gt = {g for _, g, _ in m.pairs}
    fn = [gt_boxes[g] for g in m.fn]
    sets = {
        "gt": (gt_boxes, ["GT"] * len(gt_boxes)),
        "tp": ([pred["boxes_xyxy"][i] for i in m.tp], [f"TP {pred['scores'][i]:.2f}" for i in m.tp]),
        "fn": (fn, ["FN"] * len(fn)),
        "fp": ([pred["boxes_xyxy"][i] for i in m.fp], [f"FP {pred['scores'][i]:.2f}" for i in m.fp]),
    }
    counts = {"gt": len(gt_boxes), "tp": len(m.tp), "fp": len(m.fp), "fn": len(m.fn), "kept": m.kept,
              "matched_gt": len(matched_gt)}
    sample_recall = counts["tp"] / counts["gt"] if counts["gt"] else None
    sample_precision = counts["tp"] / m.kept if m.kept else None
    out = []
    for kind in KINDS:
        boxes, labels = sets[kind]
        header = (f"{kind.upper()} | split={split} | {sample.sample_id} | GT {counts['gt']} TP {counts['tp']} "
                  f"FP {counts['fp']} FN {counts['fn']} | IoU>={iou_thr} conf>={conf_thr}")
        path = Path(out_dir) / f"{kind}_{sample.sample_id.replace('/', '__')}.png"
        atomic_write_bytes(path, _panel(image, kind, boxes, labels, scale, header))
        out.append({"kind": kind, "relpath": Path(os.path.relpath(path, run_dir)).as_posix(), "path": str(path),
                    "sample_id": sample.sample_id, "split_hash": split_hash, "sha256": sha256_file(path),
                    "bytes": path.stat().st_size, "mime_type": "image/png", "state": "READY",
                    "matching": {"sample_id": sample.sample_id, "split": split, "rule": MATCHING_RULE,
                                 "iou": iou_thr, "conf": conf_thr, "counts": counts,
                                 "boxes_drawn": len(boxes), "sample_recall": sample_recall,
                                 "sample_precision": sample_precision, "evaluation_hash": evaluation_hash,
                                 "checkpoint_sha256": checkpoint_sha256, "sample_rule": SAMPLE_RULE,
                                 "render_scale": scale}})
    return out


def render_fixed_samples(result: Mapping[str, Any], manifest: Any, out_dir: str | Path, run_dir: str | Path, *,
                         checkpoint_sha256: str | None, iou_thr: float, conf_thr: float, n: int = 2,
                         split: str = "search_val") -> list[dict[str, Any]]:
    """Panels for :func:`fixed_sample_ids` using the evaluator's in-memory ``matches``/``predictions``; ``split`` labels
    every panel (header ``split=<name>`` and ``matching.split``), e.g. ``final_eval`` for the final evaluation."""
    by_id = {s.sample_id: s for s in manifest.samples}
    out: list[dict[str, Any]] = []
    for sid in fixed_sample_ids(manifest, n):
        out.extend(render_sample(by_id[sid], result["predictions"][sid], result["matches"][sid], out_dir, run_dir,
                                 split_hash=result["split_hash"], evaluation_hash=result["evaluation_hash"],
                                 checkpoint_sha256=checkpoint_sha256, iou_thr=iou_thr, conf_thr=conf_thr,
                                 split=split))
    return out
