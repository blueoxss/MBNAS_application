"""Fixtures of the WP-5B artifact wiring tests (DD-A38/DD-A39/DD-A46; W4 wiring notes). All SYNTHETIC.

The real core app and the real gateway app run in one process (``harness.combined_app``: the gateway reaches the core
through an in-process ASGI transport), and the real ``RunnerDaemon`` (job child = real subprocess with the SYNTHETIC
``remote_handlers``) talks to the same core app through a TestClient transport, exactly like
``tests/owner/runner_protocol`` (whose fixture shapes these mirror). The runner's ``local`` transfer writes into the
gateway's artifact store directory (DD-A39), so a download through the gateway exercises the whole path:
register (E6/E7) -> transfer (local) -> core record -> store bytes with the digest re-check -> location report.

* ``wp5_settings`` / ``combo``: core settings (fixed ticket key, sweeper off, legacy ``/api/v1`` off) whose artifact
  signing key DIFFERS from the gateway's (``CORE_SIGNING_KEY`` vs ``harness.SIGNING_KEY``), so URLs signed in core views
  only work through the gateway's delegation; gateway store / manifest cache in ``tmp_path``.
* ``gateway`` / ``core``: TestClients over the two apps (core lifespan entered first); ``server_headers``: the
  gateway's service token for direct core calls.
* ``daemon_factory(**overrides)``: runner R1 (``artifact_transfer=local`` into the gateway store unless overridden).
* ``finished_run(**daemon_overrides)``: validate + create a Baseline-only run THROUGH THE GATEWAY, run its one job with
  the daemon, return ``(run_id, job)``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
if _SHARED not in sys.path:
    sys.path.append(_SHARED)

import harness  # noqa: E402
import runner_sim as rs  # noqa: E402

CORE_SIGNING_KEY = "core-signing-key-differs-from-the-gateway-0123"


@pytest.fixture
def stub(monkeypatch):
    harness.stub_env(monkeypatch)
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "remote_handlers")  # + the executed architecture.yaml
    return harness


@pytest.fixture
def core_signing_key() -> str:
    return CORE_SIGNING_KEY


@pytest.fixture
def core_overrides() -> dict[str, Any]:
    """Extra CoreSettings for one test module (e.g. split allow roots)."""
    return {}


@pytest.fixture
def wp5_settings(tmp_roots, core_overrides):
    kw = dict(ticket_key=rs.TICKET_KEY, sweeper_enabled=False, expose_legacy_api=False,
              artifact_signing_key=CORE_SIGNING_KEY)
    kw.update(core_overrides)
    return harness.make_settings(tmp_roots, **kw)


@pytest.fixture
def services(wp5_settings, registry, host_devices):
    return harness.make_services(wp5_settings, registry, host_devices)


@pytest.fixture
def store_dir(tmp_path) -> Path:
    return tmp_path / "gateway-store"


@pytest.fixture
def combo(tmp_roots, wp5_settings, services, registry, host_devices, tmp_path, store_dir):
    rs.write_synthetic_probe(wp5_settings.output_root)  # claim-time baseline resolution (runner_sim docstring)
    return harness.combined_app(tmp_roots, registry, host_devices, core_settings=wp5_settings, services=services,
                                gateway_overrides={"artifact_store": str(store_dir),
                                                   "manifest_cache_root": str(tmp_path / "gateway-cache")})


@pytest.fixture
def core(combo, stub):
    from fastapi.testclient import TestClient

    with TestClient(combo.core_app) as c:
        yield c


@pytest.fixture
def gateway(combo, core):
    from fastapi.testclient import TestClient

    with TestClient(combo.gateway_app) as c:
        yield c


@pytest.fixture
def server_headers(combo) -> dict[str, str]:
    return {"Authorization": f"Bearer {combo.tokens['server']}"}


@pytest.fixture
def db(wp5_settings):
    from mbnas_core.db.core import connect

    conn = connect(wp5_settings)
    yield conn
    conn.close()


@pytest.fixture
def runner_token(wp5_settings, core) -> str:
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth

    conn = connect(wp5_settings)
    try:
        _, tok = auth.create_principal(conn, "runner", runner_id="R1")
        return tok
    finally:
        conn.close()


@pytest.fixture
def daemon_factory(core, runner_token, tmp_path, store_dir, stub):
    made: list[Any] = []

    def build(**overrides: Any):
        kw: dict[str, Any] = dict(artifact_transfer="local", artifact_store_dir=str(store_dir),
                                  artifact_transfer_max_tries=50)
        kw.update(overrides)
        if kw.get("artifact_transfer") == "none":
            kw.pop("artifact_store_dir", None)
        settings = rs.daemon_settings(tmp_path / "daemon-R1", runner_token, "R1", **kw)
        d = rs.make_daemon(core, settings)
        made.append(d)
        return d

    yield build
    for d in made:
        d.stopping = True


@pytest.fixture
def base_proto(make_dataset, tmp_roots):
    ds = make_dataset("w", counts={"train": 4, "val": 2, "test": 2})

    def build(targets=(2,), output_root: str | None = None):
        proto = harness.small_protocol(ds, output_root or str(tmp_roots["runs"] / "out"), arms=(),
                                       baselines=("yolo11n",), targets=targets)
        # DD-A66: retention is exercised by test_t3_34_prune_roundtrip; these fixtures keep all states so their subject is unchanged
        proto["console"]["retention"] = {"full_state": "keep_all"}
        return proto
    return build


@pytest.fixture
def finished_run(gateway, daemon_factory, base_proto, db):
    from mbnas_core.db import repo

    def run(protocol: dict[str, Any] | None = None, **daemon_overrides: Any):
        run_id, _ = harness.validate_and_run(gateway, protocol or base_proto())
        d = daemon_factory(**daemon_overrides)
        assert d.run(once=True) == 0
        if d._retry_thread is not None:  # a heartbeat-kicked transfer retry may still be running
            d._retry_thread.join(30)
        (job,) = repo.list_jobs(db, run_id)
        assert job["status"] == "SUCCEEDED", job.get("error")
        return run_id, job

    return run
