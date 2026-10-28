"""DD-A64 (C04 EVAL_2 N1): the runner claim (E2) reclaims expired leases right before it leases, exactly like the owner
worker (``owner_worker/main.py`` ``tick``: reclaim, then ``acquire_lease``). A claim that comes before the core
sweeper's next pass therefore leaves the same accounting as the sweeper path (T3-13/T3-14/T3-16): the dead RUNNING
attempt gets exactly one ``failed``/lost ledger row from ``started_at`` to its last heartbeat (``LEASE_EXPIRED``), the
job's error and a run log event record LEASE_EXPIRED, and the next attempt is billed ``retry``.

Before the fix the claim re-leased the expired job at attempt 2 with no lost row and no LEASE_EXPIRED record, and
attempt 2 was billed ``train`` (evaluator probe, EVAL_2 appendix C: ``LEDGER_AFTER_CLAIM []``, sweep ``reclaimed []``,
``LEDGER_FINAL [(2, 'train', ...)]``; the same through the DD-A63 (d) CPU path). Ported here: the GPU lease path
(``repo.acquire_lease``) with the claim and the sweeper in either order (one set of expectations for both); the CPU path
(``routes._lease_for_cpu_runner``) on a mixed fleet (its own conditional UPDATE) and on a CPU-only fleet (it delegates
to ``acquire_lease``); a late result of the reclaimed attempt (still one row, extended); attempts exhausted (FAILED
LEASE_EXPIRED_MAX_ATTEMPTS); the runner's own expired LEASED job is not re-delivered; concurrent claims plus a sweeper
pass bill the dead attempt once; the normal path (nothing expired) unchanged.

SYNTHETIC: stub handlers in-process via ``runner_sim``, the expiry is written into the jobs row, the ``sweeper`` fixture
runs ``sweep_once`` on demand (the app's own sweeper thread is disabled); no GPU.
"""
from __future__ import annotations

import threading
from typing import Any

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.schemas.jobs import JobResult, JobStatus
from mbnas_contracts.util.time import iso_after, parse_iso, utcnow_iso
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx

pytestmark = pytest.mark.runner_protocol

CPU = {"device": "cpu", "device_uuid": None, "device_name": "CPU (SYNTHETIC, CUDA_VISIBLE_DEVICES='')"}
BEAT_S = 3.0  # the dead attempt's last heartbeat came 3 s after its start: the billed lost window


def _expire(db, job_id: str) -> tuple[str, str]:
    """The runner died: its attempt started 20 s ago, its last heartbeat was 3 s after that and the lease ran out 1 s
    ago; no sweeper pass has run since. Returns ``(started_at, heartbeat_at)`` as written."""
    now = utcnow_iso()
    started, beat = iso_after(now, -20), iso_after(now, -20 + BEAT_S)
    with tx(db):
        db.execute("UPDATE jobs SET started_at = ?, heartbeat_at = ?, lease_expires_at = ? WHERE id = ?",
                   (started, beat, iso_after(now, -1), job_id))
    return started, beat


def _ledger(db, run_id: str) -> list[tuple[Any, ...]]:
    return [(c["attempt"], c["kind"], (c.get("meta") or {}).get("reason_code")) for c in repo.list_costs(db, run_id)]


def _expired_logs(db, run_id: str, job_id: str) -> list[dict[str, Any]]:
    return [e for e in repo.get_events_after(db, run_id, 0, 100_000) if e["type"] == "log"
            and e["payload"].get("reason_code") == "LEASE_EXPIRED" and e["payload"].get("job_id") == job_id]


def _assert_billed_once_like_the_sweeper(db, run_id: str, job_id: str, started: str, beat: str, dead_runner: str):
    """What the sweeper's reclaim (T3-13) leaves for a dead RUNNING attempt 1: one failed/lost row over
    [started_at, heartbeat_at], the job error and one run log event saying LEASE_EXPIRED, Run.costs failed seconds."""
    rows = [c for c in repo.list_costs(db, run_id) if c["job_id"] == job_id and c["attempt"] == 1]
    assert len(rows) == 1, rows
    (row,) = rows
    assert row["kind"] == "failed" and row["meta"]["outcome"] == "lost"
    assert row["meta"]["reason_code"] == "LEASE_EXPIRED"
    assert (row["started_at"], row["finished_at"]) == (started, beat)
    assert row["gpu_seconds"] == pytest.approx(BEAT_S, abs=1e-3)
    error = repo.get_job(db, job_id)["error"]
    assert (error["reason_code"], error["attempt"], error["lease_owner"]) == ("LEASE_EXPIRED", 1, dead_runner)
    (event,) = _expired_logs(db, run_id, job_id)
    assert event["payload"]["attempt"] == 1 and event["payload"]["paused"] is False
    assert repo.get_run(db, run_id)["costs"]["failed_gpu_seconds"] == pytest.approx(BEAT_S, abs=1e-3)


