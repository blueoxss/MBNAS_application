"""Registry specs/entries: baseline14 order, status rules, contract schema, validator compatibility."""
from __future__ import annotations

import copy
import importlib.resources
import json
import os
from pathlib import Path

import jsonschema
import pytest

from mbnas_contracts.constants import DEFAULT_BASELINE14
from mbnas_core.registry import build_entries, build_entry, load_registry, scan_evidence
from mbnas_model_runtime.registry.specs import SPEC_BY_ID, SPECS, representative_for
from mbnas_contracts.schemas.api import RegistryEntry

CONTRACT = importlib.resources.files("mbnas_contracts.openapi") / "openapi.json"  # package data (DD-A10)
REAL_ROOT = os.environ["MBNAS_TEST_RUNS_ROOT"]  # WP-1 contract §9: runs root of this test run (probe evidence = host data)


def _schema() -> dict:
    doc = json.loads(CONTRACT.read_text())
    schema = copy.deepcopy(doc["components"]["schemas"]["RegistryList"])
    schema["components"] = doc["components"]
    return schema


def _rec(spec_id: str, *, build_ok: bool = True, weights: str = "VERIFIED") -> dict:
    return {"id": spec_id, "build": {"ok": build_ok, "error": None if build_ok else "boom"},
            "weights": {"status": weights, "sha256": "ab" * 32, "path": f"/w/{spec_id}.pt"},
            "config_ref": {"ok": True, "file_sha256": "cd" * 32}}


def test_specs_order_and_families() -> None:
    assert tuple(s.id for s in SPECS[:14]) == DEFAULT_BASELINE14
    assert all(s.default for s in SPECS[:14]) and not any(s.default for s in SPECS[14:])
    assert {s.id for s in SPECS[14:]} == {"yolo11n", "yolo26n", "rf-detr-nano"}
    assert {s.family for s in SPECS} == {"YOLO11", "YOLO26", "Transformer", "Hybrid"}
    assert SPEC_BY_ID["etri-controlled-cnn-transformer"].init_modes == ("scratch",)
    assert SPEC_BY_ID["rf-detr-small"].init_modes == ("scratch", "pretrained_native")  # PLAN §8.1
    assert SPEC_BY_ID["yolo26s"].inference_head == "e2e_one2one_nms_false"
    assert representative_for(SPEC_BY_ID["rt-detr-x"]) == "rt-detr-l"
    assert representative_for(SPEC_BY_ID["yolo11x"]) == "yolo11n"


def test_no_probe_means_unverified_never_skipped() -> None:
    from mbnas_core.console.projection import project_public

    entries = build_entries(None)
    assert [e["id"] for e in entries] == [s.id for s in SPECS]
    assert {e["status"] for e in entries} == {"UNVERIFIED"}
    # contract 1.5.0: the public RegistryList is the projection of these stored entries (the probe file / evidence paths
    # are owner-only, served raw by /core/v1/owner/registry); the raw rows keep the member
    assert all("probe_file" in e["evidence"] for e in entries)
    public = project_public("RegistryList", {"registry": entries})
    jsonschema.validate(public, _schema())
    assert [e["id"] for e in public["registry"]] == [e["id"] for e in entries]
    assert not any("probe_file" in (e.get("evidence") or {}) for e in public["registry"])


def test_status_rules() -> None:
    meta = {"path": "/p.json", "created_at": "t", "versions": {"ultralytics": "8.4.173", "rfdetr": "1.11.2"}}
    ok = build_entry(SPEC_BY_ID["yolo11s"], _rec("yolo11s"), meta, {})
    assert ok["status"] == "AVAILABLE" and ok["capabilities"]["version"] == "8.4.173"
    assert build_entry(SPEC_BY_ID["yolo11s"], _rec("yolo11s", build_ok=False), meta, {})["status"] == "UNSUPPORTED"
    blocked = build_entry(SPEC_BY_ID["rt-detr-x"], _rec("rt-detr-x", weights="MISSING"), meta, {})
    assert blocked["status"] == "BLOCKED_SOURCE" and "MISSING" in blocked["reason"]
    etri = build_entry(SPEC_BY_ID["etri-controlled-cnn-transformer"],
                       _rec("etri-controlled-cnn-transformer", weights="NOT_APPLICABLE"), meta, {})
    assert etri["status"] == "AVAILABLE"
    ev = {("yolo11n", "resume_equivalence"): {"path": "/e.json", "verdict": "PASS"}}
    rep = build_entry(SPEC_BY_ID["yolo11x"], _rec("yolo11x"), meta, ev)
    assert rep["capabilities"]["train"]["status"] == "VERIFIED_FAMILY_REPRESENTATIVE"
    own = build_entry(SPEC_BY_ID["yolo11n"], _rec("yolo11n"), meta, ev)
    assert own["capabilities"]["train"]["status"] == "VERIFIED_GPU"
    for e in (ok, blocked, etri, rep):
        RegistryEntry.model_validate(e)


def test_entries_drive_the_validator(ctx_factory) -> None:
    """A pilot protocol validates against registry-built entries (all forced AVAILABLE here)."""
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.protocol.validator import validate

    meta = {"path": "/p.json", "created_at": "t", "versions": {"ultralytics": "8.4.173", "rfdetr": "1.11.2"}}
    entries = {s.id: build_entry(s, _rec(s.id, weights="NOT_APPLICABLE" if not s.weights_file else "VERIFIED"),
                                 meta, {}) for s in SPECS}
    res = validate(pilot_smoke_protocol(), ctx_factory(registry_lookup=entries.get))
    errors = [e for e in res.errors if not e["code"].startswith("PATH") and e["code"] not in ("DATASET_YAML_INVALID",)]
    baseline_errors = [e for e in errors if e["field"].startswith("console.baselines")]
    assert baseline_errors == []


def _probe_files() -> list[Path]:
    try:
        return list(Path(REAL_ROOT, "registry").glob("probe_*.json"))
    except OSError:  # PermissionError when the original host's /root is unreadable
        return []


@pytest.mark.skipif(not _probe_files(), reason="no real probe evidence yet", host_data=True)  # contract §9
def test_live_registry_from_real_probe() -> None:
    reg = load_registry(REAL_ROOT)
    jsonschema.validate({"registry": list(reg.values())}, _schema())
    assert set(DEFAULT_BASELINE14) <= set(reg)
    for e in reg.values():
        assert e["evidence"]["probe_file"] and e["status"] in ("AVAILABLE", "BLOCKED_SOURCE", "UNSUPPORTED")
    assert isinstance(scan_evidence(REAL_ROOT), dict)
