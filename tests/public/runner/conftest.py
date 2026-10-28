"""Runner-test setup (moved from tests/benchmark/conftest.py): isolated ultralytics settings dir (never the user's
global one) for the benchmark and training tests of this directory."""
from __future__ import annotations

import os
from pathlib import Path

# WP-1 contract §9: the runs root of this test run (conftest_common sets MBNAS_TEST_RUNS_ROOT; no temp-dir fallback)
ULTRALYTICS_CFG = Path(os.environ["MBNAS_TEST_RUNS_ROOT"]) / "ultralytics_cfg/tests"
ULTRALYTICS_CFG.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(ULTRALYTICS_CFG))
os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
