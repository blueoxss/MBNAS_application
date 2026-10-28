"""GPU-second cost ledger.

One ledger row per finished job attempt that occupied a GPU: ``gpu_seconds = (finished_at - started_at) x gpus``,
always ``physical=1``. Row ``kind``: ``train`` (training work kept: success, a pause at a safe boundary, or the
continuation after a pause), ``retry`` (a successful re-execution after an earlier failed/lost attempt of the same
job), ``val`` (EVALUATE), ``profile`` (BENCHMARK), ``failed`` (a failed,
cancelled or lost attempt; the lost partial work of a crashed worker is billed here; a lost-lease finalize after
another worker's reclaim extends that same row, :func:`record_lost_attempt`). GA/IndependentRandom rows
also carry ``billed_arm`` (failed and retry included, profile excluded); Baseline and Final rows are never billed
to a search arm. ``Run.costs`` is :func:`repo.aggregate_costs`: a key exists only once something was measured.

Inline device profiling: training jobs and EVALUATE measure latency inside the same attempt
(``outputs.evaluation.timings.profile_s``). The worker passes those timings to :func:`record_attempt`, which carves
``profile_s x gpus`` (clamped to the attempt interval) out of the ``train``/``retry``/``val`` row into a second row
``kind='profile'`` (same job/attempt/arm/candidate, ``billed_arm`` NULL, ``meta.source='inline_latency'``). The work
row keeps ``meta.attempt_gpu_seconds`` and ``meta.inline_profile_gpu_seconds``, so ``actual_gpu_seconds`` (all
physical rows) is unchanged while the arm's billed seconds exclude profiling. Failed/cancelled/lost attempts stay one
``failed`` row. Runs recorded before this change keep their profiling inside the ``train`` rows (never rewritten).

Budget overshoot: admission is estimate-based, so a rung can push consumption past its cap. ``Run.costs``
then carries ``overshoot_gpu_seconds.<arm>.seed_<seed>`` (search caps), ``overshoot_gpu_seconds.Baseline`` and
``overshoot_gpu_seconds.Final`` = ``max(0, consumed - cap)`` for every cap that exists and has consumption (a
measured 0.0 when below the cap), plus their sum ``overshoot_gpu_seconds``; consumption is the arm/seed billed
seconds (search) or the arm's physical non-profile seconds (Baseline/Final), the same quantities admission uses.
The attempt whose row crosses a cap logs ``BUDGET_OVERSHOOT`` once (consumption only grows).

FINAL_RETRAIN children bill their work to arm ``Final`` in their own ledger (``final_gpu_seconds`` of the child).
A parent search run's ``final_gpu_seconds`` is its own Final rows plus the sum over its children
(:func:`refresh_run_costs` recomputes the parent whenever a child is refreshed; ``final_gpu_seconds_children`` keeps
the children's part separately). The parent's ``actual_gpu_seconds`` stays its own physical time.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Mapping

from mbnas_core.db import repo
from mbnas_contracts.schemas.jobs import JobKind, TRAINING_KINDS
from mbnas_contracts.util.time import parse_iso
from mbnas_core.scheduling.jobs import gpus_for

SEARCH_ARMS = ("GA", "IndependentRandom")
KEPT_WORK = ("train", "retry")
PROFILE_HOSTS = ("train", "retry", "val")  # rows inline profiling time is carved out of
OVERSHOOT_KEY = "overshoot_gpu_seconds"
OUTCOMES = ("succeeded", "paused", "failed", "cancelled", "lost")


def ledger_kind(job_kind: str, outcome: str, after_failure: bool) -> str:
    """Ledger ``kind`` for one attempt (see module docstring); ``after_failure``: an earlier attempt failed/was lost."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if outcome in ("failed", "cancelled", "lost"):
        return "failed"
    if job_kind == JobKind.EVALUATE.value:
        return "val"
    if job_kind == JobKind.BENCHMARK.value:
        return "profile"
    return "retry" if outcome == "succeeded" and after_failure else "train"


def job_arm(conn: sqlite3.Connection, job: dict[str, Any]) -> str:
    """Arm a job's GPU time belongs to: FINAL_RETRAIN -> Final, else the candidate's arm (Baseline default)."""
    if job["kind"] == JobKind.FINAL_RETRAIN.value:
        return "Final"
    cand = repo.get_candidate(conn, job["candidate_id"]) if job.get("candidate_id") else None
    if cand is not None:
        return str(cand["arm"])
    arm = (job.get("spec") or {}).get("arm")
    return arm if arm in ("Baseline", "GA", "IndependentRandom", "Final") else "Baseline"


