#!/usr/bin/env python
"""API-only smoke against a LIVE API process + a LIVE worker process with SYNTHETIC stub handlers.

What is real: the uvicorn API process (``app_from_env``), the worker process and its per-job child processes, the
SQLite DB, SSE over HTTP, signed artifact URLs, the export zip. What is synthetic: every job handler
(``tests/worker/stub_handlers.py`` — no model, no GPU work; metrics are labelled ``raw_metrics.synthetic``/``latency_scope=synthetic_stub``).

``--real-handlers`` instead runs the REAL search/training/evaluation handlers on the GPU (worker wrapped in
``flock /ABSOLUTE/PATH/TO/MBNAS_runs/locks/gpu.lock``) with a deliberately tiny search-only plan: GA arm, population 2,
one generation, rungs 4 -> 5 epochs on the PILOT_SMOKE subset (baselines need probed registry entries).

Flow: health -> presets -> validate (PILOT_SMOKE preset, temp output root, baselines removed because the registry
is probed per output root) -> run (+ idempotent retry) -> SSE -> training progress -> pause -> PAUSED at an epoch
boundary -> resume -> RUNNING -> first evaluation outcome -> signed artifact -> cancel -> CANCELLED (real mode: run
to COMPLETED, then cancel must be 409) -> SSE reconnect with Last-Event-ID -> snapshot/events contract checks ->
export bundle. Everything lives under
``/ABSOLUTE/PATH/TO/MBNAS_runs/smoke_api_only/<timestamp>``; the report goes to ``<that dir>/report.json`` and
``/ABSOLUTE/PATH/TO/MBNAS_runs/logs/smoke_api_only_<timestamp>.json``. Secrets are generated per run, passed to the
children through the environment only, and never written to disk or stdout. Exit 0 only when every check passed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from mbnas_server.api.contract_check import contract_errors
from mbnas_contracts.util.atomic import atomic_write_json

SERVER_ROOT = Path(__file__).resolve().parents[3]  # TARGET_ROOT (WP-1 layout)
STUB_DIR = SERVER_ROOT / "tests" / "_shared"  # WP-1G: stub_handlers.py moved from tests/worker
RUNS_ROOT = Path("/ABSOLUTE/PATH/TO/MBNAS_runs")


class Smoke:
    def __init__(self, root: Path, epoch_s: float, real: bool = False) -> None:
        self.root = root
        self.real = real
        self.timeout = 1800.0 if real else 120.0
        self.token = secrets.token_urlsafe(24)
        self.key = secrets.token_urlsafe(32)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
        self.env.update({"MBNAS_API_TOKEN": self.token, "MBNAS_ARTIFACT_SIGNING_KEY": self.key,
                         "MBNAS_DB_PATH": str(root / "db" / "mbnas.sqlite3"), "MBNAS_OUTPUT_ROOT": str(root),
                         "MBNAS_OUTPUT_ROOTS": json.dumps([str(root)]), "MBNAS_LOG_DIR": str(root / "logs"),
                         "MBNAS_HOST": "127.0.0.1", "MBNAS_PORT": str(self.port), "MBNAS_HEARTBEAT_S": "2",
                         "MBNAS_LEASE_TTL_S": "60" if real else "20"})
        if not real:
            self.env.update({"PYTHONPATH": str(STUB_DIR), "MBNAS_JOB_HANDLERS_MODULE": "stub_handlers",
                             "MBNAS_STUB_EPOCH_S": str(epoch_s)})
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.checks: list[dict[str, Any]] = []
        self.http = httpx.Client(base_url=self.base, headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        self.events: list[dict[str, Any]] = []
        self._sse_stop = threading.Event()

    # -- bookkeeping ----------------------------------------------------------------------------------------
    def check(self, name: str, passed: bool, **detail: Any) -> bool:
        self.checks.append({"name": name, "passed": bool(passed), **detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name} {json.dumps(detail, default=str)[:300]}", flush=True)
        return passed

    def contract(self, name: str, component: str, body: Any) -> bool:
        errs = contract_errors(component, body)
        return self.check(f"contract:{name}", not errs, errors=errs[:5])

    def spawn(self, name: str, argv: list[str]) -> None:
        log = open(self.root / "logs" / f"{name}.log", "ab")  # noqa: SIM115
        self.procs[name] = subprocess.Popen(argv, env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, start_new_session=True)

    def stop_all(self) -> None:
        self._sse_stop.set()
        for name in ("worker", "api"):
            proc = self.procs.get(name)
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)

    def wait(self, what: str, fn: Any, timeout: float | None = None) -> Any:
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            time.sleep(0.2)
        raise TimeoutError(what)

    def run_status(self, run_id: str) -> str:
        return self.http.get(f"/api/v1/runs/{run_id}").json()["status"]

    # -- SSE --------------------------------------------------------------------------------------------------
    def sse(self, run_id: str, last_event_id: int | None) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        frame: dict[str, str] = {}
        with httpx.Client(timeout=httpx.Timeout(30, read=60)) as c, \
                c.stream("GET", f"{self.base}/api/v1/runs/{run_id}/events", headers=headers) as resp:
            self.sse_headers = dict(resp.headers)
            for line in resp.iter_lines():
                if self._sse_stop.is_set():
                    return
                if line == "":
                    if "data" in frame:
                        self.events.append({"id": frame.get("id"), "event": frame.get("event"),
                                            "data": json.loads(frame["data"])})
                    frame = {}
                elif not line.startswith(":"):
                    key, _, value = line.partition(":")
                    frame[key] = value[1:] if value.startswith(" ") else value

    def outcomes(self) -> list[dict[str, Any]]:
        """Committed evaluations and evaluation failures seen on the stream (failures are not points)."""
        return [e["data"]["payload"] for e in self.events if e["event"] == "candidate.evaluated" or (
            e["event"] == "candidate.updated" and e["data"]["payload"]["status"] in ("EVAL_FAILED", "TRAIN_FAILED"))]

    # -- flow -------------------------------------------------------------------------------------------------
    def flow(self) -> None:
        py = sys.executable
        self.spawn("api", [py, "-m", "uvicorn", "--factory", "mbnas_server.api.app:app_from_env", "--host", "127.0.0.1",
                           "--port", str(self.port), "--log-level", "warning"])
        worker = [py, "-m", "mbnas_runner", "--poll-s", "0.2", "--kill-grace-s", "30"]
        if self.real:  # serialise GPU use with other latency/training measurements on this host
            (RUNS_ROOT / "locks").mkdir(parents=True, exist_ok=True)
            worker = ["flock", "-w", "3600", str(RUNS_ROOT / "locks" / "gpu.lock"), *worker]
        self.spawn("worker", worker)
        self.wait("api up", lambda: _ok(self.http, "/api/v1/health"), 120)
        health = self.wait("worker heartbeat", lambda: (h := self.http.get("/api/v1/health").json())["worker_count"] and h)
        self.check("health", health["worker_count"] == 1, status=health["status"], commit_sha=health["commit_sha"])
        self.contract("health", "Health", health)
        r = self.http.get("/api/v1/health", headers={"Authorization": "Bearer wrong"})
        self.check("auth:wrong_token_403", r.status_code == 403)
        self.check("auth:no_token_401", httpx.get(f"{self.base}/api/v1/health").status_code == 401)
        presets = self.http.get("/api/v1/protocols/presets").json()
        self.contract("presets", "PresetList", presets)
        proto = next(p["protocol"] for p in presets["presets"] if p["id"] == "PILOT_SMOKE")
        proto["console"]["paths"]["output_root"] = str(self.root / "out")
        proto["console"]["baselines"] = []
        # tiny plan (both modes): L0 = 4 epochs so a pause lands strictly inside the first training stage
        weights = [r["fitness_weights"] for r in proto["rungs"]]
        proto["rungs"] = [{"id": "L0", "epoch_target_total": 4, "fitness_weights": weights[0],
                           "promotion": {"to": "L1", "score_ge": 0.0, "ap_ge": 0.0}},
                          {"id": "L1", "epoch_target_total": 5, "fitness_weights": weights[-1], "promotion": None}]
        proto["training"]["scheduler_horizon_epochs"] = 5
        proto["console"]["baseline_epoch_target"] = 5
        proto["console"]["search"].update(arms=["GA"] if self.real else ["GA", "IndependentRandom"], population=2,
                                          generations=1)
        val = self.http.post("/api/v1/protocols/validate", json={"protocol": proto}).json()
        self.contract("validate", "ValidateResponse", val)
        if not self.check("validate", val["valid"], errors=val["errors"][:3], protocol_hash=val["protocol_hash"]):
            return
        req = {"protocol": val["resolved"], "protocol_hash": val["protocol_hash"], "idempotency_key": "smoke-1",
               "validation_id": val["validation_id"]}
        created = self.http.post("/api/v1/runs", json=req, headers={"Idempotency-Key": "smoke-1"}).json()
        retry = self.http.post("/api/v1/runs", json=req).json()
        run_id = created["run_id"]
        self.check("run_created", created["status"] in ("QUEUED", "RUNNING"), run_id=run_id)
        self.check("idempotent_retry_same_run", retry["run_id"] == run_id and retry["created"] is False)
        sse = threading.Thread(target=self.sse, args=(run_id, None), daemon=True)
        sse.start()
        self.wait("training progress", lambda: any(
            e["event"] == "candidate.updated" and e["data"]["payload"]["status"] == "TRAINING"
            and _room_to_pause(e["data"]["payload"].get("progress") or {}) for e in self.events))
        self.check("sse_headers", self.sse_headers.get("content-type", "").startswith("text/event-stream")
                   and self.sse_headers.get("x-accel-buffering") == "no")
        paused = self.http.post(f"/api/v1/runs/{run_id}/pause", json={"reason": "smoke"}).json()
        self.check("pause_requested", paused["status"] in ("PAUSE_REQUESTED", "PAUSED"), status=paused["status"])
        self.wait("PAUSED", lambda: self.run_status(run_id) == "PAUSED")
        snap = self.http.get(f"/api/v1/runs/{run_id}/snapshot").json()
        self.contract("snapshot_paused", "Snapshot", snap)
        stopped = [c["id"] for c in snap["candidates"] if c["status"] == "PAUSED"]
        self.check("paused_at_epoch_boundary", bool(stopped), paused_rows=stopped)
        resumed = self.http.post(f"/api/v1/runs/{run_id}/resume").json()
        self.check("resumed", resumed.get("status") == "RUNNING", status=resumed.get("status"))
        self.wait("evaluation outcome", lambda: self.outcomes())
        snap = self.http.get(f"/api/v1/runs/{run_id}/snapshot").json()
        signed = next(a for c in snap["candidates"] for a in c["artifacts"] if a["kind"] in ("gt", "tp", "fn", "fp"))
        img = httpx.get(signed["url"])  # no Authorization: the signature authorises
        self.check("artifact_signed_fetch", img.status_code == 200 and
                   hashlib.sha256(img.content).hexdigest() == img.headers.get("x-artifact-sha256") == signed["sha256"],
                   kind=signed["kind"], mime=img.headers.get("content-type"), auth_mode=signed["auth_mode"])
        if self.real:  # tiny real plan: let it finish, then a cancel of a terminal run must be refused
            final_status = self.wait("terminal", lambda: (st := self.run_status(run_id)) in ("COMPLETED", "FAILED") and st)
            self.check("run_terminal", final_status == "COMPLETED", status=final_status,
                       outcomes=[(o["candidate_id"], o["rung"], o["status"], o.get("reason_code")) for o in self.outcomes()])
            r = self.http.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "after completion"})
            self.check("cancel_terminal_is_409", r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION")
            expected = ["QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "RESUMING", "RUNNING", final_status]
        else:
            cancel = self.http.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "smoke done"}).json()
            self.check("cancel_requested", cancel["status"] in ("CANCEL_REQUESTED", "CANCELLED"), status=cancel["status"])
            self.wait("CANCELLED", lambda: self.run_status(run_id) == "CANCELLED")
            expected = ["QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "RESUMING", "RUNNING", "CANCEL_REQUESTED",
                        "CANCELLED"]
        time.sleep(1.0)
        self._sse_stop.set()
        last = int(self.events[-1]["id"])
        reconnect: list[dict[str, Any]] = []
        self.events, reconnect = reconnect, self.events  # replay the tail with Last-Event-ID
        self._sse_stop.clear()
        t2 = threading.Thread(target=self.sse, args=(run_id, last - 5), daemon=True)
        t2.start()
        self.wait("replay", lambda: len(self.events) >= 5, 60)
        self._sse_stop.set()
        replay_ids = [int(e["id"]) for e in self.events[:5]]
        self.check("sse_reconnect_last_event_id", replay_ids == list(range(last - 4, last + 1)), ids=replay_ids)
        seqs = [int(e["id"]) for e in reconnect]
        self.check("sse_seq_contiguous_from_1", seqs == list(range(1, len(seqs) + 1)), count=len(seqs))
        bad = [e["data"]["seq"] for e in reconnect if contract_errors("Event", e["data"])]
        self.check("sse_events_contract", not bad, invalid_seqs=bad[:5])
        types = {e["event"] for e in reconnect}
        needed = {"run.updated", "candidate.updated", "worker.updated"} | (set() if self.real else {"candidate.evaluated"})
        self.check("sse_event_types", needed <= types, types=sorted(types))
        final = self.http.get(f"/api/v1/runs/{run_id}/snapshot").json()
        self.contract("snapshot_final", "Snapshot", final)
        statuses = [e["data"]["payload"]["status"] for e in reconnect if e["event"] == "run.updated"]
        self.check("run_status_sequence", _subsequence(expected, statuses), statuses=statuses)
        self.check("costs_measured", (final["run"]["costs"] or {}).get("actual_gpu_seconds", 0) > 0,
                   costs=final["run"]["costs"])
        export = self.http.get(f"/api/v1/runs/{run_id}/export")
        zf = zipfile.ZipFile(io.BytesIO(export.content))
        blob = b"".join(zf.read(n) for n in zf.namelist())
        self.check("export_bundle", export.status_code == 200 and {"protocol/resolved.json", "evaluations.csv",
                   "lineage.json", "cost_ledger.csv", "checkpoints.json", "events.jsonl", "provenance.json",
                   "environment.lock.json", "SHA256SUMS"} <= set(zf.namelist()), files=len(zf.namelist()),
                   bytes=len(export.content), sha256=export.headers.get("x-bundle-sha256"))
        self.check("export_has_no_secrets", self.token.encode() not in blob and self.key.encode() not in blob)
        (self.root / f"{run_id}-export.zip").write_bytes(export.content)
        self.run_id = run_id


def _ok(client: httpx.Client, path: str) -> bool:
    try:
        return client.get(path).status_code == 200
    except httpx.HTTPError:
        return False


def _room_to_pause(progress: dict[str, Any]) -> bool:
    """At least one epoch done and two still to go, so the stop request is seen before the stage target."""
    epoch, target = progress.get("epoch", 0), progress.get("epoch_target_total", 0)
    return epoch >= 1 and target - epoch >= 2


def _subsequence(expected: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(any(a == e for a in it) for e in expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--epoch-s", type=float, default=1.5, help="stub seconds per epoch")
    parser.add_argument("--real-handlers", action="store_true", help="real GPU handlers, tiny search-only plan")
    args = parser.parse_args(argv)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root = RUNS_ROOT / ("smoke_api_real" if args.real_handlers else "smoke_api_only") / stamp
    (root / "logs").mkdir(parents=True, exist_ok=True)
    smoke = Smoke(root, args.epoch_s, real=args.real_handlers)

    def _terminate(signum: int, _frame: Any) -> None:  # SIGTERM/SIGINT: still stop the spawned API/worker
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    error = None
    try:
        smoke.flow()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - recorded in the report
        error = f"{type(exc).__name__}: {exc}"
        smoke.check("flow_completed", False, error=error)
    finally:
        smoke.stop_all()
    passed = all(c["passed"] for c in smoke.checks) and error is None
    evidence = ("LIVE API + LIVE worker processes + REAL handlers on the host GPU (tiny search-only smoke plan)"
                if args.real_handlers else
                "LIVE API + LIVE worker processes, SYNTHETIC stub job handlers (no GPU training)")
    report = {"evidence_type": evidence,
              "root": str(root), "run_id": getattr(smoke, "run_id", None), "passed": passed,
              "checks": smoke.checks, "logs": {n: str(root / "logs" / f"{n}.log") for n in ("api", "worker")}}
    atomic_write_json(root / "report.json", report)
    name = "smoke_api_real" if args.real_handlers else "smoke_api_only"
    atomic_write_json(RUNS_ROOT / "logs" / f"{name}_{stamp}.json", report)
    print(json.dumps({"passed": passed, "report": str(root / "report.json"),
                      "checks": f"{sum(c['passed'] for c in smoke.checks)}/{len(smoke.checks)}"}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
