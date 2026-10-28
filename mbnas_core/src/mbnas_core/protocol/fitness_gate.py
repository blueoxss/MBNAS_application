"""Source-rung fitness and the candidate-local gate decision of one evaluation payload (core policy).

The runner does not compute fitness or gates; ``orchestration.engine.commit_evaluation`` recomputes
them on the core from the raw metrics.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from mbnas_contracts.schemas.jobs import JobKind, JobSpec
from mbnas_core.protocol.fitness import FitnessAnchors, fitness
from mbnas_core.protocol.promotion import evaluate_gate


def fitness_and_gate(protocol: Mapping[str, Any], rung_id: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fitness with the source rung's named weights + fixed anchors, and the candidate-local gate decision."""
    rung = next((r for r in protocol.get("rungs", []) if r.get("id") == rung_id), None)
    if rung is None:
        return {"fitness": None, "fitness_result": None, "gate": None, "reason": f"rung {rung_id!r} not in protocol"}
    anchors = FitnessAnchors.from_protocol(protocol["fitness"])
    raw = {k: payload.get(k) for k in ("ap", "recall", "latency_ms", "size_mb")}
    m = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in raw.items()}  # NaN -> missing
    fr = fitness(m["ap"], m["recall"], m["latency_ms"], m["size_mb"], rung["fitness_weights"], anchors)
    gate = evaluate_gate(fr, m["ap"], m["latency_ms"], m["size_mb"], rung, protocol["hard_constraints"],
                         latency_source=payload.get("latency_source"))
    out = {"fitness": fr.fitness, "fitness_result": fr.as_dict(), "gate": gate.as_dict(),
           "fitness_weights": dict(rung["fitness_weights"]),
           "rewards": {"latency_reward": fr.r_T, "size_reward": fr.r_S}}
    if m != raw:
        out["non_finite_metrics"] = sorted(k for k in raw if raw[k] is not None and m[k] is None)
    return out


def _fitness_rung(protocol: Mapping[str, Any], spec: JobSpec) -> str | None:
    """Rung whose named weights score this evaluation: the job's rung, or the LAST search rung for the final
    evaluation (rung ``FINAL``; display only, never a promotion)."""
    if spec.arm == "Final" or spec.kind == JobKind.FINAL_RETRAIN:
        rungs = protocol.get("rungs") or []
        return rungs[-1]["id"] if rungs else None
    return spec.rung
