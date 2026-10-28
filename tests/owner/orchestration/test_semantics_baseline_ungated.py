"""Baseline evaluations are never gated (fix round 1, C03-D5; part of the test_semantics family).

``engine.commit_evaluation`` evaluates the gate for the search arms only, and ``engine._advance`` moves a Baseline to its
next stage without any gate decision; until now only the strict T2-7 comparison with the original package showed it
(C03 EVAL_1 D5). Here a gate that rejects every search candidate (``score_ge=0.99``) is the control: the baseline still
trains and is evaluated at every stage, each Baseline row has ``gate`` None (stored and served) and a fitness value for
display, and the candidate ends ``EVALUATED`` - never a promotion/gate state. SYNTHETIC stub handlers, in-process owner
worker (same plumbing as ``test_semantics.py``; no GPU).
"""
from __future__ import annotations

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect

GATE_STATES = {"PROMOTION_ELIGIBLE", "REJECTED_BY_GATE", "SEARCH_COMPLETE", "SEARCH_RUNG_FINAL"}


@pytest.fixture
def conn(api_settings):
    c = connect(api_settings, init=False)
    yield c
    c.close()


def test_baseline_rows_carry_no_gate_and_advance_through_every_stage_under_a_rejecting_gate(
        client, make_dataset, tmp_roots, inline_worker, conn) -> None:
    ds = make_dataset("bg", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1,
                                   baselines=("yolo11n",), score_ge=0.99)
    run_id, _ = harness.validate_and_run(client, proto)
    harness.drain(inline_worker)
    assert repo.get_run(conn, run_id)["status"] == "COMPLETED"
    cands = repo.list_candidates(conn, run_id)
    search = [c for c in cands if c["arm"] == "GA"]
    baseline = [c for c in cands if c["arm"] == "Baseline"]
    # control: the same gate rejects every search candidate at L0 and nothing of the search arm reaches L1
    assert search and {c["candidate_status"] for c in search} == {"REJECTED_BY_GATE"}
    assert len(baseline) == 1
    rows = [e for e in repo.list_evaluations(conn, run_id) if e["row"]["arm"] == "Baseline"]
    stages = [r["id"] for r in proto["rungs"]]
    assert len(stages) == 2 and sorted(e["row"]["rung"] for e in rows) == sorted(stages)  # every stage, despite the gate
    for e in rows:
        assert e["status"] == "EVALUATED" and e["candidate_id"] == baseline[0]["id"]
        assert e["row"]["gate"] is None, e["row"]["gate"]
        assert e["row"]["fitness"] is not None  # a displayed score, never a gate decision
    final = repo.get_candidate(conn, baseline[0]["id"])
    assert final["candidate_status"] == "EVALUATED" and final["candidate_status"] not in GATE_STATES
    assert all(r["billed_arm"] is None for r in repo.list_costs(conn, run_id) if r["arm"] == "Baseline")
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert snap.status_code == 200, snap.text
    served = [r for r in snap.json()["candidates"] if r["arm"] == "Baseline"]
    assert len(served) == len(stages) and all(r.get("gate") is None for r in served), served  # null: omitted
    assert all(r["status"] not in GATE_STATES for r in served)
    # control on the served shape: the search rows of the same snapshot do carry their gate decision
    gated = [r for r in snap.json()["candidates"] if r["arm"] == "GA"]
    assert gated and all((r.get("gate") or {}).get("reason_code") == "REJECTED_BY_GATE" for r in gated), gated
