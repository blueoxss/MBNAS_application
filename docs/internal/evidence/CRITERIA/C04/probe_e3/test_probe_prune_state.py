"""Probe (evaluator, C04 EVAL_3): DD-A66/A68 prune state under the C04 anchors - paths no project test exercises."""
import importlib.util
import json
from pathlib import Path

import harness
import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration import engine, retention
from mbnas_runner.daemon import execute_prune_order
from mbnas_runner.envelope import run_dir_for

ROOT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
_s = importlib.util.spec_from_file_location("c04e3_t334", ROOT / "tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py")
t = importlib.util.module_from_spec(_s)
_s.loader.exec_module(t)


def _logs(db, run_id, code):
    return [e["payload"] for e in repo.get_events_after(db, run_id, 0, 100_000)
            if e["type"] == "log" and e["payload"].get("reason_code") == code]


def _req_states(db, run_id):
    return sorted((r["state"], r["deliveries"], r["cancel_reason"]) for r in t._requests(db, run_id).values())


def test_p5_planning_failure_is_logged_and_never_fails_e6_or_e8(core, runner_sim, base_proto, db, monkeypatch):
    def broken(conn, run, candidate_id, **kw):
        conn.execute("UPDATE checkpoints SET state = 'PRUNED' WHERE run_id = ?", (run["id"],))  # a partial write ...
        raise RuntimeError("synthetic planning failure")  # ... then the failure

    monkeypatch.setattr(retention, "request_remote_prunes", broken)
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    result, _ = t._train(runner_sim, lease)  # E6 x3 must still answer 2xx (helper asserts)
    states = {e: r["state"] for e, r in t._full(db, run_id).items()}
    print("P5_E6 states", states, "requests", t._requests(db, run_id), "failed_logs", len(_logs(db, run_id, "PRUNE_PLANNING_FAILED")))
    assert states == {1: "PRESENT", 2: "PRESENT", 3: "PRESENT"} and t._requests(db, run_id) == {}
    n_e6 = len(_logs(db, run_id, "PRUNE_PLANNING_FAILED"))
    assert n_e6 >= 1
    t._finish(runner_sim, lease, result)  # E8 (job_finished plans again) must commit
    run = repo.get_run(db, run_id)
    print("P5_E8 run", run["status"], "job", repo.get_job(db, lease.job_id)["status"],
          "failed_logs", len(_logs(db, run_id, "PRUNE_PLANNING_FAILED")))
    assert run["status"] == "COMPLETED" and len(_logs(db, run_id, "PRUNE_PLANNING_FAILED")) > n_e6
    assert {r["state"] for r in t._full(db, run_id).values()} == {"PRESENT"}


def test_p6_delivery_failure_keeps_the_lease_and_the_requests(core, runner_sim, base_proto, db, monkeypatch):
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    t._train(runner_sim, lease)
    before = _req_states(db, run_id)
    exp0 = repo.get_job(db, lease.job_id)["lease_expires_at"]
    real = retention.deliver_prune_orders

    def broken(conn, runner_id, job_id, attempt, **kw):
        conn.execute("UPDATE prune_requests SET state = 'DELIVERED', deliveries = deliveries + 1, delivered_job_id = ?, "
                     "delivered_attempt = ? WHERE runner_id = ?", (job_id, attempt, runner_id))
        raise RuntimeError("synthetic delivery failure")

    monkeypatch.setattr(retention, "deliver_prune_orders", broken)
    r = runner_sim.heartbeat(lease)
    exp1 = repo.get_job(db, lease.job_id)["lease_expires_at"]
    print("P6 hb", r.status_code, "prune", r.json()["control"]["prune"], "renewed", exp1 > exp0, "requests", _req_states(db, run_id))
    assert r.status_code == 200 and r.json()["control"]["prune"] is None and exp1 > exp0
    assert _req_states(db, run_id) == before == [("PENDING", 0, None)] * 2
    monkeypatch.setattr(retention, "deliver_prune_orders", real)
    assert len(t._orders(runner_sim, lease)) == 2


