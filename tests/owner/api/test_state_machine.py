"""Run state machine through the API: pause/resume/cancel transitions, idempotent repeats, invalid transitions
(409), worker-confirmed PAUSED/CANCELLED, blocked resume. A worker is simulated by leasing jobs directly in the
DB (synthetic, no child process)."""
from __future__ import annotations

from pathlib import Path

import harness
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine
from mbnas_core.scheduling import jobs
from mbnas_contracts.util.time import utcnow_iso


def _post(client, run_id, action, **body):
    r = client.post(f"/api/v1/runs/{run_id}/{action}", json=body or None, headers=auth())
    if r.status_code == 200:
        assert contract_errors("Run", r.json()) == []
    return r


def _statuses(settings, run_id):
    conn = connect(settings, init=False)
    try:
        return [e["payload"]["status"] for e in repo.get_events_after(conn, run_id, 0, 1000) if e["type"] == "run.updated"]
    finally:
        conn.close()


def _lease(settings, worker="w-sim"):
    conn = connect(settings, init=False)
    job = repo.acquire_lease(conn, worker, utcnow_iso(), 60, kinds=["BASELINE_TRAIN"])
    assert jobs.start_attempt(conn, job["id"], worker)
    return conn, repo.get_job(conn, job["id"])


def test_pause_resume_without_active_jobs(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    r = _post(client, run_id, "pause", reason="maintenance")
    assert r.json()["status"] == "PAUSED"  # nothing running: no safe boundary to wait for
    assert _post(client, run_id, "pause").json()["status"] == "PAUSED"  # idempotent repeat
    r = _post(client, run_id, "resume")
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    assert _statuses(api_settings, run_id)[-4:] == ["PAUSE_REQUESTED", "PAUSED", "RESUMING", "RUNNING"]
    assert _post(client, run_id, "resume").json()["status"] == "RUNNING"  # idempotent


def test_pause_waits_for_safe_boundary(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    conn, job = _lease(api_settings)
    assert _post(client, run_id, "pause").json()["status"] == "PAUSE_REQUESTED"
    with tx(conn):  # the worker's child stopped at an epoch boundary
        jobs.requeue(conn, job["id"], "w-sim", error={"reason_code": "PAUSED_AT_SAFE_BOUNDARY"}, pause=True)
        engine.job_requeued(conn, repo.get_job(conn, job["id"]), paused=True, reason_code="PAUSED_AT_SAFE_BOUNDARY")
        run = engine.settle_run(conn, run_id)
    assert run["status"] == "PAUSED"
    requeued = repo.get_job(conn, job["id"])
    assert requeued["status"] == "QUEUED" and requeued["max_attempts"] == 4  # a pause does not consume a retry
    assert repo.get_candidate(conn, job["candidate_id"])["candidate_status"] == "PAUSED"
    assert repo.acquire_lease(conn, "w-2", utcnow_iso(), 60) is None  # PAUSED runs are never leased
    conn.close()
    assert _post(client, run_id, "resume").json()["status"] == "RUNNING"


def test_resume_withdraws_pending_pause(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    conn, _job = _lease(api_settings)
    conn.close()
    assert _post(client, run_id, "pause").json()["status"] == "PAUSE_REQUESTED"
    assert _post(client, run_id, "resume").json()["status"] == "RUNNING"


def test_cancel_tracks_active_jobs_until_worker_confirms(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    conn, job = _lease(api_settings)
    r = _post(client, run_id, "cancel", reason="user")
    body = r.json()
    assert body["status"] == "CANCEL_REQUESTED"
    assert body["cancel"]["active_jobs"] == [job["id"]] and body["cancel"]["queued_jobs_cancelled"] == 2
    from mbnas_contracts.schemas.jobs import JobResult, JobStatus

    with tx(conn):  # worker killed the child
        repo.finish_job(conn, job["id"], "w-sim", "CANCELLED", error={"reason_code": "RUN_CANCELLED"})
        engine.job_finished(conn, repo.get_job(conn, job["id"]),
                            JobResult(job_id=job["id"], status=JobStatus.CANCELLED, reason_code="RUN_CANCELLED"))
    run = repo.get_run(conn, run_id)
    conn.close()
    assert run["status"] == "CANCELLED" and run["cancel"]["state"] == "CANCELLED" and run["terminal"]
    assert _post(client, run_id, "cancel").json()["status"] == "CANCELLED"  # idempotent
    for action in ("pause", "resume"):
        r = _post(client, run_id, action)
        assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


def test_cancel_without_active_jobs_is_immediate(client, protocol):
    run_id, _ = harness.validate_and_run(client, protocol)
    assert _post(client, run_id, "cancel").json()["status"] == "CANCELLED"
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    assert {c["status"] for c in snap["candidates"]} == {"CANCELLED"}


def test_completed_run_rejects_transitions(client, protocol, inline_worker):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    for action in ("pause", "resume", "cancel"):
        r = _post(client, run_id, action)
        assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


def test_resume_blocked_when_dataset_changed(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    assert _post(client, run_id, "pause").json()["status"] == "PAUSED"
    label = Path(protocol["console"]["paths"]["search_val_root"].replace("/images/", "/labels/")) / "val_000.txt"
    label.write_text("0 0.3 0.3 0.1 0.1\n")
    r = _post(client, run_id, "resume")
    assert r.status_code == 409 and r.json()["code"] == "RESUME_BLOCKED"
    assert any(e["name"] == "revalidation" for e in r.json()["errors"])
    assert _statuses(api_settings, run_id)[-2:] == ["RESUMING", "PAUSED"]
    run = client.get(f"/api/v1/runs/{run_id}", headers=auth()).json()
    assert run["status"] == "PAUSED" and run["reason_code"] == "RESUME_BLOCKED"


def test_resume_blocks_candidate_with_corrupt_full_state(client, protocol, inline_worker, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    conn = connect(api_settings, init=False)
    base = repo.acquire_lease(conn, "w-x", utcnow_iso(), 60, kinds=["BASELINE_TRAIN"])  # keep baseline aside
    inline_worker.cfg.kinds = ["PROPOSE_GENERATION", "CANDIDATE_TRAIN"]
    while inline_worker.tick():  # run until every search candidate passed L0 and its L1 job is queued
        if repo.list_jobs(conn, run_id, status="QUEUED") and all(
                j["rung"] == "L1" for j in repo.list_jobs(conn, run_id, status="QUEUED")):
            break
    with tx(conn):
        repo.finish_job(conn, base["id"], "w-x", "CANCELLED", error={"reason_code": "TEST"})
    queued = [j for j in repo.list_jobs(conn, run_id, status="QUEUED") if j["kind"] == "CANDIDATE_TRAIN"]
    assert queued and all(j["rung"] == "L1" for j in queued)
    victim = queued[0]
    ck = repo.latest_checkpoint(conn, victim["candidate_id"], "training_full")
    Path(ck["path"]).write_text("corrupted")
    conn.close()
    assert _post(client, run_id, "pause").json()["status"] == "PAUSED"
    r = _post(client, run_id, "resume")
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    conn = connect(api_settings, init=False)
    assert repo.get_candidate(conn, victim["candidate_id"])["candidate_status"] == "RESUME_BLOCKED"
    assert repo.get_job(conn, victim["id"])["status"] == "CANCELLED"
    others = [j for j in queued[1:]]
    assert all(repo.get_job(conn, j["id"])["status"] == "QUEUED" for j in others)
    conn.close()


def test_transition_unknown_run_is_404(client):
    assert _post(client, "run-nope", "pause").status_code == 404
