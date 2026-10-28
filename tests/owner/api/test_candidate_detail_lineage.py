"""Candidate detail of a candidate whose lineage was written by the REAL search writer (``search.lineage.lineage_for``
on a real ``SearchSpace``), served through the API and validated against the contract (``CandidateDetail``).

Regression for the Integrate-phase live run ``run-a614e049808b``: every GA/IndependentRandom candidate detail answered
422 VALIDATION_FAILED because the real lineage stores ``template`` as an object ``{id, family, variant}`` while
contract 1.2.0 types ``CandidateDetail.lineage.template`` as a string. The API now serves ``template`` as the template
id and keeps the stored object under ``template_detail`` (``lineage`` allows additional properties).
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import harness
import pytest
from harness import auth

from mbnas_server.api.contract_check import contract_errors

# WP-1 contract §9: runs root of this test run (tests/_shared/conftest_common.py sets MBNAS_TEST_RUNS_ROOT)
ULTRALYTICS_CFG = Path(os.environ["MBNAS_TEST_RUNS_ROOT"]) / "ultralytics_cfg/tests"


@pytest.fixture(scope="module")
def real_lineage() -> dict:
    """Lineage of a real IndependentRandom proposal (same writer as the worker's PROPOSE_GENERATION job)."""
    pytest.importorskip("ultralytics")  # SearchSpace.create builds real ultralytics models (needs torch)
    ULTRALYTICS_CFG.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ULTRALYTICS_CFG))
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from mbnas_core.search.lineage import lineage_for
    from mbnas_core.search.space import SearchSpace

    space = SearchSpace.create(nc=1, imgsz=640)  # WP-1 (DD-A12): vendored mixed pool, no ETRI root
    proposal = space.sample_prior(random.Random(101), method="IndependentRandom")
    return json.loads(json.dumps(lineage_for(proposal, space, space.describe())))


def _store_lineage(api_settings, candidate_id: str, lineage: dict) -> None:
    import sqlite3

    conn = sqlite3.connect(api_settings.db_path)
    with conn:
        conn.execute("UPDATE candidates SET lineage_json = ? WHERE id = ?", (json.dumps(lineage), candidate_id))
    conn.close()


def test_real_search_lineage_is_served_contract_valid(client, protocol, inline_worker, api_settings, real_lineage):
    assert isinstance(real_lineage["template"], dict)  # the real writer's shape (stored unchanged)
    run_id, _ = harness.validate_and_run(client, protocol, key="real-lineage")
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    cid = next(c["candidate_id"] for c in snap["candidates"] if c["arm"] == "IndependentRandom")
    _store_lineage(api_settings, cid, real_lineage)
    r = client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert contract_errors("CandidateDetail", body) == []
    lin = body["lineage"]
    assert lin["template"] == real_lineage["template"]["id"]
    assert lin["template_detail"] == real_lineage["template"]
    # contract 1.5.0 (DD-A19 D2): the public lineage keeps search_space {version, variant}; the stored space_hash /
    # prior_hash (owner-only: canonical hashes of the private describe() / prior tables) are never served
    assert {"space_hash", "prior_hash"} <= set(real_lineage["search_space"])
    assert lin["operation"] == "random" and lin["search_space"] == {
        k: real_lineage["search_space"][k] for k in ("version", "variant")}


def test_a_string_template_is_served_unchanged(client, protocol, inline_worker, api_settings, real_lineage):
    run_id, _ = harness.validate_and_run(client, protocol, key="string-template")
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    cid = next(c["candidate_id"] for c in snap["candidates"] if c["arm"] == "IndependentRandom")
    _store_lineage(api_settings, cid, {**real_lineage, "template": "yolo11n:native"})
    body = client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth()).json()
    assert body["lineage"]["template"] == "yolo11n:native" and "template_detail" not in body["lineage"]


def test_a_stored_row_that_violates_the_response_contract_is_a_server_error_not_a_client_422(
        client, protocol, inline_worker, api_settings):
    """A response that does not match the contract is the server's fault: 500, never 'request does not match'."""
    run_id, _ = harness.validate_and_run(client, protocol, key="bad-row")
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    cid = next(c["candidate_id"] for c in snap["candidates"] if c["arm"] == "IndependentRandom")
    _store_lineage(api_settings, cid, {"operation": "not-an-operation"})
    r = client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth())
    assert r.status_code == 500, r.text
    body = r.json()
    assert body["code"] == "RESPONSE_CONTRACT_VIOLATION" and contract_errors("Error", body) == []
    assert "request does not match" not in body["message"]
    assert body["errors"] and body["errors"][0]["field"].startswith("lineage")
