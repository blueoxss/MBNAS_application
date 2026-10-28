"""WP-4C item (4): configuration revisions (F5d, contract 1.5.0 ``ModelConfigRevision`` / ``ModelConfigRevisionList``).

A user model YAML is uploaded through the gateway (``POST /api/v1/uploads/model-config``; exact bytes with CRLF line
ends and a UTF-8 multi-byte comment), trained through ``POST /api/v1/training-runs`` (``source.upload_id``) and run by
the in-process owner worker with SYNTHETIC handlers whose trainer writes the executed configuration
(``revision_handlers.py``). Expected kinds: ``original`` (the upload: same sha256, ``artifact_id`` = the upload id),
``selected`` (bound by the training selection, parent = original) and ``executed`` (what the trainer ran, parent =
selected); search proposals record ``generated`` (checked on the canary world's GA candidates). ``.../yaml`` returns
the exact stored bytes with ``X-Artifact-Sha256`` = sha256(body) = the revision ``source_sha256``, through the gateway
relay and from core alike; an unknown revision is 404, stored bytes that no longer match are 409 ``LOAD_FAILED``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from typing import Any

import pytest
import yaml

import projection_world as pw

UPLOAD = ("# canary user model (SYNTHETIC) - 구성 원본 바이트 그대로\r\n" + yaml.safe_dump(pw.CANARY_MODEL)
          .replace("\n", "\r\n")).encode("utf-8")
EXECUTED = "# executed by the SYNTHETIC revision stub trainer\n" + yaml.safe_dump(pw.CANARY_MODEL)
SIDES = ("gateway", "core-server")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def rev(tmp_path_factory) -> dict[str, Any]:
    """Upload -> training run -> drained by the owner worker; every revision response captured (gateway + core)."""
    import harness
    from fastapi.testclient import TestClient

    from conftest_common import A100, _datasets
    from mbnas_core.db.core import connect

    base = tmp_path_factory.mktemp("wp4c_revisions")
    roots = {"data": base / "data", "runs": base / "runs"}
    for p in roots.values():
        p.mkdir()
    mp = pytest.MonkeyPatch()
    try:
        for key in list(os.environ):
            if key.upper().startswith("MBNAS_") and not key.upper().startswith("MBNAS_TEST_"):
                mp.delenv(key, raising=False)
        harness.stub_env(mp, epoch_s=0.0)
        mp.setenv("MBNAS_JOB_HANDLERS_MODULE", "revision_handlers")
        mp.setenv("MBNAS_TEST_REVISION_EXECUTED_YAML", EXECUTED)
        registry = {}
        for entry in json.loads(pw.REGISTRY_EXAMPLE.read_text())["registry"]:  # synthetic: every entry AVAILABLE
            registry[entry["id"]] = {**copy.deepcopy(entry), "status": "AVAILABLE", "reason": "synthetic test registry"}
        combined = harness.combined_app(roots, registry, [copy.deepcopy(A100)])
        server = {"Authorization": f"Bearer {combined.tokens['server']}"}
        ds = _datasets.build_dataset(roots["data"], "ds", {"train": 4, "val": 2, "test": 2}, "0 0.5 0.5 0.25 0.25\n",
                                     None, 1)
        protocol = harness.small_protocol(ds, str(roots["runs"] / "out"))
        protocol["console"]["training"] = {"epoch_target_total": 2, "seed": 13}
        protocol["console"]["search"]["evaluation_enabled"] = False
        out: dict[str, Any] = {"settings": combined.core_settings}
        with TestClient(combined.core_app) as core, TestClient(combined.gateway_app) as gw:
            up = gw.post("/api/v1/uploads/model-config", content=UPLOAD,
                         headers={**harness.auth(), "X-Filename": "canary_user.yaml",
                                  "Content-Type": "application/x-yaml"})
            assert up.status_code == 200, up.text
            out["upload"] = up.json()
            body = {"source": {"kind": "user", "upload_id": out["upload"]["upload_id"]}, "protocol": protocol,
                    "mode": "scratch", "validation_enabled": True, "idempotency_key": "wp4c-revisions-1"}
            created = gw.post("/api/v1/training-runs", json=body, headers=harness.auth())
            assert created.status_code == 200, created.text
            out["created"] = created.json()
            run_id, cid = out["created"]["run_id"], out["created"]["candidate_id"]
            listing = f"/runs/{run_id}/candidates/{cid}/config-revisions"
            out["before_run"] = gw.get("/api/v1" + listing, headers=harness.auth()).json()
            worker = harness.make_inline_worker(combined.core_settings)
            try:
                harness.drain(worker)
            finally:
                worker.conn.close()
            out["run"] = gw.get(f"/api/v1/runs/{run_id}", headers=harness.auth()).json()
            for side, client, prefix, headers in (("gateway", gw, "/api/v1", harness.auth()),
                                                  ("core-server", core, pw.CORE_CONSOLE, server)):
                resp = client.get(prefix + listing, headers=headers)
                out[f"{side} list"] = (resp.status_code, resp.json())
                for r in (resp.json().get("revisions") or []):
                    y = client.get(f"{prefix}{listing}/{r['revision_id']}/yaml", headers=headers)
                    out[f"{side} yaml {r['kind']}"] = (y.status_code, dict(y.headers), y.content)
                missing = client.get(f"{prefix}{listing}/rev-does-not-exist/yaml", headers=headers)
                out[f"{side} yaml missing"] = (missing.status_code, missing.json())
            # stored bytes that no longer match their revision digest are never served
            conn = connect(combined.core_settings, init=False)
            try:
                rows = conn.execute("SELECT id, kind FROM config_revisions WHERE candidate_id = ?", (cid,)).fetchall()
                original = next(r[0] for r in rows if r[1] == "original")
                conn.execute("UPDATE config_revisions SET text = text || '# tampered' WHERE id = ?", (original,))
            finally:
                conn.close()
            for side, client, prefix, headers in (("gateway", gw, "/api/v1", harness.auth()),
                                                  ("core-server", core, pw.CORE_CONSOLE, server)):
                t = client.get(f"{prefix}{listing}/{original}/yaml", headers=headers)
                out[f"{side} yaml tampered"] = (t.status_code, t.json() if "json" in t.headers.get("content-type", "") else t.text)
        return out
    finally:
        mp.undo()


def _revisions(rev: dict[str, Any], side: str) -> list[dict[str, Any]]:
    status, body = rev[f"{side} list"]
    assert status == 200, body
    return body["revisions"]


def test_upload_and_training_run_are_created(rev: dict[str, Any]) -> None:
    up = rev["upload"]
    assert up == {"upload_id": f"upl-{_sha(UPLOAD)}", "source_sha256": f"sha256:{_sha(UPLOAD)}", "bytes": len(UPLOAD)}
    assert rev["run"]["status"] == "COMPLETED", rev["run"]
    assert [r["kind"] for r in rev["before_run"]["revisions"]] == ["original", "selected"]


@pytest.mark.parametrize("side", SIDES)
def test_original_selected_and_executed_revisions_are_recorded_in_order(rev: dict[str, Any], contract150: dict,
                                                                       side: str) -> None:
    status, body = rev[f"{side} list"]
    assert pw.contract_errors_150(contract150, "ModelConfigRevisionList", body) == []
    assert body["run_id"] == rev["created"]["run_id"] and body["candidate_id"] == rev["created"]["candidate_id"]
    revs = _revisions(rev, side)
    assert [r["kind"] for r in revs] == ["original", "selected", "executed"]
    original, selected, executed = revs
    assert original["source_sha256"] == rev["upload"]["source_sha256"]
    assert original["artifact_id"] == rev["upload"]["upload_id"] and original["parent_revision_id"] is None
    assert selected["parent_revision_id"] == original["revision_id"]
    assert executed["parent_revision_id"] == selected["revision_id"]
    assert executed["source_sha256"] == f"sha256:{_sha(EXECUTED.encode())}"
    assert len({r["revision_id"] for r in revs}) == 3
    from mbnas_model_runtime.genotype import Genotype

    canon = Genotype.from_yaml_text(EXECUTED, family="f", template_id="t").yaml_hash()
    assert executed["canonical_hash"] == canon  # same YAML structure: the canonical hash ignores comments/line ends
    assert original["canonical_hash"] == canon and selected["canonical_hash"] == canon
    assert all(r["created_at"] for r in revs) and [r["created_at"] for r in revs] == sorted(r["created_at"] for r in revs)


@pytest.mark.parametrize("side", SIDES)
def test_yaml_endpoint_returns_the_exact_stored_bytes(rev: dict[str, Any], side: str) -> None:
    for r in _revisions(rev, side):
        status, headers, body = rev[f"{side} yaml {r['kind']}"]
        assert status == 200, (r["kind"], body[:300])
        assert headers["content-type"].startswith("application/x-yaml")
        assert headers["x-artifact-sha256"] == _sha(body) == r["source_sha256"].removeprefix("sha256:"), r["kind"]
    assert rev[f"{side} yaml original"][2] == UPLOAD  # byte for byte (CRLF and the UTF-8 comment kept)
    assert rev[f"{side} yaml executed"][2] == EXECUTED.encode()


@pytest.mark.parametrize("side", SIDES)
def test_yaml_endpoint_refuses_unknown_and_tampered_revisions(rev: dict[str, Any], side: str) -> None:
    status, body = rev[f"{side} yaml missing"]
    assert status == 404 and body["code"] == "NOT_FOUND", body
    status, body = rev[f"{side} yaml tampered"]
    assert status == 409 and isinstance(body, dict) and body["code"] == "LOAD_FAILED", body


@pytest.mark.parametrize("side", SIDES)
def test_search_proposals_record_generated_revisions(world: pw.World, contract150: dict, side: str) -> None:
    cap = world.captures[f"{side} GET /runs/{{run}}/candidates/{{proposed}}/config-revisions"]
    assert cap.status == 200, cap.body[:300]
    body = cap.json()
    assert pw.contract_errors_150(contract150, "ModelConfigRevisionList", body) == []
    assert [r["kind"] for r in body["revisions"]] == ["generated"]
    gen = body["revisions"][0]
    y = world.captures[f"{side} GET /runs/{{run}}/candidates/{{proposed}}/config-revisions/{{generated}}/yaml"]
    assert y.status == 200 and _sha(y.body) == gen["source_sha256"].removeprefix("sha256:")
    conn = sqlite3.connect(f"file:{world.db_path}?mode=ro", uri=True)
    try:
        stored = conn.execute("SELECT architecture_yaml FROM candidates WHERE id = ?",
                              (world.proposed_candidate,)).fetchone()[0]
    finally:
        conn.close()
    assert y.body == stored.encode("utf-8")
    assert pw.leaks(world, cap.body) == [] and pw.leaks(world, y.body) == []
