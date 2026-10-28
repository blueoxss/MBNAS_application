"""DD-A75 F1b (fix round 7, C02 addendum D9), route level: spaced and unicode runner paths in a validation-ON row's
raw_metrics leave NO directory-name fragment in any public gateway response (snapshot, candidate detail, SSE replay,
run export) - a non-circular detector (planted fragments searched literally and as JSON ``\\u`` escapes in the raw
response bodies), not the ``_ABS_PATH`` regex under test.

The projection-level regression with the controls is ``tests/owner/projection/test_dd_a75b_raw_metrics_path_values.py``.
Here a run is created through the gateway, the row is stored with ``engine.put_row`` (which also emits the
``candidate.updated`` event carrying it) and the bodies are read through the gateway relay; the SSE replay over a real
uvicorn server. Positive control: the stored row and the stored event hold every fragment. The run's own protocol paths
live under pytest's tmp roots and contain none of the fragments, so the whole body is searched. SYNTHETIC; no GPU.
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

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx

RUN_DIR = "runtime/runner/runs/runs/train-x"
RECIPE_PATHS = {"data": f"/srv/mbnas owner r2c/{RUN_DIR}/data.yaml",
                "model": f"/home/김철수/mbnas/{RUN_DIR}/model.yaml",
                "project": f"  /tmp/mbnas sandbox/f1b 샌드박스 root/{RUN_DIR}",
                "save_dir": f"/home/mbnas user/김철수/{RUN_DIR}/TRAIN"}
FRAGMENTS = ("owner r2c", "김철수", "train-x", "f1b 샌드박스 root", "샌드박스", "작업", "mbnas user", "mbnas sandbox")
KEPT = {"cfg_name": "yolo11n.yaml", "precision": "FP16", "search_space": "mbnas-mixed-space/3",
        "report_url": "/api/v1/runs/run-feedfacecafe/artifacts/art-feedfacecafe",
        "precision_mode": "torch.autocast(float16) over FP32 weights", "note": "trained 1 epoch on cpu"}


def fragment_hits(*texts: str) -> list[str]:
    return [f for f in FRAGMENTS if any(f in t or json.dumps(f)[1:-1] in t for t in texts)]


def _raw_metrics(doc: Any) -> list[dict[str, Any]]:
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
    """F1b assertions on one public body; returns how many raw_metrics carried the planted recipe."""
    assert fragment_hits(text, json.dumps(doc, ensure_ascii=False)) == [], (where, fragment_hits(text))
    planted = [r for r in _raw_metrics(doc) if isinstance(r.get("resolved_recipe"), dict)
               and r["resolved_recipe"].get("note") == KEPT["note"]]
    for raw in planted:
        rr = raw["resolved_recipe"]
        assert {k: rr.get(k) for k in RECIPE_PATHS} == {k: "<path>" for k in RECIPE_PATHS}, (where, rr)
        assert {k: rr.get(k) for k in KEPT} == KEPT, (where, rr)
        assert rr["native_housekeeping"] == {"removed": ["<path>", "weights/best.pt"],
                                             "cache": {"labels": "<path>", "count": 2}, "workdir": "<path>"}, where
    return len(planted)


@pytest.fixture
def seeded(client, protocol, api_settings) -> tuple[str, str]:
    from mbnas_core.orchestration import engine

    run_id, _ = harness.validate_and_run(client, protocol, key="dd-a75b-f1b")
    conn = connect(api_settings, init=False)
    try:
        with tx(conn):
            ctx = engine.context(conn, run_id)
            cand = next(c for c in repo.list_candidates(conn, run_id) if c["arm"] == "Baseline")
            recipe = {**RECIPE_PATHS, **KEPT, "imgsz": 64, "amp": False, "native_housekeeping": {
                "removed": [f"/srv/mbnas owner r2c/{RUN_DIR}/weights/last.pt (removed after epoch 1)", "weights/best.pt"],
                "cache": {"labels": f"/tmp/mbnas sandbox/f1b 샌드박스 root/{RUN_DIR}/labels.cache stale", "count": 2},
                "workdir": f"/home/mbnas user/김철수 작업/{RUN_DIR}"}}
            raw = {"validation_enabled": True, "device_used": "cpu", "evaluation_status": "NOT_EVALUATED",
                   "reason_code": "EVALUATION_REQUIRES_CUDA", "eval_split": "search_val", "epochs": 1,
                   "resolved_recipe": recipe}
            row = engine.base_row(ctx, cand, ctx.baseline_stages()[0], "SEARCH_COMPLETE", epochs=1,
                                  performance_status="NOT_EVALUATED", reason_code="EVALUATION_REQUIRES_CUDA",
                                  latency_source="pending", evaluation_hash="pending", split_hash=None, raw_metrics=raw)
            engine.put_row(conn, ctx, row)  # stores the row and emits candidate.updated with it
    finally:
        conn.close()
    return run_id, cand["id"]


def test_positive_control_stored_row_and_event_hold_every_fragment(seeded, api_settings) -> None:
    run_id, cid = seeded
    conn = connect(api_settings, init=False)
    try:
        rows = [e["row"] for e in repo.list_evaluations(conn, run_id, candidate_id=cid)]
        events = repo.get_events_after(conn, run_id, 0, 10_000)
    finally:
        conn.close()
    stored = [r for r in rows if (r.get("raw_metrics") or {}).get("resolved_recipe")]
    assert stored and fragment_hits(json.dumps(stored, ensure_ascii=False)) == list(FRAGMENTS)
    carried = [e for e in events if e["type"] == "candidate.updated" and (e["payload"].get("raw_metrics") or {}).get(
        "resolved_recipe")]
    assert carried and fragment_hits(json.dumps(carried, ensure_ascii=False)) == list(FRAGMENTS)


def test_snapshot_and_candidate_detail_through_the_gateway(client, seeded) -> None:
    run_id, cid = seeded
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert snap.status_code == 200, snap.text
    assert _check("snapshot", snap.json(), snap.content.decode("utf-8")) >= 2  # candidates[] + events[].payload
    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=auth())
    assert detail.status_code == 200, detail.text
    assert _check("candidate detail", detail.json(), detail.content.decode("utf-8")) == 1


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
    events = [json.loads(x[len("data: "):]) for x in lines if x.startswith("data: ")]
    assert _check("SSE replay", events, "\n".join(lines)) >= 1


def test_export_through_the_gateway(client, seeded) -> None:
    run_id, _ = seeded
    r = client.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert r.status_code == 200, r.text[:400]
    carried = 0
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in zf.namelist():
            data = zf.read(name).decode("utf-8", "replace")
            assert fragment_hits(data) == [], (name, fragment_hits(data))  # every member, JSON or not
            if name.endswith(".json"):
                carried += _check(f"export {name}", json.loads(data), data)
            elif name.endswith(".jsonl"):
                carried += _check(f"export {name}", [json.loads(x) for x in data.splitlines() if x.strip()], data)
    assert carried >= 2, "the export rows and events carry the planted recipe"
