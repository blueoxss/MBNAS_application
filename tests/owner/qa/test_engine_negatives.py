"""QA: invalid measurements, generation-count invariants and the cost ledger, through the real engine (CPU, synthetic)."""
from __future__ import annotations

import copy
import math
import random
import sqlite3

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration import engine
from mbnas_contracts.schemas.records import Generation
from mbnas_core.scheduling import costs, planner

BAD_METRICS = [
    ("ap_nan", {"ap": math.nan}, "METRIC_NOT_FINITE:ap"),
    ("recall_inf", {"recall": math.inf}, "METRIC_NOT_FINITE:recall"),
    ("latency_neg_inf", {"latency_ms": -math.inf}, "METRIC_NOT_FINITE:latency_ms"),
    ("size_missing", {"size_mb": None}, "METRIC_MISSING:size_mb"),
    ("ap_above_one", {"ap": 1.5}, "AP_OUT_OF_RANGE"),
    ("recall_negative", {"recall": -0.01}, "RECALL_OUT_OF_RANGE"),
    ("latency_zero", {"latency_ms": 0.0}, "LATENCY_NONPOSITIVE"),
    ("ap_string", {"ap": "0.3"}, "METRIC_NOT_FINITE:ap"),
    ("ap_bool", {"ap": True}, "METRIC_NOT_FINITE:ap"),
]


@pytest.fixture
def staged(client, make_dataset, tmp_roots, inline_worker, conn):
    """A GA run whose generation 0 is proposed (8 QUEUED L0 training jobs) plus one genuine evaluation to copy."""
    ds = make_dataset("neg-ds", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1, population=len(BAD_METRICS) + 1,
                                   baselines=())
    proto["console"].pop("baseline_budget_gpu_hours", None)
    run_id, _ = harness.validate_and_run(client, proto, key="neg-1")
    assert inline_worker.tick()  # PROPOSE_GENERATION only
    jobs = [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["rung"] == "L0"]
    assert len(jobs) == len(BAD_METRICS) + 1 and all(j["status"] == "QUEUED" for j in jobs)
    # one genuine evaluation: run the first training job end to end, keep its evaluation payload as the template
    assert inline_worker.tick()
    done = next(j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["status"] == "SUCCEEDED")
    good = copy.deepcopy(done["result"]["outputs"]["evaluation"])
    good["artifacts"] = []
    pending = [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["rung"] == "L0" and j["status"] == "QUEUED"]
    assert len(pending) == len(BAD_METRICS)
    return run_id, pending, good


def _commit(conn, run_id: str, job: dict, ev: dict) -> dict:
    with tx(conn):
        return engine.commit_evaluation(conn, engine.context(conn, run_id), repo.get_job(conn, job["id"]), ev)


def test_non_finite_missing_and_out_of_range_metrics_are_never_scores(client, conn, staged):
    run_id, pending, good = staged
    seq0 = repo.get_run(conn, run_id)["last_seq"]
    for (label, patch, reason), job in zip(BAD_METRICS, pending):
        row = _commit(conn, run_id, job, {**good, **patch})
        assert row["status"] == "EVAL_FAILED" and row["reason_code"] == reason, (label, row["status"], row.get("reason_code"))
        assert row["fitness"] is None and row["ap"] is None and row["latency_ms"] is None and row["recall"] is None
        assert row["gate"] is None
        stored = repo.get_evaluation(conn, row["id"])
        assert stored["status"] == "EVAL_FAILED" and not stored["immutable"]
        cand = repo.get_candidate(conn, job["candidate_id"])
        assert cand["candidate_status"] == "EVAL_FAILED"
        assert planner.rung_states(cand)["L0"]["state"] == planner.FAILED
    events = repo.get_events_after(conn, run_id, seq0, 1000)
    assert not [e for e in events if e["type"] == "candidate.evaluated"]  # no point is ever announced
    # nothing was promoted: no L1 job exists for any failed candidate
    assert not [j for j in repo.list_jobs(conn, run_id) if j["rung"] == "L1" and j["candidate_id"] in {p["candidate_id"] for p in pending}]
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    failed = [c for c in snap["candidates"] if c["status"] == "EVAL_FAILED"]
    assert len(failed) == len(BAD_METRICS)
    assert all(c["fitness"] is None and c["ap"] is None for c in failed)  # never 0, never the reported value
    for c in failed:
        # contract 1.5.0: raw_metrics.reported is owner-only (DTO allowlist) and never served publicly; the stored row
        # keeps it for diagnosis (as text for non-finite values), served raw by /core/v1/owner/.../audit
        assert "reported" not in (c.get("raw_metrics") or {}), c["id"]
        reported = repo.get_evaluation(conn, c["id"])["row"]["raw_metrics"]["reported"]
        assert isinstance(reported, dict)


