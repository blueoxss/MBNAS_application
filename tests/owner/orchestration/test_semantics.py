"""Promotion/admission/generation semantics of the engine with SYNTHETIC stub handlers (in-process worker)."""
from __future__ import annotations

import copy
import sqlite3

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine
from mbnas_core.scheduling.planner import select_representative


@pytest.fixture
def conn(api_settings):
    c = connect(api_settings, init=False)
    yield c
    c.close()


def _rows(conn, run_id):
    return [e for e in repo.list_evaluations(conn, run_id)]


def test_gate_rejection_stops_promotion_and_closes_next_rung(client, make_dataset, tmp_roots, inline_worker, conn):
    ds = make_dataset("g", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1, baselines=(),
                                   score_ge=0.99)
    proto["console"].pop("baseline_budget_gpu_hours", None)
    run_id, _ = harness.validate_and_run(client, proto)
    harness.drain(inline_worker)
    cands = repo.list_candidates(conn, run_id)
    assert {c["candidate_status"] for c in cands} == {"REJECTED_BY_GATE"}
    assert {j["rung"] for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN"} == {"L0"}
    gens = {g["rung"]: g for g in repo.list_generations(conn, run_id)}
    assert gens["L0"]["complete"] and gens["L0"]["evaluated_count"] == 2
    assert gens["L1"]["complete"] and gens["L1"]["planned_count"] == 0
    assert gens["L1"]["representative_candidate_id"] is None  # no line without a valid measured evaluation
    for ev in _rows(conn, run_id):
        assert ev["row"]["gate"]["reason_code"] == "REJECTED_BY_GATE"
        assert ev["row"]["gate"]["source_rung"] == "L0"
    assert repo.get_run(conn, run_id)["status"] == "COMPLETED"


def test_budget_exhaustion_is_eligible_not_admitted(client, make_dataset, tmp_roots, inline_worker, conn, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.05")
    ds = make_dataset("b", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("IndependentRandom",), generations=2,
                                   baselines=(), budget_h=0.00002)  # 0.072 GPU-seconds per arm/seed
    run_id, _ = harness.validate_and_run(client, proto)
    harness.drain(inline_worker)
    cands = repo.list_candidates(conn, run_id)
    assert {c["candidate_status"] for c in cands} == {"ELIGIBLE_BUDGET_EXHAUSTED"}
    assert all(c["generation"] == 0 for c in cands)  # generation 1 never proposed once the arm budget is gone
    l1 = next(g for g in repo.list_generations(conn, run_id) if g["rung"] == "L1")
    assert l1["not_admitted_count"] == 2 and l1["complete"] and l1["evaluated_count"] == 0
    logs = [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 1000) if e["type"] == "log"]
    assert any(p.get("reason_code") == "BUDGET_EXHAUSTED" for p in logs)
    rows = {e["row"]["rung"]: e["row"] for e in _rows(conn, run_id) if e["status"] == "ELIGIBLE_BUDGET_EXHAUSTED"}
    assert rows["L1"]["fitness"] is None  # not admitted is not a point and never a 0 score
    admission = repo.get_candidate(conn, cands[0]["id"])["audit"]["orchestration"]["rungs"]["L1"]["admission"]
    assert admission["admitted"] is False and admission["estimate_source"] == "candidate_measured_rate"


def test_budget_overshoot_is_recorded_in_costs_and_logged_once(client, make_dataset, tmp_roots, inline_worker, conn,
                                                               monkeypatch):
    """S6-F2: an admitted rung whose real duration exceeds the cap pushes billed past it; Run.costs records
    overshoot = max(0, billed - cap) per arm/seed and the engine logs BUDGET_OVERSHOOT exactly once per arm/seed."""
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.05")
    ds = make_dataset("o", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA", "IndependentRandom"), generations=2,
                                   baselines=(), budget_h=0.00002)  # 0.072 GPU-seconds per arm/seed
    proto["console"].pop("baseline_budget_gpu_hours", None)
    run_id, _ = harness.validate_and_run(client, proto)
    harness.drain(inline_worker)
    run = repo.get_run(conn, run_id)
    cap = run["budget"]["search_per_arm_seed_s"]
    assert cap == pytest.approx(0.072)
    seeds = sorted({c["search_seed"] for c in repo.list_candidates(conn, run_id)})
    logs = [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 5000) if e["type"] == "log"
            and e["payload"].get("reason_code") == "BUDGET_OVERSHOOT"]
    total = 0.0
    for arm in ("GA", "IndependentRandom"):
        for seed in seeds:
            billed = sum(r["gpu_seconds"] for r in repo.list_costs(conn, run_id) if r["billed_arm"] == arm
                         and repo.get_candidate(conn, r["candidate_id"])["search_seed"] == seed)
            assert billed > cap  # the admitted L0 rung really overshot
            key = f"overshoot_gpu_seconds.{arm}.seed_{seed}"
            assert run["costs"][key] == pytest.approx(billed - cap)
            total += run["costs"][key]
            mine = [p for p in logs if p["arm"] == arm and p["search_seed"] == seed]
            assert len(mine) == 1, mine  # once, when the crossing attempt was billed
            assert mine[0]["cap_s"] == pytest.approx(cap) and mine[0]["billed_s"] > cap and mine[0]["job_id"]
            assert mine[0]["billed_before_s"] <= cap
    assert run["costs"]["overshoot_gpu_seconds"] == pytest.approx(total)
    assert "overshoot_gpu_seconds.Baseline" not in run["costs"]  # no baseline work, no cap: nothing to report


