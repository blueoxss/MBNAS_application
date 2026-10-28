"""Handler table of the owner worker plus the handler contract.

The handler <-> child protocol types (``JobFailure``, ``JobPaused``, ``Emitter``, ``Handler``, ``STOP_ENV``,
``PAUSED_REASON``, ``PAUSE_REASONS``, ``HANDLERS_ENV``) live in :mod:`mbnas_contracts.jobs_protocol` and are
re-exported here; this module keeps the owner's :data:`DEFAULT_HANDLERS` (PROPOSE_GENERATION only: the core search
handler), :func:`resolve_handler`, :func:`handler_override` and :func:`_load`. The five GPU kinds are executed by
remote runners (``mbnas_runner.contract`` holds their table); the core table names no
``mbnas_runner`` handler. A stub module named by ``$MBNAS_JOB_HANDLERS_MODULE`` may still serve any kind (tests).
Nothing in this module touches the DB.

Contract between the worker child and the job handlers implemented by the training/evaluation/benchmark/search
modules:

Handler signature: ``handler(spec: JobSpec, emit: Emitter) -> dict | JobResult``. ``emit`` is callable with a
ready-made ``ProgressMessage`` (``emit(msg)``) and also offers the helper methods below.

* A returned ``dict`` is the ``outputs`` of a SUCCEEDED result. Raise :class:`JobFailure` for a known failure
  (``reason_code`` such as ``RESUME_BLOCKED``, ``CUDA_OOM``, ``BUILD_INVALID``; ``retryable`` only for transient
  infrastructure problems), or :class:`JobPaused` after honouring :meth:`Emitter.stop_requested` at an epoch
  boundary with a full-state checkpoint written. Any other exception is a retryable ``CHILD_EXCEPTION``.
* stdout of the child belongs to the protocol (JSONL ``ProgressMessage``); library prints are redirected to stderr.
* With the stub override (``$MBNAS_JOB_HANDLERS_MODULE``) the child adds these generic keys to ``spec.params``: ``protocol`` (the frozen resolved
  protocol; its hash equals ``spec.protocol_hash``), ``run_dir`` (all outputs go below it), ``work_dir``
  (``run_dir/candidates/<safe candidate id>`` or ``run_dir/jobs/<job_id>``), ``resolved_protocol_path``,
  ``stop_request_path``, ``attempt``; PROPOSE_GENERATION also gets ``architecture_dir``
  (``run_dir/architectures``); training kinds get ``resume_from`` = latest ``training_full`` checkpoint row of the
  candidate (``{id, path, sha256, epoch, rung, hashes}``) or ``None``.
* The real handlers validate strict parameter models, so for them the child translates the orchestrator's job
  parameters into each handler's native form (``worker.child.native_params``): TrainJobParams (``run_dir``,
  ``adapter_id``, ``resolved_protocol``, ``arch`` for search candidates, ``init``, ``device``,
  ``stop_request_path``, ``evaluate``, ``resume_from`` = latest non-PRUNED ``training_full`` row,
  ``stage_epochs_added`` = ``stage.epochs_added``; required for CANDIDATE_TRAIN/BASELINE_TRAIN — a job without it
  fails ``JOB_PARAMS_INVALID``; ``resume_from.sha256`` is cross-checked against the resumed file's sidecar,
  ``RESUME_BLOCKED CHECKSUM_MISMATCH:recorded``), EvalJobParams (+ ``eval_ckpt`` = latest ``eval_ema`` checkpoint) and the search
  handler's loose params (``protocol``, ``generation``, ``parent_pool``, ``architecture_dir``).

Outputs the orchestrator consumes:

* training kinds and EVALUATE: ``{"epochs_done": int, "evaluation": {...}}`` where ``evaluation`` holds the
  common-evaluator result of the exact rung-end EMA snapshot: ``ap, recall`` (fractions), ``latency_ms``,
  ``size_mb``, ``latency_source``, ``latency_scope``, ``device``, ``precision``, ``evaluation_hash``,
  ``split_hash``, ``checkpoint_path``, ``checkpoint_sha256``, ``architecture_hash``, ``batch``,
  ``requested_input_shape``, ``resolved_input_shape``, optional ``raw_metrics``, ``latency_statistics``,
  ``metric_definitions`` and ``artifacts: [{kind, path, sha256?, mime_type?, sample_id?, split_hash?, matching?}]``
  (paths inside ``run_dir``). A training result without ``evaluation`` makes the orchestrator queue an EVALUATE job.
* PROPOSE_GENERATION: ``{"candidates": [{"row": <db.repo.insert_candidate kwargs without run_id>, "genotype": ...,
  ...}], "search_state"?: {...}}`` (a flat candidate dict with the same keys plus ``valid``/``reason_code`` is also
  accepted). Extra per-candidate keys (genotype, yaml_hash, params, flops) are kept in ``audit.proposal``; GA
  parent pools are built from them. ``search_state`` is handed back as ``params.previous_search_state``.
"""
from __future__ import annotations

import importlib
import os

from mbnas_contracts.jobs_protocol import (  # noqa: F401 - re-exported for owner-side callers
    HANDLERS_ENV, PAUSE_REASONS, PAUSED_REASON, STOP_ENV, Emitter, Handler, JobFailure, JobPaused,
)
from mbnas_contracts.schemas.jobs import JobKind

DEFAULT_HANDLERS: dict[str, str] = {
    JobKind.PROPOSE_GENERATION.value: "mbnas_core.search.handlers:propose_generation",
}


def _load(ref: str) -> Handler:
    module, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module), attr)


def handler_override() -> str | None:
    """Module named by ``$MBNAS_JOB_HANDLERS_MODULE`` (test/smoke stubs), or None for the real handlers."""
    return os.environ.get(HANDLERS_ENV) or None


def resolve_handler(kind: str) -> Handler:
    """Handler for ``kind``: ``$MBNAS_JOB_HANDLERS_MODULE.HANDLERS[kind]`` when set (tests), else the default."""
    override = handler_override()
    if override:
        handlers = getattr(importlib.import_module(override), "HANDLERS")
        if kind not in handlers:
            raise JobFailure("HANDLER_MISSING", f"{override}.HANDLERS has no handler for {kind}")
        return handlers[kind]
    ref = DEFAULT_HANDLERS.get(kind)
    if ref is None:
        raise JobFailure("HANDLER_MISSING", f"no handler registered for job kind {kind}")
    try:
        return _load(ref)
    except (ImportError, AttributeError) as exc:
        raise JobFailure("HANDLER_UNAVAILABLE", f"cannot import {ref}: {exc}") from exc
