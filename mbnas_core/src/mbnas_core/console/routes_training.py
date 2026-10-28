"""Authenticated explicit model selection → training/evaluation, sharing the ordinary run monitor/control API.

``POST /training-runs`` answers the contract ``TrainingRunCreated`` through the public projection
(:func:`mbnas_core.console.deps.contract_json`) like every other console route.

Gateway host inputs: the body may carry the same optional ``path_checks`` / ``dataset_yaml`` /
``dataset_manifests`` the gateway measured for validate (:mod:`mbnas_core.protocol.gateway_inputs`, not part of the
browser contract). They are removed before the request model is parsed and, when present, the training protocol is
validated with them (gateway path checks, the gateway's dataset YAML summary and split manifests) instead of core-local
path checks, and a ``mode=resume`` revalidation uses them too.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Request

from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.events.bus import EventBus
from mbnas_core.orchestration import state
from mbnas_core.training_runs.selection import TrainingRunRequest, TrainingSelectionError, create_training_run
from mbnas_core.console.deps import bus, contract_json, db, services
from mbnas_contracts.errors import ApiError
from mbnas_contracts.schemas.api import TrainingRunCreated
from mbnas_core.console.services import AppServices
from mbnas_core.console.projection import project_public, public_path

router = APIRouter()


def _same_checkpoint(given: str, ck: dict[str, Any], run_id: str) -> bool:
    """A resume ``checkpoint`` names the latest recorded full-state by its path, its public run-relative path (what a
    1.5.0 client sees) or its checkpoint id."""
    return given in (ck["path"], ck["id"], public_path(ck["path"], {run_id}))


@router.post("/training-runs")
def create_model_training(request: Request, body: dict[str, Any] = Body(...),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
        conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services), events: EventBus = Depends(bus)):
    from mbnas_core.console.routes_runs import gateway_inputs_of

    body = dict(body)
    gateway, _ = gateway_inputs_of(body)  # host inputs the gateway measured (never part of the request model)
    req = TrainingRunRequest.model_validate(body)
    if idempotency_key is not None and idempotency_key != req.idempotency_key:
        raise ApiError(409,"IDEMPOTENCY_KEY_MISMATCH","Idempotency-Key header differs from the request")
    try:
        if req.mode == "resume":
            if not req.source.run_id or not req.source.candidate_id:
                raise TrainingSelectionError("RESUME_SOURCE_REQUIRED","resume requires the existing training run and candidate")
            run = repo.get_run(conn,req.source.run_id)
            cand = repo.get_candidate(conn,req.source.candidate_id)
            if run is None or cand is None or cand["run_id"] != run["id"]:
                raise TrainingSelectionError("SOURCE_NOT_FOUND","resume source does not exist",404)
            # Exact trial continuation: reject a recipe replacement, never copy weights into a new scratch trial. The
            # stored protocol may come back as the client saw it (the 1.5.0 public projection of the run).
            stored = (run["requested_protocol"], run["resolved_protocol"])
            if req.protocol not in stored and req.protocol not in [project_public("Protocol", p) for p in stored]:
                raise TrainingSelectionError("RESUME_RECIPE_MISMATCH","resume must use the stored run protocol; use the run Resume control to preserve it",409)
            ck = repo.latest_checkpoint(conn,cand["id"],"training_full")
            if req.checkpoint:
                # a checkpoint path names the full-state to resume; only the latest full-state this
                # server recorded for the candidate qualifies. No recorded training_full at all -> the path is an
                # external file that cannot be imported into a run; a recorded one that differs -> mismatch.
                if ck is None:
                    raise TrainingSelectionError("EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED",
                        "this candidate has no recorded training_full checkpoint, and importing an external checkpoint "
                        "into a run is not supported; the supported path is resuming the latest recorded full-state of "
                        f"an existing paused run (POST /api/v1/runs/{run['id']}/resume)",409)
                if not _same_checkpoint(req.checkpoint, ck, run["id"]):
                    raise TrainingSelectionError("RESUME_CHECKPOINT_MISMATCH","resume requires the latest recorded full-state checkpoint",409)
            # No checkpoint path: ordinary run Resume path; state.resume_checks decides (e.g. a run paused
            # before its first epoch has no full-state yet and is resumed from its start).
            revalidate = svc.revalidate if gateway is None else (
                lambda resolved: svc.revalidate(resolved, gateway, local_fallback=True))
            report = state.resume_checks(conn,run,revalidate) if run["status"]=="PAUSED" else None
            with tx(conn):
                run, blocked = state.request_resume(conn,run["id"],report)
            if blocked: raise ApiError(blocked.status,blocked.code,blocked.message,errors=blocked.errors)
            created,cid=False,cand["id"]
        else:
            with tx(conn):
                run,created,cid=create_training_run(conn,req,svc,getattr(request.state,"request_id",None),gateway=gateway)
    except TrainingSelectionError as exc:
        raise ApiError(exc.status,exc.code,exc.message,errors=exc.errors) from exc
    except state.TransitionError as exc:
        raise ApiError(exc.status,exc.code,exc.message,errors=exc.errors) from exc
    events.notify(run["id"])
    return contract_json("TrainingRunCreated", TrainingRunCreated, {
        "run_id": run["id"], "status": run["status"], "protocol_hash": run["protocol_hash"], "created": created,
        "candidate_id": cid, "source": req.source.binding_dump(), "mode": req.mode})
