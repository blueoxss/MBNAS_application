"""T3-10..14, T3-16..18, T3-21, T3-22, T3-29 (design A3 sections 7, 8, 14): idempotent result commit, conflicts,
PROCESSING orphans, sweeper reclaim and billing, lost-lease finalisation, attempt fencing, core restart, commit
rollback, runner liveness. SYNTHETIC (stub handlers in-process via runner_sim, injected sweeper clock)."""
from __future__ import annotations

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.util.time import iso_after, parse_iso, utcnow_iso
from mbnas_core.db import repo
from mbnas_core.db.core import tx

pytestmark = pytest.mark.runner_protocol


def _snapshot(db, run_id: str, job_id: str) -> dict:
    job = repo.get_job(db, job_id)
    return {"job": {k: job[k] for k in ("status", "attempt", "max_attempts", "lease_owner", "result", "error",
                                         "finished_at", "started_at")},
            "costs": [(c["id"], c["kind"], c["gpu_seconds"]) for c in repo.list_costs(db, run_id)],
            "evals": [(e["id"], e["status"]) for e in repo.list_evaluations(db, run_id)],
            "cands": [(c["id"], c["candidate_status"]) for c in repo.list_candidates(db, run_id)]}


def _ready(core, runner_sim, proto):
    run_id, _ = harness.validate_and_run(core, proto)
    runner_sim.register()
    lease = runner_sim.claim()
    assert lease is not None
    return run_id, lease


def test_result_replay_same_digest_returns_stored_response_without_side_effects(core, runner_sim, base_proto, db):
    """T3-10 / F1: a resent result (lost response) gets the stored body back; ledger rows, evaluations and the run's
    event sequence do not change."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    out = runner_sim.run_attempt(lease)
    first = out["response"]
    assert first.status_code == 200 and first.json()["outcome"] == "SUCCEEDED", first.text
    before = _snapshot(db, run_id, lease.job_id)
    seq = repo.get_run(db, run_id)["last_seq"]
    again = runner_sim.result(lease, out["body"])
    assert again.status_code == 200 and again.json() == first.json()
    assert _snapshot(db, run_id, lease.job_id) == before and repo.get_run(db, run_id)["last_seq"] == seq
    assert len([c for c in repo.list_costs(db, run_id) if c["kind"] == "train"]) == 1
    assert len([e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]) == 1


def test_result_with_different_digest_is_409_conflict(core, runner_sim, base_proto, db):
    """T3-11 / F2: a second, different result for the same (job, attempt) is 409 RESULT_CONFLICT; the job, ledger,
    evaluations and candidates are unchanged; exactly one error log event records it."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    out = runner_sim.run_attempt(lease)
    assert out["response"].status_code == 200
    before = _snapshot(db, run_id, lease.job_id)

    def errors():
        return [e for e in repo.get_events_after(db, run_id, 0, 10000) if e["type"] == "log"
                and e["payload"].get("level") == "error" and e["payload"].get("reason_code") == "RESULT_CONFLICT"]

    assert errors() == []
    other = out["result"].model_copy(update={"message": "a different result"})
    r = runner_sim.result(lease, runner_sim.commit_body(lease, other, artifact_ids=out["artifact_ids"]))
    assert r.status_code == 409 and r.json()["code"] == "RESULT_CONFLICT"
    assert _snapshot(db, run_id, lease.job_id) == before
    assert len(errors()) == 1


