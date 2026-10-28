"""WP-S3 guards around the evaluation-free (OFF) path and explicit training (synthetic fixtures, no GPU, no real model).

DD-4  final retraining is refused for an evaluation-free run (409 FINAL_RETRAIN_NOT_APPLICABLE) and no longer 500s
      on placeholder rungs.
DD-11 job_finished of a training job whose run directory holds no images / no saved configuration does not fail.
DD-12 an architecture.yaml written by an older serialization is not rewritten while the configuration is unchanged.
G17   ``CandidateDetail.evaluation_status`` is only ever EVALUATED or NOT_EVALUATED.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import harness
from harness import auth
from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine, final_retrain
from mbnas_contracts.schemas.jobs import JobResult


def _off_run(client, tmp_roots, key, **extra):
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
                    "candidate_count": 1, "max_attempts": 2, "allow_duplicates": False, "arms": ["GA"]}}, **extra}
    v = client.post("/api/v1/protocols/validate", json={"protocol": p}, headers=auth()).json()
    assert v["valid"], v
    body = {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"], "validation_id": v["validation_id"],
            "idempotency_key": key}
    r = client.post("/api/v1/runs", json=body, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _complete_empty(api_settings, rid):
    conn = connect(api_settings, init=False)
    try:
        job = repo.list_jobs(conn, rid)[0]
        with tx(conn):
            conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (job["id"],))
            run = engine.settle_run(conn, rid)
        assert run["status"] == "COMPLETED"
    finally:
        conn.close()


PLACEHOLDER_RUNGS = [{"saved": "disabled settings"}]
EVAL_RUNGS = [{"id": "L0", "epoch_target_total": 2, "promotion": None,
               "fitness_weights": {"ap": 1, "recall": 0, "latency_reward": 0, "size_reward": 0}}]


@pytest.mark.parametrize("label,extra", [
    ("no_rungs_no_final", {}),
    ("rungs_kept_final_kept", {"rungs": EVAL_RUNGS, "final_retraining": {"epoch_target_total": 2, "scheduler_horizon_epochs": 2},
                               "hard_constraints": {"latency_le_ms": 100, "weights_size_le_MB": 50}}),
    ("placeholder_rungs_final_kept", {"rungs": PLACEHOLDER_RUNGS,
                                      "final_retraining": {"epoch_target_total": 2, "scheduler_horizon_epochs": 2}})])
def test_final_retrain_of_an_off_run_is_not_applicable_never_500(client, api_settings, tmp_roots, label, extra):
    """Same three protocol shapes as evidence_I3/probe_off_final.py; the third used to raise KeyError -> HTTP 500."""
    rid = _off_run(client, tmp_roots, "guard-" + label, **extra)
    _complete_empty(api_settings, rid)
    r = client.post(f"/api/v1/runs/{rid}/final-retrain", json={"idempotency_key": "f-" + label, "budget_gpu_hours": 0.1},
                    headers=auth())
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "FINAL_RETRAIN_NOT_APPLICABLE" and "evaluation_enabled=false" in body["message"]
    conn = connect(api_settings, init=False)
    try:
        assert [c for c in repo.list_runs(conn) if c.get("parent_run_id") == rid] == []  # no child run was created
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id=? AND kind!='PROPOSE_GENERATION'", (rid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_final_retrain_refused_for_an_off_run_that_is_not_completed_too(client, tmp_roots):
    rid = _off_run(client, tmp_roots, "guard-running")
    r = client.post(f"/api/v1/runs/{rid}/final-retrain", json={"idempotency_key": "f-run", "budget_gpu_hours": 0.1},
                    headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "FINAL_RETRAIN_NOT_APPLICABLE"


def test_selection_rule_and_finalists_tolerate_missing_or_placeholder_rungs():
    for resolved in ({}, {"rungs": []}, {"rungs": PLACEHOLDER_RUNGS}, {"rungs": ["x"]}, {"rungs": [{"id": ""}]}):
        assert "last search rung <none>" in final_retrain.selection_rule(resolved, 1)
        assert final_retrain.select_finalists(None, {"id": "p", "resolved_protocol": resolved}, 1) == []
    assert "last search rung L7" in final_retrain.selection_rule({"rungs": [{"id": "L0"}, {"id": "L7"}]}, 2)


def test_search_run_with_placeholder_rungs_yields_no_finalist_409_not_500(db, ctx_factory, tmp_roots):
    """The evaluation-ON branch keeps its NO_FINALIST contract when the resolved rungs are unusable."""
    from mbnas_core.protocol.validator import validate
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
                    "candidate_count": 1, "max_attempts": 2, "arms": ["GA"]}}}
    out = validate(p, ctx_factory(devices=[]))
    assert out.valid, out.errors
    resolved = copy.deepcopy(out.resolved)
    resolved["console"]["search"]["evaluation_enabled"] = True  # pretend an ON run whose rungs are placeholders
    resolved["rungs"] = PLACEHOLDER_RUNGS
    resolved["final_retraining"] = {"epoch_target_total": 2, "scheduler_horizon_epochs": 2}
    with tx(db):
        repo.create_run(db, id="on-placeholder", namespace="default", protocol_hash=out.protocol_hash, resolved_protocol=resolved,
                        requested_protocol=p, execution_mode="PILOT", idempotency_key="k-on-ph", payload_sha256="p",
                        status="COMPLETED")
    from mbnas_contracts.schemas.api import FinalRetrainRequest
    with pytest.raises(final_retrain.FinalRetrainError) as err:
        final_retrain.request_final_retrain(db, "on-placeholder", FinalRetrainRequest(idempotency_key="f", budget_gpu_hours=0.1))
    assert err.value.status == 409 and err.value.code == "NO_FINALIST"


# -- DD-11 ---------------------------------------------------------------------------------------------------------
def _training_request(protocol, key):
    p = copy.deepcopy(protocol)
    p["console"]["training"] = {"epoch_target_total": 2, "seed": 3}
    p["console"]["search"]["evaluation_enabled"] = False
    return {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": p, "mode": "scratch",
            "validation_enabled": False, "idempotency_key": key}


def test_training_job_finish_tolerates_run_dir_without_images_or_configuration(client, protocol, api_settings, monkeypatch):
    from mbnas_core.orchestration import model_artifacts
    created = client.post("/api/v1/training-runs", json=_training_request(protocol, "dd11-none"), headers=auth())
    assert created.status_code == 200, created.text
    rid, cid = created.json()["run_id"], created.json()["candidate_id"]
    calls = []
    real = model_artifacts.record_native_images
    monkeypatch.setattr(model_artifacts, "record_native_images", lambda *a, **k: calls.append(1) or real(*a, **k))
    conn = connect(api_settings, init=False)
    try:
        job = repo.list_jobs(conn, rid)[0]
        job["attempt"] = 1
        with tx(conn):
            conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (job["id"],))
            engine.job_finished(conn, job, JobResult(job_id=job["id"], status="SUCCEEDED", outputs={"epochs_done": 2}))
        assert calls == [1]  # DD-11: registration still runs for every TRAIN_KINDS job
        assert repo.get_run(conn, rid)["status"] == "COMPLETED"
        assert [a for a in repo.list_artifacts(conn, rid)
                if a["kind"] in ("pr_curve", "confusion_matrix", "other")] == []
        messages = [e["payload"].get("reason_code") for e in repo.get_events_after(conn, rid, 0, 200) if e["type"] == "log"]
        assert "MODEL_CONFIGURATION_UNAVAILABLE" not in messages
        assert repo.get_candidate(conn, cid)["candidate_status"] == "SEARCH_COMPLETE"
    finally:
        conn.close()


def test_native_image_registration_skips_files_that_are_not_images(client, protocol, api_settings):
    from mbnas_core.artifacts.registry import run_dir
    from mbnas_core.orchestration.model_artifacts import record_native_images
    created = client.post("/api/v1/training-runs", json=_training_request(protocol, "dd11-bad-image"), headers=auth())
    rid, cid = created.json()["run_id"], created.json()["candidate_id"]
    conn = connect(api_settings, init=False)
    try:
        run, cand = repo.get_run(conn, rid), repo.get_candidate(conn, cid)
        native = run_dir(run) / "candidates" / cid / "native"
        native.mkdir(parents=True)
        (native / "train_batch0.jpg").write_bytes(b"this is not a jpeg")
        (native / "results.png").write_bytes(b"")
        with tx(conn):
            assert record_native_images(conn, run, cand) == []
    finally:
        conn.close()


# -- DD-12 ---------------------------------------------------------------------------------------------------------
CFG = {"nc": 2, "scales": {"n": [0.5, 0.25, 1024]}, "backbone": [[-1, 1, "Conv", [16, 3, 2]]],
       "head": [[[0], 1, "Detect", ["nc"]]]}


def _write(tmp_path, cfg):
    pytest.importorskip("torch")
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter
    ctx = SimpleNamespace(candidate_dir=tmp_path, data=SimpleNamespace(names={0: "a", 1: "b"}))
    UltralyticsAdapter._write_arch_yaml(SimpleNamespace(), ctx, cfg)
    return tmp_path / "architecture.yaml"


def test_existing_architecture_yaml_in_an_older_format_is_not_rewritten(tmp_path):
    old = ("# written by an earlier serialization: other key order, flow style, comment\n"
           "nc: 2\nscales: {n: [0.5, 0.25, 1024]}\n"
           "head:\n  - [[0], 1, Detect, [nc]]\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\n")
    path = tmp_path / "architecture.yaml"
    path.write_text(old)
    stamp = path.stat().st_mtime_ns
    assert _write(tmp_path, copy.deepcopy(CFG)).read_text() == old
    assert path.stat().st_mtime_ns == stamp


def test_architecture_yaml_is_rewritten_when_the_configuration_differs_or_is_unreadable(tmp_path):
    first = _write(tmp_path, copy.deepcopy(CFG))
    assert first.read_text().startswith("nc: 2\n")
    changed = copy.deepcopy(CFG)
    changed["backbone"][0][3][0] = 32
    assert "32" in _write(tmp_path, changed).read_text()
    first.write_text("backbone: [unterminated\n")
    assert "Conv" in _write(tmp_path, copy.deepcopy(CFG)).read_text()
    # the recorded nc is always the executed class count of the dataset, as before
    wrong_nc = copy.deepcopy(CFG)
    wrong_nc["nc"] = 80
    other = tmp_path / "other"
    other.mkdir()
    assert _write(other, wrong_nc).read_text().startswith("nc: 2\n")


# -- G17 -----------------------------------------------------------------------------------------------------------
def test_candidate_detail_evaluation_status_is_only_evaluated_or_not_evaluated(client, protocol, api_settings):
    req = _training_request(protocol, "g17")
    req["mode"] = "evaluate_only"
    req["validation_enabled"] = True
    created = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert created.status_code == 200, created.text
    rid, cid = created.json()["run_id"], created.json()["candidate_id"]
    before = client.get(f"/api/v1/runs/{rid}/candidates/{cid}", headers=auth()).json()
    assert before["evaluation_status"] == "NOT_EVALUATED" and contract_errors("CandidateDetail", before) == []
    conn = connect(api_settings, init=False)
    try:
        run, cand = repo.get_run(conn, rid), repo.get_candidate(conn, cid)
        job = repo.list_jobs(conn, rid)[0]
        job["attempt"] = 1
        ev = {"ap": 0.25, "recall": 0.4, "latency_ms": 5.0, "size_mb": 12.0, "latency_source": "measured",
              "latency_scope": "forward_and_postprocess", "device": "host-gpu-0", "precision": "FP16",
              "evaluation_hash": "fixture-eval", "split_hash": run["resolved_protocol"]["dataset"]["search_validation_manifest_hash"],
              "checkpoint_path": "/fixture/official.pt", "checkpoint_sha256": "fixture-checksum",
              "architecture_hash": cand.get("architecture_hash") or "fixture-structure", "batch": 1,
              "requested_input_shape": [64, 64], "resolved_input_shape": [64, 64], "epochs": 0}
        with tx(conn):
            conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (job["id"],))
            engine.job_finished(conn, job, JobResult(job_id=job["id"], status="SUCCEEDED", outputs={"epochs_done": 0, "evaluation": ev}))
    finally:
        conn.close()
    after = client.get(f"/api/v1/runs/{rid}/candidates/{cid}", headers=auth()).json()
    assert after["evaluation_status"] == "EVALUATED" and contract_errors("CandidateDetail", after) == []
    assert {before["evaluation_status"], after["evaluation_status"]} <= {"EVALUATED", "NOT_EVALUATED"}
