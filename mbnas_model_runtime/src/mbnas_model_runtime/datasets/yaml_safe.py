"""Safe parsing of an ultralytics-style ``data.yaml`` (never executes anything).

재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py(yaml 케이스), tests/qa/test_security_negatives.py
(python 태그 거부, 2 MiB 초과 거부, download 키 무시), protocol/validator.py(dy.root/splits/names/nc/ignored_keys 사용).

Rules: file ≤ 1 MiB; ``yaml.safe_load`` only (python tags are an error); top level must be a mapping; ``train`` and
``val`` are required plain strings (``test`` optional); ``path`` (optional string) is the dataset root, default = the
yaml's directory; relative split entries are joined to the root; ``names`` (list or {int: str}, contiguous from 0)
and/or ``nc`` must describe the same class set. Unknown keys (``download``, ...) are reported in ``ignored_keys``
and never interpreted.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml

MAX_YAML_BYTES = 1 << 20
MAX_ALIASES = 64
KNOWN_KEYS = frozenset({"path", "train", "val", "test", "names", "nc"})
SPLIT_KEYS = ("train", "val", "test")


class DatasetYamlError(ValueError):
    """The dataset yaml is unreadable or violates the schema (``code`` is always DATASET_YAML_INVALID)."""

    code = "DATASET_YAML_INVALID"


@dataclass(frozen=True)
class DatasetYaml:
    """Parsed dataset yaml: ``root``, resolved ``splits`` (only the keys present), ``names`` {id: name}, ``nc``."""

    root: str
    splits: dict[str, str]
    names: dict[int, str]
    nc: int
    ignored_keys: tuple[str, ...] = ()


def _fail(msg: str) -> DatasetYamlError:
    return DatasetYamlError(msg)


def _names(raw: Any, nc: Any) -> dict[int, str]:
    out: dict[int, str] = {}
    if raw is None:
        if nc is None:
            raise _fail("dataset yaml needs 'names' or 'nc'")
        if isinstance(nc, bool) or not isinstance(nc, int) or nc < 1:
            raise _fail("'nc' must be a positive integer")
        return {i: str(i) for i in range(nc)}
    items = list(enumerate(raw)) if isinstance(raw, list) else list(raw.items()) if isinstance(raw, dict) else None
    if items is None:
        raise _fail("'names' must be a list or a mapping of class id to name")
    for key, val in items:
        if isinstance(key, bool) or not isinstance(key, int):
            raise _fail(f"class id {key!r} in 'names' must be an integer")
        if isinstance(val, bool) or not isinstance(val, (str, int)):
            raise _fail(f"class name for id {key} must be a string")
        out[key] = str(val)
    if not out:
        raise _fail("'names' is empty")
    if sorted(out) != list(range(len(out))):
        raise _fail("class ids in 'names' must be contiguous from 0")
    if nc is not None and (isinstance(nc, bool) or nc != len(out)):
        raise _fail(f"'nc' ({nc!r}) does not match the {len(out)} entries of 'names'")
    return dict(sorted(out.items()))


def load_dataset_yaml(path: str, *, required_splits: tuple[str, ...] = ("train", "val")) -> DatasetYaml:
    """Parse and validate ``path``; raises :class:`DatasetYamlError` for every problem.

    재구현(2026-10-15), 원본 누락, 근거: tests/unit/test_datasets.py::test_yaml_* 와 qa/test_security_negatives.py.
    """
    try:
        size = os.stat(path).st_size
        if size > MAX_YAML_BYTES:
            raise _fail(f"dataset yaml is {size} bytes (limit {MAX_YAML_BYTES})")
        with open(path, "rb") as fh:
            text = fh.read(MAX_YAML_BYTES + 1).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(f"cannot read dataset yaml: {exc}") from exc
    try:
        aliases = sum(1 for ev in yaml.parse(text, Loader=yaml.SafeLoader) if isinstance(ev, yaml.AliasEvent))
        if aliases > MAX_ALIASES:
            raise _fail(f"dataset yaml uses {aliases} aliases (limit {MAX_ALIASES})")
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail(f"dataset yaml is not valid safe YAML: {str(exc).splitlines()[0] if str(exc) else 'error'}") from exc
    if not isinstance(doc, dict):
        raise _fail("dataset yaml must be a mapping")
    here = os.path.dirname(os.path.abspath(path))
    if "path" in doc and not isinstance(doc["path"], str):
        raise _fail("'path' must be a string")
    root = os.path.normpath(os.path.join(here, doc["path"])) if doc.get("path") else here
    splits: dict[str, str] = {}
    for key in SPLIT_KEYS:
        if key not in doc:
            if key in required_splits:
                raise _fail(f"dataset yaml needs a '{key}' entry")
            continue
        val = doc[key]
        if not isinstance(val, str) or not val:
            raise _fail(f"'{key}' must be a single directory string")
        splits[key] = os.path.normpath(os.path.join(root, val))
    names = _names(doc.get("names"), doc.get("nc"))
    ignored = tuple(sorted(str(k) for k in doc if k not in KNOWN_KEYS))
    return DatasetYaml(root=root, splits=splits, names=names, nc=len(names), ignored_keys=ignored)
