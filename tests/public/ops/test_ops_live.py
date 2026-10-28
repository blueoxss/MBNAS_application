"""Installed-tree checks (marker ``live``; skipped unless ``MBNAS_TEST_OPS_ROOT`` names a tree installed with
scripts/install.sh, e.g. an unpacked ZIP in a new path with spaces; ``MBNAS_TEST_OPS_PROFILE`` = public (default) or
owner). Read-only on that tree apart from its runtime/logs/verify evidence: doctor's origin checks and
verify --suite relocation, both called from a working directory outside the tree. No process is started."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opstest_support import fake_nvidia_smi, run, script_env

pytestmark = [pytest.mark.live, pytest.mark.slow]
LIVE_ROOT = os.environ.get("MBNAS_TEST_OPS_ROOT")
PROFILE = os.environ.get("MBNAS_TEST_OPS_PROFILE", "public")


@pytest.fixture
def live_root() -> Path:
    if not LIVE_ROOT:
        pytest.skip("MBNAS_TEST_OPS_ROOT is not set (path of an installed tree)")
    return Path(LIVE_ROOT)


def test_doctor_origin_checks(live_root: Path, tmp_path: Path) -> None:
    env = script_env(tmp_path / "t", fake_bin=fake_nvidia_smi(tmp_path / "fakebin"))
    proc = run(live_root, "doctor", "--profile", PROFILE, "--json", "-", cwd=tmp_path, env=env, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    doc = json.loads(proc.stdout)
    status = {c["name"]: c["status"] for c in doc["checks"]}
    for name in ("interpreter", "entry_points", "path_strings"):
        assert status[name] == "PASS", (name, doc["checks"])
    assert all(v == "PASS" for k, v in status.items() if k.startswith("package:") and k != "package:mbnas-core")


def test_verify_relocation(live_root: Path, tmp_path: Path) -> None:
    env = script_env(tmp_path / "t", fake_bin=fake_nvidia_smi(tmp_path / "fakebin"))
    out = tmp_path / "relocation.json"
    proc = run(live_root, "verify", "--profile", PROFILE, "--suite", "relocation", "--json", str(out), cwd=tmp_path,
               env=env, timeout=900)
    doc = json.loads(out.read_text())
    assert proc.returncode == 0 and doc["status"] == "PASS", (doc, proc.stdout[-3000:])
