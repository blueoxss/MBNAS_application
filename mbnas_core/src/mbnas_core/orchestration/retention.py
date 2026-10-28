"""Full-state checkpoint retention (``MBNAS_FULL_STATE_RETENTION`` / resolved ``console.retention.full_state``).

Policies (applied to a candidate once it is terminal for training — ``SEARCH_COMPLETE``, ``REJECTED_BY_GATE``,
``TRAIN_FAILED``, ``EVAL_FAILED``, ``INVALID``, ``FINAL_COMPLETE``, or a Baseline whose last stage is evaluated;
``CANCELLED``/``RESUME_BLOCKED``/``ELIGIBLE_BUDGET_EXHAUSTED``/``PAUSED`` keep everything for audit or a later resume):

* ``keep_all`` (server default) — nothing is deleted;
* ``rung_end_only`` — delete full-state training payloads whose epoch is not a stage (rung) end of the candidate;
* ``last_rung_end_only`` (PILOT_SMOKE preset) — keep only the full state of the candidate's last reached rung end.

Only ``training_full`` payloads are deleted (the ``full_e{E}.pt`` file and, for RF-DETR, the ``native_e{E}.ckpt`` it
references). Sidecars (``*.sha256.json``), exact rung-end EMA evaluation snapshots and FP16 weights-only artifacts
are always kept. Each deletion is recorded on its ``checkpoints`` row (``state='PRUNED'``, ``pruned_at``, ``prune``
{policy, reason, deleted files, bytes}); rows are never removed. The worker applies the run's policy after every job
of a candidate (:func:`apply_for_candidate`) and also records the stage trainer's own pruning of intermediate epochs
(``STAGE_TRAINER_KEEP_NEWEST_AND_RUNG_END``) so those rows read PRUNED, not MISSING. ``python -m
mbnas_core.orchestration.retention --run-id <id> [--policy P]`` applies a policy to a whole run (``--prune-after`` of
``scripts/owner/smoke_end_to_end.py``). Candidate detail renders each row's ``state`` with :func:`checkpoint_views`, and its
``artifact_id`` (the checkpoint id once the gateway store holds verified bytes) with :func:`store_verified_checkpoints`.

Remote runs — the prune round trip (DD-A66; WP-5 section 2 R11; T3-34). The payloads of a run executed by a remote
runner (:func:`is_remote_run`) live on that runner's host: the core never deletes or stats them. Instead the run's OWN
policy is planned over the candidate's runner-held full states (rows with a ``runner_local`` location) and every
deletion becomes a ``prune_requests`` row (:func:`request_remote_prunes`; id = :func:`prune_request_id`, one request per
checkpoint, so re-planning never duplicates one). The plan runs whenever a runner registers a checkpoint (E6,
``engine.checkpoint_registered``), after a job's outcome / evaluation commit (``engine.job_finished``) and from
:func:`apply_for_candidate` / :func:`apply_for_run` (sweeper, CLI); it is applied continuously, not only at terminal
states, under two protections that hold whatever the policy says (:func:`protected_checkpoints`):

* the candidate's LATEST live full state (the resume source: max ``(epoch, created_at, id)`` of the non-PRUNED
  ``training_full`` rows, the rule of ``runner_api.envelope.latest_live_full_state``) is never pruned;
* a checkpoint an evaluation row references (``checkpoint_path`` / ``checkpoint_sha256``, ``raw_metrics``' per-kind
  digests and ``rung_result.rung_end_training_ckpt``) is never pruned.

Candidates in ``CANCELLED`` / ``RESUME_BLOCKED`` / ``ELIGIBLE_BUDGET_EXHAUSTED`` / ``PAUSED`` and runs in
``CANCEL_REQUESTED`` / ``CANCELLED`` get no new requests. :func:`deliver_prune_orders` hands the open requests of a
runner out on its heartbeat (E4 ``control.prune``) - each at most once per lease (job, attempt), re-checking the
protections first (a request whose checkpoint became protected is CANCELLED) - and :func:`record_prune_report` applies
the runner's E11 report: only then is the checkpoint row marked PRUNED (with the runner's ``already_absent`` finding when
the bytes were gone before it acted) and its ``runner_local`` location set PRUNED. A copy in the gateway store is never
touched (the gateway keeps serving it). The former forced ``keep_all`` of remote runs (``remote_policy`` /
``CoreSettings.remote_retention_policy``, design D7) is lifted; the argument is accepted and ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from mbnas_core.db import repo
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.orchestration import engine

POLICIES = ("keep_all", "rung_end_only", "last_rung_end_only")
TERMINAL_FOR_RETENTION = frozenset({"SEARCH_COMPLETE", "REJECTED_BY_GATE", "TRAIN_FAILED", "EVAL_FAILED", "INVALID",
                                    "FINAL_COMPLETE"})
TRAINER_PRUNE_REASON = "STAGE_TRAINER_KEEP_NEWEST_AND_RUNG_END"
# -- remote prune round trip (DD-A66) ---------------------------------------------------------------------------------
KEEP_EVERYTHING_STATUSES = frozenset({"CANCELLED", "RESUME_BLOCKED", "ELIGIBLE_BUDGET_EXHAUSTED", "PAUSED"})
NO_PRUNE_RUN_STATUSES = frozenset({"CANCEL_REQUESTED", "CANCELLED"})
LATEST_FULL_STATE = "LATEST_FULL_STATE"
EVALUATION_REFERENCED = "EVALUATION_REFERENCED"
REMOTE_PRUNE_REQUESTED = "REMOTE_PRUNE_REQUESTED"
CHECKPOINT_PRUNED = "CHECKPOINT_PRUNED"
PRUNE_ORDERS_PER_BEAT = 50
"""At most this many prune orders ride on one heartbeat answer (the rest follow on later beats of the lease)."""
REMOTE_KEPT = "sidecar, eval_ema snapshots, weights_only artifacts and any gateway store copy are kept"


def run_policy(run: dict[str, Any], default: str = "keep_all") -> str:
    """Frozen policy of a run (resolved ``console.retention.full_state``), else ``default`` (the server setting)."""
    value = ((run.get("resolved_protocol") or {}).get("console") or {}).get("retention", {}) or {}
    policy = value.get("full_state") if isinstance(value, dict) else None
    return policy if policy in POLICIES else default


def checkpoint_views(rows: list[dict[str, Any]], located: dict[str, str] | None = None,
                     stored: Collection[str] | None = None) -> list[dict[str, Any]]:
    """Checkpoint rows for API views: ``state`` = PRUNED (recorded deletion), PRESENT (file exists) or MISSING.

    ``located`` (WP-5B, :func:`mbnas_core.artifacts.locations.checkpoint_states`): ``{checkpoint id: state}`` from the
    recorded locations of runner-registered checkpoints - a core-local file check cannot see a runner's disk or the
    gateway store, so those rows take the recorded state; rows without locations keep the core-local check.

    ``stored`` (V4-B fix, DD-A55 a / DD-A51 c): the ids of the checkpoints whose bytes the gateway verified in its store
    (:func:`store_verified_checkpoints`). Every view then carries the 1.5.0 member ``artifact_id``: the checkpoint id
    itself - the id the gateway serves at ``GET /api/v1/runs/{run_id}/artifacts/{checkpoint id}`` - for those rows and
    ``None`` for all others. Without ``stored`` the views have no ``artifact_id`` member (callers that do not ask the
    store, e.g. the export bundle)."""
    out = []
    located = located or {}
    for r in rows:
        view = dict(r)
        if r.get("id") in located:
            view["state"] = located[r["id"]]
        elif r.get("state") != "PRUNED":
            view["state"] = "PRESENT" if os.path.isfile(r["path"]) else "MISSING"
        if stored is not None:
            view["artifact_id"] = r["id"] if r.get("id") in stored else None
        out.append(view)
    return out


def store_verified_checkpoints(conn: sqlite3.Connection, run_id: str) -> set[str]:
    """Ids of the checkpoints of ``run_id`` that the gateway serves from its store: the latest ``server_store`` location
    about the row's recorded digest is PRESENT (the gateway re-computed the SHA-256 of its store copy and reported it,
    :func:`mbnas_core.artifacts.locations.report_server_store`). It is the READY rule of
    :func:`mbnas_core.artifacts.locations.checkpoint_record`, read for a whole run in one query. A runner-local copy
    (``runner_local``: resumable on that runner, never downloadable), a LOAD_FAILED / MISSING store report, a report
    about other bytes and a core-host checkpoint without any location do not count."""
    from mbnas_core.artifacts.locations import SERVER_STORE, hex_sha

    latest: dict[str, tuple[tuple[str, str], Any]] = {}
    for loc in conn.execute(
            "SELECT l.id, l.object_id, l.sha256, l.state, l.updated_at, c.sha256 AS recorded FROM artifact_locations l "
            "JOIN checkpoints c ON c.id = l.object_id AND c.run_id = l.run_id "
            "WHERE l.run_id = ? AND l.object_kind = 'checkpoint' AND l.store = ?", (run_id, SERVER_STORE)).fetchall():
        sha = hex_sha(loc["recorded"])
        if sha is None or hex_sha(loc["sha256"]) != sha:
            continue
        key = (str(loc["updated_at"] or ""), str(loc["id"] or ""))
        if loc["object_id"] not in latest or key > latest[loc["object_id"]][0]:
            latest[loc["object_id"]] = (key, loc["state"])
    return {ck for ck, (_, state) in latest.items() if state == "PRESENT"}


def is_terminal(ctx: engine.RunContext, cand: dict[str, Any]) -> bool:
    status = cand["candidate_status"]
    if status in TERMINAL_FOR_RETENTION:
        return True
    if cand["arm"] == "Baseline" and status == "EVALUATED":
        return cand.get("current_rung") == ctx.stages_for(cand)[-1].id
    return False


def _stage_targets(ctx: engine.RunContext, cand: dict[str, Any]) -> set[int]:
    return {s.target for s in ctx.stages_for(cand)}


def _live(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["kind"] == "training_full" and r.get("state") != "PRUNED"]


def _latest_key(row: dict[str, Any]) -> tuple[int, str, str]:
    """Order of :func:`mbnas_core.runner_api.envelope.latest_live_full_state` (the resume source)."""
    return int(row["epoch"]), str(row.get("created_at") or ""), str(row["id"])


def plan(ctx: engine.RunContext, cand: dict[str, Any], rows: list[dict[str, Any]], policy: str,
         exists: Callable[[dict[str, Any]], bool] | None = None) -> list[dict[str, Any]]:
    """Checkpoint rows the policy deletes for a terminal candidate (``[]`` for keep_all). ``exists`` decides whether a
    rung-end row still holds its payload (``last_rung_end_only``); default: the core-local file check (a remote run
    passes "a runner holds it", :func:`runner_held`)."""
    if policy not in POLICIES:
        raise ValueError(f"unknown retention policy {policy!r}")
    live = _live(rows)
    if policy == "keep_all" or not live:
        return []
    ends = _stage_targets(ctx, cand)
    if policy == "rung_end_only":
        return [r for r in live if int(r["epoch"]) not in ends]
    present = exists or (lambda r: os.path.isfile(r["path"]))
    reached = [int(r["epoch"]) for r in live if int(r["epoch"]) in ends and present(r)]
    keep = max(reached) if reached else None
    return [r for r in live if int(r["epoch"]) != keep]


def _payload_files(row: dict[str, Any]) -> list[Path]:
    """The full-state payload of a row and, when present, the RF-DETR native checkpoint it references."""
    path = Path(row["path"])
    files = [path]
    epoch = path.name.removeprefix("full_e").removesuffix(".pt")
    if path.name.startswith("full_e") and epoch.isdigit():
        files.append(path.with_name(f"native_e{epoch}.ckpt"))
    return files


def _delete(row: dict[str, Any], run_root: str) -> tuple[list[dict[str, Any]], int]:
    deleted, freed = [], 0
    for f in _payload_files(row):
        real = os.path.realpath(f)
        if not real.startswith(run_root + os.sep) or not os.path.isfile(real):
            continue  # never touch anything outside the run directory
        size = os.path.getsize(real)
        os.unlink(real)
        deleted.append({"path": real, "bytes": size})
        freed += size
    return deleted, freed


def is_remote_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """True when any payload of the run is held by a remote runner (see module docstring)."""
    row = conn.execute(
        "SELECT 1 FROM artifact_locations WHERE run_id = ? AND store = 'runner_local' "
        "UNION ALL SELECT 1 FROM jobs j JOIN runners r ON r.id = j.lease_owner WHERE j.run_id = ? LIMIT 1",
        (run_id, run_id)).fetchone()
    return row is not None


# -- remote runs: the prune round trip (DD-A66) -------------------------------------------------------------------------
class PruneReportError(Exception):
    """An E11 report the core refuses: ``status`` / ``code`` / ``message`` of the Error answer."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def prune_request_id(checkpoint_id: str) -> str:
    """Deterministic id of the (single) prune request of a checkpoint: re-planning can never create a second one."""
    return "prq-" + hashlib.sha256(f"prune\0{checkpoint_id}".encode("utf-8")).hexdigest()[:24]


