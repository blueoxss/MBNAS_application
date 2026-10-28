"""WP-S7 / DD-39: the read-only 12-search campaign adapter (``archive/campaign.py``).

* synthetic campaign root (two arms as the task describes: a completed one with arm_result.json + pareto, a running one with a partial
  ledger) plus variants (blocked, interrupted, resumed, dead pid, garbage files, symlink escape, parent directory of several roots);
* run-id round trip through the API (``GET /archive/runs/{id}``), every endpoint validated against the contract (``contract_errors``);
* tolerance of unreadable / missing files, mtime-keyed caching, only-a-handful-of-files reads, nothing under the campaign root is written;
* a read-only smoke against the REAL running campaign (skipped when absent): list, open every arm, no exception, no write-mode open.

All numbers in the synthetic root are invented test data. The real campaign is only read.
"""
from __future__ import annotations

import builtins
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import archive_synth as synth
import pytest

from mbnas_server.api.contract_check import contract_errors
from mbnas_server.archive import ArchiveStore, views
from mbnas_server.archive import campaign as cp
from mbnas_server.config import GatewaySettings as Settings

REAL_CROOT = Path(os.environ.get("MBNAS_TEST_ARCHIVE_RUNS_ROOT") or f"{os.environ.get('MBNAS_TEST_ROOT', 'runtime/test_root')}/archive_runs") \
    / "visdrone_12search" / "vd12_20261021_2240"
requires_real_croot = pytest.mark.skipif(not (REAL_CROOT / "campaign.json").is_file(), reason="real campaign root not present on this host",
                                         host_data=True)
# Cold-list budget of the real-campaign smoke (12 arms, ~200 rows / ~20 generation summaries each) on an idle host. The host
# also runs the campaign, so the budget is load-aware (EVAL_1 D7): it grows with the 1-minute load average per logical CPU.
# The only loaded measurement so far took 2992 ms at load 22-25 on 48 CPUs (~2x the idle budget at ~0.5 load per CPU), hence
# the slope 3 (budget 3562-3844 ms at that load: 19-28 % above that run); on an idle host the budget is the original 1500 ms.
COLD_LIST_MS, LOAD_SLOPE = 1500.0, 3.0
CID = "vdtest_20261023_0001"
T0 = 1_790_000_000.0
API = "/api/v1/archive"


def load_aware_budget_ms(idle_ms: float) -> tuple[float, dict[str, float]]:
    """``idle_ms * (1 + LOAD_SLOPE * load1 / cpus)`` and the load it was computed from (the idle budget without getloadavg)."""
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        load1 = 0.0
    cpus = os.cpu_count() or 1
    return idle_ms * (1.0 + LOAD_SLOPE * load1 / cpus), {"load1": round(load1, 2), "cpus": cpus}


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def rid(space: str, method: str, seed: int = 42, cid: str = CID) -> str:
    return cp.run_id_of(cid, space, method, f"seed_{seed}")


# ------------------------------------------------------------------------------------------------ synthetic campaign
def ledger_rows(rows: list[dict[str, Any]], attempt: int = 1) -> list[dict[str, Any]]:
    out = [{"cid": None, "gen": None, "phase": "init", "kind": "init", "attempt": attempt, "t_start": T0, "t_end": T0 + 5, "status": "ok",
            "cache_hit": False}]
    t = T0 + 10
    for r in rows:
        hit = json.loads(r["notes"]).get("cache_hit") if isinstance(r["notes"], str) else None
        out.append({"cid": r["candidate_id"], "gen": r["generation"], "phase": "search", "kind": "cand", "attempt": attempt, "t_start": t,
                    "t_end": t + 60, "status": r["status"], "cache_hit": bool(hit), "train_time_s": 50.0})
        t += 60
    return out


def make_arm(croot: Path, space: str, method: str, seed: int = 42, *, result: dict[str, Any] | str | None = None,
             manifest: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None, gens: int = 3, ledger: bool = True,
             torn_ledger: bool = False, pareto: bool = True) -> Path:
    arm = croot / "arms" / space / method / f"seed_{seed}"
    rows = list(synth.DESIGN if rows is None else rows)
    synth.write_json(arm / "arm_manifest.json", manifest if manifest is not None else {
        "arm": f"{space}/{method}/seed_{seed}", "space": space, "method": method, "seed": seed, "gpu": 0, "attempt": 1, "resume_count": 0,
        "start_epoch": T0 - 20, "t_start": time.time() - 5, "pid": os.getpid(), "stage": "search"})  # t_start: the pid must not look reused
    if isinstance(result, str):  # raw (possibly broken) text
        (arm / "arm_result.json").write_text(result, encoding="utf-8")
    elif result is not None:
        synth.write_json(arm / "arm_result.json", result)
    synth.write_jsonl(arm / "search/candidates/candidate_evaluations.jsonl", rows)
    synth.write_cfgs(arm, synth.CFG_MODULES)
    all_gens = sorted({r["generation"] for r in rows})[:gens]
    for i, g in enumerate(all_gens):
        synth.write_json(arm / f"search/generations/gen_{g:03d}_summary.json",
                         synth._gen_summary(g, [r for r in rows if r["generation"] == g], 100.0 + i, i % 2))
    if pareto:
        (arm / "search/pareto").mkdir(parents=True, exist_ok=True)
        (arm / "search/pareto/pareto_archive.csv").write_text(
            "candidate_id,fingerprint,generation,map50_95,latency_median_ms,small_target_recall,params_deploy\n"
            "g001_c0005,F5,1,0.4,7.5,0.93,2500\ng002_c0008,F8,2,0.38,5.0,0.91,1500\n", encoding="utf-8")
        synth.write_json(arm / "search/pareto/objective_champions.json", [
            {"objective": "map50_95", "candidate_id": "g001_c0005", "fingerprint": "F5", "value": 0.4},
            {"objective": "small_target_recall", "candidate_id": "g001_c0005", "fingerprint": "F5", "value": 0.93},
            {"objective": "latency_median_ms", "candidate_id": "g002_c0008", "fingerprint": "F8", "value": 5.0},
            {"objective": "params", "candidate_id": "g002_c0008", "fingerprint": "F8", "value": 1500}])
    if ledger:
        led = ledger_rows(rows)
        tail = '{"cid": "g099_c9999", "gen": 9, "kind": "ca' if torn_ledger else ""
        synth.write_jsonl(arm / "ledger/eval_timing.jsonl", led, tail)
        synth.write_json(arm / "ledger/cost_ledger.json", {"search_s": 61200.0, "total_s": 86400.0, "cost_s": 300.0, "cost_search_s": 280.0})
    (arm / "driver").mkdir(parents=True, exist_ok=True)
    (arm / "driver/watchdog.log").write_text("2026-10-23T05:00:00 [pid 1] watch: elapsed=12.0 s budget_s=86400.0\n", encoding="utf-8")
    return arm


