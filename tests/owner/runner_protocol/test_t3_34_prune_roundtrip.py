"""T3-34 (REMOTE_PROTOCOL_DESIGN section 14; WP-5 section 2 R11; DD-A66): the retention prune round trip against the real
core app. A Baseline run with one stage (3 epochs) and ``console.retention.full_state = last_rung_end_only`` is trained by
the stub handlers on runner R1 (SYNTHETIC: tiny ``full_eNNN.json`` payloads, no model): every E6 registration plans the
run's policy, the older full states get ``prune_requests`` rows (never the latest one), the heartbeat hands them out as
``control.prune`` orders (at most once per lease), the runner deletes the files (``mbnas_runner.daemon.
execute_prune_order``, the daemon's own code) and reports E11 - and only then are the checkpoint rows PRUNED.

Also: E11 refusals (unknown request, another runner, other bytes, inconsistent body), the already-absent finding, the
delivery-time re-check of the protections, re-delivery in a later lease, and the real ``RunnerDaemon`` doing all of it
on its own (heartbeat thread + last beat + prune worker), with the gateway store copies left untouched."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import harness
import pytest

from mbnas_contracts.runner_protocol import HeartbeatResponse, PruneAck, PruneOrder
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration import retention
from mbnas_runner.daemon import execute_prune_order
from mbnas_runner.envelope import run_dir_for
from mbnas_runner.journal import PruneJournal
from mbnas_server.api.contract_check import contract_errors

pytestmark = pytest.mark.runner_protocol

PUBLIC_PRUNE_KEYS = {"policy", "reason", "deleted", "bytes_freed", "kept", "candidate_status", "note"}


def _proto(base_proto, policy: str = "last_rung_end_only", targets=(3,)):
    proto = base_proto(targets=targets)
    proto["console"]["retention"] = {"full_state": policy}
    return proto


def _full(db, run_id: str) -> dict[int, dict]:
    return {r["epoch"]: r for r in repo.list_checkpoints(db, run_id) if r["kind"] == "training_full"}


def _requests(db, run_id: str) -> dict[str, dict]:
    return {r["checkpoint_id"]: dict(r) for r in db.execute("SELECT * FROM prune_requests WHERE run_id = ?", (run_id,))}


def _locations(db, ck_id: str) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM artifact_locations WHERE object_kind = 'checkpoint' AND "
                                        "object_id = ?", (ck_id,))]


def _train(runner_sim, lease):
    """start -> stub run (3 epochs) -> E5/E6 like the daemon; returns (result, lines)."""
    runner_sim.start(lease)
    result, lines = runner_sim.execute_stub(lease)
    runner_sim.report_lines(lease, lines)
    return result, lines


def _finish(runner_sim, lease, result) -> None:
    """E7 + E8 + E9 for an attempt whose child already ran."""
    result, ids = runner_sim.preregister(lease, result)
    commit = runner_sim.result(lease, runner_sim.commit_body(lease, result, artifact_ids=ids))
    assert commit.status_code == 200, commit.text
    assert runner_sim.ack(lease).status_code == 204


def _orders(runner_sim, lease) -> list[PruneOrder]:
    r = runner_sim.heartbeat(lease)
    assert r.status_code == 200, r.text
    return list(HeartbeatResponse.model_validate(r.json()).control.prune or [])


def test_prune_request_round_trip_marks_checkpoint_pruned_only_after_runner_report(core, runner_sim, base_proto, db):
    """T3-34: PRESENT before the runner's report, PRUNED + prune record after it (design section 14 row T3-34)."""
    run_id, body = harness.validate_and_run(core, _proto(base_proto))
    assert body["resolved"]["console"]["retention"] == {"full_state": "last_rung_end_only"}
    runner_sim.register()
    lease = runner_sim.claim()
    result, _ = _train(runner_sim, lease)
    rows = _full(db, run_id)
    assert sorted(rows) == [1, 2, 3]
    reqs = _requests(db, run_id)
    # planned at E6: the older full states only; the latest (epoch 3, the resume source) is never requested
    assert set(reqs) == {rows[1]["id"], rows[2]["id"]}
    for ep in (1, 2):
        req = reqs[rows[ep]["id"]]
        loc = _locations(db, rows[ep]["id"])[0]
        assert (req["state"], req["runner_id"], req["policy"], req["reason"]) == (
            "PENDING", "R1", "last_rung_end_only", "RETENTION_LAST_RUNG_END_ONLY")
        assert (req["relpath"], req["sha256"], req["bytes"]) == (loc["relpath"], rows[ep]["sha256"], rows[ep]["bytes"])
        assert req["id"] == retention.prune_request_id(rows[ep]["id"])
    base = run_dir_for(runner_sim.work_root, run_id)
    assert {e: r["state"] for e, r in rows.items()} == {1: "PRESENT", 2: "PRESENT", 3: "PRESENT"}

    orders = _orders(runner_sim, lease)  # E4: handed out ...
    assert sorted(o.checkpoint_id for o in orders) == sorted(reqs)
    assert _orders(runner_sim, lease) == []  # ... at most once per lease
    for o in orders:
        req = _requests(db, run_id)[o.checkpoint_id]
        assert (req["state"], req["delivered_job_id"], req["delivered_attempt"], req["deliveries"]) == (
            "DELIVERED", lease.job_id, lease.attempt, 1)
        assert (o.request_id, o.run_id, o.relpath, o.sha256, o.bytes) == (
            req["id"], run_id, req["relpath"], req["sha256"], req["bytes"])
    # delivery is not deletion: still PRESENT, the runner still holds the files
    assert {e: r["state"] for e, r in _full(db, run_id).items()} == {1: "PRESENT", 2: "PRESENT", 3: "PRESENT"}
    assert all((base / o.relpath).is_file() for o in orders)

    epoch_of = {r["id"]: e for e, r in rows.items()}
    for o in orders:
        size = (base / o.relpath).stat().st_size
        report, refusal = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
        assert refusal is None and report["already_absent"] is False and report["bytes_freed"] == size == o.bytes
        assert not (base / o.relpath).exists()  # the runner deleted it, inside its own work root
        assert _full(db, run_id)[epoch_of[o.checkpoint_id]]["state"] == "PRESENT"  # until the report arrives
        r = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)
        assert r.status_code == 200, r.text
        assert PruneAck.model_validate(r.json()).model_dump() == {
            "checkpoint_id": o.checkpoint_id, "request_id": o.request_id, "state": "PRUNED", "replayed": False}
        again = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)  # a resent report changes nothing
        assert again.status_code == 200 and again.json()["replayed"] is True

    rows = _full(db, run_id)
    assert {e: r["state"] for e, r in rows.items()} == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"}
    for ep in (1, 2):
        prune = rows[ep]["prune"]
        assert (prune["policy"], prune["reason"], prune["remote"], prune["runner_id"], prune["already_absent"]) == (
            "last_rung_end_only", "RETENTION_LAST_RUNG_END_ONLY", True, "R1", False)
        assert prune["request_id"] == retention.prune_request_id(rows[ep]["id"]) and rows[ep]["pruned_at"]
        assert prune["bytes_freed"] == rows[ep]["bytes"] and prune["deleted"][0]["bytes"] == rows[ep]["bytes"]
        assert [loc["state"] for loc in _locations(db, rows[ep]["id"])] == ["PRUNED"]
        assert _requests(db, run_id)[rows[ep]["id"]]["state"] == "DONE"
        assert json.loads(_requests(db, run_id)[rows[ep]["id"]]["report_json"])["request_id"] == prune["request_id"]
    assert [loc["state"] for loc in _locations(db, rows[3]["id"])] == ["PRESENT"]
    latest_rel = _locations(db, rows[3]["id"])[0]["relpath"]
    assert (base / latest_rel).is_file()  # the latest full state was never touched

    # finish the job: the result commit plans again (job_finished + sweeper retention) - nothing new
    _finish(runner_sim, lease, result)
    assert set(_requests(db, run_id)) == {rows[1]["id"], rows[2]["id"]}
    assert _full(db, run_id)[3]["state"] == "PRESENT"
    logs = [e["payload"] for e in repo.get_events_after(db, run_id, 0, 10_000) if e["type"] == "log"]
    assert sum(1 for p in logs if p.get("reason_code") == "CHECKPOINT_PRUNED") == 2
    assert any(p.get("reason_code") == "REMOTE_PRUNE_REQUESTED" for p in logs)
    assert not any(p.get("reason_code") == "REMOTE_RETENTION_FORCED" for p in logs)  # keep_all forcing lifted

    # public candidate detail: PRUNED / PRUNED / PRESENT, the prune record projected to its public keys (no paths)
    cid = repo.get_job(db, lease.job_id)["candidate_id"]
    detail = core.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=harness.auth())
    assert detail.status_code == 200, detail.text
    doc = detail.json()
    assert contract_errors("CandidateDetail", doc) == []
    views = {c["epoch"]: c for c in doc["checkpoints"] if c["kind"] == "training_full"}
    assert {e: v["state"] for e, v in views.items()} == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"}
    for ep in (1, 2):
        assert set(views[ep]["prune"]) <= PUBLIC_PRUNE_KEYS
        assert all(set(item) <= {"bytes"} for item in views[ep]["prune"]["deleted"])
    assert "R1" not in json.dumps([views[1]["prune"], views[2]["prune"]])


