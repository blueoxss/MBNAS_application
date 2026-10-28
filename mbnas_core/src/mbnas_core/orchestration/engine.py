"""Deterministic run state machine driven by job results.

Every public function runs inside the caller's DB transaction and emits its events there (published after
commit). The same inputs always produce the same jobs: jobs are keyed ``run|kind|candidate|rung|extra``
(:func:`scheduling.jobs.ensure_job`), so replaying a result never duplicates work.

Flow of one search candidate (stable ``candidate_id`` across promotions)::

    PROPOSE_GENERATION result -> candidate BUILD_VALID -> admission(L0) -> CANDIDATE_TRAIN(L0, target t0)
    -> result with inline evaluation (or EVALUATE job) -> immutable EVALUATED row (candidate.evaluated)
    -> fitness with the SOURCE rung weights -> gate -> PROMOTION_ELIGIBLE | REJECTED_BY_GATE | SEARCH_COMPLETE
    -> admission(next rung) -> CANDIDATE_TRAIN(L1, cumulative t1, same candidate/architecture/recipe) -> ...

Baselines train with BASELINE_TRAIN jobs through the same stage targets (rung snapshots, evaluated at each,
same StageTrainer), billed to the separate Baseline budget and never gated. Generation g+1 of an arm/seed is
proposed once every L0 result of generation g is terminal; GA proposals receive the immutable L0 evaluations as
the parent pool, IndependentRandom proposals receive no fitness at all. A run completes when no job is open.

FINAL_RETRAIN child runs (``orchestration.final_retrain``) hold one arm-``Final`` candidate per frozen finalist with
one stage ``FINAL`` (scratch, the pinned final epochs/horizon): admission against the Final cap -> FINAL_RETRAIN job
-> evaluation on the FINAL EVAL split (comparison_group ``final``; fitness with the last search rung's weights for
display, no gate) -> ``FINAL_COMPLETE``. Search/baseline rows that report the final split hash, and final rows that
report any other split, become EVAL_FAILED (``FINAL_SPLIT_LEAK`` / ``FINAL_SPLIT_MISMATCH``).

An evaluation payload that declares itself ``NOT_EVALUATED`` (a runner whose job device is not a CUDA
device) is committed by :func:`commit_not_evaluated` - trained, not evaluated, the reason code
recorded, no metric, no fitness, no gate, no promotion - instead of an EVAL_FAILED row with METRIC_MISSING. The
evaluation-free generation of a split deployment keeps its journal and YAMLs in the core-local run directory;
:func:`_recover_generation` reads the journal there too and :func:`register_generated_architecture`
registers those YAMLs as ``architecture`` artifacts (``meta.location = core_local``).

Retention prune round trip of runner-held checkpoints: :func:`checkpoint_registered` (called by the runner route
E6 after it recorded a checkpoint) and the end of :func:`job_finished` (after the job's outcome / evaluation commit)
plan the run's retention policy over the candidate's runner-held full states and enqueue prune requests
(``orchestration.retention.request_remote_prunes``; the latest full state and evaluation-referenced checkpoints are never
planned). Planning runs in a SAVEPOINT and never fails the caller: an error is logged (``PRUNE_PLANNING_FAILED``).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mbnas_core.artifacts.registry import (
    CORE_LOCAL, CORE_LOCAL_MAX_BYTES, ArtifactError, artifact_path, register_file, run_dir,
)
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.events.bus import emit
from mbnas_core.protocol.fitness import FitnessAnchors, fitness
from mbnas_core.protocol.promotion import evaluate_gate
from mbnas_contracts.schemas.jobs import JobKind, JobResult
from mbnas_core.scheduling import admission, costs, jobs, planner
from mbnas_core.scheduling.planner import EVALUATED, FAILED, NOT_ADMITTED, PENDING
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.orchestration.views import run_payload

SEARCH_ARMS = ("GA", "IndependentRandom")
ARM_TAG = {"GA": "ga", "IndependentRandom": "rnd"}
INIT_GROUPS = {"scratch": "common_scratch", "pretrained_native": "native_pretrained"}
BASELINE_FINAL_STAGE = "BASE"
FINAL_STAGE = "FINAL"
FINAL_GROUP = "final"
TRAIN_KINDS = (JobKind.BASELINE_TRAIN.value, JobKind.CANDIDATE_TRAIN.value, JobKind.FINAL_RETRAIN.value)
FAILURE_STATUS = {"RESUME_BLOCKED": "RESUME_BLOCKED", "BUILD_INVALID": "INVALID", "INVALID": "INVALID",
                  "EVAL_FAILED": "EVAL_FAILED"}
PENDING_ROW_STATUSES = ("QUEUED", "TRAINING", "EVALUATING", "PAUSED", "PROMOTION_ADMITTED")
NOT_EVALUATED = "NOT_EVALUATED"
EVALUATION_REQUIRES_CUDA = "EVALUATION_REQUIRES_CUDA"
"""Reason code of a snapshot the runner could not evaluate (latency is CUDA-only)."""
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def derive_seed(*parts: object) -> int:
    """Deterministic 31-bit seed from labelled parts (same policy for both search arms)."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) % (2 ** 31)


# -- run context -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Stage:
    """One training stop: rung id, cumulative epoch target, epochs added, the rung config whose weights apply."""

    id: str
    target: int
    added: int
    cfg: dict[str, Any]


@dataclass
class RunContext:
    run: dict[str, Any]

    @property
    def resolved(self) -> dict[str, Any]:
        return self.run["resolved_protocol"]

    @property
    def console(self) -> dict[str, Any]:
        return self.resolved["console"]

    @property
    def rungs(self) -> list[dict[str, Any]]:
        return list(self.resolved["rungs"])

    @property
    def rung_ids(self) -> list[str]:
        return [r["id"] for r in self.rungs]

    def rung(self, rung_id: str) -> dict[str, Any]:
        return next(r for r in self.rungs if r["id"] == rung_id)

    @property
    def fixed_horizon(self) -> int:
        return int(self.resolved["training"].get("fixed_horizon_epochs") or self.rungs[-1]["epoch_target_total"])

    @property
    def anchors(self) -> FitnessAnchors:
        return FitnessAnchors.from_protocol(self.resolved["fitness"])

    @property
    def search_group(self) -> str:
        return INIT_GROUPS.get(self.resolved["training"].get("initialization"), "common_scratch")

    def search_stages(self) -> list[Stage]:
        out, prev = [], 0
        for r in self.rungs:
            target = int(r["epoch_target_total"])
            out.append(Stage(r["id"], target, target - prev, r))
            prev = target
        return out

    def baseline_horizon(self) -> int:
        return int(self.console.get("baseline_epoch_target") or self.fixed_horizon)

    def baseline_stages(self) -> list[Stage]:
        """Rung snapshots up to the baseline target (+ a final ``BASE`` stop when it is not a rung target)."""
        horizon, last = self.baseline_horizon(), self.rungs[-1]
        stages: list[Stage] = []
        prev = 0
        if self.console.get("baseline_capture_rungs", True):
            for r in self.rungs:
                if int(r["epoch_target_total"]) <= horizon:
                    stages.append(Stage(r["id"], int(r["epoch_target_total"]), int(r["epoch_target_total"]) - prev, r))
                    prev = int(r["epoch_target_total"])
        if not stages or stages[-1].target != horizon:
            stages.append(Stage(BASELINE_FINAL_STAGE, horizon, horizon - prev, last))
        return stages

    @property
    def kind(self) -> str:
        return str(self.run.get("kind") or "SEARCH")

    @property
    def final_recipe(self) -> dict[str, Any]:
        """Pinned final recipe of the resolved protocol (epochs/horizon/init; validated at final-retrain time)."""
        return dict(self.resolved.get("final_retraining") or {})

    def final_stage(self) -> Stage:
        """The single ``FINAL`` stop: cumulative target = final epochs; cfg = last search rung (display weights)."""
        target = int(self.final_recipe["epoch_target_total"])
        return Stage(FINAL_STAGE, target, target, self.rungs[-1])

    @property
    def final_horizon(self) -> int:
        recipe = self.final_recipe
        return int(recipe.get("scheduler_horizon_epochs") or recipe["epoch_target_total"])

    def stages_for(self, cand: dict[str, Any]) -> list[Stage]:
        if cand["arm"] == "Final":
            return [self.final_stage()]
        return self.baseline_stages() if cand["arm"] == "Baseline" else self.search_stages()

    def horizon_for(self, cand: dict[str, Any]) -> int:
        if cand["arm"] == "Final":
            return self.final_horizon
        return self.baseline_horizon() if cand["arm"] == "Baseline" else self.fixed_horizon

    def split_hash_for(self, cand: dict[str, Any]) -> str | None:
        """Split a candidate is evaluated on: the final split for arm Final, the search validation split otherwise."""
        training = ((self.console.get("extra") or {}).get("training") or {})
        if training.get("enabled") is True and training.get("validation_enabled", True) is False:
            return None  # no validation split exists; never label training data as evaluation data
        key = "final_evaluation_manifest_hash" if cand["arm"] == "Final" else "search_validation_manifest_hash"
        return self.resolved["dataset"][key]

    def baseline_entry(self, baseline_id: str) -> dict[str, Any] | None:
        return next((b for b in self.console.get("baselines", []) if b.get("id") == baseline_id), None)


