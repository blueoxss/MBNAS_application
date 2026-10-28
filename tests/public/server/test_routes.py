"""All 13 archive endpoints on synthetic runs: shape (contract), values (hand-computed), partial data, errors, filters."""
from __future__ import annotations

import json
import random

import archive_synth as synth
import pytest
from archive_realdata import NPS_DAY, VISDRONE_MINI, VISDRONE_SMOKE, has_run, real_dir, requires_nps, requires_visdrone

from mbnas_server.api.contract_check import contract_errors

RUN = "synthetic-day"
BASE = f"/api/v1/archive/runs/{RUN}"
GET_CASES = [
    ("/api/v1/archive/runs", "ArchiveRunList"), (BASE, "ArchiveRun"), (f"{BASE}/baselines", "ArchiveBaselineList"),
    (f"{BASE}/generations", "ArchiveGenerationList"), (f"{BASE}/generations/1", "ArchiveGenerationDetail"),
    (f"{BASE}/candidates", "ArchiveCandidateList"), (f"{BASE}/candidates/g001_c0005", "ArchiveCandidateDetail"),
    (f"{BASE}/pareto", "ArchivePareto"), (f"{BASE}/fidelity", "ArchiveFidelity"), (f"{BASE}/confirm", "ArchiveConfirm"),
    (f"/api/v1/archive/compare?runs={RUN}", "ArchiveCompare"), ("/api/v1/archive/live", "ArchiveLive"),
]


@pytest.mark.parametrize("path,component", GET_CASES)
def test_json_endpoints_pass_the_contract(client, path, component):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert contract_errors(component, r.json()) == [], contract_errors(component, r.json())[:5]
    assert r.json()["source"]["files"] is not None and isinstance(r.json()["warnings"], list)