def test_failed_rows_close_generation_counts_without_a_representative(client, conn, staged, inline_worker):
    run_id, pending, good = staged
    for job, (_, patch, _) in zip(pending, BAD_METRICS):
        _commit(conn, run_id, job, {**good, **patch})
    harness.drain(inline_worker)
    g0 = next(g for g in repo.list_generations(conn, run_id) if g["rung"] == "L0")
    assert g0["complete"] and g0["planning_closed"] and g0["pending_count"] == 0
    assert g0["planned_count"] == g0["evaluated_count"] + g0["failed_count"] + g0["not_admitted_count"]
    assert g0["failed_count"] == len(BAD_METRICS) and g0["evaluated_count"] == 1
    assert g0["representative_candidate_id"] and g0["representative_candidate_id"] not in {p["candidate_id"] for p in pending}


def test_gate_is_nan_safe_and_reports_eval_invalid():
    """Regression: NaN != NaN used to make the gate raise ValueError instead of answering EVAL_INVALID."""
    from mbnas_core.protocol.fitness import FitnessAnchors, fitness
    from mbnas_core.protocol.promotion import evaluate_gate

    w = {"ap": 0.2, "recall": 0.1, "latency_reward": 0.4, "size_reward": 0.3}
    fit = fitness(math.nan, 0.5, 50.0, 20.0, w, FitnessAnchors((20.0, 200.0), (5.0, 100.0)))
    cfg = {"id": "L0", "fitness_weights": w, "promotion": {"to": "L1", "score_ge": 0.0, "ap_ge": 0.0}}
    decision = evaluate_gate(fit, math.nan, 50.0, 20.0, cfg, {"latency_le_ms": 1000, "weights_size_le_MB": 500},
                             latency_source="measured")
    assert decision.reason_code == "EVAL_INVALID" and not decision.eligible


# -- generation counts invariant ---------------------------------------------------------------------------------
GEN = {"arm": "GA", "generation": 0, "rung": "L0", "comparison_group": "common_scratch", "search_seed": 101,
       "representative_candidate_id": None, "planning_closed": True, "planned_count": 4, "evaluated_count": 2,
       "failed_count": 1, "not_admitted_count": 0, "pending_count": 1, "complete": False}


@pytest.mark.parametrize("patch", [
    {"planned_count": 5},                                           # counts do not sum to planned
    {"evaluated_count": 3},                                         # over-counted
    {"complete": True},                                             # complete with a pending candidate
    {"complete": True, "pending_count": 0, "evaluated_count": 3, "planning_closed": False},  # complete before planning closed
    {"complete": True, "pending_count": 0, "evaluated_count": 3, "planning_closed": None},
    {"complete": True, "pending_count": None},                      # complete without counts
    {"failed_count": -1},                                           # negative
    {"search_seed": None},
])
def test_planner_and_store_reject_invalid_generation_rows(db, patch):
    bad = {**GEN, **patch}
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        Generation.model_validate(bad)
        with tx(db):
            repo.create_run(db, id="run1", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                            execution_mode="PILOT", idempotency_key="k", payload_sha256="p")
            repo.upsert_generation(db, "run1", bad)


def test_complete_generation_cannot_be_reopened_or_recounted(db):
    with tx(db):
        repo.create_run(db, id="run1", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                        execution_mode="PILOT", idempotency_key="k", payload_sha256="p")
        done = {**GEN, "evaluated_count": 3, "pending_count": 0, "complete": True}
        repo.upsert_generation(db, "run1", done)
    for patch in ({"failed_count": 2, "evaluated_count": 2}, {"complete": False, "pending_count": 1, "evaluated_count": 2}):
        with pytest.raises(repo.ImmutableViolation):
            with tx(db):
                repo.upsert_generation(db, "run1", {**done, **patch})


