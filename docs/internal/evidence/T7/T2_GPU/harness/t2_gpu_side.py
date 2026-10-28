#!/usr/bin/env python3
"""G2-2 (T2-GPU) side runner: ONE candidate training job on the GPU with one side's code, in its own process.

Started by ``t2_gpu.py`` as ``<frozen copy venv python> -B -s t2_gpu_side.py --side ref|new ...`` with
``CUDA_VISIBLE_DEVICES=<gpu>``, ``PYTHONDONTWRITEBYTECODE=1``, the network guard (``HTTPS_PROXY`` to a dead local port,
``YOLO_OFFLINE=1``) and HOME-like caches inside the work directory.

* side ``ref``: the ORIGINAL monolith ``mbnas_server`` of ``build/source_snapshot`` (read only). Before any MBNAS import a
  meta-path blocker makes ``mbnas_contracts`` / ``mbnas_core`` / ``mbnas_model_runtime`` / ``mbnas_runner`` unimportable
  and the snapshot's ``application/MBNAS_server/src`` + snapshot root go first on ``sys.path`` (the new ``mbnas_server``
  of site-packages is shadowed; checked through ``mbnas_server.__file__``).
* side ``new``: ``mbnas_core`` (validation only) + ``mbnas_runner`` / ``mbnas_model_runtime`` / ``mbnas_contracts``
  installed in the frozen copy's venv (the code the runner daemon's child executes).

Both sides: validate the SAME requested protocol with their own validator (dataset manifests computed by their own
DatasetService), hash the resolved protocol with their own ``protocol_hash``, build the SAME JobSpec (CANDIDATE_TRAIN,
explicit ultralytics yolo11n arch nc=1, 3 epochs = horizon, seed 41, scratch, inline evaluation) and call their own
training job handler ``run_training_job(spec, emit, settings)`` - exactly the handler the worker child (original) /
runner child (new) dispatches for that kind; torchvision is imported first as both children do. Output JSON: result,
metrics, per-epoch history, results.csv losses, tensor digests of the rung-end EMA eval snapshot and of the final
full-state model, module provenance.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib
import importlib.abc
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

NEW_PACKAGES = ("mbnas_contracts", "mbnas_core", "mbnas_model_runtime", "mbnas_runner")


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:  # noqa: D401
        if name.split(".")[0] in NEW_PACKAGES:
            raise ImportError(f"{name} is blocked on the reference side (isolation)")
        return None


def tensor_digest(obj: Any) -> dict[str, Any]:
    """sha256 over every tensor of a (nested) state in sorted key order + count; dtype/shape included."""
    import torch

    h = hashlib.sha256()
    n = 0

    def walk(prefix: str, v: Any) -> None:
        nonlocal n
        if isinstance(v, torch.Tensor):
            t = v.detach().cpu().contiguous()
            h.update(f"{prefix}|{t.dtype}|{tuple(t.shape)}\n".encode())
            h.update(t.numpy().tobytes() if t.dtype != torch.bfloat16 else t.float().numpy().tobytes())
            n += 1
        elif isinstance(v, dict):
            for k in sorted(v, key=str):
                walk(f"{prefix}/{k}", v[k])
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                walk(f"{prefix}[{i}]", x)

    walk("", obj)
    return {"sha256": "sha256:" + h.hexdigest(), "tensors": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("ref", "new"), required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--work", required=True, help="this run's work dir (fresh)")
    ap.add_argument("--data-root", required=True, help="dataset allow root (contains the dataset dir)")
    ap.add_argument("--shared-out", required=True, help="console.paths.output_root of the shared requested protocol")
    ap.add_argument("--requested", required=True, help="requested protocol JSON (identical for every run)")
    ap.add_argument("--arch", required=True, help="arch config JSON (yolo11n nc=1)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=41)
    a = ap.parse_args()
    snapshot, work = Path(a.snapshot).resolve(), Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    if a.side == "ref":
        sys.meta_path.insert(0, _Blocker())
        sys.path[:0] = [str(snapshot / "application" / "MBNAS_server" / "src"), str(snapshot)]
        importlib.invalidate_caches()
        os.environ["MBNAS_ETRI_NAS_ROOT"] = str(snapshot)
        import mbnas_server  # noqa: F401

        if not Path(mbnas_server.__file__).resolve().is_relative_to(snapshot):
            raise SystemExit(f"isolation: mbnas_server from {mbnas_server.__file__}, not the snapshot")
        M = {"config": "mbnas_server.config", "validator": "mbnas_server.protocol.validator",
             "manifest": "mbnas_server.datasets.manifest", "handler": "mbnas_server.training.handlers",
             "jobs": "mbnas_server.schemas.jobs", "phash": "mbnas_server.protocol.validator",
             "checkpoint": "mbnas_server.training.checkpoint"}
    else:
        M = {"config": "mbnas_core.config", "validator": "mbnas_core.protocol.validator",
             "manifest": "mbnas_model_runtime.datasets.manifest", "handler": "mbnas_runner.training.handlers",
             "jobs": "mbnas_contracts.schemas.jobs", "phash": "mbnas_contracts.protocol_hash",
             "checkpoint": "mbnas_model_runtime.checkpoint", "runner_config": "mbnas_runner.config"}
    mods = {k: importlib.import_module(v) for k, v in M.items()}
    import torch
    import torchvision  # noqa: F401 - both children import it before GPU kinds (NMS path)
    import ultralytics

    runs = work / "runs"
    common = dict(output_root=str(runs), output_roots=[str(runs), a.shared_out], dataset_roots=[a.data_root],
                  weights_dir=str(work / "weights"), log_dir=str(work / "logs"), min_free_disk_gb=0.0,
                  worker_python=sys.executable, db_path=str(work / "db" / "mbnas.sqlite3"))
    if a.side == "ref":
        settings = mods["config"].Settings(**common, etri_nas_root=str(snapshot), archive_roots=[],
                                            archive_driver_dirs=[])
        handler_settings = settings
    else:
        settings = mods["config"].CoreSettings(**common, model_yaml_roots=[str(runs)], sweeper_enabled=False)
        handler_settings = mods["runner_config"].RunnerSettings(
            work_root=str(runs), output_roots=[str(runs), a.shared_out], dataset_roots=[a.data_root],
            weights_dir=str(work / "weights"), log_dir=str(work / "logs"))
    (work / "weights").mkdir(exist_ok=True)
    requested = json.loads(Path(a.requested).read_text())
    name = torch.cuda.get_device_name(0)
    device = {"id": "host-gpu-0", "uuid": "GPU-t2gpu-cuda0", "name": name, "aliases": [name, "cuda:0"],
              "precisions": ["FP32", "FP16"]}
    V = mods["validator"]
    vctx = V.ValidationContext(settings=settings, registry_lookup=lambda _id: None, devices=[device],
                               dataset_service=mods["manifest"].DatasetService.from_settings(settings),
                               now=lambda: "2026-10-27T00:00:00.000000Z", disk_free_gb=lambda _p: 1000.0)
    res = V.validate(copy.deepcopy(requested), vctx)
    if not res.valid:
        Path(a.out).write_text(json.dumps({"side": a.side, "label": a.label, "error": "validation failed",
                                           "errors": res.errors}, indent=1, default=str))
        return 2
    resolved = res.resolved
    phash = mods["phash"].protocol_hash(resolved)
    arch = json.loads(Path(a.arch).read_text())
    cid = "cand-t2gpu-yolo11n"
    run_dir = runs / "run-t2gpu"
    spec = mods["jobs"].JobSpec(
        job_id="job-t2gpu-0001", run_id="run-t2gpu", kind="CANDIDATE_TRAIN", protocol_hash=phash, candidate_id=cid,
        rung="L0", cumulative_target=a.epochs, fixed_horizon=a.epochs, arm="GA", search_seed=101,
        training_seed=a.seed,
        params={"run_dir": str(run_dir), "adapter_id": cid, "resolved_protocol": resolved,
                "arch": {"model_id": cid, "family": "YOLO11", "backend": "ultralytics", "config": arch,
                         "variant": "detect", "register_hook": None},
                "init": "scratch", "device": "cuda:0", "evaluate": True, "stage_epochs_added": a.epochs})
    events: list[dict[str, Any]] = []

    def emit(msg: Any) -> None:
        d = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else dict(msg)
        if d.get("type") in ("progress", "metric"):
            events.append({k: v for k, v in d.items() if k != "ts"})

    t0 = time.time()
    result = mods["handler"].run_training_job(spec, emit, settings=handler_settings)
    train_wall = time.time() - t0
    rd = result.model_dump(mode="json")
    outs = rd.get("outputs") or {}
    ev = outs.get("evaluation") or {}
    rr = outs.get("rung_result") or {}
    digests: dict[str, Any] = {}
    ck = mods["checkpoint"]
    try:
        if ev.get("eval_checkpoint_path"):
            snap = torch.load(ev["eval_checkpoint_path"], map_location="cpu", weights_only=True)
            digests["eval_ema_snapshot"] = tensor_digest(snap)
        if rr.get("rung_end_training_ckpt"):
            full = torch.load(rr["rung_end_training_ckpt"], map_location="cpu", weights_only=True)
            for key in ("model_fp32", "ema_fp32", "optimizer"):
                if key in full:
                    digests[f"full_state.{key}"] = tensor_digest(full[key])
            digests["full_state.keys"] = sorted(full)
    except Exception as exc:  # noqa: BLE001 - recorded
        digests["error"] = f"{type(exc).__name__}: {exc}"
    losses: dict[str, Any] = {}
    for p in sorted(run_dir.rglob("results.csv")):
        with open(p, newline="") as fh:
            rows = [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(fh)]
        losses[str(p.relative_to(run_dir))] = rows
    out = {
        "side": a.side, "label": a.label, "seconds_total": round(time.time() - t_start, 2),
        "seconds_handler": round(train_wall, 2),
        "modules": {k: getattr(m, "__file__", None) for k, m in mods.items()},
        "isolation": {"blocked": list(NEW_PACKAGES) if a.side == "ref" else [],
                      "sys_path_head": sys.path[:2]},
        "versions": {"python": sys.version.split()[0], "torch": torch.__version__,
                     "torchvision": torchvision.__version__, "ultralytics": ultralytics.__version__,
                     "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(), "device": name,
                     "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES")},
        "validation": {"valid": res.valid, "warnings": res.warnings, "protocol_hash": phash,
                       "dataset": {k: (resolved.get("dataset") or {}).get(k) for k in
                                   ("train_manifest_hash", "search_validation_manifest_hash",
                                    "final_evaluation_manifest_hash", "class_mapping_hash")},
                       "training": resolved.get("training")},
        "job": {"status": rd.get("status"), "reason_code": rd.get("reason_code"), "message": (rd.get("message") or "")[:2000],
                "gpu_seconds": rd.get("gpu_seconds")},
        "metrics": {k: ev.get(k) for k in ("ap", "ap_50", "recall", "latency_ms", "latency_statistics",
                                            "latency_scope", "latency_source", "size_mb", "evaluation_hash",
                                            "split_hash", "counts", "no_predictions", "coco_stats", "precision",
                                            "resolved_input_shape", "weights_only_sha256", "device_name")},
        "latency_scopes": {k: {kk: vv for kk, vv in (v or {}).items() if kk in ("p50_ms", "p95_ms", "mean_ms", "n")}
                           for k, v in ((ev.get("latency") or {}).get("scopes") or {}).items()},
        "rung_result": {k: rr.get(k) for k in ("epochs_done", "step_count", "samples_seen", "stopped_reason", "notes",
                                                "hashes", "checkpoint_sha256", "size_mb")},
        "resolved_recipe": rr.get("resolved_recipe"), "lr_history": rr.get("lr_history"),
        "results_csv": losses, "digests": digests, "progress_events": events,
    }
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0 if rd.get("status") == "SUCCEEDED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
