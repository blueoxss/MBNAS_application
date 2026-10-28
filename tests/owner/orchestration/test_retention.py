"""Full-state retention policies (orchestration.retention) through the worker and the CLI, SYNTHETIC stub handlers.

The stub writes one tiny ``full_e<NNN>.json`` per epoch as its "full state" payload; the policy deletes payloads,
records ``state='PRUNED'`` on the checkpoint rows and candidate detail renders PRUNED (not MISSING).
"""
from __future__ import annotations

import os
from pathlib import Path

import harness
import pytest
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine, retention


@pytest.fixture
def conn(api_settings):
    c = connect(api_settings, init=False)
    yield c
    c.close()


def _proto(make_dataset, tmp_roots, policy: str | None, *, targets=(1, 3), score_ge=0.0, name="ret"):
    ds = make_dataset(name, counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1, baselines=(),
                                   targets=targets, score_ge=score_ge)
    proto["console"].pop("baseline_budget_gpu_hours", None)
    if policy is None:
        proto["console"].pop("retention", None)
    else:
        proto["console"]["retention"] = {"full_state": policy}
    return proto


def _full_rows(conn, run_id, cid):
    return {r["epoch"]: r for r in repo.list_checkpoints(conn, run_id, candidate_id=cid, kind="training_full")}


def test_last_rung_end_only_keeps_the_last_rung_end_state(client, make_dataset, tmp_roots, inline_worker, conn):
    run_id, body = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only"))
    assert body["resolved"]["console"]["retention"] == {"full_state": "last_rung_end_only"}
    harness.drain(inline_worker)
    cands = repo.list_candidates(conn, run_id)
    assert {c["candidate_status"] for c in cands} == {"SEARCH_COMPLETE"}
    for cand in cands:
        rows = _full_rows(conn, run_id, cand["id"])
        assert sorted(rows) == [1, 2, 3]
        assert [rows[e]["state"] for e in (1, 2, 3)] == ["PRUNED", "PRUNED", "PRESENT"]
        assert not os.path.exists(rows[1]["path"]) and not os.path.exists(rows[2]["path"]) and os.path.exists(rows[3]["path"])
        assert rows[1]["prune"]["policy"] == "last_rung_end_only" and rows[1]["prune"]["deleted"][0]["bytes"] > 0
        assert os.path.exists(Path(rows[3]["path"]).with_name("eval_ema_e003.json"))  # eval snapshots are kept
        detail = client.get(f"/api/v1/runs/{run_id}/candidates/{cand['id']}", headers=auth()).json()
        assert contract_errors("CandidateDetail", detail) == []
        states = {c["epoch"]: c["state"] for c in detail["checkpoints"] if c["kind"] == "training_full"}
        assert states == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"}  # PRUNED, never MISSING
    logs = [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 10_000) if e["type"] == "log"]
    assert any("retention last_rung_end_only" in p["message"] for p in logs)


def test_rejected_candidate_keeps_its_last_reached_rung_end(client, make_dataset, tmp_roots, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only", score_ge=0.99))
    harness.drain(inline_worker)
    for cand in repo.list_candidates(conn, run_id):
        assert cand["candidate_status"] == "REJECTED_BY_GATE"
        rows = _full_rows(conn, run_id, cand["id"])
        assert sorted(rows) == [1] and rows[1]["state"] == "PRESENT" and os.path.exists(rows[1]["path"])


def test_rung_end_only_and_keep_all(client, make_dataset, tmp_roots, inline_worker, conn):
    rid, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "rung_end_only"), key="re")
    harness.drain(inline_worker)
    for cand in repo.list_candidates(conn, rid):
        rows = _full_rows(conn, rid, cand["id"])
        assert [rows[e]["state"] for e in (1, 2, 3)] == ["PRESENT", "PRUNED", "PRESENT"]  # epoch 2 is no rung end
    kid, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "keep_all", name="keep"), key="ka")
    harness.drain(inline_worker)
    rows = [r for r in repo.list_checkpoints(conn, kid) if r["kind"] == "training_full"]
    assert rows and {r["state"] for r in rows} == {"PRESENT"} and all(os.path.exists(r["path"]) for r in rows)