def test_planner_derived_rows_always_satisfy_the_invariant(db):
    """Randomised rung states -> refresh_generation never produces a row that violates the counts invariant."""
    rng = random.Random(20261006)
    with tx(db):
        repo.create_run(db, id="run1", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                        execution_mode="PILOT", idempotency_key="k", payload_sha256="p")
    for trial in range(40):
        n = rng.randint(1, 6)
        with tx(db):
            for i in range(n):
                cid = f"c{trial}-{i}"
                repo.insert_candidate(db, id=cid, run_id="run1", name=cid, arm="GA", family="GA-mixed", search_seed=trial,
                                      generation=0)
                state = rng.choice(planner.RUNG_STATES)
                planner.set_rung_state(db, cid, "L0", state, **({"evaluation_id": None} if state != planner.EVALUATED else {}))
            row, became_complete = planner.refresh_generation(db, "run1", arm="GA", search_seed=trial, generation=0,
                                                              rung="L0", rung_ids=["L0", "L1"], comparison_group="g")
        assert row is not None
        assert row["planned_count"] == n
        assert row["evaluated_count"] + row["failed_count"] + row["not_admitted_count"] + row["pending_count"] == n
        assert bool(row["complete"]) == (row["pending_count"] == 0 and bool(row["planning_closed"]))
        assert became_complete == bool(row["complete"])
        assert row["representative_candidate_id"] is None or row["evaluated_count"] > 0


# -- cost ledger ----------------------------------------------------------------------------------------------
T0, T1 = "2026-10-06T00:00:00.000000Z", "2026-10-06T00:00:10.000000Z"


@pytest.fixture
def ledger_run(db):
    with tx(db):
        repo.create_run(db, id="run1", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                        execution_mode="PILOT", idempotency_key="k", payload_sha256="p")
        for cid, arm in (("cga", "GA"), ("crd", "IndependentRandom"), ("cbl", "Baseline")):
            repo.insert_candidate(db, id=cid, run_id="run1", name=cid, arm=arm, family="x", search_seed=1)
    return db


def _attempt(db, job_kind: str, cid: str | None, outcome: str, jid: str, attempt: int = 1, arm: str | None = None, seconds: int = 10):
    job = {"id": jid, "run_id": "run1", "kind": job_kind, "candidate_id": cid, "rung": "L0", "attempt": attempt,
           "spec": {"arm": arm} if arm else {}}
    from mbnas_contracts.util.time import iso_after

    with tx(db):
        return costs.record_attempt(db, job, outcome=outcome, started_at=T0, finished_at=iso_after(T0, seconds))


def test_ledger_separates_billed_physical_baseline_final_failed_retry_and_profile(ledger_run):
    db = ledger_run
    _attempt(db, "CANDIDATE_TRAIN", "cga", "succeeded", "j1", seconds=10)             # GA train 10
    _attempt(db, "CANDIDATE_TRAIN", "crd", "succeeded", "j2", seconds=7)              # Random train 7
    _attempt(db, "BASELINE_TRAIN", "cbl", "succeeded", "j3", seconds=20)              # baseline 20 (billed to nobody)
    _attempt(db, "CANDIDATE_TRAIN", "cga", "lost", "j4", attempt=1, seconds=3)        # lost work -> failed, billed to GA
    _attempt(db, "CANDIDATE_TRAIN", "cga", "succeeded", "j4", attempt=2, seconds=5)   # re-execution -> retry, billed to GA
    _attempt(db, "EVALUATE", "crd", "succeeded", "j5", seconds=2)                     # val 2 billed to Random
    _attempt(db, "BENCHMARK", "cga", "succeeded", "j6", seconds=4)                    # profile 4: physical, never billed
    _attempt(db, "FINAL_RETRAIN", None, "succeeded", "j7", seconds=30, arm="Final")   # final 30, never billed to a search arm
    assert _attempt(db, "PROPOSE_GENERATION", None, "succeeded", "j8") is None        # CPU-only job: no ledger row
    rows = repo.list_costs(db, "run1")
    by_job = {(r["job_id"], r["attempt"]): r for r in rows}
    assert by_job[("j1", 1)]["kind"] == "train" and by_job[("j1", 1)]["billed_arm"] == "GA"
    assert by_job[("j3", 1)]["billed_arm"] is None and by_job[("j3", 1)]["arm"] == "Baseline"
    assert by_job[("j4", 1)]["kind"] == "failed" and by_job[("j4", 1)]["billed_arm"] == "GA"
    assert by_job[("j4", 2)]["kind"] == "retry" and by_job[("j4", 2)]["billed_arm"] == "GA"
    assert by_job[("j5", 1)]["kind"] == "val" and by_job[("j5", 1)]["billed_arm"] == "IndependentRandom"
    assert by_job[("j6", 1)]["kind"] == "profile" and by_job[("j6", 1)]["billed_arm"] is None
    assert by_job[("j7", 1)]["arm"] == "Final" and by_job[("j7", 1)]["billed_arm"] is None
    assert all(r["physical"] == 1 for r in rows)
    with tx(db):
        agg = costs.refresh_run_costs(db, "run1")
    assert agg == {"ga_billed_gpu_seconds": 18.0, "random_billed_gpu_seconds": 9.0, "actual_gpu_seconds": 81.0,
                   "baseline_gpu_seconds": 20.0, "final_gpu_seconds": 30.0, "failed_gpu_seconds": 3.0,
                   "retry_gpu_seconds": 5.0, "profile_gpu_seconds": 4.0}
    assert repo.get_run(db, "run1")["costs"] == agg
    # billed != physical: baseline + final + profile are physical but nobody's search budget
    assert agg["actual_gpu_seconds"] - agg["ga_billed_gpu_seconds"] - agg["random_billed_gpu_seconds"] == 20 + 30 + 4


