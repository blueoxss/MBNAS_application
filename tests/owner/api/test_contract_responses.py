"""Live API responses validated with jsonschema against contracts/openapi.json components (synthetic data)."""
from __future__ import annotations

import harness
from harness import auth

from mbnas_server.api.contract_check import contract_errors


def _ok(resp, component):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert contract_errors(component, body) == [], contract_errors(component, body)[:5]
    return body


def test_health_presets_registry(client):
    health = _ok(client.get("/api/v1/health", headers=auth()), "Health")
    assert health["commit_sha"].startswith("sha256:")
    assert set(health["capabilities"]) >= {"full_state_resume", "export_onnx", "export_trt", "sse", "idempotency"}
    assert health["allowlists"]["output_roots"] and health["worker_count"] == 0
    assert health["status"] == "degraded" and any("worker" in w for w in health["warnings"])
    presets = _ok(client.get("/api/v1/protocols/presets", headers=auth()), "PresetList")
    assert {p["id"] for p in presets["presets"]} == {"DEFAULT_BASELINE14", "PILOT_SMOKE"}
    _ok(client.get("/api/v1/registry", headers=auth()), "RegistryList")


def test_validate_valid_and_invalid(client, protocol):
    body = _ok(client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth()),
               "ValidateResponse")
    assert body["valid"] and body["validation_id"].startswith("val-") and body["protocol_hash"].startswith("sha256:")
    bad = dict(protocol, rungs=[dict(r) for r in protocol["rungs"]])
    bad["rungs"][0]["fitness_weights"] = {"ap": 0.2, "recall": 0.1, "latency_reward": 0.1, "size_reward": 0.03}
    body = _ok(client.post("/api/v1/protocols/validate", json={"protocol": bad}, headers=auth()), "ValidateResponse")
    assert not body["valid"] and body["validation_id"] is None and body["resolved"] is None
    assert "WEIGHTS_SUM_NOT_ONE" in {e["code"] for e in body["errors"]}


def test_validate_schema_violation_is_422_error(client):
    r = client.post("/api/v1/protocols/validate", json={"protocol": {"schema_version": "x"}}, headers=auth())
    assert r.status_code == 422 and contract_errors("Error", r.json()) == []
    r = client.post("/api/v1/protocols/validate", json={"protocol": {}, "extra": 1}, headers=auth())
    assert r.status_code == 422


def test_run_responses(client, protocol, inline_worker):
    run_id, _ = harness.validate_and_run(client, protocol)
    run = _ok(client.get(f"/api/v1/runs/{run_id}", headers=auth()), "Run")
    assert run["resolved_protocol"]["status"] == "RESOLVED_PILOT" and run["status"] == "QUEUED"
    listed = _ok(client.get("/api/v1/runs?namespace=pilot_smoke&limit=5", headers=auth()), "RunList")
    assert [r["id"] for r in listed["runs"]] == [run_id]
    harness.drain(inline_worker)
    snap = _ok(client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()), "Snapshot")
    for event in snap["events"]:
        assert contract_errors("Event", event) == []
    for worker in snap["workers"]:
        assert contract_errors("Worker", worker) == []
    cid = next(c["candidate_id"] for c in snap["candidates"] if c["arm"] == "GA" and c["generation"] == 1)
    detail = _ok(client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth()), "CandidateDetail")
    assert detail["parents"] and detail["checkpoints"] and detail["gate_history"]
    assert client.get(f"/api/v1/runs/{run_id}/candidates/nope", headers=auth()).status_code == 404
    art = snap["candidates"][-1]["artifacts"][0]
    _ok(client.post(f"/api/v1/runs/{run_id}/artifacts/{art['id']}/refresh", headers=auth()), "Artifact")