def context(conn: sqlite3.Connection, run_id: str) -> RunContext:
    run = repo.get_run(conn, run_id)
    if run is None:
        raise repo.NotFound(run_id)
    return RunContext(run)


def _stage(ctx: RunContext, cand: dict[str, Any], stage_id: str) -> tuple[int, Stage]:
    stages = ctx.stages_for(cand)
    idx = next(i for i, s in enumerate(stages) if s.id == stage_id)
    return idx, stages[idx]


def objective_hash(ctx: RunContext, stage: Stage) -> str:
    fit = ctx.resolved["fitness"]
    return canonical_sha256({"formula": fit.get("formula_version"), "weights": stage.cfg["fitness_weights"],
                             "latency_bounds_ms": fit["latency_bounds_ms"], "size_bounds_MB": fit["size_bounds_MB"],
                             "hard_constraints": ctx.resolved.get("hard_constraints"), "stage": stage.id})


# -- evaluation rows ---------------------------------------------------------------------------------------------
def evaluation_id(candidate_id: str, stage_id: str) -> str:
    return f"{candidate_id}.{stage_id}.eval"


def _shapes(ctx: RunContext, cand: dict[str, Any]) -> tuple[list[int] | None, list[int] | None]:
    meta = planner.orchestration_audit(cand)
    req = meta.get("requested_input_shape") or ctx.resolved["training"].get("requested_input_size")
    return req, meta.get("resolved_input_shape") or req


def base_row(ctx: RunContext, cand: dict[str, Any], stage: Stage, status: str, **extra: Any) -> dict[str, Any]:
    """Provisional CandidateEvaluation for ``cand`` at ``stage`` (metrics null until the evaluator commits)."""
    bench = ctx.console.get("benchmark", {})
    req_shape, res_shape = _shapes(ctx, cand)
    meta = planner.orchestration_audit(cand)
    training = (ctx.console.get("extra") or {}).get("training") or {}
    # an explicit evaluate_only run trains no epochs: its rows say epochs=0 and carry no epoch target (the
    # CandidateEvaluation contract allows only >= 1 or null there; the explicit 0 is console.extra.training.epoch_target_total).
    evaluate_only = training.get("enabled") is True and training.get("mode") == "evaluate_only"
    epochs, epoch_target = (0, None) if evaluate_only else (stage.target, stage.target)
    row: dict[str, Any] = {
        "id": evaluation_id(cand["id"], stage.id), "candidate_id": cand["id"], "name": cand["name"],
        "family": cand["family"], "arm": cand["arm"], "generation": cand["generation"], "rung": stage.id,
        "epochs": epochs, "epoch_target_total": epoch_target, "status": status, "ap": None, "recall": None,
        "latency_ms": None, "size_mb": None, "fitness": None,
        "device": (bench.get("device_resolved") or {}).get("name") or bench.get("device") or "unresolved",
        "precision": bench.get("precision") or "FP32", "latency_source": "pending", "parents": cand["parents"],
        "blocks": cand["blocks"], "architecture_path": cand.get("architecture_path"), "checkpoint_path": None,
        "artifacts": [], "objective_hash": objective_hash(ctx, stage), "evaluation_hash": "pending",
        "split_hash": ctx.split_hash_for(cand),
        "latency_scope": ctx.resolved["fitness"].get("latency_scope") or "pending",
        "comparison_group": meta.get("comparison_group") or ctx.search_group, "search_seed": cand.get("search_seed"),
        "training_seed": cand.get("training_seed"), "batch": bench.get("batch"), "requested_input_shape": req_shape,
        "resolved_input_shape": res_shape, "protocol_hash": ctx.run["protocol_hash"],
        "architecture_hash": cand.get("architecture_hash") or None, "architecture_id": cand.get("architecture_hash") or None,
        "namespace": ctx.run["namespace"], "execution_mode": ctx.run["execution_mode"],
        "fitness_weights": stage.cfg["fitness_weights"], "candidate_status": status, "revision": 1}
    row.update(extra)
    return row


def put_row(conn: sqlite3.Connection, ctx: RunContext, row: dict[str, Any], event: str = "candidate.updated") -> dict[str, Any]:
    """Insert/update a provisional row (immutable rows are never touched) and emit the event."""
    existing = repo.get_evaluation(conn, row["id"])
    if existing is not None and existing["immutable"]:
        return existing["row"]
    stored = repo.insert_evaluation(conn, ctx.run["id"], row)
    emit(conn, ctx.run["id"], event, stored["row"])
    return stored["row"]


def set_candidate(conn: sqlite3.Connection, cand_id: str, status: str, rung: str | None = None) -> dict[str, Any]:
    return repo.update_candidate(conn, cand_id, candidate_status=status, current_rung=rung)


def log(conn: sqlite3.Connection, run_id: str, message: str, **payload: Any) -> None:
    emit(conn, run_id, "log", {"message": message, "level": payload.pop("level", "info"), **payload})


def set_run_status(conn: sqlite3.Connection, run_id: str, status: str, reason_code: str | None = None) -> dict[str, Any]:
    run = repo.update_run_status(conn, run_id, status, reason_code=reason_code)
    emit(conn, run_id, "run.updated", run_payload(run))
    return run


