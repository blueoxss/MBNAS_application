"""Owner worker process: ``python -m mbnas_core.owner_worker`` (console script ``mbnas-core-owner-worker``).

WP-2 contract section 1.7 / DD-A4: the default (``--owner``) mode leases only PROPOSE_GENERATION jobs (all of them:
``WorkerConfig(kinds=["PROPOSE_GENERATION"], generation_only=None)``, R6) and needs no GPU; BASELINE_TRAIN,
CANDIDATE_TRAIN, FINAL_RETRAIN, EVALUATE and BENCHMARK are claimed by remote runners over ``/core/v1/runner``
(:mod:`mbnas_core.runner_api`). ``--cpu`` keeps the former evaluation-free proposal queue (generation_only jobs).
The NVML occupancy wait and GPU sampling of WP-1 (``mbnas_runner.gpu``) are gone from the core (DD-A27 expired):
:meth:`Worker.wait_gpu_clear` returns immediately and the workers row carries no device samples. The single-host DB
loop itself is unchanged::

    -> reclaim expired leases (bill the lost attempt as failed cost, requeue or fail; the core sweeper does the same)
    -> acquire_lease (atomic; a job is never leased twice) -> start attempt
    -> spawn child [worker_python, -m, mbnas_core.owner_worker.child, --job-id, id, --db-path, db] (argv list, no
       shell) with CUDA_VISIBLE_DEVICES set as before, the non-secret settings as MBNAS_RUNNER_* / MBNAS_CORE_*
       variables (:func:`child_settings_env`), without any API token/signing/ticket key or dotenv path
       (:data:`SECRET_ENV`), PDEATHSIG=SIGKILL, and an inherited GPU-lock descriptor when one is open
    -> heartbeat thread renews the lease and the workers row every heartbeat_s
    -> read the child's JSONL: progress (throttled <= 1/s into candidate.updated + worker.updated),
       checkpoint (checkpoints row, path must be inside the run dir), log, result
    -> watch the run: PAUSE_REQUESTED writes the stop request (the child stops at its next epoch boundary),
       CANCEL_REQUESTED terminates the child (SIGTERM, SIGKILL after the grace period)
    -> finalize in one transaction: :func:`mbnas_core.runner_api.settle.settle_job` (job state, cost ledger row,
       orchestration step, run settlement) - the same function the remote result endpoint uses (R5).

A child that exits without a result is a retryable failure (``CHILD_EXIT_NO_RESULT``); a pause at a safe
boundary requeues the job without consuming a retry; a lost lease discards the result (another worker owns it).
A ``BLOCKED`` result (e.g. ``TARGET_BLOCKED``: the target device is unavailable) is final and never retried; the
``jobs`` row is FAILED with the BLOCKED result in ``error_json``. After every finished job the run's full-state
retention policy is applied to the job's candidate in its own transaction (``orchestration.retention``).

DD-A60 (2): the worker's own files live on the core host (:mod:`mbnas_core.owner_worker.paths`): the stop file is
``<MBNAS_CORE_OUTPUT_ROOT>/<run>/control/<job>.stop`` (default ``MBNAS_HOME/core/runs/<run>/control``), and the
per-attempt stderr log moves next to it when the run's own ``console.paths.output_root`` directory is not usable on this
host (a split deployment's customer-only output root, which the core host must never try to create). An exception
while the worker handles one leased job (a directory that cannot be created, a child that cannot be spawned, ...) fails
THAT job with reason ``WORKER_IO_ERROR`` (``OSError``) or ``WORKER_JOB_ERROR`` (:meth:`Worker.job_error`; never
retried, traceback in the worker log, reason in the run log) and the worker keeps running. The entry point
(:func:`main`) logs to stderr - the worker's existing log destination - through :func:`configure_logging`, which
installs :func:`mbnas_contracts.util.log_redact.install_log_redaction` on that handler like the core and gateway apps
do for their uvicorn records (C05 D10).
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import logging
import math
import os
import sys
import queue
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mbnas_core.artifacts.registry import run_dir
from mbnas_core.config import ENV_FILE_VAR, CoreSettings
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.events.bus import emit
from mbnas_core.orchestration import engine, retention
from mbnas_core.orchestration.views import worker_payload
from mbnas_contracts.schemas.jobs import JobResult, JobStatus, ProgressMessage
from mbnas_core.scheduling import costs, jobs
from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.log_redact import install_log_redaction
from mbnas_contracts.util.paths import is_within
from mbnas_contracts.util.time import iso_after, utcnow_iso
from mbnas_contracts.jobs_protocol import PAUSE_REASONS, STOP_ENV  # noqa: F401 - re-exported for callers
from mbnas_core.owner_worker.paths import core_run_dir, usable_here
from mbnas_core.runner_api.settle import RETRYABLE_REASONS, inline_timings as _inline_timings, settle_job  # noqa: F401

# never inherited by a child: this host's secrets, the other hosts' secrets (a dev stack may share one environment)
# and the pre-split names; every MBNAS_*_ENV_FILE variable is dropped as well (:func:`_inherited`)
SECRET_ENV = ("MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY", "MBNAS_GW_API_TOKEN", "MBNAS_GW_CORE_TOKEN",
              "MBNAS_GW_ARTIFACT_SIGNING_KEY", "MBNAS_RUNNER_TOKEN", "MBNAS_API_TOKEN", "MBNAS_ARTIFACT_SIGNING_KEY",
              "MBNAS_CORE_TICKET_KEY")
EVENT_INTERVAL_S = 1.0
WORKER_ROW_RETENTION_S = 24 * 3600  # worker rows older than a day are deleted at worker startup
LOCK_FD_ENV = "MBNAS_GPU_LOCK_FD"  # descriptor of a held GPU lock (runner gpu_guard), passed on when open
OWNER_KINDS = ["PROPOSE_GENERATION"]  # WP-2 section 1.7: the owner worker's lease filter
_PR_SET_PDEATHSIG = 1
WORKER_IO_ERROR = "WORKER_IO_ERROR"  # DD-A60 (2): an OSError on this host while handling the job (e.g. a directory)
WORKER_JOB_ERROR = "WORKER_JOB_ERROR"  # DD-A60 (2): any other exception of the worker around one job
LOG_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s"
logger = logging.getLogger("mbnas_core.owner_worker")


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """StreamHandler on the *current* ``sys.stderr`` (looked up per record, like logging's last-resort handler), so a
    replaced stderr never leaves the handler writing to a closed stream."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def configure_logging() -> logging.Logger:
    """Entry-point logging of the owner worker (C05 D10): one handler on the root logger writing UTC-stamped lines to
    stderr (the worker's log destination under start.sh / systemd / docker), then
    :func:`~mbnas_contracts.util.log_redact.install_log_redaction` on the root logger and that handler, so no worker
    line carries a URL/header secret (``token=``, ``access_token=``, ``sig=``, ``exp=``, ``Authorization``) - the same
    filter the core and gateway apps install for their uvicorn records. Idempotent; returns the worker's logger."""
    root = logging.getLogger()
    if not any(isinstance(h, _StderrHandler) for h in root.handlers):
        handler = _StderrHandler()
        formatter = logging.Formatter(LOG_FORMAT, "%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    install_log_redaction(("", logger.name))
    return logger


def _traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()


def _stop_child(proc: subprocess.Popen[str], grace_s: float) -> None:
    """Terminate a still running job child (SIGTERM, SIGKILL after ``grace_s``); never raises."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=max(grace_s, 0.1))
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError:
        pass


def child_settings_env(settings: CoreSettings) -> dict[str, str]:
    """The worker's effective non-secret settings for the job child (children never read a dotenv file; WP-1 contract
    section 5): ``MBNAS_RUNNER_*`` for the runner handlers (``RunnerSettings``; work root = the core output root, native
    pickle resume enabled on the owner host, DD-A20) and ``MBNAS_CORE_*`` for the child's own core side (DB loader,
    baseline ArchSpec, PROPOSE_GENERATION)."""
    output_roots, dataset_roots = json.dumps(settings.output_roots), json.dumps(settings.dataset_roots)
    return {"MBNAS_RUNNER_WORK_ROOT": settings.output_root, "MBNAS_RUNNER_OUTPUT_ROOTS": output_roots,
            "MBNAS_RUNNER_DATASET_ROOTS": dataset_roots, "MBNAS_RUNNER_WEIGHTS_DIR": settings.weights_dir,
            "MBNAS_RUNNER_LOG_DIR": str(settings.log_dir), "MBNAS_RUNNER_ALLOW_PICKLE_RESUME": "1",
            "MBNAS_CORE_DB_PATH": settings.db_path, "MBNAS_CORE_OUTPUT_ROOT": settings.output_root,
            "MBNAS_CORE_OUTPUT_ROOTS": output_roots, "MBNAS_CORE_DATASET_ROOTS": dataset_roots,
            "MBNAS_CORE_WEIGHTS_DIR": settings.weights_dir,
            "MBNAS_CORE_MODEL_YAML_ROOTS": json.dumps(settings.model_yaml_roots),
            "MBNAS_CORE_LOG_DIR": str(settings.log_dir), "MBNAS_CORE_WORKER_PYTHON": settings.worker_python}


def _inherited(key: str) -> bool:
    """True when the child may inherit environment variable ``key`` (no secret, no dotenv path)."""
    return key not in SECRET_ENV and not (key.startswith("MBNAS_") and key.endswith("_ENV_FILE"))


def _normal_sha(value: Any) -> str:
    """Lower-case hex digest of a reported sha256 (an optional ``sha256:`` prefix is tolerated); "" when absent."""
    text = str(value or "").strip().lower()
    return text[7:] if text.startswith("sha256:") else text


def _die_with_parent() -> None:  # runs in the child between fork and exec
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    except OSError:
        pass


GENERATION_ONLY_AUTO = "auto"


@dataclass
class WorkerConfig:
    gpu_index: int = 0
    cpu: bool = False  # CPU-only, evaluation-free proposal queue; never leases a training/evaluation job
    poll_s: float = 1.0
    kill_grace_s: float = 10.0
    max_jobs: int | None = None
    once: bool = False
    kinds: list[str] | None = None
    gpu_clear_timeout_s: float = 15.0  # bounded wait for foreign NVML compute pids before leasing / spawning
    # lease filter on params.generation_only, separated from ``cpu`` (R6): True = only generation_only jobs,
    # False = only the others, None = no filter; "auto" (default) keeps the original coupling (True for --cpu).
    generation_only: bool | None | Literal["auto"] = GENERATION_ONLY_AUTO

    def lease_generation_only(self) -> bool | None:
        """The ``generation_only`` filter passed to ``repo.acquire_lease``."""
        if self.generation_only == GENERATION_ONLY_AUTO:
            return True if self.cpu else None
        return self.generation_only  # type: ignore[return-value]


@dataclass
class _Live:
    """State shared between the monitor loop and the heartbeat thread."""

    started_at: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    progress_version: int = 0
    lease_expires_at: str | None = None
    lease_lost: bool = False
    pid: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Worker:
    """See module docstring."""

    def __init__(self, settings: CoreSettings, cfg: WorkerConfig | None = None, *, worker_id: str | None = None) -> None:
        self.settings = settings
        self.cfg = cfg or WorkerConfig()
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.conn = connect(settings)
        # no NVML in the core (WP-2): a non-cpu worker (tests with stub handlers) reports an unverified device label
        self.device = ({"id": "cpu", "uuid": "cpu", "name": "CPU structure generation"} if self.cfg.cpu else
                       {"id": f"{socket.gethostname()}-gpu-{self.cfg.gpu_index}", "uuid": f"gpu-index-{self.cfg.gpu_index}",
                        "name": "unverified device (owner worker has no NVML)"})
        self.stopping = False
        self.jobs_done = 0
        self._idle_beat_at: float | None = None
        self._child_pid: int | None = None

    # -- device occupancy -------------------------------------------------------------------------------------
    def wait_gpu_clear(self, job: dict[str, Any] | None = None) -> dict[str, Any]:
        """No-op since WP-2: the owner worker runs CPU proposal jobs only; the NVML occupancy wait (S7-F1) moved to
        the runner daemon together with the GPU kinds."""
        return {"cleared": True, "waited_s": 0.0, "foreign": [], "nvml": False}

    # -- worker row ------------------------------------------------------------------------------------------
    def _row(self, status: str, job: dict[str, Any] | None, live: _Live | None) -> dict[str, Any]:
        sample = {"utilization_percent": None, "vram_mb": None, "power_w": None, "sm_clock_mhz": None}
        payload: dict[str, Any] = {"id": self.id, "status": status, "device": self.device["id"],
                                   "device_uuid": self.device["uuid"], "device_name": self.device["name"],
                                   "heartbeat_at": utcnow_iso(), **sample, "pid": os.getpid()}
        if job is not None:
            payload.update(job_id=job["id"], run_id=job["run_id"], candidate_id=job.get("candidate_id"),
                           rung=job.get("rung"), attempt=job.get("attempt"))
        if live is not None:
            with live.lock:
                prog = dict(live.progress)
                payload.update(lease_expires_at=live.lease_expires_at, child_pid=live.pid)
            for key in ("epoch", "epoch_target_total", "learning_rate", "global_step"):
                if prog.get(key) is not None:
                    payload[key] = prog[key]
        return payload

    def publish(self, conn: sqlite3.Connection, status: str, job: dict[str, Any] | None, live: _Live | None,
                *, event_run: str | None = None) -> None:
        """Upsert the workers row and, when a run is given, emit worker.updated (inside the caller's tx)."""
        payload = self._row(status, job, live)
        row = repo.upsert_worker(conn, id=self.id, status=status, device=self.device["id"],
                                 device_uuid=self.device["uuid"], device_name=self.device["name"],
                                 job_id=payload.get("job_id"), run_id=payload.get("run_id"),
                                 candidate_id=payload.get("candidate_id"), heartbeat_at=payload["heartbeat_at"],
                                 row=payload)
        if event_run:
            emit(conn, event_run, "worker.updated", {k: v for k, v in worker_payload(row).items() if v is not None})

    # -- loop ------------------------------------------------------------------------------------------------
    def run(self) -> int:
        """Main loop until SIGTERM/SIGINT, ``--once`` or ``--max-jobs``."""
        self.prune_worker_rows()
        self.idle_heartbeat(force=True)
        try:
            while not self.stopping:
                worked = self.tick()
                if self.cfg.once or (self.cfg.max_jobs is not None and self.jobs_done >= self.cfg.max_jobs):
                    break
                if not worked:
                    self.idle_heartbeat()
                    time.sleep(self.cfg.poll_s)
        finally:
            with tx(self.conn):
                self.publish(self.conn, "offline", None, None)
            self.conn.close()
        return 0

    def prune_worker_rows(self, now: str | None = None) -> int:
        """Age out worker rows (other workers) whose heartbeat is older than ``WORKER_ROW_RETENTION_S`` (S7-F4):
        offline workers and workers killed without writing ``offline`` no longer pile up in ``workers``."""
        with tx(self.conn):
            return repo.prune_workers(self.conn, iso_after(now or utcnow_iso(), -WORKER_ROW_RETENTION_S), keep=self.id)

    def idle_heartbeat(self, *, force: bool = False) -> bool:
        """Refresh the ``idle`` workers row at most every ``heartbeat_s`` so /health keeps counting an idle worker
        as live (a busy worker is refreshed by its heartbeat thread). Returns True when a row was written."""
        now = time.monotonic()
        if not force and self._idle_beat_at is not None and now - self._idle_beat_at < self.settings.heartbeat_s:
            return False
        with tx(self.conn):
            self.publish(self.conn, "idle", None, None)
        self._idle_beat_at = now
        return True

    def tick(self) -> bool:
        """Wait for the device, reclaim expired leases, then lease and execute at most one job; True when a job ran.

        DD-A60 (2): an exception while the leased job is handled fails that job (:meth:`job_error`) and the worker
        goes on with the next tick instead of exiting."""
        self.wait_gpu_clear()
        # reclaim right before leasing: acquire_lease also takes expired leases, which must be billed by reclaim first
        self.reclaim()
        job = repo.acquire_lease(self.conn, self.id, utcnow_iso(), self.settings.lease_ttl_s, kinds=self.cfg.kinds,
                                 generation_only=self.cfg.lease_generation_only())
        if job is None:
            return False
        try:
            self.execute(job)
        except Exception as exc:  # noqa: BLE001 - DD-A60 (2): one job's failure never stops the worker
            self.job_error(job, exc)
        self.jobs_done += 1
        return True

    def job_error(self, job: dict[str, Any], exc: BaseException) -> JobResult | None:
        """Record ``exc`` (raised while this worker handled ``job``) as the job's final FAILED result: reason
        ``WORKER_IO_ERROR`` for an ``OSError`` on this host (e.g. a directory it cannot create), ``WORKER_JOB_ERROR``
        otherwise; never retried (the same host would fail the same way). The message names the exception type and
        errno only (no host path); the traceback goes to the worker log, the reason code to the run log. A job this
        worker does not own any more (lease lost, already settled) is left alone. Returns the recorded result, or None
        when nothing was recorded (then the lease expires and reclaim / the core sweeper takes over)."""
        code = WORKER_IO_ERROR if isinstance(exc, OSError) else WORKER_JOB_ERROR
        detail = type(exc).__name__
        if isinstance(exc, OSError) and exc.errno:
            detail += f" ({errno.errorcode.get(exc.errno, exc.errno)})"
        message = f"the owner worker could not run this job on the core host: {detail}; see the owner worker log"
        logger.error("job %s (%s, run %s) failed in the owner worker [%s]: %s", job.get("id"), job.get("kind"),
                     job.get("run_id"), code, _traceback(exc))
        try:
            with tx(self.conn):
                current = repo.get_job(self.conn, job["id"])
                held = current is not None and current["lease_owner"] == self.id
                if not held or current["status"] not in jobs.ACTIVE_STATUSES:
                    logger.warning("job %s is no longer held by %s; its failure is not recorded", job.get("id"),
                                   self.id)
                    return None
                result = JobResult(job_id=job["id"], status=JobStatus.FAILED,
                                   attempt=max(1, int(current["attempt"] or 1)), reason_code=code, message=message,
                                   outputs={"retryable": False})
                result = settle_job(self.conn, current, result, None, False, self.stopping, current.get("started_at"),
                                    utcnow_iso(), self.id, self.device["uuid"])
                engine.log(self.conn, job["run_id"], f"job {job['id']} failed in the owner worker ({code})",
                           level="error", job_id=job["id"], reason_code=code)
                self.publish(self.conn, "idle", None, None, event_run=job["run_id"])
        except Exception as again:  # noqa: BLE001 - logged; the lease then expires (reclaim / sweeper requeue it)
            logger.error("could not record the failure of job %s: %s", job.get("id"), _traceback(again))
            return None
        self.apply_retention(job)
        return result

    def reclaim(self, now: str | None = None) -> list[str]:
        """Requeue (or fail) jobs whose lease expired at ``now``; bill the lost attempt as failed cost from its start
        to its last evidence of life (lease heartbeat or child progress, whichever is later)."""
        with tx(self.conn):
            pairs = jobs.reclaim(self.conn, now)
            for before, after in pairs:
                if before["status"] == "RUNNING":  # a LEASED-only attempt never started work: nothing to bill
                    costs.record_attempt(self.conn, before, outcome="lost", started_at=before.get("started_at"),
                                         finished_at=before.get("heartbeat_at") or utcnow_iso(),
                                         reason_code="LEASE_EXPIRED")
                if after["status"] == "FAILED":
                    result = JobResult(job_id=after["id"], status=JobStatus.FAILED, attempt=max(1, after["attempt"]),
                                       reason_code="LEASE_EXPIRED_MAX_ATTEMPTS", message="lease expired, no attempts left")
                    engine.job_finished(self.conn, after, result)
                else:
                    engine.job_requeued(self.conn, after, paused=False, reason_code="LEASE_EXPIRED")
                    engine.settle_run(self.conn, after["run_id"])
                costs.refresh_run_costs(self.conn, after["run_id"])
        for _, after in pairs:
            if after["status"] == "FAILED":  # the candidate may now be terminal (TRAIN_FAILED): retention applies
                self.apply_retention(after)
        return [after["id"] for _, after in pairs]

    # -- one job ---------------------------------------------------------------------------------------------
    def _paths(self, job: dict[str, Any]) -> tuple[Path, Path, Path]:
        """``(run dir, stop file, attempt stderr log)``. DD-A60 (2): the stop file always lives in the core-local
        ``core_run_dir(settings, run)/control``; the attempt log stays in ``<run dir>/logs`` when the run's directory is
        usable on this host (owner all-in-one: unchanged) and goes to ``core_run_dir(settings, run)/logs`` otherwise.
        ``base`` remains the run's own directory (checkpoint reports are checked against it)."""
        run = repo.get_run(self.conn, job["run_id"])
        base = run_dir(run)
        local = core_run_dir(self.settings, run["id"])
        stop = local / "control" / f"{job['id']}.stop"
        logs = (base if usable_here(base, self.settings) else local) / "logs"
        attempt_log = logs / f"{job['id']}.attempt{job['attempt']}.stderr.log"
        for p in (stop, attempt_log):
            p.parent.mkdir(parents=True, exist_ok=True)
        return base, stop, attempt_log

    def _spawn(self, job: dict[str, Any], stop: Path, log: Path) -> subprocess.Popen[str]:
        env = {k: v for k, v in os.environ.items() if _inherited(k)}
        env.update(child_settings_env(self.settings))
        env.update({"CUDA_VISIBLE_DEVICES": ("" if self.cfg.cpu or ((job.get("spec") or {}).get("params") or {}).get("generation_only")
                                                 else str(self.cfg.gpu_index)), "PYTHONUNBUFFERED": "1", STOP_ENV: str(stop),
                    "MBNAS_WORKER_ID": str(self.id)})  # recorded in generation audit (DD-6); not a secret
        argv = [self.settings.worker_python, "-m", "mbnas_core.owner_worker.child", "--job-id", job["id"],
                "--db-path", self.settings.db_path]
        with open(log, "a", encoding="utf-8") as stderr:  # the child keeps its own descriptor
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, env=env,
                                    text=True, bufsize=1, preexec_fn=_die_with_parent, close_fds=True,
                                    pass_fds=_lock_fds())

    def execute(self, job: dict[str, Any]) -> None:
        """Run one leased job to a final state (see module docstring); an exception propagates to :meth:`tick`
        (DD-A60 (2)) after the job's child, if any, was stopped."""
        base, stop, attempt_log = self._paths(job)
        stop.unlink(missing_ok=True)
        self.wait_gpu_clear(job)  # before the attempt starts: waiting for the device is not billed GPU time
        with tx(self.conn):
            if not jobs.start_attempt(self.conn, job["id"], self.id):
                return
            job = repo.get_job(self.conn, job["id"])
            engine.job_started(self.conn, job, self.id)
            self.publish(self.conn, "busy", job, None, event_run=job["run_id"])
        live = _Live(started_at=job.get("started_at"), lease_expires_at=job.get("lease_expires_at"))
        proc = self._spawn(job, stop, attempt_log)
        live.pid = self._child_pid = proc.pid
        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_pump, args=(proc, lines), daemon=True)
        reader.start()
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, live, done), daemon=True)
        beat.start()
        try:
            result, cancelled = self._monitor(job, proc, lines, live, base, stop)
        except BaseException:
            _stop_child(proc, self.cfg.kill_grace_s)  # DD-A60 (2): no child outlives a failed attempt
            raise
        finally:
            done.set()
            beat.join(timeout=5)
            proc.wait()
            self._child_pid = None
        self._finalize(job, result, proc.returncode, cancelled, live)
        stop.unlink(missing_ok=True)

    def _heartbeat(self, job: dict[str, Any], live: _Live, done: threading.Event) -> None:
        conn = connect(self.settings, init=False)
        try:
            while not done.wait(self.settings.heartbeat_s):
                now = utcnow_iso()
                if not repo.renew_lease(conn, job["id"], self.id, now, self.settings.lease_ttl_s):
                    with live.lock:
                        live.lease_lost = True
                    return
                exp = repo.get_job(conn, job["id"])["lease_expires_at"]
                with live.lock:
                    live.lease_expires_at = exp
                with tx(conn):
                    self.publish(conn, "busy", job, live)
        finally:
            conn.close()

    def _monitor(self, job: dict[str, Any], proc: subprocess.Popen[str], lines: "queue.Queue[str | None]",
                 live: _Live, base: Path, stop: Path) -> tuple[JobResult | None, bool]:
        result: JobResult | None = None
        cancelled = False
        kill_at: float | None = None
        last_event = last_check = 0.0
        sent_version = 0
        eof = False
        while not eof:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                eof = True
            elif line:
                result = self._handle_line(job, line, live, base) or result
            now = time.monotonic()
            with live.lock:
                lost = live.lease_lost
            if lost and kill_at is None:
                proc.terminate()
                kill_at = now + self.cfg.kill_grace_s
            if now - last_check >= 0.5 and kill_at is None:
                last_check = now
                status = repo.get_run(self.conn, job["run_id"])["status"]
                if status == "CANCEL_REQUESTED" or self.stopping:
                    stop.write_text(json.dumps({"action": "cancel", "requested_at": utcnow_iso()}))
                    proc.terminate()
                    cancelled = status == "CANCEL_REQUESTED"
                    kill_at = now + self.cfg.kill_grace_s
                elif status == "PAUSE_REQUESTED" and not stop.exists():
                    stop.write_text(json.dumps({"action": "pause", "requested_at": utcnow_iso()}))
                elif status != "PAUSE_REQUESTED" and stop.exists():
                    stop.unlink(missing_ok=True)  # pause withdrawn
            if kill_at is not None and now >= kill_at and proc.poll() is None:
                proc.kill()
            if now - last_event >= EVENT_INTERVAL_S and live.progress_version != sent_version:
                last_event, sent_version = now, live.progress_version
                self._progress_event(job, live)
        return result, cancelled

    def _handle_line(self, job: dict[str, Any], line: str, live: _Live, base: Path) -> JobResult | None:
        try:
            msg = ProgressMessage.parse_line(line)
        except ValueError:
            return None  # stray non-protocol output; the child redirects library prints to stderr
        if msg.job_id != job["id"]:
            return None
        if msg.type == "progress":
            if msg.payload.get("generation_summary") is not None:
                with tx(self.conn):
                    current = repo.get_job(self.conn, job["id"])
                    if current["lease_owner"] == self.id and current["status"] == "RUNNING":
                        engine.generation_progress(self.conn, current, msg.payload)
                        repo.touch_job(self.conn, job["id"], self.id, utcnow_iso())
                return None
            with live.lock:
                live.progress.update({k: v for k, v in msg.model_dump().items() if k in (
                    "epoch", "epoch_target_total", "global_step", "samples_seen", "learning_rate") and v is not None})
                live.progress.update(_numeric_training_progress(msg.payload))
                live.progress_version += 1
        elif msg.type == "checkpoint":
            self._checkpoint(job, msg.payload, base)
        elif msg.type == "result":
            try:
                return JobResult.model_validate(msg.payload)
            except ValueError:
                return None
        return None

    def _checkpoint(self, job: dict[str, Any], p: dict[str, Any], base: Path) -> None:
        path = os.path.realpath(str(p.get("path", "")))
        ok = (is_within(path, os.path.realpath(base)) and os.path.isfile(path)
              and os.path.getsize(path) == p.get("bytes") and p.get("kind") in
              ("training_full", "eval_ema", "weights_only", "best") and job.get("candidate_id"))
        problem = "outside run dir, missing or size mismatch"
        actual = reported = ""
        if ok:
            # DD-8 / G11: never trust the child's reported digest; recompute it from the bytes on disk (streaming).
            reported = _normal_sha(p.get("sha256"))
            try:
                actual = sha256_file(path)
            except OSError:
                ok, problem = False, "unreadable file"
            else:
                if not reported or actual != reported:
                    ok, problem = False, "sha256 mismatch (reported digest does not match the file)"
        with tx(self.conn):
            if not ok:
                engine.log(self.conn, job["run_id"], f"checkpoint report rejected ({problem})", level="error",
                           job_id=job["id"], path=path, reason_code="CHECKPOINT_REPORT_REJECTED",
                           reported_sha256=reported or None, computed_sha256=actual or None)
                return
            repo.insert_checkpoint(self.conn, id=f"ck-{os.urandom(8).hex()}", run_id=job["run_id"],
                                   candidate_id=job["candidate_id"], epoch=int(p.get("epoch") or 0), kind=p["kind"],
                                   path=path, sha256=actual, bytes=int(p["bytes"]),
                                   rung=p.get("rung") or job.get("rung"), hashes=p.get("hashes") or {})

    def _progress_event(self, job: dict[str, Any], live: _Live) -> None:
        with live.lock:
            prog = dict(live.progress)
        with tx(self.conn):
            repo.touch_job(self.conn, job["id"], self.id, utcnow_iso())  # liveness for lost-work billing
            engine.job_progress(self.conn, repo.get_job(self.conn, job["id"]), prog)
            self.publish(self.conn, "busy", job, live, event_run=job["run_id"])

    def _finalize(self, job: dict[str, Any], result: JobResult | None, exit_code: int | None, cancelled: bool,
                  live: _Live) -> None:
        finished = utcnow_iso()
        with tx(self.conn):
            current = repo.get_job(self.conn, job["id"])
            started = live.started_at  # this attempt's start (the row may already belong to another worker)
            owned = current["lease_owner"] == self.id and current["status"] in jobs.ACTIVE_STATUSES
            if not owned:
                # one physical row per (job, attempt): complements a reclaimer's failed row (S7-F2)
                costs.record_lost_attempt(self.conn, current | {"attempt": job["attempt"]}, started_at=started,
                                          finished_at=finished, reason_code="LEASE_LOST",
                                          device_uuid=self.device["uuid"])
                engine.log(self.conn, job["run_id"], f"lease of {job['id']} lost; result discarded", level="warning",
                           job_id=job["id"])
            else:
                self._settle_job(current, result, exit_code, cancelled, started, finished)
            costs.refresh_run_costs(self.conn, job["run_id"])
            self.publish(self.conn, "idle", None, None, event_run=job["run_id"])
        self.apply_retention(job)

    def apply_retention(self, job: dict[str, Any]) -> dict[str, Any] | None:
        """Run the retention policy for the job's candidate after the job's state committed (own transaction)."""
        if not job.get("candidate_id"):
            return None
        try:
            with tx(self.conn):
                run = repo.get_run(self.conn, job["run_id"])
                report = retention.apply_for_candidate(self.conn, run, job["candidate_id"],
                                                       default_policy=self.settings.full_state_retention)
                if report["pruned"]:
                    engine.log(self.conn, job["run_id"], f"retention {report['policy']}: pruned "
                               f"{len(report['pruned'])} full-state payload(s) of {job['candidate_id']}",
                               candidate_id=job["candidate_id"], policy=report["policy"],
                               bytes_freed=report["bytes_freed"])
            return report
        except Exception as exc:  # noqa: BLE001 - housekeeping must never fail a finished job; recorded instead
            with tx(self.conn):
                engine.log(self.conn, job["run_id"], f"retention failed for {job['candidate_id']}: "
                           f"{type(exc).__name__}: {exc}"[:500], level="error", candidate_id=job["candidate_id"])
            return None

    def _settle_job(self, job: dict[str, Any], result: JobResult | None, exit_code: int | None, cancelled: bool,
                    started: str | None, finished: str) -> None:
        settle_job(self.conn, job, result, exit_code, cancelled, self.stopping, started, finished, self.id,
                   self.device["uuid"])