def test_server_default_applies_when_the_protocol_has_no_retention(client, make_dataset, tmp_roots, inline_worker, conn):
    run_id, body = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, None))
    assert body["resolved"]["console"]["retention"] == {"full_state": "keep_all"}  # MBNAS_FULL_STATE_RETENTION default
    harness.drain(inline_worker)
    assert {r["state"] for r in repo.list_checkpoints(conn, run_id)} == {"PRESENT"}


def test_missing_vs_pruned_and_trainer_reconciliation(client, make_dataset, tmp_roots, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "keep_all"))
    harness.drain(inline_worker)
    cand = repo.list_candidates(conn, run_id)[0]
    rows = _full_rows(conn, run_id, cand["id"])
    os.unlink(rows[2]["path"])  # what the stage trainer's own prune does to a non-rung-end intermediate epoch
    os.unlink(rows[3]["path"])  # an unexplained loss: must read MISSING, never PRUNED
    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{cand['id']}", headers=auth()).json()
    states = {c["epoch"]: c["state"] for c in detail["checkpoints"] if c["kind"] == "training_full"}
    assert states == {1: "PRESENT", 2: "MISSING", 3: "MISSING"}
    with tx(conn):
        report = retention.apply_for_candidate(conn, repo.get_run(conn, run_id), cand["id"])
    assert report["pruned"] == [] and len(report["trainer_pruned"]) == 1
    rows = _full_rows(conn, run_id, cand["id"])
    assert rows[2]["state"] == "PRUNED" and rows[2]["prune"]["reason"] == retention.TRAINER_PRUNE_REASON
    assert rows[3]["state"] == "PRESENT"  # newest/rung-end loss is not explained by the trainer: stays MISSING in views
    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{cand['id']}", headers=auth()).json()
    assert {c["epoch"]: c["state"] for c in detail["checkpoints"] if c["kind"] == "training_full"} == {
        1: "PRESENT", 2: "PRUNED", 3: "MISSING"}


def test_cancelled_candidates_keep_everything(client, make_dataset, tmp_roots, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only"))
    assert inline_worker.tick()  # proposals
    assert inline_worker.tick()  # first L0 training
    client.post(f"/api/v1/runs/{run_id}/cancel", headers=auth())
    harness.drain(inline_worker)
    assert repo.get_run(conn, run_id)["status"] == "CANCELLED"
    with tx(conn):
        out = retention.apply_for_run(conn, run_id)
    assert out["pruned"] == 0
    assert {r["state"] for r in repo.list_checkpoints(conn, run_id)} == {"PRESENT"}


def test_cli_applies_a_policy_after_the_run(client, make_dataset, tmp_roots, inline_worker, conn, api_settings,
                                            monkeypatch, capsys):
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "keep_all"))
    harness.drain(inline_worker)
    monkeypatch.setenv("MBNAS_CORE_DB_PATH", api_settings.db_path)  # the CLI loads CoreSettings (DD-A13 prefix)
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", api_settings.output_root)
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOTS", ",".join(api_settings.output_roots))
    import json

    def report() -> dict:
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])[0]

    capsys.readouterr()
    assert retention.main(["--run-id", run_id, "--policy", "last_rung_end_only"]) == 0
    first = report()
    assert first["pruned"] == 4 and first["policy"] == "last_rung_end_only"  # two candidates x epochs 1 and 2
    states = sorted((r["epoch"], r["state"]) for r in repo.list_checkpoints(conn, run_id) if r["kind"] == "training_full")
    assert states == [(1, "PRUNED"), (1, "PRUNED"), (2, "PRUNED"), (2, "PRUNED"), (3, "PRESENT"), (3, "PRESENT")]
    assert retention.main(["--run-id", run_id, "--policy", "last_rung_end_only"]) == 0  # idempotent
    assert report()["pruned"] == 0


