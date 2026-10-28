"""SYNTHETIC job handlers for API/worker/orchestration tests and scripts/smoke_api_only.py (no GPU, no model).

Activated in a worker child with ``MBNAS_JOB_HANDLERS_MODULE=stub_handlers`` and this directory (tests/_shared) on
PYTHONPATH; also the stub module of ``python -m mbnas_runner.child --spec-file`` (tests/public/runner).
Metrics are deterministic functions of (candidate id, rung) — they are NOT measurements and are labelled
``raw_metrics.synthetic=true`` / ``latency_scope=synthetic_stub`` / ``device=stub-device``. They report
``latency_source=measured`` by default because they stand in for the real evaluator (which always writes measured)
and the promotion gate only lets measured latency satisfy the hard latency constraint (S8-F2).

Knobs (environment):
* ``MBNAS_STUB_EPOCH_S``      seconds slept per epoch (default 0.02)
* ``MBNAS_STUB_FAIL``         comma list of candidate-id substrings whose training fails (BUILD_INVALID-like)
* ``MBNAS_STUB_CRASH_ONCE``   path of a marker file: the first training attempt that sees it missing creates it and
                              hard-exits (``os._exit(137)``) at epoch 1 — a crashed child without a result
* ``MBNAS_STUB_SHA_BY_KIND``  report ``checkpoint_sha256`` as a per-kind dict (like the real training handler)
* ``MBNAS_STUB_HANG_FILE``    while this file exists, training sleeps before every epoch after the first
                              (the epoch-1 checkpoint exists; lets tests kill the worker or cancel mid-job)
* ``MBNAS_STUB_LATENCY_SOURCE`` latency_source reported by the stub (default ``measured``; the numbers stay
                              SYNTHETIC); proxy tests set ``unverified_proxy`` / ``calibrated_proxy`` to check that
                              the gate and the finalist rule refuse non-measured latency
* ``MBNAS_STUB_POSTPROCESS_RUNTIME`` when set, the evaluation reports this ``postprocess_runtime`` (S8-F5)
* ``MBNAS_STUB_REAL_SPLITS``  when set, training calls the REAL ``training.context.split_manifests`` for the splits
                              its kind may see (FINAL_RETRAIN: train/search_val/final_eval) so the run's split access
                              log is written by the production code (``RunnerSettings.load()`` from ``MBNAS_RUNNER_*``
                              env, the names the owner worker hands to its children, contract §5)
* ``MBNAS_STUB_FAIL_FINAL``   FINAL_RETRAIN training fails for candidate ids containing one of these substrings
* ``MBNAS_STUB_PROFILE_S``    when set, the evaluation sleeps this long as SYNTHETIC "inline latency profiling" and
                              reports ``timings = {val_s, profile_s}`` like the real evaluator (the worker bills
                              profile_s as a separate unbilled ``profile`` ledger row); the QA handlers default it on

FINAL_RETRAIN (arm Final) evaluations report the final split hash (``final_evaluation_manifest_hash``) and label their
panels ``split=final_eval``, like the real training handler.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mbnas_contracts.schemas.jobs import JobSpec
from mbnas_contracts.util.atomic import atomic_write_bytes, sha256_file
from mbnas_contracts.jobs_protocol import Emitter, JobFailure, JobPaused


def _unit(*parts: object) -> float:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _png(path: Path, seed: float) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (int(seed * 255), 80, 160)).save(path)


def propose(spec: JobSpec, emit: Emitter) -> dict[str, Any]:
    p = spec.params
    gen, pop = int(p["generation"]), int(p.get("population") or 2)
    pool = p.get("parent_pool") if spec.arm == "GA" else None  # Random never reads fitness
    cands = []
    for i in range(pop):
        parents: list[str] = []
        op = "random"
        if pool:
            ranked = sorted(pool, key=lambda r: (-(r.get("fitness") or 0), r["candidate_id"]))
            parents = [r["candidate_id"] for r in ranked[:2]] if len(ranked) >= 2 and i % 2 == 0 else [ranked[0]["candidate_id"]]
            op = "crossover" if len(parents) == 2 else "clone"
        arch = hashlib.sha256(f"{spec.arm}|{spec.search_seed}|{gen}|{i}".encode()).hexdigest()
        cands.append({"row": {"name": f"{spec.arm[:3].upper()}-G{gen:02d}-C{i:03d}", "family": "NAS:stub",
                              "architecture_hash": f"sha256:{arch}", "architecture_yaml": f"# stub {arch[:12]}\n",
                              "parents": parents, "parent_evaluation_ids": [], "lineage": {"operation": op},
                              "blocks": [{"name": "Conv", "source": None, "mutated": False, "old_name": None,
                                          "source_kind": "random" if op == "random" else "parent_a"}]},
                      "genotype": {"stub": arch}})
        emit.log(f"proposed {i}")
    return {"candidates": cands, "search_state": {"generation": gen}}


def train(spec: JobSpec, emit: Emitter, profile_default_s: float | None = None) -> dict[str, Any]:
    p = spec.params
    cid, rung = spec.candidate_id or "", spec.rung or ""
    work = Path(p["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    if any(s and s in cid for s in os.environ.get("MBNAS_STUB_FAIL", "").split(",")):
        raise JobFailure("BUILD_INVALID", f"synthetic failure for {cid}")
    if _final(spec) and any(s and s in cid for s in os.environ.get("MBNAS_STUB_FAIL_FINAL", "").split(",")):
        raise JobFailure("TRAIN_FAILED", f"synthetic final failure for {cid}")
    if os.environ.get("MBNAS_STUB_REAL_SPLITS"):
        _read_splits(spec)
    print("library noise on stdout must not break the JSONL protocol")  # child redirects fd 1 to stderr
    resume = p.get("resume_from") or None
    start = 0
    if resume:
        if sha256_file(resume["path"]) != resume["sha256"]:
            raise JobFailure("RESUME_BLOCKED", "full-state checkpoint hash mismatch")
        start = int(resume["epoch"])
    epoch_s = float(os.environ.get("MBNAS_STUB_EPOCH_S", "0.02"))
    target = int(spec.cumulative_target or 1)
    for epoch in range(start, target):
        crash = os.environ.get("MBNAS_STUB_CRASH_ONCE")
        if crash and epoch == 1 and not os.path.exists(crash):
            Path(crash).write_text(spec.job_id)
            os._exit(137)
        while epoch >= 1 and os.environ.get("MBNAS_STUB_HANG_FILE") and os.path.exists(os.environ["MBNAS_STUB_HANG_FILE"]):
            time.sleep(0.05)
        time.sleep(epoch_s)
        ck = work / f"full_e{epoch + 1:03d}.json"
        atomic_write_bytes(ck, json.dumps({"format": "stub.full_state", "candidate": cid, "epoch": epoch + 1,
                                           "protocol": spec.protocol_hash}).encode())
        emit.progress(epoch=epoch + 1, epoch_target_total=target, global_step=(epoch + 1) * 4,
                      samples_seen=(epoch + 1) * 32, learning_rate=0.001 * (1 - (epoch + 1) / (spec.fixed_horizon or 1)))
        emit.checkpoint(kind="training_full", path=str(ck), sha256=sha256_file(ck), bytes=ck.stat().st_size,
                        epoch=epoch + 1, rung=rung, hashes={"protocol": spec.protocol_hash})
        if emit.stop_requested() and epoch + 1 < target:
            raise JobPaused({"epochs_done": epoch + 1})
    ev = evaluation(spec, work, target, profile_default_s)
    ev["raw_metrics"]["child_env"] = {"CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
                                      "api_token_present": any(k in os.environ for k in (
                                          "MBNAS_CORE_API_TOKEN", "MBNAS_API_TOKEN")),  # DD-A13 name + legacy name
                                      "signing_key_present": any(k in os.environ for k in (
                                          "MBNAS_CORE_ARTIFACT_SIGNING_KEY", "MBNAS_ARTIFACT_SIGNING_KEY")),
                                      "resumed_from_epoch": start,
                                      "torchvision_imported": "torchvision" in sys.modules}
    return {"epochs_done": target, "evaluation": ev}


def _final(spec: JobSpec) -> bool:
    return spec.kind.value == "FINAL_RETRAIN" or spec.arm == "Final"


def _read_splits(spec: JobSpec) -> None:
    """Real split reads (production split_manifests + access log) for the splits this job kind may see."""
    from mbnas_runner.config import RunnerSettings as Settings
    from mbnas_runner.training.context import SplitAccess, split_manifests

    splits = ("train", "search_val", "final_eval") if _final(spec) else ("train", "search_val")
    split_manifests(spec.params["protocol"], Settings.load(), splits,
                    access=SplitAccess.for_job(spec.params["run_dir"], spec, purpose="stub training"))


def _profile_s(default: float | None) -> float | None:
    raw = os.environ.get("MBNAS_STUB_PROFILE_S")
    return float(raw) if raw not in (None, "") else default


def evaluation(spec: JobSpec, work: Path, epochs: int, profile_default_s: float | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    cid, rung = spec.candidate_id or "", spec.rung or ""
    u = _unit(cid, rung)
    dataset = spec.params["protocol"]["dataset"]
    split = "final_eval" if _final(spec) else "search_val"
    split_hash = dataset["final_evaluation_manifest_hash" if _final(spec) else "search_validation_manifest_hash"]
    ev_ck = work / f"eval_ema_e{epochs:03d}.json"
    atomic_write_bytes(ev_ck, json.dumps({"stub_eval": cid, "epoch": epochs}).encode())
    arts = []
    for kind in ("gt", "tp", "fn", "fp"):
        path = work / "images" / f"{rung}_{kind}.png"
        _png(path, u)
        arts.append({"kind": kind, "path": str(path), "sample_id": "sample-000", "split_hash": split_hash,
                     "matching": {"rule": "synthetic", "sample_id": "sample-000", "split": split,
                                  "counts": {"tp": 1, "fp": 0, "fn": 0, "gt": 1}}})
    t1 = time.perf_counter()
    profile_s = _profile_s(profile_default_s)
    timings = None
    if profile_s is not None:  # SYNTHETIC profiling interval, measured like evaluation/handlers.py does
        time.sleep(profile_s)
        timings = {"val_s": round(t1 - t0, 4), "profile_s": round(time.perf_counter() - t1, 4)}
    return {**({"timings": timings} if timings else {}), "ap": round(0.05 + 0.4 * u, 6), "recall": round(0.1 + 0.5 * u, 6),
            "latency_ms": round(20 + 150 * (1 - u), 4), "size_mb": round(5 + 50 * u, 4),
            "latency_source": os.environ.get("MBNAS_STUB_LATENCY_SOURCE", "measured"),
            "latency_scope": "synthetic_stub", "device": "stub-device", "precision": "FP16",
            "evaluation_hash": "sha256:stub-evaluator|" + split_hash, "split_hash": split_hash, "eval_split": split,
            "checkpoint_path": str(ev_ck), "checkpoint_sha256": (
                {"training_full": sha256_file(ev_ck), "eval_ema": "0" * 64} if os.environ.get("MBNAS_STUB_SHA_BY_KIND")
                else sha256_file(ev_ck)), "batch": 1,
            "resolved_input_shape": [64, 64], "raw_metrics": {"synthetic": True}, "artifacts": arts,
            **({"postprocess_runtime": os.environ["MBNAS_STUB_POSTPROCESS_RUNTIME"]}
               if os.environ.get("MBNAS_STUB_POSTPROCESS_RUNTIME") else {})}


def evaluate(spec: JobSpec, emit: Emitter, profile_default_s: float | None = None) -> dict[str, Any]:
    work = Path(spec.params["work_dir"])
    return {"evaluation": evaluation(spec, work, int((spec.params.get("stage") or {}).get("epoch_target_total") or 1),
                                     profile_default_s)}


def benchmark_blocked(spec: JobSpec, emit: Emitter) -> Any:
    """BENCHMARK on an unavailable target: a BLOCKED result (TARGET_BLOCKED), never a proxy number."""
    from mbnas_contracts.schemas.jobs import JobResult, JobStatus

    return JobResult(job_id=spec.job_id, status=JobStatus.BLOCKED, attempt=spec.attempt, reason_code="TARGET_BLOCKED",
                     message="synthetic: target device offline", outputs={"status": "BLOCKED"})


HANDLERS = {"PROPOSE_GENERATION": propose, "BASELINE_TRAIN": train, "CANDIDATE_TRAIN": train,
            "FINAL_RETRAIN": train, "EVALUATE": evaluate, "BENCHMARK": benchmark_blocked}
