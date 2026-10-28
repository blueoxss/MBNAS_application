"""POST /runs: idempotency matrix, protocol-hash checks, stale validation, run fields (synthetic data)."""
from __future__ import annotations

import copy
import threading
from pathlib import Path

import harness
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect


def _validate(client, protocol):
    body = client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth()).json()
    assert body["valid"], body["errors"]
    return body


def _run(client, body, key, *, protocol=None, phash=None, vid="same", headers=None):
    payload = {"protocol": protocol if protocol is not None else body["resolved"],
               "protocol_hash": phash or body["protocol_hash"], "idempotency_key": key}
    if vid:
        payload["validation_id"] = body["validation_id"] if vid == "same" else vid
    return client.post("/api/v1/runs", json=payload, headers={**auth(), **(headers or {})})


def _count(settings, sql, *args):
    conn = connect(settings, init=False)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def test_same_key_same_payload_returns_same_run(client, protocol, api_settings):
    body = _validate(client, protocol)
    first = _run(client, body, "k-1")
    second = _run(client, body, "k-1")
    third = _run(client, body, "k-1", vid=None)  # retry without validation_id: same payload sha
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"] == third.json()["run_id"]
    assert first.json()["created"] is True and second.json()["created"] is False
    assert _count(api_settings, "SELECT COUNT(*) FROM runs") == 1
    assert _count(api_settings, "SELECT COUNT(*) FROM jobs") == 3  # baseline L0 + one proposal per arm


def test_same_key_different_payload_conflicts(client, protocol):
    body = _validate(client, protocol)
    assert _run(client, body, "k-2").status_code == 200
    other = copy.deepcopy(protocol)
    other["console"]["search"]["population"] = 3
    body2 = _validate(client, other)
    r = _run(client, body2, "k-2")
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_header_key_must_match_body(client, protocol):
    body = _validate(client, protocol)
    r = _run(client, body, "k-3", headers={"Idempotency-Key": "other"})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert _run(client, body, "k-3", headers={"Idempotency-Key": "k-3"}).status_code == 200


def test_concurrent_duplicate_requests_create_one_run(client, protocol, api_settings):
    body = _validate(client, protocol)
    results = []

    def go():
        results.append(_run(client, body, "k-race"))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["run_id"] for r in results}) == 1
    assert _count(api_settings, "SELECT COUNT(*) FROM runs") == 1
    assert _count(api_settings, "SELECT COUNT(*) FROM jobs") == 3


def test_hash_mismatch_and_unvalidated(client, protocol):
    body = _validate(client, protocol)
    tampered = copy.deepcopy(body["resolved"])
    tampered["rungs"][0]["promotion"]["score_ge"] = 0.5
    r = _run(client, body, "k-4", protocol=tampered)
    assert r.status_code == 409 and r.json()["code"] == "PROTOCOL_HASH_MISMATCH"
    r = _run(client, body, "k-5", phash="sha256:" + "f" * 64)
    assert r.status_code == 409 and r.json()["code"] == "PROTOCOL_HASH_MISMATCH"
    r = _run(client, body, "k-6", vid="val-does-not-exist")
    assert r.status_code == 422 and r.json()["code"] == "UNVALIDATED"
    from mbnas_contracts.protocol_hash import protocol_hash

    r = _run(client, body, "k-7", protocol=tampered, phash=protocol_hash(tampered), vid=None)
    assert r.status_code == 422 and r.json()["code"] == "UNVALIDATED"
    # hash-excluded label may differ (not part of the frozen policy)
    relabelled = copy.deepcopy(body["resolved"])
    relabelled["console"]["label"] = "my label"
    assert _run(client, body, "k-8", protocol=relabelled).status_code == 200


def test_stale_validation_when_dataset_changed(client, protocol):
    body = _validate(client, protocol)
    label = Path(protocol["console"]["paths"]["train_root"].replace("/images/", "/labels/")) / "train_000.txt"
    label.write_text("0 0.4 0.4 0.2 0.2\n")
    r = _run(client, body, "k-stale")
    assert r.status_code == 409 and r.json()["code"] == "STALE_VALIDATION"
    assert any(e["field"] == "dataset.train_manifest_hash" for e in r.json()["errors"])


def test_run_row_fields_and_planned_jobs(client, protocol, api_settings):
    run_id, val = harness.validate_and_run(client, protocol, key="k-fields")
    conn = connect(api_settings, init=False)
    try:
        run = repo.get_run(conn, run_id)
        jobs = repo.list_jobs(conn, run_id)
        events = repo.get_events_after(conn, run_id, 0, 100)
    finally:
        conn.close()
    assert run["status"] == "QUEUED" and run["namespace"] == "pilot_smoke" and run["execution_mode"] == "PILOT"
    assert run["purpose"] == "SMOKE" and run["preset"] == "PILOT_SMOKE" and run["validation_id"] == val["validation_id"]
    assert run["budget"]["search_per_arm_seed_s"] == 0.75 * 3600 and run["budget"]["baseline_s"] == 0.25 * 3600
    assert run["source_commit"] == "sha256:" + "0" * 64 and run["costs"] == {}
    kinds = sorted(j["kind"] for j in jobs)
    assert kinds == ["BASELINE_TRAIN", "PROPOSE_GENERATION", "PROPOSE_GENERATION"]
    base = next(j for j in jobs if j["kind"] == "BASELINE_TRAIN")
    assert (base["rung"], base["cumulative_target"], base["fixed_horizon"]) == ("L0", 1, 2)
    assert events[0]["type"] == "run.updated" and events[0]["seq"] == 1
    run_dir = Path(protocol["console"]["paths"]["output_root"]) / run_id / "protocol"
    assert (run_dir / "resolved.json").is_file() and (run_dir / "requested.json").is_file()


def test_run_request_rejects_unknown_fields(client, protocol):
    body = _validate(client, protocol)
    r = client.post("/api/v1/runs", json={"protocol": body["resolved"], "protocol_hash": body["protocol_hash"],
                                         "idempotency_key": "k", "surprise": True}, headers=auth())
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
