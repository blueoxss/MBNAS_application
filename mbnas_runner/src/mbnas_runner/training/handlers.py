"""Training job handler for ``CANDIDATE_TRAIN`` / ``BASELINE_TRAIN`` / ``FINAL_RETRAIN``.

``run_training_job(spec, emit, settings=None) -> JobResult``:

1. validates ``spec.params`` (:class:`TrainJobParams`; ``stage_epochs_added`` is required for ``CANDIDATE_TRAIN`` /
   ``BASELINE_TRAIN`` — a job without the stage hint is ``JOB_PARAMS_INVALID``, never a scratch start), loads the resolved protocol (hash must equal
   ``spec.protocol_hash``), checks ``run_dir`` against the output allowlist and prepares backend environments;
2. resolves the model from ``params.arch`` (required: the core resolves registry baselines and their availability,
   DD-A16), the split manifests (hashes must equal the protocol's) and a run-local YOLO view, and the recipe from the
   protocol;
3. runs ``StageTrainer.run_until(candidate_id, cumulative_target, fixed_horizon, protocol_hash)`` with
   ``expected_resume=params.resume_from`` (the orchestrator's latest non-PRUNED ``training_full`` row) and
   ``prior_epochs = cumulative_target - params.stage_epochs_added``: a later stage whose state is absent is
   ``RESUME_BLOCKED MISSING_STATE:checkpoint_file`` (never a scratch restart); a pause request
   (``params.stop_request_path`` exists) stops at the next epoch boundary and returns ``CANCELLED`` with
   ``reason_code="PAUSED_AT_EPOCH"`` (the full state of that epoch is on disk; the next attempt resumes from it);
4. evaluates the exact rung-end EMA snapshot inline (``evaluation.handlers.evaluate_checkpoint``) on ``search_val``
   (``final_eval`` only for ``FINAL_RETRAIN``; search jobs never load it); returns ``SUCCEEDED`` with ``outputs = {"epochs_done", "rung_result", "evaluation",
   "recipe", "init", "architecture_hash", "timings"}`` (the worker contract's ``{"epochs_done", "evaluation"}``).
   The runner computes no fitness and no gate: the core scores the raw metrics with the source rung's weights (DD-A15).
   ``FINAL_RETRAIN`` (rung ``FINAL``, scratch, the pinned final epochs/horizon from the job spec): the final split is
   evaluated with panels labelled ``split=final_eval`` (first 2 sorted final samples). Every split read is in
   ``<run_dir>/logs/split_access.jsonl``.

Progress goes through ``emit`` as :class:`ProgressMessage` (``progress`` per epoch, ``checkpoint`` per file, ``metric``
for the evaluation). Failure reason codes: ``JOB_PARAMS_INVALID``, ``RESUME_BLOCKED`` (detail in message),
``PROTOCOL_HASH_MISMATCH``,
``DATASET_HASH_MISMATCH``, ``DATASET_DRIFT``, ``ARCH_REQUIRED``, capability codes (``INIT_UNSUPPORTED``,
``PRECISION_FALLBACK``, ``BATCH_CHANGED`` …), ``EVAL_FAILED`` (training finished, evaluation failed) and
``TRAIN_FAILED`` (anything else; message carries the exception).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from mbnas_model_runtime.adapters.base import LATENCY_SCOPES, CapabilityError, DataSpec, ResumeBlocked
from mbnas_runner.config import RunnerSettings
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobResult, JobSpec, JobStatus, ProgressMessage
from mbnas_contracts.util.time import utcnow_iso
from mbnas_runner.training.context import (
    JobInputError, SplitAccess, check_run_dir, jsonable, load_protocol, prepare_backend_env, progress_bridge,
    resolve_model, split_manifests,
)
from mbnas_model_runtime.datasets.yolo_view import DatasetDrift, build_yolo_view
from mbnas_runner.training.recipe import baseline_resolved, input_shape_for, recipe_from_protocol
from mbnas_runner.training.stage_trainer import StageTrainer


class TrainJobParams(BaseModel):
    """``JobSpec.params`` of a training job (the worker/scheduler fills it)."""

    model_config = ConfigDict(extra="forbid")

    run_dir: str
    adapter_id: str
    protocol_path: str | None = None
    resolved_protocol: dict[str, Any] | None = None
    arch: dict[str, Any] | None = None
    init: Literal["scratch", "pretrained_native"] | None = None
    input_shape: list[int] | None = None
    device: str = "cuda:0"
    device_entry: str | None = None
    stop_request_path: str | None = None
    evaluate: bool = True
    eval_batch: int = Field(default=8, ge=1)
    image_samples: int = Field(default=2, ge=0)
    latency_scopes: list[str] = Field(default_factory=lambda: list(LATENCY_SCOPES))
    # the orchestrator's expected resume source: latest non-PRUNED training_full row {id, path, sha256, epoch, ...}
    resume_from: dict[str, Any] | None = None
    # epochs this job's stage adds; < cumulative_target means a later stage (a scratch start is refused). Required for
    # CANDIDATE_TRAIN / BASELINE_TRAIN (validated with context={"kind", "cumulative_target"}); FINAL_RETRAIN (always a
    # scratch run of the whole final horizon) defaults it to cumulative_target.
    stage_epochs_added: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _stage_is_explicit(self, info: ValidationInfo) -> "TrainJobParams":
        ctx = info.context or {}
        kind = ctx.get("kind")
        kind = getattr(kind, "value", kind)
        if self.stage_epochs_added is None and kind in (JobKind.CANDIDATE_TRAIN.value, JobKind.BASELINE_TRAIN.value):
            raise ValueError(f"stage_epochs_added is required for {kind} jobs: without the stage hint a later stage "
                             "would run with first-stage (scratch) semantics")
        if self.stage_epochs_added is None and kind == JobKind.FINAL_RETRAIN.value and ctx.get("cumulative_target"):
            self.stage_epochs_added = int(ctx["cumulative_target"])
        return self

    @classmethod
    def for_job(cls, spec: JobSpec) -> "TrainJobParams":
        """Validate ``spec.params`` for this job's kind (the stage hint is enforced per kind)."""
        return cls.model_validate(spec.params, context={"kind": spec.kind, "cumulative_target": spec.cumulative_target})


