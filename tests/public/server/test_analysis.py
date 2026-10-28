"""Front / delta / histogram / curve formulas, checked by hand-computed cases, a brute-force oracle and the real runs.

The oracle functions below are written independently of ``mbnas_server.archive.analysis`` (plain loops over the raw jsonl
rows and cfg.json files) so a mistake in the implementation cannot hide behind itself."""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest
import archive_synth as synth
from archive_realdata import NPS_DAY, REAL_ROOTS, VISDRONE_SMOKE, real_dir, requires_nps, requires_visdrone

from mbnas_server.archive import analysis as an
from mbnas_server.archive import parsers


def cands_from(rows):
    out, _ = an.normalize_rows(rows)
    return out


def oracle_front(items, senses):
    """items: list of (idx, tuple); sense +1 = maximise, -1 = minimise. Return sorted idx of non-dominated items."""
    def better_or_equal(a, b):
        return all((x >= y) if s > 0 else (x <= y) for x, y, s in zip(a, b, senses))

    def strictly(a, b):
        return any((x > y) if s > 0 else (x < y) for x, y, s in zip(a, b, senses))
    out = []
    for i, a in items:
        if not any(better_or_equal(b, a) and strictly(b, a) for j, b in items if j != i):
            out.append(i)
    return sorted(out)


# ------------------------------------------------------------------------------------------------ dominance

def test_dominates_and_ties():
    assert an.dominates((1, 1), (2, 2)) and an.dominates((1, 2), (2, 2)) and not an.dominates((1, 1), (1, 1))
    assert not an.dominates((1, 3), (2, 2)) and not an.dominates((2, 2), (1, 3))


@pytest.mark.parametrize("seed", range(6))
def test_incremental_fronts_equal_the_brute_force_oracle(seed):
    rng = random.Random(seed)
    rows = []
    for i in range(120):
        g = i // 12
        ok = rng.random() > 0.15
        m = synth.metrics(rng.choice([0.2, 0.3, 0.4, 0.5]) if ok else None, rng.choice([4.0, 5.0, 6.0, 7.0]), rng.choice([0.8, 0.9, 0.95]),
                          rng.choice([1000, 2000, 3000]))
        rows.append(synth.row(f"g{g:03d}_c{i:04d}", g, f"fp{i % 50}", "ok" if ok else "oom", m, {}))
    cands = cands_from(rows)
    gens = sorted({c["generation"] for c in cands})
    series = an.front_series(cands, gens)
    for g in gens:
        upto = [c for c in cands if c["generation"] <= g and c["status"] == "ok"]
        want2 = oracle_front([(c["idx"], (c["metrics"]["map50_95"], c["metrics"]["latency_median_ms"])) for c in upto], (1, -1))
        want4 = oracle_front([(c["idx"], (c["metrics"]["map50_95"], c["metrics"]["small_target_recall"], c["metrics"]["latency_median_ms"],
                                          c["metrics"]["params_deploy"])) for c in upto], (1, 1, -1, -1))
        assert series["2d"][g] == want2 and series["4d"][g] == want4, (seed, g)


def test_failed_and_incomplete_rows_never_enter_a_front():
    rows = [synth.row("g000_c0001", 0, "A", "degenerate", synth.metrics(0.9, 1.0, 0.99, 10), {}),  # status != ok
            synth.row("g000_c0002", 0, "B", "ok", synth.metrics(0.5, 5.0, None, 100), {}),  # no recall: 2D only
            synth.row("g000_c0003", 0, "C", "ok", {"map50_95": 0.4, "latency_median_ms": float("nan")}, {}),  # non-finite latency
            synth.row("g000_c0004", 0, "D", "ok", {**synth.metrics(0.3, 6.0, 0.9, None), "params": 50}, {})]  # params_deploy -> params
    c = cands_from(rows)
    s = an.front_series(c, [0])
    assert [c[i]["candidate_id"] for i in s["2d"][0]] == ["g000_c0002"]
    assert [c[i]["candidate_id"] for i in s["4d"][0]] == ["g000_c0004"]
    assert an.metric(c[3], "params_deploy") == 50.0  # params_deploy is null -> params


def test_params_deploy_falls_back_to_params():
    m = synth.metrics(0.3, 6.0, 0.9, 123)
    del m["params_deploy"]
    (c,) = cands_from([synth.row("g000_c0001", 0, "A", "ok", m, {})])
    assert an.metric(c, "params_deploy") == 123.0
    m2 = {**m, "params_deploy": 77}
    (c2,) = cands_from([synth.row("g000_c0001", 0, "A", "ok", m2, {})])
    assert an.metric(c2, "params_deploy") == 77.0