# -- initial planning --------------------------------------------------------------------------------------------
def plan_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Queue the first work of a new run: first baseline stage per enabled baseline, generation-0 proposals."""
    ctx = context(conn, run_id)
    if _explicit_training(ctx.run):
        from mbnas_core.training_runs.selection import plan_training_run
        return plan_training_run(conn, run_id)
    search = ctx.console.get("search", {})
    if search.get("evaluation_enabled", True) is False:
        return _plan_generation(conn, ctx)
    created: list[dict[str, Any]] = []
    for b in ctx.console.get("baselines", []):
        if b.get("enabled"):
            created.extend(_plan_baseline(conn, ctx, b))
    search = ctx.console.get("search", {})
    for arm in search.get("arms") or []:
        for seed in search.get("seeds") or []:
            job, new = _propose(conn, ctx, arm, int(seed), 0)
            if new:
                created.append(job)
    return created


def _plan_baseline(conn: sqlite3.Connection, ctx: RunContext, b: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = b.get("resolved") or {}
    cid = f"{ctx.run['id']}.base.{b['id']}"
    cand = repo.insert_candidate(
        conn, id=cid, run_id=ctx.run["id"], name=b.get("name") or b["id"], arm="Baseline",
        family=b.get("family") or "unknown", generation=0, training_seed=derive_seed("baseline", b["id"]),
        lineage={"operation": "baseline", "source_template": b["id"]}, candidate_status="BUILD_VALID",
        audit={"orchestration": {"baseline_id": b["id"], "comparison_group": resolved.get("comparison_group"),
                                 "init": resolved.get("init"), "requested_input_shape": resolved.get("requested_input_shape"),
                                 "resolved_input_shape": resolved.get("resolved_input_shape")}})
    job = _schedule_stage(conn, ctx, cand, ctx.baseline_stages()[0])
    return [job] if job else []


def _propose(conn: sqlite3.Connection, ctx: RunContext, arm: str, seed: int, generation: int) -> tuple[dict[str, Any], bool]:
    search = ctx.console["search"]
    params: dict[str, Any] = {
        "arm": arm, "search_seed": seed, "generation": generation, "population": search.get("population"),
        "mutation_rate": search.get("mutation_rate"), "comparison_group": ctx.search_group,
        "proposal_policy": (ctx.resolved.get("proposal") or {}).get(arm),
        "previous_search_state": _previous_search_state(conn, ctx.run["id"], arm, seed, generation)}
    if arm == "GA" and generation > 0:
        params["parent_pool"] = _parent_pool(conn, ctx, seed)
    return jobs.ensure_job(conn, run_id=ctx.run["id"], kind=JobKind.PROPOSE_GENERATION.value,
                           protocol_hash=ctx.run["protocol_hash"], arm=arm, search_seed=seed, params=params,
                           key_extra=f"{arm}|{seed}|g{generation}")


def _previous_search_state(conn: sqlite3.Connection, run_id: str, arm: str, seed: int, generation: int) -> Any:
    if generation == 0:
        return None
    key = jobs.job_key(run_id, JobKind.PROPOSE_GENERATION.value, None, None, f"{arm}|{seed}|g{generation - 1}")
    row = conn.execute("SELECT id FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
    job = repo.get_job(conn, row[0]) if row else None
    return ((job or {}).get("result") or {}).get("outputs", {}).get("search_state")


def _parent_pool(conn: sqlite3.Connection, ctx: RunContext, seed: int) -> list[dict[str, Any]]:
    """Immutable first-rung (L0) evaluations of this GA seed: the only fidelity used for parent selection."""
    first = ctx.rung_ids[0]
    pool = []
    for ev in repo.list_evaluations(conn, ctx.run["id"], include_superseded=False):
        row = ev["row"]
        if ev["status"] != "EVALUATED" or row["arm"] != "GA" or row.get("search_seed") != seed or row["rung"] != first:
            continue
        cand = repo.get_candidate(conn, row["candidate_id"]) or {}
        proposal = (cand.get("audit") or {}).get("proposal") or {}
        entry = {k: row.get(k) for k in ("candidate_id", "generation", "rung", "status", "fitness", "ap", "recall",
                                         "latency_ms", "size_mb", "architecture_hash", "objective_hash")}
        entry.update(evaluation_id=row["id"], architecture_yaml=cand.get("architecture_yaml"),
                     architecture_path=cand.get("architecture_path"), family=cand.get("family"))
        if proposal.get("genotype") is not None:
            entry["genotype"] = proposal["genotype"]
        pool.append(entry)
    return pool


# -- scheduling one stage ----------------------------------------------------------------------------------------
def _candidate_params(ctx: RunContext, cand: dict[str, Any]) -> dict[str, Any]:
    meta = planner.orchestration_audit(cand)
    out = {k: cand.get(k) for k in ("id", "name", "family", "arm", "generation", "search_seed", "training_seed",
                                    "architecture_hash", "architecture_yaml", "architecture_path")}
    out.update({"baseline_id": meta.get("baseline_id"), "init": meta.get("init") or ctx.resolved["training"].get("initialization"),
                "comparison_group": meta.get("comparison_group") or ctx.search_group,
                "requested_input_shape": _shapes(ctx, cand)[0], "resolved_input_shape": _shapes(ctx, cand)[1]})
    return out


def _schedule_stage(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any], stage: Stage) -> dict[str, Any] | None:
    """Admission + training job for one stage; records the planning state and the provisional row."""
    decision = admission.admit(conn, ctx.run, cand, stage.added)
    if not decision.admitted:
        planner.set_rung_state(conn, cand["id"], stage.id, NOT_ADMITTED, reason_code=decision.reason_code,
                               admission=decision.as_dict())
        set_candidate(conn, cand["id"], "ELIGIBLE_BUDGET_EXHAUSTED", stage.id)
        put_row(conn, ctx, base_row(ctx, repo.get_candidate(conn, cand["id"]), stage, "ELIGIBLE_BUDGET_EXHAUSTED",
                                    reason_code=decision.reason_code))
        return None
    is_base, is_final = cand["arm"] == "Baseline", cand["arm"] == "Final"
    horizon = ctx.horizon_for(cand)
    params = {"candidate": _candidate_params(ctx, cand), "evaluate_inline": True, "admission": decision.as_dict(),
              "stage": {"id": stage.id, "epoch_target_total": stage.target, "epochs_added": stage.added,
                        "fitness_weights": stage.cfg["fitness_weights"]}}
    if is_base:
        params["baseline"] = ctx.baseline_entry(planner.orchestration_audit(cand).get("baseline_id") or "")
    if is_final:
        params.update(eval_split="final_eval", final=planner.orchestration_audit(cand).get("final") or {})
    kind = (JobKind.BASELINE_TRAIN if is_base else JobKind.FINAL_RETRAIN if is_final else JobKind.CANDIDATE_TRAIN).value
    job, _ = jobs.ensure_job(conn, run_id=ctx.run["id"], kind=kind, protocol_hash=ctx.run["protocol_hash"],
                             candidate_id=cand["id"], rung=stage.id, cumulative_target=stage.target,
                             fixed_horizon=horizon, arm=cand["arm"], search_seed=cand.get("search_seed"),
                             training_seed=cand.get("training_seed"), params=params)
    planner.set_rung_state(conn, cand["id"], stage.id, PENDING, job_id=job["id"], admission=decision.as_dict())
    status = "FINALIST" if is_final else (
        "PROMOTION_ADMITTED" if stage.id != ctx.stages_for(cand)[0].id and not is_base else "QUEUED")
    cand = set_candidate(conn, cand["id"], status, stage.id)
    put_row(conn, ctx, base_row(ctx, cand, stage, "QUEUED", job_id=job["id"], candidate_status=status))
    return job


# -- worker callbacks --------------------------------------------------------------------------------------------
def job_started(conn: sqlite3.Connection, job: dict[str, Any], worker_id: str) -> None:
    """A worker started an attempt: run QUEUED -> RUNNING, candidate TRAINING/EVALUATING."""
    ctx = context(conn, job["run_id"])
    if ctx.run["status"] == "QUEUED":
        ctx.run = set_run_status(conn, ctx.run["id"], "RUNNING")
    if not job.get("candidate_id"):
        return
    cand = repo.get_candidate(conn, job["candidate_id"])
    _, stage = _stage(ctx, cand, job["rung"])
    status = "EVALUATING" if job["kind"] == JobKind.EVALUATE.value else _training_status(cand)
    cand = set_candidate(conn, cand["id"], status, stage.id)
    put_row(conn, ctx, base_row(ctx, cand, stage, status, job_id=job["id"], attempt=job["attempt"]))


def _training_status(cand: dict[str, Any]) -> str:
    return "FINAL_RETRAINING" if cand["arm"] == "Final" else "TRAINING"


def job_progress(conn: sqlite3.Connection, job: dict[str, Any], progress: dict[str, Any]) -> None:
    """Throttled live progress of a training attempt (provisional row only)."""
    if not job.get("candidate_id") or job["kind"] not in TRAIN_KINDS:
        return
    ctx = context(conn, job["run_id"])
    cand = repo.get_candidate(conn, job["candidate_id"])
    _, stage = _stage(ctx, cand, job["rung"])
    prog = {k: v for k, v in progress.items() if v is not None}
    put_row(conn, ctx, base_row(ctx, cand, stage, _training_status(cand), job_id=job["id"], attempt=job["attempt"],
                                progress=prog))


def job_requeued(conn: sqlite3.Connection, job: dict[str, Any], *, paused: bool, reason_code: str) -> None:
    """An attempt ended without a final result and the job is QUEUED again (retry or pause boundary)."""
    ctx = context(conn, job["run_id"])
    if ((job.get("spec") or {}).get("params") or {}).get("generation_only"):
        generation_progress(conn, job, _recover_generation(ctx, job, conn))
    if job.get("candidate_id") and job.get("rung"):
        cand = repo.get_candidate(conn, job["candidate_id"])
        _, stage = _stage(ctx, cand, job["rung"])
        status = "PAUSED" if paused else "QUEUED"
        cand = set_candidate(conn, cand["id"], status, stage.id)
        put_row(conn, ctx, base_row(ctx, cand, stage, status, job_id=job["id"], attempt=job["attempt"],
                                    reason_code=reason_code))
    log(conn, ctx.run["id"], f"job {job['id']} requeued ({reason_code})", job_id=job["id"], reason_code=reason_code,
        attempt=job["attempt"], paused=paused)


def job_finished(conn: sqlite3.Connection, job: dict[str, Any], result: JobResult) -> None:
    """Terminal outcome of a job (SUCCEEDED, or FAILED/CANCELLED with no retry left)."""
    ctx = context(conn, job["run_id"])
    if job["kind"] in TRAIN_KINDS and job.get("candidate_id"):
        from mbnas_core.orchestration.model_artifacts import (
            record_model_configuration, record_model_configuration_from_report, record_native_images,
        )
        report = (result.outputs or {}).get("execution_configuration")
        try:
            if isinstance(report, dict):  # a remote runner reported it; DB update only
                record_model_configuration_from_report(conn, ctx.run, repo.get_candidate(conn, job["candidate_id"]),
                                                       report)
            else:
                candidate = record_model_configuration(conn, ctx.run, repo.get_candidate(conn, job["candidate_id"]))
                record_native_images(conn, ctx.run, candidate)
        except (ValueError, OSError, ArtifactError):
            log(conn, ctx.run["id"], "executed model configuration could not be registered",
                candidate_id=job["candidate_id"], reason_code="MODEL_CONFIGURATION_UNAVAILABLE")
    if _explicit_training(ctx.run):
        from mbnas_core.training_runs.selection import on_training_result
        on_training_result(conn, job, result)
        settle_run(conn, ctx.run["id"])
        request_prunes(conn, ctx.run["id"], job.get("candidate_id"), planned_by="job_finished")
        return
    kind = job["kind"]
    if kind == JobKind.PROPOSE_GENERATION.value and (job.get("spec", {}).get("params") or {}).get("generation_only"):
        recovered = _recover_generation(ctx, job, conn)
        generation_progress(conn, job, {**recovered, **result.outputs})
    elif kind == JobKind.PROPOSE_GENERATION.value:
        _proposal_finished(conn, ctx, job, result)
    elif result.status.value == "SUCCEEDED":
        evaluation = result.outputs.get("evaluation")
        if kind in TRAIN_KINDS and not isinstance(evaluation, dict):
            _queue_evaluation(conn, ctx, job, result)
        elif kind in TRAIN_KINDS or kind == JobKind.EVALUATE.value:
            commit_evaluation(conn, ctx, job, with_device_used(evaluation or {}, result.outputs), outputs=result.outputs)
        else:
            log(conn, ctx.run["id"], f"{kind} job {job['id']} succeeded", job_id=job["id"])
    elif job.get("candidate_id") and job.get("rung"):
        _candidate_failed(conn, ctx, job, result)
    else:
        log(conn, ctx.run["id"], f"{kind} job {job['id']} ended {result.status.value}", level="error",
            job_id=job["id"], reason_code=result.reason_code)
    settle_run(conn, ctx.run["id"])
    request_prunes(conn, ctx.run["id"], job.get("candidate_id"), planned_by="job_finished")


def request_prunes(conn: sqlite3.Connection, run_id: str, candidate_id: str | None, *,
                   default_policy: str = "keep_all", planned_by: str = "engine") -> list[dict[str, Any]]:
    """Plan the run's retention policy over the candidate's runner-held full states and enqueue the prune
    requests (``orchestration.retention.request_remote_prunes``) inside the caller's transaction, in a SAVEPOINT; an
    error is logged (``PRUNE_PLANNING_FAILED``), never raised. A run without runner-held checkpoints plans nothing."""
    if not candidate_id:
        return []
    from mbnas_core.orchestration import retention  # retention imports this module

    try:
        with tx(conn):
            return retention.request_remote_prunes(conn, repo.get_run(conn, run_id), candidate_id,
                                                   default_policy=default_policy, planned_by=planned_by)
    except Exception as exc:  # noqa: BLE001 - housekeeping never fails a registration or a result commit
        log(conn, run_id, f"prune planning failed for {candidate_id}: {type(exc).__name__}: {exc}"[:500],
            level="error", candidate_id=candidate_id, reason_code="PRUNE_PLANNING_FAILED")
        return []


def checkpoint_registered(conn: sqlite3.Connection, job: dict[str, Any], checkpoint: dict[str, Any], *,
                          default_policy: str = "keep_all") -> list[dict[str, Any]]:
    """A runner registered ``checkpoint`` for ``job`` (route E6, inside its transaction): every older runner-held full
    state of the candidate the run's policy deletes gets a prune request now, so the holding runner receives it on a
    heartbeat of this same lease (:func:`request_prunes`; ``default_policy`` = the server setting for a run without a
    resolved ``console.retention``)."""
    return request_prunes(conn, job["run_id"], checkpoint.get("candidate_id") or job.get("candidate_id"),
                          default_policy=default_policy, planned_by="checkpoint_registered")


def _queue_evaluation(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], result: JobResult) -> None:
    cand = repo.get_candidate(conn, job["candidate_id"])
    _, stage = _stage(ctx, cand, job["rung"])
    ev_job, _ = jobs.ensure_job(conn, run_id=ctx.run["id"], kind=JobKind.EVALUATE.value,
                                protocol_hash=ctx.run["protocol_hash"], candidate_id=cand["id"], rung=stage.id,
                                arm=cand["arm"], search_seed=cand.get("search_seed"),
                                training_seed=cand.get("training_seed"),
                                params={"candidate": _candidate_params(ctx, cand), "training_job_id": job["id"],
                                        "training_outputs": result.outputs,
                                        "stage": {"id": stage.id, "epoch_target_total": stage.target},
                                        **({"eval_split": "final_eval"} if cand["arm"] == "Final" else {})})
    planner.set_rung_state(conn, cand["id"], stage.id, PENDING, job_id=ev_job["id"])
    cand = set_candidate(conn, cand["id"], "EVALUATING", stage.id)
    put_row(conn, ctx, base_row(ctx, cand, stage, "EVALUATING", job_id=ev_job["id"]))


def _register_artifacts(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any], ev_id: str,
                        items: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    views, problems = [], []
    for item in items:
        if isinstance(item, dict) and item.get("artifact_id"):  # pre-registered by a runner
            art = repo.get_artifact(conn, ctx.run["id"], str(item["artifact_id"]))
            if art is None:
                problems.append(f"{item.get('kind')}: artifact {item['artifact_id']} is not registered for this run")
                continue
            art = repo.link_artifact(conn, ctx.run["id"], art["id"], evaluation_id=ev_id, candidate_id=cand["id"])
            views.append({"id": art["id"], "kind": art["kind"], "url": artifact_path(ctx.run["id"], art["id"]),
                          "auth_mode": "bearer_fetch", "state": art["state"], "sample_id": art.get("sample_id"),
                          "split_hash": art.get("split_hash"), "sha256": art["sha256"], "mime_type": art["mime_type"],
                          "evaluation_id": ev_id, "matching": item.get("matching")})
            continue
        if not isinstance(item, dict) or not item.get("path") or not item.get("kind"):
            problems.append(f"malformed artifact entry: {item!r}"[:300])
            continue
        try:
            art = register_file(conn, ctx.run, kind=item["kind"], path=str(item["path"]), candidate_id=cand["id"],
                                evaluation_id=ev_id, sample_id=item.get("sample_id"), split_hash=item.get("split_hash"),
                                mime_type=item.get("mime_type"), sha256=item.get("sha256"),
                                meta={"matching": item.get("matching")} if item.get("matching") else {})
        except (ArtifactError, ValueError) as exc:
            problems.append(f"{item.get('kind')}: {exc}")
            continue
        views.append({"id": art["id"], "kind": art["kind"], "url": artifact_path(ctx.run["id"], art["id"]),
                      "auth_mode": "bearer_fetch", "state": art["state"], "sample_id": art.get("sample_id"),
                      "split_hash": art.get("split_hash"), "sha256": art["sha256"], "mime_type": art["mime_type"],
                      "evaluation_id": ev_id, "matching": item.get("matching")})
    return views, problems


METRIC_KEYS = ("latency_source", "latency_scope", "device", "precision", "evaluation_hash", "split_hash",
               "checkpoint_path", "checkpoint_sha256", "architecture_hash", "batch", "requested_input_shape",
               "resolved_input_shape", "raw_metrics", "latency_statistics", "metric_definitions", "postprocess_runtime")


def _normalise_evaluation(ev: dict[str, Any]) -> dict[str, Any]:
    """Map handler output variants onto contract field types (originals kept for raw_metrics).

    ``checkpoint_sha256`` may arrive per checkpoint kind (``{"training_full": sha, ...}``); the contract field is the
    hash of ``checkpoint_path`` (the training full state), the full mapping is kept as ``checkpoint_sha256_by_kind``.
    """
    out = dict(ev)
    sha = out.get("checkpoint_sha256")
    if isinstance(sha, dict):
        out["checkpoint_sha256_by_kind"] = sha
        out["checkpoint_sha256"] = sha.get("training_full") or next((v for v in sha.values() if isinstance(v, str)), None)
    return out


def with_device_used(ev: dict[str, Any], outputs: dict[str, Any] | None) -> dict[str, Any]:
    """A result that reports ``outputs.device_used`` (a CPU-only runner mapped ``cuda:0`` to ``cpu``) records it
    in the evaluation row's ``raw_metrics.device_used``; ``ev`` is returned unchanged otherwise."""
    used = (outputs or {}).get("device_used") if isinstance(outputs, dict) else None
    if not used:
        return ev
    return {**ev, "raw_metrics": {**(ev.get("raw_metrics") or {}), "device_used": str(used)}}


