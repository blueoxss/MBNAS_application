"""DD-A75 F1 (fix round 7), route level: the runner paths of a validation-ON row's ``raw_metrics.resolved_recipe``
never reach a public response of the gateway (snapshot, candidate detail, SSE replay, run export).

The projection-level regression with the full fixture and the negative controls is
``tests/owner/projection/test_dd_a75_raw_metrics_paths.py``; here the row goes through the real writer and routes: a
run is created through the gateway, a validation-ON NOT_EVALUATED row (CPU runner shape, DD-A62 (a)) is stored for its
baseline candidate with ``engine.put_row`` - which also emits the ``candidate.updated`` event carrying the row - and
the public bodies are read through the gateway relay (``/api/v1`` -> ``/core/v1/console``; the SSE replay over a real
uvicorn server). Positive control: the stored row and the stored event still hold the paths (stored rows are never
rewritten). The run's own protocol paths (the client-provided ``resolved_protocol.console.paths`` /
``resolution.paths.*.requested`` echo) are outside this check: ``_ABS_PATH`` is applied to the raw_metrics subtrees,
and the planted runner paths are searched verbatim in the whole body. SYNTHETIC; no GPU.
"""
from __future__ import annotations

import io
import json
import socket
import threading
import time
import zipfile
from typing import Any

import harness
import httpx
import pytest
import uvicorn
from harness import auth

from mbnas_core.console import projection
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx

RUNNER_PATHS = {"data": "/home/mbnas-owner/datasets/pilot_smoke/data.yaml",
                "model": "/work/runs/run-feedfacecafe/model-feedfacecafe/model.yaml",
                "project": "/work/runs/run-feedfacecafe/model-feedfacecafe/ultralytics",
                "save_dir": "/work/runs/run-feedfacecafe/model-feedfacecafe/ultralytics/TRAIN"}
KEPT = {"cfg_name": "yolo11n.yaml", "precision": "FP16", "search_space": "mbnas-mixed-space/3",
        "report_url": "/api/v1/runs/run-feedfacecafe/artifacts/art-feedfacecafe"}


def _leaks(text: str) -> list[str]:
    return [m.group(0) for m in projection._ABS_PATH.finditer(text)
            if not m.group(0).startswith(projection._PUBLIC_PATH_PREFIXES)]


def _raw_metrics(doc: Any) -> list[dict[str, Any]]:
    """Every ``raw_metrics`` object of ``doc`` (any depth)."""
    out: list[dict[str, Any]] = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "raw_metrics" and isinstance(v, dict):
                out.append(v)
            out.extend(_raw_metrics(v))
    elif isinstance(doc, list):
        for v in doc:
            out.extend(_raw_metrics(v))
    return out


def _check(where: str, doc: Any, text: str) -> int:
    """F1 assertions on one public body; returns how many raw_metrics carried the planted recipe."""
    planted = [r for r in _raw_metrics(doc) if isinstance(r.get("resolved_recipe"), dict)
               and set(RUNNER_PATHS) <= set(r["resolved_recipe"]) and r["resolved_recipe"].get("cfg_name") == "yolo11n.yaml"]
    for raw in _raw_metrics(doc):
        assert _leaks(json.dumps(raw)) == [], (where, _leaks(json.dumps(raw)))
    for raw in planted:
        rr = raw["resolved_recipe"]
        assert {k: rr[k] for k in RUNNER_PATHS} == {k: "<path>" for k in RUNNER_PATHS}, (where, rr)
        assert {k: rr[k] for k in KEPT} == KEPT and rr["imgsz"] == 64 and rr["amp"] is False, (where, rr)
        assert raw["validation_enabled"] is True and raw["device_used"] == "cpu", (where, raw)
    assert [p for p in RUNNER_PATHS.values() if p in text] == [], where
    return len(planted)


