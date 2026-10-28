"""DD-A66 (C08 EVAL_1 D4/D5): retention policies are APPLIED to remote runs through the prune round trip - no more forced
``keep_all`` - and two protections hold whatever the policy says: the candidate's latest full-state checkpoint and every
checkpoint an evaluation row references are never pruned.

SYNTHETIC: a Baseline run (stages at epochs 1 and 3) is created through the gateway (``client``) and trained by a runner
simulated over the core app (``runner_sim.RunnerSim``, stub handlers in-process: tiny ``full_eNNN.json`` payloads). Like
the real training handler (``mbnas_runner/training/handlers.py``: ``checkpoint_path = rung_result.
rung_end_training_ckpt``), the simulated runner commits evaluations that reference the stage-end FULL state (the stub
alone references its eval snapshot file); one scenario keeps the stub's own reference as the contrast."""
from __future__ import annotations

import os
from pathlib import Path

import harness
import pytest
import runner_sim as rs

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine, retention
from mbnas_runner import reports
from mbnas_runner.envelope import run_dir_for


@pytest.fixture
def conn(api_settings):
    c = connect(api_settings, init=False)
    yield c
    c.close()


@pytest.fixture
def sim(core_client, api_settings, stub, tmp_path):
    """Runner R1 (runner principal issued on the core DB) simulated over the core app; registered."""
    from mbnas_core.runner_api import auth

    rs.write_synthetic_probe(api_settings.output_root)  # claim-time baseline resolution (see runner_sim)
    c = connect(api_settings)
    try:
        _, token = auth.create_principal(c, "runner", runner_id="R1")
    finally:
        c.close()
    rs.runtime_manifest()
    runner = rs.RunnerSim(core_client, token, "R1", tmp_path / "runner-R1")
    runner.register()
    return runner


def _proto(make_dataset, tmp_roots, policy: str, name: str):
    ds = make_dataset(name, counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=(), baselines=("yolo11n",),
                                   targets=(1, 3))
    proto["console"]["retention"] = {"full_state": policy}
    return proto


def _requested(conn, run_id: str) -> dict[int, dict]:
    eps = {r["id"]: r["epoch"] for r in repo.list_checkpoints(conn, run_id)}
    return {eps[r["checkpoint_id"]]: dict(r) for r in conn.execute(
        "SELECT * FROM prune_requests WHERE run_id = ?", (run_id,))}


def _full(conn, run_id: str) -> dict[int, dict]:
    return {r["epoch"]: r for r in repo.list_checkpoints(conn, run_id) if r["kind"] == "training_full"}


def _run_job(sim, conn, run_id: str, *, reference_full_state: bool) -> list[tuple[int, list[int]]]:
    """Claim -> start -> stub run -> E6 one checkpoint at a time (recording which epochs have a prune request after each
    registration) -> E7/E8/E9. With ``reference_full_state`` the committed evaluation references the stage-end full
    state like the real training handler does."""
    lease = sim.claim()
    assert lease is not None and lease.run_id == run_id
    sim.start(lease)
    result, lines = sim.execute_stub(lease)
    spec = lease.envelope["job"]["spec"]
    after: list[tuple[int, list[int]]] = []
    last = None
    for msg in lines:
        if msg["type"] != "checkpoint":
            continue
        report, detail = reports.verify_checkpoint(msg["payload"], sim.run_dir(lease), spec.get("candidate_id"),
                                                   spec.get("rung"))
        assert report is not None, detail
        ck_id = f"ck-{lease.job_id[-8:]}-{report['epoch']}"
        r = sim.checkpoint(lease, {"checkpoint_id": ck_id, **report})
        assert r.status_code == 200, r.text
        last = report
        after.append((report["epoch"], sorted(_requested(conn, run_id))))
    if reference_full_state and last is not None:
        ev = result.outputs["evaluation"]
        ev["checkpoint_path"], ev["checkpoint_sha256"] = last["uri"], last["sha256"]
    result, ids = sim.preregister(lease, result)
    commit = sim.result(lease, sim.commit_body(lease, result, artifact_ids=ids))
    assert commit.status_code == 200, commit.text
    assert sim.ack(lease).status_code == 204
    return after