def test_files_endpoint_kinds(client):
    for kind in ("plot_pareto_map50_latency", "plot_final_pareto_val", "plot_hypervolume", "plot_params_vs_map50",
                 "plot_rank_correlation", "viz_train"):
        r = client.get(f"{BASE}/files/{kind}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", kind
        assert r.content == synth.png_1x1() and r.headers["cache-control"] == "no-store"
    assert client.get(f"{BASE}/files/viz_val").status_code == 404  # no such file in the synthetic run
    md = client.get(f"{BASE}/files/final_report_md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown") and "synthetic" in md.text
    tail = client.get(f"{BASE}/files/run_log_tail")
    assert tail.status_code == 200 and "NBCONVERT_EXIT=0" in tail.text


def test_run_list_and_summary_values(client, synth_root):
    body = client.get("/api/v1/archive/runs").json()
    assert body["roots"] == [{"path": str(synth_root), "family": "synth", "exists": True}]
    (s,) = body["runs"]
    assert s["id"] == RUN and s["status"] == "done" and s["status_detail"].startswith("NBCONVERT_EXIT=0")
    assert s["scale"] == "day" and s["dataset_id"] == "nps_drones"
    assert s["generations"] == {"planned": 2, "done": 3}
    assert s["candidates"] == {"total": 9, "ok": 6, "failed": 3, "by_status": {"degenerate": 1, "eval_fail": 1, "ok": 6, "oom": 1}}
    assert s["best"] == {"map50_95": 0.40, "small_target_recall": 0.93, "latency_min_ms": 5.0}
    assert s["judge_baseline"]["name"] == "baseline_yolo26n" and s["judge_baseline"]["tier"] == "final@18e"
    assert s["judge_baseline"]["map50_95"] == 0.33
    assert s["verdicts"] == {"notebook": "PASS_WITH_WARNINGS", "transition": None, "confirm": "EXECUTION_FAILED_WITH_EVIDENCE", "smoke_check": None}
    assert s["started_at"] == "2026-09-30T16:00:00Z" and s["ended_at"] == "2026-09-30T16:11:00Z" and s["duration_s"] == 660.0
    assert s["hashes"] == {"protocol_v2_train": "train0001", "metric_protocol": "metric0001", "dataset_manifest": "dsmanifest0001"}
    assert s["arm"] is None and s["has"]["baselines"] and s["has"]["generations"] and s["has"]["confirm"] and not s["has"]["live"]


def test_run_detail(client):
    d = client.get(BASE).json()
    assert [(x["label"], x["duration_s"]) for x in d["stages"]] == [("P0", 10.5), ("P5-1", 600.0), ("P10-1", 49.0)]
    assert [g["name"] for g in d["gates"]] == ["[BOOT] env", "[P8] jitter", "[P9] floor"]  # checklist order kept
    assert d["gates"][1]["measured"] == '{"a": 1}' and d["gates"][1]["hard"] is False and d["gates"][2]["hard"] is True
    assert {r["role"]: (r["candidate_id"], r["metric"], r["value"]) for r in d["roles"]} == {
        "BEST_ACCURACY": ("g001_c0005", "map50_95", 0.40), "FASTEST_VALID": ("g002_c0008", "latency_median_ms", 5.0)}
    assert d["final_val"][0]["candidate_id"] == "g001_c0005" and "cache_context" not in d["final_val"][0]
    assert d["fidelity_summary"] == {"spearman": 0.5, "kendall": 0.3, "n": 5, "snr": 5.0}
    assert d["live"] is None and {p["kind"]: p["exists"] for p in d["plots"]}["viz_val"] is False
    gg = d["gpu_guard"]
    assert gg["records"] == 5 and gg["giveups"] == 1  # the BOOT record without an `action` is not a guard record
    ep = gg["episodes"]
    assert [(e["gpu"], e["outcome"], e["waited_s"], e["external_mib_max"]) for e in ep] == [
        ("1", "proceed", 200.0, 16002.0), ("0", "giveup", 7200.0, 9000.0)]


def test_baselines_keep_tiers_and_anchor(client):
    b = client.get(f"{BASE}/baselines").json()
    names = [x["name"] for x in b["baselines"]]
    assert names == ["baseline_yolo26n", "baseline_yolov11n", "anchor_yolo26n"]
    judge = b["baselines"][0]
    assert judge["is_judge"] and judge["tier"] == "final@18e" and judge["epochs"] == 18 and judge["map50_95"] == 0.33
    assert judge["confirm"]["val_map50_95"] == 0.33 and judge["confirm"]["test_tiles_map50_95"] == 0.31
    assert b["baselines"][1]["confirm"] is None and b["baselines"][1]["head_mode"] is None  # absent -> null, not 0
    anc = b["baselines"][2]
    assert anc["kind"] == "anchor" and anc["tier"] == "T@5e" and anc["epochs"] == 5 and anc["latency_exclusive_ms"] == 7.7
    assert b["anchor"]["candidate_id"] == "g000_c0001" and any("different training budgets" in n for n in b["notes"])


def test_generation_list_values(client):
    body = client.get(f"{BASE}/generations").json()
    ex = synth.EXPECTED
    gens = body["generations"]
    assert [g["generation"] for g in gens] == [0, 1, 2]
    for g in gens:
        assert g["front2d"] == ex["front2d"][g["generation"]], g["generation"]
        assert g["front4d"] == ex["front4d"][g["generation"]], g["generation"]
        assert g["new_fingerprints"] == ex["new_fp"][g["generation"]]
    assert gens[0]["new_fingerprint_ids"] == ["g000_c0001", "g000_c0002", "g000_c0003", "g000_c0004"]
    assert gens[1]["new_fingerprint_ids"] == ["g001_c0005", "g001_c0007"]  # g001_c0006 repeats fingerprint F2
    assert gens[1]["front_added"] == ["g001_c0005", "g001_c0006"] and gens[1]["front_removed"] == []
    assert gens[2]["front_added"] == ["g002_c0008"] and gens[2]["front_removed"] == ["g000_c0002", "g001_c0006"]
    assert gens[0]["lineage_modes"] == {"random_crossover": 2, "seeded_anchor": 2} and gens[1]["lineage_modes"] == {"ga_crossover": 3}
    assert gens[0]["status_counts"] == {"ok": 3, "oom": 1}
    assert gens[1]["delta_vs_prev"]["hypervolume"] == 1.0 and gens[0]["delta_vs_prev"]["hypervolume"] is None
    assert gens[1]["delta_vs_prev"]["best_map50_95"] == pytest.approx(0.05)
    assert gens[2]["delta_vs_prev"]["best_latency"] == pytest.approx(5.0 - 7.0)
    assert gens[1]["delta_vs_baseline"]["best_map50_95"] == pytest.approx(0.40 - 0.33)
    assert gens[1]["delta_vs_baseline"]["baseline_tier"] == "final@18e" and "final-tier" in gens[1]["delta_vs_baseline"]["note"]
    assert gens[1]["delta_vs_baseline"]["best_latency"] == pytest.approx(7.0 - 6.5)
    assert gens[2]["cum_best"] == {"map50_95": 0.40, "small_target_recall": 0.93, "latency_median_ms": 5.0, "params_deploy": 1500.0}
    assert body["hv_history"] == [100.0, 101.0, 102.0] and any("no scalar fitness" in n for n in body["notes"])
    assert [o["key"] for o in body["objectives"]] == ["map50_95", "small_target_recall", "latency_median_ms", "params_deploy"]


def test_generation_characteristics_are_cfg_sums(client):
    gens = client.get(f"{BASE}/generations").json()["generations"]
    c0, c1 = gens[0]["characteristics"], gens[1]["characteristics"]
    assert c0["module_histogram"] == synth.EXPECTED["hist_gen0"] and c0["module_histogram_prev"] == {}
    assert c0["n"] == 3 and c0["n_cfg"] == 2 and c0["n_cfg_missing"] == 1
    assert c1["module_histogram"] == synth.EXPECTED["hist_gen1"] and c1["module_histogram_prev"] == synth.EXPECTED["hist_gen0"]
    assert c1["n"] == 2 and c1["n_cfg"] == 1 and c1["n_prev_cfg"] == 2
    top = {t["module"]: (t["delta"], t["count"], t["count_prev"]) for t in c1["top_modules_delta"]}
    assert top == {"Conv": (-1, 2, 3), "Concat": (-1, 1, 2), "Detect": (-1, 1, 2)}
    assert c0["head_mode"] == {"e2e": 3, "nms": 0}
    assert c0["params_deploy"] == {"min": 2000.0, "med": 2100.0, "max": 3000.0}
    assert c0["depth"]["n_backbone"] == {"min": 3.0, "med": 3.0, "max": 3.0}


def test_generation_detail(client):
    d = client.get(f"{BASE}/generations/1").json()
    assert [c["candidate_id"] for c in d["candidates"]] == ["g001_c0005", "g001_c0006", "g001_c0007"]
    new = {c["candidate_id"]: c["new_fingerprint"] for c in d["candidates"]}
    assert new == {"g001_c0005": True, "g001_c0006": False, "g001_c0007": True}
    imps = {i["objective"]: i for i in d["improvements"]}
    assert set(imps) == {"map50_95", "small_target_recall"} and imps["map50_95"]["candidate_id"] == "g001_c0005"
    assert imps["map50_95"]["prev_best"] == 0.35 and imps["map50_95"]["new_best"] == 0.40 and imps["map50_95"]["delta"] == pytest.approx(0.05)
    assert [p["candidate_id"] for p in d["front2d_points"]] == ["g001_c0005", "g000_c0002", "g001_c0006"]
    assert [p["candidate_id"] for p in d["prev_front2d_points"]] == ["g000_c0002"]
    lin = {x["candidate_id"]: x for x in d["lineage"]}
    assert lin["g001_c0006"]["cache_hit"] == "g000_c0002" and lin["g001_c0005"]["mutation"]["mutation_rate"] == 0.3
    d2 = client.get(f"{BASE}/generations/2").json()
    assert {i["objective"]: (i["prev_best"], i["new_best"], i["delta"]) for i in d2["improvements"]} == {
        "latency_median_ms": (7.0, 5.0, -2.0), "params_deploy": (2000.0, 1500.0, -500.0)}
    d0 = client.get(f"{BASE}/generations/0").json()
    assert d0["prev_front2d_points"] == [] and all(i["kind"] == "initial" and i["delta"] is None for i in d0["improvements"])


def test_candidate_rows_filters_and_paging(client):
    all_ = client.get(f"{BASE}/candidates").json()
    assert all_["total"] == 9 and [c["candidate_id"] for c in all_["candidates"]][:3] == ["g000_c0001", "g000_c0002", "g000_c0003"]
    assert client.get(f"{BASE}/candidates?generation=1").json()["total"] == 3
    assert client.get(f"{BASE}/candidates?status=oom").json()["candidates"][0]["error"] == "CUDA out of memory"
    assert client.get(f"{BASE}/candidates?tier=T@5e&limit=2&offset=1").json()["candidates"][0]["candidate_id"] == "g000_c0002"
    assert client.get(f"{BASE}/candidates?tier=nope").json()["total"] == 0
    for bad in ("limit=0", "limit=99999", "offset=-1", "generation=-3", "generation=x"):
        assert client.get(f"{BASE}/candidates?{bad}").status_code == 422, bad
    rows = {c["candidate_id"]: c for c in all_["candidates"]}
    c5 = rows["g001_c0005"]
    assert c5["on_front2d"] and c5["on_front4d"] and c5["roles"] == ["BEST_ACCURACY"] and c5["is_final"]
    assert c5["delta_vs_baseline"] == {"map50_95": pytest.approx(0.07), "latency_median_ms": pytest.approx(1.0),
                                        "params_deploy": pytest.approx(2500 - 2375031.0), "baseline": "baseline_yolo26n",
                                        "baseline_tier": "final@18e"}
    assert not rows["g000_c0003"]["on_front2d"] and rows["g000_c0003"]["has_cfg"] is False and rows["g000_c0001"]["has_cfg"] is True
    oom = rows["g000_c0004"]
    assert oom["metrics"] == {} and oom["delta_vs_baseline"] == {"map50_95": None, "latency_median_ms": None, "params_deploy": None,
                                                                 "baseline": "baseline_yolo26n", "baseline_tier": "final@18e"}
    assert not oom["on_front2d"] and rows["g002_c0009"]["on_front2d"] is False  # degenerate rows never join a front
    assert all(c["has_checkpoint"] is False for c in rows.values())


def test_candidate_detail(client):
    d = client.get(f"{BASE}/candidates/g001_c0005").json()
    assert d["row"]["candidate_id"] == "g001_c0005" and d["roles"] == ["BEST_ACCURACY"]
    stages = [e["stage"] for e in d["evaluations"]]
    assert stages == ["search", "final", "confirm"] and d["evaluations"][1]["tier"] == "final@18e" and d["evaluations"][1]["metrics"]["map50_95"] == 0.41
    assert "cache_context" not in str(d["evaluations"][1])
    arch = d["architecture"]
    assert arch["source"] == {"kind": "own", "candidate_id": "g001_c0005"} and arch["yaml_text"].startswith("nc: 1")
    assert arch["n_backbone"] == 4 and arch["n_head"] == 3 and arch["stage_count"] == 2
    assert arch["module_histogram"] == {"Conv": 2, "C3k2": 2, "SPPF": 1, "Concat": 1, "Detect": 1}
    assert [s["modules"] for s in arch["stages"]] == [["Conv"], ["Conv", "C3k2", "SPPF"]] and arch["stages_source"] == "derived_from_cfg"
    lin = d["lineage"]
    assert lin["mode"] == "ga_crossover" and [p["candidate_id"] for p in lin["parents"]] == ["g000_c0001", "g000_c0002"]
    assert lin["parents"][0]["kind"] == "candidate" and lin["parents"][0]["has_cfg"] and lin["parents"][0]["generation"] == 0
    diffs = {x["parent"]: x for x in lin["diff_vs_parents"]}
    assert diffs["g000_c0001"]["modules_added"] == ["C3k2", "SPPF"] and diffs["g000_c0001"]["modules_removed"] == []
    assert diffs["g000_c0001"]["stage_diff"] == [{"stage_index": 1, "parent_modules": ["Conv", "C3k2"], "child_modules": ["Conv", "C3k2", "SPPF"]}]
    assert diffs["g000_c0002"]["modules_added"] == ["C3k2", "Conv"] and diffs["g000_c0002"]["modules_removed"] == []
    assert diffs["g000_c0002"]["stage_diff"] == [
        {"stage_index": 0, "parent_modules": ["Conv", "C3k2", "SPPF"], "child_modules": ["Conv"]},
        {"stage_index": 1, "parent_modules": [], "child_modules": ["Conv", "C3k2", "SPPF"]}]
    assert diffs["g000_c0001"]["n_rows_parent"] == 5 and diffs["g000_c0001"]["n_rows_child"] == 7
    assert d["latency"]["median"] == 7.5 and d["latency"]["head_mode"] == "e2e" and d["latency"]["exclusive"] is None
    assert [c["tier"] for c in d["checkpoints"]] == ["T@5e", "final@18e"] and d["checkpoints"][0]["exists"] is None
    assert d["checkpoints"][1]["sha256"] == "sha-final"
    assert d["confirm"]["summary"]["n_success"] == 3 and d["confirm"]["verdicts"]["strict"]["threshold"] is None  # Infinity -> null
    assert d["provenance"] == {"cfg_path": "search/candidates/train_runs/g001_c0005.cfg.json",
                               "yaml_path": "search/candidates/train_runs/g001_c0005.yaml",
                               "jsonl_path": "search/candidates/candidate_evaluations.jsonl"}


def test_candidate_detail_twin_external_parents_and_missing(client):
    twin = client.get(f"{BASE}/candidates/g001_c0006").json()  # cache-hit twin of g000_c0002: no cfg of its own
    assert twin["architecture"]["source"] == {"kind": "fingerprint_twin", "candidate_id": "g000_c0002"}
    assert twin["row"]["has_cfg"] is False and any("same fingerprint" in w for w in twin["warnings"])
    seeded = client.get(f"{BASE}/candidates/g000_c0001").json()
    assert seeded["lineage"]["parents"] == [{"candidate_id": synth.ANCHOR, "kind": "external_config", "fingerprint": None,
                                             "generation": None, "metrics_summary": None, "has_cfg": False}]
    assert seeded["lineage"]["diff_vs_parents"] == []
    nocfg = client.get(f"{BASE}/candidates/g000_c0003").json()
    assert nocfg["architecture"] is None and any("no cfg.json" in w for w in nocfg["warnings"])
    excl = client.get(f"{BASE}/candidates/g000_c0001").json()
    assert any(e["stage"] == "exclusive" for e in excl["evaluations"]) and excl["latency"]["exclusive"]["latency_median_ms"] == 7.7
    failed = client.get(f"{BASE}/candidates/g000_c0004").json()
    assert failed["row"]["status"] == "oom" and failed["row"]["error"] == "CUDA out of memory" and failed["latency"]["median"] is None


def test_pareto_fidelity_confirm(client):
    p = client.get(f"{BASE}/pareto").json()
    assert p["front2d_ids"] == synth.EXPECTED["front2d"][2] and p["front4d_ids"] == synth.EXPECTED["front4d"][2]
    assert p["archive_entries"][0]["candidate_id"] == "g000_c0002" and "genotype" not in p["archive_entries"][0]
    assert p["runtime_archive_ids"] == ["g000_c0002"]
    f = client.get(f"{BASE}/fidelity").json()
    assert "비증거" in f["evidence_note"] and f["rank_correlation"]["n"] == 5 and f["noise"]["snr"] == 5.0
    c = client.get(f"{BASE}/confirm").json()
    assert c["final_verdict"] == "EXECUTION_FAILED_WITH_EVIDENCE" and c["seeds"] == [42, 142, 242] and c["max_det_observed"] == 300
    assert c["test_unlock_used"] is True and "synthetic" in c["final_report_md"] and c["verdicts"]["final_g001_c0005"]["strict"]["threshold"] is None


def test_compare_curves(client, synth_root):
    synth.make_run(synth_root, "synthetic-other", rows=synth.DESIGN[:5])
    body = client.get(f"/api/v1/archive/compare?runs={RUN},synthetic-other").json()
    assert contract_errors("ArchiveCompare", body) == []
    a = body["runs"][0]
    pts = a["curves"]["best_so_far_map50_95"]
    # rows in file order: c1 .30 | c2 .35 | c3 | c4 | c5 .40 | c6 | c7 | c8 | c9 ; each row adds train_time_s 100 (oom/eval_fail rows have none)
    # cumulative: c1 100, c2 200, c3 300, c4 -, c5 400, c6 500, c7 -, c8 600, c9 700
    assert [(p["candidate_id"], p["value"]) for p in pts] == [("g000_c0001", 0.30), ("g000_c0002", 0.35), ("g001_c0005", 0.40), ("g001_c0005", 0.40)]
    assert [p["cum_train_time_s"] for p in pts] == [100.0, 200.0, 400.0, 700.0]  # c4 (oom) and c7 (eval_fail) contribute nothing
    assert a["train_time_rows_missing"] == 2 and a["cache_hit_rows"] == 1
    assert pts[-1]["cum_train_time_s_no_cache"] == 600.0  # the cache-hit twin g001_c0006 is left out
    assert a["curves"]["hypervolume"] == [{"generation": 0, "value": 100.0}, {"generation": 1, "value": 101.0}, {"generation": 2, "value": 102.0}]
    assert [f["candidate_id"] for f in a["finalists"]] == ["g001_c0005"] and body["comparable"] is True


def test_compare_flags_different_datasets(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_x"
    root.mkdir()
    synth.make_run(root, "synthetic-a")
    synth.make_run(root, "synthetic-b")
    import json
    p = root / "synthetic-b/final_summary.json"
    fs = json.loads(p.read_text())
    fs["dataset"] = {"dataset_id": "other_ds", "manifest_hash": "m2"}
    p.write_text(json.dumps(fs))
    c, _ = make_client([root])
    body = c.get("/api/v1/archive/compare?runs=synthetic-a,synthetic-b").json()
    assert body["comparable"] is False and "dataset_id differs" in body["incomparable_reason"]
    assert all(r["comparable"] is False for r in body["runs"])


def test_error_bodies(client):
    cases = [
        (f"/api/v1/archive/runs/nope-run", 404, "ARCHIVE_RUN_NOT_FOUND"),
        (f"{BASE}/generations/99", 404, "ARCHIVE_NOT_FOUND"),
        (f"{BASE}/candidates/g999_c9999", 404, "ARCHIVE_NOT_FOUND"),
        (f"{BASE}/candidates/bad%20id", 422, "ARCHIVE_BAD_ID"),
        (f"{BASE}/generations/-1", 422, "VALIDATION_FAILED"),
        (f"{BASE}/files/not_a_kind", 422, "ARCHIVE_BAD_KIND"),
        ("/api/v1/archive/compare?runs=", 422, "VALIDATION_FAILED"),
        ("/api/v1/archive/compare?runs=a,a", 422, "ARCHIVE_BAD_ID"),
        ("/api/v1/archive/compare?runs=a,b,c,d,e,f,g", 422, "ARCHIVE_BAD_ID"),
        (f"/api/v1/archive/compare?runs={RUN},nope-run", 404, "ARCHIVE_RUN_NOT_FOUND"),
    ]
    for path, status, code in cases:
        r = client.get(path)
        assert r.status_code == status and r.json()["code"] == code, (path, r.status_code, r.text)
        assert contract_errors("Error", r.json()) == []
        assert r.json()["request_id"] == r.headers["x-request-id"]


def test_no_archive_root_is_503(make_client, tmp_path):
    c, _ = make_client([tmp_path / "does-not-exist"])
    for path in ("/api/v1/archive/runs", "/api/v1/archive/runs/x", "/api/v1/archive/live"):
        r = c.get(path)
        assert r.status_code == 503 and r.json()["code"] == "ARCHIVE_ROOT_UNAVAILABLE", path


def test_health_reports_archive_roots(client, synth_root):
    # DD-A59 (a): archive_roots come from the gateway's archive settings; dataset/output roots are the gateway's own
    # (none configured here: empty lists, never the core's). The split stub-core rows: test_health_allowlists_split.py
    h = client.get("/api/v1/health").json()
    assert h["allowlists"]["archive_roots"] == [str(synth_root)]
    assert str(synth_root) not in h["allowlists"]["output_roots"]
    assert h["allowlists"] == {"dataset_roots": [], "output_roots": [], "archive_roots": [str(synth_root)]}


# ================================================================================================ real runs (read-only)
REAL_RUNS = [pytest.param(NPS_DAY, marks=requires_nps), pytest.param(VISDRONE_SMOKE, marks=requires_visdrone),
             pytest.param(VISDRONE_MINI, marks=pytest.mark.skipif(not has_run(VISDRONE_MINI), reason="mini run absent"))]


@pytest.mark.parametrize("run_id", REAL_RUNS)
def test_real_run_all_thirteen_endpoint_shapes_pass_the_contract(real_client, run_id):
    b = f"/api/v1/archive/runs/{run_id}"
    paths = [("/api/v1/archive/runs", "ArchiveRunList"), (b, "ArchiveRun"), (f"{b}/baselines", "ArchiveBaselineList"),
             (f"{b}/generations", "ArchiveGenerationList"), (f"{b}/generations/0", "ArchiveGenerationDetail"),
             (f"{b}/candidates", "ArchiveCandidateList"), (f"{b}/pareto", "ArchivePareto"), (f"{b}/fidelity", "ArchiveFidelity"),
             (f"{b}/confirm", "ArchiveConfirm"), (f"/api/v1/archive/compare?runs={run_id}", "ArchiveCompare"), ("/api/v1/archive/live", "ArchiveLive")]
    first = real_client.get(f"{b}/candidates").json()["candidates"][0]["candidate_id"]
    paths.append((f"{b}/candidates/{first}", "ArchiveCandidateDetail"))
    for path, comp in paths:
        r = real_client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert contract_errors(comp, r.json()) == [], (path, contract_errors(comp, r.json())[:3])
        json.dumps(r.json(), allow_nan=False)
    for kind in ("plot_pareto_map50_latency", "plot_final_pareto_val", "plot_hypervolume", "plot_params_vs_map50", "plot_rank_correlation", "viz_train"):
        r = real_client.get(f"{b}/files/{kind}")
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", (run_id, kind)
    assert real_client.get(f"{b}/files/run_log_tail").status_code == 200
    assert real_client.get(f"{b}/files/final_report_md").status_code in (200, 404)


@requires_nps
def test_real_run_list_roots_and_statuses(real_client):
    body = real_client.get("/api/v1/archive/runs").json()
    assert [(r["family"], r["exists"]) for r in body["roots"]] == [("drone", True), ("visdrone", True)]
    by = {r["id"]: r for r in body["runs"]}
    assert by[NPS_DAY]["status"] == "done" and by[NPS_DAY]["verdicts"]["notebook"] == "PASS_WITH_WARNINGS"
    assert by[NPS_DAY]["verdicts"]["confirm"] == "EXECUTION_FAILED_WITH_EVIDENCE" and by[NPS_DAY]["generations"] == {"planned": 8, "done": 12}
    assert by[NPS_DAY]["candidates"]["total"] == 126 and by[NPS_DAY]["judge_baseline"]["name"] == "baseline_yolo26n"
    assert by[NPS_DAY]["judge_baseline"]["tier"] == "final@18e"
    if VISDRONE_SMOKE in by:
        assert by[VISDRONE_SMOKE]["verdicts"]["smoke_check"] == "SMOKE_PASS" and by[VISDRONE_SMOKE]["dataset_id"] == "visdrone_person"
        assert by[VISDRONE_SMOKE]["verdicts"]["confirm"] == "EFFICIENCY_WINNER_ONLY"
    assert not any(k.startswith("drone26_cpugate") for k in by)  # directories without a run marker are not runs
    failed = [r for r in body["runs"] if r["id"].startswith("FAILED_")]
    assert all(r["status"] == "failed" for r in failed)


@requires_nps
def test_real_candidate_values_equal_the_jsonl_for_30_random_cells(real_client):
    raw = [json.loads(line) for line in (real_dir(NPS_DAY) / "search/candidates/candidate_evaluations.jsonl").read_text().splitlines()]
    api = {c["candidate_id"]: c for c in real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates?limit=5000").json()["candidates"]}
    assert len(api) == len(raw) == 126
    rng = random.Random(20261015)
    keys = sorted({k for r in raw for k in r["metrics"]})
    checked = 0
    for _ in range(30):
        r = rng.choice(raw)
        key = rng.choice(keys)
        got = api[r["candidate_id"]]["metrics"].get(key)
        assert got == r["metrics"].get(key), (r["candidate_id"], key)
        assert api[r["candidate_id"]]["status"] == r["status"] and api[r["candidate_id"]]["generation"] == r["generation"]
        assert api[r["candidate_id"]]["fingerprint"] == r["architecture_fingerprint"] and api[r["candidate_id"]]["tier"] == r["fidelity_tier"]
        assert api[r["candidate_id"]]["hashes"]["config"] == r["config_hash"]
        checked += 1
    assert checked == 30
    missing = [r["candidate_id"] for r in raw if r["metrics"].get("map50_95") is None]
    assert missing and all(api[c]["metrics"].get("map50_95") is None for c in missing)  # never a 0 in place of a missing value


@requires_nps
def test_real_baselines_and_anchor_equal_the_artifacts(real_client):
    d = real_dir(NPS_DAY)
    want = json.loads((d / "protocol/baselines_v2.json").read_text())
    got = {x["name"]: x for x in real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/baselines").json()["baselines"]}
    for name, e in want.items():
        for k in ("map50_95", "map50", "small_target_recall", "latency_median_ms", "params_deploy", "params_train", "peak_mem_mb", "train_time_s"):
            assert got[name][k] == e[k], (name, k)
        assert got[name]["tier"] == "final@18e" and got[name]["kind"] == "baseline"
    anchor = got["anchor_yolo26n"]
    ra = json.loads((d / "search/relative_to_anchor.json").read_text())
    assert anchor["kind"] == "anchor" and anchor["tier"] == "T@5e" and anchor["map50_95"] == ra["anchor_metrics"]["map50_95"]
    excl = json.loads((d / "search/exclusive_latency.json").read_text())[ra["anchor_candidate_id"]]
    assert anchor["latency_exclusive_ms"] == excl["latency_median_ms"] != anchor["latency_median_ms"]
    ts = json.loads((d / "three_seed/three_seed_summary.json").read_text())["summary"]["baseline_yolo26n"]
    assert got["baseline_yolo26n"]["confirm"]["val_map50_95"] == ts["val_map50_95_mean"]
    assert got["baseline_yolo26n"]["confirm"]["test_native_map50_95"] == ts["test_native_map50_95_mean"]


@requires_nps
def test_real_run_detail_equals_the_artifacts(real_client):
    d = real_dir(NPS_DAY)
    body = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}").json()
    cl = json.loads((d / "checklist.json").read_text())
    assert [g["name"] for g in body["gates"]] == list(cl) and len(cl) == 44
    for g in body["gates"]:
        assert g["status"] == cl[g["name"]]["status"] and g["criterion"] == cl[g["name"]]["criterion"]
    fs = json.loads((d / "final_summary.json").read_text())
    assert {r["role"]: r["candidate_id"] for r in body["roles"]} == {k: v["candidate_id"] for k, v in fs["roles"].items()}
    assert [f["candidate_id"] for f in body["final_val"]] == [f["cid"] for f in fs["final_val"]]
    assert all("cache_context" not in f for f in body["final_val"])
    assert [s["label"] for s in body["stages"]][:3] == ["P0", "P1-1", "P1-2"] and body["stages"][-1]["label"] == "P10-1"
    assert body["stages"][0]["started_at"] == "2026-09-30T16:05:24Z" and body["fidelity_summary"]["spearman"] == 0.8095
    assert body["gpu_guard"]["giveups"] == 15 and body["gpu_guard"]["records"] > 1000
    assert body["stop_rule"] is None and body["live"] is None and body["dataset_id"] == "nps_drones"


@requires_nps
def test_real_generation_rows_pareto_and_roles(real_client):
    d = real_dir(NPS_DAY)
    gens = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/generations").json()
    assert len(gens["generations"]) == 12 and gens["hv_history"][-1] == 502.5761
    summ = json.loads((d / "search/generations/gen_005_summary.json").read_text())
    g5 = gens["generations"][5]
    for k in ("phase", "population", "valid", "degenerate", "unique_fingerprints", "archive_size", "hypervolume", "stagnation", "best_map50_95", "mutation"):
        assert g5[k] == summ[k], k
    cand = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates?limit=5000").json()["candidates"]
    roles = {c["candidate_id"]: c["roles"] for c in cand if c["roles"]}
    assert roles["g008_c0138"] == ["BEST_ACCURACY"] and "PARETO_KNEE" in roles["g011_c0182"] and "FASTEST_VALID" in roles["g000_c0007"]
    p = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/pareto").json()
    assert len(p["archive_entries"]) == 41 and set(p["runtime_archive_ids"]) <= {c["candidate_id"] for c in cand}
    on4 = {c["candidate_id"] for c in cand if c["on_front4d"]}
    assert on4 == set(p["front4d_ids"])
    detail = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates/g009_c0143").json()
    assert detail["lineage"]["cache_hit"] == "g003_c0060" and detail["lineage"]["mode"] == "ga_crossover"
    assert detail["architecture"]["source"]["kind"] == "fingerprint_twin"
    conf = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates/g011_c0182").json()
    assert [e["stage"] for e in conf["evaluations"]] == ["search", "final", "exclusive", "confirm"]
    assert [p["kind"] for p in conf["lineage"]["parents"]] == ["candidate", "candidate"] and len(conf["lineage"]["diff_vs_parents"]) == 2
    assert conf["architecture"]["source"]["kind"] == "own" and conf["lineage"]["diff_vs_parents"]


@requires_nps
def test_real_truncated_notes_are_reported_not_guessed(real_client):
    body = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates?status=eval_fail").json()
    assert body["total"] == 3
    cut = [c for c in body["candidates"] if c["lineage"]["notes_parse"] == "truncated"]
    assert cut, "expected the runtime's 500-char cut notes in this run"
    for c in cut:
        assert c["lineage"]["mode"] == "ga_crossover" or c["lineage"]["mode"] is None
        assert c["metrics"].get("map50_95") is None and c["error"]
    detail = real_client.get(f"/api/v1/archive/runs/{NPS_DAY}/candidates/{cut[0]['candidate_id']}").json()
    assert any("cut off" in w for w in detail["warnings"])


@requires_visdrone
def test_real_visdrone_driver_state_and_verdicts(real_client):
    live = real_client.get("/api/v1/archive/live").json()
    assert [d["path"].rsplit("/", 1)[-1] for d in live["drivers"]] == ["driver", "driver_visdrone"]
    vis = [d for d in live["drivers"] if d["path"].endswith("driver_visdrone")][0]
    assert vis["status_json"]["smoke_check_verdict"] == "SMOKE_PASS" and len(vis["pipeline_log_tail"]) == 20
    body = real_client.get(f"/api/v1/archive/runs/{VISDRONE_SMOKE}").json()
    assert body["transition"]["decision"] == "proceed" and body["orphan_check"]["ok"] is True
    assert body["preset"]["dataset_id"] == "visdrone_person"
    assert body["budget"]["s2_ratio"] == json.loads((real_dir(VISDRONE_SMOKE) / "budget.json").read_text())["s2_ratio"]
    assert body["status"] == "done" and body["live"] is None
    cmp_ = real_client.get(f"/api/v1/archive/compare?runs={NPS_DAY},{VISDRONE_SMOKE}").json()
    assert cmp_["comparable"] is False and "dataset_id differs" in cmp_["incomparable_reason"]
