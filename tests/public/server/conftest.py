"""Fixtures for the archive API tests: synthetic runs in tmp roots, an app/client over them, real-data constants.

The real runs under ``$MBNAS_TEST_ARCHIVE_RUNS_ROOT`` (``realdata.py``) are opened read-only; tests that need them carry
the ``host_data`` marker and skip when they are absent. The app is the gateway (``mbnas_server.api.app.create_app`` over
``GatewaySettings``; WP-1 contract §9).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_WORKER = str(Path(__file__).resolve().parents[2] / "_shared")
if _WORKER not in sys.path:  # appended, never inserted at 0: other suites import their own ``conftest`` by name
    sys.path.append(_WORKER)


def _load(alias: str, filename: str):
    """Import a helper of this directory under a unique module name (no sys.path change, no clash with other conftests)."""
    spec = importlib.util.spec_from_file_location(alias, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


synth = _load("archive_synth", "synth.py")
realdata = _load("archive_realdata", "realdata.py")
# tests/_shared/harness.py is owner-only (DD-A43(b)): only the client fixtures below need it, so tests without a client
# still run in a bundle that does not ship it
from conftest_common import owner_harness  # noqa: E402

REAL_DRIVERS, REAL_ROOTS = realdata.REAL_DRIVERS, realdata.REAL_ROOTS


@pytest.fixture
def synth_root(tmp_path: Path) -> Path:
    root = tmp_path / "yolo_ga_full_synth"
    root.mkdir()
    synth.make_run(root, "synthetic-day")
    return root


@pytest.fixture
def make_client(tmp_roots):
    """Factory: ``make_client(roots, drivers=(), ttl=5.0)`` -> (TestClient, app); clients are closed at teardown."""
    from fastapi.testclient import TestClient

    harness = owner_harness()

    from mbnas_server.api.app import create_app

    opened = []

    def factory(roots, drivers=(), ttl=5.0, **overrides):
        settings = harness.make_gateway_settings(tmp_roots, archive_roots=[str(r) for r in roots],
                                                 archive_driver_dirs=[str(d) for d in drivers], archive_cache_ttl_s=ttl,
                                                 **overrides)
        app = create_app(settings)
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        c.headers.update(harness.auth())
        return c, app

    yield factory
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client, synth_root):
    return make_client([synth_root])[0]


@pytest.fixture
def real_client(make_client):
    return make_client(REAL_ROOTS, REAL_DRIVERS)[0]
