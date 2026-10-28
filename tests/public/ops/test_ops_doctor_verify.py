"""doctor.sh and verify.sh behaviour on a throw-away ROOT (WP-7 contract section 1): JSON report shapes, exit codes,
--strict, statuses PASS / FAIL / NOT_APPLICABLE / BLOCKED_BY_ENV of the suites that do not need an installed stack
(gpu through a fake nvidia-smi only, package manifest check, public integration without a core, unit via pytest on a
stub test file), and that --pytest-args is split, never evaluated by a shell."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from opstest_support import fake_nvidia_smi, run, script_env, write

pytestmark = pytest.mark.slow


def _verify(root: Path, cwd: Path, tmp: Path, *args: str, extra: dict[str, str] | None = None, gpu_mode: str = "busy"):
    fake = fake_nvidia_smi(tmp / "fakebin")
    env = script_env(tmp / "t", {"STUB_GPU_MODE": gpu_mode, **(extra or {})}, fake_bin=fake)
    out = tmp / "verify.json"
    proc = run(root, "verify", "--profile", "public", "--json", str(out), *args, cwd=cwd, env=env, timeout=600)
    return proc, (json.loads(out.read_text()) if out.is_file() else None)


def test_doctor_reports_root_and_strict_exit(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    pids = ops_root / "runtime" / "pids"
    pids.mkdir(parents=True)
    (pids / "runner.pid").write_text("999999999\n")
    env = script_env(tmp_path / "t", fake_bin=fake_nvidia_smi(tmp_path / "fakebin"))
    proc = run(ops_root, "doctor", "--profile", "public", "--json", "-", cwd=ext_cwd, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)  # --json - : JSON on stdout, the text report on stderr
    assert doc["schema"] == "mbnas.doctor/1" and doc["root"] == str(ops_root) and doc["profile"] == "public"
    by_name = {c["name"]: c for c in doc["checks"]}
    assert by_name["interpreter"]["status"] == "FAIL"  # the fake venv python execs another interpreter
    assert by_name["process:runner"]["status"] == "WARN" and "stale" in by_name["process:runner"]["detail"]
    assert by_name["gpu"]["status"] == "PASS" and "Stub GPU" in by_name["gpu"]["detail"]
    assert "[FAIL] interpreter" in proc.stderr and not doc["ok"]
    strict = run(ops_root, "doctor", "--profile", "public", "--strict", cwd=ext_cwd, env=env, timeout=300)
    assert strict.returncode == 1


def test_doctor_without_venv(tmp_path: Path, ext_cwd: Path) -> None:
    from opstest_support import make_root

    root = make_root(tmp_path / "bare", venv=False)
    proc = run(root, "doctor", "--profile", "public", "--strict", cwd=ext_cwd,
               env=script_env(tmp_path / "t", fake_bin=fake_nvidia_smi(tmp_path / "fakebin")), timeout=300)
    assert proc.returncode == 1
    assert "[FAIL] interpreter" in proc.stdout and "scripts/install.sh" in proc.stdout


def test_gpu_suite_never_runs_on_a_busy_or_unapproved_gpu(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "gpu", gpu_mode="busy")
    assert proc.returncode == 3 and doc["status"] == "BLOCKED_BY_ENV" and "no idle GPU" in doc["reason"]
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "gpu", gpu_mode="idle")
    assert proc.returncode == 3 and doc["status"] == "BLOCKED_BY_ENV" and "--allow-gpu" in doc["reason"]
    assert doc["counts"] == {"gpus": 1, "idle": 1}
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "gpu", gpu_mode="fail")
    assert proc.returncode == 3 and doc["status"] == "BLOCKED_BY_ENV"


def test_package_suite_manifest(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "package")
    assert proc.returncode == 0 and doc["status"] == "NOT_APPLICABLE" and "FILE_MANIFEST.jsonl" in doc["reason"]
    data = write(ops_root / "docs" / "a.txt", "hello\n")
    good = {"path": "docs/a.txt", "size": 6, "sha256": hashlib.sha256(b"hello\n").hexdigest()}
    manifest = ops_root / "FILE_MANIFEST.jsonl"
    manifest.write_text(json.dumps(good) + "\n")
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "package")
    assert proc.returncode == 0 and doc["status"] == "PASS", doc
    assert doc["counts"]["manifest"]["ok"] == 1 and "canary scan not run" in doc["reason"]
    data.write_text("tampered\n")
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "package")
    assert proc.returncode == 1 and doc["status"] == "FAIL" and doc["counts"]["manifest"]["mismatch"] == 1
    manifest.write_text(json.dumps({**good, "path": "../escape.txt"}) + "\n")
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "package")
    assert doc["status"] == "FAIL" and doc["counts"]["manifest"]["invalid"] == 1


def test_public_integration_needs_a_core_url(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "integration")
    assert proc.returncode == 3 and doc["status"] == "BLOCKED_BY_ENV" and "MBNAS_CORE_URL" in doc["reason"]


def test_multi_suite_report_shape(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "package,gpu")
    assert proc.returncode == 3
    assert doc["suite"] == "package,gpu" and doc["status"] == "BLOCKED_BY_ENV"
    assert doc["counts"] == {"PASS": 0, "FAIL": 0, "NOT_APPLICABLE": 1, "BLOCKED_BY_ENV": 1}
    assert [r["suite"] for r in doc["results"]] == ["package", "gpu"]
    for r in doc["results"]:
        assert {"suite", "status", "counts", "evidence", "reason"} <= set(r)
    assert all(e.startswith("runtime/logs/verify/") for e in doc["evidence"])


def test_unit_suite_runs_pytest_and_never_evaluates_arguments(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "unit")
    assert proc.returncode == 1 and doc["status"] == "FAIL" and "tests/public" in doc["reason"]  # no test directory
    write(ops_root / "tests" / "public" / "test_stub.py", "def test_ok():\n    assert True\n")
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "unit", "--pytest-args", "tests/public/test_stub.py")
    assert proc.returncode == 0 and doc["status"] == "PASS", doc
    assert doc["counts"]["tests"] == 1 and doc["counts"]["passed"] == 1
    marker = tmp_path / "PWNED"
    proc, doc = _verify(ops_root, ext_cwd, tmp_path, "--suite", "unit",
                        "--pytest-args", f"tests/public/test_stub.py $(touch {marker})", "--", f"`touch {marker}`")
    assert doc["status"] == "FAIL"  # pytest got the literal words as file names
    assert not marker.exists()
    assert f"$(touch" in " ".join(doc["command"]) and f"`touch {marker}`" in doc["command"]


def test_verify_never_stops_running_processes(tmp_path: Path, ext_cwd: Path) -> None:
    """Regression: the relocation suite once ran stop.sh --role web through its spaced path and stopped a live web
    preview. Every process running before verify (gateway, and web when node exists) is still running after it."""
    import shutil

    from opstest_support import alive, free_port, kill_own, make_root

    root = make_root(tmp_path, web=True)
    env = script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(free_port()), "MBNAS_WEB_PORT": str(free_port())})
    roles = ["gateway"] + (["web"] if shutil.which("node") else [])
    try:
        for role in roles:
            start = run(root, "start", "--profile", "public", "--role", role, "--wait-s", "20", cwd=ext_cwd, env=env)
            assert start.returncode == 0, start.stdout + start.stderr
        pids = {role: int((root / "runtime" / "pids" / f"{role}.pid").read_text().split()[0]) for role in roles}
        _verify(root, ext_cwd, tmp_path, "--suite", "relocation,package,gpu")
        for role, pid in pids.items():
            assert alive(pid) and (root / "runtime" / "pids" / f"{role}.pid").is_file(), role
        assert run(root, "stop", "--profile", "public", cwd=ext_cwd, env=env).returncode == 0
    finally:
        kill_own(root)