@pytest.fixture
def seeded(client, protocol, api_settings) -> tuple[str, str]:
    from mbnas_core.orchestration import engine

    run_id, _ = harness.validate_and_run(client, protocol, key="dd-a75-f1")
    conn = connect(api_settings, init=False)
    try:
        with tx(conn):
            ctx = engine.context(conn, run_id)
            cand = next(c for c in repo.list_candidates(conn, run_id) if c["arm"] == "Baseline")
            raw = {"validation_enabled": True, "device_used": "cpu", "evaluation_status": "NOT_EVALUATED",
                   "reason_code": "EVALUATION_REQUIRES_CUDA", "eval_split": "search_val", "epochs": 1,
                   "resolved_recipe": {**RUNNER_PATHS, **KEPT, "imgsz": 64, "amp": False, "cfg": None}}
            row = engine.base_row(ctx, cand, ctx.baseline_stages()[0], "SEARCH_COMPLETE", epochs=1,
                                  performance_status="NOT_EVALUATED", reason_code="EVALUATION_REQUIRES_CUDA",
                                  latency_source="pending", evaluation_hash="pending", split_hash=None, raw_metrics=raw)
            engine.put_row(conn, ctx, row)  # stores the row and emits candidate.updated with it
    finally:
        conn.close()
    return run_id, cand["id"]


def test_positive_control_stored_row_and_event_hold_the_runner_paths(seeded, api_settings) -> None:
    run_id, cid = seeded
    conn = connect(api_settings, init=False)
    try:
        rows = [e["row"] for e in repo.list_evaluations(conn, run_id, candidate_id=cid)]
        events = repo.get_events_after(conn, run_id, 0, 10_000)
    finally:
        conn.close()
    stored = [r["raw_metrics"]["resolved_recipe"] for r in rows if (r.get("raw_metrics") or {}).get("resolved_recipe")]
    assert stored and all({k: rr[k] for k in RUNNER_PATHS} == RUNNER_PATHS for rr in stored)
    carried = [e for e in events if e["type"] == "candidate.updated"
               and ((e["payload"].get("raw_metrics") or {}).get("resolved_recipe") or {}).get("data") == RUNNER_PATHS["data"]]
    assert carried, "the stored candidate.updated event carries the unscrubbed row (the projection must clean it)"


def test_snapshot_and_candidate_detail_through_the_gateway(client, seeded) -> None:
    run_id, cid = seeded
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert snap.status_code == 200, snap.text  # contract_body validated the projected body (else 500)
    assert _check("snapshot", snap.json(), snap.text) >= 2  # candidates[] and events[].payload
    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth())
    assert detail.status_code == 200, detail.text
    assert _check("candidate detail", detail.json(), detail.text) == 1


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


def test_sse_replay_through_the_gateway(live, client, seeded, api_settings) -> None:
    run_id, _ = seeded
    conn = connect(api_settings, init=False)
    try:
        last_seq = int(repo.get_run(conn, run_id)["last_seq"])
    finally:
        conn.close()
    lines, seen = [], 0
    deadline = time.monotonic() + 20
    with httpx.Client(timeout=20) as c, c.stream("GET", f"{live}/api/v1/runs/{run_id}/events?after_seq=0",
                                                 headers=auth()) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            lines.append(line)
            if line.startswith("id: "):
                seen = max(seen, int(line[4:]))
            if (line == "" and seen >= last_seq) or time.monotonic() > deadline:
                break
    assert seen >= last_seq > 0, (seen, last_seq)
    text = "\n".join(lines)
    events = [json.loads(x[len("data: "):]) for x in lines if x.startswith("data: ")]
    assert _check("SSE replay", events, text) >= 1


def test_export_through_the_gateway(client, seeded) -> None:
    run_id, _ = seeded
    r = client.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert r.status_code == 200, r.text[:400]
    carried = 0
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in zf.namelist():
            data = zf.read(name).decode("utf-8", "replace")
            if name.endswith(".json"):
                carried += _check(f"export {name}", json.loads(data), data)
            elif name.endswith(".jsonl"):
                carried += _check(f"export {name}", [json.loads(x) for x in data.splitlines() if x.strip()], data)
    assert carried >= 2, "the export rows and events carry the planted recipe"
