"""protocol.promotion: candidate-local gate with source-rung weights only."""
from __future__ import annotations

import itertools

import pytest

from mbnas_core.protocol.fitness import FitnessAnchors, fitness
from mbnas_core.protocol.presets import handoff_protocol
from mbnas_core.protocol.promotion import evaluate_gate

PROTO = handoff_protocol()
RUNGS = PROTO["rungs"]
HARD = PROTO["hard_constraints"]
ANCH = FitnessAnchors.from_protocol(PROTO["fitness"])


def decide(ap, recall, lat, size, rung_index=0, source="measured", hard=HARD):
    rung = RUNGS[rung_index]
    fr = fitness(ap, recall, lat, size, rung["fitness_weights"], ANCH)
    return evaluate_gate(fr, ap, lat, size, rung, hard, latency_source=source)


def test_eligible_and_promote_to() -> None:
    d = decide(0.3, 0.5, 30.0, 10.0)
    assert d.eligible and d.reason_code == "PROMOTION_ELIGIBLE" and d.promote_to == "L1" and d.source_rung == "L0"
    assert {c["name"] for c in d.checks} == {"fitness", "ap", "latency_ms", "latency_source", "size_mb"}
    assert all(c["passed"] for c in d.checks)


@pytest.mark.parametrize("metrics,failed", [
    ((0.01, 0.5, 30.0, 10.0), "ap"),          # AP guard .02
    ((0.0, 0.0, 190.0, 95.0), "fitness"),     # score < .40
    ((0.9, 0.9, 150.0, 10.0), "latency_ms"),  # hard latency 100
    ((0.9, 0.9, 30.0, 90.0), "size_mb"),      # hard size 80
])
def test_rejected_by_gate(metrics, failed: str) -> None:
    d = decide(*metrics)
    assert not d.eligible and d.reason_code == "REJECTED_BY_GATE" and d.promote_to is None
    assert failed in {c["name"] for c in d.checks if not c["passed"]}


def test_invalid_evaluation_is_not_zero() -> None:
    d = decide(None, 0.5, 30.0, 10.0)
    assert d.reason_code == "EVAL_INVALID" and not d.eligible
    assert d.checks[0]["value"] == "METRIC_MISSING:ap"


def test_final_rung_returns_search_rung_final() -> None:
    d = decide(0.9, 0.9, 25.0, 6.0, rung_index=2)
    assert d.reason_code == "SEARCH_RUNG_FINAL" and not d.eligible and d.source_rung == "L2"


def test_source_rung_weights_enforced() -> None:
    fr = fitness(0.3, 0.5, 30.0, 10.0, RUNGS[1]["fitness_weights"], ANCH)  # L1 weights
    with pytest.raises(ValueError, match="source rung"):
        evaluate_gate(fr, 0.3, 30.0, 10.0, RUNGS[0], HARD, latency_source="measured")


def test_metrics_must_match_fitness_inputs() -> None:
    fr = fitness(0.3, 0.5, 30.0, 10.0, RUNGS[0]["fitness_weights"], ANCH)
    with pytest.raises(ValueError, match="differ"):
        evaluate_gate(fr, 0.9, 30.0, 10.0, RUNGS[0], HARD, latency_source="measured")


def test_candidate_local_independence() -> None:
    cands = {"A": (0.3, 0.5, 30.0, 10.0), "B": (0.01, 0.4, 40.0, 20.0), "C": (0.9, 0.9, 150.0, 10.0),
             "D": (None, 0.5, 30.0, 10.0)}
    reference = {k: decide(*m) for k, m in cands.items()}
    for order in itertools.permutations(cands):
        decisions = {}
        for k in order:
            decisions[k] = decide(*cands[k])
        assert decisions == reference
    # B evaluated before and after A yields identical decisions
    b_before = decide(*cands["B"])
    decide(*cands["A"])
    assert decide(*cands["B"]) == b_before


