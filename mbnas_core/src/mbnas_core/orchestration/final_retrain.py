"""Separate final retraining of a COMPLETED search run (contract 1.2.0 ``POST /runs/{run_id}/final-retrain``).

The request never changes the search run. It creates a **child run** (``kind=FINAL_RETRAIN``, ``parent_run_id``) that
reuses the parent's resolved protocol unchanged (same ``protocol_hash``) and adds two run-level fields: the Final cap
``budget.final_s`` from the request and the frozen ``finalists``.

Finalist rule (:data:`SELECTION_RULE_ID`, frozen and recorded verbatim in every ``finalists[].selection_rule``):
among GA/IndependentRandom candidates with an immutable, non-superseded EVALUATED row at the LAST search rung
(``resolved rungs[-1].id``) whose ``latency_source`` is ``measured``, ``latency_ms <= hard_constraints.latency_le_ms``
and ``size_mb <= hard_constraints.weights_size_le_MB``: highest AP, tie-break lower ``latency_ms``, then
``candidate_id``; ``top_k`` per (arm, search_seed) (request ``top_k_per_arm_per_seed``, else the protocol's
``final_retraining.top_k_per_arm_per_seed``, else 1). No eligible row -> ``NO_FINALIST`` (409); a lower rung is never
substituted. The final evaluation split is never used to choose finalists (only search_val rows are read).

Each finalist becomes a NEW candidate (new trial id, arm ``Final``, name ``FINAL-<arm>-s<seed>-<source name>``,
lineage ``{operation: final, source_candidate_id, source_evaluation_id}``, parents ``[source]``, architecture and
blocks copied) trained from scratch with the pinned final recipe (``final_retraining.epoch_target_total`` /
``scheduler_horizon_epochs``) by one FINAL_RETRAIN job (rung ``FINAL``) admitted against the Final cap, then evaluated
on the final split (comparison_group ``final``). The training seed comes from the protocol's seed policy
(``training.seed_policy`` base, else the search seed) and the source candidate id.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
import uuid
from typing import Any

from mbnas_core.db import repo
from mbnas_core.events.bus import emit
from mbnas_contracts.schemas.api import FinalRetrainRequest
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_core.orchestration import engine
from mbnas_core.orchestration.views import run_payload

SELECTION_RULE_ID = "mbnas-final-select/1"
NO_FINALIST_MESSAGE = "no eligible last-rung finalist; lower rungs are never substituted"
FINAL_SEED_ID = "mbnas-final-train-seed/1"
SEARCH_ARMS = engine.SEARCH_ARMS


class FinalRetrainError(Exception):
    """Rejected final-retrain request; the API maps it to an Error body with ``status``."""

    def __init__(self, status: int, code: str, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.errors = status, code, message, errors


# -- the frozen rule ---------------------------------------------------------------------------------------------
def default_top_k(resolved: dict[str, Any]) -> int:
    value = (resolved.get("final_retraining") or {}).get("top_k_per_arm_per_seed")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def selection_rule(resolved: dict[str, Any], top_k: int) -> str:
    """Human- and machine-readable statement of the frozen rule (stored on every finalist)."""
    hc = resolved.get("hard_constraints") or {}
    last = _last_rung_id(resolved) or "<none>"
    return (f"{SELECTION_RULE_ID}: GA/IndependentRandom candidates with an immutable non-superseded EVALUATED row at "
            f"the last search rung {last}, latency_source=measured, latency_ms<={hc.get('latency_le_ms')}, "
            f"size_mb<={hc.get('weights_size_le_MB')}; highest AP, tie-break lower latency_ms, then candidate_id; "
            f"top {top_k} per (arm, search_seed); lower rungs are never substituted")


def _last_rung_id(resolved: dict[str, Any]) -> str | None:
    """Id of the last resolved search rung, or None when the protocol has no usable rungs (e.g. evaluation OFF)."""
    rungs = resolved.get("rungs")
    if not isinstance(rungs, list) or not rungs:
        return None
    last = rungs[-1]
    rung_id = last.get("id") if isinstance(last, dict) else None
    return rung_id if isinstance(rung_id, str) and rung_id else None


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def eligible_rows(conn: sqlite3.Connection, parent: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows that satisfy the rule's filters (before ranking)."""
    resolved = parent["resolved_protocol"]
    last = _last_rung_id(resolved)
    if last is None:  # no usable rung -> no evaluated finalist can exist
        return []
    hc = resolved.get("hard_constraints") or {}
    lat_cap, size_cap = hc.get("latency_le_ms"), hc.get("weights_size_le_MB")
    out = []
    for ev in repo.list_evaluations(conn, parent["id"], include_superseded=False):
        row = ev["row"]
        if not ev["immutable"] or ev["status"] != "EVALUATED" or row.get("arm") not in SEARCH_ARMS:
            continue
        if row.get("rung") != last or row.get("latency_source") != "measured":
            continue
        ap, lat, size = row.get("ap"), row.get("latency_ms"), row.get("size_mb")
        if not (_finite(ap) and _finite(lat) and _finite(size)):
            continue
        if not (_finite(lat_cap) and lat <= lat_cap and _finite(size_cap) and size <= size_cap):
            continue
        out.append(row)
    return out


