"""Named weighted fitness ``F = w_ap*AP + w_recall*R + w_lat*r_T + w_size*r_S`` with fixed shared anchors.

``r_T = 1 - clip((latency_ms - T_min) / (T_max - T_min), 0, 1)`` and ``r_S`` likewise with the size anchors.
Anchors are the protocol's preregistered ``fitness.latency_bounds_ms`` / ``fitness.size_bounds_MB``; they are never
re-derived from observed generations. Inputs are fractions in [0, 1] (AP@[.50:.95], fixed-rule recall).

An evaluation is *invalid* — ``fitness=None``, never 0 — when any metric is missing, NaN/inf, AP or recall is
outside [0, 1], latency <= 0 or size <= 0. Raw values are kept on the result for display.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

WEIGHT_KEYS = ("ap", "recall", "latency_reward", "size_reward")
FORMULA_VERSION = "named_weighted_reward_v1"


@dataclass(frozen=True)
class FitnessAnchors:
    latency_ms: tuple[float, float]
    size_mb: tuple[float, float]

    def __post_init__(self) -> None:
        for name, (lo, hi) in (("latency_ms", self.latency_ms), ("size_mb", self.size_mb)):
            if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
                raise ValueError(f"invalid {name} anchors {lo, hi}: need 0 < min < max")

    @classmethod
    def from_protocol(cls, fitness_section: Mapping[str, Any]) -> "FitnessAnchors":
        """Read ``latency_bounds_ms`` / ``size_bounds_MB`` from a (validated) protocol ``fitness`` section."""
        lat, size = fitness_section["latency_bounds_ms"], fitness_section["size_bounds_MB"]
        return cls(latency_ms=(float(lat[0]), float(lat[1])), size_mb=(float(size[0]), float(size[1])))


@dataclass(frozen=True)
class FitnessResult:
    valid: bool
    fitness: float | None
    r_T: float | None
    r_S: float | None
    reason: str | None
    weights: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _linear_reward(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return 1.0 - min(max((value - lo) / (hi - lo), 0.0), 1.0)


def reward_latency(ms: float, anchors: tuple[float, float]) -> float:
    """Latency reward in [0, 1]: 1 at or below T_min, 0 at or above T_max."""
    return _linear_reward(ms, anchors)


def reward_size(mb: float, anchors: tuple[float, float]) -> float:
    """Size reward in [0, 1] with the fixed decimal-MB anchors."""
    return _linear_reward(mb, anchors)


def check_weights(weights: Mapping[str, float], tolerance: float = 1e-6) -> dict[str, float]:
    """Return the four named weights as floats or raise ``ValueError`` (unknown/missing/negative/sum != 1)."""
    if set(weights) != set(WEIGHT_KEYS):
        raise ValueError(f"weights must be exactly {WEIGHT_KEYS}, got {sorted(weights)}")
    out = {k: float(weights[k]) for k in WEIGHT_KEYS}
    if any(not math.isfinite(v) or v < 0 for v in out.values()):
        raise ValueError("weights must be finite and >= 0")
    if abs(sum(out.values()) - 1.0) > tolerance:
        raise ValueError(f"weights sum to {sum(out.values())}, not 1 (no normalisation)")
    return out


def _invalid_reason(ap: Any, recall: Any, latency_ms: Any, size_mb: Any) -> str | None:
    values = {"ap": ap, "recall": recall, "latency_ms": latency_ms, "size_mb": size_mb}
    for name, v in values.items():
        if v is None:
            return f"METRIC_MISSING:{name}"
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return f"METRIC_NOT_FINITE:{name}"
    if not 0.0 <= ap <= 1.0:
        return "AP_OUT_OF_RANGE"
    if not 0.0 <= recall <= 1.0:
        return "RECALL_OUT_OF_RANGE"
    if latency_ms <= 0:
        return "LATENCY_NONPOSITIVE"
    if size_mb <= 0:
        return "SIZE_NONPOSITIVE"
    return None


def fitness(ap: float | None, recall: float | None, latency_ms: float | None, size_mb: float | None,
            weights: Mapping[str, float], anchors: FitnessAnchors) -> FitnessResult:
    """Compute the named weighted fitness; invalid inputs give ``valid=False, fitness=None``."""
    w = check_weights(weights)
    raw = {"ap": ap, "recall": recall, "latency_ms": latency_ms, "size_mb": size_mb}
    reason = _invalid_reason(ap, recall, latency_ms, size_mb)
    if reason is not None:
        return FitnessResult(False, None, None, None, reason, w, raw)
    r_t = reward_latency(float(latency_ms), anchors.latency_ms)  # type: ignore[arg-type]
    r_s = reward_size(float(size_mb), anchors.size_mb)  # type: ignore[arg-type]
    value = w["ap"] * float(ap) + w["recall"] * float(recall) + w["latency_reward"] * r_t + w["size_reward"] * r_s  # type: ignore[arg-type]
    return FitnessResult(True, value, r_t, r_s, None, w, raw)
