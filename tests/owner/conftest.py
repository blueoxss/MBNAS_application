"""Root fixtures of the owner suite (``mbnas_core`` installed; WP-1 contract §9, DD-A22).

Shared plumbing (``clean_env``, ``tmp_roots``, ``make_dataset``, ``pilot_required``, the host_data rule and the GPU
guard) comes from ``tests/_shared/conftest_common.py``. Owner-only fixtures:

* ``settings``: ``mbnas_core.config.CoreSettings`` rooted in a tmp dir (DB, output root, manifest cache, logs); the allow
  roots also hold the run's host roots ``$MBNAS_TEST_RUNS_ROOT`` / ``$MBNAS_TEST_DATA_ROOT`` (read-only use).
* ``runner_settings``: the ``mbnas_runner.config.RunnerSettings`` the owner worker hands to a job child for these core
  settings (contract §5 ``child_settings_env``); for owner tests that call runner handlers directly.
* ``registry``: lookup over ``config/examples/model_registry.example.json`` with every status forced to AVAILABLE
  (synthetic).
* ``host_devices``: a static device list shaped like ``provenance.env_lock.validation_devices()``.
* ``ctx_factory``: builds a ValidationContext; ``db``: an initialised connection.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SHARED = str(Path(__file__).resolve().parents[1] / "_shared")
if _SHARED not in sys.path:  # appended, never inserted at 0 (tests/_shared/datasets.py must not shadow site-packages)
    sys.path.append(_SHARED)

from conftest_common import *  # noqa: E402,F401,F403  (fixtures + hooks, see conftest_common.__all__)
from conftest_common import A100, REPO_ROOT, TEST_DATA_ROOT, TEST_RUNS_ROOT, TEST_WEIGHTS_DIR  # noqa: E402

REGISTRY_EXAMPLE = REPO_ROOT / "config" / "examples" / "model_registry.example.json"


@pytest.fixture
def settings(tmp_roots: dict[str, Path]):
    from mbnas_core.config import CoreSettings

    return CoreSettings(db_path=str(tmp_roots["runs"] / "db" / "test.sqlite3"), output_root=str(tmp_roots["runs"]),
                        output_roots=[str(tmp_roots["runs"]), TEST_RUNS_ROOT],
                        dataset_roots=[str(tmp_roots["data"]), TEST_DATA_ROOT],
                        weights_dir=TEST_WEIGHTS_DIR, log_dir=str(tmp_roots["runs"] / "logs"))


@pytest.fixture
def runner_settings(settings):
    import harness

    return harness.runner_settings_for(settings)


def _registry_entries() -> dict[str, dict[str, Any]]:
    doc = json.loads(REGISTRY_EXAMPLE.read_text())
    out = {}
    for entry in doc["registry"]:
        e = copy.deepcopy(entry)
        e["status"], e["reason"] = "AVAILABLE", "synthetic test registry (example entries forced AVAILABLE)"
        out[e["id"]] = e
    return out


@pytest.fixture
def registry() -> dict[str, dict[str, Any]]:
    """Mutable id -> RegistryEntry dict (synthetic, all AVAILABLE)."""
    return _registry_entries()


@pytest.fixture
def host_devices() -> list[dict[str, Any]]:
    return [copy.deepcopy(A100)]


@pytest.fixture
def ctx_factory(settings, registry, host_devices):
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_core.protocol.validator import ValidationContext

    def build(**overrides: Any) -> ValidationContext:
        kw: dict[str, Any] = {"settings": settings, "registry_lookup": registry.get, "devices": host_devices,
                              "dataset_service": DatasetService.from_settings(settings),
                              "now": lambda: "2026-10-06T00:00:00.000000Z"}
        kw.update(overrides)
        return ValidationContext(**kw)

    return build


@pytest.fixture
def db(settings):
    from mbnas_core.db.core import connect

    conn = connect(settings)
    yield conn
    conn.close()
