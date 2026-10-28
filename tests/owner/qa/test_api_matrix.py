"""QA: API matrix beyond tests/api: retries, key variants, stale-validation causes, unvalidated payloads (synthetic)."""
from __future__ import annotations

import copy
import threading
from pathlib import Path

import harness
import pytest
from harness import auth

from mbnas_core.db.core import connect


def _validated(client, protocol):
    body = client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth()).json()
    assert body["valid"], body["errors"]
    return body


def _post(client, body, key, headers=None, **over):
    payload = {"protocol": body["resolved"], "protocol_hash": body["protocol_hash"], "idempotency_key": key,
               "validation_id": body["validation_id"], **over}
    return client.post("/api/v1/runs", json=payload, headers={**auth(), **(headers or {})})


def _count(settings, table):
    conn = connect(settings, init=False)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_lost_response_retries_never_duplicate_runs_jobs_or_events(client, protocol, api_settings):
    body = _validated(client, protocol)
    first = _post(client, body, "retry-1", headers={"Idempotency-Key": "retry-1"})
    runs, jobs, seq = _count(api_settings, "runs"), _count(api_settings, "jobs"), _count(api_settings, "events")
    for _ in range(5):  # client timed out and retried, with and without the header
        again = _post(client, body, "retry-1", headers={"Idempotency-Key": "retry-1"} if _ % 2 else None)
        assert again.status_code == 200 and again.json()["run_id"] == first.json()["run_id"] and again.json()["created"] is False
    assert (_count(api_settings, "runs"), _count(api_settings, "jobs"), _count(api_settings, "events")) == (runs, jobs, seq)


def test_retry_after_completion_and_after_the_dataset_changed_still_returns_the_same_run(client, protocol, inline_worker, api_settings):
    body = _validated(client, protocol)
    first = _post(client, body, "late-retry").json()["run_id"]
    harness.drain(inline_worker)
    Path(protocol["console"]["paths"]["train_root"].replace("/images/", "/labels/")).joinpath("train_000.txt").write_text("0 0.4 0.4 0.2 0.2\n")
    again = _post(client, body, "late-retry")
    assert again.status_code == 200 and again.json()["run_id"] == first and again.json()["created"] is False
    assert _count(api_settings, "runs") == 1
    fresh = _post(client, body, "another-key")  # a NEW key meets the staleness rule
    assert fresh.status_code == 409 and fresh.json()["code"] == "STALE_VALIDATION"


@pytest.mark.parametrize("variant", ["K-1", "k-1 ", " k-1", "k-1​", "K-１"])
def test_keys_differing_in_case_or_whitespace_are_different_keys(client, protocol, api_settings, variant):
    body = _validated(client, protocol)
    a = _post(client, body, "k-1").json()["run_id"]
    b = _post(client, body, variant)
    assert b.status_code == 200 and b.json()["run_id"] != a  # same payload, different key: a distinct run
    assert _count(api_settings, "runs") == 2


@pytest.mark.parametrize("bad_key", ["", None, 123, ["a"], {"k": 1}])
def test_malformed_idempotency_key_is_a_contract_error(client, protocol, api_settings, bad_key):
    body = _validated(client, protocol)
    r = _post(client, body, bad_key)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert _count(api_settings, "runs") == 0


def test_header_and_body_key_mismatch_is_409_and_creates_nothing(client, protocol, api_settings):
    body = _validated(client, protocol)
    r = _post(client, body, "body-key", headers={"Idempotency-Key": "header-key"})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert _count(api_settings, "runs") == 0
    ok = _post(client, body, "body-key", headers={"Idempotency-Key": "body-key"})
    assert ok.status_code == 200 and _count(api_settings, "runs") == 1
    assert _post(client, body, "body-key", headers={"Idempotency-Key": "BODY-KEY"}).status_code == 409


def test_same_key_with_a_different_protocol_body_but_same_hash_is_a_conflict(client, protocol):
    body = _validated(client, protocol)
    assert _post(client, body, "k-body").status_code == 200
    tampered = copy.deepcopy(body["resolved"])
    tampered["console"]["label"] = "tampered"
    r = _post(client, body, "k-body", protocol=tampered)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_eight_concurrent_submissions_of_one_key_create_one_run(client, protocol, api_settings):
    body = _validated(client, protocol)
    answers, errors = [], []

    def go():
        try:
            answers.append(_post(client, body, "race").json())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len({a["run_id"] for a in answers}) == 1
    assert sum(1 for a in answers if a["created"]) == 1
    assert _count(api_settings, "runs") == 1


