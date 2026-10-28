"""``/core/v1/runner/*`` endpoints E1-E11 and
the minimal ``/core/v1/owner/{runners,principals}`` reads.

Wire models are :mod:`mbnas_contracts.runner_protocol` (``extra="forbid"``). Every route requires a principal of scope
``runner`` (runner or admin token; :func:`.auth.require_principal`), every body naming a ``runner_id`` is checked against
the token's runner (403 ``RUNNER_MISMATCH``), and every job-scoped write carries ``(runner_id, attempt, ticket)``: the
ticket is verified (403 ``TICKET_INVALID``) and the job row is fenced on ``(lease_owner, attempt, status)`` (409
``LEASE_LOST``), with the same ``attempt`` condition on the SQL writes. One endpoint = one short ``BEGIN IMMEDIATE``
transaction calling the existing repo/jobs/costs/engine functions; SSE subscribers are woken after commit.

Result commit (E8): key ``(job_id, attempt)`` in ``idempotent_responses``; same digest -> stored response
replayed (no DB change), other digest -> 409 ``RESULT_CONFLICT`` (+ error log). The ``PROCESSING`` insert, the fence,
``settle_job`` and the ``DONE`` update run in ONE transaction (nested ``tx`` = SAVEPOINT), so this route never commits a
``PROCESSING`` row - a crash or exception rolls the row back with everything else. The 202 + ``Retry-After: 2`` answer
to a ``PROCESSING`` row (and the sweeper's 60 s orphan cleanup) is therefore a defensive path for rows left by an
external or older writer only (a row "left behind by a crash mid-commit" does not occur with this single-tx
implementation). A lost fence -> 409 ``LEASE_LOST`` after ``costs.record_lost_attempt`` (stored
and replayed like a success); an exception rolls everything back -> 500 ``COMMIT_FAILED``. A result for a run in
CANCEL_REQUESTED/CANCELLED (or with ``cancel_observed``) is accepted as ``CANCELLED/RUN_CANCELLED`` with the original
kept in ``error.discarded_result``.

Claim (E2) and the device (mixed fleet): a runner registered as a CPU device (``device = "cpu"``)
does not lease a job whose result needs a CUDA measurement (:func:`requires_cuda`: any job of an evaluation-ON search /
final-retrain run, a BENCHMARK, an explicit training job that evaluates) while an ONLINE non-CPU runner offers that job
kind (:func:`needs_cuda`): such a job is skipped - never leased, it stays QUEUED for the CUDA runner - and the claim
answers 204 when nothing else is leasable. On a CPU-only fleet (no such runner) the CPU runner leases exactly as before
(``repo.acquire_lease``) and an evaluation it cannot measure is committed NOT_EVALUATED with its reason.
GPU runners always lease through ``repo.acquire_lease`` unchanged.

Claim (E2) and expired leases: both lease paths also take a LEASED/RUNNING job whose lease expired, so
every claim iteration first runs the core sweeper's reclaim (:func:`.sweeper.reclaim`) at the same ``now`` - the owner
worker's rule (reclaim right before leasing). A dead RUNNING attempt is therefore billed ``failed``/lost from
``started_at`` to its last heartbeat (``LEASE_EXPIRED``), the job is requeued (or failed when no attempt is left) and
the next attempt is billed ``retry`` - the same accounting as when the sweeper's own pass comes first. The reclaim also
precedes the re-delivery of this runner's own LEASED job (E2(a)), so an expired lease is never handed out again.

Retention prune round trip (runner protocol 1.1.0): E6 records a checkpoint and, in
the same transaction, ``engine.checkpoint_registered`` plans the run's retention policy over the candidate's runner-held
full states (``prune_requests`` rows; never the latest full state, never a checkpoint an evaluation row references).
E4 hands the open requests of the calling runner out as ``control.prune`` orders - each at most once per lease (job,
attempt), re-checked against those protections first (``retention.deliver_prune_orders``). E11 ``POST
/checkpoints/{checkpoint_id}/pruned`` (runner token, no ticket: the order may be reported after its lease ended; only the
runner the request is addressed to) applies the runner's report: the checkpoint row becomes PRUNED only here
(``retention.record_prune_report``), a repeated report of a done request is answered ``replayed``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from mbnas_contracts.errors import ApiError, pydantic_errors
from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.runner_protocol import (
    ENVELOPE_VERSION, AckRequest, ArtifactReport, CheckpointReport, ClaimRequest, DeregisterRequest, HeartbeatRequest,
    ProgressBatch, PruneReport, RegisterRequest, ResultCommit, StartRequest,
)
from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobStatus
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.time import iso_after, parse_iso, utcnow_iso
from mbnas_core.artifacts.registry import ARTIFACT_KINDS
from mbnas_core.config import CoreSettings
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.events.bus import emit
from mbnas_core.orchestration import engine, retention
from mbnas_core.orchestration.views import worker_payload
from mbnas_core.runner_api import sweeper, tickets
from mbnas_core.runner_api.auth import (
    check_runner, list_principals, principal_db, require_principal,
)
from mbnas_core.runner_api.envelope import build_envelope, stage_of
from mbnas_core.runner_api.fence import fenced_job, lease_lost, owns
from mbnas_core.runner_api.settle import settle_job
from mbnas_core.scheduling import costs, jobs

log = logging.getLogger("mbnas_core.runner_api")

RUNNER_PREFIX = "/core/v1/runner"
OWNER_PREFIX = "/core/v1/owner"
ALLOWED_KINDS = jobs.GPU_KINDS
MAX_BATCH_MESSAGES = 100
PROGRESS_INTERVAL_S = 1.0
KILL_GRACE_S = 10.0
ARTIFACT_STATES = ("READY", "NOT_READY", "EXPIRED", "ACCESS_DENIED", "MISSING", "LOAD_FAILED")
PROGRESS_KEYS = ("epoch", "epoch_target_total", "global_step", "samples_seen", "learning_rate")
ID_RE = re.compile(tickets.ID_PATTERN)
CLAIM_ATTEMPTS = 5
CPU_DEVICE = "cpu"
"""``runners.device`` of a CPU-only runner (same spelling as ``protocol.validator.CPU_DEVICE``)."""

router = APIRouter(dependencies=[Depends(require_principal("runner"))])
owner_router = APIRouter(dependencies=[Depends(require_principal("admin"))])


# -- helpers ---------------------------------------------------------------------------------------------------------
def _settings(request: Request) -> CoreSettings:
    return request.app.state.services.settings


def _key(request: Request) -> bytes:
    return request.app.state.ticket_key


def _principal(request: Request) -> dict[str, Any]:
    return request.state.principal


def _notify(request: Request, run_id: str | None) -> None:
    if run_id:
        request.app.state.bus.notify(run_id)


def _runner(conn: sqlite3.Connection, runner_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM runners WHERE id = ?", (runner_id,)).fetchone()
    return dict(row) if row is not None else None


def _seen(conn: sqlite3.Connection, runner_id: str, now: str, status: str = "ONLINE") -> None:
    conn.execute("UPDATE runners SET last_seen_at = ?, status = ?, updated_at = ? WHERE id = ?",
                 (now, status, now, runner_id))


def _publish(conn: sqlite3.Connection, runner: dict[str, Any], status: str, job: dict[str, Any] | None, *,
             progress: dict[str, Any] | None = None, sample: dict[str, Any] | None = None,
             event_run: str | None = None) -> None:
    """Workers row of a runner (``workers.id`` = runner_id; same payload shape as ``Worker.publish``). The row also
    names the core-issued ``runner_id`` and the runtime-manifest label of the registration (contract 1.5.0 Worker;
    the public projection replaces ``device_uuid`` by the core-issued ``device_id``)."""
    now = utcnow_iso()
    sample = sample or {}
    try:
        verification = (json.loads(runner.get("runtime_json") or "{}") or {}).get("verification")
    except (TypeError, ValueError):
        verification = None
    payload: dict[str, Any] = {"id": runner["id"], "status": status, "device": runner.get("device") or "unknown",
                               "device_uuid": runner.get("device_uuid"), "device_name": runner.get("device_name"),
                               "heartbeat_at": now, "pid": None, "runner_id": runner["id"],
                               "runtime_manifest_status": verification if verification in (
                                   "verified", "mismatch", "unknown") else "unknown",
                               **{k: sample.get(k) for k in ("utilization_percent", "vram_mb", "power_w", "sm_clock_mhz")}}
    if job is not None:
        payload.update(job_id=job["id"], run_id=job["run_id"], candidate_id=job.get("candidate_id"),
                       rung=job.get("rung"), attempt=job.get("attempt"), lease_expires_at=job.get("lease_expires_at"))
    for key in ("epoch", "epoch_target_total", "learning_rate", "global_step"):
        if (progress or {}).get(key) is not None:
            payload[key] = progress[key]  # type: ignore[index]
    row = repo.upsert_worker(conn, id=runner["id"], status=status, device=payload["device"],
                             device_uuid=payload["device_uuid"], device_name=payload["device_name"],
                             job_id=payload.get("job_id"), run_id=payload.get("run_id"),
                             candidate_id=payload.get("candidate_id"), heartbeat_at=now, row=payload)
    if event_run:
        emit(conn, event_run, "worker.updated", {k: v for k, v in worker_payload(row).items() if v is not None})


def _job_write(request: Request, job_id: str, body: Any) -> None:
    """Token binding + ticket of a job-scoped write."""
    check_runner(_principal(request), body.runner_id)
    tickets.verify(_key(request), body.ticket, job_id=job_id, attempt=body.attempt, runner_id=body.runner_id)


def _registered(conn: sqlite3.Connection, runner_id: str) -> dict[str, Any]:
    runner = _runner(conn, runner_id)
    if runner is None or runner["status"] == "REGISTERED" and runner.get("last_seen_at") is None:
        raise ApiError(409, "RUNNER_NOT_REGISTERED", f"runner {runner_id} must call /register first")
    return runner


def runtime_label(reported: dict[str, str]) -> str:
    """``verified`` when the runner's operator files equal this host's, ``mismatch`` when any differs, else unknown."""
    if not reported:
        return "unknown"
    try:  # same function the runner reports with (keys ``mbnas_model_runtime/operators/<module>.py``)
        from mbnas_model_runtime.operators import operator_files_sha256

        mine = operator_files_sha256()
    except Exception:  # noqa: BLE001 - the label is informational
        return "unknown"
    return "verified" if reported == mine else "mismatch"


# -- E1 register -----------------------------------------------------------------------------------------------------
@router.post("/register")
def register(body: RegisterRequest, request: Request, conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    check_runner(_principal(request), body.runner_id)
    if not ID_RE.match(body.runner_id):
        raise ApiError(422, "VALIDATION_FAILED", "runner_id must match " + tickets.ID_PATTERN)
    if body.runtime.envelope_version != ENVELOPE_VERSION:
        raise ApiError(409, "VERSION_MISMATCH", f"runner speaks envelope_version {body.runtime.envelope_version}, "
                                                 f"core {ENVELOPE_VERSION}")
    settings = _settings(request)
    kinds = sorted(k.value for k in body.kinds if k.value in ALLOWED_KINDS)
    label = runtime_label(dict(body.runtime.operator_files_sha256))
    runtime = {**body.runtime.model_dump(mode="json"), "verification": label}
    now = utcnow_iso()
    with tx(conn):
        conn.execute(
            "INSERT INTO runners (id, status, host, device, device_uuid, device_name, kinds_json, runtime_json, "
            "data_mounts_json, registered_at, last_seen_at, created_at, updated_at) "
            "VALUES (?, 'ONLINE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = 'ONLINE', "
            "host = excluded.host, device = excluded.device, device_uuid = excluded.device_uuid, "
            "device_name = excluded.device_name, kinds_json = excluded.kinds_json, runtime_json = excluded.runtime_json, "
            "data_mounts_json = excluded.data_mounts_json, registered_at = excluded.registered_at, "
            "last_seen_at = excluded.last_seen_at, updated_at = excluded.updated_at",
            (body.runner_id, body.host, body.device, body.device_uuid, body.device_name, json.dumps(kinds),
             json.dumps(runtime, sort_keys=True), json.dumps(body.data_mounts, sort_keys=True), now, now, now, now))
        _publish(conn, _runner(conn, body.runner_id), "idle", None)  # type: ignore[arg-type]
    return JSONResponse({
        "runner_id": body.runner_id, "core_contract": request.app.state.core_contract,
        "envelope_version": ENVELOPE_VERSION, "lease_ttl_s": float(settings.lease_ttl_s),
        "heartbeat_s": float(settings.heartbeat_s), "progress_interval_s": PROGRESS_INTERVAL_S,
        "max_batch_messages": MAX_BATCH_MESSAGES, "max_body_bytes": settings.runner_body_limit_bytes,
        "allowed_kinds": sorted(ALLOWED_KINDS), "artifact_store": "runner_local", "runtime_verification": label,
        "server_time": now})


# -- E2 claim --------------------------------------------------------------------------------------------------------
def cpu_runner(runner: dict[str, Any] | None) -> bool:
    """A runner registered with device ``cpu`` (CPU-only; its daemon maps the assigned ``cuda:0`` to ``cpu``)."""
    return str((runner or {}).get("device") or "").strip().lower() == CPU_DEVICE


def requires_cuda(resolved: Any, kind: str) -> bool:
    """True when a job of ``kind`` of a run with protocol ``resolved`` is only complete with a measurement
    on a CUDA device (latency is measured on CUDA only):

    * ``BENCHMARK``: a latency measurement;
    * any job of an evaluation-ON search or final-retrain run (``console.search.evaluation_enabled`` not false, no
      explicit training): its inline / explicit evaluation measures latency and the fitness needs it;
    * an explicit training job (``console.extra.training.enabled``) that evaluates (``validation_enabled`` or
      ``evaluate_only``).
    Explicit training without validation and evaluation-OFF runs never do (a CPU runner maps ``cuda:0`` to ``cpu``)."""
    if kind == JobKind.BENCHMARK.value:
        return True
    console = resolved.get("console") if isinstance(resolved, dict) else None
    console = console if isinstance(console, dict) else {}
    training = (console.get("extra") or {}).get("training") if isinstance(console.get("extra"), dict) else None
    if isinstance(training, dict) and training.get("enabled") is True:
        return training.get("validation_enabled", True) is not False or training.get("mode") == "evaluate_only"
    search = console.get("search") if isinstance(console.get("search"), dict) else {}
    return search.get("evaluation_enabled", True) is not False


def needs_cuda(resolved: Any, kind: str, cuda_kinds: frozenset[str] | set[str]) -> bool:
    """A CPU runner leaves this job to a CUDA runner - the job :func:`requires_cuda` and an ONLINE non-CPU
    runner offers its ``kind`` (``cuda_kinds``, :func:`_cuda_runner_kinds`; a mixed fleet). With no such runner (a
    CPU-only fleet) the CPU runner takes it and an evaluation it cannot measure is committed NOT_EVALUATED
    instead of leaving the run QUEUED forever."""
    return kind in cuda_kinds and requires_cuda(resolved, kind)


def _cuda_runner_kinds(conn: sqlite3.Connection) -> frozenset[str]:
    """Job kinds offered by the ONLINE runners that are not CPU runners (their device can measure latency)."""
    kinds: set[str] = set()
    for r in conn.execute("SELECT device, kinds_json FROM runners WHERE status = 'ONLINE'"):
        if cpu_runner(dict(r)):
            continue
        try:
            offered = json.loads(r["kinds_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if isinstance(offered, list):
            kinds.update(k for k in offered if isinstance(k, str))
    return frozenset(kinds)


def _lease_for_cpu_runner(conn: sqlite3.Connection, runner_id: str, now: str, ttl_s: float,
                          kinds: list[str]) -> dict[str, Any] | None:
    """``repo.acquire_lease`` for a CPU runner. Without an ONLINE non-CPU runner (CPU-only fleet) it IS
    ``repo.acquire_lease``. Otherwise: the first job leasable under the same conditions and in the same order (QUEUED or
    lease expired, attempts left, run in a runnable status, a requested kind; ``priority, created_at, id``) that does
    not :func:`needs_cuda`; a job that does is skipped and stays as it is. Each lease is one conditional ``UPDATE ...
    RETURNING`` that re-checks those conditions (a concurrent claimer makes it miss and the next candidate is tried);
    leasing increments ``attempt`` as ``acquire_lease`` does. The claim reclaims expired leases at the same ``now``
    right before, so the expired-lease condition, kept for parity with ``acquire_lease``, finds nothing
    unbilled."""
    cuda_kinds = _cuda_runner_kinds(conn).intersection(kinds)
    if not cuda_kinds:
        return repo.acquire_lease(conn, runner_id, now, ttl_s, kinds=kinds)
    statuses = list(repo.RUNNABLE_RUN_STATUSES)
    run_marks, kind_marks = ", ".join("?" * len(statuses)), ", ".join("?" * len(kinds))
    rows = conn.execute(
        "SELECT j.id, j.kind, j.run_id FROM jobs j JOIN runs r ON r.id = j.run_id "
        "WHERE (j.status = 'QUEUED' OR (j.status IN ('LEASED', 'RUNNING') AND j.lease_expires_at < ?)) "
        f"AND j.attempt < j.max_attempts AND r.status IN ({run_marks}) AND j.kind IN ({kind_marks}) "
        "ORDER BY j.priority, j.created_at, j.id", (now, *statuses, *kinds)).fetchall()
    protocols: dict[str, Any] = {}
    skipped = 0
    for job_id, kind, run_id in rows:
        if kind in cuda_kinds and run_id not in protocols:
            protocols[run_id] = (repo.get_run(conn, run_id) or {}).get("resolved_protocol") or {}
        if needs_cuda(protocols.get(run_id), kind, cuda_kinds):
            skipped += 1
            continue
        leased = conn.execute(
            "UPDATE jobs SET status = 'LEASED', lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?, "
            "attempt = attempt + 1, updated_at = ? WHERE id = ? AND attempt < max_attempts "
            "AND (status = 'QUEUED' OR (status IN ('LEASED', 'RUNNING') AND lease_expires_at < ?)) "
            f"AND run_id IN (SELECT id FROM runs WHERE status IN ({run_marks})) RETURNING id",
            (runner_id, iso_after(now, ttl_s), now, now, job_id, now, *statuses)).fetchone()
        if leased is not None:
            return repo.get_job(conn, leased[0])
    if skipped:
        log.debug("CPU runner %s: %d leasable job(s) left to an online CUDA runner (DD-A62 (d)); none leased",
                  runner_id, skipped)
    return None


def _fail_unrunnable(conn: sqlite3.Connection, job: dict[str, Any], runner_id: str, fail: JobFailure) -> None:
    """A leased job the core cannot hand out (baseline not AVAILABLE): final failure, nothing billed."""
    with tx(conn):
        result = JobResult(job_id=job["id"], status=JobStatus.FAILED, attempt=max(1, job["attempt"]),
                           reason_code=fail.reason_code, message=fail.message, finished_at=utcnow_iso())
        if repo.finish_job(conn, job["id"], runner_id, "FAILED", error=result.model_dump(mode="json"),
                           attempt=job["attempt"]):
            engine.job_finished(conn, repo.get_job(conn, job["id"]), result)


@router.post("/claim")
def claim(body: ClaimRequest, request: Request, conn: sqlite3.Connection = Depends(principal_db)) -> Response:
    check_runner(_principal(request), body.runner_id)
    requested = [k.value for k in body.kinds]
    outside = sorted(set(requested) - ALLOWED_KINDS)
    if outside:
        raise ApiError(400, "KIND_NOT_ALLOWED", f"runners may not claim {outside}; allowed: {sorted(ALLOWED_KINDS)}")
    settings = _settings(request)
    cpu_only = cpu_runner(_registered(conn, body.runner_id))  # mixed fleet -> CUDA jobs to the GPU
    if conn.execute("SELECT 1 FROM jobs WHERE lease_owner = ? AND status = 'RUNNING'", (body.runner_id,)).fetchone():
        raise ApiError(429, "RUNNER_BUSY", "this runner already runs a job (one job per runner)")
    for _ in range(CLAIM_ATTEMPTS):
        now = utcnow_iso()
        # reclaim right before leasing, at the same ``now`` (owner worker rule, owner_worker/main.py
        # tick): both lease paths below also take expired leases, which reclaim must bill first (lost row,
        # LEASE_EXPIRED, the next attempt billed as retry); one BEGIN IMMEDIATE transaction, serialised with the sweeper
        # and the owner worker, so a lease is never billed twice
        for reclaimed_id in sweeper.reclaim(conn, settings, now):
            _notify(request, (repo.get_job(conn, reclaimed_id) or {}).get("run_id"))
        # (a) re-delivery of a lease whose claim response was lost (E2(a))
        row = conn.execute("SELECT id FROM jobs WHERE lease_owner = ? AND status = 'LEASED' "
                           "ORDER BY updated_at, id LIMIT 1", (body.runner_id,)).fetchone()
        if row is not None:
            job = repo.get_job(conn, row[0])
        elif cpu_only:
            job = _lease_for_cpu_runner(conn, body.runner_id, now, settings.lease_ttl_s, requested)
        else:
            job = repo.acquire_lease(conn, body.runner_id, now, settings.lease_ttl_s, kinds=requested)
        with tx(conn):
            _seen(conn, body.runner_id, now)
        if job is None:
            return Response(status_code=204)
        try:
            with tx(conn, immediate=False):
                env = build_envelope(conn, job, runner_id=body.runner_id, settings=settings,
                                     claim_id=f"clm-{secrets.token_hex(8)}",
                                     core_contract=request.app.state.core_contract, kill_grace_s=KILL_GRACE_S,
                                     progress_interval_s=PROGRESS_INTERVAL_S)
        except JobFailure as fail:
            _fail_unrunnable(conn, job, body.runner_id, fail)
            _notify(request, job["run_id"])
            continue
        ticket = tickets.issue(_key(request), job["id"], int(job["attempt"]), stage_of(job), body.runner_id,
                               int(time.time() + settings.ticket_ttl_s))
        return JSONResponse({"envelope": env, "ticket": ticket, "server_time": utcnow_iso()})
    return Response(status_code=204)


# -- E3 start --------------------------------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/start")
def start(job_id: str, body: StartRequest, request: Request,
          conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    _job_write(request, job_id, body)
    with tx(conn):
        job = fenced_job(conn, job_id, body.runner_id, body.attempt)
        if job["status"] == "LEASED":
            if not jobs.start_attempt(conn, job_id, body.runner_id, attempt=body.attempt):
                raise lease_lost(job_id, body.attempt)
            job = repo.get_job(conn, job_id)
            engine.job_started(conn, job, body.runner_id)
            runner = _registered(conn, body.runner_id)
            _seen(conn, body.runner_id, utcnow_iso())
            _publish(conn, runner, "busy", job, event_run=job["run_id"])
        # RUNNING at the same attempt: a retried start whose response was lost -> same answer, no second start
    _notify(request, job["run_id"])
    return JSONResponse({"started_at": job["started_at"], "lease_expires_at": job["lease_expires_at"],
                         "server_time": utcnow_iso()})


# -- E4 heartbeat ----------------------------------------------------------------------------------------------------
def control_for(run_status: str) -> dict[str, Any]:
    """Heartbeat control flags from the run status (same decisions as ``Worker._monitor``)."""
    action = ("cancel" if run_status in ("CANCEL_REQUESTED", "CANCELLED") else
              "pause" if run_status == "PAUSE_REQUESTED" else None)
    return {"action": action, "run_status": run_status, "stop_requested": action is not None, "prune": None}


def _prune_orders(conn: sqlite3.Connection, runner_id: str, job_id: str, attempt: int, now: str) -> list[dict[str, Any]]:
    """The prune orders of this heartbeat (``retention.deliver_prune_orders``, in a SAVEPOINT of the caller's
    transaction). A failure is logged and answers no orders - it never costs the runner its lease renewal."""
    try:
        with tx(conn):
            return retention.deliver_prune_orders(conn, runner_id, job_id, attempt, now=now)
    except Exception:  # noqa: BLE001 - the orders come again on a later beat / lease
        log.exception("prune orders of runner %s (job %s attempt %s) not delivered", runner_id, job_id, attempt)
        return []


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: str, body: HeartbeatRequest, request: Request,
              conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    _job_write(request, job_id, body)
    settings = _settings(request)
    now = utcnow_iso()
    if not repo.renew_lease(conn, job_id, body.runner_id, now, settings.lease_ttl_s, attempt=body.attempt):
        raise lease_lost(job_id, body.attempt)
    with tx(conn):
        job = repo.get_job(conn, job_id)
        runner = _registered(conn, body.runner_id)
        _seen(conn, body.runner_id, now)
        _publish(conn, runner, "busy", job, progress=body.progress, sample=body.device_sample)
        run = repo.get_run(conn, job["run_id"])
        control = control_for(run["status"])
        control["prune"] = _prune_orders(conn, body.runner_id, job_id, body.attempt, now) or None
    return JSONResponse({"lease_expires_at": job["lease_expires_at"], "control": control,
                         "server_time": utcnow_iso()})


# -- E5 progress -----------------------------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/progress")
def progress(job_id: str, body: ProgressBatch, request: Request,
             conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    from mbnas_core.owner_worker.main import _numeric_training_progress

    _job_write(request, job_id, body)
    if len(body.messages) > MAX_BATCH_MESSAGES:
        raise ApiError(422, "VALIDATION_FAILED", f"at most {MAX_BATCH_MESSAGES} messages per batch")
    merged: dict[str, Any] = {}
    logs: list[dict[str, Any]] = []
    for msg in body.messages:
        if msg.job_id != job_id:
            raise ApiError(422, "VALIDATION_FAILED", "every message must belong to the job of the path")
        if msg.type == "progress":
            merged.update({k: v for k, v in msg.model_dump().items() if k in PROGRESS_KEYS and v is not None})
            merged.update(_numeric_training_progress(msg.payload))
        elif msg.type == "log" and (msg.payload.get("level") in ("error", "warning") or msg.payload.get("reason_code")):
            # the text lives in ProgressMessage.message (jobs protocol); payload.message is accepted as a fallback
            logs.append({**msg.payload, "message": msg.message or msg.payload.get("message")})
    with tx(conn):
        job = fenced_job(conn, job_id, body.runner_id, body.attempt, ("RUNNING",))
        now = utcnow_iso()
        repo.touch_job(conn, job_id, body.runner_id, now, attempt=body.attempt)  # liveness for lost-work billing
        for entry in logs:  # e.g. CHECKPOINT_REPORT_REJECTED by the runner daemon
            extra = {k: v for k, v in entry.items() if k in ("reason_code", "path", "reported_sha256", "computed_sha256")}
            engine.log(conn, job["run_id"], str(entry.get("message") or entry.get("reason_code") or "runner log")[:500],
                       level=str(entry.get("level") or "error"), job_id=job_id, **extra)
        if merged:
            engine.job_progress(conn, repo.get_job(conn, job_id), merged)
        _seen(conn, body.runner_id, now)
        _publish(conn, _registered(conn, body.runner_id), "busy", job, progress=merged, event_run=job["run_id"])
    _notify(request, job["run_id"])
    return JSONResponse({"accepted_seq": body.seq_start + len(body.messages) - 1})


# -- E6 checkpoints --------------------------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/checkpoints")
async def checkpoints(job_id: str, request: Request) -> JSONResponse:
    try:
        body = CheckpointReport.model_validate(await request.json())
    except ValidationError as exc:
        raise ApiError(422, "CHECKPOINT_REPORT_REJECTED", "checkpoint report rejected (format)",
                       errors=pydantic_errors(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "CHECKPOINT_REPORT_REJECTED", "checkpoint report is not JSON") from exc
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(_checkpoint_sync, request, job_id, body)


def _checkpoint_sync(request: Request, job_id: str, body: CheckpointReport) -> JSONResponse:
    from mbnas_core.db.core import connect

    _job_write(request, job_id, body)
    if not ID_RE.match(body.checkpoint_id):
        raise ApiError(422, "CHECKPOINT_REPORT_REJECTED", "checkpoint_id must match " + tickets.ID_PATTERN)
    conn = connect(_settings(request), init=False, check_same_thread=False)
    try:
        with tx(conn):
            job = fenced_job(conn, job_id, body.runner_id, body.attempt)
            if job.get("candidate_id") != body.candidate_id:
                raise ApiError(422, "CHECKPOINT_REPORT_REJECTED", "candidate_id differs from the job's candidate")
            existing = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (body.checkpoint_id,)).fetchone()
            if existing is not None:  # a retried report (response lost): same digest -> same answer
                if existing["sha256"] != body.sha256 or existing["run_id"] != job["run_id"]:
                    raise ApiError(409, "CHECKPOINT_CONFLICT", f"checkpoint {body.checkpoint_id} exists with another digest")
                locs = repo.list_artifact_locations(conn, "checkpoint", body.checkpoint_id)
                return JSONResponse({"checkpoint_id": body.checkpoint_id, "location_id": locs[-1]["id"] if locs else "",
                                     "state": existing["state"]})
            repo.insert_checkpoint(conn, id=body.checkpoint_id, run_id=job["run_id"], candidate_id=body.candidate_id,
                                   epoch=body.epoch, kind=body.kind, path=body.uri or body.relpath, sha256=body.sha256,
                                   bytes=body.bytes, rung=body.rung or job.get("rung"), hashes=body.hashes)
            loc = repo.insert_artifact_location(
                conn, id=f"loc-{secrets.token_hex(8)}", object_kind="checkpoint", object_id=body.checkpoint_id,
                run_id=job["run_id"], runner_id=body.runner_id, store="runner_local", relpath=body.relpath,
                uri=body.uri, sha256=body.sha256, bytes=body.bytes, verified_by=f"runner:{body.runner_id}",
                job_id=job_id, attempt=body.attempt)
            # older runner-held full states the run's policy deletes get their prune requests now
            engine.checkpoint_registered(conn, job, {"id": body.checkpoint_id, "candidate_id": body.candidate_id},
                                         default_policy=_settings(request).full_state_retention)
        return JSONResponse({"checkpoint_id": body.checkpoint_id, "location_id": loc["id"], "state": "PRESENT"})
    finally:
        conn.close()


# -- E7 artifacts ----------------------------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/artifacts")
def artifacts(job_id: str, body: ArtifactReport, request: Request,
              conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    _job_write(request, job_id, body)
    for item in body.artifacts:
        if item.kind not in ARTIFACT_KINDS or item.state not in ARTIFACT_STATES:
            raise ApiError(422, "VALIDATION_FAILED", f"artifact kind/state not allowed: {item.kind}/{item.state}")
    out = []
    with tx(conn):
        job = fenced_job(conn, job_id, body.runner_id, body.attempt)
        run_id = job["run_id"]
        for item in body.artifacts:
            art_id = "art-" + hashlib.sha256(f"{run_id}\0{item.relpath}\0{item.evaluation_id or ''}".encode()).hexdigest()[:20]
            row = repo.get_artifact(conn, run_id, art_id)
            changed = row is None or row.get("sha256") != item.sha256
            if row is None:
                row = repo.insert_artifact(conn, id=art_id, run_id=run_id, kind=item.kind, relpath=item.relpath,
                                           evaluation_id=item.evaluation_id,
                                           candidate_id=item.candidate_id or job.get("candidate_id"),
                                           sha256=item.sha256, mime_type=item.mime_type, bytes=item.bytes,
                                           sample_id=item.sample_id, split_hash=item.split_hash, state=item.state,
                                           meta={**item.meta, "registered_by": f"runner:{body.runner_id}"})
            elif changed:
                row = repo.update_artifact(conn, run_id, art_id, state=item.state, sha256=item.sha256, bytes=item.bytes,
                                           mime_type=item.mime_type)
            if changed:
                repo.insert_artifact_location(
                    conn, id=f"loc-{secrets.token_hex(8)}", object_kind="artifact", object_id=art_id, run_id=run_id,
                    runner_id=body.runner_id, store="runner_local", relpath=item.relpath, uri=None, sha256=item.sha256,
                    bytes=item.bytes, verified_by=f"runner:{body.runner_id}", job_id=job_id, attempt=body.attempt)
            out.append({"relpath": item.relpath, "artifact_id": art_id, "state": row["state"]})
    return JSONResponse(out)


# -- E8 result -------------------------------------------------------------------------------------------------------
def _idem(conn: sqlite3.Connection, job_id: str, attempt: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM idempotent_responses WHERE job_id = ? AND attempt = ?", (job_id, attempt)).fetchone()
    return dict(row) if row is not None else None


def _replay(row: dict[str, Any], body: ResultCommit, conn: sqlite3.Connection, job_id: str) -> JSONResponse | None:
    """Design 7.2 for an existing (job, attempt) row; None when no row exists."""
    if row["payload_digest"] != body.payload_digest:
        job = repo.get_job(conn, job_id)
        if job is not None:
            with tx(conn):
                engine.log(conn, job["run_id"], f"second, different result for {job_id} attempt {body.attempt} rejected",
                           level="error", job_id=job_id, reason_code="RESULT_CONFLICT")
        raise ApiError(409, "RESULT_CONFLICT", "a different result was already committed for this attempt")
    if row["state"] == "PROCESSING":
        raise ApiError(202, "PROCESSING", "the result is still being committed; retry", headers={"Retry-After": "2"})
    return JSONResponse(json.loads(row["response_json"]), status_code=int(row["status_code"] or 200))


def _store(conn: sqlite3.Connection, job_id: str, attempt: int, status: int, payload: dict[str, Any]) -> None:
    conn.execute("UPDATE idempotent_responses SET state = 'DONE', status_code = ?, response_json = ?, "
                 "response_digest = ?, committed_at = ? WHERE job_id = ? AND attempt = ?",
                 (status, json.dumps(payload, sort_keys=True), payload.get("response_digest"), utcnow_iso(),
                  job_id, attempt))


@router.post("/jobs/{job_id}/result")
def result(job_id: str, body: ResultCommit, request: Request,
           conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    from mbnas_core.runner_api.sweeper import apply_retention

    _job_write(request, job_id, body)
    if body.result.job_id != job_id or (body.result.attempt is not None and int(body.result.attempt) != body.attempt):
        raise ApiError(422, "VALIDATION_FAILED", "result.job_id/attempt must equal the path job and the fenced attempt")
    try:
        if parse_iso(body.started_at) > parse_iso(body.finished_at):
            raise ValueError("started_at after finished_at")
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_FAILED", f"started_at/finished_at invalid: {exc}") from exc
    existing = _idem(conn, job_id, body.attempt)
    if existing is not None:
        return _replay(existing, body, conn, job_id)  # type: ignore[return-value]
    settings = _settings(request)
    owned_job: dict[str, Any] | None = None
    try:
        with tx(conn):
            if _idem(conn, job_id, body.attempt) is not None:  # lost the race against a concurrent retry
                raise _Retry()
            now = utcnow_iso()
            conn.execute("INSERT INTO idempotent_responses (job_id, attempt, runner_id, payload_digest, state, "
                         "processing_since) VALUES (?, ?, ?, ?, 'PROCESSING', ?)",
                         (job_id, body.attempt, body.runner_id, body.payload_digest, now))
            current = repo.get_job(conn, job_id)
            if current is None:
                raise ApiError(404, "JOB_NOT_FOUND", f"job {job_id} not found")
            runner = _registered(conn, body.runner_id)
            if not owns(current, body.runner_id, body.attempt):
                if parse_iso(body.started_at) > parse_iso(now):  # started_at must not lie ahead of core time
                    raise ApiError(422, "VALIDATION_FAILED", "started_at lies in the future")
                # one physical row per (job, attempt): complements the sweeper's failed row
                costs.record_lost_attempt(conn, current | {"attempt": body.attempt}, started_at=body.started_at,
                                          finished_at=body.finished_at, reason_code="LEASE_LOST",
                                          device_uuid=body.device_uuid)
                engine.log(conn, current["run_id"], f"lease of {job_id} lost; result discarded", level="warning",
                           job_id=job_id, attempt=body.attempt, runner_id=body.runner_id)
                costs.refresh_run_costs(conn, current["run_id"])
                payload = {"code": "LEASE_LOST", "message": f"lease of {job_id} attempt {body.attempt} was lost; "
                                                            "result discarded", "request_id": None}
                _store(conn, job_id, body.attempt, 409, payload)
                status_code, run_id = 409, current["run_id"]
            else:
                run = repo.get_run(conn, current["run_id"])
                cancelled = run["status"] in ("CANCEL_REQUESTED", "CANCELLED") or body.cancel_observed
                started = current.get("started_at") or body.started_at  # this attempt's start (set by E3)
                final = settle_job(conn, current, body.result, body.result.exit_code, cancelled,
                                   body.runner_shutting_down, started, body.finished_at, body.runner_id,
                                   body.device_uuid, attempt=body.attempt, keep_discarded=True)
                costs.refresh_run_costs(conn, current["run_id"])
                _seen(conn, body.runner_id, utcnow_iso())
                _publish(conn, runner, "idle", None, event_run=current["run_id"])
                after = repo.get_job(conn, job_id)
                summary = {"outcome": final.status.value, "job_status": after["status"],
                           "run_status": repo.get_run(conn, current["run_id"])["status"]}
                payload = {**summary, "response_digest": canonical_sha256(summary)}
                _store(conn, job_id, body.attempt, 200, payload)
                status_code, run_id, owned_job = 200, current["run_id"], after
    except _Retry:
        return _replay(_idem(conn, job_id, body.attempt), body, conn, job_id)  # type: ignore[arg-type,return-value]
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - rolled back (incl. the PROCESSING row); the runner retries
        log.exception("result commit of %s attempt %s failed", job_id, body.attempt)
        raise ApiError(500, "COMMIT_FAILED", f"result commit failed and was rolled back ({type(exc).__name__}); retry") from exc
    _notify(request, run_id)
    if owned_job is not None:
        apply_retention(conn, settings, owned_job)
    if status_code == 409:
        payload = {**payload, "request_id": getattr(request.state, "request_id", None)}
    return JSONResponse(payload, status_code=status_code)


class _Retry(Exception):
    """Internal: another request inserted the idempotency row first."""


# -- E11 checkpoint pruned ------------------------------------------------------------------------------------------
@router.post("/checkpoints/{checkpoint_id}/pruned")
def checkpoint_pruned(checkpoint_id: str, body: PruneReport, request: Request,
                      conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    """A runner reports the outcome of a prune order (see module docstring): the checkpoint row becomes PRUNED."""
    check_runner(_principal(request), body.runner_id)
    now = utcnow_iso()
    with tx(conn):
        _registered(conn, body.runner_id)
        try:
            ack = retention.record_prune_report(conn, checkpoint_id, body.model_dump(mode="json"),
                                                runner_id=body.runner_id, now=now)
        except retention.PruneReportError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        _seen(conn, body.runner_id, now)
        row = conn.execute("SELECT run_id FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
    if not ack.get("replayed"):
        _notify(request, row["run_id"] if row is not None else None)
    return JSONResponse(ack)


# -- E9 ack / E10 deregister -----------------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/ack", status_code=204)
def ack(job_id: str, body: AckRequest, request: Request, conn: sqlite3.Connection = Depends(principal_db)) -> Response:
    _job_write(request, job_id, body)
    with tx(conn):
        n = conn.execute("UPDATE idempotent_responses SET acked_at = COALESCE(acked_at, ?) WHERE job_id = ? AND "
                         "attempt = ? AND runner_id = ? AND state = 'DONE'",
                         (utcnow_iso(), job_id, body.attempt, body.runner_id)).rowcount
    if n != 1:
        raise ApiError(404, "NOT_FOUND", f"no committed result for {job_id} attempt {body.attempt}")
    return Response(status_code=204)


@router.post("/deregister", status_code=204)
def deregister(body: DeregisterRequest, request: Request, conn: sqlite3.Connection = Depends(principal_db)) -> Response:
    check_runner(_principal(request), body.runner_id)
    runner = _runner(conn, body.runner_id)
    if runner is not None:
        with tx(conn):
            _publish(conn, runner, "offline", None)
            _seen(conn, body.runner_id, utcnow_iso(), status="OFFLINE")
    return Response(status_code=204)


# -- owner reads (scope admin) ---------------------------------------------------------------------------------------
@owner_router.get("/runners")
def owner_runners(conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    out = []
    for r in conn.execute("SELECT * FROM runners ORDER BY id"):
        row = dict(r)
        for key in ("kinds_json", "runtime_json", "data_mounts_json"):
            row[key.removesuffix("_json")] = json.loads(row.pop(key) or "null")
        out.append(row)
    return JSONResponse({"runners": out})


@owner_router.get("/principals")
def owner_principals(conn: sqlite3.Connection = Depends(principal_db)) -> JSONResponse:
    return JSONResponse({"principals": list_principals(conn)})


