"""DD-A60 (5) gateway side: one access-log line per HTTP request (method, path, status, request_id, duration_ms,
client) on the gateway's access logger, written to the process stderr through the redaction filter; secrets in the
query string are redacted, control characters escaped, and early answers (401, dot-segment 404, 413) are logged too.
Needs only mbnas_contracts + mbnas_server (no core); SYNTHETIC; no socket.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mbnas_server.api import app as appmod
from mbnas_server.config import GatewaySettings

TOKEN = "gw-test-token-dd-a60-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NO_WEB_DIST = str(Path(__file__).resolve().parent / "no-such-web-dist")
LINE = re.compile(r"^method=(?P<method>\S+) path=(?P<path>\S+) status=(?P<status>\d{3}) request_id=(?P<rid>\S+) "
                  r"duration_ms=(?P<ms>\d+\.\d) client=(?P<client>\S+)$")


@pytest.fixture(autouse=True)
def _home(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))


@pytest.fixture
def lines():
    out: list[str] = []

    class Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            out.append(record.getMessage())

    lg = logging.getLogger(appmod.ACCESS_LOGGER)
    cap = Cap(logging.DEBUG)
    lg.addHandler(cap)
    yield out
    lg.removeHandler(cap)


def _client(**kw) -> TestClient:
    kw.setdefault("web_dist", NO_WEB_DIST)
    return TestClient(appmod.create_app(GatewaySettings(api_token=TOKEN, **kw)))


def _parsed(lines: list[str], rid: str | None = None) -> list[dict[str, str]]:
    out = []
    for line in lines:
        m = LINE.match(line)
        assert m, f"malformed access line: {line!r}"
        if rid is None or m["rid"] == rid:
            out.append(m.groupdict())
    return out


def test_exactly_one_line_per_request_with_the_echoed_request_id(lines, capsys):
    c = _client()
    r = c.get("/api/v1/health", headers={**AUTH, "X-Request-ID": "dd-a60-gw-1"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "dd-a60-gw-1"
    (line,) = _parsed(lines, "dd-a60-gw-1")
    assert (line["method"], line["path"], line["status"]) == ("GET", "/api/v1/health", "200")
    generated = c.get("/api/v1/health", headers=AUTH).headers["x-request-id"]  # no client id: the generated one
    assert [x["rid"] for x in _parsed(lines, generated)] == [generated]
    assert len(lines) == 2
    err = capsys.readouterr().err  # the gateway's existing log destination: its process stderr
    assert f"{appmod.ACCESS_LOGGER} method=GET path=/api/v1/health status=200 request_id=dd-a60-gw-1 " in err


def test_early_answers_are_logged_and_query_secrets_redacted(lines):
    secret = "querysecret-DD-A60-gateway"
    c = _client(max_request_body_bytes=1024)
    assert c.get(f"/api/v1/health?token={secret}").status_code == 401
    assert c.get(f"/api/v1/runs/x/artifacts/a?exp=1799999999&sig={secret}").status_code in (401, 403, 404, 503)
    assert c.get("/api/v1/runs/%2E%2E/x", headers=AUTH).status_code == 404  # DD-A44 (e) path guard
    assert c.post("/api/v1/health", content=b"x" * 2048, headers={**AUTH, "X-Request-ID": "dd-a60-413"}).status_code == 413
    assert c.get("/api/v1/a%0Ab", headers={**AUTH, "X-Request-ID": "dd-a60-gw-esc"}).status_code == 404
    assert all(secret not in x and "\n" not in x for x in lines), lines
    rows = _parsed(lines)
    assert ("/api/v1/health?token=***", "401") in [(x["path"], x["status"]) for x in rows]
    assert any(x["path"].endswith("?exp=***&sig=***") for x in rows)
    assert ("/api/v1/runs/../x", "404") in [(x["path"], x["status"]) for x in rows]  # logged before routing
    assert [x["status"] for x in _parsed(lines, "dd-a60-413")] == ["413"]
    assert [x["path"] for x in _parsed(lines, "dd-a60-gw-esc")] == ["/api/v1/a%0Ab"]


def test_mbnas_gateway_runs_uvicorn_without_its_own_access_line(monkeypatch):
    import uvicorn

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    assert appmod.main([]) == 0
    assert seen["access_log"] is False and seen["factory"] is True
