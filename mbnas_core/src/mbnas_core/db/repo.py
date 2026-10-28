"""Typed repository functions over the schema in ``schema.sql``.

Conventions
* Every multi-statement write requires an open transaction (``with db.core.tx(conn): ...``); calling one outside
  a transaction raises ``RuntimeError`` so no caller can publish half a state change. The single-statement lease
  operations (``acquire_lease``, ``renew_lease``, ``mark_job_running``) are atomic on their own.
* Rows come back as plain dicts; ``*_json`` columns are decoded to the key without the suffix
  (``costs_json`` -> ``costs``, ``row_json`` -> ``row``).
* ``now`` parameters are fixed-width UTC ISO strings (``util.time.utcnow_iso``); they default to the current time.
* Evaluation rows with ``status=EVALUATED`` are immutable (Python check + SQL triggers); corrections are new rows
  via :func:`supersede_evaluation`. Event ``seq`` is run-local, allocated by incrementing ``runs.last_seq`` in the
  caller's transaction, so it is published only when that transaction commits.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable, Sequence

from mbnas_contracts.schemas.enums import TERMINAL_RUN_STATUSES, RunStatus
from mbnas_core.db.rows import CandidateEvaluation, Event, Generation  # storage rows (owner side), not the public DTO mirrors
from mbnas_contracts.util.time import iso_after, utcnow_iso

RUNNABLE_RUN_STATUSES: tuple[str, ...] = ("QUEUED", "RUNNING", "RESUMING")


class RepoError(Exception):
    """Base class for repository-level contract violations."""


class ImmutableViolation(RepoError):
    """Attempt to overwrite an immutable row (committed EVALUATED evaluation, completed generation)."""


class IdempotencyConflict(RepoError):
    """Same idempotency key reused with a different payload."""


class InvalidTransition(RepoError):
    """Status change not allowed (e.g. leaving a terminal run state)."""


class NotFound(RepoError, KeyError):
    """Referenced row does not exist."""


# -- helpers -----------------------------------------------------------------------------------------------------
def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    out: dict[str, Any] = {}
    for key in r.keys():
        value = r[key]
        if key.endswith("_json"):
            out[key[:-5]] = json.loads(value) if value is not None else None
        else:
            out[key] = value
    return out


def _rows(rs: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]  # type: ignore[misc]


def _require_tx(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("repository writes must run inside db.core.tx(conn)")


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    _require_tx(conn)
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values)


def _update(conn: sqlite3.Connection, table: str, key: dict[str, Any], values: dict[str, Any]) -> int:
    _require_tx(conn)
    sets = ", ".join(f"{k} = :v_{k}" for k in values)
    where = " AND ".join(f"{k} = :k_{k}" for k in key)
    params = {**{f"v_{k}": v for k, v in values.items()}, **{f"k_{k}": v for k, v in key.items()}}
    return conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", params).rowcount


def _get(conn: sqlite3.Connection, table: str, **key: Any) -> dict[str, Any] | None:
    where = " AND ".join(f"{k} = :{k}" for k in key)
    return _row(conn.execute(f"SELECT * FROM {table} WHERE {where}", key).fetchone())


def _must(row: dict[str, Any] | None, what: str) -> dict[str, Any]:
    if row is None:
        raise NotFound(what)
    return row


# -- validations -------------------------------------------------------------------------------------------------
def insert_validation(conn: sqlite3.Connection, *, id: str, protocol_hash: str, resolved: dict[str, Any],
                      requested_sha256: str, capabilities: dict[str, Any] | None = None,
                      dataset_manifest_hashes: dict[str, Any] | None = None, now: str | None = None) -> dict[str, Any]:
    now = now or utcnow_iso()
    _insert(conn, "validations", {
        "id": id, "protocol_hash": protocol_hash, "resolved_json": _dump(resolved), "requested_sha256": requested_sha256,
        "capabilities_json": _dump(capabilities or {}), "dataset_manifest_hashes_json": _dump(dataset_manifest_hashes or {}),
        "created_at": now, "updated_at": now})
    return _must(get_validation(conn, id), id)


def get_validation(conn: sqlite3.Connection, validation_id: str) -> dict[str, Any] | None:
    return _get(conn, "validations", id=validation_id)


# -- runs --------------------------------------------------------------------------------------------------------
def create_run(conn: sqlite3.Connection, *, id: str, namespace: str, protocol_hash: str, resolved_protocol: dict[str, Any],
               requested_protocol: dict[str, Any], execution_mode: str, idempotency_key: str, payload_sha256: str,
               purpose: str | None = None, preset: str | None = None, validation_id: str | None = None,
               budget: dict[str, Any] | None = None, source_commit: str | None = None,
               status: str = RunStatus.QUEUED.value, kind: str = "SEARCH", parent_run_id: str | None = None,
               finalists: Sequence[dict[str, Any]] = (), now: str | None = None) -> tuple[dict[str, Any], bool]:
    """Idempotent run creation: returns ``(run, created)``.

    Same ``idempotency_key`` + same ``payload_sha256`` returns the existing run (``created=False``); a different
    payload raises :class:`IdempotencyConflict` (API maps it to 409). ``kind`` is ``SEARCH`` or ``FINAL_RETRAIN``
    (a child of ``parent_run_id`` carrying its frozen ``finalists``).
    """
    _require_tx(conn)
    existing = get_run_by_idempotency_key(conn, idempotency_key)
    if existing is not None:
        if existing["payload_sha256"] != payload_sha256:
            raise IdempotencyConflict(f"idempotency key {idempotency_key!r} was used with a different payload")
        return existing, False
    now = now or utcnow_iso()
    _insert(conn, "runs", {
        "id": id, "namespace": namespace, "status": RunStatus(status).value, "protocol_hash": protocol_hash,
        "resolved_protocol_json": _dump(resolved_protocol), "requested_protocol_json": _dump(requested_protocol),
        "execution_mode": execution_mode, "purpose": purpose, "preset": preset, "idempotency_key": idempotency_key,
        "payload_sha256": payload_sha256, "validation_id": validation_id, "budget_json": _dump(budget or {}),
        "source_commit": source_commit, "terminal": int(RunStatus(status) in TERMINAL_RUN_STATUSES),
        "kind": kind, "parent_run_id": parent_run_id, "finalists_json": _dump(list(finalists)),
        "created_at": now, "updated_at": now})
    return _must(get_run(conn, id), id), True


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    return _get(conn, "runs", id=run_id)


def get_run_by_idempotency_key(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    return _get(conn, "runs", idempotency_key=key)


def list_runs(conn: sqlite3.Connection, namespace: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if namespace is None:
        q = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, id LIMIT ?", (limit,))
    else:
        q = conn.execute("SELECT * FROM runs WHERE namespace = ? ORDER BY created_at DESC, id LIMIT ?", (namespace, limit))
    return _rows(q)


def list_child_runs(conn: sqlite3.Connection, parent_run_id: str) -> list[dict[str, Any]]:
    """FINAL_RETRAIN children of a run, oldest first."""
    return _rows(conn.execute("SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at, id", (parent_run_id,)))


def update_run_status(conn: sqlite3.Connection, run_id: str, status: str, *, reason_code: str | None = None,
                      now: str | None = None) -> dict[str, Any]:
    """Set run status and its reason_code (None clears it); leaving a terminal state raises InvalidTransition."""
    run = _must(get_run(conn, run_id), run_id)
    new = RunStatus(status)
    if run["terminal"] and run["status"] != new.value:
        raise InvalidTransition(f"run {run_id} is {run['status']} (terminal); cannot become {new.value}")
    _update(conn, "runs", {"id": run_id}, {"status": new.value, "reason_code": reason_code,
                                           "terminal": int(new in TERMINAL_RUN_STATUSES),
                                           "updated_at": now or utcnow_iso()})
    return _must(get_run(conn, run_id), run_id)


def update_run_fields(conn: sqlite3.Connection, run_id: str, *, costs: dict[str, Any] | None = None,
                      budget: dict[str, Any] | None = None, cancel: dict[str, Any] | None = None,
                      source_commit: str | None = None, now: str | None = None) -> dict[str, Any]:
    """Replace the given JSON fields (None = unchanged)."""
    values: dict[str, Any] = {"updated_at": now or utcnow_iso()}
    for key, val in (("costs_json", costs), ("budget_json", budget), ("cancel_json", cancel)):
        if val is not None:
            values[key] = _dump(val)
    if source_commit is not None:
        values["source_commit"] = source_commit
    if not _update(conn, "runs", {"id": run_id}, values):
        raise NotFound(run_id)
    return _must(get_run(conn, run_id), run_id)


# -- candidates --------------------------------------------------------------------------------------------------
def insert_candidate(conn: sqlite3.Connection, *, id: str, run_id: str, name: str, arm: str, family: str,
                     generation: int = 0, search_seed: int | None = None, training_seed: int | None = None,
                     architecture_hash: str | None = None, architecture_yaml: str | None = None,
                     architecture_path: str | None = None, parents: Sequence[str] = (),
                     parent_evaluation_ids: Sequence[str] = (), lineage: dict[str, Any] | None = None,
                     blocks: Sequence[dict[str, Any]] = (), candidate_status: str = "PROPOSED",
                     current_rung: str | None = None, audit: dict[str, Any] | None = None,
                     now: str | None = None) -> dict[str, Any]:
    """Insert a candidate trial (stable ``id`` across promotions; 0/1/2 parents)."""
    if len(parents) > 2 or len(set(parents)) != len(parents):
        raise ValueError("a candidate has at most two distinct parents")
    now = now or utcnow_iso()
    _insert(conn, "candidates", {
        "id": id, "run_id": run_id, "name": name, "arm": arm, "family": family, "generation": generation,
        "search_seed": search_seed, "training_seed": training_seed, "architecture_hash": architecture_hash,
        "architecture_yaml": architecture_yaml, "architecture_path": architecture_path, "parents_json": _dump(list(parents)),
        "parent_evaluation_ids_json": _dump(list(parent_evaluation_ids)), "lineage_json": _dump(lineage or {}),
        "blocks_json": _dump(list(blocks)), "candidate_status": candidate_status, "current_rung": current_rung,
        "audit_json": _dump(audit or {}), "created_at": now, "updated_at": now})
    return _must(get_candidate(conn, id), id)


def get_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    return _get(conn, "candidates", id=candidate_id)


def list_candidates(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM candidates WHERE run_id = ? ORDER BY created_at, id", (run_id,)))


def update_candidate(conn: sqlite3.Connection, candidate_id: str, *, candidate_status: str | None = None,
                     current_rung: str | None = None, architecture_hash: str | None = None,
                     architecture_path: str | None = None, architecture_yaml: str | None = None,
                     audit: dict[str, Any] | None = None,
                     now: str | None = None) -> dict[str, Any]:
    """Update the mutable candidate fields (None = unchanged)."""
    values: dict[str, Any] = {"updated_at": now or utcnow_iso()}
    for key, val in (("candidate_status", candidate_status), ("current_rung", current_rung),
                     ("architecture_hash", architecture_hash), ("architecture_path", architecture_path),
                     ("architecture_yaml", architecture_yaml)):
        if val is not None:
            values[key] = val
    if audit is not None:
        values["audit_json"] = _dump(audit)
    if not _update(conn, "candidates", {"id": candidate_id}, values):
        raise NotFound(candidate_id)
    return _must(get_candidate(conn, candidate_id), candidate_id)


# -- evaluations -------------------------------------------------------------------------------------------------
def insert_evaluation(conn: sqlite3.Connection, run_id: str, row: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Insert or update a provisional evaluation row; an EVALUATED row becomes immutable.

    ``row`` is a CandidateEvaluation (validated here). Updating an existing immutable row raises
    :class:`ImmutableViolation`.
    """
    _require_tx(conn)
    ev = CandidateEvaluation.model_validate(row).model_dump(mode="json", exclude_none=False)
    ev["revision"] = ev.get("revision") or 1
    existing = get_evaluation(conn, ev["id"])
    if existing is not None and existing["immutable"]:
        raise ImmutableViolation(f"evaluation {ev['id']} is committed (EVALUATED); write a superseding row")
    now = now or utcnow_iso()
    values = {"run_id": run_id, "candidate_id": ev["candidate_id"], "rung": ev["rung"], "epochs": ev["epochs"],
              "status": ev["status"], "revision": ev["revision"],
              "supersedes_evaluation_id": ev.get("supersedes_evaluation_id"), "row_json": _dump(ev),
              "immutable": int(ev["status"] == "EVALUATED"), "updated_at": now}
    if existing is None:
        _insert(conn, "evaluations", {"id": ev["id"], **values, "created_at": now})
    else:
        if existing["run_id"] != run_id or existing["candidate_id"] != ev["candidate_id"]:
            raise ImmutableViolation("an evaluation id cannot move to another run/candidate")
        _update(conn, "evaluations", {"id": ev["id"]}, values)
    return _must(get_evaluation(conn, ev["id"]), ev["id"])