RAW_EXCLUDED = frozenset({*METRIC_KEYS, "ap", "recall", "latency_ms", "size_mb", "artifacts", "per_sample", "fitness",
                          "fitness_result", "gate", "rewards", "fitness_weights", "lr_history"})


def commit_evaluation(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], ev: dict[str, Any],
                      outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fitness + gate with the source stage weights, immutable row, candidate.evaluated, then advance.

    A payload that declares itself NOT_EVALUATED (:func:`is_not_evaluated`) is committed by
    :func:`commit_not_evaluated` (``outputs``: the job result's outputs - rung result, trained architecture hash)."""
    cand = repo.get_candidate(conn, job["candidate_id"])
    idx, stage = _stage(ctx, cand, job["rung"])
    ev_id = evaluation_id(cand["id"], stage.id)
    existing = repo.get_evaluation(conn, ev_id)
    if existing is not None and existing["immutable"]:
        return existing["row"]  # replayed result: the committed measurement wins
    if is_not_evaluated(ev):
        return commit_not_evaluated(conn, ctx, job, ev, outputs)
    metrics = {k: ev.get(k) for k in ("ap", "recall", "latency_ms", "size_mb")}
    fit = fitness(metrics["ap"], metrics["recall"], metrics["latency_ms"], metrics["size_mb"],
                  stage.cfg["fitness_weights"], ctx.anchors)
    artifacts, problems = _register_artifacts(conn, ctx, cand, ev_id, list(ev.get("artifacts") or []))
    ev = _normalise_evaluation(ev)
    extra = _json_safe({k: ev[k] for k in METRIC_KEYS if ev.get(k) is not None})
    explicit = ((ctx.console.get('extra') or {}).get('training') or {})
    if explicit.get('enabled') is True and explicit.get('mode') == 'evaluate_only':
        extra['epochs'] = 0
    extra["raw_metrics"] = _json_safe({**{k: v for k, v in ev.items() if k not in RAW_EXCLUDED},
                                       **(ev.get("raw_metrics") or {}),
                                       **({"handler_fitness": ev["fitness"]} if "fitness" in ev else {})})
    gate = None
    if cand["arm"] in SEARCH_ARMS:
        gate = evaluate_gate(fit, metrics["ap"], metrics["latency_ms"], metrics["size_mb"], stage.cfg,
                             ctx.resolved["hard_constraints"], latency_source=ev.get("latency_source")).as_dict()
    value = min(max(fit.fitness, 0.0), 1.0) if fit.valid and fit.fitness is not None else None
    row = base_row(ctx, cand, stage, "EVALUATED", **metrics, **extra, fitness=value, artifacts=artifacts, gate=gate,
                   job_id=job["id"], attempt=job["attempt"],
                   rewards={"latency_reward": fit.r_T, "size_reward": fit.r_S},
                   cost=costs.candidate_seconds(conn, cand["id"]) or None)
    if problems:
        row["raw_metrics"] = {**(row.get("raw_metrics") or {}), "artifact_problems": problems}
    if extra.get("architecture_hash") and not cand.get("architecture_hash"):
        repo.update_candidate(conn, cand["id"], architecture_hash=extra["architecture_hash"])
    reason = fit.reason if not fit.valid else None
    if fit.valid and not row.get("checkpoint_path"):
        reason = "CHECKPOINT_PATH_MISSING"
    split_reason = _split_violation(ctx, cand, row.get("split_hash"))
    if split_reason is not None:
        reason = split_reason
    # the trained architecture must be the proposed one; a mismatch invalidates the measurement.
    if verify_architecture_hash(conn, ctx, cand, extra.get("architecture_hash")) is False:
        reason = ARCHITECTURE_HASH_MISMATCH
    if reason is not None:
        return _evaluation_failed(conn, ctx, cand, stage, row, metrics, reason)
    try:
        stored = repo.insert_evaluation(conn, ctx.run["id"], row)["row"]
    except ValueError as exc:  # contract violation of the evaluator output (e.g. a missing cohort field)
        row["raw_metrics"] = {**(row.get("raw_metrics") or {}), "contract_error": str(exc)[:2000]}
        return _evaluation_failed(conn, ctx, cand, stage, row, metrics, "EVALUATION_CONTRACT_INVALID")
    emit(conn, ctx.run["id"], "candidate.evaluated", stored)
    planner.set_rung_state(conn, cand["id"], stage.id, EVALUATED, evaluation_id=ev_id,
                           gate=(gate or {}).get("reason_code"))
    _advance(conn, ctx, repo.get_candidate(conn, cand["id"]), idx, stage, gate)
    return stored


def is_not_evaluated(ev: Any) -> bool:
    """``ev`` is an evaluation payload that declares its snapshot NOT evaluated (``evaluation_status`` or
    ``performance_status`` NOT_EVALUATED: the runner's job device is not a CUDA device, so no latency - and
    therefore no fitness - was measured)."""
    return isinstance(ev, dict) and NOT_EVALUATED in (ev.get("evaluation_status"), ev.get("performance_status"))


def not_evaluated_reason(ev: dict[str, Any]) -> str:
    """The payload's ``reason_code`` when it is a plain upper-case code (EVALUATION_REQUIRES_CUDA from the runner), else
    ``NOT_EVALUATED`` (free text never becomes a public reason code)."""
    code = ev.get("reason_code")
    return code if isinstance(code, str) and _REASON_CODE.fullmatch(code) else NOT_EVALUATED


def _first_count(*values: Any, default: int) -> int:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return default


def commit_not_evaluated(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], ev: dict[str, Any],
                         outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Commit a NOT_EVALUATED payload (:func:`is_not_evaluated`) without inventing a measurement.

    The row of the job's stage is ``SEARCH_COMPLETE`` with ``performance_status`` NOT_EVALUATED and the payload's reason
    code (EVALUATION_REQUIRES_CUDA): ``ap``/``recall``/``latency_ms``/``size_mb``/``fitness`` null (a value the payload
    reported anyway is kept only as ``raw_metrics.reported``), no gate, ``latency_source`` and ``evaluation_hash``
    pending, ``split_hash`` null (no split was evaluated), the trained checkpoint (``checkpoint_path`` /
    ``checkpoint_sha256``) kept, ``raw_metrics`` with the runner's reason and ``device_used``. No ``candidate.evaluated``
    event, no promotion, no next stage; a committed (immutable) EVALUATED row is never replaced.

    * explicit training run (``console.extra.training``): the candidate is ``SEARCH_COMPLETE`` and its rung state
      ``evaluated`` with ``performance_status`` NOT_EVALUATED - the shape of a validation-off run - so the run
      COMPLETES (instead of FAILED / MODEL_RUN_FAILED); the row keeps ``source_kind`` / ``training_mode`` and
      ``raw_metrics.validation_enabled``;
    * every other arm (GA / IndependentRandom / Baseline / Final): the candidate stays unevaluated - ``SEARCH_COMPLETE``,
      never promoted, no further stage - and its rung state is ``failed`` with the reason, so the generation counts keep
      ``evaluated`` for measured rows only and a GA never takes it as a parent (:func:`_parent_pool` reads EVALUATED
      rows only).

    A trained architecture hash that differs from the candidate's is flagged as for a measurement: the explicit
    row's ``reason_code`` becomes ARCHITECTURE_HASH_MISMATCH, any other arm ends EVAL_FAILED with that reason.
    """
    cand = repo.get_candidate(conn, job["candidate_id"])
    _idx, stage = _stage(ctx, cand, job["rung"])
    ev_id = evaluation_id(cand["id"], stage.id)
    existing = repo.get_evaluation(conn, ev_id)
    if existing is not None and existing["immutable"]:
        return existing["row"]  # a committed measurement is never replaced by "not evaluated"
    outputs = outputs if isinstance(outputs, dict) else {}
    ev = _normalise_evaluation(ev)
    reason = not_evaluated_reason(ev)
    training = _explicit_training_cfg(ctx.run)
    rr = outputs.get("rung_result") if isinstance(outputs.get("rung_result"), dict) else {}
    artifacts, problems = _register_artifacts(conn, ctx, cand, ev_id, list(ev.get("artifacts") or []))
    raw: dict[str, Any] = {**{k: v for k, v in ev.items() if k not in RAW_EXCLUDED}, **(ev.get("raw_metrics") or {}),
                           "evaluation_status": NOT_EVALUATED, "reason_code": reason}
    reported = {k: ev[k] for k in ("ap", "recall", "latency_ms", "size_mb", "fitness") if ev.get(k) is not None}
    if reported:
        raw["reported"] = reported
    if problems:
        raw["artifact_problems"] = problems
    if training is not None:
        raw["validation_enabled"] = training.get("validation_enabled", True) is not False
        if rr:
            raw["rung_result"] = rr
    trained_hash = outputs.get("architecture_hash") or ev.get("architecture_hash")
    mismatch = verify_architecture_hash(conn, ctx, cand, trained_hash) is False
    evaluate_only = training is not None and training.get("mode") == "evaluate_only"
    sha = ev.get("checkpoint_sha256")
    extra: dict[str, Any] = {
        "epochs": 0 if evaluate_only else _first_count(ev.get("epochs"), outputs.get("epochs_done"), default=stage.target),
        "checkpoint_path": ev.get("checkpoint_path") or rr.get("rung_end_training_ckpt"),
        "checkpoint_sha256": sha if isinstance(sha, str) else None, "performance_status": NOT_EVALUATED,
        "reason_code": reason, "split_hash": None, "latency_source": "pending", "evaluation_hash": "pending",
        "artifacts": artifacts, "gate": None, "job_id": job["id"], "attempt": job["attempt"],
        "raw_metrics": _json_safe(raw), "cost": costs.candidate_seconds(conn, cand["id"]) or None}
    if trained_hash:
        extra["architecture_hash"] = str(trained_hash)
    if training is not None:
        extra.update(source_kind=(training.get("source") or {}).get("kind"), training_mode=training.get("mode"))
    if mismatch and training is None:
        row = base_row(ctx, cand, stage, "EVAL_FAILED", **extra)
        return _evaluation_failed(conn, ctx, cand, stage, row, {k: None for k in ("ap", "recall", "latency_ms", "size_mb")},
                                  ARCHITECTURE_HASH_MISMATCH)
    if mismatch:
        extra["reason_code"] = ARCHITECTURE_HASH_MISMATCH  # flagged, never silent (the cause stays in raw)
    row = _json_safe(base_row(ctx, cand, stage, "SEARCH_COMPLETE", **extra))
    for key in ("batch", "resolved_input_shape", "requested_input_shape"):
        if not _valid_cohort_value(row.get(key)):
            row[key] = None
    try:
        stored = put_row(conn, ctx, row)
    except ValueError as exc:  # unusable even as a not-evaluated record: keep the protocol fields and the reason
        stored = put_row(conn, ctx, base_row(
            ctx, cand, stage, "SEARCH_COMPLETE", performance_status=NOT_EVALUATED, reason_code=row["reason_code"],
            split_hash=None, job_id=job["id"], attempt=job["attempt"],
            raw_metrics={"evaluation_status": NOT_EVALUATED, "reason_code": reason, "row_error": str(exc)[:2000]}))
    cand = set_candidate(conn, cand["id"], "SEARCH_COMPLETE", stage.id)
    if training is not None:
        planner.set_rung_state(conn, cand["id"], stage.id, EVALUATED, performance_status=NOT_EVALUATED,
                               reason_code=stored.get("reason_code"))
    else:
        planner.set_rung_state(conn, cand["id"], stage.id, FAILED, reason_code=reason, performance_status=NOT_EVALUATED)
    log(conn, ctx.run["id"], f"candidate {cand['id']} stage {stage.id}: trained, not evaluated ({reason})",
        level="warning", candidate_id=cand["id"], job_id=job["id"], reason_code=reason)
    if training is None and cand["arm"] in SEARCH_ARMS:
        _refresh_generations(conn, ctx, cand)
    return stored


ARCHITECTURE_HASH_MISMATCH = "ARCHITECTURE_HASH_MISMATCH"


def _same_hash(a: Any, b: Any) -> bool:
    def norm(v: Any) -> str:
        text = str(v).strip().lower()
        return text[7:] if text.startswith("sha256:") else text
    return norm(a) == norm(b)


def verify_architecture_hash(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any],
                             reported: Any) -> bool | None:
    """Cross-check the candidate's architecture hash with the trainer-reported one.

    ``True``/``False`` when both exist and match/differ; ``None`` when either side is missing (not verifiable, never
    reported as verified). A mismatch is logged as an error event with reason ``ARCHITECTURE_HASH_MISMATCH``. For an
    explicit training run the outcome is stored as ``audit.training_source.architecture_hash_verified``.
    """
    expected = cand.get("architecture_hash")
    verified = None if not expected or not reported else _same_hash(expected, reported)
    if _explicit_training(ctx.run):
        audit = dict(cand.get("audit") or {})
        source = dict(audit.get("training_source") or {})
        if "architecture_hash_verified" not in source or source["architecture_hash_verified"] != verified:
            source["architecture_hash_verified"] = verified
            audit["training_source"] = source
            repo.update_candidate(conn, cand["id"], audit=audit)
    if verified is False:
        log(conn, ctx.run["id"], "trained architecture hash differs from the candidate's architecture hash",
            level="error", candidate_id=cand["id"], reason_code=ARCHITECTURE_HASH_MISMATCH,
            candidate_architecture_hash=expected, trainer_architecture_hash=str(reported))
    return verified


def _split_violation(ctx: RunContext, cand: dict[str, Any], split_hash: Any) -> str | None:
    """FINAL_SPLIT_LEAK when a search/baseline row reports the final split; FINAL_SPLIT_MISMATCH when a final row
    reports anything but the final split (the final split is evaluated only for arm Final)."""
    final_hash = ctx.resolved.get("dataset", {}).get("final_evaluation_manifest_hash")
    if cand["arm"] == "Final":
        return None if split_hash == final_hash else "FINAL_SPLIT_MISMATCH"
    return "FINAL_SPLIT_LEAK" if final_hash and split_hash == final_hash else None


def _evaluation_failed(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any], stage: Stage,
                       row: dict[str, Any], metrics: dict[str, Any], reason: str) -> dict[str, Any]:
    """Store an EVAL_FAILED provisional row: reported metrics move to raw_metrics, never become points or 0."""
    raw = _json_safe({**(row.get("raw_metrics") or {}), "reported": metrics})
    row.update({k: None for k in ("ap", "recall", "latency_ms", "size_mb", "fitness")}, status="EVAL_FAILED",
               candidate_status="EVAL_FAILED", reason_code=reason, raw_metrics=raw, gate=None)
    for key in ("batch", "resolved_input_shape", "requested_input_shape"):
        if not _valid_cohort_value(row.get(key)):
            row[key] = None
    try:
        stored = put_row(conn, ctx, _json_safe(row))
    except ValueError as exc:  # evaluator output unusable even as a failure record: keep only the protocol fields
        stored = put_row(conn, ctx, base_row(ctx, cand, stage, "EVAL_FAILED", reason_code=reason,
                                             raw_metrics={**raw, "row_error": str(exc)[:2000]}))
    _mark_failed(conn, ctx, cand, stage, "EVAL_FAILED", reason)
    return stored


def _json_safe(value: Any) -> Any:
    """Copy with non-finite floats replaced by their string form (JSON columns reject NaN/Infinity)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _valid_cohort_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 1
    return isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in value)


