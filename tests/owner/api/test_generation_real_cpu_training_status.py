"""``training_status`` of a real evaluation-OFF run is ``UNTRAINED`` as served (fix round 1, C03-D3).

The contract enum ``GeneratedCandidate.training_status`` also admits ``TRAINED``, and until now no test asserted the
value itself on a real OFF snapshot (C03 EVAL_1 D3). Same real CPU path as ``test_generation_real_cpu.py``: the run is
created through the gateway relay, the real owner worker runs PROPOSE_GENERATION on CPU (real structure checks; no
mock checker, trainer or evaluator; no dataset, no GPU), and the snapshot and every candidate detail are read back.
"""
from __future__ import annotations

import json
import sys

import pytest

from harness import auth
from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.owner_worker.main import Worker, WorkerConfig
from mbnas_server.api.contract_check import contract_errors


@pytest.mark.owner_worker  # PROPOSE_GENERATION runs on the owner worker (DD-A4)
def test_real_off_run_serves_every_generated_candidate_as_untrained(client, api_settings, tmp_roots, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    monkeypatch.delenv("MBNAS_JOB_HANDLERS_MODULE", raising=False)
    api_settings.worker_python = sys.executable
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
                    "candidate_count": 2, "max_attempts": 6, "allow_duplicates": False, "arms": ["GA"]}}}
    v = client.post("/api/v1/protocols/validate", json={"protocol": p}, headers=auth())
    assert v.status_code == 200, v.text
    v = v.json()
    assert v["valid"], v
    created = client.post("/api/v1/runs", json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                               "validation_id": v["validation_id"],
                                               "idempotency_key": "real-generation-training-status"}, headers=auth())
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    worker = Worker(api_settings, WorkerConfig(cpu=True, once=True), worker_id="isolated-cpu-generator")
    try:
        assert worker.tick() is True
        assert worker.tick() is False
    finally:
        worker.conn.close()
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert snap.status_code == 200, snap.text
    snap = snap.json()
    assert snap["run"]["status"] == "COMPLETED", snap["run"]
    assert contract_errors("Snapshot", snap) == []
    assert snap["candidates"] == []  # an OFF run has no evaluation rows
    generated = snap["generated_candidates"]
    assert len(generated) == 2
    assert [c["training_status"] for c in generated] == ["UNTRAINED", "UNTRAINED"]
    assert all(c["evaluation_status"] == "NOT_EVALUATED" for c in generated)
    assert '"TRAINED"' not in json.dumps(generated)  # the other enum value occurs nowhere in the served rows
    for candidate in generated:
        response = client.get(f"/api/v1/runs/{run_id}/candidates/{candidate['candidate_id']}", headers=auth())
        assert response.status_code == 200, response.text
        detail = response.json()
        assert contract_errors("CandidateDetail", detail) == []
        assert detail["training_status"] == "UNTRAINED" and detail["evaluations"] == []
    conn = connect(api_settings, init=False)
    try:  # the served value matches what happened: nothing was trained or evaluated
        assert [j["kind"] for j in repo.list_jobs(conn, run_id)] == ["PROPOSE_GENERATION"]
        assert conn.execute("SELECT COUNT(*) FROM evaluations WHERE run_id=?", (run_id,)).fetchone()[0] == 0
    finally:
        conn.close()
