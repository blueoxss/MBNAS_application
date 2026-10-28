"""protocol.fitness: reward clipping with fixed anchors, named weighted sum, invalid metrics -> None (never 0)."""
from __future__ import annotations

import math

import pytest

from mbnas_core.protocol.fitness import FitnessAnchors, check_weights, fitness, reward_latency, reward_size

W0 = {"ap": 0.2, "recall": 0.1, "latency_reward": 0.4, "size_reward": 0.3}
ANCH = FitnessAnchors(latency_ms=(20.0, 200.0), size_mb=(5.0, 100.0))


@pytest.mark.parametrize("ms,expected", [(10, 1.0), (20, 1.0), (110, 0.5), (200, 0.0), (500, 0.0)])
def test_reward_latency_clip(ms: float, expected: float) -> None:
    assert reward_latency(ms, (20, 200)) == pytest.approx(expected)


def test_reward_size() -> None:
    assert reward_size(52.5, (5, 100)) == pytest.approx(0.5)
    assert reward_size(1, (5, 100)) == 1.0 and reward_size(1000, (5, 100)) == 0.0


def test_fitness_formula() -> None:
    r = fitness(0.3, 0.5, 110.0, 52.5, W0, ANCH)
    assert r.valid and r.reason is None
    assert r.r_T == pytest.approx(0.5) and r.r_S == pytest.approx(0.5)
    assert r.fitness == pytest.approx(0.2 * 0.3 + 0.1 * 0.5 + 0.4 * 0.5 + 0.3 * 0.5)
    assert r.weights == W0 and r.raw["latency_ms"] == 110.0


@pytest.mark.parametrize("args,reason", [
    ((None, 0.5, 10.0, 10.0), "METRIC_MISSING:ap"),
    ((0.3, None, 10.0, 10.0), "METRIC_MISSING:recall"),
    ((0.3, 0.5, None, 10.0), "METRIC_MISSING:latency_ms"),
    ((math.nan, 0.5, 10.0, 10.0), "METRIC_NOT_FINITE:ap"),
    ((0.3, 0.5, math.inf, 10.0), "METRIC_NOT_FINITE:latency_ms"),
    ((1.5, 0.5, 10.0, 10.0), "AP_OUT_OF_RANGE"),
    ((0.3, -0.1, 10.0, 10.0), "RECALL_OUT_OF_RANGE"),
    ((0.3, 0.5, 0.0, 10.0), "LATENCY_NONPOSITIVE"),
    ((0.3, 0.5, 10.0, -2.0), "SIZE_NONPOSITIVE"),
    ((0.3, True, 10.0, 10.0), "METRIC_NOT_FINITE:recall"),
])
def test_invalid_metrics_give_none(args, reason: str) -> None:
    r = fitness(*args, W0, ANCH)
    assert not r.valid and r.fitness is None and r.r_T is None and r.reason == reason


@pytest.mark.parametrize("weights", [
    {"ap": 0.2, "recall": 0.1, "latency_reward": 0.1, "size_reward": 0.03},  # legacy .43 sum
    {"ap": 0.5, "recall": 0.5, "latency_reward": 0.5, "size_reward": -0.5},
    {"ap": 1.0, "recall": 0.0, "latency_reward": 0.0},
    {"ap": 1.0, "recall": 0.0, "latency_reward": 0.0, "size_reward": 0.0, "extra": 0.0},
])
def test_bad_weights_raise(weights) -> None:
    with pytest.raises(ValueError):
        fitness(0.3, 0.5, 10.0, 10.0, weights, ANCH)


def test_check_weights_tolerance() -> None:
    assert check_weights({"ap": 0.6, "recall": 0.1, "latency_reward": 0.2, "size_reward": 0.1 + 5e-7})


def test_anchors_from_protocol_and_validation() -> None:
    a = FitnessAnchors.from_protocol({"latency_bounds_ms": [20, 200], "size_bounds_MB": [5, 100]})
    assert a == ANCH
    with pytest.raises(ValueError):
        FitnessAnchors(latency_ms=(200.0, 20.0), size_mb=(5.0, 100.0))
    with pytest.raises(ValueError):
        FitnessAnchors(latency_ms=(0.0, 20.0), size_mb=(5.0, 100.0))