def candidate_dir(run_dir: Path, candidate_id: str) -> Path:
    """``<run_dir>/candidates/<candidate_id>`` (ids are validated to be single path components)."""
    if not candidate_id or "/" in candidate_id or candidate_id in (".", "..") or "\x00" in candidate_id:
        raise JobInputError("CANDIDATE_ID_INVALID", f"candidate id {candidate_id!r} is not a safe path component")
    return run_dir / "candidates" / candidate_id


def run_training_job(spec: JobSpec, emit: Callable[[ProgressMessage], None],
                     settings: RunnerSettings | None = None) -> JobResult:
    if spec.kind not in TRAINING_KINDS:
        raise ValueError(f"run_training_job got a {spec.kind} job")
    started = utcnow_iso()
    t0 = time.perf_counter()
    rung_result = None
    try:
        params = TrainJobParams.for_job(spec)
        settings = settings or RunnerSettings.load()
        protocol = load_protocol(params, spec)
        run_dir = check_run_dir(params.run_dir, settings)
        prepare_backend_env(settings)
        arch, adapter, entry = resolve_model(params, settings)
        final = spec.kind == JobKind.FINAL_RETRAIN
        explicit_cfg = (protocol.get("console", {}).get("extra") or {}).get("training") or {}
        no_validation = explicit_cfg.get("enabled") is True and params.evaluate is False
        names, manifests = split_manifests(protocol, settings,
                                           ("train", "search_val", "final_eval") if final else ("train",) if no_validation else ("train", "search_val"),
                                           access=SplitAccess.for_job(run_dir, spec, purpose=(
                                               "final retrain: train/search_val for training, final_eval only for "
                                               "the final evaluation" if final else "search/baseline training")))
        if no_validation:
            manifests["search_val"] = manifests["train"]  # hash-only placeholder; no validation loader/evaluator reads it
        view_yaml = build_yolo_view(run_dir / "data_view", {"train": manifests["train"], "val": manifests["search_val"]},
                                    names)
        caps = adapter.capability()
        shape = input_shape_for(protocol, params.adapter_id, params.input_shape,
                                caps.get("input_size_rule"))
        recipe = recipe_from_protocol(protocol, shape, fixed_horizon=int(spec.fixed_horizon))  # type: ignore[arg-type]
        resolved = baseline_resolved(protocol, params.adapter_id) or {}
        init = params.init or resolved.get("init") or protocol["training"]["initialization"]
        stop_path = Path(params.stop_request_path) if params.stop_request_path else None
        trainer = StageTrainer(adapter=adapter, candidate_dir=candidate_dir(run_dir, spec.candidate_id),  # type: ignore[arg-type]
                               arch=arch, data=DataSpec(names, manifests["train"], manifests["search_val"],
                                                        view_root=view_yaml.parent),
                               recipe=recipe, init=init, training_seed=int(spec.training_seed or 0),
                               device=params.device, progress=progress_bridge(spec, emit))
        target = int(spec.cumulative_target)  # type: ignore[arg-type]
        added = params.stage_epochs_added if params.stage_epochs_added is not None else target  # FINAL w/o target
        prior = max(0, target - added)
        rung_result = trainer.run_until(spec.candidate_id, target, int(spec.fixed_horizon),  # type: ignore[arg-type]
                                        spec.protocol_hash, rung=spec.rung,
                                        stop_requested=(lambda: stop_path.exists()) if stop_path else None,
                                        expected_resume=params.resume_from, prior_epochs=min(prior, target - 1))
        train_s = time.perf_counter() - t0
        outputs: dict[str, Any] = {"epochs_done": rung_result.epochs_done,
                                   "rung_result": jsonable(rung_result.as_dict()), "recipe": recipe, "init": init,
                                   "architecture_hash": rung_result.hashes.get("architecture"),
                                   "registry_status": entry["status"] if entry else None}
        if rung_result.stopped_reason == "PAUSED_AT_EPOCH":
            return JobResult(job_id=spec.job_id, status=JobStatus.CANCELLED, attempt=spec.attempt, exit_code=0,
                             reason_code="PAUSED_AT_EPOCH", message=f"paused after epoch {rung_result.epochs_done}",
                             outputs=jsonable(outputs), gpu_seconds=round(train_s, 4), started_at=started,
                             finished_at=utcnow_iso())
        timings = {"train_s": round(train_s, 4)}
        if params.evaluate:
            outputs["evaluation"], eval_timings = _evaluate(spec, params, protocol, adapter, rung_result, manifests,
                                                            run_dir, shape, emit)
            timings.update(eval_timings)
        outputs["timings"] = timings
        return JobResult(job_id=spec.job_id, status=JobStatus.SUCCEEDED, attempt=spec.attempt, exit_code=0,
                         outputs=jsonable(outputs), gpu_seconds=round(sum(timings.values()), 4), started_at=started,
                         finished_at=utcnow_iso())
    except ValidationError as exc:
        return _failed(spec, "JOB_PARAMS_INVALID", str(exc), started, t0)
    except ResumeBlocked as exc:
        return _failed(spec, "RESUME_BLOCKED", f"{exc.reason}: {exc.detail}", started, t0)
    except (JobInputError, CapabilityError) as exc:
        return _failed(spec, exc.code, exc.message, started, t0)
    except DatasetDrift as exc:
        return _failed(spec, "DATASET_DRIFT", str(exc), started, t0)
    except _EvalFailed as exc:
        return _failed(spec, "EVAL_FAILED", str(exc), started, t0,
                       outputs={"rung_result": jsonable(rung_result.as_dict())} if rung_result else None)
    except Exception as exc:  # noqa: BLE001 - every other failure is a recorded TRAIN_FAILED, never a crash
        return _failed(spec, "TRAIN_FAILED", f"{type(exc).__name__}: {exc}",
                       started, t0)