def _advance(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any], idx: int, stage: Stage,
             gate: dict[str, Any] | None) -> None:
    stages = ctx.stages_for(cand)
    if cand["arm"] == "Final":  # reported with fitness, never promoted
        set_candidate(conn, cand["id"], "FINAL_COMPLETE", stage.id)
        return
    if cand["arm"] == "Baseline":
        if idx + 1 < len(stages):
            _schedule_stage(conn, ctx, cand, stages[idx + 1])
        else:
            set_candidate(conn, cand["id"], "EVALUATED", stage.id)
        return
    code = (gate or {}).get("reason_code")
    if code == "PROMOTION_ELIGIBLE" and idx + 1 < len(stages):
        set_candidate(conn, cand["id"], "PROMOTION_ELIGIBLE", stage.id)
        _schedule_stage(conn, ctx, repo.get_candidate(conn, cand["id"]), stages[idx + 1])
    elif code == "REJECTED_BY_GATE":
        set_candidate(conn, cand["id"], "REJECTED_BY_GATE", stage.id)
    else:
        set_candidate(conn, cand["id"], "SEARCH_COMPLETE", stage.id)
    _refresh_generations(conn, ctx, cand)


def _candidate_failed(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], result: JobResult) -> None:
    cand = repo.get_candidate(conn, job["candidate_id"])
    _, stage = _stage(ctx, cand, job["rung"])
    reason = result.reason_code or "UNKNOWN"
    if result.status.value == "CANCELLED":
        status = "CANCELLED"
    elif job["kind"] == JobKind.EVALUATE.value:
        status = "EVAL_FAILED"
    else:
        status = FAILURE_STATUS.get(reason, "TRAIN_FAILED")
    put_row(conn, ctx, base_row(ctx, cand, stage, status, job_id=job["id"], attempt=job["attempt"], reason_code=reason))
    _mark_failed(conn, ctx, cand, stage, status, reason)


