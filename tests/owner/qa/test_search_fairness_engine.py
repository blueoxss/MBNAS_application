"""QA: search-arm fairness checked through the REAL engine with the REAL proposers (synthetic metrics, CPU).

* Random independence: changing the observed metrics (any rung) never changes the IndependentRandom proposal sequence;
  the same perturbation DOES change GA proposals when it touches L0 (so the check is not vacuous), and does NOT when it
  touches only L1 (GA parents come from immutable L0 evaluations only).
* Both arms of a run share one protocol hash, objective hash per rung and comparison group.
* The final_eval split is never requested by search/baseline jobs and never evaluated outside the Final arm.
"""
from __future__ import annotations

import harness
import pytest

from mbnas_core.db import repo


def _sequence(conn, run_id: str, arm: str) -> list[tuple[int, str, str]]:
    cands = [c for c in repo.list_candidates(conn, run_id) if c["arm"] == arm]
    return sorted((c["generation"], c["name"], c["architecture_hash"]) for c in cands)


def test_random_sequence_is_identical_when_metrics_change(run_qa, conn):
    a = run_qa(arms=("IndependentRandom",), key="rnd-a", salts={"L0": "a", "L1": "a"})
    b = run_qa(arms=("IndependentRandom",), key="rnd-b", salts={"L0": "b", "L1": "b"})
    seq_a, seq_b = _sequence(conn, a, "IndependentRandom"), _sequence(conn, b, "IndependentRandom")
    assert len(seq_a) == 9 and {g for g, _, _ in seq_a} == {0, 1, 2}  # 3 generations x population 3
    assert seq_a == seq_b
    metrics = lambda rid: sorted((e["row"]["name"], e["row"]["rung"], e["row"]["fitness"]) for e in repo.list_evaluations(conn, rid))
    assert metrics(a) != metrics(b), "the perturbation did not change any observed metric (vacuous check)"
    assert all(c["parents"] == [] for c in repo.list_candidates(conn, a))


def test_random_sequence_is_independent_of_a_companion_ga_arm(run_qa, conn):
    solo = run_qa(arms=("IndependentRandom",), key="rnd-solo", salts={"L0": "x"})
    both = run_qa(arms=("GA", "IndependentRandom"), key="rnd-both", salts={"L0": "y"})
    assert _sequence(conn, solo, "IndependentRandom") == _sequence(conn, both, "IndependentRandom")


def test_ga_reacts_to_l0_metrics_but_never_to_l1(run_qa, conn):
    base = run_qa(arms=("GA",), key="ga-base", salts={"L0": "a", "L1": "a"})
    l1_only = run_qa(arms=("GA",), key="ga-l1", salts={"L0": "a", "L1": "zzz"})
    l0_changed = run_qa(arms=("GA",), key="ga-l0", salts={"L0": "b", "L1": "a"})
    seq = lambda rid: [(g, h) for g, _, h in _sequence(conn, rid, "GA")]
    later = lambda s: [x for x in s if x[0] >= 1]
    assert seq(base)[:3] == seq(l0_changed)[:3]  # generation 0 is the shared prior
    assert seq(base) == seq(l1_only), "GA proposals changed after only L1 metrics changed (parent fidelity violated)"
    assert later(seq(base)) != later(seq(l0_changed)), "GA ignored L0 fitness (check would be vacuous otherwise)"
    # every GA parent of a later generation is a candidate that has an immutable EVALUATED L0 row
    l0_done = {e["candidate_id"] for e in repo.list_evaluations(conn, base) if e["status"] == "EVALUATED" and e["row"]["rung"] == "L0"}
    for c in repo.list_candidates(conn, base):
        if c["generation"] >= 1:
            assert c["parents"] and set(c["parents"]) <= l0_done
            assert len(c["parents"]) in (1, 2)


def test_both_arms_share_protocol_objective_and_comparison_group(run_qa, conn):
    run_id = run_qa(arms=("GA", "IndependentRandom"), key="fair-1", baselines=("yolo11n",))
    run = repo.get_run(conn, run_id)
    rows = [e["row"] for e in repo.list_evaluations(conn, run_id)]
    assert {r["protocol_hash"] for r in rows} == {run["protocol_hash"]}
    assert {j["protocol_hash"] if "protocol_hash" in j else j["spec"]["protocol_hash"] for j in repo.list_jobs(conn, run_id)} == {run["protocol_hash"]}
    for rung in ("L0", "L1"):
        search = [r for r in rows if r["rung"] == rung and r["arm"] in ("GA", "IndependentRandom")]
        assert {r["arm"] for r in search} == {"GA", "IndependentRandom"}
        assert len({r["objective_hash"] for r in search}) == 1
        assert len({r["split_hash"] for r in search}) == 1 and len({r["comparison_group"] for r in search}) == 1
        assert len({r["epochs"] for r in search}) == 1
        assert len({str(sorted(r["fitness_weights"].items())) for r in search}) == 1
    sizes = {a: sum(1 for c in repo.list_candidates(conn, run_id) if c["arm"] == a and c["generation"] == 0)
             for a in ("GA", "IndependentRandom")}
    assert sizes["GA"] == sizes["IndependentRandom"] == 3  # same population per arm
    planned = {g["arm"]: g["planned_count"] for g in repo.list_generations(conn, run_id) if g["rung"] == "L0" and g["generation"] == 0}
    assert planned == {"GA": 3, "IndependentRandom": 3}