def test_generation_without_rows_repeats_the_previous_front():
    cands = cands_from(synth.DESIGN)
    s = an.front_series(cands, [0, 1, 2, 3])
    assert s["2d"][3] == s["2d"][2] and s["4d"][3] == s["4d"][2]


# ------------------------------------------------------------------------------------------------ per generation facts

def test_new_fingerprints_improvements_and_lineage_on_the_design():
    c = cands_from(synth.DESIGN)
    nf = an.new_fingerprints(c)
    assert {g: v[0] for g, v in nf.items()} == synth.EXPECTED["new_fp"]
    assert nf[1][1] == ["g001_c0005", "g001_c0007"]
    assert an.lineage_modes([x for x in c if x["generation"] == 0]) == {"random_crossover": 2, "seeded_anchor": 2}
    imp = {i["objective"]: i for i in an.improvements(c, 1)}
    assert imp["map50_95"]["delta"] == pytest.approx(0.05) and "latency_median_ms" not in imp and "params_deploy" not in imp
    assert an.cumulative_best(c, 1)["latency_median_ms"] == 7.0 and an.cumulative_best(c, 2)["latency_median_ms"] == 5.0
    assert an.cumulative_best([], 0) == {"map50_95": None, "small_target_recall": None, "latency_median_ms": None, "params_deploy": None}


def test_best_of_prefers_the_first_on_ties_and_ignores_failures():
    c = cands_from(synth.DESIGN)
    assert an.best_of([x for x in c if x["generation"] == 1], "latency_median_ms") == (7.0, "g001_c0006")
    assert an.best_of([x for x in c if x["status"] != "ok"], "map50_95") is None


# ------------------------------------------------------------------------------------------------ architectures

def test_derive_stages_and_histogram():
    cfg = {"backbone": [[-1, 1, "Conv", [64, 3, 2]], [-1, 1, "Conv", [64, 3, 1]], [-1, 2, "C3k2", [128]], [-1, 1, "nn.MaxPool2d", [2]],
                        [-1, 1, "Conv", [128, 3, 2]], [-1, 1, "SCDown", [256, 3, 2]], [-1, 1, "SPPF", [256, 5]]],
           "head": [[-1, 1, "nn.Upsample", ["None", 2, "nearest"]], [[-1, 2], 1, "Concat", [1]], [[3], 1, "Detect", ["nc"]]]}
    st = an.derive_stages(cfg)
    assert [s["modules"] for s in st] == [["Conv", "Conv", "C3k2"], ["nn.MaxPool2d"], ["Conv"], ["SCDown", "SPPF"]]
    assert [s["global_start"] for s in st] == [0, 3, 4, 5] and [s["downsamples"] for s in st] == [1, 1, 1, 1]
    assert an.module_histogram([cfg, cfg])["Conv"] == 6 and an.module_names(cfg).count("Concat") == 1  # repeats (the "2" of C3k2) are not expanded
    assert an.module_names({"backbone": "bad", "head": [[1, 2]]}) == [] and an.derive_stages(None) == []


def test_parent_diff_counts_and_stage_differences():
    a = synth.make_cfg(["Conv", "C3k2"], ["Concat", "Detect"])
    b = synth.make_cfg(["Conv", "C3k2", "C3k2", "SPPF"], ["Detect"])
    d = an.parent_diff("p", a, b)
    assert d["modules_added"] == ["C3k2", "SPPF"] and d["modules_removed"] == ["Concat"]
    assert d["module_delta"] == {"C3k2": 1, "Concat": -1, "SPPF": 1} and (d["n_rows_parent"], d["n_rows_child"]) == (4, 5)
    assert d["stage_diff"] == [{"stage_index": 0, "parent_modules": ["Conv", "C3k2"], "child_modules": ["Conv", "C3k2", "C3k2", "SPPF"]}]


def test_characteristics_top_delta_is_by_absolute_count_change():
    cfgs = {"a": synth.make_cfg(["Conv", "C3k2"], ["Detect"]), "b": synth.make_cfg(["Conv", "SPPF", "SPPF"], ["Detect"]), "c": None}
    cur = cands_from([synth.row("g001_a", 1, "x", "ok", synth.metrics(0.3, 5.0, 0.9, 10), {}),
                      synth.row("g001_c", 1, "y", "ok", synth.metrics(0.3, 6.0, 0.9, 20), {})])
    prev = cands_from([synth.row("g000_b", 0, "z", "ok", synth.metrics(0.3, 5.0, 0.9, 30), {})])
    ch = an.characteristics(cur, prev, lambda cid: cfgs[cid.split("_")[1]])
    assert ch["n"] == 2 and ch["n_cfg"] == 1 and ch["n_cfg_missing"] == 1 and ch["n_prev_cfg"] == 1
    assert [(t["module"], t["delta"]) for t in ch["top_modules_delta"]] == [("SPPF", -2), ("C3k2", 1)]
    assert ch["top_modules_delta"][0]["delta_per_candidate"] == -2.0
    assert ch["head_mode"] == {"e2e": 2, "nms": 0}
    assert an.characteristics([], [], lambda c: None)["depth"]["n_backbone"] == {"min": None, "med": None, "max": None}


