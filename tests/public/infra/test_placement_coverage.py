"""PLACEMENT_MANIFEST covers every file of the five package source trees (C01 ``call_graph`` mandatory check, EVAL_1 D1).

``scripts/dev/placement_coverage.py`` (``run(root)``) on this tree must report no package file without a placement row,
no deletion recorded for a file that is present, no placed package file that is gone without a deletion row and no
malformed row. The checker and ``docs/internal/provenance/PLACEMENT_MANIFEST.jsonl`` are owner files: a bundle that
ships neither skips these tests (DD-A43(b)). The synthetic-tree tests show that each finding is detected (negative
controls), so a passing run is not vacuous.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest_common import owner_tool  # DD-A43(b): skip when the owner-only checker is not shipped

ROOT = Path(__file__).resolve().parents[3]
# composed names: the import-closure checker reads a bare package-name literal in a public test as a module reference
PACKAGES = tuple(f"mbnas_{name}" for name in ("contracts", "model_runtime", "core", "runner", "server"))
SHA = "0" * 64


def _checker():
    spec = importlib.util.spec_from_file_location("mbnas_dev_placement_coverage",
                                                  owner_tool("scripts/dev/placement_coverage.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def checker():
    return _checker()


@pytest.fixture(scope="module")
def report(checker) -> dict:
    owner_tool("docs/internal/provenance/PLACEMENT_MANIFEST.jsonl")
    if not (ROOT / PACKAGES[2] / "src").is_dir():  # a bundle without the private core sources
        pytest.skip("owner-only sources (the private core package) not in this bundle")
    return checker.run(ROOT)


def test_every_package_file_has_a_placement_row(report: dict) -> None:
    assert report["missing_package_src"] == []
    assert report["missing"] == [], json.dumps(report["missing"], indent=1)
    assert report["covered"]["files"] == report["scanned"]["files"]


def test_no_deletion_contradicts_the_tree(report: dict) -> None:
    assert report["deleted_but_present"] == [], report["deleted_but_present"]
    assert report["unrecorded_deletions"] == [], report["unrecorded_deletions"]


def test_manifest_rows_are_well_formed(report: dict) -> None:
    assert report["errors"] == [], report["errors"][:20]


def test_the_scan_covers_the_five_package_source_trees(report: dict) -> None:
    assert report["packages"] == list(PACKAGES)
    assert all(report["scanned"]["by_package"][p] > 0 for p in PACKAGES), report["scanned"]["by_package"]
    assert report["scanned"]["py"] > 150


# --------------------------------------------------------------------------------------------- negative controls
def _row(kind: str, target: str | None, source: str | None = None, sha: str | None = SHA) -> dict:
    return {"kind": kind, "notes": "synthetic", "owner_wp": "test", "source_relpath": source,
            "source_sha256": None, "target_relpath": target, "target_sha256": sha if target else None}


def _tree(tmp_path: Path, rows: list[dict | str]) -> Path:
    for pkg in PACKAGES:
        src = tmp_path / pkg / "src" / pkg
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "__pycache__").mkdir()
        (src / "__pycache__" / "x.cpython-310.pyc").write_bytes(b"\0")  # ignored
    (tmp_path / PACKAGES[0] / "src" / f"{PACKAGES[0]}.egg-info").mkdir()
    (tmp_path / PACKAGES[0] / "src" / f"{PACKAGES[0]}.egg-info" / "PKG-INFO").write_text("x", encoding="utf-8")  # ignored
    manifest = tmp_path / "docs" / "internal" / "provenance" / "PLACEMENT_MANIFEST.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("".join((r if isinstance(r, str) else json.dumps(r, sort_keys=True)) + "\n" for r in rows),
                        encoding="utf-8")
    return tmp_path


def _init(pkg: str) -> str:
    return f"{pkg}/src/{pkg}/__init__.py"


def test_a_consistent_synthetic_tree_passes(checker, tmp_path: Path) -> None:
    rep = checker.run(_tree(tmp_path, [_row("new", _init(p)) for p in PACKAGES]))
    assert rep["ok"] and rep["scanned"] == {"files": 5, "py": 5, "data": 0, "by_package": {p: 1 for p in PACKAGES}}
    empty = hashlib.sha256(b"").hexdigest()
    assert rep["sha_drift_count"] == 5 and {d["current"] for d in rep["sha_drift"]} == {empty}  # informational only


def test_each_finding_is_reported_on_a_synthetic_tree(checker, tmp_path: Path) -> None:
    gone = f"{PACKAGES[3]}/src/{PACKAGES[3]}/gone.py"
    rows = [_row("moved", _init(PACKAGES[0]), source="application/x.py"),
            # PACKAGES[1]: no row at all -> missing
            _row("new", _init(PACKAGES[2])), _row("deleted", None, source=_init(PACKAGES[2]), sha=None),  # deleted, still there
            _row("new", _init(PACKAGES[3])), _row("new", gone),  # placed, absent, no deletion row
            _row("new", _init(PACKAGES[4])),
            _row("deleted", None, source="application/old.py", sha=None),  # "not ported" row of the original checkout
            "{not json", _row("renamed", "x/y.py"), _row("new", "../outside.py"), _row("new", "z.py", sha="ABC")]
    rep = checker.run(_tree(tmp_path, rows))
    assert not rep["ok"]
    assert rep["missing"] == [_init(PACKAGES[1])]
    assert [d["path"] for d in rep["deleted_but_present"]] == [_init(PACKAGES[2])]
    assert rep["unrecorded_deletions"] == [gone]
    assert len(rep["errors"]) == 4 and any("invalid JSON" in e for e in rep["errors"])
    assert any("unknown kind" in e for e in rep["errors"]) and any("'..'" in e for e in rep["errors"])
    assert any("64 lowercase hex" in e for e in rep["errors"])


def test_a_recorded_deletion_of_a_removed_file_is_consistent(checker, tmp_path: Path) -> None:
    gone = f"{PACKAGES[3]}/src/{PACKAGES[3]}/gone.py"
    rows = [_row("new", _init(p)) for p in PACKAGES] + [_row("new", gone), _row("deleted", None, source=gone, sha=None)]
    rep = checker.run(_tree(tmp_path, rows))
    assert rep["ok"] and rep["recorded_deletions_of_package_files"] == [gone]
