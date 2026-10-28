"""WP-3C gateway unit tests against a STUB core (contract WP-3 sections 2.1-2.8, 3; SECURITY_BASELINE R1/R7/R11/R13).

The gateway app is real (``mbnas_server.api.app.create_app``); the upstream is ``stub_core.StubCore`` reached through
``harness.StreamingASGITransport`` (no sockets), except the CORE_UNAVAILABLE rows, which point the gateway at a closed
127.0.0.1 port with no transport override (real connection refusal). The stub's answers are not core's: these rows pin
what the gateway does with a given upstream answer. The real relay is exercised by tests/owner via ``combined_app``.
"""
from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest_common import owner_harness  # DD-A43(b): owner-only harness; module skipped without it

from stub_core import CORE_TOKEN, PREFIX, StubCore

harness = owner_harness()
ORIGIN, TOKEN, auth = harness.ORIGIN, harness.TOKEN, harness.auth


@pytest.fixture
def stub() -> StubCore:
    return StubCore()


@pytest.fixture
def gw(tmp_roots, stub):
    """``gw(**settings_overrides)`` -> TestClient over a gateway relaying to ``stub``."""
    opened: list[TestClient] = []

    def build(**overrides: Any) -> TestClient:
        settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN, **overrides)
        app = harness.gateway_app_over(settings, stub.app, harness.StreamingASGITransport(stub.app))
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c

    yield build
    for c in opened:
        c.__exit__(None, None, None)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------------------------------------------ auth (R1)
@pytest.mark.parametrize("method,path", [("GET", "/api/v1/runs"), ("POST", "/api/v1/protocols/validate"),
                                         ("GET", "/api/v1/runs/r1/events"), ("POST", "/api/v1/uploads/model-config"),
                                         ("GET", "/api/v1/runs/r1/candidates/c1")])
def test_user_bearer_required_and_never_reaches_core(gw, stub, method, path):
    c = gw()
    r = c.request(method, path)
    assert r.status_code == 401 and r.json()["code"] == "AUTH_REQUIRED"
    assert r.headers["www-authenticate"] == "Bearer"
    r = c.request(method, path, headers=auth("wrong-token"))
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED"
    # the gateway's own service token is not a user credential
    r = c.request(method, path, headers=auth(CORE_TOKEN))
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED"
    assert stub.calls == []  # nothing was relayed before authentication