def _hex(value: Any) -> str | None:
    from mbnas_core.artifacts.locations import hex_sha

    return hex_sha(value)


def runner_held(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, Any]]:
    """``{checkpoint id: runner_local location}`` of the run's checkpoints a runner still holds (location PRESENT)."""
    latest: dict[str, dict[str, Any]] = {}
    for loc in conn.execute("SELECT * FROM artifact_locations WHERE run_id = ? AND object_kind = 'checkpoint' "
                            "AND store = 'runner_local' ORDER BY created_at, id", (run_id,)).fetchall():
        latest[loc["object_id"]] = {k: loc[k] for k in loc.keys()}
    return {ck: loc for ck, loc in latest.items() if loc.get("state") == "PRESENT" and loc.get("runner_id")}


def evaluation_references(conn: sqlite3.Connection, run_id: str, candidate_id: str) -> tuple[set[str], set[str]]:
    """(paths, hex digests) of the checkpoints the candidate's evaluation rows (any status, superseded ones included)
    reference: ``checkpoint_path``, ``checkpoint_sha256`` (a string or a per-kind mapping), ``raw_metrics``'
    ``checkpoint_sha256_by_kind`` and ``rung_result.rung_end_training_ckpt``."""
    paths: set[str] = set()
    shas: set[str] = set()

    def digests(value: Any) -> None:
        for v in (value.values() if isinstance(value, dict) else [value]):
            sha = _hex(v)
            if sha:
                shas.add(sha)

    for ev in repo.list_evaluations(conn, run_id, candidate_id=candidate_id):
        row = ev.get("row") if isinstance(ev.get("row"), dict) else {}
        raw = row.get("raw_metrics") if isinstance(row.get("raw_metrics"), dict) else {}
        rung = raw.get("rung_result") if isinstance(raw.get("rung_result"), dict) else {}
        for p in (row.get("checkpoint_path"), raw.get("checkpoint_path"), rung.get("rung_end_training_ckpt")):
            if isinstance(p, str) and p:
                paths.add(p)
        for value in (row.get("checkpoint_sha256"), raw.get("checkpoint_sha256"), raw.get("checkpoint_sha256_by_kind")):
            digests(value)
    return paths, shas


