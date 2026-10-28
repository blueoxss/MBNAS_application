"""Synthetic ETRI_NAS-shaped run directories for tests/archive (all numbers invented; nothing here is a measurement).

``candidate_evaluations.jsonl`` is created from strings at test time (``*.jsonl`` is git-ignored, so no fixture file for it
exists in the repository). Images are 1x1 PNG bytes built with zlib.
"""
from __future__ import annotations

import json
import os
import random
import struct
import time
import zlib
from pathlib import Path
from typing import Any


def png_1x1() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def conv(c: int, s: int) -> list[Any]:
    return [-1, 1, "Conv", [c, 3, s]]


def make_cfg(backbone: list[str], head: list[str]) -> dict[str, Any]:
    """A cfg.json whose rows are the given module names; every ``Conv`` is a stride-2 conv (it starts a new stage)."""
    bb = [conv(64, 2) if m == "Conv" else [-1, 1, m, [128]] for m in backbone]
    return {"nc": 1, "scales": {"n": [0.5, 0.25, 1024]}, "backbone": bb, "head": [[-1, 1, m, [64]] for m in head]}


def metrics(map50_95: float | None, lat: float | None, rec: float | None, params: int | None, **extra: Any) -> dict[str, Any]:
    if map50_95 is None:
        return {}
    m = {"map50_95": map50_95, "map50": min(1.0, map50_95 + 0.4), "small_target_recall": rec, "latency_median_ms": lat,
         "params": params, "params_deploy": params, "params_train": (params or 0) + 100, "latency_p90_ms": (lat or 0) * 1.05,
         "head_mode": "e2e", "train_time_s": 100.0, "peak_mem_mb": 1000.0, "forward_only_median_ms": (lat or 0) * 0.9,
         "t_post_ms": (lat or 0) * 0.1, "latency_cv": 0.02, "n_small_gt": 100}
    m.update(extra)
    return m


def row(cid: str, gen: int, fp: str, status: str, m: dict[str, Any], notes: dict[str, Any] | str, tier: str = "T@5e",
        attempt: int = 1, ckpt: str | None = None) -> dict[str, Any]:
    return {"candidate_id": cid, "slot_id": cid, "generation": gen, "attempt": attempt, "architecture_fingerprint": fp,
            "genotype_hash": fp, "config_hash": "cfg_" + cid, "dataset_manifest_hash": "dsmanifest0001", "training_protocol_hash": "train0001",
            "metric_protocol_hash": "metric0001", "fidelity_tier": tier, "seed": 42, "epochs": 5, "train_images": 0,
            "checkpoint_path": ckpt or f"/nonexistent/{cid}/weights/best.pt", "checkpoint_hash": None, "metrics": m, "status": status,
            "notes": notes if isinstance(notes, str) else json.dumps(notes)}


