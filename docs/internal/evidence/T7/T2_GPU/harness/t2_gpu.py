#!/usr/bin/env python3
"""G2-2 (T2-GPU) orchestrator: the same candidate training (yolo11n nc=1, 3 epochs = horizon, 128 px, seed 41,
deterministic=True via the pinned ultralytics recipe, inline evaluation with measured latency) run twice with the NEW
code and once with the ORIGINAL monolith code (``build/source_snapshot``, read only), all on one GPU of the frozen
copy's venv, sequentially (new_1, ref, new_2). Writes ``ref.json``, ``new.json`` (new_1), ``new_2.json``,
``snapshot_guard.json`` and ``T2_GPU_REPORT.md`` into ``--evidence-dir``; every JSON carries ``tree_hash``.

Usage (from the frozen copy):
  CUDA_VISIBLE_DEVICES='' runtime/venv/bin/python -B <this> --g2-root <g2> --snapshot <live>/build/source_snapshot \
      --evidence-dir <live>/docs/internal/evidence/T7/T2_GPU --tree-hash sha256:... --gpu 1
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
T7_HARNESS = HERE.parents[1] / "harness" / "t7_gpu_path.py"


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def tree_sha(root: Path) -> dict[str, Any]:
    h = hashlib.sha256()
    files = 0
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(str(p.relative_to(root)).encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
            files += 1
    return {"sha256": h.hexdigest(), "files": files}


def nvsmi() -> dict[str, Any]:
    out = {}
    for key, q in (("gpus", "--query-gpu=index,uuid,name,memory.used,utilization.gpu"),
                   ("compute_apps", "--query-compute-apps=pid,process_name,used_memory,gpu_uuid")):
        p = subprocess.run(["nvidia-smi", q, "--format=csv,noheader"], capture_output=True, text=True, timeout=60)
        out[key] = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--g2-root", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--tree-hash", required=True)
    ap.add_argument("--gpu", type=int, default=1)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--imgsz", type=int, default=128)
    ap.add_argument("--order", default="new_1,ref,new_2")
    ap.add_argument("--train-count", type=int, default=64)
    ap.add_argument("--data-seed", type=int, default=2028)
    a = ap.parse_args()
    g2, snapshot, ev = Path(a.g2_root).resolve(), Path(a.snapshot).resolve(), Path(a.evidence_dir)
    (ev / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = g2 / "runtime" / "t2_gpu" / stamp
    work.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("t7h", T7_HARNESS)
    t7 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t7)  # type: ignore[union-attr]
    ds = t7.build_shapes(work / "data", "t2shapes", {"train": a.train_count, "val": 16, "test": 16}, 160, a.data_seed)
    shared_out = work / "out"
    shared_out.mkdir()
    import yaml
    import ultralytics
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    text = (Path(ultralytics.__file__).parent / "cfg/models/11/yolo11.yaml").read_text().replace("nc: 80", "nc: 1\nscale: n")
    arch = yaml.safe_load(text)
    (work / "arch_yolo11n_nc1.json").write_text(json.dumps(arch, indent=1))
    proto = pilot_smoke_protocol({**{k: ds[k] for k in ("dataset_yaml", "train_root", "search_val_root",
                                                          "final_eval_root")}, "output_root": str(shared_out)})
    proto["training"].update(requested_input_size=[a.imgsz, a.imgsz], batch_per_gpu=8, workers=0, warmup_epochs=0,
                             scheduler_horizon_epochs=a.epochs)
    proto["rungs"] = [{"id": "L0", "epoch_target_total": a.epochs, "fitness_weights": proto["rungs"][0]["fitness_weights"],
                       "promotion": None}]
    proto["final_retraining"].update(epoch_target_total=2, scheduler_horizon_epochs=2)
    proto["console"]["baselines"] = []  # no registry in the side processes (registry_lookup -> None)
    proto["console"]["baseline_override_reason"] = "G2-2 T2-GPU: one search candidate only"
    proto["console"]["search"].update(population=2, generations=1, seeds=[101])
    (work / "requested_protocol.json").write_text(json.dumps(proto, indent=1))
    guard = "http://127.0.0.1:9"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update(CUDA_VISIBLE_DEVICES=str(a.gpu), PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE="/dev/null",
               HTTPS_PROXY=guard, https_proxy=guard, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               YOLO_OFFLINE="1", YOLO_AUTOINSTALL="false", HF_HUB_OFFLINE="1", OMP_NUM_THREADS="2",
               MKL_NUM_THREADS="2", MBNAS_HOME=str(work / "mbnas_home"), YOLO_CONFIG_DIR=str(work / "ultralytics_cfg"),
               TORCH_HOME=str(work / "torch_home"), PYTHONHASHSEED="0")
    guard_before = tree_sha(snapshot)
    runs: dict[str, Any] = {}
    steps = []
    for label in [x.strip() for x in a.order.split(",") if x.strip()]:
        side = "ref" if label.startswith("ref") else "new"
        out = ev / ("ref.json" if label == "ref" else "new.json" if label == "new_1" else f"{label}.json")
        log = ev / "logs" / f"{stamp}_{label}.log"
        argv = [sys.executable, "-B", "-s", str(HERE / "t2_gpu_side.py"), "--side", side, "--label", label,
                "--snapshot", str(snapshot), "--work", str(work / label), "--data-root", str(work / "data"),
                "--shared-out", str(shared_out), "--requested", str(work / "requested_protocol.json"),
                "--arch", str(work / "arch_yolo11n_nc1.json"), "--out", str(out), "--epochs", str(a.epochs)]
        smi_b = nvsmi()
        t0, start = time.monotonic(), utc()
        with open(log, "w") as fh:
            fh.write(f"# TREE_HASH {a.tree_hash}\n# {' '.join(argv)}\n")
            fh.flush()
            p = subprocess.run(argv, env=env, cwd=str(work), stdout=fh, stderr=subprocess.STDOUT, timeout=3600)
        rec = {"label": label, "side": side, "exit_code": p.returncode, "start_utc": start, "end_utc": utc(),
               "seconds": round(time.monotonic() - t0, 1), "out": str(out), "log": str(log),
               "nvidia_smi_before": smi_b, "nvidia_smi_after": nvsmi()}
        steps.append(rec)
        print(json.dumps({k: rec[k] for k in ("label", "exit_code", "seconds")}), flush=True)
        if out.is_file():
            d = json.loads(out.read_text())
            d["tree_hash"] = a.tree_hash
            d["run_record"] = rec
            out.write_text(json.dumps(d, indent=1, sort_keys=True, default=str))
            runs[label] = d
    guard_after = tree_sha(snapshot)
    guard = {"tree_hash": a.tree_hash, "snapshot": str(snapshot), "before": guard_before, "after": guard_after,
             "unchanged": guard_before == guard_after}
    (ev / "snapshot_guard.json").write_text(json.dumps(guard, indent=1))
    summary = {"tree_hash": a.tree_hash, "work": str(work), "steps": steps, "dataset": {k: ds[k] for k in ("dataset_yaml", "counts", "size", "seed")},
               "requested_protocol": str(work / "requested_protocol.json"), "snapshot_guard": guard}
    (ev / f"t2_gpu_runs_{stamp}.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({"snapshot_unchanged": guard["unchanged"], "runs": list(runs)}), flush=True)
    return 0 if all(s["exit_code"] == 0 for s in steps) and guard["unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