# ------------------------------------------------------------------------------------------------ curves and gpu guard

def test_best_so_far_curve_skips_missing_times_without_adding_zero():
    rows = [synth.row("g000_c0001", 0, "A", "ok", synth.metrics(0.3, 5.0, 0.9, 10, train_time_s=50.0), {}),
            synth.row("g000_c0002", 0, "B", "oom", {}, {}),
            synth.row("g000_c0003", 0, "C", "ok", synth.metrics(0.25, 5.0, 0.9, 10, train_time_s=70.0), {}),
            synth.row("g001_c0004", 1, "D", "ok", {k: v for k, v in synth.metrics(0.5, 5.0, 0.9, 10).items() if k != "train_time_s"}, {})]
    pts, skipped, hits = an.best_so_far_curve(cands_from(rows))
    assert skipped == 2 and hits == 0
    assert [(p["candidate_id"], p["cum_train_time_s"], p["value"]) for p in pts] == [
        ("g000_c0001", 50.0, 0.3), ("g001_c0004", 120.0, 0.5)]  # the last row has no time: it adds nothing, but it improves the best
    assert an.best_so_far_curve([]) == ([], 0, 0)


def test_gpu_guard_open_episode_and_unsorted_input():
    recs = [{"timestamp": 30, "gpu": "0", "action": "wait", "external_mib": 5, "waited_s": 20},
            {"timestamp": 10, "gpu": "0", "action": "wait", "external_mib": 9, "waited_s": 0},
            {"timestamp": 5, "gpu": "0", "phase": "BOOT"}, "junk"]
    out = an.gpu_guard_summary(recs)
    assert out["records"] == 2 and out["giveups"] == 0
    (ep,) = out["episodes"]
    assert (ep["gpu"], ep["outcome"], ep["waited_s"], ep["external_mib_max"]) == ("0", "open", 20.0, 9.0)
    assert ep["start"] == "1970-01-01T00:00:10Z" and ep["end"] == "1970-01-01T00:00:30Z"


def test_notebook_stage_parsing():
    nb = synth.notebook([("P0", "2026-09-30T16:00:00.250000Z", "2026-09-30T16:00:10.500000Z"), ("P7-1", "bad", "2026-10-01T11:19:35Z")])
    nb["cells"].insert(1, {"cell_type": "code", "metadata": {}, "source": ["print('no label')"]})
    assert parsers.notebook_stages(nb) == [
        {"label": "P0", "started_at": "2026-09-30T16:00:00Z", "ended_at": "2026-09-30T16:00:10Z", "duration_s": 10.25},
        {"label": "P7-1", "started_at": None, "ended_at": "2026-10-01T11:19:35Z", "duration_s": None}]
    assert parsers.notebook_span(nb) == ("2026-09-30T16:00:00Z", "2026-10-01T11:19:35Z")
    assert parsers.notebook_stages({}) == []


# ------------------------------------------------------------------------------------------------ real runs vs independent recomputation

def raw_rows(run_dir: Path):
    return [json.loads(line) for line in (run_dir / "search/candidates/candidate_evaluations.jsonl").read_text().splitlines() if line.strip()]


def real_store(run_id):
    from mbnas_server.archive import ArchiveStore
    return ArchiveStore(REAL_ROOTS).run(run_id)


def oracle_cfg_hist(run_dir: Path, rows):
    h = Counter()
    for r in rows:
        if r["status"] != "ok":
            continue
        p = run_dir / "search/candidates/train_runs" / f"{r['candidate_id']}.cfg.json"
        if not p.is_file():
            continue
        cfg = json.loads(p.read_text())
        h.update(row[2] for row in cfg["backbone"] + cfg["head"])
    return dict(h)