def test_no_overshoot_key_is_positive_when_billed_stays_below_the_cap(client, protocol, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    run = repo.get_run(conn, run_id)
    keys = [k for k in run["costs"] if k.startswith("overshoot_gpu_seconds")]
    assert keys and all(run["costs"][k] == 0.0 for k in keys)  # measured against the cap: exactly 0, not absent
    assert "overshoot_gpu_seconds.Baseline" in run["costs"]
    assert not [e for e in repo.get_events_after(conn, run_id, 0, 5000)
                if e["type"] == "log" and e["payload"].get("reason_code") == "BUDGET_OVERSHOOT"]


@pytest.mark.parametrize("arm,cap_key", [("GA", "search_per_arm_seed_s"), ("IndependentRandom", "search_per_arm_seed_s"),
                                         ("Baseline", "baseline_s"), ("Final", "final_s")])
def test_admission_refuses_work_whose_cap_is_missing(db, arm, cap_key):
    """S6-F4: an absent cap is never unlimited: admit() returns BUDGET_MISSING (not admitted) for every cap kind."""
    from mbnas_core.scheduling import admission

    budget = {"unit": "gpu_seconds", "search_per_arm_seed_s": 100.0, "baseline_s": 100.0, "final_s": 100.0}
    with tx(db):
        repo.create_run(db, id="rb", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                        execution_mode="PILOT", idempotency_key="kb", payload_sha256="p")
        repo.update_run_fields(db, "rb", budget=budget)
        cand = repo.insert_candidate(db, id="c1", run_id="rb", name="c1", arm=arm, family="x",
                                     search_seed=1 if arm in ("GA", "IndependentRandom") else None)
    run = repo.get_run(db, "rb")
    assert admission.admit(db, run, cand, 1).admitted  # control: the same request with the cap present is admitted
    with tx(db):
        run = repo.update_run_fields(db, "rb", budget={**budget, cap_key: None})
    d = admission.admit(db, run, cand, 1)
    assert d.admitted is False and d.reason_code == "BUDGET_MISSING"
    assert d.cap_s is None and d.remaining_s is None and d.arm == arm
    other = next(k for k in ("search_per_arm_seed_s", "baseline_s", "final_s") if k != cap_key)
    with tx(db):
        run = repo.update_run_fields(db, "rb", budget={**budget, other: None})
    assert admission.admit(db, run, cand, 1).admitted  # only the cap this arm needs matters


def test_missing_search_cap_makes_candidates_eligible_budget_exhausted(client, make_dataset, tmp_roots, inline_worker,
                                                                       conn):
    """S6-F4 (engine): with the search cap missing, _schedule_stage admits nothing; each candidate becomes
    ELIGIBLE_BUDGET_EXHAUSTED with reason BUDGET_MISSING on its rung audit and its provisional row."""
    ds = make_dataset("m", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("IndependentRandom",), generations=1,
                                   baselines=())
    proto["console"].pop("baseline_budget_gpu_hours", None)
    run_id, _ = harness.validate_and_run(client, proto)
    with tx(conn):
        budget = repo.get_run(conn, run_id)["budget"]
        repo.update_run_fields(conn, run_id, budget={**budget, "search_per_arm_seed_s": None})
    harness.drain(inline_worker)
    cands = repo.list_candidates(conn, run_id)
    assert cands and {c["candidate_status"] for c in cands} == {"ELIGIBLE_BUDGET_EXHAUSTED"}
    assert not [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN"]  # nothing admitted
    assert repo.list_costs(conn, run_id) == []
    for cand in cands:
        rung = repo.get_candidate(conn, cand["id"])["audit"]["orchestration"]["rungs"]["L0"]
        assert rung["state"] == "not_admitted" and rung["reason_code"] == "BUDGET_MISSING"
        assert rung["admission"]["admitted"] is False and rung["admission"]["cap_s"] is None
    rows = [e["row"] for e in _rows(conn, run_id) if e["row"]["rung"] == "L0"]
    assert len(rows) == len(cands)
    assert all(r["status"] == "ELIGIBLE_BUDGET_EXHAUSTED" and r["reason_code"] == "BUDGET_MISSING"
               and r["fitness"] is None for r in rows)


def test_random_proposals_never_receive_fitness_and_ga_uses_l0_pool(client, protocol, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    proposals = [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "PROPOSE_GENERATION"]
    for job in proposals:
        params = job["spec"]["params"]
        if params["arm"] == "IndependentRandom" or params["generation"] == 0:
            assert "parent_pool" not in params
        else:
            pool = params["parent_pool"]
            assert pool and {p["rung"] for p in pool} == {"L0"} and {p["status"] for p in pool} == {"EVALUATED"}
            assert all(p["genotype"] for p in pool)
            gen0 = {c["id"] for c in repo.list_candidates(conn, run_id) if c["arm"] == "GA" and c["generation"] == 0}
            assert {p["candidate_id"] for p in pool} == gen0
    ga1 = [c for c in repo.list_candidates(conn, run_id) if c["arm"] == "GA" and c["generation"] == 1]
    assert {len(c["parents"]) for c in ga1} == {1, 2}  # clone + crossover lineage kept


def test_failed_training_counts_as_failed_and_bills_arm(client, protocol, inline_worker, conn, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_FAIL", "g00:c001,.g0.c001")
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    failed = [c for c in repo.list_candidates(conn, run_id) if c["candidate_status"] == "INVALID"]
    assert len(failed) == 2  # one per arm in generation 0
    l0 = [g for g in repo.list_generations(conn, run_id) if g["rung"] == "L0" and g["generation"] == 0]
    assert all(g["failed_count"] == 1 and g["evaluated_count"] == 1 and g["complete"] for g in l0)
    ledger = repo.list_costs(conn, run_id)
    fails = [r for r in ledger if r["kind"] == "failed"]
    assert {r["billed_arm"] for r in fails} == {"GA", "IndependentRandom"}
    assert all(r["physical"] == 1 for r in ledger)
    assert all(r["billed_arm"] is None for r in ledger if r["arm"] == "Baseline")
    costs = repo.get_run(conn, run_id)["costs"]
    assert costs["failed_gpu_seconds"] == pytest.approx(sum(r["gpu_seconds"] for r in fails))
    assert repo.get_run(conn, run_id)["status"] == "COMPLETED"
    rows = [e for e in _rows(conn, run_id) if e["candidate_id"] in {c["id"] for c in failed}]
    assert {r["status"] for r in rows} == {"INVALID"} and all(r["row"]["fitness"] is None for r in rows)


def test_evaluated_rows_are_immutable_and_replay_is_idempotent(client, protocol, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    ev = next(e for e in _rows(conn, run_id) if e["status"] == "EVALUATED")
    with pytest.raises(sqlite3.IntegrityError):
        with tx(conn):
            conn.execute("UPDATE evaluations SET row_json = '{}' WHERE id = ?", (ev["id"],))
    with pytest.raises(repo.ImmutableViolation):
        with tx(conn):
            repo.insert_evaluation(conn, run_id, {**ev["row"], "ap": 0.99})
    job = repo.get_job(conn, ev["row"]["job_id"])
    seq_before, jobs_before = repo.get_run(conn, run_id)["last_seq"], len(repo.list_jobs(conn, run_id))
    with tx(conn):  # replay of the same training result
        out = engine.commit_evaluation(conn, engine.context(conn, run_id), job, job["result"]["outputs"]["evaluation"])
    assert out == ev["row"]
    assert repo.get_run(conn, run_id)["last_seq"] == seq_before and len(repo.list_jobs(conn, run_id)) == jobs_before
    with tx(conn):  # a correction is a superseding row, the original stays
        repo.supersede_evaluation(conn, ev["id"], {**ev["row"], "id": ev["id"] + ".r2", "ap": 0.5})
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    ids = {c["id"]: c for c in snap["candidates"]}
    assert ids[ev["id"]]["ap"] == ev["row"]["ap"] and ids[ev["id"] + ".r2"]["revision"] == 2


def test_progress_and_failures_never_touch_committed_rows(client, protocol, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    ev = next(e for e in _rows(conn, run_id) if e["status"] == "EVALUATED")
    job = repo.get_job(conn, ev["row"]["job_id"])
    with tx(conn):
        engine.job_progress(conn, job, {"epoch": 1, "epoch_target_total": 2})
    assert repo.get_evaluation(conn, ev["id"])["row"] == ev["row"]


def test_representative_rule():
    rows = [{"candidate_id": "b", "id": "1", "status": "EVALUATED", "fitness": 0.5, "latency_ms": 30},
            {"candidate_id": "a", "id": "2", "status": "EVALUATED", "fitness": 0.5, "latency_ms": 30},
            {"candidate_id": "c", "id": "3", "status": "EVALUATED", "fitness": 0.5, "latency_ms": 20},
            {"candidate_id": "d", "id": "4", "status": "EVAL_FAILED", "fitness": None, "latency_ms": None}]
    assert select_representative(rows)["candidate_id"] == "c"
    assert select_representative(rows[:2])["candidate_id"] == "a"
    assert select_representative(rows[3:]) is None


def test_runs_are_deterministic_in_structure(client, make_dataset, tmp_roots, inline_worker, conn):
    ds = make_dataset("d", counts={"train": 4, "val": 2, "test": 2})
    shapes = []
    for key in ("det-1", "det-2"):
        proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"))
        proto["console"]["label"] = key
        run_id, _ = harness.validate_and_run(client, copy.deepcopy(proto), key=key)
        harness.drain(inline_worker)
        # stub metrics depend on the (run-scoped) candidate id, so compare the planned structure only
        rows = sorted((e["row"]["arm"], e["row"]["generation"], e["row"]["rung"], e["row"]["name"], e["status"])
                      for e in _rows(conn, run_id))
        shapes.append(rows)
    assert shapes[0] == shapes[1]



def test_per_kind_checkpoint_hash_is_normalised(client, protocol, inline_worker, conn, monkeypatch):
    """The real training handler reports checkpoint_sha256 per checkpoint kind (found by the GPU smoke)."""
    monkeypatch.setenv("MBNAS_STUB_SHA_BY_KIND", "1")
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    evaluated = [e for e in _rows(conn, run_id) if e["status"] == "EVALUATED"]
    assert evaluated and not [e for e in _rows(conn, run_id) if e["status"] == "EVAL_FAILED"]
    for ev in evaluated:
        by_kind = ev["row"]["raw_metrics"]["checkpoint_sha256_by_kind"]
        assert ev["row"]["checkpoint_sha256"] == by_kind["training_full"]
