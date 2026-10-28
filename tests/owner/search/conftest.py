"""Search-test fixtures: isolated ultralytics settings dir (never the user's global one) and one shared space."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# WP-1 contract §9: the runs root of this test run (conftest_common sets MBNAS_TEST_RUNS_ROOT; no temp-dir fallback)
ULTRALYTICS_CFG = Path(os.environ["MBNAS_TEST_RUNS_ROOT"]) / "ultralytics_cfg/tests"
ULTRALYTICS_CFG.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(ULTRALYTICS_CFG))
os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("YOLO_AUTOINSTALL", "false")


@pytest.fixture(scope="session")
def space():
    """Shared mixed space for nc=1 at 640 (ETRI pool registered on the installed ultralytics)."""
    # 2026-10-21 (D4): the shared space builds real ultralytics models + registers the ETRI pool -> skip (not error)
    # for every test using this fixture where the ml extras are absent.
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_core.search.space import SearchSpace

    # WP-1 (DD-A12): the mixed pool is vendored; there is no ETRI root to pass any more
    return SearchSpace.create(nc=1, imgsz=640)