def _inline_profile_s(timings: Mapping[str, Any] | None) -> float | None:
    value = (timings or {}).get("profile_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def record_attempt(conn: sqlite3.Connection, job: dict[str, Any], *, outcome: str, started_at: str | None,
                   finished_at: str, device_uuid: str | None = None, reason_code: str | None = None,
                   inline_timings: Mapping[str, Any] | None = None) -> int | None:
    """Append the ledger row(s) of one attempt (inside the caller's tx); None for CPU-only jobs or unknown start.

    ``inline_timings`` (the job's ``evaluation.timings``): its ``profile_s`` becomes a separate unbilled ``profile``
    row carved out of a ``train``/``retry``/``val`` row (module docstring). Returns the id of the work row."""
    gpus = gpus_for(job["kind"])
    if gpus == 0 or not started_at:
        return None
    seconds = max(0.0, (parse_iso(finished_at) - parse_iso(started_at)).total_seconds()) * gpus
    after_failure = conn.execute("SELECT 1 FROM cost_ledger WHERE job_id = ? AND kind = 'failed' LIMIT 1",
                                 (job["id"],)).fetchone() is not None
    kind = ledger_kind(job["kind"], outcome, after_failure)
    arm = job_arm(conn, job)
    billed = arm if arm in SEARCH_ARMS and kind != "profile" else None
    spec = job.get("spec") or {}
    meta: dict[str, Any] = {"outcome": outcome, "reason_code": reason_code, "gpus": gpus, "job_kind": job["kind"],
                            "rung": job.get("rung"), "search_seed": spec.get("search_seed")}
    profile_s = _inline_profile_s(inline_timings) if kind in PROFILE_HOSTS else None
    profile = min(seconds, profile_s * gpus) if profile_s is not None else 0.0
    if profile_s is not None:
        meta.update(attempt_gpu_seconds=seconds, inline_profile_gpu_seconds=profile)
    crossing = _crossing_before(conn, job, arm, kind)
    common = dict(run_id=job["run_id"], arm=arm, physical=True, candidate_id=job.get("candidate_id"), job_id=job["id"],
                  attempt=job.get("attempt"), device_uuid=device_uuid, started_at=started_at, finished_at=finished_at)
    row_id = repo.append_cost(conn, kind=kind, gpu_seconds=seconds - profile, billed_arm=billed, meta=meta, **common)
    if profile_s is not None:
        repo.append_cost(conn, kind="profile", gpu_seconds=profile, billed_arm=None, **common, meta={
            "source": "inline_latency", "host_kind": kind, "host_row_id": row_id, "outcome": outcome, "gpus": gpus,
            "job_kind": job["kind"], "rung": job.get("rung"), "search_seed": spec.get("search_seed"),
            "profile_s": profile_s, "val_s": (inline_timings or {}).get("val_s"),
            "note": "inline latency profiling inside the attempt interval; physical, never billed to a search arm"})
    if crossing is not None:
        _log_overshoot(conn, job, crossing)
    return row_id


def record_lost_attempt(conn: sqlite3.Connection, job: dict[str, Any], *, started_at: str | None, finished_at: str,
                        device_uuid: str | None = None, reason_code: str = "LEASE_LOST") -> int | None:
    """Bill an attempt whose owner found its lease lost when its child finished.

    When another worker already reclaimed the expired lease it billed the attempt as a ``failed`` row from its start
    to its last evidence of life. The owner's child really ran until ``finished_at``, so that row is *extended* in
    place to the full attempt interval (``max`` of both, never shorter) instead of appending a second row for the
    same physical seconds: one physical ledger row per (job, attempt), ``actual_gpu_seconds`` == the attempt interval.
    The row keeps its id; ``meta`` records the reclaim's reason and window (``reclaimed_*``). Without such a row this
    is :func:`record_attempt` with outcome ``lost``."""
    gpus = gpus_for(job["kind"])
    attempt = job.get("attempt")
    existing = repo.find_cost(conn, job["id"], attempt, "failed") if gpus and attempt is not None else None
    if existing is None or not started_at:
        return record_attempt(conn, job, outcome="lost", started_at=started_at, finished_at=finished_at,
                              device_uuid=device_uuid, reason_code=reason_code)
    start = existing.get("started_at") or started_at
    full = max(0.0, (parse_iso(finished_at) - parse_iso(start)).total_seconds()) * gpus
    if full <= float(existing["gpu_seconds"]):
        return int(existing["id"])
    meta = dict(existing.get("meta") or {})
    meta.update(reclaimed_reason_code=meta.get("reason_code"), reclaimed_finished_at=existing.get("finished_at"),
                reclaimed_gpu_seconds=float(existing["gpu_seconds"]), reason_code=reason_code, outcome="lost",
                extended_by="owner_lost_lease_finalize")
    repo.update_cost(conn, int(existing["id"]), gpu_seconds=full, finished_at=finished_at, meta=meta)
    return int(existing["id"])


