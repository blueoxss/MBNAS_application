"""DD-A62 (a) for the search arms: a CANDIDATE_TRAIN result whose evaluation payload says NOT_EVALUATED - what a runner
whose job device is not a CUDA device returns (DD-A60 (4), ``mbnas_runner.evaluation.handlers.not_evaluated_payload``) -
is committed without a measurement: reason EVALUATION_REQUIRES_CUDA, no metric, no fitness, no gate, latency pending,
the trained checkpoint kept. The candidate stays unevaluated: never promoted, never a GA parent, counted ``failed`` (not
``evaluated``) in its generation row, no ``candidate.evaluated`` event. Before DD-A62 the core committed that payload as
EVAL_FAILED with METRIC_MISSING:ap.

SYNTHETIC stub handlers over the WP-3 combined harness (gateway -> core, in-process owner worker): the GA arm's training
results are rewritten into the runner's NOT_EVALUATED payload, the IndependentRandom arm and the baseline keep their
synthetic measurement. No GPU.
"""
from __future__ import annotations

from typing import Any

import harness
import pytest
from harness import auth

from mbnas_contracts.schemas.jobs import JobKind
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine
from mbnas_server.api.contract_check import contract_errors

REASON = "EVALUATION_REQUIRES_CUDA"


def _not_evaluated(spec: Any, ev: dict[str, Any]) -> dict[str, Any]:
    """The runner's real NOT_EVALUATED payload for this stub snapshot (CPU job device, DD-A60 (4))."""
    from mbnas_runner.evaluation.handlers import latency_device_problem, not_evaluated_payload

    bench = spec.params["protocol"]["console"]["benchmark"]
    sha = ev["checkpoint_sha256"]
    return not_evaluated_payload(reason=latency_device_problem("cpu"), device="cpu", device_entry="cpu",
                                 precision="FP16", eval_batch=8, input_shape=[64, 64], benchmark=bench,
                                 split="search_val", epochs=int(spec.cumulative_target or 1),
                                 eval_ckpt=ev["checkpoint_path"], eval_sha256=sha, checkpoint_path=ev["checkpoint_path"],
                                 checkpoint_sha256=sha)


@pytest.fixture
def ga_not_evaluated(monkeypatch, stub):
    """CANDIDATE_TRAIN of the GA arm returns the NOT_EVALUATED payload (with ``device_used`` cpu, as the CPU daemon
    reports); every other job keeps the synthetic stub result."""
    import stub_handlers

    original = stub_handlers.HANDLERS["CANDIDATE_TRAIN"]

    def train(spec, emit, profile_default_s=None):
        out = original(spec, emit, profile_default_s)
        if spec.arm == "GA":
            out = {**out, "evaluation": _not_evaluated(spec, out["evaluation"]), "device_used": "cpu"}
        return out

    monkeypatch.setitem(stub_handlers.HANDLERS, "CANDIDATE_TRAIN", train)
    return train


def _rows(settings, run_id: str) -> list[dict[str, Any]]:
    conn = connect(settings, init=False)
    try:
        return [{**e["row"], "status": e["status"], "immutable": bool(e["immutable"])}
                for e in repo.list_evaluations(conn, run_id)]
    finally:
        conn.close()


