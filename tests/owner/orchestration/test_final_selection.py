"""Frozen finalist rule (orchestration.final_retrain.select_finalists) on hand-made immutable rows (synthetic, CPU)."""
from __future__ import annotations

from typing import Any

import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import final_retrain

RESOLVED = {"rungs": [{"id": "L0"}, {"id": "L1"}, {"id": "L2"}],
            "hard_constraints": {"latency_le_ms": 1000, "weights_size_le_MB": 500},
            "final_retraining": {"top_k_per_arm_per_seed": 1, "epoch_target_total": 2, "scheduler_horizon_epochs": 2,
                                 "initialization": "scratch_with_pinned_recipe"}}


@pytest.fixture
def conn(settings):
    c = connect(settings)
    yield c
    c.close()


def _row(cid: str, arm: str, seed: int | None, rung: str, ap: float, lat: float, size: float = 10.0,
         source: str = "measured", **over: Any) -> dict[str, Any]:
    row = {"id": f"{cid}.{rung}.eval", "candidate_id": cid, "name": cid, "family": "f", "arm": arm, "generation": 0,
           "rung": rung, "epochs": 2, "status": "EVALUATED", "ap": ap, "recall": 0.1, "latency_ms": lat, "size_mb": size,
           "fitness": 0.5, "device": "d", "precision": "FP16", "latency_source": source, "parents": [], "blocks": [],
           "architecture_path": None, "checkpoint_path": "/c.pt", "artifacts": [], "objective_hash": "o",
           "evaluation_hash": "e", "split_hash": "s", "latency_scope": "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)",
           "comparison_group": "common_scratch", "protocol_hash": "sha256:p", "batch": 1,
           "resolved_input_shape": [64, 64], "search_seed": seed}
    row.update(over)
    return row


@pytest.fixture
def parent(conn):
    with tx(conn):
        repo.create_run(conn, id="run-p", namespace="t", protocol_hash="sha256:p", resolved_protocol=RESOLVED,
                        requested_protocol={}, execution_mode="PILOT", idempotency_key="k", payload_sha256="x",
                        status="COMPLETED")
        rows = [
            _row("ga-c1", "GA", 101, "L2", 0.5, 10.0),
            _row("ga-c2", "GA", 101, "L2", 0.5, 8.0),   # AP tie with c1 -> lower latency wins
            _row("ga-c3", "GA", 101, "L2", 0.5, 8.0),   # AP + latency tie with c2 -> candidate_id decides
            _row("ga-c0", "GA", 101, "L1", 0.99, 1.0),  # lower rung: never substituted
            _row("ga-c4", "GA", 202, "L2", 0.9, 5.0, source="calibrated_proxy"),  # not measured
            _row("ga-c5", "GA", 202, "L2", 0.2, 50.0),
            _row("rnd-c7", "IndependentRandom", 101, "L2", 0.4, 2000.0),  # latency above the hard constraint
            _row("rnd-c8", "IndependentRandom", 101, "L2", 0.3, 20.0, size=600.0),  # size above the constraint
            _row("base-1", "Baseline", None, "L2", 0.99, 1.0),  # baselines are not search finalists
            _row("rnd-c9", "IndependentRandom", 202, "L2", 0.97, 4.0),  # superseded below by a correction
        ]
        for r in rows:
            repo.insert_candidate(conn, id=r["candidate_id"], run_id="run-p", name=r["name"], arm=r["arm"], family="f",
                                  search_seed=r["search_seed"], candidate_status="SEARCH_COMPLETE")
            repo.insert_evaluation(conn, "run-p", r)
        repo.supersede_evaluation(conn, "rnd-c9.L2.eval", _row("rnd-c9", "IndependentRandom", 202, "L2", 0.1, 4.0,
                                                                id="rnd-c9.L2.eval.r2"))
    return repo.get_run(conn, "run-p")


def test_rule_ties_exclusions_and_supersession(conn, parent) -> None:
    got = final_retrain.select_finalists(conn, parent, top_k=1)
    assert [(f["arm"], f["search_seed"], f["candidate_id"]) for f in got] == [
        ("GA", 101, "ga-c2"), ("GA", 202, "ga-c5"), ("IndependentRandom", 202, "rnd-c9")]
    c9 = got[-1]
    assert c9["source_evaluation_id"] == "rnd-c9.L2.eval.r2" and c9["ap"] == 0.1  # the correction row, never the old one
    rule = got[0]["selection_rule"]
    assert rule.startswith("mbnas-final-select/1") and "last search rung L2" in rule and "latency_ms<=1000" in rule
    assert all(f["selection_rule"] == rule and f["rung"] == "L2" for f in got)


def test_top_k_orders_ties_by_latency_then_candidate_id(conn, parent) -> None:
    got = final_retrain.select_finalists(conn, parent, top_k=3)
    ga101 = [f["candidate_id"] for f in got if (f["arm"], f["search_seed"]) == ("GA", 101)]
    assert ga101 == ["ga-c2", "ga-c3", "ga-c1"]
    assert [f["rank"] for f in got if (f["arm"], f["search_seed"]) == ("GA", 101)] == [1, 2, 3]
    assert not [f for f in got if f["candidate_id"] in ("ga-c0", "ga-c4", "rnd-c7", "rnd-c8", "base-1")]


def test_no_finalist_and_recipe_errors(conn, parent) -> None:
    empty = {**parent, "resolved_protocol": {**RESOLVED, "hard_constraints": {"latency_le_ms": 0.5,
                                                                             "weights_size_le_MB": 500}}}
    assert final_retrain.select_finalists(conn, empty, top_k=1) == []
    with pytest.raises(final_retrain.FinalRetrainError) as exc:
        final_retrain.final_recipe({"final_retraining": {"epoch_target_total": 0}})
    assert exc.value.code == "FINAL_RECIPE_INVALID" and exc.value.status == 422
    recipe = final_retrain.final_recipe(RESOLVED)
    assert (recipe["epoch_target_total"], recipe["scheduler_horizon_epochs"], recipe["init"]) == (2, 2, "scratch")
    assert final_retrain.default_top_k(RESOLVED) == 1 and final_retrain.default_top_k({}) == 1


def test_final_training_seed_follows_the_seed_policy(conn, parent) -> None:
    a, policy = final_retrain.final_training_seed(RESOLVED, 101, "ga-c2")
    assert a == final_retrain.final_training_seed(RESOLVED, 101, "ga-c2")[0]  # deterministic
    assert a != final_retrain.final_training_seed(RESOLVED, 101, "ga-c3")[0]
    b, policy_b = final_retrain.final_training_seed({**RESOLVED, "training": {"seed_policy": {"base_seed": 7}}}, 101, "ga-c2")
    assert b != a and policy_b["base_source"] == "training.seed_policy.base_seed" and policy["base"] == 101
