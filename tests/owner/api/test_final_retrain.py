"""POST /runs/{run_id}/final-retrain (contract 1.2.0): finalist rule, idempotency, preconditions, child run plumbing,
costs and export — with SYNTHETIC stub handlers and the in-process worker (no GPU, synthetic metrics)."""
from __future__ import annotations

import io
import json
import zipfile

import harness
import pytest
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine, final_retrain
from mbnas_runner.training.context import read_split_access_log

FINAL_URL = "/api/v1/runs/{}/final-retrain"


@pytest.fixture
def conn(api_settings):
    c = connect(api_settings, init=False)
    yield c
    c.close()


@pytest.fixture
def measured(monkeypatch):
    """The finalist rule only accepts measured latency: the (synthetic) stub reports latency_source=measured."""
    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "measured")


@pytest.fixture
def parent(client, protocol, inline_worker, measured, conn):
    """A COMPLETED search run (GA + IndependentRandom, seed 101, rungs L0/L1, one baseline)."""
    run_id, _ = harness.validate_and_run(client, protocol, key="parent-1")
    harness.drain(inline_worker)
    assert repo.get_run(conn, run_id)["status"] == "COMPLETED"
    return run_id


def _post(client, run_id, body, headers=None):
    return client.post(FINAL_URL.format(run_id), json=body, headers={**auth(), **(headers or {})})


def _expected_finalists(conn, run_id, top_k=1):
    rows = [e["row"] for e in repo.list_evaluations(conn, run_id, include_superseded=False)
            if e["status"] == "EVALUATED" and e["row"]["arm"] in ("GA", "IndependentRandom") and e["row"]["rung"] == "L1"]
    out = []
    for arm in ("GA", "IndependentRandom"):
        ranked = sorted((r for r in rows if r["arm"] == arm), key=lambda r: (-r["ap"], r["latency_ms"], r["candidate_id"]))
        out.extend(r["candidate_id"] for r in ranked[:top_k])
    return out


