"""Assemble the contract-shaped payloads (``schemas/archive.py``) from :class:`scanner.RunData`.

Rules shared by every builder: values are copied from the artifacts unchanged (no interpolation, no fake scalar fitness);
a missing value is ``None``; derived values (fronts, deltas, histograms, curves) are computed by :mod:`analysis`; every
response carries ``source`` (run dir + the files that fed it) and ``warnings``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from mbnas_server.archive import analysis as an
from mbnas_server.archive.live import driver_state, gpu_usage
from mbnas_server.archive.parsers import finite, read_text, tail_lines
from mbnas_server.archive.scanner import ArchiveError, ArchiveStore, CFG_DIR, RunData, within

MAX_WARNINGS = 60
MAX_FILE_BYTES = 8 << 20
MAX_YAML_BYTES = 256 << 10
MAX_REPORT_BYTES = 512 << 10

FILE_KINDS: dict[str, tuple[str, str]] = {
    "plot_pareto_map50_latency": ("plots/pareto_map50_latency.png", "image/png"),
    "plot_final_pareto_val": ("plots/final_pareto_val.png", "image/png"),
    "plot_hypervolume": ("plots/hypervolume.png", "image/png"),
    "plot_params_vs_map50": ("plots/params_vs_map50.png", "image/png"),
    "plot_rank_correlation": ("fidelity/rank_correlation.png", "image/png"),
    "viz_train": ("viz/samples_*_train.png", "image/png"),
    "viz_val": ("viz/samples_*_val.png", "image/png"),
    "final_report_md": ("three_seed/final_report.md", "text/markdown; charset=utf-8"),
    "run_log_tail": ("run.log", "text/plain; charset=utf-8"),
}
OBJECTIVE_ROWS = [{"key": k, "direction": d, "label": lbl} for k, d, lbl in an.OBJECTIVES]
STAGNATION_RULE = ("stagnation = number of consecutive generations without a hypervolume improvement (runtime value, copied "
                   "from gen_NNN_summary.json); the day pipeline keeps searching while stagnation < 3 and generation < max "
                   "(notebook cell P7-2, spec_v4 6.3)")
SEARCH_METRIC_KEYS = ("map50_95", "map50", "small_target_recall", "latency_median_ms", "params_deploy", "params")


# ----------------------------------------------------------------------------------------------------- helpers

def source(rd: RunData | None, *rels: str) -> dict[str, Any]:
    return {"run_dir": rd.ref.dir if rd else None, "files": rd.sources(*rels) if rd else []}


def warns(rd: RunData | None, *extra: str) -> list[str]:
    out: list[str] = []
    for w in list(extra) + (rd.warnings if rd else []):
        if w not in out:
            out.append(w)
    if len(out) > MAX_WARNINGS:
        out = out[:MAX_WARNINGS] + [f"... {len(out) - MAX_WARNINGS} more warning(s) omitted"]
    return out


def by_gen(rd: RunData) -> dict[int, list[dict[str, Any]]]:
    def build() -> dict[int, list[dict[str, Any]]]:
        out: dict[int, list[dict[str, Any]]] = {}
        for c in rd.cands:
            out.setdefault(c["generation"], []).append(c)
        return out
    return rd.once("by_gen", build)


def roles_map(rd: RunData) -> dict[str, list[str]]:
    def build() -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        roles = (rd.jdict("final_summary.json") or {}).get("roles")
        if isinstance(roles, dict):
            for role, entry in roles.items():
                if isinstance(entry, dict) and isinstance(entry.get("candidate_id"), str):
                    out.setdefault(entry["candidate_id"], []).append(role)
        return out
    return rd.once("roles_map", build)


def final_val_map(rd: RunData) -> dict[str, dict[str, Any]]:
    def build() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        fv = (rd.jdict("final_summary.json") or {}).get("final_val")
        for e in fv if isinstance(fv, list) else []:
            if isinstance(e, dict) and isinstance(e.get("cid"), str):
                out[e["cid"]] = {k: v for k, v in e.items() if k != "cache_context"}
        return out
    return rd.once("final_val_map", build)


def judge(rd: RunData) -> dict[str, Any] | None:
    def build() -> dict[str, Any] | None:
        name = rd.judge_name()
        if name is None:
            return None
        b, _ = rd.baselines()
        e = b[name]
        return {"name": name, "tier": rd.final_tier_label(), "status": e.get("status"), "map50_95": finite(e.get("map50_95")),
                "latency_median_ms": finite(e.get("latency_median_ms")), "params_deploy": finite(e.get("params_deploy"))}
    return rd.once("judge", build)


def cfg_ids(rd: RunData) -> set[str]:
    """Candidate ids that have a readable ``train_runs/<cid>.cfg.json`` inside the run (symlinks leaving the run are left out)."""
    def build() -> set[str]:
        p = rd.path(CFG_DIR)
        if p is None or not os.path.isdir(p):
            return set()
        suffix = ".cfg.json"
        out: set[str] = set()
        with os.scandir(p) as it:
            for e in it:
                if not e.name.endswith(suffix) or not an.valid_id(e.name[: -len(suffix)]):
                    continue
                if e.is_symlink() and not within(os.path.realpath(e.path), rd.ref.real):
                    continue
                out.add(e.name[: -len(suffix)])
        return out
    return rd.once("cfg_ids", build)


def ckpt_exists(rd: RunData, path: Any) -> bool | None:
    """True/False when ``path`` lies inside an archive root (checked with isfile), None when outside (not checked)."""
    if not isinstance(path, str) or not path:
        return None

    def check() -> bool | None:
        real = os.path.realpath(path)
        roots = [r.real for r in rd.store.discover()[0] if r.exists]
        if not any(within(real, r) for r in roots):
            return None
        return os.path.isfile(real)
    return rd.once("ckpt:" + path, check)


def metrics_copy(m: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if not isinstance(v, float) or finite(v) is not None else None) for k, v in m.items()}


# ----------------------------------------------------------------------------------------------------- candidates

def delta_vs_baseline(rd: RunData, cand: dict[str, Any]) -> dict[str, float | None]:
    """candidate minus the judge baseline; ``baseline`` / ``baseline_tier`` name what was subtracted (final tier vs search tier)."""
    j = judge(rd)
    if not j:
        return {"map50_95": None, "latency_median_ms": None, "params_deploy": None, "baseline": None, "baseline_tier": None}
    return {"map50_95": an.sub(an.metric(cand, "map50_95"), j["map50_95"]),
            "latency_median_ms": an.sub(an.metric(cand, "latency_median_ms"), j["latency_median_ms"]),
            "params_deploy": an.sub(an.metric(cand, "params_deploy"), j["params_deploy"]),
            "baseline": j["name"], "baseline_tier": j["tier"]}


def lineage_of(cand: dict[str, Any]) -> dict[str, Any]:
    n = cand["notes"]
    return {"mode": n["mode"], "parents": list(n["parents"]), "mutation": n["mutation"], "cache_hit": n["cache_hit"],
            "notes_parse": n["parse"]}


def candidate_row(rd: RunData, cand: dict[str, Any]) -> dict[str, Any]:
    f2, f4 = rd.front_flags()
    raw = cand["raw"]
    cid = cand["candidate_id"]
    new_ids = rd.new_fp().get(cand["generation"], (0, []))[1]
    return {
        "candidate_id": cid, "generation": cand["generation"], "fingerprint": cand["fingerprint"], "tier": cand["tier"],
        "status": cand["status"], "error": cand["notes"]["error"], "seed": cand["seed"], "epochs": cand["epochs"],
        "metrics": metrics_copy(cand["metrics"]), "lineage": lineage_of(cand), "roles": list(roles_map(rd).get(cid, [])),
        "is_final": cid in final_val_map(rd), "on_front2d": cand["idx"] in f2, "on_front4d": cand["idx"] in f4,
        "new_fingerprint": cid in new_ids,
        "delta_vs_baseline": delta_vs_baseline(rd, cand),
        "hashes": {"config": raw.get("config_hash"), "genotype": raw.get("genotype_hash"),
                   "metric_protocol": raw.get("metric_protocol_hash"), "training_protocol": raw.get("training_protocol_hash")},
        "has_cfg": cid in cfg_ids(rd), "has_checkpoint": bool(ckpt_exists(rd, raw.get("checkpoint_path"))),
    }


def rows_for(rd: RunData, cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache: dict[int, dict[str, Any]] = rd.once("row_cache", dict)
    out = []
    for c in cands:
        if c["idx"] not in cache:
            cache[c["idx"]] = candidate_row(rd, c)
        out.append(cache[c["idx"]])
    return out


# ----------------------------------------------------------------------------------------------------- run summary

def _hash_mode(values: list[Any]) -> str | None:
    vals = [v for v in values if isinstance(v, str) and v]
    return max(set(vals), key=vals.count) if vals else None


def arm_of(rd: RunData) -> dict[str, Any] | None:
    for d in (rd.jdict("arm.json"), (rd.jdict("preset.json") or {}).get("arm")):
        if isinstance(d, dict) and {"space", "method"} & set(d):
            return {"space": d.get("space"), "method": d.get("method"), "seed": d.get("seed")}
    return None


def run_summary(rd: RunData) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        fs = rd.jdict("final_summary.json") or {}
        doc, _ = rd.preset_doc()
        status, detail = rd.status()
        started, ended = rd.started_ended()
        cands = rd.cands
        ok = [c for c in cands if an.is_ok(c)]
        bst = {k: an.best_of(ok, k) for k in ("map50_95", "small_target_recall", "latency_median_ms")}
        by_status = an.status_counts(cands)
        manifest = rd.jdict("protocol_manifest.json") or {}
        planned = rd.preset().get("gens")
        planned = planned if isinstance(planned, int) and not isinstance(planned, bool) else None
        if planned is None and status == "done":
            sg = (fs.get("search") or {}).get("generations")
            planned = sg if isinstance(sg, int) and not isinstance(sg, bool) else None
        ts = rd.jdict("three_seed/three_seed_summary.json") or {}
        trans = rd.jdict("driver/transition.json") or {}
        smoke = rd.jdict("smoke_verdict.json") or {}
        ds = fs.get("dataset") if isinstance(fs.get("dataset"), dict) else {}
        plots_any = any(rd.exists(rel) for rel, _ in FILE_KINDS.values() if rel.startswith(("plots/", "fidelity/")))
        out = {
            "id": rd.id, "family": rd.family, "path": rd.ref.dir, "dataset_id": rd.dataset_id(),
            "scale": fs.get("scale") or (doc or {}).get("scale"), "status": status, "status_detail": detail,
            "started_at": started, "ended_at": ended, "duration_s": an.duration_s(started, ended),
            "verdicts": {"notebook": _s(fs.get("verdict")), "transition": _s(trans.get("verdict")),
                         "confirm": _s(ts.get("final_verdict")), "smoke_check": _s(smoke.get("verdict"))},
            "generations": {"planned": planned, "done": len(rd.summaries())},
            "candidates": {"total": len(cands), "ok": len(ok), "failed": len(cands) - len(ok), "by_status": by_status},
            "best": {"map50_95": bst["map50_95"][0] if bst["map50_95"] else None,
                     "small_target_recall": bst["small_target_recall"][0] if bst["small_target_recall"] else None,
                     "latency_min_ms": bst["latency_median_ms"][0] if bst["latency_median_ms"] else None},
            "judge_baseline": judge(rd), "arm": arm_of(rd),
            "hashes": {"protocol_v2_train": manifest.get("protocol_v2_train_hash") or _hash_mode([c["raw"].get("training_protocol_hash") for c in cands]),
                       "metric_protocol": _hash_mode([c["raw"].get("metric_protocol_hash") for c in cands]),
                       "dataset_manifest": _s(ds.get("manifest_hash")) or _hash_mode([c["raw"].get("dataset_manifest_hash") for c in cands])},
            "has": {"baselines": bool(rd.baselines()[0]), "generations": bool(rd.summaries()),
                    "pareto": rd.exists("search/archive.json") or rd.exists("search/pareto/pareto_archive.csv"),
                    "fidelity": rd.exists("fidelity/rank_correlation.json"), "confirm": rd.exists("three_seed/three_seed_summary.json"),
                    "plots": plots_any, "live": status == "running" or bool(rd.pid_states().get("confirm_pid"))},
        }
        out.update(rd.summary_extras(out))  # campaign arms: grouping fields / progress (archive/campaign.py); {} for plain runs
        return out
    return rd.cached_view("vol:summary", build)


def _s(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def run_list(store: ArchiveStore) -> dict[str, Any]:
    roots, refs, warns_ = store.discover()
    if not any(r.exists for r in roots):
        raise ArchiveError(503, "ARCHIVE_ROOT_UNAVAILABLE", "no configured archive root is available")

    def build() -> dict[str, Any]:
        runs = []
        wl = list(warns_)
        for ref in refs:
            try:
                rd = store.run(ref.id)
                runs.append(run_summary(rd))
                for w in rd.warnings:
                    wl.append(f"{ref.id}: {w}")
            except Exception as exc:  # noqa: BLE001 - one broken run must not hide the others
                wl.append(f"{ref.id}: could not be summarised ({type(exc).__name__})")
        order = {"running": 0, "done": 1, "failed": 2, "unknown": 3}
        runs.sort(key=lambda r: (order.get(r["status"], 9), -(an.parse_ts(r["started_at"]) or 0.0)))
        from mbnas_contracts.util.time import utcnow_iso
        return {"roots": [{"path": r.path, "family": r.family, "exists": r.exists, **({"kind": r.kind} if r.kind != "runs" else {})}
                          for r in roots], "runs": runs,
                "scanned_at": utcnow_iso(), "source": {"run_dir": None, "files": []}, "warnings": warns(None, *wl)}
    return store.ttl_memo("run_list", build)


def stages_of(rd: RunData) -> list[dict[str, Any]]:
    nb = rd.notebook()
    return list(nb["stages"]) if nb else []


def gates_of(rd: RunData) -> list[dict[str, Any]]:
    cl = rd.jdict("checklist.json") or {}
    out = []
    for name, v in cl.items():
        if not isinstance(v, dict):
            continue
        meas = v.get("measured")
        out.append({"name": name, "status": _s(v.get("status")), "hard": v.get("hard") if isinstance(v.get("hard"), bool) else None,
                    "measured": None if meas is None else (meas if isinstance(meas, str) else json.dumps(meas, ensure_ascii=False)),
                    "criterion": _s(v.get("criterion"))})
    return out


def roles_list(rd: RunData) -> list[dict[str, Any]]:
    roles = (rd.jdict("final_summary.json") or {}).get("roles")
    out = []
    for role, e in (roles.items() if isinstance(roles, dict) else []):
        if not isinstance(e, dict):
            continue
        vals = {k: finite(v) for k, v in e.items() if k != "candidate_id" and finite(v) is not None}
        key = next(iter(vals), None)
        out.append({"role": role, "candidate_id": _s(e.get("candidate_id")), "metric": key,
                    "value": vals[key] if key else None, "values": vals})
    return out


def fidelity_summary(rd: RunData) -> dict[str, Any] | None:
    rc = rd.jdict("fidelity/rank_correlation.json") or (rd.jdict("final_summary.json") or {}).get("fidelity")
    if not isinstance(rc, dict):
        return None
    noise = rd.jdict("fidelity/noise.json") or {}
    return {"spearman": finite(rc.get("spearman")), "kendall": finite(rc.get("kendall")),
            "n": rc.get("n") if isinstance(rc.get("n"), int) else None, "snr": finite(noise.get("snr"))}


def plots_of(rd: RunData) -> list[dict[str, Any]]:
    return [{"kind": k, "exists": resolve_kind(rd, k) is not None} for k in FILE_KINDS]


def run_detail(rd: RunData, live_fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        s = dict(run_summary(rd))
        fs = rd.jdict("final_summary.json") or {}
        fv = [{"candidate_id": cid, **e} for cid, e in final_val_map(rd).items()]
        af = rd.jdict("search/accuracy_floor.json")
        gg = an.gpu_guard_summary(rd.gpu_guard_records())
        s.update({
            "stages": stages_of(rd), "gates": gates_of(rd), "budget": rd.jdict("budget.json"),
            "preset": rd.preset_doc()[0], "dataset": fs.get("dataset") if isinstance(fs.get("dataset"), dict) else None,
            "accuracy_floor": af if af is not None else (fs.get("accuracy_floor") if isinstance(fs.get("accuracy_floor"), dict) else None),
            "roles": roles_list(rd), "final_val": fv, "fidelity_summary": fidelity_summary(rd), "gpu_guard": gg,
            "stop_rule": rd.jdict("driver/stop_rule.json"), "transition": rd.jdict("driver/transition.json"),
            "orphan_check": rd.jdict("driver/orphan_check.json"), "plots": plots_of(rd),
        })
        return s
    out = dict(rd.cached_view("vol:detail", build))
    out["live"] = live_fn() if out["has"]["live"] else None
    out["source"] = source(rd, f"{rd.id}.executed.ipynb", "run.log", "final_summary.json", "checklist.json", "budget.json",
                           "preset.json", "protocol_manifest.json", "search/accuracy_floor.json", "gpu_guard.log",
                           "search/gpu_guard.log", "driver/stop_rule.json", "driver/transition.json", "driver/orphan_check.json",
                           "driver/run.pids.json", "fidelity/rank_correlation.json", "fidelity/noise.json",
                           "search/candidates/candidate_evaluations.jsonl", "protocol/baselines_v2.json")
    out["warnings"] = warns(rd)
    return out


# ----------------------------------------------------------------------------------------------------- baselines

def _confirm_for(rd: RunData, name: str) -> dict[str, Any] | None:
    ts = rd.jdict("three_seed/three_seed_summary.json") or {}
    s = (ts.get("summary") or {}).get(name)
    if not isinstance(s, dict):
        return None
    return {"val_map50_95": finite(s.get("val_map50_95_mean")), "test_tiles_map50_95": finite(s.get("test_tile_map50_95_mean")),
            "test_native_map50_95": finite(s.get("test_native_map50_95_mean")),
            "latency_median_ms": finite(s.get("latency_median_ms_mean")), "n_success": s.get("n_success"),
            "val_map50_95_std": finite(s.get("val_map50_95_std")), "tier": "confirm 3-seed mean"}


def anchor_of(rd: RunData) -> dict[str, Any] | None:
    ra = rd.jdict("search/relative_to_anchor.json")
    if not ra or not isinstance(ra.get("anchor_metrics"), dict):
        return None
    cid = ra.get("anchor_candidate_id")
    row = rd.latest(cid) if isinstance(cid, str) else None
    return {"candidate_id": cid if isinstance(cid, str) else None, "fingerprint": ra.get("anchor_fingerprint"),
            "metrics": metrics_copy(ra["anchor_metrics"]), "tier": row["tier"] if row else None,
            "epochs": row["epochs"] if row else None}


def baselines_view(rd: RunData) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        base, src = rd.baselines()
        jn = rd.judge_name()
        tier, epochs = rd.final_tier_label(), rd.final_epochs()
        items = []
        for name, e in base.items():
            items.append({
                "name": name, "kind": "baseline", "cfg_name": name[len("baseline_"):] if name.startswith("baseline_") else name,
                "tier": tier, "epochs": epochs, "is_judge": name == jn, "status": _s(e.get("status")),
                "map50_95": finite(e.get("map50_95")), "map50": finite(e.get("map50")),
                "small_target_recall": finite(e.get("small_target_recall")), "latency_median_ms": finite(e.get("latency_median_ms")),
                "latency_exclusive_ms": None, "forward_only_median_ms": finite(e.get("forward_only_median_ms")),
                "t_post_ms": finite(e.get("t_post_ms")), "params_deploy": finite(e.get("params_deploy")),
                "params_train": finite(e.get("params_train")), "peak_mem_mb": finite(e.get("peak_mem_mb")),
                "train_time_s": finite(e.get("train_time_s")), "head_mode": _s(e.get("head_mode")),
                "best_ckpt": _s(e.get("best_ckpt")), "checkpoint_sha256": _s(e.get("checkpoint_sha256")),
                "confirm": _confirm_for(rd, name)})
        anchor = anchor_of(rd)
        if anchor:
            m = anchor["metrics"]
            row = rd.latest(anchor["candidate_id"]) if anchor["candidate_id"] else None
            parents = row["notes"]["parents"] if row else []
            stem = os.path.basename(parents[0]).rsplit(".", 1)[0] if parents else "anchor"
            excl = (rd.jdict("search/exclusive_latency.json") or {}).get(anchor["candidate_id"]) or {}
            items.append({
                "name": f"anchor_{stem}", "kind": "anchor", "cfg_name": stem, "tier": anchor["tier"], "epochs": anchor["epochs"],
                "is_judge": False, "status": "ok", "map50_95": finite(m.get("map50_95")), "map50": finite(m.get("map50")),
                "small_target_recall": finite(m.get("small_target_recall")), "latency_median_ms": finite(m.get("latency_median_ms")),
                "latency_exclusive_ms": finite(excl.get("latency_median_ms")),
                "forward_only_median_ms": finite(m.get("forward_only_median_ms")), "t_post_ms": finite(m.get("t_post_ms")),
                "params_deploy": finite(m.get("params_deploy")), "params_train": finite(m.get("params_train")),
                "peak_mem_mb": finite(m.get("peak_mem_mb")), "train_time_s": finite(m.get("train_time_s")),
                "head_mode": _s(m.get("head_mode")), "best_ckpt": _s(row["raw"].get("checkpoint_path")) if row else None,
                "checkpoint_sha256": None, "confirm": None})
        search_tier = anchor["tier"] if anchor else _hash_mode([c["tier"] for c in rd.cands])
        notes = [f"baselines are measured at the final tier ({tier}); the anchor row and every search candidate at the search tier "
                 f"({search_tier or 'unknown'}): different training budgets, do not read differences as like-for-like.",
                 "latency_median_ms is the tier's own latency protocol; latency_exclusive_ms is the exclusive GPU re-measurement "
                 "(search/exclusive_latency.json), available for the anchor and finalists only."]
        w = []
        if not base:
            w.append("protocol/baselines_v2.json missing: no baselines available")
        return {"judge_baseline": judge(rd), "baselines": items, "anchor": anchor, "notes": notes,
                "source": source(rd, src or "protocol/baselines_v2.json", "search/relative_to_anchor.json",
                                "search/exclusive_latency.json", "three_seed/three_seed_summary.json", "preset.json",
                                "protocol_manifest.json"),
                "warnings": warns(rd, *w)}
    return rd.cached_view("baselines", build)


# ----------------------------------------------------------------------------------------------------- generations

def _front_points(rd: RunData, idxs: list[int]) -> list[dict[str, Any]]:
    out = []
    for i in sorted(idxs, key=lambda i: (-(an.metric(rd.cands[i], "map50_95") or 0.0), i)):
        c = rd.cands[i]
        out.append({"candidate_id": c["candidate_id"], "map50_95": an.metric(c, "map50_95"),
                    "latency_median_ms": an.metric(c, "latency_median_ms"),
                    "small_target_recall": an.metric(c, "small_target_recall"), "params_deploy": an.metric(c, "params_deploy")})
    return out


def generation_items(rd: RunData) -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        gens = rd.generation_numbers()
        summ, bg, fr, nfp = rd.summaries(), by_gen(rd), rd.fronts(), rd.new_fp()
        j = judge(rd)
        out: list[dict[str, Any]] = []
        prev_g: int | None = None
        prev_summary: dict[str, Any] | None = None
        prev_f2: list[str] = []
        prev_f4: list[str] = []
        for g in gens:
            rows = bg.get(g, [])
            s = summ.get(g)
            item: dict[str, Any] = dict(s) if s else {}
            for k in ("phase", "population", "valid", "train_fail", "degenerate", "unique_fingerprints", "archive_size",
                      "hypervolume", "hv_improved", "stagnation", "best_map50_95", "best_small_recall", "best_latency", "mutation"):
                item.setdefault(k, None)
            item["generation"] = g
            item["has_summary"] = s is not None
            f2, f4 = an.ids_of(rd.cands, fr["2d"][g]), an.ids_of(rd.cands, fr["4d"][g])
            n_new, new_ids = nfp.get(g, (0, []))
            cum = an.cumulative_best(rd.cands, g)

            def d(key: str) -> float | None:
                return an.sub(finite(s.get(key)) if s else None, finite(prev_summary.get(key)) if prev_summary else None)
            best_map = finite(item["best_map50_95"])
            best_lat = finite(item["best_latency"])
            item.update({
                "n_rows": len(rows), "status_counts": an.status_counts(rows), "new_fingerprints": n_new,
                "new_fingerprint_ids": new_ids, "lineage_modes": an.lineage_modes(rows),
                "delta_vs_prev": {"hypervolume": d("hypervolume"), "best_map50_95": d("best_map50_95"),
                                  "best_small_recall": d("best_small_recall"), "best_latency": d("best_latency")},
                "delta_vs_baseline": {"best_map50_95": an.sub(best_map, j["map50_95"] if j else None),
                                      "best_latency": an.sub(best_lat, j["latency_median_ms"] if j else None),
                                      "cum_best_map50_95": an.sub(cum["map50_95"], j["map50_95"] if j else None),
                                      "baseline_name": j["name"] if j else None, "baseline_tier": j["tier"] if j else None,
                                      "note": "the baseline is a final-tier run, the generation bests are search-tier values"},
                "cum_best": cum, "front2d": f2, "front4d": f4,
                "front_added": [c for c in f2 if c not in prev_f2], "front_removed": [c for c in prev_f2 if c not in f2],
                "front4d_added": [c for c in f4 if c not in prev_f4], "front4d_removed": [c for c in prev_f4 if c not in f4],
                "characteristics": an.characteristics(rows, bg.get(prev_g, []) if prev_g is not None else [],
                                                      lambda cid: rd.cfg(cid)),
            })
            out.append(item)
            prev_g, prev_f2, prev_f4 = g, f2, f4
            if s:
                prev_summary = s
        return out
    return rd.cached_view("generation_items", build)


def hv_history(rd: RunData) -> list[float]:
    ar = rd.jdict("search/archive.json") or {}
    h = ar.get("hv_history_raw")
    if isinstance(h, list):
        vals = [finite(v) for v in h]
        if all(v is not None for v in vals):
            return [v for v in vals if v is not None]
    return [v for v in (finite(rd.summaries()[g].get("hypervolume")) for g in sorted(rd.summaries())) if v is not None]


def generations_view(rd: RunData) -> dict[str, Any]:
    items = generation_items(rd)
    extra = []
    if not items:
        extra.append("no generation data (search/generations and candidate rows are empty)")
    miss = sum(i["characteristics"]["n_cfg_missing"] for i in items)
    if miss:
        extra.append(f"{miss} ok candidate(s) have no train_runs cfg.json (cache-hit twins); they are left out of module histograms")
    j = judge(rd)
    notes = ["NSGA-II multi-objective search: there is no scalar fitness; hypervolume / stagnation / best_* are runtime values copied "
             "from gen_NNN_summary.json (best_* are the best of that generation, not cumulative; cum_best is computed).",
             "front2d = (map50_95 max, latency_median_ms min) non-dominated set of ok rows with generation <= g; front4d adds "
             "small_target_recall max and params_deploy min.",
             "delta_vs_baseline = value - judge baseline (" + (f"{j['name']}, {j['tier']}" if j else "none") + "); the baseline is "
             "trained longer than search candidates, so the difference mixes tiers."]
    return {"objectives": OBJECTIVE_ROWS, "generations": items, "hv_history": hv_history(rd), "stagnation_rule": STAGNATION_RULE,
            "notes": notes, "source": source(rd, "search/candidates/candidate_evaluations.jsonl", "search/archive.json",
                                             "protocol/baselines_v2.json", *[f"{os.path.join('search/generations', f'gen_{g:03d}_summary.json')}"
                                                                           for g in sorted(rd.summaries())]),
            "warnings": warns(rd, *extra)}


def generation_detail(rd: RunData, g: int) -> dict[str, Any]:
    items = generation_items(rd)
    gens = [i["generation"] for i in items]
    if g not in gens:
        raise ArchiveError(404, "ARCHIVE_NOT_FOUND", f"generation {g} not found in run {rd.id}")
    item = items[gens.index(g)]
    prev_g = gens[gens.index(g) - 1] if gens.index(g) > 0 else None
    fr = rd.fronts()["2d"]
    rows = by_gen(rd).get(g, [])
    extra = [] if item["has_summary"] else [f"gen_{g:03d}_summary.json missing: generation still running or summary not written"]
    return {"generation": item, "candidates": rows_for(rd, rows), "improvements": an.improvements(rd.cands, g),
            "front2d_points": _front_points(rd, fr[g]), "prev_front2d_points": _front_points(rd, fr[prev_g]) if prev_g is not None else [],
            "lineage": [{"candidate_id": c["candidate_id"], **lineage_of(c)} for c in rows],
            "characteristics": item["characteristics"],
            "source": source(rd, "search/candidates/candidate_evaluations.jsonl", f"search/generations/gen_{g:03d}_summary.json"),
            "warnings": warns(rd, *extra)}


# ----------------------------------------------------------------------------------------------------- candidate list/detail

def candidate_list(rd: RunData, generation: int | None, tier: str | None, status: str | None, limit: int, offset: int) -> dict[str, Any]:
    sel = [c for c in rd.cands if (generation is None or c["generation"] == generation) and (tier is None or c["tier"] == tier)
           and (status is None or c["status"] == status)]
    page = sel[offset: offset + limit]
    return {"total": len(sel), "candidates": rows_for(rd, page),
            "source": source(rd, "search/candidates/candidate_evaluations.jsonl", "search/pareto/pareto_archive.csv"),
            "warnings": warns(rd)}


def _eval_entry(stage: str, tier: str | None, epochs: Any, status: Any, metrics: dict[str, Any], src: str,
                seed: Any = None) -> dict[str, Any]:
    return {"stage": stage, "tier": tier, "epochs": epochs if isinstance(epochs, int) else None, "seed": seed if isinstance(seed, int) else None,
            "status": status if isinstance(status, str) else None, "metrics": metrics_copy(metrics), "source": src}


def _final_metrics(e: dict[str, Any]) -> dict[str, Any]:
    m = {k: v for k, v in e.items() if k not in ("cid", "status", "best_ckpt", "checkpoint_sha256", "cache_context")}
    m["map50_95"] = e.get("val_map50_95")
    m["map50"] = e.get("val_map50")
    m["train_time_s"] = e.get("train_s")
    return m


def candidate_detail(rd: RunData, cid: str) -> dict[str, Any]:
    if not an.valid_id(cid):
        raise ArchiveError(422, "ARCHIVE_BAD_ID", "candidate id must match [A-Za-z0-9._-]{1,128} and not start with '.'")
    rows = rd.by_id().get(cid)
    if not rows:
        raise ArchiveError(404, "ARCHIVE_NOT_FOUND", f"candidate {cid} not found in run {rd.id}")
    cand = rows[-1]
    w: list[str] = []
    evals = [_eval_entry("search", c["tier"], c["epochs"], c["status"], c["metrics"],
                         "search/candidates/candidate_evaluations.jsonl", c["seed"]) for c in rows]
    fv = final_val_map(rd).get(cid)
    if fv:
        evals.append(_eval_entry("final", rd.final_tier_label(), rd.final_epochs(), fv.get("status"), _final_metrics(fv), "final_summary.json"))
    excl = (rd.jdict("search/exclusive_latency.json") or {}).get(cid)
    if isinstance(excl, dict):
        evals.append(_eval_entry("exclusive", "exclusive latency", None, excl.get("status"),
                                 {k: v for k, v in excl.items() if k not in ("checkpoint", "latency_protocol", "status")},
                                 "search/exclusive_latency.json"))
    ts = rd.jdict("three_seed/three_seed_summary.json") or {}
    conf_key = f"final_{cid}"
    conf = (ts.get("summary") or {}).get(conf_key)
    confirm = None
    if isinstance(conf, dict):
        verdict = (ts.get("verdicts") or {}).get(conf_key)
        confirm = {"name": conf_key, "summary": conf, "verdicts": verdict, "final_verdict": ts.get("final_verdict"),
                   "seeds": ts.get("seeds")}
        evals.append(_eval_entry("confirm", "confirm 3-seed mean", None, "ok" if conf.get("n_success") else "failed",
                                 {"map50_95": conf.get("val_map50_95_mean"), "map50": conf.get("val_map50_mean"),
                                  "test_tile_map50_95": conf.get("test_tile_map50_95_mean"),
                                  "test_native_map50_95": conf.get("test_native_map50_95_mean"),
                                  "latency_median_ms": conf.get("latency_median_ms_mean"), "params_deploy": conf.get("params_deploy"),
                                  "n_success": conf.get("n_success")}, "three_seed/three_seed_summary.json"))
    # architecture
    cfg, cfg_src = rd.cfg_or_twin(cid)
    arch = None
    yaml_rel = None
    if cfg is not None and cfg_src is not None:
        yaml_rel = f"{CFG_DIR}/{cfg_src}.yaml"
        yp = rd.path(yaml_rel)
        ytext = read_text(yp, MAX_YAML_BYTES)[0] if yp else None
        stages = an.derive_stages(cfg)
        arch = {"yaml_text": ytext, "cfg": {"nc": cfg.get("nc"), "scales": cfg.get("scales"), "backbone": cfg.get("backbone"),
                                            "head": cfg.get("head")},
                "n_backbone": len(an.cfg_rows(cfg, "backbone")), "n_head": len(an.cfg_rows(cfg, "head")),
                "stage_count": len(stages), "stages": stages, "stages_source": "derived_from_cfg",
                "module_histogram": an.module_histogram([cfg]),
                "source": {"kind": "own" if cfg_src == cid else "fingerprint_twin", "candidate_id": cfg_src}}
        if cfg_src != cid:
            w.append(f"{cid} has no cfg.json of its own; the architecture shown is that of {cfg_src} (same fingerprint)")
    else:
        w.append(f"no cfg.json for {cid}: architecture unavailable")
    # lineage
    n = cand["notes"]
    parents, diffs = [], []
    for p in n["parents"]:
        prow = rd.latest(p) if an.valid_id(p) else None
        if prow is not None:
            parents.append({"candidate_id": p, "kind": "candidate", "fingerprint": prow["fingerprint"], "generation": prow["generation"],
                            "metrics_summary": {"status": prow["status"], **{k: an.metric(prow, k) for k in
                                                                              ("map50_95", "small_target_recall", "latency_median_ms", "params_deploy")}},
                            "has_cfg": p in cfg_ids(rd)})
            pcfg, _ = rd.cfg_or_twin(p)
            if cfg is not None and pcfg is not None:
                diffs.append(an.parent_diff(p, pcfg, cfg))
        else:
            parents.append({"candidate_id": p[:200], "kind": "external_config", "fingerprint": None, "generation": None,
                            "metrics_summary": None, "has_cfg": False})
    if n["parse"] != "ok":
        w.append(f"notes of {cid} are {'cut off (500-char limit)' if n['parse'] == 'truncated' else 'missing'}: lineage fields recovered from the visible part only")
    m = cand["metrics"]
    ex_obj = excl if isinstance(excl, dict) else None
    latency = {"median": finite(m.get("latency_median_ms")), "p90": finite(m.get("latency_p90_ms")), "mean": finite(m.get("latency_mean_ms")),
               "std": finite(m.get("latency_std_ms")), "cv": finite(m.get("latency_cv")),
               "forward_only": finite(m.get("forward_only_median_ms")), "t_post": finite(m.get("t_post_ms")),
               "head_mode": _s(m.get("head_mode")), "protocol_hash": _s(m.get("latency_protocol_hash")),
               "exclusive": ({k: v for k, v in ex_obj.items() if k != "checkpoint"} if ex_obj else None)}
    ckpts, seen_paths = [], set()
    for c in rows:
        p = c["raw"].get("checkpoint_path")
        if isinstance(p, str) and p not in seen_paths:
            seen_paths.add(p)
            ckpts.append({"tier": c["tier"], "path": p, "exists": ckpt_exists(rd, p), "sha256": _s(c["raw"].get("checkpoint_hash"))})
    if fv and isinstance(fv.get("best_ckpt"), str) and fv["best_ckpt"] not in seen_paths:
        ckpts.append({"tier": rd.final_tier_label(), "path": fv["best_ckpt"], "exists": ckpt_exists(rd, fv["best_ckpt"]),
                      "sha256": _s(fv.get("checkpoint_sha256"))})
    row = rows_for(rd, [cand])[0]
    return {"row": row, "evaluations": evals, "architecture": arch,
            "lineage": {"mode": n["mode"], "parents": parents, "mutation": n["mutation"], "cache_hit": n["cache_hit"],
                        "notes_parse": n["parse"], "diff_vs_parents": diffs},
            "latency": latency, "checkpoints": ckpts, "roles": list(roles_map(rd).get(cid, [])), "confirm": confirm,
            "provenance": {"cfg_path": f"{CFG_DIR}/{cid}.cfg.json" if cid in cfg_ids(rd) else None,
                           "yaml_path": yaml_rel, "jsonl_path": "search/candidates/candidate_evaluations.jsonl"},
            "source": source(rd, "search/candidates/candidate_evaluations.jsonl", f"{CFG_DIR}/{cid}.cfg.json",
                             f"{CFG_DIR}/{cid}.yaml", "search/exclusive_latency.json", "final_summary.json",
                             "three_seed/three_seed_summary.json", *( [yaml_rel] if yaml_rel else [])),
            "warnings": warns(rd, *w)}


# ----------------------------------------------------------------------------------------------------- pareto / fidelity / confirm

def pareto_view(rd: RunData) -> dict[str, Any]:
    ar = rd.jdict("search/archive.json")
    entries: list[dict[str, Any]] = []
    w: list[str] = []
    src = "search/archive.json"
    if ar and isinstance(ar.get("archive"), list):
        for e in ar["archive"]:
            if isinstance(e, dict):
                entries.append({k: v for k, v in e.items() if k != "genotype"})
    else:
        rows = rd.pareto_csv_rows()
        src = "search/pareto/pareto_archive.csv"
        for r in rows:
            e: dict[str, Any] = {}
            for k, v in r.items():
                try:
                    e[k] = float(v) if v not in ("", None) and k not in ("candidate_id", "fingerprint", "head_mode", "latency_protocol_hash") else (v or None)
                except ValueError:
                    e[k] = v
            if isinstance(e.get("generation"), float):
                e["generation"] = int(e["generation"])
            entries.append(e)
        w.append("search/archive.json missing: pareto archive entries read from pareto_archive.csv" if rows
                 else "no runtime pareto archive found (search/archive.json and pareto_archive.csv missing)")
    return {"objectives": OBJECTIVE_ROWS, "archive_entries": entries, "front4d_ids": rd.final_front_ids("4d"),
            "front2d_ids": rd.final_front_ids("2d"), "hv_history": hv_history(rd),
            "runtime_archive_ids": [e.get("candidate_id") for e in entries if isinstance(e.get("candidate_id"), str)],
            "source": source(rd, src, "search/candidates/candidate_evaluations.jsonl"),
            "warnings": warns(rd, *w)}


def fidelity_view(rd: RunData) -> dict[str, Any]:
    rc = rd.jdict("fidelity/rank_correlation.json")
    n = rc.get("n") if isinstance(rc, dict) else None
    note: str
    if rc is None:
        note = "fidelity/rank_correlation.json missing: no fidelity evidence for this run"
    elif isinstance(n, int) and n <= 5:
        note = f"n={n}: 파이프라인 점검, 비증거 (too few pairs for a rank-correlation claim)"
    else:
        ci = (rd.jdict("fidelity/bootstrap_ci.json") or {}).get("ci95")
        note = (f"n={n}: small sample; bootstrap CI95 of Spearman {ci}" if ci else f"n={n}") + " (pre-registered gate values are in bootstrap_ci.json)"
    miss = [r for r in ("rank_correlation", "noise", "corr_by_head_mode", "bootstrap_ci") if not rd.exists(f"fidelity/{r}.json")]
    return {"rank_correlation": rc, "noise": rd.jdict("fidelity/noise.json"), "corr_by_head_mode": rd.jdict("fidelity/corr_by_head_mode.json"),
            "bootstrap_ci": rd.jdict("fidelity/bootstrap_ci.json"), "evidence_note": note,
            "source": source(rd, *[f"fidelity/{r}.json" for r in ("rank_correlation", "noise", "corr_by_head_mode", "bootstrap_ci")]),
            "warnings": warns(rd, *([f"fidelity files missing: {', '.join(miss)}"] if miss else []))}


def confirm_view(rd: RunData) -> dict[str, Any]:
    ts = rd.jdict("three_seed/three_seed_summary.json")
    report = None
    rp = rd.path("three_seed/final_report.md")
    if rp:
        report = read_text(rp, MAX_REPORT_BYTES)[0]
    ts = ts or {}
    prot = ts.get("protocol") if isinstance(ts.get("protocol"), dict) else {}
    w = [] if ts else ["three_seed/three_seed_summary.json missing: confirm stage not run or not finished"]
    return {"final_verdict": _s(ts.get("final_verdict")), "seeds": ts.get("seeds") if isinstance(ts.get("seeds"), list) else None,
            "baseline_name": _s(ts.get("baseline_name")), "reference": _s(ts.get("reference")),
            "summary": ts.get("summary") if isinstance(ts.get("summary"), dict) else None,
            "verdicts": ts.get("verdicts") if isinstance(ts.get("verdicts"), dict) else None,
            "max_det_observed": prot.get("max_det_observed"), "test_unlock_used": ts.get("test_unlock_used"),
            "final_report_md": report,
            "source": source(rd, "three_seed/three_seed_summary.json", "three_seed/final_report.md"), "warnings": warns(rd, *w)}


# ----------------------------------------------------------------------------------------------------- compare

def _str(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def conditions_of(rd: RunData, summary: dict[str, Any]) -> dict[str, Any]:
    """Run conditions for compare (contract 1.4.0 ``ArchiveCompareConditions``): only values the run artifacts record, else None.

    dataset = ``dataset_id`` (as the summary); split = ``protocol/protocol_v2.json`` eval.split; stage = the distinct search
    ``fidelity_tier`` labels of the evaluation rows plus the final tier label when ``preset.final_epochs`` (or the confirm
    protocol) records it; epochs_total = that final epoch count; protocol_hash = the run's training protocol hash (summary
    ``hashes.protocol_v2_train``); device / precision = the latency protocol's ``latency.device`` / ``latency.fp16`` (the only
    place the protocol records them; training amp is not reported here); checkpoint = ``best_ckpt`` of the BEST_ACCURACY
    finalist in ``final_summary.json`` (None when no such role/entry).
    """
    def build() -> dict[str, Any]:
        proto = rd.jdict("protocol/protocol_v2.json") or {}
        lat = proto.get("latency") if isinstance(proto.get("latency"), dict) else {}
        ev = proto.get("eval") if isinstance(proto.get("eval"), dict) else {}
        fp16 = lat.get("fp16")
        precision = _str(lat.get("precision")) or ("fp16" if fp16 is True else "fp32" if fp16 is False else None)
        epochs = rd.final_epochs()
        tiers = sorted({c["tier"] for c in rd.cands if isinstance(c.get("tier"), str) and c["tier"]})
        parts = (["search " + ",".join(tiers)] if tiers else []) + ([rd.final_tier_label()] if epochs is not None else [])
        roles = (rd.jdict("final_summary.json") or {}).get("roles")
        best = roles.get("BEST_ACCURACY") if isinstance(roles, dict) else None
        cid = best.get("candidate_id") if isinstance(best, dict) else None
        ckpt = _str(final_val_map(rd).get(cid, {}).get("best_ckpt")) if isinstance(cid, str) else None
        return {"dataset": summary.get("dataset_id"), "split": _str(ev.get("split")), "stage": "; ".join(parts) or None,
                "epochs_total": epochs, "protocol_hash": summary["hashes"]["protocol_v2_train"],
                "device": _str(lat.get("device")), "precision": precision, "checkpoint": ckpt}
    return rd.once("conditions", build)


def compare_view(store: ArchiveStore, ids: list[str]) -> dict[str, Any]:
    runs, hashes, datasets = [], set(), set()
    wl: list[str] = []
    for rid in ids:
        rd = store.run(rid)
        s = run_summary(rd)
        pts, skipped, hits = an.best_so_far_curve(rd.cands)
        hv = [{"generation": g, "value": finite(rd.summaries()[g].get("hypervolume"))} for g in sorted(rd.summaries())]
        fin = []
        for cid, e in final_val_map(rd).items():
            fin.append({"candidate_id": cid, "val_map50_95": finite(e.get("val_map50_95")), "val_map50": finite(e.get("val_map50")),
                        "small_target_recall": finite(e.get("small_target_recall")), "latency_median_ms": finite(e.get("latency_median_ms")),
                        "params_deploy": finite(e.get("params_deploy")), "roles": roles_map(rd).get(cid, []),
                        "tier": rd.final_tier_label()})
        b = baselines_view(rd)
        runs.append({"id": rid, "family": rd.family, "arm": s["arm"], "dataset_id": s["dataset_id"], "judge_baseline": s["judge_baseline"],
                     "scale": s["scale"], "status": s["status"], "metric_protocol_hash": s["hashes"]["metric_protocol"],
                     "conditions": conditions_of(rd, s),
                     "curves": {"best_so_far_map50_95": pts, "hypervolume": hv}, "finalists": fin,
                     "baselines": [{k: x[k] for k in ("name", "kind", "tier", "map50_95", "small_target_recall",
                                                      "latency_median_ms", "params_deploy", "is_judge")} for x in b["baselines"]],
                     "train_time_rows_missing": skipped, "cache_hit_rows": hits})
        hashes.add(s["hashes"]["metric_protocol"])
        datasets.add(s["dataset_id"])
        for x in rd.warnings:
            wl.append(f"{rid}: {x}")
    reasons = []
    if len(datasets) > 1 or None in datasets:
        reasons.append("dataset_id differs or is unknown: " + ", ".join(sorted(str(d) for d in datasets)))
    if len(hashes) > 1 or None in hashes:
        reasons.append("metric_protocol_hash differs or is unknown: " + ", ".join(sorted(str(h) for h in hashes)))
    comparable = not reasons
    for r in runs:
        r["comparable"] = comparable
        r["incomparable_reason"] = "; ".join(reasons) if reasons else None
    notes = ["cum_train_time_s sums metrics.train_time_s in candidate_evaluations.jsonl row order; rows without a value are skipped "
             "(counted in train_time_rows_missing); cache-hit rows carry a copy of their source's time (cum_train_time_s_no_cache leaves them out)."]
    return {"runs": runs, "comparable": comparable, "incomparable_reason": "; ".join(reasons) if reasons else None, "notes": notes,
            "source": {"run_dir": None, "files": []}, "warnings": warns(None, *wl)}


# ----------------------------------------------------------------------------------------------------- live

def _watchdog(rd: RunData, pids: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    lines = rd.watchdog_tail(60)
    last = lines[-1] if lines else None
    budget = elapsed = None
    import re
    for ln in lines:
        m = re.search(r"budget_s=([0-9.]+)", ln)
        if m:
            budget = float(m.group(1))
        m = re.search(r"elapsed=([0-9.]+)", ln)
        if m:
            elapsed = float(m.group(1))
    if elapsed is None:
        ep = finite(pids.get("launcher_start_epoch"))
        if ep is not None:
            import time
            elapsed = round(max(0.0, time.time() - ep), 1)
    return last, budget, elapsed


def live_view(store: ArchiveStore) -> dict[str, Any]:
    from mbnas_contracts.util.time import utcnow_iso
    _roots, refs, _w = store.discover()
    running = []
    for ref in refs:
        rd = store.run(ref.id)
        s = run_summary(rd)
        if not s["has"]["live"]:
            continue
        pids = rd.pids() or {}
        ps = rd.pid_states()
        last, budget, elapsed = _watchdog(rd, pids)
        running.append({"id": ref.id, "stage": _s(pids.get("stage")), "nb_pid_alive": bool(ps.get("nb_pid", False)),
                        "kernel_pid_alive": bool(ps.get("kernel_pid", False)), "confirm_pid_alive": bool(ps.get("confirm_pid", False)),
                        "budget_s": budget, "elapsed_s": elapsed, "watchdog_last": last})
    w = [f"driver directory not found: {d}" for d in store.driver_dirs if not os.path.isdir(d)]
    return {"drivers": [driver_state(d) for d in store.driver_dirs if os.path.isdir(d)], "running_runs": running,
            "gpus": gpu_usage(), "server_time": utcnow_iso(), "source": {"run_dir": None, "files": []}, "warnings": warns(None, *w)}


# ----------------------------------------------------------------------------------------------------- files

def resolve_kind(rd: RunData, kind: str) -> str | None:
    rel, _ = FILE_KINDS[kind]
    if "*" in rel:
        base, pattern = rel.rsplit("/", 1)
        d = rd.path(base)
        if d is None or not os.path.isdir(d):
            return None
        import fnmatch
        names = sorted(n for n in os.listdir(d) if fnmatch.fnmatch(n, pattern))
        pref = [n for n in names if "AOT" not in n] or names  # the cross-domain (AOT) sheet only when nothing else exists
        return rd.path(f"{base}/{pref[0]}") if pref else None
    return rd.path(rel)


def read_file(rd: RunData, kind: str) -> tuple[bytes, str]:
    """(bytes, media type) of a registered kind; 404 when absent, 413 when larger than 8 MiB (run_log_tail is always a tail)."""
    if kind not in FILE_KINDS:
        raise ArchiveError(422, "ARCHIVE_BAD_KIND", f"unknown file kind {kind!r}")
    media = FILE_KINDS[kind][1]
    p = resolve_kind(rd, kind)
    if p is None or not os.path.isfile(p):
        raise ArchiveError(404, "ARCHIVE_NOT_FOUND", f"{kind} not available for run {rd.id}")
    if kind == "run_log_tail":
        lines, _ = tail_lines(p, 200, max_chars=2000, strip_ansi=True)
        return ("\n".join(lines) + "\n").encode("utf-8"), media
    if os.path.getsize(p) > MAX_FILE_BYTES:
        raise ArchiveError(413, "ARCHIVE_FILE_TOO_LARGE", f"{kind} exceeds {MAX_FILE_BYTES} bytes")
    with open(p, "rb") as fh:
        return fh.read(MAX_FILE_BYTES + 1), media