def test_ga_not_evaluated_candidates_record_the_reason_and_are_never_promoted(client, protocol, inline_worker,
                                                                              api_settings, ga_not_evaluated):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    r = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth())
    assert r.status_code == 200, r.text
    snap = r.json()
    assert contract_errors("Snapshot", snap) == []  # the public projection of the new rows is contract-valid
    assert snap["run"]["status"] == "COMPLETED", snap["run"]

    rows = _rows(api_settings, run_id)
    ga = [row for row in rows if row["arm"] == "GA"]
    assert ga and {row["rung"] for row in ga} == {"L0"}, [(r_["candidate_id"], r_["rung"]) for r_ in ga]  # never promoted
    for row in ga:
        assert row["status"] == "SEARCH_COMPLETE" and row["candidate_status"] == "SEARCH_COMPLETE"
        assert row["performance_status"] == "NOT_EVALUATED" and row["reason_code"] == REASON
        assert [row[k] for k in ("ap", "recall", "latency_ms", "size_mb", "fitness")] == [None] * 5  # no fake metric
        assert row["gate"] is None and row["latency_source"] == "pending" and row["evaluation_hash"] == "pending"
        assert row["split_hash"] is None and row["immutable"] is False
        assert row["checkpoint_path"] and row["checkpoint_sha256"]  # the trained snapshot is kept
        raw = row["raw_metrics"]
        assert raw["evaluation_status"] == "NOT_EVALUATED" and raw["reason_code"] == REASON
        assert raw["device_used"] == "cpu" and "CUDA" in raw["not_evaluated_reason"]
        assert "reported" not in raw  # the payload carried no metric at all
    # the other arm and the baseline are measured as before (and the random arm is promoted to L1)
    rnd = [row for row in rows if row["arm"] == "IndependentRandom"]
    assert rnd and all(row["status"] == "EVALUATED" and row["fitness"] is not None for row in rnd)
    assert {row["rung"] for row in rnd} == {"L0", "L1"}
    assert all(row["status"] == "EVALUATED" for row in rows if row["arm"] == "Baseline")

    conn = connect(api_settings, init=False)
    try:
        ga_ids = {row["candidate_id"] for row in ga}
        for cid in ga_ids:
            cand = repo.get_candidate(conn, cid)
            assert cand["candidate_status"] == "SEARCH_COMPLETE"
            state = cand["audit"]["orchestration"]["rungs"]["L0"]
            assert state["state"] == "failed" and state["reason_code"] == REASON
            assert state["performance_status"] == "NOT_EVALUATED"
        jobs = repo.list_jobs(conn, run_id)
        assert not [j for j in jobs if j["candidate_id"] in ga_ids and j["rung"] != "L0"]  # no promotion job
        # generation 1 of the GA arm was proposed from an EMPTY parent pool: an unevaluated candidate is never a parent
        g1 = [j for j in jobs if j["kind"] == JobKind.PROPOSE_GENERATION.value and j["spec"].get("arm") == "GA"
              and (j["spec"].get("params") or {}).get("generation") == 1]
        assert len(g1) == 1 and g1[0]["spec"]["params"]["parent_pool"] == []
        gens = [g["row"] for g in repo.list_generations(conn, run_id) if g["row"]["arm"] == "GA"]
        assert gens and all(g["rung"] == "L0" or g["planned_count"] == 0 for g in gens)
        for g in (g for g in gens if g["rung"] == "L0"):
            assert g["complete"] and g["evaluated_count"] == 0 and g["failed_count"] == g["planned_count"] > 0
            assert g["representative_candidate_id"] is None
        events = repo.get_events_after(conn, run_id, 0, 100_000)
        evaluated = [e for e in events if e["type"] == "candidate.evaluated"]
        assert evaluated and not [e for e in evaluated if e["payload"].get("candidate_id") in ga_ids]
        logs = [e["payload"] for e in events if e["type"] == "log" and e["payload"].get("reason_code") == REASON]
        assert {p["candidate_id"] for p in logs} == ga_ids

        # replay guard: a NOT_EVALUATED payload never replaces a committed (immutable) measurement
        rnd_job = next(j for j in jobs if j["kind"] == JobKind.CANDIDATE_TRAIN.value
                       and j["spec"].get("arm") == "IndependentRandom" and j["rung"] == "L0")
        before = repo.get_evaluation(conn, engine.evaluation_id(rnd_job["candidate_id"], "L0"))
        assert before["immutable"]
        fake = {"evaluation_status": "NOT_EVALUATED", "reason_code": REASON, "ap": None, "latency_source": "pending"}
        with tx(conn):
            kept = engine.commit_evaluation(conn, engine.context(conn, run_id), rnd_job, fake)
        after = repo.get_evaluation(conn, before["id"])
        assert kept == before["row"] and after["row"] == before["row"] and after["status"] == "EVALUATED"
    finally:
        conn.close()


def test_a_payload_without_metrics_that_does_not_say_not_evaluated_still_fails(client, protocol, inline_worker,
                                                                              api_settings, monkeypatch, stub):
    """Guard: only an explicit NOT_EVALUATED declaration takes the new path; a measurement that merely lacks its AP is
    still EVAL_FAILED with METRIC_MISSING (unchanged)."""
    import stub_handlers

    original = stub_handlers.HANDLERS["CANDIDATE_TRAIN"]

    def train(spec, emit, profile_default_s=None):
        out = original(spec, emit, profile_default_s)
        if spec.arm == "GA":
            out = {**out, "evaluation": {**out["evaluation"], "ap": None}}
        return out

    monkeypatch.setitem(stub_handlers.HANDLERS, "CANDIDATE_TRAIN", train)
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    ga = [row for row in _rows(api_settings, run_id) if row["arm"] == "GA"]
    assert ga and all(row["status"] == "EVAL_FAILED" for row in ga)
    assert all(str(row["reason_code"]).startswith("METRIC_MISSING") for row in ga), {row["reason_code"] for row in ga}
    assert all(row.get("performance_status") is None for row in ga)
