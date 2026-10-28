"""DD-A59 (a) / T8 section 2: the gateway health never relays the core's allowlists (split deployment).

The public gateway app (``mbnas_server.api.app.create_app``) relays ``GET /api/v1/health`` to a STUB core
(``stub_core.StubCore`` through ``httpx.ASGITransport``: no socket, no ``mbnas_core``, no owner harness, so this module
also runs from the public and registration bundles). The stub's health answer carries absolute core-host roots in
``allowlists`` (plus a key the 1.5.0 contract does not know). The gateway must answer with its own ``dataset_roots`` /
``output_roots`` (empty lists when unset, the ``install.sh --profile public`` default where T8 found the leak) and its
``archive_roots``, and keep the rest of the merged health (core status summary, versions, warnings). The core-host
paths are synthetic needles, not host facts; none of them may appear anywhere in the response text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mbnas_server.api.app import create_app
from mbnas_server.api.contract_check import contract_errors
from mbnas_server.config import GatewaySettings

from stub_core import CORE_TOKEN, PREFIX, StubCore

TOKEN = "gw-split-health-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# absolute roots of the core (owner) host; the marker is unique, so a hit anywhere in the response is a relay
CORE_HOST_MARK = "owner-core-host-a59"
CORE_ROOTS = {"dataset_roots": [f"/{CORE_HOST_MARK}/core/datasets", "/srv/core-only/datasets-b"],
              "output_roots": [f"/{CORE_HOST_MARK}/core/runs"],
              "model_yaml_roots": [f"/{CORE_HOST_MARK}/core/uploads"]}  # not a contract key: must not pass either
CORE_VERSIONS = {"python": "3.10-stub", "packages": {"stub-package": "0.0.0"}, "environment_lock": None}
CORE_WARNINGS = ["no live worker heartbeat (jobs will stay QUEUED)"]
COMMIT = "sha256:" + "5" * 64


def core_health() -> dict[str, Any]:
    """Contract-shaped core health answer (a degraded core: no worker yet) whose allowlists name core-host roots."""
    return {"status": "degraded", "warnings": list(CORE_WARNINGS), "capabilities": {"sse": True, "idempotency": True},
            "versions": CORE_VERSIONS, "devices": [], "registry": [], "commit_sha": COMMIT,
            "source_tree": {"algorithm": "stub", "sha256": COMMIT, "files": 1}, "server_time": "2026-10-28T00:00:00Z",
            "allowlists": {k: list(v) for k, v in CORE_ROOTS.items()}, "presets": [], "worker_count": 0}


@pytest.fixture(autouse=True)
def _gateway_home_in_tmp(clean_env, monkeypatch, tmp_path):
    """Any ``$MBNAS_HOME`` default stays in the test's tmp dir (every gateway path is also pinned below)."""
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))


def customer_roots(tmp_path: Path) -> dict[str, list[str]]:
    roots = {"dataset_roots": [str(tmp_path / "customer" / "data")],
             "output_roots": [str(tmp_path / "customer" / "runs"), str(tmp_path / "customer" / "runs2")],
             "archive_roots": [str(tmp_path / "customer" / "archive")]}
    for paths in roots.values():
        for p in paths:
            Path(p).mkdir(parents=True)
    return roots


def gateway_over(stub: StubCore, tmp_path: Path, **roots: Any) -> TestClient:
    gw = tmp_path / "gateway"
    settings = GatewaySettings(api_token=TOKEN, core_url="http://core.invalid", core_token=CORE_TOKEN,
                               artifact_store=str(gw / "artifacts"), log_dir=str(gw / "logs"),
                               audit_log=str(gw / "logs" / "audit.jsonl"), manifest_cache_root=str(gw),
                               web_dist=str(gw / "no-web-dist"), **roots)
    return TestClient(create_app(settings, core_transport=httpx.ASGITransport(app=stub.app)))


@pytest.mark.parametrize("gateway_roots", ["empty", "set"])
def test_gateway_health_never_relays_core_allowlists(tmp_path, gateway_roots):
    stub = StubCore()
    stub.on("GET", f"{PREFIX}/health", body=core_health())
    roots = customer_roots(tmp_path) if gateway_roots == "set" else {}
    expected = {key: roots.get(key, []) for key in ("dataset_roots", "output_roots", "archive_roots")}
    with gateway_over(stub, tmp_path, **roots) as c:
        r = c.get("/api/v1/health", headers=AUTH)
    assert r.status_code == 200, r.text
    call = stub.last("/health")  # answered by the core (not the unreachable branch), with the service token
    assert call["path"] == f"{PREFIX}/health" and call["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"
    body = r.json()
    # only the gateway's own roots, exactly (empty lists when unset), and the contract's three keys
    assert body["allowlists"] == expected
    for needle in (CORE_HOST_MARK, *[p for paths in CORE_ROOTS.values() for p in paths], "model_yaml_roots"):
        assert needle not in r.text, needle
    # the rest of the merged health is unchanged: core status summary, versions, warnings, gateway self status
    assert body["core"] == {"status": "degraded", "reachable": True}
    assert body["status"] == "degraded" and body["warnings"] == CORE_WARNINGS
    assert body["versions"] == CORE_VERSIONS and body["commit_sha"] == COMMIT and body["worker_count"] == 0
    assert body["gateway"]["status"] == "ok" and body["gateway"]["core_url_configured"] is True
    assert body["request_id"] == r.headers["x-request-id"]
    assert contract_errors("Health", body) == [], contract_errors("Health", body)[:5]