def test_result_processing_row_yields_202_with_retry_after_and_sweeper_clears_orphan(core, runner_sim, base_proto,
                                                                                    sweeper, db):
    """T3-12: an idempotency row left PROCESSING by an interrupted commit answers 202 + Retry-After 2; the sweeper
    deletes it once it is older than 60 s; the retried commit then succeeds (200)."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    out = runner_sim.run_attempt(lease, commit=False)
    body = runner_sim.commit_body(lease, out["result"], artifact_ids=out["artifact_ids"])
    with tx(db):
        db.execute("INSERT INTO idempotent_responses (job_id, attempt, runner_id, payload_digest, state, "
                   "processing_since) VALUES (?, 1, 'R1', ?, 'PROCESSING', ?)",
                   (lease.job_id, body["payload_digest"], utcnow_iso()))
    r = runner_sim.result(lease, body)
    assert r.status_code == 202 and r.headers["retry-after"] == "2" and r.json()["code"] == "PROCESSING"
    assert sweeper()["processing_cleared"] == 0  # younger than 60 s: kept
    with tx(db):  # age it past the orphan window (the lease itself stays valid: no reclaim)
        db.execute("UPDATE idempotent_responses SET processing_since = ? WHERE job_id = ?",
                   (iso_after(utcnow_iso(), -61), lease.job_id))
    swept = sweeper()
    assert swept["processing_cleared"] == 1 and swept["reclaimed"] == []
    r = runner_sim.result(lease, body)
    assert r.status_code == 200 and r.json()["outcome"] == "SUCCEEDED", r.text
    assert repo.get_job(db, lease.job_id)["status"] == "SUCCEEDED"


def test_sweeper_reclaims_expired_lease_and_bills_lost_window(core, runner_sim, runner_sim2, base_proto, sweeper, db):
    """T3-13 / F8 (test_lease.py:51): an expired RUNNING lease is requeued (LEASE_EXPIRED) and billed failed/lost
    from started_at to the last heartbeat; another runner then leases attempt 2."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    st = runner_sim.start(lease)
    t0 = st["started_at"]
    assert repo.renew_lease(db, lease.job_id, "R1", iso_after(t0, 3), 0.001, attempt=1)  # last heartbeat at t0+3s
    runner_sim2.register()
    assert runner_sim2.claim() is None  # still owned
    rep = sweeper(now=iso_after(t0, 10))
    assert rep["reclaimed"] == [lease.job_id]
    after = repo.get_job(db, lease.job_id)
    assert after["status"] == "QUEUED" and after["lease_owner"] is None and after["error"]["reason_code"] == "LEASE_EXPIRED"
    ledger = repo.list_costs(db, run_id)
    assert len(ledger) == 1 and ledger[0]["kind"] == "failed" and ledger[0]["meta"]["outcome"] == "lost"
    assert ledger[0]["gpu_seconds"] == pytest.approx(3.0, abs=0.01) and ledger[0]["attempt"] == 1
    assert repo.get_run(db, run_id)["costs"]["failed_gpu_seconds"] == pytest.approx(3.0, abs=0.01)
    again = runner_sim2.claim()
    assert again is not None and again.job_id == lease.job_id and again.attempt == 2


