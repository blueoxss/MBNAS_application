"""DD-A32 / DD-A49 (a) end to end at the core: the runner daemon's ``outputs.device_used`` reaches the evaluation row's
``raw_metrics.device_used`` (``mbnas_core.orchestration.engine.with_device_used``), for a search run (engine
``job_finished``) and for an explicit training run (``training_runs.selection.on_training_result``, the path of T7 item
a2). Real core app + real ``RunnerDaemon`` over the TestClient transport + real child subprocess with the SYNTHETIC stub
handlers, on this CPU host: the runner registers as a CPU device (``CUDA_VISIBLE_DEVICES=""``), so DD-A32 says ``cpu``.
"""
from __future__ import annotations

import copy

import harness
import pytest

from mbnas_core.db import repo

pytestmark = pytest.mark.runner_protocol


def _cpu_daemon(daemon_factory, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")  # a CPU-only runner (DD-A32), whatever the pytest environment
    d = daemon_factory()
    assert d.cpu_only and d.device["id"] == "cpu"
    return d


def _single_evaluated_row(db, run_id: str) -> dict:
    rows = [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    assert len(rows) == 1, [(e["id"], e["status"]) for e in repo.list_evaluations(db, run_id)]
    return rows[0]["row"]


def _assert_cpu_recorded(db, run_id: str) -> None:
    jobs = repo.list_jobs(db, run_id)
    assert [j["status"] for j in jobs] == ["SUCCEEDED"], [(j["kind"], j["status"], j.get("error")) for j in jobs]
    out = jobs[0]["result"]["outputs"]
    assert out["device_used"] == "cpu" and out["device_uuid"] is None
    raw = _single_evaluated_row(db, run_id)["raw_metrics"]
    assert raw["device_used"] == "cpu"
    assert raw["child_env"]["CUDA_VISIBLE_DEVICES"] == ""  # the stub child really ran without a CUDA device
    runner = db.execute("SELECT device, device_uuid FROM runners WHERE id = ?", ("R1",)).fetchone()
    assert tuple(runner) == ("cpu", None)


def test_search_run_records_raw_metrics_device_used_cpu(core, daemon_factory, base_proto, db, monkeypatch):
    run_id, _ = harness.validate_and_run(core, base_proto())
    d = _cpu_daemon(daemon_factory, monkeypatch)
    assert d.run(once=True) == 0
    _assert_cpu_recorded(db, run_id)


def test_explicit_training_run_records_raw_metrics_device_used_cpu(core, daemon_factory, base_proto, db, monkeypatch):
    proto = copy.deepcopy(base_proto())
    proto["console"]["training"] = {"epoch_target_total": 2, "seed": 13}
    proto["console"]["search"]["evaluation_enabled"] = False
    req = {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "scratch",
           "validation_enabled": True, "idempotency_key": "device-used-explicit"}
    r = core.post("/api/v1/training-runs", json=req, headers=harness.auth())
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert [j["kind"] for j in repo.list_jobs(db, run_id)] == ["BASELINE_TRAIN"]
    d = _cpu_daemon(daemon_factory, monkeypatch)
    assert d.run(once=True) == 0
    _assert_cpu_recorded(db, run_id)
    assert repo.get_run(db, run_id)["status"] == "COMPLETED"