def select_finalists(conn: sqlite3.Connection, parent: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    """Frozen finalists of ``parent`` (see module docstring), ordered by arm, search seed, rank."""
    resolved = parent["resolved_protocol"]
    rule = selection_rule(resolved, top_k)
    groups: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
    for row in eligible_rows(conn, parent):
        groups.setdefault((row["arm"], row.get("search_seed")), []).append(row)
    finalists = []
    for (arm, seed) in sorted(groups, key=lambda k: (k[0], -1 if k[1] is None else k[1])):
        ranked = sorted(groups[(arm, seed)], key=lambda r: (-float(r["ap"]), float(r["latency_ms"]), str(r["candidate_id"])))
        for rank, row in enumerate(ranked[:top_k], start=1):
            finalists.append({"candidate_id": row["candidate_id"], "source_evaluation_id": row["id"], "arm": arm,
                              "search_seed": seed, "rung": row["rung"], "ap": float(row["ap"]),
                              "latency_ms": float(row["latency_ms"]), "size_mb": float(row["size_mb"]),
                              "fitness": row.get("fitness"), "rank": rank, "name": row.get("name"),
                              "evaluation_hash": row.get("evaluation_hash"), "split_hash": row.get("split_hash"),
                              "selection_rule": rule})
    return finalists


def final_recipe(resolved: dict[str, Any]) -> dict[str, Any]:
    """Pinned final recipe (epochs, horizon, init) of the parent protocol; 422 FINAL_RECIPE_INVALID otherwise."""
    final = resolved.get("final_retraining") or {}
    target, horizon = final.get("epoch_target_total"), final.get("scheduler_horizon_epochs", final.get("epoch_target_total"))
    init_raw = str(final.get("initialization") or "scratch")
    init = "pretrained_native" if init_raw.startswith("pretrained_native") else "scratch" if init_raw.startswith(
        "scratch") else None
    problems = []
    if not (isinstance(target, int) and not isinstance(target, bool) and target >= 1):
        problems.append({"field": "final_retraining.epoch_target_total", "message": "integer >= 1 required",
                         "code": "FINAL_RECIPE_INVALID"})
    if not (isinstance(horizon, int) and not isinstance(horizon, bool) and isinstance(target, int) and horizon >= target):
        problems.append({"field": "final_retraining.scheduler_horizon_epochs", "message": "integer >= epoch_target_total",
                         "code": "FINAL_RECIPE_INVALID"})
    if init is None:
        problems.append({"field": "final_retraining.initialization", "message": f"unsupported {init_raw!r}",
                         "code": "FINAL_RECIPE_INVALID"})
    if problems:
        raise FinalRetrainError(422, "FINAL_RECIPE_INVALID", "the parent protocol has no usable pinned final recipe",
                                errors=problems)
    return {"epoch_target_total": int(target), "scheduler_horizon_epochs": int(horizon), "init": init,
            "initialization": init_raw, "rung": engine.FINAL_STAGE, "comparison_group": engine.FINAL_GROUP}


def final_training_seed(resolved: dict[str, Any], search_seed: int | None, source_candidate_id: str) -> tuple[int, dict[str, Any]]:
    """Training seed of a final candidate from the protocol seed policy (base) and the finalist source id."""
    from mbnas_core.search.streams import training_seed_policy

    policy = training_seed_policy(resolved, int(search_seed or 0))
    text = f"{FINAL_SEED_ID}|base={policy['base']}|source={source_candidate_id}"
    seed = int.from_bytes(hashlib.sha256(text.encode("ascii")).digest()[:8], "big") % (2 ** 31)
    return seed, {**policy, "final_id": FINAL_SEED_ID, "final_derivation": "sha256(id|base|source)[:8] mod 2**31"}


# -- request handling --------------------------------------------------------------------------------------------
def scoped_key(parent_id: str, key: str) -> str:
    """Child runs share the runs.idempotency_key namespace: scope the client's key to its parent run."""
    return f"final-retrain|{parent_id}|{key}"


def payload_sha(parent_id: str, req: FinalRetrainRequest) -> str:
    return canonical_sha256({"parent_run_id": parent_id, "request": req.model_dump(mode="json")})


def existing_child(conn: sqlite3.Connection, parent_id: str, req: FinalRetrainRequest) -> dict[str, Any] | None:
    """The child created earlier for this key (same payload), or None; a different payload is 409."""
    child = repo.get_run_by_idempotency_key(conn, scoped_key(parent_id, req.idempotency_key))
    if child is not None and child["payload_sha256"] != payload_sha(parent_id, req):
        raise FinalRetrainError(409, "IDEMPOTENCY_CONFLICT",
                                "idempotency key already used for this run with a different final-retrain payload")
    return child


def check_preconditions(parent: dict[str, Any]) -> None:
    # DD-4: an evaluation-free (generation-only) run has no evaluated finalists, whatever its status.
    search = (((parent.get("resolved_protocol") or {}).get("console") or {}).get("search") or {})
    if search.get("evaluation_enabled", True) is False:
        raise FinalRetrainError(409, "FINAL_RETRAIN_NOT_APPLICABLE",
                                f"run {parent['id']} was generated with evaluation_enabled=false; it has no evaluated "
                                "finalists, so final retraining does not apply (train a generated candidate through "
                                "POST /training-runs instead)",
                                errors=[{"field": "console.search.evaluation_enabled", "message": "false",
                                         "code": "FINAL_RETRAIN_NOT_APPLICABLE"}])
    if (parent.get("kind") or "SEARCH") != "SEARCH" or parent["status"] != "COMPLETED":
        raise FinalRetrainError(409, "INVALID_STATE", f"final retraining needs a COMPLETED SEARCH run; run {parent['id']} "
                                f"is {parent.get('kind') or 'SEARCH'} / {parent['status']}",
                                errors=[{"field": "status", "message": parent["status"], "code": "INVALID_STATE"}])


def child_budget(parent: dict[str, Any], req: FinalRetrainRequest, top_k: int) -> dict[str, Any]:
    return {"unit": "gpu_seconds", "scope": "final_retrain", "final_s": float(req.budget_gpu_hours) * 3600.0,
            "requested_gpu_hours": float(req.budget_gpu_hours), "search_per_arm_seed_s": None, "baseline_s": None,
            "top_k_per_arm_per_seed": top_k, "parent_run_id": parent["id"],
            "policy": "FINAL_RETRAIN jobs are admitted against final_s (estimate = finalist source measured "
                      "seconds/epoch x final epochs; unknown estimate admits while remaining > 0)"}


def create_child(conn: sqlite3.Connection, parent: dict[str, Any], req: FinalRetrainRequest,
                 finalists: list[dict[str, Any]], recipe: dict[str, Any], *, child_id: str, top_k: int,
                 source_commit: str | None, request_id: str | None = None) -> tuple[dict[str, Any], bool]:
    """Child run + one Final candidate per finalist + planning (inside the caller's tx); ``(child, created)``."""
    resolved = parent["resolved_protocol"]
    try:
        child, created = _create_run(conn, parent, req, child_id=child_id, top_k=top_k, finalists=finalists,
                                     resolved=resolved, source_commit=source_commit)
    except repo.IdempotencyConflict as exc:
        raise FinalRetrainError(409, "IDEMPOTENCY_CONFLICT", str(exc)) from exc
    if not created:
        return child, False
    return _populate(conn, parent, child, req, finalists, recipe, request_id=request_id), True


def _create_run(conn: sqlite3.Connection, parent: dict[str, Any], req: FinalRetrainRequest, *, child_id: str,
                top_k: int, finalists: list[dict[str, Any]], resolved: dict[str, Any],
                source_commit: str | None) -> tuple[dict[str, Any], bool]:
    return repo.create_run(
        conn, id=child_id, namespace=parent["namespace"], protocol_hash=parent["protocol_hash"],
        resolved_protocol=resolved, requested_protocol=parent["requested_protocol"],
        execution_mode=parent["execution_mode"], idempotency_key=scoped_key(parent["id"], req.idempotency_key),
        payload_sha256=payload_sha(parent["id"], req), purpose=parent.get("purpose"), preset=parent.get("preset"),
        validation_id=parent.get("validation_id"), budget=child_budget(parent, req, top_k), source_commit=source_commit,
        kind="FINAL_RETRAIN", parent_run_id=parent["id"], finalists=finalists)


def _populate(conn: sqlite3.Connection, parent: dict[str, Any], child: dict[str, Any], req: FinalRetrainRequest,
              finalists: list[dict[str, Any]], recipe: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
    child_id = child["id"]
    emit(conn, child_id, "run.updated", run_payload(child))
    engine.log(conn, child_id, "final retrain run created", parent_run_id=parent["id"], request_id=request_id,
               reason=req.reason, finalists=[f["candidate_id"] for f in finalists], recipe=recipe,
               selection_rule=finalists[0]["selection_rule"] if finalists else None)
    for f in finalists:
        _insert_final_candidate(conn, parent, child_id, f, recipe)
    engine.log(conn, parent["id"], f"final retrain child {child_id} created with {len(finalists)} finalist(s)",
               child_run_id=child_id, finalists=[f["candidate_id"] for f in finalists], request_id=request_id)
    engine.plan_final(conn, child_id)
    return engine.settle_run(conn, child_id)


def _insert_final_candidate(conn: sqlite3.Connection, parent: dict[str, Any], child_id: str, f: dict[str, Any],
                            recipe: dict[str, Any]) -> dict[str, Any]:
    src = repo.get_candidate(conn, f["candidate_id"])
    if src is None or src["run_id"] != parent["id"]:
        raise FinalRetrainError(409, "NO_FINALIST", f"finalist source {f['candidate_id']} not found in {parent['id']}")
    tag = engine.ARM_TAG.get(f["arm"], "x")
    cid = f"{child_id}.final.{tag}{f['search_seed']}.k{int(f['rank']):02d}"
    seed, seed_policy = final_training_seed(parent["resolved_protocol"], f.get("search_seed"), src["id"])
    lineage = {"operation": "final", "source_candidate_id": src["id"], "source_evaluation_id": f["source_evaluation_id"],
               "source_run_id": parent["id"], "source_arm": f["arm"], "source_rung": f["rung"],
               "source_name": src["name"], "rank": f["rank"], "selection_rule": f["selection_rule"],
               "init": recipe["init"], "recipe": recipe, "template": (src.get("lineage") or {}).get("template")}
    src_audit = src.get("audit") or {}
    src_orch = (src_audit.get("orchestration") or {})
    final_meta = {"source_candidate_id": src["id"], "source_evaluation_id": f["source_evaluation_id"],
                  "source_run_id": parent["id"], "rank": f["rank"], "recipe": recipe, "training_seed_policy": seed_policy}
    audit = {"proposal": src_audit.get("proposal") or {}, "built": src_audit.get("built"),
             "orchestration": {"comparison_group": engine.FINAL_GROUP, "init": recipe["init"], "final": final_meta,
                               "requested_input_shape": src_orch.get("requested_input_shape"),
                               "resolved_input_shape": src_orch.get("resolved_input_shape")}}
    cand = repo.insert_candidate(
        conn, id=cid, run_id=child_id, name=f"FINAL-{f['arm']}-s{f['search_seed']}-{src['name']}", arm="Final",
        family=src["family"], generation=src["generation"], search_seed=f.get("search_seed"), training_seed=seed,
        architecture_hash=src.get("architecture_hash"), architecture_yaml=src.get("architecture_yaml"),
        architecture_path=src.get("architecture_path"), parents=[src["id"]],
        parent_evaluation_ids=[f["source_evaluation_id"]], lineage=lineage, blocks=list(src.get("blocks") or []),
        candidate_status="FINALIST", current_rung=engine.FINAL_STAGE,
        audit={k: v for k, v in audit.items() if v is not None})
    text = src.get("architecture_yaml")
    if isinstance(text, str) and text:  # F5d: the finalist's configuration, derived from its source candidate's latest
        from mbnas_core.orchestration import config_revisions

        config_revisions.try_record(
            conn, engine.log, run_id=child_id, candidate_id=cid, kind="selected", text=text,
            canonical_hash=((src_audit.get("proposal") or {}).get("yaml_hash")), architecture_hash=src.get("architecture_hash"),
            parent_revision_id=(config_revisions.latest_revision(conn, src["id"], parent["id"]) or {}).get("id"),
            meta={"source_kind": "final_retrain"})
    return cand


def request_final_retrain(conn: sqlite3.Connection, parent_id: str, req: FinalRetrainRequest, *,
                          source_commit: str | None = None, request_id: str | None = None,
                          on_child_dir: Any = None, check_fresh: Any = None) -> tuple[dict[str, Any], bool]:
    """Full request flow (idempotency, preconditions, rule, child creation); returns ``(child, created)``.

    Raises :class:`FinalRetrainError`. ``NO_FINALIST`` is logged on the parent in its own committed transaction
    before it is raised. ``on_child_dir(child_id)`` (optional) runs before the creating transaction (e.g. to write
    the child's protocol files). ``check_fresh(parent)`` (optional, DD-A46) runs after the preconditions, before the
    finalists are selected: it may raise (the console raises 409 STALE_VALIDATION when the gateway-measured host
    inputs no longer resolve the parent protocol to its hash).
    """
    from mbnas_core.db.core import tx

    parent = repo.get_run(conn, parent_id)
    if parent is None:
        raise FinalRetrainError(404, "RUN_NOT_FOUND", f"run {parent_id} not found")
    if not (_finite(req.budget_gpu_hours) and req.budget_gpu_hours > 0):
        raise FinalRetrainError(409, "BUDGET_INVALID", "budget_gpu_hours must be a finite number > 0")
    child = existing_child(conn, parent_id, req)
    if child is not None:
        return child, False
    check_preconditions(parent)
    if check_fresh is not None:
        check_fresh(parent)
    recipe = final_recipe(parent["resolved_protocol"])
    top_k = int(req.top_k_per_arm_per_seed or default_top_k(parent["resolved_protocol"]))
    finalists = select_finalists(conn, parent, top_k)
    if not finalists:
        with tx(conn):
            engine.log(conn, parent_id, NO_FINALIST_MESSAGE, level="warning", reason_code="NO_FINALIST",
                       request_id=request_id, selection_rule=selection_rule(parent["resolved_protocol"], top_k))
        raise FinalRetrainError(409, "NO_FINALIST", NO_FINALIST_MESSAGE)
    child_id = f"run-{uuid.uuid4().hex[:12]}"
    if on_child_dir is not None:
        on_child_dir(child_id)
    with tx(conn):
        return create_child(conn, parent, req, finalists, recipe, child_id=child_id, top_k=top_k,
                            source_commit=source_commit, request_id=request_id)