def _lock_fds() -> tuple[int, ...]:
    """The inherited GPU-lock descriptor (``MBNAS_GPU_LOCK_FD``) when it is open in this process, else ()."""
    raw = os.environ.get(LOCK_FD_ENV, "")
    try:
        fd = int(raw)
        os.fstat(fd)
    except (ValueError, OSError):
        return ()
    return (fd,)


def _pump(proc: subprocess.Popen[str], out: "queue.Queue[str | None]") -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        out.put(line.strip())
    out.put(None)


def owner_config(*, cpu_only: bool = False, **kw: Any) -> WorkerConfig:
    """WorkerConfig of the CLI: owner mode (default) = ``kinds=[PROPOSE_GENERATION], generation_only=None``;
    ``cpu_only`` = the former ``--cpu`` queue (generation_only PROPOSE_GENERATION jobs). Both run without a GPU."""
    return WorkerConfig(cpu=True, kinds=list(OWNER_KINDS), generation_only=True if cpu_only else None, **kw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MBNAS owner worker (PROPOSE_GENERATION jobs on the core host; GPU "
                                                 "kinds are claimed by remote runners)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--owner", action="store_true",
                      help="default: lease every PROPOSE_GENERATION job (kinds=[PROPOSE_GENERATION], no "
                           "generation_only filter); requires no GPU")
    mode.add_argument("--cpu", action="store_true", help="lease only evaluation-free generation jobs "
                                                         "(params.generation_only); requires no GPU")
    parser.add_argument("--kinds", default=None,
                        help="comma-separated lease kinds overriding the owner filter; with the real handlers only "
                             "PROPOSE_GENERATION runs (other kinds fail KIND_NOT_ALLOWED), so this is for stub-handler "
                             "tests/smokes ($MBNAS_JOB_HANDLERS_MODULE)")
    parser.add_argument("--gpu-index", type=int, default=int(os.environ.get("MBNAS_WORKER_GPU_INDEX", "0")),
                        help="CUDA_VISIBLE_DEVICES of non-generation stub children (only with --kinds)")
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--kill-grace-s", type=float, default=10.0)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="run at most one job, then exit")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--gpu-clear-timeout-s", type=float,
                        default=float(os.environ.get("MBNAS_WORKER_GPU_CLEAR_TIMEOUT_S", "15")),
                        help="unused since WP-2 (the NVML occupancy wait moved to the runner daemon)")
    parser.add_argument("--env-file", default=None, help="dotenv file (else environment / MBNAS_CORE_ENV_FILE)")
    args = parser.parse_args(argv)
    configure_logging()  # C05 D10: redacted stderr lines, like the core and gateway apps
    if args.env_file:
        # like mbnas-core-api: every CoreSettings.load() of THIS process - e.g. the engine's core-local journal lookup
        # (DD-A62 (c)) - then reads the same file; children never inherit it (_inherited drops MBNAS_*_ENV_FILE)
        os.environ[ENV_FILE_VAR] = args.env_file
    settings = CoreSettings.load()
    cfg = owner_config(cpu_only=args.cpu, gpu_index=args.gpu_index, poll_s=args.poll_s,
                       kill_grace_s=args.kill_grace_s, max_jobs=args.max_jobs, once=args.once,
                       gpu_clear_timeout_s=args.gpu_clear_timeout_s)
    if args.kinds:
        cfg.kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        cfg.cpu = False  # CUDA_VISIBLE_DEVICES for non-generation children follows --gpu-index as before
    worker = Worker(settings, cfg, worker_id=args.worker_id)

    def _stop(signum: int, _frame: Any) -> None:
        worker.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("owner worker %s started (kinds=%s, generation_only=%s, core-local run root %s)", worker.id,
                cfg.kinds, cfg.lease_generation_only(), settings.output_root)
    code = worker.run()
    logger.info("owner worker %s stopped after %d job(s)", worker.id, worker.jobs_done)
    return code


def _numeric_training_progress(payload: dict[str, Any]) -> dict[str, Any]:
    """Forward measured scalar loss/speed values only; never serialize arbitrary backend state or errors.

    Ultralytics emits native_metrics; ETRI hybrid emits train_loss. Missing measurements remain absent. Metric
    dictionaries are one level deep and contain finite numbers only (bool/string/NaN/Inf/nested values drop).
    """
    def numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    out = {k: payload[k] for k in ("loss", "train_loss", "images_per_second", "samples_per_second", "throughput",
                                   "epoch_seconds", "batch_seconds") if numeric(payload.get(k))}
    for key in ("native_metrics", "losses"):
        values = payload.get(key)
        if isinstance(values, dict):
            safe = {k: v for k, v in values.items() if isinstance(k, str) and len(k) <= 128 and "\n" not in k
                    and "\r" not in k and numeric(v)}
            if safe:
                out[key] = safe
    return out