def test_sweeper_fails_job_and_candidate_when_attempts_exhausted(core, runner_sim, base_proto, sweeper, db):
    """T3-14 (test_lease.py:71): three started attempts that all expire -> FAILED LEASE_EXPIRED_MAX_ATTEMPTS,
    candidate TRAIN_FAILED, three failed ledger rows."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    job_id = None
    for attempt in range(1, 4):
        lease = runner_sim.claim()
        assert lease is not None and lease.attempt == attempt
        job_id = lease.job_id
        st = runner_sim.start(lease)
        sweeper(now=iso_after(st["started_at"], 3600))
    final = repo.get_job(db, job_id)
    assert final["status"] == "FAILED" and final["error"]["reason_code"] == "LEASE_EXPIRED_MAX_ATTEMPTS"
    assert repo.get_candidate(db, final["candidate_id"])["candidate_status"] == "TRAIN_FAILED"
    assert len([r for r in repo.list_costs(db, run_id) if r["kind"] == "failed"]) == 3
    assert runner_sim.claim() is None


def test_stale_succeeded_result_is_discarded_and_lost_row_extended_once(core, runner_sim, runner_sim2, base_proto,
                                                                        sweeper, db):
    """T3-16 / F10 (test_lease.py:157): attempt 1 ran t0..t0+20 s, its lease expired after the heartbeat at t0+3 s and
    was reclaimed (lost billed 3 s). Its late SUCCEEDED result is 409 LEASE_LOST; the existing failed row is extended
    to the whole attempt (one row, extended_by owner_lost_lease_finalize), no evaluation is recorded; attempt 2 on
    another runner succeeds and is billed as a retry."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    runner_sim.start(lease)
    out = runner_sim.run_attempt(lease, commit=False)  # the child finished: a real SUCCEEDED result
    t0 = iso_after(utcnow_iso(), -20)
    with tx(db):  # the attempt started 20 s ago and its last heartbeat was at t0+3 s
        db.execute("UPDATE jobs SET started_at = ?, heartbeat_at = ? WHERE id = ?", (t0, iso_after(t0, 3), lease.job_id))
    assert sweeper(after_s=3600)["reclaimed"] == [lease.job_id]
    (reclaimed,) = repo.list_costs(db, run_id)
    assert reclaimed["gpu_seconds"] == pytest.approx(3.0, abs=0.01)
    body = runner_sim.commit_body(lease, out["result"], started_at=t0, artifact_ids=out["artifact_ids"])
    r = runner_sim.result(lease, body)
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST", r.text
    mine = [c for c in repo.list_costs(db, run_id) if c["job_id"] == lease.job_id and c["attempt"] == 1]
    assert len(mine) == 1 and mine[0]["id"] == reclaimed["id"] and mine[0]["kind"] == "failed"
    row = mine[0]
    interval = (parse_iso(row["finished_at"]) - parse_iso(t0)).total_seconds()
    assert interval == pytest.approx(20.0, abs=2.0) and row["started_at"] == t0
    assert row["gpu_seconds"] == pytest.approx(interval, abs=1e-6)
    assert row["meta"]["outcome"] == "lost" and row["meta"]["reason_code"] == "LEASE_LOST"
    assert row["meta"]["extended_by"] == "owner_lost_lease_finalize"
    assert row["meta"]["reclaimed_reason_code"] == "LEASE_EXPIRED"
    assert not [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    assert runner_sim.result(lease, body).json()["code"] == "LEASE_LOST"  # replayed, never a second row
    assert len([c for c in repo.list_costs(db, run_id) if c["attempt"] == 1]) == 1
    runner_sim2.register()
    second = runner_sim2.claim()
    assert second is not None and second.job_id == lease.job_id and second.attempt == 2
    done = runner_sim2.run_attempt(second)
    assert done["response"].status_code == 200 and done["response"].json()["outcome"] == "SUCCEEDED"
    kinds = [(c["attempt"], c["kind"]) for c in repo.list_costs(db, run_id)]
    assert kinds == [(1, "failed"), (2, "retry")]


def test_lost_lease_without_reclaim_row_appends_one_lost_row(core, runner_sim, base_proto, sweeper, db):
    """T3-17 (test_lease.py:201): a LEASED-only attempt is reclaimed without billing; the runner's late result for it
    appends exactly one lost row (LEASE_LOST) covering its interval."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    assert sweeper(after_s=3600)["reclaimed"] == [lease.job_id]
    assert repo.list_costs(db, run_id) == []
    from mbnas_contracts.schemas.jobs import JobResult, JobStatus

    t0 = iso_after(utcnow_iso(), -8)
    res = JobResult(job_id=lease.job_id, status=JobStatus.FAILED, attempt=1, reason_code="CHILD_EXIT_NO_RESULT",
                    outputs={"retryable": True})
    r = runner_sim.result(lease, runner_sim.commit_body(lease, res, started_at=t0))
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST"
    rows = [c for c in repo.list_costs(db, run_id) if c["job_id"] == lease.job_id]
    assert len(rows) == 1 and rows[0]["meta"]["reason_code"] == "LEASE_LOST"
    assert rows[0]["gpu_seconds"] == pytest.approx(8.0, abs=2.0)


def test_stale_attempt_of_same_runner_cannot_renew_touch_or_finish(core, runner_sim, base_proto, sweeper, db):
    """T3-18 / F16 / R1: the same runner re-leases the job (attempt 2) after a reclaim; every write of attempt 1
    (start, heartbeat, progress, checkpoint, artifacts, result) is 409 LEASE_LOST and the attempt-2 row is untouched;
    a ticket of one attempt never authorises the other (403 TICKET_INVALID)."""
    run_id, first = _ready(core, runner_sim, base_proto())
    runner_sim.start(first)
    assert sweeper(after_s=3600)["reclaimed"] == [first.job_id]
    second = runner_sim.claim()
    assert second is not None and second.job_id == first.job_id and second.attempt == 2
    row = repo.get_job(db, first.job_id)
    for r in (runner_sim.start_raw(first), runner_sim.heartbeat(first),
              runner_sim.progress(first, [{"type": "progress", "job_id": first.job_id, "ts": utcnow_iso(),
                                           "epoch": 1, "payload": {}}]),
              runner_sim.checkpoint(first, {"checkpoint_id": "ck-stale-1", "candidate_id": row["candidate_id"],
                                            "kind": "training_full", "epoch": 1, "relpath": "candidates/x/full.json",
                                            "uri": "/w/full.json", "sha256": "a" * 64, "bytes": 1, "hashes": {}}),
              runner_sim.artifacts(first, [{"kind": "gt", "relpath": "candidates/x/gt.png", "sha256": "b" * 64,
                                            "bytes": 1, "state": "READY", "meta": {}}])):
        assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST", r.text
    from mbnas_contracts.schemas.jobs import JobResult, JobStatus

    res = JobResult(job_id=first.job_id, status=JobStatus.SUCCEEDED, attempt=1, outputs={})
    r = runner_sim.result(first, runner_sim.commit_body(first, res, started_at=iso_after(utcnow_iso(), -1)))
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST"
    crossed = rs.Lease({**second.claim, "ticket": first.ticket}, "R1")  # attempt-2 body with the attempt-1 ticket
    assert runner_sim.heartbeat(crossed).json()["code"] == "TICKET_INVALID"
    after = repo.get_job(db, first.job_id)
    assert {k: after[k] for k in ("status", "attempt", "lease_owner", "lease_expires_at", "heartbeat_at", "started_at",
                                  "result", "error")} == \
           {k: row[k] for k in ("status", "attempt", "lease_owner", "lease_expires_at", "heartbeat_at", "started_at",
                                "result", "error")}
    assert repo.list_checkpoints(db, run_id) == []
    assert [a for a in repo.list_artifacts(db, run_id) if a["relpath"] == "candidates/x/gt.png"] == []
    assert all(c["attempt"] == 1 for c in repo.list_costs(db, run_id))  # nothing billed to attempt 2
    runner_sim.start(second)  # attempt 2 still works
    assert runner_sim.heartbeat(second).status_code == 200


def test_core_restart_between_claim_and_result_preserves_lease_and_commit(rp_settings, services, core, runner_sim,
                                                                         base_proto, tokens, db):
    """T3-21 / F5: a new core app instance (same DB and ticket key) accepts the heartbeat, progress and result of a
    lease issued by the previous instance; the commit is the same as without the restart."""
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app

    run_id, lease = _ready(core, runner_sim, base_proto())
    runner_sim.start(lease)
    out = runner_sim.run_attempt(lease, commit=False)
    with TestClient(create_app(rp_settings, services)) as fresh:
        sim = rs.RunnerSim(fresh, tokens["R1"], "R1", runner_sim.work_root)
        assert sim.heartbeat(lease).status_code == 200
        r = sim.progress(lease, [{"type": "progress", "job_id": lease.job_id, "ts": utcnow_iso(), "epoch": 2,
                                  "payload": {}}])
        assert r.status_code == 200, r.text
        res = sim.result(lease, sim.commit_body(lease, out["result"], artifact_ids=out["artifact_ids"]))
        assert res.status_code == 200 and res.json()["outcome"] == "SUCCEEDED", res.text
        assert sim.ack(lease).status_code == 204
    assert repo.get_job(db, lease.job_id)["status"] == "SUCCEEDED"
    assert repo.get_run(db, run_id)["status"] == "COMPLETED"
    assert [(c["kind"], c["meta"]["outcome"]) for c in repo.list_costs(db, run_id)] == [("train", "succeeded")]


def test_commit_exception_rolls_back_and_leaves_no_idempotent_row(core, runner_sim, base_proto, db, monkeypatch):
    """T3-22 / F6: an exception inside the commit transaction -> 500 COMMIT_FAILED, everything rolled back (no
    idempotency row, job still RUNNING, no ledger row); the retried commit succeeds once."""
    from mbnas_core.runner_api import routes

    run_id, lease = _ready(core, runner_sim, base_proto())
    out = runner_sim.run_attempt(lease, commit=False)
    body = runner_sim.commit_body(lease, out["result"], artifact_ids=out["artifact_ids"])
    real = routes.settle_job

    def broken(*a, **k):
        real(*a, **k)  # the settlement ran inside the transaction ...
        raise RuntimeError("synthetic failure after settlement")  # ... and then the commit fails

    monkeypatch.setattr(routes, "settle_job", broken)
    r = runner_sim.result(lease, body)
    assert r.status_code == 500 and r.json()["code"] == "COMMIT_FAILED", r.text
    assert db.execute("SELECT COUNT(*) FROM idempotent_responses").fetchone()[0] == 0
    assert repo.get_job(db, lease.job_id)["status"] == "RUNNING"
    assert repo.list_costs(db, run_id) == []
    assert all(e["status"] != "EVALUATED" for e in repo.list_evaluations(db, run_id))
    monkeypatch.setattr(routes, "settle_job", real)
    r = runner_sim.result(lease, body)
    assert r.status_code == 200 and r.json()["outcome"] == "SUCCEEDED", r.text
    assert [(c["kind"], c["meta"]["outcome"]) for c in repo.list_costs(db, run_id)] == [("train", "succeeded")]


def test_runner_without_heartbeat_is_reported_lost(core, runner_sim, base_proto, sweeper, db):
    """T3-29 (test_lease.py:139): a runner that stopped heartbeating is LOST in ``runners`` after a sweep, its worker
    row is 'lost' in the run snapshot and /health counts 0 live workers."""
    run_id, lease = _ready(core, runner_sim, base_proto())
    runner_sim.start(lease)
    assert core.get("/api/v1/health", headers=harness.auth()).json()["worker_count"] == 1
    silent = iso_after(utcnow_iso(), -120)  # no heartbeat for 2 minutes (> 3 x heartbeat_s + 5 s; < the 1-day prune)
    with tx(db):
        db.execute("UPDATE runners SET last_seen_at = ? WHERE id = 'R1'", (silent,))
        db.execute("UPDATE workers SET heartbeat_at = ? WHERE id = 'R1'", (silent,))
    assert sweeper()["runners_lost"] == 1
    assert db.execute("SELECT status FROM runners WHERE id = 'R1'").fetchone()["status"] == "LOST"
    snap = core.get(f"/api/v1/runs/{run_id}/snapshot", headers=harness.auth()).json()
    assert {w["id"]: w["status"] for w in snap["workers"]}.get("R1") == "lost"
    assert core.get("/api/v1/health", headers=harness.auth()).json()["worker_count"] == 0
