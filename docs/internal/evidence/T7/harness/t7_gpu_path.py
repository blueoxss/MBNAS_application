#!/usr/bin/env python3
"""T7 GPU path harness (WP-G2 item G2-1): real core + owner worker + gateway + GPU runner processes, driven through the
gateway's user ``/api/v1`` with the user bearer (direct core calls only for the admin runner list and for the runner
replay check of item e; DB reads are read-only verification).

Runs from the FROZEN copy (``--g2-root``, ``runtime/test_root/g2_tree``) with the copy's interpreter
(``<g2>/runtime/venv/bin/python``); reuses the copy's ``scripts/dev/wp3_three_process_smoke.py`` (Smoke3) plumbing.
Evidence (steps.jsonl with UTC times/request ids/pids/exit codes, per-item snapshot + DB view JSON, process log copies,
nvidia-smi before/after) goes to ``--evidence-dir`` (the LIVE tree's docs/internal/evidence/T7). Every JSON record
carries ``tree_hash`` (``--tree-hash``). No secret is written to the evidence: the user/service/runner tokens live only
under the work root (0600) and are redacted from every evidence text.

Items (``--items``, default a,b,h,c,d,e,f):
  a  BASELINE_TRAIN (explicit training, user yolo11n YAML nc=1, 2 epochs, 128 px, validation on) on cuda:0
  b  search run, evaluation ON (2 rungs L0=1/L1=2 epochs, population 2, generations 2, seeds 101/202, GA+Random)
  h  FINAL_RETRAIN of the item-b run (top_k 1 per arm/seed, final recipe 2 epochs)
  c  pause during GPU training -> PAUSED_AT_SAFE_BOUNDARY -> resume -> full-state resume
  d  cancel during GPU training
  e  SIGKILL the runner daemon mid-job -> lease expiry -> sweeper requeue -> restarted runner claims attempt+1;
     stale attempt-1 commit rejected (journal replay by the restarted daemon + explicit replay); attempt-2 result
     replay idempotent
  f  artifact relay: runner local transfer into the gateway store (DD-A39 layout, digests) + downloads through the
     gateway with digest match
Network guard: HTTPS_PROXY=http://127.0.0.1:9 (+NO_PROXY loopback), YOLO_OFFLINE=1, HF_HUB_OFFLINE=1 for every spawned
process, so no weight/asset download can happen (ultralytics' AMP check then reports "checks skipped", AMP stays on).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "PAUSED"}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def nvsmi() -> dict[str, Any]:
    out: dict[str, Any] = {"utc": utc()}
    for key, q in (("gpus", "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu"),
                   ("compute_apps", "--query-compute-apps=pid,process_name,used_memory,gpu_uuid")):
        p = subprocess.run(["nvidia-smi", q, "--format=csv,noheader"], capture_output=True, text=True, timeout=60)
        out[key] = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        out[key + "_exit"] = p.returncode
    return out


def build_shapes(data_root: Path, name: str, counts: dict[str, int], size: int, seed: int) -> dict[str, str]:
    """SYNTHETIC 1-class detection set: dark random background + 1-3 bright axis-aligned boxes per image (class 0)."""
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    root = data_root / name
    for split, n in counts.items():
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img = Image.new("RGB", (size, size), tuple(rng.randrange(0, 70) for _ in range(3)))
            d = ImageDraw.Draw(img)
            for _ in range(rng.randint(10, 30)):  # clutter dots (not labelled)
                x, y = rng.randrange(size), rng.randrange(size)
                d.point((x, y), fill=tuple(rng.randrange(0, 120) for _ in range(3)))
            lines = []
            for _ in range(rng.randint(1, 3)):
                w, h = rng.randint(size // 8, size // 3), rng.randint(size // 8, size // 3)
                x0, y0 = rng.randint(0, size - w), rng.randint(0, size - h)
                d.rectangle([x0, y0, x0 + w - 1, y0 + h - 1],
                            fill=(rng.randint(160, 255), rng.randint(160, 255), rng.randint(0, 90)))
                lines.append(f"0 {(x0 + w / 2) / size:.6f} {(y0 + h / 2) / size:.6f} {w / size:.6f} {h / size:.6f}")
            img.save(root / "images" / split / f"{split}_{i:04d}.png")
            (root / "labels" / split / f"{split}_{i:04d}.txt").write_text("\n".join(lines) + "\n")
    (root / "data.yaml").write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\ntest: images/test\nnames: [\"box\"]\n")
    return {"root": str(root), "dataset_yaml": str(root / "data.yaml"), "train_root": str(root / "images/train"),
            "search_val_root": str(root / "images/val"), "final_eval_root": str(root / "images/test"),
            "counts": counts, "size": size, "seed": seed}


def load_wp3(g2: Path) -> Any:
    spec = importlib.util.spec_from_file_location("wp3_three_process_smoke", g2 / "scripts/dev/wp3_three_process_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def jl(text: Any) -> Any:
    if isinstance(text, (dict, list)) or text is None:
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def make_harness(wp3: Any) -> type:
    PY = wp3.PY

    class T7(wp3.Smoke3):
        def __init__(self, args: argparse.Namespace) -> None:
            super().__init__(args)
            self.ev = Path(args.evidence_dir)
            for sub in ("logs", "snapshots", "dbviews"):
                (self.ev / sub).mkdir(parents=True, exist_ok=True)
            self.stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.steps_path = self.ev / "steps.jsonl"
            self.transcript_path = self.ev / "logs" / f"t7_transcript_{self.stamp}.log"
            self.transcript = open(self.transcript_path, "a", encoding="utf-8")
            self.transcript.write(f"# TREE_HASH {args.tree_hash}\n# harness {Path(__file__).resolve()}\n"
                                  f"# g2 root {wp3.ROOT}  interpreter {PY}  work root {self.root}\n")
            self.tree_hash = args.tree_hash
            self.gpu = args.gpu
            self.runner_id = f"t7-gpu{args.gpu}"
            self.item = "setup"
            self.results: dict[str, dict[str, Any]] = {}
            self.ds: dict[str, dict[str, Any]] = {}
            self.upload_id: str | None = None
            self.gpu_uuid: str | None = None
            self.search_run: str | None = None
            self.gpu_windows: list[dict[str, Any]] = []

        # -- recording ------------------------------------------------------------------------------------------
        def redact(self, text: str) -> str:
            for secret in (self.user_token, self.api_token, *self.tokens.values()):
                if secret:
                    text = text.replace(secret, "<redacted>")
            return text

        def note(self, msg: str) -> None:
            super().note(msg)
            self.transcript.write(self.redact(f"{wp3.now()} [{self.item}] {msg}") + "\n")
            self.transcript.flush()

        def step(self, name: str, ok: bool, t0: float, **detail: Any) -> bool:
            res = super().step(name, ok, t0, **detail)
            rec = {"utc": utc(), "tree_hash": self.tree_hash, "item": self.item, **self.steps[-1]}
            with open(self.steps_path, "a", encoding="utf-8") as fh:
                fh.write(self.redact(json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False)) + "\n")
            return res

        def save(self, sub: str, name: str, obj: Any) -> Path:
            path = self.ev / sub / name
            text = json.dumps({"tree_hash": self.tree_hash, "item": self.item, "saved_utc": utc(), "data": obj},
                              indent=1, sort_keys=True, default=str, ensure_ascii=False)
            path.write_text(self.redact(text) + "\n", encoding="utf-8")
            return path

        def smi(self, label: str) -> dict[str, Any]:
            snap = nvsmi()
            t0 = time.monotonic()
            self.step(f"nvidia-smi-{label}", snap["gpus_exit"] == 0, t0, nvidia_smi=snap)
            return snap

        # -- env ------------------------------------------------------------------------------------------------
        def base_env(self) -> dict[str, str]:
            env = super().base_env()
            guard = "http://127.0.0.1:9"  # discard port: every https download fails fast (no weights/assets fetched)
            env.update(HTTPS_PROXY=guard, https_proxy=guard, NO_PROXY="127.0.0.1,localhost",
                       no_proxy="127.0.0.1,localhost", HF_HUB_OFFLINE="1", YOLO_OFFLINE="1",
                       TORCH_HOME=str(self.root / "torch_home"))
            return env

        def runner_env(self, name: str = "runner", runner_id: str = "") -> dict[str, str]:
            env = super().runner_env(name, runner_id or self.runner_id)
            env.update(CUDA_VISIBLE_DEVICES=str(self.gpu), MBNAS_RUNNER_GPU_INDEX=str(self.gpu),
                       MBNAS_RUNNER_ARTIFACT_TRANSFER="local",
                       MBNAS_RUNNER_ARTIFACT_STORE_DIR=str(self.root / "gateway" / "artifacts"),
                       MBNAS_RUNNER_KILL_GRACE_S="10", MBNAS_RUNNER_GPU_CLEAR_TIMEOUT_S="30")
            return env

        # -- setup ----------------------------------------------------------------------------------------------
        def build_inputs(self) -> dict[str, str]:  # noqa: D401 - replaces Smoke3.build_inputs
            t0 = time.monotonic()
            core_data = self.root / "core_data"
            self.ds["small"] = build_shapes(core_data, "t7shapes", {"train": 64, "val": 16, "test": 16}, 160, 2026)
            self.ds["big"] = build_shapes(core_data, "t7shapes_big", {"train": 480, "val": 16, "test": 16}, 160, 2027)
            if "g" in self.args.items.split(",") and self.args.coco128:
                self.ds["coco128"] = self.copy_coco128(Path(self.args.coco128), core_data / "coco128")
            shutil.copytree(core_data, self.root / "runner_data")
            import ultralytics

            text = (Path(ultralytics.__file__).parent / "cfg" / "models" / "11" / "yolo11.yaml").read_text()
            (self.root / "uploads").mkdir(parents=True, exist_ok=True)
            (self.root / "uploads" / "yolo11n.yaml").write_text(text.replace("nc: 80", "nc: 1\nscale: n"))
            (self.root / "weights").mkdir(parents=True, exist_ok=True)
            (self.root / "gateway" / "artifacts").mkdir(parents=True, exist_ok=True)
            self.step("inputs", True, t0, datasets={k: {kk: v[kk] for kk in ("dataset_yaml", "counts", "size", "seed")}
                                                    for k, v in self.ds.items()},
                      note="SYNTHETIC shapes datasets (160 px PNG, 1 class); runner byte copy via dataset mounts")
            return self.ds["small"]

        def start_workers(self) -> None:
            t0 = time.monotonic()
            self.spawn("owner", [PY, "-B", "-m", "mbnas_core.owner_worker", "--owner", "--poll-s", "0.5",
                                 "--worker-id", "t7-owner-1"], self.core_env())
            self.step("owner-start", True, t0, owner_pid=self.procs["owner"].pid)
            self.start_runner()

        def start_runner(self, label: str = "runner-start") -> dict[str, Any]:
            t0 = time.monotonic()
            self.spawn("runner", [PY, "-B", "-m", "mbnas_runner", "run"], self.runner_env())
            row = self.wait("runner registration", lambda: self.registered(self.runner_id), timeout=240)
            self.gpu_uuid = row.get("device_uuid")
            self.gpu_windows.append({"start_utc": utc(), "pid": self.procs["runner"].pid, "label": label})
            self.step(label, True, t0, runner_pid=self.procs["runner"].pid,
                      runner={k: row.get(k) for k in ("id", "status", "kinds", "device", "device_uuid", "device_name",
                                                       "data_mounts")},
                      runtime_verification=(row.get("runtime") or {}).get("verification"))
            return row

        def registered(self, runner_id: str) -> dict[str, Any] | None:
            import httpx

            r = httpx.get(self.base + "/core/v1/owner/runners", headers={"Authorization": f"Bearer {self.tokens['admin']}"},
                          timeout=30)
            rows = [x for x in (r.json() or {}).get("runners", []) if x["id"] == runner_id] if r.status_code == 200 else []
            return rows[0] if rows and rows[0]["status"] == "ONLINE" else None

        def runner_child_pids(self) -> list[int]:
            p = self.procs.get("runner")
            if p is None:
                return []
            out = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True)
            return [int(x) for x in out.stdout.split()]

        # -- DB views (read-only) -----------------------------------------------------------------------------------
        def jobs_view(self, run_id: str) -> list[dict[str, Any]]:
            out = []
            for j in self.db_rows("SELECT id, kind, status, attempt, max_attempts, lease_owner, candidate_id, rung, "
                                  "cumulative_target, started_at, finished_at, error_json, result_json FROM jobs "
                                  "WHERE run_id = ? ORDER BY created_at, id", (run_id,)):
                err, res = jl(j.pop("error_json")) or {}, jl(j.pop("result_json")) or {}
                outs = res.get("outputs") or {}
                ev = outs.get("evaluation") or {}
                rr = outs.get("rung_result") or {}
                j.update(error=err if err else None, result_status=res.get("status"),
                         result_reason=res.get("reason_code"), result_message=str(res.get("message") or "")[:400],
                         outputs_keys=sorted(outs), device_used=outs.get("device_used"),
                         gpu_seconds=res.get("gpu_seconds"),
                         rung_result={k: rr.get(k) for k in ("epochs_done", "resumed_from", "step_count",
                                                              "samples_seen", "stopped_reason", "notes",
                                                              "cumulative_target") if k in rr},
                         evaluation={k: ev.get(k) for k in ("ap", "ap_50", "recall", "latency_ms", "latency_source",
                                                             "latency_scope", "size_mb", "device", "device_uuid",
                                                             "device_name", "precision", "eval_split", "epochs",
                                                             "evaluation_hash") if k in ev},
                         artifact_transfer=outs.get("artifact_transfer"), timings=outs.get("timings"))
                out.append(j)
            return out

        def evals_view(self, run_id: str) -> list[dict[str, Any]]:
            out = []
            for e in self.db_rows("SELECT id, candidate_id, rung, epochs, status, revision, immutable, row_json "
                                  "FROM evaluations WHERE run_id = ? ORDER BY created_at, id", (run_id,)):
                row = jl(e.pop("row_json")) or {}
                raw = row.get("raw_metrics") or {}
                keep = {k: row.get(k) for k in ("fitness", "fitness_rung", "latency_source", "latency_ms", "ap",
                                                 "ap_50", "recall", "size_mb", "gate", "promotion", "decision",
                                                 "reason_code", "performance_status", "device", "device_uuid",
                                                 "device_name", "arm", "search_seed", "generation", "comparison_group",
                                                 "training_mode", "checkpoint_path") if k in row}
                keep["raw_metrics_device_used"] = raw.get("device_used")
                keep["row_keys"] = sorted(row)
                out.append({**e, **keep})
            return out

        def db_view(self, run_id: str) -> dict[str, Any]:
            return {
                "run": self.db_rows("SELECT id, kind, status, reason_code, parent_run_id, protocol_hash, created_at, "
                                    "updated_at FROM runs WHERE id = ?", (run_id,)) if self._has_col("runs", "reason_code")
                else self.db_rows("SELECT id, status, created_at, updated_at FROM runs WHERE id = ?", (run_id,)),
                "jobs": self.jobs_view(run_id), "evaluations": self.evals_view(run_id),
                "checkpoints": self.db_rows("SELECT id, candidate_id, rung, epoch, kind, state, sha256, bytes, path "
                                            "FROM checkpoints WHERE run_id = ? ORDER BY created_at, id", (run_id,)),
                "artifacts": self.db_rows("SELECT id, candidate_id, evaluation_id, kind, relpath, sha256, bytes, state "
                                          "FROM artifacts WHERE run_id = ? ORDER BY created_at, id", (run_id,)),
                "artifact_locations": self.db_rows("SELECT object_kind, object_id, store, runner_id, state, verified_by, "
                                                   "sha256, bytes, relpath, job_id, attempt FROM artifact_locations "
                                                   "WHERE run_id = ? ORDER BY created_at, id", (run_id,)),
                "cost_ledger": self.db_rows("SELECT arm, candidate_id, job_id, attempt, kind, gpu_seconds, physical, "
                                            "device_uuid, started_at, finished_at FROM cost_ledger WHERE run_id = ? "
                                            "ORDER BY id", (run_id,)),
                "idempotent_responses": self.db_rows(
                    "SELECT i.job_id, i.attempt, i.runner_id, i.state, i.status_code, i.committed_at, i.acked_at, "
                    "i.payload_digest FROM idempotent_responses i JOIN jobs j ON j.id = i.job_id WHERE j.run_id = ? "
                    "ORDER BY i.processing_since", (run_id,)),
                "runners": self.db_rows("SELECT id, status, device, device_uuid, device_name, last_seen_at FROM runners"),
            }

        def _has_col(self, table: str, col: str) -> bool:
            return any(r["name"] == col for r in self.db_rows(f"PRAGMA table_info({table})"))

        def run_wait(self, run_id: str, timeout: float, statuses: set[str] = TERMINAL) -> dict[str, Any]:
            return self.wait(f"run {run_id} in {sorted(statuses)}", lambda: (lambda r: r if r and r.get("status") in
                             statuses else None)(self.run_status(run_id)), timeout=timeout, interval=3.0)

        @staticmethod
        def trim(obj: Any, dropped: dict[str, int]) -> Any:
            """Copy of a snapshot without the bulky per-sample arrays (``per_sample``, latency ``samples``)."""
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if k in ("per_sample", "samples") and isinstance(v, (list, dict)):
                        dropped[k] = dropped.get(k, 0) + 1
                        out[k] = f"<trimmed: {len(v)} entries>"
                    else:
                        out[k] = T7.trim(v, dropped)
                return out
            if isinstance(obj, list):
                return [T7.trim(v, dropped) for v in obj]
            return obj

        def evidence_for(self, label: str, run_id: str) -> dict[str, Any]:
            snap = self.snapshot(run_id)
            dropped: dict[str, int] = {}
            snap = self.trim(snap, dropped)
            if isinstance(snap, dict):
                snap["_harness_trimmed_keys"] = dropped
            self.save("snapshots", f"{label}_{run_id}.json", snap)
            view = self.db_view(run_id)
            self.save("dbviews", f"{label}_{run_id}.json", view)
            return view

        def epoch_reached(self, run_id: str, epoch: int) -> dict[str, Any] | None:
            rows = self.db_rows("SELECT epoch, kind FROM checkpoints WHERE run_id = ? AND kind = 'training_full' "
                                "ORDER BY epoch DESC LIMIT 1", (run_id,))
            return rows[0] if rows and int(rows[0]["epoch"]) >= epoch else None

        # -- requests -------------------------------------------------------------------------------------------
        def upload_yaml(self) -> str:
            if self.upload_id is None:
                body = self.upload("yolo11n.yaml")
                self.upload_id = body["upload_id"]
            return self.upload_id

        def training_body(self, label: str, ds: dict[str, Any], epochs: int, *, validation: bool = True,
                          imgsz: int = 128) -> dict[str, Any]:
            from mbnas_core.protocol.presets import pilot_smoke_protocol

            proto = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
            proto["training"].update(requested_input_size=[imgsz, imgsz], batch_per_gpu=8, workers=0, warmup_epochs=0)
            proto["console"]["training"] = {"epoch_target_total": epochs, "seed": 41, "native_image_artifacts": True}
            return {"source": {"kind": "user", "upload_id": self.upload_yaml()}, "protocol": proto, "mode": "scratch",
                    "validation_enabled": validation, "idempotency_key": f"t7-{label}-{uuid.uuid4().hex[:8]}"}

        def create_training(self, label: str, ds: dict[str, Any], epochs: int, *, validation: bool = True) -> tuple[str, str]:
            t0 = time.monotonic()
            code, created, rid, _ = self.gw("POST", "/api/v1/training-runs", self.training_body(label, ds, epochs,
                                                                                               validation=validation))
            ok = code == 200 and isinstance(created, dict) and created.get("run_id")
            self.step(f"{label}-create", bool(ok), t0, http=code, request_id=rid,
                      body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id",
                                                                                "protocol_hash", "created")})
            if not ok:
                raise RuntimeError(f"training-runs {label} -> {code} {str(created)[:500]}")
            return created["run_id"], rid

        # -- item g inputs ----------------------------------------------------------------------------------------
        def copy_coco128(self, src: Path, dst: Path) -> dict[str, Any]:
            """REAL COCO images (coco128: 128 train2017 images + labels; local copy, read only) for official evaluation."""
            import yaml
            import ultralytics

            for sub in ("images/train2017", "labels/train2017"):
                (dst / sub).mkdir(parents=True, exist_ok=True)
                for f in sorted((src / sub).iterdir()):
                    if f.is_file() and f.suffix != ".cache":
                        shutil.copyfile(f, dst / sub / f.name)
            cfg = yaml.safe_load((Path(ultralytics.__file__).parent / "cfg/datasets/coco128.yaml").read_text())
            names = [cfg["names"][i] for i in sorted(cfg["names"])]
            (dst / "data.yaml").write_text(f"path: {dst}\ntrain: images/train2017\nval: images/train2017\n"
                                           f"test: images/train2017\nnames: {json.dumps(names)}\n")
            return {"root": str(dst), "dataset_yaml": str(dst / "data.yaml"), "counts": {"train2017": 128},
                    "size": "original", "seed": None, "source": str(src), "names": len(names)}

        def prepare_g(self) -> dict[str, Any]:
            """Before core starts: official yolo11n.pt (sha256 checked against the registry example's recorded
            weights_sha256; copied from a local file, never downloaded), weights_manifest.json, CPU registry probe."""
            t0 = time.monotonic()
            src = Path(self.args.official_weights) if self.args.official_weights else None
            reg = json.loads((wp3.ROOT / "mbnas_core/src/mbnas_core/registry/model_registry.example.json").read_text())
            ent = next(e for e in reg["registry"] if e["id"] == "yolo11n")["evidence"]
            if src is None or not src.is_file():
                self.step("g-official-weights", False, t0, reason="no local official yolo11n.pt (BLOCKED_BY_ENV)")
                return {"ok": False}
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            ok_digest = digest == ent["weights_sha256"] and src.stat().st_size == ent["weights_bytes"]
            wdir = self.root / "weights"
            shutil.copyfile(src, wdir / "yolo11n.pt")
            (wdir / "weights_manifest.json").write_text(json.dumps({"yolo11n.pt": {
                "sha256": ent["weights_sha256"], "bytes": ent["weights_bytes"], "source_url": ent["weights_source_url"],
                "digest_verification": "registry example evidence.weights_sha256 (model_registry.example.json)"}}, indent=1))
            env = self.base_env()
            env["CUDA_VISIBLE_DEVICES"] = ""
            p = subprocess.run([PY, "-B", str(wp3.ROOT / "scripts/owner/probe_registry.py"), "--only", "yolo11n",
                                "--output-root", str(self.root / "core" / "runs"), "--weights-dir", str(wdir)],
                               env=env, cwd=str(wp3.ROOT), capture_output=True, text=True, timeout=900)
            (self.ev / "logs" / f"{self.stamp}_probe_registry.log").write_text(
                f"# TREE_HASH {self.tree_hash}\n# exit {p.returncode}\n" + p.stdout[-20000:] + "\n--- stderr ---\n"
                + p.stderr[-20000:])
            regs = sorted((self.root / "core" / "runs" / "registry").glob("registry_*.json"))
            status = None
            if regs:
                doc = json.loads(regs[-1].read_text())
                status = next((e["status"] for e in doc["registry"] if e["id"] == "yolo11n"), None)
            ok = ok_digest and status == "AVAILABLE"
            self.step("g-official-weights-and-probe", ok, t0, source=str(src), sha256=digest,
                      registry_example_sha256=ent["weights_sha256"], digest_match=ok_digest, probe_exit=p.returncode,
                      yolo11n_registry_status=status,
                      note="probe exit 2 is expected: every other registry entry stays UNVERIFIED (only yolo11n probed)")
            return {"ok": ok}

        def item_g(self) -> dict[str, Any]:
            if not getattr(self, "g_ready", False):
                return {"verdict": "BLOCKED", "reason": "official weights / registry probe not ready (see g-official-weights)"}
            from mbnas_core.protocol.presets import pilot_smoke_protocol

            out: dict[str, Any] = {}
            # (g1) EVALUATE: official evaluation-only of the verified yolo11n baseline, through the gateway
            t0 = time.monotonic()
            ds = self.ds["coco128"]
            proto = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
            proto["training"].update(requested_input_size=[640, 640], batch_per_gpu=8, workers=0, warmup_epochs=0)
            proto["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
            body = {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "evaluate_only",
                    "validation_enabled": True, "idempotency_key": "t7-g-eval-" + uuid.uuid4().hex[:8]}
            code, created, rid, _ = self.gw("POST", "/api/v1/training-runs", body)
            ok_c = code == 200 and isinstance(created, dict) and created.get("run_id")
            self.step("g-evaluate-create", bool(ok_c), t0, http=code, request_id=rid, body=created)
            ev_ok = False
            if ok_c:
                run_id = created["run_id"]
                t1 = time.monotonic()
                run = self.run_wait(run_id, self.args.timeout)
                view = self.evidence_for("g_evaluate", run_id)
                jobs = [j for j in view["jobs"] if j["kind"] == "EVALUATE"]
                evs = [e for e in view["evaluations"] if e["status"] == "EVALUATED"]
                ev = (jobs[-1]["evaluation"] if jobs else {}) or {}
                # device evidence: the official-evaluation payload carries no device_uuid/latency manifest (open issue);
                # the attempt's cost row records the committing runner's device uuid
                led = [c for c in view["cost_ledger"] if jobs and c["job_id"] == jobs[-1]["id"]]
                dev_ok = bool(led) and all(c["device_uuid"] == self.gpu_uuid for c in led)
                ev_ok = (run["status"] == "COMPLETED" and jobs and jobs[-1]["status"] == "SUCCEEDED" and bool(evs)
                         and isinstance(ev.get("ap"), (int, float)) and ev.get("ap", 0) > 0
                         and ev.get("latency_source") == "measured" and dev_ok)
                self.step("g-evaluate-official-yolo11n", bool(ev_ok), t1, run_id=run_id, run_status=run["status"],
                          jobs=[{k: j[k] for k in ("id", "kind", "status", "attempt", "lease_owner", "result_reason",
                                                    "result_message")} for j in jobs],
                          evaluation=ev, evaluated_rows=len(evs), cost_ledger=led, runner_gpu_uuid=self.gpu_uuid,
                          payload_device_uuid=ev.get("device_uuid"),
                          row=[{k: e.get(k) for k in ("ap", "fitness", "latency_ms", "latency_source", "comparison_group")}
                               for e in evs])
                out["evaluate"] = {"run_id": run_id, "ok": bool(ev_ok), "ap": ev.get("ap"), "ap_50": ev.get("ap_50"),
                                   "latency_ms": ev.get("latency_ms")}
            # (g2) BENCHMARK: the runner child (the entry the daemon spawns) with a BENCHMARK JobSpec; core never
            # creates BENCHMARK jobs in this tree (runner_api/envelope.py), so the queue path cannot be exercised
            t2 = time.monotonic()
            bdir = self.root / "runner" / "work" / "benchmark"
            bdir.mkdir(parents=True, exist_ok=True)
            names = {str(i): n for i, n in enumerate(json.loads(
                (self.root / "runner_data" / "coco128" / "data.yaml").read_text().split("names: ", 1)[1]))}
            spec = {"job_id": "job-t7-bench-yolo11n", "run_id": "standalone-t7-benchmark", "kind": "BENCHMARK",
                    "protocol_hash": "standalone:t7_gpu_path", "attempt": 1,
                    "params": {"device": "host-gpu-0", "imgsz": 640, "warmup": 20, "repeats": 100,
                               "subject": {"backend": "ultralytics", "source": "official_weights", "family": "YOLO11",
                                           "path": str(self.root / "weights" / "yolo11n.pt")},
                               "split": {"root": str(self.root / "runner_data" / "coco128" / "images" / "train2017"),
                                         "names": names},
                               "output_dir": str(bdir / "yolo11n")}}
            spec_path = bdir / "spec.json"
            spec_path.write_text(json.dumps(spec, indent=1))
            env = self.runner_env()
            env.update(MBNAS_RUNNER_WORK_ROOT=str(self.root / "runner" / "work"),
                       MBNAS_RUNNER_OUTPUT_ROOTS=json.dumps([str(self.root / "runner" / "work")]),
                       MBNAS_RUNNER_ARTIFACT_TRANSFER="none")
            env.pop("MBNAS_RUNNER_TOKEN_FILE", None)
            log = self.ev / "logs" / f"{self.stamp}_benchmark_child.log"
            with open(log, "w") as fh:
                fh.write(f"# TREE_HASH {self.tree_hash}\n# {PY} -B -m mbnas_runner --spec-file {spec_path}\n")
                fh.flush()
                p = subprocess.run([PY, "-B", "-m", "mbnas_runner", "--spec-file", str(spec_path)], env=env,
                                   cwd=str(wp3.ROOT), stdout=subprocess.PIPE, stderr=fh, text=True, timeout=3600)
            lines = [jl(x) for x in p.stdout.splitlines() if x.strip().startswith("{")]
            result = next((x["payload"] for x in reversed(lines) if isinstance(x, dict) and x.get("type") == "result"), {})
            outs = result.get("outputs") or {}
            bench_json = Path(outs.get("benchmark_path") or "")
            record = jl(bench_json.read_text()) if bench_json.is_file() else None
            if record is not None:
                self.save("dbviews", "g_benchmark_record.json", record)
            runtime_status = outs.get("runtime_status") or {}
            measured = [k for k, v in runtime_status.items() if v in ("MEASURED", "OK", "ok", "measured")]
            b_ok = p.returncode == 0 and result.get("status") == "SUCCEEDED" and any(k.startswith("pytorch") for k in measured)
            self.step("g-benchmark-child", bool(b_ok), t2, exit_code=p.returncode, status=result.get("status"),
                      reason_code=result.get("reason_code"), message=str(result.get("message") or "")[:600],
                      runtime_status=runtime_status, engines=outs.get("engines"), latency_table=outs.get("latency_table"),
                      accuracy_delta=outs.get("accuracy_delta"), benchmark_path=str(bench_json), log=str(log))
            out["benchmark"] = {"ok": bool(b_ok), "runtime_status": runtime_status, "exit_code": p.returncode,
                                "reason_code": result.get("reason_code"), "message": str(result.get("message") or "")[:300]}
            if not b_ok:  # DIAGNOSTIC ONLY: how far the pipeline gets with an in-process Manifest.root shim
                t3 = time.monotonic()
                spec["params"]["output_dir"] = str(bdir / "yolo11n_diag")
                spec["job_id"] = "job-t7-bench-yolo11n-diag"
                (bdir / "spec_diag.json").write_text(json.dumps(spec, indent=1))
                dlog = self.ev / "logs" / f"{self.stamp}_benchmark_diag.log"
                with open(dlog, "w") as fh:
                    fh.write(f"# TREE_HASH {self.tree_hash}\n# DIAGNOSTIC shim run (Manifest.root -> roots[0])\n")
                    fh.flush()
                    q = subprocess.run([PY, "-B", str(Path(__file__).with_name("bench_diag.py")), str(bdir / "spec_diag.json")],
                                       env=env, cwd=str(wp3.ROOT), stdout=subprocess.PIPE, stderr=fh, text=True,
                                       timeout=3600)
                diag = jl(q.stdout.strip().splitlines()[-1]) if q.stdout.strip() else {}
                dres = (diag or {}).get("result") or {}
                douts = dres.get("outputs") or {}
                dbench = Path(douts.get("benchmark_path") or "")
                if dbench.is_file():
                    self.save("dbviews", "g_benchmark_diag_record.json", jl(dbench.read_text()))
                self.step("g-benchmark-diagnostic-shim", dres.get("status") == "SUCCEEDED", t3, exit_code=q.returncode,
                          status=dres.get("status"), reason_code=dres.get("reason_code"),
                          message=str(dres.get("message") or "")[:600], runtime_status=douts.get("runtime_status"),
                          engines=douts.get("engines"), latency_table=douts.get("latency_table"),
                          accuracy_delta=douts.get("accuracy_delta"), log=str(dlog),
                          note="DIAGNOSTIC ONLY with an in-process shim; not counted as a PASS of item g")
                out["benchmark_diagnostic"] = {"status": dres.get("status"), "runtime_status": douts.get("runtime_status")}
            out["verdict"] = "PASS" if ev_ok and b_ok else "FAIL"
            return out

        # -- items ------------------------------------------------------------------------------------------------
        def item_a(self) -> dict[str, Any]:
            run_id, rid = self.create_training("a-baseline-train", self.ds["small"], 2)
            t0 = time.monotonic()
            run = self.run_wait(run_id, self.args.timeout)
            view = self.evidence_for("a", run_id)
            jobs = view["jobs"]
            bt = [j for j in jobs if j["kind"] == "BASELINE_TRAIN"]
            evals = [e for e in view["evaluations"] if e["status"] == "EVALUATED"]
            claimed = [r for r in view["idempotent_responses"] if r["runner_id"] == self.runner_id]
            ev = (bt[-1]["evaluation"] if bt else {}) or {}
            gpu_ok = ev.get("device_uuid") == self.gpu_uuid and bool(self.gpu_uuid)
            device_used = [j.get("device_used") for j in bt]
            raw_used = [e.get("raw_metrics_device_used") for e in view["evaluations"]]
            train_ok = (run["status"] == "COMPLETED" and bt and all(j["status"] == "SUCCEEDED" for j in bt)
                        and bool(claimed) and bool(evals) and ev.get("latency_source") == "measured" and gpu_ok)
            used_ok = any(u == "cuda:0" for u in device_used + raw_used)
            self.step("a-baseline-train-gpu", bool(train_ok), t0, run_id=run_id, run_status=run["status"],
                      request_id=rid, jobs=[{k: j[k] for k in ("id", "kind", "status", "attempt", "lease_owner",
                                                                "result_reason", "gpu_seconds", "device_used")}
                                            for j in jobs],
                      evaluation=ev, evaluated_rows=len(evals), runner_gpu_uuid=self.gpu_uuid,
                      eval_device_uuid_matches_runner_gpu=gpu_ok, commits=claimed,
                      checkpoints=[(c["kind"], c["epoch"]) for c in view["checkpoints"]])
            self.step("a-device-used-recorded", used_ok, time.monotonic(), outputs_device_used=device_used,
                      evaluation_raw_metrics_device_used=raw_used,
                      note="criterion: device_used=cuda:0 recorded in result outputs / evaluation raw_metrics")
            return {"verdict": "PASS" if train_ok and used_ok else "FAIL", "run_id": run_id,
                    "train_on_cuda_ok": bool(train_ok), "device_used_recorded": used_ok}

        def search_protocol(self) -> dict[str, Any]:
            from mbnas_core.protocol.presets import pilot_smoke_protocol

            ds = self.ds["small"]
            proto = pilot_smoke_protocol({**{k: ds[k] for k in ("dataset_yaml", "train_root", "search_val_root",
                                                                  "final_eval_root")},
                                          "output_root": str(self.root / "core" / "runs")})
            proto["training"].update(requested_input_size=[128, 128], batch_per_gpu=8, workers=0, warmup_epochs=0,
                                     scheduler_horizon_epochs=2)
            w = [r["fitness_weights"] for r in proto["rungs"]]
            proto["rungs"] = [
                {"id": "L0", "epoch_target_total": 1, "fitness_weights": w[0],
                 "promotion": {"to": "L1", "score_ge": 0.0, "ap_ge": 0.0}},
                {"id": "L1", "epoch_target_total": 2, "fitness_weights": w[1], "promotion": None}]
            proto["comparison"]["independent_search_seed_pairs"] = [101, 202]
            proto["final_retraining"].update(epoch_target_total=2, scheduler_horizon_epochs=2,
                                             finalist_selection="highest_AP_at_L1_with_measured_latency_constraint")
            c = proto["console"]
            for b in c["baselines"]:
                b["enabled"] = False
            c["baseline_override_reason"] = ("T7 GPU path: search arms only; registry baselines are UNVERIFIED on a fresh "
                                             "home (no probe evidence)")
            c["search"].update(population=2, generations=2, seeds=[101, 202])
            c["label"] = "T7 GPU path (smoke values, not research results)"
            return proto

        def item_v(self) -> dict[str, Any]:
            """Validation-only dry check of the item-b protocol (no run is created)."""
            t0 = time.monotonic()
            code, v, rid, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": self.search_protocol()})
            ok = code == 200 and isinstance(v, dict) and v.get("valid") is True
            self.step("v-validate-search-protocol", bool(ok), t0, http=code, request_id=rid,
                      errors=(v or {}).get("errors") if isinstance(v, dict) else v,
                      warnings=(v or {}).get("warnings") if isinstance(v, dict) else None)
            return {"verdict": "PASS" if ok else "FAIL"}

        def item_b(self) -> dict[str, Any]:
            t0 = time.monotonic()
            proto = self.search_protocol()
            code, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": proto})
            valid = code == 200 and isinstance(v, dict) and v.get("valid") is True
            self.step("b-validate", bool(valid), t0, http=code, request_id=rid_v,
                      errors=(v or {}).get("errors") if isinstance(v, dict) else v,
                      warnings=(v or {}).get("warnings") if isinstance(v, dict) else None,
                      capabilities=(v or {}).get("capabilities") if isinstance(v, dict) else None)
            if not valid:
                return {"verdict": "FAIL", "reason": "validation failed", "errors": (v or {}).get("errors")}
            t1 = time.monotonic()
            code, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {
                "protocol": v["resolved"], "protocol_hash": v["protocol_hash"], "validation_id": v["validation_id"],
                "idempotency_key": "t7-b-search-" + uuid.uuid4().hex[:8]})
            ok = code == 200 and isinstance(created, dict) and created.get("run_id")
            self.step("b-create", bool(ok), t1, http=code, request_id=rid_c, body=created)
            if not ok:
                return {"verdict": "FAIL", "reason": f"create {code}"}
            run_id = created["run_id"]
            self.search_run = run_id
            t2 = time.monotonic()
            run = self.run_wait(run_id, self.args.search_timeout)
            view = self.evidence_for("b", run_id)
            jobs, evals = view["jobs"], view["evaluations"]
            by_kind: dict[str, dict[str, int]] = {}
            for j in jobs:
                by_kind.setdefault(j["kind"], {}).setdefault(j["status"], 0)
                by_kind[j["kind"]][j["status"]] += 1
            evaluated = [e for e in evals if e["status"] == "EVALUATED"]
            real = [e for e in evaluated if e.get("latency_source") not in (None, "pending")
                    and isinstance(e.get("fitness"), (int, float))]
            gates = [e.get("gate") for e in evaluated if e.get("gate")]
            decisions: dict[str, int] = {}
            for g in gates:
                code_ = g.get("reason_code") if isinstance(g, dict) else str(g)
                decisions[str(code_)] = decisions.get(str(code_), 0) + 1
            ct_ok = any(j["kind"] == "CANDIDATE_TRAIN" and j["status"] == "SUCCEEDED" for j in jobs)
            ok = run["status"] == "COMPLETED" and ct_ok and bool(real) and bool(gates)
            self.step("b-search-eval-on", bool(ok), t2, run_id=run_id, run_status=run["status"], jobs_by_kind=by_kind,
                      evaluated=len(evaluated), evaluated_real=len(real), promotion_decisions=decisions,
                      sample_rows=[{k: e.get(k) for k in ("candidate_id", "rung", "fitness", "ap", "latency_ms",
                                                           "latency_source", "size_mb", "device_uuid")} for e in real[:6]],
                      failed_jobs=[{k: j[k] for k in ("id", "kind", "status", "result_reason", "result_message")}
                                   for j in jobs if j["status"] == "FAILED"])
            return {"verdict": "PASS" if ok else "FAIL", "run_id": run_id, "jobs_by_kind": by_kind,
                    "evaluated_real": len(real), "promotion_decisions": decisions}

        def item_h(self) -> dict[str, Any]:
            if not self.search_run:
                return {"verdict": "BLOCKED", "reason": "no search run from item b"}
            parent = self.run_status(self.search_run) or {}
            if parent.get("status") != "COMPLETED":
                return {"verdict": "BLOCKED", "reason": f"search run status {parent.get('status')}"}
            t0 = time.monotonic()
            key = "t7-h-final-" + uuid.uuid4().hex[:8]
            code, created, rid, _ = self.gw("POST", f"/api/v1/runs/{self.search_run}/final-retrain",
                                            {"idempotency_key": key, "budget_gpu_hours": 0.5, "top_k_per_arm_per_seed": 1,
                                             "reason": "T7 GPU path FINAL_RETRAIN short run"})
            ok = code == 200 and isinstance(created, dict) and created.get("run_id")
            self.step("h-create", bool(ok), t0, http=code, request_id=rid, body=created)
            if not ok:
                return {"verdict": "FAIL", "reason": f"final-retrain {code}", "body": created}
            child = created["run_id"]
            t1 = time.monotonic()
            run = self.run_wait(child, self.args.timeout * 2)
            view = self.evidence_for("h", child)
            fr = [j for j in view["jobs"] if j["kind"] == "FINAL_RETRAIN"]
            evals = [e for e in view["evaluations"] if e["status"] == "EVALUATED"]
            ok = run["status"] == "COMPLETED" and fr and all(j["status"] == "SUCCEEDED" for j in fr) and bool(evals)
            self.step("h-final-retrain", bool(ok), t1, run_id=child, run_status=run["status"],
                      final_jobs=[{k: j[k] for k in ("id", "status", "attempt", "result_reason", "evaluation")} for j in fr],
                      evaluated=len(evals), finalist_count=created.get("finalist_count"))
            return {"verdict": "PASS" if ok else "FAIL", "run_id": child, "final_jobs": len(fr), "evaluated": len(evals)}

        def item_c(self) -> dict[str, Any]:
            run_id, _ = self.create_training("c-pause-resume", self.ds["big"], 8)
            t0 = time.monotonic()
            reached = self.wait("epoch >= 2 full state (c)", lambda: self.epoch_reached(run_id, 2), timeout=900, interval=1.0)
            code, body, rid, _ = self.gw("POST", f"/api/v1/runs/{run_id}/pause", {"reason": "T7 pause during GPU training"})
            self.step("c-pause-request", code == 200, t0, http=code, request_id=rid, at_checkpoint=reached,
                      run_status=(body or {}).get("status") if isinstance(body, dict) else body)
            t1 = time.monotonic()
            run = self.run_wait(run_id, 900, {"PAUSED", "COMPLETED", "FAILED", "CANCELLED"})
            mid = self.evidence_for("c_paused", run_id)
            job = [j for j in mid["jobs"] if j["kind"] == "BASELINE_TRAIN"][-1]
            pause_reason = (job.get("error") or {}).get("reason_code")
            # contract PAUSE_REASONS = {PAUSED_AT_SAFE_BOUNDARY (generic child), PAUSED_AT_EPOCH (training handler)}
            paused_ok = (run["status"] == "PAUSED" and job["status"] == "QUEUED"
                         and pause_reason in ("PAUSED_AT_SAFE_BOUNDARY", "PAUSED_AT_EPOCH"))
            full_states = [(c["epoch"], c["kind"]) for c in mid["checkpoints"] if c["kind"] == "training_full"]
            self.step("c-paused", bool(paused_ok), t1, run_status=run["status"], pause_reason=pause_reason,
                      run_reason=run.get("reason_code"), job=job, full_states=full_states,
                      child_pids_after_pause=self.runner_child_pids(), nvidia_smi=nvsmi())
            t2 = time.monotonic()
            code, body, rid, _ = self.gw("POST", f"/api/v1/runs/{run_id}/resume", {})
            self.step("c-resume-request", code == 200, t2, http=code, request_id=rid,
                      body=body if code != 200 else {"status": (body or {}).get("status")})
            t3 = time.monotonic()
            run = self.run_wait(run_id, 1800, {"COMPLETED", "FAILED", "CANCELLED"})
            view = self.evidence_for("c", run_id)
            job = [j for j in view["jobs"] if j["kind"] == "BASELINE_TRAIN"][-1]
            rr = job.get("rung_result") or {}
            paused_epoch = max((e for e, _ in full_states), default=None)
            resumed_from = str(rr.get("resumed_from") or "")
            full_resume = bool(paused_epoch) and f"full_e{int(paused_epoch):04d}" in resumed_from
            evals = [e for e in view["evaluations"] if e["status"] == "EVALUATED"]
            ok = (paused_ok and run["status"] == "COMPLETED" and job["status"] == "SUCCEEDED" and job["attempt"] >= 2
                  and full_resume and int(rr.get("epochs_done") or 0) == 8 and bool(evals))
            self.step("c-full-state-resume", bool(ok), t3, run_status=run["status"], job_attempt=job["attempt"],
                      job_status=job["status"], rung_result=rr, paused_epoch=paused_epoch, resumed_from=resumed_from,
                      full_state_resume=full_resume, evaluated=len(evals),
                      commits=view["idempotent_responses"])
            return {"verdict": "PASS" if ok else "FAIL", "run_id": run_id, "paused_epoch": paused_epoch,
                    "pause_reason": pause_reason,
                    "resumed_from": resumed_from, "attempt": job["attempt"]}

        def item_d(self) -> dict[str, Any]:
            run_id, _ = self.create_training("d-cancel", self.ds["big"], 8)
            t0 = time.monotonic()
            reached = self.wait("epoch >= 1 full state (d)", lambda: self.epoch_reached(run_id, 1), timeout=900, interval=1.0)
            child_before = self.runner_child_pids()
            code, body, rid, _ = self.gw("POST", f"/api/v1/runs/{run_id}/cancel", {"reason": "T7 cancel during GPU training"})
            self.step("d-cancel-request", code == 200, t0, http=code, request_id=rid, at_checkpoint=reached,
                      child_pids_before=child_before)
            t1 = time.monotonic()
            run = self.run_wait(run_id, 600, {"CANCELLED", "COMPLETED", "FAILED"})
            time.sleep(3)
            view = self.evidence_for("d", run_id)
            job = [j for j in view["jobs"] if j["kind"] == "BASELINE_TRAIN"][-1]
            alive = [p for p in child_before if Path(f"/proc/{p}").exists()]
            smi = nvsmi()
            ok = run["status"] == "CANCELLED" and job["status"] == "CANCELLED" and not alive
            self.step("d-cancelled", bool(ok), t1, run_status=run["status"], job=job, child_pids_still_alive=alive,
                      nvidia_smi=smi)
            return {"verdict": "PASS" if ok else "FAIL", "run_id": run_id, "job_status": job["status"],
                    "job_reason": job.get("result_reason") or (job.get("error") or {}).get("reason_code")}

        def item_e(self) -> dict[str, Any]:
            run_id, _ = self.create_training("e-runner-kill", self.ds["big"], 8)
            t0 = time.monotonic()
            reached = self.wait("epoch >= 2 full state (e)", lambda: self.epoch_reached(run_id, 2), timeout=900, interval=1.0)
            runner = self.procs["runner"]
            children = self.runner_child_pids()
            job_before = [j for j in self.jobs_view(run_id) if j["kind"] == "BASELINE_TRAIN"][-1]
            runner._expected_exit = True  # type: ignore[attr-defined]
            os.kill(runner.pid, signal.SIGKILL)
            runner.wait(timeout=30)
            self.gpu_windows.append({"end_utc": utc(), "pid": runner.pid, "label": "SIGKILL"})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and any(Path(f"/proc/{p}").exists() for p in children):
                time.sleep(0.2)
            alive = [p for p in children if Path(f"/proc/{p}").exists()]
            self.step("e-sigkill-runner", not alive, t0, runner_pid=runner.pid, runner_exit=runner.returncode,
                      child_pids=children, child_pids_alive_after=alive, at_checkpoint=reached,
                      job_before={k: job_before[k] for k in ("id", "status", "attempt", "lease_owner")})
            t1 = time.monotonic()

            def requeued() -> dict[str, Any] | None:
                rows = self.db_rows("SELECT id, status, attempt, lease_owner, error_json FROM jobs WHERE id = ?",
                                    (job_before["id"],))
                return rows[0] if rows and rows[0]["status"] == "QUEUED" else None

            rq = self.wait("lease expiry + sweeper requeue", requeued, timeout=300, interval=1.0)
            runner_row = self.db_rows("SELECT id, status, last_seen_at FROM runners WHERE id = ?", (self.runner_id,))
            self.step("e-lease-expired-requeued", True, t1, job={**rq, "error_json": jl(rq["error_json"])},
                      runner_row=runner_row, seconds_after_kill=round(time.monotonic() - t1, 1))
            t2 = time.monotonic()
            self.start_runner("e-runner-restart")
            run = self.run_wait(run_id, 1800, {"COMPLETED", "FAILED", "CANCELLED"})
            view = self.evidence_for("e", run_id)
            job = [j for j in view["jobs"] if j["kind"] == "BASELINE_TRAIN"][-1]
            work = self.root / "runner" / "work" / "journal" / job["id"]
            a1 = work / f"a{job_before['attempt']}"
            journal = {p.name: sorted(x.name for x in p.iterdir()) for p in sorted(work.iterdir())} if work.is_dir() else {}
            discarded = jl((a1 / "discarded").read_text()) if (a1 / "discarded").is_file() else None
            rr = job.get("rung_result") or {}
            ok_main = (run["status"] == "COMPLETED" and job["status"] == "SUCCEEDED"
                       and job["attempt"] == job_before["attempt"] + 1
                       and isinstance(discarded, dict) and discarded.get("code") == "LEASE_LOST")
            self.step("e-reclaimed-attempt-plus-1", bool(ok_main), t2, run_status=run["status"],
                      job={k: job[k] for k in ("id", "status", "attempt", "lease_owner", "result_reason")},
                      rung_result=rr, journal=journal, stale_attempt_discarded=discarded,
                      commits=view["idempotent_responses"])
            # explicit replays (simulated late/duplicate deliveries of the frozen result.json files)
            t3 = time.monotonic()
            replay = self.replay_results(job["id"], [job_before["attempt"], job["attempt"]])
            after = self.jobs_view(run_id)
            job_after = [j for j in after if j["id"] == job["id"]][0]
            stale = replay.get(str(job_before["attempt"])) or {}
            fresh = replay.get(str(job["attempt"])) or {}
            ok_replay = (stale.get("status") == 409 and stale.get("code") == "LEASE_LOST"
                         and fresh.get("status") == 200 and fresh.get("same_as_stored_response") is True
                         and job_after["status"] == "SUCCEEDED" and job_after["attempt"] == job["attempt"])
            self.step("e-replay-idempotent", bool(ok_replay), t3, replays=replay,
                      job_after={k: job_after[k] for k in ("status", "attempt", "result_status")})
            return {"verdict": "PASS" if ok_main and ok_replay else "FAIL", "run_id": run_id,
                    "attempts": [job_before["attempt"], job["attempt"]], "stale_discarded": discarded,
                    "replays": {k: {kk: v.get(kk) for kk in ("status", "code")} for k, v in replay.items()}}

        def replay_results(self, job_id: str, attempts: list[int]) -> dict[str, Any]:
            from mbnas_runner.client import CoreClient, CoreError, JobFence
            from mbnas_runner.config import RunnerSettings

            settings = RunnerSettings(core_url=self.base, token_file=str(self.root / "secrets" / "runner.token"),
                                      runner_id=self.runner_id, work_root=str(self.root / "runner" / "work"),
                                      request_retries=0)
            client = CoreClient(settings)
            out: dict[str, Any] = {}
            try:
                for a in attempts:
                    d = self.root / "runner" / "work" / "journal" / job_id / f"a{a}"
                    claim, commit = jl((d / "envelope.json").read_text()), jl((d / "result.json").read_text())
                    stored = jl((d / "response.json").read_text()) if (d / "response.json").is_file() else None
                    fence = JobFence.from_claim(claim)
                    rec: dict[str, Any] = {"attempt": a, "payload_digest": commit.get("payload_digest"),
                                           "result_status": (commit.get("result") or {}).get("status"),
                                           "result_reason": (commit.get("result") or {}).get("reason_code")}
                    try:
                        resp = client.result(fence, commit)
                        rec.update(status=200, response=resp, same_as_stored_response=(resp == stored) if stored else None)
                    except CoreError as exc:
                        rec.update(status=exc.status, code=exc.code, message=exc.message)
                    out[str(a)] = rec
            finally:
                client.close()
            return out

        def item_f(self) -> dict[str, Any]:
            t0 = time.monotonic()
            store = self.root / "gateway" / "artifacts"
            objs = sorted(p for p in store.rglob("*") if p.is_file() and len(p.name) == 64 and p.suffix == "")
            checked, bad = [], []
            for p in objs:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                meta_p = p.with_name(p.name + ".json")
                meta = jl(meta_p.read_text()) if meta_p.is_file() else None
                rec = {"sha256": p.name, "bytes": p.stat().st_size, "digest_ok": digest == p.name,
                       "layout_ok": p.parent.name == p.name[:2], "meta": meta, "nlink": p.stat().st_nlink}
                (checked if rec["digest_ok"] and rec["layout_ok"] and isinstance(meta, dict) else bad).append(rec)
            # every READY/registered checkpoint + artifact of the runs, matched against the store by sha256
            runs = [r["id"] for r in self.db_rows("SELECT id FROM runs ORDER BY created_at")]
            in_store = {c["sha256"] for c in checked}
            ck = self.db_rows("SELECT id, run_id, kind, epoch, sha256, bytes, state FROM checkpoints")
            arts = self.db_rows("SELECT id, run_id, kind, relpath, sha256, bytes, state FROM artifacts")
            ck_missing = [c for c in ck if c["state"] == "PRESENT" and str(c["sha256"]).removeprefix("sha256:") not in in_store]
            art_runner = [a for a in arts if a["sha256"] and any(
                (c.get("meta") or {}).get("artifact_id") == a["id"] for c in checked)]
            self.step("f-store-contents", bool(checked) and not bad and not ck_missing, t0, store=str(store),
                      objects=len(objs), verified=len(checked), bad=bad[:5], checkpoints_total=len(ck),
                      checkpoints_not_in_store=[(c["id"], c["kind"], c["epoch"]) for c in ck_missing][:10],
                      artifacts_total=len(arts), artifacts_in_store=len(art_runner),
                      meta_example=checked[0]["meta"] if checked else None)
            self.save("dbviews", "f_store_listing.json", {"objects": checked, "bad": bad})
            # downloads through the gateway
            t1 = time.monotonic()
            downloads = []
            run_a = self.results.get("a", {}).get("run_id") or (runs[0] if runs else None)
            cand_arts = [a for a in arts if a["run_id"] == run_a] if run_a else []
            picks = []
            for kind in ("other", "gt", "tp", "architecture"):
                x = next((a for a in cand_arts if a["kind"] == kind), None)
                if x:
                    picks.append(("artifact", x))
            ck_a = next((c for c in ck if c["run_id"] == run_a and c["kind"] == "training_full"), None)
            if ck_a:
                picks.append(("checkpoint", ck_a))
            for what, rec in picks:
                code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{rec['run_id']}/artifacts/{rec['id']}", raw=True)
                hdr = resp.headers.get("X-Artifact-Sha256")
                digest = hashlib.sha256(data).hexdigest() if code == 200 else None
                want = str(rec["sha256"] or "").removeprefix("sha256:")
                body = None
                if code != 200:
                    try:
                        body = json.loads(data)
                    except ValueError:
                        body = data[:300].decode(errors="replace")
                downloads.append({"object": what, "id": rec["id"], "kind": rec["kind"], "http": code, "request_id": rid,
                                  "bytes": len(data) if code == 200 else None, "sha256": digest, "header_sha": hdr,
                                  "record_sha": want, "digest_match": bool(digest) and digest == want == (hdr or "").removeprefix("sha256:"),
                                  "in_gateway_store": want in in_store, "error": body})
            code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{run_a}/export", raw=True)
            exp = {"object": "export_bundle", "http": code, "request_id": rid, "bytes": len(data),
                   "header_sha": resp.headers.get("X-Bundle-Sha256"),
                   "digest_match": code == 200 and hashlib.sha256(data).hexdigest() == (resp.headers.get("X-Bundle-Sha256") or "").removeprefix("sha256:")}
            downloads.append(exp)
            runner_objs = [d for d in downloads if d["object"] in ("artifact", "checkpoint") and d.get("in_gateway_store")]
            dl_ok = bool(runner_objs) and all(d["http"] == 200 and d["digest_match"] for d in runner_objs)
            self.step("f-download-via-gateway", dl_ok, t1, downloads=downloads,
                      note="runner-produced objects (in the gateway store) must download through the gateway with "
                           "X-Artifact-Sha256 == sha256(body) == record")
            ok = bool(checked) and not bad and not ck_missing and dl_ok
            return {"verdict": "PASS" if ok else "FAIL", "store_objects": len(objs), "verified": len(checked),
                    "checkpoints_not_in_store": len(ck_missing),
                    "downloads": [{k: d.get(k) for k in ("object", "kind", "http", "digest_match", "in_gateway_store")}
                                  for d in downloads]}

        # -- driver -----------------------------------------------------------------------------------------------
        def copy_logs(self) -> None:
            for name in ("core", "owner", "runner", "gateway"):
                src = self.root / "logs" / f"{name}.log"
                if src.is_file():
                    dst = self.ev / "logs" / f"{self.stamp}_{name}.log"
                    dst.write_text(f"# TREE_HASH {self.tree_hash}\n# copy of {src}\n" +
                                   self.redact(src.read_text(errors="replace")))
            for p in sorted((self.root / "runner" / "work" / "runs").glob("*/logs/*.stderr.log")):
                dst = self.ev / "logs" / f"{self.stamp}_child_{p.parent.parent.name}_{p.name}"
                dst.write_text(f"# TREE_HASH {self.tree_hash}\n# copy of {p}\n" + self.redact(p.read_text(errors="replace")))

        def run(self) -> int:  # noqa: C901
            self.root.mkdir(parents=True, exist_ok=True)
            self.note(f"T7 work root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}; "
                      f"gpu {self.gpu}; items {self.args.items}")
            before = self.smi("before")
            busy = [a for a in before["compute_apps"]]
            if busy:
                self.step("gpu-idle-precheck", False, time.monotonic(), compute_apps=busy)
                self.note("GPU not idle: STOP (no process is touched)")
                return 3
            ok_setup = False
            try:
                self.build_inputs()
                if "g" in self.args.items.split(","):
                    self.g_ready = self.prepare_g().get("ok", False)
                self.start_core()
                self.principals((("server", "server", []), ("runner", "runner", ["--runner-id", self.runner_id]),
                                 ("admin", "admin", [])))
                self.start_workers()
                ok_setup = self.start_gateway()
            except Exception as exc:  # noqa: BLE001
                self.step("setup-error", False, time.monotonic(), error=f"{type(exc).__name__}: {exc}"[:2000],
                          tb=traceback.format_exc()[-3000:])
            if ok_setup:
                for item in [i.strip() for i in self.args.items.split(",") if i.strip()]:
                    self.item = item
                    t0 = time.monotonic()
                    smi_b = nvsmi()
                    try:
                        res = getattr(self, f"item_{item}")()
                    except Exception as exc:  # noqa: BLE001 - recorded; continue with the next item
                        res = {"verdict": "FAIL", "error": f"{type(exc).__name__}: {exc}"[:2000],
                               "tb": traceback.format_exc()[-3000:]}
                        self.step(f"{item}-error", False, t0, **res)
                    res["seconds"] = round(time.monotonic() - t0, 1)
                    res["nvidia_smi_before"], res["nvidia_smi_after"] = smi_b, nvsmi()
                    self.results[item] = res
                    self.note(f"ITEM {item}: {res.get('verdict')} ({res['seconds']} s)")
                    r = self.procs.get("runner")
                    if r is None or r.poll() is not None:
                        self.note("runner process gone; restarting for the next item")
                        try:
                            self.start_runner("runner-restart-between-items")
                        except Exception as exc:  # noqa: BLE001
                            self.step("runner-restart-error", False, time.monotonic(), error=str(exc)[:500])
                            break
            self.item = "teardown"
            for p in self.procs.values():
                p._expected_exit = True  # type: ignore[attr-defined]
            stopped = self.stop_all()
            self.gpu_windows.append({"end_utc": utc(), "label": "teardown"})
            time.sleep(3)
            after = self.smi("after")
            self.copy_logs()
            summary = {"tree_hash": self.tree_hash, "work_root": str(self.root), "items": self.results,
                       "gpu": self.gpu, "gpu_uuid": self.gpu_uuid, "runner_id": self.runner_id,
                       "gpu_windows": self.gpu_windows, "stopped_all": stopped,
                       "pids": {n: p.pid for n, p in self.procs.items()},
                       "exit_codes": {n: p.returncode for n, p in self.procs.items()},
                       "nvidia_smi_before": before, "nvidia_smi_after": after,
                       "started_utc": self.started_utc, "finished_utc": utc()}
            out = self.ev / f"t7_summary_{self.stamp}.json"
            out.write_text(self.redact(json.dumps(summary, indent=1, sort_keys=True, default=str)) + "\n")
            self.note(f"summary -> {out}")
            self.transcript.close()
            return 0 if ok_setup and all(r.get("verdict") == "PASS" for r in self.results.values()) else 1

    return T7


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--g2-root", required=True)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--tree-hash", required=True)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--items", default="a,b,h,c,d,e,f")
    ap.add_argument("--timeout", type=float, default=1200, help="seconds per training run")
    ap.add_argument("--search-timeout", type=float, default=5400, help="seconds for the item-b search run")
    ap.add_argument("--root", default=None)
    ap.add_argument("--official-weights", default=None, help="local official yolo11n.pt for item g (never downloaded)")
    ap.add_argument("--coco128", default=None, help="local coco128 dataset dir for item g (copied, read only)")
    a = ap.parse_args()
    g2 = Path(a.g2_root).resolve()
    if not Path(sys.prefix).resolve().is_relative_to(g2):  # the venv prefix (the executable resolves to the base)
        raise SystemExit(f"run with the frozen copy's interpreter (got prefix {sys.prefix})")
    wp3 = load_wp3(g2)
    T7 = make_harness(wp3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = a.root or str(g2 / "runtime" / "t7" / stamp)
    args = argparse.Namespace(port=0, gw_port=0, root=root, timeout=a.timeout, web_dist=str(g2 / "mbnas_web" / "dist"),
                              device_source="runners", evidence=str(Path(a.evidence_dir) / "unused.txt"),
                              evidence_dir=a.evidence_dir, tree_hash=a.tree_hash, gpu=a.gpu, items=a.items,
                              search_timeout=a.search_timeout, official_weights=a.official_weights,
                              coco128=a.coco128)
    h = T7(args)
    h.started_utc = utc()
    return h.run()


if __name__ == "__main__":
    raise SystemExit(main())
