"""Canonical JSON and SHA-256 helpers (canonicalization id ``mbnas-canon/1``).

Contract: ``canonical_json`` serialises with sorted keys, compact separators, ASCII-only output and no NaN/Infinity.
Before serialisation tuples become lists, enums become their value, and mapping keys must be strings. Integral
floats stay floats (``1.0`` stays ``1.0``), so ``1`` and ``1.0`` hash differently on purpose: the protocol keeps the
type the client sent. Sets are rejected because their order is undefined.
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

CANONICALIZATION_ID = "mbnas-canon/1"


def _normalise(obj: Any, path: str = "$") -> Any:
    if isinstance(obj, enum.Enum):
        return _normalise(obj.value, path)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"non-finite float at {path}")
        return obj
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"non-string key {k!r} at {path}")
            out[k] = _normalise(v, f"{path}.{k}")
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalise(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    raise TypeError(f"unsupported type {type(obj).__name__} at {path}")


def canonical_json(obj: Any) -> bytes:
    """Return the canonical UTF-8 (ASCII) JSON encoding of ``obj``."""
    return json.dumps(
        _normalise(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(obj: Any) -> str:
    """``"sha256:<hex>"`` of the canonical JSON of ``obj``."""
    return "sha256:" + sha256_hex(canonical_json(obj))