def _mark_failed(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any], stage: Stage, status: str,
                 reason: str) -> None:
    planner.set_rung_state(conn, cand["id"], stage.id, FAILED, reason_code=reason)
    cand = set_candidate(conn, cand["id"], status, stage.id)
    if cand["arm"] in SEARCH_ARMS:
        _refresh_generations(conn, ctx, cand)


def _refresh_generations(conn: sqlite3.Connection, ctx: RunContext, cand: dict[str, Any]) -> None:
    """Recompute every rung row of the candidate's generation and push changes; maybe propose the next one.

    Contract 1.2.9: an open row that changed (new row, counts, planning_closed, representative) is pushed as
    ``generation.updated``; the transition to complete is pushed once as ``generation.completed`` (and not also as
    an update); an unchanged row and an already complete row push nothing.
    """
    arm, seed, gen = cand["arm"], cand["search_seed"], cand["generation"]
    for rung in ctx.rung_ids:
        key = repo.generation_group_key(arm, seed, gen, rung, ctx.search_group)
        before = repo.get_generation(conn, ctx.run["id"], key)
        row, completed = planner.refresh_generation(conn, ctx.run["id"], arm=arm, search_seed=seed, generation=gen,
                                                    rung=rung, rung_ids=ctx.rung_ids, comparison_group=ctx.search_group)
        if row is None:
            continue
        if completed:
            emit(conn, ctx.run["id"], "generation.completed", row["row"])
            if rung == ctx.rung_ids[0]:
                _maybe_next_generation(conn, ctx, arm, seed, gen)
        elif not row["complete"] and (before is None or before["row"] != row["row"]):
            emit(conn, ctx.run["id"], "generation.updated", row["row"])


def _maybe_next_generation(conn: sqlite3.Connection, ctx: RunContext, arm: str, seed: int, gen: int) -> None:
    run = repo.get_run(conn, ctx.run["id"])
    if run["status"] not in ("QUEUED", "RUNNING", "RESUMING", "PAUSE_REQUESTED", "PAUSED"):
        return
    if gen + 1 >= int(ctx.console["search"].get("generations") or 0):
        return
    remaining = admission.remaining_seconds(conn, run, arm, seed)
    if remaining is not None and remaining <= 0:
        log(conn, run["id"], f"{arm} seed {seed}: budget exhausted, generation {gen + 1} not proposed",
            arm=arm, search_seed=seed, generation=gen + 1, reason_code="BUDGET_EXHAUSTED")
        return
    _propose(conn, ctx, arm, seed, gen + 1)


def _proposal_finished(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], result: JobResult) -> None:
    params = (job.get("spec") or {}).get("params") or {}
    arm, seed, gen = params["arm"], int(params["search_seed"]), int(params["generation"])
    if result.status.value != "SUCCEEDED":
        log(conn, ctx.run["id"], f"{arm} seed {seed} generation {gen} proposal failed", level="error",
            job_id=job["id"], reason_code=result.reason_code, message_detail=result.message)
        return
    first = ctx.search_stages()[0]
    proposals = list(result.outputs.get("candidates") or [])
    inserted: list[dict[str, Any]] = []
    for i, prop in enumerate(proposals):
        cand = _insert_proposal(conn, ctx, job, arm, seed, gen, i, prop if isinstance(prop, dict) else {})
        if cand is None:
            continue
        inserted.append(cand)
        if cand["candidate_status"] != "INVALID":
            _schedule_stage(conn, ctx, cand, first)
        else:
            reason = planner.orchestration_audit(cand).get("invalid_reason") or "BUILD_INVALID"
            put_row(conn, ctx, base_row(ctx, cand, first, "INVALID", reason_code=reason))
            planner.set_rung_state(conn, cand["id"], first.id, FAILED, reason_code=reason)
    if not inserted:
        log(conn, ctx.run["id"], f"{arm} seed {seed} generation {gen}: proposal produced no new candidates",
            level="warning", job_id=job["id"])
        return
    _refresh_generations(conn, ctx, repo.get_candidate(conn, inserted[0]["id"]))


PROPOSAL_ROW_KEYS = ("name", "family", "architecture_hash", "architecture_yaml", "architecture_path", "parents",
                     "parent_evaluation_ids", "lineage", "blocks", "training_seed", "candidate_status", "audit", "id")


