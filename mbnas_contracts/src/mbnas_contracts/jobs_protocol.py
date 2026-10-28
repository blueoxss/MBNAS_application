"""Contract between the worker child and the job handlers implemented by the training/evaluation/benchmark/search
modules. Handler authors import from here; nothing in this module touches the DB.

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

This module holds the handler-protocol types only (``JobFailure``, ``JobPaused``, ``Emitter``, ``Handler``,
``STOP_ENV``, ``PAUSED_REASON``, ``PAUSE_REASONS``, ``HANDLERS_ENV``). The handler table and its
resolution (``DEFAULT_HANDLERS``, ``resolve_handler``, ``handler_override``, ``_load``) live with the processes that run
handlers: ``mbnas_runner.contract`` (runner job kinds) and the owner worker of the core host.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import IO, Any

from mbnas_contracts.schemas.jobs import JobResult, JobSpec, ProgressMessage
from mbnas_contracts.util.time import utcnow_iso

HANDLERS_ENV = "MBNAS_JOB_HANDLERS_MODULE"
STOP_ENV = "MBNAS_STOP_REQUEST_FILE"
PAUSED_REASON = "PAUSED_AT_SAFE_BOUNDARY"
PAUSE_REASONS = frozenset({PAUSED_REASON, "PAUSED_AT_EPOCH"})  # the training handler reports PAUSED_AT_EPOCH


class JobFailure(Exception):
    """Known job failure reported by a handler."""

    def __init__(self, reason_code: str, message: str = "", *, retryable: bool = False,
                 outputs: dict[str, Any] | None = None) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code
        self.retryable = retryable
        self.outputs = outputs or {}


class JobPaused(Exception):
    """The handler stopped at a safe boundary because a stop was requested (checkpoint already written)."""

    def __init__(self, outputs: dict[str, Any] | None = None, message: str = "stopped at a safe boundary") -> None:
        super().__init__(message)
        self.outputs = outputs or {}


Handler = Callable[[JobSpec, "Emitter"], "dict[str, Any] | JobResult"]


class Emitter:
    """Writes ``ProgressMessage`` JSON lines to the protocol stream (the child's original stdout)."""

    def __init__(self, job_id: str, stream: IO[str], stop_request_path: str | None = None) -> None:
        self.job_id = job_id
        self._stream = stream
        self.stop_request_path = stop_request_path

    def __call__(self, msg: ProgressMessage) -> None:
        """Forward a ready-made ProgressMessage (handlers that build their own messages call ``emit(msg)``)."""
        self._stream.write(msg.to_line() + "\n")
        self._stream.flush()

    def _send(self, type: str, **fields: Any) -> None:
        msg = ProgressMessage(type=type, job_id=self.job_id, ts=utcnow_iso(), **fields)  # type: ignore[arg-type]
        self._stream.write(msg.to_line() + "\n")
        self._stream.flush()

    def progress(self, *, epoch: int | None = None, epoch_target_total: int | None = None,
                 global_step: int | None = None, samples_seen: int | None = None,
                 learning_rate: float | None = None, **payload: Any) -> None:
        """Live training progress (the worker throttles DB/event writes to <= 1/s)."""
        self._send("progress", epoch=epoch, epoch_target_total=epoch_target_total, global_step=global_step,
                   samples_seen=samples_seen, learning_rate=learning_rate, payload=payload)

    def checkpoint(self, *, kind: str, path: str, sha256: str, bytes: int, epoch: int, rung: str | None = None,
                   hashes: dict[str, Any] | None = None) -> None:
        """A durable checkpoint file was written atomically (kind: training_full|eval_ema|weights_only|best)."""
        self._send("checkpoint", epoch=epoch, payload={"kind": kind, "path": path, "sha256": sha256, "bytes": bytes,
                                                       "epoch": epoch, "rung": rung, "hashes": hashes or {}})

    def log(self, message: str, **payload: Any) -> None:
        self._send("log", message=message, payload=payload)

    def metric(self, **payload: Any) -> None:
        self._send("metric", payload=payload)

    def heartbeat(self) -> None:
        self._send("heartbeat")

    def result(self, result: JobResult) -> None:
        """Final line (written by the child runner, not by handlers)."""
        self._send("result", payload=result.model_dump(mode="json"))

    def stop_requested(self) -> bool:
        """True once the worker asked this job to stop at the next safe boundary (pause/cancel)."""
        return bool(self.stop_request_path) and os.path.exists(self.stop_request_path)  # type: ignore[arg-type]

    def stop_request(self) -> dict[str, Any] | None:
        """Content of the stop request (``{"action": "pause"|"cancel", "requested_at": ...}``) or None."""
        if not self.stop_requested():
            return None
        try:
            return json.loads(open(self.stop_request_path, encoding="utf-8").read())  # type: ignore[arg-type]
        except (OSError, ValueError):
            return {"action": "pause"}