# ---- the hand-checked design (see EXPECTED) -----------------------------------------------------------------------
ANCHOR = "/x/cfg/yolo26n.yaml"
DESIGN: list[dict[str, Any]] = [
    row("g000_c0001", 0, "F1", "ok", metrics(0.30, 8.0, 0.90, 2000), {"mode": "seeded_anchor", "parents": [ANCHOR], "error": ""}),
    row("g000_c0002", 0, "F2", "ok", metrics(0.35, 7.0, 0.92, 2100), {"mode": "seeded_anchor", "parents": ["/x/cfg/yolov11n.yaml"], "error": ""}),
    row("g000_c0003", 0, "F3", "ok", metrics(0.20, 9.0, 0.80, 3000), {"mode": "random_crossover", "parents": ["a.yaml", "b.yaml"], "error": ""}),
    row("g000_c0004", 0, "F4", "oom", {}, {"mode": "random_crossover", "parents": ["a.yaml", "b.yaml"], "error": "CUDA out of memory"}),
    row("g001_c0005", 1, "F5", "ok", metrics(0.40, 7.5, 0.93, 2500),
        {"mode": "ga_crossover", "parents": ["g000_c0001", "g000_c0002"],
         "mutation": {"mutation_rate": 0.3, "prefer_modular": False, "pure_random_injection": False, "reasons": []}, "error": ""}),
    row("g001_c0006", 1, "F2", "ok", metrics(0.35, 7.0, 0.92, 2100),
        {"mode": "ga_crossover", "parents": ["g000_c0002", "g000_c0002"], "cache_hit": "g000_c0002", "error": ""}),
    row("g001_c0007", 1, "F7", "eval_fail", {}, {"mode": "ga_crossover", "parents": ["g000_c0001", "g000_c0003"], "error": "eval: RuntimeError"}),
    row("g002_c0008", 2, "F8", "ok", metrics(0.38, 5.0, 0.91, 1500),
        {"mode": "ga_crossover", "parents": ["g001_c0005", "g001_c0006"],
         "mutation": {"mutation_rate": 0.45, "prefer_modular": True, "pure_random_injection": False, "reasons": ["stagnation"]}, "error": ""}),
    row("g002_c0009", 2, "F9", "degenerate", metrics(0.0, 6.0, 0.0, 1800), {"mode": "ga_crossover", "parents": ["g001_c0005", "g000_c0001"], "error": ""}),
]
CFG_MODULES: dict[str, tuple[list[str], list[str]]] = {  # candidate -> (backbone modules, head modules); g001_c0006 / g000_c0003 have none
    "g000_c0001": (["Conv", "Conv", "C3k2"], ["Concat", "Detect"]),
    "g000_c0002": (["Conv", "C3k2", "SPPF"], ["Concat", "Detect"]),
    "g001_c0005": (["Conv", "Conv", "C3k2", "SPPF"], ["Concat", "C3k2", "Detect"]),
    "g002_c0008": (["Conv", "C3k2", "C3k2"], ["Concat", "Detect"]),
}
EXPECTED: dict[str, Any] = {
    "front2d": {0: ["g000_c0002"], 1: ["g001_c0005", "g000_c0002", "g001_c0006"], 2: ["g001_c0005", "g002_c0008"]},
    "front4d": {0: ["g000_c0002", "g000_c0001"], 1: ["g001_c0005", "g000_c0002", "g001_c0006", "g000_c0001"],
                2: ["g001_c0005", "g002_c0008", "g000_c0002", "g001_c0006"]},
    "new_fp": {0: 4, 1: 2, 2: 2},
    "hist_gen0": {"Conv": 3, "C3k2": 2, "Concat": 2, "Detect": 2, "SPPF": 1},
    "hist_gen1": {"Conv": 2, "C3k2": 2, "Concat": 1, "Detect": 1, "SPPF": 1},
}


def _gen_summary(g: int, rows: list[dict[str, Any]], hv: float, stagnation: int) -> dict[str, Any]:
    ok = [r for r in rows if r["status"] == "ok"]
    best = lambda k, f: f(r["metrics"][k] for r in ok) if ok else None  # noqa: E731
    return {"generation": g, "phase": "early", "population": len(rows), "valid": len(ok), "train_fail": 0, "degenerate": 0,
            "unique_fingerprints": len({r["architecture_fingerprint"] for r in rows}), "archive_size": 3 + g, "hypervolume": hv,
            "hv_improved": stagnation == 0, "stagnation": stagnation, "best_map50_95": best("map50_95", max),
            "best_small_recall": best("small_target_recall", max), "best_latency": best("latency_median_ms", min),
            "mutation": {"mutation_rate": 0.3, "prefer_modular": False, "pure_random_injection": False, "reasons": []}}


def write_jsonl(path: Path, rows: list[dict[str, Any]], tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail, encoding="utf-8")


def write_cfgs(run: Path, cfgs: dict[str, tuple[list[str], list[str]]]) -> None:
    for cid, (bb, hd) in cfgs.items():
        cfg = make_cfg(bb, hd)
        write_json(run / "search/candidates/train_runs" / f"{cid}.cfg.json", cfg)
        (run / "search/candidates/train_runs" / f"{cid}.yaml").write_text(f"nc: 1\n# yaml of {cid}\n", encoding="utf-8")


