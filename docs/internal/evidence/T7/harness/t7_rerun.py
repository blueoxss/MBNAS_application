#!/usr/bin/env python3
"""T7 GPU re-run (DD-A49 f, DD-A52) on a NEW frozen copy (``runtime/test_root/g2_tree2``): reuses ``t7_gpu_path.py``
(same process plumbing, network guard, evidence layout) and replaces/extends the items whose verdict was FAIL or whose
code changed. Steps go to ``steps_rerun.jsonl``; per-run summary ``t7_rerun_summary_<stamp>.json``.

Items (``--items``, default a,g,f,i):
  a  BASELINE_TRAIN via the gateway on the GPU-0 runner; strict device checks: job ``outputs.device_used == "cuda:0"``,
     ``outputs.device_uuid`` == the runner's registered uuid, evaluation ``raw_metrics.device_used == "cuda:0"``
  g  official EVALUATE of yolo11n (local byte-identical weights) on the GPU-0 runner; the payload carries
     ``device_uuid`` (== runner uuid) and ``latency.manifest`` with ``nvml_lookup == "uuid"`` (manifest uuid == runner uuid)
  f  (f2) runner-produced checkpoint(s) + evaluation artifact(s) downloaded THROUGH the gateway (store-served) with
     sha256(body) == X-Artifact-Sha256 == record; DB after: no artifact/checkpoint flipped to MISSING
  i  DD-A52 b: the GPU-0 runner is stopped, a runner registered on GPU 1 (CUDA_VISIBLE_DEVICES=1, gpu_index 1) runs a
     short BASELINE_TRAIN (training evaluation) and an official EVALUATE; every recorded device uuid / latency manifest
     uuid must be GPU 1's NVML uuid and differ from GPU 0's
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("t7_gpu_path", HERE / "t7_gpu_path.py")
t7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t7)  # type: ignore[union-attr]
utc, nvsmi, jl = t7.utc, t7.nvsmi, t7.jl


def gpu_uuids() -> dict[int, str]:
    p = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"], capture_output=True,
                       text=True, timeout=60)
    out = {}
    for line in p.stdout.splitlines():
        i, u = [x.strip() for x in line.split(",")]
        out[int(i)] = u
    return out


def make_rerun(wp3: Any) -> type:
    Base = t7.make_harness(wp3)
    PY = wp3.PY

    class Rerun(Base):
        def __init__(self, args: argparse.Namespace) -> None:
            super().__init__(args)
            self.steps_path = self.ev / "steps_rerun.jsonl"
            self.phys = gpu_uuids()
            self.gpu1_runner_id = "t7-gpu1"

        # -- helpers ----------------------------------------------------------------------------------------------
        def job_result(self, job_id: str) -> dict[str, Any]:
            rows = self.db_rows("SELECT result_json FROM jobs WHERE id = ?", (job_id,))
            return (jl(rows[0]["result_json"]) or {}) if rows else {}

        def eval_rows(self, run_id: str) -> list[dict[str, Any]]:
            out = []
            for e in self.db_rows("SELECT id, status, row_json FROM evaluations WHERE run_id = ?", (run_id,)):
                row = jl(e["row_json"]) or {}
                out.append({"id": e["id"], "status": e["status"], "row": row})
            return out

        @staticmethod
        def lat_manifest(ev: dict[str, Any]) -> dict[str, Any]:
            return ((ev.get("latency") or {}).get("manifest") or {}) if isinstance(ev, dict) else {}

        def device_facts(self, run_id: str, kind: str) -> dict[str, Any]:
            jobs = [j for j in self.db_rows("SELECT id, kind, status, attempt, lease_owner FROM jobs WHERE run_id = ? "
                                            "ORDER BY created_at, id", (run_id,)) if j["kind"] == kind]
            res = self.job_result(jobs[-1]["id"]) if jobs else {}
            outs = res.get("outputs") or {}
            ev = outs.get("evaluation") or {}
            man = self.lat_manifest(ev)
            evs = self.eval_rows(run_id)
            raw = [((e["row"].get("raw_metrics") or {}).get("device_used")) for e in evs]
            row_uuid = [e["row"].get("device_uuid") for e in evs]
            led = self.db_rows("SELECT job_id, attempt, device_uuid, gpu_seconds FROM cost_ledger WHERE run_id = ?",
                               (run_id,))
            return {"job": jobs[-1] if jobs else None, "result_status": res.get("status"),
                    "result_reason": res.get("reason_code"), "result_message": str(res.get("message") or "")[:400],
                    "outputs_device_used": outs.get("device_used"), "outputs_device_uuid": outs.get("device_uuid"),
                    "payload_device_uuid": ev.get("device_uuid"), "payload_device_name": ev.get("device_name"),
                    "payload_device": ev.get("device"), "ap": ev.get("ap"), "ap_50": ev.get("ap_50"),
                    "latency_ms": ev.get("latency_ms"), "latency_source": ev.get("latency_source"),
                    "manifest_present": bool(man), "manifest_nvml_lookup": man.get("nvml_lookup"),
                    "manifest_nvml_lookup_warning": man.get("nvml_lookup_warning"), "manifest_uuid": man.get("uuid"),
                    "manifest_name": man.get("name"), "manifest_cuda_index": man.get("cuda_index"),
                    "manifest_nvml_index": man.get("nvml_index"),
                    "manifest_keys": sorted(man), "evaluation_rows": [e["status"] for e in evs],
                    "raw_metrics_device_used": raw, "eval_row_device_uuid": row_uuid, "cost_ledger": led}

        def eval_protocol(self, device: str) -> dict[str, Any]:
            from mbnas_core.protocol.presets import pilot_smoke_protocol

            ds = self.ds["coco128"]
            proto = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
            proto["training"].update(requested_input_size=[640, 640], batch_per_gpu=8, workers=0, warmup_epochs=0)
            proto["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
            proto["console"]["benchmark"]["device"] = device
            return proto

        def submit_and_wait(self, label: str, body: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
            t0 = time.monotonic()
            code, created, rid, _ = self.gw("POST", "/api/v1/training-runs", body)
            ok = code == 200 and isinstance(created, dict) and created.get("run_id")
            self.step(f"{label}-create", bool(ok), t0, http=code, request_id=rid,
                      body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id")})
            if not ok:
                return None, {"status": None}
            run = self.run_wait(created["run_id"], self.args.timeout)
            return created["run_id"], run

        # -- item a (strict device_used) -----------------------------------------------------------------------------
        def item_a(self) -> dict[str, Any]:
            run_id, _ = self.create_training("a-baseline-train", self.ds["small"], 2)
            t0 = time.monotonic()
            run = self.run_wait(run_id, self.args.timeout)
            self.evidence_for("rerun_a", run_id)
            f = self.device_facts(run_id, "BASELINE_TRAIN")
            checks = {
                "run_completed": run["status"] == "COMPLETED",
                "job_succeeded_on_gpu0_runner": bool(f["job"]) and f["job"]["status"] == "SUCCEEDED"
                and f["job"]["lease_owner"] == self.runner_id,
                "outputs.device_used==cuda:0": f["outputs_device_used"] == "cuda:0",
                "outputs.device_uuid==runner uuid": bool(self.gpu_uuid) and f["outputs_device_uuid"] == self.gpu_uuid,
                "raw_metrics.device_used==cuda:0": bool(f["raw_metrics_device_used"])
                and all(x == "cuda:0" for x in f["raw_metrics_device_used"]),
                "runner uuid == nvidia-smi GPU0": self.gpu_uuid == self.phys.get(0),
                "latency measured": f["latency_source"] == "measured",
                "payload/manifest uuid == runner uuid": f["payload_device_uuid"] == self.gpu_uuid
                and f["manifest_uuid"] == self.gpu_uuid,
            }
            ok = all(checks.values())
            self.step("a-device-used-strict", ok, t0, run_id=run_id, checks=checks, facts=f,
                      runner_gpu_uuid=self.gpu_uuid, nvidia_smi_uuids=self.phys)
            return {"verdict": "PASS" if ok else "FAIL", "run_id": run_id, "checks": checks}

        # -- item g (official EVALUATE payload device evidence) -------------------------------------------------------
        def item_g(self) -> dict[str, Any]:
            if not getattr(self, "g_ready", False):
                return {"verdict": "BLOCKED", "reason": "official weights / registry probe not ready"}
            body = {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": self.eval_protocol("host-gpu-0"),
                    "mode": "evaluate_only", "validation_enabled": True,
                    "idempotency_key": "t7r-g-eval-" + uuid.uuid4().hex[:8]}
            run_id, run = self.submit_and_wait("g-evaluate", body)
            if not run_id:
                return {"verdict": "FAIL", "reason": "create failed"}
            t0 = time.monotonic()
            self.evidence_for("rerun_g_evaluate", run_id)
            f = self.device_facts(run_id, "EVALUATE")
            checks = {
                "run_completed": run["status"] == "COMPLETED",
                "job_succeeded_on_gpu0_runner": bool(f["job"]) and f["job"]["status"] == "SUCCEEDED"
                and f["job"]["lease_owner"] == self.runner_id,
                "EVALUATED row": "EVALUATED" in f["evaluation_rows"],
                "ap > 0": isinstance(f["ap"], (int, float)) and f["ap"] > 0,
                "payload.device_uuid == runner uuid": bool(self.gpu_uuid) and f["payload_device_uuid"] == self.gpu_uuid,
                "latency.manifest present": f["manifest_present"],
                "latency.manifest.nvml_lookup == uuid": f["manifest_nvml_lookup"] == "uuid",
                "latency.manifest.uuid == runner uuid": f["manifest_uuid"] == self.gpu_uuid,
                "outputs.device_used==cuda:0": f["outputs_device_used"] == "cuda:0",
            }
            ok = all(checks.values())
            self.step("g-evaluate-payload-device", ok, t0, run_id=run_id, checks=checks, facts=f,
                      runner_gpu_uuid=self.gpu_uuid)
            return {"verdict": "PASS" if ok else "FAIL", "run_id": run_id, "checks": checks, "ap": f["ap"],
                    "ap_50": f["ap_50"], "latency_ms": f["latency_ms"]}

        # -- item f (f2: store-served downloads) -----------------------------------------------------------------------
        def item_f(self) -> dict[str, Any]:
            t0 = time.monotonic()
            store = self.root / "gateway" / "artifacts"
            in_store = {p.name for p in store.rglob("*") if p.is_file() and len(p.name) == 64 and p.suffix == ""}
            runs = [self.results.get(k, {}).get("run_id") for k in ("a", "g")]
            runs = [r for r in runs if r]
            states_before = {
                "artifacts": self.db_rows("SELECT id, run_id, kind, state FROM artifacts"),
                "checkpoints": self.db_rows("SELECT id, run_id, kind, state FROM checkpoints")}
            picks: list[tuple[str, dict[str, Any]]] = []
            for rid_ in runs:
                cks = self.db_rows("SELECT id, run_id, kind, epoch, sha256, bytes, state FROM checkpoints WHERE run_id = ? "
                                   "ORDER BY epoch DESC", (rid_,))
                for c in cks:
                    if str(c["sha256"]).removeprefix("sha256:") in in_store:
                        picks.append(("checkpoint", c))
                        break
                arts = self.db_rows("SELECT a.id, a.run_id, a.kind, a.relpath, a.sha256, a.bytes, a.state FROM artifacts a "
                                    "WHERE a.run_id = ? ORDER BY a.created_at, a.id", (rid_,))
                runner_ids = {r["object_id"] for r in self.db_rows(
                    "SELECT object_id FROM artifact_locations WHERE run_id = ? AND object_kind = 'artifact' AND "
                    "runner_id IS NOT NULL", (rid_,))}
                ev_arts = [a for a in arts if a["id"] in runner_ids
                           and str(a["sha256"] or "").removeprefix("sha256:") in in_store]
                # evaluation.json (kind other) first, then the sample panels
                ev_arts.sort(key=lambda a: (0 if str(a["relpath"]).endswith("evaluation.json") else 1, a["kind"]))
                picks += [("artifact", a) for a in ev_arts[:3]]
            downloads = []
            for what, rec in picks:
                code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{rec['run_id']}/artifacts/{rec['id']}", raw=True)
                hdr = (resp.headers.get("X-Artifact-Sha256") or "").removeprefix("sha256:")
                digest = hashlib.sha256(data).hexdigest() if code == 200 else None
                want = str(rec["sha256"] or "").removeprefix("sha256:")
                err = None
                if code != 200:
                    try:
                        err = json.loads(data)
                    except ValueError:
                        err = data[:300].decode(errors="replace")
                downloads.append({"object": what, "id": rec["id"], "run_id": rec["run_id"], "kind": rec["kind"],
                                  "relpath": rec.get("relpath"), "epoch": rec.get("epoch"), "http": code,
                                  "request_id": rid, "bytes": len(data) if code == 200 else None, "sha256": digest,
                                  "header_sha": hdr or None, "verification": resp.headers.get("X-Artifact-Verification"),
                                  "record_sha": want, "digest_match": bool(digest) and digest == want == hdr,
                                  "error": err})
            time.sleep(3)  # the gateway reports PRESENT@server_store best-effort after the response
            states_after = {
                "artifacts": self.db_rows("SELECT id, run_id, kind, state FROM artifacts"),
                "checkpoints": self.db_rows("SELECT id, run_id, kind, state FROM checkpoints")}
            missing = {k: [r for r in v if r["state"] == "MISSING"] for k, v in states_after.items()}
            before_map = {(k, r["id"]): r["state"] for k, v in states_before.items() for r in v}
            flipped = [{"table": k, **r, "before": before_map.get((k, r["id"]))} for k, v in states_after.items()
                       for r in v if before_map.get((k, r["id"])) not in (None, r["state"])]
            locs = self.db_rows("SELECT object_kind, object_id, store, runner_id, state, verified_by FROM "
                                "artifact_locations WHERE object_id IN (%s)" % ",".join("?" * len(picks)),
                                tuple(r["id"] for _, r in picks)) if picks else []
            ck_ok = [d for d in downloads if d["object"] == "checkpoint" and d["http"] == 200 and d["digest_match"]]
            ar_ok = [d for d in downloads if d["object"] == "artifact" and d["http"] == 200 and d["digest_match"]]
            checks = {"checkpoint downloaded with digest match": bool(ck_ok),
                      "evaluation artifact downloaded with digest match": bool(ar_ok),
                      "every picked download OK": bool(downloads) and all(d["http"] == 200 and d["digest_match"]
                                                                         for d in downloads),
                      "no artifact/checkpoint MISSING in DB": not missing["artifacts"] and not missing["checkpoints"],
                      "no state flipped by the downloads": not flipped}
            ok = all(checks.values())
            self.step("f2-download-via-gateway-store", ok, t0, checks=checks, downloads=downloads,
                      store_objects=len(in_store), missing=missing, flipped=flipped, locations_after=locs)
            self.save("dbviews", "rerun_f_states_after.json", {"states_after": states_after, "locations": locs})
            return {"verdict": "PASS" if ok else "FAIL", "checks": checks,
                    "downloads": [{k: d.get(k) for k in ("object", "kind", "http", "digest_match", "bytes")}
                                  for d in downloads]}

        # -- item i (runner on GPU 1) -----------------------------------------------------------------------------------
        def stop_proc(self, name: str) -> dict[str, Any]:
            p = self.procs.get(name)
            if p is None or p.poll() is not None:
                return {"name": name, "already_exited": True}
            p._expected_exit = True  # type: ignore[attr-defined]
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                import os
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=30)
            return {"name": name, "pid": p.pid, "exit": p.returncode}

        def item_i(self) -> dict[str, Any]:
            t0 = time.monotonic()
            stopped = self.stop_proc("runner")
            self.gpu_windows.append({"end_utc": utc(), "label": "gpu0-runner-stopped-for-i"})
            gpu0_uuid = self.gpu_uuid
            self.gpu = 1
            self.runner_id = self.gpu1_runner_id
            env_name = "runner1"
            # the GPU-1 runner: its own principal, token file, work root (runner_env(name) -> <root>/runner1)
            self.principals(((env_name, "runner", ["--runner-id", self.gpu1_runner_id]),))
            orig_env = self.runner_env

            def runner_env(name: str = env_name, runner_id: str = "") -> dict[str, str]:
                return orig_env(env_name, self.gpu1_runner_id)

            self.runner_env = runner_env  # type: ignore[method-assign]
            row = self.start_runner("gpu1-runner-start")
            gpu1_uuid = self.gpu_uuid
            reg_ok = gpu1_uuid == self.phys.get(1) and gpu1_uuid != gpu0_uuid
            self.step("i-gpu1-runner-registered", reg_ok, t0, stopped_gpu0_runner=stopped,
                      registered={k: row.get(k) for k in ("id", "device", "device_uuid", "device_name")},
                      nvidia_smi_uuids=self.phys, gpu0_runner_uuid=gpu0_uuid)
            out: dict[str, Any] = {"registered_ok": reg_ok, "gpu1_uuid": gpu1_uuid}
            # (1) short BASELINE_TRAIN (training evaluation) on host-gpu-1
            body = self.training_body("i-train-gpu1", self.ds["small"], 2)
            body["protocol"]["console"]["benchmark"]["device"] = "host-gpu-1"
            run_t, run = self.submit_and_wait("i-train-gpu1", body)
            ft = self.device_facts(run_t, "BASELINE_TRAIN") if run_t else {}
            if run_t:
                self.evidence_for("rerun_i_train", run_t)
            ct = {"run_completed": run.get("status") == "COMPLETED",
                  "claimed by t7-gpu1": bool(ft.get("job")) and ft["job"]["lease_owner"] == self.gpu1_runner_id,
                  "outputs.device_uuid == GPU1": ft.get("outputs_device_uuid") == gpu1_uuid,
                  "payload.device_uuid == GPU1": ft.get("payload_device_uuid") == gpu1_uuid,
                  "manifest.uuid == GPU1": ft.get("manifest_uuid") == gpu1_uuid,
                  "manifest.nvml_lookup == uuid": ft.get("manifest_nvml_lookup") == "uuid",
                  "outputs.device_used == cuda:0": ft.get("outputs_device_used") == "cuda:0",
                  "cost_ledger uuid == GPU1": bool(ft.get("cost_ledger"))
                  and all(c["device_uuid"] == gpu1_uuid for c in ft["cost_ledger"]),
                  "no GPU0 uuid anywhere": gpu0_uuid not in json.dumps(ft, default=str)}
            self.step("i-train-gpu1-device", all(ct.values()), time.monotonic(), run_id=run_t, checks=ct, facts=ft,
                      gpu1_uuid=gpu1_uuid, gpu0_uuid=gpu0_uuid)
            # (2) official EVALUATE on host-gpu-1
            ce: dict[str, bool] = {}
            run_e = None
            if getattr(self, "g_ready", False):
                body = {"source": {"kind": "baseline", "baseline_id": "yolo11n"},
                        "protocol": self.eval_protocol("host-gpu-1"), "mode": "evaluate_only", "validation_enabled": True,
                        "idempotency_key": "t7r-i-eval-" + uuid.uuid4().hex[:8]}
                run_e, run = self.submit_and_wait("i-evaluate-gpu1", body)
                fe = self.device_facts(run_e, "EVALUATE") if run_e else {}
                if run_e:
                    self.evidence_for("rerun_i_evaluate", run_e)
                ce = {"run_completed": run.get("status") == "COMPLETED",
                      "claimed by t7-gpu1": bool(fe.get("job")) and fe["job"]["lease_owner"] == self.gpu1_runner_id,
                      "payload.device_uuid == GPU1": fe.get("payload_device_uuid") == gpu1_uuid,
                      "manifest.uuid == GPU1": fe.get("manifest_uuid") == gpu1_uuid,
                      "manifest.nvml_lookup == uuid": fe.get("manifest_nvml_lookup") == "uuid",
                      "no GPU0 uuid anywhere": gpu0_uuid not in json.dumps(fe, default=str)}
                self.step("i-evaluate-gpu1-device", all(ce.values()), time.monotonic(), run_id=run_e, checks=ce,
                          facts=fe, gpu1_uuid=gpu1_uuid, gpu0_uuid=gpu0_uuid)
            ok = reg_ok and all(ct.values()) and bool(ce) and all(ce.values())
            out.update(verdict="PASS" if ok else "FAIL", train_run=run_t, train_checks=ct, eval_run=run_e,
                       eval_checks=ce)
            return out

        def run(self) -> int:
            rc = super().run()
            # the base writes t7_summary_<stamp>.json; keep it under the rerun name as well
            src = self.ev / f"t7_summary_{self.stamp}.json"
            if src.is_file():
                src.rename(self.ev / f"t7_rerun_summary_{self.stamp}.json")
            return rc

    return Rerun


def make_full(wp3: Any) -> type:
    """``--mode full`` (DD-A54): the first run's items of ``t7_gpu_path.py`` UNCHANGED (a,b,h,c,d,e,f), only the evidence
    sinks differ so the first run's files are never overwritten: steps -> ``steps_rerun.jsonl``, every saved
    snapshot/DB view name prefixed ``rerun_full_`` (the base writes the fixed name ``f_store_listing.json``), summary ->
    ``t7_rerun_full_summary_<stamp>.json``."""
    Base = t7.make_harness(wp3)

    class Full(Base):
        def __init__(self, args: argparse.Namespace) -> None:
            super().__init__(args)
            self.steps_path = self.ev / "steps_rerun.jsonl"

        def save(self, sub: str, name: str, obj: Any) -> Path:
            return super().save(sub, "rerun_full_" + name, obj)

        def run(self) -> int:
            rc = super().run()
            src = self.ev / f"t7_summary_{self.stamp}.json"
            if src.is_file():
                src.rename(self.ev / f"t7_rerun_full_summary_{self.stamp}.json")
            return rc

    return Full


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--g2-root", required=True)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--tree-hash", required=True)
    ap.add_argument("--mode", choices=("rerun", "full"), default="rerun",
                    help="rerun: items a,g,f,i of this file; full: the unchanged t7_gpu_path.py items (DD-A54)")
    ap.add_argument("--items", default=None, help="default a,g,f,i (rerun) / a,b,h,c,d,e,f (full)")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--search-timeout", type=float, default=None, help="full mode, item b (default = --timeout)")
    ap.add_argument("--official-weights", default=None)
    ap.add_argument("--coco128", default=None)
    a = ap.parse_args()
    items = a.items or ("a,g,f,i" if a.mode == "rerun" else "a,b,h,c,d,e,f")
    if a.mode == "rerun" and not (a.official_weights and a.coco128):
        raise SystemExit("--mode rerun needs --official-weights and --coco128")
    g2 = Path(a.g2_root).resolve()
    if not Path(sys.prefix).resolve().is_relative_to(g2):
        raise SystemExit(f"run with the frozen copy's interpreter (got prefix {sys.prefix})")
    wp3 = t7.load_wp3(g2)
    R = make_rerun(wp3) if a.mode == "rerun" else make_full(wp3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args = argparse.Namespace(port=0, gw_port=0, root=str(g2 / "runtime" / "t7" / stamp), timeout=a.timeout,
                              web_dist=str(g2 / "mbnas_web" / "dist"), device_source="runners",
                              evidence=str(Path(a.evidence_dir) / "unused.txt"), evidence_dir=a.evidence_dir,
                              tree_hash=a.tree_hash, gpu=0, items=items, search_timeout=a.search_timeout or a.timeout,
                              official_weights=a.official_weights, coco128=a.coco128)
    h = R(args)
    h.started_utc = utc()
    return h.run()


if __name__ == "__main__":
    raise SystemExit(main())