def test_baseline_and_final_overshoot_are_recorded_and_logged_once(ledger_run):
    """S6-F2 (unit): Baseline/Final caps are per run; overshoot = max(0, consumed - cap); profile never counts."""
    db = ledger_run
    with tx(db):
        repo.insert_candidate(db, id="cfn", run_id="run1", name="cfn", arm="Final", family="x")
        repo.update_run_fields(db, "run1", budget={"unit": "gpu_seconds", "search_per_arm_seed_s": 12.0,
                                                   "baseline_s": 15.0, "final_s": 100.0})
    _attempt(db, "BASELINE_TRAIN", "cbl", "succeeded", "b1", seconds=10)   # 10 <= 15
    _attempt(db, "BASELINE_TRAIN", "cbl", "succeeded", "b2", seconds=8)    # 18 > 15: overshoot 3, logged
    _attempt(db, "BASELINE_TRAIN", "cbl", "failed", "b3", seconds=2)       # 20: overshoot 5, not logged again
    _attempt(db, "FINAL_RETRAIN", "cfn", "succeeded", "f1", seconds=30)    # 30 <= 100
    _attempt(db, "CANDIDATE_TRAIN", "cga", "succeeded", "g1", seconds=10)  # GA seed 1: 10 <= 12
    from mbnas_contracts.util.time import iso_after
    job = {"id": "g2", "run_id": "run1", "kind": "CANDIDATE_TRAIN", "candidate_id": "cga", "rung": "L0", "attempt": 1,
           "spec": {}}
    with tx(db):  # 9 s attempt, 7 s of it profiling: billed 2 -> 12 == cap, no overshoot
        costs.record_attempt(db, job, outcome="succeeded", started_at=T0, finished_at=iso_after(T0, 9),
                             inline_timings={"profile_s": 7.0})
    with tx(db):
        agg = costs.refresh_run_costs(db, "run1")
    assert agg["overshoot_gpu_seconds.Baseline"] == pytest.approx(5.0)
    assert agg["overshoot_gpu_seconds.Final"] == 0.0
    assert agg["overshoot_gpu_seconds.GA.seed_1"] == 0.0
    assert "overshoot_gpu_seconds.IndependentRandom.seed_1" not in agg  # nothing billed to that arm/seed yet
    assert agg["overshoot_gpu_seconds"] == pytest.approx(5.0)
    logs = [e["payload"] for e in repo.get_events_after(db, "run1", 0, 1000) if e["type"] == "log"
            and e["payload"].get("reason_code") == "BUDGET_OVERSHOOT"]
    assert len(logs) == 1 and logs[0]["arm"] == "Baseline" and logs[0]["job_id"] == "b2"
    assert logs[0]["cap_s"] == 15.0 and logs[0]["billed_s"] == pytest.approx(18.0)
    assert logs[0]["billed_before_s"] == pytest.approx(10.0) and logs[0]["overshoot_s"] == pytest.approx(3.0)


