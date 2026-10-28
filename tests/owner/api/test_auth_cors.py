"""Bearer auth on every /api/v1 route, exact-origin CORS, request ids, Error bodies (synthetic test settings)."""
from __future__ import annotations

import pytest
from harness import ORIGIN, auth

from mbnas_server.api.contract_check import contract_errors

ROUTES = [
    ("GET", "/api/v1/health"), ("GET", "/api/v1/protocols/presets"), ("GET", "/api/v1/registry"),
    ("POST", "/api/v1/protocols/validate"), ("POST", "/api/v1/runs"), ("GET", "/api/v1/runs"),
    ("GET", "/api/v1/runs/run-x"), ("GET", "/api/v1/runs/run-x/snapshot"), ("GET", "/api/v1/runs/run-x/events"),
    ("POST", "/api/v1/runs/run-x/pause"), ("POST", "/api/v1/runs/run-x/resume"), ("POST", "/api/v1/runs/run-x/cancel"),
    ("GET", "/api/v1/runs/run-x/candidates/c1"), ("GET", "/api/v1/runs/run-x/artifacts/a1"),
    ("POST", "/api/v1/runs/run-x/artifacts/a1/refresh"), ("GET", "/api/v1/runs/run-x/export"),
    # contract 1.3.0 read-only archive API: mounted on the gateway since WP-1 (contract §3.1(b)); its 13 rows moved
    # unchanged to tests/public/server/test_auth_cors_gateway.py (A5)
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_requires_bearer(client, method, path):
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401, (path, r.text)
    assert r.headers["www-authenticate"] == "Bearer"
    body = r.json()
    assert body["code"] == "AUTH_REQUIRED" and body["request_id"] == r.headers["x-request-id"]
    assert contract_errors("Error", body) == []
    r = client.request(method, path, json={} if method == "POST" else None, headers=auth("wrong-token"))
    assert r.status_code == 403, (path, r.text)
    assert r.json()["code"] == "ACCESS_DENIED"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer   ", "token abc"])
def test_malformed_authorization_is_401(client, header):
    assert client.get("/api/v1/health", headers={"Authorization": header}).status_code == 401


def test_api_refuses_to_start_without_token(tmp_roots):
    import harness

    from mbnas_core.api.app import create_app

    settings = harness.make_settings(tmp_roots, api_token=None)
    with pytest.raises(RuntimeError, match="MBNAS_CORE_API_TOKEN"):  # DD-A13: core env prefix
        create_app(settings)


def test_cors_exact_origin_only(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = client.options("/api/v1/runs", headers={"Origin": ORIGIN, **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in ok.headers
    bad = client.options("/api/v1/runs", headers={"Origin": "http://evil.example", **pre})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    simple = client.get("/api/v1/health", headers={"Origin": ORIGIN, **auth()})
    assert simple.headers["access-control-allow-origin"] == ORIGIN
    assert "X-Request-ID" in simple.headers["access-control-expose-headers"]
    other = client.get("/api/v1/health", headers={"Origin": "http://localhost:5174", **auth()})
    assert "access-control-allow-origin" not in other.headers
    # 401 answers still carry CORS headers for the allowed origin, so the browser can read the Error body
    denied = client.get("/api/v1/health", headers={"Origin": ORIGIN})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == ORIGIN


def test_wildcard_origin_rejected_by_settings(tmp_roots):
    import harness

    with pytest.raises(ValueError):
        harness.make_settings(tmp_roots, cors_origins=["*"])


def test_request_id_echo_and_replacement(client):
    r = client.get("/api/v1/runs/nope", headers={"X-Request-ID": "trace-123.abc", **auth()})
    assert r.status_code == 404
    assert r.headers["x-request-id"] == "trace-123.abc" and r.json()["request_id"] == "trace-123.abc"
    r = client.get("/api/v1/health", headers={"X-Request-ID": "bad id with spaces", **auth()})
    assert r.headers["x-request-id"] != "bad id with spaces" and len(r.headers["x-request-id"]) == 32


def test_unknown_route_and_docs_disabled(client):
    for path in ("/docs", "/openapi.json", "/api/v1/nope"):
        r = client.get(path, headers=auth())
        assert r.status_code == 404 and contract_errors("Error", r.json()) == []