def supersede_evaluation(conn: sqlite3.Connection, old_id: str, new_row: dict[str, Any], *,
                         now: str | None = None) -> dict[str, Any]:
    """Add a correction row superseding ``old_id`` (old row unchanged; revision = old + 1)."""
    old = _must(get_evaluation(conn, old_id), old_id)
    if new_row.get("id") == old_id:
        raise ImmutableViolation("a superseding row needs its own evaluation id")
    if new_row.get("candidate_id") != old["candidate_id"]:
        raise ValueError("a superseding row must belong to the same candidate")
    row = {**new_row, "supersedes_evaluation_id": old_id, "revision": int(old["revision"]) + 1}
    return insert_evaluation(conn, old["run_id"], row, now=now)


def get_evaluation(conn: sqlite3.Connection, evaluation_id: str) -> dict[str, Any] | None:
    return _get(conn, "evaluations", id=evaluation_id)


def list_evaluations(conn: sqlite3.Connection, run_id: str, *, candidate_id: str | None = None,
                     include_superseded: bool = True) -> list[dict[str, Any]]:
    """Evaluation rows of a run (optionally one candidate), oldest first."""
    sql = "SELECT * FROM evaluations e WHERE run_id = :run_id"
    if candidate_id is not None:
        sql += " AND candidate_id = :candidate_id"
    if not include_superseded:
        sql += " AND NOT EXISTS (SELECT 1 FROM evaluations s WHERE s.supersedes_evaluation_id = e.id)"
    sql += " ORDER BY created_at, id"
    return _rows(conn.execute(sql, {"run_id": run_id, "candidate_id": candidate_id}))


