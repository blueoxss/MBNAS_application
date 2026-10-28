"""Evaluation payload -> source-rung fitness + candidate-local gate (protocol.fitness / protocol.promotion)."""
from __future__ import annotations

import pytest

from mbnas_core.protocol.fitness_gate import fitness_and_gate
from mbnas_core.protocol.presets import default_protocol, pilot_smoke_protocol


def payload(**kw) -> dict:  # the real evaluator always writes latency_source=measured (evaluation/handlers.py)
    return {"ap": 0.30, "recall": 0.50, "latency_ms": 20.0, "size_mb": 5.0, "latency_source": "measured", **kw}


def test_fitness_uses_source_rung_weights_and_fixed_anchors() -> None:
    proto = default_protocol()  # anchors 20-200 ms, 5-100 MB; L0 weights .2/.1/.4/.3
    out = fitness_and_gate(proto, "L0", payload())
    assert out["fitness"] == pytest.approx(0.2 * 0.3 + 0.1 * 0.5 + 0.4 * 1.0 + 0.3 * 1.0)
    assert out["fitness_weights"] == proto["rungs"][0]["fitness_weights"]
    assert out["gate"]["reason_code"] == "PROMOTION_ELIGIBLE" and out["gate"]["promote_to"] == "L1"
    l1 = fitness_and_gate(proto, "L1", payload())
    assert l1["fitness"] != out["fitness"]  # different rung -> different named weights
    # S8-F2: the same metrics without a measured latency source never pass the hard latency constraint
    for source in (None, "unverified_proxy", "calibrated_proxy"):
        proxy = fitness_and_gate(proto, "L0", payload(latency_source=source))
        assert proxy["fitness"] == out["fitness"] and proxy["gate"]["reason_code"] == "REJECTED_BY_GATE"


def test_gate_rejects_and_invalid_is_not_zero() -> None:
    proto = default_protocol()
    slow = fitness_and_gate(proto, "L0", payload(latency_ms=150.0))  # hard constraint 100 ms
    assert slow["gate"]["eligible"] is False and slow["gate"]["reason_code"] == "REJECTED_BY_GATE"
    missing = fitness_and_gate(proto, "L0", payload(ap=None))
    assert missing["fitness"] is None and missing["gate"]["reason_code"] == "EVAL_INVALID"
    nan = fitness_and_gate(proto, "L0", payload(latency_ms=float("nan")))
    assert nan["fitness"] is None and nan["gate"]["reason_code"] == "EVAL_INVALID"


def test_final_rung_and_unknown_rung() -> None:
    proto = pilot_smoke_protocol()
    assert fitness_and_gate(proto, "L2", payload())["gate"]["reason_code"] == "SEARCH_RUNG_FINAL"
    assert fitness_and_gate(proto, "L9", payload())["fitness"] is None
