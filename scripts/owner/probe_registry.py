#!/usr/bin/env python
"""Probe every registry entry (baseline14 + PILOT representatives) and write registry evidence JSON.

Writes ``<output_root>/registry/probe_<ts>.json`` (raw probe records) and ``<output_root>/registry/registry_<ts>.json``
(the contract RegistryEntry list built from it plus the GPU and TensorRT evidence under ``<output_root>/evidence``;
identical to ``load_registry(output_root)`` for that probe file). CPU only:
ultralytics yaml builds (params for nc=80 and nc=1), official weight sha256 vs ``weights_manifest.json``, pretrained
weight load, RF-DETR instantiation for nano/small/medium/large (scratch build + published-MD5-verified pretrained
load from ``RF_HOME``), ETRI hybrid build, and with ``--export-onnx`` a CPU ONNX export of each random-init model.

Usage: .venv/bin/python scripts/probe_registry.py [--export-onnx] [--only id1,id2] [--output-root DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output-root", default="/root/detection/MBNAS_runs")
    ap.add_argument("--weights-dir", default="/root/detection/MBNAS_data/weights")
    ap.add_argument("--export-onnx", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--merge-latest", action="store_true",
                    help="start from the newest probe file's records and replace only the re-probed ids")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="no probing: rebuild registry_<ts>.json from the newest probe file + current GPU evidence")
    ap.add_argument("--gpu-smoke", action="store_true",
                    help="REAL GPU (wrap in flock gpu.lock): per entry run_until(1) -> resume run_until(2) on the "
                         "PILOT subset + predict + common evaluator; writes evidence/train_smoke_<id>_<ts>.json "
                         "(checkpoint payloads are deleted after each entry, sidecars stay)")
    ap.add_argument("--smoke-imgsz", type=int, default=320)
    args = ap.parse_args()
    if args.gpu_smoke:
        return gpu_smoke(args)
    out_root = Path(args.output_root)
    os.environ["RF_HOME"] = str(Path(args.weights_dir) / "rfdetr")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""  # CPU-only probe: never touch the training GPU (--gpu-smoke exits above)
    from mbnas_model_runtime.adapters.ultralytics_adapter import prepare_ultralytics_env

    prepare_ultralytics_env(out_root / "ultralytics_cfg", args.weights_dir)
    from mbnas_core.registry import build_entries, scan_evidence, scan_trt_evidence
    from mbnas_model_runtime.registry.probe import PROBE_SCHEMA, probe_spec, versions
    from mbnas_model_runtime.registry.specs import SPECS
    from mbnas_contracts.util.atomic import atomic_write_json
    from mbnas_contracts.util.time import utcnow_iso

    if args.rebuild_only:
        from mbnas_core.registry import latest_probe_path

        latest = latest_probe_path(str(out_root))
        if latest is None:
            print("no probe file to rebuild from", file=sys.stderr)
            return 2
        registry = build_entries(json.loads(latest.read_text()), scan_evidence(str(out_root)), str(latest),
                                 scan_trt_evidence(str(out_root)))
        reg_path = out_root / "registry" / f"registry_{time.strftime('%Y%m%dT%H%M%S')}.json"
        atomic_write_json(reg_path, {"registry": registry, "probe_file": str(latest)})
        _print(registry)
        print(f"probe {latest}\nregistry {reg_path}")
        return 0 if all(e["status"] == "AVAILABLE" for e in registry) else 2
    manifest = json.loads((Path(args.weights_dir) / "weights_manifest.json").read_text())
    only = {s for s in args.only.split(",") if s}
    entries: dict = {}
    merged_from = None
    if args.merge_latest:
        from mbnas_core.registry import latest_probe_path

        merged_from = latest_probe_path(str(out_root))
        if merged_from is not None:
            entries = dict(json.loads(merged_from.read_text())["entries"])
    for spec in SPECS:
        if only and spec.id not in only:
            continue
        t0 = time.time()
        entries[spec.id] = probe_spec(spec, weights_dir=Path(args.weights_dir), manifest=manifest,
                                      export_onnx=args.export_onnx)
        rec = entries[spec.id]
        print(f"{spec.id:34s} build={rec['build'].get('ok')} weights={rec['weights'].get('status')} "
              f"pretrained={rec.get('pretrained_load', {}).get('ok')} "
              f"onnx={rec.get('onnx_export', {}).get('ok')} {time.time() - t0:.1f}s", flush=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    probe = {"schema": PROBE_SCHEMA, "created_at": utcnow_iso(), "host": platform.node(), "versions": versions(),
             "command": " ".join([sys.executable, *sys.argv]), "export_onnx": args.export_onnx,
             "merged_from": str(merged_from) if merged_from else None, "entries": entries}
    probe_path = out_root / "registry" / f"probe_{ts}.json"
    atomic_write_json(probe_path, probe)
    registry = build_entries(probe, scan_evidence(str(out_root)), str(probe_path), scan_trt_evidence(str(out_root)))
    reg_path = out_root / "registry" / f"registry_{ts}.json"
    atomic_write_json(reg_path, {"registry": registry, "probe_file": str(probe_path)})
    _print(registry)
    print(f"probe {probe_path}\nregistry {reg_path}")
    failed = [e["id"] for e in registry if e["status"] != "AVAILABLE"]
    return 0 if not failed else 2


def gpu_smoke(args: argparse.Namespace) -> int:
    """Per-entry GPU train/resume/predict/evaluate smoke (tiny: 2 epochs of 32 images); one evidence file per entry."""
    import torch

    out_root = Path(args.output_root)
    os.environ["RF_HOME"] = str(Path(args.weights_dir) / "rfdetr")
    from mbnas_model_runtime.adapters.ultralytics_adapter import prepare_ultralytics_env

    prepare_ultralytics_env(out_root / "ultralytics_cfg", args.weights_dir)
    from mbnas_model_runtime.adapters.base import DataSpec
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
    from mbnas_model_runtime.evaluation import evaluator as ev
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_model_runtime.registry import adapter_for, arch_spec
    from mbnas_core.registry import load_registry
    from mbnas_model_runtime.registry.specs import SPECS
    from mbnas_model_runtime.datasets.yolo_view import build_yolo_view
    from mbnas_runner.training.recipe import recipe_from_protocol
    from mbnas_runner.training.stage_trainer import StageTrainer
    from mbnas_contracts.util.atomic import atomic_write_json
    from mbnas_contracts.util.canon import canonical_sha256
    from mbnas_contracts.util.time import utcnow_iso

    pilot = Path("/root/detection/MBNAS_data/pilot_visdrone_v1")
    ts = time.strftime("%Y%m%dT%H%M%S")
    work = out_root / "evidence_work" / f"train_smoke_{ts}"
    names = {int(k): v for k, v in load_dataset_yaml(str(pilot / "data.yaml")).names.items()}
    svc = DatasetService(str(work), [str(pilot.parent)])
    train, val = (svc.manifest_for(str(pilot / f"images/{x}"), names) for x in ("train", "search_val"))
    view = build_yolo_view(work / "data_view", {"train": train, "val": val}, names)
    data = DataSpec(names, train, val, view_root=view.parent)
    proto = pilot_smoke_protocol()
    phash = canonical_sha256(proto)
    registry = load_registry(str(out_root))
    only = {s for s in args.only.split(",") if s}
    failures = 0
    for spec in SPECS:
        if only and spec.id not in only:
            continue
        adapter = adapter_for(spec)
        rule = adapter.capability()["input_size_rule"]
        shape = rule["shape"] if rule["kind"] == "fixed" else [args.smoke_imgsz] * 2
        recipe = recipe_from_protocol(proto, shape, fixed_horizon=2)
        recipe["batch"] = 4
        t0 = time.time()
        doc = {"kind": "train_smoke", "model_id": spec.id, "created_at": utcnow_iso(), "imgsz": shape, "batch": 4,
               "horizon": 2, "init": "scratch", "dataset": str(pilot), "registry_status": registry[spec.id]["status"]}
        try:
            st = StageTrainer(adapter=adapter, candidate_dir=work / spec.id, arch=arch_spec(spec.id, args.weights_dir,
                              registry[spec.id]), data=data, recipe=recipe, init="scratch", training_seed=101,
                              device="cuda:0")
            r1 = st.run_until(spec.id, 1, 2, phash, rung="S1", prior_epochs=0, expected_resume=None)
            r2 = st.run_until(spec.id, 2, 2, phash, rung="S2", prior_epochs=1, expected_resume=None)
            model = adapter.load_eval_model(r2.rung_end_ema_eval_ckpt, "cuda:0")
            preds = adapter.predict(model, [(s.sample_id, s.image) for s in val.samples[:2]],
                                    {"conf": 0.001, "max_det": 300, "shape": shape, "batch": 2, "precision": "FP16"})
            res = ev.evaluate(adapter, model, val, ev.EvalConfig(tuple(shape), 8, "FP16", torch.cuda.get_device_name(0)))
            doc["evaluation"] = {k: res[k] for k in ("ap", "ap_50", "recall", "counts", "evaluation_hash",
                                                     "split_hash", "no_predictions", "timings")}
            ok = (r1.epochs_done == 1 and r2.epochs_done == 2 and bool(r2.resumed_from)
                  and r2.step_count > r1.step_count and len(preds) == 2 and res["ap"] is not None
                  and res["recall"] is not None)
            doc.update({"verdict": "PASS" if ok else "FAIL", "stage1": {"steps": r1.step_count, "lr": r1.lr_history},
                        "stage2": {"steps": r2.step_count, "resumed_from": r2.resumed_from, "lr": r2.lr_history,
                                   "size_mb": r2.size_mb, "checkpoint_sha256": r2.checkpoint_sha256},
                        "architecture_hash": r2.hashes.get("architecture"),
                        "predictions": [len(p["scores"]) for p in preds],
                        "resolved_recipe": {k: v for k, v in r2.resolved_recipe.items()
                                            if isinstance(v, (int, float, str, bool, type(None)))}})
        except Exception as exc:  # noqa: BLE001 - recorded as FAIL evidence, never skipped
            import traceback

            doc.update({"verdict": "FAIL", "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=6)})
        doc["seconds"] = round(time.time() - t0, 1)
        doc["summary"] = (f"{spec.id}: scratch run_until(1) -> resume run_until(2) at {shape} on PILOT, predict + "
                          f"common evaluator on search_val (16 imgs): {doc['verdict']} ({doc['seconds']}s)")
        for p in (work / spec.id).rglob("*"):  # per-entry payload cleanup keeps peak disk use to one model
            if p.is_file() and p.suffix in (".pt", ".ckpt", ".pth"):
                p.unlink()
        failures += doc["verdict"] != "PASS"
        atomic_write_json(out_root / "evidence" / f"train_smoke_{spec.id}_{ts}.json", doc)
        print(doc["summary"], doc.get("error", ""), flush=True)
    return 0 if failures == 0 else 2


def _print(registry: list) -> None:
    for e in registry:
        print(f"{e['id']:34s} {e['status']:15s} train={e['capabilities']['train']['status']:31s} "
              f"onnx={e['capabilities']['export_onnx']['status']}")


if __name__ == "__main__":
    sys.exit(main())
