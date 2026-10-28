"""QA: server-side SSE replay guarantees (duplicate / out-of-order / gap handling) over a real uvicorn server.

The client half (vitest ``sse.test.ts`` + ``qa_sse_client.test.ts``) ignores duplicates, other-run events and refetches a
snapshot on a gap; these tests prove the server never produces any of them for a well-behaved cursor.
"""
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


def read_sse(url: str, headers: dict[str, str], until, timeout: float = 10.0) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=timeout) as c, c.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            if line == "":
                if cur:
                    frames.append(cur)
                    cur = {}
            elif line.startswith(":"):
                frames.append({"comment": line[1:].strip()})
            else:
                key, _, value = line.partition(":")
                cur[key] = value[1:] if value.startswith(" ") else value
            if until(frames) or time.monotonic() > deadline:
                break
    return frames


def events(frames):
    return [dict(f, data=json.loads(f["data"])) for f in frames if "event" in f]


def seqs(frames):
    return [int(e["id"]) for e in events(frames) if "id" in e]


def last_seq(settings, run_id):
    conn = connect(settings, init=False)
    try:
        return repo.get_run(conn, run_id)["last_seq"]
    finally:
        conn.close()


@pytest.fixture
def run_id(client, protocol, inline_worker):
    rid, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    return rid


def test_full_replay_is_contiguous_monotonic_and_contract_valid(live, run_id, api_settings):
    last = last_seq(api_settings, run_id)
    assert last > 30
    frames = read_sse(f"{live}/api/v1/runs/{run_id}/events", auth(), lambda fs: len(seqs(fs)) >= last)
    got = seqs(frames)
    assert got == list(range(1, last + 1))  # no gap, no duplicate, no reordering
    assert all(e["data"]["run_id"] == run_id and e["data"]["seq"] == int(e["id"]) and e["event"] == e["data"]["type"] for e in events(frames))
    assert all(contract_errors("Event", e["data"]) == [] for e in events(frames))


@pytest.mark.parametrize("cut", [1, 7, 20])
def test_reconnect_with_last_event_id_continues_without_overlap_or_loss(live, run_id, api_settings, cut):
    last = last_seq(api_settings, run_id)
    first = read_sse(f"{live}/api/v1/runs/{run_id}/events", auth(), lambda fs: len(seqs(fs)) >= cut)
    seen = seqs(first)[:cut]
    rest = read_sse(f"{live}/api/v1/runs/{run_id}/events", {**auth(), "Last-Event-ID": str(seen[-1])},
                    lambda fs: len(seqs(fs)) >= last - cut)
    assert seen + seqs(rest) == list(range(1, last + 1))
    via_query = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={seen[-1]}", auth(), lambda fs: len(seqs(fs)) >= last - cut)
    assert seqs(via_query) == seqs(rest)


@pytest.mark.parametrize("bad", ["abc", "-1", "1e3", "", "0x10", b"\xb2", b"\xb9\xb2"])
def test_malformed_last_event_id_falls_back_to_after_seq_and_never_errors(live, run_id, api_settings, bad):
    last = last_seq(api_settings, run_id)
    frames = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last - 2}", {**auth(), "Last-Event-ID": bad},
                      lambda fs: len(seqs(fs)) >= 2, timeout=6)
    assert seqs(frames)[:2] == [last - 1, last]  # the malformed header is ignored; after_seq decides


@pytest.mark.parametrize("huge", ["9" * 25, "9" * 5000])
def test_absurd_last_event_id_is_a_cursor_ahead_not_a_500(live, run_id, api_settings, huge):
    frames = read_sse(f"{live}/api/v1/runs/{run_id}/events", {**auth(), "Last-Event-ID": huge},
                      lambda fs: bool(events(fs)), timeout=6)
    ev = events(frames)[0]
    assert ev["event"] == "snapshot.required" and ev["data"]["payload"]["reason"] == "CURSOR_AHEAD_OF_LAST_SEQ"


def test_absurd_after_seq_query_is_also_a_cursor_ahead(live, run_id):
    frames = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={'9' * 40}", auth(), lambda fs: bool(events(fs)), timeout=6)
    assert events(frames)[0]["data"]["payload"]["reason"] == "CURSOR_AHEAD_OF_LAST_SEQ"


