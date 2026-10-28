"""T2 core equivalence (owner only; docs/internal/WORK_PACKAGES/T2_REFERENCE_PLAN.md).

Each case runs ``scripts/owner/t2_compare.py --scenario <id>`` into a temporary directory: the scenario is executed on
the ORIGINAL monolith (``build/source_snapshot``, subprocess) and on the split packages (subprocess, same venv), and
the case passes only when the comparator reports ``EXACT_MATCH`` or ``ALLOWED_DIFF`` (every difference covered by a
documented allowed-difference rule) and the snapshot tree is unchanged. A ``MISMATCH`` is a defect of the split code,
never a reason to change the comparator.

The torch-free scenarios (T2-5 fitness/gate, T2-6 admission/settle, T2-9 seed streams; about 1 s per side) run in the
default owner gate. The scenarios that build models on the CPU or drain an engine flow on both sides (T2-1..T2-4,
T2-7, T2-8, T2-10; 3-8 s per side each) run when ``MBNAS_TEST_T2=1`` and are skipped otherwise with that reason. The
evidence of record is written by running the script without ``--out-dir`` (``docs/internal/evidence/T2/``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "owner" / "t2_compare.py"
SNAPSHOT = REPO / "build" / "source_snapshot"
FAST = ("T2-5", "T2-6", "T2-9")
HEAVY = ("T2-1", "T2-2", "T2-3", "T2-4", "T2-7", "T2-8", "T2-10")
FULL = os.environ.get("MBNAS_TEST_T2") == "1"

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "application" / "MBNAS_server" / "src" / "mbnas_server").is_dir() or not SCRIPT.is_file(),
    reason="reference snapshot build/source_snapshot (or scripts/owner/t2_compare.py) not present: owner build host only")


def _run(scenario: str, tmp_path: Path) -> dict:
    out, work = tmp_path / "out", tmp_path / "work"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update(PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="", PIP_CONFIG_FILE=os.devnull)
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--scenario", scenario, "--out-dir", str(out),
                           "--work-root", str(work), "--no-report"], capture_output=True, text=True, timeout=1800,
                          cwd=str(REPO), env=env)
    summary_path = out / "T2_SUMMARY.json"
    assert summary_path.is_file(), f"no summary (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    summary = json.loads(summary_path.read_text())
    assert summary["snapshot_guard"]["unchanged"], summary["snapshot_guard"]["diff"]
    assert proc.returncode in (0, 1), proc.stderr[-4000:]
    return summary["scenarios"][scenario]


def _check(entry: dict) -> None:
    detail = {k: entry.get(k) for k in ("verdict", "runner_error", "isolation")}
    detail["mismatches"] = (entry.get("mismatches") or [])[:20]
    assert entry["verdict"] in ("EXACT_MATCH", "ALLOWED_DIFF"), json.dumps(detail, indent=1, ensure_ascii=False)[:8000]
    assert not entry["isolation"]["ref"] and not entry["isolation"]["new"]


@pytest.mark.parametrize("scenario", FAST)
def test_t2_fast(scenario: str, tmp_path: Path) -> None:
    _check(_run(scenario, tmp_path))


@pytest.mark.slow
@pytest.mark.parametrize("scenario", HEAVY)
def test_t2_heavy(scenario: str, tmp_path: Path) -> None:
    if not FULL:
        pytest.skip("CPU model-building / engine-flow T2 scenario: set MBNAS_TEST_T2=1 (or run scripts/owner/t2_compare.py)")
    _check(_run(scenario, tmp_path))
