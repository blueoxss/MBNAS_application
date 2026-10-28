"""verify.sh --suite relocation writes the per-module origin table (C09 EVAL_1 D2): every module of the installed mbnas
packages, submodules included and located without importing them, with its origin relative to ROOT (or flagged
OUTSIDE), sys.path, the venv's .pth files with their lines, and the names (never values) of Python search-path
variables in the caller's environment, into relocation.json (+ relocation_origins.tsv). The PASS rule is unchanged:
the module check passes only when every recorded origin lies under ROOT.

The throw-away ROOT gets a real venv (``python -m venv --without-pip``, so ``runtime/venv/bin/python -I`` resolves
packages inside ROOT) holding stub packages ``mbnas_contracts`` / ``mbnas_model_runtime`` / ``mbnas_runner`` /
``mbnas_server`` (public profile) and a .pth file with a comment, an import line, a search path inside ROOT and one
outside ROOT. The other relocation checks (console scripts, doctor) are not asserted here: the stub install has no
console scripts. No process of the tree is started; the GPU check of doctor.sh only sees the fake nvidia-smi."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from opstest_support import fake_nvidia_smi, make_root, run, script_env, write

pytestmark = pytest.mark.slow

MODS = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server")
MODULE_CHECK = "module __file__ under ROOT (cwd outside ROOT)"
SUBMODULES = ("", ".mod_a", ".inner", ".inner.mod_b")


def _stub_package(base: Path, name: str) -> None:
    write(base / name / "__init__.py", "")
    write(base / name / "mod_a.py", "")
    write(base / name / "inner" / "__init__.py", "")
    write(base / name / "inner" / "mod_b.py", "")
    write(base / name / "data" / "not_a_module.json", "{}\n")  # package data: not a module


def _make_venv(root: Path) -> Path:
    venv = root / "runtime" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True,
                   timeout=300)
    site = subprocess.run([str(venv / "bin" / "python"), "-I", "-c",
                           "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          check=True, capture_output=True, text=True, timeout=60).stdout.strip()
    return Path(site)


def _relocation(root: Path, cwd: Path, tmp: Path, extra: dict[str, str]) -> tuple[subprocess.CompletedProcess, dict]:
    env = script_env(tmp / "t", fake_bin=fake_nvidia_smi(tmp / "fakebin"))
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):  # only what the test sets
        env.pop(key, None)
    env.update(extra)
    out = tmp / "verify.json"
    proc = run(root, "verify", "--profile", "public", "--suite", "relocation", "--json", str(out), cwd=cwd, env=env,
               timeout=600)
    doc = json.loads(out.read_text())
    reloc = root / doc["evidence"][0]
    assert reloc.name == "relocation.json", doc["evidence"]
    return proc, {"verify": doc, "relocation": json.loads(reloc.read_text()), "text": reloc.read_text(),
                  "tsv": (reloc.parent / "relocation_origins.tsv").read_text()}


def test_relocation_json_records_every_module_origin_and_the_pth_files(tmp_path: Path) -> None:
    root = make_root(tmp_path, venv=False)
    site = _make_venv(root)
    for mod in MODS:
        _stub_package(site, mod)
    (site / "extra_dir").mkdir()
    outside = tmp_path / "outside lib"
    outside.mkdir()
    write(site / "zz_relocation_test.pth", f"# a comment\nimport os\nextra_dir\n{outside}\n")
    ext = tmp_path / "elsewhere"
    ext.mkdir()
    secret_like = str(tmp_path / "caller path value")

    proc, got = _relocation(root, ext, tmp_path, {"PYTHONPATH": secret_like})
    reloc = got["relocation"]
    checks = {c["check"]: c for c in reloc["checks"]}
    assert checks[MODULE_CHECK]["ok"] is True, checks[MODULE_CHECK]
    assert "16 modules" in checks[MODULE_CHECK]["detail"]
    assert reloc["module_counts"] == {m: len(SUBMODULES) for m in MODS}
    rows = {r["module"]: r for r in reloc["module_origins"]}
    assert set(rows) == {m + s for m in MODS for s in SUBMODULES}  # data/ (no __init__) is not a module
    site_rel = site.resolve().relative_to(root.resolve()).as_posix()
    for name, row in rows.items():
        assert row["location"] == "ROOT", row
        assert not row["origin"].startswith("/") and row["origin"].startswith(site_rel + "/"), row
    # stub module names are built from parts: scripts/dev/check_import_closure.py reads a dotted string literal that
    # starts with a package name as a module reference, and these stub submodules do not exist in the real packages
    stub = "mbnas_runner"
    assert rows[f"{stub}.inner.mod_b"]["origin"] == f"{site_rel}/mbnas_runner/inner/mod_b.py"
    assert rows[f"{stub}.inner"]["origin"] == f"{site_rel}/mbnas_runner/inner/__init__.py"
    pth = [p for p in reloc["pth_files"] if p["file"].endswith("zz_relocation_test.pth")]
    assert len(pth) == 1 and pth[0]["location"] == "ROOT", reloc["pth_files"]
    lines = pth[0]["lines"]
    assert [x["kind"] for x in lines] == ["import", "path", "path"], lines  # the comment is skipped
    assert lines[0]["text"] == "import os"
    assert lines[1] == {"kind": "path", "path": f"{site_rel}/extra_dir", "location": "ROOT", "exists": True}
    assert lines[2]["location"] == "OUTSIDE" and lines[2]["path"] == str(outside.resolve()) and lines[2]["exists"]
    locations = {e["location"] for e in reloc["sys_path"]}
    assert {"ROOT", "BASE"} <= locations  # venv site-packages, the base interpreter's stdlib
    assert {"path": str(outside.resolve()), "location": "OUTSIDE"} in reloc["sys_path"]  # added by the .pth line
    assert reloc["caller_env_python_vars"] == ["PYTHONPATH"]
    assert secret_like not in got["text"]  # key names only
    tsv = got["tsv"].splitlines()
    assert tsv[0] == "module\tlocation\torigin" and len(tsv) == 1 + 16
    assert f"mbnas_server.mod_a\tROOT\t{site_rel}/mbnas_server/mod_a.py" in tsv
    assert any(e.endswith("relocation_origins.tsv") for e in got["verify"]["evidence"])
    assert "per-module origins in" in proc.stdout

    # the same tree with mbnas_runner found outside ROOT (through the .pth line): the module check fails
    shutil.rmtree(site / "mbnas_runner")
    _stub_package(outside, "mbnas_runner")
    proc, got = _relocation(root, ext, tmp_path, {})
    reloc = got["relocation"]
    check = {c["check"]: c for c in reloc["checks"]}[MODULE_CHECK]
    assert check["ok"] is False and "mbnas_runner" in check["detail"], check
    rows = {r["module"]: r for r in reloc["module_origins"]}
    assert rows["mbnas_runner"]["location"] == "OUTSIDE"
    assert rows["mbnas_runner"]["origin"] == str((outside / "mbnas_runner" / "__init__.py").resolve())
    assert rows["mbnas_server"]["location"] == "ROOT"
    assert reloc["caller_env_python_vars"] == []
    assert got["verify"]["status"] == "FAIL" and proc.returncode == 1