TERMINAL = [{"cid": "g001_c0005", "fp": "F5", "params_deploy": 2500.0, "params_train": 2600.0, "val_map50_95": 0.41, "val_map50": 0.9,
             "small_recall": 0.94, "latency_v2": 7.4, "head_mode": "e2e", "best_ckpt": "/nonexistent/final/best.pt", "checkpoint_sha256": "abc123",
             "forward_only_median_ms": 6.9, "t_post_ms": 0.5, "peak_mem_mb": None}]
COMPLETED = {"status": "ARM_COMPLETED", "arm": "S2/ga/seed_42", "attempt": 1, "n_eval": 9, "generations": 3, "stop_reason": "budget_search",
             "final_stop_reason": "reserve_exhausted", "cost_s": 4000.0, "terminal": TERMINAL, "terminal_count": 1, "best_terminal": TERMINAL[0],
             "flags": ["EXTERNAL_GPU"]}


def make_campaign(base: Path, name: str = CID, *, with_prep: bool = True) -> Path:
    croot = base / name
    croot.mkdir(parents=True, exist_ok=True)
    prep = croot / "prep/yolo_ga_full_visdrone/prep_test"
    synth.write_json(croot / "campaign.json", {
        "schema": "visdrone_12search/campaign/v2", "campaign_id": name,
        "contract": {"config": {"preset": {"name": "day", "final_epochs": 18, "pop": 10, "phase_gens": 12},
                                "budget": {"arm_s": 86400, "search_s": 61200, "final_reserve_s": 25200}}},
        "runtime_metadata": {"paths": {"prep_run_dir": str(prep)}}})
    synth.write_json(croot / "queue.json", {
        "schema": "visdrone_12search/queue/v1", "n_pairs": 2, "pair_done": [0],
        "arms": [{"k": 0, "arm": "S2/ga/seed_42", "state": "done", "t_end": T0 + 4000.0, "status": "ARM_COMPLETED"},
                 {"k": 0, "arm": "S2/random/seed_42", "state": "done", "status": "BLOCKED_CONTRACT", "t_end": T0 + 4100.0},
                 {"k": 1, "arm": "S1/ga/seed_42", "state": "running"},
                 {"k": 1, "arm": "S1/random/seed_42", "state": "queued"},
                 {"k": 2, "arm": "S2/ga/seed_2024", "state": "queued"}]})
    if with_prep:
        synth.write_json(prep / "prep_manifest.json", {
            "DATASET_MANIFEST_HASH": "dsmanifest0001", "protocol_v2_train_hash": "train0001",
            "BASELINES": {"baseline_yolo26n": {"status": "ok", "map50_95": 0.33, "map50": 0.9, "small_target_recall": 0.9, "latency_median_ms": 6.5,
                                               "params_deploy": 2375031.0, "params_train": 2504190.0, "best_ckpt": "/nonexistent/b/best.pt",
                                               "checkpoint_sha256": "abc", "cache_context": {"x": "y" * 50}},
                          "baseline_yolov11n": {"status": "ok", "map50_95": 0.34, "latency_median_ms": 7.0, "params_deploy": 2582347.0}}})
        synth.write_json(prep / "preset.json", {"dataset_id": "synthetic_visdrone", "preset": {"gens": 8}})
        synth.write_json(prep / "protocol/protocol_v2.json", {"eval": {"split": "val"}, "latency": {"device": "cuda:0", "fp16": True}})
    make_arm(croot, "S2", "ga", result=COMPLETED)
    make_arm(croot, "S2", "random", result={"status": "BLOCKED_CONTRACT", "arm": "S2/random/seed_42", "attempt": 1}, rows=synth.DESIGN[:5])
    make_arm(croot, "S1", "ga", rows=synth.DESIGN[:7], gens=1, torn_ledger=True)  # running: no arm_result.json, partial ledger, live pid
    return croot


@pytest.fixture
def croot(tmp_path: Path) -> Path:
    return make_campaign(tmp_path / "visdrone_12search")