def protected_checkpoints(conn: sqlite3.Connection, run_id: str, candidate_id: str, rows: list[dict[str, Any]],
                          held: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    """``{checkpoint id: reason}`` of the candidate's checkpoints no policy may prune (module docstring): the latest live
    full state (:data:`LATEST_FULL_STATE`) and every checkpoint an evaluation row references by path, by its runner
    relpath or by digest (:data:`EVALUATION_REFERENCED`)."""
    out: dict[str, str] = {}
    live = _live(rows)
    if live:
        out[max(live, key=_latest_key)["id"]] = LATEST_FULL_STATE
    paths, shas = evaluation_references(conn, run_id, candidate_id)
    if not (paths or shas):
        return out
    for r in rows:
        rel = str(((held or {}).get(r["id"]) or {}).get("relpath") or "")
        if (r.get("path") in paths or _hex(r.get("sha256")) in shas
                or (rel and any(p == rel or p.endswith("/" + rel) for p in paths))):
            out.setdefault(r["id"], EVALUATION_REFERENCED)
    return out


def _open_request(conn: sqlite3.Connection, row: dict[str, Any], loc: dict[str, Any], policy: str, planned_by: str,
                  now: str) -> dict[str, Any] | None:
    """Create the request of ``row`` (or re-arm a CANCELLED one); None when one is already open or done."""
    rid = prune_request_id(row["id"])
    reason = f"RETENTION_{policy.upper()}"
    existing = conn.execute("SELECT state FROM prune_requests WHERE id = ?", (rid,)).fetchone()
    if existing is not None:
        if existing["state"] != "CANCELLED":
            return None
        conn.execute("UPDATE prune_requests SET state = 'PENDING', policy = ?, reason = ?, planned_by = ?, "
                     "cancel_reason = NULL, delivered_job_id = NULL, delivered_attempt = NULL, updated_at = ? "
                     "WHERE id = ? AND state = 'CANCELLED'", (policy, reason, planned_by, now, rid))
    else:
        conn.execute(
            "INSERT INTO prune_requests (id, checkpoint_id, run_id, candidate_id, runner_id, relpath, sha256, bytes, "
            "policy, reason, state, planned_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)",
            (rid, row["id"], row["run_id"], row["candidate_id"], loc["runner_id"], loc["relpath"],
             _hex(row["sha256"]) or _hex(loc["sha256"]) or str(row["sha256"]), int(row["bytes"]), policy, reason,
             planned_by, now, now))
    return {"request_id": rid, "checkpoint_id": row["id"], "runner_id": loc["runner_id"], "epoch": int(row["epoch"]),
            "relpath": loc["relpath"], "policy": policy, "reason": reason}


def request_remote_prunes(conn: sqlite3.Connection, run: dict[str, Any] | None, candidate_id: str, *,
                          policy: str | None = None, default_policy: str = "keep_all", planned_by: str = "retention",
                          now: str | None = None) -> list[dict[str, Any]]:
    """Plan the run's policy over the candidate's runner-held full states and open a prune request for every row it
    deletes that is not protected (module docstring); inside the caller's transaction. Returns the requests opened by
    this call (none for keep_all, for keep-everything candidate / run statuses, or when nothing is runner-held)."""
    cand = repo.get_candidate(conn, candidate_id) if run is not None else None
    if cand is None or run is None:
        return []
    policy = policy or run_policy(run, default_policy)
    if policy not in POLICIES:
        raise ValueError(f"unknown retention policy {policy!r}")
    if (policy == "keep_all" or cand["candidate_status"] in KEEP_EVERYTHING_STATUSES
            or run.get("status") in NO_PRUNE_RUN_STATUSES):
        return []
    held = runner_held(conn, run["id"])
    rows = repo.list_checkpoints(conn, run["id"], candidate_id=candidate_id)
    if not any(r["id"] in held for r in rows):
        return []
    protected = protected_checkpoints(conn, run["id"], candidate_id, rows, held)
    now = now or utcnow_iso()
    made = []
    for row in plan(engine.RunContext(run), cand, rows, policy, exists=lambda r: r["id"] in held):
        loc = held.get(row["id"])
        if loc is None or row["id"] in protected:
            continue
        opened = _open_request(conn, row, loc, policy, planned_by, now)
        if opened is not None:
            made.append(opened)
    if made:
        engine.log(conn, run["id"], f"retention {policy}: {len(made)} prune request(s) for runner-held full state(s) "
                   f"of {candidate_id} (deleted only when the runner reports it)", candidate_id=candidate_id,
                   policy=policy, reason_code=REMOTE_PRUNE_REQUESTED, planned_by=planned_by,
                   checkpoint_ids=[m["checkpoint_id"] for m in made], epochs=[m["epoch"] for m in made])
    return made


def _cancel(conn: sqlite3.Connection, request_id: str, reason: str, now: str) -> None:
    conn.execute("UPDATE prune_requests SET state = 'CANCELLED', cancel_reason = ?, updated_at = ? "
                 "WHERE id = ? AND state IN ('PENDING', 'DELIVERED')", (reason, now, request_id))


def deliver_prune_orders(conn: sqlite3.Connection, runner_id: str, job_id: str, attempt: int, *,
                         limit: int = PRUNE_ORDERS_PER_BEAT, now: str | None = None) -> list[dict[str, Any]]:
    """The ``control.prune`` orders of a heartbeat of ``runner_id``'s lease ``(job_id, attempt)`` (inside the caller's
    transaction): PENDING requests of that runner and DELIVERED ones handed out in an EARLIER lease without a report
    (never twice in the same lease). Each is re-checked first - checkpoint gone or already PRUNED, the runner no longer
    holding it, the checkpoint now protected, its candidate now in a keep-everything status (``CANDIDATE_<status>``) or
    its run cancelled (``RUN_<status>``) -> CANCELLED (``cancel_reason``) instead of handed out; re-planning re-arms a
    cancelled request once the checkpoint may be pruned again."""
    now = now or utcnow_iso()
    reqs = conn.execute(
        "SELECT * FROM prune_requests WHERE runner_id = ? AND (state = 'PENDING' OR (state = 'DELIVERED' AND "
        "NOT (delivered_job_id IS ? AND delivered_attempt IS ?))) ORDER BY created_at, id LIMIT ?",
        (runner_id, job_id, int(attempt), int(limit))).fetchall()
    orders: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, str], str | None]] = {}
    for req in reqs:
        ck = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (req["checkpoint_id"],)).fetchone()
        if ck is None or ck["state"] == "PRUNED":
            _cancel(conn, req["id"], "ALREADY_PRUNED", now)
            continue
        key = (req["run_id"], req["candidate_id"])
        if key not in cache:
            held = runner_held(conn, req["run_id"])
            rows = repo.list_checkpoints(conn, req["run_id"], candidate_id=req["candidate_id"])
            run = repo.get_run(conn, req["run_id"]) or {}
            cand = repo.get_candidate(conn, req["candidate_id"]) or {}
            keep = (f"RUN_{run.get('status')}" if run.get("status") in NO_PRUNE_RUN_STATUSES else
                    f"CANDIDATE_{cand.get('candidate_status')}"
                    if cand.get("candidate_status") in KEEP_EVERYTHING_STATUSES else None)
            cache[key] = (held, protected_checkpoints(conn, req["run_id"], req["candidate_id"], rows, held), keep)
        held, protected, keep = cache[key]
        if keep is not None:
            _cancel(conn, req["id"], keep, now)
            continue
        loc = held.get(req["checkpoint_id"])
        if loc is None or loc.get("runner_id") != runner_id:
            _cancel(conn, req["id"], "NOT_HELD_BY_RUNNER", now)
            continue
        if req["checkpoint_id"] in protected:
            _cancel(conn, req["id"], protected[req["checkpoint_id"]], now)
            continue
        conn.execute("UPDATE prune_requests SET state = 'DELIVERED', deliveries = deliveries + 1, delivered_job_id = ?, "
                     "delivered_attempt = ?, delivered_at = ?, updated_at = ? WHERE id = ?",
                     (job_id, int(attempt), now, now, req["id"]))
        orders.append({"request_id": req["id"], "checkpoint_id": req["checkpoint_id"], "run_id": req["run_id"],
                       "relpath": req["relpath"], "sha256": req["sha256"], "bytes": int(req["bytes"]),
                       "policy": req["policy"], "reason": req["reason"]})
    return orders


