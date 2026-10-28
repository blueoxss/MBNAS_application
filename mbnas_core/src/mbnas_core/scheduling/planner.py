"""Generation planning, closure and representatives (PLAN §2 ``scheduling/planner.py``).

Each search candidate records, per rung, one planning state in ``candidates.audit.orchestration.rungs[rung]``:
``pending`` (planned, work queued or running), ``evaluated`` (immutable EVALUATED row committed),
``failed`` (invalid build, train/eval failure, resume blocked, cancelled) or ``not_admitted`` (eligible but the
budget could not admit it). A candidate is *planned* at rung ``r`` when it has a state there: every proposal of
the generation at the first rung, and at a later rung exactly the candidates whose gate at the previous rung said
PROMOTION_ELIGIBLE (admitted or not).

:func:`refresh_generation` recomputes one generation row ``(arm, seed, generation, rung, comparison_group)``
from those states, so the counts invariant ``evaluated + failed + not_admitted + pending = planned`` holds by
construction. Planning is closed at the first rung once the proposals are stored, and at a later rung once no
candidate of the previous rung is pending. ``complete = planning_closed and pending == 0``; the caller emits
``generation.updated`` when an open row changed and ``generation.completed`` only on the transition to complete. Representative rule: highest current-rung fitness,
then lower latency, then candidate id; no representative without a valid measured evaluation.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from mbnas_core.db import repo

PENDING, EVALUATED, FAILED, NOT_ADMITTED = "pending", "evaluated", "failed", "not_admitted"
RUNG_STATES = (PENDING, EVALUATED, FAILED, NOT_ADMITTED)
REPRESENTATIVE_RULE = "current-rung highest fitness → lower latency → stable candidate id"
_COUNT_KEY = {EVALUATED: "evaluated_count", FAILED: "failed_count", NOT_ADMITTED: "not_admitted_count",
              PENDING: "pending_count"}


def orchestration_audit(candidate: dict[str, Any]) -> dict[str, Any]:
    """The engine-owned part of ``candidate.audit`` (other audit keys belong to the search/registry modules)."""
    return dict((candidate.get("audit") or {}).get("orchestration") or {})


def rung_states(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{rung: {"state": ..., ...}}`` of one candidate."""
    return dict(orchestration_audit(candidate).get("rungs") or {})


def set_rung_state(conn: sqlite3.Connection, candidate_id: str, rung: str, state: str, **info: Any) -> dict[str, Any]:
    """Record the planning state of ``candidate_id`` at ``rung`` (inside the caller's tx), keeping other audit keys."""
    if state not in RUNG_STATES:
        raise ValueError(f"unknown rung state {state!r}")
    cand = repo.get_candidate(conn, candidate_id)
    if cand is None:
        raise repo.NotFound(candidate_id)
    audit = dict(cand.get("audit") or {})
    orch = orchestration_audit(cand)
    rungs = dict(orch.get("rungs") or {})
    rungs[rung] = {**(rungs.get(rung) or {}), **info, "state": state}
    orch["rungs"] = rungs
    audit["orchestration"] = orch
    return repo.update_candidate(conn, candidate_id, audit=audit)


def update_orchestration(conn: sqlite3.Connection, candidate_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into ``candidate.audit.orchestration`` (inside the caller's tx)."""
    cand = repo.get_candidate(conn, candidate_id)
    if cand is None:
        raise repo.NotFound(candidate_id)
    audit = dict(cand.get("audit") or {})
    audit["orchestration"] = {**orchestration_audit(cand), **values}
    return repo.update_candidate(conn, candidate_id, audit=audit)


def select_representative(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Representative among EVALUATED rows with a finite fitness and latency (see module docstring)."""
    valid = [r for r in rows if r.get("status") == "EVALUATED" and isinstance(r.get("fitness"), (int, float))
             and isinstance(r.get("latency_ms"), (int, float))]
    if not valid:
        return None
    return min(valid, key=lambda r: (-float(r["fitness"]), float(r["latency_ms"]), str(r["candidate_id"])))


def _generation_candidates(conn: sqlite3.Connection, run_id: str, arm: str, seed: int, generation: int) -> list[dict[str, Any]]:
    ids = conn.execute("SELECT id FROM candidates WHERE run_id = ? AND arm = ? AND search_seed = ? AND generation = ? "
                       "ORDER BY id", (run_id, arm, seed, generation)).fetchall()
    return [repo.get_candidate(conn, r[0]) for r in ids]  # type: ignore[misc]


def _counts(cands: list[dict[str, Any]], rung: str) -> tuple[dict[str, int], list[str]]:
    counts = {"planned_count": 0, **{k: 0 for k in _COUNT_KEY.values()}}
    evaluated_ids: list[str] = []
    for c in cands:
        st = rung_states(c).get(rung)
        if st is None:
            continue
        counts["planned_count"] += 1
        counts[_COUNT_KEY[st["state"]]] += 1
        if st["state"] == EVALUATED and st.get("evaluation_id"):
            evaluated_ids.append(st["evaluation_id"])
    return counts, evaluated_ids


def refresh_generation(conn: sqlite3.Connection, run_id: str, *, arm: str, search_seed: int, generation: int,
                       rung: str, rung_ids: list[str], comparison_group: str) -> tuple[dict[str, Any] | None, bool]:
    """Recompute and upsert one generation row; returns ``(row or None, became_complete)``."""
    cands = _generation_candidates(conn, run_id, arm, search_seed, generation)
    counts, evaluated_ids = _counts(cands, rung)
    idx = rung_ids.index(rung)
    if idx == 0:
        closed = bool(cands)
    else:
        prev_counts, _ = _counts(cands, rung_ids[idx - 1])
        closed = prev_counts["planned_count"] > 0 and prev_counts["pending_count"] == 0
    if counts["planned_count"] == 0 and not closed:
        return None, False
    rows = [r["row"] for r in (repo.get_evaluation(conn, e) for e in evaluated_ids) if r is not None]
    rep = select_representative(rows)
    complete = closed and counts["pending_count"] == 0
    key = repo.generation_group_key(arm, search_seed, generation, rung, comparison_group)
    before = conn.execute("SELECT complete FROM generations WHERE run_id = ? AND group_key = ?", (run_id, key)).fetchone()
    if before is not None and before[0]:
        return _stored(conn, run_id, key), False
    row = {"arm": arm, "search_seed": search_seed, "generation": generation, "rung": rung,
           "comparison_group": comparison_group, "planning_closed": closed, **counts, "complete": complete,
           "representative_candidate_id": rep["candidate_id"] if rep else None,
           "representative_evaluation_id": rep["id"] if rep else None,
           "representative_selection_rule": REPRESENTATIVE_RULE, "representative_rule": REPRESENTATIVE_RULE}
    stored = repo.upsert_generation(conn, run_id, row)
    return stored, complete


def _stored(conn: sqlite3.Connection, run_id: str, key: str) -> dict[str, Any] | None:
    for g in repo.list_generations(conn, run_id):
        if g["group_key"] == key:
            return g
    return None
