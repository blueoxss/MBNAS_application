"""Tiny synthetic YOLO datasets for tests (moved from the old root ``tests/conftest.py`` ``make_dataset`` fixture).

``build_dataset(data_root, name, ...)`` writes real PNG files plus YOLO label files and a ``data.yaml`` and returns the
paths dict the protocol fixtures use. Everything is SYNTHETIC (solid-colour images, one fixed box per image).

Loaded by ``conftest_common`` under an alias (``mbnas_test_datasets``), never as ``import datasets``: the module name is
fixed by WP-1 contract §9 and must not shadow a site-packages ``datasets`` distribution.
"""
from __future__ import annotations

import json
from pathlib import Path


def png(path: Path, seed: int, size: tuple[int, int] = (32, 24)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)).save(path)


def build_dataset(data_root: Path, name: str = "ds", counts: dict[str, int] | None = None,
                  label: str = "0 0.5 0.5 0.25 0.25\n", names: list[str] | None = None,
                  seed0: int = 1) -> dict[str, str]:
    """make_dataset(name, counts={'train':4,'val':2,'test':2}, label=..., names=['drone']) -> paths dict."""
    root = data_root / name
    counts = counts or {"train": 4, "val": 2, "test": 2}
    seed = seed0
    for split, n in counts.items():
        for i in range(n):
            png(root / "images" / split / f"{split}_{i:03d}.png", seed)
            (root / "labels" / split).mkdir(parents=True, exist_ok=True)
            (root / "labels" / split / f"{split}_{i:03d}.txt").write_text(label)
            seed += 1
    names = names or ["drone"]
    (root / "data.yaml").write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\ntest: images/test\nnames: {json.dumps(names)}\n")
    return {"root": str(root), "dataset_yaml": str(root / "data.yaml"), "train_root": str(root / "images/train"),
            "search_val_root": str(root / "images/val"), "final_eval_root": str(root / "images/test")}