def _insert_proposal(conn: sqlite3.Connection, ctx: RunContext, job: dict[str, Any], arm: str, seed: int, gen: int,
                     index: int, prop: dict[str, Any]) -> dict[str, Any] | None:
    """Store one proposed candidate (``{"row": insert_candidate kwargs, ...extras}`` or a flat dict)."""
    row = dict(prop.get("row") or {k: prop[k] for k in PROPOSAL_ROW_KEYS if k in prop})
    extras = {k: v for k, v in prop.items() if k not in ("row", *PROPOSAL_ROW_KEYS)}
    cid = str(row.get("id") or f"{ctx.run['id']}.{ARM_TAG[arm]}{seed}.g{gen}.c{index:03d}")
    if repo.get_candidate(conn, cid) is not None:
        return None  # replayed proposal result
    valid = prop.get("valid", True) is not False and row.get("candidate_status", "BUILD_VALID") != "INVALID"
    parents = [p for p in dict.fromkeys(row.get("parents") or []) if p][:2]
    tseed = row.get("training_seed")
    orchestration = {"comparison_group": ctx.search_group, "proposal_job_id": job["id"], "index": index}
    if not valid:
        orchestration["invalid_reason"] = prop.get("reason_code") or "BUILD_INVALID"
    audit = {**(row.get("audit") or {}), "proposal": extras, "orchestration": orchestration}
    cand = repo.insert_candidate(
        conn, id=cid, run_id=ctx.run["id"], name=row.get("name") or cid, arm=arm,
        family=row.get("family") or "Searched", generation=gen, search_seed=seed,
        training_seed=int(tseed) if isinstance(tseed, int) and not isinstance(tseed, bool)
        else derive_seed("search-train", seed, gen, index),
        architecture_hash=row.get("architecture_hash"), architecture_yaml=row.get("architecture_yaml"),
        architecture_path=row.get("architecture_path"), parents=parents,
        parent_evaluation_ids=list(row.get("parent_evaluation_ids") or []), lineage=row.get("lineage") or {},
        blocks=list(row.get("blocks") or []), candidate_status="BUILD_VALID" if valid else "INVALID", audit=audit)
    record_generated_revision(conn, ctx.run["id"], cand, yaml_hash=extras.get("yaml_hash"))
    return cand


def record_generated_revision(conn: sqlite3.Connection, run_id: str, cand: dict[str, Any], *,
                              yaml_hash: str | None = None, artifact_id: str | None = None) -> None:
    """The configuration the proposer wrote for ``cand`` (revision kind ``generated``; never blocks a proposal)."""
    from mbnas_core.orchestration import config_revisions

    text = cand.get("architecture_yaml")
    if isinstance(text, str) and text:
        config_revisions.try_record(conn, log, run_id=run_id, candidate_id=cand["id"], kind="generated", text=text,
                                    canonical_hash=yaml_hash or ((cand.get("audit") or {}).get("proposal") or {}).get(
                                        "yaml_hash"), architecture_hash=cand.get("architecture_hash"),
                                    artifact_id=artifact_id, parent_revision_id=None)


# -- run status settlement ---------------------------------------------------------------------------------------
def _cancel_pending_candidates(conn: sqlite3.Connection, ctx: RunContext) -> None:
    for cand in repo.list_candidates(conn, ctx.run["id"]):
        pending = [r for r, st in planner.rung_states(cand).items() if st.get("state") == PENDING]
        for rung in pending:
            stage = next((s for s in ctx.stages_for(cand) if s.id == rung), None)
            if stage is None:
                continue
            put_row(conn, ctx, base_row(ctx, cand, stage, "CANCELLED", reason_code="RUN_CANCELLED"))
            _mark_failed(conn, ctx, cand, stage, "CANCELLED", "RUN_CANCELLED")
            cand = repo.get_candidate(conn, cand["id"])


def settle_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Apply worker-confirmed transitions: PAUSE_REQUESTED->PAUSED, CANCEL_REQUESTED->CANCELLED, done->COMPLETED."""
    run = repo.get_run(conn, run_id)
    if _explicit_training(run):
        from mbnas_core.training_runs.selection import settle_training_run
        return settle_training_run(conn, run_id)
    active = jobs.count_jobs(conn, run_id, jobs.ACTIVE_STATUSES)
    open_ = jobs.count_jobs(conn, run_id, jobs.OPEN_STATUSES)
    status = run["status"]
    if status == "PAUSE_REQUESTED" and active == 0:
        run = set_run_status(conn, run_id, "PAUSED")
    elif status == "CANCEL_REQUESTED" and active == 0:
        repo.cancel_queued_jobs(conn, run_id)
        _cancel_pending_candidates(conn, context(conn, run_id))
        cancel = {**(run.get("cancel") or {}), "state": "CANCELLED", "completed_at": utcnow_iso()}
        repo.update_run_fields(conn, run_id, cancel=cancel)
        run = set_run_status(conn, run_id, "CANCELLED", reason_code=run.get("reason_code") or "CANCELLED_BY_USER")
    elif status in ("QUEUED", "RUNNING", "RESUMING") and open_ == 0:
        n_eval = conn.execute("SELECT COUNT(*) FROM evaluations WHERE run_id = ? AND status = 'EVALUATED'",
                              (run_id,)).fetchone()[0]
        n_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id = ?", (run_id,)).fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id = ? AND status = 'FAILED'", (run_id,)).fetchone()[0]
        generation_only = ((run["resolved_protocol"].get("console") or {}).get("search") or {}).get("evaluation_enabled", True) is False
        if generation_only and failed:
            run = set_run_status(conn, run_id, "FAILED", reason_code="GENERATION_FAILED")
        elif n_jobs and n_eval == 0 and failed:
            run = set_run_status(conn, run_id, "FAILED", reason_code="NO_SUCCESSFUL_EVALUATION")
        else:
            run = set_run_status(conn, run_id, "COMPLETED", reason_code=generation_shortfall(run) if generation_only else None)
    if run["terminal"] and status != run["status"] and run.get("parent_run_id"):
        log(conn, run["parent_run_id"], f"final retrain child {run_id} {run['status']}", child_run_id=run_id,
            child_status=run["status"], reason_code=run.get("reason_code"))
    return run


def generation_shortfall(run: dict[str, Any]) -> str | None:
    """The reason_code of a COMPLETED evaluation-free run that produced fewer candidates than requested.

    ``GENERATION_EMPTY`` when no seed generated any candidate, ``GENERATION_PARTIAL`` when at least one seed fell short
    of its requested count, ``None`` when every seed is complete. Reads the per-seed ``budget.generation_results``
    summaries; a seed without a summary counts as 0 generated.
    """
    search = ((run.get("resolved_protocol") or {}).get("console") or {}).get("search") or {}
    results = (run.get("budget") or {}).get("generation_results") or {}
    seeds = [str(s) for s in (search.get("seeds") or [])] or sorted(results)
    wanted = search.get("candidate_count")
    counts, short = [], False
    for seed in seeds:
        summary = results.get(seed) if isinstance(results.get(seed), dict) else {}
        generated = summary.get("generated_count")
        generated = generated if isinstance(generated, int) and not isinstance(generated, bool) else 0
        requested = summary.get("requested_count", wanted)
        counts.append(generated)
        if not (isinstance(requested, int) and generated >= requested):
            short = True
    if not counts or all(c == 0 for c in counts):
        return "GENERATION_EMPTY"
    return "GENERATION_PARTIAL" if short else None


def plan_final(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Queue the FINAL stage of every finalist candidate of a FINAL_RETRAIN child run (admission against Final)."""
    ctx = context(conn, run_id)
    created = []
    for cand in repo.list_candidates(conn, run_id):
        if cand["arm"] == "Final" and cand["candidate_status"] == "FINALIST":
            job = _schedule_stage(conn, ctx, cand, ctx.final_stage())
            if job is not None:
                created.append(job)
    return created


def abandon_queued_job(conn: sqlite3.Connection, job: dict[str, Any], *, candidate_status: str, rung_state: str,
                       reason_code: str) -> None:
    """Cancel a QUEUED job whose candidate cannot continue (RESUME_BLOCKED, budget exhausted at resume)."""
    if not jobs.cancel_job(conn, job["id"], reason_code):
        return
    ctx = context(conn, job["run_id"])
    if not (job.get("candidate_id") and job.get("rung")):
        return
    cand = repo.get_candidate(conn, job["candidate_id"])
    _, stage = _stage(ctx, cand, job["rung"])
    put_row(conn, ctx, base_row(ctx, cand, stage, candidate_status, job_id=job["id"], reason_code=reason_code))
    planner.set_rung_state(conn, cand["id"], stage.id, rung_state, reason_code=reason_code)
    cand = set_candidate(conn, cand["id"], candidate_status, stage.id)
    if cand["arm"] in SEARCH_ARMS:
        _refresh_generations(conn, ctx, cand)



# -- separate evaluation-free generation path --------------------------------------------------------------------
def _explicit_training(run: dict[str, Any]) -> bool:
    c = (run.get("resolved_protocol") or {}).get("console") or {}
    return bool(((c.get("extra") or {}).get("training") or {}).get("enabled"))


def _explicit_training_cfg(run: dict[str, Any]) -> dict[str, Any] | None:
    """``console.extra.training`` of an explicit training run, else None."""
    c = (run.get("resolved_protocol") or {}).get("console") or {}
    cfg = (c.get("extra") or {}).get("training")
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") else None