def _assert_retry_succeeds(sim: rs.RunnerSim, lease: rs.Lease, db, run_id: str) -> None:
    done = sim.run_attempt(lease)
    assert done["response"].status_code == 200, done["response"].text
    assert done["response"].json()["outcome"] == "SUCCEEDED"
    assert _ledger(db, run_id) == [(1, "failed", "LEASE_EXPIRED"), (2, "retry", None)]
    assert "retry_gpu_seconds" in repo.get_run(db, run_id)["costs"]


@pytest.mark.parametrize("first", ["claim", "sweeper"])
def test_claim_before_or_after_the_sweep_bills_the_dead_attempt_the_same(first, core, runner_sim, runner_sim2,
                                                                         base_proto, sweeper, db):
    """GPU path (``repo.acquire_lease``), EVAL_2 probe P1. ``claim``: an idle runner polls after the lease expired and
    before the sweeper's next pass (C04 N1). ``sweeper``: the sweeper's pass comes first (T3-13 order). Both orders
    must leave the identical accounting, and a later sweep finds nothing left (never billed twice)."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    dead = runner_sim.claim()
    assert dead is not None and dead.attempt == 1
    runner_sim.start(dead)
    started, beat = _expire(db, dead.job_id)
    runner_sim2.register()
    if first == "sweeper":
        assert sweeper()["reclaimed"] == [dead.job_id]
    second = runner_sim2.claim()
    assert second is not None and (second.job_id, second.attempt) == (dead.job_id, 2)
    job = repo.get_job(db, dead.job_id)
    assert (job["status"], job["lease_owner"], job["attempt"]) == ("LEASED", "R2", 2)
    _assert_billed_once_like_the_sweeper(db, run_id, dead.job_id, started, beat, "R1")
    assert sweeper()["reclaimed"] == []
    _assert_billed_once_like_the_sweeper(db, run_id, dead.job_id, started, beat, "R1")
    _assert_retry_succeeds(runner_sim2, second, db, run_id)
    assert len(_expired_logs(db, run_id, dead.job_id)) == 1


@pytest.mark.parametrize("fleet", ["mixed", "cpu_only"])
def test_cpu_claim_path_reclaims_before_leasing(fleet, core, rp_settings, base_proto, sweeper, db, tmp_path,
                                                monkeypatch):
    """DD-A63 (d) CPU path, EVAL_2 probe P4. ``mixed``: an ONLINE GPU runner offers EVALUATE only, so the CPU claims
    lease the BASELINE_TRAIN job through ``_lease_for_cpu_runner``'s own conditional UPDATE (``acquire_lease`` never
    called); ``cpu_only``: no GPU runner, ``_lease_for_cpu_runner`` delegates to ``acquire_lease``. Either way the
    second CPU runner's claim before any sweep bills the dead attempt like the sweeper and attempt 2 is a retry."""
    from mbnas_core.runner_api import auth, routes

    calls = {"cpu_path": 0, "acquire_lease": 0}
    real_cpu_path, real_acquire = routes._lease_for_cpu_runner, repo.acquire_lease

    def cpu_path(*a: Any, **k: Any) -> Any:
        calls["cpu_path"] += 1
        return real_cpu_path(*a, **k)

    def acquire(*a: Any, **k: Any) -> Any:
        calls["acquire_lease"] += 1
        return real_acquire(*a, **k)

    monkeypatch.setattr(routes, "_lease_for_cpu_runner", cpu_path)
    monkeypatch.setattr(repo, "acquire_lease", acquire)
    run_id, _ = harness.validate_and_run(core, base_proto())
    conn = connect(rp_settings, init=False)
    try:
        toks = {rid: auth.create_principal(conn, "runner", runner_id=rid)[1] for rid in ("GPU0", "CPU1", "CPU2")}
    finally:
        conn.close()
    if fleet == "mixed":
        rs.RunnerSim(core, toks["GPU0"], "GPU0", tmp_path / "g0").register(kinds=["EVALUATE"])
    c1 = rs.RunnerSim(core, toks["CPU1"], "CPU1", tmp_path / "c1")
    c2 = rs.RunnerSim(core, toks["CPU2"], "CPU2", tmp_path / "c2")
    c1.register(**CPU)
    c2.register(**CPU)
    dead = c1.claim()
    assert dead is not None and dead.run_id == run_id and dead.attempt == 1
    c1.start(dead)
    started, beat = _expire(db, dead.job_id)
    second = c2.claim()  # the CPU path, before any sweeper pass
    assert second is not None and (second.job_id, second.attempt) == (dead.job_id, 2)
    assert calls == {"cpu_path": 2, "acquire_lease": 0 if fleet == "mixed" else 2}, calls
    _assert_billed_once_like_the_sweeper(db, run_id, dead.job_id, started, beat, "CPU1")
    assert sweeper()["reclaimed"] == []
    _assert_retry_succeeds(c2, second, db, run_id)


