"""S11-F3 (API level): a real uvicorn server serving create_app() never writes a URL/header secret to its logs."""
from __future__ import annotations

import logging
import socket
import threading
import time

import httpx
import uvicorn

SECRET = "querysecret-DO-NOT-LOG-123"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_access_log_never_contains_query_or_header_secret(app, caplog):
    port = _free_port()
    cap = _Capture()
    loggers = [logging.getLogger(n) for n in ("uvicorn.access", "uvicorn.error")]
    for lg in loggers:
        lg.addHandler(cap)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=True, log_config=None,
                            lifespan="off")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        deadline = time.time() + 20
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        base = f"http://127.0.0.1:{port}/api/v1"
        with caplog.at_level(logging.INFO):
            with httpx.Client(timeout=10) as c:
                codes = [
                    c.get(f"{base}/health?token={SECRET}").status_code,
                    c.get(f"{base}/runs?access_token={SECRET}").status_code,
                    c.get(f"{base}/runs/run-x/artifacts/a1?exp=1799999999&sig={SECRET}").status_code,
                    c.get(f"{base}/runs", headers={"Authorization": f"Bearer {SECRET}"}).status_code,
                ]
            time.sleep(0.3)  # let the access-log lines flush
    finally:
        server.should_exit = True
        t.join(timeout=10)
        for lg in loggers:
            lg.removeHandler(cap)
    assert all(code in (401, 403, 404, 410) for code in codes[1:]), codes
    joined = "\n".join(cap.messages)
    assert "token=***" in joined and "access_token=***" in joined and "sig=***" in joined, joined
    assert SECRET not in joined
    server_records = [r for r in caplog.records if r.name.startswith("uvicorn")]  # httpx logs the client's own URL
    assert any(r.name == "uvicorn.access" for r in server_records)
    assert all(SECRET not in r.getMessage() for r in server_records)
