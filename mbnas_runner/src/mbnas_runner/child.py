"""Per-job runner child: ``python -m mbnas_runner.child --spec-file <job-spec.json>``.

The child reads its JobSpec from a JSON file (``job_id, run_id, kind, protocol_hash, candidate_id, rung,
cumulative_target, fixed_horizon, arm, search_seed, training_seed, attempt, params``; ``params`` is already in the form
the handler for the job kind expects, see :mod:`mbnas_contracts.jobs_protocol`), runs the handler for the job kind and
reports through JSONL ``ProgressMessage`` lines on the original stdout. Everything else written to fd 1 by libraries is
redirected to stderr (the caller stores stderr in the per-attempt job log). The last line is always one ``result``
message; the exit code is 0 for SUCCEEDED, 3 otherwise. The child has no database and imports nothing from the core:
who writes the spec file (smoke runs and tests today, the runner service from WP-2 on) is not its concern.

GPU job kinds import torchvision before the handler runs: ultralytics' NMS (native per-epoch validation, AP
evaluation, latency) uses ``torchvision.ops.nms`` only when torchvision is already imported and otherwise falls back to
a Python loop; a fresh child would not have it imported.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import IO, Any

from pydantic import ValidationError

from mbnas_contracts.jobs_protocol import PAUSED_REASON, STOP_ENV, Emitter, JobFailure, JobPaused
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobResult, JobSpec, JobStatus
from mbnas_contracts.util.time import utcnow_iso
from mbnas_runner.contract import handler_override, resolve_handler


def _protocol_stream() -> IO[str]:
    """Duplicate fd 1 for the protocol, then point fd 1 (and sys.stdout) at stderr."""
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


def safe_name(candidate_id: str) -> str:
    """Filesystem-safe directory name of a candidate id (``:``/``/`` replaced)."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in candidate_id)


NMS_KINDS = frozenset({*(k.value for k in TRAINING_KINDS), JobKind.EVALUATE.value, JobKind.BENCHMARK.value})


def load_spec(path: str | os.PathLike[str]) -> JobSpec:
    """JobSpec from a JSON file; ``JobFailure("SPEC_INVALID")`` when the file is unreadable or not a valid spec."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobFailure("SPEC_INVALID", f"cannot read job spec {path}: {type(exc).__name__}: {exc}"[:2000]) from exc
    try:
        return JobSpec.model_validate(raw)
    except ValidationError as exc:
        raise JobFailure("SPEC_INVALID", f"job spec {path} is invalid: {exc}"[:2000]) from exc


def _job_id_hint(path: str | os.PathLike[str]) -> str:
    """``job_id`` of a spec file that failed validation (so the result line still names the job), else ``unknown``."""
    try:
        job_id = json.loads(Path(path).read_text(encoding="utf-8")).get("job_id")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return job_id if isinstance(job_id, str) and job_id else "unknown"


def observed_cuda_device() -> dict[str, Any]:
    """``{device_used: "cuda:<i>", device_uuid: "GPU-<uuid>"}`` of the CUDA device this process used, read through
    torch, or ``{}`` when this process never initialised CUDA (a CPU job, or a handler that failed before touching the
    device). Read-only: CUDA is never initialised here. The uuid is NVML's ``GPU-`` form of torch's device uuid."""
    torch = sys.modules.get("torch")
    if torch is None:
        return {}
    try:
        if not torch.cuda.is_initialized():
            return {}
        index = int(torch.cuda.current_device())
        uuid = str(torch.cuda.get_device_properties(index).uuid)
    except Exception:  # noqa: BLE001 - an observation, never a reason to fail the job
        return {}
    return {"device_used": f"cuda:{index}", "device_uuid": uuid if uuid.startswith("GPU-") else f"GPU-{uuid}"}


def with_observed_device(spec: JobSpec, result: JobResult) -> JobResult:
    """DD-A49 (a), child side: results of the device kinds (:data:`NMS_KINDS`) carry the CUDA device the job ran on
    (:func:`observed_cuda_device`); values the handler set itself are kept. The runner daemon completes and, for a
    CPU-only runner (DD-A32), overrides them before the commit (``RunnerDaemon._with_device``)."""
    if spec.kind.value not in NMS_KINDS:
        return result
    seen = observed_cuda_device()
    if not seen:
        return result
    return result.model_copy(update={"outputs": {**seen, **(result.outputs or {})}})


def run_job(spec: JobSpec, emit: Emitter) -> JobResult:
    """Run the handler and convert its outcome (or exception) into a JobResult (with the device it ran on, see
    :func:`with_observed_device`)."""
    return with_observed_device(spec, _run_handler(spec, emit))


def _run_handler(spec: JobSpec, emit: Emitter) -> JobResult:
    started = utcnow_iso()
    base = {"job_id": spec.job_id, "attempt": spec.attempt, "started_at": started}
    try:
        training_cfg = ((spec.params.get("resolved_protocol") or {}).get("console", {}).get("extra") or {}).get("training") or {}
        if spec.kind == JobKind.EVALUATE and training_cfg.get("enabled") is True and training_cfg.get("mode") == "evaluate_only" and handler_override() is None:
            from mbnas_runner.evaluation.official_evaluation import run_official_evaluation
            out = run_official_evaluation(spec, emit)
        else:
            out = resolve_handler(spec.kind.value)(spec, emit)
        if isinstance(out, JobResult):
            return out
        return JobResult(status=JobStatus.SUCCEEDED, outputs=dict(out or {}), finished_at=utcnow_iso(), **base)
    except JobPaused as paused:
        return JobResult(status=JobStatus.CANCELLED, reason_code=PAUSED_REASON, message=str(paused),
                         outputs=paused.outputs, finished_at=utcnow_iso(), **base)
    except JobFailure as fail:
        return JobResult(status=JobStatus.FAILED, reason_code=fail.reason_code, message=fail.message,
                         outputs={**fail.outputs, "retryable": fail.retryable}, finished_at=utcnow_iso(), **base)
    except Exception as exc:  # noqa: BLE001 - reported as a retryable failure, traceback on stderr
        traceback.print_exc()
        return JobResult(status=JobStatus.FAILED, reason_code="CHILD_EXCEPTION",
                         message=f"{type(exc).__name__}: {exc}"[:2000], outputs={"retryable": True},
                         finished_at=utcnow_iso(), **base)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mbnas_runner.child",
                                     description="MBNAS runner child: run one job from a JobSpec JSON file")
    parser.add_argument("--spec-file", required=True, help="JobSpec JSON file (see the module docstring for its keys)")
    args = parser.parse_args(argv)
    proto = _protocol_stream()
    stop_path = os.environ.get(STOP_ENV)
    try:
        spec = load_spec(args.spec_file)
    except JobFailure as fail:
        job_id = _job_id_hint(args.spec_file)
        Emitter(job_id, proto, stop_path).result(JobResult(job_id=job_id, status=JobStatus.FAILED,
                                                           reason_code=fail.reason_code, message=fail.message,
                                                           finished_at=utcnow_iso()))
        return 3
    # the spec author may name the stop file in params when the caller sets no $MBNAS_STOP_REQUEST_FILE
    emit = Emitter(spec.job_id, proto, stop_path or spec.params.get("stop_request_path") or None)
    if spec.kind.value in NMS_KINDS:
        from mbnas_model_runtime.evaluation.latency import ensure_torchvision

        ensure_torchvision()
    result = run_job(spec, emit)
    emit.result(result)
    return 0 if result.status == JobStatus.SUCCEEDED else 3


if __name__ == "__main__":
    raise SystemExit(main())
