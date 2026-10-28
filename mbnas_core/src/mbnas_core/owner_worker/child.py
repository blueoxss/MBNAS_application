"""Per-job child process of the owner worker: ``python -m mbnas_core.owner_worker.child --job-id <id> --db-path <db>``.

The child reads its JobSpec, run and candidate context through a **read-only** SQLite connection (it never
writes the DB), builds the handler parameters documented in :mod:`.contract` (generic keys for the stub override,
the native parameter models for the real handlers), runs the handler for
the job kind and reports through JSONL ``ProgressMessage`` lines on the original stdout. Everything else written
to fd 1 by libraries is redirected to stderr (the worker stores stderr in the per-attempt job log). The last line
is always one ``result`` message; the exit code is 0 for SUCCEEDED, 3 otherwise.

GPU job kinds import torchvision before the handler runs: ultralytics' NMS (native per-epoch validation, AP
evaluation, latency) uses ``torchvision.ops.nms`` only when torchvision is already imported and otherwise falls back to
a Python loop; a fresh child would not have it imported.

Baseline ArchSpec (DD-A16): ``baseline_arch``/``native_params``/``load_context`` live in
:mod:`mbnas_core.runner_api.envelope` (moved there in WP-2 so the claim envelope builder and this child share one
implementation; re-exported here). WP-2 contract section 1.7: with the real handlers this child runs only
PROPOSE_GENERATION (:data:`OWNER_KINDS`; any other kind fails ``KIND_NOT_ALLOWED``); training, evaluation and benchmark
jobs are claimed by remote runners over ``/core/v1/runner``. The stub override (``$MBNAS_JOB_HANDLERS_MODULE``, tests
and smokes) still runs every kind in-process. The core never imports ``mbnas_runner`` (DD-A25 rule iii).

Owner-host adjustments of a PROPOSE_GENERATION spec (:func:`owner_params`, applied by :func:`main` after
``load_context``): DD-A60 (2) the ``architecture_dir`` moves to the core-local run directory when the run's own
directory is not usable on this host, and DD-A60 (3) an evaluation-ON proposal takes its class count from the gateway's
dataset summary recorded with the run's validation instead of reading ``console.paths.dataset_yaml`` here.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import IO, Any, Callable

from mbnas_core.config import CoreSettings
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobResult, JobSpec, JobStatus
from mbnas_contracts.util.time import utcnow_iso
from mbnas_contracts.jobs_protocol import PAUSED_REASON, STOP_ENV, Emitter, JobFailure, JobPaused
from mbnas_core.db import repo
from mbnas_core.owner_worker.contract import handler_override, resolve_handler
from mbnas_core.owner_worker.paths import core_run_dir, usable_here
from mbnas_core.runner_api.envelope import (  # noqa: F401 - moved here from this module (WP-2, R7); re-exported
    MIXED_HOOK, TRAIN_PARAM_KEYS, _read_only, baseline_arch, latest_live_full_state, load_context, native_params, safe_name,
    search_arch,
)


def _protocol_stream() -> IO[str]:
    """Duplicate fd 1 for the protocol, then point fd 1 (and sys.stdout) at stderr."""
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


NMS_KINDS = frozenset({*(k.value for k in TRAINING_KINDS), JobKind.EVALUATE.value, JobKind.BENCHMARK.value})
OWNER_KINDS = frozenset({JobKind.PROPOSE_GENERATION.value})
"""Kinds this child executes with the real handlers (WP-2 contract section 1.7): GPU kinds run on remote runners."""


def gateway_dataset_summary(db_path: str, run_id: str) -> dict[str, Any] | None:
    """DD-A60 (3): the gateway's dataset summary recorded with the run's validation, as
    ``{"source": "gateway", "validation_id", "names", "nc"}``, when that validation used the gateway's host inputs
    (``capabilities.validation_host == "gateway"``, DD-A34/A37) and recorded the class names
    (``capabilities.dataset.names``); None otherwise (read-only DB access)."""
    conn = _read_only(db_path)
    try:
        run = repo.get_run(conn, run_id)
        vid = (run or {}).get("validation_id")
        val = repo.get_validation(conn, vid) if vid else None
    finally:
        conn.close()
    caps = (val or {}).get("capabilities")
    caps = caps if isinstance(caps, dict) else {}
    dataset = caps.get("dataset") if isinstance(caps.get("dataset"), dict) else {}
    names = dataset.get("names")
    if caps.get("validation_host") != "gateway" or not isinstance(names, dict) or not names:
        return None
    return {"source": "gateway", "validation_id": vid, "names": {str(k): str(v) for k, v in names.items()},
            "nc": len(names)}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def owner_params(spec: JobSpec, db_path: str, settings: Any = None,
                 note: Callable[[str], None] | None = None) -> JobSpec:
    """Owner-host adjustments of a PROPOSE_GENERATION spec; any other kind is returned unchanged.

    * DD-A60 (2) ``architecture_dir``: kept as ``<run dir>/architectures`` when that directory is usable on this host
      (inside the core output allowlist and creatable: owner all-in-one, unchanged); otherwise - a split deployment's
      customer-only ``console.paths.output_root`` - it becomes ``core_run_dir(settings, run)/architectures`` on the core
      host, which is never asked to create the customer path. The candidates' YAML text is stored in the database
      either way.
    * DD-A60 (3) ``nc`` (evaluation ON, when the job carries none): the class count of the gateway's dataset summary
      recorded with the run's validation (:func:`gateway_dataset_summary`; kept as ``params.dataset_summary``), so the
      proposal does not read ``console.paths.dataset_yaml`` on the core host. Where the gateway sent no dataset summary
      (the run was validated core-locally) the handler still reads the dataset YAML on this host: the core-local read
      is only the fallback. Limitation (as DD-A53 g for training): in a split deployment an evaluation-ON search must be
      validated through the gateway, otherwise that core-local read fails with DATASET_YAML_INVALID.

    ``note`` receives one line per adjustment (the child's stderr = the attempt log). A failure to adjust is noted and
    leaves the spec unchanged (the handler then reports its own error)."""
    if spec.kind != JobKind.PROPOSE_GENERATION:
        return spec
    note = note or (lambda _msg: None)
    params = dict(spec.params or {})
    try:
        if settings is None:
            settings = CoreSettings.load()
        target = params.get("architecture_dir")
        if isinstance(target, str) and target and not usable_here(target, settings):
            params["architecture_dir"] = str(core_run_dir(settings, spec.run_id) / "architectures")
            note(f"job {spec.job_id}: the run directory is not usable on the core host (customer-only output root); "
                 "architectures are written to the core-local run directory")
        protocol = params.get("protocol") if isinstance(params.get("protocol"), dict) else {}
        search = ((protocol.get("console") or {}).get("search") or {})
        if search.get("evaluation_enabled", True) is not False and not _positive_int(params.get("nc")):
            summary = gateway_dataset_summary(db_path, spec.run_id)
            if summary is not None:
                params["nc"], params["dataset_summary"] = summary["nc"], summary
                note(f"job {spec.job_id}: class count {summary['nc']} from the gateway dataset summary of validation "
                     f"{summary['validation_id']} (console.paths.dataset_yaml is not read on the core host)")
            else:
                note(f"job {spec.job_id}: no gateway dataset summary recorded for this run; the class count is read "
                     "from console.paths.dataset_yaml on the core host (fallback)")
    except Exception as exc:  # noqa: BLE001 - noted; the handler reports its own error for the unchanged spec
        note(f"job {spec.job_id}: owner parameter adjustment skipped ({type(exc).__name__}: {exc})")
        return spec
    return spec.model_copy(update={"params": params})


def run_job(spec: JobSpec, emit: Emitter) -> JobResult:
    """Run the handler and convert its outcome (or exception) into a JobResult."""
    started = utcnow_iso()
    base = {"job_id": spec.job_id, "attempt": spec.attempt, "started_at": started}
    try:
        if spec.kind.value not in OWNER_KINDS and handler_override() is None:
            raise JobFailure("KIND_NOT_ALLOWED", f"the owner worker child runs only {sorted(OWNER_KINDS)}; "
                                                 f"{spec.kind.value} jobs are claimed by remote runners")
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
    parser = argparse.ArgumentParser(description="MBNAS owner worker child: run one job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--db-path", default=None, help="defaults to MBNAS_CORE_DB_PATH / settings")
    args = parser.parse_args(argv)
    proto = _protocol_stream()
    stop_path = os.environ.get(STOP_ENV)
    emit = Emitter(args.job_id, proto, stop_path)
    db_path = args.db_path or CoreSettings.load().db_path
    try:
        spec = load_context(db_path, args.job_id, stop_path, native=handler_override() is None)
    except JobFailure as fail:
        emit.result(JobResult(job_id=args.job_id, status=JobStatus.FAILED, reason_code=fail.reason_code,
                              message=fail.message, finished_at=utcnow_iso()))
        return 3
    spec = owner_params(spec, db_path, note=lambda msg: print(msg, file=sys.stderr, flush=True))  # DD-A60 (2)/(3)
    if spec.kind.value in NMS_KINDS:
        from mbnas_model_runtime.evaluation.latency import ensure_torchvision

        ensure_torchvision()
    result = run_job(spec, emit)
    emit.result(result)
    return 0 if result.status == JobStatus.SUCCEEDED else 3


if __name__ == "__main__":
    raise SystemExit(main())