@pytest.fixture
def make_campaign_client(tmp_roots):
    """Factory: ``f(campaign_roots, roots=(), ttl=5.0, **settings)`` -> (TestClient, app, store); ``settings`` override
    ``GatewaySettings`` fields (e.g. ``rate_limit_enabled=False``, as ``make_client`` of test_perf.py). The app is created
    with the new setting and its archive store is rebuilt exactly as ``api/app.py`` is asked to (request R1 of WP-S7): ``ArchiveStore(..., campaign_roots=settings.archive_campaign_roots)``."""
    from conftest_common import owner_harness  # DD-A43(b): owner-only harness; skipped without it
    from fastapi.testclient import TestClient

    harness = owner_harness()

    from mbnas_server.api.app import create_app

    opened = []

    def factory(campaign_roots, roots=(), ttl=5.0, **overrides):
        settings = harness.make_gateway_settings(tmp_roots, archive_roots=[str(r) for r in roots], archive_driver_dirs=[],
                                                 archive_campaign_roots=[str(r) for r in campaign_roots], archive_cache_ttl_s=ttl,
                                                 **overrides)
        app = create_app(settings)
        app.state.archive = ArchiveStore(settings.archive_roots, settings.archive_driver_dirs, settings.archive_cache_ttl_s,
                                         campaign_roots=settings.archive_campaign_roots)
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        c.headers.update(harness.auth())
        return c, app, app.state.archive

    yield factory
    for c in opened:
        c.__exit__(None, None, None)


def runs_by_id(client) -> dict[str, dict[str, Any]]:
    r = client.get(f"{API}/runs")
    assert r.status_code == 200, r.text
    assert contract_errors("ArchiveRunList", r.json()) == [], contract_errors("ArchiveRunList", r.json())[:5]
    return {x["id"]: x for x in r.json()["runs"]}


# ------------------------------------------------------------------------------------------------ discovery and run ids
def test_run_id_format_is_url_safe_and_unambiguous(croot):
    assert rid("S2", "ga") == "vdtest_20261023_0001__S2__ga__seed_42"
    from mbnas_server.archive import analysis as an
    assert an.valid_id(rid("S2", "ga"))
    store = ArchiveStore([], campaign_roots=[str(croot)])
    roots, refs, warns = store.discover()
    assert warns == []
    assert sorted(r.id for r in refs) == sorted(rid(s, m) for s, m in (("S2", "ga"), ("S2", "random"), ("S1", "ga")))
    assert all(r.family == "visdrone-campaign" and r.campaign is not None for r in refs)
    assert [(r.family, r.exists, r.kind) for r in roots] == [("visdrone-campaign", True, "campaign")]


def test_list_reports_campaign_root_kind_and_grouping_fields(make_campaign_client, croot):
    c, _, _ = make_campaign_client([croot])
    body = c.get(f"{API}/runs").json()
    assert body["roots"] == [{"path": str(croot), "family": "visdrone-campaign", "exists": True, "kind": "campaign"}]
    runs = runs_by_id(c)
    assert set(runs) == {rid("S2", "ga"), rid("S2", "random"), rid("S1", "ga")}
    for r in runs.values():
        assert r["family"] == "visdrone-campaign" and r["campaign_id"] == CID
        assert r["campaign_arm"] == f"{r['space']}/{r['method']}/seed_42" and r["seed"] == 42
        assert r["arm"] == {"space": r["space"], "method": r["method"], "seed": 42}
        assert r["campaign_arms_total"] == 5  # queue.json lists 5 arms, 3 have a directory yet
        assert r["dataset_id"] == "synthetic_visdrone" and r["scale"] == "day"
    assert runs[rid("S2", "ga")]["pair"] == 0 and runs[rid("S2", "ga")]["pair_done"] is True
    assert runs[rid("S1", "ga")]["pair"] == 1 and runs[rid("S1", "ga")]["pair_done"] is False
    assert runs[rid("S1", "ga")]["queue_state"] == "running" and runs[rid("S2", "ga")]["queue_state"] == "done"


def test_completed_arm_summary(make_campaign_client, croot):
    c, _, _ = make_campaign_client([croot])
    s = runs_by_id(c)[rid("S2", "ga")]
    assert s["status"] == "done" and s["status_detail"] == "ARM_COMPLETED; stop budget_search / reserve_exhausted; 9 evaluations"
    assert s["arm_status"] == "ARM_COMPLETED" and s["has"]["live"] is False
    # a finished arm: "planned" = the generations the arm reports in arm_result.json (as a plain run takes it from final_summary.json);
    # while it runs the number of generations is budget-driven and unknown, so it stays null (see the running-arm test)
    assert s["generations"] == {"planned": 3, "done": 3}
    assert s["candidates"]["total"] == 9 and s["candidates"]["ok"] == 6
    assert s["best"] == {"map50_95": 0.40, "small_target_recall": 0.93, "latency_min_ms": 5.0}
    assert s["judge_baseline"]["name"] == "baseline_yolo26n" and s["judge_baseline"]["map50_95"] == 0.33 and s["judge_baseline"]["tier"] == "final@18e"
    assert s["hashes"]["dataset_manifest"] == "dsmanifest0001" and s["hashes"]["protocol_v2_train"] == "train0001"
    assert s["started_at"] == iso(T0) and s["ended_at"] == iso(T0 + 4000.0)  # ledger first t_start / queue.json t_end
    assert s["duration_s"] == 4000.0
    assert s["result"]["best_terminal"]["candidate_id"] == "g001_c0005" and s["result"]["best_terminal"]["val_map50_95"] == 0.41
    assert s["result"]["stop_reason"] == "budget_search" and s["result"]["flags"] == ["EXTERNAL_GPU"]
    assert s["progress"]["generations_seen"] == 3 and s["progress"]["candidates"]["total"] == 9
    assert s["progress"]["cost_s"] == 4000.0 and s["progress"]["search_budget_s"] == 61200.0


