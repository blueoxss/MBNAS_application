"""DD-26: an unreadable registry root is skipped (with a diagnostic), never the reason for an all-UNVERIFIED registry.

The unreadable root is a real directory created with mode 000 (restored in teardown); no mocks of the filesystem.
CPU only; temporary roots only.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import mbnas_core.registry as registry
from mbnas_core.registry import latest_probe_path, load_registry, roots_skipped, scan_evidence
from mbnas_contracts.schemas.api import RegistryEntry


def _probe_doc() -> dict:
    return {"created_at": "2026-10-22T00:00:00Z", "versions": {"ultralytics": "8.4.173", "rfdetr": "1.11.2", "torch": "2.6.0"},
            "entries": {"yolo11n": {"id": "yolo11n", "build": {"ok": True, "error": None},
                                    "weights": {"status": "VERIFIED", "sha256": "ab" * 32, "path": "/w/yolo11n.pt"},
                                    "config_ref": {"ok": True, "file_sha256": "cd" * 32}}}}


def _write_readable_root(root: Path) -> None:
    (root / "registry").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "registry" / "probe_20261022T000000.json").write_text(json.dumps(_probe_doc()))
    (root / "evidence" / "train_smoke_yolo11n.json").write_text(json.dumps(
        {"kind": "train_smoke", "model_id": "yolo11n", "verdict": "PASS", "created_at": "2026-10-22T00:00:01Z",
         "summary": "qa"}))


@pytest.fixture
def unreadable(tmp_path: Path):
    """A sibling root with mode 000; skipped when the process can still list it (e.g. running as root)."""
    root = tmp_path / "unreadable_root"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "train_smoke_decoy.json").write_text("{}")
    root.chmod(0)
    try:
        try:
            os.scandir(root).close()
        except OSError:
            pass
        else:
            pytest.skip("this process can read a mode-000 directory (root user?)")
        yield root
    finally:
        root.chmod(stat.S_IRWXU)


@pytest.fixture
def readable(tmp_path: Path) -> Path:
    root = tmp_path / "readable_root"
    _write_readable_root(root)
    return root


def test_readable_root_alone_yields_verified_entries_beside_an_unreadable_root(readable, unreadable, monkeypatch):
    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(unreadable))  # the "canonical" root of this host
    reg = load_registry(str(readable))  # must not raise PermissionError
    y = reg["yolo11n"]
    assert y["status"] == "AVAILABLE", y["reason"]
    assert y["capabilities"]["train"]["status"] == "VERIFIED_GPU"  # evidence file from the readable root only
    assert y["evidence"]["probe_file"].startswith(str(readable))
    assert y["evidence"]["registry_roots_skipped"][0]["path"] == str(unreadable)
    assert "PermissionError" in y["evidence"]["registry_roots_skipped"][0]["reason"]
    others = [e for i, e in reg.items() if i != "yolo11n"]
    assert others and all(e["status"] == "UNVERIFIED" and "skipped" in e["reason"] and str(unreadable) in e["reason"]
                          for e in others)
    for e in reg.values():
        RegistryEntry.model_validate(e)
    assert [s["path"] for s in roots_skipped(str(readable))] == [str(unreadable)]
    assert set(scan_evidence(str(readable))) == {("yolo11n", "train_smoke")}
    assert latest_probe_path(str(readable)).parent == readable / "registry"


def test_unreadable_configured_root_falls_back_to_the_readable_canonical_root(readable, unreadable, monkeypatch):
    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(readable))
    reg = load_registry(str(unreadable))
    assert reg["yolo11n"]["status"] == "AVAILABLE"
    assert [s["path"] for s in roots_skipped(str(unreadable))] == [str(unreadable)]


def test_all_roots_unreadable_is_all_unverified_with_the_cause_and_no_exception(unreadable, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(unreadable))
    assert latest_probe_path(str(unreadable)) is None and scan_evidence(str(unreadable)) == {}
    reg = load_registry(str(unreadable))
    assert {e["status"] for e in reg.values()} == {"UNVERIFIED"}
    assert all("PermissionError" in e["reason"] for e in reg.values())


def test_unreadable_evidence_subdirectory_only_is_reported_and_probe_still_used(readable, monkeypatch):
    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(readable))
    ev = readable / "evidence"
    ev.chmod(0)
    try:
        try:
            os.scandir(ev).close()
        except OSError:
            pass
        else:
            pytest.skip("this process can read a mode-000 directory (root user?)")
        skipped = roots_skipped(str(readable))
        assert [s["path"] for s in skipped] == [str(ev)]
        assert scan_evidence(str(readable)) == {}
        assert load_registry(str(readable))["yolo11n"]["status"] == "AVAILABLE"
    finally:
        ev.chmod(stat.S_IRWXU)


def test_all_readable_adds_no_diagnostic(readable, tmp_path, monkeypatch):
    other = tmp_path / "other_root"
    other.mkdir()
    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(other))
    assert roots_skipped(str(readable)) == []
    assert "registry_roots_skipped" not in load_registry(str(readable))["yolo11n"]["evidence"]
    # a root that does not exist is not "unreadable": it simply has no evidence
    assert roots_skipped(str(tmp_path / "absent")) == []


def test_services_do_not_fall_back_to_the_example_registry(readable, unreadable, monkeypatch, settings):
    from mbnas_core.console.services import load_registry as services_registry

    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(unreadable))
    settings = settings.model_copy(update={"output_root": str(readable)})
    entries, source, warning = services_registry(settings)
    assert source == "mbnas_core.registry.load_registry" and warning is None
    assert {e["id"]: e["status"] for e in entries}["yolo11n"] == "AVAILABLE"


def test_doctor_reports_the_skipped_roots(readable, unreadable, monkeypatch, settings):
    from mbnas_core.ops import doctor

    monkeypatch.setattr(registry, "DEFAULT_OUTPUT_ROOT", str(unreadable))
    settings = settings.model_copy(update={"output_root": str(readable)})
    doc, _problems = doctor.report(settings)
    assert doc["registry"]["source"] == "mbnas_core.registry.load_registry" and doc["registry"]["warning"] is None
    assert doc["registry"]["by_status"].get("AVAILABLE") == 1
    assert [r["path"] for r in doc["registry"]["roots_skipped"]] == [str(unreadable)]