def test_final_eval_split_is_never_used_by_search_jobs(run_qa, conn):
    run_id = run_qa(arms=("GA", "IndependentRandom"), key="final-1", baselines=("yolo11n",))
    resolved = repo.get_run(conn, run_id)["resolved_protocol"]
    search_hash = resolved["dataset"]["search_validation_manifest_hash"]
    final_hash = resolved["dataset"]["final_evaluation_manifest_hash"]
    assert final_hash and final_hash != search_hash
    rows = [e["row"] for e in repo.list_evaluations(conn, run_id)]
    assert rows and {r["split_hash"] for r in rows} == {search_hash}
    kinds = {j["kind"] for j in repo.list_jobs(conn, run_id)}
    assert "FINAL_RETRAIN" not in kinds  # separate trial: never planned inside the search run
    for job in repo.list_jobs(conn, run_id):
        assert (job["spec"].get("params") or {}).get("eval_split", "search_val") == "search_val"
    assert final_hash not in str([r.get("raw_metrics") for r in rows])


@pytest.mark.parametrize("kind,expected", [("CANDIDATE_TRAIN", ("train", "search_val")), ("BASELINE_TRAIN", ("train", "search_val")),
                                           ("FINAL_RETRAIN", ("train", "search_val", "final_eval"))])
def test_training_handler_requests_only_the_splits_its_kind_may_see(kind, expected, monkeypatch, tmp_path):
    """Access log of the REAL training handler: which manifests it asks for (everything after is short-circuited)."""
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec
    from mbnas_runner.training import handlers

    seen: list[tuple[str, ...]] = []
    accesses: list[object] = []

    class Stop(Exception):
        pass

    def spy(protocol, settings, splits, *, access=None):
        seen.append(tuple(splits))
        accesses.append(access)
        raise Stop

    monkeypatch.setattr(handlers, "load_protocol", lambda params, spec: {"console": {"benchmark": {}}})
    monkeypatch.setattr(handlers, "check_run_dir", lambda run_dir, settings: tmp_path)
    monkeypatch.setattr(handlers, "prepare_backend_env", lambda settings: None)
    monkeypatch.setattr(handlers, "resolve_model", lambda params, settings: (None, None, None))
    monkeypatch.setattr(handlers, "split_manifests", spy)
    from mbnas_runner.config import RunnerSettings as Settings  # runner handler: RunnerSettings (DD-A13)

    spec = JobSpec(job_id="j", run_id="r", kind=JobKind(kind), protocol_hash="sha256:x", candidate_id="c", rung="L0",
                   cumulative_target=1, fixed_horizon=2, training_seed=1, arm="Final" if kind == "FINAL_RETRAIN" else "GA",
                   params={"run_dir": str(tmp_path), "adapter_id": "yolo11n", "stage_epochs_added": 1})
    result = handlers.run_training_job(spec, lambda m: None, Settings())
    assert seen == [expected], result
    assert ("final_eval" in seen[0]) is (kind == "FINAL_RETRAIN")
    access = accesses[0]  # every read is attributed to its job in the run's split access log
    assert access is not None and access.job_kind == kind and access.job_id == "j" and access.run_dir == str(tmp_path)


def test_split_manifests_refuses_final_eval_without_a_final_job(tmp_path):
    """The final split is readable only for FINAL_RETRAIN / arm Final work, whatever the caller asks for."""
    from mbnas_runner.config import RunnerSettings as Settings  # runner context: RunnerSettings (DD-A13)
    from mbnas_runner.training.context import JobInputError, SplitAccess, split_manifests

    proto = {"console": {"paths": {"dataset_yaml": str(tmp_path / "missing.yaml")}}}
    for access in (None, SplitAccess(str(tmp_path), "j", "CANDIDATE_TRAIN", "GA", "c", "x"),
                   SplitAccess(str(tmp_path), "j", "EVALUATE", "IndependentRandom", "c", "x")):
        with pytest.raises(JobInputError) as exc:
            split_manifests(proto, Settings(), ("search_val", "final_eval"), access=access)
        assert exc.value.code == "FINAL_SPLIT_FORBIDDEN"
    assert not (tmp_path / "logs" / "split_access.jsonl").exists()  # refused before anything was read


def test_evaluation_handler_refuses_final_split_outside_the_final_arm(tmp_path, monkeypatch):
    from mbnas_runner.config import RunnerSettings as Settings  # runner handler: RunnerSettings (DD-A13)
    from mbnas_runner.evaluation.handlers import run_evaluation_job
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec

    spec = JobSpec(job_id="j", run_id="r", kind=JobKind.EVALUATE, protocol_hash="sha256:x", candidate_id="c", rung="L0",
                   arm="GA", params={"run_dir": str(tmp_path), "adapter_id": "yolo11n", "eval_ckpt": str(tmp_path / "x"),
                                     "resolved_protocol": {}, "eval_split": "final_eval"})
    import mbnas_runner.training.context as context

    monkeypatch.setattr(context, "load_protocol", lambda params, spec: {})
    res = run_evaluation_job(spec, lambda m: None, Settings(output_roots=[str(tmp_path)]))
    assert res.status.value == "FAILED" and res.reason_code == "FINAL_SPLIT_FORBIDDEN"