def _cap_and_consumed(conn: sqlite3.Connection, run: dict[str, Any], arm: str,
                      seed: int | None) -> tuple[float | None, float]:
    """(cap, consumption) of the budget an arm's work draws on (same quantities as admission)."""
    budget = run.get("budget") or {}
    if arm in SEARCH_ARMS:
        return budget.get("search_per_arm_seed_s"), billed_seconds(conn, run["id"], arm, seed)
    cap = budget.get("baseline_s") if arm == "Baseline" else budget.get("final_s") if arm == "Final" else None
    return cap, arm_budget_seconds(conn, run["id"], arm)


def _crossing_before(conn: sqlite3.Connection, job: dict[str, Any], arm: str, kind: str) -> dict[str, Any] | None:
    """Cap/consumption before this attempt's row when its arm has a cap not yet exceeded (else None)."""
    if kind == "profile" or arm not in (*SEARCH_ARMS, "Baseline", "Final"):
        return None
    run = repo.get_run(conn, job["run_id"])
    if run is None:
        return None
    seed = None
    if arm in SEARCH_ARMS:
        cand = repo.get_candidate(conn, job["candidate_id"]) if job.get("candidate_id") else None
        seed = cand.get("search_seed") if cand else None
    cap, before = _cap_and_consumed(conn, run, arm, seed)
    if cap is None or before > cap:
        return None
    return {"run": run, "arm": arm, "seed": seed, "cap": float(cap), "before": before}


def _log_overshoot(conn: sqlite3.Connection, job: dict[str, Any], crossing: dict[str, Any]) -> None:
    cap, after = _cap_and_consumed(conn, crossing["run"], crossing["arm"], crossing["seed"])
    if after <= crossing["cap"]:
        return
    from mbnas_core.orchestration import engine

    arm, seed = crossing["arm"], crossing["seed"]
    scope = f"{arm} seed {seed}" if arm in SEARCH_ARMS else arm
    engine.log(conn, job["run_id"], f"{scope}: budget overshoot {after - crossing['cap']:.3f} GPU-s "
               f"(billed {after:.3f} > cap {crossing['cap']:.3f}) by job {job['id']}", level="warning",
               reason_code="BUDGET_OVERSHOOT", arm=arm, search_seed=seed, job_id=job["id"],
               candidate_id=job.get("candidate_id"), attempt=job.get("attempt"), cap_s=crossing["cap"],
               billed_before_s=crossing["before"], billed_s=after, overshoot_s=after - crossing["cap"])


def overshoot(conn: sqlite3.Connection, run: dict[str, Any]) -> dict[str, float]:
    """``overshoot_gpu_seconds.*`` keys of ``Run.costs`` (module docstring); empty when no capped work was billed."""
    out: dict[str, float] = {}
    pairs = conn.execute(
        "SELECT DISTINCT c.billed_arm, k.search_seed FROM cost_ledger c JOIN candidates k ON k.id = c.candidate_id "
        "WHERE c.run_id = ? AND c.billed_arm IS NOT NULL ORDER BY c.billed_arm, k.search_seed", (run["id"],)).fetchall()
    for arm, seed in pairs:
        cap, used = _cap_and_consumed(conn, run, arm, seed)
        if cap is not None:
            out[f"{OVERSHOOT_KEY}.{arm}.seed_{seed}"] = max(0.0, used - float(cap))
    for arm in ("Baseline", "Final"):
        has = conn.execute("SELECT 1 FROM cost_ledger WHERE run_id = ? AND arm = ? AND physical = 1 "
                           "AND kind != 'profile' LIMIT 1", (run["id"], arm)).fetchone()
        cap, used = _cap_and_consumed(conn, run, arm, None)
        if has and cap is not None:
            out[f"{OVERSHOOT_KEY}.{arm}"] = max(0.0, used - float(cap))
    if out:
        out[OVERSHOOT_KEY] = sum(out.values())
    return out


