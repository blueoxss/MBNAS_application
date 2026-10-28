"""T3-30..33 (design A3 sections 11, 14, D6): principal scopes and runner binding, expiry/revocation, rotation grace,
TLS refusal on the runner side, request body limits. SYNTHETIC."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import harness
import pytest
import runner_sim as rs

from mbnas_core.db import repo
from mbnas_core.db.core import connect

pytestmark = pytest.mark.runner_protocol


def _state(db, run_id):
    return ([(j["id"], j["status"], j["attempt"], j["lease_owner"]) for j in repo.list_jobs(db, run_id)],
            [dict(r) for r in db.execute("SELECT id, status, last_seen_at FROM runners ORDER BY id")],
            [(c["id"], c["gpu_seconds"]) for c in repo.list_costs(db, run_id)])


def test_runner_token_bound_to_other_runner_id_is_403(core, runner_sim, tokens, base_proto, db, tmp_path):
    """T3-30a: R2's valid token naming runner R1 is refused 403 RUNNER_MISMATCH on every route; the DB is unchanged."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    runner_sim.start(lease)
    before = _state(db, run_id)
    impostor = rs.RunnerSim(core, tokens["R2"], "R1", tmp_path / "imp")
    calls = [impostor.post("/register", {"runner_id": "R1", "runtime": rs.runtime_manifest()}),
             impostor.claim_raw(), impostor.heartbeat(lease), impostor.start_raw(lease),
             impostor.post(f"/jobs/{lease.job_id}/ack", lease.fence()), impostor.deregister()]
    for r in calls:
        assert r.status_code == 403 and r.json()["code"] == "RUNNER_MISMATCH", r.text
    assert _state(db, run_id) == before


def test_expired_or_revoked_token_is_401(core, rp_settings, runner_sim, tokens, base_proto, db):
    """T3-30b: an expired or a revoked runner token is 401 AUTH_REQUIRED (same answer as an unknown token); no write."""
    from mbnas_core.runner_api import auth

    run_id, _ = harness.validate_and_run(core, base_proto())
    conn = connect(rp_settings, init=False)
    try:
        _, short = auth.create_principal(conn, "runner", runner_id="R1", ttl_s=0.5)
    finally:
        conn.close()
    sim = rs.RunnerSim(core, short, "R1", runner_sim.work_root)
    sim.register()
    time.sleep(0.8)
    before = _state(db, run_id)
    expired = sim.claim_raw()
    assert expired.status_code == 401 and expired.json()["code"] == "AUTH_REQUIRED"
    conn = connect(rp_settings, init=False)
    try:
        auth.revoke_principal(conn, tokens["R1_id"])
    finally:
        conn.close()
    revoked = runner_sim.claim_raw()
    assert revoked.status_code == 401 and revoked.json()["code"] == "AUTH_REQUIRED"
    unknown = rs.RunnerSim(core, "mbnas_runner_00000000_" + "x" * 43, "R1", runner_sim.work_root).claim_raw()
    assert unknown.status_code == 401 and unknown.json()["code"] == "AUTH_REQUIRED"
    assert core.post(rs.PREFIX + "/claim", json={"runner_id": "R1"}).status_code == 401  # no header
    assert _state(db, run_id) == before