def test_pruned_report_refusals_never_change_the_row(core, runner_sim, runner_sim2, base_proto, db, tokens):
    """E11 refusals: unknown request / another checkpoint's request (404 PRUNE_REQUEST_NOT_FOUND), a request addressed
    to another runner or a body naming another runner than the token (403 RUNNER_MISMATCH), other bytes or another
    location (409 PRUNE_REPORT_CONFLICT), an inconsistent body (422). Nothing changes; the honest report still works."""
    run_id, _ = harness.validate_and_run(core, _proto(base_proto))
    runner_sim.register()
    runner_sim2.register()
    lease = runner_sim.claim()
    _train(runner_sim, lease)
    o1, o2 = sorted(_orders(runner_sim, lease), key=lambda o: o.relpath)
    report, _ = execute_prune_order(runner_sim.work_root, o1.model_dump(mode="json"), runner_id="R1")
    cases = [
        (runner_sim, f"/checkpoints/{o1.checkpoint_id}/pruned", {**report, "request_id": "prq-unknown"}, 404,
         "PRUNE_REQUEST_NOT_FOUND"),
        (runner_sim, f"/checkpoints/{o2.checkpoint_id}/pruned", report, 404, "PRUNE_REQUEST_NOT_FOUND"),
        (runner_sim2, f"/checkpoints/{o1.checkpoint_id}/pruned", {**report, "runner_id": "R2"}, 403, "RUNNER_MISMATCH"),
        (runner_sim, f"/checkpoints/{o1.checkpoint_id}/pruned", {**report, "runner_id": "R2"}, 403, "RUNNER_MISMATCH"),
        (runner_sim, f"/checkpoints/{o1.checkpoint_id}/pruned",
         {**report, "sha256": "b" * 64, "removed": [{**report["removed"][0], "sha256": "b" * 64}]}, 409,
         "PRUNE_REPORT_CONFLICT"),
        (runner_sim, f"/checkpoints/{o1.checkpoint_id}/pruned",
         {**report, "relpath": o2.relpath, "removed": [{**report["removed"][0], "relpath": o2.relpath}]}, 409,
         "PRUNE_REPORT_CONFLICT"),
    ]
    for sim, path, body, status, code in cases:
        r = sim.post(path, body)
        assert r.status_code == status and r.json()["code"] == code, (path, body.get("runner_id"), r.text)
    bad = runner_sim.post(f"/checkpoints/{o1.checkpoint_id}/pruned", {**report, "already_absent": True})
    assert bad.status_code == 422, bad.text  # already_absent with removed files: inconsistent body
    assert {e: r["state"] for e, r in _full(db, run_id).items()} == {1: "PRESENT", 2: "PRESENT", 3: "PRESENT"}
    assert {r["state"] for r in _requests(db, run_id).values()} == {"DELIVERED"}
    ok = runner_sim.post(f"/checkpoints/{o1.checkpoint_id}/pruned", report)
    assert ok.status_code == 200 and ok.json()["replayed"] is False, ok.text
    assert _full(db, run_id)[1]["state"] == "PRUNED" and _full(db, run_id)[2]["state"] == "PRESENT"