def test_late_result_of_the_claim_reclaimed_attempt_extends_its_one_lost_row(core, runner_sim, runner_sim2, base_proto,
                                                                             db):
    """EVAL_2 probe P4 (late result) after the claim-side reclaim: the dead runner's late attempt-1 result is 409
    LEASE_LOST and extends the reclaim's row to the whole attempt (T3-16 semantics) - one row per (job, attempt)."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    dead = runner_sim.claim()
    runner_sim.start(dead)
    started, _beat = _expire(db, dead.job_id)
    runner_sim2.register()
    second = runner_sim2.claim()
    assert second is not None and second.attempt == 2
    (reclaimed,) = repo.list_costs(db, run_id)
    assert reclaimed["meta"]["reason_code"] == "LEASE_EXPIRED" and reclaimed["gpu_seconds"] == pytest.approx(BEAT_S,
                                                                                                             abs=1e-3)
    late = JobResult(job_id=dead.job_id, status=JobStatus.FAILED, attempt=1, reason_code="CHILD_EXIT_NO_RESULT",
                     outputs={"retryable": True})
    r = runner_sim.result(dead, runner_sim.commit_body(dead, late, started_at=started))
    assert r.status_code == 409 and r.json()["code"] == "LEASE_LOST", r.text
    (row,) = [c for c in repo.list_costs(db, run_id) if c["attempt"] == 1]
    assert row["id"] == reclaimed["id"] and row["kind"] == "failed" and row["started_at"] == started
    assert row["meta"]["reason_code"] == "LEASE_LOST" and row["meta"]["reclaimed_reason_code"] == "LEASE_EXPIRED"
    assert row["meta"]["extended_by"] == "owner_lost_lease_finalize"
    interval = (parse_iso(row["finished_at"]) - parse_iso(started)).total_seconds()
    assert row["gpu_seconds"] == pytest.approx(interval, abs=1e-6) and interval == pytest.approx(20.0, abs=2.0)
    job = repo.get_job(db, dead.job_id)
    assert (job["status"], job["lease_owner"], job["attempt"]) == ("LEASED", "R2", 2)  # attempt 2 untouched


def test_claim_reclaim_fails_the_job_when_no_attempt_is_left(core, runner_sim, runner_sim2, base_proto, sweeper, db):
    """T3-14 through the claim path: three started attempts that all die, each found expired by the next claim (the
    two runners alternate, so neither is RUNNER_BUSY) -> three failed/lost rows, and the claim after the third reclaims
    it as FAILED LEASE_EXPIRED_MAX_ATTEMPTS (candidate TRAIN_FAILED; retention runs in the claim) and answers 204."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    sims = (runner_sim, runner_sim2)
    for s in sims:
        s.register()
    job_id = None
    for attempt in (1, 2, 3):
        sim = sims[(attempt - 1) % 2]
        lease = sim.claim()
        assert lease is not None and lease.attempt == attempt
        job_id = lease.job_id
        sim.start(lease)
        _expire(db, job_id)
    assert sims[1].claim() is None
    final = repo.get_job(db, job_id)
    assert final["status"] == "FAILED" and final["error"]["reason_code"] == "LEASE_EXPIRED_MAX_ATTEMPTS"
    assert repo.get_candidate(db, final["candidate_id"])["candidate_status"] == "TRAIN_FAILED"
    assert _ledger(db, run_id) == [(n, "failed", "LEASE_EXPIRED") for n in (1, 2, 3)]
    assert all(c["gpu_seconds"] == pytest.approx(BEAT_S, abs=1e-3) for c in repo.list_costs(db, run_id))
    assert sweeper()["reclaimed"] == []
    assert len(repo.list_costs(db, run_id)) == 3


