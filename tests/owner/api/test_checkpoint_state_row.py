"""S10-F4: every served CandidateEvaluation row carries ``checkpoint_state`` for its ``checkpoint_path``.

PRESENT = the file exists; PRUNED = the file is gone and the run's ``checkpoints`` table records the deletion
(retention policy, ``state='PRUNED'``); MISSING = the file is gone without such a record; null = the row has no
checkpoint_path (provisional rows). The stored immutable row is never rewritten: the state is a view overlay.
Stub numbers are SYNTHETIC.
"""
from __future__ import annotations

import os

import harness
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.console.views import checkpoint_state
from mbnas_core.db import repo
from mbnas_core.db.core import tx


def _rows(client, run_id: str) -> list[dict]:
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    for r in snap["candidates"]:
        assert contract_errors("CandidateEvaluation", r) == [], r["id"]
    return snap["candidates"]


def test_state_rule() -> None:
    assert checkpoint_state(None, set()) is None and checkpoint_state("", {""}) is None
    assert checkpoint_state(__file__, {__file__}) == "PRESENT"  # an existing file is PRESENT whatever the record says
    assert checkpoint_state("/nonexistent/full_e0001.pt", {"/nonexistent/full_e0001.pt"}) == "PRUNED"
    assert checkpoint_state("/nonexistent/full_e0001.pt", set()) == "MISSING"


def test_rows_carry_present_pruned_missing_and_null(client, protocol, inline_worker) -> None:
    run_id, _ = harness.validate_and_run(client, protocol, key="ckpt-state")
    harness.drain(inline_worker)
    rows = _rows(client, run_id)
    evaluated = [r for r in rows if r["status"] == "EVALUATED"]
    assert len(evaluated) >= 3
    assert all(r["checkpoint_state"] == "PRESENT" for r in evaluated), [r.get("checkpoint_state") for r in evaluated]
    assert all(r.get("checkpoint_state") is None for r in rows if not r.get("checkpoint_path"))

    pruned, missing = evaluated[0], evaluated[1]
    conn = inline_worker.conn
    # contract 1.5.0 (value rule "paths"): the served checkpoint_path is run-relative; the stored row keeps the
    # absolute location on the trainer host, which retention records and deletes
    stored = {r["id"]: repo.get_evaluation(conn, r["id"])["row"]["checkpoint_path"] for r in (pruned, missing)}
    for r in (pruned, missing):
        assert not r["checkpoint_path"].startswith("/") and stored[r["id"]].endswith("/" + r["checkpoint_path"]), r
    with tx(conn):  # what retention does: record the deletion on the checkpoint row, then the file is gone
        repo.insert_checkpoint(conn, id="ck-s10f4", run_id=run_id, candidate_id=pruned["candidate_id"], epoch=1,
                               kind="training_full", path=stored[pruned["id"]], sha256="0" * 64, bytes=1)
        repo.mark_checkpoint_pruned(conn, "ck-s10f4", prune={"policy": "last_rung_end_only", "reason": "test"})
    os.remove(stored[pruned["id"]])
    os.remove(stored[missing["id"]])  # deleted outside the retention policy: no record

    by_id = {r["id"]: r for r in _rows(client, run_id)}
    assert by_id[pruned["id"]]["checkpoint_state"] == "PRUNED"
    assert by_id[missing["id"]]["checkpoint_state"] == "MISSING"
    assert by_id[pruned["id"]]["checkpoint_path"] == pruned["checkpoint_path"]  # the (run-relative) location is still shown
    others = [r for r in evaluated[2:] if r["checkpoint_path"] not in (pruned["checkpoint_path"], missing["checkpoint_path"])]
    assert others and all(by_id[r["id"]]["checkpoint_state"] == "PRESENT" for r in others)

    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{pruned['candidate_id']}", headers=auth()).json()
    assert contract_errors("CandidateDetail", detail) == []
    ev = next(e for e in detail["evaluations"] if e["id"] == pruned["id"])
    assert ev["checkpoint_state"] == "PRUNED"
    stored = repo.get_evaluation(conn, pruned["id"])
    assert "checkpoint_state" not in stored["row"]  # view overlay only; the immutable row is unchanged