def test_already_absent_payload_is_reported_and_recorded_as_such(core, runner_sim, base_proto, db):
    """The stage trainer pruned the payload before the order arrived (real ultralytics behaviour for non-rung-end
    epochs): the runner deletes nothing, reports ``already_absent`` and the row reads PRUNED with that finding. A file
    that now holds OTHER bytes under the same name is left alone and reported the same way."""
    run_id, _ = harness.validate_and_run(core, _proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    _train(runner_sim, lease)
    o1, o2 = sorted(_orders(runner_sim, lease), key=lambda o: o.relpath)
    base = run_dir_for(runner_sim.work_root, run_id)
    (base / o1.relpath).unlink()  # gone before the order (the trainer's own prune)
    (base / o2.relpath).write_bytes(b"other bytes written later under the same name")
    for o in (o1, o2):
        report, refusal = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
        assert refusal is None and report["already_absent"] is True and report["removed"] == []
        r = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)
        assert r.status_code == 200, r.text
    assert (base / o2.relpath).read_bytes() == b"other bytes written later under the same name"  # kept
    rows = _full(db, run_id)
    for ep in (1, 2):
        assert rows[ep]["state"] == "PRUNED" and rows[ep]["prune"]["already_absent"] is True
        assert rows[ep]["prune"]["bytes_freed"] == 0 and rows[ep]["prune"]["deleted"] == []
        assert "already gone" in rows[ep]["prune"]["note"]