def record_prune_report(conn: sqlite3.Connection, checkpoint_id: str, report: dict[str, Any], *, runner_id: str,
                        now: str | None = None) -> dict[str, Any]:
    """Apply a runner's E11 report (a validated ``PruneReport`` dict) inside the caller's transaction; returns the
    ``PruneAck`` body. The checkpoint row becomes PRUNED here and only here for a remote run (with the request, the
    runner, what it removed or its ``already_absent`` finding), its ``runner_local`` location PRUNED and the request
    DONE. A second report of a DONE request is answered ``replayed`` without any change. Refused
    (:class:`PruneReportError`): unknown request or one of another checkpoint (404 PRUNE_REQUEST_NOT_FOUND), a request
    addressed to another runner (403 RUNNER_MISMATCH), a report naming other bytes than the request (409
    PRUNE_REPORT_CONFLICT)."""
    req = conn.execute("SELECT * FROM prune_requests WHERE id = ?", (report.get("request_id"),)).fetchone()
    if req is None or req["checkpoint_id"] != checkpoint_id:
        raise PruneReportError(404, "PRUNE_REQUEST_NOT_FOUND",
                               f"no prune request {report.get('request_id')} for checkpoint {checkpoint_id}")
    if req["runner_id"] != runner_id:
        raise PruneReportError(403, "RUNNER_MISMATCH", "this prune request is addressed to another runner")
    named = (report.get("run_id"), report.get("relpath"), _hex(report.get("sha256")), report.get("bytes"))
    if named != (req["run_id"], req["relpath"], _hex(req["sha256"]), int(req["bytes"])):
        raise PruneReportError(409, "PRUNE_REPORT_CONFLICT", "the report names another run, location, digest or size "
                                                             "than the prune request")
    ack = {"checkpoint_id": checkpoint_id, "request_id": req["id"], "state": "PRUNED", "replayed": False}
    if req["state"] == "DONE":
        return {**ack, "replayed": True}
    ck = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
    if ck is None:
        raise PruneReportError(404, "PRUNE_REQUEST_NOT_FOUND", f"checkpoint {checkpoint_id} does not exist")
    now = now or utcnow_iso()
    absent = bool(report.get("already_absent"))
    removed = [i for i in report.get("removed") or [] if isinstance(i, dict)]
    freed = int(report.get("bytes_freed") or 0)
    cand = repo.get_candidate(conn, ck["candidate_id"]) or {}
    # public text (CheckpointPrune.note): no runner id, no path - those stay in the owner-only members below
    note = ("the holding runner found the payload already gone (nothing deleted by this request)" if absent else
            f"deleted by the holding runner ({freed} bytes)")
    prune = {"policy": req["policy"], "reason": req["reason"],
             "deleted": [{"relpath": i.get("relpath"), "bytes": i.get("bytes")} for i in removed],
             "bytes_freed": freed, "kept": REMOTE_KEPT, "candidate_status": cand.get("candidate_status"), "note": note,
             # owner-only members (the public CheckpointPrune projection keeps the seven keys above, bytes only)
             "remote": True, "runner_id": runner_id, "request_id": req["id"], "already_absent": absent,
             "sha256": req["sha256"], "relpath": req["relpath"], "runner_pruned_at": report.get("pruned_at"),
             "reported_at": now, "detail": report.get("detail")}
    if ck["state"] != "PRUNED":
        repo.mark_checkpoint_pruned(conn, checkpoint_id, prune=prune, now=now)
    conn.execute("UPDATE artifact_locations SET state = 'PRUNED', verified_at = ?, updated_at = ? WHERE "
                 "object_kind = 'checkpoint' AND object_id = ? AND store = 'runner_local' AND runner_id = ?",
                 (now, now, checkpoint_id, runner_id))
    conn.execute("UPDATE prune_requests SET state = 'DONE', report_json = ?, reported_at = ?, updated_at = ? "
                 "WHERE id = ?", (json.dumps(report, sort_keys=True), now, now, req["id"]))
    engine.log(conn, ck["run_id"], f"checkpoint {checkpoint_id} (epoch {ck['epoch']}) pruned on its runner: "
               + ("already absent there" if absent else f"{freed} bytes freed"), candidate_id=ck["candidate_id"],
               checkpoint_id=checkpoint_id, request_id=req["id"], policy=req["policy"], bytes_freed=freed,
               already_absent=absent, reason_code=CHECKPOINT_PRUNED,
               **({"after_cancel": req["cancel_reason"]} if req["state"] == "CANCELLED" else {}))
    return ack


