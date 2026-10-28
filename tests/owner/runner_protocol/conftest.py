"""Fixtures of the T3 runner-protocol matrix (WP-2 contract section 4; design A3 section 14). All SYNTHETIC.

* ``rp_settings``: core settings of ``harness.make_settings`` (lease_ttl_s=30, heartbeat_s=1) with a fixed
  ``ticket_key`` (tickets survive an app re-creation, T3-21) and ``sweeper_enabled=False``: lease reclaim happens only
  when a test calls the ``sweeper`` fixture with an injected clock, never behind the test's back.
* ``core_app`` / ``core``: the real core app (``/api/v1`` console + ``/core/v1/runner`` + ``/core/v1/owner``) and a
  TestClient over it (lifespan entered).
* ``tokens``: principals issued through ``mbnas_core.runner_api.auth`` (runner R1, runner R2, server, admin).
* ``runner_sim`` / ``runner_sim2``: :class:`runner_sim.RunnerSim` for R1 / R2 (each with its own runner work root).
* ``sweeper``: ``sweep(now=None, after_s=None)`` = ``sweeper.sweep_once`` on the injected clock (``after_s`` seconds
  after the real now).
* ``owner_worker``: the in-process owner worker in owner mode (``kinds=[PROPOSE_GENERATION]``, no generation_only
  filter), stub handlers.
* ``daemon_factory``: the real ``RunnerDaemon`` over the TestClient transport (job child = real subprocess).
* ``base_proto``: a Baseline-only protocol (one BASELINE_TRAIN job), the shape ``tests/owner/worker/test_subprocess.py``
  uses; ``search_proto``: GA + Random arms with evaluation (PROPOSE_GENERATION + training jobs).
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

TICKET_KEY = rs.TICKET_KEY


@pytest.fixture
def stub(monkeypatch):
    harness.stub_env(monkeypatch)
    return harness


@pytest.fixture
def rp_settings(tmp_roots):
    return harness.make_settings(tmp_roots, ticket_key=TICKET_KEY, sweeper_enabled=False)


@pytest.fixture
def services(rp_settings, registry, host_devices):
    return harness.make_services(rp_settings, registry, host_devices)


@pytest.fixture
def core_app(rp_settings, services):
    from mbnas_core.api.app import create_app

    rs.write_synthetic_probe(rp_settings.output_root)  # see runner_sim.write_synthetic_probe (WP-2A finding)

    return create_app(rp_settings, services)


@pytest.fixture
def core(core_app, stub):
    from fastapi.testclient import TestClient

    with TestClient(core_app) as c:
        yield c


@pytest.fixture
def db(rp_settings):
    from mbnas_core.db.core import connect

    conn = connect(rp_settings)
    yield conn
    conn.close()


@pytest.fixture
def tokens(rp_settings, core) -> dict[str, Any]:
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth

    conn = connect(rp_settings)
    try:
        out: dict[str, Any] = {}
        for name, kind, rid in (("R1", "runner", "R1"), ("R2", "runner", "R2"), ("server", "server", None),
                                ("admin", "admin", None)):
            row, tok = auth.create_principal(conn, kind, runner_id=rid)
            out[name], out[f"{name}_id"] = tok, row["id"]
        return out
    finally:
        conn.close()


@pytest.fixture
def runner_sim(core, tokens, tmp_path):
    # Warm the runner runtime manifest before any test body runs: its first call in a process imports torch and the
    # runtime packages (about 1.7 s cold, a few ms warm). Done here, that import can never fall inside a short token
    # lifetime a test issues itself (T3-30b issues a 0.5 s token and registers with it; run alone, the cold import made
    # the token expire before /register). The manifest itself is not cached: every register() still computes it.
    rs.runtime_manifest()
    return rs.RunnerSim(core, tokens["R1"], "R1", tmp_path / "runner-R1")


@pytest.fixture
def runner_sim2(core, tokens, tmp_path):
    return rs.RunnerSim(core, tokens["R2"], "R2", tmp_path / "runner-R2")


@pytest.fixture
def sweeper(rp_settings, core):
    from mbnas_contracts.util.time import iso_after, utcnow_iso
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api.sweeper import sweep_once

    def sweep(now: str | None = None, after_s: float | None = None) -> dict[str, Any]:
        when = now or (iso_after(utcnow_iso(), after_s) if after_s is not None else utcnow_iso())
        conn = connect(rp_settings, init=False)
        try:
            return sweep_once(conn, rp_settings, now=when)
        finally:
            conn.close()

    return sweep


@pytest.fixture
def owner_worker(rp_settings, stub):
    w = harness.make_inline_worker(rp_settings, "owner-worker:1")
    w.cfg.kinds, w.cfg.generation_only = ["PROPOSE_GENERATION"], None  # owner mode (contract section 1.7)
    yield w
    w.conn.close()


@pytest.fixture
def daemon_factory(core, tokens, tmp_path, stub):
    made: list[Any] = []

    def build(runner_id: str = "R1", **overrides: Any):
        settings = rs.daemon_settings(tmp_path / f"daemon-{runner_id}", tokens[runner_id], runner_id, **overrides)
        d = rs.make_daemon(core, settings)
        made.append(d)
        return d

    yield build
    for d in made:
        d.stopping = True


@pytest.fixture
def base_proto(make_dataset, tmp_roots):
    ds = make_dataset("w", counts={"train": 4, "val": 2, "test": 2})

    def build(targets=(2,)):
        proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=(), baselines=("yolo11n",),
                                       targets=targets)
        # DD-A66: retention is exercised by test_t3_34_prune_roundtrip; these fixtures keep all states so their subject is unchanged
        proto["console"]["retention"] = {"full_state": "keep_all"}
        return proto
    return build


@pytest.fixture
def search_proto(make_dataset, tmp_roots):
    ds = make_dataset("ds", counts={"train": 4, "val": 2, "test": 2})
    return harness.small_protocol(ds, str(tmp_roots["runs"] / "out"))
