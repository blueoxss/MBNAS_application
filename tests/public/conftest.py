"""Root fixtures of the public suite (contracts, model_runtime, runner, server; no ``mbnas_core`` import anywhere here).

WP-1 contract §9 / DD-A22. Shared plumbing (``clean_env``, ``tmp_roots``, ``make_dataset``, ``pilot_required``, the
host_data rule and the GPU guard) comes from ``tests/_shared/conftest_common.py``.

* ``settings``: ``mbnas_runner.config.RunnerSettings`` rooted in a tmp dir (work root = output root, manifest cache,
  logs); the allow roots also hold ``$MBNAS_TEST_RUNS_ROOT`` / ``$MBNAS_TEST_DATA_ROOT``. ``tests/public/server``
  overrides it with ``GatewaySettings``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SHARED = str(Path(__file__).resolve().parents[1] / "_shared")
if _SHARED not in sys.path:  # appended, never inserted at 0 (tests/_shared/datasets.py must not shadow site-packages)
    sys.path.append(_SHARED)

from conftest_common import *  # noqa: E402,F401,F403  (fixtures + hooks, see conftest_common.__all__)
from conftest_common import TEST_DATA_ROOT, TEST_RUNS_ROOT, TEST_WEIGHTS_DIR  # noqa: E402


@pytest.fixture
def settings(tmp_roots: dict[str, Path]):
    from mbnas_runner.config import RunnerSettings

    return RunnerSettings(work_root=str(tmp_roots["runs"]), output_roots=[str(tmp_roots["runs"]), TEST_RUNS_ROOT],
                          dataset_roots=[str(tmp_roots["data"]), TEST_DATA_ROOT], weights_dir=TEST_WEIGHTS_DIR,
                          log_dir=str(tmp_roots["runs"] / "logs"))
