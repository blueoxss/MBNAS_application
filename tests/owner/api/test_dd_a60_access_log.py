"""DD-A60 (5) (C11 "T5 log request id linkage"): one access-log line per request on BOTH sides, same request id.

The WP-3 combined harness (gateway app relaying ``/api/v1`` to the core app over the in-process transport) is driven
with the user bearer; the gateway forwards its ``X-Request-ID`` to the core. Each app writes exactly one
``method= path= status= request_id= duration_ms= client=`` line per HTTP request to its access logger (stderr handler,
redaction filter); query-string secrets are redacted and control characters escaped. SYNTHETIC; no socket, no GPU.
"""
from __future__ import annotations

import logging
import re

import pytest
from harness import auth

LINE = re.compile(r"^method=(?P<method>\S+) path=(?P<path>\S+) status=(?P<status>\d{3}) request_id=(?P<rid>\S+) "
                  r"duration_ms=(?P<ms>\d+\.\d) client=(?P<client>\S+)$")


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def parsed(self, rid: str | None = None) -> list[dict[str, str]]:
        out = []
        for line in self.lines:
            m = LINE.match(line)
            assert m, f"malformed access line: {line!r}"
            if rid is None or m["rid"] == rid:
                out.append(m.groupdict())
        return out


@pytest.fixture
def access():
    from mbnas_core.api import app as core_mod
    from mbnas_server.api import app as gw_mod

    hooks = {}
    for side, mod in (("core", core_mod), ("gateway", gw_mod)):
        lg = logging.getLogger(mod.ACCESS_LOGGER)
        handler = _Records()
        lg.addHandler(handler)
        hooks[side] = (lg, handler)
    yield {side: handler for side, (_lg, handler) in hooks.items()}
    for lg, handler in hooks.values():
        lg.removeHandler(handler)


def test_one_line_per_request_on_both_sides_with_the_same_request_id(client, access):
    rid = "dd-a60-linkage-0001"
    r = client.get("/api/v1/runs", headers={**auth(), "X-Request-ID": rid})
    assert r.status_code == 200 and r.headers["x-request-id"] == rid
    (gw,) = access["gateway"].parsed(rid)
    (core,) = access["core"].parsed(rid)
    assert (gw["method"], gw["path"], gw["status"]) == ("GET", "/api/v1/runs", "200")
    assert (core["method"], core["path"], core["status"]) == ("GET", "/core/v1/console/runs", "200")
    assert float(gw["ms"]) >= float(core["ms"]) >= 0.0  # the gateway's request includes the relayed core call
    # a relayed error keeps the linkage: the core's 404 and the gateway's relayed 404 carry the same id
    rid2 = "dd-a60-linkage-0002"
    missing = client.get("/api/v1/runs/run-doesnotexist", headers={**auth(), "X-Request-ID": rid2})
    assert missing.status_code == 404 and missing.json()["request_id"] == rid2
    assert [x["status"] for x in access["gateway"].parsed(rid2)] == ["404"]
    assert [x["status"] for x in access["core"].parsed(rid2)] == ["404"]


def test_secrets_in_the_query_string_are_redacted_and_control_characters_escaped(core_client, access, capsys):
    secret = "querysecret-DD-A60-not-for-logs"
    assert core_client.get(f"/core/v1/console/health?token={secret}&x=1").status_code == 401
    assert core_client.get(f"/core/v1/console/runs/x/artifacts/a?exp=1799999999&sig={secret}").status_code in (401, 403, 404)
    assert core_client.get("/core/v1/console/a%0Ab%0D%20c", headers={"X-Request-ID": "dd-a60-escape"}).status_code in (
        401, 404)
    lines = access["core"].lines
    assert lines and all(secret not in line and "\n" not in line and "\r" not in line for line in lines), lines
    paths = [p["path"] for p in access["core"].parsed()]
    assert "/core/v1/console/health?token=***&x=1" in paths, paths
    assert any(p.endswith("?exp=***&sig=***") for p in paths), paths
    (escaped,) = access["core"].parsed("dd-a60-escape")
    assert escaped["path"] == "/core/v1/console/a%0Ab%0D%20c"
    err = capsys.readouterr().err  # the access logger's own handler writes to the process stderr (core.log)
    assert "mbnas_core.access method=GET path=/core/v1/console/health?token=***&x=1 status=401" in err
    assert secret not in err


def test_the_console_entry_point_runs_uvicorn_without_its_own_access_line(monkeypatch):
    import uvicorn

    from mbnas_core.api import app as core_mod

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    assert core_mod.main([]) == 0
    assert seen["access_log"] is False and seen["factory"] is True