def test_claim_does_not_redeliver_its_own_expired_lease(core, runner_sim, base_proto, db):
    """E2(a) re-delivers a LEASED job whose claim response was lost (T3-03) only while that lease is live: once it
    expired, the claim's reclaim requeues it first (a LEASED-only attempt never started work: nothing billed, as T3-05)
    and the runner gets a fresh lease at attempt 2; the stale attempt-1 ticket can no longer start it."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    first = runner_sim.claim()
    assert first is not None and first.attempt == 1
    with tx(db):  # the claim response was lost and the lease ran out before the runner asked again
        db.execute("UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (iso_after(utcnow_iso(), -1), first.job_id))
    again = runner_sim.claim()
    assert again is not None and (again.job_id, again.attempt) == (first.job_id, 2)
    job = repo.get_job(db, first.job_id)
    assert (job["status"], job["lease_owner"], job["attempt"]) == ("LEASED", "R1", 2)
    assert job["error"]["reason_code"] == "LEASE_EXPIRED" and len(_expired_logs(db, run_id, first.job_id)) == 1
    assert repo.list_costs(db, run_id) == []
    stale = runner_sim.start_raw(first)
    assert stale.status_code == 409 and stale.json()["code"] == "LEASE_LOST"
    runner_sim.start(again)


def test_normal_claim_path_is_unchanged(core, runner_sim, runner_sim2, base_proto, db):
    """Nothing expired: the claim's reclaim finds nothing. A QUEUED job is leased at attempt 1 with nothing billed and
    no LEASE_EXPIRED record; the lost-response re-delivery returns the same lease (attempt, expiry: T3-03); a live
    RUNNING lease of another runner is left exactly as it was (204); the attempt is then billed ``train``."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    first = runner_sim.claim()
    assert first is not None and first.attempt == 1
    leased = repo.get_job(db, first.job_id)
    assert (leased["status"], leased["lease_owner"], leased["error"]) == ("LEASED", "R1", None)
    again = runner_sim.claim()
    assert again is not None and (again.job_id, again.attempt) == (first.job_id, 1)
    assert repo.get_job(db, first.job_id)["lease_expires_at"] == leased["lease_expires_at"]
    runner_sim.start(first)
    running = repo.get_job(db, first.job_id)
    runner_sim2.register()
    assert runner_sim2.claim() is None
    assert repo.get_job(db, first.job_id) == running
    assert repo.list_costs(db, run_id) == [] and not _expired_logs(db, run_id, first.job_id)
    done = runner_sim.run_attempt(first)
    assert done["response"].status_code == 200 and done["response"].json()["outcome"] == "SUCCEEDED"
    assert _ledger(db, run_id) == [(1, "train", None)]
    assert "failed_gpu_seconds" not in repo.get_run(db, run_id)["costs"]


def test_concurrent_claims_and_sweeper_bill_the_dead_attempt_once(core, rp_settings, runner_sim, base_proto, sweeper,
                                                                  db, tmp_path):
    """Four idle runners claim and a sweeper pass runs at the same moment after the lease expired: the reclaims
    serialise on BEGIN IMMEDIATE, so attempt 1 is billed exactly once (one lost row, one LEASE_EXPIRED log event) and
    exactly one claim gets attempt 2 (the others 204)."""
    from mbnas_core.runner_api import auth

    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    dead = runner_sim.claim()
    runner_sim.start(dead)
    conn = connect(rp_settings, init=False)
    try:
        sims = [rs.RunnerSim(core, auth.create_principal(conn, "runner", runner_id=f"RC{i}")[1], f"RC{i}",
                             tmp_path / f"rc{i}") for i in range(4)]
    finally:
        conn.close()
    for s in sims:
        s.register()
    started, beat = _expire(db, dead.job_id)
    got: dict[str, Any] = {}
    swept: list[list[str]] = []
    barrier = threading.Barrier(len(sims) + 1)

    def claim(s: rs.RunnerSim) -> None:
        barrier.wait()
        lease = s.claim()
        got[s.runner_id] = None if lease is None else (lease.job_id, lease.attempt)

    def sweep() -> None:
        barrier.wait()
        swept.append(sweeper()["reclaimed"])

    threads = [threading.Thread(target=claim, args=(s,)) for s in sims] + [threading.Thread(target=sweep)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert len(got) == len(sims) and len(swept) == 1, (got, swept)
    assert [v for v in got.values() if v is not None] == [(dead.job_id, 2)], got
    assert swept[0] in ([], [dead.job_id])
    _assert_billed_once_like_the_sweeper(db, run_id, dead.job_id, started, beat, "R1")
