"""Bearer auth on the gateway routes (A5 split of the snapshot ``tests/api/test_auth_cors.py``).

The 13 read-only archive rows of contract 1.3.0 moved with the archive router to the gateway (WP-1 contract §3.1(b));
the assertions are the original ones, run against ``mbnas_server.api.app.create_app(GatewaySettings)``. The core rows
stay in ``tests/owner/api/test_auth_cors.py``. Synthetic settings and archive root.
"""
from __future__ import annotations

import pytest
from conftest_common import owner_harness  # DD-A43(b): owner-only harness; module skipped without it

from mbnas_server.api.contract_check import contract_errors

harness = owner_harness()
auth = harness.auth

ROUTES = [
    ("GET", "/api/v1/health"),  # the gateway's own minimal health (contract §3.1(b))
    # contract 1.3.0: read-only archive API
    ("GET", "/api/v1/archive/runs"), ("GET", "/api/v1/archive/runs/run-x"), ("GET", "/api/v1/archive/runs/run-x/baselines"),
    ("GET", "/api/v1/archive/runs/run-x/generations"), ("GET", "/api/v1/archive/runs/run-x/generations/0"),
    ("GET", "/api/v1/archive/runs/run-x/candidates"), ("GET", "/api/v1/archive/runs/run-x/candidates/c1"),
    ("GET", "/api/v1/archive/runs/run-x/pareto"), ("GET", "/api/v1/archive/runs/run-x/fidelity"),
    ("GET", "/api/v1/archive/runs/run-x/confirm"), ("GET", "/api/v1/archive/runs/run-x/files/plot_hypervolume"),
    ("GET", "/api/v1/archive/compare?runs=run-x"), ("GET", "/api/v1/archive/live"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_requires_bearer(make_client, synth_root, method, path):
    client = make_client([synth_root])[0]
    client.headers.pop("Authorization", None)  # the conftest client carries the token by default
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401, (path, r.text)
    assert r.headers["www-authenticate"] == "Bearer"
    body = r.json()
    assert body["code"] == "AUTH_REQUIRED" and body["request_id"] == r.headers["x-request-id"]
    assert contract_errors("Error", body) == []
    r = client.request(method, path, json={} if method == "POST" else None, headers=auth("wrong-token"))
    assert r.status_code == 403, (path, r.text)
    assert r.json()["code"] == "ACCESS_DENIED"


def test_gateway_refuses_to_start_without_token(tmp_roots):
    from mbnas_server.api.app import create_app

    settings = harness.make_gateway_settings(tmp_roots, api_token=None)
    with pytest.raises(RuntimeError, match="MBNAS_GW_API_TOKEN"):  # DD-A13: gateway env prefix
        create_app(settings)
