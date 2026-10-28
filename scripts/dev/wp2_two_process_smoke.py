#!/usr/bin/env python3
"""WP-2 real two-process smoke (WP-2 contract section 4): core app + owner worker + runner daemon as real processes.

What it does (every process is started here and stopped here; only its own pids are signalled):

1. builds a SYNTHETIC 4-image YOLO dataset under ``$MBNAS_TEST_ROOT/wp2_smoke/<stamp>/core_data`` and a byte copy of it
   under ``.../runner_data`` (the runner reaches it only through ``MBNAS_RUNNER_DATASET_MOUNTS``
   ``{core_data: runner_data}``), plus the yolo11 YAML with ``nc: 1`` as a user model configuration;
2. starts the core app (``uvicorn --factory mbnas_core.api.app:app_from_env``) on 127.0.0.1 and an ephemeral port
   (or ``--port``), with ``MBNAS_CORE_*`` settings below the smoke root and ``MBNAS_CORE_EXPOSE_LEGACY_API=true``:
   since WP-3A the core serves the console only at ``/core/v1/console`` (scoped principals) and mounts the
   static-bearer ``/api/v1`` owner debug path only on request; this smoke drives that owner debug path (the user
   path through the gateway is ``scripts/dev/wp3_three_process_smoke.py``);
3. issues a server-scope, a runner-scope and an admin principal with the ``mbnas-core-principal`` CLI;
4. starts the owner worker (``python -m mbnas_core.owner_worker --owner``) and the runner daemon
   (``python -m mbnas_runner run``, ``MBNAS_RUNNER_*`` settings, token file, dataset mounts);
5. step (a): a generation-only protocol (evaluation OFF, CPU) through the console API -> run COMPLETED, all jobs
   PROPOSE_GENERATION run by the owner worker;
6. step (b): an explicit single-model training run (POST /api/v1/training-runs, user source = yolo11n YAML, 1 epoch,
   64 px, ``validation_enabled=false``) -> the BASELINE_TRAIN job is claimed by the runner, executed by the real
   ultralytics backend on the runner's CPU, its result committed; the run snapshot is read and every job / evaluation /
   checkpoint row is recorded. The explicit-training recipe pins ``device=cuda:0`` (core validation refuses anything
   else, DEVICE_UNSUPPORTED), so on a CPU-only runner (``CUDA_VISIBLE_DEVICES=""``) the outcome is whatever the runner
   backend does with that device; the script records it and passes the step only if the job SUCCEEDED;
7. step (c): stops runner (SIGTERM: it deregisters), owner worker and core; verifies they exited.

Exit 0 only when every step passed. The step log (UTC times, request ids, pids, exit codes, durations) is written to
``--evidence`` (default docs/internal/evidence/WP2/two_process_smoke.txt). No GPU (``CUDA_VISIBLE_DEVICES=""``), no
network beyond 127.0.0.1, nothing written outside the smoke root and the evidence file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # TARGET_ROOT
PY = sys.executable
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "PAUSED"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Smoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        test_root = Path(os.environ.get("MBNAS_TEST_ROOT") or ROOT / "runtime" / "test_root")
        self.root = Path(args.root) if args.root else test_root / "wp2_smoke" / stamp
        self.log: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.procs: dict[str, subprocess.Popen] = {}
        self.api_token = "smoke-api-token-" + uuid.uuid4().hex
        self.port = args.port or self._free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.tokens: dict[str, str] = {}
        self.active_runner = "runner"

    # -- plumbing ------------------------------------------------------------------------------------------------
    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def note(self, msg: str) -> None:
        line = f"{now()} {msg}"
        self.log.append(line)
        print(line, flush=True)

    def step(self, name: str, ok: bool, t0: float, **detail: Any) -> bool:
        self.steps.append({"step": name, "ok": ok, "duration_s": round(time.monotonic() - t0, 2), **detail})
        self.note(f"STEP {name}: {'PASS' if ok else 'FAIL'} ({time.monotonic() - t0:.1f} s) "
                  f"{json.dumps(detail, sort_keys=True, default=str)[:1500]}")
        return ok

    def base_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_") or k.startswith("MBNAS_TEST_")}
        env.update(CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1", MBNAS_HOME=str(ROOT / "runtime"),
                   YOLO_CONFIG_DIR=str(self.root / "ultralytics_cfg"), YOLO_OFFLINE="1", YOLO_AUTOINSTALL="false",
                   OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
        return env

    def core_env(self) -> dict[str, str]:
        c = self.root / "core"
        env = self.base_env()
        env.update({"MBNAS_CORE_API_TOKEN": self.api_token,
                    "MBNAS_CORE_ARTIFACT_SIGNING_KEY": "smoke-signing-key-" + "k" * 24,
                    "MBNAS_CORE_TICKET_KEY": "smoke-ticket-key-" + "t" * 24,
                    "MBNAS_CORE_DB_PATH": str(c / "db" / "mbnas.sqlite3"),
                    "MBNAS_CORE_OUTPUT_ROOT": str(c / "runs"), "MBNAS_CORE_OUTPUT_ROOTS": json.dumps([str(c / "runs")]),
                    "MBNAS_CORE_DATASET_ROOTS": json.dumps([str(self.root / "core_data")]),
                    "MBNAS_CORE_MODEL_YAML_ROOTS": json.dumps([str(self.root / "uploads")]),
                    "MBNAS_CORE_WEIGHTS_DIR": str(self.root / "weights"), "MBNAS_CORE_LOG_DIR": str(c / "logs"),
                    "MBNAS_CORE_HOST": "127.0.0.1", "MBNAS_CORE_PORT": str(self.port),
                    "MBNAS_CORE_LEASE_TTL_S": "60", "MBNAS_CORE_HEARTBEAT_S": "2",
                    "MBNAS_CORE_MIN_FREE_DISK_GB": "0", "MBNAS_CORE_DEVICE_SOURCE": self.args.device_source,
                    # owner local debugging mount /api/v1 (static bearer), off by default since WP-3A
                    "MBNAS_CORE_EXPOSE_LEGACY_API": "true"})
        return env

    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        r = self.root / name
        env = self.base_env()
        env.update({"MBNAS_RUNNER_CORE_URL": self.base, "MBNAS_RUNNER_TOKEN_FILE": str(self.root / "secrets" / f"{name}.token"),
                    "MBNAS_RUNNER_RUNNER_ID": runner_id, "MBNAS_RUNNER_WORK_ROOT": str(r / "work"),
                    "MBNAS_RUNNER_OUTPUT_ROOTS": json.dumps([str(r / "work")]),
                    "MBNAS_RUNNER_DATASET_ROOTS": json.dumps([str(self.root / "runner_data")]),
                    "MBNAS_RUNNER_DATASET_MOUNTS": json.dumps({str(self.root / "core_data"): str(self.root / "runner_data")}),
                    "MBNAS_RUNNER_WEIGHTS_DIR": str(self.root / "weights"), "MBNAS_RUNNER_LOG_DIR": str(r / "logs"),
                    "MBNAS_RUNNER_POLL_S": "0.5", "MBNAS_RUNNER_KILL_GRACE_S": "5"})
        return env

    def spawn(self, name: str, argv: list[str], env: dict[str, str]) -> subprocess.Popen:
        logf = self.root / "logs" / f"{name}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(argv, env=env, cwd=str(ROOT), stdout=open(logf, "a"), stderr=subprocess.STDOUT,
                             start_new_session=True)
        self.procs[name] = p
        self.note(f"spawned {name} pid={p.pid} argv={' '.join(argv)} log={logf}")
        return p

    def http(self, method: str, path: str, body: Any = None, token: str | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any, str]:
        import httpx

        rid = "smoke-" + uuid.uuid4().hex[:16]
        h = {"Authorization": f"Bearer {token or self.api_token}", "X-Request-ID": rid, **(headers or {})}
        r = httpx.request(method, self.base + path, json=body, headers=h, timeout=60)
        try:
            data = r.json()
        except ValueError:
            data = r.text
        echoed = r.headers.get("X-Request-ID")
        self.note(f"HTTP {method} {path} -> {r.status_code} request_id={rid} echoed={echoed == rid}")
        return r.status_code, data, rid

    def wait(self, what: str, fn: Any, timeout: float, interval: float = 1.0) -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for name, p in self.procs.items():
                if p.poll() is not None and name in ("core", "owner") or (
                        name.startswith("runner") and p.poll() is not None and not getattr(p, "_stopped", False)
                        and name == self.active_runner):
                    raise RuntimeError(f"{name} (pid {p.pid}) exited with {p.returncode} while waiting for {what}")
            try:
                last = fn()
            except Exception as exc:  # noqa: BLE001 - polled again
                last = f"{type(exc).__name__}: {exc}"
            if last and not isinstance(last, str):
                return last
            time.sleep(interval)
        raise TimeoutError(f"{what} not reached within {timeout} s (last: {str(last)[:300]})")

    # -- setup ---------------------------------------------------------------------------------------------------
    def build_inputs(self) -> dict[str, str]:
        sys.path.append(str(ROOT / "tests" / "_shared"))
        import importlib.util

        spec = importlib.util.spec_from_file_location("wp2_smoke_datasets", ROOT / "tests" / "_shared" / "datasets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ds = mod.build_dataset(self.root / "core_data", "smoke4", counts={"train": 4, "val": 4})
        shutil.copytree(self.root / "core_data", self.root / "runner_data")
        import ultralytics

        text = (Path(ultralytics.__file__).parent / "cfg" / "models" / "11" / "yolo11.yaml").read_text()
        (self.root / "uploads").mkdir(parents=True, exist_ok=True)
        (self.root / "uploads" / "yolo11n.yaml").write_text(text.replace("nc: 80", "nc: 1\nscale: n"))
        (self.root / "weights").mkdir(parents=True, exist_ok=True)
        self.note(f"synthetic dataset {ds['dataset_yaml']} (4 train + 4 val PNG, 1 class); runner copy "
                  f"{self.root / 'runner_data'}; model yaml {self.root / 'uploads' / 'yolo11n.yaml'}")
        return ds

    def principals(self, which: tuple = (("server", "server", []), ("runner", "runner", ["--runner-id", "smoke-runner-1"]),
                                         ("admin", "admin", []))) -> None:
        cli = str(Path(PY).parent / "mbnas-core-principal")
        for name, kind, extra in which:
            t0 = time.monotonic()
            p = subprocess.run([cli, "create", "--kind", kind, *extra], env=self.core_env(), cwd=str(ROOT),
                               capture_output=True, text=True, timeout=120)
            ok = p.returncode == 0
            if ok:
                out = json.loads(p.stdout)
                self.tokens[name] = out["token"]
                pid_ = out["principal"]["id"]
            self.step(f"principal-{name}", ok, t0, exit_code=p.returncode, principal_id=pid_ if ok else None,
                      stderr=p.stderr[-300:] if not ok else "")
            if not ok:
                raise RuntimeError(f"principal create {kind} failed")
            if kind == "runner":
                tok = self.root / "secrets" / f"{name}.token"
                tok.parent.mkdir(parents=True, exist_ok=True)
                tok.write_text(self.tokens[name] + "\n")
                os.chmod(tok, 0o600)

    # -- steps ---------------------------------------------------------------------------------------------------
    def start_core(self) -> None:
        t0 = time.monotonic()
        self.spawn("core", [PY, "-B", "-m", "uvicorn", "--factory", "mbnas_core.api.app:app_from_env", "--host",
                            "127.0.0.1", "--port", str(self.port), "--log-level", "warning"], self.core_env())
        health = self.wait("core /api/v1/health", lambda: (lambda r: r[1] if r[0] == 200 else None)(
            self.http("GET", "/api/v1/health")), timeout=120, interval=1.0)
        self.step("core-start", True, t0, pid=self.procs["core"].pid, port=self.port,
                  health={k: health.get(k) for k in ("status", "worker_count", "contract_version")})

    def start_workers(self) -> None:
        t0 = time.monotonic()
        self.spawn("owner", [PY, "-B", "-m", "mbnas_core.owner_worker", "--owner", "--poll-s", "0.5", "--worker-id",
                             "smoke-owner-1"], self.core_env())
        self.spawn("runner", [PY, "-B", "-m", "mbnas_runner", "run"], self.runner_env())

        row = self.wait("runner registration", lambda: self.registered("smoke-runner-1"), timeout=180, interval=1.0)
        self.step("owner+runner-start", True, t0, owner_pid=self.procs["owner"].pid, runner_pid=self.procs["runner"].pid,
                  runner={k: row.get(k) for k in ("id", "status", "kinds", "device", "data_mounts")},
                  runtime_verification=(row.get("runtime") or {}).get("verification"))

    def registered(self, runner_id: str) -> dict[str, Any] | None:
        code, body, _ = self.http("GET", "/core/v1/owner/runners", token=self.tokens["admin"])
        rows = [r for r in (body or {}).get("runners", []) if r["id"] == runner_id] if code == 200 else []
        return rows[0] if rows and rows[0]["status"] == "ONLINE" else None

    def run_status(self, run_id: str) -> dict[str, Any] | None:
        code, body, _ = self.http("GET", f"/api/v1/runs/{run_id}")
        return body if code == 200 and isinstance(body, dict) else None

    def db_rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        """Read-only view of the core DB (verification only; the processes under test never see this connection)."""
        import sqlite3

        db = self.root / "core" / "db" / "mbnas.sqlite3"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def snapshot(self, run_id: str) -> dict[str, Any]:
        code, body, rid = self.http("GET", f"/api/v1/runs/{run_id}/snapshot")
        return body if code == 200 else {"error": body, "request_id": rid}

    def step_generation(self, ds: dict[str, str]) -> bool:
        t0 = time.monotonic()
        policy = {"schema_version": "nas-policy/1",
                  "console": {"paths": {"output_root": str(self.root / "core" / "runs")},
                              "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                         "seeds": [7], "candidate_count": 2, "max_attempts": 12,
                                         "allow_duplicates": False, "arms": ["GA"]}}}
        code, v, rid_v = self.http("POST", "/api/v1/protocols/validate", {"protocol": policy})
        if code != 200 or not v.get("valid"):
            return self.step("a-generation-only", False, t0, validate_status=code, body=v, request_id=rid_v)
        code, created, rid_c = self.http("POST", "/api/v1/runs", {"protocol": v["resolved"],
                                                                  "protocol_hash": v["protocol_hash"],
                                                                  "validation_id": v["validation_id"],
                                                                  "idempotency_key": "wp2-smoke-gen-" + uuid.uuid4().hex[:8]})
        if code != 200:
            return self.step("a-generation-only", False, t0, create_status=code, body=created, request_id=rid_c)
        run_id = created["run_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        snap = self.snapshot(run_id)
        jobs = [(j["kind"], j["status"], j["attempt"], j["lease_owner"]) for j in
                self.db_rows("SELECT kind, status, attempt, lease_owner FROM jobs WHERE run_id = ?", (run_id,))]
        cands = len(snap.get("generated_candidates", []))
        ok = run["status"] == "COMPLETED" and jobs and all(k == "PROPOSE_GENERATION" and s == "SUCCEEDED"
                                                           for k, s, _, _ in jobs) and cands == 2
        return self.step("a-generation-only", bool(ok), t0, run_id=run_id, run_status=run["status"], jobs=jobs,
                         candidates=cands, request_ids={"validate": rid_v, "create": rid_c},
                         workers=[(w.get("id"), w.get("status")) for w in snap.get("workers", [])])

    def step_training(self, ds: dict[str, str], *, name: str = "b-remote-cpu-training", runner_id: str = "smoke-runner-1",
                      validation_enabled: bool = False, note: str = "") -> bool:
        t0 = time.monotonic()
        sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        policy = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
        policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                  warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        policy["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
        body = {"source": {"kind": "user", "model_path": str(self.root / "uploads" / "yolo11n.yaml")},
                "protocol": policy, "mode": "scratch", "validation_enabled": validation_enabled,
                "idempotency_key": "wp2-smoke-train-" + uuid.uuid4().hex[:8]}
        code, created, rid_c = self.http("POST", "/api/v1/training-runs", body)
        if code != 200:
            return self.step(name, False, t0, create_status=code, body=created, request_id=rid_c)
        run_id = created["run_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        snap = self.snapshot(run_id)
        jobs = []
        for j in self.db_rows("SELECT id, kind, status, attempt, lease_owner, error_json, result_json FROM jobs "
                              "WHERE run_id = ?", (run_id,)):
            err = json.loads(j.pop("error_json") or "null") or {}
            res = json.loads(j.pop("result_json") or "null") or {}
            jobs.append({**j, "reason_code": err.get("reason_code") or res.get("reason_code"),
                         "message": str(err.get("message") or res.get("message") or "")[:600],
                         "outputs_keys": sorted((res.get("outputs") or {}).keys())})
        evals = [{k: e.get(k) for k in ("evaluation_id", "id", "status", "rung", "candidate_id", "evaluation_status")}
                 for e in snap.get("candidates", [])]
        cks = self.db_rows("SELECT id, kind, epoch, state, path FROM checkpoints WHERE run_id = ?", (run_id,))
        locs = self.db_rows("SELECT object_kind, store, runner_id, verified_by, relpath FROM artifact_locations "
                            "WHERE run_id = ?", (run_id,))
        costs = self.db_rows("SELECT kind, attempt, gpu_seconds, meta_json FROM cost_ledger WHERE run_id = ?", (run_id,))
        attempts = self.db_rows("SELECT job_id, attempt, runner_id, state, acked_at FROM idempotent_responses")
        claimed_by_runner = any(a["runner_id"] == runner_id for a in attempts
                                if a["job_id"] in {j["id"] for j in jobs})
        runner_rows = self.http("GET", "/core/v1/owner/runners", token=self.tokens["admin"])[1]
        evaluated = any("EVALUATED" in (e.get("status"), e.get("evaluation_status")) for e in evals)
        ok = (run["status"] == "COMPLETED" and claimed_by_runner
              and any(j["kind"] == "BASELINE_TRAIN" and j["status"] == "SUCCEEDED" for j in jobs)
              and (evaluated or not validation_enabled))
        return self.step(name, bool(ok), t0, run_id=run_id, run_status=run["status"], jobs=jobs,
                         evaluations=evals, evaluated_row_present=evaluated, checkpoints=cks,
                         artifact_locations=locs, costs=costs, result_commits=attempts,
                         claimed_by_runner=claimed_by_runner, request_ids={"create": rid_c},
                         validation_enabled=validation_enabled, note=note,
                         runners=[{k: r.get(k) for k in ("id", "status", "last_seen_at")}
                                  for r in (runner_rows or {}).get("runners", [])])

    def step_stub_runner(self, ds: dict[str, str]) -> bool:
        """SYNTHETIC extra step (b2): the same explicit training request with validation enabled, executed by a second
        runner process whose job child uses the stub handlers (tests/_shared/stub_handlers.py: synthetic metrics). Shows
        the claim -> artifacts -> result -> EVALUATED commit over real HTTP between real processes; it is NOT a training
        measurement."""
        t0 = time.monotonic()
        real = self.procs.get("runner")
        if real is not None and real.poll() is None:
            real.send_signal(signal.SIGTERM)
            real.wait(timeout=60)
        self.note(f"real runner stopped for step b2 (exit {real.returncode if real else None})")
        self.principals((("runner_stub", "runner", ["--runner-id", "smoke-runner-stub"]),))
        env = self.runner_env("runner_stub", "smoke-runner-stub")
        env.update(PYTHONPATH=str(ROOT / "tests" / "_shared"), MBNAS_JOB_HANDLERS_MODULE="stub_handlers",
                   MBNAS_STUB_EPOCH_S="0.05")
        self.active_runner = "runner_stub"
        self.spawn("runner_stub", [PY, "-B", "-m", "mbnas_runner", "run"], env)
        self.wait("stub runner registration", lambda: self.registered("smoke-runner-stub"), timeout=180)
        self.step("b2-stub-runner-start", True, t0, pid=self.procs["runner_stub"].pid)
        return self.step_training(ds, name="b2-remote-stub-evaluated", runner_id="smoke-runner-stub",
                                  validation_enabled=True,
                                  note="SYNTHETIC: stub handlers on the runner (no model is trained; metrics synthetic)")

    def stop_all(self) -> bool:
        t0 = time.monotonic()
        codes = {}
        for name in ("runner_stub", "runner", "owner", "core"):
            p = self.procs.get(name)
            if p is None:
                continue
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self.note(f"{name} pid={p.pid} did not stop on SIGTERM: SIGKILL")
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=30)
            codes[name] = p.returncode
        alive = [n for n, p in self.procs.items() if p.poll() is None]
        return self.step("c-stop-own-processes", not alive, t0, exit_codes=codes, still_alive=alive,
                         pids={n: p.pid for n, p in self.procs.items()})

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# WP-2 two-process smoke ({started} .. {now()}, {dur:.1f} s) result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B scripts/dev/wp2_two_process_smoke.py {' '.join(sys.argv[1:])}",
                 f"# cwd: {ROOT}  interpreter: {PY}  CUDA_VISIBLE_DEVICES=''  core: {self.base}",
                 f"# smoke root: {self.root}  process logs: {self.root / 'logs'}",
                 "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str) for s in self.steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        for name in ("core", "owner", "runner", "runner_stub"):
            logf = self.root / "logs" / f"{name}.log"
            if logf.is_file():
                tail = logf.read_text(errors="replace").splitlines()[-40:]
                lines += [f"# tail of {name}.log ({len(tail)} lines):"] + ["  " + t for t in tail]
        out.write_text("\n".join(lines) + "\n")
        print(f"evidence -> {out}")

    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; interpreter {PY}; port {self.port}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            a = self.step_generation(ds)
            b = self.step_training(ds, note="REAL ultralytics backend on the runner CPU; validation disabled: no AP/latency "
                                            "is measured (latency needs CUDA), so no EVALUATED row is expected here")
            b2 = self.step_stub_runner(ds) if self.args.stub_step else True
            ok = a and b and b2
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0, help="core port (default: ephemeral; never 8000-8002/5173-5175)")
    p.add_argument("--root", default=None, help="smoke root (default $MBNAS_TEST_ROOT/wp2_smoke/<stamp>)")
    p.add_argument("--timeout", type=float, default=900, help="seconds per run to reach a terminal state")
    p.add_argument("--no-stub-step", dest="stub_step", action="store_false",
                   help="skip the SYNTHETIC stub-handler runner step b2")
    p.add_argument("--device-source", choices=("local", "runners"), default="local")
    p.add_argument("--evidence", default=str(ROOT / "docs" / "internal" / "evidence" / "WP2" / "two_process_smoke.txt"))
    args = p.parse_args(argv)
    if args.port in (8000, 8001, 8002, 5173, 5174, 5175):
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    return Smoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
