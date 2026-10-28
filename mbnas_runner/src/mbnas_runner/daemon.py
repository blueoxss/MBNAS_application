"""Runner daemon: the owner worker's ``execute/_monitor/_handle_line/_heartbeat`` loop over HTTP.

Loop (one job at a time on one device)::

    register (runtime manifest) -> replay the journal
    -> wait (bounded) for foreign compute pids to leave the GPU -> POST /claim {kinds}
    -> journal envelope.json -> derive the child's JobSpec (mbnas_runner.envelope) -> spec.json,
       <run_dir>/protocol/resolved.json, <run_dir>/control/
    -> wait for the device again (unbilled) -> POST /start (started.json; the billed attempt starts here)
    -> spawn [worker_python, -B, -m, mbnas_runner.child, --spec-file, <journal>/spec.json] (argv list, no shell;
       no secrets, no dotenv path in its env; PDEATHSIG=SIGKILL; stop file in $MBNAS_STOP_REQUEST_FILE;
       CUDA_VISIBLE_DEVICES=<gpu_index> with CUDA_DEVICE_ORDER=PCI_BUS_ID, so the child's cuda:0 is the NVML device
       gpu_index this runner registered - CUDA and NVML enumerate in the same PCI bus order)
    -> heartbeat thread: POST /heartbeat every heartbeat_s renews the lease; control.action "pause"/"cancel" is
       materialised as <run_dir>/control/<job>.stop = {"action": ..., "requested_at": ...}; action null after a pause
       deletes the file (withdrawal); 409 LEASE_LOST stops the child
    -> child JSONL: progress merged and sent as one batch at most every progress_interval_s; checkpoint lines
       verified by this process (run_dir containment, size, kind, sha256 recompute) before POST /checkpoints, a
       rejected one becomes a CHECKPOINT_REPORT_REJECTED log message of the next batch; log/metric lines stay in the
       stderr log
    -> lease-expiry watchdog: with kill_on_lease_expiry (default on) a lease that ran out on the core's clock without a
       renewal stops the child
    -> result: training and EVALUATE results carry ``outputs.device_used`` (``cpu`` on a CPU-only runner;
       else the CUDA device the child ran on) and ``outputs.device_uuid`` (:meth:`RunnerDaemon._with_device`);
       evaluation artifacts (and the executed architecture.yaml / trainer plots of training kinds) are
       pre-registered with POST /artifacts and carry their artifact_id; result.json is frozen with payload_digest;
       POST /result -> response.json -> POST /ack -> acked.

Artifact transfer (``artifact_transfer=local``; :mod:`mbnas_runner.transfer`): right after the core accepted a
registration (E6 checkpoint, E7 batch) the daemon copies those files into the artifact store directory (hard link first,
byte copy otherwise, digest verified by re-reading, journalled under ``transfers/``) and before the commit it adds
``outputs.artifact_transfer = {mode, ok, transferred, failed}`` to the result. A transfer failure never fails or holds
back the job; failed items are retried on the heartbeat (background thread) and in the idle loop, bounded by
``artifact_transfer_max_tries``. With ``artifact_transfer=none`` (default) none of this happens.

Retention prune orders (runner protocol 1.1.0): a heartbeat answer's ``control.prune`` lists checkpoints
this runner registered that the run's retention policy deletes (never the latest full state, never one an evaluation
references - the core decides). They are queued for a background thread (:meth:`RunnerDaemon.submit_prunes`; a deletion
must never delay a lease renewal) and each is carried out by :func:`execute_prune_order` - only the named payload
``<work_root>/runs/<run_id>/<relpath>`` (plus the RF-DETR ``native_eNNNN.ckpt`` beside a ``full_eNNNN.pt``; the sidecar
stays), only when it is a regular file inside this runner's own work root (never a symlink, never the artifact store)
whose bytes hash to the ordered digest; a file that is gone or now holds other bytes is reported ``already_absent`` and
left alone; an order that fails these checks is refused (journalled and logged, nothing deleted, nothing reported). A
file whose transfer into the artifact store is still owed (journal item pending / failed and retryable) is not deleted
either: the order is deferred (``PRUNE_TRANSFER_PENDING``) and comes back on a later lease, so pruning never loses bytes
the runner has yet to deliver to the store. The
report is journalled before it is sent (:class:`mbnas_runner.journal.PruneJournal`), so a re-delivered order resends the
same report instead of deleting again; unanswered reports are resent on the idle loop. A last heartbeat right after the
child exits (before the result commit) collects the orders the job's own checkpoint registrations produced. The gateway
store copy is never touched.

SIGTERM/SIGINT set ``stopping``: the running child gets a cancel stop file and SIGTERM (SIGKILL after kill_grace_s),
the result is committed with ``runner_shutting_down=true`` (the core settles it as WORKER_SHUTDOWN: requeued without
consuming a retry), then the runner deregisters. The core is the only judge of cancellation, pause, lease loss and
budgets; the runner reports observations (``cancel_observed``, ``started_at``, ``runner_shutting_down``).

The daemon never imports ``mbnas_core`` or ``mbnas_server``.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import posixpath
import queue
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from mbnas_contracts.jobs_protocol import STOP_ENV, JobFailure
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobResult, JobStatus, ProgressMessage
from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.paths import is_within
from mbnas_contracts.util.time import parse_iso, utcnow, utcnow_iso
from mbnas_runner import gpu, reports
from mbnas_runner.contract import handler_override
from mbnas_runner.client import CoreClient, CoreError, CoreUnavailable, JobFence, claim_envelope
from mbnas_runner.envelope import run_dir_for, spec_from_envelope, stop_path_for
from mbnas_runner.journal import AttemptJournal, PruneJournal, Stage, open_attempts, transfer_entries
from mbnas_runner.transfer import DEFAULT_MIME, IN_PLACE_ROLES, ArtifactTransfer, transfer_item

#: never inherited by the child (this host's and the other hosts' secrets; any *_ENV_FILE variable is dropped too)
SECRET_ENV = ("MBNAS_RUNNER_TOKEN", "MBNAS_RUNNER_TOKEN_FILE", "MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY",
              "MBNAS_CORE_TICKET_KEY", "MBNAS_GW_API_TOKEN", "MBNAS_GW_CORE_TOKEN", "MBNAS_GW_ARTIFACT_SIGNING_KEY",
              "MBNAS_API_TOKEN", "MBNAS_ARTIFACT_SIGNING_KEY", "MBNAS_RUNNER_GATEWAY_UPLOAD_TOKEN",
              "MBNAS_RUNNER_GATEWAY_UPLOAD_TOKEN_FILE")
LOCK_FD_ENV = "MBNAS_GPU_LOCK_FD"
#: the child's CUDA enumeration order: NVML's (PCI bus) order, so the NVML index ``gpu_index`` and the child's
#: ``CUDA_VISIBLE_DEVICES=<gpu_index>`` name the same GPU (overrides any inherited value; see RunnerDaemon._spawn)
CUDA_DEVICE_ORDER = "PCI_BUS_ID"
_PR_SET_PDEATHSIG = 1
_TRAINING = {k.value for k in TRAINING_KINDS}
#: kinds whose results carry ``outputs.device_used`` / ``device_uuid`` (RunnerDaemon._with_device)
DEVICE_RESULT_KINDS = frozenset({*_TRAINING, JobKind.EVALUATE.value})
PROGRESS_KEYS = ("epoch", "epoch_target_total", "global_step", "samples_seen", "learning_rate")
#: a full-state payload whose RF-DETR native companion ``native_e<same digits>.ckpt`` goes with it (retention rule)
FULL_STATE_PAYLOAD = re.compile(r"^full_e(\d+)\.pt$")
PRUNE_WAIT_S = 30.0
"""Bound of the wait for running prune orders when the daemon leaves its loop (the journal keeps anything unfinished)."""


def _sha256_fd(fd: int) -> str:
    h = hashlib.sha256()
    while True:
        block = os.read(fd, 1 << 20)
        if not block:
            return h.hexdigest()
        h.update(block)


def execute_prune_order(work_root: str | os.PathLike[str], order: Any, *, runner_id: str,
                        store_dir: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Carry out one ``control.prune`` order on this host (see module docstring): ``(PruneReport body, None)`` after the
    unlink succeeded, or when no file with the ordered digest is at the ordered location (``already_absent``: nothing
    deleted); ``(None, {code, detail})`` when the order is refused - then nothing was deleted at all.

    Refusals: ``PRUNE_ORDER_INVALID`` (not a valid ``PruneOrder``: absolute / ``..`` relpath, unsafe run id, ...),
    ``PRUNE_OUTSIDE_WORK_ROOT`` (the resolved directory is not inside ``<realpath(work_root)>/runs/<run_id>``, e.g. a
    symlinked directory), ``PRUNE_IN_ARTIFACT_STORE``, ``PRUNE_SYMLINK_REFUSED``, ``PRUNE_NOT_A_FILE``,
    ``PRUNE_FILE_CHANGED`` (replaced while it was checked), ``PRUNE_IO_ERROR``. The directory is opened once and every
    check, the hash and the unlink use that descriptor (``dir_fd``), so a path swapped after the containment check
    cannot redirect the deletion; the hashed file must still be the same inode when it is unlinked."""
    from mbnas_contracts.runner_protocol import PruneOrder

    try:
        o = PruneOrder.model_validate(order)
    except ValueError as exc:
        return None, {"code": "PRUNE_ORDER_INVALID", "detail": str(exc)[:300]}
    root = os.path.realpath(str(work_root))
    run_base = os.path.join(root, "runs", o.run_id)  # not resolved: a symlinked run dir resolves outside -> refused
    parent, name = os.path.split(os.path.normpath(os.path.join(run_base, o.relpath)))
    real_parent = os.path.realpath(parent)
    if not name or not is_within(real_parent, run_base) or not is_within(real_parent, root):
        return None, {"code": "PRUNE_OUTSIDE_WORK_ROOT",
                      "detail": "the ordered file is not inside this runner's <work_root>/runs/<run_id>"}
    if store_dir and is_within(os.path.join(real_parent, name), os.path.realpath(str(store_dir))):
        return None, {"code": "PRUNE_IN_ARTIFACT_STORE", "detail": "the artifact store is never pruned by a runner"}
    base = {"runner_id": str(runner_id), "request_id": o.request_id, "run_id": o.run_id, "relpath": o.relpath,
            "sha256": o.sha256, "bytes": o.bytes}

    def absent(detail: str) -> tuple[dict[str, Any], None]:
        return {**base, "already_absent": True, "bytes_freed": 0, "removed": [], "detail": detail,
                "pruned_at": utcnow_iso()}, None

    try:
        dfd = os.open(real_parent, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return absent("the ordered directory no longer exists on this runner")
    except OSError as exc:
        return None, {"code": "PRUNE_IO_ERROR", "detail": f"directory not readable ({type(exc).__name__})"}
    removed: list[dict[str, Any]] = []
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return absent("no file at the ordered location (deleted before this order, e.g. by the stage trainer)")
        if stat.S_ISLNK(st.st_mode):
            return None, {"code": "PRUNE_SYMLINK_REFUSED", "detail": "the ordered location is a symlink"}
        if not stat.S_ISREG(st.st_mode):
            return None, {"code": "PRUNE_NOT_A_FILE", "detail": "the ordered location is not a regular file"}
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        except FileNotFoundError:
            return absent("no file at the ordered location (deleted concurrently, not by this order)")
        try:
            fst = os.fstat(fd)
            digest = _sha256_fd(fd)
        finally:
            os.close(fd)
        if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            return None, {"code": "PRUNE_FILE_CHANGED", "detail": "the file was replaced while it was checked"}
        if digest != o.sha256 or fst.st_size != o.bytes:
            return absent("other bytes at the ordered location: the ordered checkpoint is gone; that file was kept")
        try:
            now_st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return absent("no file at the ordered location (deleted concurrently, not by this order)")
        if (now_st.st_dev, now_st.st_ino) != (fst.st_dev, fst.st_ino):
            return None, {"code": "PRUNE_FILE_CHANGED", "detail": "the file was replaced while it was checked"}
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            return absent("no file at the ordered location (deleted concurrently, not by this order)")
        removed.append({"relpath": o.relpath, "bytes": int(fst.st_size), "sha256": digest})
        match = FULL_STATE_PAYLOAD.match(name)
        if match:  # the RF-DETR native checkpoint the full state references (same rule as core retention)
            native = f"native_e{match.group(1)}.ckpt"
            try:
                nst = os.stat(native, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISREG(nst.st_mode):
                    os.unlink(native, dir_fd=dfd)
                    removed.append({"relpath": posixpath.join(posixpath.dirname(o.relpath), native),
                                    "bytes": int(nst.st_size)})
            except FileNotFoundError:
                pass
    except OSError as exc:
        if not removed:
            return None, {"code": "PRUNE_IO_ERROR", "detail": f"{type(exc).__name__}"[:300]}
    finally:
        os.close(dfd)
    # by construction the PruneReport shape: the payload entry carries the ordered relpath, digest and size
    return {**base, "already_absent": False, "bytes_freed": sum(i["bytes"] for i in removed), "removed": removed,
            "detail": None, "pruned_at": utcnow_iso()}, None


def _torch_device(value: Any) -> bool:
    """``cpu`` / ``cuda`` / ``cuda:<n>``: a torch device string (a target id such as ``host-gpu-0`` is not one)."""
    return isinstance(value, str) and (value in ("cpu", "cuda") or (value.startswith("cuda:") and value[5:].isdigit()))


def _inherited(key: str) -> bool:
    return key not in SECRET_ENV and not (key.startswith("MBNAS_") and key.endswith("_ENV_FILE"))


def _die_with_parent() -> None:  # runs in the child between fork and exec
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    except OSError:
        pass


def _lock_fds() -> tuple[int, ...]:
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


def numeric_training_progress(payload: dict[str, Any]) -> dict[str, Any]:
    """Measured scalar loss/speed values only (owner worker ``_numeric_training_progress``, unchanged)."""
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


def child_settings_env(settings: Any) -> dict[str, str]:
    """The daemon's effective non-secret settings for the child's ``RunnerSettings`` (children read no dotenv)."""
    return {"MBNAS_RUNNER_WORK_ROOT": settings.work_root, "MBNAS_RUNNER_OUTPUT_ROOTS": json.dumps(settings.output_roots),
            "MBNAS_RUNNER_DATASET_ROOTS": json.dumps(settings.dataset_roots),
            "MBNAS_RUNNER_DATASET_MOUNTS": json.dumps(settings.dataset_mounts),
            "MBNAS_RUNNER_WEIGHTS_DIR": settings.weights_dir, "MBNAS_RUNNER_LOG_DIR": settings.log_dir,
            "MBNAS_RUNNER_ALLOW_PICKLE_RESUME": "1" if settings.allow_pickle_resume else "0",
            "MBNAS_RUNNER_RUNNER_ID": str(settings.runner_id),
            # the daemon, never the child, transfers artifacts; "none" also keeps an inherited
            # MBNAS_RUNNER_ARTIFACT_TRANSFER=local without its store dir from breaking the child's RunnerSettings.load()
            "MBNAS_RUNNER_ARTIFACT_TRANSFER": "none"}


@dataclass
class _Live:
    """State shared by the monitor loop and the heartbeat thread of one attempt."""

    lease_expires_at: str | None = None
    lease_lost: bool = False
    cancel_requested: bool = False
    stop_action: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    pending_progress: dict[str, Any] | None = None  # latest progress message not yet sent (batches keep the newest)
    messages: list[dict[str, Any]] = field(default_factory=list)  # pending log messages
    pid: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class RunnerDaemon:
    """See module docstring. ``client`` and ``spawn`` are injectable for tests."""

    def __init__(self, settings: Any, client: CoreClient | None = None, *, log: Callable[[str], None] | None = None,
                 spawn: Callable[..., subprocess.Popen[str]] | None = None) -> None:
        self.settings = settings
        self.client = client or CoreClient(settings)  # CORE_URL_INSECURE is raised here, before anything runs
        self.stopping = False
        self.jobs_done = 0
        self.cpu_only = os.environ.get("CUDA_VISIBLE_DEVICES") == ""
        self.device = gpu.device_info(settings.gpu_index)
        if self.cpu_only:
            self.device = {**self.device, "id": "cpu", "uuid": None, "name": "CPU (CUDA_VISIBLE_DEVICES='')"}
        self.config: dict[str, Any] = {}
        self.clock_offset_s = 0.0  # core clock - local clock, from server_time when the core reports it
        self._log = log or (lambda msg: print(f"{utcnow_iso()} runner {settings.runner_id}: {msg}", file=sys.stderr,
                                              flush=True))
        self._spawn_fn = spawn
        self._child_pid: int | None = None
        self._gpu_ignored: set[int] = set()  # live foreign pids that outlasted a full wait (waited for once)
        self.transfer = ArtifactTransfer(settings, log=self._log)  # disabled unless artifact_transfer=local
        self._retry_thread: threading.Thread | None = None
        self._next_retry_check = 0.0  # monotonic; the idle loop scans for transfers to retry at most once per heartbeat
        # retention prune orders: journal (outbox), queue and one background worker thread
        self.prunes = PruneJournal(settings.work_root)
        self._prune_lock = threading.Lock()
        self._prune_queue: list[dict[str, Any]] = []
        self._prune_pending: set[str] = set()  # request ids queued or being executed
        self._prune_thread: threading.Thread | None = None
        self._next_prune_resend = 0.0  # monotonic; unanswered prune reports are resent at most once per heartbeat
        if self.transfer.enabled:
            self._log(f"artifact transfer: local into {settings.artifact_store_dir} (store mode "
                      f"{self.transfer.store.mode}, max {self.transfer.max_tries} tries per file)")

    # -- configuration negotiated at register --------------------------------------------------------------------
    @property
    def heartbeat_s(self) -> float:
        return float(self.config.get("heartbeat_s") or self.settings.heartbeat_s)

    @property
    def progress_interval_s(self) -> float:
        return max(float(self.config.get("progress_interval_s") or self.settings.progress_interval_s), 1.0)

    @property
    def max_batch_messages(self) -> int:
        return int(min(self.config.get("max_batch_messages") or self.settings.max_batch_messages,
                       self.settings.max_batch_messages))

    @property
    def kinds(self) -> list[str]:
        allowed = self.config.get("allowed_kinds")
        mine = list(self.settings.kinds)
        return [k for k in mine if k in allowed] if isinstance(allowed, list) else mine

    def _note_server_time(self, body: Any) -> None:
        if isinstance(body, dict) and isinstance(body.get("server_time"), str):
            try:
                self.clock_offset_s = (parse_iso(body["server_time"]) - utcnow()).total_seconds()
            except ValueError:
                pass

    def core_now(self) -> float:
        return utcnow().timestamp() + self.clock_offset_s

    def register(self) -> dict[str, Any]:
        """E1 with the device and the runtime manifest; keeps the negotiated configuration."""
        import socket

        mounts = {root: os.path.isdir(local) for root, local in self.settings.dataset_mounts.items()}
        self.config = self.client.register(kinds=list(self.settings.kinds), runtime=reports.runtime_manifest(),
                                           host=socket.gethostname(), device=self.device.get("id"),
                                           device_uuid=self.device.get("uuid"), device_name=self.device.get("name"),
                                           data_mounts=mounts)
        self._note_server_time(self.config)
        self.transfer.retry_base_s = self.heartbeat_s
        return self.config

    # -- loop ----------------------------------------------------------------------------------------------------
    def install_signals(self) -> None:
        def _stop(signum: int, _frame: Any) -> None:
            self.stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def run(self, *, once: bool = False, max_jobs: int | None = None) -> int:
        """Register, replay the journal, then claim and execute jobs until stopped (``once``: at most one claim)."""
        self.register()
        try:
            while not self.stopping:
                self.replay()
                self.retry_transfers_due()
                self.resend_prune_reports_due()
                worked = self.tick()
                if once or (max_jobs is not None and self.jobs_done >= max_jobs):
                    break
                if not worked and not self.stopping:
                    self._sleep(self.settings.poll_s)
            self.replay()  # deliver what is still in the outbox before going offline
            self.wait_prunes(PRUNE_WAIT_S)  # a deletion in progress finishes and is reported before going offline
            if not self.stopping:  # on SIGTERM unanswered prune reports wait in the journal for the next start
                self.resend_prune_reports()
        finally:
            try:
                self.client.deregister()
            except (CoreError, CoreUnavailable) as exc:
                self._log(f"deregister failed: {exc}")
            self.client.close()
        return 0

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < end:
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))

    def tick(self) -> bool:
        """Wait for the device, claim at most one job and run it; True when a job was claimed."""
        self.wait_gpu_clear()
        try:
            env = self.client.claim(self.kinds)
        except CoreUnavailable as exc:
            self._log(f"claim failed: {exc}")
            return False
        if env is None:
            return False
        self.execute(env)  # the claim response {envelope, ticket, server_time}
        self.jobs_done += 1
        return True

    def _own_pids(self) -> set[int]:
        """This process, its ancestors (never a job of this runner), remembered live foreign pids and the child."""
        own = {os.getpid(), *self._gpu_ignored}
        pid = os.getppid()
        while pid > 1 and pid not in own:
            own.add(pid)
            try:
                with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
                    pid = int(fh.read().rsplit(")", 1)[-1].split()[1])
            except (OSError, ValueError, IndexError):
                break
        if self._child_pid is not None:
            own.add(self._child_pid)
        return own

    def wait_gpu_clear(self) -> dict[str, Any]:
        """Bounded, logged wait until NVML lists no foreign compute pid. A
        *live* foreign pid still listed after a full wait is remembered and not waited for again; a dead or zombie
        pid is always waited for."""
        if self.cpu_only:
            return {"cleared": True, "waited_s": 0.0, "foreign": [], "nvml": False}
        out = gpu.wait_clear(self.settings.gpu_index, exclude=self._own_pids,
                             timeout_s=self.settings.gpu_clear_timeout_s, log=self._log)
        if not out["cleared"]:
            self._gpu_ignored.update(p for p in out["foreign"] if gpu.proc_state(p) not in (None, "Z"))
        return out

    # -- restart replay ----------------------------------------------------------------------------------------
    def replay(self) -> list[tuple[str, int, str]]:
        """Close every open journal attempt; returns ``(job_id, attempt, what was done)``."""
        done: list[tuple[str, int, str]] = []
        for j in open_attempts(self.settings.work_root):
            claim = j.claim or {}
            try:
                fence = JobFence.from_claim(claim)
            except (KeyError, TypeError, ValueError):
                j.record_discarded("unreadable envelope", "ENVELOPE_INVALID")
                done.append((j.job_id, j.attempt, "discarded"))
                continue
            stage = j.stage
            if stage == Stage.CLAIMED:
                # the lease may still be ours: start it and run the job as a fresh attempt of this runner
                if self._child_pid is not None:
                    continue
                self.execute(claim, journal=j)
                done.append((j.job_id, j.attempt, "executed"))
                continue
            if stage == Stage.STARTED:  # the child died with the old daemon (PDEATHSIG): no result exists
                started = j.started or {}
                result = JobResult(job_id=fence.job_id, status=JobStatus.FAILED, attempt=max(1, fence.attempt),
                                   reason_code="CHILD_EXIT_NO_RESULT", message="runner restarted; the child exited "
                                   "without a result", outputs={"retryable": True}, started_at=started.get("started_at"))
                spec = j.spec or {}  # the child of the previous daemon ran with this spec on this runner's device
                if str(spec.get("kind")) in DEVICE_RESULT_KINDS:
                    result = self._with_device(result, spec.get("params"))
                result = self._with_transfer_summary(j, result)
                j.record_result(self._commit(result, started, utcnow_iso(), cancel_observed=False,
                                             shutting_down=False, artifact_ids=[]))
                stage = Stage.RESULT
            if stage == Stage.RESULT:
                self.send_result(j, fence)
                done.append((j.job_id, j.attempt, j.stage.value))
                continue
            if stage == Stage.RESPONDED:
                self._ack(j, fence)
                done.append((j.job_id, j.attempt, j.stage.value))
        return done

    # -- one job -------------------------------------------------------------------------------------------------
    def execute(self, claim: dict[str, Any], *, journal: AttemptJournal | None = None) -> None:
        """Run one claimed job (``ClaimResponse``) to a committed (or discarded) result; see module docstring."""
        fence = JobFence.from_claim(claim)
        j = journal or AttemptJournal.create(self.settings.work_root, fence.job_id, fence.attempt)
        claim = j.record_claim(claim)
        if journal is None:
            self._note_server_time(claim)
        env = claim_envelope(claim)
        run_id = str(env["run"]["run_id"])
        try:
            base = run_dir_for(self.settings.work_root, run_id)
            # stub override (tests/smokes): generic params, like the owner child; otherwise the native handler params
            spec = spec_from_envelope(env, self.settings.work_root, native=handler_override() is None)
            spec = self._assigned_device(spec)
        except JobFailure as fail:
            self._fail_before_spawn(j, fence, fail)
            return
        stop = stop_path_for(base, fence.job_id)
        log = base / "logs" / f"{fence.job_id}.attempt{fence.attempt}.stderr.log"
        for p in (stop, log, base / "protocol" / "resolved.json"):
            p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(base / "protocol" / "resolved.json", env["run"]["resolved_protocol"])
        stop.unlink(missing_ok=True)
        spec_path = j.record_spec(spec.model_dump(mode="json"))
        self.wait_gpu_clear()  # before the attempt starts: waiting for the device is not billed
        started = self._start(j, fence)
        if started is None:
            return
        live = _Live(lease_expires_at=started.get("lease_expires_at"))
        proc = self._spawn(spec_path, stop, log)
        live.pid = self._child_pid = proc.pid
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_pump, args=(proc, lines), daemon=True).start()
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(fence, live, stop, done), daemon=True)
        beat.start()
        try:
            result, cancel_observed = self._monitor(j, fence, env, proc, lines, live, base, stop)
        finally:
            done.set()
            beat.join(timeout=self.heartbeat_s + 5)
            proc.wait()
            self._child_pid = None
        finished = utcnow_iso()
        self._resend_checkpoints(j, fence)
        self._last_beat(fence, live)  # prune orders produced by this job's own checkpoint registrations
        if result is None:  # the owner worker's _settle_job rule, decided where the child ran
            reason = "WORKER_SHUTDOWN" if self.stopping else "CHILD_EXIT_NO_RESULT"
            result = JobResult(job_id=fence.job_id, status=JobStatus.FAILED, attempt=max(1, fence.attempt),
                               reason_code=reason, exit_code=proc.returncode,
                               message=f"child exited with code {proc.returncode} without a result",
                               outputs={"retryable": True}, started_at=started.get("started_at"))
        else:
            result = result.model_copy(update={"exit_code": proc.returncode})
        if spec.kind.value in DEVICE_RESULT_KINDS:
            result = self._with_device(result, spec.params)
        result, artifact_ids = self._preregister(j, fence, env, result, base)  # E7 (+ transfer right after it)
        result = self._with_transfer_summary(j, result)
        commit = self._commit(result, started, finished, cancel_observed=cancel_observed,
                              shutting_down=self.stopping, artifact_ids=artifact_ids)
        if self._body_bytes(commit) > self._body_budget():
            result, artifact_ids = self._externalise_samples(j, fence, env, result, base, artifact_ids)
            result = self._with_transfer_summary(j, result)
            commit = self._commit(result, started, finished, cancel_observed=cancel_observed,
                                  shutting_down=self.stopping, artifact_ids=artifact_ids)
        j.record_result(commit)
        self.send_result(j, fence)
        stop.unlink(missing_ok=True)

    def _assigned_device(self, spec: Any) -> Any:
        """The core names the runner's *assigned* device ``cuda:0`` (training_runs/selection.py: the runner exposes its
        assigned GPU as cuda:0). A runner that registered as a CPU device (``CUDA_VISIBLE_DEVICES=''``, E1
        ``device='cpu'``) has no CUDA device, so its assigned device is ``cpu``; any other device name is refused
        as before by the backend. Logged; the core sees the CPU device in the register row and the result."""
        params = dict(spec.params or {})
        if not self.cpu_only or params.get("device") != "cuda:0":
            return spec
        params["device"] = "cpu"
        self._log(f"job {spec.job_id}: assigned device cuda:0 mapped to cpu (runner registered as a CPU device)")
        return spec.model_copy(update={"params": params})

    def _with_device(self, result: JobResult, params: Any) -> JobResult:
        """``outputs.device_used`` + ``outputs.device_uuid`` on the result of an attempt whose child
        ran on this runner (the core copies ``device_used`` into ``raw_metrics.device_used``).

        A CPU-only runner (registered as ``device=cpu``) reports ``cpu`` and no uuid, whatever was assigned. A GPU
        runner reports the CUDA device the child observed (``mbnas_runner.child.observed_cuda_device``), else the
        assigned ``params.device``, else ``cuda:0`` (the core's name of the runner's assigned GPU); its uuid is the one
        the child read through torch, else the NVML uuid this runner registered (None when neither exists). The child
        runs with ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` and ``CUDA_VISIBLE_DEVICES=<gpu_index>`` (:meth:`_spawn`), so CUDA
        and NVML order agree and both uuids name the same GPU; a child uuid that differs from the registered one is
        still logged (something outside the runner changed the device mapping) and the child's uuid is recorded."""
        outputs = dict(result.outputs or {})
        if self.cpu_only:
            used, uuid = "cpu", None
        else:
            assigned = params.get("device") if isinstance(params, dict) else None
            used = next((d for d in (outputs.get("device_used"), assigned) if _torch_device(d)), "cuda:0")
            registered = self.device.get("uuid")
            observed = outputs.get("device_uuid") if isinstance(outputs.get("device_uuid"), str) else None
            uuid = (observed or registered) if used.startswith("cuda") else None
            if observed and registered and observed != registered and used.startswith("cuda"):
                self._log(f"job {result.job_id}: the child ran on {observed} but this runner registered {registered} "
                          f"(gpu_index {self.settings.gpu_index}, child CUDA_DEVICE_ORDER={CUDA_DEVICE_ORDER}); check "
                          "CUDA_DEVICE_ORDER / CUDA_VISIBLE_DEVICES")
        outputs.update(device_used=used, device_uuid=uuid)
        return result.model_copy(update={"outputs": outputs})

    def _start(self, j: AttemptJournal, fence: JobFence) -> dict[str, Any] | None:
        if j.started is not None:
            return j.started
        try:
            started = self.client.start(fence, device_uuid=self.device.get("uuid"))
        except CoreError as exc:
            j.record_discarded(f"start refused: {exc.message}", exc.code)
            self._log(f"start of {fence.job_id} attempt {fence.attempt} refused: {exc}")
            return None
        except CoreUnavailable as exc:
            self._log(f"start of {fence.job_id} not delivered ({exc}); left in the journal")
            return None
        self._note_server_time(started)
        return j.record_started(started)

    def _fail_before_spawn(self, j: AttemptJournal, fence: JobFence, fail: JobFailure) -> None:
        """An envelope the runner cannot turn into a job: start the attempt and commit the failure (the core settles
        it exactly like a child that failed the same way)."""
        started = self._start(j, fence)
        if started is None:
            return
        result = JobResult(job_id=fence.job_id, status=JobStatus.FAILED, attempt=max(1, fence.attempt),
                           reason_code=fail.reason_code, message=fail.message,
                           outputs={**fail.outputs, "retryable": fail.retryable},
                           started_at=started.get("started_at"), finished_at=utcnow_iso())
        result = self._with_transfer_summary(j, result)
        j.record_result(self._commit(result, started, utcnow_iso(), cancel_observed=False,
                                     shutting_down=self.stopping, artifact_ids=[]))
        self.send_result(j, fence)

    def _spawn(self, spec_path: Path, stop: Path, log: Path) -> subprocess.Popen[str]:
        env = {k: v for k, v in os.environ.items() if _inherited(k)}
        env.update(child_settings_env(self.settings))
        # gpu_index is an NVML index (PCI bus order; device_info/sample/wait_clear use it). CUDA_DEVICE_ORDER=PCI_BUS_ID
        # makes CUDA enumerate in the same order, so CUDA_VISIBLE_DEVICES=<gpu_index> exposes exactly the GPU this
        # runner registered as the child's cuda:0 (CUDA's default FASTEST_FIRST order may differ on mixed-GPU hosts).
        env.update({"CUDA_VISIBLE_DEVICES": "" if self.cpu_only else str(self.settings.gpu_index),
                    "CUDA_DEVICE_ORDER": CUDA_DEVICE_ORDER,
                    "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", STOP_ENV: str(stop),
                    "MBNAS_WORKER_ID": str(self.settings.runner_id)})
        argv = [self.settings.worker_python, "-B", "-m", "mbnas_runner.child", "--spec-file", str(spec_path)]
        if self._spawn_fn is not None:
            return self._spawn_fn(argv, env=env, log=log)
        with open(log, "a", encoding="utf-8") as stderr:
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, env=env,
                                    text=True, bufsize=1, preexec_fn=_die_with_parent, close_fds=True,
                                    pass_fds=_lock_fds())

    # -- heartbeat thread ------------------------------------------------------------------------------------------
    def _heartbeat(self, fence: JobFence, live: _Live, stop: Path, done: threading.Event) -> None:
        while not done.wait(self.heartbeat_s):
            self._kick_transfer_retry()  # artifact transfers to retry (background thread; the store is not the core)
            with live.lock:
                prog = {k: live.progress[k] for k in PROGRESS_KEYS if live.progress.get(k) is not None}
            try:
                body = self.client.heartbeat(fence, device_sample={} if self.cpu_only else gpu.sample(
                    self.settings.gpu_index), progress=prog)
            except CoreError as exc:
                if exc.code == "LEASE_LOST" or exc.status == 409:
                    with live.lock:
                        live.lease_lost = True
                    self._log(f"lease of {fence.job_id} attempt {fence.attempt} lost: stopping the child")
                    return
                self._log(f"heartbeat refused: {exc}")
                continue
            except CoreUnavailable as exc:
                self._log(f"heartbeat not delivered: {exc}")
                continue
            self._note_server_time(body)
            with live.lock:
                if body.get("lease_expires_at"):
                    live.lease_expires_at = body["lease_expires_at"]
            self.apply_control(body.get("control") or {}, live, stop)

    def _last_beat(self, fence: JobFence, live: _Live) -> None:
        """One more heartbeat once the child has exited and before the result commit (the lease is still held): it
        collects the ``control.prune`` orders the core planned from this job's last checkpoint registrations, which
        the beat thread may not have seen yet. Its action flags are not applied (the child is gone); a lost
        lease or an unreachable core changes nothing here (the result commit decides)."""
        with live.lock:
            if live.lease_lost:
                return
            prog = {k: live.progress[k] for k in PROGRESS_KEYS if live.progress.get(k) is not None}
        try:
            body = self.client.heartbeat(fence, device_sample={}, progress=prog)
        except (CoreError, CoreUnavailable) as exc:
            self._log(f"last heartbeat of {fence.job_id} attempt {fence.attempt} not answered: {exc}")
            return
        self._note_server_time(body)
        self.submit_prunes((body.get("control") or {}).get("prune"))

    # -- retention prune orders ----------------------------------------------------------------------------------------
    def submit_prunes(self, orders: Any) -> int:
        """Queue the prune orders of a heartbeat answer for the background worker (a request id that is already queued
        or running is skipped); returns how many were queued."""
        if not isinstance(orders, list) or not orders:
            return 0
        queued = 0
        with self._prune_lock:
            for order in orders:
                rid = order.get("request_id") if isinstance(order, dict) else None
                if not isinstance(rid, str) or not rid or rid in self._prune_pending:
                    continue
                self._prune_pending.add(rid)
                self._prune_queue.append(order)
                queued += 1
            if queued and (self._prune_thread is None or not self._prune_thread.is_alive()):
                self._prune_thread = threading.Thread(target=self._prune_worker, name="checkpoint-prune", daemon=True)
                self._prune_thread.start()
        return queued

    def _prune_worker(self) -> None:
        while True:
            with self._prune_lock:
                if not self._prune_queue:
                    self._prune_thread = None
                    return
                order = self._prune_queue.pop(0)
            try:
                self.prune_one(order)
            except Exception as exc:  # noqa: BLE001 - one bad order never stops the others (the journal keeps state)
                self._log(f"prune order {order.get('request_id')} failed: {type(exc).__name__}: {exc}")
            finally:
                with self._prune_lock:
                    self._prune_pending.discard(str(order.get("request_id")))

    def wait_prunes(self, timeout: float) -> bool:
        """Wait (bounded) until the queued prune orders are done; True when nothing is left."""
        end = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < end:
            with self._prune_lock:
                thread = self._prune_thread
                if thread is None and not self._prune_queue:
                    return True
            if thread is not None:
                thread.join(timeout=max(0.0, min(0.5, end - time.monotonic())))
            else:
                time.sleep(0.05)
        return False

    def prune_one(self, order: dict[str, Any]) -> str:
        """Execute one prune order (idempotent by ``request_id``) and report it (E11); returns ``deleted``,
        ``already_absent``, ``refused``, ``deferred`` (store transfer still owed) or ``unreported`` (the report is
        journalled and resent later)."""
        rid = str(order.get("request_id") or "")
        try:
            entry = self.prunes.get(rid)
        except ValueError:  # not a safe file name: certainly not an order of the core
            self._log(f"prune order refused (PRUNE_ORDER_INVALID): unusable request id {rid[:80]!r}")
            return "refused"
        if entry is not None and entry.get("report") is not None:
            # a re-delivered order: its report (or the answer) was lost - resend the same report, never delete again
            return self._send_prune_report(entry)
        if self._transfer_owed(order):
            self.prunes.record_refused(order, {"code": "PRUNE_TRANSFER_PENDING", "detail": "the file's transfer into "
                                               "the artifact store is not done yet; the order comes back later"})
            self._log(f"prune order {rid} for checkpoint {order.get('checkpoint_id')} deferred: its artifact store "
                      "transfer is still pending (nothing deleted)")
            return "deferred"
        report, refusal = execute_prune_order(self.settings.work_root, order, runner_id=str(self.settings.runner_id),
                                              store_dir=getattr(self.settings, "artifact_store_dir", None))
        if report is None:
            refusal = refusal or {"code": "PRUNE_REFUSED"}
            self.prunes.record_refused(order, refusal)
            self._log(f"prune order {rid} for checkpoint {order.get('checkpoint_id')} refused ({refusal.get('code')}): "
                      "nothing deleted")
            return "refused"
        entry = self.prunes.record_report(order, report)  # outbox rule: journalled before it is sent
        return self._send_prune_report(entry)

    def _transfer_owed(self, order: dict[str, Any]) -> bool:
        """True when the journal still owes the artifact store the ordered bytes (a transfer item of the same run,
        relpath and digest that is pending or failed and still retryable); False when transfers are off."""
        if not self.transfer.enabled:
            return False
        sha = reports.normal_sha(order.get("sha256"))
        try:
            for j, path in transfer_entries(self.settings.work_root):
                for item in (j.read_transfers(path).get("items") or []):
                    if (item.get("run_id") == order.get("run_id") and item.get("relpath") == order.get("relpath")
                            and reports.normal_sha(item.get("sha256")) == sha and self.transfer.retryable(item)):
                        return True
        except (OSError, ValueError):  # an unreadable journal: be safe, keep the file for now
            return True
        return False

    def _send_prune_report(self, entry: dict[str, Any]) -> str:
        order, report = entry.get("order") or {}, entry["report"]
        outcome = "already_absent" if report.get("already_absent") else "deleted"
        rid = str(report.get("request_id"))
        try:
            _, ack, _ = self.client.request(
                "POST", f"/checkpoints/{quote(str(order.get('checkpoint_id') or ''), safe='')}/pruned", report)
        except CoreError as exc:  # final: the core refuses this report (logged; the entry keeps the answer)
            self.prunes.record_response(rid, {"error": exc.code, "status": exc.status})
            self._log(f"prune report {rid} refused by the core: {exc}")
            return outcome
        except CoreUnavailable as exc:
            self._log(f"prune report {rid} not delivered ({exc}); resent later")
            return "unreported"
        self.prunes.record_response(rid, {"ack": ack})
        self._log(f"checkpoint {order.get('checkpoint_id')} pruned ({outcome}, {report.get('bytes_freed', 0)} bytes "
                  f"freed; request {rid})")
        return outcome

    def resend_prune_reports(self) -> int:
        """Resend every journalled prune report the core never answered; returns how many were sent."""
        sent = 0
        for entry in self.prunes.unanswered():
            if self._send_prune_report(entry) != "unreported":
                sent += 1
        return sent

    def resend_prune_reports_due(self) -> None:
        """Idle-loop resend of unanswered prune reports, at most once per heartbeat interval."""
        now = time.monotonic()
        if now < self._next_prune_resend:
            return
        self._next_prune_resend = now + self.heartbeat_s
        try:
            self.resend_prune_reports()
        except Exception as exc:  # noqa: BLE001 - best effort; the journal keeps the reports
            self._log(f"prune report resend pass failed: {exc}")

    def apply_control(self, control: dict[str, Any], live: _Live, stop: Path) -> None:
        """Materialise ``control.action`` as the child's stop file (pause/cancel) or withdraw a pause (null); queue
        ``control.prune`` orders for the prune worker."""
        self.submit_prunes(control.get("prune"))
        action = control.get("action")
        with live.lock:
            current = live.stop_action
        if action in ("pause", "cancel") and current != action and current != "cancel":
            stop.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(stop, {"action": action, "requested_at": utcnow_iso()}, indent=None)
            with live.lock:
                live.stop_action = action
                live.cancel_requested = live.cancel_requested or action == "cancel"
        elif action is None and current == "pause":
            stop.unlink(missing_ok=True)  # pause withdrawn
            with live.lock:
                live.stop_action = None

    # -- monitor -------------------------------------------------------------------------------------------------
    def _lease_expired(self, live: _Live) -> bool:
        with live.lock:
            exp = live.lease_expires_at
        if not exp or not self.settings.kill_on_lease_expiry:
            return False
        try:
            return self.core_now() > parse_iso(exp).timestamp()
        except ValueError:
            return False

    def _monitor(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], proc: subprocess.Popen[str],
                 lines: "queue.Queue[str | None]", live: _Live, base: Path,
                 stop: Path) -> tuple[JobResult | None, bool]:
        result: JobResult | None = None
        cancel_observed = False
        kill_at: float | None = None
        last_flush = 0.0
        eof = False
        grace = float((env.get("control") or {}).get("kill_grace_s") or self.settings.kill_grace_s)
        while not eof:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                eof = True
            elif line:
                result = self._handle_line(j, fence, env, line, live, base) or result
            now = time.monotonic()
            with live.lock:
                lost, cancel = live.lease_lost, live.cancel_requested
            if kill_at is None:
                if lost or self._lease_expired(live):
                    if not lost:
                        self._log(f"lease of {fence.job_id} expired locally without renewal: stopping the child")
                    proc.terminate()
                    kill_at = now + grace
                elif cancel or self.stopping:
                    if self.stopping and live.stop_action != "cancel":
                        atomic_write_json(stop, {"action": "cancel", "requested_at": utcnow_iso()}, indent=None)
                        live.stop_action = "cancel"
                    cancel_observed = cancel
                    proc.terminate()
                    kill_at = now + grace
            if kill_at is not None and now >= kill_at and proc.poll() is None:
                proc.kill()
            if now - last_flush >= self.progress_interval_s and (live.pending_progress or live.messages):
                last_flush = now
                self._flush_progress(j, fence, live)
        if live.pending_progress or live.messages:
            self._flush_progress(j, fence, live)
        return result, cancel_observed

    def _handle_line(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], line: str, live: _Live,
                     base: Path) -> JobResult | None:
        try:
            msg = ProgressMessage.parse_line(line)
        except ValueError:
            return None  # stray non-protocol output
        if msg.job_id != fence.job_id:
            return None
        if msg.type == "progress":
            if msg.payload.get("generation_summary") is not None:
                return None  # owner-only payload (E5 rejects it); a runner kind never emits it
            scalars = {k: v for k, v in msg.model_dump().items() if k in PROGRESS_KEYS and v is not None}
            measured = numeric_training_progress(msg.payload)
            with live.lock:
                live.progress.update(scalars)
                live.progress.update(measured)
                # only measured scalars travel (the owner worker never forwarded arbitrary payload state)
                live.pending_progress = {"type": "progress", "job_id": msg.job_id, "ts": msg.ts, **scalars,
                                         "payload": measured}
        elif msg.type == "checkpoint":
            self._checkpoint(j, fence, env, msg.payload, live, base)
        elif msg.type == "result":
            try:
                return JobResult.model_validate(msg.payload)
            except ValueError:
                return None
        return None

    def _checkpoint(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], p: dict[str, Any], live: _Live,
                    base: Path) -> None:
        spec = env["job"]["spec"]
        report, detail = reports.verify_checkpoint(p, base, spec.get("candidate_id"), spec.get("rung"))
        if report is None:
            self._message(live, fence.job_id, "error", f"checkpoint report rejected ({detail['problem']})",
                          reason_code="CHECKPOINT_REPORT_REJECTED", path=detail["path"],
                          reported_sha256=detail["reported_sha256"], computed_sha256=detail["computed_sha256"])
            return
        report = {"checkpoint_id": f"ck-{os.urandom(8).hex()}", **report}  # fixed before sending: resends reuse it
        entry = j.record_checkpoint(report, report["sha256"])
        try:
            resp = self.client.checkpoint(fence, report)
        except CoreError as exc:
            if exc.code == "LEASE_LOST":
                with live.lock:
                    live.lease_lost = True
            j.record_response_of(entry, {"error": exc.code, "status": exc.status})
            self._log(f"checkpoint registration refused: {exc}")
            return
        except CoreUnavailable as exc:
            self._log(f"checkpoint registration not delivered: {exc}")
            return
        j.record_response_of(entry, resp)
        self._transfer_checkpoint(j, fence, report, resp, run_id=env["run"]["run_id"])

    def _resend_checkpoints(self, j: AttemptJournal, fence: JobFence) -> None:
        """Deliver checkpoint reports whose request never got an answer (outbox entries with ``response`` null)."""
        for path, entry in j.outbox("checkpoints"):
            if entry.get("response") is not None:
                continue
            try:
                resp = self.client.checkpoint(fence, entry["report"])
            except CoreError as exc:
                j.record_response_of(path, {"error": exc.code, "status": exc.status})
            except CoreUnavailable as exc:
                self._log(f"checkpoint registration still not delivered: {exc}")
            else:
                j.record_response_of(path, resp)
                self._transfer_checkpoint(j, fence, entry["report"], resp)

    def _message(self, live: _Live, job_id: str, level: str, message: str, **fields: Any) -> None:
        """Queue a ``log`` ProgressMessage for the next batch (bounded; the stderr log keeps everything)."""
        with live.lock:
            if len(live.messages) < 4 * self.settings.max_batch_messages:
                live.messages.append({"type": "log", "job_id": job_id, "ts": utcnow_iso(), "message": message[:500],
                                      "payload": {"level": level, **{k: v for k, v in fields.items() if v is not None}}})

    def _flush_progress(self, j: AttemptJournal, fence: JobFence, live: _Live) -> None:
        with live.lock:
            logs = live.messages[: max(0, self.max_batch_messages - (1 if live.pending_progress else 0))]
            prog = live.pending_progress
            batch = [*logs, *([prog] if prog else [])]
        if not batch:
            return
        seq = j.progress_seq + 1
        try:
            resp = self.client.progress(fence, seq_start=seq, messages=batch, retries=1)
        except CoreError as exc:
            if exc.code == "LEASE_LOST":
                with live.lock:
                    live.lease_lost = True
            self._log(f"progress batch refused: {exc}")
            return
        except CoreUnavailable as exc:
            self._log(f"progress batch not delivered: {exc}")
            return
        with live.lock:
            del live.messages[: len(logs)]
            if live.pending_progress is prog:
                live.pending_progress = None
        j.record_progress_seq(int(resp.get("accepted_seq") or seq + len(batch) - 1))

    # -- result --------------------------------------------------------------------------------------------------
    def _preregister(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], result: JobResult,
                     base: Path) -> tuple[JobResult, list[str]]:
        """Register result artifacts (E7) and attach their ids; returns the updated result and the id list."""
        outputs = json.loads(json.dumps(result.outputs))
        evaluation = outputs.get("evaluation") if isinstance(outputs.get("evaluation"), dict) else None
        items: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []  # (role, target entry, registration)
        problems: list[str] = []
        for entry in (evaluation or {}).get("artifacts") or []:
            reg, problem = reports.artifact_registration(entry, base)
            if reg is None:
                problems.append(problem or "artifact not registered")
                continue
            items.append(("evaluation", entry, reg))
        spec = env["job"]["spec"]
        cand = env.get("candidate") or {}
        exec_report: dict[str, Any] | None = None
        if str(spec.get("kind")) in _TRAINING and spec.get("candidate_id"):
            item, exec_report = reports.execution_configuration(base, spec["candidate_id"], cand.get("family"))
            if item is not None:
                items.append(("execution_configuration", None, item))
            for img in reports.native_images(base, spec["candidate_id"]):
                items.append(("native_image", None, img))
        ids: list[str] = []
        if items:
            batch = [reg for _, _, reg in items]
            entry_path = j.record_artifacts(batch)
            try:
                resp = self.client.artifacts(fence, batch)
            except (CoreError, CoreUnavailable) as exc:
                j.record_response_of(entry_path, {"error": getattr(exc, "code", type(exc).__name__)})
                problems.append(f"artifact pre-registration failed: {exc}"[:300])
                resp = []
            else:
                j.record_response_of(entry_path, resp)
            by_rel = {r.get("relpath"): r for r in resp if isinstance(r, dict)}
            native_ids: list[str] = []
            registered: list[tuple[str, dict[str, Any], str]] = []
            for role, target, reg in items:
                row = by_rel.get(reg["relpath"])
                if not row or not row.get("artifact_id"):
                    continue
                ids.append(str(row["artifact_id"]))
                registered.append((role, reg, str(row["artifact_id"])))
                if role == "evaluation" and target is not None:
                    target["artifact_id"] = row["artifact_id"]
                elif role == "execution_configuration" and exec_report is not None:
                    exec_report["artifact_id"] = row["artifact_id"]
                elif role == "native_image":
                    native_ids.append(str(row["artifact_id"]))
            if native_ids:
                outputs["native_image_artifact_ids"] = native_ids
            self._transfer_artifacts(j, fence, env, base, registered)
        if exec_report is not None:
            outputs["execution_configuration"] = exec_report
        if problems:
            outputs["runner_artifact_problems"] = problems
        return result.model_copy(update={"outputs": outputs}), ids

    # -- result bodies above the core's runner body limit -----------------------------------------------------------
    @staticmethod
    def _body_bytes(commit: dict[str, Any]) -> int:
        return len(json.dumps(commit, separators=(",", ":"), default=str).encode("utf-8"))

    def _body_budget(self) -> int:
        """``max_body_bytes`` negotiated at register (E1) minus room for the fence, ticket and digest."""
        limit = self.config.get("max_body_bytes") or getattr(self.settings, "max_body_bytes", None) or (32 << 20)
        return max(int(limit) - 8192, 1024)

    def _externalise_samples(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], result: JobResult,
                             base: Path, artifact_ids: list[str]) -> tuple[JobResult, list[str]]:
        """Move ``evaluation.per_sample`` and ``evaluation.latency.samples`` into an ``evaluation_detail`` JSON artifact
        (registered with E7) and leave a reference in their place, so the commit fits under the body limit."""
        outputs = json.loads(json.dumps(result.outputs, default=str))
        evaluation = outputs.get("evaluation")
        if not isinstance(evaluation, dict):
            return result, artifact_ids
        detail: dict[str, Any] = {}
        if "per_sample" in evaluation:
            detail["per_sample"] = evaluation.pop("per_sample")
        latency = evaluation.get("latency")
        if isinstance(latency, dict) and "samples" in latency:
            detail["latency_samples"] = latency.pop("samples")
        if not detail:
            return result, artifact_ids
        cid = (env["job"]["spec"].get("candidate_id") or "job")
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{cid}")
        path = base / "evaluations" / safe / f"evaluation_detail_{fence.job_id}_a{fence.attempt}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, detail)
        reg, problem = reports.artifact_registration(
            {"kind": "other", "path": str(path), "mime_type": "application/json", "state": "READY",
             "candidate_id": env["job"]["spec"].get("candidate_id"), "split_hash": evaluation.get("split_hash"),
             "meta": {"role": "evaluation_detail", "moved": sorted(detail)}}, base)
        ref: dict[str, Any] = {"moved": sorted(detail),
                               "counts": {k: len(v) if isinstance(v, (list, dict)) else None for k, v in detail.items()}}
        ids = list(artifact_ids)
        if reg is None:
            ref["problem"] = problem
        else:
            ref.update(relpath=reg["relpath"], sha256=reg["sha256"], bytes=reg["bytes"])
            entry_path = j.record_artifacts([reg])
            try:
                resp = self.client.artifacts(fence, [reg])
            except (CoreError, CoreUnavailable) as exc:
                j.record_response_of(entry_path, {"error": getattr(exc, "code", type(exc).__name__)})
                ref["problem"] = f"artifact pre-registration failed: {exc}"[:300]
            else:
                j.record_response_of(entry_path, resp)
                row = next((r for r in resp if isinstance(r, dict) and r.get("relpath") == reg["relpath"]), None)
                if row and row.get("artifact_id"):
                    ref["artifact_id"] = row["artifact_id"]
                    ids.append(str(row["artifact_id"]))
                    self._transfer_artifacts(j, fence, env, base, [("evaluation_detail", reg, str(row["artifact_id"]))])
                    evaluation.setdefault("artifacts", []).append(
                        {"kind": "other", "relpath": reg["relpath"], "path": str(path), "sha256": reg["sha256"],
                         "bytes": reg["bytes"], "mime_type": "application/json", "state": "READY",
                         "artifact_id": row["artifact_id"]})
        evaluation["externalised_detail"] = ref
        self._log(f"result of {fence.job_id} above the body budget: moved {sorted(detail)} to {path.name}")
        return result.model_copy(update={"outputs": outputs}), ids

    # -- artifact transfer -----------------------------------------------------------------------------------------
    @staticmethod
    def _journal_run_id(j: AttemptJournal) -> str:
        return str(claim_envelope(j.claim or {})["run"]["run_id"])

    def _transfer_checkpoint(self, j: AttemptJournal, fence: JobFence, report: dict[str, Any], resp: Any, *,
                             run_id: str | None = None) -> None:
        """Store a checkpoint the core just registered (E6 200 answer); never raises."""
        if not self.transfer.enabled or not isinstance(resp, dict) or resp.get("error") is not None:
            return
        try:
            item = transfer_item(obj="checkpoint", artifact_id=str(resp.get("checkpoint_id") or report["checkpoint_id"]),
                                 run_id=str(run_id or self._journal_run_id(j)), job_id=fence.job_id,
                                 attempt=fence.attempt, relpath=report["relpath"], path=report["uri"],
                                 sha256=report["sha256"], size=report["bytes"], kind="checkpoint", mime=DEFAULT_MIME,
                                 link=True, registered_by=str(self.settings.runner_id),
                                 checkpoint_kind=report.get("kind"), epoch=report.get("epoch"))
            self.transfer.transfer(j, [item])
        except Exception as exc:  # noqa: BLE001 - a transfer problem never stops the job (journal/disk errors included)
            self._log(f"artifact transfer of checkpoint {report.get('checkpoint_id')} not recorded: {exc}")

    def _transfer_artifacts(self, j: AttemptJournal, fence: JobFence, env: dict[str, Any], base: Path,
                            registered: list[tuple[str, dict[str, Any], str]]) -> None:
        """Store the files of an E7 batch the core just answered (``(role, registration, artifact_id)``); never
        raises. Files their writer rewrites in place (:data:`IN_PLACE_ROLES`) are copied, never hard-linked."""
        if not self.transfer.enabled or not registered:
            return
        try:
            root = Path(os.path.realpath(str(base)))
            items = [transfer_item(obj="artifact", artifact_id=aid, run_id=str(env["run"]["run_id"]),
                                   job_id=fence.job_id, attempt=fence.attempt, relpath=reg["relpath"],
                                   path=root / reg["relpath"], sha256=reg["sha256"], size=reg["bytes"],
                                   kind=reg["kind"], mime=reg.get("mime_type"), link=role not in IN_PLACE_ROLES,
                                   registered_by=str(self.settings.runner_id), role=role)
                     for role, reg, aid in registered]
            self.transfer.transfer(j, items)
        except Exception as exc:  # noqa: BLE001 - see _transfer_checkpoint
            self._log(f"artifact transfer of {len(registered)} registered file(s) not recorded: {exc}")

    def _with_transfer_summary(self, j: AttemptJournal, result: JobResult) -> JobResult:
        """``outputs.artifact_transfer`` of this attempt (local mode only; the result is unchanged otherwise)."""
        if not self.transfer.enabled:
            return result
        try:
            summary = self.transfer.summary(j)
        except Exception as exc:  # noqa: BLE001 - an unreadable journal entry must not block the commit
            self._log(f"artifact transfer summary unavailable: {exc}")
            summary = {"mode": self.transfer.mode, "ok": False, "transferred": 0, "failed": [],
                       "error": "TRANSFER_JOURNAL_UNREADABLE"}
        if summary is None:
            return result
        outputs = dict(result.outputs)
        outputs["artifact_transfer"] = summary
        return result.model_copy(update={"outputs": outputs})

    def retry_transfers(self, *, now: float | None = None) -> dict[str, int]:
        """One retry pass over the journal's failed/pending transfer items (due ones only); see
        :meth:`mbnas_runner.transfer.ArtifactTransfer.retry_pending`."""
        if not self.transfer.enabled:
            return {}
        try:
            return self.transfer.retry_pending(self.settings.work_root, now=now)
        except Exception as exc:  # noqa: BLE001 - retries are best effort; the journal keeps the items
            self._log(f"artifact transfer retry pass failed: {exc}")
            return {"error": 1}

    def retry_transfers_due(self) -> None:
        """Idle-loop retry: at most one pass per heartbeat interval, only while something may be left to retry."""
        if not (self.transfer.enabled and self.transfer.pending_hint):
            return
        now = time.monotonic()
        if now < self._next_retry_check:
            return
        self._next_retry_check = now + self.heartbeat_s
        self.retry_transfers()

    def _kick_transfer_retry(self) -> None:
        """Heartbeat-driven retry in a background thread (a copy must never delay the lease renewal)."""
        if not (self.transfer.enabled and self.transfer.pending_hint):
            return
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._retry_thread = threading.Thread(target=self.retry_transfers, name="artifact-transfer-retry", daemon=True)
        self._retry_thread.start()

    def _commit(self, result: JobResult, started: dict[str, Any], finished: str, *, cancel_observed: bool,
                shutting_down: bool, artifact_ids: list[str]) -> dict[str, Any]:
        """The ``ResultCommit`` body without the fence (``payload_digest`` is added when the journal freezes it)."""
        return {"result": result.model_dump(mode="json"), "started_at": started.get("started_at"),
                "finished_at": finished, "cancel_observed": bool(cancel_observed),
                "runner_shutting_down": bool(shutting_down), "artifact_ids": list(artifact_ids),
                "device_uuid": self.device.get("uuid")}

    def send_result(self, j: AttemptJournal, fence: JobFence) -> str:
        """POST the frozen ``result.json`` (same bytes on every resend) then ack; returns the journal stage."""
        commit = j.result
        assert commit is not None
        try:
            resp = self.client.result(fence, commit)
        except CoreError as exc:
            if exc.status in (401, 403) and exc.code not in ("RUNNER_MISMATCH", "TICKET_INVALID"):
                self._log(f"result of {fence.job_id} refused ({exc}); kept in the journal for a later resend")
                return j.stage.value
            j.record_discarded(f"result refused: {exc.message}", exc.code)
            level = "lost lease; result discarded" if exc.code == "LEASE_LOST" else "result refused"
            self._log(f"{level} for {fence.job_id} attempt {fence.attempt}: {exc}")
            return j.stage.value
        except CoreUnavailable as exc:
            self._log(f"result of {fence.job_id} not delivered ({exc}); kept in the journal")
            return j.stage.value
        j.record_response(resp)
        self._ack(j, fence)
        return j.stage.value

    def _ack(self, j: AttemptJournal, fence: JobFence) -> None:
        try:
            self.client.ack(fence)
        except CoreError as exc:
            if exc.status == 404 or exc.code == "LEASE_LOST":
                j.record_discarded(f"ack refused: {exc.message}", exc.code)
            else:
                self._log(f"ack of {fence.job_id} refused: {exc}")
            return
        except CoreUnavailable as exc:
            self._log(f"ack of {fence.job_id} not delivered: {exc}")
            return
        j.record_acked()


def main(settings: Any, *, once: bool = False, max_jobs: int | None = None) -> int:
    """``mbnas-runner run``: build the daemon (``CORE_URL_INSECURE`` refuses to start), install signal handlers, run."""
    daemon = RunnerDaemon(settings)
    daemon.install_signals()
    return daemon.run(once=once, max_jobs=max_jobs)