def test_relay_swaps_user_bearer_for_service_token_and_chains_request_id(gw, stub):
    stub.on("GET", f"{PREFIX}/runs", body={"runs": [], "next": None})
    r = gw().get("/api/v1/runs?limit=5", headers={**auth(), "X-Request-ID": "rid-abc-123"})
    assert r.status_code == 200, r.text
    call = stub.last("/runs")
    assert call["path"] == f"{PREFIX}/runs" and call["query"].get("limit") == "5"
    assert call["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"
    assert TOKEN not in json.dumps(call["headers"])  # the user's token never leaves the gateway
    assert call["headers"].get("x-request-id") == "rid-abc-123"
    assert r.headers["x-request-id"] == "rid-abc-123"


def test_core_error_is_relayed_with_same_code_and_location_core(gw, stub):
    stub.on("GET", f"{PREFIX}/runs/nope", status=404,
            body={"code": "RUN_NOT_FOUND", "message": "run nope not found", "request_id": "core-rid"})
    r = gw().get("/api/v1/runs/nope", headers={**auth(), "X-Request-ID": "rid-404"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RUN_NOT_FOUND" and body["location"] == "core"
    assert body["request_id"] == "rid-404"
    assert set(body) >= {"code", "location", "message", "request_id"}


# ------------------------------------------------------------------------------------------------ path confusion
DOT = "%2E%2E"


@pytest.mark.parametrize("method,path", [
    ("GET", f"/api/v1/runs/r1/artifacts/{DOT}"), ("GET", "/api/v1/runs/r1/artifacts/%2E"),
    ("GET", f"/api/v1/runs/{DOT}/snapshot"), ("GET", f"/api/v1/runs/{DOT}/candidates/{DOT}"),
    ("POST", f"/api/v1/runs/{DOT}/candidates/{DOT}/structure-check"), ("GET", f"/api/v1/registry/{DOT}/structure"),
    ("POST", f"/api/v1/runs/{DOT}/artifacts/{DOT}/refresh")])
def test_dot_segment_path_params_never_reach_another_core_route(gw, stub, method, path):
    """A percent-encoded dot segment in a path parameter must not be normalised into a DIFFERENT core route
    (measured 2026-10-27: ``/api/v1/runs/r1/artifacts/%2E%2E`` was relayed as ``/core/v1/console/runs/r1``, and
    ``/api/v1/runs/%2E%2E/candidates/%2E%2E/structure-check`` as ``/core/v1/console/structure-check``)."""
    r = gw().request(method, path, headers=auth(), json={} if method == "POST" else None, follow_redirects=False)
    assert r.status_code in (400, 404), (r.status_code, r.text)
    assert r.headers["content-type"].startswith("application/json")
    assert stub.calls == [], [c["path"] for c in stub.calls]


@pytest.mark.parametrize("raw,decoded", [("%00", "\x00"), ("art-%00", "art-\x00"), ("a%0Ab", "a\nb"),
                                         ("%7F", "\x7f")])
def test_control_characters_in_path_params_never_crash_the_relay(gw, stub, raw, decoded):
    """``/api/v1/runs/r1/artifacts/%00`` raised ``httpx.InvalidURL`` inside the relay (core_client.py:122, measured
    2026-10-27) = an unhandled 500. The gateway must answer a JSON 4xx itself, or relay the parameter percent-encoded
    so core sees exactly the decoded value (core answers 404 for an unknown id)."""
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/{decoded}", status=404,
            body={"code": "NOT_FOUND", "message": "artifact not found"})
    r = gw().get(f"/api/v1/runs/r1/artifacts/{raw}", headers=auth())
    assert r.status_code in (400, 404), (r.status_code, r.text)
    assert r.headers["content-type"].startswith("application/json")
    assert all(c["path"] == f"{PREFIX}/runs/r1/artifacts/{decoded}" for c in stub.calls), [c["path"] for c in stub.calls]


# ------------------------------------------------------------------------------------------------ CORS
def test_cors_exact_origin_only(gw):
    c = gw()
    ok = c.options("/api/v1/runs", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                            "Access-Control-Request-Headers": "Authorization"})
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    assert "access-control-allow-credentials" not in ok.headers
    bad = c.options("/api/v1/runs", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert bad.headers.get("access-control-allow-origin") is None


# ------------------------------------------------------------------------------------------------ limits
def test_body_limit_413_before_relay(gw, stub):
    c = gw(max_request_body_bytes=1024)
    r = c.post("/api/v1/protocols/validate", content=b"{" + b" " * 4096 + b"}",
               headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["code"] == "PAYLOAD_TOO_LARGE" and r.json()["location"] == "gateway"  # C05 D3
    assert stub.calls == []


def test_rate_limit_token_bucket_429(gw, stub):
    c = gw(rate_limit_rps=0.001, rate_limit_burst=3)
    codes = [c.get("/api/v1/runs", headers=auth()).status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200], codes
    assert set(codes[3:]) == {429}, codes
    r = c.get("/api/v1/runs", headers=auth())
    assert r.json()["code"] == "RATE_LIMITED" and r.json()["location"] == "gateway"
    assert len(stub.calls) == 3  # rejected requests never reach core


def test_expensive_routes_per_minute_limit(gw, stub):
    c = gw(expensive_per_minute=2, rate_limit_burst=50)
    codes = [c.post("/api/v1/protocols/validate", json={"protocol": {}}, headers=auth()).status_code for _ in range(3)]
    assert codes[2] == 429, codes
    assert c.post("/api/v1/protocols/validate", json={"protocol": {}}, headers=auth()).json()["code"] == "RATE_LIMITED"


# ------------------------------------------------------------------------------------------------ redaction
def test_validate_response_realpath_is_scrubbed_to_requested(gw, stub, tmp_roots):
    secret_real = "/srv/private/host/layout/data"
    stub.on("POST", f"{PREFIX}/protocols/validate", body={
        "valid": True, "errors": [], "warnings": [], "protocol_hash": "sha256:" + "1" * 64, "validation_id": "v1",
        "resolved": {}, "resolution": {"paths": {"train_root": {"requested": "/data/train", "realpath": secret_real}}}})
    r = gw().post("/api/v1/protocols/validate", json={"protocol": {}}, headers=auth())
    assert r.status_code == 200, r.text
    assert secret_real not in r.text
    paths = r.json()["resolution"]["paths"]
    assert paths["train_root"]["realpath"] == "/data/train"


def test_core_token_never_in_any_response_or_error(gw, stub):
    c = gw()
    stub.on("GET", f"{PREFIX}/runs/x", status=500, body={"code": "INTERNAL", "message": "boom"})
    for r in (c.get("/api/v1/runs/x", headers=auth()), c.get("/api/v1/health", headers=auth()),
              c.get("/api/v1/runs", headers=auth("bad"))):
        assert CORE_TOKEN not in r.text and "stub-core-secret" not in r.text


# ------------------------------------------------------------------------------------------------ CORE_UNAVAILABLE
def test_core_down_health_degraded_and_relays_503_without_fallback(tmp_roots):
    from mbnas_server.api.app import create_app

    settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN, core_url=f"http://127.0.0.1:{_closed_port()}",
                                                 core_connect_timeout_s=1.0, core_read_timeout_s=2.0)
    with TestClient(create_app(settings)) as c:
        h = c.get("/api/v1/health", headers=auth())
        assert h.status_code == 200
        assert h.json()["status"] == "degraded" and h.json()["core"] == "unreachable"
        for method, path, body in (("POST", "/api/v1/protocols/validate", {"protocol": {}}), ("GET", "/api/v1/runs", None),
                                   ("GET", "/api/v1/protocols/presets", None),
                                   ("POST", "/api/v1/runs/r1/pause", {}), ("GET", "/api/v1/runs/r1/snapshot", None)):
            r = c.request(method, path, json=body, headers=auth())
            assert r.status_code == 503, (path, r.status_code, r.text)
            assert r.json()["code"] == "CORE_UNAVAILABLE" and r.json()["location"] == "core", r.text
            assert "valid" not in r.json()  # no local validation fallback


# ------------------------------------------------------------------------------------------------ SSE relay
def test_sse_relay_is_byte_faithful_and_forwards_last_event_id(gw, stub):
    c = gw()
    with c.stream("GET", "/api/v1/runs/r1/events?after_seq=0", headers={**auth(), "Last-Event-ID": "7"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    assert text == "".join(stub.sse_frames)
    call = stub.last("/events")
    assert call["headers"].get("last-event-id") == "7"
    assert call["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"


@pytest.mark.parametrize("bad", [b"\xb2", b"\xb9\xb2", "abc", "-1", ""])
def test_sse_relay_survives_a_malformed_last_event_id(gw, stub, bad):
    """Core ignores a malformed ``Last-Event-ID`` (owner qa/test_sse_server.py:106); the relay must not turn it into a
    500: a non-ASCII value raised ``UnicodeEncodeError`` in httpx header encoding (core_client.py:136, measured
    2026-10-27). Either forwarded byte-exact or dropped; never an error."""
    c = gw()
    r = c.get("/api/v1/runs/r1/events?after_seq=0", headers={**auth(), "Last-Event-ID": bad})
    assert r.status_code == 200, r.text
    assert r.text == "".join(stub.sse_frames)
    sent = stub.last("/events")["headers"].get("last-event-id")
    expected = bad.decode("latin-1") if isinstance(bad, bytes) else bad
    assert sent in (None, expected), (sent, expected)


def test_sse_concurrency_per_token(gw, stub):
    """Starlette's TestClient collects the whole response body before ``stream()`` returns, so the held-open first
    stream runs in a thread; the second request is made while the stub still holds the first one open."""
    import threading
    import time

    c = gw(sse_max_per_token=1)
    stub.sse_hold_open = True
    first: dict[str, Any] = {}

    def hold() -> None:
        r = c.get("/api/v1/runs/r1/events", headers=auth())
        first["status"], first["body"] = r.status_code, r.text

    t = threading.Thread(target=hold, daemon=True)
    try:
        t.start()
        deadline = time.monotonic() + 10
        while not any(call["path"].endswith("/runs/r1/events") for call in stub.calls):
            assert time.monotonic() < deadline, "first stream never reached the stub core"
            time.sleep(0.02)
        second = c.get("/api/v1/runs/r1/events", headers=auth())
        assert second.status_code == 429 and second.json()["code"] == "RATE_LIMITED", second.text
    finally:
        stub.sse_hold_open = False
        t.join(timeout=15)
    assert not t.is_alive()
    assert first["status"] == 200 and "event: run.created" in first["body"], first
    # the slot is released once the first stream ended
    assert c.get("/api/v1/runs/r1/events", headers=auth()).status_code == 200


# ------------------------------------------------------------------------------------------------ uploads (F5a)
YAML_OK = b"# user model\nnc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [-1, 1, Detect, [nc]]\n"


def test_upload_raw_body_stores_original_bytes(gw, tmp_roots):
    c = gw()
    r = c.post("/api/v1/uploads/model-config", content=YAML_OK,
               headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": "m.yaml"})
    assert r.status_code == 200, r.text
    body = r.json()
    sha = hashlib.sha256(YAML_OK).hexdigest()
    assert body["source_sha256"].removeprefix("sha256:") == sha and body["bytes"] == len(YAML_OK)
    assert body["upload_id"]
    stored = Path(tmp_roots["runs"]) / "gateway" / "artifacts" / "uploads" / f"{sha}.yaml"
    assert stored.read_bytes() == YAML_OK  # original bytes preserved (comments included)


def test_upload_multipart_and_rejections(gw):
    c = gw()
    r = c.post("/api/v1/uploads/model-config", files={"file": ("m.yml", YAML_OK, "application/x-yaml")}, headers=auth())
    assert r.status_code == 200, r.text
    bad_ext = c.post("/api/v1/uploads/model-config", files={"file": ("m.txt", YAML_OK, "text/plain")}, headers=auth())
    assert bad_ext.status_code in (415, 422), bad_ext.text
    big = c.post("/api/v1/uploads/model-config", files={"file": ("m.yaml", b"a: 1\n" * 300_000, "application/x-yaml")},
                 headers=auth())
    assert big.status_code == 413, big.status_code


def test_structure_check_upload_id_is_resolved_to_text_and_sha(gw, stub):
    c = gw()
    up = c.post("/api/v1/uploads/model-config", files={"file": ("m.yaml", YAML_OK, "application/x-yaml")},
                headers=auth()).json()
    r = c.post("/api/v1/structure-check", json={"upload_id": up["upload_id"], "input_size": 320}, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/structure-check")["body"]
    assert sent.get("model_yaml_text") == YAML_OK.decode()
    assert sent.get("source_sha256", "").removeprefix("sha256:") == hashlib.sha256(YAML_OK).hexdigest()
    assert "upload_id" not in sent and "model_path" not in sent  # core never receives a gateway-local path
    unknown = c.post("/api/v1/structure-check", json={"upload_id": "0" * 64}, headers=auth())
    assert unknown.status_code in (404, 422) and unknown.json()["location"] == "gateway"


# ------------------------------------------------------------------------------------------------ audit log (R13)
def test_sensitive_reads_are_audited_with_token_hash(gw, stub, tmp_roots):
    c = gw()
    c.get("/api/v1/runs/r1/candidates/c1", headers=auth())
    log = Path(harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN).audit_log)
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert lines, "no audit line written"
    text = log.read_text()
    assert TOKEN not in text and CORE_TOKEN not in text
    assert any("c1" in json.dumps(x) for x in lines)


# ------------------------------------------------------------------------------------------------ static web (DD-A30)
@pytest.fixture
def dist(tmp_path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>MBNAS</title><div id=root></div>")
    (d / "assets" / "app-3f2a1b.js").write_text("console.log(1)")
    return d


def test_static_web_spa_fallback_and_asset_cache(gw, dist):
    c = gw(web_dist=str(dist))
    root = c.get("/")
    assert root.status_code == 200 and "MBNAS" in root.text
    deep = c.get("/monitor/run-1")
    assert deep.status_code == 200 and deep.text == root.text  # SPA fallback
    asset = c.get("/assets/app-3f2a1b.js")
    assert asset.status_code == 200 and "immutable" in asset.headers.get("cache-control", "")
    api = c.get("/api/v1/does-not-exist", headers=auth())
    assert api.status_code == 404 and api.headers["content-type"].startswith("application/json")
    assert c.get("/../../etc/passwd").status_code in (400, 404) or "root:" not in c.get("/../../etc/passwd").text


def test_static_web_absent_answers_json_404(gw, tmp_path):
    c = gw(web_dist=str(tmp_path / "missing"))
    r = c.get("/")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


# ------------------------------------------------------------------------------------------------ validate preprocessing
def test_validate_attaches_gateway_manifests_and_path_checks(gw, stub, make_dataset, tmp_roots):
    ds = make_dataset("gwds", counts={"train": 3, "val": 2, "test": 2})
    proto = {"console": {"paths": {"dataset_yaml": ds["dataset_yaml"], "train_root": ds["train_root"],
                                   "search_val_root": ds["search_val_root"], "final_eval_root": ds["final_eval_root"],
                                   "output_root": str(tmp_roots["runs"] / "out")}}}
    r = gw().post("/api/v1/protocols/validate", json={"protocol": proto}, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/protocols/validate")["body"]
    manifests = sent.get("dataset_manifests")
    assert manifests, sent.keys()
    # DD-A37: the wire format is the core gateway_inputs schema
    assert manifests["format"] == "mbnas.gateway-manifests/1", manifests
    assert set(manifests) <= {"format", "splits", "cross_split_overlap", "cross_split_overlap_total"}, manifests.keys()
    assert set(manifests["splits"]) == {"train", "search_val", "final_eval"}, manifests["splits"].keys()
    for m in manifests["splits"].values():
        assert {"split_hash", "counts", "class_mapping_hash", "names", "sample_ids_sha256"} <= set(m)
        assert "format" not in m
    assert isinstance(manifests["cross_split_overlap"], list)
    assert manifests["cross_split_overlap_total"] == len(manifests["cross_split_overlap"])
    assert sent.get("path_checks")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_validate_with_non_finite_numbers_on_the_wire_never_crashes(gw, stub, token):
    """Python's JSON parser accepts NaN/Infinity; re-encoding them for core raised ``ValueError: Out of range float
    values are not JSON compliant`` in the relay (relay.py:179 -> core_client.py:122, measured 2026-10-27; owner
    qa/test_validator_negatives.py). The gateway must answer 422 itself (or let core decide) - never crash, and its
    answer is strict JSON."""
    raw = '{"protocol": {"console": {"search": {"budget_gpu_hours": %s}}}}' % token
    r = gw().post("/api/v1/protocols/validate", content=raw, headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code in (200, 422), (r.status_code, r.text[:300])
    if r.status_code == 422:
        assert r.json()["code"] == "VALIDATION_FAILED"
    assert "NaN" not in r.text and "Infinity" not in r.text


def test_validate_path_outside_gateway_roots_is_refused_or_flagged(gw, stub, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    proto = {"console": {"paths": {"train_root": str(outside), "output_root": str(outside)}}}
    r = gw().post("/api/v1/protocols/validate", json={"protocol": proto}, headers=auth())
    if r.status_code == 200:  # relayed with a failing path check: core decides, gateway must have flagged it
        checks = json.dumps(stub.last("/protocols/validate")["body"].get("path_checks"))
        assert str(outside) in checks and "false" in checks.lower()
    else:
        assert r.status_code == 422 and r.json()["location"] == "gateway"
    assert stub.calls == [] or "dataset_manifests" not in stub.last()["body"] or str(outside) not in json.dumps(
        stub.last()["body"]["dataset_manifests"])