def _plan_generation(conn: sqlite3.Connection, ctx: RunContext) -> list[dict[str, Any]]:
    """Only CPU proposer jobs. No baseline, admission, stage, evaluator, parent pool or final retrain plan."""
    cfg = ctx.console["search"]
    created = []
    for seed in cfg["seeds"]:
        job, new = jobs.ensure_job(conn, run_id=ctx.run["id"], kind=JobKind.PROPOSE_GENERATION.value,
            protocol_hash=ctx.run["protocol_hash"], arm="IndependentRandom", search_seed=int(seed),
            params={"generation_only": True, "generation": 0, "arm": "IndependentRandom", "search_seed": int(seed),
                    "requested_method": cfg.get("requested_method", cfg.get("arms", [])), "effective_method": "RandomGeneration"},
            key_extra=f"RandomGeneration|{seed}")
        if new:
            created.append(job)
    return created


def generation_progress(conn: sqlite3.Connection, job: dict[str, Any], payload: dict[str, Any]) -> None:
    """Commit accepted candidates immediately (also on journal replay), never make provisional evaluation rows."""
    if not ((job.get("spec") or {}).get("params") or {}).get("generation_only"):
        return
    run = repo.get_run(conn, job["run_id"])
    if ((run["resolved_protocol"].get("console") or {}).get("search") or {}).get("evaluation_enabled", True) is not False:
        return
    candidates = payload.get("candidates") or ([payload["generated_candidate"]] if payload.get("generated_candidate") else [])
    for prop in candidates:
        row = dict(prop.get("row") or {})
        audit = row.get("audit") or {}
        if audit.get("origin") != "random_generation" or (audit.get("structure") or {}).get("status") != "PASS":
            continue
        cid = row.get("id")
        if not cid or repo.get_candidate(conn, cid) is not None:
            continue
        # Keep genotype/hash metadata, without creating an evaluation identity or a score.
        audit["proposal"] = {k: v for k, v in prop.items() if k != "row"}
        row["audit"] = audit
        allowed = {"id", "name", "arm", "family", "generation", "search_seed", "training_seed", "architecture_hash",
                   "architecture_yaml", "architecture_path", "parents", "parent_evaluation_ids", "lineage", "blocks", "candidate_status", "audit"}
        cand = repo.insert_candidate(conn, run_id=run["id"], **{k: v for k, v in row.items() if k in allowed})
        artifact = None
        if cand.get("architecture_path"):
            try:
                artifact = register_generated_architecture(conn, run, cand, meta={
                    "yaml_hash": prop.get("yaml_hash"), "architecture_hash": cand.get("architecture_hash")})
            except (ArtifactError, TypeError):
                # Missing or rejected files are not advertised as downloadable. Candidate structure is still durable.
                log(conn, run["id"], "generated configuration artifact could not be registered", candidate_id=cid,
                    reason_code="ARCHITECTURE_ARTIFACT_UNAVAILABLE")
        record_generated_revision(conn, run["id"], cand, yaml_hash=prop.get("yaml_hash"),
                                  artifact_id=(artifact or {}).get("id"))
    summary = payload.get("generation_summary")
    if isinstance(summary, dict):
        budget = dict(run.get("budget") or {})
        results = dict(budget.get("generation_results") or {})
        results[str(summary.get("seed", (job.get("spec") or {}).get("search_seed")))] = summary
        budget["generation_results"] = results
        updated = repo.update_run_fields(conn, run["id"], budget=budget)
        emit(conn, run["id"], "run.updated", run_payload(updated))
        emit(conn, run["id"], "snapshot.required", {"reason": "generated_candidates_changed"})


def _recover_generation(ctx: RunContext, job: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Recover a save-before-notification candidate after cancel, crash, lease loss or pause.

    This reads only the fixed journal name ``generation-<seed>.json`` of this run/seed and verifies immutable identities
    (run id, protocol hash, seed); it cannot follow arbitrary API paths or import source from the journal. Atomic journal
    replacement makes a concurrent read safe. The journal lives in ``run_dir(run)/architectures`` when the
    run's directory is usable on the core host (owner all-in-one), else in the core-local run directory where the owner
    worker wrote it; both are tried (:func:`_generation_journal_paths`), the first journal with this run's
    identity wins.
    """
    import json
    from mbnas_core.search.generation import _summary
    seed = (job.get("spec") or {}).get("search_seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        return {}
    for path in _generation_journal_paths(ctx, seed, conn):
        try:
            state = json.loads(path.read_text())
            if state["run_id"] != ctx.run["id"] or state["protocol_hash"] != ctx.run["protocol_hash"] or state["seed"] != seed:
                continue
            return {"candidates": state["candidates"], "generation_summary": _summary(state, ctx.console["search"])}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {}


def _generation_journal_paths(ctx: RunContext, seed: int, conn: sqlite3.Connection | None) -> list[Path]:
    """Candidate locations of the generation journal of ``seed``, in this order:

    1. ``run_dir(run)/architectures`` (owner all-in-one; the former only location);
    2. ``core_run_dir(<core settings>)/architectures`` with the core settings of this process's environment
       (:func:`_core_run_dir_from_settings`; best effort - an owner worker started with ``--env-file`` does not export
       them, then 3. applies);
    3. with ``conn``: the core-local run directory of one of this run's stored generated candidates, verified by
       :func:`core_local_run_dir` (its YAML file holds exactly the candidate's recorded text).
    """
    from mbnas_contracts.util.paths import is_within

    name = f"generation-{seed}.json"
    out: list[Path] = []
    try:
        base = run_dir(ctx.run).resolve()
        own = base / "architectures" / name
        if is_within(str(own.resolve()), str(base)):
            out.append(own)
    except ArtifactError:
        pass
    local = _core_run_dir_from_settings(ctx.run["id"])
    if local is not None:
        out.append(local / "architectures" / name)
    if conn is not None:
        for cand in repo.list_candidates(conn, ctx.run["id"]):
            found = core_local_run_dir(ctx.run, cand)
            if found is not None:
                out.append(found / "architectures" / name)
                break
    return list(dict.fromkeys(out))


def _core_run_dir_from_settings(run_id: str) -> Path | None:
    """``core_run_dir(CoreSettings.load(), run)`` (core-local layout) or None when the settings cannot be loaded."""
    try:
        from mbnas_core.config import CoreSettings
        from mbnas_core.owner_worker.paths import core_run_dir

        return core_run_dir(CoreSettings.load(), str(run_id))
    except Exception:  # noqa: BLE001 - best effort; the stored-candidate source still applies
        return None


def core_local_run_dir(run: dict[str, Any], cand: dict[str, Any]) -> Path | None:
    """The core-local run directory that holds ``cand``'s generated YAML file, or None.

    Accepted only when the candidate's recorded ``architecture_path`` is an absolute
    ``<root>/<run id>/architectures/<candidate id>.yaml`` (the layout of the owner worker's PROPOSE_GENERATION, a ``:`` of
    the id written as ``_``) and names a regular file (no symlink) of at most ``CORE_LOCAL_MAX_BYTES`` (1 MiB) whose
    bytes are exactly the candidate's recorded ``architecture_yaml``: a candidate row never makes the core read or
    register another file. The core download route applies the same guards when it serves the file
    (``artifacts.registry.resolve_file``).
    Returns ``<root>/<run id>``."""
    raw, text = cand.get("architecture_path"), cand.get("architecture_yaml")
    if not isinstance(raw, str) or not isinstance(text, str) or not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    path = Path(raw)
    if ".." in path.parts or path.name != str(cand.get("id") or "").replace(":", "_") + ".yaml":
        return None
    arch = path.parent
    if arch.name != "architectures" or arch.parent.name != str(run.get("id") or ""):
        return None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > CORE_LOCAL_MAX_BYTES:
            return None
        if path.read_bytes() != text.encode("utf-8"):
            return None
    except OSError:
        return None
    return arch.parent


def register_generated_architecture(conn: sqlite3.Connection, run: dict[str, Any], cand: dict[str, Any], *,
                                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register a generated candidate's YAML file (evaluation-free generation) as its ``architecture`` artifact.

    A file inside ``run_dir(run)`` is registered relative to it, as before (owner all-in-one). In a split
    deployment ``console.paths.output_root`` is a customer-host path and the owner worker writes the YAMLs to the
    core-local run directory; a file outside the run directory is then registered relative to the
    core-local run directory that :func:`core_local_run_dir` verified, with ``meta.location = core_local`` (the core
    resolves its bytes there; the relpath stays ``architectures/<file>``). Raises :class:`ArtifactError` like
    :func:`register_file` when neither applies."""
    meta = dict(meta or {})
    path = str(cand.get("architecture_path") or "")
    try:
        return register_file(conn, run=run, path=path, kind="architecture", candidate_id=cand["id"], meta=meta)
    except ArtifactError as exc:
        local = core_local_run_dir(run, cand) if exc.code == "ACCESS_DENIED" else None
        if local is None:
            raise
    shell = {"id": run["id"], "resolved_protocol": {"console": {"paths": {"output_root": str(local.parent)}}}}
    return register_file(conn, run=shell, path=path, kind="architecture", candidate_id=cand["id"],
                         meta={**meta, "location": CORE_LOCAL})
