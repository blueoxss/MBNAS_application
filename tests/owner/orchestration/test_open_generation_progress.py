"""S9-F3 (contract 1.2.9): open generations are pushed live as ``generation.updated`` events.

While a generation row is open (``complete=false``) the engine emits ``generation.updated`` with the current
Generation row every time the row changes (never when it is unchanged); ``generation.completed`` is still emitted
exactly once, on the transition to complete, and nothing is emitted for a row after that. The snapshot keeps every
row (open or complete) with live counts. Stub numbers are SYNTHETIC.
"""
from __future__ import annotations

import harness
from harness import auth

from mbnas_core.console.projection import project_public
from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect

COUNTS = ("planned_count", "evaluated_count", "failed_count", "not_admitted_count", "pending_count")
GEN_TYPES = ("generation.updated", "generation.completed")


def _key(p: dict) -> tuple:
    return (p["arm"], p["search_seed"], p["generation"], p["rung"], p["comparison_group"])


def _check_generation_events(events: list[dict]) -> dict[tuple, dict]:
    """Per-key invariants of the generation event stream; returns the last payload per key."""
    last: dict[tuple, dict] = {}
    done: set[tuple] = set()
    for e in events:
        if e["type"] not in GEN_TYPES:
            continue
        # contract 1.5.0: the public form of a stored event is its projection (what the SSE stream sends; the stored
        # generation row keeps the owner-only group_key)
        public = project_public("Event", {k: e[k] for k in ("seq", "type", "run_id", "payload")})
        assert contract_errors("Event", public) == [] and "group_key" not in public["payload"]
        p, key = e["payload"], _key(e["payload"])
        assert key not in done, f"event after completion for {key}"
        assert sum(p[k] for k in COUNTS[1:]) == p["planned_count"]
        if e["type"] == "generation.updated":
            assert p["complete"] is False
            assert last.get(key) != p, f"generation.updated without a change for {key}"
        else:
            assert p["complete"] is True and p["pending_count"] == 0
            done.add(key)
        last[key] = p
    return last


def test_open_generations_are_pushed_as_generation_updated(client, protocol, inline_worker, api_settings) -> None:
    run_id, _ = harness.validate_and_run(client, protocol, key="open-gen")
    conn = connect(api_settings, init=False)
    open_pending_pushed = False
    updates_per_key: dict[tuple, int] = {}
    try:
        while inline_worker.tick():
            snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
            assert contract_errors("Snapshot", snap) == []
            db_rows = {g["group_key"]: g for g in repo.list_generations(conn, run_id)}
            assert len(snap["generations"]) == len(db_rows)  # every stored row, open or complete
            events = repo.get_events_after(conn, run_id, 0, 100_000)
            last = _check_generation_events(events)
            # the pushed stream alone reproduces every stored generation row (open rows included)
            for g in snap["generations"]:
                pushed = last.get(_key(g))
                assert pushed is not None, f"generation row {_key(g)} was never pushed"
                assert tuple(pushed[k] for k in COUNTS) == tuple(g[k] for k in COUNTS)
                assert pushed["complete"] == g["complete"]
                if not g["complete"] and g["pending_count"] > 0:
                    open_pending_pushed = True
            updates_per_key = {}
            for e in events:
                if e["type"] == "generation.updated":
                    updates_per_key[_key(e["payload"])] = updates_per_key.get(_key(e["payload"]), 0) + 1
    finally:
        conn.close()
    assert open_pending_pushed, "open generations with pending work must be pushed as generation.updated"
    assert any(n > 1 for n in updates_per_key.values()), "counts must be re-pushed as an open generation progresses"
    final = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    assert final["generations"] and all(g["complete"] for g in final["generations"])
    conn = connect(api_settings, init=False)
    try:
        events = repo.get_events_after(conn, run_id, 0, 100_000)
    finally:
        conn.close()
    completed = [_key(e["payload"]) for e in events if e["type"] == "generation.completed"]
    assert sorted(completed) == sorted({_key(g) for g in final["generations"]})  # exactly once per row
