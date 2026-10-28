"""SSE over a real uvicorn server (in a thread): replay, cursor precedence, heartbeat, live wakeups, cross-process
polling, snapshot.required on replay gaps. Synthetic data; no GPU."""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import harness
import httpx
import pytest
import uvicorn
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx


@pytest.fixture
def live(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    harness.wait_for(lambda: server.started, timeout=10)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def read_sse(url: str, headers: dict[str, str], until, timeout: float = 10.0) -> tuple[list[dict[str, Any]], httpx.Response]:
    """Collect SSE frames until ``until(frames)`` is true (or fail after ``timeout``)."""
    frames: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=timeout) as c, c.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 200:
            resp.read()
            return frames, resp
        for line in resp.iter_lines():
            if line == "":
                if cur:
                    frames.append(cur)
                    cur = {}
                    if until(frames):
                        return frames, resp
            elif line.startswith(":"):
                frames.append({"comment": line[1:].strip()})
                if until(frames):
                    return frames, resp
            else:
                key, _, value = line.partition(":")
                cur[key] = value[1:] if value.startswith(" ") else value
            if time.monotonic() > deadline:
                break
    raise AssertionError(f"SSE condition not met; frames={frames[-5:]}")


def _events(frames):
    return [dict(f, data=json.loads(f["data"])) for f in frames if "event" in f]


@pytest.fixture
def run_id(client, protocol):
    rid, _ = harness.validate_and_run(client, protocol)
    return rid


def _last_seq(settings, rid):
    conn = connect(settings, init=False)
    try:
        return repo.get_run(conn, rid)["last_seq"]
    finally:
        conn.close()


def test_replay_headers_and_contract(live, run_id, api_settings):
    last = _last_seq(api_settings, run_id)
    frames, resp = read_sse(f"{live}/api/v1/runs/{run_id}/events", auth(),
                            lambda fs: len(_events(fs)) >= last)
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache" and resp.headers["x-accel-buffering"] == "no"
    events = _events(frames)
    assert [int(e["id"]) for e in events] == list(range(1, last + 1))
    for e in events:
        assert e["event"] == e["data"]["type"] and contract_errors("Event", e["data"]) == []


def test_cursor_after_seq_and_last_event_id(live, run_id, api_settings):
    last = _last_seq(api_settings, run_id)
    assert last >= 3
    frames, _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq=2", auth(),
                         lambda fs: len(_events(fs)) >= last - 2)
    assert int(_events(frames)[0]["id"]) == 3
    frames, _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq=0", {**auth(), "Last-Event-ID": str(last - 1)},
                         lambda fs: len(_events(fs)) >= 1)
    assert int(_events(frames)[0]["id"]) == last  # Last-Event-ID wins over after_seq (reconnect)


def test_heartbeat_comment(live, run_id, api_settings):
    last = _last_seq(api_settings, run_id)
    frames, _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last}", auth(),
                         lambda fs: any(f.get("comment") == "heartbeat" for f in fs), timeout=5)
    assert not _events(frames)


def test_live_event_after_api_transition(live, run_id, api_settings):
    last = _last_seq(api_settings, run_id)
    got: dict[str, Any] = {}

    def listen():
        got["frames"], _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last}", auth(),
                                    lambda fs: any(e["data"]["type"] == "run.updated" for e in _events(fs)))

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    r = httpx.post(f"{live}/api/v1/runs/{run_id}/pause", headers=auth(), json={"reason": "test"})
    assert r.status_code == 200, r.text
    t.join(timeout=10)
    assert time.monotonic() - t0 < 5
    statuses = [e["data"]["payload"]["status"] for e in _events(got["frames"]) if e["data"]["type"] == "run.updated"]
    assert statuses[0] in ("PAUSE_REQUESTED", "PAUSED")
    assert all(int(e["id"]) > last for e in _events(got["frames"]))


def test_event_committed_by_other_process_is_polled(live, run_id, api_settings):
    last = _last_seq(api_settings, run_id)
    got: dict[str, Any] = {}

    def listen():
        got["frames"], _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last}", auth(),
                                    lambda fs: any(e["data"]["type"] == "log" for e in _events(fs)))

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.3)
    conn = connect(api_settings, init=False)  # a separate writer, like the worker process: no in-process notify
    with tx(conn):
        repo.append_event(conn, run_id, "log", {"message": "from another process"})
    conn.close()
    t.join(timeout=10)
    assert _events(got["frames"])[0]["data"]["payload"]["message"] == "from another process"


def test_snapshot_required_on_gap_and_ahead_cursor(live, run_id, api_settings):
    conn = connect(api_settings, init=False)
    with tx(conn):
        for i in range(60):  # max_replay_events=50 in the test settings
            repo.append_event(conn, run_id, "log", {"message": f"filler {i}"})
    last = repo.get_run(conn, run_id)["last_seq"]
    conn.close()
    frames, _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq=1", auth(),
                         lambda fs: any(f.get("comment") == "heartbeat" for f in fs))
    events = _events(frames)
    assert events[0]["event"] == "snapshot.required" and "id" not in events[0]
    assert events[0]["data"]["payload"]["reason"] == "CURSOR_OLDER_THAN_REPLAY_RETENTION"
    assert events[0]["data"]["payload"]["last_seq"] == last and len(events) == 1  # continues live from last_seq
    frames, _ = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last + 10}", auth(),
                         lambda fs: len(_events(fs)) >= 1)
    assert _events(frames)[0]["data"]["payload"]["reason"] == "CURSOR_AHEAD_OF_LAST_SEQ"


def test_sse_errors(live, run_id):
    _, resp = read_sse(f"{live}/api/v1/runs/run-missing/events", auth(), lambda fs: True)
    assert resp.status_code == 404 and resp.json()["code"] == "RUN_NOT_FOUND"
    _, resp = read_sse(f"{live}/api/v1/runs/{run_id}/events", {}, lambda fs: True)
    assert resp.status_code == 401