def test_rung_raw_guards() -> None:
    rung = {**RUNGS[0], "promotion": {**RUNGS[0]["promotion"], "latency_le_ms": 25.0}}
    fr = fitness(0.3, 0.5, 30.0, 10.0, rung["fitness_weights"], ANCH)
    d = evaluate_gate(fr, 0.3, 30.0, 10.0, rung, HARD, latency_source="measured")
    assert d.reason_code == "REJECTED_BY_GATE" and [c["name"] for c in d.checks if not c["passed"]] == ["rung_latency_ms"]


# -- S8-F2: the hard latency constraint is certified only by a trustworthy latency source -------------------------
def _source_check(d):
    return next(c for c in d.checks if c["name"] == "latency_source")


def test_latency_source_measured_passes_the_hard_constraint() -> None:
    d = decide(0.3, 0.5, 30.0, 10.0, source="measured")
    assert d.eligible and d.reason_code == "PROMOTION_ELIGIBLE"
    assert _source_check(d) == {"name": "latency_source", "passed": True, "value": "measured",
                                "threshold": ["measured"], "op": "in"}


@pytest.mark.parametrize("flag", [False, None, "yes", 1])
def test_calibrated_proxy_is_rejected_unless_the_protocol_allows_proxy_certification(flag) -> None:
    hard = {k: v for k, v in HARD.items() if k != "proxy_may_certify_final_deployment"}  # flag None = absent
    if flag is not None:
        hard["proxy_may_certify_final_deployment"] = flag
    d = decide(0.3, 0.5, 30.0, 10.0, source="calibrated_proxy", hard=hard)
    assert not d.eligible and d.reason_code == "REJECTED_BY_GATE" and d.promote_to is None
    assert [c["name"] for c in d.checks if not c["passed"]] == ["latency_source"]


def test_calibrated_proxy_passes_only_with_proxy_may_certify_true() -> None:
    hard = {**HARD, "proxy_may_certify_final_deployment": True}
    d = decide(0.3, 0.5, 30.0, 10.0, source="calibrated_proxy", hard=hard)
    assert d.eligible and _source_check(d)["passed"] and _source_check(d)["threshold"] == ["measured", "calibrated_proxy"]


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("source", ["unverified_proxy", "pending", None])
def test_unverified_proxy_never_passes_the_hard_constraint(source, flag) -> None:
    hard = {**HARD, "proxy_may_certify_final_deployment": flag}
    d = decide(0.3, 0.5, 30.0, 10.0, source=source, hard=hard)
    assert not d.eligible and d.reason_code == "REJECTED_BY_GATE"
    assert [c["name"] for c in d.checks if not c["passed"]] == ["latency_source"]


def test_latency_source_enum_is_accepted() -> None:
    from mbnas_contracts.schemas import LatencySource

    assert decide(0.3, 0.5, 30.0, 10.0, source=LatencySource.MEASURED).eligible
    assert not decide(0.3, 0.5, 30.0, 10.0, source=LatencySource.UNVERIFIED_PROXY).eligible


def test_latency_source_is_a_required_argument() -> None:
    fr = fitness(0.3, 0.5, 30.0, 10.0, RUNGS[0]["fitness_weights"], ANCH)
    with pytest.raises(TypeError):
        evaluate_gate(fr, 0.3, 30.0, 10.0, RUNGS[0], HARD)  # type: ignore[call-arg]


def test_fitness_and_gate_passes_the_payload_latency_source() -> None:
    from mbnas_core.protocol.fitness_gate import fitness_and_gate

    base = {"ap": 0.3, "recall": 0.5, "latency_ms": 30.0, "size_mb": 10.0}
    ok = fitness_and_gate(PROTO, "L0", {**base, "latency_source": "measured"})["gate"]
    bad = fitness_and_gate(PROTO, "L0", {**base, "latency_source": "unverified_proxy"})["gate"]
    assert ok["eligible"] and not bad["eligible"]
    assert [c["name"] for c in bad["checks"] if not c["passed"]] == ["latency_source"]
