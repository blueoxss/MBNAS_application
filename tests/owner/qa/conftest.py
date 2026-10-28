"""Fixtures for the QA pass (tests/qa). Everything here is SYNTHETIC: tmp DB/output root, stub handlers, no GPU.

The shared API fixtures (settings, client, protocol, inline_worker) mirror tests/owner/orchestration/conftest.py; the
helpers themselves live in tests/_shared/harness.py (core app + owner worker, contract §9).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[2]
if str(_TESTS / "_shared") not in sys.path:  # harness / qa_handlers / stub_handlers (appended: see tests/_shared)
    sys.path.append(str(_TESTS / "_shared"))
if str(Path(__file__).resolve().parent) not in sys.path:  # `from conftest import codes_of` in this directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402


@pytest.fixture
def api_settings(tmp_roots):
    return harness.make_settings(tmp_roots, max_replay_events=5000, expose_legacy_api=False)  # WP-3: behind the gateway  # full replays from seq 0 must be possible here


@pytest.fixture
def services(api_settings, registry, host_devices):
    return harness.make_services(api_settings, registry, host_devices)


from combined_fixtures import *  # noqa: E402,F401,F403  (WP-3C: app = gateway, core_app, client, core_client)


@pytest.fixture
def stub(monkeypatch):
    harness.stub_env(monkeypatch)
    return harness


@pytest.fixture
def protocol(make_dataset, tmp_roots):
    ds = make_dataset("ds", counts={"train": 4, "val": 2, "test": 2})
    return harness.small_protocol(ds, str(tmp_roots["runs"] / "out"))


@pytest.fixture
def inline_worker(api_settings, stub):
    w = harness.make_inline_worker(api_settings)
    yield w
    w.conn.close()


@pytest.fixture
def conn(api_settings):
    from mbnas_core.db.core import connect

    c = connect(api_settings, init=False)
    yield c
    c.close()


@pytest.fixture
def validate(client):
    """POST /protocols/validate with the bearer token; returns the response."""

    def call(proto, **kw):
        return client.post("/api/v1/protocols/validate", json={"protocol": proto}, headers=harness.auth(), **kw)

    return call


def codes_of(body: dict) -> set[str]:
    return {e["code"] for e in body.get("errors", [])}


@pytest.fixture
def qa_env(monkeypatch, api_settings):
    """Real proposers + controllable synthetic metrics for the in-process worker (real handlers read Settings.load())."""
    harness.stub_env(monkeypatch)
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "qa_handlers")
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", api_settings.output_root)  # core proposers: CoreSettings.load()
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOTS", ",".join(api_settings.output_roots))
    monkeypatch.setenv("MBNAS_CORE_DATASET_ROOTS", ",".join(api_settings.dataset_roots))
    monkeypatch.setenv("MBNAS_CORE_DB_PATH", api_settings.db_path)
    monkeypatch.setenv("MBNAS_RUNNER_WORK_ROOT", api_settings.output_root)  # runner handlers: RunnerSettings.load()
    monkeypatch.setenv("MBNAS_RUNNER_OUTPUT_ROOTS", ",".join(api_settings.output_roots))
    monkeypatch.setenv("MBNAS_RUNNER_DATASET_ROOTS", ",".join(api_settings.dataset_roots))
    return monkeypatch


@pytest.fixture
def run_qa(client, make_dataset, tmp_roots, inline_worker, conn, qa_env):
    """``run_qa(arms, key, salts={'L0': 'a'}, generations=3, population=3, budget_h=0.75)`` -> run id after draining."""
    import copy

    def go(arms=("GA", "IndependentRandom"), key="qa-1", salts=None, generations=3, population=3, baselines=(),
           budget_h=0.75, mutate=None):
        if any(a in ("GA", "IndependentRandom") for a in arms):  # 2026-10-15: real proposers need torch + ultralytics
            pytest.importorskip("torch", reason="real search proposers need torch + ultralytics (not installed here)")
        for rung in ("L0", "L1"):
            qa_env.delenv(f"MBNAS_QA_SALT_{rung}", raising=False)
        for rung, salt in (salts or {}).items():
            qa_env.setenv(f"MBNAS_QA_SALT_{rung}", salt)
        ds = make_dataset("qa-ds", counts={"train": 4, "val": 2, "test": 2})
        proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=arms, generations=generations,
                                       population=population, baselines=baselines, budget_h=budget_h)
        if not baselines:
            proto["console"].pop("baseline_budget_gpu_hours", None)
        if mutate:
            mutate(proto)
        run_id, _ = harness.validate_and_run(client, copy.deepcopy(proto), key=key)
        harness.drain(inline_worker)
        return run_id

    return go
