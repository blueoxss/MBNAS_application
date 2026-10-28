"""T3-33 (runner part, design D6): ``test_result_body_over_limit_is_413_and_runner_externalises_per_sample``.

The core here has a small runner body limit (256 KiB) so a SYNTHETIC per-image list of ~400 KB (``remote_handlers``,
``MBNAS_STUB_PER_SAMPLE_BYTES``) pushes the result over it. Expected by the design: the oversize commit is 413, and the
runner then commits a body below the limit with ``per_sample`` moved into an ``evaluation.json`` artifact reference, so
the job still commits (SUCCEEDED, EVALUATED row)."""
from __future__ import annotations

import harness
import pytest
import runner_sim as rs

from mbnas_core.db import repo

pytestmark = [pytest.mark.runner_protocol, pytest.mark.slow]
LIMIT = 256 << 10


@pytest.fixture
def rp_settings(tmp_roots):
    return harness.make_settings(tmp_roots, ticket_key=rs.TICKET_KEY, sweeper_enabled=False,
                                 runner_body_limit_bytes=LIMIT)


def test_result_body_over_limit_is_413_and_runner_externalises_per_sample(core, runner_sim, daemon_factory, base_proto,
                                                                          db, monkeypatch):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "remote_handlers")
    monkeypatch.setenv("MBNAS_STUB_PER_SAMPLE_BYTES", str(400 << 10))
    run_id, _ = harness.validate_and_run(core, base_proto())
    # (a) the core: a commit above the runner limit is 413 PAYLOAD_TOO_LARGE
    runner_sim.register()
    lease = runner_sim.claim()
    out = runner_sim.run_attempt(lease, commit=False)
    assert len(str(out["result"].outputs["evaluation"]["per_sample"])) > LIMIT
    body = runner_sim.commit_body(lease, out["result"], artifact_ids=out["artifact_ids"])
    r = runner_sim.result(lease, body)
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert repo.get_job(db, lease.job_id)["status"] == "RUNNING"
    runner_sim.deregister()
    # (b) the runner daemon on a fresh job of the same shape: the commit must get through below the limit
    run2, _ = harness.validate_and_run(core, base_proto(), key="idem-t333")
    d = daemon_factory("R2")
    d.run(max_jobs=1)
    job = repo.list_jobs(db, run2)[0]
    assert job["status"] == "SUCCEEDED", (job["status"], job.get("error"))
    ev = [e for e in repo.list_evaluations(db, run2) if e["status"] == "EVALUATED"]
    assert len(ev) == 1
    assert "per_sample" not in (job["result"]["outputs"].get("evaluation") or {}) or \
        len(str(job["result"]["outputs"]["evaluation"]["per_sample"])) < LIMIT
