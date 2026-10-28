"""Split manifests: every image of a split with sha256, size, validated YOLO boxes and a content-addressed hash.

재구현(2026-10-15), 원본 누락, 근거: docs/orchestration/PLAN.md(datasets/ 항목: sample id, sha256 image+label,
class ids, box validation, duplicates, cross-split leakage → split_hash), tests/unit/test_datasets.py(manifest 케이스),
protocol/validator.py(issues·duplicates·counts·cross_split_overlap 사용), evaluation/·training/·adapters/(Manifest
필드 사용), tests/unit/test_protocol_validator.py, tests/qa/test_validator_negatives.py.

Conventions (all derived from those callers/tests):

* Images are found recursively below the split root (dot-directories skipped; ultralytics ``*.cache`` and every
  non-image file are ignored); ``sample_id`` = image path relative to the root without extension (``/`` kept).
* Label of ``<prefix>/images/<rest>/<name>.<ext>`` is ``<prefix>/labels/<rest>/<name>.txt`` (last ``/images/``
  component), see :func:`label_path_for`. A missing label file = background-only image (``label`` is None).
* ``split_hash`` = canonical sha256 over (class mapping hash, sorted (sample_id, image_sha256, label_sha256)); it
  does not depend on where the data lives. Duplicate = same image bytes inside one split; overlap = same bytes in
  two splits (:func:`cross_split_overlap`).
* Images whose real path (symlinks followed) leaves ``allow_roots`` are excluded with a PATH_OUTSIDE_ALLOWLIST issue.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mbnas_contracts.util.atomic import atomic_write_json, sha256_file
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.paths import is_within

IMAGE_EXTS = frozenset({".bmp", ".dng", ".jpeg", ".jpg", ".mpo", ".png", ".tif", ".tiff", ".webp", ".pfm", ".heic"})
MANIFEST_FORMAT = "mbnas.manifest/1"
MAX_LABEL_BYTES = 16 << 20


class Issue(BaseModel):
    """A problem found while building a manifest (``line`` is 1-based inside the label file)."""

    model_config = ConfigDict(extra="forbid")
    code: str
    sample_id: str | None = None
    line: int | None = None
    message: str = ""


class Sample(BaseModel):
    """One image: ``boxes`` are valid (class, cx, cy, w, h) rows, normalised to the image."""

    model_config = ConfigDict(extra="forbid")
    sample_id: str
    image: str
    label: str | None = None
    image_sha256: str
    label_sha256: str | None = None
    width: int
    height: int
    boxes: list[tuple[int, float, float, float, float]] = Field(default_factory=list)


class Manifest(BaseModel):
    """All samples of one split plus hashes, counts, issues and duplicate groups."""

    model_config = ConfigDict(extra="forbid")
    format: str = MANIFEST_FORMAT
    roots: list[str] = Field(default_factory=list)
    names: dict[int, str]
    class_ids: list[int]
    class_mapping_hash: str
    split_hash: str
    samples: list[Sample]
    counts: dict[str, Any]
    issues: list[Issue] = Field(default_factory=list)
    duplicates: list[list[str]] = Field(default_factory=list)


def class_mapping_hash(names: Mapping[int, str]) -> str:
    """``sha256:`` of the {id: name} mapping (order independent).

    재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py::test_build_manifest_tiny, validator.dataset_hash.
    """
    return canonical_sha256({"class_mapping": {str(int(k)): str(v) for k, v in sorted(names.items())}})


def label_path_for(image_path: str) -> str:
    """Label file of an image: the last ``/images/`` path component becomes ``/labels/``, extension → ``.txt``.

    Paths without an ``images`` component fall back to the image's own directory.

    재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py::test_label_rule (ultralytics img2label_paths).
    """
    stem = os.path.splitext(image_path)[0]
    sep = f"{os.sep}images{os.sep}"
    idx = stem.rfind(sep)
    if idx < 0:
        return stem + ".txt"
    return stem[:idx] + f"{os.sep}labels{os.sep}" + stem[idx + len(sep):] + ".txt"


# -- scanning ------------------------------------------------------------------------------------------------------
def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _list_images(root: str | Sequence[str]) -> tuple[list[tuple[str, str]], list[str], list[Issue]]:
    """((sample_id, image path) sorted, root list, issues) of a split directory or an explicit image list."""
    if isinstance(root, (str, os.PathLike)):
        base = os.path.abspath(os.fspath(root))
        if not os.path.isdir(base):
            raise FileNotFoundError(f"split root {base} is not a directory")
        found: list[tuple[str, str]] = []
        for cur, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if _is_image(f):
                    full = os.path.join(cur, f)
                    found.append((Path(os.path.splitext(os.path.relpath(full, base))[0]).as_posix(), full))
        roots = [base]
    else:
        paths = [os.path.abspath(os.fspath(p)) for p in root]
        if not paths:
            raise ValueError("an image list needs at least one image")
        top = os.path.commonpath([os.path.dirname(p) for p in paths])
        found = [(Path(os.path.splitext(os.path.relpath(p, top))[0]).as_posix(), p) for p in paths]
        roots = []
    found.sort()
    issues: list[Issue] = []
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sid, path in found:
        if sid in seen:
            issues.append(Issue(code="SAMPLE_ID_COLLISION", sample_id=sid,
                                message=f"{sid}: another image with the same name stem exists; this one is excluded"))
            continue
        seen.add(sid)
        unique.append((sid, path))
    return unique, roots, issues


def _parse_label(text: str, sample_id: str, names: Mapping[int, str]) -> tuple[list[tuple[int, float, float, float, float]], list[Issue]]:
    boxes: list[tuple[int, float, float, float, float]] = []
    issues: list[Issue] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()

        def bad(code: str, why: str, lineno: int = lineno) -> None:
            issues.append(Issue(code=code, sample_id=sample_id, line=lineno, message=f"{sample_id}:{lineno}: {why}"))

        if len(parts) != 5:
            bad("LABEL_INVALID", f"expected 5 fields (class cx cy w h), found {len(parts)}")
            continue
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            bad("LABEL_INVALID", "non-numeric field")
            continue
        if not all(math.isfinite(v) for v in vals):
            bad("LABEL_INVALID", "non-finite value")
            continue
        _, cx, cy, w, h = vals
        if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            bad("LABEL_INVALID", "box values must satisfy 0<=cx,cy<=1 and 0<w,h<=1")
            continue
        cls = vals[0]
        if not cls.is_integer() or int(cls) not in names:
            bad("CLASS_MAP_MISMATCH", f"class {parts[0]} is not in the class mapping {sorted(names)}")
            continue
        boxes.append((int(cls), cx, cy, w, h))
    return boxes, issues


def _allowed(path: str, roots: Sequence[str] | None) -> bool:
    if roots is None:
        return True
    real = os.path.realpath(path)
    return any(is_within(real, r) for r in roots)


def build_manifest(root: str | os.PathLike[str] | Sequence[str], *, names: Mapping[int, str],
                   allow_roots: Sequence[str] | None = None) -> Manifest:
    """Scan a split directory (or an explicit image list) into a :class:`Manifest`.

    재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py(test_build_manifest_tiny, test_label_row_validation,
    test_duplicates_overlap_and_bad_image, test_symlink_escape_excluded, test_manifest_from_image_list) 와
    benchmark/handlers.py(``build_manifest(root, names=names)``).
    """
    names = {int(k): str(v) for k, v in names.items()}
    real_roots = None if allow_roots is None else [os.path.realpath(r) for r in allow_roots]
    listing, roots, issues = _list_images(root if not isinstance(root, os.PathLike) else os.fspath(root))
    samples: list[Sample] = []
    per_class: Counter[int] = Counter()
    n_boxes = n_labelled = n_empty = n_missing = 0
    from PIL import Image  # local import: Pillow is only needed when a manifest is really built

    for sid, image in listing:
        if not _allowed(image, real_roots):
            issues.append(Issue(code="PATH_OUTSIDE_ALLOWLIST", sample_id=sid,
                                message=f"{sid}: image resolves outside the allowed dataset roots; excluded"))
            continue
        try:
            with Image.open(image) as im:
                width, height = im.size
            image_sha = sha256_file(image)
        except (OSError, ValueError, ImportError, Image.DecompressionBombError) as exc:
            issues.append(Issue(code="IMAGE_INVALID", sample_id=sid,
                                message=f"{sid}: unreadable image ({type(exc).__name__})"))
            continue
        label = label_path_for(image)
        boxes: list[tuple[int, float, float, float, float]] = []
        label_sha: str | None = None
        if os.path.isfile(label):
            if not _allowed(label, real_roots):
                issues.append(Issue(code="PATH_OUTSIDE_ALLOWLIST", sample_id=sid,
                                    message=f"{sid}: label resolves outside the allowed dataset roots; excluded"))
                continue
            if os.path.getsize(label) > MAX_LABEL_BYTES:
                issues.append(Issue(code="LABEL_INVALID", sample_id=sid, line=1, message=f"{sid}: label file too large"))
                continue
            data = Path(label).read_bytes()
            label_sha = sha256_file(label)
            boxes, label_issues = _parse_label(data.decode("utf-8", errors="replace"), sid, names)
            issues.extend(label_issues)
            n_labelled += 1
            if not data.strip():
                n_empty += 1
        else:
            label = None  # type: ignore[assignment]
            n_missing += 1
        for b in boxes:
            per_class[b[0]] += 1
        n_boxes += len(boxes)
        samples.append(Sample(sample_id=sid, image=image, label=label, image_sha256=image_sha, label_sha256=label_sha,
                              width=width, height=height, boxes=boxes))
    by_hash: dict[str, list[str]] = defaultdict(list)
    for s in samples:
        by_hash[s.image_sha256].append(s.sample_id)
    duplicates = sorted(sorted(ids) for ids in by_hash.values() if len(ids) > 1)
    cm_hash = class_mapping_hash(names)
    split_hash = canonical_sha256({"format": "mbnas.split/1", "class_mapping_hash": cm_hash,
                                   "samples": [[s.sample_id, s.image_sha256, s.label_sha256] for s in samples]})
    counts = {"images": len(samples), "labelled": n_labelled, "missing_labels": n_missing, "empty_labels": n_empty,
              "boxes": n_boxes, "per_class": {str(k): v for k, v in sorted(per_class.items())}, "issues": len(issues)}
    return Manifest(roots=roots, names=names, class_ids=sorted(names), class_mapping_hash=cm_hash,
                    split_hash=split_hash, samples=samples, counts=counts, issues=issues, duplicates=duplicates)


def cross_split_overlap(manifests: Mapping[str, Manifest]) -> list[dict[str, Any]]:
    """Images with identical bytes present in two or more splits.

    One entry per shared image: ``{"image_sha256", "splits": [sorted split names], "sample_ids": {split: [ids]}}``.

    재구현(2026-10-15), 원본 누락, 근거: validator.manifests(`"/".join(o["splits"])`, `o["sample_ids"]`),
    test_duplicates_overlap_and_bad_image, tests/qa/test_validator_negatives.py(내용 기준, 이름 무관).
    """
    where: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for split, m in manifests.items():
        for s in m.samples:
            where[s.image_sha256][split].append(s.sample_id)
    out = []
    for sha in sorted(where):
        per = where[sha]
        if len(per) > 1:
            out.append({"image_sha256": sha, "splits": sorted(per), "sample_ids": {k: sorted(v) for k, v in sorted(per.items())}})
    return out


# -- caching service -----------------------------------------------------------------------------------------------
def _stat_sig(path: str) -> list[int]:
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return [-1, -1]


class DatasetService:
    """Builds manifests under the dataset allow-roots and caches them as ``<output_root>/manifests/<hex>.json``.

    The cache is keyed twice: the JSON file by ``split_hash`` (also read by export/bundle.py), and a small index
    ``_index/<signature>.json`` that maps a cheap (path, size, mtime) signature of the split to its ``split_hash``
    so an unchanged split is not re-hashed. Any change of size/mtime, names or allow-roots → fresh scan.

    재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py::test_dataset_service_cache,
    ``DatasetService(workdir, [roots])`` 호출(scripts/*), ``DatasetService.from_settings`` (api/services.py,
    training/context.py), export/bundle.py(manifests/ 경로).
    """

    def __init__(self, output_root: str, allow_roots: Sequence[str]) -> None:
        self.output_root = str(output_root)
        self.allow_roots = [str(r) for r in allow_roots]
        self.cache_dir = Path(self.output_root) / "manifests"

    @classmethod
    def from_settings(cls, settings: Any) -> "DatasetService":
        """Service rooted at ``settings.output_root`` with ``settings.dataset_roots`` as the allowlist.

        재구현(2026-10-15), 원본 누락, 근거: api/services.py, training/context.py, tests/conftest.py(ctx_factory).
        """
        return cls(settings.output_root, list(settings.dataset_roots))

    def _signature(self, root: str, names: Mapping[int, str]) -> str:
        rows: list[Any] = []
        listing, _, _ = _list_images(root)
        for sid, image in listing:
            label = label_path_for(image)
            rows.append([sid, _stat_sig(image), _stat_sig(label) if os.path.exists(label) else None])
        return canonical_sha256({"root": os.path.abspath(root), "names": class_mapping_hash(names),
                                 "allow": sorted(self.allow_roots), "files": rows}).split(":", 1)[1]

    def manifest_for(self, root: str, names: Mapping[int, str]) -> Manifest:
        """Manifest of the split directory ``root`` (cached; rebuilt when any file's size/mtime changed).

        재구현(2026-10-15), 원본 누락, 근거: validator.manifests, training/context.split_manifests, test_dataset_service_cache
        (캐시 파일 ``<output_root>/manifests/<hex>.json`` 이 ``Manifest.model_validate_json`` 로 왕복).
        """
        root = os.path.abspath(os.fspath(root))
        names = {int(k): str(v) for k, v in names.items()}
        sig = self._signature(root, names)
        index = self.cache_dir / "_index" / f"{sig}.json"
        try:
            split_hash = json.loads(index.read_text(encoding="utf-8"))["split_hash"]
            cached = Manifest.model_validate_json((self.cache_dir / f"{split_hash.split(':', 1)[1]}.json").read_text(encoding="utf-8"))
            if cached.split_hash == split_hash and cached.roots == [root] and cached.names == names:
                return cached
        except (OSError, ValueError, KeyError, IndexError):
            pass
        manifest = build_manifest(root, names=names, allow_roots=self.allow_roots)
        try:
            atomic_write_json(self.cache_dir / f"{manifest.split_hash.split(':', 1)[1]}.json",
                              manifest.model_dump(mode="json"), indent=None)
            atomic_write_json(index, {"split_hash": manifest.split_hash, "root": root}, indent=None)
        except OSError:
            pass  # the cache is an optimisation; a read-only output root must not break validation
        return manifest