def test_running_arm_progress_from_partial_ledger(make_campaign_client, croot):
    c, _, _ = make_campaign_client([croot])
    s = runs_by_id(c)[rid("S1", "ga")]
    assert s["status"] == "running" and s["status_detail"].startswith(f"arm process {os.getpid()} alive")
    assert s["has"]["live"] is True and s["ended_at"] is None and s["arm_status"] is None and "result" not in s
    p = s["progress"]
    rows = synth.DESIGN[:7]  # g000 x4 (3 ok, 1 oom), g001 x3 (2 ok, 1 eval_fail): ok 5, oom 1, eval_fail 1
    assert p["generations_seen"] == 2 and p["last_generation"] == 1
    assert p["candidates"] == {"total": 7, "ok": 5, "eval_fail": 1, "degenerate": 0, "cache_hit": 1,
                               "by_status": {"eval_fail": 1, "ok": 5, "oom": 1}}
    last_end = T0 + 10 + 60 * (len(rows) - 1) + 60
    assert p["last_eval_at"] == iso(last_end)
    assert p["ledger_rows"] == len(rows) + 1  # + the init row; the torn last line is not a row
    assert s["generations"]["done"] == 1 and s["generations"]["planned"] is None  # only gen 0 has a summary; gen 1 has rows but is still running
    assert s["started_at"] == iso(T0)
    detail = c.get(f"{API}/runs/{rid('S1', 'ga')}")
    assert detail.status_code == 200
    assert any("ledger/eval_timing.jsonl: 1 unparsable line" in w for w in detail.json()["warnings"])  # the torn last line is reported, not fatal
    live = c.get(f"{API}/live").json()
    assert [r["id"] for r in live["running_runs"]] == [rid("S1", "ga")] and live["running_runs"][0]["nb_pid_alive"] is True


def test_blocked_interrupted_resumed_dead_and_unknown_statuses(make_campaign_client, tmp_path):
    base = tmp_path / "vd"
    root = make_campaign(base, "vdstat_1")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    make_arm(root, "S1", "random", result={"status": "ARM_INTERRUPTED", "attempt": 1}, rows=synth.DESIGN[:4])
    make_arm(root, "S1", "ga", seed=2024, result={"status": "ARM_INTERRUPTED", "attempt": 1}, rows=synth.DESIGN[:4],
             manifest={"attempt": 2, "t_start": time.time() - 5, "pid": os.getpid(), "stage": "preflight"})  # resumed: manifest attempt > result attempt
    make_arm(root, "S2", "ga", seed=2024, rows=synth.DESIGN[:4], manifest={"attempt": 1, "t_start": T0 - 1, "pid": dead.pid})  # no result, dead pid
    make_arm(root, "S2", "random", seed=2024, rows=synth.DESIGN[:4], manifest={"attempt": 1})  # no result, no pid recorded
    make_arm(root, "S1", "random", seed=3407, result={"status": "SOMETHING_NEW"}, rows=synth.DESIGN[:4])
    make_arm(root, "S1", "ga", seed=3407, result="{not json", rows=synth.DESIGN[:4])
    make_arm(root, "S2", "ga", seed=3407, result={"status": "NO_TERMINAL_RESULT", "attempt": 1, "n_eval": 4}, rows=synth.DESIGN[:4])
    c, _, _ = make_campaign_client([root])
    runs = runs_by_id(c)
    cid = "vdstat_1"
    st = {k: (v["status"], v["status_detail"]) for k, v in runs.items()}
    assert st[rid("S2", "random", cid=cid)][0] == "failed" and st[rid("S2", "random", cid=cid)][1].startswith("BLOCKED_CONTRACT: arm blocked")
    assert st[rid("S1", "random", cid=cid)][0] == "failed" and "ARM_INTERRUPTED (attempt 1)" in st[rid("S1", "random", cid=cid)][1]
    assert st[rid("S1", "ga", 2024, cid)][0] == "running" and "resumed after ARM_INTERRUPTED" in st[rid("S1", "ga", 2024, cid)][1]
    assert st[rid("S2", "ga", 2024, cid)][0] == "unknown" and f"arm process {dead.pid} is not alive" in st[rid("S2", "ga", 2024, cid)][1]
    assert st[rid("S2", "random", 2024, cid)][0] == "running" and "liveness unknown" in st[rid("S2", "random", 2024, cid)][1]
    assert st[rid("S1", "random", 3407, cid)][0] == "unknown" and "SOMETHING_NEW" in st[rid("S1", "random", 3407, cid)][1]
    assert st[rid("S1", "ga", 3407, cid)][0] == "unknown" and "unreadable" in st[rid("S1", "ga", 3407, cid)][1]
    assert st[rid("S2", "ga", 3407, cid)][0] == "done" and st[rid("S2", "ga", 3407, cid)][1].startswith("NO_TERMINAL_RESULT")
    assert runs[rid("S1", "random", cid=cid)]["ended_at"] is not None  # failed arms with a result have an end time (ledger last event)


