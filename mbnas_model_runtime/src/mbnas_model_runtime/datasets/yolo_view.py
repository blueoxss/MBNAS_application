"""Run-local YOLO dataset views built from split manifests.

Backends never read the protocol's raw split directories. ``build_yolo_view`` materialises exactly the manifest's
samples under ``<view_root>``: images are symlinks to the manifest's source files (realpaths, never modified), labels
are *copied* after their SHA-256 is checked against the manifest (a drifted label raises ``DatasetDrift``). Native
side effects such as ultralytics' ``labels/<split>.cache`` therefore land inside the run directory, not next to the
source dataset. A view is keyed by the split hashes and built in a temporary directory that is renamed into place, so
concurrent jobs either reuse a complete view or build their own.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

from mbnas_model_runtime.datasets.manifest import Manifest
from mbnas_contracts.util.atomic import atomic_write_bytes, sha256_file
from mbnas_contracts.util.canon import canonical_sha256

VIEW_SCHEMA = "mbnas.yolo_view/1"


class DatasetDrift(RuntimeError):
    """A source label no longer matches the manifest the protocol was validated with."""


def view_key(manifests: Mapping[str, Manifest]) -> str:
    """Stable 16-hex key over (split name, split hash) pairs."""
    return canonical_sha256({k: m.split_hash for k, m in sorted(manifests.items())}).split(":", 1)[1][:16]


def _populate(tmp: Path, manifests: Mapping[str, Manifest], names: Mapping[int, str]) -> None:
    for split, manifest in manifests.items():
        for s in manifest.samples:
            ext = Path(s.image).suffix
            img = tmp / "images" / split / f"{s.sample_id}{ext}"
            img.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.path.realpath(s.image), img)
            if s.label is not None:
                if sha256_file(s.label) != s.label_sha256:
                    raise DatasetDrift(f"label of {split}/{s.sample_id} changed since manifest {manifest.split_hash}")
                lbl = tmp / "labels" / split / f"{s.sample_id}.txt"
                lbl.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(s.label, lbl)
    lines = [f"# {VIEW_SCHEMA}: run-local view of manifest splits (images are symlinks, labels copies)",
             f"path: {tmp.name}", *(f"{k}: images/{k}" for k in manifests),
             f"nc: {len(names)}", "names:", *(f"  {k}: {json.dumps(v)}" for k, v in sorted(names.items()))]
    atomic_write_bytes(tmp / "data.yaml", ("\n".join(lines) + "\n").encode())


def build_yolo_view(parent: str | Path, manifests: Mapping[str, Manifest], names: Mapping[int, str]) -> Path:
    """Return ``<parent>/<key>/data.yaml`` (built once; ``manifests`` maps view split name -> Manifest).

    The yaml's ``path`` is rewritten to the final absolute directory after the atomic rename.
    """
    root = Path(parent) / view_key(manifests)
    done = root / "view.json"
    if done.is_file():
        return root / "data.yaml"
    Path(parent).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".view-", dir=parent))
    try:
        _populate(tmp, manifests, names)
        text = (tmp / "data.yaml").read_text().replace(f"path: {tmp.name}", f"path: {root}")
        atomic_write_bytes(tmp / "data.yaml", text.encode())
        meta = {"schema": VIEW_SCHEMA, "splits": {k: m.split_hash for k, m in manifests.items()},
                "counts": {k: len(m.samples) for k, m in manifests.items()}, "names": {str(k): v for k, v in names.items()}}
        atomic_write_bytes(tmp / "view.json", (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode())
        try:
            os.rename(tmp, root)
        except OSError:
            if not done.is_file():
                raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return root / "data.yaml"
