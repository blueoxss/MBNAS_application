"""ETRI YOLO-format split resolution, label parsing and letterbox dataset.

Provenance: extracted VERBATIM (top-level definitions only, no edits to their bodies) from
``ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb`` (git blob sha1 629ce856b4c6ea337edc8943b9e65ed91ef60e27, ETRI_NAS HEAD
1d3a459aa6356e11b43c0e58bc778030ba78f950), cell indices [6]: IMAGE_SUFFIXES, _lexical_absolute, resolve_yolo_split, _label_path, _read_yolo_labels, YoloDetectionDataset. The notebook was read, never executed or modified.
Only the module imports below were added. Family label: ETRI controlled CNN+Transformer hybrid (not SOTA,
not pure Transformer).
"""
# ruff: noqa
import math
import os
import random
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image, ImageOps
from torch.utils.data import Dataset

from mbnas_model_runtime.adapters.etri_hybrid.boxes import cxcywh_to_xyxy


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _lexical_absolute(path):
    """Normalize dots without following image symlinks into a raw-data directory."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def resolve_yolo_split(data_yaml, split):
    """Read dirs, image lists, text manifests; text paths are manifest-relative."""
    yaml_path = Path(data_yaml).expanduser().resolve()
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or split not in config:
        raise ValueError(f"Missing YOLO split {split!r} in {yaml_path}")
    raw_names = config.get("names")
    if isinstance(raw_names, dict):
        if any(not math.isfinite(float(k)) or float(k) != int(float(k)) for k in raw_names):
            raise ValueError("YOLO names keys must be finite integer class IDs")
        keyed = {int(float(k)): str(v) for k, v in raw_names.items()}
        if len(keyed) != len(raw_names) or sorted(keyed) != list(range(len(keyed))):
            raise ValueError("YOLO names IDs must be contiguous, starting at 0")
        names = [keyed[i] for i in range(len(keyed))]
    elif isinstance(raw_names, list):
        names = [str(name) for name in raw_names]
    elif raw_names is None and isinstance(config.get("nc"), int):
        names = [f"class_{i}" for i in range(config["nc"])]
    else:
        raise ValueError("Provide a nonempty names list/dict or integer nc in YOLO YAML")
    if not names or ("nc" in config and int(config["nc"]) != len(names)):
        raise ValueError("YOLO names and nc are empty or inconsistent")
    root = Path(str(config.get("path") or ".")).expanduser()
    root = _lexical_absolute(root if root.is_absolute() else yaml_path.parent / root)
    specs = config[split] if isinstance(config[split], list) else [config[split]]
    images = []
    for spec in specs:
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(f"Split paths must be strings, received {spec!r}")
        source = Path(spec).expanduser()
        source = _lexical_absolute(source if source.is_absolute() else root / source)
        if source.is_dir():
            images.extend(p for p in source.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
        elif source.is_file() and source.suffix.lower() == ".txt":
            for line in source.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    item = Path(line).expanduser()
                    images.append(_lexical_absolute(item if item.is_absolute() else source.parent / item))
        elif source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
            images.append(source)
        else:
            raise FileNotFoundError(f"YOLO split input not found/supported: {source}")
    images = sorted(set(_lexical_absolute(p) for p in images))
    if not images:
        raise ValueError(f"Split {split!r} contains no images")
    missing = [str(p) for p in images if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES]
    if missing:
        raise FileNotFoundError(f"Invalid manifest image entries: {missing[:5]}")
    return images, names


def _label_path(image_path):
    parts = list(Path(image_path).parts)
    indices = [i for i, part in enumerate(parts) if part == "images"]
    if not indices:
        raise ValueError(f"Cannot infer annotation path without an 'images' directory: {image_path}. "
                         "Use images/... and matching labels/... or implement an explicit label mapping.")
    parts[indices[-1]] = "labels"
    return Path(*parts).with_suffix(".txt")


def _read_yolo_labels(label, num_classes):
    rows = []
    if not label.exists():
        return rows
    for line_no, line in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            values = [float(x) for x in line.split()]
        except ValueError as exc:
            raise ValueError(f"Nonnumeric YOLO label: {label}:{line_no}") from exc
        if len(values) != 5 or not all(math.isfinite(x) for x in values):
            raise ValueError(f"Expected 5 finite detection-label values: {label}:{line_no}")
        cls, x, y, bw, bh = values
        if cls != int(cls) or not 0 <= cls < num_classes:
            raise ValueError(f"Class ID out of range: {label}:{line_no}")
        if (not 0 <= x <= 1 or not 0 <= y <= 1 or not 0 < bw <= 1 or not 0 < bh <= 1
                or x - bw / 2 < -1e-5 or y - bh / 2 < -1e-5
                or x + bw / 2 > 1 + 1e-5 or y + bh / 2 > 1 + 1e-5):
            raise ValueError(f"Invalid normalized YOLO box: {label}:{line_no}")
        rows.append((int(cls), x, y, bw, bh))
    return rows


class YoloDetectionDataset(Dataset):
    def __init__(self, image_files, imgsz, num_classes, augment=False):
        self.image_files = [_lexical_absolute(p) for p in image_files]
        self.image_paths = self.image_files
        self.imgsz, self.num_classes, self.augment = int(imgsz), int(num_classes), bool(augment)
        if self.imgsz < 1 or self.num_classes < 1:
            raise ValueError("imgsz and num_classes must be positive")
        self.label_files = [_label_path(p) for p in self.image_files]
        self.missing_labels = [p for p in self.label_files if not p.exists()]

    def __len__(self):
        return len(self.image_files)

    def __getitem__(self, index):
        path, label = self.image_files[index], self.label_files[index]
        with Image.open(path) as loaded:
            image = loaded.convert("RGB")
        width, height = image.size
        rows = _read_yolo_labels(label, self.num_classes)
        labels = torch.tensor([r[0] for r in rows], dtype=torch.long)
        boxes = torch.tensor([r[1:] for r in rows], dtype=torch.float32).reshape(-1, 4)
        orig_boxes = cxcywh_to_xyxy(boxes) * torch.tensor([width, height, width, height])
        if self.augment and random.random() < 0.5:
            image = ImageOps.mirror(image)
            orig_boxes[:, [0, 2]] = width - orig_boxes[:, [2, 0]]
        scale = min(self.imgsz / width, self.imgsz / height)
        resized = (max(1, round(width * scale)), max(1, round(height * scale)))
        left, top = (self.imgsz - resized[0]) // 2, (self.imgsz - resized[1]) // 2
        image = image.resize(resized, resample=Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (self.imgsz, self.imgsz), (114, 114, 114))
        canvas.paste(image, (left, top))
        tensor = torch.from_numpy(np.asarray(canvas, dtype=np.float32).copy()).permute(2, 0, 1) / 255
        transformed = orig_boxes * scale + torch.tensor([left, top, left, top])
        centers = (transformed[:, :2] + transformed[:, 2:]) / 2
        extents = transformed[:, 2:] - transformed[:, :2]
        target = {"boxes": torch.cat([centers, extents], dim=1) / self.imgsz,
                  "labels": labels, "image_id": index, "orig_size": (height, width),
                  "orig_boxes": orig_boxes, "path": str(path), "scale": scale,
                  "pad": (left, top), "input_size": (self.imgsz, self.imgsz)}
        return tensor, target
