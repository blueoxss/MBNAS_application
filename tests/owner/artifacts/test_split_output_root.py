"""DD-A46 acceptance (gateway side, WP-5B + core side, WP-4B): a protocol whose dataset and ``console.paths.output_root``
exist only on the CUSTOMER (gateway) side - outside every allow root of the core - is validated, created, executed by a
runner and COMPLETED through the gateway. The gateway attaches the host inputs it measured (``path_checks`` /
``dataset_yaml`` / ``dataset_manifests``) to ``POST /runs``; without them the core re-validates against its own roots
and refuses the run (the 409 STALE_VALIDATION / path errors of evidence/WP7/7B/split_output_root_probe.txt).

In-process both sides share one filesystem; "absent on the core host" is modelled by core allow roots
(``dataset_roots`` / ``output_roots``) that do not contain the protocol's paths, so every core-local path check fails.
SYNTHETIC data; CPU only."""
from __future__ import annotations

import pytest

import harness
from harness import auth

from mbnas_core.db import repo

pytestmark = pytest.mark.runner_protocol


@pytest.fixture
def core_overrides(tmp_path):
    core_runs, core_data = tmp_path / "core-host" / "runs", tmp_path / "core-host" / "data"
    core_runs.mkdir(parents=True)
    core_data.mkdir(parents=True)
    return {"output_root": str(core_runs), "output_roots": [str(core_runs)], "dataset_roots": [str(core_data)],
            "db_path": str(core_runs / "db" / "mbnas.sqlite3"), "log_dir": str(core_runs / "logs")}


def test_core_alone_refuses_the_customer_paths(core, server_headers, base_proto):
    """Control: the same protocol validated by the core itself (no gateway inputs) fails its path checks."""
    r = core.post("/core/v1/console/protocols/validate", json={"protocol": base_proto()}, headers=server_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert {e["code"] for e in body["errors"]} & {"PATH_OUTSIDE_ALLOWLIST", "PATH_NOT_FOUND"}, body["errors"]


def test_run_with_customer_side_paths_completes_through_the_gateway(gateway, core, server_headers, db,
                                                                    daemon_factory, base_proto, tmp_roots):
    proto = base_proto()
    v = gateway.post("/api/v1/protocols/validate", json={"protocol": proto}, headers=auth())
    assert v.status_code == 200 and v.json()["valid"], v.text
    assert v.json()["capabilities"].get("validation_host") == "gateway"
    body = v.json()
    run_body = {"protocol": body["resolved"], "protocol_hash": body["protocol_hash"], "idempotency_key": "split-1",
                "validation_id": body["validation_id"]}
    # the core alone (no host inputs) cannot re-validate it: this is what DD-A46 removes
    direct = core.post("/core/v1/console/runs", json=run_body, headers=server_headers)
    assert direct.status_code in (409, 422), direct.text
    r = gateway.post("/api/v1/runs", json=run_body, headers=auth())
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    d = daemon_factory()
    assert d.run(once=True) == 0
    if d._retry_thread is not None:
        d._retry_thread.join(30)
    (job,) = repo.list_jobs(db, run_id)
    assert job["status"] == "SUCCEEDED", job.get("error")
    run = gateway.get(f"/api/v1/runs/{run_id}", headers=auth()).json()
    assert run["status"] == "COMPLETED", run["status"]
    arts = repo.list_artifacts(db, run_id)
    assert arts
    for a in arts:  # and its artifacts download through the gateway store
        got = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{a['id']}", headers=auth())
        assert got.status_code == 200 and got.headers["x-artifact-sha256"] == a["sha256"].removeprefix("sha256:")