# -- generations -------------------------------------------------------------------------------------------------
def generation_group_key(arm: str, search_seed: int, generation: int, rung: str, comparison_group: str) -> str:
    """Contract group key: arm + search_seed + generation + rung + comparison_group."""
    return f"{arm}|{search_seed}|{generation}|{rung}|{comparison_group}"


def upsert_generation(conn: sqlite3.Connection, run_id: str, row: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Insert/update a generation row (Generation contract; counts must sum to planned_count).

    A generation that is already ``complete`` cannot be reopened or changed (:class:`ImmutableViolation`).
    """
    _require_tx(conn)
    gen = Generation.model_validate(row).model_dump(mode="json")
    if gen.get("search_seed") is None:
        raise ValueError("generation rows need search_seed")
    counts = {k: int(gen.get(k) or 0) for k in ("planned_count", "evaluated_count", "failed_count",
                                                  "not_admitted_count", "pending_count")}
    key = generation_group_key(gen["arm"], gen["search_seed"], gen["generation"], gen["rung"], gen["comparison_group"])
    gen["group_key"] = key
    existing = _get(conn, "generations", run_id=run_id, group_key=key)
    if existing is not None and existing["complete"]:
        if existing["row"] == gen:
            return existing
        raise ImmutableViolation(f"generation {key} is complete and cannot change")
    now = now or utcnow_iso()
    values = {"arm": gen["arm"], "search_seed": gen["search_seed"], "generation": gen["generation"], "rung": gen["rung"],
              "comparison_group": gen["comparison_group"], "row_json": _dump(gen),
              "planning_closed": int(bool(gen.get("planning_closed"))), **counts, "complete": int(gen["complete"]),
              "representative_candidate_id": gen.get("representative_candidate_id"),
              "representative_evaluation_id": gen.get("representative_evaluation_id"), "updated_at": now}
    if existing is None:
        _insert(conn, "generations", {"run_id": run_id, "group_key": key, **values, "created_at": now})
    else:
        _update(conn, "generations", {"run_id": run_id, "group_key": key}, values)
    return _must(_get(conn, "generations", run_id=run_id, group_key=key), key)


def get_generation(conn: sqlite3.Connection, run_id: str, group_key: str) -> dict[str, Any] | None:
    """One stored generation row by contract group key (None when not yet planned)."""
    return _get(conn, "generations", run_id=run_id, group_key=group_key)


def list_generations(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM generations WHERE run_id = ? ORDER BY arm, search_seed, generation, rung, comparison_group",
        (run_id,)))


# -- jobs and leases ---------------------------------------------------------------------------------------------
def enqueue_job(conn: sqlite3.Connection, *, id: str, run_id: str, kind: str, spec: dict[str, Any], idempotency_key: str,
                candidate_id: str | None = None, rung: str | None = None, cumulative_target: int | None = None,
                fixed_horizon: int | None = None, priority: int = 100, max_attempts: int = 3,
                now: str | None = None) -> tuple[dict[str, Any], bool]:
    """Queue a job once per ``idempotency_key``: returns ``(job, created)``; a different spec raises IdempotencyConflict."""
    _require_tx(conn)
    existing = _get(conn, "jobs", idempotency_key=idempotency_key)
    if existing is not None:
        if existing["spec"] != json.loads(_dump(spec) or "null"):
            raise IdempotencyConflict(f"job key {idempotency_key!r} reused with a different spec")
        return existing, False
    now = now or utcnow_iso()
    _insert(conn, "jobs", {
        "id": id, "run_id": run_id, "kind": kind, "candidate_id": candidate_id, "rung": rung,
        "cumulative_target": cumulative_target, "fixed_horizon": fixed_horizon, "status": "QUEUED",
        "priority": priority, "attempt": 0, "max_attempts": max_attempts, "spec_json": _dump(spec),
        "idempotency_key": idempotency_key, "created_at": now, "updated_at": now})
    return _must(get_job(conn, id), id), True


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    return _get(conn, "jobs", id=job_id)


def list_jobs(conn: sqlite3.Connection, run_id: str, status: str | None = None) -> list[dict[str, Any]]:
    if status is None:
        return _rows(conn.execute("SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at, id", (run_id,)))
    return _rows(conn.execute("SELECT * FROM jobs WHERE run_id = ? AND status = ? ORDER BY created_at, id",
                              (run_id, status)))


def acquire_lease(conn: sqlite3.Connection, worker_id: str, now: str, ttl: float, *,
                  kinds: Sequence[str] | None = None, generation_only: bool | None = None,
                  runnable_run_statuses: Sequence[str] = RUNNABLE_RUN_STATUSES) -> dict[str, Any] | None:
    """Atomically lease the next job, or return None.

    Candidates: QUEUED jobs, or LEASED/RUNNING jobs whose lease expired, with ``attempt < max_attempts`` and a run
    in ``runnable_run_statuses``; order ``priority, created_at, id`` (lower priority value first). Leasing
    increments ``attempt``. One ``UPDATE ... WHERE id = (subquery) RETURNING`` statement, so two workers can never
    lease the same job.
    """
    params: dict[str, Any] = {"w": worker_id, "now": now, "exp": iso_after(now, ttl)}
    kind_sql = ""
    if kinds:
        kind_sql = " AND j.kind IN (" + ", ".join(f":k{i}" for i in range(len(kinds))) + ")"
        params.update({f"k{i}": k for i, k in enumerate(kinds)})
    if generation_only is not None:
        kind_sql += " AND COALESCE(json_extract(j.spec_json, '$.params.generation_only'), 0) = :generation_only"
        params["generation_only"] = int(generation_only)
    run_sql = ", ".join(f":r{i}" for i in range(len(runnable_run_statuses)))
    params.update({f"r{i}": s for i, s in enumerate(runnable_run_statuses)})
    sql = f"""
        UPDATE jobs SET status = 'LEASED', lease_owner = :w, lease_expires_at = :exp, heartbeat_at = :now,
               attempt = attempt + 1, updated_at = :now
        WHERE id = (
            SELECT j.id FROM jobs j JOIN runs r ON r.id = j.run_id
            WHERE (j.status = 'QUEUED' OR (j.status IN ('LEASED', 'RUNNING') AND j.lease_expires_at < :now))
              AND j.attempt < j.max_attempts AND r.status IN ({run_sql}){kind_sql}
            ORDER BY j.priority, j.created_at, j.id LIMIT 1)
        RETURNING *"""
    return _row(conn.execute(sql, params).fetchone())


def _attempt_sql(attempt: int | None) -> tuple[str, tuple[Any, ...]]:
    """Lease fencing: ``AND attempt = ?`` when an attempt (lease epoch) is given."""
    return ("", ()) if attempt is None else (" AND attempt = ?", (int(attempt),))


def renew_lease(conn: sqlite3.Connection, job_id: str, worker_id: str, now: str, ttl: float, *,
                attempt: int | None = None) -> bool:
    """Extend a lease held by ``worker_id`` (and, when given, at ``attempt``); False if the job is no longer
    leased/running by this worker/attempt."""
    a_sql, a_arg = _attempt_sql(attempt)
    cur = conn.execute(
        "UPDATE jobs SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ? "
        "WHERE id = ? AND lease_owner = ? AND status IN ('LEASED', 'RUNNING')" + a_sql,
        (iso_after(now, ttl), now, now, job_id, worker_id, *a_arg))
    return cur.rowcount == 1


def touch_job(conn: sqlite3.Connection, job_id: str, worker_id: str, now: str, *, attempt: int | None = None) -> bool:
    """Record evidence that the owner's attempt is alive (progress from its child) without extending the lease.

    ``heartbeat_at`` is the end of the billed window when the lease later expires (lost work is billed from
    ``started_at`` to the last evidence of life: a lower bound, never an estimate).
    """
    a_sql, a_arg = _attempt_sql(attempt)
    cur = conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND lease_owner = ? AND status = 'RUNNING' "
                       "AND (heartbeat_at IS NULL OR heartbeat_at < ?)" + a_sql, (now, job_id, worker_id, now, *a_arg))
    return cur.rowcount == 1


def mark_job_running(conn: sqlite3.Connection, job_id: str, worker_id: str, now: str | None = None, *,
                     attempt: int | None = None) -> bool:
    """LEASED -> RUNNING for the lease owner (sets started_at); ``attempt`` fences on the lease epoch."""
    now = now or utcnow_iso()
    a_sql, a_arg = _attempt_sql(attempt)
    cur = conn.execute(
        "UPDATE jobs SET status = 'RUNNING', started_at = COALESCE(started_at, ?), updated_at = ? "
        "WHERE id = ? AND lease_owner = ? AND status = 'LEASED'" + a_sql, (now, now, job_id, worker_id, *a_arg))
    return cur.rowcount == 1


def finish_job(conn: sqlite3.Connection, job_id: str, worker_id: str, status: str, *,
               result: dict[str, Any] | None = None, error: dict[str, Any] | None = None,
               now: str | None = None, attempt: int | None = None) -> bool:
    """Close a job held by ``worker_id`` (at ``attempt`` when given) as SUCCEEDED/FAILED/CANCELLED; False if the
    lease was lost."""
    if status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        raise ValueError(f"finish_job status must be terminal, got {status}")
    _require_tx(conn)
    now = now or utcnow_iso()
    a_sql, a_arg = _attempt_sql(attempt)
    cur = conn.execute(
        "UPDATE jobs SET status = ?, result_json = ?, error_json = ?, finished_at = ?, updated_at = ?, "
        "lease_expires_at = NULL WHERE id = ? AND lease_owner = ? AND status IN ('LEASED', 'RUNNING')" + a_sql,
        (status, _dump(result), _dump(error), now, now, job_id, worker_id, *a_arg))
    return cur.rowcount == 1


def reclaim_expired_leases(conn: sqlite3.Connection, now: str) -> list[dict[str, Any]]:
    """Requeue expired LEASED/RUNNING jobs (or FAIL them when attempts are exhausted); returns the changed jobs."""
    _require_tx(conn)
    expired = _rows(conn.execute(
        "SELECT * FROM jobs WHERE status IN ('LEASED', 'RUNNING') AND lease_expires_at < ? ORDER BY id", (now,)))
    changed = []
    for job in expired:
        info = {"reason_code": "LEASE_EXPIRED", "lease_owner": job["lease_owner"], "attempt": job["attempt"],
                "lease_expires_at": job["lease_expires_at"], "reclaimed_at": now}
        exhausted = job["attempt"] >= job["max_attempts"]
        conn.execute(
            "UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL, error_json = ?, updated_at = ?, "
            "finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE id = ?",
            ("FAILED" if exhausted else "QUEUED",
             _dump({**info, "reason_code": "LEASE_EXPIRED_MAX_ATTEMPTS"} if exhausted else info),
             now, int(exhausted), now, job["id"]))
        changed.append(_must(get_job(conn, job["id"]), job["id"]))
    return changed


def cancel_queued_jobs(conn: sqlite3.Connection, run_id: str, now: str | None = None) -> int:
    """Cancel not-yet-leased jobs of a run; leased/running jobs are stopped by their worker."""
    _require_tx(conn)
    now = now or utcnow_iso()
    return conn.execute("UPDATE jobs SET status = 'CANCELLED', finished_at = ?, updated_at = ? "
                        "WHERE run_id = ? AND status = 'QUEUED'", (now, now, run_id)).rowcount


# -- workers -----------------------------------------------------------------------------------------------------
def upsert_worker(conn: sqlite3.Connection, *, id: str, status: str, device: str, device_uuid: str | None = None,
                  device_name: str | None = None, job_id: str | None = None, run_id: str | None = None,
                  candidate_id: str | None = None, heartbeat_at: str | None = None, row: dict[str, Any] | None = None,
                  now: str | None = None) -> dict[str, Any]:
    """Insert or replace the worker's current state (``row`` = full Worker contract payload)."""
    _require_tx(conn)
    now = now or utcnow_iso()
    values = {"status": status, "device": device, "device_uuid": device_uuid, "device_name": device_name,
              "job_id": job_id, "run_id": run_id, "candidate_id": candidate_id, "heartbeat_at": heartbeat_at or now,
              "row_json": _dump(row or {}), "updated_at": now}
    if get_worker(conn, id) is None:
        _insert(conn, "workers", {"id": id, **values, "created_at": now})
    else:
        _update(conn, "workers", {"id": id}, values)
    return _must(get_worker(conn, id), id)


def get_worker(conn: sqlite3.Connection, worker_id: str) -> dict[str, Any] | None:
    return _get(conn, "workers", id=worker_id)


def list_workers(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict[str, Any]]:
    if run_id is None:
        return _rows(conn.execute("SELECT * FROM workers ORDER BY id"))
    return _rows(conn.execute("SELECT * FROM workers WHERE run_id = ? ORDER BY id", (run_id,)))


def list_workers_for_run(conn: sqlite3.Connection, run_id: str, since: str) -> list[dict[str, Any]]:
    """Workers a run snapshot shows: attached to ``run_id`` (any age), or not ``offline`` with a heartbeat at
    or after ``since`` (alive, or recently lost — readers mark a stale row ``lost``). Historical rows are hidden."""
    return _rows(conn.execute(
        "SELECT * FROM workers WHERE run_id = ? OR (status != 'offline' AND heartbeat_at >= ?) ORDER BY id",
        (run_id, since)))


def prune_workers(conn: sqlite3.Connection, before: str, *, keep: str | None = None) -> int:
    """Delete worker rows whose heartbeat is older than ``before`` (offline, or dead workers that never wrote
    ``offline``), except ``keep``; returns the number of rows removed (inside the caller's tx)."""
    _require_tx(conn)
    cur = conn.execute("DELETE FROM workers WHERE (heartbeat_at IS NULL OR heartbeat_at < ?) AND id != ?",
                       (before, keep or ""))
    return int(cur.rowcount)


# -- events ------------------------------------------------------------------------------------------------------
def append_event(conn: sqlite3.Connection, run_id: str, type: str, payload: dict[str, Any], *,
                 now: str | None = None) -> int:
    """Allocate the next run-local seq (``runs.last_seq + 1``) and store the event inside the caller's tx.

    The event is validated against the contract ``Event`` (typed payload per event type) before anything is
    written; a malformed payload raises pydantic ``ValidationError`` (a ``ValueError``).
    """
    _require_tx(conn)
    Event.model_validate({"seq": 1, "type": type, "run_id": run_id, "payload": payload})
    now = now or utcnow_iso()
    r = conn.execute("UPDATE runs SET last_seq = last_seq + 1, updated_at = ? WHERE id = ? RETURNING last_seq",
                     (now, run_id)).fetchone()
    if r is None:
        raise NotFound(run_id)
    seq = int(r[0])
    _insert(conn, "events", {"run_id": run_id, "seq": seq, "type": type, "payload_json": _dump(payload),
                             "created_at": now})
    return seq


def get_events_after(conn: sqlite3.Connection, run_id: str, after_seq: int, limit: int = 500) -> list[dict[str, Any]]:
    """Events with ``seq > after_seq`` in seq order (contract Event shape plus ``created_at``)."""
    rows = _rows(conn.execute("SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                              (run_id, after_seq, limit)))
    return [{"seq": r["seq"], "type": r["type"], "run_id": r["run_id"], "payload": r["payload"],
             "created_at": r["created_at"]} for r in rows]


def event_bounds(conn: sqlite3.Connection, run_id: str) -> tuple[int | None, int | None]:
    """(min seq, max seq) still stored for a run; (None, None) when there are none."""
    r = conn.execute("SELECT MIN(seq), MAX(seq) FROM events WHERE run_id = ?", (run_id,)).fetchone()
    return r[0], r[1]


# -- artifacts ---------------------------------------------------------------------------------------------------
def insert_artifact(conn: sqlite3.Connection, *, id: str, run_id: str, kind: str, relpath: str,
                    evaluation_id: str | None = None, candidate_id: str | None = None, sha256: str | None = None,
                    mime_type: str | None = None, bytes: int | None = None, sample_id: str | None = None,
                    split_hash: str | None = None, state: str = "NOT_READY", meta: dict[str, Any] | None = None,
                    now: str | None = None) -> dict[str, Any]:
    """Register an artifact by run-relative path (absolute paths and ``..`` are refused)."""
    if os.path.isabs(relpath) or ".." in relpath.replace("\\", "/").split("/") or "\x00" in relpath or not relpath:
        raise ValueError(f"artifact relpath must be relative without '..': {relpath!r}")
    now = now or utcnow_iso()
    _insert(conn, "artifacts", {
        "id": id, "run_id": run_id, "evaluation_id": evaluation_id, "candidate_id": candidate_id, "kind": kind,
        "relpath": relpath, "sha256": sha256, "mime_type": mime_type, "bytes": bytes, "sample_id": sample_id,
        "split_hash": split_hash, "state": state, "meta_json": _dump(meta or {}), "created_at": now, "updated_at": now})
    return _must(get_artifact(conn, run_id, id), id)


def get_artifact(conn: sqlite3.Connection, run_id: str, artifact_id: str) -> dict[str, Any] | None:
    """Artifact scoped to its run (an id from another run returns None)."""
    return _get(conn, "artifacts", id=artifact_id, run_id=run_id)


def list_artifacts(conn: sqlite3.Connection, run_id: str, evaluation_id: str | None = None) -> list[dict[str, Any]]:
    if evaluation_id is None:
        return _rows(conn.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, id", (run_id,)))
    return _rows(conn.execute("SELECT * FROM artifacts WHERE run_id = ? AND evaluation_id = ? ORDER BY kind, id",
                              (run_id, evaluation_id)))


def update_artifact(conn: sqlite3.Connection, run_id: str, artifact_id: str, *, state: str | None = None,
                    sha256: str | None = None, bytes: int | None = None, mime_type: str | None = None,
                    now: str | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {"updated_at": now or utcnow_iso()}
    for key, val in (("state", state), ("sha256", sha256), ("bytes", bytes), ("mime_type", mime_type)):
        if val is not None:
            values[key] = val
    if not _update(conn, "artifacts", {"id": artifact_id, "run_id": run_id}, values):
        raise NotFound(artifact_id)
    return _must(get_artifact(conn, run_id, artifact_id), artifact_id)


def link_artifact(conn: sqlite3.Connection, run_id: str, artifact_id: str, *, evaluation_id: str | None = None,
                  candidate_id: str | None = None, now: str | None = None) -> dict[str, Any]:
    """Attach a pre-registered artifact (runner route E7) to its evaluation/candidate (None = unchanged)."""
    values: dict[str, Any] = {"updated_at": now or utcnow_iso()}
    if evaluation_id is not None:
        values["evaluation_id"] = evaluation_id
    if candidate_id is not None:
        values["candidate_id"] = candidate_id
    if not _update(conn, "artifacts", {"id": artifact_id, "run_id": run_id}, values):
        raise NotFound(artifact_id)
    return _must(get_artifact(conn, run_id, artifact_id), artifact_id)


# -- artifact locations (schema v4) --------------------------------------------------------------------------------
def insert_artifact_location(conn: sqlite3.Connection, *, id: str, object_kind: str, object_id: str, run_id: str,
                             store: str, relpath: str, sha256: str, bytes: int, verified_by: str,
                             runner_id: str | None = None, uri: str | None = None, state: str = "PRESENT",
                             job_id: str | None = None, attempt: int | None = None,
                             now: str | None = None) -> dict[str, Any]:
    """Where the bytes of an artifact/checkpoint row live."""
    now = now or utcnow_iso()
    _insert(conn, "artifact_locations", {
        "id": id, "object_kind": object_kind, "object_id": object_id, "run_id": run_id, "runner_id": runner_id,
        "store": store, "relpath": relpath, "uri": uri, "sha256": sha256, "bytes": bytes, "verified_by": verified_by,
        "verified_at": now, "state": state, "job_id": job_id, "attempt": attempt, "created_at": now, "updated_at": now})
    return _must(_get(conn, "artifact_locations", id=id), id)


def list_artifact_locations(conn: sqlite3.Connection, object_kind: str, object_id: str) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM artifact_locations WHERE object_kind = ? AND object_id = ? "
                              "ORDER BY created_at, id", (object_kind, object_id)))


# -- cost ledger -------------------------------------------------------------------------------------------------
def append_cost(conn: sqlite3.Connection, *, run_id: str, arm: str, kind: str, gpu_seconds: float, physical: bool,
                billed_arm: str | None = None, candidate_id: str | None = None, job_id: str | None = None,
                attempt: int | None = None, device_uuid: str | None = None, started_at: str | None = None,
                finished_at: str | None = None, meta: dict[str, Any] | None = None, now: str | None = None) -> int:
    """Append one ledger row; physical GPU time and arm-billed time are separate rows/flags."""
    now = now or utcnow_iso()
    _insert(conn, "cost_ledger", {
        "run_id": run_id, "arm": arm, "candidate_id": candidate_id, "job_id": job_id, "attempt": attempt, "kind": kind,
        "gpu_seconds": float(gpu_seconds), "physical": int(physical), "billed_arm": billed_arm,
        "device_uuid": device_uuid, "started_at": started_at, "finished_at": finished_at, "meta_json": _dump(meta or {}),
        "created_at": now, "updated_at": now})
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def find_cost(conn: sqlite3.Connection, job_id: str, attempt: int, kind: str) -> dict[str, Any] | None:
    """The first ledger row of ``(job_id, attempt, kind)`` (one physical row per lost attempt)."""
    row = conn.execute("SELECT * FROM cost_ledger WHERE job_id = ? AND attempt = ? AND kind = ? ORDER BY id LIMIT 1",
                       (job_id, attempt, kind)).fetchone()
    return _row(row) if row is not None else None


def update_cost(conn: sqlite3.Connection, row_id: int, *, gpu_seconds: float, finished_at: str,
                meta: dict[str, Any], now: str | None = None) -> None:
    """Extend one ledger row in place (inside the caller's tx); used only to complement a lost attempt's row."""
    _require_tx(conn)
    conn.execute("UPDATE cost_ledger SET gpu_seconds = ?, finished_at = ?, meta_json = ?, updated_at = ? WHERE id = ?",
                 (float(gpu_seconds), finished_at, _dump(meta), now or utcnow_iso(), row_id))


def list_costs(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM cost_ledger WHERE run_id = ? ORDER BY id", (run_id,)))


_COST_SUMS = {
    "ga_billed_gpu_seconds": "billed_arm = 'GA'",
    "random_billed_gpu_seconds": "billed_arm = 'IndependentRandom'",
    "actual_gpu_seconds": "physical = 1",
    "baseline_gpu_seconds": "physical = 1 AND arm = 'Baseline'",
    "final_gpu_seconds": "physical = 1 AND arm = 'Final'",
    "failed_gpu_seconds": "physical = 1 AND kind = 'failed'",
    "retry_gpu_seconds": "physical = 1 AND kind = 'retry'",
    "profile_gpu_seconds": "physical = 1 AND kind = 'profile'",
}


def aggregate_costs(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """Run.costs from the ledger; a key is present only when at least one matching row exists (never a fake 0)."""
    out: dict[str, float] = {}
    for key, cond in _COST_SUMS.items():
        n, total = conn.execute(f"SELECT COUNT(*), SUM(gpu_seconds) FROM cost_ledger WHERE run_id = ? AND {cond}",
                                (run_id,)).fetchone()
        if n:
            out[key] = float(total)
    return out


# -- checkpoints -------------------------------------------------------------------------------------------------
def insert_checkpoint(conn: sqlite3.Connection, *, id: str, run_id: str, candidate_id: str, epoch: int, kind: str,
                      path: str, sha256: str, bytes: int, rung: str | None = None,
                      hashes: dict[str, Any] | None = None, now: str | None = None) -> dict[str, Any]:
    now = now or utcnow_iso()
    _insert(conn, "checkpoints", {
        "id": id, "run_id": run_id, "candidate_id": candidate_id, "rung": rung, "epoch": epoch, "kind": kind,
        "path": path, "sha256": sha256, "bytes": bytes, "hashes_json": _dump(hashes or {}), "created_at": now,
        "updated_at": now})
    return _must(_get(conn, "checkpoints", id=id), id)


def list_checkpoints(conn: sqlite3.Connection, run_id: str, candidate_id: str | None = None,
                     kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM checkpoints WHERE run_id = :run_id"
    if candidate_id is not None:
        sql += " AND candidate_id = :candidate_id"
    if kind is not None:
        sql += " AND kind = :kind"
    return _rows(conn.execute(sql + " ORDER BY epoch, created_at, id",
                              {"run_id": run_id, "candidate_id": candidate_id, "kind": kind}))


def latest_checkpoint(conn: sqlite3.Connection, candidate_id: str, kind: str) -> dict[str, Any] | None:
    """Highest-epoch checkpoint of ``kind`` for a candidate (a PRUNED row is returned as such: callers must refuse
    it, never fall back to an older file or a restart from scratch)."""
    return _row(conn.execute("SELECT * FROM checkpoints WHERE candidate_id = ? AND kind = ? "
                             "ORDER BY epoch DESC, created_at DESC, id DESC LIMIT 1", (candidate_id, kind)).fetchone())


def mark_checkpoint_pruned(conn: sqlite3.Connection, checkpoint_id: str, *, prune: dict[str, Any],
                           now: str | None = None) -> dict[str, Any]:
    """Record that a checkpoint payload was deleted by a retention policy (``state='PRUNED'``; row kept for audit)."""
    now = now or utcnow_iso()
    if not _update(conn, "checkpoints", {"id": checkpoint_id},
                   {"state": "PRUNED", "pruned_at": now, "prune_json": _dump(prune), "updated_at": now}):
        raise NotFound(checkpoint_id)
    return _must(_get(conn, "checkpoints", id=checkpoint_id), checkpoint_id)