# ------------------------------------------------------------------------------------------------ API round trip + contract
def endpoints(run_id: str, cid: str = "g001_c0005", gen: int = 1) -> list[tuple[str, str]]:
    b = f"{API}/runs/{run_id}"
    return [(b, "ArchiveRun"), (f"{b}/baselines", "ArchiveBaselineList"), (f"{b}/generations", "ArchiveGenerationList"),
            (f"{b}/generations/{gen}", "ArchiveGenerationDetail"), (f"{b}/candidates", "ArchiveCandidateList"),
            (f"{b}/candidates/{cid}", "ArchiveCandidateDetail"), (f"{b}/pareto", "ArchivePareto"), (f"{b}/fidelity", "ArchiveFidelity"),
            (f"{b}/confirm", "ArchiveConfirm")]


@pytest.mark.parametrize("space,method", [("S2", "ga"), ("S2", "random"), ("S1", "ga")])
def test_every_endpoint_opens_by_campaign_run_id_and_passes_the_contract(make_campaign_client, croot, space, method):
    c, _, _ = make_campaign_client([croot])
    run_id = rid(space, method)
    for path, comp in endpoints(run_id):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert contract_errors(comp, r.json()) == [], (path, contract_errors(comp, r.json())[:3])
        json.dumps(r.json(), allow_nan=False)
    detail = c.get(f"{API}/runs/{run_id}").json()
    assert detail["id"] == run_id and detail["campaign_id"] == CID and detail["space"] == space
    assert detail["source"]["run_dir"] == str(croot / "arms" / space / method / "seed_42")
    cmp_ = c.get(f"{API}/compare?runs={rid('S2', 'ga')},{run_id}" if run_id != rid("S2", "ga") else f"{API}/compare?runs={run_id}")
    assert cmp_.status_code == 200 and contract_errors("ArchiveCompare", cmp_.json()) == []
    assert contract_errors("ArchiveLive", c.get(f"{API}/live").json()) == []


def test_completed_arm_views_use_the_existing_parsers_and_map_the_campaign_files(make_campaign_client, croot):
    c, _, _ = make_campaign_client([croot])
    b = f"{API}/runs/{rid('S2', 'ga')}"
    gens = c.get(f"{b}/generations").json()["generations"]
    assert [g["generation"] for g in gens] == [0, 1, 2] and all(g["has_summary"] for g in gens)
    assert gens[0]["n_rows"] == 4 and gens[0]["status_counts"] == {"ok": 3, "oom": 1}
    cands = c.get(f"{b}/candidates").json()
    assert cands["total"] == 9
    fin = next(x for x in cands["candidates"] if x["candidate_id"] == "g001_c0005")
    assert fin["is_final"] is True and fin["roles"] == ["BEST_TERMINAL"]
    base = c.get(f"{b}/baselines").json()
    assert [x["name"] for x in base["baselines"]] == ["baseline_yolo26n", "baseline_yolov11n"]
    assert base["judge_baseline"]["name"] == "baseline_yolo26n" and base["baselines"][0]["is_judge"] is True
    assert base["baselines"][0]["tier"] == "final@18e" and base["baselines"][0]["map50_95"] == 0.33
    det = c.get(f"{API}/runs/{rid('S2', 'ga')}").json()
    assert det["budget"] == {"arm_s": 86400, "search_s": 61200, "final_reserve_s": 25200}
    assert [r["role"] for r in det["roles"]] == ["BEST_TERMINAL"] and det["final_val"][0]["candidate_id"] == "g001_c0005"
    assert det["final_val"][0]["val_map50_95"] == 0.41 and det["final_val"][0]["latency_median_ms"] == 7.4
    par = c.get(f"{b}/pareto").json()
    assert {e["candidate_id"] for e in par["archive_entries"]} == {"g001_c0005", "g002_c0008"}
    cd = c.get(f"{b}/candidates/g001_c0005").json()
    assert cd["architecture"] is not None and [e["stage"] for e in cd["evaluations"]] == ["search", "final"]
    cmp_ = c.get(f"{API}/compare?runs={rid('S2', 'ga')},{rid('S1', 'ga')}").json()
    cond = {r["id"]: r["conditions"] for r in cmp_["runs"]}[rid("S2", "ga")]
    assert cond["dataset"] == "synthetic_visdrone" and cond["split"] == "val" and cond["epochs_total"] == 18
    assert cond["device"] == "cuda:0" and cond["precision"] == "fp16" and cond["checkpoint"] is None  # no BEST_ACCURACY role in a campaign arm


def test_unknown_and_malformed_campaign_ids(make_campaign_client, croot):
    c, _, _ = make_campaign_client([croot])
    assert c.get(f"{API}/runs/{rid('S1', 'ga', 7)}").status_code == 404  # well-formed but no such arm directory
    assert c.get(f"{API}/runs/{CID}__S1").status_code == 404
    bad = c.get(f"{API}/runs/{CID}%2F..%2F..%2Fetc")  # encoded slash: a path segment, never a path
    assert bad.status_code in (404, 422)
    assert c.get(f"{API}/runs/{CID}__S1__ga__seed_42/../x").status_code in (404, 422)