def test_server_scope_token_cannot_call_runner_routes(core, runner_sim, tokens, base_proto, db):
    """T3-30c: server-scope tokens get 403 ACCESS_DENIED on /core/v1/runner/*; runner tokens get 403 on the owner
    routes; only admin reads /core/v1/owner/* (and principals never carry their hash)."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    before = _state(db, run_id)
    srv = core.post(rs.PREFIX + "/claim", json={"runner_id": "R1"}, headers=rs.H(tokens["server"]))
    assert srv.status_code == 403 and srv.json()["code"] == "ACCESS_DENIED"
    srv = core.post(rs.PREFIX + "/register", json={"runner_id": "R1", "runtime": rs.runtime_manifest()},
                    headers=rs.H(tokens["server"]))
    assert srv.status_code == 403 and srv.json()["code"] == "ACCESS_DENIED"
    assert _state(db, run_id) == before
    for tok in (tokens["R1"], tokens["server"]):
        r = core.get("/core/v1/owner/runners", headers=rs.H(tok))
        assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED"
    runner_sim.register()
    rows = core.get("/core/v1/owner/runners", headers=rs.H(tokens["admin"])).json()["runners"]
    assert {r["id"]: r["status"] for r in rows}["R1"] == "ONLINE"
    principals = core.get("/core/v1/owner/principals", headers=rs.H(tokens["admin"])).json()["principals"]
    assert len(principals) == 4 and all("token_hash" not in p for p in principals)
    assert not any(tokens[k] in json.dumps(principals) for k in ("R1", "R2", "server", "admin"))


def test_rotated_token_overlaps_for_grace_then_old_is_rejected(core, rp_settings, runner_sim, tokens):
    """T3-31: after rotation both tokens work during the grace window; afterwards the old one is 401."""
    from mbnas_core.runner_api import auth

    conn = connect(rp_settings, init=False)
    try:
        _, new_tok = auth.rotate_principal(conn, tokens["R1_id"], grace_s=1.0)
    finally:
        conn.close()
    new = rs.RunnerSim(core, new_tok, "R1", runner_sim.work_root)
    assert runner_sim.post("/register", {"runner_id": "R1", "runtime": rs.runtime_manifest()}).status_code == 200
    assert new.post("/register", {"runner_id": "R1", "runtime": rs.runtime_manifest()}).status_code == 200
    time.sleep(1.3)
    old = runner_sim.post("/register", {"runner_id": "R1", "runtime": rs.runtime_manifest()})
    assert old.status_code == 401 and old.json()["code"] == "AUTH_REQUIRED"
    assert new.post("/register", {"runner_id": "R1", "runtime": rs.runtime_manifest()}).status_code == 200


def test_runner_refuses_plain_http_to_non_loopback_core(tmp_path):
    """T3-32 / DD-A18: ``core_url`` plain http to a non-loopback host refuses to start (CORE_URL_INSECURE, CLI exit
    2); loopback http and https are accepted."""
    from mbnas_runner.client import CoreClient, RunnerConfigError
    from mbnas_runner.daemon import RunnerDaemon

    for url in ("http://10.1.2.3:8700", "http://core.example:8700", "http://192.168.0.5"):
        with pytest.raises(RunnerConfigError) as exc:
            RunnerDaemon(rs.daemon_settings(tmp_path / "w", "tok", "R1", core_url=url))
        assert exc.value.code == "CORE_URL_INSECURE"
    for url in ("http://127.0.0.1:8700", "http://localhost:8700", "http://[::1]:8700", "https://core.example:8700"):
        CoreClient(rs.daemon_settings(tmp_path / "w", "tok", "R1", core_url=url)).close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update(MBNAS_RUNNER_CORE_URL="http://10.9.9.9:8700", MBNAS_RUNNER_TOKEN="x", MBNAS_HOME=str(tmp_path / "home"),
               CUDA_VISIBLE_DEVICES="")
    p = subprocess.run([sys.executable, "-B", "-m", "mbnas_runner", "run", "--once"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert p.returncode == 2 and "CORE_URL_INSECURE" in p.stderr


def test_runner_routes_have_their_own_32mib_body_limit(core, runner_sim):
    """T3-33 (core part): runner routes accept bodies above the 1 MiB console limit (rejected by the model, not by
    size) and answer 413 PAYLOAD_TOO_LARGE above 32 MiB; console routes keep 1 MiB."""
    big = "x" * (2 << 20)
    r = core.post(rs.PREFIX + "/register", content=('{"runner_id":"R1","pad":"' + big + '"}').encode(),
                  headers={**rs.H(runner_sim.token), "content-type": "application/json"})
    assert r.status_code == 422, r.text[:200]
    huge = "x" * ((32 << 20) + 1024)
    r = core.post(rs.PREFIX + "/register", content=('{"runner_id":"R1","pad":"' + huge + '"}').encode(),
                  headers={**rs.H(runner_sim.token), "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert "location" not in r.json()  # C05 D3: the runner protocol ErrorBody has no location field
    r = core.post("/api/v1/protocols/validate", content=('{"pad":"' + big + '"}').encode(),
                  headers={**harness.auth(), "content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["location"] == "core"  # the legacy owner debug mount is core, not the gateway its path suggests
    r = core.post("/core/v1/console/protocols/validate", content=('{"pad":"' + big + '"}').encode(),
                  headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["location"] == "core"  # refused before authentication too