def notebook(stage_times: list[tuple[str, str, str]]) -> dict[str, Any]:
    cells = [{"cell_type": "markdown", "metadata": {}, "source": ["# title"]}]
    for label, a, b in stage_times:
        cells.append({"cell_type": "code", "metadata": {"execution": {"iopub.execute_input": a, "shell.execute_reply": b}},
                      "source": [f"# [{label}] synthetic stage\n", "x = 1\n"], "outputs": [], "execution_count": 1})
    return {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def make_run(root: Path, run_id: str = "synthetic-day", *, rows: list[dict[str, Any]] | None = None, finished: bool = True,
             pids: bool = False, extras: bool = True, jsonl_tail: str = "", cfgs: bool = True) -> Path:
    """A complete (finished) run by default; ``finished=False`` omits final_summary / run.log exit / baselines / confirm /
    pareto; ``pids=True`` writes driver/run.pids.json naming this process as the notebook (a live process)."""
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    rows = list(DESIGN if rows is None else rows)
    write_jsonl(run / "search/candidates/candidate_evaluations.jsonl", rows, jsonl_tail)
    if cfgs:
        write_cfgs(run, CFG_MODULES)
    gens = sorted({r["generation"] for r in rows if isinstance(r["generation"], int)})
    done_gens = gens if finished else gens[:-1]
    hv = 100.0
    for i, g in enumerate(done_gens):
        write_json(run / f"search/generations/gen_{g:03d}_summary.json", _gen_summary(g, [r for r in rows if r["generation"] == g], hv + i, 0 if i % 2 == 0 else 1))
    if finished:
        write_json(run / "search/archive.json", {"hv_history_raw": [100.0 + i for i in range(len(done_gens))], "note": "synthetic",
                                                 "archive": [{"candidate_id": "g000_c0002", "fingerprint": "F2", "generation": 0,
                                                              **metrics(0.35, 7.0, 0.92, 2100), "genotype": {"stages": []}}]})
        write_json(run / "protocol/baselines_v2.json", {
            "baseline_yolo26n": {"status": "ok", "map50": 0.9, "map50_95": 0.33, "small_target_recall": 0.9, "latency_median_ms": 6.5,
                                 "params_deploy": 2375031.0, "params_train": 2504190.0, "train_time_s": 3000.0, "peak_mem_mb": 2000.0,
                                 "best_ckpt": "/nonexistent/b/best.pt", "checkpoint_sha256": "abc", "cache_context": {"big": "x" * 50}},
            "baseline_yolov11n": {"status": "ok", "map50": 0.91, "map50_95": 0.34, "small_target_recall": 0.91, "latency_median_ms": 7.0,
                                  "params_deploy": 2582347.0}})
        write_json(run / "protocol_manifest.json", {"scale": "day", "preset": {"gens": 2, "final_epochs": 18, "pop": 4}, "seed": 42,
                                                    "protocol_v2_train_hash": "train0001"})
        write_json(run / "search/relative_to_anchor.json", {"anchor_fingerprint": "F1", "anchor_candidate_id": "g000_c0001",
                                                            "anchor_metrics": metrics(0.30, 8.0, 0.90, 2000), "per_generation": []})
        write_json(run / "search/exclusive_latency.json", {"g000_c0001": {"status": "ok", "latency_median_ms": 7.7, "exclusive": True,
                                                                         "checkpoint": "/x", "latency_protocol": {"warmup": 50}}})
        write_json(run / "final_summary.json", {
            "verdict": "PASS_WITH_WARNINGS", "scale": "day", "run_id": run_id, "baseline": "yolo26n",
            "dataset": {"nps_root": "/x/nps", "manifest_hash": "dsmanifest0001", "subsets": {"val": 10}},
            "roles": {"BEST_ACCURACY": {"candidate_id": "g001_c0005", "map50_95": 0.40},
                      "FASTEST_VALID": {"candidate_id": "g002_c0008", "latency_median_ms": 5.0}},
            "final_val": [{"cid": "g001_c0005", "status": "ok", "val_map50": 0.9, "val_map50_95": 0.41, "params_train": 2600.0, "train_s": 900.0,
                           "best_ckpt": "/nonexistent/final/best.pt", "small_target_recall": 0.94, "latency_median_ms": 7.4,
                           "params_deploy": 2500.0, "head_mode": "e2e", "checkpoint_sha256": "sha-final", "cache_context": {"huge": "y" * 30}}],
            "search": {"generations": len(gens), "archive": 1}, "fidelity": {"spearman": 0.5, "kendall": 0.3, "n": 5},
            "baselines_v2": {}})
        write_json(run / "checklist.json", {"[BOOT] env": {"status": "PASS", "measured": "ok", "criterion": ""},
                                            "[P8] jitter": {"status": "WARN", "measured": {"a": 1}, "criterion": "x < y", "hard": False},
                                            "[P9] floor": {"status": "FAIL", "measured": "0.01", "criterion": ">= 0.02", "hard": True}})
        (run / "run.log").write_text("[NbConvertApp] Converting\nNBCONVERT_EXIT=0\nTEST_UNLOCK_EVENT #3 2026-10-02T10:07:53\n", encoding="utf-8")
        write_json(run / f"{run_id}.executed.ipynb", notebook([
            ("P0", "2026-09-30T16:00:00.000000Z", "2026-09-30T16:00:10.500000Z"),
            ("P5-1", "2026-09-30T16:00:10.600000Z", "2026-09-30T16:10:10.600000Z"),
            ("P10-1", "2026-09-30T16:10:11.000000Z", "2026-09-30T16:11:00.000000Z")]))
        if extras:
            write_json(run / "three_seed/three_seed_summary.json", {
                "seeds": [42, 142, 242], "baseline_name": "baseline_yolo26n", "reference": "baseline_yolov11n",
                "protocol": {"epochs": 18, "max_det_observed": 300}, "test_unlock_used": True, "final_verdict": "EXECUTION_FAILED_WITH_EVIDENCE",
                "summary": {"final_g001_c0005": {"n_success": 3, "val_map50_95_mean": 0.41, "val_map50_mean": 0.9, "val_map50_95_std": 0.002,
                                                  "test_tile_map50_95_mean": 0.4, "test_native_map50_95_mean": 0.39,
                                                  "latency_median_ms_mean": 7.5, "params_deploy": 2500},
                            "baseline_yolo26n": {"n_success": 3, "val_map50_95_mean": 0.33, "test_tile_map50_95_mean": 0.31,
                                                 "test_native_map50_95_mean": 0.3, "latency_median_ms_mean": 6.6}},
                "verdicts": {"final_g001_c0005": {"strict": {"passed": False, "threshold": float("inf"), "fails": ["x"]}}}})
            (run / "three_seed/final_report.md").write_text("# report\n\nsynthetic\n", encoding="utf-8")
            for k, v in {"fidelity/rank_correlation.json": {"spearman": 0.5, "kendall": 0.3, "n": 5, "table": []},
                         "fidelity/noise.json": {"snr": 5.0}, "fidelity/corr_by_head_mode.json": {"e2e": {"n": 5}},
                         "fidelity/bootstrap_ci.json": {"ci95": [0.0, 1.0]}}.items():
                write_json(run / k, v)
            for rel in ("plots/pareto_map50_latency.png", "plots/final_pareto_val.png", "plots/hypervolume.png",
                        "plots/params_vs_map50.png", "fidelity/rank_correlation.png", "viz/samples_NPS_train.png",
                        "viz/samples_AOT_train.png"):
                (run / rel).parent.mkdir(parents=True, exist_ok=True)
                (run / rel).write_bytes(png_1x1())
            guard = [{"timestamp": 1790000000 + i * 60, "gpu": "1", "action": "wait", "external_mib": 16000 + i, "waited_s": i * 60}
                     for i in range(3)] + [
                {"timestamp": 1790000200, "gpu": "1", "action": "proceed", "external_mib": 0, "waited_s": 200},
                {"timestamp": 1790000300, "gpu": "0", "external_mib": 0, "phase": "BOOT"},
                {"timestamp": 1790000400, "gpu": "0", "action": "gpu_busy_giveup", "external_mib": 9000, "waited_s": 7200}]
            write_jsonl(run / "gpu_guard.log", guard)
    else:
        (run / "run.log").write_text("[NbConvertApp] Converting notebook\n", encoding="utf-8")
    if pids:
        import psutil

        now = time.time()
        started = psutil.Process(os.getpid()).create_time() - 10  # the "launcher" started before this (notebook) process
        write_json(run / "driver/run.pids.json", {"stage": "search", "run_id": run_id, "launcher_pid": os.getppid(), "launcher_start_epoch": started,
                                                  "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                                                  "nb_pid": os.getpid(), "kernel_pid": os.getpid()})
        (run / "driver").mkdir(exist_ok=True)
        (run / "driver/watchdog.log").write_text(
            "2026-10-13T05:31:32 [pid 1] watch 시작: stage=search nb_pid=1 budget_s=5400.0 interval=300\n"
            "2026-10-13T05:36:32 [pid 1] loop 2: elapsed=301.1 s, jsonl 불량행=0, 발동=없음\n", encoding="utf-8")
    return run


def make_big_run(root: Path, run_id: str = "synthetic-big", n_rows: int = 2000, per_gen: int = 100, seed: int = 7) -> Path:
    """A finished-looking run with ``n_rows`` random ok rows and a cfg.json per candidate (for timing tests)."""
    rng = random.Random(seed)
    mods = ["Conv", "C3k2", "C2f", "SPPF", "Bottleneck", "SCDown", "C2PSA"]
    rows, cfgs = [], {}
    for i in range(n_rows):
        g = i // per_gen
        cid = f"g{g:03d}_c{i:05d}"
        ok = rng.random() > 0.08
        m = metrics(rng.uniform(0.2, 0.55), rng.uniform(4, 12), rng.uniform(0.8, 0.99), rng.randint(900_000, 9_000_000)) if ok else {}
        rows.append(row(cid, g, f"fp{i % 1500:05d}", "ok" if ok else "oom", m,
                        {"mode": "ga_crossover", "parents": [f"g{max(g - 1, 0):03d}_c{max(i - per_gen, 0):05d}"] * 2, "error": ""}))
        cfgs[cid] = ([rng.choice(mods) for _ in range(rng.randint(5, 12))], ["Concat", "Detect"])
    write_cfgs(root / run_id, cfgs)
    return make_run(root, run_id, rows=rows, finished=True, extras=False, cfgs=False)
