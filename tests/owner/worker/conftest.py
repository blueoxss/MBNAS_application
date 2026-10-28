"""Fixtures for API/worker/orchestration tests (helpers live in tests/_shared/harness.py; all synthetic).

WP-3 harness (contract WP-3 §2.8): ``client`` is the GATEWAY app relaying ``/api/v1`` to the core app in-process
(``tests/_shared/combined_fixtures.py``; ``core_client`` for core-only routes), ``inline_worker`` is the owner worker (``mbnas_core.owner_worker``) running handlers in-process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HARNESS_DIR = str(Path(__file__).resolve().parents[2] / "_shared")  # WP-1G: harness moved
if _HARNESS_DIR not in sys.path:  # appended (tests/_shared/datasets.py must not shadow site-packages)
    sys.path.append(_HARNESS_DIR)

import harness  # noqa: E402


@pytest.fixture
def api_settings(tmp_roots):
    return harness.make_settings(tmp_roots, expose_legacy_api=False)  # WP-3: core behind the gateway


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
