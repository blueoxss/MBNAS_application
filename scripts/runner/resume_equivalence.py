#!/usr/bin/env python
"""Full-state resume equivalence on the PILOT_SMOKE subset (real GPU evidence).

Run A trains one candidate continuously ``run_until(H)``; run B trains the same candidate in stages
``run_until(c1) -> run_until(c2) -> ... -> run_until(H)``, every stage in a FRESH child process (exactly how the worker
runs jobs). Both use the same recipe (PILOT preset values, ``--warmup`` overrides warmup so it crosses the first cut),
seed and data. The report compares, at every cut and at H: per-epoch LR lists, global step, samples seen, optimizer
state tensor digests, EMA update counters, model/EMA max-abs weight difference, RNG (python/numpy/torch/CUDA) and loader
generator equality. Negative cases: a checkpoint with the ``optimizer`` key deleted and one with a wrong
architecture hash must both raise ``RESUME_BLOCKED`` (no weights-only fallback); a native ultralytics ``last.pt`` is
rejected as ``FORMAT_UNKNOWN``.

Verdict: PASS = counters/LR/optimizer/RNG exact and weights bitwise; TOLERANCE = counters/LR exact, weights differ
(max-abs reported; e.g. RT-DETR's nondeterministic grid_sample backward); FAIL otherwise.

Paths are parameters (never fixed host paths; WP-G2 G2-3 adaptation for the split tree):

* ``--data-root`` (env ``MBNAS_RESUME_DATA_ROOT``, required): a PILOT-layout dataset directory with ``data.yaml``
  (``names``) and the train / search-validation image directories ``--train-dir`` / ``--val-dir`` (relative to the data
  root; defaults ``images/train`` / ``images/search_val``, the PILOT_SMOKE subset layout);
* ``--weights-dir`` (env ``MBNAS_RUNNER_WEIGHTS_DIR``, default ``$MBNAS_HOME/weights``): the ultralytics/RF-DETR
  weights directory (``RF_HOME`` below it);
* ``--runs-root`` (env ``MBNAS_RESUME_RUNS_ROOT``, default ``$MBNAS_HOME/runner/resume_equivalence``): work
  directories (``evidence_work/``), the stage log (``logs/``) and the report (``evidence/``) unless ``--workdir`` /
  ``--log`` / ``--out`` name them;
* ``--protocol-json`` (required): the resolved protocol file written by the owner host (e.g. the PILOT_SMOKE preset).

Usage (one GPU, chosen with ``CUDA_VISIBLE_DEVICES``; wrap in the host's GPU lock when other jobs share the device)::

  CUDA_VISIBLE_DEVICES=1 runtime/venv/bin/python scripts/runner/resume_equivalence.py --model yolo11n --horizon 6 \
      --cuts 2,4 --data-root /abs/pilot_subset --protocol-json /abs/protocol.json [--out /abs/report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DATA_ROOT_ENV = "MBNAS_RESUME_DATA_ROOT"
RUNS_ROOT_ENV = "MBNAS_RESUME_RUNS_ROOT"
WEIGHTS_DIR_ENV = "MBNAS_RUNNER_WEIGHTS_DIR"


def _home(*parts: str) -> Path:
    from mbnas_contracts.util.home import mbnas_home

    return mbnas_home().joinpath(*parts)


def _abs(value: str | os.PathLike[str], what: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{what} must be an absolute path, got {str(value)!r}")
    return path


def _setup(workdir: Path, protocol_json: str, data_root: str | os.PathLike[str], train_dir: str = "images/train",
           val_dir: str = "images/search_val") -> tuple[Any, Any, Any, dict[str, Any], str]:
    """Settings-free equivalents of the handler plumbing: data spec, adapter, arch, recipe, protocol hash."""
    from mbnas_model_runtime.adapters.base import DataSpec
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
    from mbnas_contracts.util.canon import canonical_sha256

    pilot = Path(data_root)
    names = {int(k): v for k, v in load_dataset_yaml(str(pilot / "data.yaml")).names.items()}
    service = DatasetService(str(workdir), [str(pilot.parent)])
    train = service.manifest_for(str(pilot / train_dir), names)
    val = service.manifest_for(str(pilot / val_dir), names)
    from mbnas_model_runtime.datasets.yolo_view import build_yolo_view

    view = build_yolo_view(workdir / "data_view", {"train": train, "val": val}, names)
    proto = json.loads(Path(protocol_json).read_text(encoding="utf-8"))  # WP-1 §11: owner-generated file
    return DataSpec(names, train, val, view_root=view.parent), names, proto, {}, canonical_sha256(proto)


def stage(args: argparse.Namespace) -> None:
    """Child process: one run_until call; copies every full state it writes into ``--copy-states``."""
    workdir = Path(args.workdir)
    from mbnas_model_runtime.adapters.ultralytics_adapter import prepare_ultralytics_env

    prepare_ultralytics_env(workdir / "ultralytics_cfg", str(args.weights_dir))
    from mbnas_model_runtime.adapters.rfdetr_adapter import default_rf_home

    os.environ.setdefault("RF_HOME", default_rf_home(str(args.weights_dir)))
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for, arch_spec
    from mbnas_runner.training.recipe import recipe_from_protocol
    from mbnas_runner.training.stage_trainer import StageTrainer

    data, names, proto, _, phash = _setup(workdir, args.protocol_json, args.data_root, args.train_dir, args.val_dir)
    spec = SPEC_BY_ID[args.model]
    adapter = adapter_for(spec)
    rule = adapter.capability()["input_size_rule"]
    shape = rule["shape"] if rule["kind"] == "fixed" else [args.imgsz, args.imgsz]
    recipe = recipe_from_protocol(proto, shape, fixed_horizon=args.horizon)
    recipe["warmup_epochs"] = float(args.warmup)
    copy_dir = Path(args.copy_states) if args.copy_states else None
    events: list[dict[str, Any]] = []

    def progress(ev: dict[str, Any]) -> None:
        events.append({k: v for k, v in ev.items() if k != "native_metrics"})
        if copy_dir is not None and ev.get("type") == "checkpoint" and ev.get("kind") == "training_full":
            copy_dir.mkdir(parents=True, exist_ok=True)
            src = Path(ev["path"])
            shutil.copyfile(src, copy_dir / src.name)
            shutil.copyfile(str(src) + ".sha256.json", copy_dir / (src.name + ".sha256.json"))
            native = src.parent / src.name.replace("full_e", "native_e").replace(".pt", ".ckpt")
            if native.is_file():  # RF-DETR: the referenced PTL checkpoint is pruned with its full state
                shutil.copyfile(native, copy_dir / native.name)

    if spec.backend == "ultralytics":
        adapter.keep_native_weights = True  # kept for the native-last.pt negative case
    trainer = StageTrainer(adapter=adapter, candidate_dir=workdir / args.candidate, arch=arch_spec(args.model),
                           data=data, recipe=recipe, init="scratch", training_seed=args.seed, device="cuda:0",
                           progress=progress)
    t0 = time.time()
    res = trainer.run_until(args.candidate, args.target, args.horizon, phash, rung=f"T{args.target}",
                            prior_epochs=args.prior, expected_resume=None)
    import torch

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    print("STAGE_RESULT " + json.dumps({"seconds": round(time.time() - t0, 2), "epochs_done": res.epochs_done,
                                         "device_name": device_name,
                                         "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
                                         "resumed_from": res.resumed_from, "steps": res.step_count,
                                         "stopped": res.stopped_reason, "events": len(events),
                                         "recipe_optimizer": res.resolved_recipe.get("optimizer_class"),
                                         "notes": res.notes}), flush=True)


def _run_stage(args: argparse.Namespace, candidate: str, target: int, copy_states: Path | None, log,
               prior: int = 0) -> dict[str, Any]:
    cmd = [sys.executable, __file__, "stage", "--model", args.model, "--workdir", str(args.workdir), "--candidate",
           candidate, "--target", str(target), "--prior", str(prior), "--horizon", str(args.horizon),
           "--seed", str(args.seed),
           "--imgsz", str(args.imgsz), "--warmup", str(args.warmup), "--protocol-json", str(args.protocol_json),
           "--data-root", str(args.data_root), "--train-dir", args.train_dir, "--val-dir", args.val_dir,
           "--weights-dir", str(args.weights_dir)]
    if copy_states is not None:
        cmd += ["--copy-states", str(copy_states)]
    log.write(f"$ {' '.join(cmd)}\n")
    log.flush()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
    log.write(proc.stdout)
    log.flush()
    if proc.returncode != 0:
        raise RuntimeError(f"stage {candidate}->{target} exited {proc.returncode}")
    line = next(l for l in proc.stdout.splitlines() if l.startswith("STAGE_RESULT "))
    return json.loads(line.split(" ", 1)[1])


def _load(path: Path) -> dict[str, Any]:
    """Load a full state; for RF-DETR states map the referenced native PTL checkpoint onto the common keys."""
    import torch

    state = torch.load(path, map_location="cpu", weights_only=True)
    ref = state.get("native_resume_ckpt")
    if ref is None:
        return state
    native_path = Path(ref["path"])
    if not native_path.is_file():  # copied next to the full state by the A-run copy hook
        native_path = Path(path).parent / native_path.name
    native = torch.load(native_path, map_location="cpu", weights_only=False)
    ema = next((v for k, v in native["callbacks"].items() if "RFDETREMACallback" in k), {})
    sched = native["lr_schedulers"][0]
    state.update({
        "model_fp32": native["state_dict"], "ema_fp32": ema.get("average_model_state_dict"),
        "ema_updates": ema.get("latest_update_step"), "optimizer": native["optimizer_states"][0],
        "scheduler": {"last_epoch": sched.get("last_epoch"), "_step_count": sched.get("_step_count")},
        # state["loader_generator_state"] (the RF-DETR sampler generator, saved by the adapter) is kept as is
        "native_summary": {"keys": sorted(native), "epoch": native.get("epoch"), "global_step": native.get("global_step"),
                           "callbacks": sorted(native["callbacks"]), "optimizer_param_groups":
                               len(native["optimizer_states"][0]["param_groups"]),
                           "dtypes": sorted({str(v.dtype) for v in native["state_dict"].values()})},
    })
    return state


def _max_abs(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, float]:
    import torch

    bitwise, diff = True, 0.0
    for k, v in a.items():
        w = b[k]
        if not torch.equal(v, w):
            bitwise = False
        if v.is_floating_point():
            diff = max(diff, float((v.float() - w.float()).abs().max()) if v.numel() else 0.0)
    return bitwise, diff


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    from mbnas_model_runtime.adapters.common import tensor_state_digest
    from mbnas_model_runtime.checkpoint import rng_equal

    import torch

    model_bw, model_d = _max_abs(a["model_fp32"], b["model_fp32"]) if a.get("model_fp32") else (None, None)
    ema_bw, ema_d = _max_abs(a["ema_fp32"], b["ema_fp32"]) if a.get("ema_fp32") else (None, None)
    lr_a = [h["lr"] for h in a["lr_history"]]
    lr_b = [h["lr"] for h in b["lr_history"]]
    out = {
        "epochs_done": [a["epochs_done"], b["epochs_done"]],
        "global_step": [a["global_step"], b["global_step"]], "samples_seen": [a["samples_seen"], b["samples_seen"]],
        "lr_equal": lr_a == lr_b, "lr_A": lr_a, "lr_B": lr_b,
        "ema_updates": [a.get("ema_updates"), b.get("ema_updates")],
        "optimizer_digest": [tensor_state_digest(a["optimizer"]["state"]) if a.get("optimizer") else None,
                             tensor_state_digest(b["optimizer"]["state"]) if b.get("optimizer") else None],
        "scheduler": [a.get("scheduler", {}).get("last_epoch"), b.get("scheduler", {}).get("last_epoch")],
        "scaler": [a.get("scaler"), b.get("scaler")],
        "model_bitwise": model_bw, "model_max_abs_diff": model_d, "ema_bitwise": ema_bw, "ema_max_abs_diff": ema_d,
        "rng_equal": rng_equal(a["rng"], b["rng"]),
        "loader_generator_equal": bool(torch.equal(a["loader_generator_state"], b["loader_generator_state"]))
        if a.get("loader_generator_state") is not None else None,
        "adapter_meta_equal": {k: a.get("adapter_meta", {}).get(k) == b.get("adapter_meta", {}).get(k)
                               for k in sorted(set(a.get("adapter_meta", {})) | set(b.get("adapter_meta", {})))},
    }
    out["optimizer_equal"] = out["optimizer_digest"][0] == out["optimizer_digest"][1]
    out["counters_equal"] = (out["global_step"][0] == out["global_step"][1]
                             and out["samples_seen"][0] == out["samples_seen"][1]
                             and out["ema_updates"][0] == out["ema_updates"][1] and out["lr_equal"]
                             and out["scheduler"][0] == out["scheduler"][1])
    return out


def negative_cases(args: argparse.Namespace, b_state_path: Path, cut: int) -> dict[str, Any]:
    """Stripped optimizer / wrong architecture hash / native last.pt must all be RESUME_BLOCKED."""
    import torch

    from mbnas_model_runtime.adapters.base import ResumeBlocked
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for, arch_spec
    from mbnas_model_runtime import checkpoint as ck
    from mbnas_runner.training.recipe import recipe_from_protocol
    from mbnas_runner.training.stage_trainer import StageTrainer

    workdir = Path(args.workdir)
    data, names, proto, _, phash = _setup(workdir, args.protocol_json, args.data_root, args.train_dir, args.val_dir)
    spec = SPEC_BY_ID[args.model]
    adapter = adapter_for(spec)
    rule = adapter.capability()["input_size_rule"]
    shape = rule["shape"] if rule["kind"] == "fixed" else [args.imgsz, args.imgsz]
    recipe = recipe_from_protocol(proto, shape, fixed_horizon=args.horizon)
    recipe["warmup_epochs"] = float(args.warmup)
    results: dict[str, Any] = {}

    def attempt(name: str, mutate) -> None:  # noqa: ANN001
        cdir = workdir / f"neg_{name}"
        shutil.rmtree(cdir, ignore_errors=True)
        import torch

        state = torch.load(b_state_path, map_location="cpu", weights_only=True)  # raw MBNAS state (not mapped)
        mutate(state)
        ck.save_full_state(ck.full_state_path(cdir / "ckpt", cut), state, rung_end=True)
        trainer = StageTrainer(adapter=adapter, candidate_dir=cdir, arch=arch_spec(args.model), data=data,
                               recipe=recipe, init="scratch", training_seed=args.seed, device="cuda:0")
        try:
            trainer.run_until(name, cut + 1, args.horizon, phash, prior_epochs=cut, expected_resume=None)
            results[name] = {"blocked": False, "error": "resume was NOT blocked"}
        except ResumeBlocked as exc:
            results[name] = {"blocked": True, "reason": exc.reason, "detail": exc.detail}
        trained = sorted(p.name for p in (cdir / "ckpt").glob("full_e*.pt") if p.name != f"full_e{cut:04d}.pt")
        results[name]["new_checkpoints_written"] = trained

    strip = "native_resume_ckpt" if spec.backend == "rfdetr" else "optimizer"
    attempt(f"stripped_{strip}", lambda s: s.pop(strip))
    attempt("wrong_architecture_hash", lambda s: s["hashes"].__setitem__("architecture", "sha256:" + "0" * 64))
    native = sorted((workdir / "B" / "native").glob("*/weights/last.pt"))
    if native:
        try:
            ck.check_state(torch.load(native[0], map_location="cpu", weights_only=False))
            results["native_last_pt"] = {"blocked": False}
        except ResumeBlocked as exc:
            results["native_last_pt"] = {"blocked": True, "reason": exc.reason, "path": str(native[0])}
    return results


def _restoration_record(args: argparse.Namespace) -> dict[str, Any]:
    """What a resume restores for this backend (declared capability + observed final-state keys)."""
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for

    cap = adapter_for(SPEC_BY_ID[args.model]).capability()
    final = _load(Path(args.workdir) / "B" / "ckpt" / f"full_e{args.horizon:04d}.pt")
    return {"declared": cap["full_state_resume"], "detail": cap.get("full_state_resume_detail"),
            "state_keys": sorted(k for k in final if not k.startswith("native_summary")),
            "adapter_meta": final.get("adapter_meta"), "native": final.get("native_summary")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for p in (ap, sub.add_parser("stage")):
        p.add_argument("--model", default="yolo11n")
        p.add_argument("--horizon", type=int, default=6)
        p.add_argument("--seed", type=int, default=101)
        p.add_argument("--imgsz", type=int, default=640)
        p.add_argument("--warmup", type=float, default=3.0)
        p.add_argument("--workdir", default=None)
        p.add_argument("--protocol-json", default=None,
                       help="resolved protocol JSON (e.g. the PILOT_SMOKE preset written by the owner host); required")
        p.add_argument("--data-root", default=os.environ.get(DATA_ROOT_ENV),
                       help=f"PILOT-layout dataset directory (data.yaml + image dirs); env {DATA_ROOT_ENV}; required")
        p.add_argument("--train-dir", default="images/train", help="train image dir relative to --data-root")
        p.add_argument("--val-dir", default="images/search_val", help="search-validation image dir relative to --data-root")
        p.add_argument("--weights-dir", default=os.environ.get(WEIGHTS_DIR_ENV),
                       help=f"weights directory (env {WEIGHTS_DIR_ENV}; default $MBNAS_HOME/weights)")
    ap.add_argument("--cuts", default="2,4")
    ap.add_argument("--runs-root", default=os.environ.get(RUNS_ROOT_ENV),
                    help=f"work/log/report root (env {RUNS_ROOT_ENV}; default $MBNAS_HOME/runner/resume_equivalence)")
    ap.add_argument("--out", default=None, help="report JSON path (default <runs-root>/evidence/resume_equivalence_"
                                                 "<model>_<ts>.json)")
    ap.add_argument("--log", default=None, help="stage log path (default <runs-root>/logs/resume_equivalence_<model>_"
                                                 "<ts>.log)")
    ap.add_argument("--keep-payloads", action="store_true",
                    help="keep checkpoint payloads in the work dir (default: delete *.pt/*.ckpt after the report; "
                         "sidecars with sha256 stay)")
    sp = sub.choices["stage"]
    sp.add_argument("--candidate", required=True)
    sp.add_argument("--target", type=int, required=True)
    sp.add_argument("--prior", type=int, required=True,
                    help="epochs completed before this stage (0 for the first stage, else the previous cut)")
    sp.add_argument("--copy-states", default=None)
    args = ap.parse_args()
    if not args.protocol_json:
        ap.error("--protocol-json is required (WP-1 contract §11: the runner host receives the protocol as a file)")
    if not args.data_root:
        ap.error(f"--data-root (or {DATA_ROOT_ENV}) is required: the PILOT-layout dataset directory")
    args.data_root = _abs(args.data_root, "--data-root")
    args.protocol_json = str(_abs(args.protocol_json, "--protocol-json"))
    args.weights_dir = _abs(args.weights_dir, "--weights-dir") if args.weights_dir else _home("weights")
    if args.cmd == "stage":
        stage(args)
        return 0
    ts = time.strftime("%Y%m%dT%H%M%S")
    runs = _abs(args.runs_root, "--runs-root") if args.runs_root else _home("runner", "resume_equivalence")
    args.workdir = Path(args.workdir or runs / "evidence_work" / f"resume_{args.model}_{ts}")
    shutil.rmtree(args.workdir, ignore_errors=True)
    args.workdir.mkdir(parents=True)
    cuts = [int(c) for c in args.cuts.split(",") if c]
    log_path = Path(args.log) if args.log else runs / "logs" / f"resume_equivalence_{args.model}_{ts}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        t0 = time.time()
        a_stage = _run_stage(args, "A", args.horizon, args.workdir / "A_states", log)
        targets = [*cuts, args.horizon]
        b_stages = [_run_stage(args, "B", t, None, log, prior=([0, *targets][i])) for i, t in enumerate(targets)]
        boundaries = {}
        for t in [*cuts, args.horizon]:
            a = _load(args.workdir / "A_states" / f"full_e{t:04d}.pt")
            b = _load(args.workdir / "B" / "ckpt" / f"full_e{t:04d}.pt")
            boundaries[str(t)] = compare(a, b)
        neg = negative_cases(args, args.workdir / "B" / "ckpt" / f"full_e{cuts[0]:04d}.pt", cuts[0])
        restored = _restoration_record(args)
        final = boundaries[str(args.horizon)]
        exact_counters = all(v["counters_equal"] for v in boundaries.values())
        bitwise = all(v["model_bitwise"] and v["ema_bitwise"] in (True, None) and v["optimizer_equal"]
                      and all(v["rng_equal"].values()) and v["loader_generator_equal"] in (True, None)
                      for v in boundaries.values())
        neg_ok = all(r.get("blocked") for r in neg.values())
        if bitwise and exact_counters and neg_ok:
            verdict = "PASS"
        elif exact_counters and neg_ok:
            verdict = "TOLERANCE"
        else:
            verdict = "FAIL"
        from mbnas_contracts.util.time import utcnow_iso

        report = {
            "kind": "resume_equivalence" if len(cuts) > 1 else "resume_sanity", "model_id": args.model,
            "verdict": verdict, "created_at": utcnow_iso(), "seconds": round(time.time() - t0, 1),
            "summary": (f"{args.model}: {'/'.join(map(str, cuts))}->{args.horizon} vs continuous {args.horizon}: "
                        f"counters/LR exact={exact_counters}, bitwise={bitwise}, final model max|d|="
                        f"{final['model_max_abs_diff']}, EMA max|d|={final['ema_max_abs_diff']}, "
                        f"negative cases blocked={neg_ok}"),
            "settings": {"horizon": args.horizon, "cuts": cuts, "seed": args.seed, "imgsz": args.imgsz,
                         "warmup_epochs": args.warmup, "dataset": str(args.data_root),
                         "train_dir": args.train_dir, "val_dir": args.val_dir, "weights_dir": str(args.weights_dir),
                         "protocol_json": str(args.protocol_json), "process_isolation": True,
                         "device": "cuda:0 = " + str(sorted({s.get("device_name") for s in [a_stage, *b_stages]}, key=str))
                                   + f" (CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')!r})"},
            "A_stage": a_stage, "B_stages": b_stages, "boundaries": boundaries, "negative_cases": neg,
            "restoration": restored,
            "log": str(log_path), "workdir": str(args.workdir),
        }
    out = Path(args.out) if args.out else runs / "evidence" / f"resume_equivalence_{args.model}_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    if not args.keep_payloads:
        for p in Path(args.workdir).rglob("*"):
            if p.is_file() and p.suffix in (".pt", ".ckpt", ".pth"):
                p.unlink()
    print(f"VERDICT {verdict} {report['summary']}")
    print(f"evidence {out}\nlog {log_path}")
    return 0 if verdict in ("PASS", "TOLERANCE") else 1


if __name__ == "__main__":
    sys.exit(main())
