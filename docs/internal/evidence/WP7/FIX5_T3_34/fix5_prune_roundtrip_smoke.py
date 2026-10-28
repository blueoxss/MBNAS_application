#!/usr/bin/env python3
"""FIX5 / DD-A66 / T3-34 real-process smoke of the retention prune round trip (evidence wrapper, not a repository script).

Wraps the unchanged WP-3 three-process smoke classes (``scripts/dev/wp3_three_process_smoke.py``: core under uvicorn +
owner worker + ``python -m mbnas_runner run`` + gateway under uvicorn, all real processes on 127.0.0.1 ephemeral ports,
CPU only, local artifact transfer into the gateway store) and drives, through the gateway's user API only:

A. REAL: the uploaded yolo11 YAML (nc 1) trained by the REAL ultralytics backend on the runner CPU, 3 epochs, 64 px,
   validation off, ``console.retention.full_state = last_rung_end_only`` (one stage of 3 epochs, so the policy keeps only
   the last full state: "keep_last=1"). The older full states get prune requests, the runner carries out the orders it
   receives on its heartbeats and reports E11, the core rows become PRUNED, the latest (epoch 3) stays and is
   downloaded through the gateway (sha256(body) == X-Artifact-Sha256 == registered digest). NOTE: the ultralytics stage
   trainer deletes non-rung-end intermediate full states itself one epoch later, so for A the runner normally finds them
   already gone and reports ``already_absent`` (recorded as observed; nothing is faked);
B. SYNTHETIC trainer, real processes: the same request (validation on) executed by a second runner process whose job
   child uses the stub handlers (tiny ``full_eNNN.json`` payloads the stub never deletes): here the RUNNER deletes the
   older payloads itself (``already_absent`` false, bytes freed), the rows become PRUNED, the latest stays downloadable;
   the gateway store copies of the pruned checkpoints are untouched in both parts.

Verification reads the core DB read-only, the runner work roots and the gateway store. Exit 0 only when every step
passed. usage: runtime/venv/bin/python -B <this file> [--root DIR] [--evidence FILE] [--timeout S]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
_spec = importlib.util.spec_from_file_location("wp3_three_process_smoke", ROOT / "scripts" / "dev" /
                                               "wp3_three_process_smoke.py")
wp3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp3)  # type: ignore[union-attr]
PY, now, TERMINAL = wp3.PY, wp3.now, wp3.TERMINAL
OPEN_STATES = ("PENDING", "DELIVERED")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class PruneSmoke(wp3.Smoke3):
    def __init__(self, args: argparse.Namespace) -> None:
        if not args.root:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            test_root = Path(os.environ.get("MBNAS_TEST_ROOT") or ROOT / "runtime" / "test_root")
            args.root = str(test_root / "fix5_prune_smoke" / stamp)
        super().__init__(args)

    # processes stopped on purpose (the real runner before part B) are not failures
    def wait(self, what: str, fn: Any, timeout: float, interval: float = 1.0) -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for name, p in self.procs.items():
                if p.poll() is not None and not getattr(p, "_expected_exit", False):
                    raise RuntimeError(f"{name} (pid {p.pid}) exited with {p.returncode} while waiting for {what}")
            try:
                last = fn()
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            if last and not isinstance(last, str):
                return last
            time.sleep(interval)
        raise TimeoutError(f"{what} not reached within {timeout} s (last: {str(last)[:300]})")

    def stop_all(self) -> bool:
        t0 = time.monotonic()
        codes = {}
        for name in ("gateway", "runner_stub", "runner", "owner", "core"):
            p = self.procs.get(name)
            if p is None:
                continue
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    self.note(f"{name} pid={p.pid} did not stop on SIGTERM: SIGKILL")
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=30)
            codes[name] = p.returncode
        alive = [n for n, p in self.procs.items() if p.poll() is None]
        return self.step("stop-own-processes", not alive, t0, exit_codes=codes, still_alive=alive,
                         pids={n: p.pid for n, p in self.procs.items()})

    # -- helpers -----------------------------------------------------------------------------------------------------
    def training_policy(self, ds: dict[str, str]) -> dict[str, Any]:
        if str(ROOT / "mbnas_core" / "src") not in sys.path:
            sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        policy = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
        policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                  warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        policy["console"]["training"] = {"epoch_target_total": 3, "seed": 41, "native_image_artifacts": False}
        policy["console"]["retention"] = {"full_state": "last_rung_end_only"}  # one 3-epoch stage: keep the last one
        return policy

    def prune_rows(self, run_id: str) -> list[dict[str, Any]]:
        return self.db_rows("SELECT id, checkpoint_id, runner_id, relpath, sha256, bytes, policy, reason, state, "
                            "planned_by, deliveries, delivered_job_id, delivered_attempt, cancel_reason, report_json, "
                            "created_at, reported_at FROM prune_requests WHERE run_id = ? ORDER BY created_at, id",
                            (run_id,))

    def train_and_verify(self, ds: dict[str, str], *, part: str, runner_id: str, runner_dir: str,
                         validation_enabled: bool, expect_runner_deleted: bool) -> bool:
        t0 = time.monotonic()
        up = self.upload("yolo11n.yaml")
        body = {"source": {"kind": "user", "upload_id": up["upload_id"]}, "protocol": self.training_policy(ds),
                "mode": "scratch", "validation_enabled": validation_enabled,
                "idempotency_key": f"fix5-{part}-" + uuid.uuid4().hex[:8]}
        code, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        if code != 200 or not isinstance(created, dict):
            return self.step(f"{part}-training-via-gateway", False, t0, http=code, body=created, request_id=rid_c)
        run_id, cid = created["run_id"], created["candidate_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        jobs = self.db_rows("SELECT id, kind, status, attempt, lease_owner FROM jobs WHERE run_id = ?", (run_id,))
        resolved = json.loads(self.db_rows("SELECT resolved_protocol_json FROM runs WHERE id = ?", (run_id,))[0]
                              ["resolved_protocol_json"])
        retention = (resolved.get("console") or {}).get("retention")
        a = self.step(f"{part}-training-via-gateway", run["status"] == "COMPLETED" and any(
            j["status"] == "SUCCEEDED" and j["lease_owner"] == runner_id for j in jobs)
            and retention == {"full_state": "last_rung_end_only"}, t0, run_id=run_id, candidate_id=cid,
            run_status=run["status"], jobs=jobs, request_id=rid_c, resolved_retention=retention,
            stages=[r.get("epoch_target_total") for r in resolved.get("rungs") or []])

        # the round trip: every prune request reaches DONE (the runner reported it) - bounded wait
        t1 = time.monotonic()
        try:
            self.wait(f"prune requests of {run_id} done", lambda: (lambda rows: rows if rows and not [
                r for r in rows if r["state"] in OPEN_STATES] else None)(self.prune_rows(run_id)), timeout=120)
        except TimeoutError as exc:
            self.note(f"prune requests not all done: {exc}")
        reqs = self.prune_rows(run_id)
        cks = self.db_rows("SELECT id, epoch, kind, state, sha256, bytes, pruned_at, prune_json FROM checkpoints "
                           "WHERE run_id = ? AND kind = 'training_full' ORDER BY epoch, created_at", (run_id,))
        locs = {r["object_id"]: r for r in self.db_rows(
            "SELECT object_id, store, runner_id, relpath, state FROM artifact_locations WHERE run_id = ? AND "
            "object_kind = 'checkpoint' AND store = 'runner_local'", (run_id,))}
        work = self.root / runner_dir / "work" / "runs" / run_id
        store = self.root / "gateway" / "artifacts"
        by_epoch = {c["epoch"]: c for c in cks}
        latest = by_epoch.get(max(by_epoch)) if by_epoch else None
        older = [c for c in cks if latest is not None and c["id"] != latest["id"]]
        detail_rows = []
        ok = bool(latest) and sorted(by_epoch) == [1, 2, 3] and len(reqs) == len(older) == 2
        for c in cks:
            prune = json.loads(c["prune_json"] or "null") or {}
            loc = locs.get(c["id"]) or {}
            on_disk = (work / loc["relpath"]).is_file() if loc.get("relpath") else None
            sha = str(c["sha256"])
            stored = store / sha[:2] / sha
            in_store = stored.is_file() and sha256(stored) == sha
            req = next((r for r in reqs if r["checkpoint_id"] == c["id"]), None)
            report = json.loads(req["report_json"]) if req and req.get("report_json") else None
            row = {"epoch": c["epoch"], "checkpoint_id": c["id"], "state": c["state"], "runner_local": loc.get("state"),
                   "file_on_runner": on_disk, "store_copy_verified": in_store, "request_state": (req or {}).get("state"),
                   "deliveries": (req or {}).get("deliveries"), "planned_by": (req or {}).get("planned_by"),
                   "already_absent": prune.get("already_absent"), "bytes_freed": prune.get("bytes_freed"),
                   "report_removed": (report or {}).get("removed"), "report_detail": (report or {}).get("detail")}
            detail_rows.append(row)
            if c is latest:
                ok = ok and c["state"] == "PRESENT" and loc.get("state") == "PRESENT" and on_disk is True and req is None
            else:
                ok = ok and c["state"] == "PRUNED" and loc.get("state") == "PRUNED" and on_disk is False
                ok = ok and (req or {}).get("state") == "DONE" and prune.get("remote") is True and in_store
                if expect_runner_deleted:
                    ok = ok and prune.get("already_absent") is False and int(prune.get("bytes_freed") or 0) > 0
        b = self.step(f"{part}-prune-round-trip", ok, t1, run_id=run_id, checkpoints=detail_rows,
                      prune_requests=[{k: r[k] for k in ("checkpoint_id", "state", "deliveries", "delivered_job_id",
                                                          "cancel_reason", "planned_by", "created_at", "reported_at")}
                                      for r in reqs],
                      expect_runner_deleted=expect_runner_deleted)

        # the latest full state stays downloadable through the gateway; the public detail shows PRUNED / PRESENT
        t2 = time.monotonic()
        code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{latest['id']}", raw=True) if latest \
            else (0, b"", None, None)
        digest = hashlib.sha256(data).hexdigest() if data else None
        header = (resp.headers.get("X-Artifact-Sha256") or "").removeprefix("sha256:") if resp is not None else None
        code_d, detail, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        views = {v["epoch"]: v["state"] for v in ((detail or {}).get("checkpoints") or []) if v.get("kind") ==
                 "training_full"} if isinstance(detail, dict) else {}
        pruned_dl = []
        for c in older:  # observation only: what the gateway answers for a pruned checkpoint whose store copy exists
            pc, pdata, prid, presp = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{c['id']}", raw=True)
            pruned_dl.append({"checkpoint_id": c["id"], "http": pc, "bytes": len(pdata) if pc == 200 else None,
                              "request_id": prid})
        ok2 = (code == 200 and digest == header == str(latest["sha256"]) and len(data) == int(latest["bytes"])
               and code_d == 200 and views == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"})
        c_ = self.step(f"{part}-latest-downloadable", bool(ok2), t2, run_id=run_id, checkpoint_id=(latest or {}).get("id"),
                       http=code, bytes=len(data), sha256=digest, header_sha=header,
                       registered_sha=(latest or {}).get("sha256"), verification=resp.headers.get(
                           "X-Artifact-Verification") if resp is not None else None, request_ids=[rid, rid_d],
                       public_checkpoint_states=views, pruned_checkpoint_downloads_observed=pruned_dl)
        logs = self.db_rows("SELECT payload_json FROM events WHERE run_id = ? AND type = 'log'", (run_id,))
        codes = [json.loads(e["payload_json"]).get("reason_code") for e in logs]
        self.note(f"{part}: log reason codes {sorted(set(c for c in codes if c))} CHECKPOINT_PRUNED x"
                  f"{codes.count('CHECKPOINT_PRUNED')} REMOTE_PRUNE_REQUESTED x{codes.count('REMOTE_PRUNE_REQUESTED')} "
                  f"REMOTE_RETENTION_FORCED x{codes.count('REMOTE_RETENTION_FORCED')}")
        return a and b and c_

    def start_stub_runner(self) -> bool:
        t0 = time.monotonic()
        real = self.procs.get("runner")
        if real is not None and real.poll() is None:
            real._expected_exit = True  # type: ignore[attr-defined]
            real.send_signal(signal.SIGTERM)
            real.wait(timeout=90)
        self.note(f"real runner stopped for part B (exit {real.returncode if real else None})")
        self.principals((("runner_stub", "runner", ["--runner-id", "smoke-runner-stub"]),))
        env = self.runner_env("runner_stub", "smoke-runner-stub")
        env.update(PYTHONPATH=str(ROOT / "tests" / "_shared"), MBNAS_JOB_HANDLERS_MODULE="stub_handlers",
                   MBNAS_STUB_EPOCH_S="0.3")
        self.spawn("runner_stub", [PY, "-B", "-m", "mbnas_runner", "run"], env)
        row = self.wait("stub runner registration", lambda: self.registered("smoke-runner-stub"), timeout=180)
        return self.step("B-stub-runner-start", bool(row), t0, pid=self.procs["runner_stub"].pid)

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# FIX5 T3-34 prune round-trip smoke ({started} .. {now()}, {dur:.1f} s) result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B {Path(__file__).relative_to(ROOT)} {' '.join(sys.argv[1:])}",
                 f"# cwd: {ROOT}  interpreter: {PY}  CUDA_VISIBLE_DEVICES=''  core: {self.base}  gateway: {self.gw_base}",
                 f"# smoke root: {self.root}  process logs: {self.root / 'logs'}", "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        for name in ("core", "owner", "runner", "runner_stub", "gateway"):
            logf = self.root / "logs" / f"{name}.log"
            if logf.is_file():
                tail = [t for t in logf.read_text(errors="replace").splitlines() if "prune" in t.lower()][-40:]
                lines += [f"# prune lines of {name}.log ({len(tail)} lines):"] + ["  " + t for t in tail]
        text = "\n".join(lines) + "\n"
        for secret in (self.user_token, *self.tokens.values()):  # evidence never carries a live token
            text = text.replace(secret, "<redacted>")
        out.write_text(text)
        print(f"evidence -> {out}")

    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            ok = self.train_and_verify(ds, part="A-real", runner_id="smoke-runner-1", runner_dir="runner",
                                       validation_enabled=False, expect_runner_deleted=False) and ok
            ok = self.start_stub_runner() and ok
            ok = self.train_and_verify(ds, part="B-stub", runner_id="smoke-runner-stub", runner_dir="runner_stub",
                                       validation_enabled=True, expect_runner_deleted=True) and ok
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", default=None)
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--device-source", choices=("local", "runners"), default="local")
    p.add_argument("--evidence", default=str(Path(__file__).resolve().parent / "prune_roundtrip_smoke.txt"))
    args = p.parse_args(argv)
    if args.port in wp3.RESERVED or args.gw_port in wp3.RESERVED:
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    return PruneSmoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