# ------------------------------------------------------------------------------------------------ tolerance
def test_unreadable_and_missing_files_degrade_to_null_and_warnings(make_campaign_client, tmp_path):
    root = make_campaign(tmp_path / "vd", "vdbroken_1")
    (root / "campaign.json").write_text("{garbage", encoding="utf-8")
    (root / "queue.json").write_bytes(b"\xff\xfe\x00")
    prep = root / "prep/yolo_ga_full_visdrone/prep_test"
    (prep / "prep_manifest.json").write_text("[1, 2", encoding="utf-8")
    arm = root / "arms/S2/ga/seed_42"
    (arm / "search/pareto/objective_champions.json").write_text("nope", encoding="utf-8")
    (arm / "search/generations/gen_001_summary.json").write_text("", encoding="utf-8")
    (arm / "ledger/cost_ledger.json").write_text("{", encoding="utf-8")
    arm2 = root / "arms/S1/ga/seed_42"
    (arm2 / "ledger/eval_timing.jsonl").unlink()
    (arm2 / "arm_manifest.json").unlink()
    c, _, _ = make_campaign_client([root])
    body = c.get(f"{API}/runs").json()
    assert contract_errors("ArchiveRunList", body) == []
    runs = {r["id"]: r for r in body["runs"]}
    cid = "vdbroken_1"  # campaign.json is unreadable: the directory name is the campaign id
    a, b = runs[rid("S2", "ga", cid=cid)], runs[rid("S1", "ga", cid=cid)]
    assert a["status"] == "done" and a["dataset_id"] is None and a["judge_baseline"] is None and a["pair"] is None
    assert a["campaign_arms_total"] is None and a["progress"]["cost_s"] == 4000.0  # arm_result.json still gives the cost
    assert b["progress"]["generations_seen"] is None and b["progress"]["candidates"] is None and b["progress"]["last_eval_at"] is None
    assert b["status"] == "running" and "liveness unknown" in b["status_detail"] and b["started_at"] is None
    assert any("campaign.json" in w and "unreadable" in w for w in body["warnings"])
    for run_id in (rid("S2", "ga", cid=cid), rid("S1", "ga", cid=cid), rid("S2", "random", cid=cid)):
        for path, comp in endpoints(run_id):
            r = c.get(path)
            assert r.status_code == 200, (path, r.text[:300])
            assert contract_errors(comp, r.json()) == [], (path, contract_errors(comp, r.json())[:3])
    base = c.get(f"{API}/runs/{rid('S2', 'ga', cid=cid)}/baselines").json()
    assert base["baselines"] == [] and any("prep_manifest.json" in w or "baselines" in w for w in base["warnings"])
    gens = c.get(f"{API}/runs/{rid('S2', 'ga', cid=cid)}/generations").json()["generations"]
    assert next(g for g in gens if g["generation"] == 1)["has_summary"] is False  # the unreadable summary is "not written", rows still count


def test_symlink_escape_and_odd_names_are_skipped(make_campaign_client, croot, tmp_path):
    outside = tmp_path / "elsewhere"
    make_arm(outside, "S9", "ga", result=COMPLETED)  # a real arm directory outside the campaign root
    (croot / "arms/S1/random").mkdir(parents=True, exist_ok=True)
    os.symlink(outside / "arms/S9/ga/seed_42", croot / "arms/S1/random/seed_42")
    (croot / "arms/S1/rand__om").mkdir(parents=True, exist_ok=True)
    (croot / "arms/S1/rand__om/seed_1").mkdir()
    (croot / "arms/S1/rand__om/seed_1/arm_manifest.json").write_text("{}", encoding="utf-8")
    store = ArchiveStore([], campaign_roots=[str(croot)])
    _, refs, warns = store.discover()
    assert rid("S1", "random") not in {r.id for r in refs} and not any("rand__om" in r.id for r in refs)
    assert any("escapes its campaign root via a symlink" in w for w in warns) and any("cannot form a run id" in w for w in warns)


def test_configured_parent_directory_of_several_campaigns_and_missing_roots(make_campaign_client, tmp_path):
    base = tmp_path / "visdrone_12search"
    make_campaign(base, "vdone_1")
    make_campaign(base, "vdtwo_2")
    (base / "not_a_campaign").mkdir()
    c, _, _ = make_campaign_client([base, tmp_path / "missing", tmp_path / "empty_dir_but_missing"])
    body = c.get(f"{API}/runs").json()
    assert [(r["path"].rsplit("/", 1)[-1], r["exists"], r["kind"]) for r in body["roots"][:2]] == [("vdone_1", True, "campaign"), ("vdtwo_2", True, "campaign")]
    assert [r["exists"] for r in body["roots"][2:]] == [False, False]
    assert any("campaign root not found" in w for w in body["warnings"])
    ids = {r["id"] for r in body["runs"]}
    assert {rid("S2", "ga", cid="vdone_1"), rid("S2", "ga", cid="vdtwo_2")} <= ids and len(ids) == 6
    # both campaigns can be compared in one request
    r = c.get(f"{API}/compare?runs={rid('S2', 'ga', cid='vdone_1')},{rid('S2', 'ga', cid='vdtwo_2')}")
    assert r.status_code == 200 and contract_errors("ArchiveCompare", r.json()) == []


def test_campaign_runs_coexist_with_plain_runs_and_ids_do_not_collide(make_campaign_client, croot, synth_root):
    c, _, _ = make_campaign_client([croot], roots=[synth_root])
    body = c.get(f"{API}/runs").json()
    assert [r["kind"] if "kind" in r else "runs" for r in body["roots"]] == ["runs", "campaign"]
    assert body["roots"][0] == {"path": str(synth_root), "family": "synth", "exists": True}  # plain roots are unchanged (no kind key)
    ids = {r["id"] for r in body["runs"]}
    assert "synthetic-day" in ids and rid("S2", "ga") in ids
    plain = next(r for r in body["runs"] if r["id"] == "synthetic-day")
    assert "campaign_id" not in plain and "progress" not in plain


