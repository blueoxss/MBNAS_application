"""Manifest -> COCO json / RF-DETR layout round trip (source pixels, class-index category order, no source edits)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from mbnas_model_runtime.datasets.coco_convert import ANNOTATION_FILE, manifest_to_coco, write_coco_layout
from mbnas_model_runtime.datasets.manifest import build_manifest


def test_round_trip_boxes_and_categories(make_dataset) -> None:
    paths = make_dataset("c", counts={"train": 2}, label="1 0.5 0.5 0.25 0.5\n0 0.25 0.25 0.5 0.5\n",
                         names=["car", "none"])
    m = build_manifest(paths["train_root"], names={0: "car", 1: "none"})
    coco = manifest_to_coco(m)
    assert [c["id"] for c in coco["categories"]] == [0, 1]
    assert all(c["supercategory"] not in ("car", "none") for c in coco["categories"])  # no parent/child confusion
    img = coco["images"][0]
    assert (img["width"], img["height"]) == (32, 24) and img["sample_id"] == m.samples[0].sample_id
    ann = [a for a in coco["annotations"] if a["image_id"] == 1]
    x, y, w, h = ann[0]["bbox"]
    # back to YOLO normalised cx, cy, w, h == the manifest's first box
    back = (ann[0]["category_id"], (x + w / 2) / 32, (y + h / 2) / 24, w / 32, h / 24)
    assert all(abs(a - b) < 1e-9 for a, b in zip(back, m.samples[0].boxes[0]))
    assert coco["info"]["split_hash"] == m.split_hash


def test_layout_symlinks_and_reuse(make_dataset, tmp_path: Path) -> None:
    paths = make_dataset("l", counts={"train": 2, "val": 1})
    before = {p: os.stat(p).st_mtime_ns for p in Path(paths["root"]).rglob("*") if p.is_file()}
    train = build_manifest(paths["train_root"], names={0: "drone"})
    val = build_manifest(paths["search_val_root"], names={0: "drone"})
    root = write_coco_layout({"train": train, "valid": val}, tmp_path)
    for split, m in (("train", train), ("valid", val)):
        doc = json.loads((root / split / ANNOTATION_FILE).read_text())
        assert len(doc["images"]) == len(m.samples)
        for img, s in zip(doc["images"], m.samples):
            f = root / split / img["file_name"]
            assert f.is_symlink() and os.path.realpath(f) == os.path.realpath(s.image)
    assert write_coco_layout({"train": train, "valid": val}, tmp_path) == root
    after = {p: os.stat(p).st_mtime_ns for p in Path(paths["root"]).rglob("*") if p.is_file()}
    assert before == after  # source dataset untouched
