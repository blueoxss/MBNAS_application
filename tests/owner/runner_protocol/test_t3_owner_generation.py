"""T3-37 (design A3 section 14): ``test_sigkill_mid_generation_recovers_saved_candidates_once`` - the owner path is
unchanged by WP-2. The assertions are exactly those of tests/owner/worker/test_generation_crash.py:81 (imported and
called, not copied); the only difference is the worker command line: the owner worker's default (owner) mode instead
of ``--cpu``, i.e. the configuration ``mbnas-core-owner-worker`` starts with after WP-2 (contract section 1.7).
REAL CPU generation backend (ultralytics + ETRI-aware search space), no stub handler, no GPU."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import harness
import pytest

pytestmark = [pytest.mark.runner_protocol, pytest.mark.owner_worker, pytest.mark.slow]
CRASH_TEST = Path(__file__).resolve().parents[1] / "worker" / "test_generation_crash.py"


@pytest.fixture
def api_settings(tmp_roots):
    return harness.make_settings(tmp_roots)


@pytest.fixture
def client(api_settings, registry, host_devices):
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app

    with TestClient(create_app(api_settings, harness.make_services(api_settings, registry, host_devices))) as c:
        yield c


def _load_crash_module():
    spec = importlib.util.spec_from_file_location("t3_37_generation_crash", CRASH_TEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sigkill_mid_generation_recovers_saved_candidates_once(client, api_settings, tmp_roots, tmp_path, monkeypatch):
    mod = _load_crash_module()
    argvs: list[list[str]] = []

    def spawn_default_owner(settings, tmp, name, worker_id, **extra):
        argv = [sys.executable, "-m", "mbnas_core.owner_worker", "--poll-s", "0.1", "--kill-grace-s", "2",
                "--worker-id", worker_id]  # no --cpu: the default owner mode
        argvs.append(argv)
        return subprocess.Popen(argv, env=mod._env(settings, tmp, **extra), stdout=open(tmp / f"{name}.log", "a"),
                                stderr=subprocess.STDOUT)

    monkeypatch.setattr(mod, "_spawn", spawn_default_owner)
    mod.test_sigkill_mid_generation_recovers_saved_candidates_once_without_resampling(client, api_settings, tmp_roots,
                                                                                       tmp_path)
    assert len(argvs) == 2 and all("--cpu" not in a for a in argvs)
