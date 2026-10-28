"""Constants for the real ETRI_NAS run artifacts (opened read-only; tests skip when a run is absent on the host)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# A8: host data root of the real archive runs (read-only), named by the operator; default under the test root
RUNS_ROOT = Path(os.environ.get("MBNAS_TEST_ARCHIVE_RUNS_ROOT")
                 or f"{os.environ.get('MBNAS_TEST_ROOT', 'runtime/test_root')}/archive_runs")
REAL_ROOTS = [str(RUNS_ROOT / "yolo_ga_full_drone"), str(RUNS_ROOT / "yolo_ga_full_visdrone")]
REAL_DRIVERS = [str(RUNS_ROOT / "driver"), str(RUNS_ROOT / "driver_visdrone")]
NPS_DAY = "drone26_day_20261001_010516"
VISDRONE_SMOKE = "visdrone_person_smoke_20261012_224829"
VISDRONE_MINI = "visdrone_person_mini_20261012_224829"


def has_run(run_id: str) -> bool:
    return any((Path(r) / run_id / "search").is_dir() for r in REAL_ROOTS)


def real_dir(run_id: str) -> Path:
    for r in REAL_ROOTS:
        if (Path(r) / run_id).is_dir():
            return Path(r) / run_id
    raise FileNotFoundError(run_id)


requires_nps = pytest.mark.skipif(not has_run(NPS_DAY), reason="real NPS day run not present on this host", host_data=True)
requires_visdrone = pytest.mark.skipif(not has_run(VISDRONE_SMOKE), reason="real VisDrone smoke run not present on this host",
                                       host_data=True)