def apply_for_candidate(conn: sqlite3.Connection, run: dict[str, Any], candidate_id: str, *,
                        policy: str | None = None, default_policy: str = "keep_all",
                        force: bool = False, remote_policy: str | None = None) -> dict[str, Any]:
    """Apply the retention policy to one candidate (inside the caller's tx); returns a report.

    Deletions happen only for candidates that are terminal (see module docstring) unless ``force``. A remote run
    (``report["remote"]``) applies its own policy through the prune round trip instead: no core-side deletion, the
    opened requests are listed in ``report["requested"]`` (:func:`request_remote_prunes`), the local-file
    reconciliation is skipped. ``remote_policy`` (the former forced policy of remote runs, design D7) is ignored since
    DD-A66."""
    from mbnas_core.artifacts.registry import run_dir

    del remote_policy  # DD-A66: remote runs follow their own policy (prune round trip)
    ctx = engine.RunContext(run)
    cand = repo.get_candidate(conn, candidate_id)
    policy = policy or run_policy(run, default_policy)
    remote = is_remote_run(conn, run["id"])
    report: dict[str, Any] = {"candidate_id": candidate_id, "policy": policy, "pruned": [], "bytes_freed": 0,
                              "terminal": bool(cand and is_terminal(ctx, cand)), "remote": remote}
    if cand is None:
        return report
    if remote:
        report["trainer_pruned"] = []
        report["requested"] = [r["checkpoint_id"] for r in request_remote_prunes(
            conn, run, candidate_id, policy=policy, planned_by="retention")]
        return report
    rows = repo.list_checkpoints(conn, run["id"], candidate_id=candidate_id)
    report["trainer_pruned"] = reconcile_trainer_prunes(conn, ctx, cand, rows)
    if not (report["terminal"] or force):
        return report
    run_root = os.path.realpath(run_dir(run))
    rows = repo.list_checkpoints(conn, run["id"], candidate_id=candidate_id)
    now = utcnow_iso()
    for row in plan(ctx, cand, rows, policy):
        if row.get("state") == "PRUNED":  # another row of the same file was handled in this pass
            continue
        deleted, freed = _delete(row, run_root)
        if not deleted:  # the file was already gone before this policy ran: it stays MISSING, never "pruned"
            continue
        same = [r for r in rows if r["path"] == row["path"] and r.get("state") != "PRUNED"]
        for r in same:
            repo.mark_checkpoint_pruned(conn, r["id"], now=now, prune={
                "policy": policy, "reason": f"RETENTION_{policy.upper()}", "deleted": deleted, "bytes_freed": freed,
                "kept": "sidecar, eval_ema snapshots and weights_only artifacts are kept",
                "candidate_status": cand["candidate_status"]})
            r["state"] = "PRUNED"
        report["pruned"].append({"checkpoint_id": row["id"], "epoch": row["epoch"], "deleted": deleted})
        report["bytes_freed"] += freed
    return report