def test_p7_cancelled_run_cancels_open_orders_at_delivery(core, runner_sim, base_proto, db):
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    result, _ = t._train(runner_sim, lease)
    assert _req_states(db, run_id) == [("PENDING", 0, None)] * 2
    c = core.post(f"/api/v1/runs/{run_id}/cancel", headers=harness.auth(), json={"reason": "probe"})
    assert c.status_code == 200 and c.json()["status"] == "CANCEL_REQUESTED", c.text
    hb = runner_sim.heartbeat(lease).json()["control"]
    states = _req_states(db, run_id)
    files = [(run_dir_for(runner_sim.work_root, run_id) / r["relpath"]).is_file() for r in t._requests(db, run_id).values()]
    print("P7 control", hb["action"], hb["prune"], "requests", states, "files_kept", files)
    assert hb["action"] == "cancel" and hb["prune"] is None
    assert {s[0] for s in states} == {"CANCELLED"} and {s[2] for s in states} == {"RUN_CANCEL_REQUESTED"} and all(files)
    t._finish(runner_sim, lease, result)  # accepted as CANCELLED; job_finished plans nothing for a cancelled run
    print("P7 after commit run", repo.get_run(db, run_id)["status"], "requests", _req_states(db, run_id))
    assert repo.get_run(db, run_id)["status"] == "CANCELLED" and {s[0] for s in _req_states(db, run_id)} == {"CANCELLED"}


def test_p8_e11_exception_rolls_back_every_write(core, runner_sim, base_proto, db, monkeypatch):
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    t._train(runner_sim, lease)
    o = sorted(t._orders(runner_sim, lease), key=lambda x: x.relpath)[0]
    report, _ = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
    real_log = engine.log

    def broken_log(*a, **k):
        if k.get("reason_code") == "CHECKPOINT_PRUNED":  # the last statement of record_prune_report, after its writes
            raise RuntimeError("synthetic E11 failure after the writes")
        return real_log(*a, **k)

    monkeypatch.setattr(engine, "log", broken_log)
    try:
        r = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)
        outcome = r.status_code
    except RuntimeError as exc:  # TestClient re-raises server exceptions
        outcome = f"raised {exc}"
    ck = {r_["id"]: r_["state"] for r_ in repo.list_checkpoints(db, run_id)}[o.checkpoint_id]
    loc = [x["state"] for x in t._locations(db, o.checkpoint_id)]
    req = t._requests(db, run_id)[o.checkpoint_id]
    print("P8 outcome", outcome, "checkpoint", ck, "location", loc, "request", req["state"], req["report_json"])
    assert ck == "PRESENT" and loc == ["PRESENT"] and req["state"] == "DELIVERED" and req["report_json"] is None
    monkeypatch.setattr(engine, "log", real_log)
    ok = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)
    print("P8 retry", ok.status_code, ok.json())
    assert ok.status_code == 200 and ok.json()["replayed"] is False
    assert t._requests(db, run_id)[o.checkpoint_id]["state"] == "DONE"


def test_p9_stale_attempt_heartbeat_gets_no_orders(core, runner_sim, base_proto, sweeper, db):
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    first = runner_sim.claim()
    t._train(runner_sim, first)
    assert sweeper(after_s=3600)["reclaimed"] == [first.job_id]
    r = runner_sim.heartbeat(first)
    print("P9 stale hb", r.status_code, r.json().get("code"), "requests", _req_states(db, run_id))
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST"
    assert _req_states(db, run_id) == [("PENDING", 0, None)] * 2
    second = runner_sim.claim()
    runner_sim.start(second)
    orders = t._orders(runner_sim, second)
    reqs = t._requests(db, run_id).values()
    print("P9 attempt2 orders", len(orders), [(q["delivered_job_id"] == second.job_id, q["delivered_attempt"]) for q in reqs])
    assert len(orders) == 2 and all(q["delivered_attempt"] == second.attempt == 2 for q in reqs)


def test_p10_late_report_of_a_cancelled_request_is_recorded(core, runner_sim, base_proto, db):
    run_id, _ = harness.validate_and_run(core, t._proto(base_proto))
    runner_sim.register()
    lease = runner_sim.claim()
    t._train(runner_sim, lease)
    o = sorted(t._orders(runner_sim, lease), key=lambda x: x.relpath)[0]
    report, _ = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")  # deleted
    with tx(db):  # SYNTHETIC: the request was cancelled at a later delivery after the runner had executed it
        db.execute("UPDATE prune_requests SET state = 'CANCELLED', cancel_reason = 'LATEST_FULL_STATE' WHERE id = ?",
                   (o.request_id,))
    r = runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report)
    ck = {x["id"]: x["state"] for x in repo.list_checkpoints(db, run_id)}[o.checkpoint_id]
    after = [p for p in _logs(db, run_id, "CHECKPOINT_PRUNED") if p.get("checkpoint_id") == o.checkpoint_id]
    print("P10", r.status_code, r.json(), "checkpoint", ck, "after_cancel", [p.get("after_cancel") for p in after])
    assert r.status_code == 200 and ck == "PRUNED" and after and after[0].get("after_cancel") == "LATEST_FULL_STATE"
