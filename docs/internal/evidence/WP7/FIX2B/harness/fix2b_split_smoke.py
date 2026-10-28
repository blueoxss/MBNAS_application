#!/usr/bin/env python3
"""FIX2B (DD-A62 (a)-(d)) CPU-only two-host simulation: the FIX2 harness (``../../FIX2/harness/fix2_split_smoke.py``,
same sandboxes, processes and steps A, B, C, E, stop) re-run on the changed tree, plus two counted steps:

 D  explicit CPU training WITH validation (informational in FIX2, counted here): the runner job SUCCEEDS with the
    NOT_EVALUATED payload (DD-A60 (4)) and the core commits it as ``SEARCH_COMPLETE`` + ``performance_status``
    NOT_EVALUATED + ``reason_code`` EVALUATION_REQUIRES_CUDA - ap/recall/latency_ms/size_mb/fitness null, latency_source
    pending, checkpoint_path kept, raw_metrics.device_used cpu - and the run is COMPLETED (FIX2: EVAL_FAILED
    METRIC_MISSING:ap, run FAILED MODEL_RUN_FAILED); the gateway's public row says the same  [DD-A62 (a)]
 F  the OFF candidates of step A have a downloadable architecture artifact through the gateway: the snapshot lists one
    READY ``architecture`` artifact per generated candidate (core DB: ``meta.location = core_local``, relpath
    ``architectures/<file>``; no ARCHITECTURE_ARTIFACT_UNAVAILABLE log), and ``GET /api/v1/runs/{run}/artifacts/{id}``
    with the user bearer answers 200 with exactly the candidate's YAML (sha256(body) == X-Artifact-Sha256 == the
    registry sha256)  [DD-A62 (c)]

``--device-source runners`` (default, as FIX2) or ``local`` (the default ``MBNAS_CORE_DEVICE_SOURCE``: the core sandbox
has no GPU device nodes, so its NVML list is empty and the registered CPU runner's ``cpu`` entry must be used, DD-A62
(b); step C then also shows ``available: ['cpu']``). No GPU, no network beyond 127.0.0.1; only processes started here
are signalled. Exit 0 when every counted step passed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
ROOT = HERE.parents[6]  # TARGET_ROOT (docs/internal/evidence/WP7/FIX2B/harness/<this>)
_spec = importlib.util.spec_from_file_location("fix2_split_smoke",
                                               ROOT / "docs/internal/evidence/WP7/FIX2/harness/fix2_split_smoke.py")
fix2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fix2
_spec.loader.exec_module(fix2)  # type: ignore[union-attr]
wp3, now, PY = fix2.wp3, fix2.now, fix2.PY
REASON = "EVALUATION_REQUIRES_CUDA"
METRICS = ("ap", "recall", "latency_ms", "size_mb", "fitness")


class Fix2bSmoke(fix2.SplitSmoke):
    def __init__(self, args: argparse.Namespace) -> None:
        if not args.root:
            args.root = str(ROOT / "runtime" / "test_root" / "fix2b_smoke" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
        super().__init__(args)  # FIX2 sets args.device_source = "runners" for the base classes
        self.core_device_source = args.core_device_source
        self.off_run_id: str | None = None

    def core_env(self) -> dict[str, str]:
        env = super().core_env()
        env["MBNAS_CORE_DEVICE_SOURCE"] = self.core_device_source
        return env

    # -- A (FIX2) + the run id for F -------------------------------------------------------------------------------
    def step_off_generation(self) -> bool:
        ok = super().step_off_generation()
        self.off_run_id = self.steps[-1].get("run_id")
        return ok

    # -- D (counted) -----------------------------------------------------------------------------------------------
    def step_cpu_training_with_validation_counted(self, ds: dict[str, str]) -> bool:
        t0 = time.monotonic()
        run_id, out = self.run_training(ds, "train-val", validation=True)
        if run_id is None:
            return self.step("D-cpu-training-with-validation", False, t0, **out)
        self.request_ids["D-create"] = out["request_id"]
        rows = self.db_rows("SELECT status, row_json FROM evaluations WHERE run_id = ?", (run_id,))
        stored = [{"status": r["status"], **{k: v for k, v in json.loads(r["row_json"]).items()
                                             if k in (*METRICS, "performance_status", "reason_code", "latency_source",
                                                      "split_hash", "checkpoint_path", "candidate_status", "gate")}}
                  for r in rows]
        snap = self.snapshot(run_id)
        public = [{k: c.get(k) for k in ("status", "performance_status", "reason_code", "fitness", "latency_source",
                                         "training_status")} for c in snap.get("candidates") or []]
        job = (out["jobs"] or [{}])[0]
        ok = (out["run_status"] == "COMPLETED" and out["run_reason"] is None
              and [j["status"] for j in out["jobs"]] == ["SUCCEEDED"] and job.get("device_used") == "cpu"
              and job.get("evaluation_status") == "NOT_EVALUATED" and job.get("evaluation_reason") == REASON
              and len(stored) == 1 and stored[0]["status"] == "SEARCH_COMPLETE"
              and stored[0]["performance_status"] == "NOT_EVALUATED" and stored[0]["reason_code"] == REASON
              and all(stored[0][k] is None for k in METRICS) and stored[0]["latency_source"] == "pending"
              and stored[0]["split_hash"] is None and bool(stored[0]["checkpoint_path"]) and stored[0]["gate"] is None
              and out["evaluation_rows"][0]["device_used"] == "cpu"
              and out["checkpoints"] and all(c["state"] == "PRESENT" for c in out["checkpoints"])
              and len(public) == 1 and public[0]["status"] == "SEARCH_COMPLETE"
              and public[0]["performance_status"] == "NOT_EVALUATED" and public[0]["reason_code"] == REASON
              and public[0]["fitness"] is None and public[0]["latency_source"] == "pending")
        stored[0]["checkpoint_path"] = bool(stored[0]["checkpoint_path"]) if stored else None  # no host path in evidence
        return self.step("D-cpu-training-with-validation", bool(ok), t0, run_id=run_id, stored_rows=stored,
                         public_rows=public, **out)

    # -- F ---------------------------------------------------------------------------------------------------------
    def step_architecture_download(self) -> bool:
        t0 = time.monotonic()
        run_id = self.off_run_id
        if not run_id:
            return self.step("F-off-architecture-download", False, t0, error="step A produced no run")
        snap = self.snapshot(run_id)
        cands = snap.get("generated_candidates") or []
        arts = {a["id"]: a for a in self.db_rows(
            "SELECT id, candidate_id, kind, relpath, sha256, bytes, state, meta_json FROM artifacts WHERE run_id = ?",
            (run_id,))}
        logs = [json.loads(r["payload_json"]) for r in self.db_rows(
            "SELECT payload_json FROM events WHERE run_id = ? AND type = 'log'", (run_id,))]
        unavailable = [p for p in logs if p.get("reason_code") == "ARCHITECTURE_ARTIFACT_UNAVAILABLE"]
        downloads, ok = [], bool(cands)
        for cand in cands:
            views = [a for a in cand.get("artifacts") or [] if a.get("kind") == "architecture"]
            if len(views) != 1:
                ok = False
                downloads.append({"candidate_id": cand.get("id"), "architecture_views": len(views)})
                continue
            view = views[0]
            reg = arts.get(view["id"]) or {}
            meta = json.loads(reg.get("meta_json") or "{}")
            core_local = self.root / "core" / "runs" / run_id / str(reg.get("relpath") or "-")
            expected = core_local.read_bytes() if core_local.is_file() else None
            code, body, rid, resp = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{view['id']}", raw=True)
            digest = hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else None
            rec = {"candidate_id": cand.get("id"), "artifact_id": view["id"], "view_state": view.get("state"),
                   "registry": {"relpath": reg.get("relpath"), "state": reg.get("state"),
                                "location": meta.get("location"), "sha256": reg.get("sha256")},
                   "http": code, "request_id": rid, "x_artifact_sha256": resp.headers.get("X-Artifact-Sha256"),
                   "x_artifact_verification": resp.headers.get("X-Artifact-Verification"), "body_sha256": digest,
                   "body_is_the_core_local_yaml": expected is not None and body == expected,
                   "error": None if code == 200 else (body[:300].decode(errors="replace") if isinstance(body, bytes)
                                                      else str(body)[:300])}
            downloads.append(rec)
            ok = ok and (view.get("state") == "READY" and reg.get("state") == "READY" and meta.get("location") ==
                         "core_local" and code == 200 and digest == reg.get("sha256") == rec["x_artifact_sha256"]
                         and rec["body_is_the_core_local_yaml"])
        ok = ok and not unavailable
        return self.step("F-off-architecture-download", bool(ok), t0, run_id=run_id, generated_candidates=len(cands),
                         downloads=downloads, architecture_artifact_unavailable_logs=len(unavailable))

    # -- run / evidence --------------------------------------------------------------------------------------------
    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}; "
                  f"bwrap {fix2.BWRAP}; MBNAS_CORE_DEVICE_SOURCE={self.core_device_source}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            ok = self.step_off_generation() and ok
            ok = self.step_architecture_download() and ok
            ok = self.step_cpu_training(ds) and ok
            ok = self.step_eval_on_refused(ds) and ok
            ok = self.step_cpu_training_with_validation_counted(ds) and ok
            ok = self.step_request_id_linkage() and ok
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# FIX2B (DD-A62 (a)-(d)) CPU-only two-host simulation ({started} .. {now()}, {dur:.1f} s) "
                 f"result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B docs/internal/evidence/WP7/FIX2B/harness/fix2b_split_smoke.py "
                 f"{' '.join(sys.argv[1:])}",
                 f"# MBNAS_CORE_DEVICE_SOURCE={self.core_device_source}; PYTHONPATH="
                 f"{os.environ.get('PYTHONPATH') or '(unset)'}",
                 f"# core: {self.base} (bwrap: customer root read-only, customer data hidden, no GPU device nodes); "
                 f"gateway: {self.gw_base}; runner: bwrap, no GPU device nodes, CUDA_VISIBLE_DEVICES=''",
                 f"# smoke root: {self.root}  process logs: {self.root / 'logs'}", "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        text = "\n".join(lines) + "\n"
        for secret in (self.user_token, self.api_token, *self.tokens.values()):  # evidence never carries a live token
            text = text.replace(secret, "<redacted>")
        out.write_text(fix2.redact_host(text))
        logs_out = out.parent / f"logs_{out.stem}"
        logs_out.mkdir(exist_ok=True)
        for name in ("core", "owner", "runner", "gateway"):
            src = self.root / "logs" / f"{name}.log"
            if src.is_file():
                body = src.read_text(errors="replace")
                for secret in (self.user_token, self.api_token, *self.tokens.values()):
                    body = body.replace(secret, "<redacted>")
                (logs_out / f"{name}.log").write_text(fix2.redact_host(body))
        print(f"evidence -> {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", default=None, help="smoke root (default <ROOT>/runtime/test_root/fix2b_smoke/<stamp>)")
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--device-source", dest="core_device_source", choices=("runners", "local"), default="runners")
    p.add_argument("--evidence", default=str(ROOT / "docs" / "internal" / "evidence" / "WP7" / "FIX2B" /
                                             "split_smoke.txt"))
    args = p.parse_args(argv)
    args.with_validation_probe = False  # D is a counted step here
    if args.port in wp3.RESERVED or args.gw_port in wp3.RESERVED:
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    return Fix2bSmoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
