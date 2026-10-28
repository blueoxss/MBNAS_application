"""util.canon / util.atomic / util.time contracts."""
from __future__ import annotations

import json
import math
import os
import stat
from enum import Enum

import pytest

from mbnas_contracts.util.atomic import atomic_write_bytes, atomic_write_json, atomic_write_stream, sha256_file
from mbnas_contracts.util.canon import canonical_json, canonical_sha256, sha256_hex
from mbnas_contracts.util.time import format_iso, iso_after, parse_iso, utcnow_iso


def test_canonical_json_sorted_compact_ascii() -> None:
    assert canonical_json({"b": 1, "a": [1, 2], "c": "é"}) == b'{"a":[1,2],"b":1,"c":"\\u00e9"}'


def test_canonical_json_key_order_independent_and_tuples_to_lists() -> None:
    assert canonical_json({"x": (1, 2), "y": {"q": 1, "p": 2}}) == canonical_json({"y": {"p": 2, "q": 1}, "x": [1, 2]})


def test_integral_float_stays_float_and_hash_differs() -> None:
    assert canonical_json({"v": 1.0}) == b'{"v":1.0}'
    assert canonical_sha256({"v": 1}) != canonical_sha256({"v": 1.0})


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"v": [bad]})


def test_non_string_key_and_set_rejected() -> None:
    with pytest.raises(TypeError):
        canonical_json({1: "a"})
    with pytest.raises(TypeError):
        canonical_json({"s": {1, 2}})


def test_enum_values_and_hash_format() -> None:
    class Color(str, Enum):
        RED = "red"

    assert canonical_json({"c": Color.RED}) == b'{"c":"red"}'
    digest = canonical_sha256({"a": 1})
    assert digest == "sha256:" + sha256_hex(b'{"a":1}') and len(digest) == 7 + 64


def test_atomic_write_json_and_mode(tmp_path) -> None:
    p = tmp_path / "sub" / "x.json"
    atomic_write_json(p, {"b": 2, "a": 1})
    assert json.loads(p.read_text()) == {"a": 1, "b": 2}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]


def test_atomic_write_failure_keeps_old_content(tmp_path) -> None:
    p = tmp_path / "f.bin"
    atomic_write_bytes(p, b"old")

    def boom(fh) -> None:
        fh.write(b"partial")
        raise RuntimeError("crash mid-write")

    with pytest.raises(RuntimeError):
        atomic_write_stream(p, boom)
    assert p.read_bytes() == b"old"
    assert [f.name for f in tmp_path.iterdir()] == ["f.bin"]


def test_atomic_json_rejects_nan(tmp_path) -> None:
    with pytest.raises(ValueError):
        atomic_write_json(tmp_path / "n.json", {"v": math.nan})


def test_sha256_file(tmp_path) -> None:
    p = tmp_path / "a"
    p.write_bytes(b"abc")
    assert sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_time_fixed_width_and_ordering() -> None:
    now = utcnow_iso()
    assert len(now) == 27 and now.endswith("Z")
    later = iso_after(now, 0.5)
    assert later > now and len(later) == 27
    assert parse_iso(later) > parse_iso(now)
    assert iso_after("2026-10-06T00:00:59.999999Z", 1) == "2026-10-06T00:01:00.999999Z"


def test_format_iso_rejects_naive() -> None:
    from datetime import datetime

    with pytest.raises(ValueError):
        format_iso(datetime(2026, 1, 1))
