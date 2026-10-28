"""WP-S6 (DD-38): GET /api/v1/structure/block-catalog and the catalog on declared GET documents.  CPU only."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from mbnas_model_runtime import block_catalog  # noqa: E402


def auth():
    import harness
    return harness.auth()


def test_block_catalog_route_requires_the_bearer_token(client):
    assert client.get("/api/v1/structure/block-catalog").status_code == 401
    assert client.get("/api/v1/structure/block-catalog", headers={"Authorization": "Bearer wrong"}).status_code in (401, 403)


def test_block_catalog_route_returns_the_static_table_and_imports_nothing(client):
    before = {m for m in ("torch", "ultralytics", "rfdetr") if m in sys.modules}
    r = client.get("/api/v1/structure/block-catalog", headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == block_catalog.static_table()
    assert body["schema"] == "mbnas-block-catalog/1"
    assert body["entries"]["C3k2"]["origin_family"] == "YOLO11"
    assert body["entries"]["NASWindowTransformer"]["origin_family"] == "ETRI mixed operator pool"
    assert body["native_entries"]["rfdetr:MultiScaleProjector"]["origin_family"] == "LW-DETR"
    assert "ETRI mixed operator pool" in body["families"]
    assert all("source_path" not in e for e in (*body["entries"].values(), *body["native_entries"].values()))
    assert {m for m in ("torch", "ultralytics", "rfdetr") if m in sys.modules} == before


def test_registry_structure_get_carries_the_catalog_with_a_null_source_path(client):
    r = client.get("/api/v1/registry/yolo11n/structure", headers=auth())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["evidence_source"] == "declared_configuration" and doc["blocks"]
    assert all(b["catalog"]["source_path"] is None for b in doc["blocks"])
    assert {b["catalog"]["origin_family"] for b in doc["blocks"]} >= {"YOLO11", "YOLOv5"}
    summary = doc["catalog_summary"]
    assert sum(summary["by_origin_family"].values()) + summary["unknown"] == len(doc["blocks"])
    # declared-only native documents have no blocks yet -> an empty, truthful summary
    nano = client.get("/api/v1/registry/rf-detr-nano/structure", headers=auth()).json()
    assert nano["blocks"] == [] and nano["catalog_summary"] == {"by_origin_family": {}, "unknown": 0}


def test_explicit_check_resolves_source_path_and_the_saved_get_returns_it(client):
    """POST (explicit check) fills catalog.source_path from the installed package; the later GET serves the stored document."""
    pytest.importorskip("ultralytics")
    r = client.post("/api/v1/registry/yolo11n/structure-check", json={"input_size": 64}, headers=auth())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS"
    by_module = {b["module"]: b["catalog"] for b in doc["blocks"]}
    assert by_module["Conv"]["source_path"] == "ultralytics/nn/modules/conv.py"
    assert by_module["C3k2"]["source_path"] == "ultralytics/nn/modules/block.py" and by_module["C3k2"]["origin_family"] == "YOLO11"
    assert by_module["Detect"]["source_path"] == "ultralytics/nn/modules/head.py"
    assert doc["catalog_summary"]["unknown"] == 0
    saved = client.get("/api/v1/registry/yolo11n/structure", headers=auth()).json()
    assert saved["evidence_source"] == "saved_structure_check"
    assert [b["catalog"]["source_path"] for b in saved["blocks"]] == [b["catalog"]["source_path"] for b in doc["blocks"]]
    assert saved["catalog_summary"] == doc["catalog_summary"]
