"""A BLOCKED JobResult (target unavailable) is final: never retried, stored as FAILED with the BLOCKED result (stubs)."""
from __future__ import annotations

import harness

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.scheduling import jobs


def test_blocked_benchmark_job_is_final_and_not_retried(client, protocol, inline_worker, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol, key="blocked-1")
    conn = connect(api_settings, init=False)
    try:
        with tx(conn):
            job, _ = jobs.ensure_job(conn, run_id=run_id, kind="BENCHMARK", protocol_hash=repo.get_run(conn, run_id)["protocol_hash"],
                                     params={"target": "jetson-orin-nx"}, key_extra="target-jetson")
        harness.drain(inline_worker)
        done = repo.get_job(conn, job["id"])
        assert done["status"] == "FAILED" and done["attempt"] == 1  # one attempt, never requeued
        assert done["error"]["status"] == "BLOCKED" and done["error"]["reason_code"] == "TARGET_BLOCKED"
        rows = [c for c in repo.list_costs(conn, run_id) if c["job_id"] == job["id"]]
        assert len(rows) == 1 and rows[0]["kind"] == "failed" and rows[0]["billed_arm"] is None
        logs = [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 10_000) if e["type"] == "log"]
        assert any("BENCHMARK" in p["message"] and "BLOCKED" in p["message"] for p in logs)
        assert repo.get_run(conn, run_id)["status"] == "COMPLETED"
    finally:
        conn.close()
