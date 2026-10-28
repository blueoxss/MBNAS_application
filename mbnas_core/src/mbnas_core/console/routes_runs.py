"""Protocol validation, idempotent run creation, run queries and pause/resume/cancel.

``POST /runs`` rules (contract ``createRun``):

1. ``Idempotency-Key`` header, when given, must equal ``body.idempotency_key`` (else 409 IDEMPOTENCY_KEY_MISMATCH).
2. Same key + same payload (canonical SHA-256 of ``{protocol, protocol_hash}``) returns the same run (200);
   same key + different payload is 409 IDEMPOTENCY_CONFLICT. Checked before anything else, so a timed-out retry
   never creates a second run.
3. The payload must match a stored validation (``validation_id``, else the latest validation with that hash; none:
   422 UNVALIDATED) and ``protocol_hash`` must equal both the canonical hash of ``protocol`` and the validation's
   hash (else 409 PROTOCOL_HASH_MISMATCH).
4. The stored resolved protocol is re-validated now; any change of dataset manifests, registry capability or
   devices yields a different hash: 409 STALE_VALIDATION (validate again). When the gateway sent its host inputs
   (``path_checks`` / ``dataset_yaml`` / ``dataset_manifests``, DD-A34/DD-A46) the re-validation uses them instead of
   core-local path checks, so a protocol whose dataset and ``output_root`` live on the customer host is not refused.
5. One transaction creates the QUEUED run (namespace/execution_mode/purpose/preset from the resolved protocol,
   frozen budget, source tree hash), plans the first jobs and emits ``run.updated``.

``GET /runs/{id}/snapshot`` lists the run's workers (S7-F4): workers attached to the run (``workers.run_id``, any
age) plus every non-offline worker whose heartbeat is within ``SNAPSHOT_WORKER_WINDOW_HEARTBEATS x heartbeat_s``
(alive, or recently lost: readers mark rows stale beyond 3 heartbeats + 5 s as ``lost``). Offline rows and long-dead
workers of other runs are not listed; workers delete rows older than a day at startup (``repo.prune_workers``).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request

from mbnas_core.artifacts import locations
from mbnas_core.artifacts.registry import run_dir
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.events.bus import EventBus, emit
from mbnas_core.orchestration import engine, final_retrain, state
from mbnas_core.orchestration.retention import checkpoint_views, store_verified_checkpoints
from mbnas_core.orchestration.views import generation_payload, lineage_view, run_payload, worker_payload
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_core.protocol.validator import validate
from mbnas_contracts.schemas.api import FinalRetrainRequest, RunCreated, RunList, RunRequest, ValidateRequest, ValidateResponse
from mbnas_contracts.schemas.records import CandidateDetail, Run, Snapshot, StructureReport
from mbnas_core.scheduling import admission, costs
from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.time import iso_after, utcnow_iso
from mbnas_core.console.deps import bus, contract_json, db, get_run_or_404, services
from mbnas_contracts.errors import ApiError, pydantic_errors
from mbnas_core.console.services import AppServices
from mbnas_core.protocol.gateway_inputs import BODY_FIELDS, GatewayInputs
from pydantic import ValidationError
from mbnas_core.console.views import evaluation_views
from mbnas_core.console.candidate_views import candidate_artifacts, generation_candidates

router = APIRouter()
log = logging.getLogger("mbnas_core.console")
SNAPSHOT_WORKER_WINDOW_HEARTBEATS = 24
DATASET_HASH_KEYS = ("train_manifest_hash", "search_validation_manifest_hash", "final_evaluation_manifest_hash",
                     "class_mapping_hash")


def _requested_path(svc: AppServices, validation_id: str) -> str:
    return f"{svc.settings.output_root}/validations/{validation_id}.requested.json"


def _gateway_path(svc: AppServices, validation_id: str) -> str:
    return f"{svc.settings.output_root}/validations/{validation_id}.gateway.json"


def gateway_inputs_of(body: dict[str, Any]) -> tuple[GatewayInputs | None, dict[str, Any]]:
    """Pop and parse the optional gateway host inputs (``path_checks`` / ``dataset_yaml`` / ``dataset_manifests``) of a
    validate, run-create, training-run or final-retrain body (DD-A34 / DD-A46); 422 VALIDATION_FAILED when malformed."""
    raw = {k: body.pop(k) for k in BODY_FIELDS if k in body}
    try:
        return GatewayInputs.parse(raw.get("path_checks"), raw.get("dataset_yaml"), raw.get("dataset_manifests")), raw
    except (ValueError, ValidationError) as exc:
        errors = pydantic_errors(exc) if isinstance(exc, ValidationError) else [
            {"field": "path_checks", "message": str(exc), "code": "FIELD_INVALID"}]
        raise ApiError(422, "VALIDATION_FAILED", "path_checks / dataset_yaml / dataset_manifests do not match their schema",
                       errors=errors) from exc


_gateway_inputs = gateway_inputs_of  # pre-DD-A46 name


@router.post("/protocols/validate")
def validate_protocol(body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(db),
                      svc: AppServices = Depends(services)):
    body = dict(body)
    # WP-3 section 1.3: optional host-side inputs measured by the gateway (not part of the browser contract)
    gateway, raw_inputs = _gateway_inputs(body)
    ValidateRequest.model_validate(body)
    requested = body["protocol"]
    result = validate(requested, svc.validation_context(gateway))
    validation_id = None
    if result.valid and result.resolved is not None:
        validation_id = f"val-{uuid.uuid4().hex[:16]}"
        dataset = result.resolved.get("dataset", {})
        with tx(conn):
            repo.insert_validation(conn, id=validation_id, protocol_hash=result.protocol_hash,  # type: ignore[arg-type]
                                   resolved=result.resolved, requested_sha256=canonical_sha256(requested),
                                   capabilities=result.capabilities,
                                   dataset_manifest_hashes={k: dataset.get(k) for k in DATASET_HASH_KEYS})
        atomic_write_json(_requested_path(svc, validation_id), requested)
        if gateway is not None:
            atomic_write_json(_gateway_path(svc, validation_id), raw_inputs)
    return contract_json("ValidateResponse", ValidateResponse, result.to_response(validation_id))


def _created(run: dict[str, Any], created: bool) -> Any:
    return contract_json("RunCreated", RunCreated, {"run_id": run["id"], "status": run["status"],
                                                    "protocol_hash": run["protocol_hash"], "created": created})


def _find_validation(conn: sqlite3.Connection, req: RunRequest) -> dict[str, Any]:
    if req.validation_id:
        val = repo.get_validation(conn, req.validation_id)
        if val is None:
            raise ApiError(422, "UNVALIDATED", f"validation {req.validation_id} does not exist; validate first")
        return val
    row = conn.execute("SELECT id FROM validations WHERE protocol_hash = ? ORDER BY created_at DESC LIMIT 1",
                       (req.protocol_hash,)).fetchone()
    if row is None:
        raise ApiError(422, "UNVALIDATED", "no server validation matches this protocol_hash; validate first")
    return repo.get_validation(conn, row[0])  # type: ignore[return-value]


def _check_hashes(body: dict[str, Any], req: RunRequest, val: dict[str, Any]) -> None:
    try:
        computed = protocol_hash(body["protocol"])
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "PROTOCOL_NOT_CANONICAL", f"protocol cannot be hashed: {exc}") from exc
    if computed != req.protocol_hash or val["protocol_hash"] != req.protocol_hash:
        raise ApiError(409, "PROTOCOL_HASH_MISMATCH", "protocol, protocol_hash and the validation do not agree",
                       errors=[{"field": "protocol_hash", "message": "hash mismatch", "code": "PROTOCOL_HASH_MISMATCH",
                                "requested": req.protocol_hash, "computed_from_protocol": computed,
                                "validation": val["protocol_hash"], "validation_id": val["id"]}])


def check_fresh(svc: AppServices, resolved: dict[str, Any], expected_hash: str, gateway: GatewayInputs | None = None,
                stored_hashes: dict[str, Any] | None = None) -> None:
    """Re-validate ``resolved`` and compare its hash with ``expected_hash``: 409 STALE_VALIDATION when it differs.

    With the host inputs the gateway measured for THIS request (``path_checks`` / ``dataset_yaml`` /
    ``dataset_manifests`` on ``POST /runs``, ``/training-runs`` or ``/final-retrain``, DD-A46) the validator uses them
    instead of core-local path checks, else it checks core-locally. The inputs recorded at validation time
    (``validations/<id>.gateway.json``) are audit only: re-using them would make the freshness check blind to data
    changed since validation."""
    valid, new_hash, errors = svc.revalidate(resolved) if gateway is None else svc.revalidate(resolved, gateway)
    if valid and new_hash == expected_hash:
        return
    stored = stored_hashes or {}
    now = {}
    if valid:
        dataset = (validate(resolved, svc.validation_context(gateway)).resolved or {}).get("dataset", {})
        now = {k: dataset.get(k) for k in DATASET_HASH_KEYS}
    changed = [{"field": f"dataset.{k}", "message": "changed since validation", "code": "STALE_VALIDATION",
                "validated": stored.get(k), "now": now.get(k)} for k in DATASET_HASH_KEYS if now and stored.get(k) != now.get(k)]
    raise ApiError(409, "STALE_VALIDATION", "the validated protocol no longer resolves to the same hash; validate again",
                   errors=changed or [dict(e, code=e.get("code") or "STALE_VALIDATION") for e in errors[:20]] or
                   [{"field": "protocol_hash", "message": f"now {new_hash}", "code": "STALE_VALIDATION"}])


def _check_fresh(svc: AppServices, val: dict[str, Any], gateway: GatewayInputs | None = None) -> None:
    """Freshness of a stored validation before ``POST /runs`` creates its run (:func:`check_fresh`)."""
    check_fresh(svc, val["resolved"], val["protocol_hash"], gateway, val.get("dataset_manifest_hashes"))


def write_protocol_copies(shell: dict[str, Any], resolved: dict[str, Any], requested: dict[str, Any], *,
                          best_effort: bool) -> None:
    """``<run dir>/protocol/{resolved,requested}.json`` on this host (audit copies; the database row is authoritative).

    ``best_effort`` (the gateway sent its host inputs, i.e. ``console.paths.output_root`` is a customer-host path that
    this core host may not be able to create, DD-A46): an ``OSError`` is logged and the run is still created - the
    runner writes its own copy into the run directory on the customer host."""
    try:
        atomic_write_json(run_dir(shell) / "protocol" / "resolved.json", resolved)
        atomic_write_json(run_dir(shell) / "protocol" / "requested.json", requested)
    except OSError as exc:
        if not best_effort:
            raise
        log.warning("run %s: protocol copies not written under the customer output_root on this host (%s); the "
                    "database keeps both protocols", shell.get("id"), type(exc).__name__)


def _load_requested(svc: AppServices, val: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        with open(_requested_path(svc, val["id"]), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return fallback


@router.post("/runs")
def create_run(request: Request, body: dict[str, Any] = Body(...),
               idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
               conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services),
               events: EventBus = Depends(bus)):
    body = dict(body)
    gateway, _ = _gateway_inputs(body)
    req = RunRequest.model_validate(body)
    if idempotency_key is not None and idempotency_key != req.idempotency_key:
        raise ApiError(409, "IDEMPOTENCY_KEY_MISMATCH", "Idempotency-Key header differs from body.idempotency_key")
    payload_sha = canonical_sha256({"protocol": body["protocol"], "protocol_hash": req.protocol_hash})
    existing = repo.get_run_by_idempotency_key(conn, req.idempotency_key)
    if existing is not None:
        if existing["payload_sha256"] != payload_sha:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "idempotency key already used with a different payload")
        return _created(existing, False)
    val = _find_validation(conn, req)
    _check_hashes(body, req, val)
    _check_fresh(svc, val, gateway)
    resolved = val["resolved"]
    console = resolved["console"]
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    requested = _load_requested(svc, val, body["protocol"])
    shell = {"id": run_id, "resolved_protocol": resolved}
    write_protocol_copies(shell, resolved, requested, best_effort=gateway is not None)
    try:
        with tx(conn):
            run, created = repo.create_run(
                conn, id=run_id, namespace=console.get("run_namespace") or "default", protocol_hash=val["protocol_hash"],
                resolved_protocol=resolved, requested_protocol=requested,
                execution_mode=console.get("execution_mode") or "PILOT", idempotency_key=req.idempotency_key,
                payload_sha256=payload_sha, purpose=console.get("purpose"), preset=console.get("preset"),
                validation_id=val["id"], budget=admission.frozen_budget(resolved),
                source_commit=svc.source().get("sha256"))
            if created:
                emit(conn, run_id, "run.updated", run_payload(run))
                engine.log(conn, run_id, "run created", validation_id=val["id"], request_id=request.state.request_id)
                engine.plan_run(conn, run_id)
                run = engine.settle_run(conn, run_id)
    except repo.IdempotencyConflict as exc:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", str(exc)) from exc
    events.notify(run["id"])
    return _created(run, created)


@router.get("/runs")
def list_runs(namespace: str | None = Query(None), limit: int = Query(100, ge=1, le=500),
              conn: sqlite3.Connection = Depends(db)):
    runs = [run_payload(r) for r in repo.list_runs(conn, namespace=namespace, limit=limit)]
    return contract_json("RunList", RunList, {"runs": runs})


def _run_view(conn: sqlite3.Connection, run: dict[str, Any]) -> dict[str, Any]:
    """Run payload with the resolved protocol and, for a search run, its FINAL_RETRAIN children."""
    out = run_payload(run, include_protocol=True)
    children = repo.list_child_runs(conn, run["id"])
    if children:
        out["final_retrain_runs"] = [{"id": c["id"], "status": c["status"], "created_at": c["created_at"],
                                      "finalist_count": len(c.get("finalists") or [])} for c in children]
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: str, conn: sqlite3.Connection = Depends(db)):
    run = get_run_or_404(conn, run_id)
    return contract_json("Run", Run, {**_run_view(conn, run), "last_seq": int(run["last_seq"])})


@router.post("/runs/{run_id}/final-retrain")
def create_final_retrain(request: Request, run_id: str, body: dict[str, Any] = Body(...),
                         idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                         conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services),
                         events: EventBus = Depends(bus)):
    """Create (or return, for the same key + payload) the FINAL_RETRAIN child of a COMPLETED search run.

    Order: 404 RUN_NOT_FOUND; 409 BUDGET_INVALID (``budget_gpu_hours`` <= 0 / non-finite); 422 schema; 409
    IDEMPOTENCY_KEY_MISMATCH (header != body key); same key: same child (200) or 409 IDEMPOTENCY_CONFLICT; 409
    INVALID_STATE (parent not a COMPLETED SEARCH run); 409 STALE_VALIDATION (only when the gateway sent its host
    inputs, DD-A46: the parent protocol re-validated with them no longer resolves to its protocol_hash); 422
    FINAL_RECIPE_INVALID; 409 NO_FINALIST (logged on the parent); else the child run is created, planned and returned
    (RunCreated + parent_run_id/finalist_count). Without gateway inputs the parent protocol is not re-validated (the
    child reuses the parent's frozen resolved protocol and hash).
    """
    get_run_or_404(conn, run_id)
    body = dict(body)
    budget = body.get("budget_gpu_hours")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and not (math.isfinite(budget) and budget > 0):
        raise ApiError(409, "BUDGET_INVALID", "budget_gpu_hours must be a finite number of GPU hours > 0",
                       errors=[{"field": "budget_gpu_hours", "message": str(budget), "code": "BUDGET_INVALID"}])
    gateway, _ = gateway_inputs_of(body)  # DD-A46: optional host inputs (422 VALIDATION_FAILED when malformed)
    req = FinalRetrainRequest.model_validate(body)
    if idempotency_key is not None and idempotency_key != req.idempotency_key:
        raise ApiError(409, "IDEMPOTENCY_KEY_MISMATCH", "Idempotency-Key header differs from body.idempotency_key")

    def fresh(parent: dict[str, Any]) -> None:  # DD-A46: gateway inputs re-validate the parent's frozen protocol
        if gateway is not None:
            check_fresh(svc, parent["resolved_protocol"], parent["protocol_hash"], gateway)

    def write_protocol(child_id: str) -> None:
        parent = repo.get_run(conn, run_id)
        shell = {"id": child_id, "resolved_protocol": parent["resolved_protocol"]}
        write_protocol_copies(shell, parent["resolved_protocol"], parent["requested_protocol"],
                              best_effort=gateway is not None)

    try:
        child, created = final_retrain.request_final_retrain(
            conn, run_id, req, source_commit=svc.source().get("sha256"), request_id=request.state.request_id,
            on_child_dir=write_protocol, check_fresh=fresh)
    except final_retrain.FinalRetrainError as exc:
        events.notify(run_id)
        raise ApiError(exc.status, exc.code, exc.message, errors=exc.errors) from exc
    events.notify(child["id"])
    events.notify(run_id)
    return contract_json("RunCreated", RunCreated, {
        "run_id": child["id"], "status": child["status"], "protocol_hash": child["protocol_hash"], "created": created,
        "parent_run_id": run_id, "finalist_count": len(child.get("finalists") or []), "kind": child["kind"]})


@router.get("/runs/{run_id}/snapshot")
def snapshot(request: Request, run_id: str, conn: sqlite3.Connection = Depends(db),
             svc: AppServices = Depends(services)):
    with tx(conn, immediate=False):  # one consistent read view
        run = get_run_or_404(conn, run_id)
        evals = evaluation_views(conn, run_id, repo.list_evaluations(conn, run_id, include_superseded=True),
                                 base_url=str(request.base_url), svc=svc)
        last = int(run["last_seq"])
        events = repo.get_events_after(conn, run_id, max(0, last - svc.snapshot_event_limit), svc.snapshot_event_limit)
        body = {"run": _run_view(conn, run), "candidates": evals,
                "generated_candidates": generation_candidates(conn, run_id, base_url=str(request.base_url), svc=svc),
                "generations": [generation_payload(g) for g in repo.list_generations(conn, run_id)],
                "workers": [worker_payload(w, heartbeat_s=svc.settings.heartbeat_s)
                            for w in repo.list_workers_for_run(conn, run_id, iso_after(
                                utcnow_iso(), -SNAPSHOT_WORKER_WINDOW_HEARTBEATS * svc.settings.heartbeat_s))],
                "events": events, "last_seq": last}
    return contract_json("Snapshot", Snapshot, body)


@router.get("/runs/{run_id}/candidates/{candidate_id}")
def candidate_detail(request: Request, run_id: str, candidate_id: str, conn: sqlite3.Connection = Depends(db),
                     svc: AppServices = Depends(services)):
    with tx(conn, immediate=False):
        run = get_run_or_404(conn, run_id)
        cand = repo.get_candidate(conn, candidate_id)
        if cand is None or cand["run_id"] != run_id:
            raise ApiError(404, "CANDIDATE_NOT_FOUND", f"candidate {candidate_id} not found in run {run_id}")
        evals = evaluation_views(conn, run_id, repo.list_evaluations(conn, run_id, candidate_id=candidate_id),
                                 base_url=str(request.base_url), svc=svc)
        from mbnas_core.console.model_services import describe_candidate
        structure = describe_candidate(cand, run)
        body = {"candidate_id": cand["id"], "run_id": run_id, "name": cand["name"], "arm": cand["arm"],
                "family": cand["family"], "architecture_hash": cand.get("architecture_hash") or "unresolved",
                "architecture_yaml": cand.get("architecture_yaml"), "architecture_path": cand.get("architecture_path"),
                "blocks": cand["blocks"], "parents": cand["parents"],
                "parent_evaluation_ids": cand["parent_evaluation_ids"], "lineage": lineage_view(cand["lineage"]),
                "evaluations": evals,
                # artifact_id = the checkpoint id once the gateway store holds verified bytes (DD-A55 a, DD-A51 c)
                "checkpoints": checkpoint_views(repo.list_checkpoints(conn, run_id, candidate_id=candidate_id),
                                                locations.checkpoint_states(conn, run_id),
                                                stored=store_verified_checkpoints(conn, run_id)),
                "cost": costs.candidate_seconds(conn, candidate_id),
                "gate_history": [{"evaluation_id": e["id"], "rung": e["rung"], **(e.get("gate") or {})}
                                 for e in evals if e.get("gate")],
                "candidate_status": cand["candidate_status"], "current_rung": cand.get("current_rung"),
                "audit": cand.get("audit") or {}, "search_seed": cand.get("search_seed"),
                "training_seed": cand.get("training_seed"), "generation": cand["generation"],
                "structure": structure, "structure_status": structure.get("status", "UNVERIFIED"),
                "evaluation_status": "EVALUATED" if any(e['status'] == 'EVALUATED' for e in evals) else "NOT_EVALUATED",
                "origin": (cand.get('audit') or {}).get('origin') or ('baseline' if cand['arm'] == 'Baseline' else 'search'),
                "artifacts": candidate_artifacts(conn, run_id, candidate_id, base_url=str(request.base_url), svc=svc)}
    return contract_json("CandidateDetail", CandidateDetail, body)


STRUCTURE_HISTORY_MAX = 5


def _archive_structure(audit: dict[str, Any]) -> None:
    """Move the evidence currently in ``audit['structure']`` into ``audit['structure_history']`` (DD-7).

    History is chronological (oldest first, newest last) and capped at the 5 most recent entries; each entry keeps
    ``checked_at`` (the evidence's own ``observation.checked_at`` when it has one), ``yaml_hash``, ``status`` and the
    ``observation`` block, plus ``archived_at`` (when it was superseded). No idempotency key: the check is pure.
    """
    previous = audit.get('structure')
    if not isinstance(previous, dict) or not previous:
        return
    observation = previous.get('observation') if isinstance(previous.get('observation'), dict) else None
    entry = {'checked_at': (observation or {}).get('checked_at') or previous.get('checked_at'),
             'yaml_hash': previous.get('yaml_hash'), 'status': previous.get('status', 'UNVERIFIED'),
             'observation': observation, 'archived_at': utcnow_iso()}
    history = [h for h in (audit.get('structure_history') or []) if isinstance(h, dict)]
    audit['structure_history'] = (history + [entry])[-STRUCTURE_HISTORY_MAX:]


@router.post('/runs/{run_id}/candidates/{candidate_id}/structure-check')
def check_candidate_structure(run_id: str, candidate_id: str, conn: sqlite3.Connection = Depends(db),
                              events: EventBus = Depends(bus)):
    """Explicit CPU-only verification; ordinary reads never rebuild or run inference."""
    from mbnas_core.console.model_services import run_structure_check
    run = get_run_or_404(conn, run_id)
    cand = repo.get_candidate(conn, candidate_id)
    if cand is None or cand['run_id'] != run_id:
        raise ApiError(404, 'CANDIDATE_NOT_FOUND', 'candidate not found in this run')
    result = run_structure_check(cand, run)
    with tx(conn):
        audit = dict(cand.get('audit') or {})
        _archive_structure(audit)  # DD-7: audit.structure stays the latest evidence, the previous one is kept
        audit['structure'] = result
        repo.update_candidate(conn, candidate_id, audit=audit)
        engine.log(conn, run_id, 'explicit structure check finished', candidate_id=candidate_id,
                   structure_status=result.get('status', 'UNVERIFIED'))
    events.notify(run_id)
    return contract_json("StructureReport", StructureReport, result, run_id=run_id)


def _reason(body: dict[str, Any] | None) -> str | None:
    reason = (body or {}).get("reason")
    return str(reason)[:500] if reason is not None else None


def _transition(conn: sqlite3.Connection, events: EventBus, run_id: str, fn: Any, *args: Any) -> Any:
    get_run_or_404(conn, run_id)
    try:
        with tx(conn):
            run = fn(conn, run_id, *args)
    except state.TransitionError as exc:
        raise ApiError(exc.status, exc.code, exc.message, errors=exc.errors) from exc
    events.notify(run_id)
    return contract_json("Run", Run, run_payload(run))


@router.post("/runs/{run_id}/pause")
def pause(run_id: str, body: dict[str, Any] | None = Body(None), conn: sqlite3.Connection = Depends(db),
          events: EventBus = Depends(bus)):
    return _transition(conn, events, run_id, state.request_pause, _reason(body))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, body: dict[str, Any] | None = Body(None), conn: sqlite3.Connection = Depends(db),
           events: EventBus = Depends(bus)):
    return _transition(conn, events, run_id, state.request_cancel, _reason(body))


@router.post("/runs/{run_id}/resume")
def resume(run_id: str, body: dict[str, Any] | None = Body(None), conn: sqlite3.Connection = Depends(db),
           svc: AppServices = Depends(services), events: EventBus = Depends(bus)):
    run = get_run_or_404(conn, run_id)
    # DD-A46 (same rule as run creation): host inputs the gateway measured re-validate the protocol on resume
    gateway = gateway_inputs_of(dict(body))[0] if isinstance(body, dict) else None
    revalidate = svc.revalidate if gateway is None else (lambda resolved: svc.revalidate(resolved, gateway))
    report = state.resume_checks(conn, run, revalidate) if run["status"] == "PAUSED" else None
    try:
        with tx(conn):
            run, blocked = state.request_resume(conn, run_id, report)
    except state.TransitionError as exc:
        raise ApiError(exc.status, exc.code, exc.message, errors=exc.errors) from exc
    events.notify(run_id)
    if blocked is not None:
        raise ApiError(blocked.status, blocked.code, blocked.message, errors=blocked.errors)
    return contract_json("Run", Run, run_payload(run))
