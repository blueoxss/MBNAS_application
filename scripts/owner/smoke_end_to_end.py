#!/usr/bin/env python
"""End-to-end PILOT_SMOKE run against the LIVE dev stack (``scripts/dev_stack.py``): real API, real worker, real GPU.

Evidence type: LIVE uvicorn API + LIVE worker + REAL training/evaluation/search handlers on the host GPU, tiny real
data (PILOT_SMOKE subset). Values are smoke values, never research results.

Flow (each step is a recorded check; exit code 0 only when all pass):
  health (bearer; 401 without) -> PILOT_SMOKE preset (unchanged) -> validate (valid; resolved_diff, protocol_hash)
  -> POST /runs with an Idempotency-Key -> identical retry (same run_id) -> same key + other payload (409)
  -> SSE follower (auto-reconnect with after_seq, gap/duplicate accounting) + snapshot poller (monotonic last_seq,
     EVALUATED rows immutable) -> first candidate.evaluated == snapshot row
  -> pause as soon as a training job starts (worker stops the child at the next epoch boundary) -> PAUSED
  -> resume -> RUNNING -> API restart through ``dev_stack.py --restart api`` while the worker trains (same job,
     attempt and worker pid; last_seq grows while the API is down; reconnect replays without gaps)
  -> worker crash: SIGKILL the worker mid-stage (its training child dies with it), ``dev_stack.py --restart worker``;
     the expired lease is reclaimed, the same job runs as attempt+1 from the latest full-state checkpoint, NVML never
     shows two CUDA processes, the lost attempt is billed as failed work
  -> generation.completed for GA gen 0 L0 with the counts invariant -> run terminal (bounded wait)
  -> candidate details (blocks; parents: crossover 2, clone/mutation 1, random/baseline 0)
  -> gt/tp/fn/fp of one evaluation via bearer and via signed URL (sha256 == artifact row)
  -> export zip (contents listed, no secrets) -> evidence JSON
  ``/root/detection/MBNAS_runs/evidence/smoke_e2e_<ts>.json`` (``smoke_e2e_final_<ts>.json`` with ``--final-retrain``,
  ``smoke_e2e_crash_<ts>.json`` with ``--plan crash``).

``--plan crash`` is a reduced re-check of the crash path only: PILOT_SMOKE with no baselines and two GA candidates
(one generation), then health -> validate -> run -> worker crash/recovery -> terminal -> stream/contract summary.

``--final-retrain`` (after a COMPLETED run): ``POST /runs/{id}/final-retrain`` with a ``--final-budget-gpu-hours``
(default 0.25) budget and an Idempotency-Key -> identical retry returns the same child -> wait for the child run
(bounded by ``--final-max-wall-s``) -> final rows have rung FINAL, comparison_group ``final``, split_hash == the
final split manifest hash, panels labelled split=final_eval -> ``costs.final_gpu_seconds`` > 0 on the child AND the
parent -> export of both runs (child ``final_retrain.json`` with parent_run_id + finalist rule; parent
``final_retrain_children.json``).

``--prune-after [POLICY]`` (after everything else): applies the full-state retention policy
(``python -m mbnas_server.orchestration.retention``; POLICY keep_all | rung_end_only | last_rung_end_only, default
each run's resolved ``console.retention.full_state``) to the run (and the final child) and checks that candidate
detail shows the deleted payloads as PRUNED, never MISSING.

The token is read from the dotenv file and never printed or written.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import httpx
from dotenv import dotenv_values

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.orchestration.final_retrain import NO_FINALIST_MESSAGE
from mbnas_contracts.constants import DEFAULT_BASELINE14
from mbnas_contracts.util.atomic import atomic_write_json

SERVER_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = Path("/root/detection/MBNAS_runs/evidence")
IMAGE_KINDS = ("gt", "tp", "fn", "fp")
METRIC_KEYS = ("ap", "recall", "latency_ms", "size_mb", "fitness", "evaluation_hash", "checkpoint_path")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


class SseFollower(threading.Thread):
    """Follows ``/events`` like the web client: dedupe by seq, record gaps, reconnect with ``after_seq``."""

    def __init__(self, base: str, token: str, run_id: str) -> None:
        super().__init__(daemon=True)
        self.base, self.token, self.run_id = base, token, run_id
        self.events: dict[int, dict[str, Any]] = {}
        self.recv_ts: dict[int, float] = {}
        self.last_seq = 0
        self.duplicates = 0
        self.gaps: list[tuple[int, int]] = []
        self.snapshot_required: list[dict[str, Any]] = []
        self.connections: list[dict[str, Any]] = []
        self.heartbeats = 0
        self.cond = threading.Condition()
        self.stop = threading.Event()
        self.hold = threading.Event()  # set: drop the connection and stay disconnected until cleared
        self.connected = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            if self.hold.is_set():
                time.sleep(0.2)
                continue
            conn = {"after_seq": self.last_seq, "opened_at": time.time(), "first_seq": None, "events": 0}
            self.connections.append(conn)
            try:
                self._stream(conn)
                conn["closed"] = "server_closed"
            except (httpx.HTTPError, OSError) as exc:
                conn["closed"] = f"{type(exc).__name__}"
            conn["closed_at"] = time.time()
            self.connected.clear()
            if not self.stop.is_set() and not self.hold.is_set():
                time.sleep(1.0)

    def _stream(self, conn: dict[str, Any]) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{self.base}/api/v1/runs/{self.run_id}/events"
        frame: dict[str, str] = {}
        with httpx.Client(timeout=httpx.Timeout(10, read=40)) as client, \
                client.stream("GET", url, params={"after_seq": conn["after_seq"]}, headers=headers) as resp:
            conn["http"] = resp.status_code
            conn["content_type"] = resp.headers.get("content-type")
            if resp.status_code != 200:
                return
            self.connected.set()
            for line in resp.iter_lines():
                if self.stop.is_set() or self.hold.is_set():
                    return
                if line.startswith(":"):
                    self.heartbeats += line.startswith(": heartbeat")
                elif line == "":
                    if "data" in frame:
                        self._accept(frame, conn)
                    frame = {}
                else:
                    key, _, value = line.partition(":")
                    frame[key] = value[1:] if value.startswith(" ") else value

    def _accept(self, frame: dict[str, str], conn: dict[str, Any]) -> None:
        data = json.loads(frame["data"])
        if "id" not in frame:  # snapshot.required carries no id (cursor must not advance)
            self.snapshot_required.append(data)
            return
        seq = int(frame["id"])
        with self.cond:
            if conn["first_seq"] is None:
                conn["first_seq"] = seq
            conn["events"] += 1
            if seq <= self.last_seq:
                self.duplicates += 1
                return
            if seq != self.last_seq + 1:
                self.gaps.append((self.last_seq, seq))
            self.events[seq] = data
            self.recv_ts[seq] = time.time()
            self.last_seq = seq
            self.cond.notify_all()

    def find(self, pred: Callable[[dict[str, Any]], bool], after: int = 0) -> dict[str, Any] | None:
        with self.cond:
            for seq in sorted(s for s in self.events if s > after):
                if pred(self.events[seq]):
                    return self.events[seq]
        return None

    def wait(self, what: str, pred: Callable[[dict[str, Any]], bool], timeout: float, after: int = 0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hit = self.find(pred, after)
            if hit is not None:
                return hit
            with self.cond:
                self.cond.wait(timeout=2.0)
        raise TimeoutError(f"SSE: {what} not seen within {timeout:.0f}s")


class SnapshotPoller(threading.Thread):
    """Polls ``/snapshot``: last_seq must never decrease and EVALUATED rows must never change or disappear."""

    def __init__(self, http: httpx.Client, run_id: str, interval_s: float = 3.0) -> None:
        super().__init__(daemon=True)
        self.http, self.run_id, self.interval_s = http, run_id, interval_s
        self.polls = 0
        self.errors = 0
        self.last_seq = 0
        self.violations: list[str] = []
        self.evaluated: dict[str, tuple[Any, ...]] = {}
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.interval_s):
            try:
                snap = self.http.get(f"/api/v1/runs/{self.run_id}/snapshot", timeout=15).json()
            except (httpx.HTTPError, ValueError):
                self.errors += 1  # expected while the API restarts
                continue
            self.polls += 1
            if snap["last_seq"] < self.last_seq:
                self.violations.append(f"last_seq decreased {self.last_seq}->{snap['last_seq']}")
            self.last_seq = snap["last_seq"]
            rows = {c["id"]: c for c in snap["candidates"]}
            for rid, frozen in self.evaluated.items():
                row = rows.get(rid)
                if row is None or row["status"] != "EVALUATED" or _metrics(row) != frozen:
                    self.violations.append(f"EVALUATED row changed or vanished: {rid}")
            for rid, row in rows.items():
                if row["status"] == "EVALUATED" and rid not in self.evaluated:
                    self.evaluated[rid] = _metrics(row)


class GpuProcessSampler(threading.Thread):
    """Samples NVML compute processes on GPU 0 every 0.25 s (duplicate-CUDA-job detector)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        import pynvml

        pynvml.nvmlInit()
        self.nvml = pynvml
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.max_procs = 0
        self.samples = 0
        self.pids: set[int] = set()
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(0.25):
            procs = self.nvml.nvmlDeviceGetComputeRunningProcesses(self.handle)
            self.samples += 1
            self.max_procs = max(self.max_procs, len(procs))
            self.pids.update(p.pid for p in procs)


