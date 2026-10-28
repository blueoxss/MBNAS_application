"""Budget admission (protocol ``allocation`` section).

Budgets are frozen at run creation in GPU-seconds (:func:`frozen_budget`): one cap **per search arm per search
seed** (``console.search.budget_gpu_hours``), a separate Baseline cap (``console.baseline_budget_gpu_hours``) and
a separate Final cap. An absent cap is never unlimited: work that needs a missing cap is not admitted.

:func:`admit` decides whether one more rung of one candidate may start:

``remaining = cap - billed(arm[, seed]) - reserved`` (billed: the arm/seed billed seconds for search caps, the
arm's physical non-profile seconds for Baseline/Final — inline profiling is never billed, see ``costs``) where ``reserved`` sums the admission estimates of that
arm/seed's jobs that are queued or running and not yet billed (Baseline and Final caps are per run, so their
reservations span the whole arm). ``estimate = seconds_per_epoch x epochs_added`` using the candidate's own measured
rate, else (Final) the measured rate of its finalist source candidate in the parent run, else the mean measured rate
of its arm/seed, else unknown. The job is
admitted when ``remaining >= estimate`` (unknown estimate: when ``remaining > 0``); otherwise the candidate
becomes ``ELIGIBLE_BUDGET_EXHAUSTED`` (eligibility is not admission). Every decision is returned with its inputs so
the caller can record it on the job spec and the candidate.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from mbnas_core.scheduling import costs

SEARCH_ARMS = ("GA", "IndependentRandom")


def _hours_to_s(value: Any) -> float | None:
    return float(value) * 3600.0 if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def frozen_budget(resolved: dict[str, Any]) -> dict[str, Any]:
    """``runs.budget``: caps in GPU-seconds derived from the resolved protocol."""
    console = resolved.get("console", {})
    search = console.get("search", {})
    final = resolved.get("final_retraining", {}) or {}
    return {
        "unit": "gpu_seconds",
        "search_scope": "per_arm_per_seed",
        "search_per_arm_seed_s": _hours_to_s(search.get("budget_gpu_hours")),
        "arms": list(search.get("arms") or []),
        "seeds": list(search.get("seeds") or []),
        "baseline_s": _hours_to_s(console.get("baseline_budget_gpu_hours")),
        "final_s": _hours_to_s(final.get("budget_GPU_hours_separate")),
        "policy": "admit_if_remaining_ge_estimate (estimate = measured seconds/epoch x epochs added); "
                  "unknown estimate admits while remaining > 0; missing cap admits nothing",
    }


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason_code: str
    arm: str
    search_seed: int | None
    cap_s: float | None
    billed_s: float
    reserved_s: float
    remaining_s: float | None
    estimate_s: float | None
    estimate_source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seed_filter(arm: str, seed: int | None) -> tuple[str, tuple[Any, ...]]:
    """Search caps are per arm and seed; Baseline/Final caps are per run (all candidates of the arm)."""
    return (" AND k.search_seed IS ?", (seed,)) if arm in SEARCH_ARMS else ("", ())


def _reserved(conn: sqlite3.Connection, run_id: str, arm: str, seed: int | None) -> float:
    cond, args = _seed_filter(arm, seed)
    rows = conn.execute(
        "SELECT json_extract(j.spec_json, '$.params.admission.estimate_s') FROM jobs j "
        f"JOIN candidates k ON k.id = j.candidate_id WHERE j.run_id = ? AND k.arm = ?{cond} "
        "AND j.status IN ('QUEUED', 'LEASED', 'RUNNING')", (run_id, arm, *args)).fetchall()
    return float(sum(r[0] for r in rows if r[0] is not None))


def _arm_rate(conn: sqlite3.Connection, run_id: str, arm: str, seed: int | None) -> float | None:
    cond, args = _seed_filter(arm, seed)
    ids = [r[0] for r in conn.execute(f"SELECT k.id FROM candidates k WHERE k.run_id = ? AND k.arm = ?{cond}",
                                      (run_id, arm, *args)).fetchall()]
    rates = [r for r in (costs.seconds_per_epoch(conn, cid) for cid in ids) if r is not None]
    return sum(rates) / len(rates) if rates else None


def _estimate(conn: sqlite3.Connection, run_id: str, candidate: dict[str, Any],
              epochs_added: int) -> tuple[float | None, str]:
    own = costs.seconds_per_epoch(conn, candidate["id"])
    if own is not None:
        return own * epochs_added, "candidate_measured_rate"
    source = (candidate.get("lineage") or {}).get("source_candidate_id") if candidate["arm"] == "Final" else None
    source_rate = costs.seconds_per_epoch(conn, source) if source else None
    if source_rate is not None:
        return source_rate * epochs_added, "finalist_source_measured_rate"
    arm_rate = _arm_rate(conn, run_id, candidate["arm"], candidate.get("search_seed"))
    if arm_rate is not None:
        return arm_rate * epochs_added, "arm_seed_mean_measured_rate"
    return None, "unmeasured"


def admit(conn: sqlite3.Connection, run: dict[str, Any], candidate: dict[str, Any], epochs_added: int) -> AdmissionDecision:
    """Admission of ``epochs_added`` more epochs for ``candidate`` (see module docstring)."""
    budget = run.get("budget") or {}
    arm, seed = candidate["arm"], candidate.get("search_seed")
    if arm in SEARCH_ARMS:
        cap, billed = budget.get("search_per_arm_seed_s"), costs.billed_seconds(conn, run["id"], arm, seed)
    elif arm == "Baseline":
        cap, billed = budget.get("baseline_s"), costs.arm_budget_seconds(conn, run["id"], "Baseline")
    else:
        cap, billed = budget.get("final_s"), costs.arm_budget_seconds(conn, run["id"], "Final")
    reserved = _reserved(conn, run["id"], arm, seed)
    estimate, source = _estimate(conn, run["id"], candidate, epochs_added)
    if cap is None:
        return AdmissionDecision(False, "BUDGET_MISSING", arm, seed, None, billed, reserved, None, estimate, source)
    remaining = cap - billed - reserved
    ok = remaining >= estimate if estimate is not None else remaining > 0
    return AdmissionDecision(ok, "ADMITTED" if ok else "BUDGET_EXHAUSTED", arm, seed, cap, billed, reserved,
                             remaining, estimate, source)


def remaining_seconds(conn: sqlite3.Connection, run: dict[str, Any], arm: str, seed: int | None) -> float | None:
    """Unreserved remaining search budget of an arm/seed (None when the cap is missing)."""
    cap = (run.get("budget") or {}).get("search_per_arm_seed_s")
    if cap is None:
        return None
    return cap - costs.billed_seconds(conn, run["id"], arm, seed) - _reserved(conn, run["id"], arm, seed)