def test_final_retrain_child_run_end_to_end(client, parent, inline_worker, conn, api_settings):
    before = repo.get_run(conn, parent)
    r = _post(client, parent, {"idempotency_key": "fr-1", "budget_gpu_hours": 0.25, "reason": "test"},
              headers={"Idempotency-Key": "fr-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert contract_errors("RunCreated", body) == []
    assert body["parent_run_id"] == parent and body["finalist_count"] == 2 and body["status"] == "QUEUED"
    child_id = body["run_id"]
    assert child_id != parent
    run = client.get(f"/api/v1/runs/{child_id}", headers=auth()).json()
    assert contract_errors("Run", run) == []
    assert run["kind"] == "FINAL_RETRAIN" and run["parent_run_id"] == parent
    assert run["protocol_hash"] == before["protocol_hash"] and run["resolved_protocol"] == before["resolved_protocol"]
    assert (run["namespace"], run["execution_mode"], run["purpose"]) == (before["namespace"], before["execution_mode"],
                                                                         before["purpose"])
    assert run["budget"]["final_s"] == pytest.approx(900.0)
    finalists = run["finalists"]
    assert [f["candidate_id"] for f in finalists] == _expected_finalists(conn, parent)
    assert {f["arm"] for f in finalists} == {"GA", "IndependentRandom"} and {f["rung"] for f in finalists} == {"L1"}
    assert all(f["selection_rule"].startswith(final_retrain.SELECTION_RULE_ID) for f in finalists)
    assert all(f["source_evaluation_id"] == f"{f['candidate_id']}.L1.eval" for f in finalists)

    cands = repo.list_candidates(conn, child_id)
    assert len(cands) == 2
    for cand, f in zip(sorted(cands, key=lambda c: c["id"]), finalists):
        src = repo.get_candidate(conn, f["candidate_id"])
        assert cand["arm"] == "Final" and cand["candidate_status"] == "FINALIST" and cand["id"] != src["id"]
        assert cand["name"] == f"FINAL-{f['arm']}-s101-{src['name']}"
        assert cand["parents"] == [src["id"]] and cand["parent_evaluation_ids"] == [f["source_evaluation_id"]]
        assert cand["lineage"]["operation"] == "final" and cand["lineage"]["source_candidate_id"] == src["id"]
        assert cand["architecture_hash"] == src["architecture_hash"] and cand["blocks"] == src["blocks"]
        assert cand["training_seed"] is not None and cand["training_seed"] != src["training_seed"]
    jobs = repo.list_jobs(conn, child_id)
    assert {j["kind"] for j in jobs} == {"FINAL_RETRAIN"} and len(jobs) == 2
    final_cfg = before["resolved_protocol"]["final_retraining"]
    for j in jobs:  # pinned final recipe: PILOT_SMOKE 2/2 epochs, rung FINAL, admitted against the Final cap
        assert (j["rung"], j["cumulative_target"], j["fixed_horizon"]) == (
            "FINAL", final_cfg["epoch_target_total"], final_cfg["scheduler_horizon_epochs"])
        adm = j["spec"]["params"]["admission"]
        assert adm["admitted"] and adm["arm"] == "Final" and adm["cap_s"] == pytest.approx(900.0)
        assert j["spec"]["params"]["eval_split"] == "final_eval" and j["spec"]["arm"] == "Final"

    harness.drain(inline_worker)
    child = repo.get_run(conn, child_id)
    assert child["status"] == "COMPLETED"
    final_hash = before["resolved_protocol"]["dataset"]["final_evaluation_manifest_hash"]
    rows = [e for e in repo.list_evaluations(conn, child_id) if e["status"] == "EVALUATED"]
    assert len(rows) == 2
    for e in rows:
        row = e["row"]
        assert row["rung"] == "FINAL" and row["comparison_group"] == "final" and row["split_hash"] == final_hash
        assert row["arm"] == "Final" and row["gate"] is None and row["fitness"] is not None
        last_weights = before["resolved_protocol"]["rungs"][-1]["fitness_weights"]
        assert row["fitness_weights"] == last_weights  # display only: last search rung weights
        assert {a.get("split_hash") for a in row["artifacts"]} == {final_hash}
        assert {a["matching"]["split"] for a in row["artifacts"]} == {"final_eval"}
    assert {c["candidate_status"] for c in repo.list_candidates(conn, child_id)} == {"FINAL_COMPLETE"}

    child_costs = child["costs"]
    assert child_costs["final_gpu_seconds"] > 0 and "ga_billed_gpu_seconds" not in child_costs
    par = repo.get_run(conn, parent)
    assert par["costs"]["final_gpu_seconds"] == pytest.approx(child_costs["final_gpu_seconds"])
    assert par["costs"]["actual_gpu_seconds"] == pytest.approx(before["costs"]["actual_gpu_seconds"])
    assert par["status"] == "COMPLETED"  # the search run itself never changes state

    parent_logs = [e["payload"] for e in repo.get_events_after(conn, parent, 0, 10_000) if e["type"] == "log"]
    assert any(p.get("child_run_id") == child_id and "created" in p["message"] for p in parent_logs)
    assert any(p.get("child_run_id") == child_id and p.get("child_status") == "COMPLETED" for p in parent_logs)

    snap = client.get(f"/api/v1/runs/{child_id}/snapshot", headers=auth()).json()
    assert contract_errors("Snapshot", snap) == []
    assert snap["run"]["kind"] == "FINAL_RETRAIN" and snap["generations"] == []
    detail = client.get(f"/api/v1/runs/{child_id}/candidates/{cands[0]['id']}", headers=auth()).json()
    assert contract_errors("CandidateDetail", detail) == [] and detail["lineage"]["operation"] == "final"
    parent_view = client.get(f"/api/v1/runs/{parent}", headers=auth()).json()
    assert [c["id"] for c in parent_view["final_retrain_runs"]] == [child_id]
    assert parent_view["kind"] == "SEARCH" and "finalists" not in parent_view

    zc = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/runs/{child_id}/export", headers=auth()).content))
    prov = json.loads(zc.read("provenance.json"))
    assert prov["kind"] == "FINAL_RETRAIN" and prov["parent_run_id"] == parent
    fr = json.loads(zc.read("final_retrain.json"))
    assert fr["parent_run_id"] == parent and fr["finalists"] == finalists
    assert fr["selection_rule"] == finalists[0]["selection_rule"]
    zp = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/runs/{parent}/export", headers=auth()).content))
    children = json.loads(zp.read("final_retrain_children.json"))
    assert [c["run_id"] for c in children] == [child_id]
    assert {r["rung"] for r in children[0]["results"]} == {"FINAL"} and len(children[0]["results"]) == 2
    assert all(r["split_hash"] == final_hash for r in children[0]["results"])
    assert json.loads(zp.read("provenance.json"))["final_retrain_run_ids"] == [child_id]


def test_idempotency_same_key_same_child_and_conflicts(client, parent, conn):
    body = {"idempotency_key": "fr-k", "budget_gpu_hours": 0.25}
    first = _post(client, parent, body)
    again = _post(client, parent, body, headers={"Idempotency-Key": "fr-k"})
    assert first.status_code == again.status_code == 200
    assert first.json()["run_id"] == again.json()["run_id"] and again.json()["created"] is False
    other = _post(client, parent, {**body, "budget_gpu_hours": 0.5})
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_CONFLICT"
    mismatch = _post(client, parent, body, headers={"Idempotency-Key": "something-else"})
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert len(repo.list_child_runs(conn, parent)) == 1
    second = _post(client, parent, {**body, "idempotency_key": "fr-k2"})  # a new key is a new, separate request
    assert second.status_code == 200 and second.json()["run_id"] != first.json()["run_id"]
    assert len(repo.list_child_runs(conn, parent)) == 2


def test_preconditions_invalid_state_budget_and_schema(client, protocol, parent, conn, inline_worker):
    assert _post(client, "run-nope", {"idempotency_key": "x", "budget_gpu_hours": 1}).status_code == 404
    for budget in (0, -1, 0.0):
        r = _post(client, parent, {"idempotency_key": "b", "budget_gpu_hours": budget})
        assert r.status_code == 409 and r.json()["code"] == "BUDGET_INVALID", (budget, r.text)
    for bad in ({"budget_gpu_hours": 1}, {"idempotency_key": "x"}, {"idempotency_key": "x", "budget_gpu_hours": 1,
                                                                    "top_k_per_arm_per_seed": 0},
                {"idempotency_key": "x", "budget_gpu_hours": 1, "unknown": True}):
        r = _post(client, parent, bad)
        assert r.status_code == 422, (bad, r.text)
    running, _ = harness.validate_and_run(client, protocol, key="still-running")
    r = _post(client, running, {"idempotency_key": "r", "budget_gpu_hours": 1})
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATE"
    child = _post(client, parent, {"idempotency_key": "c", "budget_gpu_hours": 1}).json()["run_id"]
    harness.drain(inline_worker)
    r = _post(client, child, {"idempotency_key": "cc", "budget_gpu_hours": 1})  # a child is never a parent
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATE"
    assert repo.list_child_runs(conn, running) == []


def test_no_finalist_without_measured_latency_is_reported_and_logged(client, protocol, inline_worker, conn,
                                                                     monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "unverified_proxy")
    run_id, _ = harness.validate_and_run(client, protocol, key="proxy-only")
    harness.drain(inline_worker)
    # S8-F2: an unverified proxy never satisfies the hard latency constraint, so nothing is promoted past L0
    gated = [e["row"] for e in repo.list_evaluations(conn, run_id) if e["row"].get("gate")]
    assert gated and all(e["latency_source"] == "unverified_proxy" for e in gated)
    assert any(r["gate"]["reason_code"] == "REJECTED_BY_GATE" for r in gated)
    assert not any(r["gate"]["eligible"] for r in gated)
    for row in gated:
        if row["gate"]["reason_code"] in ("PROMOTION_ELIGIBLE", "REJECTED_BY_GATE"):
            src = next(c for c in row["gate"]["checks"] if c["name"] == "latency_source")
            assert src["passed"] is False and row["gate"]["eligible"] is False, row["gate"]
    seq = repo.get_run(conn, run_id)["last_seq"]
    r = _post(client, run_id, {"idempotency_key": "nf", "budget_gpu_hours": 0.25})
    assert r.status_code == 409 and r.json()["code"] == "NO_FINALIST"
    assert r.json()["message"] == "no eligible last-rung finalist; lower rungs are never substituted"
    logs = [e for e in repo.get_events_after(conn, run_id, seq, 100) if e["type"] == "log"]
    assert logs and logs[-1]["payload"]["reason_code"] == "NO_FINALIST"
    assert repo.list_child_runs(conn, run_id) == []


def test_no_finalist_when_only_lower_rungs_exist(client, make_dataset, tmp_roots, inline_worker, conn, measured):
    ds = make_dataset("low", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1, baselines=(),
                                   score_ge=0.99)  # every candidate is rejected at L0: no L1 (last rung) rows
    proto["console"].pop("baseline_budget_gpu_hours", None)
    run_id, _ = harness.validate_and_run(client, proto, key="low-1")
    harness.drain(inline_worker)
    l0 = [e for e in repo.list_evaluations(conn, run_id) if e["status"] == "EVALUATED"]
    assert l0 and {e["row"]["rung"] for e in l0} == {"L0"}
    assert all(e["row"]["latency_source"] == "measured" for e in l0)
    r = _post(client, run_id, {"idempotency_key": "low", "budget_gpu_hours": 0.25})
    assert r.status_code == 409 and r.json()["code"] == "NO_FINALIST"


def test_parent_costs_sum_over_children_and_top_k(client, parent, inline_worker, conn):
    a = _post(client, parent, {"idempotency_key": "a", "budget_gpu_hours": 0.25}).json()
    b = _post(client, parent, {"idempotency_key": "b", "budget_gpu_hours": 0.25, "top_k_per_arm_per_seed": 2}).json()
    assert a["finalist_count"] == 2 and b["finalist_count"] == 4
    assert [f["candidate_id"] for f in repo.get_run(conn, b["run_id"])["finalists"]] == _expected_finalists(conn, parent, 2)
    harness.drain(inline_worker)
    ca, cb = (repo.get_run(conn, x["run_id"])["costs"]["final_gpu_seconds"] for x in (a, b))
    par = repo.get_run(conn, parent)["costs"]
    assert par["final_gpu_seconds"] == pytest.approx(ca + cb) and par["final_gpu_seconds_children"] == pytest.approx(ca + cb)
    updates = [e for e in repo.get_events_after(conn, parent, 0, 10_000) if e["type"] == "run.updated"]
    assert updates[-1]["payload"]["costs"]["final_gpu_seconds"] == pytest.approx(ca + cb)


def test_final_budget_exhausted_and_failures(client, parent, inline_worker, conn, monkeypatch):
    tiny = _post(client, parent, {"idempotency_key": "tiny", "budget_gpu_hours": 1e-9}).json()
    assert tiny["status"] == "COMPLETED"  # nothing admitted: no job was ever queued
    cands = repo.list_candidates(conn, tiny["run_id"])
    assert {c["candidate_status"] for c in cands} == {"ELIGIBLE_BUDGET_EXHAUSTED"}
    rows = [e["row"] for e in repo.list_evaluations(conn, tiny["run_id"])]
    assert {r["status"] for r in rows} == {"ELIGIBLE_BUDGET_EXHAUSTED"} and all(r["fitness"] is None for r in rows)
    adm = cands[0]["audit"]["orchestration"]["rungs"]["FINAL"]["admission"]
    assert adm["estimate_source"] == "finalist_source_measured_rate" and not adm["admitted"]
    monkeypatch.setenv("MBNAS_STUB_FAIL_FINAL", ".final.")
    failing = _post(client, parent, {"idempotency_key": "fail", "budget_gpu_hours": 0.25}).json()
    harness.drain(inline_worker)
    child = repo.get_run(conn, failing["run_id"])
    assert child["status"] == "FAILED" and child["reason_code"] == "NO_SUCCESSFUL_EVALUATION"
    assert {c["candidate_status"] for c in repo.list_candidates(conn, failing["run_id"])} == {"TRAIN_FAILED"}


def test_pause_resume_cancel_on_the_child(client, parent, inline_worker, conn):
    child = _post(client, parent, {"idempotency_key": "p", "budget_gpu_hours": 0.25}).json()["run_id"]
    r = client.post(f"/api/v1/runs/{child}/pause", headers=auth())
    assert r.status_code == 200 and r.json()["status"] == "PAUSED"  # nothing running yet: straight to PAUSED
    assert harness.drain(inline_worker) == 0  # a paused child leases nothing
    r = client.post(f"/api/v1/runs/{child}/resume", headers=auth())
    assert r.status_code == 200 and r.json()["status"] == "RUNNING", r.text
    harness.drain(inline_worker)
    assert repo.get_run(conn, child)["status"] == "COMPLETED"
    other = _post(client, parent, {"idempotency_key": "q", "budget_gpu_hours": 0.25}).json()["run_id"]
    r = client.post(f"/api/v1/runs/{other}/cancel", headers=auth())
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert {c["candidate_status"] for c in repo.list_candidates(conn, other)} == {"CANCELLED"}
    assert repo.get_run(conn, parent)["status"] == "COMPLETED"


def test_final_split_is_read_only_by_final_retrain_jobs(client, protocol, inline_worker, conn, measured, monkeypatch,
                                                        api_settings):
    """Access-log assertion: the production split_manifests (called by the stub) logs every split read per run."""
    monkeypatch.setenv("MBNAS_STUB_REAL_SPLITS", "1")
    for key, value in (("MBNAS_RUNNER_WORK_ROOT", api_settings.output_root), ("MBNAS_CORE_DB_PATH", api_settings.db_path),
                       ("MBNAS_RUNNER_OUTPUT_ROOTS", ",".join(api_settings.output_roots)),
                       ("MBNAS_RUNNER_DATASET_ROOTS", ",".join(api_settings.dataset_roots))):  # contract §5 child env
        monkeypatch.setenv(key, value)
    run_id, _ = harness.validate_and_run(client, protocol, key="access-1")
    harness.drain(inline_worker)
    from mbnas_core.artifacts.registry import run_dir

    search_log = read_split_access_log(run_dir(repo.get_run(conn, run_id)))
    assert search_log and {e["split"] for e in search_log} == {"train", "search_val"}  # never final_eval
    assert {e["job_kind"] for e in search_log} == {"BASELINE_TRAIN", "CANDIDATE_TRAIN"}
    child = _post(client, run_id, {"idempotency_key": "acc", "budget_gpu_hours": 0.25}).json()["run_id"]
    harness.drain(inline_worker)
    final_log = read_split_access_log(run_dir(repo.get_run(conn, child)))
    final_reads = [e for e in final_log if e["split"] == "final_eval"]
    assert len(final_reads) == 2 and {(e["job_kind"], e["arm"]) for e in final_reads} == {("FINAL_RETRAIN", "Final")}
    assert {e["job_kind"] for e in final_log} == {"FINAL_RETRAIN"}
    assert read_split_access_log(run_dir(repo.get_run(conn, run_id))) == search_log  # parent untouched


def test_engine_rejects_final_split_on_search_rows(client, protocol, inline_worker, conn, measured):
    """A search/baseline evaluation that reports the final split hash is EVAL_FAILED FINAL_SPLIT_LEAK, not a point."""
    run_id, _ = harness.validate_and_run(client, protocol, key="leak-1")
    while True:
        assert inline_worker.tick()
        train = [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["status"] == "SUCCEEDED"]
        if train:
            break
    template = train[0]["result"]["outputs"]["evaluation"]
    queued = next(j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["status"] == "QUEUED")
    final_hash = repo.get_run(conn, run_id)["resolved_protocol"]["dataset"]["final_evaluation_manifest_hash"]
    with tx(conn):
        row = engine.commit_evaluation(conn, engine.context(conn, run_id), repo.get_job(conn, queued["id"]),
                                       {**template, "artifacts": [], "split_hash": final_hash})
    assert row["status"] == "EVAL_FAILED" and row["reason_code"] == "FINAL_SPLIT_LEAK" and row["fitness"] is None