def test_plan_deletes_rfdetr_native_companions_and_never_leaves_the_run_dir(tmp_path, settings):
    """A pruned RF-DETR full state also removes its referenced ``native_e{E}.ckpt``; paths outside are untouched."""
    run = {"id": "run-x", "resolved_protocol": {"console": {"paths": {"output_root": str(tmp_path)}}, "rungs": []}}
    ckpt = tmp_path / "run-x" / "candidates" / "c" / "ckpt"
    ckpt.mkdir(parents=True)
    files = {n: ckpt / n for n in ("full_e0002.pt", "native_e0002.ckpt", "full_e0002.pt.sha256.json")}
    for p in files.values():
        p.write_bytes(b"x" * 10)
    outside = tmp_path / "elsewhere.pt"
    outside.write_bytes(b"y")
    deleted, freed = retention._delete({"path": str(files["full_e0002.pt"])}, os.path.realpath(tmp_path / "run-x"))
    assert sorted(Path(d["path"]).name for d in deleted) == ["full_e0002.pt", "native_e0002.ckpt"] and freed == 20
    assert files["full_e0002.pt.sha256.json"].exists()  # sidecars are kept
    assert retention._delete({"path": str(outside)}, os.path.realpath(tmp_path / "run-x")) == ([], 0)
    assert outside.exists()


def test_final_complete_candidates_follow_the_policy(client, protocol, inline_worker, conn, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "measured")
    parent, _ = harness.validate_and_run(client, protocol, key="fin-ret")
    harness.drain(inline_worker)
    r = client.post(f"/api/v1/runs/{parent}/final-retrain", json={"idempotency_key": "x", "budget_gpu_hours": 0.25},
                    headers=auth())
    child = r.json()["run_id"]
    harness.drain(inline_worker)
    for cand in repo.list_candidates(conn, child):
        assert cand["candidate_status"] == "FINAL_COMPLETE"
        rows = _full_rows(conn, child, cand["id"])
        assert [rows[e]["state"] for e in sorted(rows)] == ["PRUNED", "PRESENT"]  # final 2 epochs: keep epoch 2
    ctx = engine.context(conn, child)
    assert all(retention.is_terminal(ctx, c) for c in repo.list_candidates(conn, child))


def test_resume_check_refuses_a_pruned_full_state(client, make_dataset, tmp_roots, inline_worker, conn):
    """A PRUNED row is never a resume source: the queued later stage is FULL_STATE_PRUNED (never a scratch restart)."""
    from mbnas_core.orchestration import state

    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "keep_all"))
    harness.drain(inline_worker)
    cand = repo.list_candidates(conn, run_id)[0]
    rows = _full_rows(conn, run_id, cand["id"])
    with tx(conn):
        repo.mark_checkpoint_pruned(conn, rows[3]["id"], prune={"policy": "test"})
    job = {"candidate_id": cand["id"], "rung": "L1", "kind": "CANDIDATE_TRAIN"}
    assert state._verify_full_state(conn, repo.get_run(conn, run_id), job) == "FULL_STATE_PRUNED"


def test_last_rung_end_only_never_removes_the_resume_source_of_a_live_candidate(client, make_dataset, tmp_roots,
                                                                                inline_worker, conn):
    """S5-F1: retention applied after every job (and once more per tick here) never prunes the latest full state of a
    candidate that may still train — its next stage resumes from it."""
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only"))
    live_checked = 0
    for _ in range(200):
        progressed = inline_worker.tick()
        with tx(conn):
            retention.apply_for_run(conn, run_id)
        ctx = engine.context(conn, run_id)
        for cand in repo.list_candidates(conn, run_id):
            rows = sorted(_full_rows(conn, run_id, cand["id"]).values(), key=lambda r: int(r["epoch"]))
            if not rows or retention.is_terminal(ctx, cand):
                continue
            live_checked += 1
            assert rows[-1]["state"] == "PRESENT" and os.path.exists(rows[-1]["path"]), (cand["candidate_status"], rows[-1])
        if not progressed:
            break
    assert live_checked > 0  # live candidates with a recorded full state were observed
    assert {c["candidate_status"] for c in repo.list_candidates(conn, run_id)} == {"SEARCH_COMPLETE"}
