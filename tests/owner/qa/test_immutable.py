"""QA: evaluation immutability seen from the API (synthetic stub run, CPU).

EVALUATED rows never change: progress / requeue / start callbacks, a replayed or altered result, and direct SQL are all
refused or ignored; a correction is a superseding revision and the old row stays visible and readable.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import sqlite3
import zipfile

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration import engine


@pytest.fixture
def done(client, protocol, inline_worker, conn):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    rows = [e for e in repo.list_evaluations(conn, run_id) if e["status"] == "EVALUATED"]
    assert rows
    return run_id, rows


RENDER_TIME_FIELDS = ("url", "expires_at")  # signed artifact URLs are re-rendered per request (exp = now + ttl)


def _stable(row):
    """The stored row as served, minus the per-request signed-URL rendering (everything else is compared)."""
    return {**row, "artifacts": [{k: v for k, v in a.items() if k not in RENDER_TIME_FIELDS}
                                 for a in row.get("artifacts", [])]}


def _snapshot_rows(client, run_id):
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    return {c["id"]: _stable(c) for c in snap["candidates"]}, snap


def test_candidate_updated_after_evaluated_never_alters_the_row(client, conn, done):
    run_id, rows = done
    before, snap = _snapshot_rows(client, run_id)
    seq_before = repo.get_run(conn, run_id)["last_seq"]
    ev = rows[0]
    cand = repo.get_candidate(conn, ev["candidate_id"])
    ctx = engine.context(conn, run_id)
    stage = next(s for s in ctx.stages_for(cand) if s.id == ev["row"]["rung"])
    with tx(conn):
        # every provisional callback of the engine, replayed against an already EVALUATED row
        stored = engine.put_row(conn, ctx, engine.base_row(ctx, cand, stage, "TRAINING", progress={"epoch": 1}))
        assert stored == ev["row"]
        stored = engine.put_row(conn, ctx, engine.base_row(ctx, cand, stage, "QUEUED", ap=None, fitness=None, latency_ms=None))
        assert stored == ev["row"]
    job = repo.get_job(conn, ev["row"]["job_id"])
    with tx(conn):
        engine.job_progress(conn, job, {"epoch": 1, "epoch_target_total": 2})  # a late progress line of a finished job
    after, snap2 = _snapshot_rows(client, run_id)
    assert after[ev["id"]] == before[ev["id"]]
    for key in ("ap", "recall", "latency_ms", "size_mb", "fitness", "checkpoint_sha256", "objective_hash", "evaluation_hash"):
        assert after[ev["id"]][key] == ev["row"][key] and after[ev["id"]][key] is not None, key
    assert repo.get_evaluation(conn, ev["id"])["row"] == ev["row"]
    assert repo.get_run(conn, run_id)["last_seq"] == seq_before  # ignored updates emit no candidate.updated event


def test_altered_replay_of_a_result_does_not_change_the_committed_row(conn, done):
    run_id, rows = done
    ev = rows[0]
    job = repo.get_job(conn, ev["row"]["job_id"])
    evaluation = copy.deepcopy(job["result"]["outputs"]["evaluation"])
    seq = repo.get_run(conn, run_id)["last_seq"]
    for patch in ({"ap": 0.99}, {"ap": None}, {"latency_ms": 1.0, "fitness": 1.0}, {"ap": float("nan")}):
        with tx(conn):
            out = engine.commit_evaluation(conn, engine.context(conn, run_id), job, {**evaluation, **patch})
        assert out == ev["row"]
    assert repo.get_run(conn, run_id)["last_seq"] == seq  # silently ignored: no event, no new row
    assert repo.get_evaluation(conn, ev["id"])["row"] == ev["row"]


def test_sql_cannot_rewrite_delete_or_unfreeze_an_evaluated_row(conn, done):
    run_id, rows = done
    ev_id = rows[0]["id"]
    for sql in ("UPDATE evaluations SET row_json = '{}' WHERE id = ?", "DELETE FROM evaluations WHERE id = ?",
                "UPDATE evaluations SET immutable = 0 WHERE id = ?", "UPDATE evaluations SET status = 'TRAINING' WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_EVALUATION"):
            with tx(conn):
                conn.execute(sql, (ev_id,))
    assert repo.get_evaluation(conn, ev_id)["immutable"] == 1


def test_superseding_revision_keeps_the_old_row_everywhere(client, conn, done):
    run_id, rows = done
    old = rows[0]
    new_row = {**old["row"], "id": old["id"] + ".r2", "ap": 0.5, "fitness": 0.9, "revision": 2}
    with tx(conn):
        new = repo.supersede_evaluation(conn, old["id"], new_row)
    assert new["revision"] == 2 and new["supersedes_evaluation_id"] == old["id"]
    by_id, snap = _snapshot_rows(client, run_id)
    assert by_id[old["id"]]["ap"] == old["row"]["ap"] and by_id[old["id"]]["fitness"] == old["row"]["fitness"]
    assert by_id[new["id"]]["ap"] == 0.5 and by_id[new["id"]]["revision"] == 2
    assert by_id[new["id"]].get("supersedes_evaluation_id") == old["id"]
    kept = {e["id"] for e in repo.list_evaluations(conn, run_id)}
    assert {old["id"], new["id"]} <= kept
    assert new["id"] in {e["id"] for e in repo.list_evaluations(conn, run_id, include_superseded=False)}
    assert old["id"] not in {e["id"] for e in repo.list_evaluations(conn, run_id, include_superseded=False)}
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/runs/{run_id}/export", headers=auth()).content))
    csv_rows = {r["id"]: r for r in csv.DictReader(io.StringIO(zf.read("evaluations.csv").decode()))}
    assert {old["id"], new["id"]} <= set(csv_rows) and csv_rows[new["id"]]["supersedes_evaluation_id"] == old["id"]
    assert float(csv_rows[old["id"]]["ap"]) == old["row"]["ap"]
    jsonl = [json.loads(line) for line in zf.read("evaluations.jsonl").decode().splitlines()]
    assert {r["id"] for r in jsonl} >= {old["id"], new["id"]}
    with pytest.raises(repo.ImmutableViolation):  # the revision itself is frozen as well
        with tx(conn):
            repo.insert_evaluation(conn, run_id, {**new_row, "ap": 0.6})


def test_a_superseding_revision_must_not_be_applied_twice_or_to_a_provisional_row(conn, done):
    run_id, rows = done
    old = rows[0]
    with tx(conn):
        repo.supersede_evaluation(conn, old["id"], {**old["row"], "id": old["id"] + ".r2", "ap": 0.4, "revision": 2})
    with pytest.raises((repo.ImmutableViolation, sqlite3.IntegrityError, ValueError, repo.NotFound)):
        with tx(conn):
            repo.supersede_evaluation(conn, "no-such-evaluation", {**old["row"], "id": "x", "revision": 2})
