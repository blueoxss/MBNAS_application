#!/usr/bin/env python3
"""FIX2 (DD-A60) CPU-only two-host simulation on one machine, built on scripts/dev/wp3_three_process_smoke.py.

Topology (every process started and stopped here; only its own process groups are signalled):

* "core host" = core API + owner worker, each inside ``bwrap --bind / / --dev /dev --proc /proc`` with
  ``--ro-bind <customer>`` (the customer output root's parent is READ-ONLY there: the core host cannot create the
  gateway's output root) and ``--tmpfs <customer_data>`` (the customer's dataset is NOT visible on the core host);
  core roots ``<root>/core/runs`` (output) and ``<root>/core_local_data`` (empty dataset root);
  ``MBNAS_CORE_DEVICE_SOURCE=runners`` (validation devices = registered runners; no NVML read);
* "customer host" = gateway (plain process; output roots ``[<customer>]``, dataset roots ``[<customer_data>]``) + runner
  (bwrap without GPU device nodes, ``CUDA_VISIBLE_DEVICES=""``: registers as the CPU device; dataset mount
  ``customer_data -> runner_data``; local artifact transfer into the gateway store).

Steps (user API ``/api/v1`` of the gateway with the user bearer only; each with UTC time, request id, HTTP status):
 A  OFF generation run with output_root = <customer>/out  -> COMPLETED; nothing created under <customer>; the owner
    worker's control/attempt-log/architectures files are under <root>/core/runs/<run>  [DD-A60 (2)]
 B  explicit CPU training (validation off, DD-A53 g explicit split roots, benchmark device "cpu") -> COMPLETED; the
    evaluation row is NOT_EVALUATED with latency_source pending; device_used cpu  [DD-A60 (1)]
 C  evaluation-ON search protocol -> validate refuses with EVALUATION_REQUIRES_CUDA (device "cpu"; and the preset
    device: DEVICE_UNAVAILABLE + EVALUATION_REQUIRES_CUDA)  [DD-A60 (1)]
 D  (informational) explicit CPU training WITH validation: the runner job SUCCEEDS with the NOT_EVALUATED payload
    instead of the CUDA AssertionError [DD-A60 (4), runner side]; the core's commit of that payload is recorded as is
 E  one request id of step B (and of step C) found in BOTH logs/gateway.log and logs/core.log  [DD-A60 (5)]
Exit 0 when A, B, C, E and the stop step pass (D is recorded, never counted). No GPU, no network beyond 127.0.0.1.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[6]  # TARGET_ROOT (docs/internal/evidence/WP7/FIX2/harness/<this>)
SCRIPTS = ROOT / "scripts" / "dev"
_spec = importlib.util.spec_from_file_location("wp3_three_process_smoke", SCRIPTS / "wp3_three_process_smoke.py")
wp3 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = wp3
_spec.loader.exec_module(wp3)  # type: ignore[union-attr]
now, TERMINAL, PY = wp3.now, wp3.TERMINAL, wp3.PY
BWRAP = shutil.which("bwrap") or "bwrap"


class SplitSmoke(wp3.Smoke3):
    def __init__(self, args: argparse.Namespace) -> None:
        if not args.root:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            args.root = str(ROOT / "runtime" / "test_root" / "fix2_smoke" / stamp)
        args.device_source = "runners"
        super().__init__(args)
        self.customer = self.root / "customer"  # output root parent: writable here, read-only on the "core host"
        self.customer_out = self.customer / "out"
        self.customer_data = self.root / "customer_data"
        self.core_local_data = self.root / "core_local_data"
        self.request_ids: dict[str, str] = {}
        self.info_steps: list[dict[str, Any]] = []

    # -- sandboxes -------------------------------------------------------------------------------------------------
    def sandbox(self, name: str) -> list[str]:
        base = [BWRAP, "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent"]
        if name in ("core", "owner"):
            return base + ["--ro-bind", str(self.customer), str(self.customer),
                           "--tmpfs", str(self.customer_data), "--"]
        if name.startswith("runner"):
            return base + ["--"]
        return []

    def spawn(self, name: str, argv: list[str], env: dict[str, str]) -> subprocess.Popen:
        return super().spawn(name, self.sandbox(name) + argv, env)

    # -- env -------------------------------------------------------------------------------------------------------
    def core_env(self) -> dict[str, str]:
        env = super().core_env()
        env.update({"MBNAS_CORE_DATASET_ROOTS": json.dumps([str(self.core_local_data)]),
                    "MBNAS_CORE_DEVICE_SOURCE": "runners"})
        return env

    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        env = super().runner_env(name, runner_id)
        env.update({"MBNAS_RUNNER_DATASET_MOUNTS": json.dumps({str(self.customer_data): str(self.root / "runner_data")}),
                    "CUDA_VISIBLE_DEVICES": ""})
        return env

    def gateway_env(self) -> dict[str, str]:
        env = super().gateway_env()
        env.update({"MBNAS_GW_DATASET_ROOTS": json.dumps([str(self.customer_data)]),
                    "MBNAS_GW_OUTPUT_ROOTS": json.dumps([str(self.customer)])})
        return env

    # -- inputs ----------------------------------------------------------------------------------------------------
    def build_inputs(self) -> dict[str, str]:
        spec = importlib.util.spec_from_file_location("fix2_datasets", ROOT / "tests" / "_shared" / "datasets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        ds = mod.build_dataset(self.customer_data, "fix2split", counts={"train": 4, "val": 2, "test": 2})
        shutil.copytree(self.customer_data, self.root / "runner_data")
        for p in (self.customer, self.core_local_data, self.root / "weights", self.root / "uploads"):
            p.mkdir(parents=True, exist_ok=True)
        import ultralytics

        text = (Path(ultralytics.__file__).parent / "cfg" / "models" / "11" / "yolo11.yaml").read_text()
        (self.root / "uploads" / "yolo11n.yaml").write_text(text.replace("nc: 80", "nc: 1\nscale: n"))
        (self.root / "uploads" / "bad_module.yaml").write_text(wp3.BAD_YAML)
        self.note(f"customer dataset {ds['dataset_yaml']} (hidden on the core host), runner copy "
                  f"{self.root / 'runner_data'}, customer output root {self.customer_out} (read-only parent on the "
                  "core host)")
        return ds

    # -- process control (bwrap does not forward SIGTERM: the sandboxed program itself is signalled) ----------------
    @staticmethod
    def _sandboxed(pid: int) -> list[int]:
        """Descendants of ``pid`` that are not bwrap (the sandboxed program; /proc/<pid>/task/*/children)."""
        out, todo = [], [pid]
        while todo:
            cur = todo.pop()
            for task in Path(f"/proc/{cur}/task").glob("*"):
                try:
                    kids = [int(k) for k in (task / "children").read_text().split()]
                except (OSError, ValueError):
                    kids = []
                for k in kids:
                    try:
                        comm = Path(f"/proc/{k}/comm").read_text().strip()
                    except OSError:
                        continue
                    (todo if comm == "bwrap" else out).append(k)
        return out

    def stop_all(self) -> bool:
        t0 = time.monotonic()
        codes: dict[str, Any] = {}
        graceful: dict[str, Any] = {}
        for name in ("gateway", "runner", "owner", "core"):
            p = self.procs.get(name)
            if p is None:
                continue
            if p.poll() is None:
                targets = self._sandboxed(p.pid) if self.sandbox(name) else [p.pid]
                graceful[name] = {"signalled": targets}
                for pid in targets or [p.pid]:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self.note(f"{name} pgid={p.pid} did not stop on SIGTERM: SIGKILL")
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=30)
            codes[name] = p.returncode
        alive = [n for n, p in self.procs.items() if p.poll() is None]
        # uvicorn >= 0.29 re-raises the captured SIGTERM after its graceful shutdown: -15 (plain) / 143 (bwrap 128+15)
        clean = {"core": (0, -15, 143), "gateway": (0, -15, 143), "owner": (0,), "runner": (0,)}
        ok = not alive and all(code in clean.get(name, (0,)) for name, code in codes.items())
        return self.step("stop-own-processes", ok, t0, exit_codes=codes, still_alive=alive, signalled=graceful,
                         note="SIGTERM to the sandboxed program (not bwrap); uvicorn re-raises SIGTERM after shutdown")

    # -- steps -----------------------------------------------------------------------------------------------------
    def jobs_of(self, run_id: str) -> list[dict[str, Any]]:
        out = []
        for j in self.db_rows("SELECT id, kind, status, attempt, lease_owner, error_json, result_json FROM jobs "
                              "WHERE run_id = ?", (run_id,)):
            err = json.loads(j.pop("error_json") or "null") or {}
            res = json.loads(j.pop("result_json") or "null") or {}
            out.append({**j, "reason_code": err.get("reason_code") or res.get("reason_code"),
                        "message": str(err.get("message") or res.get("message") or "")[:400],
                        "outputs": res.get("outputs") or err.get("outputs") or {}})
        return out

    def wait_run(self, run_id: str) -> dict[str, Any]:
        return self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)

    def step_off_generation(self) -> bool:
        t0 = time.monotonic()
        policy = {"schema_version": "nas-policy/1",
                  "console": {"paths": {"output_root": str(self.customer_out)},
                              "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                         "seeds": [7], "candidate_count": 2, "max_attempts": 12,
                                         "allow_duplicates": False, "arms": ["GA"]}}}
        code, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": policy})
        if code != 200 or not v.get("valid"):
            return self.step("A-off-generation-customer-root", False, t0, http=code, body=v, request_id=rid_v)
        code, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"],
                                                                   "protocol_hash": v["protocol_hash"],
                                                                   "validation_id": v["validation_id"],
                                                                   "idempotency_key": "fix2-gen-" + uuid.uuid4().hex[:8]})
        if code != 200:
            return self.step("A-off-generation-customer-root", False, t0, http=code, body=created, request_id=rid_c)
        run_id = created["run_id"]
        self.request_ids["A-create"] = rid_c
        run = self.wait_run(run_id)
        jobs = self.jobs_of(run_id)
        snap = self.snapshot(run_id)
        cands = snap.get("generated_candidates") or []
        local = self.root / "core" / "runs" / run_id
        files = sorted(str(p.relative_to(local)) for p in local.rglob("*") if p.is_file()) if local.is_dir() else []
        owner_log = (self.root / "logs" / "owner.log").read_text(errors="replace")
        ok = (run["status"] == "COMPLETED" and [(j["kind"], j["status"]) for j in jobs] == [("PROPOSE_GENERATION",
                                                                                            "SUCCEEDED")]
              and len(cands) == 2 and not self.customer_out.exists() and (local / "control").is_dir()
              and (local / "architectures" / "generation-7.json").is_file() and "Traceback" not in owner_log
              and self.procs["owner"].poll() is None)
        return self.step("A-off-generation-customer-root", bool(ok), t0, run_id=run_id, run_status=run["status"],
                         run_reason=run.get("reason_code"), jobs=[{k: j[k] for k in ("kind", "status", "attempt",
                                                                                      "reason_code")} for j in jobs],
                         generated_candidates=len(cands), validation_host=(v.get("capabilities") or {}).get(
                             "validation_host"), customer_output_root_created_on_core_host=self.customer_out.exists(),
                         core_local_files=files, owner_worker_alive=self.procs["owner"].poll() is None,
                         owner_log_has_traceback="Traceback" in owner_log, request_ids={"validate": rid_v, "create": rid_c})

    def training_protocol(self, ds: dict[str, str], device: str) -> dict[str, Any]:
        sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        policy = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.customer_out),
                                       "train_root": ds["train_root"], "search_val_root": ds["search_val_root"],
                                       "final_eval_root": ds["final_eval_root"]})
        policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                  warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        policy["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
        policy["console"]["benchmark"]["device"] = device
        return policy

    def run_training(self, ds: dict[str, str], name: str, *, validation: bool) -> tuple[str | None, dict[str, Any]]:
        up = self.upload("yolo11n.yaml")
        body = {"source": {"kind": "user", "upload_id": up["upload_id"], "model_name": "yolo11n_fix2"},
                "protocol": self.training_protocol(ds, "cpu"), "mode": "scratch", "validation_enabled": validation,
                "idempotency_key": f"fix2-{name}-" + uuid.uuid4().hex[:8]}
        code, created, rid, _ = self.gw("POST", "/api/v1/training-runs", body)
        if code != 200 or not isinstance(created, dict):
            return None, {"http": code, "body": created, "request_id": rid}
        run_id = created["run_id"]
        run = self.wait_run(run_id)
        evs = self.db_rows("SELECT id, status, row_json FROM evaluations WHERE run_id = ?", (run_id,))
        rows = []
        for e in evs:
            r = json.loads(e["row_json"] or "null") or {}
            rows.append({"status": e["status"], "performance_status": r.get("performance_status"),
                         "latency_source": r.get("latency_source"), "reason_code": r.get("reason_code"),
                         "device": r.get("device"), "device_used": (r.get("raw_metrics") or {}).get("device_used"),
                         "checkpoint_path_set": bool(r.get("checkpoint_path"))})
        jobs = self.jobs_of(run_id)
        cks = self.db_rows("SELECT kind, epoch, state FROM checkpoints WHERE run_id = ?", (run_id,))
        return run_id, {"request_id": rid, "run_status": run["status"], "run_reason": run.get("reason_code"),
                        "evaluation_rows": rows, "checkpoints": cks,
                        "jobs": [{"kind": j["kind"], "status": j["status"], "reason_code": j["reason_code"],
                                  "device_used": j["outputs"].get("device_used"),
                                  "evaluation_status": (j["outputs"].get("evaluation") or {}).get("evaluation_status"),
                                  "evaluation_reason": (j["outputs"].get("evaluation") or {}).get("reason_code"),
                                  "latency_source": (j["outputs"].get("evaluation") or {}).get("latency_source")}
                                 for j in jobs]}

    def step_cpu_training(self, ds: dict[str, str]) -> bool:
        t0 = time.monotonic()
        run_id, out = self.run_training(ds, "train", validation=False)
        if run_id is None:
            return self.step("B-explicit-cpu-training", False, t0, **out)
        self.request_ids["B-create"] = out["request_id"]
        rows = out["evaluation_rows"]
        ok = (out["run_status"] == "COMPLETED" and [j["status"] for j in out["jobs"]] == ["SUCCEEDED"]
              and out["jobs"][0]["device_used"] == "cpu" and rows
              and all(r["performance_status"] == "NOT_EVALUATED" and r["latency_source"] == "pending" for r in rows))
        return self.step("B-explicit-cpu-training", bool(ok), t0, run_id=run_id, **out)

    def step_eval_on_refused(self, ds: dict[str, str]) -> bool:
        t0 = time.monotonic()
        sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        paths = {k: ds[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
        paths["output_root"] = str(self.customer_out)
        results = {}
        for label, device in (("device-cpu", "cpu"), ("preset-device", None)):
            proto = pilot_smoke_protocol(paths)
            for b in proto["console"].get("baselines") or []:
                b["enabled"] = False
            proto["console"]["baseline_override_reason"] = "FIX2 smoke: evaluation-ON search refusal probe"
            if device:
                proto["console"]["benchmark"]["device"] = device
            code, v, rid, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": proto})
            self.request_ids[f"C-{label}"] = rid
            results[label] = {"http": code, "valid": (v or {}).get("valid"), "request_id": rid,
                              "errors": [{k: e.get(k) for k in ("field", "code", "message")}
                                         for e in (v or {}).get("errors") or []]}
        codes_cpu = {e["code"] for e in results["device-cpu"]["errors"]}
        codes_preset = {e["code"] for e in results["preset-device"]["errors"]}
        ok = (results["device-cpu"]["http"] == 200 and results["device-cpu"]["valid"] is False
              and codes_cpu == {"EVALUATION_REQUIRES_CUDA"}
              and {"DEVICE_UNAVAILABLE", "EVALUATION_REQUIRES_CUDA"} <= codes_preset)
        return self.step("C-evaluation-on-refused-on-cpu-fleet", bool(ok), t0, **results)

    def step_cpu_training_with_validation(self, ds: dict[str, str]) -> None:
        t0 = time.monotonic()
        run_id, out = self.run_training(ds, "train-val", validation=True)
        rec = {"step": "D-cpu-training-with-validation (informational)", "duration_s": round(time.monotonic() - t0, 2),
               "run_id": run_id, **out}
        self.info_steps.append(rec)
        self.note(f"INFO {rec['step']}: {json.dumps(rec, default=str)[:1500]}")

    def step_request_id_linkage(self) -> bool:
        t0 = time.monotonic()
        gw_log = (self.root / "logs" / "gateway.log").read_text(errors="replace").splitlines()
        core_log = (self.root / "logs" / "core.log").read_text(errors="replace").splitlines()
        found: dict[str, Any] = {}
        for key in ("B-create", "C-device-cpu", "A-create"):
            rid = self.request_ids.get(key)
            if not rid:
                continue
            pat = re.compile(rf"\brequest_id={re.escape(rid)}\b")
            found[key] = {"request_id": rid, "gateway": [l for l in gw_log if pat.search(l)],
                          "core": [l for l in core_log if pat.search(l)]}
        access_gw = [l for l in gw_log if " mbnas_server.access " in l]
        access_core = [l for l in core_log if " mbnas_core.access " in l]
        b = found.get("B-create") or {}
        ok = (len(b.get("gateway") or []) == 1 and len(b.get("core") or []) == 1
              and all(len(v["gateway"]) == 1 and len(v["core"]) >= 1 for v in found.values()))
        return self.step("E-request-id-in-both-logs", bool(ok), t0, linkage=found,
                         access_lines={"gateway": len(access_gw), "core": len(access_core)})

    # -- run -------------------------------------------------------------------------------------------------------
    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# FIX2 (DD-A60) CPU-only two-host simulation ({started} .. {now()}, {dur:.1f} s) "
                 f"result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B docs/internal/evidence/WP7/FIX2/harness/fix2_split_smoke.py "
                 f"{' '.join(sys.argv[1:])}",
                 f"# core: {self.base} (bwrap: customer root read-only, customer data hidden, no GPU device nodes); "
                 f"gateway: {self.gw_base}; runner: bwrap, no GPU device nodes, CUDA_VISIBLE_DEVICES=''",
                 f"# smoke root: {self.root}  process logs: {self.root / 'logs'}", "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.steps]
        lines += ["# informational steps (not counted):"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.info_steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        text = "\n".join(lines) + "\n"
        for secret in (self.user_token, self.api_token, *self.tokens.values()):  # evidence never carries a live token
            text = text.replace(secret, "<redacted>")
        out.write_text(redact_host(text))
        logs_out = out.parent / "logs"
        logs_out.mkdir(exist_ok=True)
        for name in ("core", "owner", "runner", "gateway"):
            src = self.root / "logs" / f"{name}.log"
            if src.is_file():
                body = src.read_text(errors="replace")
                for secret in (self.user_token, self.api_token, *self.tokens.values()):
                    body = body.replace(secret, "<redacted>")
                (logs_out / f"{name}.log").write_text(redact_host(body))
        print(f"evidence -> {out}")

    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}; "
                  f"bwrap {BWRAP}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            ok = self.step_off_generation() and ok
            ok = self.step_cpu_training(ds) and ok
            ok = self.step_eval_on_refused(ds) and ok
            if self.args.with_validation_probe:
                self.step_cpu_training_with_validation(ds)
            ok = self.step_request_id_linkage() and ok
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1


def redact_host(text: str) -> str:
    """Host paths of this machine replaced by placeholders (the evidence is read elsewhere)."""
    for real, label in ((str(ROOT), "<ROOT>"), (str(Path.home()), "<HOME>")):
        text = text.replace(real, label)
    return text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", default=None, help="smoke root (default <ROOT>/runtime/test_root/fix2_smoke/<stamp>)")
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--no-validation-probe", dest="with_validation_probe", action="store_false")
    p.add_argument("--evidence", default=str(ROOT / "docs" / "internal" / "evidence" / "WP7" / "FIX2" /
                                             "split_smoke.txt"))
    args = p.parse_args(argv)
    if args.port in wp3.RESERVED or args.gw_port in wp3.RESERVED:
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    return SplitSmoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
