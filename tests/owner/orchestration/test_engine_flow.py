"""Orchestration state machine over the API + in-process worker with SYNTHETIC stub handlers (no GPU)."""
from __future__ import annotations

import harness
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect


def _snapshot(client, run_id):
    r = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert contract_errors("Snapshot", body) == []
    return body


def test_full_run_completes_with_rungs_generations_and_costs(client, protocol, inline_worker, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    executed = harness.drain(inline_worker)
    snap = _snapshot(client, run_id)
    assert snap["run"]["status"] == "COMPLETED", snap["run"]
    # 1 baseline x 2 stages + 2 arms x (2 proposals + 2 gens x 2 cands x 2 rungs)
    assert executed == 2 + 2 * (2 + 2 * 2 * 2)
    evaluated = [c for c in snap["candidates"] if c["status"] == "EVALUATED"]
    assert len(evaluated) == 2 + 2 * 2 * 2 * 2
    for row in evaluated:
        assert row["immutable"] is True
        assert row["fitness_weights"] == next(r for r in protocol["rungs"] if r["id"] == row["rung"])["fitness_weights"]
        assert {a["kind"] for a in row["artifacts"]} == {"gt", "tp", "fn", "fp"}
    gens = snap["generations"]
    assert len(gens) == 2 * 2 * 2  # arm x generation x rung
    for g in gens:
        assert g["complete"] and g["planning_closed"] and g["pending_count"] == 0
        assert g["evaluated_count"] + g["failed_count"] + g["not_admitted_count"] + g["pending_count"] == g["planned_count"]
        assert g["representative_candidate_id"]
    costs = snap["run"]["costs"]
    assert costs["actual_gpu_seconds"] > 0 and costs["ga_billed_gpu_seconds"] > 0
    assert costs["random_billed_gpu_seconds"] > 0 and costs["baseline_gpu_seconds"] > 0
    assert "final_gpu_seconds" not in costs  # nothing measured -> absent, never 0
    conn = connect(api_settings, init=False)
    try:
        types = [e["type"] for e in repo.get_events_after(conn, run_id, 0, 10_000)]
    finally:
        conn.close()
    assert types.count("generation.completed") == len(gens)
    assert "candidate.evaluated" in types and types[0] == "run.updated"