def reconcile_trainer_prunes(conn: sqlite3.Connection, ctx: engine.RunContext, cand: dict[str, Any],
                             rows: list[dict[str, Any]]) -> list[str]:
    """Mark full-state rows the stage trainer already deleted (newer state exists, not a stage end, file gone)."""
    live = _live(rows)
    if not live:
        return []
    newest = max(int(r["epoch"]) for r in live)
    ends = _stage_targets(ctx, cand)
    marked = []
    for r in live:
        epoch = int(r["epoch"])
        if epoch < newest and epoch not in ends and not os.path.exists(r["path"]):
            repo.mark_checkpoint_pruned(conn, r["id"], prune={
                "policy": "stage_trainer", "reason": TRAINER_PRUNE_REASON, "deleted": [{"path": r["path"]}],
                "note": "training.checkpoint.prune keeps the newest and every rung-end full state"})
            marked.append(r["id"])
    return marked


def apply_for_run(conn: sqlite3.Connection, run_id: str, *, policy: str | None = None, default_policy: str = "keep_all",
                  force: bool = False) -> dict[str, Any]:
    """Apply the policy to every candidate of a run (inside the caller's tx); returns a run report (``requested``: the
    prune requests opened for a remote run, deleted later by its runners)."""
    run = repo.get_run(conn, run_id)
    if run is None:
        raise repo.NotFound(run_id)
    reports = [apply_for_candidate(conn, run, c["id"], policy=policy, default_policy=default_policy, force=force)
               for c in repo.list_candidates(conn, run_id)]
    pruned = sum(len(r["pruned"]) for r in reports)
    requested = sum(len(r.get("requested") or []) for r in reports)
    freed = sum(r["bytes_freed"] for r in reports)
    if pruned:
        engine.log(conn, run_id, f"retention {policy or run_policy(run, default_policy)}: pruned {pruned} full-state "
                   f"payload(s), {freed} bytes", policy=policy or run_policy(run, default_policy), bytes_freed=freed)
    return {"run_id": run_id, "policy": policy or run_policy(run, default_policy), "candidates": reports,
            "pruned": pruned, "requested": requested, "bytes_freed": freed}


def main(argv: list[str] | None = None) -> int:
    from mbnas_core.config import Settings
    from mbnas_core.db.core import connect, tx

    parser = argparse.ArgumentParser(description="Apply the full-state retention policy to a run's terminal candidates")
    parser.add_argument("--run-id", required=True, action="append", help="repeatable")
    parser.add_argument("--policy", choices=POLICIES, default=None, help="default: the run's resolved policy")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args(argv)
    settings = Settings.load(args.env_file)
    conn = connect(settings, init=False)
    try:
        out = []
        for run_id in args.run_id:
            with tx(conn):
                out.append(apply_for_run(conn, run_id, policy=args.policy, default_policy=settings.full_state_retention))
    finally:
        conn.close()
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
