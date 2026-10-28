"""Parity of the vendored ETRI operators with the original (WP-1 contract §6.11, DD-A12, DD-A29). CPU only.

Child A loads the snapshot original ``build/source_snapshot/yolo_ga_runtime/mixed_blocks.py`` by file (synthetic package,
its ``__init__`` not executed), registers it with its own AST patch + exec and builds the yolo11n/yolo26n seed variants
(native, cnn_backbone, transformer_backbone, plus an extra NASGlobalTransformer variant for the +2 repeat path) from the
original ``make_mixed_seed_variants``. Child B builds the same YAML dicts after
``mbnas_model_runtime.operators.ensure_registered()``. Equal: module class-name sequences, parameter counts,
``structural_hash`` after mapping the operator module prefix ``yolo_ga_runtime.mixed_blocks`` <->
``mbnas_model_runtime.operators.etri_mixed_blocks`` (raw equality where no operator is used), per-layer rows, state and
64 px forward output (shapes and values). Child B must not load any ``yolo_ga_runtime`` module.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("ultralytics")

REPO = Path(__file__).resolve().parents[3]
ETRI_DIR = REPO / "build" / "source_snapshot" / "yolo_ga_runtime"  # read-only input snapshot (owner only)
ORIGINAL_SHA256 = "4233dd324e755440d1bc223241fe2e0c95ca37f71e4c3aac8513778491496e70"
CHILD = REPO / "tests" / "_shared" / "operators_parity.py"
VARIANTS = {f"{f}:{v}" for f in ("yolo11n", "yolo26n")
            for v in ("native", "cnn_backbone", "transformer_backbone", "transformer_global_extra")}


def _child_module():
    spec = importlib.util.spec_from_file_location("mbnas_test_operators_parity", CHILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run(*args: str) -> None:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-I", "-B", str(CHILD), *args], env=env, capture_output=True, text=True,
                          timeout=900)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-4000:]


@pytest.fixture(scope="module")
def parity(tmp_path_factory) -> dict:
    if not (ETRI_DIR / "mixed_blocks.py").is_file():
        pytest.skip(f"input snapshot of the ETRI reference missing at {ETRI_DIR}")
    out = tmp_path_factory.mktemp("parity")
    ref, ven = out / "ref.json", out / "vendored.json"
    _run("--mode", "ref", "--etri-dir", str(ETRI_DIR), "--out", str(ref))
    _run("--mode", "vendored", "--yamls-from", str(ref), "--out", str(ven))
    a, b = json.loads(ref.read_text()), json.loads(ven.read_text())
    return {"ref": a, "vendored": b, "comparison": _child_module().compare(a, b)}


def test_both_children_built_every_variant(parity) -> None:
    assert set(parity["ref"]["records"]) == VARIANTS == set(parity["vendored"]["records"])
    assert parity["ref"]["etri_modules_loaded"]  # A really ran the original
    assert parity["ref"]["mixed_blocks_sha256"] == ORIGINAL_SHA256  # the HEAD 18c4815 bytes (VENDORED_FROM)
    assert parity["vendored"]["etri_modules_loaded"] == []  # B never touched yolo_ga_runtime


@pytest.mark.parametrize("key", sorted(VARIANTS))
def test_variant_is_identical(parity, key: str) -> None:
    row = parity["comparison"]["rows"][key]
    assert all(row["fields_equal"].values()), {f: ok for f, ok in row["fields_equal"].items() if not ok}
    a, b = parity["ref"]["records"][key], parity["vendored"]["records"][key]
    assert a["class_names"] == b["class_names"] and a["params"] == b["params"]
    assert a["structural_hash_normalised"] == b["structural_hash_normalised"]
    assert a["forward64_shapes"] == b["forward64_shapes"]
    if not row["uses_vendored_operators"]:  # without operators the raw hash cannot depend on the module path
        assert a["structural_hash_raw"] == b["structural_hash_raw"]
    assert row["ok"]


def test_operator_variants_really_use_the_operators(parity) -> None:
    rows = parity["comparison"]["rows"]
    assert not rows["yolo11n:native"]["uses_vendored_operators"]
    assert rows["yolo11n:transformer_backbone"]["uses_vendored_operators"]
    assert rows["yolo11n:transformer_global_extra"]["uses_vendored_operators"]
    names = set(parity["vendored"]["records"]["yolo11n:transformer_global_extra"]["class_names"])
    assert {"NASWindowTransformer", "NASGlobalTransformer"} <= names


def test_registration_capabilities(parity) -> None:
    ref, ven = parity["ref"]["registration"], parity["vendored"]["registration"]
    assert ven["installed"] is True and ven["parse_model_extension_mode"] == "static_copy"
    assert ven["parser_source_sha256"] == ref["parser_source_sha256"]  # the same installed ultralytics parser
    assert ven["custom_modules"] == ref["custom_modules"] and ven["repeat_modules"] == ref["repeat_modules"]
    assert ven["implementation_version"] == ref["implementation_version"]
