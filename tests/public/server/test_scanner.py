"""Discovery, status rules, caching and tolerant parsing (synthetic runs in tmp dirs; process liveness uses real pids)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import archive_synth as synth

from mbnas_server.api.contract_check import contract_errors
from mbnas_server.archive import parsers
from mbnas_server.archive.live import pid_alive
from mbnas_server.archive.scanner import ArchiveError, ArchiveStore


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def store_for(root: Path, **kw) -> ArchiveStore:
    return ArchiveStore([str(root)], [], **kw)


# ------------------------------------------------------------------------------------------------ discovery

def test_discovery_markers_ids_and_symlinks(tmp_path):
    root = tmp_path / "yolo_ga_full_x"
    root.mkdir()
    synth.make_run(root, "run-ok")
    (root / "cpugate_only" / "g3").mkdir(parents=True)  # no run marker -> not a run
    (root / ".hidden").mkdir()
    (root / ".hidden" / "run.log").write_text("x")
    (root / "bad name").mkdir()
    (root / "bad name" / "run.log").write_text("x")
    (root / "plain_file").write_text("x")
    outside = tmp_path / "outside_run"
    (outside / "search").mkdir(parents=True)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "alias").symlink_to(root / "run-ok", target_is_directory=True)  # inside the root: allowed
    roots, refs, warnings = store_for(root).discover()
    assert [r.id for r in refs] == ["alias", "run-ok"] and roots[0].family == "x" and roots[0].exists
    assert any("escape" in w and "symlink" in w for w in warnings)


def test_duplicate_run_id_across_roots_uses_first(tmp_path):
    a, b = tmp_path / "ra", tmp_path / "rb"
    a.mkdir(), b.mkdir()
    synth.make_run(a, "same-id")
    synth.make_run(b, "same-id")
    st = ArchiveStore([str(a), str(b)])
    _, refs, warnings = st.discover()
    assert [r.root for r in refs] == [str(a)] and any("more than one root" in w for w in warnings)


def test_ref_errors(tmp_path):
    st = store_for(tmp_path)
    for bad in ("", ".", "..", ".hidden", "a/b", "a b", "x" * 129, "é"):
        with pytest.raises(ArchiveError) as e:
            st.ref(bad)
        assert (e.value.status, e.value.code) == (422, "ARCHIVE_BAD_ID"), bad
    with pytest.raises(ArchiveError) as e:
        st.ref("nope")
    assert (e.value.status, e.value.code) == (404, "ARCHIVE_RUN_NOT_FOUND")
    with pytest.raises(ArchiveError) as e:
        ArchiveStore([str(tmp_path / "missing")]).ref("nope")
    assert (e.value.status, e.value.code) == (503, "ARCHIVE_ROOT_UNAVAILABLE")


# ------------------------------------------------------------------------------------------------ status rules

def status_of(root: Path, rid: str):
    return store_for(root).run(rid).status()


def test_status_done_failed_unknown(tmp_path):
    synth.make_run(tmp_path, "done-run")
    assert status_of(tmp_path, "done-run")[0] == "done"
    r = synth.make_run(tmp_path, "bad-exit")
    (r / "run.log").write_text("NBCONVERT_EXIT=1\n")
    assert status_of(tmp_path, "bad-exit") == ("failed", "NBCONVERT_EXIT=1")
    r = synth.make_run(tmp_path, "later-exit")
    (r / "run.log").write_text("NBCONVERT_EXIT=1\nNBCONVERT_EXIT=0\n")  # the last marker wins (re-run)
    assert status_of(tmp_path, "later-exit")[0] == "done"
    r = synth.make_run(tmp_path, "no-final")
    (r / "final_summary.json").unlink()
    st, detail = status_of(tmp_path, "no-final")
    assert st == "unknown" and "final_summary.json is missing" in detail
    r = synth.make_run(tmp_path, "unfinished", finished=False)
    assert status_of(tmp_path, "unfinished")[0] == "unknown"
    (tmp_path / "FAILED_attempt").mkdir()
    (tmp_path / "FAILED_attempt" / "run.log").write_text("traceback only\n")
    assert status_of(tmp_path, "FAILED_attempt") == ("failed", "directory name starts with FAILED_")


def test_stop_rule_wins_over_a_clean_exit(tmp_path):
    r = synth.make_run(tmp_path, "stopped")
    synth.write_json(r / "driver/stop_rule.json", {"rule": "S1 budget exceeded", "at": "2026-10-13"})
    assert status_of(tmp_path, "stopped") == ("failed", "stop_rule: S1 budget exceeded")
    synth.write_json(r / "driver/stop_rule.json", {"unexpected": 1})
    assert status_of(tmp_path, "stopped") == ("failed", "stop_rule.json present")


def test_running_needs_a_live_notebook_process(tmp_path):
    r = synth.make_run(tmp_path, "live-run", finished=False, pids=True)
    st, detail = status_of(tmp_path, "live-run")
    assert st == "running" and detail == "nb_pid alive; stage search"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    d = json.loads((r / "driver/run.pids.json").read_text())
    d["nb_pid"] = child.pid  # reaped: no such process any more
    (r / "driver/run.pids.json").write_text(json.dumps(d))
    assert status_of(tmp_path, "live-run")[0] == "unknown"


def test_pid_reuse_guards():
    me = os.getpid()
    now = time.time()
    assert pid_alive(me) and pid_alive(me, started_after=now - 3600, started_before=now + 60)
    assert not pid_alive(me, started_after=now + 3600)  # launcher "started later" than this process -> a reused pid
    assert not pid_alive(me, started_before=now - 36000)  # record written long before the process existed
    for bad in (None, 0, -1, True, "12", 10 ** 9):
        assert not pid_alive(bad)


def test_status_refreshes_after_ttl_without_file_changes(tmp_path):
    r = synth.make_run(tmp_path, "dying", finished=False, pids=False)
    victim = subprocess.Popen(["sleep", "30"])
    try:
        now = time.time()
        synth.write_json(r / "driver/run.pids.json", {"stage": "search", "launcher_start_epoch": now - 30, "nb_pid": victim.pid,
                                                      "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))})
        clock = Clock()
        st = store_for(tmp_path, clock=clock, ttl_s=5.0)
        assert st.run("dying").status()[0] == "running"
        victim.kill()
        victim.wait()
        clock.t += 1.0  # inside the ttl: cached
        assert st.run("dying").status()[0] == "running"
        clock.t += 10.0  # ttl over: signature unchanged, but liveness is re-evaluated
        builds = st.stats["run_builds"]
        assert st.run("dying").status()[0] == "unknown" and st.stats["run_builds"] == builds
    finally:
        victim.kill()
        victim.wait()


# ------------------------------------------------------------------------------------------------ caching

def test_ttl_and_mtime_invalidation(tmp_path):
    r = synth.make_run(tmp_path, "cached")
    clock = Clock()
    st = store_for(tmp_path, clock=clock, ttl_s=5.0)
    rd = st.run("cached")
    assert len(rd.cands) == 9 and st.stats["run_builds"] == 1
    rows = synth.DESIGN + [synth.row("g002_c0010", 2, "F10", "ok", synth.metrics(0.5, 4.0, 0.95, 1000), {"mode": "ga_crossover", "parents": []})]
    synth.write_jsonl(r / "search/candidates/candidate_evaluations.jsonl", rows)
    clock.t += 1
    assert st.run("cached") is rd and len(st.run("cached").cands) == 9  # within ttl nothing is re-read
    clock.t += 5
    rd2 = st.run("cached")
    assert rd2 is not rd and len(rd2.cands) == 10 and st.stats["run_builds"] == 2
    clock.t += 6
    assert st.run("cached") is rd2 and st.stats["run_builds"] == 2  # unchanged signature: same object, no rebuild
    # same size and mtime_ns would be invisible; a changed size is not
    p = r / "search/archive.json"
    p.write_text(p.read_text() + " ")
    clock.t += 6
    assert st.run("cached") is not rd2


def test_cfg_files_are_parsed_once_across_rebuilds(tmp_path):
    r = synth.make_run(tmp_path, "cfgcache")
    clock = Clock()
    st = store_for(tmp_path, clock=clock)
    rd = st.run("cfgcache")
    from mbnas_server.archive import views
    views.generation_items(rd)
    first = st.stats["cfg_parses"]
    assert first == len(synth.CFG_MODULES)
    synth.write_jsonl(r / "search/candidates/candidate_evaluations.jsonl", synth.DESIGN[:-1])
    clock.t += 10
    rd2 = st.run("cfgcache")
    assert rd2 is not rd
    views.generation_items(rd2)
    assert st.stats["cfg_parses"] == first


def test_discovery_is_cached_for_the_ttl(tmp_path):
    clock = Clock()
    st = store_for(tmp_path, clock=clock)
    assert st.discover()[1] == []
    synth.make_run(tmp_path, "late")
    clock.t += 1
    assert st.discover()[1] == []
    clock.t += 5
    assert [r.id for r in st.discover()[1]] == ["late"]


# ------------------------------------------------------------------------------------------------ tolerant parsing

def test_jsonl_skips_partial_and_garbage_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\nnot json\n[1, 2]\n\n{"b": NaN, "c": Infinity}\n{"cut": "abc', encoding="utf-8")
    rows, bad, err = parsers.read_jsonl(str(p))
    assert rows == [{"a": 1}, {"b": None, "c": None}] and bad == 3 and err is None
    assert parsers.read_jsonl(str(tmp_path / "missing"))[2] == "missing"


def test_json_with_nonfinite_numbers_becomes_strict(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"t": Infinity, "n": [NaN, 1.5], "d": {"x": -Infinity}}')
    assert parsers.read_json(str(p)) == ({"t": None, "n": [None, 1.5], "d": {"x": None}}, None)
    p.write_text('{"big": 1e999, "small": 1e-999, "ok": 2.5e3, "l": [-1E400]}')  # overflow -> inf in Python: must not survive
    assert parsers.read_json(str(p)) == ({"big": None, "small": 0.0, "ok": 2500.0, "l": [None]}, None)
    p.write_text("{broken")
    assert parsers.read_json(str(p))[1].startswith("unreadable")
    assert parsers.finite(True) is None and parsers.finite(float("nan")) is None and parsers.finite(3) == 3.0 and parsers.finite("1") is None


def test_notes_parsing_variants():
    ok = parsers.parse_notes(json.dumps({"mode": "ga_crossover", "parents": ["a", "b"], "mutation": {"mutation_rate": 0.3},
                                          "cache_hit": "a", "error": ""}))
    assert ok == {"mode": "ga_crossover", "parents": ["a", "b"], "mutation": {"mutation_rate": 0.3}, "cache_hit": "a", "error": None, "parse": "ok"}
    cut = '{"mode": "ga_crossover", "parents": ["g002_c0047", "g000_c0005"], "mutation": {"mutation_rate": 0.3, "prefer_mod'
    p = parsers.parse_notes(cut)
    assert (p["mode"], p["parents"], p["mutation"], p["parse"]) == ("ga_crossover", ["g002_c0047", "g000_c0005"], None, "truncated")
    err = parsers.parse_notes('{"mode": "x", "parents": [], "error": "eval: RuntimeError: out of memory. Tried to alloc')
    assert err["parse"] == "truncated" and err["error"].startswith("eval: RuntimeError") and err["cache_hit"] is None
    assert parsers.parse_notes("plain error text")["error"] == "plain error text"
    for empty in (None, "", "   ", 7):
        assert parsers.parse_notes(empty) == {"mode": None, "parents": [], "mutation": None, "cache_hit": None, "error": None, "parse": "missing"}


def test_rows_with_bad_ids_are_dropped_and_reattempts_keep_the_last(tmp_path):
    rows = synth.DESIGN[:2] + [
        synth.row("../evil", 0, "FX", "ok", synth.metrics(0.9, 1.0, 0.9, 10), {}),
        synth.row("g000_c0002", 0, "F2", "ok", synth.metrics(0.36, 7.0, 0.92, 2100), {}, attempt=1),  # same (cid, tier, attempt): replaces
        dict(synth.row("g000_c0099", 0, "FY", "ok", {}, {}), generation="zero")]
    synth.make_run(tmp_path, "weird", rows=rows)
    rd = store_for(tmp_path).run("weird")
    assert [c["candidate_id"] for c in rd.cands] == ["g000_c0001", "g000_c0002"]
    assert rd.cands[1]["metrics"]["map50_95"] == 0.36
    assert any("without a valid candidate_id/generation" in w for w in rd.warnings)


# ------------------------------------------------------------------------------------------------ partial / running runs over the API

def test_running_run_with_partial_jsonl_and_missing_files(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_live"
    root.mkdir()
    synth.make_run(root, "live-run", finished=False, pids=True, jsonl_tail='{"candidate_id": "g002_c0010", "generation": 2, "metr')
    c, _ = make_client([root])
    base = "/api/v1/archive/runs/live-run"
    runs = c.get("/api/v1/archive/runs").json()["runs"]
    assert runs[0]["status"] == "running" and runs[0]["has"]["live"] and runs[0]["generations"] == {"planned": None, "done": 2}
    assert runs[0]["verdicts"] == {"notebook": None, "transition": None, "confirm": None, "smoke_check": None}
    assert runs[0]["judge_baseline"] is None and runs[0]["candidates"]["total"] == 9
    detail = c.get(base).json()
    assert contract_errors("ArchiveRun", detail) == []
    assert detail["live"]["running_runs"][0]["id"] == "live-run" and detail["stages"] == [] and detail["gates"] == []
    assert any("unparsable line" in w for w in detail["warnings"])
    gens = c.get(f"{base}/generations").json()["generations"]
    assert [g["has_summary"] for g in gens] == [True, True, False]  # generation 2 has rows but no summary yet
    assert gens[2]["hypervolume"] is None and gens[2]["delta_vs_prev"]["hypervolume"] is None and gens[2]["n_rows"] == 2
    assert gens[2]["delta_vs_baseline"]["best_map50_95"] is None  # no baseline file: null, not 0
    gd = c.get(f"{base}/generations/2").json()
    assert any("gen_002_summary.json missing" in w for w in gd["warnings"])
    b = c.get(f"{base}/baselines").json()
    assert b["judge_baseline"] is None and b["baselines"] == [] and b["anchor"] is None and any("baselines_v2.json missing" in w for w in b["warnings"])
    p = c.get(f"{base}/pareto").json()
    assert p["archive_entries"] == [] and any("no runtime pareto archive" in w for w in p["warnings"]) and p["front2d_ids"]
    f = c.get(f"{base}/fidelity").json()
    assert f["rank_correlation"] is None and "missing" in f["evidence_note"]
    cf = c.get(f"{base}/confirm").json()
    assert cf["final_verdict"] is None and cf["summary"] is None and any("three_seed_summary.json missing" in w for w in cf["warnings"])
    for kind in ("plot_pareto_map50_latency", "final_report_md"):
        assert c.get(f"{base}/files/{kind}").status_code == 404
    live = c.get("/api/v1/archive/live").json()
    r0 = live["running_runs"][0]
    assert (r0["id"], r0["stage"], r0["nb_pid_alive"], r0["kernel_pid_alive"], r0["budget_s"], r0["elapsed_s"]) == (
        "live-run", "search", True, True, 5400.0, 301.1)
    assert r0["watchdog_last"].endswith("발동=없음") and live["drivers"] == []


def test_driver_status_and_log_tail(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_a"
    root.mkdir()
    synth.make_run(root, "r1")
    drv = tmp_path / "driver"
    drv.mkdir()
    (drv / "STATUS.json").write_text(json.dumps({"stage": "done", "x": float("nan")}))
    (drv / "pipeline.log").write_text("".join(f"line {i}\n" for i in range(50)))
    c, _ = make_client([root], [drv, tmp_path / "no-such-driver"])
    live = c.get("/api/v1/archive/live").json()
    assert contract_errors("ArchiveLive", live) == []
    (d,) = live["drivers"]
    assert d["status_json"] == {"stage": "done", "x": None} and d["pipeline_log_tail"] == [f"line {i}" for i in range(30, 50)]
    assert any("driver directory not found" in w for w in live["warnings"]) and live["running_runs"] == []


def test_api_status_follows_process_liveness_after_the_ttl(make_client, tmp_path):
    """A notebook that dies without touching any file: the cached `running` must turn into `unknown` within one TTL."""
    root = tmp_path / "yolo_ga_full_ttl"
    root.mkdir()
    r = synth.make_run(root, "dying", finished=False)
    victim = subprocess.Popen(["sleep", "30"])
    try:
        now = time.time()
        synth.write_json(r / "driver/run.pids.json", {"stage": "search", "launcher_start_epoch": now - 30, "nb_pid": victim.pid,
                                                      "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))})
        c, _ = make_client([root], ttl=0.2)
        assert c.get("/api/v1/archive/runs/dying").json()["status"] == "running"
        assert c.get("/api/v1/archive/runs").json()["runs"][0]["status"] == "running"
        victim.kill()
        victim.wait()
        time.sleep(0.5)
        assert c.get("/api/v1/archive/runs/dying").json()["status"] == "unknown"
        assert c.get("/api/v1/archive/runs").json()["runs"][0]["status"] == "unknown"
        assert c.get("/api/v1/archive/live").json()["running_runs"] == []
    finally:
        victim.kill()
        victim.wait()


def test_exit_marker_is_found_in_a_huge_log(tmp_path):
    r = synth.make_run(tmp_path, "biglog")
    with open(r / "run.log", "w") as fh:
        fh.write("x" * 100 + "\n")
        fh.write(("progress line\n" * 1_500_000))  # ~21 MB
        fh.write("NBCONVERT_EXIT=0\nTEST_UNLOCK_EVENT #3\n")
    assert status_of(tmp_path, "biglog")[0] == "done"