# ------------------------------------------------------------------------------------------------ caching / file touching / read-only
class OpenSpy:
    """Records every ``open(path, mode)`` through ``builtins.open`` and refuses any write mode on a path under ``guard``."""

    def __init__(self, guard: Path) -> None:
        self.guard = str(guard)
        self.opened: list[tuple[str, str]] = []

    def __enter__(self) -> "OpenSpy":
        self._orig = builtins.open
        spy = self

        def patched(file, mode="r", *args, **kwargs):  # noqa: ANN001
            path = os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else None
            if isinstance(path, bytes):
                path = os.fsdecode(path)
            if path is not None and os.path.realpath(path).startswith(spy.guard):
                spy.opened.append((os.path.realpath(path), str(mode)))
                if any(ch in str(mode) for ch in "wax+"):
                    raise AssertionError(f"write-mode open of a campaign file: {path} ({mode})")
            return spy._orig(file, mode, *args, **kwargs)
        builtins.open = patched
        return self

    def __exit__(self, *exc: Any) -> None:
        builtins.open = self._orig

    def names(self, root: Path) -> list[str]:
        return sorted({os.path.relpath(p, str(root)) for p, _ in self.opened})


def test_reading_an_arm_touches_only_the_expected_handful_of_files(croot):
    store = ArchiveStore([], campaign_roots=[str(croot)])
    store.discover()
    allowed_dirs = ("train_runs", "final", "cache", "checkpoints")
    with OpenSpy(croot) as spy:
        rd = store.run(rid("S2", "ga"))
        views.run_summary(rd)
    names = spy.names(croot)
    assert not any(any(f"/{d}/" in f"/{n}" for d in allowed_dirs) for n in names), names  # no per-candidate files for the summary
    assert all(m == "r" or m == "rb" or m == "rt" for _, m in spy.opened)
    arm = "arms/S2/ga/seed_42/"
    expected_arm = {"arm_manifest.json", "arm_result.json", "ledger/cost_ledger.json", "ledger/eval_timing.jsonl",
                    "search/candidates/candidate_evaluations.jsonl", "search/pareto/objective_champions.json"}
    assert expected_arm <= {n[len(arm):] for n in names if n.startswith(arm)}
    gen_files = [n for n in names if "/generations/gen_" in n]
    others = [n for n in names if n not in gen_files and n.startswith(arm) and n[len(arm):] not in expected_arm]
    assert others == [], others  # nothing else in the arm directory
    campaign_level = [n for n in names if not n.startswith(arm) and "/generations/" not in n]
    assert set(campaign_level) <= {"campaign.json", "queue.json", "prep/yolo_ga_full_visdrone/prep_test/prep_manifest.json",
                                   "prep/yolo_ga_full_visdrone/prep_test/preset.json"}, campaign_level
    assert len(names) <= 16, names


def test_signature_cache_rebuilds_only_when_files_change(make_campaign_client, croot):
    c, _, store = make_campaign_client([croot], ttl=0.0)  # ttl 0: every request re-validates the stat signature
    run_id = rid("S1", "ga")
    first = c.get(f"{API}/runs/{run_id}").json()
    builds = store.stats["run_builds"]
    for _ in range(5):
        assert c.get(f"{API}/runs/{run_id}").status_code == 200
        assert c.get(f"{API}/runs/{run_id}/generations").status_code == 200
    assert store.stats["run_builds"] == builds  # unchanged files: no rebuild
    arm = croot / "arms/S1/ga/seed_42"
    with open(arm / "ledger/eval_timing.jsonl", "a", encoding="utf-8") as fh:  # the runtime appends a candidate row
        fh.write("\n" + json.dumps({"cid": "g002_c0100", "gen": 2, "phase": "search", "kind": "cand", "attempt": 1, "t_start": T0 + 900,
                             "t_end": T0 + 960, "status": "degenerate", "cache_hit": False}) + "\n")
    os.utime(arm / "ledger/eval_timing.jsonl", (T0, T0 + 1000))
    second = c.get(f"{API}/runs/{run_id}").json()
    assert store.stats["run_builds"] == builds + 1
    assert second["progress"]["candidates"]["total"] == first["progress"]["candidates"]["total"] + 1
    assert second["progress"]["candidates"]["degenerate"] == 1 and second["progress"]["generations_seen"] == 3
    # the end of the arm: arm_result.json appears -> status flips on the next signature check
    synth.write_json(arm / "arm_result.json", {**COMPLETED, "arm": "S1/ga/seed_42"})
    third = c.get(f"{API}/runs/{run_id}").json()
    assert third["status"] == "done" and third["has"]["live"] is False


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_mtime_ns, st.st_size)
    return out


def test_nothing_under_the_campaign_root_is_written_by_any_endpoint(make_campaign_client, croot):
    before = tree_state(croot)
    c, _, _ = make_campaign_client([croot], ttl=0.0)
    with OpenSpy(croot) as spy:
        for run_id in (rid("S2", "ga"), rid("S2", "random"), rid("S1", "ga")):
            for path, _comp in endpoints(run_id):
                assert c.get(path).status_code == 200
            for kind in ("plot_hypervolume", "run_log_tail", "final_report_md"):
                assert c.get(f"{API}/runs/{run_id}/files/{kind}").status_code in (200, 404)
        assert c.get(f"{API}/compare?runs={rid('S2', 'ga')},{rid('S1', 'ga')}").status_code == 200
        assert c.get(f"{API}/live").status_code == 200
        assert c.get(f"{API}/runs").status_code == 200
    assert spy.opened and all(m in ("r", "rb", "rt") for _, m in spy.opened)
    assert tree_state(croot) == before  # same files, same mtimes, same sizes: nothing created, touched or removed