# -- unvalidated / stale ----------------------------------------------------------------------------------------
def test_never_validated_protocol_is_refused_even_with_a_correct_hash(client, protocol, api_settings):
    from mbnas_contracts.protocol_hash import protocol_hash

    r = client.post("/api/v1/runs", headers=auth(), json={"protocol": protocol, "protocol_hash": protocol_hash(protocol),
                                                         "idempotency_key": "never"})
    assert r.status_code == 422 and r.json()["code"] == "UNVALIDATED"
    body = _validated(client, protocol)
    r = _post(client, body, "ghost", validation_id="val-doesnotexist")
    assert r.status_code == 422 and r.json()["code"] == "UNVALIDATED"
    assert _count(api_settings, "runs") == 0


def test_validation_of_another_protocol_cannot_be_reused(client, protocol, api_settings):
    a = _validated(client, protocol)
    other = copy.deepcopy(protocol)
    other["console"]["search"]["population"] = 3
    b = _validated(client, other)
    r = client.post("/api/v1/runs", headers=auth(), json={"protocol": b["resolved"], "protocol_hash": b["protocol_hash"],
                                                         "idempotency_key": "mix", "validation_id": a["validation_id"]})
    assert r.status_code == 409 and r.json()["code"] == "PROTOCOL_HASH_MISMATCH"
    assert _count(api_settings, "runs") == 0


def test_stale_when_registry_capability_changes(client, protocol, registry, api_settings):
    body = _validated(client, protocol)
    bid = next(b["id"] for b in protocol["console"]["baselines"] if b.get("enabled"))
    registry[bid].update(status="BLOCKED_SOURCE", reason="weights hash drifted since validation")
    r = _post(client, body, "stale-registry")
    assert r.status_code == 409 and r.json()["code"] == "STALE_VALIDATION"
    assert _count(api_settings, "runs") == 0


def test_stale_when_the_device_disappears(client, protocol, services, api_settings):
    body = _validated(client, protocol)
    cached = services._cache["devices"]  # noqa: SLF001 - the host lost its GPU after validation (30 s TTL cache)
    cached.fn, cached.at = (lambda: []), None
    r = _post(client, body, "stale-device")
    assert r.status_code == 409 and r.json()["code"] == "STALE_VALIDATION"
    assert _count(api_settings, "runs") == 0


def test_stale_when_a_split_image_is_added(client, protocol, api_settings):
    body = _validated(client, protocol)
    from PIL import Image

    Image.new("RGB", (32, 24), (9, 9, 9)).save(Path(protocol["console"]["paths"]["train_root"]) / "late_addition.png")
    r = _post(client, body, "stale-data")
    assert r.status_code == 409 and r.json()["code"] == "STALE_VALIDATION"
    assert any(e["field"] == "dataset.train_manifest_hash" for e in r.json()["errors"])
    again = _validated(client, protocol)  # validate again -> a fresh id and hash -> runnable
    assert again["protocol_hash"] != body["protocol_hash"]
    assert _post(client, again, "stale-data-2").status_code == 200


def test_unknown_run_and_state_endpoints_are_404_not_500(client):
    for method, path in (("GET", "/api/v1/runs/run-nope"), ("GET", "/api/v1/runs/run-nope/snapshot"),
                         ("GET", "/api/v1/runs/run-nope/events"), ("POST", "/api/v1/runs/run-nope/pause"),
                         ("POST", "/api/v1/runs/run-nope/resume"), ("POST", "/api/v1/runs/run-nope/cancel"),
                         ("GET", "/api/v1/runs/run-nope/candidates/c1"), ("GET", "/api/v1/runs/run-nope/export")):
        r = client.request(method, path, headers=auth())
        assert r.status_code == 404, (method, path, r.status_code, r.text[:100])
        assert r.json()["code"] and r.json()["request_id"]


def test_methods_not_in_the_contract_are_rejected(client):
    for method, path in (("DELETE", "/api/v1/runs/run-x"), ("PUT", "/api/v1/runs"), ("PATCH", "/api/v1/runs/run-x"),
                         ("POST", "/api/v1/health"), ("GET", "/api/v1/protocols/validate")):
        r = client.request(method, path, headers=auth())
        assert r.status_code in (404, 405), (method, path, r.status_code)
