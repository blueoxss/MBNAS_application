"""REQ-6.06 / V23: an unhandled server error answers with a safe body and a trace id, never a traceback or a server path.

The trace id is the ``request_id`` of the contract ``Error`` body (``api/errors.py``); a caller-supplied well-formed
``X-Request-ID`` is kept, so the id a client logs is the one in the server log line ``unhandled error (request_id=...)``.
"""
from __future__ import annotations

import logging

import pytest
from harness import auth

SECRET_PATH = "/srv/secret/mbnas_server/search/verdict_logic.py"


@pytest.mark.parametrize("side", ["gateway", "core"])
def test_unhandled_error_has_a_trace_id_and_no_traceback_or_path(side, app, core_app, combined, api_settings, caplog):
    """WP-3C: run on BOTH apps of the combined harness. ``app`` is now the gateway, whose SPA/static fallback is
    registered before a route added afterwards, so the probe is inserted at the front of the router (it is otherwise the
    same probe); the core probe sits on the console mount and is called with the gateway's server principal."""
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient

    def _boom():  # a route that fails with a message that names a server source path
        raise RuntimeError(f"boom in {SECRET_PATH}")

    target, path, token, logger = ((app, "/api/v1/_trace_probe", api_settings.api_token, "mbnas_server.api")
                                   if side == "gateway" else
                                   (core_app, "/core/v1/console/_trace_probe", combined.tokens["server"],
                                    "mbnas_core.api"))  # SYNC-1 A19: core logger (mbnas_core.api.errors)
    target.router.routes.insert(0, APIRoute(path, _boom, methods=["GET"]))
    with TestClient(target, raise_server_exceptions=False) as c, caplog.at_level(logging.ERROR, logger=logger):
        r = c.get(path, headers={**auth(token), "X-Request-ID": "trace-abc123"})
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "INTERNAL_ERROR" and body["request_id"] == "trace-abc123"
    text = r.text
    assert "Traceback" not in text and SECRET_PATH not in text and "boom" not in text and "verdict_logic" not in text
    # the same id is what the operator finds in the server log (the traceback stays there, not in the response)
    assert any("request_id=trace-abc123" in rec.getMessage() for rec in caplog.records)


def test_error_without_a_supplied_id_still_gets_one(client):
    r = client.get("/api/v1/runs/run-x")  # no bearer -> 401 Error body
    assert r.status_code == 401
    assert r.json()["request_id"] and r.json()["request_id"] == r.headers["x-request-id"]