# ------------------------------------------------------------------------------------------------ settings / wiring
def test_setting_parses_env_list_and_defaults_to_off(monkeypatch, tmp_path):
    base = dict(api_token="t" * 20, artifact_signing_key="k" * 32, db_path=str(tmp_path / "d.sqlite3"), output_root=str(tmp_path),
                output_roots=[str(tmp_path)], dataset_roots=[str(tmp_path)])
    assert Settings(**base).archive_campaign_roots == []
    monkeypatch.setenv("MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS", f"{tmp_path}/a, {tmp_path}/b")
    assert Settings(**base).archive_campaign_roots == [f"{tmp_path}/a", f"{tmp_path}/b"]
    monkeypatch.setenv("MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS", "relative/path")
    with pytest.raises(ValueError):
        Settings(**base)


def test_create_app_wires_the_setting_into_the_store(tmp_roots, croot):
    """``create_app`` passes ``campaign_roots=settings.archive_campaign_roots`` to the store (WPS7 request R1, wired in S01_fix1)."""
    from conftest_common import owner_harness  # DD-A43(b): owner-only harness; skipped without it

    harness = owner_harness()

    from mbnas_server.api.app import create_app

    settings = harness.make_gateway_settings(tmp_roots, archive_roots=[], archive_driver_dirs=[], archive_campaign_roots=[str(croot)])
    app = create_app(settings)
    assert app.state.archive.campaign_roots == [str(croot)]


# ------------------------------------------------------------------------------------------------ the real campaign (read-only)
@requires_real_croot
def test_real_campaign_read_only_smoke(make_campaign_client):
    """Lists the runs of the real (RUNNING) campaign and opens every arm through every endpoint. Reads only: any write-mode ``open`` of a
    path under the campaign root raises, no write API exists on the archive router, and the opened files are only the known small set.

    ~11 requests per arm (12 arms: ~135 back-to-back requests) exceed the gateway's per-token bucket (burst 50 @ 10 rps, R13,
    tested in test_wp3_gateway_relay.py), so the bucket is off as in test_perf.py (EVAL_1 D7: 429 RATE_LIMITED with it on). The
    cold list is timed like ``test_perf.timed_cold`` (DD-40: no full collection of the pytest heap inside the timed call) against
    the load-aware budget ``load_aware_budget_ms(COLD_LIST_MS)``."""
    c, _, store = make_campaign_client([REAL_CROOT], rate_limit_enabled=False)
    with OpenSpy(REAL_CROOT) as spy:
        budget_ms, load = load_aware_budget_ms(COLD_LIST_MS)
        gc.collect()
        gc.freeze()
        try:
            t = time.perf_counter()
            listing = c.get(f"{API}/runs")
            cold_ms = (time.perf_counter() - t) * 1000
        finally:
            gc.unfreeze()
        assert listing.status_code == 200, listing.text[:300]
        assert cold_ms < budget_ms, (f"cold campaign list took {cold_ms:.0f} ms > load-aware budget {budget_ms:.0f} ms (idle "
                                     f"{COLD_LIST_MS:.0f} ms, load1 {load['load1']} on {load['cpus']} CPUs; the real arms are "
                                     "~200 rows / ~20 generation summaries each)")
        body = listing.json()
        assert contract_errors("ArchiveRunList", body) == [], contract_errors("ArchiveRunList", body)[:3]
        assert body["roots"] == [{"path": str(REAL_CROOT), "family": "visdrone-campaign", "exists": True, "kind": "campaign"}]
        runs = body["runs"]
        assert runs, "the campaign has at least one arm directory"
        for r in runs:
            assert r["family"] == "visdrone-campaign" and r["campaign_id"] == REAL_CROOT.name
            assert r["status"] in ("running", "done", "failed", "unknown")
            assert r["campaign_arm"] == f"{r['space']}/{r['method']}/seed_{r['seed']}"
            assert r["id"] == cp.run_id_of(r["campaign_id"], r["space"], r["method"], f"seed_{r['seed']}")
        for r in runs:
            run_id = r["id"]
            first = c.get(f"{API}/runs/{run_id}/candidates?limit=1").json()
            cid = first["candidates"][0]["candidate_id"] if first["candidates"] else "g000_c0001"
            gens = c.get(f"{API}/runs/{run_id}/generations").json()["generations"]
            paths = [(p, comp) for p, comp in endpoints(run_id, cid=cid, gen=gens[0]["generation"] if gens else 0)
                     if gens or "/generations/" not in p]
            for path, comp in paths:
                resp = c.get(path)
                if path.endswith(f"/candidates/{cid}") and not first["candidates"]:
                    assert resp.status_code == 404
                    continue
                assert resp.status_code == 200, (path, resp.text[:300])
                assert contract_errors(comp, resp.json()) == [], (path, contract_errors(comp, resp.json())[:3])
                json.dumps(resp.json(), allow_nan=False)
        done = [r for r in runs if r["status"] == "done"]
        if len(done) >= 2:
            cmp_ = c.get(f"{API}/compare?runs={done[0]['id']},{done[1]['id']}")
            assert cmp_.status_code == 200 and contract_errors("ArchiveCompare", cmp_.json()) == []
        assert c.get(f"{API}/live").status_code == 200
    assert spy.opened and all(m in ("r", "rb", "rt") for _, m in spy.opened), "only read-mode opens under the campaign root"
    print("REAL_CAMPAIGN", json.dumps({"arms": len(runs), "list_cold_ms": round(cold_ms, 1), "budget_ms": round(budget_ms), **load,
                                       "opened_files": len(spy.opened), "run_builds": store.stats["run_builds"]}))
