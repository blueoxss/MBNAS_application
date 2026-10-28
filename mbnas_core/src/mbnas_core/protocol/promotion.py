"""Candidate-local promotion gate.

``evaluate_gate`` is a pure function of ONE candidate's evaluation at its source rung and that rung's config:
it never looks at other candidates, so evaluation order cannot change a decision. It enforces that the fitness was
computed with the *source rung's* named weights and from the same raw metrics. Decision codes:

* ``EVAL_INVALID`` — the fitness result is invalid (missing/NaN/out-of-range metrics); never treated as 0.
* ``SEARCH_RUNG_FINAL`` — the source rung has ``promotion: null`` (end of search; finalist logic applies).
* ``PROMOTION_ELIGIBLE`` / ``REJECTED_BY_GATE`` — all / not all checks passed: fitness >= score_ge,
  AP >= ap_ge, latency <= hard latency_le_ms, size <= hard weights_size_le_MB, plus optional per-rung raw guards.

The hard latency constraint is only certified by a trustworthy latency: the ``latency_source`` check passes for
``measured`` and, only when ``hard_constraints.proxy_may_certify_final_deployment is True``, for ``calibrated_proxy``;
``unverified_proxy`` / ``pending`` / missing never pass (no uncalibrated proxy satisfies the
device constraint). This host has no calibrated proxy pipeline, so in practice only measured rows are promoted.

Eligibility is not admission: budget admission and GPU allocation happen later in the scheduler.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from mbnas_core.protocol.fitness import FitnessResult, check_weights


@dataclass(frozen=True)
class GateDecision:
    eligible: bool
    reason_code: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    source_rung: str | None = None
    promote_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(name: str, value: float, threshold: float, ge: bool) -> dict[str, Any]:
    passed = value >= threshold if ge else value <= threshold
    return {"name": name, "passed": bool(passed), "value": value, "threshold": threshold,
            "op": ">=" if ge else "<="}


def _same_metric(a: Any, b: Any) -> bool:
    """Equality that treats two NaNs as the same (an invalid metric must reach the EVAL_INVALID decision, not raise)."""
    return a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b))


def certifying_latency_sources(hard_constraints: Mapping[str, Any]) -> list[str]:
    """Latency sources allowed to satisfy the hard latency constraint under ``hard_constraints``."""
    if hard_constraints.get("proxy_may_certify_final_deployment") is True:
        return ["measured", "calibrated_proxy"]
    return ["measured"]


def _source_check(latency_source: Any, hard_constraints: Mapping[str, Any]) -> dict[str, Any]:
    value = getattr(latency_source, "value", latency_source)  # LatencySource enum or its string value
    allowed = certifying_latency_sources(hard_constraints)
    return {"name": "latency_source", "passed": value in allowed, "value": value, "threshold": allowed, "op": "in"}


def evaluate_gate(fitness_result: FitnessResult, ap: float | None, latency_ms: float | None, size_mb: float | None,
                  rung_cfg: Mapping[str, Any], hard_constraints: Mapping[str, Any], *,
                  latency_source: Any) -> GateDecision:
    """Decide promotion eligibility of one evaluation using only its source rung config (see module docstring)."""
    rung_id = rung_cfg.get("id")
    if fitness_result.weights and fitness_result.weights != check_weights(rung_cfg["fitness_weights"]):
        raise ValueError(f"fitness was not computed with the source rung {rung_id!r} weights")
    given = {"ap": ap, "latency_ms": latency_ms, "size_mb": size_mb}
    if fitness_result.raw and any(not _same_metric(fitness_result.raw.get(k), v) for k, v in given.items()):
        raise ValueError("gate metrics differ from the metrics the fitness was computed from")
    if not fitness_result.valid or fitness_result.fitness is None:
        return GateDecision(False, "EVAL_INVALID",
                            [{"name": "evaluation_valid", "passed": False, "value": fitness_result.reason,
                              "threshold": None}], rung_id)
    promo = rung_cfg.get("promotion")
    if promo is None:
        return GateDecision(False, "SEARCH_RUNG_FINAL", [], rung_id)
    checks = [
        _check("fitness", fitness_result.fitness, float(promo["score_ge"]), ge=True),
        _check("ap", float(ap), float(promo["ap_ge"]), ge=True),  # type: ignore[arg-type]
        _check("latency_ms", float(latency_ms), float(hard_constraints["latency_le_ms"]), ge=False),  # type: ignore[arg-type]
        _source_check(latency_source, hard_constraints),
        _check("size_mb", float(size_mb), float(hard_constraints["weights_size_le_MB"]), ge=False),  # type: ignore[arg-type]
    ]
    if "latency_le_ms" in promo:
        checks.append(_check("rung_latency_ms", float(latency_ms), float(promo["latency_le_ms"]), ge=False))  # type: ignore[arg-type]
    if "weights_size_le_MB" in promo:
        checks.append(_check("rung_size_mb", float(size_mb), float(promo["weights_size_le_MB"]), ge=False))  # type: ignore[arg-type]
    eligible = all(c["passed"] for c in checks)
    return GateDecision(eligible, "PROMOTION_ELIGIBLE" if eligible else "REJECTED_BY_GATE", checks, rung_id,
                        promo.get("to") if eligible else None)
