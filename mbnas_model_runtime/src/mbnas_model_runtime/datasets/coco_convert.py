"""Manifest → COCO json / RF-DETR directory layout (source ids preserved; source files never modified).

COCO boxes are ``[x, y, w, h]`` in **source pixels**; image ids are 1-based in sample order; category ids are class ids
plus ``category_id_offset`` in class order; the ``supercategory`` is a fixed placeholder that never equals a class name.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_model_runtime.datasets.manifest import Manifest

ANNOTATION_FILE = "_annotations.coco.json"
LAYOUT_SCHEMA = "mbnas.coco_layout/1"


def _supercategory(names: Mapping[int, str]) -> str:
    sup = "none"
    while sup in set(names.values()):
        sup = "_" + sup
    return sup


def _file_name(sample_id: str, image: str) -> str:
    return sample_id.replace("/", "__") + os.path.splitext(image)[1]


def manifest_to_coco(manifest: Manifest, *, category_id_offset: int = 0) -> dict[str, Any]:
    """COCO detection dict of a manifest (``info.split_hash`` ties it to the manifest)."""
    sup = _supercategory(manifest.names)
    images, annotations = [], []
    ann_id = 1
    for idx, s in enumerate(manifest.samples, start=1):
        images.append({"id": idx, "file_name": _file_name(s.sample_id, s.image), "width": s.width, "height": s.height,
                       "sample_id": s.sample_id})
        for cls, cx, cy, w, h in s.boxes:
            bw, bh = w * s.width, h * s.height
            annotations.append({"id": ann_id, "image_id": idx, "category_id": cls + category_id_offset,
                                "bbox": [(cx - w / 2) * s.width, (cy - h / 2) * s.height, bw, bh], "area": bw * bh,
                                "iscrowd": 0})
            ann_id += 1
    return {"info": {"description": "mbnas split manifest", "split_hash": manifest.split_hash,
                     "class_mapping_hash": manifest.class_mapping_hash, "category_id_offset": category_id_offset},
            "images": images, "annotations": annotations,
            "categories": [{"id": cid + category_id_offset, "name": manifest.names[cid], "supercategory": sup}
                           for cid in sorted(manifest.names)]}


def write_coco_json(manifest: Manifest, path: str | os.PathLike[str], *, category_id_offset: int = 0) -> Path:
    """Write :func:`manifest_to_coco` atomically to ``path`` and return it."""
    return atomic_write_json(path, manifest_to_coco(manifest, category_id_offset=category_id_offset), indent=None)


def write_coco_layout(manifests: Mapping[str, Manifest], parent: str | os.PathLike[str]) -> Path:
    """RF-DETR layout ``<parent>/coco-<key>/<split>/{<images as symlinks>, _annotations.coco.json}``; returns its root.

    The key is derived from the split names and hashes, so an identical call reuses the finished directory.
    Image files are symlinks to the manifest's real source files (never copied or edited).
    """
    key = canonical_sha256({k: m.split_hash for k, m in sorted(manifests.items())}).split(":", 1)[1][:16]
    parent = Path(parent)
    root = parent / f"coco-{key}"
    if (root / "layout.json").is_file():
        return root
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".coco-", dir=parent))
    try:
        for split, m in manifests.items():
            (tmp / split).mkdir()
            for s in m.samples:
                os.symlink(os.path.realpath(s.image), tmp / split / _file_name(s.sample_id, s.image))
            (tmp / split / ANNOTATION_FILE).write_text(json.dumps(manifest_to_coco(m)), encoding="utf-8")
        (tmp / "layout.json").write_text(json.dumps({"schema": LAYOUT_SCHEMA, "splits": {k: m.split_hash for k, m in manifests.items()}},
                                                    sort_keys=True), encoding="utf-8")
        try:
            os.rename(tmp, root)
        except OSError:
            if not (root / "layout.json").is_file():
                raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return root