def test_pause_is_kept_work_and_unknown_start_is_not_billed(ledger_run):
    db = ledger_run
    _attempt(db, "CANDIDATE_TRAIN", "cga", "paused", "jp", seconds=6)
    assert repo.list_costs(db, "run1")[0]["kind"] == "train"
    job = {"id": "jx", "run_id": "run1", "kind": "CANDIDATE_TRAIN", "candidate_id": "cga", "attempt": 1, "spec": {}}
    with tx(db):
        assert costs.record_attempt(db, job, outcome="failed", started_at=None, finished_at=T1) is None
    with pytest.raises(ValueError):
        costs.ledger_kind("CANDIDATE_TRAIN", "exploded", False)
    assert costs.billed_seconds(db, "run1", "GA", 1) == 6.0
    assert costs.arm_physical_seconds(db, "run1", "Baseline") == 0.0
    assert "final_gpu_seconds" not in repo.aggregate_costs(db, "run1")  # absent, never a fake 0


def test_run_costs_on_the_api_match_the_ledger_and_export_csv(client, run_qa, conn):
    import csv
    import io
    import zipfile

    run_id = run_qa(arms=("GA", "IndependentRandom"), key="cost-1", baselines=("yolo11n",), generations=2)
    run = client.get(f"/api/v1/runs/{run_id}", headers=auth()).json()
    ledger = repo.list_costs(conn, run_id)
    assert ledger
    c = run["costs"]
    phys = sum(r["gpu_seconds"] for r in ledger if r["physical"])
    assert c["actual_gpu_seconds"] == pytest.approx(phys)
    assert c["ga_billed_gpu_seconds"] == pytest.approx(sum(r["gpu_seconds"] for r in ledger if r["billed_arm"] == "GA"))
    assert c["random_billed_gpu_seconds"] == pytest.approx(sum(r["gpu_seconds"] for r in ledger if r["billed_arm"] == "IndependentRandom"))
    assert c["baseline_gpu_seconds"] == pytest.approx(sum(r["gpu_seconds"] for r in ledger if r["arm"] == "Baseline"))
    assert "final_gpu_seconds" not in c
    # S6-F1: inline latency profiling (evaluation.timings.profile_s) is its own physical, never-billed 'profile' row
    profile = [r for r in ledger if r["kind"] == "profile"]
    assert profile and all(r["physical"] == 1 and r["billed_arm"] is None for r in profile)
    assert all(r["meta"]["source"] == "inline_latency" for r in profile)
    work = {(r["job_id"], r["attempt"]): r for r in ledger if r["kind"] in ("train", "retry", "val")}
    for r in profile:  # same job/attempt and arm as the training/eval row it was carved out of
        host = work[(r["job_id"], r["attempt"])]
        assert host["arm"] == r["arm"] and host["candidate_id"] == r["candidate_id"]
        assert host["meta"]["inline_profile_gpu_seconds"] == pytest.approx(r["gpu_seconds"])
        assert host["gpu_seconds"] + r["gpu_seconds"] == pytest.approx(host["meta"]["attempt_gpu_seconds"])
    assert {r["arm"] for r in profile} >= {"GA", "IndependentRandom", "Baseline"}
    assert c["profile_gpu_seconds"] > 0
    assert c["profile_gpu_seconds"] == pytest.approx(sum(r["gpu_seconds"] for r in profile))
    for arm, key in (("GA", "ga_billed_gpu_seconds"), ("IndependentRandom", "random_billed_gpu_seconds")):
        billed = sum(r["gpu_seconds"] for r in ledger if r["arm"] == arm and r["kind"] in ("train", "retry", "failed", "val"))
        assert c[key] == pytest.approx(billed)  # billed arm seconds exclude the arm's profile rows
        assert c[key] < sum(r["gpu_seconds"] for r in ledger if r["arm"] == arm)
    # no search arm is billed for baseline work; billed arms + baseline + search-arm profile partition the physical time
    assert all(r["billed_arm"] is None for r in ledger if r["arm"] == "Baseline")
    search_profile = sum(r["gpu_seconds"] for r in profile if r["arm"] != "Baseline")
    assert (c["ga_billed_gpu_seconds"] + c["random_billed_gpu_seconds"] + c["baseline_gpu_seconds"] + search_profile
            == pytest.approx(c["actual_gpu_seconds"]))
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/runs/{run_id}/export", headers=auth()).content))
    rows = list(csv.DictReader(io.StringIO(zf.read("cost_ledger.csv").decode())))
    assert len(rows) == len(ledger)
    assert sum(float(r["gpu_seconds"]) for r in rows if r["billed_arm"] == "GA") == pytest.approx(c["ga_billed_gpu_seconds"])
    assert sum(float(r["gpu_seconds"]) for r in rows) == pytest.approx(c["actual_gpu_seconds"])
    csv_profile = [r for r in rows if r["kind"] == "profile"]
    assert len(csv_profile) == len(profile) and all(r["billed_arm"] == "" for r in csv_profile)
    assert sum(float(r["gpu_seconds"]) for r in csv_profile) == pytest.approx(c["profile_gpu_seconds"])