@pytest.mark.parametrize("run_id", [pytest.param(NPS_DAY, marks=requires_nps), pytest.param(VISDRONE_SMOKE, marks=requires_visdrone)])
def test_real_run_fronts_histograms_and_new_fingerprints_match_the_oracle(run_id):
    from mbnas_server.archive import views
    d = real_dir(run_id)
    raw = raw_rows(d)
    rd = real_store(run_id)
    items = views.generation_items(rd)
    gens = sorted({r["generation"] for r in raw})
    assert [i["generation"] for i in items] == gens
    seen_fp: set = set()
    for it in items:
        g = it["generation"]
        upto = [r for r in raw if r["generation"] <= g and r["status"] == "ok"]
        pts2 = [(k, (r["metrics"]["map50_95"], r["metrics"]["latency_median_ms"])) for k, r in enumerate(upto)
                if r["metrics"].get("map50_95") is not None and r["metrics"].get("latency_median_ms") is not None]
        want2 = {upto[k]["candidate_id"] for k in oracle_front(pts2, (1, -1))}
        assert set(it["front2d"]) == want2, (run_id, g)
        pts4 = []
        for k, r in enumerate(upto):
            m = r["metrics"]
            pd = m.get("params_deploy", m.get("params"))
            if None not in (m.get("map50_95"), m.get("small_target_recall"), m.get("latency_median_ms"), pd):
                pts4.append((k, (m["map50_95"], m["small_target_recall"], m["latency_median_ms"], pd)))
        assert set(it["front4d"]) == {upto[k]["candidate_id"] for k in oracle_front(pts4, (1, 1, -1, -1))}, (run_id, g)
        here = [r for r in raw if r["generation"] == g]
        fps = {r["architecture_fingerprint"] for r in here if r["architecture_fingerprint"]}
        assert it["new_fingerprints"] == len(fps - seen_fp), (run_id, g)
        seen_fp |= fps
        want_modes = Counter(json.loads(r["notes"]).get("mode", "unknown") if r["notes"].endswith("}") else None for r in here)
        got_modes = it["lineage_modes"]
        if None not in want_modes:
            assert got_modes == dict(want_modes)
        ok_here = [r for r in here if r["status"] == "ok"]
        assert it["characteristics"]["module_histogram"] == dict(sorted(oracle_cfg_hist(d, ok_here).items(), key=lambda kv: (-kv[1], kv[0])))
        assert it["characteristics"]["n"] == len(ok_here) and it["status_counts"] == dict(sorted(Counter(r["status"] for r in here).items()))


@requires_nps
def test_nps_summary_best_values_equal_the_rows_and_hv_is_copied():
    from mbnas_server.archive import views
    d = real_dir(NPS_DAY)
    raw = raw_rows(d)
    rd = real_store(NPS_DAY)
    for it in views.generation_items(rd):
        g = it["generation"]
        s = json.loads((d / f"search/generations/gen_{g:03d}_summary.json").read_text())
        ok = [r["metrics"] for r in raw if r["generation"] == g and r["status"] == "ok"]
        assert it["best_map50_95"] == s["best_map50_95"] == max(m["map50_95"] for m in ok)  # the runtime's own value, not re-derived
        assert it["hypervolume"] == s["hypervolume"] and it["stagnation"] == s["stagnation"] and it["hv_improved"] == s["hv_improved"]
    assert views.hv_history(rd) == json.loads((d / "search/archive.json").read_text())["hv_history_raw"]


@requires_nps
def test_derived_stages_agree_with_the_runtime_genotype_stages():
    d = real_dir(NPS_DAY)
    ar = json.loads((d / "search/archive.json").read_text())["archive"]
    checked = 0
    for e in ar:
        p = d / "search/candidates/train_runs" / f"{e['candidate_id']}.cfg.json"
        if not p.is_file():
            continue
        want = [{k: s[k] for k in ("stage_index", "global_start", "modules", "downsamples")} for s in e["genotype"]["stages"]]
        assert an.derive_stages(json.loads(p.read_text())) == want, e["candidate_id"]
        checked += 1
    assert checked >= 30


@requires_nps
def test_nps_running_best_curve_and_cache_hits():
    d = real_dir(NPS_DAY)
    raw = raw_rows(d)
    rd = real_store(NPS_DAY)
    pts, skipped, hits = an.best_so_far_curve(rd.cands)
    assert skipped == sum(1 for r in raw if r["metrics"].get("train_time_s") is None) and hits == 17
    cum = sum(r["metrics"]["train_time_s"] for r in raw if r["metrics"].get("train_time_s") is not None)
    assert pts[-1]["cum_train_time_s"] == pytest.approx(cum, abs=0.01) and pts[-1]["value"] == pytest.approx(0.505054425635025)
    assert all(b["value"] > a["value"] for a, b in zip(pts[:-2], pts[1:-1]))


def test_gpu_guard_episode_fields_stay_null_when_the_log_has_no_value():
    out = an.gpu_guard_summary([{"timestamp": 100, "gpu": "1", "action": "gpu_busy_giveup"}])
    (ep,) = out["episodes"]
    assert out["giveups"] == 1 and ep["outcome"] == "giveup" and ep["waited_s"] is None and ep["external_mib_max"] is None