def test_orders_are_rechecked_at_delivery_and_redelivered_only_in_a_later_lease(core, runner_sim, base_proto, db):
    """A request whose checkpoint became protected (now the latest live full state) or was pruned otherwise is
    CANCELLED at delivery, never handed out; an order delivered in one lease and never reported comes again in the
    runner's NEXT lease (a job of another run), exactly once there."""
    run_a, _ = harness.validate_and_run(core, _proto(base_proto), key="t334-a")
    run_b, _ = harness.validate_and_run(core, _proto(base_proto), key="t334-b")
    runner_sim.register()
    lease_a = runner_sim.claim()
    assert lease_a.run_id == run_a
    result_a, _ = _train(runner_sim, lease_a)
    rows = _full(db, run_a)
    with tx(db):  # SYNTHETIC: epochs 2 and 3 recorded as pruned by someone else -> epoch 1 is now the latest live state
        for ep in (2, 3):
            repo.mark_checkpoint_pruned(db, rows[ep]["id"], prune={"policy": "test", "reason": "SYNTHETIC"})
    assert _orders(runner_sim, lease_a) == []
    reqs = _requests(db, run_a)
    assert (reqs[rows[1]["id"]]["state"], reqs[rows[1]["id"]]["cancel_reason"]) == ("CANCELLED", "LATEST_FULL_STATE")
    assert (reqs[rows[2]["id"]]["state"], reqs[rows[2]["id"]]["cancel_reason"]) == ("CANCELLED", "ALREADY_PRUNED")
    assert all((run_dir_for(runner_sim.work_root, run_a) / r["relpath"]).is_file() for r in reqs.values())
    _finish(runner_sim, lease_a, result_a)

    # run B: its orders are delivered in lease B1 (job of run B) and never reported ...
    lease_b = runner_sim.claim()
    assert lease_b.run_id == run_b
    result_b, _ = _train(runner_sim, lease_b)
    first = _orders(runner_sim, lease_b)
    assert len(first) == 2 and _orders(runner_sim, lease_b) == []
    _finish(runner_sim, lease_b, result_b)
    # ... so the runner's next lease (a third run) carries them once more, once
    run_c, _ = harness.validate_and_run(core, _proto(base_proto, policy="keep_all"), key="t334-c")
    lease_c = runner_sim.claim()
    assert lease_c.run_id == run_c
    runner_sim.start(lease_c)
    again = _orders(runner_sim, lease_c)
    assert sorted(o.request_id for o in again) == sorted(o.request_id for o in first)
    assert _orders(runner_sim, lease_c) == []
    for req in _requests(db, run_b).values():
        assert (req["state"], req["deliveries"], req["delivered_job_id"]) == ("DELIVERED", 2, lease_c.job_id)
    for o in again:
        report, _ = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
        assert runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report).status_code == 200
    assert {e: r["state"] for e, r in _full(db, run_b).items()} == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"}
    assert _requests(db, run_c) == {}  # keep_all: nothing planned