@pytest.mark.parametrize("policy,expected", [("last_rung_end_only", [2]), ("rung_end_only", [2]), ("keep_all", [])])
def test_policies_apply_to_remote_runs_with_both_protections(client, make_dataset, tmp_roots, sim, conn, policy,
                                                              expected):
    """Stage ends at epochs 1 and 3; the L0 evaluation references epoch 1, the L1 evaluation epoch 3 (real-handler
    shape). Epoch 2 (no stage end, no reference) is requested only once it is no longer the latest; epoch 1 is protected
    by its evaluation reference although last_rung_end_only would delete it; epoch 3 is the latest. keep_all plans
    nothing. Nothing is deleted by the core: the runner's files are all still there and every row stays PRESENT until a
    runner reports (no REMOTE_RETENTION_FORCED override any more)."""
    run_id, body = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, policy, f"rp-{policy}"),
                                            key=f"remote-{policy}")
    assert body["resolved"]["console"]["retention"] == {"full_state": policy}
    first = _run_job(sim, conn, run_id, reference_full_state=True)   # L0: epoch 1
    second = _run_job(sim, conn, run_id, reference_full_state=True)  # L1: epochs 2, 3 (resumed from epoch 1)
    assert [e for e, _ in first] == [1] and [e for e, _ in second] == [2, 3]
    assert first == [(1, [])]
    assert second[0] == (2, [])  # epoch 2 is the latest full state when it is registered: never requested then
    assert second[1] == (3, expected)
    run = repo.get_run(conn, run_id)
    cand = repo.list_candidates(conn, run_id)[0]
    assert retention.is_remote_run(conn, run_id)
    reqs = _requested(conn, run_id)
    assert sorted(reqs) == expected  # job_finished + sweeper planning after both commits added nothing
    rows = _full(conn, run_id)
    held = retention.runner_held(conn, run_id)
    protected = retention.protected_checkpoints(conn, run_id, cand["id"], list(rows.values()), held)
    assert protected[rows[3]["id"]] == retention.LATEST_FULL_STATE
    assert protected[rows[1]["id"]] == retention.EVALUATION_REFERENCED
    if policy == "last_rung_end_only":  # the policy itself would delete epoch 1: only the protection keeps it
        planned = retention.plan(engine.RunContext(run), cand, list(rows.values()), policy,
                                 exists=lambda r: r["id"] in held)
        assert sorted(r["epoch"] for r in planned) == [1, 2]
    for ep, req in reqs.items():
        assert (req["state"], req["policy"], req["runner_id"]) == ("PENDING", policy, "R1")
    assert {r["state"] for r in rows.values()} == {"PRESENT"}
    assert all(Path(r["path"]).is_file() for r in rows.values())  # the core deleted nothing on the runner's disk
    with tx(conn):  # the sweeper path (settings.remote_retention_policy = keep_all is passed) no longer forces keep_all
        report = retention.apply_for_candidate(conn, run, cand["id"], remote_policy="keep_all", force=True)
    assert report["remote"] is True and report["pruned"] == [] and report["policy"] == policy
    assert report["requested"] == []  # already requested: re-planning never duplicates a request
    assert all(Path(r["path"]).is_file() for r in rows.values())
    logs = [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 10_000) if e["type"] == "log"]
    assert not any(p.get("reason_code") == "REMOTE_RETENTION_FORCED" for p in logs)
    assert any(p.get("reason_code") == "REMOTE_PRUNE_REQUESTED" for p in logs) == bool(expected)


def test_without_the_evaluation_reference_the_policy_requests_epoch_one_too(client, make_dataset, tmp_roots, sim, conn):
    """Contrast to the test above: the stub's own evaluations reference its eval snapshot file, not a full state, so
    last_rung_end_only requests epochs 1 AND 2 (the latest, epoch 3, still never)."""
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only", "rp-noref"),
                                         key="remote-noref")
    _run_job(sim, conn, run_id, reference_full_state=False)
    after = _run_job(sim, conn, run_id, reference_full_state=False)
    assert after == [(2, []), (3, [1, 2])]
    assert sorted(_requested(conn, run_id)) == [1, 2]


@pytest.mark.parametrize("status", sorted(retention.KEEP_EVERYTHING_STATUSES))
def test_keep_everything_candidates_and_cancelled_runs_get_no_requests(client, make_dataset, tmp_roots, sim, conn,
                                                                         status):
    """CANCELLED / RESUME_BLOCKED / ELIGIBLE_BUDGET_EXHAUSTED / PAUSED candidates and CANCEL_REQUESTED / CANCELLED runs keep
    everything (audit, later resume): planning opens no request for them."""
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "last_rung_end_only", "rp-keep"),
                                         key=f"remote-keep-{status}")
    _run_job(sim, conn, run_id, reference_full_state=False)
    _run_job(sim, conn, run_id, reference_full_state=False)
    with tx(conn):
        conn.execute("DELETE FROM prune_requests WHERE run_id = ?", (run_id,))  # SYNTHETIC reset of the plan
        cand = repo.list_candidates(conn, run_id)[0]
        repo.update_candidate(conn, cand["id"], candidate_status=status)
        assert retention.request_remote_prunes(conn, repo.get_run(conn, run_id), cand["id"]) == []
        repo.update_candidate(conn, cand["id"], candidate_status="SEARCH_COMPLETE")
        conn.execute("UPDATE runs SET status = 'CANCEL_REQUESTED' WHERE id = ?", (run_id,))
        assert retention.request_remote_prunes(conn, repo.get_run(conn, run_id), cand["id"]) == []
        conn.execute("UPDATE runs SET status = 'COMPLETED' WHERE id = ?", (run_id,))
        made = retention.request_remote_prunes(conn, repo.get_run(conn, run_id), cand["id"])
    assert sorted(m["epoch"] for m in made) == [1, 2]  # the same candidate, once it may be pruned again