def _live_busy(workers: list[dict[str, Any]]) -> dict[str, Any]:
    """The busy worker with the freshest heartbeat (killed workers are reported 'lost' by the API)."""
    return max((w for w in workers if w["status"] == "busy"), key=lambda w: w.get("heartbeat_at") or "")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def _metrics(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(k) for k in METRIC_KEYS)


class E2E:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        env = dotenv_values(args.env_file)
        self.token = env.get("MBNAS_API_TOKEN") or ""
        self.signing_key = env.get("MBNAS_ARTIFACT_SIGNING_KEY") or ""
        if not self.token:
            raise SystemExit(f"MBNAS_API_TOKEN missing in {args.env_file}")
        self.base = args.base.rstrip("/")
        self.http = httpx.Client(base_url=self.base, headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        self.checks: list[dict[str, Any]] = []
        self.timings: dict[str, float] = {}
        self.t0 = time.time()
        self.report: dict[str, Any] = {}

    # -- bookkeeping ----------------------------------------------------------------------------------------------
    def check(self, name: str, passed: bool, **detail: Any) -> bool:
        self.checks.append({"name": name, "passed": bool(passed), "t_s": round(time.time() - self.t0, 1), **detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name} {json.dumps(detail, default=str)[:400]}", flush=True)
        return passed

    def mark(self, phase: str) -> None:
        self.timings[phase] = round(time.time() - self.t0, 1)
        print(f"-- {phase} at +{self.timings[phase]}s", flush=True)

    def contract(self, name: str, component: str, body: Any) -> bool:
        errs = contract_errors(component, body)
        return self.check(f"contract:{name}", not errs, errors=errs[:5])

    def run_status(self) -> str:
        return self.http.get(f"/api/v1/runs/{self.run_id}").json()["status"]

    def snapshot(self) -> dict[str, Any]:
        return self.http.get(f"/api/v1/runs/{self.run_id}/snapshot").json()

    # -- steps -----------------------------------------------------------------------------------------------------
    def health(self) -> None:
        h = self.http.get("/api/v1/health").json()
        ids = [e["id"] for e in h["registry"]]
        self.contract("health", "Health", h)
        self.check("health_registry_baseline14", all(i in ids for i in DEFAULT_BASELINE14) and len(DEFAULT_BASELINE14) == 14,
                   registry_entries=len(ids), default14_missing=[i for i in DEFAULT_BASELINE14 if i not in ids],
                   status={e["id"]: e.get("status") for e in h["registry"] if e["id"] in DEFAULT_BASELINE14})
        self.check("health_presets", {"PILOT_SMOKE", "DEFAULT_BASELINE14"} <= {p["id"] for p in h["presets"]},
                   presets=[p["id"] for p in h["presets"]])
        self.check("health_worker_live", h["worker_count"] >= 1, worker_count=h["worker_count"], status=h["status"])
        r = httpx.get(f"{self.base}/api/v1/health")
        self.check("health_unauthenticated_401", r.status_code == 401 and r.json().get("code") == "AUTH_REQUIRED",
                   status=r.status_code)
        self.report["health"] = {"status": h["status"], "commit_sha": h["commit_sha"], "registry_entries": len(ids),
                                 "devices": [{k: d.get(k) for k in ("name", "uuid", "worker_id")} for d in h["devices"]]}

    def validate_and_create(self) -> None:
        presets = self.http.get("/api/v1/protocols/presets").json()
        proto = next(p["protocol"] for p in presets["presets"] if p["id"] == "PILOT_SMOKE")
        if self.args.plan == "crash":  # reduced plan: the crash path only (documented in the module docstring)
            proto["console"]["baselines"] = []
            proto["console"]["search"].update(arms=["GA"], population=2, generations=1)  # contract minimum population
        val = self.http.post("/api/v1/protocols/validate", json={"protocol": proto}).json()
        self.contract("validate", "ValidateResponse", val)
        print("resolved_diff:", json.dumps(val.get("resolved_diff"), indent=1)[:4000], flush=True)
        print("protocol_hash:", val.get("protocol_hash"), flush=True)
        self.report["validation"] = {"protocol_hash": val["protocol_hash"], "validation_id": val.get("validation_id"),
                                     "resolved_diff": val.get("resolved_diff"), "warnings": val.get("warnings"),
                                     "errors": val["errors"]}
        if not self.check("validate_pilot_smoke", val["valid"] and bool(val["protocol_hash"]),
                          protocol_hash=val["protocol_hash"], errors=val["errors"][:3]):
            raise RuntimeError("PILOT_SMOKE did not validate")
        key = f"e2e-{int(self.t0)}"
        req = {"protocol": val["resolved"], "protocol_hash": val["protocol_hash"], "idempotency_key": key,
               "validation_id": val["validation_id"]}
        first = self.http.post("/api/v1/runs", json=req, headers={"Idempotency-Key": key})
        self.contract("run_created", "RunCreated", first.json())
        self.run_id = first.json()["run_id"]
        retry = self.http.post("/api/v1/runs", json=req, headers={"Idempotency-Key": key})
        self.check("run_created", first.status_code in (200, 201) and first.json()["status"] in ("QUEUED", "RUNNING"),
                   run_id=self.run_id, http=first.status_code)
        self.check("idempotent_retry_same_run", retry.status_code in (200, 201) and retry.json()["run_id"] == self.run_id,
                   http=retry.status_code, created=retry.json().get("created"))
        changed = json.loads(json.dumps(val["resolved"]))
        changed["console"]["extra"] = {**(changed["console"].get("extra") or {}), "e2e_note": "different payload"}
        conflict = self.http.post("/api/v1/runs", json={**req, "protocol": changed}, headers={"Idempotency-Key": key})
        self.check("idempotency_key_other_payload_409", conflict.status_code == 409
                   and conflict.json().get("code") == "IDEMPOTENCY_CONFLICT",
                   http=conflict.status_code, code=conflict.json().get("code"))
        runs = self.http.get("/api/v1/runs").json()
        same_key = [r for r in runs.get("runs", []) if r["id"] == self.run_id]
        self.check("one_run_for_key", len(same_key) == 1, runs_listed=len(runs.get("runs", [])))
        self.report["run_id"] = self.run_id
        self.report["protocol_hash"] = val["protocol_hash"]
        self.report["idempotency_key"] = key

    def first_evaluation_consistency(self, sse: SseFollower) -> None:
        ev = sse.wait("first candidate.evaluated", lambda e: e["type"] == "candidate.evaluated", self.args.phase_timeout_s)
        self.first_eval_seq = ev["seq"]
        self.mark("first_candidate_evaluated")
        snap = self.snapshot()
        row = next((c for c in snap["candidates"] if c["id"] == ev["payload"]["id"]), None)
        evaluated_events = {e["payload"]["id"] for s, e in sse.events.items()
                            if e["type"] == "candidate.evaluated" and s <= snap["last_seq"]}
        evaluated_snap = {c["id"] for c in snap["candidates"] if c["status"] == "EVALUATED"}
        self.check("first_evaluated_matches_snapshot",
                   row is not None and snap["last_seq"] >= ev["seq"] and row["status"] == "EVALUATED"
                   and _metrics(row) == _metrics(ev["payload"]) and evaluated_events <= evaluated_snap,
                   evaluation_id=ev["payload"]["id"], seq=ev["seq"], snapshot_last_seq=snap["last_seq"],
                   metrics=dict(zip(METRIC_KEYS, _metrics(ev["payload"]))))
        self.contract("snapshot_first_eval", "Snapshot", snap)
        point = ev["payload"]
        self.check("first_point_finite", all(isinstance(point.get(k), (int, float)) and math.isfinite(point[k])
                                             for k in ("ap", "recall", "latency_ms", "size_mb", "fitness")),
                   latency_source=point.get("latency_source"), device=point.get("device"))

    def pause_resume(self, sse: SseFollower) -> None:
        after = self.first_eval_seq
        # the job start (TRAINING with no epoch progress yet): the stop request then reaches the child before its
        # first epoch ends, so the pause lands strictly inside the stage at an epoch boundary
        start = sse.wait("a training job starting", lambda e: e["type"] == "candidate.updated"
                         and e["payload"].get("status") == "TRAINING" and not (e["payload"].get("progress") or {}).get("epoch")
                         and e["seq"] >= sse.last_seq - 2, self.args.phase_timeout_s, after=after)
        paused = self.http.post(f"/api/v1/runs/{self.run_id}/pause", json={"reason": "e2e smoke pause"})
        self.check("pause_requested", paused.status_code == 200 and paused.json()["status"] in ("PAUSE_REQUESTED", "PAUSED"),
                   status=paused.json().get("status"), training_row=start["payload"]["id"])
        sse.wait("run PAUSE_REQUESTED", lambda e: e["type"] == "run.updated"
                 and e["payload"]["status"] == "PAUSE_REQUESTED", 60, after=after)
        pev = sse.wait("run PAUSED", lambda e: e["type"] == "run.updated" and e["payload"]["status"] == "PAUSED",
                       self.args.phase_timeout_s, after=after)
        self.mark("paused")
        snap = self.snapshot()
        stopped = [c for c in snap["candidates"] if c["status"] == "PAUSED"]
        busy = [w for w in snap["workers"] if w["status"] == "busy"]
        detail = self.http.get(f"/api/v1/runs/{self.run_id}/candidates/{start['payload']['candidate_id']}").json()
        ckpts = [c for c in detail.get("checkpoints", []) if c.get("kind") == "training_full"]
        self.check("paused_worker_confirmed", snap["run"]["status"] == "PAUSED" and not busy,
                   paused_event_seq=pev["seq"], paused_rows=[c["id"] for c in stopped],
                   boundary="mid_stage_epoch" if stopped else "stage_end",
                   full_state_checkpoints=[(c.get("epoch"), c.get("sha256", "")[:19]) for c in ckpts][-3:])
        self.report["pause"] = {"training_row": start["payload"]["id"], "paused_rows": [c["id"] for c in stopped],
                                "checkpoints": ckpts[-3:]}
        resumed = self.http.post(f"/api/v1/runs/{self.run_id}/resume")
        self.check("resumed", resumed.status_code == 200 and resumed.json()["status"] in ("RUNNING", "RESUMING"),
                   status=resumed.json().get("status"), http=resumed.status_code)
        sse.wait("run RUNNING after resume", lambda e: e["type"] == "run.updated" and e["payload"]["status"] == "RUNNING",
                 60, after=pev["seq"])
        if stopped:  # the paused training must continue from its full-state checkpoint, not restart from scratch
            rid = stopped[0]["id"]
            ev = sse.wait("paused row trains again", lambda e: e["type"] == "candidate.updated" and e["payload"]["id"] == rid
                          and e["payload"].get("status") in ("TRAINING", "EVALUATED", "EVALUATING"),
                          self.args.phase_timeout_s, after=pev["seq"])
            self.check("paused_row_resumed", True, row=rid, status=ev["payload"].get("status"))
        self.mark("resumed")

    def api_restart(self, sse: SseFollower) -> None:
        after = sse.last_seq
        sse.wait("training progress before restart", lambda e: e["type"] == "worker.updated"
                 and e["payload"].get("status") == "busy" and e["payload"].get("job_id"), self.args.phase_timeout_s,
                 after=after)
        sse.hold.set()  # the browser loses its stream when the API goes away; reconnect only after the restart
        deadline = time.monotonic() + 60
        while sse.connected.is_set() and time.monotonic() < deadline:
            time.sleep(0.2)
        cursor = sse.last_seq
        before = self.snapshot()
        w0 = _live_busy(before["workers"])
        seq0 = before["last_seq"]
        argv = [sys.executable, str(SERVER_ROOT / "scripts" / "dev_stack.py"), "--env-file", self.args.env_file,
                "--restart", "api"]
        t = time.time()
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)
        restart_s = round(time.time() - t, 1)
        out = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {"raw": proc.stdout[-500:]}
        self.check("api_restarted", proc.returncode == 0, restart_s=restart_s, old_pid=out.get("old_pid"),
                   new_pid=out.get("new_pid"))
        self.mark("api_restarted")
        after_restart = self.snapshot()
        w1 = next((w for w in after_restart["workers"] if w["id"] == w0["id"]), {})
        continued = w1.get("job_id") == w0["job_id"] and w1.get("attempt") == w0.get("attempt")
        finished = any(c["candidate_id"] == w0.get("candidate_id") and c["status"] == "EVALUATED"
                       and c.get("rung") == w0.get("rung") for c in after_restart["candidates"])
        self.check("worker_unaffected_by_api_restart", (continued or finished) and w1.get("status") != "offline",
                   worker=w0["id"], job_before=w0["job_id"], job_after=w1.get("job_id"),
                   attempt_before=w0.get("attempt"), attempt_after=w1.get("attempt"), worker_pid=w1.get("pid"),
                   child_pid_before=w0.get("child_pid"), child_pid_after=w1.get("child_pid"),
                   finished_during_restart=finished and not continued)
        # the worker keeps committing events after the restart (seq0 = last_seq seen just before it) while the client
        # is still disconnected at ``cursor``
        deadline = time.monotonic() + 180
        while self.snapshot()["last_seq"] <= seq0 and time.monotonic() < deadline:
            time.sleep(1.0)
        after_restart = self.snapshot()
        self.check("seq_grew_across_restart", after_restart["last_seq"] > seq0 >= cursor, before=seq0,
                   cursor=cursor, after=after_restart["last_seq"])
        # reconnect with after_seq = last received seq; the replay must start at cursor+1 and be contiguous
        target = after_restart["last_seq"]
        sse.hold.clear()
        deadline = time.monotonic() + 90
        while sse.last_seq < target and time.monotonic() < deadline:
            time.sleep(0.5)
        reconnects = [c for c in sse.connections if c["opened_at"] >= t]
        self.check("sse_reconnect_after_seq_no_gap", sse.last_seq >= target and not sse.gaps
                   and reconnects and reconnects[0]["after_seq"] == cursor and reconnects[0]["first_seq"] == cursor + 1,
                   reconnects=[{k: c.get(k) for k in ("after_seq", "first_seq", "events", "http")} for c in reconnects],
                   replayed_to=target, gaps=sse.gaps, last_seq=sse.last_seq)
        progress_after = sse.find(lambda e: e["type"] in ("candidate.updated", "candidate.evaluated", "worker.updated"), after=seq0)
        self.check("worker_progress_after_restart", progress_after is not None,
                   seq=(progress_after or {}).get("seq"))
        self.report["api_restart"] = {"restart_s": restart_s, "last_seq_before": seq0,
                                      "last_seq_after": after_restart["last_seq"], "worker_job": w0["job_id"],
                                      "attempt": w0.get("attempt"), "old_pid": out.get("old_pid"),
                                      "new_pid": out.get("new_pid")}

    def worker_crash(self, sse: SseFollower) -> None:
        def prog(e: dict[str, Any]) -> dict[str, Any]:
            return e["payload"].get("progress") or {}

        # an epoch boundary that is not the stage end: a full state for that epoch exists, the stage is unfinished
        ev = sse.wait("mid-stage epoch progress", lambda e: e["type"] == "candidate.updated"
                      and e["payload"].get("status") == "TRAINING" and prog(e).get("epoch")
                      and prog(e)["epoch"] < prog(e).get("epoch_target_total", 0) and e["seq"] >= sse.last_seq - 2,
                      self.args.phase_timeout_s, after=sse.last_seq)
        w0 = _live_busy(self.snapshot()["workers"])
        row_id = ev["payload"]["id"]
        gpu = GpuProcessSampler()
        gpu.start()
        t = time.time()
        os.kill(int(w0["pid"]), signal.SIGKILL)  # the worker python process (not its flock wrapper)
        child = w0.get("child_pid")
        deadline = time.monotonic() + 15
        while child and _pid_alive(int(child)) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.check("worker_killed_child_died", not (child and _pid_alive(int(child))), worker_pid=w0["pid"],
                   child_pid=child, job=w0["job_id"], attempt=w0.get("attempt"), killed_at_epoch=prog(ev).get("epoch"),
                   epoch_target_total=prog(ev).get("epoch_target_total"), row=row_id)
        argv = [sys.executable, str(SERVER_ROOT / "scripts" / "dev_stack.py"), "--env-file", self.args.env_file,
                "--restart", "worker"]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)
        out = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {"raw": proc.stdout[-500:]}
        self.check("worker_restarted", proc.returncode == 0, new_pid=out.get("new_pid"))
        self.mark("worker_killed_and_restarted")
        retry = sse.wait("reclaimed job running as the next attempt", lambda e: e["type"] == "worker.updated"
                         and e["payload"].get("job_id") == w0["job_id"]
                         and (e["payload"].get("attempt") or 0) > (w0.get("attempt") or 0), 600, after=ev["seq"])
        done = sse.wait("crashed row evaluated", lambda e: e["type"] == "candidate.evaluated"
                        and e["payload"]["id"] == row_id, 900, after=ev["seq"])
        gpu.stop.set()
        gpu.join(timeout=5)
        self.mark("crashed_job_recovered")
        detail = self.http.get(f"/api/v1/runs/{self.run_id}/candidates/{ev['payload']['candidate_id']}").json()
        full = [c for c in detail.get("checkpoints", []) if c.get("kind") == "training_full"]
        failed_s = (detail.get("cost") or {}).get("failed_gpu_seconds") or (detail.get("cost") or {}).get("failed")
        self.check("crash_recovered_same_job_next_attempt", done["payload"].get("attempt") == retry["payload"]["attempt"]
                   and done["payload"].get("job_id") in (None, w0["job_id"]), row=row_id, job=w0["job_id"],
                   attempt_before=w0.get("attempt"), attempt_after=retry["payload"]["attempt"],
                   reclaim_after_s=round(sse.recv_ts[retry["seq"]] - t, 1),
                   full_state_epochs=[c.get("epoch") for c in full])
        self.check("no_duplicate_cuda_job", gpu.max_procs <= 1, max_concurrent_cuda_processes=gpu.max_procs,
                   samples=gpu.samples, pids_seen=sorted(gpu.pids)[:10])
        self.check("lost_attempt_billed_as_failed", bool(failed_s and failed_s > 0), candidate_cost=detail.get("cost"))
        self.report["worker_crash"] = {"row": row_id, "job": w0["job_id"], "attempt_before": w0.get("attempt"),
                                       "attempt_after": retry["payload"]["attempt"], "killed_worker_pid": w0["pid"],
                                       "killed_child_pid": child, "new_worker_pid": out.get("new_pid"),
                                       "reclaim_after_s": round(sse.recv_ts[retry["seq"]] - t, 1),
                                       "candidate_cost": detail.get("cost"), "max_cuda_processes": gpu.max_procs}

    def generation_gen0(self, sse: SseFollower) -> None:
        ev = sse.wait("GA gen0 L0 generation.completed", lambda e: e["type"] == "generation.completed"
                      and e["payload"]["arm"] == "GA" and e["payload"]["generation"] == 0
                      and e["payload"]["rung"] == "L0", self.args.phase_timeout_s)
        g = ev["payload"]
        counts = sum(g.get(k, 0) for k in ("evaluated_count", "failed_count", "not_admitted_count", "pending_count"))
        self.check("generation_ga_g0_L0_closed", g["complete"] and g.get("planning_closed") and g.get("pending_count") == 0
                   and counts == g.get("planned_count"), generation=g)
        self.mark("ga_gen0_L0_completed")

    def wait_terminal(self, sse: SseFollower) -> str:
        deadline = time.monotonic() + self.args.max_wall_s
        status = self.run_status()
        while status not in TERMINAL and time.monotonic() < deadline:
            time.sleep(5)
            status = self.run_status()
        self.mark(f"run_{status.lower()}")
        self.check("run_completed", status == "COMPLETED", status=status, wall_s=round(time.time() - self.t0))
        return status

    def lineage(self, snap: dict[str, Any]) -> None:
        cands = sorted({c["candidate_id"] for c in snap["candidates"]})
        rows: list[dict[str, Any]] = []
        bad: list[str] = []
        for cid in cands:
            resp = self.http.get(f"/api/v1/runs/{self.run_id}/candidates/{cid}")
            d = resp.json()
            if resp.status_code != 200:  # recorded as a failed check; the remaining phases still run and report
                bad.append(f"{cid}: HTTP {resp.status_code} {d.get('code')} {(d.get('errors') or [{}])[0].get('field')}")
                continue
            if contract_errors("CandidateDetail", d):
                bad.append(f"{cid}: contract")
            op = (d.get("lineage") or {}).get("operation") or (d.get("lineage") or {}).get("kind")
            n = len(d["parents"])
            want = {"crossover": 2, "clone": 1, "elite_clone": 1, "elite": 1, "mutation": 1, "random": 0,
                    "baseline": 0, "seed": 0}.get(op)
            if d["arm"] in ("IndependentRandom", "Baseline") and n != 0:
                bad.append(f"{cid}: {d['arm']} has {n} parents")
            if want is not None and n != want:
                bad.append(f"{cid}: {op} has {n} parents")
            if not d["blocks"] and d["arm"] != "Baseline":  # baselines are registry models, not searched genotypes
                bad.append(f"{cid}: no blocks")
            rows.append({"candidate_id": cid, "arm": d["arm"], "generation": d.get("generation"), "operation": op,
                         "parents": d["parents"], "blocks": len(d["blocks"]), "status": d["candidate_status"],
                         "architecture_hash": d["architecture_hash"]})
        crossover = [r for r in rows if r["arm"] == "GA" and r["generation"] == 1 and len(r["parents"]) == 2]
        random_g1 = [r for r in rows if r["arm"] == "IndependentRandom" and r["generation"] == 1]
        self.check("lineage_parent_counts", not bad and bool(crossover) and bool(random_g1)
                   and all(not r["parents"] for r in random_g1), problems=bad[:10],
                   ga_g1_crossover=[r["candidate_id"] for r in crossover],
                   random_g1=[r["candidate_id"] for r in random_g1],
                   operations=Counter(f"{r['arm']}:{r['operation']}" for r in rows))
        self.report["candidates"] = rows

    def artifacts(self, snap: dict[str, Any]) -> None:
        row = next(c for c in snap["candidates"] if c["status"] == "EVALUATED"
                   and set(IMAGE_KINDS) <= {a["kind"] for a in c.get("artifacts", [])})
        first_sample = sorted(a["sample_id"] for a in row["artifacts"] if a["kind"] in IMAGE_KINDS)[0]
        all_images = [a for a in row["artifacts"] if a["kind"] in IMAGE_KINDS]
        arts = [a for a in all_images if a["sample_id"] == first_sample]
        out = []
        ok = True
        for a in arts:
            signed = httpx.get(a["url"]) if a.get("auth_mode") == "signed_url" else None
            bearer = self.http.get(f"/api/v1/runs/{self.run_id}/artifacts/{a['id']}")
            s_sha = hashlib.sha256(signed.content).hexdigest() if signed is not None and signed.status_code == 200 else None
            b_sha = hashlib.sha256(bearer.content).hexdigest() if bearer.status_code == 200 else None
            good = s_sha == b_sha == a["sha256"] and bearer.headers.get("x-artifact-sha256") == a["sha256"]
            ok &= good and bool(bearer.content[:8] == b"\x89PNG\r\n\x1a\n")
            out.append({"id": a["id"], "kind": a["kind"], "sample_id": a.get("sample_id"), "sha256": a["sha256"],
                        "signed_http": signed.status_code if signed is not None else None, "bearer_http": bearer.status_code,
                        "bytes": len(bearer.content), "evaluation_id": a.get("evaluation_id"),
                        "matching": a.get("matching")})
        nosig = httpx.get(f"{self.base}/api/v1/runs/{self.run_id}/artifacts/{arts[0]['id']}")
        forged = httpx.get(arts[0]["url"].replace("sig=", "sig=0"))
        same_sample = len({a["sample_id"] for a in out}) == 1 and len({a["evaluation_id"] for a in out}) == 1
        self.check("artifacts_four_kinds_bearer_and_signed", ok and same_sample and sorted(a["kind"] for a in out)
                   == sorted(IMAGE_KINDS), evaluation_id=row["id"], sample_id=first_sample,
                   samples=sorted({a["sample_id"] for a in all_images}),
                   artifacts=[(a["kind"], a["sha256"][:12]) for a in out])
        self.check("artifact_without_auth_rejected", nosig.status_code == 401 and forged.status_code == 403,
                   no_auth=nosig.status_code, forged_sig=forged.status_code)
        self.report["artifacts"] = {"evaluation_id": row["id"], "files": out}

    def export(self, run_id: str | None = None, *, label: str = "export",
               extra: tuple[str, ...] = ()) -> zipfile.ZipFile | None:
        run_id = run_id or self.run_id
        r = self.http.get(f"/api/v1/runs/{run_id}/export")
        if r.status_code != 200:
            self.check(f"{label}_bundle", False, http=r.status_code, run_id=run_id)
            return None
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names)
        needed = {"protocol/resolved.json", "evaluations.csv", "lineage.json", "cost_ledger.csv", "checkpoints.json",
                  "events.jsonl", "provenance.json", "environment.lock.json", "SHA256SUMS", *extra}
        self.check(f"{label}_bundle", needed <= set(names)
                   and hashlib.sha256(r.content).hexdigest() == r.headers.get("x-bundle-sha256"),
                   run_id=run_id, files=len(names), missing=sorted(needed - set(names)), bytes=len(r.content))
        secrets_found = [s for s in (self.token, self.signing_key) if s and s.encode() in blob]
        self.check(f"{label}_has_no_secrets", not secrets_found)
        path = EVIDENCE_DIR / f"{run_id}-export.zip"
        path.write_bytes(r.content)
        self.report[label] = {"path": str(path), "sha256": r.headers.get("x-bundle-sha256"), "bytes": len(r.content),
                              "files": [{"name": i.filename, "bytes": i.file_size} for i in zf.infolist()]}
        return zf

    def final_retrain(self) -> None:
        """POST final-retrain, wait for the child, check rows/costs/exports (see module docstring)."""
        key = f"e2e-final-{int(self.t0)}"
        body = {"idempotency_key": key, "budget_gpu_hours": self.args.final_budget_gpu_hours,
                "reason": "smoke_end_to_end --final-retrain"}
        url = f"/api/v1/runs/{self.run_id}/final-retrain"
        first = self.http.post(url, json=body, headers={"Idempotency-Key": key})
        created = first.json()
        self.report["final_retrain"] = {"request": body, "http": first.status_code, "response": created}
        if first.status_code == 409 and created.get("code") == "NO_FINALIST":
            # the documented "no finalist" outcome: reported, never substituted by a lower rung, no child created
            parent = self.http.get(f"/api/v1/runs/{self.run_id}").json()
            self.check("final_retrain_no_finalist_reported", created.get("message") == NO_FINALIST_MESSAGE
                       and not parent.get("final_retrain_runs"), message=created.get("message"),
                       children=parent.get("final_retrain_runs"))
        if not self.check("final_retrain_created", first.status_code == 200 and created.get("parent_run_id") == self.run_id
                          and created.get("finalist_count", 0) >= 1, http=first.status_code, response=created):
            return
        self.contract("final_retrain_created", "RunCreated", created)
        child = self.final_child_id = created["run_id"]
        retry = self.http.post(url, json=body, headers={"Idempotency-Key": key})
        self.check("final_retrain_idempotent_retry", retry.status_code == 200 and retry.json().get("run_id") == child,
                   http=retry.status_code, created=retry.json().get("created"))
        self.mark("final_retrain_created")
        deadline = time.monotonic() + self.args.final_max_wall_s
        status = self.http.get(f"/api/v1/runs/{child}").json()["status"]
        while status not in TERMINAL and time.monotonic() < deadline:
            time.sleep(5)
            status = self.http.get(f"/api/v1/runs/{child}").json()["status"]
        self.mark(f"final_retrain_{status.lower()}")
        run = self.http.get(f"/api/v1/runs/{child}").json()
        parent = self.http.get(f"/api/v1/runs/{self.run_id}").json()
        self.contract("final_child_run", "Run", run)
        self.check("final_retrain_child_completed", status == "COMPLETED" and run.get("kind") == "FINAL_RETRAIN"
                   and run.get("parent_run_id") == self.run_id and run.get("protocol_hash") == parent["protocol_hash"],
                   status=status, kind=run.get("kind"), reason_code=run.get("reason_code"),
                   finalists=[(f.get("arm"), f.get("candidate_id"), f.get("ap"), f.get("latency_ms"))
                              for f in run.get("finalists") or []])
        final_hash = parent["resolved_protocol"]["dataset"]["final_evaluation_manifest_hash"]
        snap = self.http.get(f"/api/v1/runs/{child}/snapshot").json()
        self.contract("final_child_snapshot", "Snapshot", snap)
        rows = [c for c in snap["candidates"] if c["status"] == "EVALUATED"]
        self.check("final_rows_rung_group_split", bool(rows) and all(
            r["rung"] == "FINAL" and r["comparison_group"] == "final" and r["split_hash"] == final_hash
            and r["arm"] == "Final" and r.get("gate") is None for r in rows), final_split_hash=final_hash,
                   rows=[{k: r.get(k) for k in ("id", "rung", "comparison_group", "split_hash", "ap", "recall",
                                                "latency_ms", "latency_scope", "size_mb", "fitness")} for r in rows])
        panels = [a for r in rows for a in r.get("artifacts", []) if a["kind"] in IMAGE_KINDS]
        self.check("final_panels_labelled_final_eval", bool(panels) and all(
            (a.get("matching") or {}).get("split") == "final_eval" and a.get("split_hash") == final_hash for a in panels),
                   panels=len(panels), samples=sorted({a.get("sample_id") for a in panels}))
        child_cost = (run.get("costs") or {}).get("final_gpu_seconds")
        parent_cost = (parent.get("costs") or {}).get("final_gpu_seconds")
        self.check("final_costs_child_and_parent", bool(child_cost and child_cost > 0 and parent_cost
                                                        and parent_cost >= child_cost),
                   child_final_gpu_seconds=child_cost, parent_final_gpu_seconds=parent_cost,
                   parent_children=parent.get("final_retrain_runs"))
        zc = self.export(child, label="final_child_export", extra=("final_retrain.json",))
        zp = self.export(self.run_id, label="parent_export_with_children", extra=("final_retrain_children.json",))
        if zc is not None and zp is not None:
            fr = json.loads(zc.read("final_retrain.json"))
            kids = json.loads(zp.read("final_retrain_children.json"))
            self.check("final_exports_link_parent_and_child", fr.get("parent_run_id") == self.run_id
                       and bool(fr.get("selection_rule")) and child in [k["run_id"] for k in kids],
                       selection_rule=fr.get("selection_rule"), children=[k["run_id"] for k in kids])
        self.report["final_retrain"].update({"child_run_id": child, "status": status, "rows": [
            {k: r.get(k) for k in ("id", "candidate_id", "name", "ap", "recall", "latency_ms", "latency_scope", "size_mb",
                                   "fitness", "evaluation_hash", "split_hash")} for r in rows],
            "child_costs": run.get("costs"), "parent_costs": parent.get("costs"), "finalists": run.get("finalists")})

    def prune_after(self) -> None:
        """Apply the retention policy to the run (and the final child) and check PRUNED rendering."""
        runs = [self.run_id] + ([self.final_child_id] if getattr(self, "final_child_id", None) else [])
        argv = [sys.executable, "-m", "mbnas_core.orchestration.retention", "--env-file", self.args.env_file]
        for rid in runs:
            argv += ["--run-id", rid]
        if self.args.prune_after:
            argv += ["--policy", self.args.prune_after]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False)
        lines = proc.stdout.strip().splitlines()
        reports = json.loads(lines[-1]) if lines and lines[-1].startswith("[") else []
        self.check("prune_after_applied", proc.returncode == 0 and len(reports) == len(runs),
                   exit_code=proc.returncode, stderr=proc.stderr[-500:],
                   summary=[{k: r.get(k) for k in ("run_id", "policy", "pruned", "bytes_freed")} for r in reports])
        # every checkpoint of every candidate (pruned by the worker during the run or by this call) must read PRESENT
        # or PRUNED in candidate detail — MISSING would be an unexplained loss; the rows this call pruned must be PRUNED
        problems, states_seen, policies = [], {"PRESENT": 0, "PRUNED": 0, "MISSING": 0}, set()
        for rep in reports:
            policies.add(rep.get("policy"))
            for cand in rep.get("candidates", []):
                d = self.http.get(f"/api/v1/runs/{rep['run_id']}/candidates/{cand['candidate_id']}").json()
                states = {c["id"]: c.get("state") for c in d.get("checkpoints", [])}
                for state in states.values():
                    states_seen[state] = states_seen.get(state, 0) + 1
                problems += [f"{cand['candidate_id']}:{i}=MISSING" for i, st in states.items() if st == "MISSING"]
                problems += [f"{cand['candidate_id']}:{p['checkpoint_id']} not PRUNED" for p in cand.get("pruned", [])
                             if states.get(p["checkpoint_id"]) != "PRUNED"]
        deleting = policies - {"keep_all"}
        self.check("pruned_checkpoints_shown_as_pruned", not problems and (not deleting or states_seen["PRUNED"] > 0),
                   policies=sorted(p for p in policies if p), states=states_seen, problems=problems[:10])
        self.report["prune_after"] = {"policy_override": self.args.prune_after or None, "reports": [
            {k: r.get(k) for k in ("run_id", "policy", "pruned", "bytes_freed")} for r in reports]}

    def summarize(self, sse: SseFollower, poller: SnapshotPoller, snap: dict[str, Any]) -> None:
        seqs = sorted(sse.events)
        self.check("sse_contiguous_no_gaps", seqs == list(range(1, len(seqs) + 1)) and not sse.gaps,
                   received=len(seqs), last_seq=sse.last_seq, duplicates=sse.duplicates, gaps=sse.gaps[:5],
                   connections=len(sse.connections), snapshot_required=len(sse.snapshot_required))
        bad = [s for s, e in sse.events.items() if contract_errors("Event", e)]
        self.check("sse_events_contract", not bad, invalid=bad[:5])
        self.check("snapshot_poller_consistent", not poller.violations and poller.polls > 0, polls=poller.polls,
                   errors_during_restart=poller.errors, violations=poller.violations[:5],
                   evaluated_rows_tracked=len(poller.evaluated))
        self.contract("snapshot_final", "Snapshot", snap)
        points = [c for c in snap["candidates"] if c["status"] == "EVALUATED"]
        nonpoints = [c for c in snap["candidates"] if c["status"] != "EVALUATED"]
        self.check("no_null_points", all(c.get("fitness") is not None and c.get("latency_ms") is not None for c in points),
                   points=len(points), other_rows=Counter(c["status"] for c in nonpoints))
        gens = snap["generations"]
        broken = [g for g in gens if sum(g.get(k, 0) for k in ("evaluated_count", "failed_count", "not_admitted_count",
                                                                  "pending_count")) != g.get("planned_count")]
        self.check("generation_counts_invariant", not broken and bool(gens), generations=len(gens),
                   broken=[g.get("group_key") for g in broken])
        statuses = [e["payload"]["status"] for _, e in sorted(sse.events.items()) if e["type"] == "run.updated"]
        self.report.update({
            "event_counts": dict(Counter(e["type"] for e in sse.events.values())),
            "last_seq": sse.last_seq, "sse_connections": sse.connections, "sse_heartbeats": sse.heartbeats,
            "run_status_sequence": [s for i, s in enumerate(statuses) if i == 0 or statuses[i - 1] != s],
            "points": [{k: c.get(k) for k in ("id", "candidate_id", "arm", "family", "generation", "rung", "epochs", "ap",
                                              "raw_metrics", "recall", "latency_ms", "size_mb", "fitness", "latency_source",
                                              "latency_scope", "device", "precision", "evaluation_hash",
                                              "comparison_group", "checkpoint_path")} for c in points],
            "non_point_rows": [{k: c.get(k) for k in ("id", "status", "reason_code")} for c in nonpoints],
            "generations": gens, "costs": snap["run"].get("costs"), "budget": snap["run"].get("budget"),
            "run": {k: snap["run"].get(k) for k in ("id", "status", "namespace", "execution_mode", "purpose", "preset",
                                                    "created_at", "updated_at", "reason_code", "source_commit")},
        })

    # -- driver ------------------------------------------------------------------------------------------------------
    def flow(self) -> None:
        self.health()
        self.validate_and_create()
        self.mark("run_created")
        sse = SseFollower(self.base, self.token, self.run_id)
        poller = SnapshotPoller(self.http, self.run_id)
        sse.start()
        poller.start()
        if self.args.plan == "crash":
            try:
                self.worker_crash(sse)
                status = self.wait_terminal(sse)
                time.sleep(3)
                self.summarize(sse, poller, self.snapshot())
                self.after_completion(status)
            finally:
                sse.stop.set()
                poller.stop.set()
            return
        try:
            self.first_evaluation_consistency(sse)
            self.pause_resume(sse)
            self.api_restart(sse)
            self.worker_crash(sse)
            self.generation_gen0(sse)
            status = self.wait_terminal(sse)
            time.sleep(3)
            snap = self.snapshot()
            self.summarize(sse, poller, snap)
            if status == "COMPLETED":
                self.lineage(snap)
                self.artifacts(snap)
                self.export()
            self.after_completion(status)
        finally:
            sse.stop.set()
            poller.stop.set()

    def after_completion(self, status: str) -> None:
        """Optional phases after the search run: final retraining, then the retention policy."""
        if self.args.final_retrain:
            if status == "COMPLETED":
                self.final_retrain()
            else:
                self.check("final_retrain_created", False, reason=f"search run ended {status}; final retraining "
                                                                   "needs a COMPLETED run")
        if self.args.prune_after is not None:
            self.prune_after()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=str(SERVER_ROOT / ".env"))
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--max-wall-s", type=float, default=3300, help="bound for the whole run to reach a terminal state")
    parser.add_argument("--phase-timeout-s", type=float, default=1800)
    parser.add_argument("--plan", choices=("full", "crash"), default="full")
    parser.add_argument("--final-retrain", action="store_true",
                        help="after a COMPLETED run: POST final-retrain, wait for the child, check rows/costs/exports")
    parser.add_argument("--final-budget-gpu-hours", type=float, default=0.25)
    parser.add_argument("--final-max-wall-s", type=float, default=1800)
    parser.add_argument("--prune-after", nargs="?", const="", default=None,
                        choices=("", "keep_all", "rung_end_only", "last_rung_end_only"), metavar="POLICY",
                        help="apply the full-state retention policy at the end (default: each run's resolved policy)")
    args = parser.parse_args(argv)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    e2e = E2E(args)
    error = None
    try:
        e2e.flow()
    except Exception as exc:  # noqa: BLE001 - recorded in the evidence
        error = f"{type(exc).__name__}: {exc}"
        e2e.check("flow_completed", False, error=error)
    passed = error is None and all(c["passed"] for c in e2e.checks)
    evidence = {
        "evidence_type": "LIVE API + LIVE worker (dev_stack) + REAL handlers on the host A100; PILOT_SMOKE tiny real "
                         "data; smoke values, not research results",
        "generated_at": stamp, "plan": args.plan, "final_retrain": args.final_retrain, "prune_after": args.prune_after,
        "passed": passed, "error": error, "base": args.base,
        "timings_s": e2e.timings,
        "checks": e2e.checks, **e2e.report,
        "logs": {"api": "/root/detection/MBNAS_runs/logs/dev_stack/api.log",
                 "worker": "/root/detection/MBNAS_runs/logs/dev_stack/worker.log"},
    }
    tag = "crash_" if args.plan == "crash" else "final_" if args.final_retrain else ""
    out = EVIDENCE_DIR / f"smoke_e2e_{tag}{stamp}.json"
    atomic_write_json(out, evidence)
    print(json.dumps({"passed": passed, "evidence": str(out), "run_id": e2e.report.get("run_id"),
                      "checks": f"{sum(c['passed'] for c in e2e.checks)}/{len(e2e.checks)}"}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