def test_real_daemon_prunes_on_its_own_and_keeps_the_store_copies(core, daemon_factory, base_proto, db, tmp_path,
                                                                   monkeypatch):
    """The real RunnerDaemon (heartbeat thread, last beat before the commit, prune worker; job child = real
    ``python -m mbnas_runner.child`` with the stub handlers) runs the 3-epoch job: the older full states are deleted on
    its disk and reported, the rows read PRUNED, the latest stays; the journal holds each report with the core's ack;
    the gateway store copies (local transfer) of the pruned checkpoints are untouched."""
    monkeypatch.setattr("mbnas_runner.gpu.device_info", lambda index: {
        "id": f"host-gpu-{index}", "index": index, "uuid": None, "name": None, "memory_total_mb": None})  # no NVML
    run_id, _ = harness.validate_and_run(core, _proto(base_proto))
    store = tmp_path / "gateway" / "artifacts"
    d = daemon_factory(artifact_transfer="local", artifact_store_dir=str(store))
    assert d.run(once=True) == 0
    assert d.wait_prunes(30)
    (job,) = repo.list_jobs(db, run_id)
    assert job["status"] == "SUCCEEDED", job.get("error")
    rows = _full(db, run_id)
    assert {e: r["state"] for e, r in rows.items()} == {1: "PRUNED", 2: "PRUNED", 3: "PRESENT"}
    base = run_dir_for(d.settings.work_root, run_id)
    journal = PruneJournal(d.settings.work_root)
    for ep in (1, 2):
        rel = _locations(db, rows[ep]["id"])[0]["relpath"]
        assert not (base / rel).exists()
        prune = rows[ep]["prune"]
        assert prune["already_absent"] is False and prune["bytes_freed"] == rows[ep]["bytes"] > 0
        entry = journal.get(prune["request_id"])
        assert entry["report"]["request_id"] == prune["request_id"] and entry["response"]["ack"]["state"] == "PRUNED"
        sha = rows[ep]["sha256"]
        stored = store / sha[:2] / sha
        assert stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == sha  # store copy untouched
    latest = _locations(db, rows[3]["id"])[0]
    assert (base / latest["relpath"]).is_file() and latest["state"] == "PRESENT"
    assert {r["state"] for r in _requests(db, run_id).values()} == {"DONE"}
    outbox = sorted(p.name for p in Path(d.settings.work_root, "prunes").iterdir())
    assert outbox == sorted(f"{r['id']}.json" for r in _requests(db, run_id).values())  # no temp names left


def test_keep_everything_candidate_cancels_open_orders_and_replanning_rearms_them(core, runner_sim, base_proto, db):
    """Planned while the candidate trained; before delivery the candidate entered a keep-everything status (PAUSED:
    kept for a later resume) -> the orders are CANCELLED (``CANDIDATE_PAUSED``), nothing is handed out or deleted. Once
    the candidate may be pruned again, re-planning re-arms the SAME requests (same ids) and they are delivered."""
    run_id, _ = harness.validate_and_run(core, _proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    _train(runner_sim, lease)
    reqs = _requests(db, run_id)
    assert len(reqs) == 2 and {r["state"] for r in reqs.values()} == {"PENDING"}
    cid = repo.get_job(db, lease.job_id)["candidate_id"]
    with tx(db):
        repo.update_candidate(db, cid, candidate_status="PAUSED")
    assert _orders(runner_sim, lease) == []
    after = _requests(db, run_id)
    assert {(r["state"], r["cancel_reason"]) for r in after.values()} == {("CANCELLED", "CANDIDATE_PAUSED")}
    assert all((run_dir_for(runner_sim.work_root, run_id) / r["relpath"]).is_file() for r in after.values())
    with tx(db):
        repo.update_candidate(db, cid, candidate_status="TRAINING")
        rearmed = retention.request_remote_prunes(db, repo.get_run(db, run_id), cid, planned_by="test")
    assert sorted(r["request_id"] for r in rearmed) == sorted(r["id"] for r in after.values())
    assert {r["state"] for r in _requests(db, run_id).values()} == {"PENDING"}
    orders = _orders(runner_sim, lease)
    assert sorted(o.request_id for o in orders) == sorted(r["id"] for r in after.values())