def test_inline_profile_is_split_from_the_attempt_and_never_billed(ledger_run):
    """S6-F1 (unit): profile_s of an attempt becomes a 'profile' row; the work row keeps the rest; total unchanged."""
    db = ledger_run
    from mbnas_contracts.util.time import iso_after

    def attempt(kind, cid, jid, seconds, timings, outcome="succeeded"):
        job = {"id": jid, "run_id": "run1", "kind": kind, "candidate_id": cid, "rung": "L0", "attempt": 1, "spec": {}}
        with tx(db):
            return costs.record_attempt(db, job, outcome=outcome, started_at=T0, finished_at=iso_after(T0, seconds),
                                        inline_timings=timings)

    attempt("CANDIDATE_TRAIN", "cga", "a1", 10, {"val_s": 1.5, "profile_s": 4.0})
    attempt("EVALUATE", "crd", "a2", 3, {"val_s": 1.0, "profile_s": 1.25})
    attempt("BASELINE_TRAIN", "cbl", "a3", 8, {"val_s": 1.0, "profile_s": 2.0})
    attempt("CANDIDATE_TRAIN", "cga", "a4", 2, {"profile_s": 9.0})     # clamped to the attempt interval
    attempt("CANDIDATE_TRAIN", "crd", "a5", 5, {"profile_s": 3.0}, outcome="failed")  # failed: whole attempt failed
    attempt("CANDIDATE_TRAIN", "crd", "a6", 4, None)                   # no timings: unchanged behaviour
    rows = {(r["job_id"], r["kind"]): r for r in repo.list_costs(db, "run1")}
    assert rows[("a1", "train")]["gpu_seconds"] == pytest.approx(6.0) and rows[("a1", "train")]["billed_arm"] == "GA"
    p1 = rows[("a1", "profile")]
    assert p1["gpu_seconds"] == pytest.approx(4.0) and p1["billed_arm"] is None and p1["arm"] == "GA"
    assert p1["meta"]["source"] == "inline_latency" and p1["meta"]["host_kind"] == "train" and p1["attempt"] == 1
    assert p1["meta"]["val_s"] == 1.5 and p1["meta"]["profile_s"] == 4.0
    assert rows[("a2", "val")]["gpu_seconds"] == pytest.approx(1.75) and rows[("a2", "profile")]["gpu_seconds"] == pytest.approx(1.25)
    assert rows[("a3", "train")]["gpu_seconds"] == pytest.approx(6.0) and rows[("a3", "profile")]["arm"] == "Baseline"
    assert rows[("a4", "train")]["gpu_seconds"] == pytest.approx(0.0) and rows[("a4", "profile")]["gpu_seconds"] == pytest.approx(2.0)
    assert ("a5", "profile") not in rows and rows[("a5", "failed")]["gpu_seconds"] == pytest.approx(5.0)
    assert ("a6", "profile") not in rows and rows[("a6", "train")]["gpu_seconds"] == pytest.approx(4.0)
    with tx(db):
        agg = costs.refresh_run_costs(db, "run1")
    assert agg["actual_gpu_seconds"] == pytest.approx(10 + 3 + 8 + 2 + 5 + 4)  # physical time is unchanged
    assert agg["profile_gpu_seconds"] == pytest.approx(4.0 + 1.25 + 2.0 + 2.0)
    assert agg["ga_billed_gpu_seconds"] == pytest.approx(6.0 + 0.0)
    assert agg["random_billed_gpu_seconds"] == pytest.approx(1.75 + 5.0 + 4.0)
    assert agg["baseline_gpu_seconds"] == pytest.approx(8.0)  # physical Baseline time keeps its profile row
    assert costs.billed_seconds(db, "run1", "GA", 1) == pytest.approx(6.0)
    assert costs.arm_budget_seconds(db, "run1", "Baseline") == pytest.approx(6.0)  # cap consumption excludes profile