def children_final_seconds(conn: sqlite3.Connection, run_id: str) -> float | None:
    """Physical Final GPU seconds billed by the FINAL_RETRAIN children of ``run_id`` (None when none measured)."""
    n, total = conn.execute(
        "SELECT COUNT(*), SUM(c.gpu_seconds) FROM cost_ledger c JOIN runs r ON r.id = c.run_id "
        "WHERE r.parent_run_id = ? AND c.physical = 1 AND c.arm = 'Final'", (run_id,)).fetchone()
    return float(total) if n else None


def run_costs(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """``Run.costs`` of one run: its ledger aggregate plus its children's Final seconds (see module docstring)."""
    costs = repo.aggregate_costs(conn, run_id)
    run = repo.get_run(conn, run_id)
    if run is not None:
        costs.update(overshoot(conn, run))
    children = children_final_seconds(conn, run_id)
    if children is not None:
        costs["final_gpu_seconds"] = costs.get("final_gpu_seconds", 0.0) + children
        costs["final_gpu_seconds_children"] = children
    return costs


def refresh_run_costs(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """Recompute ``runs.costs`` (inside the caller's tx) and return it; a child also refreshes its parent, which
    emits ``run.updated`` when its ``final_gpu_seconds`` changed."""
    costs = run_costs(conn, run_id)
    run = repo.update_run_fields(conn, run_id, costs=costs)
    parent_id = run.get("parent_run_id")
    if parent_id:
        parent = repo.get_run(conn, parent_id)
        if parent is not None:
            before = (parent.get("costs") or {}).get("final_gpu_seconds")
            parent_costs = run_costs(conn, parent_id)
            parent = repo.update_run_fields(conn, parent_id, costs=parent_costs)
            if parent_costs.get("final_gpu_seconds") != before:
                from mbnas_core.events.bus import emit
                from mbnas_core.orchestration.views import run_payload

                emit(conn, parent_id, "run.updated", run_payload(parent))
    return costs


def billed_seconds(conn: sqlite3.Connection, run_id: str, arm: str, search_seed: int | None) -> float:
    """GPU seconds billed to ``arm`` for one search seed (failed/retry included)."""
    row = conn.execute(
        "SELECT SUM(c.gpu_seconds) FROM cost_ledger c JOIN candidates k ON k.id = c.candidate_id "
        "WHERE c.run_id = ? AND c.billed_arm = ? AND k.search_seed IS ?", (run_id, arm, search_seed)).fetchone()
    return float(row[0] or 0.0)


def arm_physical_seconds(conn: sqlite3.Connection, run_id: str, arm: str) -> float:
    """Physical GPU seconds of one non-search arm (Baseline / Final)."""
    row = conn.execute("SELECT SUM(gpu_seconds) FROM cost_ledger WHERE run_id = ? AND arm = ? AND physical = 1",
                       (run_id, arm)).fetchone()
    return float(row[0] or 0.0)


def arm_budget_seconds(conn: sqlite3.Connection, run_id: str, arm: str) -> float:
    """Seconds of one non-search arm (Baseline / Final) drawn from its per-run cap: physical, profile excluded."""
    row = conn.execute("SELECT SUM(gpu_seconds) FROM cost_ledger WHERE run_id = ? AND arm = ? AND physical = 1 "
                       "AND kind != 'profile'", (run_id, arm)).fetchone()
    return float(row[0] or 0.0)


def candidate_seconds(conn: sqlite3.Connection, candidate_id: str) -> dict[str, float]:
    """Per-kind GPU seconds of one candidate (only kinds that were measured)."""
    rows = conn.execute("SELECT kind, SUM(gpu_seconds) FROM cost_ledger WHERE candidate_id = ? GROUP BY kind",
                        (candidate_id,)).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def seconds_per_epoch(conn: sqlite3.Connection, candidate_id: str) -> float | None:
    """Measured training seconds per epoch of a candidate (kept work / epochs of its latest committed rung)."""
    kinds = ", ".join(f"'{k.value}'" for k in TRAINING_KINDS)
    seconds = conn.execute(
        f"SELECT SUM(gpu_seconds) FROM cost_ledger WHERE candidate_id = ? AND kind IN ('train', 'retry') "
        f"AND json_extract(meta_json, '$.job_kind') IN ({kinds})", (candidate_id,)).fetchone()[0]
    epochs = conn.execute("SELECT MAX(epochs) FROM evaluations WHERE candidate_id = ? AND status = 'EVALUATED'",
                          (candidate_id,)).fetchone()[0]
    if not seconds or not epochs:
        return None
    return float(seconds) / float(epochs)