class _EvalFailed(RuntimeError):
    pass


def _evaluate(spec: JobSpec, params: TrainJobParams, protocol: dict[str, Any], adapter: Any, rung_result: Any,
              manifests: dict[str, Any], run_dir: Path, shape: list[int],
              emit: Callable[[ProgressMessage], None]) -> tuple[dict[str, Any], dict[str, float]]:
    from mbnas_runner.evaluation.handlers import evaluate_checkpoint

    bench = protocol["console"]["benchmark"]
    final = spec.kind == JobKind.FINAL_RETRAIN
    split = "final_eval" if final else "search_val"  # search never sees final_eval
    out_dir = Path(rung_result.rung_end_ema_eval_ckpt).parent.parent / "eval" / (
        f"{spec.rung or 'stage'}_{split}_e{rung_result.epochs_done:04d}_{spec.job_id}")
    try:
        payload = evaluate_checkpoint(adapter, rung_result.rung_end_ema_eval_ckpt, manifests[split],
                                      run_dir=run_dir, out_dir=out_dir, device=params.device,
                                      precision=bench.get("precision", "FP16"), eval_batch=params.eval_batch,
                                      input_shape=shape, benchmark=bench, scopes=params.latency_scopes,
                                      image_samples=params.image_samples,
                                      device_entry=params.device_entry or bench.get("device"), split=split)
    except Exception as exc:  # noqa: BLE001 - reported as EVAL_FAILED with the trained checkpoint kept
        raise _EvalFailed(f"{type(exc).__name__}: {exc}") from exc
    payload.update({"eval_split": split, "checkpoint_path": rung_result.rung_end_training_ckpt,
                    "checkpoint_sha256": rung_result.checkpoint_sha256.get("training_full"),
                    "checkpoint_sha256s": rung_result.checkpoint_sha256, "lr_history": rung_result.lr_history,
                    "requested_input_shape": list(protocol["training"]["requested_input_size"]),
                    "train_batch": int(protocol["training"]["batch_per_gpu"]),
                    "resolved_recipe": rung_result.resolved_recipe, "global_step": rung_result.step_count,
                    "samples_seen": rung_result.samples_seen, "epochs": rung_result.epochs_done,
                    "epoch_target_total": rung_result.cumulative_target,
                    "architecture_hash": rung_result.hashes.get("architecture")})
    emit(ProgressMessage(type="metric", job_id=spec.job_id, ts=utcnow_iso(),
                         payload=jsonable({k: payload.get(k) for k in ("ap", "ap_50", "recall", "latency_ms", "size_mb",
                                                                        "evaluation_hash")})))
    return jsonable(payload), dict(payload["timings"])


def _failed(spec: JobSpec, code: str, message: str, started: str, t0: float,
            outputs: dict[str, Any] | None = None) -> JobResult:
    return JobResult(job_id=spec.job_id, status=JobStatus.FAILED, attempt=spec.attempt, exit_code=1, reason_code=code,
                     message=message[:4000], outputs=outputs or {}, gpu_seconds=round(time.perf_counter() - t0, 4),
                     started_at=started, finished_at=utcnow_iso())