def test_cursor_ahead_gets_snapshot_required_then_exactly_the_next_event(live, run_id, api_settings):
    last = last_seq(api_settings, run_id)
    got: dict[str, Any] = {}

    def listen():
        got["frames"] = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last + 25}", auth(),
                                 lambda fs: any(e["event"] == "log" for e in events(fs)))

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.4)
    conn = connect(api_settings, init=False)
    with tx(conn):
        repo.append_event(conn, run_id, "log", {"message": "after the ahead cursor"})
    conn.close()
    t.join(timeout=10)
    ev = events(got["frames"])
    assert ev[0]["event"] == "snapshot.required" and "id" not in ev[0]  # does not advance the client cursor
    assert ev[0]["data"]["payload"]["reason"] == "CURSOR_AHEAD_OF_LAST_SEQ"
    assert [int(e["id"]) for e in ev if "id" in e] == [last + 1]  # no duplicate of old events, no skipped seq


def test_three_concurrent_writers_are_streamed_gapless_in_order(live, run_id, api_settings):
    last = last_seq(api_settings, run_id)
    per_writer, writers = 15, 3
    total = last + per_writer * writers
    got: dict[str, Any] = {}

    def listen():
        got["frames"] = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last}", auth(),
                                 lambda fs: len(seqs(fs)) >= per_writer * writers, timeout=20)

    def write(tag: int):
        conn = connect(api_settings, init=False)  # independent connection = independent process semantics
        for i in range(per_writer):
            with tx(conn):
                repo.append_event(conn, run_id, "log", {"message": f"w{tag}-{i}"})
            time.sleep(0.003)
        conn.close()

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.3)
    ws = [threading.Thread(target=write, args=(k,)) for k in range(writers)]
    [w.start() for w in ws]
    [w.join() for w in ws]
    t.join(timeout=25)
    got_seqs = seqs(got["frames"])
    assert got_seqs == list(range(last + 1, total + 1))
    messages = [e["data"]["payload"]["message"] for e in events(got["frames"])]
    for k in range(writers):  # per-writer order is preserved inside the global order
        mine = [int(m.split("-")[1]) for m in messages if m.startswith(f"w{k}-")]
        assert mine == list(range(per_writer))
    assert last_seq(api_settings, run_id) == total


def test_two_subscribers_receive_identical_streams(live, run_id, api_settings):
    last = last_seq(api_settings, run_id)
    out: list[Any] = [None, None]

    def listen(i: int):
        out[i] = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={last - 5}", auth(), lambda fs: len(seqs(fs)) >= 8, timeout=15)

    ts = [threading.Thread(target=listen, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    time.sleep(0.4)
    conn = connect(api_settings, init=False)
    with tx(conn):
        for i in range(3):
            repo.append_event(conn, run_id, "log", {"message": f"shared {i}"})
    conn.close()
    [t.join(timeout=20) for t in ts]
    assert seqs(out[0]) == seqs(out[1]) == list(range(last - 4, last + 4))
    assert [e["data"] for e in events(out[0])] == [e["data"] for e in events(out[1])]


def test_streams_never_mix_runs(client, live, protocol, inline_worker, make_dataset, tmp_roots, api_settings):
    a, _ = harness.validate_and_run(client, protocol, key="run-a")
    ds2 = make_dataset("other-ds", counts={"train": 4, "val": 2, "test": 2}, seed0=500)
    b, _ = harness.validate_and_run(client, harness.small_protocol(ds2, str(tmp_roots["runs"] / "out2")), key="run-b")
    harness.drain(inline_worker)
    for rid in (a, b):
        frames = read_sse(f"{live}/api/v1/runs/{rid}/events", auth(), lambda fs: len(seqs(fs)) >= last_seq(api_settings, rid))
        assert {e["data"]["run_id"] for e in events(frames)} == {rid}
        assert seqs(frames) == list(range(1, last_seq(api_settings, rid) + 1))


def test_snapshot_and_event_cursor_agree(client, live, run_id, api_settings):
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    assert snap["last_seq"] == last_seq(api_settings, run_id)
    conn = connect(api_settings, init=False)
    with tx(conn):
        repo.append_event(conn, run_id, "log", {"message": "after the snapshot"})
    conn.close()
    frames = read_sse(f"{live}/api/v1/runs/{run_id}/events?after_seq={snap['last_seq']}", auth(), lambda fs: len(seqs(fs)) >= 1)
    assert seqs(frames) == [snap["last_seq"] + 1]  # nothing baked into the snapshot is delivered a second time
