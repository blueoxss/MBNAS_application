"""Import closure and trust boundaries of the five packages (WP-1 contract §13.2, DD-A25/A26/A27).

Static: ``scripts/dev/check_import_closure.py`` (``run(root)``; AST only, nothing imported) must report no violation, no
unresolved reference and no forbidden string, apart from the documented exceptions below; every transitional allowance
names its expiry. Dynamic: importing each public package (and its main entry modules) in a fresh ``-I -B`` interpreter
must not load ``mbnas_core``; ``mbnas_contracts`` must load no other internal package and no ML stack.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest_common import owner_tool  # DD-A43(b): skip when the owner-only checker is not shipped

ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / "scripts" / "dev" / "check_import_closure.py"
# module names are composed (not literal module references) so the checker does not read this table as imports of a
# public test file
CORE, RUNNER, OLD = "mbnas_" + "core", "mbnas_runner", "mbnas_server"
# The WP-1 transitional core -> runner edges (contract §7, DD-A14/A25/A27) expired with WP-2: no allowance remains.
# DD-A26 documented data literal (file, literal) -> expiry: data, not a reference. Same table as DATA_EXCEPTIONS of
# scripts/dev/check_import_closure.py: the base.py legacy hook string, extended to contract 1.6 by DD-A58 (public ZIP
# compatibility shim); the openapi.json literal expired with WP-4 (contract 1.5.0) and is gone from the tree
DATA_EXCEPTIONS = {("mbnas_model_runtime/src/mbnas_model_runtime/adapters/base.py",
                    f"{OLD}.search.etri_bridge:ensure_mixed_registered"): "1.6"}
LEGACY_HOOK_MODULE = f"{OLD}.search.etri_bridge"


def _checker():
    spec = importlib.util.spec_from_file_location("mbnas_check_import_closure",
                                                  owner_tool("scripts/dev/check_import_closure.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def report() -> dict:
    return _checker().run(ROOT)


def _is_documented(finding: dict) -> bool:
    """DD-A26 data literal of base.py (reported by rule iv/resolve as a string reference)."""
    return (finding.get("file") == "mbnas_model_runtime/src/mbnas_model_runtime/adapters/base.py"
            and finding.get("kind") == "string"
            and (finding.get("target") == LEGACY_HOOK_MODULE or finding.get("missing") == LEGACY_HOOK_MODULE))


def test_no_boundary_violation(report: dict) -> None:
    bad = [v for v in report["violations"] if not _is_documented(v)]
    assert bad == [], json.dumps(bad, indent=1)[:4000]


def test_no_unresolved_reference(report: dict) -> None:
    bad = [u for u in report["unresolved"] if not _is_documented(u)]
    assert bad == [], json.dumps(bad, indent=1)[:4000]


def test_no_forbidden_string_outside_the_documented_data_exceptions(report: dict) -> None:
    bad = [f for f in report["forbidden_strings"]
           if not any(f["file"] == file and literal in f.get("text", "") for file, literal in DATA_EXCEPTIONS)]
    assert bad == [], json.dumps(bad, indent=1)[:4000]


def test_checker_errors_are_empty(report: dict) -> None:
    assert report["errors"] == []


def test_no_transitional_allowance_remains_and_core_never_imports_runner(report: dict) -> None:
    """WP-2 §4: the WP-1 transitional allowlist expired; any mbnas_core -> mbnas_runner edge is a violation."""
    assert report["allowlist"] == []
    assert report["allowlisted"] == []
    assert [v for v in report["violations"] if v.get("rule") == "iii"] == []


PUBLIC_IMPORTS = {
    "mbnas_contracts": ["mbnas_contracts", "mbnas_contracts.openapi", "mbnas_contracts.jobs_protocol",
                        "mbnas_contracts.protocol_hash", "mbnas_contracts.constants", "mbnas_contracts.structure_doc",
                        "mbnas_contracts.errors", "mbnas_contracts.util.source_hash"],
    "mbnas_model_runtime": ["mbnas_model_runtime", "mbnas_model_runtime.registry", "mbnas_model_runtime.operators",
                            "mbnas_model_runtime.adapters.base"],
    "mbnas_runner": ["mbnas_runner", "mbnas_runner.child", "mbnas_runner.contract", "mbnas_runner.config",
                     "mbnas_runner.__main__"],
    "mbnas_server": ["mbnas_server", "mbnas_server.api.app", "mbnas_server.config", "mbnas_server.archive"],
}


def _loaded_after(modules: list[str]) -> list[str]:
    code = (f"import importlib, json, sys\nfor m in {modules!r}: importlib.import_module(m)\n"
            "print(json.dumps(sorted(sys.modules)))")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=300,
                          env={"CUDA_VISIBLE_DEVICES": "", "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("package", sorted(PUBLIC_IMPORTS))
def test_public_packages_do_not_load_mbnas_core(package: str) -> None:
    loaded = _loaded_after(PUBLIC_IMPORTS[package])
    assert [m for m in loaded if m.split(".")[0] == CORE] == []
    if package == "mbnas_server":
        assert [m for m in loaded if m.split(".")[0] in ("mbnas_runner", "mbnas_model_runtime")] == []


def test_contracts_load_no_other_internal_package_and_no_ml_stack() -> None:
    loaded = _loaded_after(PUBLIC_IMPORTS["mbnas_contracts"])
    assert sorted({m.split(".")[0] for m in loaded if m.startswith("mbnas_")}) == ["mbnas_contracts"]
    assert [m for m in loaded if m.split(".")[0] in ("torch", "ultralytics", "fastapi", "starlette")] == []