def test_apply_for_run_on_a_remote_run_requests_and_deletes_nothing_itself(client, make_dataset, tmp_roots, sim,
                                                                           conn, api_settings):
    """The CLI / sweeper entry point (``apply_for_run``, policy override allowed) opens requests for a remote run and
    never unlinks a runner file itself; ``python -m mbnas_core.orchestration.retention`` reports them."""
    run_id, _ = harness.validate_and_run(client, _proto(make_dataset, tmp_roots, "keep_all", "rp-cli"),
                                         key="remote-cli")
    _run_job(sim, conn, run_id, reference_full_state=False)
    _run_job(sim, conn, run_id, reference_full_state=False)
    assert _requested(conn, run_id) == {}
    with tx(conn):
        out = retention.apply_for_run(conn, run_id, policy="rung_end_only")
    assert out["pruned"] == 0 and out["requested"] == 1 and out["bytes_freed"] == 0
    assert sorted(_requested(conn, run_id)) == [2]
    assert all(Path(r["path"]).is_file() for r in _full(conn, run_id).values())
    assert {r["state"] for r in _full(conn, run_id).values()} == {"PRESENT"}


def test_evaluation_reference_matching(monkeypatch):
    """Unit: a checkpoint is evaluation-referenced by its stored path, by its runner relpath at the end of a reported
    path, by its digest (plain, ``sha256:``-prefixed or per checkpoint kind) or by ``rung_result.rung_end_training_ckpt``;
    the latest live full state is protected even without any evaluation."""
    h = {i: f"{i}" * 64 for i in "abcdef"}
    rows = [{"id": f"ck-{i}", "kind": "training_full", "epoch": i, "created_at": f"t{i}", "state": "PRESENT",
             "path": f"/w/runs/r/candidates/c/full_e{i:03d}.json", "sha256": h["abcdef"[i - 1]]} for i in range(1, 7)]
    held = {f"ck-{i}": {"relpath": f"candidates/c/full_e{i:03d}.json"} for i in range(1, 7)}
    evs = [{"row": {"checkpoint_path": "/w/runs/r/candidates/c/full_e001.json", "checkpoint_sha256": None}},
           {"row": {"checkpoint_path": "/elsewhere/mount/runs/r/candidates/c/full_e002.json"}},
           {"row": {"checkpoint_path": None, "checkpoint_sha256": "sha256:" + h["c"]}},
           {"row": {"raw_metrics": {"checkpoint_sha256_by_kind": {"training_full": h["d"], "eval_ema": "0" * 64}}}},
           {"row": {"raw_metrics": {"rung_result": {"rung_end_training_ckpt": "/w/runs/r/candidates/c/full_e005.json"}}}}]
    monkeypatch.setattr(repo, "list_evaluations", lambda conn, run_id, candidate_id=None, **k: evs)
    out = retention.protected_checkpoints(None, "r", "c", rows, held)
    assert out == {"ck-6": retention.LATEST_FULL_STATE, **{f"ck-{i}": retention.EVALUATION_REFERENCED for i in range(1, 6)}}
    monkeypatch.setattr(repo, "list_evaluations", lambda conn, run_id, candidate_id=None, **k: [])
    assert retention.protected_checkpoints(None, "r", "c", rows, held) == {"ck-6": retention.LATEST_FULL_STATE}
    pruned = [{**r, "state": "PRUNED"} if r["id"] == "ck-6" else r for r in rows]
    assert retention.protected_checkpoints(None, "r", "c", pruned, held) == {"ck-5": retention.LATEST_FULL_STATE}
    assert retention.prune_request_id("ck-1") == retention.prune_request_id("ck-1") != retention.prune_request_id("ck-2")
    assert os.path.basename(retention.prune_request_id("ck-1")).startswith("prq-")
