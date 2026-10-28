"""db.core / db.repo: WAL pragmas, transactions, immutability, idempotency, lease atomicity, event seq."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

import pytest
from pydantic import ValidationError

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_contracts.util.time import iso_after

T0 = "2026-10-06T00:00:00.000000Z"


def make_run(conn, run_id: str = "run1", key: str = "idem-1", payload: str = "sha256:p", **kw: Any) -> dict:
    with tx(conn):
        run, _ = repo.create_run(conn, id=run_id, namespace="pilot_smoke", protocol_hash="sha256:h",
                                 resolved_protocol={"a": 1}, requested_protocol={"a": 1}, execution_mode="PILOT",
                                 idempotency_key=key, payload_sha256=payload, purpose="SMOKE", preset="PILOT_SMOKE", **kw)
    return run


def eval_row(eid: str = "ev1", status: str = "EVALUATED", **over: Any) -> dict:
    row = {"id": eid, "candidate_id": "c1", "name": "cand", "family": "GA-mixed", "arm": "GA", "generation": 0,
           "rung": "L0", "epochs": 2, "status": status, "ap": 0.1, "recall": 0.2, "latency_ms": 3.0, "size_mb": 6.0,
           "fitness": 0.4, "device": "host-gpu-0", "precision": "FP16", "latency_source": "measured", "parents": [],
           "blocks": [], "architecture_path": "/a.yaml", "checkpoint_path": "/c.pt", "artifacts": [],
           "objective_hash": "o", "evaluation_hash": "e", "split_hash": "s", "latency_scope": "fwd+post",
           "comparison_group": "common_scratch", "protocol_hash": "sha256:h", "batch": 1,
           "resolved_input_shape": [640, 640], "search_seed": 101}
    row.update(over)
    return row


def test_pragmas_and_schema(db, settings) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "validations", "candidates", "evaluations", "generations", "jobs", "workers", "events",
            "artifacts", "cost_ledger", "checkpoints", "meta"} <= tables
    connect(settings).close()  # schema re-application is idempotent
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "6"
    # DD-A66 DB v6 (additive): the retention prune round-trip requests
    assert "prune_requests" in tables
    # WP-2 DB v4 (contract §1.1, design §12): the four runner-protocol tables
    assert {"runners", "principals", "idempotent_responses", "artifact_locations"} <= tables
    # WP-4 DB v5 (additive): configuration revisions (F5d) and the per-database salt of the opaque public ids
    assert "config_revisions" in tables
    assert db.execute("SELECT value FROM meta WHERE key='public_id_salt'").fetchone()[0]


def test_writes_require_transaction(db) -> None:
    with pytest.raises(RuntimeError, match="inside db.core.tx"):
        repo.create_run(db, id="r", namespace="n", protocol_hash="h", resolved_protocol={}, requested_protocol={},
                        execution_mode="PILOT", idempotency_key="k", payload_sha256="p")


def test_tx_rollback_and_savepoint(db) -> None:
    make_run(db)
    with pytest.raises(ZeroDivisionError):
        with tx(db):
            repo.append_event(db, "run1", "log", {"m": 1})
            1 / 0
    assert repo.get_run(db, "run1")["last_seq"] == 0 and repo.get_events_after(db, "run1", 0) == []
    with tx(db):
        repo.append_event(db, "run1", "log", {"m": 1})
        with pytest.raises(ValueError):
            with tx(db):
                repo.append_event(db, "run1", "log", {"m": 2})
                raise ValueError("inner")
    assert [e["payload"] for e in repo.get_events_after(db, "run1", 0)] == [{"m": 1}]


def test_run_idempotency(db) -> None:
    run = make_run(db)
    assert run["status"] == "QUEUED" and run["terminal"] == 0 and run["resolved_protocol"] == {"a": 1}
    with tx(db):
        again, created = repo.create_run(db, id="run-other", namespace="x", protocol_hash="h", resolved_protocol={},
                                         requested_protocol={}, execution_mode="PILOT", idempotency_key="idem-1",
                                         payload_sha256="sha256:p")
    assert not created and again["id"] == "run1"
    with pytest.raises(repo.IdempotencyConflict):
        make_run(db, run_id="run2", payload="sha256:different")
    with pytest.raises(sqlite3.IntegrityError):  # the DB constraint backs the Python check
        with tx(db):
            db.execute("INSERT INTO runs (id, namespace, status, protocol_hash, resolved_protocol_json, "
                       "requested_protocol_json, execution_mode, idempotency_key, payload_sha256, created_at, "
                       "updated_at) VALUES ('r3','n','QUEUED','h','{}','{}','PILOT','idem-1','p',?,?)", (T0, T0))


def test_run_status_terminal_is_final(db) -> None:
    make_run(db)
    with tx(db):
        assert repo.update_run_status(db, "run1", "RUNNING")["terminal"] == 0
        done = repo.update_run_status(db, "run1", "CANCELLED", reason_code="USER_CANCEL")
    assert done["terminal"] == 1 and done["reason_code"] == "USER_CANCEL"
    with pytest.raises(repo.InvalidTransition):
        with tx(db):
            repo.update_run_status(db, "run1", "RUNNING")
    with pytest.raises(ValueError):
        with tx(db):
            repo.update_run_status(db, "run1", "CANCELED")


def _candidate(db, cid: str = "c1") -> None:
    with tx(db):
        repo.insert_candidate(db, id=cid, run_id="run1", name="cand", arm="GA", family="GA-mixed", search_seed=101,
                              parents=["p1", "p2"], blocks=[{"name": "C3k2", "source": "p1", "mutated": False,
                                                             "old_name": None}])


def test_evaluation_immutability_and_supersede(db) -> None:
    make_run(db)
    _candidate(db)
    with tx(db):
        prov = repo.insert_evaluation(db, "run1", eval_row(status="TRAINING", ap=None, recall=None, latency_ms=None,
                                                            size_mb=None, fitness=None, latency_source="pending"))
        assert prov["immutable"] == 0
        done = repo.insert_evaluation(db, "run1", eval_row())
    assert done["immutable"] == 1 and done["row"]["ap"] == 0.1 and done["row"]["revision"] == 1
    with pytest.raises(repo.ImmutableViolation):
        with tx(db):
            repo.insert_evaluation(db, "run1", eval_row(ap=0.9))
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_EVALUATION"):
        with tx(db):
            db.execute("UPDATE evaluations SET row_json = '{}' WHERE id = 'ev1'")
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_EVALUATION"):
        with tx(db):
            db.execute("DELETE FROM evaluations WHERE id = 'ev1'")
    with tx(db):
        new = repo.supersede_evaluation(db, "ev1", eval_row("ev1-r2", ap=0.12))
    assert new["revision"] == 2 and new["supersedes_evaluation_id"] == "ev1" and new["row"]["revision"] == 2
    assert [e["id"] for e in repo.list_evaluations(db, "run1")] == ["ev1", "ev1-r2"]
    assert [e["id"] for e in repo.list_evaluations(db, "run1", include_superseded=False)] == ["ev1-r2"]
    with pytest.raises(ValidationError):
        with tx(db):
            repo.insert_evaluation(db, "run1", eval_row("bad", fitness=None))  # EVALUATED needs metrics


def test_generation_invariants_and_completion_lock(db) -> None:
    make_run(db)
    base = {"arm": "GA", "generation": 0, "rung": "L0", "comparison_group": "common_scratch", "search_seed": 101,
            "representative_candidate_id": None, "planning_closed": True, "planned_count": 4, "evaluated_count": 2,
            "failed_count": 1, "not_admitted_count": 0, "pending_count": 1, "complete": False}
    with tx(db):
        g = repo.upsert_generation(db, "run1", base)
    assert g["group_key"] == "GA|101|0|L0|common_scratch" and g["complete"] == 0
    final = {**base, "evaluated_count": 3, "pending_count": 0, "complete": True, "representative_candidate_id": "c1"}
    with tx(db):
        assert repo.upsert_generation(db, "run1", final)["complete"] == 1
        assert repo.upsert_generation(db, "run1", final)["complete"] == 1  # idempotent re-publish
    with pytest.raises(repo.ImmutableViolation):
        with tx(db):
            repo.upsert_generation(db, "run1", {**final, "representative_candidate_id": "c2"})
    with pytest.raises(sqlite3.IntegrityError):  # SQL CHECK: counts must sum to planned
        with tx(db):
            db.execute("UPDATE generations SET planned_count = 9")


def _job(db, jid: str, prio: int = 100, created: str = T0, run_id: str = "run1", max_attempts: int = 3) -> None:
    with tx(db):
        repo.enqueue_job(db, id=jid, run_id=run_id, kind="CANDIDATE_TRAIN", spec={"job_id": jid},
                         idempotency_key=f"key-{jid}", candidate_id="c1", rung="L0", cumulative_target=2,
                         fixed_horizon=6, priority=prio, max_attempts=max_attempts, now=created)


def test_job_idempotency(db) -> None:
    make_run(db)
    _job(db, "j1")
    with tx(db):
        job, created = repo.enqueue_job(db, id="j1-dup", run_id="run1", kind="CANDIDATE_TRAIN", spec={"job_id": "j1"},
                                        idempotency_key="key-j1")
    assert not created and job["id"] == "j1"
    with pytest.raises(repo.IdempotencyConflict):
        with tx(db):
            repo.enqueue_job(db, id="j1-x", run_id="run1", kind="CANDIDATE_TRAIN", spec={"other": 1},
                             idempotency_key="key-j1")


def test_lease_order_renew_finish_and_reclaim(db) -> None:
    make_run(db)
    _job(db, "j-b", prio=100, created=T0)
    _job(db, "j-a", prio=100, created=T0)
    _job(db, "j-hi", prio=10, created=iso_after(T0, 5))
    order = [repo.acquire_lease(db, "w1", T0, 60)["id"] for _ in range(3)]
    assert order == ["j-hi", "j-a", "j-b"] and repo.acquire_lease(db, "w1", T0, 60) is None
    assert repo.mark_job_running(db, "j-a", "w1", T0) and not repo.mark_job_running(db, "j-a", "w2", T0)
    assert repo.renew_lease(db, "j-a", "w1", iso_after(T0, 30), 60)
    assert not repo.renew_lease(db, "j-a", "intruder", iso_after(T0, 30), 60)
    with tx(db):
        assert repo.finish_job(db, "j-hi", "w1", "SUCCEEDED", result={"ok": True})
    later = iso_after(T0, 120)  # j-b lease (exp T0+60) and j-a lease (renewed to T0+90) both expired
    with tx(db):
        reclaimed = repo.reclaim_expired_leases(db, later)
    assert sorted(j["id"] for j in reclaimed) == ["j-a", "j-b"] and {j["status"] for j in reclaimed} == {"QUEUED"}
    assert reclaimed[0]["error"]["reason_code"] == "LEASE_EXPIRED"
    again = repo.acquire_lease(db, "w2", later, 60)
    assert again["attempt"] == 2 and again["lease_owner"] == "w2"
    with tx(db):
        assert not repo.finish_job(db, again["id"], "w1", "FAILED")  # old owner lost the lease


def test_reclaim_fails_when_attempts_exhausted_and_paused_runs_not_leased(db) -> None:
    make_run(db)
    _job(db, "j1", max_attempts=1)
    assert repo.acquire_lease(db, "w1", T0, 1)["attempt"] == 1
    with tx(db):
        (job,) = repo.reclaim_expired_leases(db, iso_after(T0, 10))
    assert job["status"] == "FAILED" and job["error"]["reason_code"] == "LEASE_EXPIRED_MAX_ATTEMPTS"
    _job(db, "j2")
    with tx(db):
        repo.update_run_status(db, "run1", "PAUSED")
    assert repo.acquire_lease(db, "w1", T0, 60) is None
    with tx(db):
        repo.update_run_status(db, "run1", "RESUMING")
        assert repo.cancel_queued_jobs(db, "run1") == 1
    assert repo.acquire_lease(db, "w1", T0, 60) is None


def test_lease_atomic_under_two_connections(settings) -> None:
    setup = connect(settings)
    make_run(setup)
    for i in range(40):
        _job(setup, f"j{i:02d}")
    got: dict[str, list[str]] = {"wA": [], "wB": []}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        conn = connect(settings, init=False)
        barrier.wait()
        while (job := repo.acquire_lease(conn, name, T0, 60)) is not None:
            got[name].append(job["id"])
        conn.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in got]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    leased = got["wA"] + got["wB"]
    assert sorted(leased) == [f"j{i:02d}" for i in range(40)] and len(set(leased)) == 40
    owners = dict(setup.execute("SELECT id, lease_owner FROM jobs").fetchall())
    assert all(owners[j] == w for w, js in got.items() for j in js)
    setup.close()


def test_event_seq_monotonic_across_two_writers(settings) -> None:
    setup = connect(settings)
    make_run(setup)
    errors: list[BaseException] = []

    def writer(tag: str) -> None:
        conn = connect(settings, init=False)
        try:
            for i in range(50):
                with tx(conn):
                    repo.append_event(conn, "run1", "log", {"w": tag, "i": i})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("api", "worker")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    events = repo.get_events_after(setup, "run1", 0, limit=1000)
    assert [e["seq"] for e in events] == list(range(1, 101))
    for tag in ("api", "worker"):
        assert [e["payload"]["i"] for e in events if e["payload"]["w"] == tag] == list(range(50))
    assert repo.get_run(setup, "run1")["last_seq"] == 100 and repo.event_bounds(setup, "run1") == (1, 100)
    assert [e["seq"] for e in repo.get_events_after(setup, "run1", 97)] == [98, 99, 100]
    with pytest.raises(ValueError):
        with tx(setup):
            repo.append_event(setup, "run1", "bogus.type", {})
    with pytest.raises(ValueError):  # contract: candidate.evaluated payload must be a CandidateEvaluation
        with tx(setup):
            repo.append_event(setup, "run1", "candidate.evaluated", {"id": "x"})
    assert repo.get_run(setup, "run1")["last_seq"] == 100
    with pytest.raises(repo.NotFound):
        with tx(setup):
            repo.append_event(setup, "missing-run", "log", {})
    setup.close()


def test_artifacts_scoped_and_relpath_safe(db) -> None:
    make_run(db)
    make_run(db, run_id="run2", key="idem-2")
    with tx(db):
        a = repo.insert_artifact(db, id="a1", run_id="run1", kind="tp", relpath="eval/ev1/tp_0001.png",
                                 evaluation_id="ev1", sample_id="0001", state="READY", meta={"tp": 3})
        for bad in ("/etc/passwd", "../x.png", "a/../../x", ""):
            with pytest.raises(ValueError):
                repo.insert_artifact(db, id="bad", run_id="run1", kind="gt", relpath=bad)
    assert a["meta"] == {"tp": 3} and repo.get_artifact(db, "run2", "a1") is None
    with tx(db):
        assert repo.update_artifact(db, "run1", "a1", state="EXPIRED")["state"] == "EXPIRED"
    assert [x["id"] for x in repo.list_artifacts(db, "run1", evaluation_id="ev1")] == ["a1"]


def test_cost_ledger_aggregation(db) -> None:
    make_run(db)
    assert repo.aggregate_costs(db, "run1") == {}  # nothing measured -> no fake zeros
    with tx(db):
        repo.append_cost(db, run_id="run1", arm="GA", kind="train", gpu_seconds=100, physical=True, billed_arm="GA")
        repo.append_cost(db, run_id="run1", arm="IndependentRandom", kind="train", gpu_seconds=40, physical=True,
                         billed_arm="IndependentRandom")
        repo.append_cost(db, run_id="run1", arm="GA", kind="failed", gpu_seconds=5, physical=True, billed_arm="GA")
        repo.append_cost(db, run_id="run1", arm="Baseline", kind="train", gpu_seconds=30, physical=True)
        repo.append_cost(db, run_id="run1", arm="IndependentRandom", kind="train", gpu_seconds=20, physical=False,
                         billed_arm="IndependentRandom", meta={"reason": "shared cache billed to both arms"})
    costs = repo.aggregate_costs(db, "run1")
    assert costs == {"ga_billed_gpu_seconds": 105.0, "random_billed_gpu_seconds": 60.0, "actual_gpu_seconds": 175.0,
                     "baseline_gpu_seconds": 30.0, "failed_gpu_seconds": 5.0}
    with pytest.raises(sqlite3.IntegrityError):
        with tx(db):
            repo.append_cost(db, run_id="run1", arm="GA", kind="train", gpu_seconds=-1, physical=True)


def test_workers_checkpoints_validations(db) -> None:
    make_run(db)
    with tx(db):
        repo.upsert_worker(db, id="w1", status="IDLE", device="host-gpu-0", device_uuid="GPU-x", row={"pid": 1})
        w = repo.upsert_worker(db, id="w1", status="RUNNING", device="host-gpu-0", run_id="run1", job_id="j1")
        repo.insert_checkpoint(db, id="ck1", run_id="run1", candidate_id="c1", rung="L0", epoch=2,
                               kind="training_full", path="/r/ck1.pt", sha256="ab", bytes=10)
        repo.insert_checkpoint(db, id="ck2", run_id="run1", candidate_id="c1", rung="L1", epoch=4,
                               kind="training_full", path="/r/ck2.pt", sha256="cd", bytes=10, hashes={"protocol": "h"})
        v = repo.insert_validation(db, id="val1", protocol_hash="sha256:h", resolved={"x": 1}, requested_sha256="r",
                                   dataset_manifest_hashes={"train": "sha256:t"})
    assert w["status"] == "RUNNING" and [x["id"] for x in repo.list_workers(db, "run1")] == ["w1"]
    assert repo.latest_checkpoint(db, "c1", "training_full")["id"] == "ck2"
    assert [c["id"] for c in repo.list_checkpoints(db, "run1", candidate_id="c1")] == ["ck1", "ck2"]
    assert v["resolved"] == {"x": 1} and v["dataset_manifest_hashes"] == {"train": "sha256:t"}
    with pytest.raises(sqlite3.IntegrityError):
        with tx(db):
            repo.insert_checkpoint(db, id="ck3", run_id="run1", candidate_id="c1", epoch=1, kind="stripped",
                                   path="/p", sha256="x", bytes=1)
    with pytest.raises(ValueError):
        with tx(db):
            repo.insert_candidate(db, id="c9", run_id="run1", name="n", arm="GA", family="f", parents=["a", "b", "c"])
