"""Response-time budgets (design sections 5/6): real 126-row run < 300 ms cold and < 30 ms cached; 2,000-row synthetic run < 1.5 s;
no file-handle growth under repeated requests. Timings are printed with -s and written to $ARCHIVE_PERF_LOG when set."""
from __future__ import annotations

import gc
import json
import os
import time

import pytest
import archive_synth as synth
from archive_realdata import NPS_DAY, requires_nps

COLD_MS, WARM_MS, BIG_S = 300.0, 30.0, 1.5


def log(entry: dict) -> None:
    print("PERF", json.dumps(entry))
    path = os.environ.get("ARCHIVE_PERF_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def timed(client, path: str) -> tuple[float, int]:
    t = time.perf_counter()
    r = client.get(path)
    return (time.perf_counter() - t) * 1000.0, r.status_code


def timed_cold(client, path: str) -> tuple[float, int]:
    """Timed first (cold) call, isolated from the heap the rest of the test process has accumulated.

    A full (generation 2) garbage collection is stop-the-world and costs time proportional to the number of live objects in the whole
    pytest process; in the standard gate (worker/api tests, torch, ultralytics, rfdetr imported) one such pause fell inside the timed
    cold call and pushed it over COLD_MS although the endpoint itself is fast (design decision DD-40). `gc.collect(); gc.freeze()` right
    before the call moves everything alive into the permanent generation so the call only collects its own young objects; `gc.unfreeze()`
    in `finally` restores normal collection. The budget (COLD_MS), the markers and the measured endpoints are unchanged.
    Evidence: docs/integration_20261021/evidence_impl/F/perf_generations_investigation.md (387 ms after tests/worker; 0 misses with freeze).
    """
    gc.collect()
    gc.freeze()
    try:
        return timed(client, path)
    finally:
        gc.unfreeze()


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


@requires_nps
def test_real_nps_day_candidates_cold_and_cached(real_client):
    path = f"/api/v1/archive/runs/{NPS_DAY}/candidates"
    cold, status = timed_cold(real_client, path)
    assert status == 200
    warm = min(timed(real_client, path)[0] for _ in range(5))
    log({"test": "nps_day_candidates", "rows": 126, "cold_ms": round(cold, 1), "cached_ms": round(warm, 1), "budget_cold_ms": COLD_MS, "budget_cached_ms": WARM_MS})
    assert cold < COLD_MS, f"cold {cold:.0f} ms"
    assert warm < WARM_MS, f"cached {warm:.0f} ms"


@requires_nps
@pytest.mark.parametrize("suffix", ["", "/baselines", "/generations", "/generations/5", "/pareto", "/fidelity", "/confirm", "/candidates/g009_c0143"])
def test_real_nps_day_other_endpoints_within_budget(make_client, suffix):
    from archive_realdata import REAL_ROOTS
    c, _ = make_client(REAL_ROOTS)  # a fresh app per endpoint: every first call is cold
    path = f"/api/v1/archive/runs/{NPS_DAY}{suffix}"
    cold, status = timed_cold(c, path)
    warm = min(timed(c, path)[0] for _ in range(5))
    log({"test": "nps_day_endpoint", "endpoint": suffix or "(run)", "cold_ms": round(cold, 1), "cached_ms": round(warm, 1)})
    assert status == 200 and cold < COLD_MS and warm < WARM_MS, (suffix, cold, warm)


@requires_nps
def test_real_run_list_and_compare_budget(make_client):
    from archive_realdata import REAL_ROOTS, VISDRONE_SMOKE
    c, _ = make_client(REAL_ROOTS)
    cold, status = timed_cold(c, "/api/v1/archive/runs")
    warm = min(timed(c, "/api/v1/archive/runs")[0] for _ in range(5))
    log({"test": "runs_list", "cold_ms": round(cold, 1), "cached_ms": round(warm, 1)})
    assert status == 200 and cold < COLD_MS * 2 and warm < WARM_MS
    cmp_path = f"/api/v1/archive/compare?runs={NPS_DAY},{VISDRONE_SMOKE}"
    t, status = timed_cold(c, cmp_path)
    log({"test": "compare_2_runs", "ms": round(t, 1)})
    assert status == 200 and t < COLD_MS * 2


def test_synthetic_2000_rows_within_one_and_a_half_seconds(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_big"
    root.mkdir()
    synth.make_big_run(root, "synthetic-big", n_rows=2000, per_gen=100)
    c, _ = make_client([root])
    base = "/api/v1/archive/runs/synthetic-big"
    results = {}
    for name, path in (("candidates", f"{base}/candidates?limit=5000"), ("generations", f"{base}/generations"), ("run", base),
                       ("pareto", f"{base}/pareto"), ("generation_detail", f"{base}/generations/10"), ("list", "/api/v1/archive/runs")):
        ms, status = timed_cold(c, path)
        warm = min(timed(c, path)[0] for _ in range(3))
        results[name] = (ms, warm)
        log({"test": "synthetic_2000", "endpoint": name, "cold_ms": round(ms, 1), "cached_ms": round(warm, 1)})
        assert status == 200
        assert ms < BIG_S * 1000, f"{name}: {ms:.0f} ms"
        assert warm < WARM_MS * 3, f"{name} cached: {warm:.0f} ms"
    body = c.get(f"{base}/candidates?limit=5000").json()
    assert body["total"] == 2000 and len(body["candidates"]) == 2000
    gens = c.get(f"{base}/generations").json()["generations"]
    assert len(gens) == 20 and all(g["n_rows"] == 100 for g in gens)


def test_no_file_handle_growth_and_bounded_caches(make_client, synth_root):
    # ttl 0: every request re-validates signatures and rebuilds changed state. 480 back-to-back requests exceed the WP-3
    # per-token bucket (R13, burst 50 @ 10 rps, tested in test_wp3_gateway_relay.py); this row measures fds/caches only.
    c, app = make_client([synth_root], ttl=0.0, rate_limit_enabled=False)
    paths = [f"/api/v1/archive/runs/synthetic-day{s}" for s in ("", "/baselines", "/generations", "/generations/1", "/candidates",
                                                                "/candidates/g001_c0005", "/pareto", "/files/plot_hypervolume", "/files/run_log_tail")]
    paths += ["/api/v1/archive/runs", "/api/v1/archive/live", "/api/v1/archive/compare?runs=synthetic-day"]
    for p in paths:
        c.get(p)
    before = open_fds()
    for _ in range(40):
        for p in paths:
            assert c.get(p).status_code == 200
    after = open_fds()
    assert after <= before + 2, f"file descriptors grew {before} -> {after}"
    store = app.state.archive
    assert len(store._runs) <= store.max_runs and len(store._cfg) <= 8192 and len(store.memo) <= 64
    assert store.stats["run_builds"] <= 3  # ttl 0 re-checks the signature but does not rebuild an unchanged run
