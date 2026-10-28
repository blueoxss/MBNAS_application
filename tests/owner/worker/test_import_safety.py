"""S2-R1: importing a module of ``mbnas_core`` must never run a process loop (owner half of the split test).

Split from the snapshot ``tests/worker/test_import_safety.py`` (WP-1 contract §9, adaptation A10: the walk over the one
``mbnas_server`` package becomes a walk per package; the public packages are in
``tests/public/infra/test_import_safety.py``). The set-2 reviewer's ``pkgutil.walk_packages`` import walk once started a
real worker through ``worker/__main__``; ``python -m mbnas_core.owner_worker`` (the old ``-m mbnas_server.worker``,
DD-A14) must keep working, a plain import must not.

Evidence type: unit. The dynamic test runs the walk in a subprocess whose ``MBNAS_*`` settings point at a tmp dir and
whose GPU is hidden.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ('mbnas_core',)
# 2026-10-15: a module that needs an optional ml dependency (torch, ...) that is not installed is counted as
# "skipped_ml", not as a failure; every other import error (including a missing internal module) still fails.
WALK = ("import importlib, pkgutil, sys\n"
        "ML = {'torch', 'torchvision', 'ultralytics', 'rfdetr', 'onnx', 'onnxruntime', 'tensorrt', 'cv2', 'transformers'}\n"
        "pkg = importlib.import_module(sys.argv[1])\n"
        "names = [m.name for m in pkgutil.walk_packages(pkg.__path__, sys.argv[1] + '.')]\n"
        "skipped = []\n"
        "for n in names:\n"
        "    try:\n"
        "        importlib.import_module(n)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        if (exc.name or '').split('.')[0] not in ML:\n"
        "            raise\n"
        "        skipped.append(n)\n"
        "print('SKIPPED_ML', len(skipped), ','.join(skipped), file=sys.stderr)\n"
        "print('IMPORTED', len(names), int(any(n.endswith('.__main__') for n in names)))\n")


def _src(package: str) -> Path:
    return ROOT / package / "src" / package


def _import_time_statements(path: Path) -> list[str]:
    """Top-level statements that execute work on import: a bare call (``main()``) or a ``raise``."""
    out = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Raise) or (isinstance(node, ast.Expr) and isinstance(node.value, (ast.Call, ast.Await))):
            out.append(f"{node.lineno}: {ast.unparse(node)}")
    return out


def test_no_module_runs_code_at_import_time() -> None:
    offenders = {str(p.relative_to(SRC)): s for p in sorted(SRC.rglob("*.py")) if (s := _import_time_statements(p))}
    assert not offenders, f"guard these with `if __name__ == \"__main__\":` -> {offenders}"


@pytest.mark.parametrize("package", PACKAGES)
def test_no_module_runs_code_at_import_time(package: str) -> None:
    src = _src(package)
    offenders = {str(p.relative_to(src)): s for p in sorted(src.rglob("*.py")) if (s := _import_time_statements(p))}
    assert not offenders, f"guard these with `if __name__ == \"__main__\":` -> {offenders}"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MBNAS_")}
    runs = tmp_path / "runs"
    env.update({"MBNAS_HOME": str(tmp_path / "home"), "MBNAS_CORE_DB_PATH": str(runs / "db" / "import_walk.sqlite3"),
                "MBNAS_CORE_OUTPUT_ROOT": str(runs), "MBNAS_CORE_OUTPUT_ROOTS": json.dumps([str(runs)]),
                "MBNAS_CORE_LOG_DIR": str(runs / "logs"), "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


@pytest.mark.parametrize("package", PACKAGES)
def test_importing_every_module_returns_and_starts_no_worker(tmp_path: Path, package: str) -> None:
    env = _isolated_env(tmp_path)
    try:
        proc = subprocess.run([sys.executable, "-B", "-c", WALK, package], env=env, cwd=tmp_path, capture_output=True,
                              text=True, timeout=240)
    except subprocess.TimeoutExpired as exc:  # an unguarded __main__ polls forever
        raise AssertionError(f"the import walk did not return within {exc.timeout} s: a module runs a loop on import; "
                             f"stderr tail: {(exc.stderr or b'')[-800:]!r}") from None
    tail = proc.stderr[-1500:]
    assert proc.returncode == 0, f"exit {proc.returncode}: {tail}"
    assert "IMPORTED" in proc.stdout, proc.stdout
    assert proc.stdout.strip().endswith(" 1"), proc.stdout  # owner_worker.__main__ was imported too
    assert not list(tmp_path.rglob("*.sqlite3")), "an import opened (and created) a DB"

def test_python_dash_m_still_starts_the_worker_entry_point(tmp_path: Path) -> None:
    """The guard must not break ``python -m mbnas_core.owner_worker``: ``--help`` reaches the worker's argparse."""
    proc = subprocess.run([sys.executable, "-B", "-m", "mbnas_core.owner_worker", "--help"], env=_isolated_env(tmp_path),
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "MBNAS owner worker" in proc.stdout and "--gpu-index" in proc.stdout, proc.stdout
