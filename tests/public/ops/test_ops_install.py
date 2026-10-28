"""install.sh on a throw-away ROOT (WP-7 contract section 1/1b, DD-A31): usage errors, the Python 3.10 check, the
refusal to replace editable development installs, and a real offline install (fresh venv, pip --no-index from a local
wheelhouse) of hand-built stub wheels, followed by the configuration step: env files copied from config/examples,
secrets generated (never printed), placeholders and empty values handled, files 0600 / directory 0700, idempotent
re-run. Owner principals need mbnas-core and are exercised by the installed-tree evidence, not here."""
from __future__ import annotations

import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from opstest_support import REPO, make_root, make_wheel, run, script_env, write

PUBLIC_DISTS = ("mbnas-contracts", "mbnas-model-runtime", "mbnas-runner", "mbnas-server")
GATEWAY_VARIANT = """# test example with active placeholders
MBNAS_GW_API_TOKEN=CHANGE_ME
MBNAS_GW_CORE_TOKEN=<ISSUED-BY-CORE-OWNER>
MBNAS_GW_CORS_ORIGINS=
MBNAS_GW_HOST=127.0.0.1
# MBNAS_GW_ARTIFACT_SIGNING_KEY=<GENERATED>
"""


def _bare_root(tmp_path: Path) -> Path:
    return make_root(tmp_path, venv=False, env_files=False)


def _active(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_usage_errors(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    assert run(root, "install", cwd=tmp_path, env=env).returncode == 2
    assert run(root, "install", "--profile", "public", "--wheelhouse", str(tmp_path), cwd=tmp_path, env=env).returncode == 2
    assert run(root, "install", "--profile", "public", "--python", cwd=tmp_path, env=env).returncode == 2
    assert not (root / "runtime" / "venv").exists()


def test_offline_needs_a_wheelhouse_and_a_requirements_file(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    proc = run(root, "install", "--profile", "public", "--offline", cwd=tmp_path, env=env)
    assert proc.returncode == 1 and "requirements file not found" in proc.stderr
    write(root / "requirements-public.txt", "# empty\n")
    proc = run(root, "install", "--profile", "public", "--offline", cwd=tmp_path, env=env)
    assert proc.returncode == 1 and "needs a wheelhouse" in proc.stderr


def test_python_other_than_3_10_is_refused(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    write(root / "requirements-public.txt", "# empty\n")
    fake = write(tmp_path / "bin" / "python3.12", "#!/bin/sh\necho 3.12.1\n", 0o755)
    proc = run(root, "install", "--profile", "public", "--python", str(fake), cwd=tmp_path, env=env)
    assert proc.returncode == 1 and "Python 3.10 is required" in proc.stderr
    assert not (root / "runtime" / "venv").exists()


def test_owner_profile_refused_for_a_public_package(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    write(root / "requirements-owner.txt", "# empty\n")
    write(root / "PACKAGE_PROFILE.json", '{"profile": "public"}\n')
    proc = run(root, "install", "--profile", "owner", "--python", sys.executable, cwd=tmp_path, env=env)
    assert proc.returncode == 1 and "owner-private package" in proc.stdout + proc.stderr
    assert not (root / "runtime" / "venv").exists()


@pytest.mark.slow
def test_editable_development_venv_is_never_replaced(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    write(root / "requirements-public.txt", "# empty\n")
    venv = root / "runtime" / "venv"
    subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(venv)], check=True)
    site = next((venv / "lib").glob("python3*/site-packages"))
    info = site / "mbnas_contracts-0.1.0.dist-info"
    write(info / "METADATA", "Metadata-Version: 2.1\nName: mbnas-contracts\nVersion: 0.1.0\n")
    write(info / "direct_url.json", '{"url": "file:///src/mbnas_contracts", "dir_info": {"editable": true}}\n')
    proc = run(root, "install", "--profile", "public", "--python", sys.executable, cwd=tmp_path, env=env)
    assert proc.returncode == 1 and "editable development installs" in proc.stderr
    assert (info / "direct_url.json").is_file()


@pytest.mark.slow
def test_offline_install_and_configuration(tmp_path: Path) -> None:
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    wheelhouse = tmp_path / "wheelhouse"
    wheels = [make_wheel(root / "wheels", d) for d in PUBLIC_DISTS]
    for w in wheels:
        (wheelhouse / w.name).parent.mkdir(parents=True, exist_ok=True)
        (wheelhouse / w.name).write_bytes(w.read_bytes())
    write(root / "requirements-public.txt", "# stub requirements\n" + "".join(f"./wheels/{w.name}\n" for w in wheels))
    examples = root / "config" / "examples"
    write(examples / "gateway.env.example", GATEWAY_VARIANT)
    write(examples / "runner.env.example", (REPO / "config" / "examples" / "runner.env.example").read_text())
    args = ("--profile", "public", "--offline", "--wheelhouse", str(wheelhouse), "--python", sys.executable)
    # as in a distribution (DD-A76): no tests/ folder and no root pyproject.toml - install.sh needs neither
    assert not (root / "tests").exists() and not (root / "pyproject.toml").exists()
    proc = run(root, "install", *args, cwd=tmp_path, env=env, timeout=600)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    venv_cfg = (root / "runtime" / "venv" / "pyvenv.cfg").read_text()
    assert re.search(r"^include-system-site-packages\s*=\s*false", venv_cfg, re.M)
    for d in ("pids", "logs", "data", "weights", "uploads", "locks"):
        assert (root / "runtime" / d).is_dir()
    cfg = root / "config" / "public"
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o700
    for name in ("gateway.env", "runner.env"):
        assert stat.S_IMODE((cfg / name).stat().st_mode) == 0o600
    gw = _active(cfg / "gateway.env")
    assert len(gw["MBNAS_GW_API_TOKEN"]) >= 40 and gw["MBNAS_GW_API_TOKEN"] != "CHANGE_ME"
    assert len(gw["MBNAS_GW_ARTIFACT_SIGNING_KEY"]) >= 40
    assert "MBNAS_GW_CORE_TOKEN" not in gw and "MBNAS_GW_CORS_ORIGINS" not in gw  # placeholder / empty: unset
    assert gw["MBNAS_GW_HOST"] == "127.0.0.1"
    assert "MBNAS_RUNNER_TOKEN" not in _active(cfg / "runner.env")
    printed = proc.stdout + proc.stderr + "".join(p.read_text() for p in (root / "runtime" / "logs").glob("install-*.log"))
    for key in ("MBNAS_GW_API_TOKEN", "MBNAS_GW_ARTIFACT_SIGNING_KEY"):
        assert gw[key] not in printed, f"{key} value was printed"
    assert "MBNAS_GW_CORE_TOKEN: NOT SET" in proc.stdout
    before = {n: (cfg / n).read_bytes() for n in ("gateway.env", "runner.env")}
    again = run(root, "install", *args, cwd=tmp_path, env=env, timeout=600)
    assert again.returncode == 0, again.stdout[-3000:] + again.stderr[-3000:]
    assert "reusing venv" in again.stdout
    assert {n: (cfg / n).read_bytes() for n in before} == before  # secrets kept, nothing rewritten


@pytest.mark.slow
def test_examples_with_commented_keys_are_filled(tmp_path: Path) -> None:
    """The shipped examples keep every key commented (`# KEY=<GENERATED>`): install uncomments and fills them."""
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    wheelhouse = tmp_path / "wheelhouse"
    wheels = [make_wheel(wheelhouse, d) for d in PUBLIC_DISTS]
    write(root / "requirements-public.txt", "".join(f"{w}\n" for w in wheels))  # absolute wheel paths
    for name in ("gateway", "runner"):
        src = REPO / "config" / "examples" / f"{name}.env.example"
        write(root / "config" / "examples" / f"{name}.env.example", src.read_text())
    proc = run(root, "install", "--profile", "public", "--offline", "--wheelhouse", str(wheelhouse), "--python",
               sys.executable, cwd=tmp_path, env=env, timeout=600)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    gw = _active(root / "config" / "public" / "gateway.env")
    assert set(gw) >= {"MBNAS_GW_API_TOKEN", "MBNAS_GW_ARTIFACT_SIGNING_KEY"}
    assert all(v and "<" not in v for v in gw.values())


@pytest.mark.slow
def test_owner_requirements_override_adds_the_core_wheel(tmp_path: Path) -> None:
    """--profile owner with a requirements file listing only the public wheels (like requirements-cpu.txt) also
    installs wheels/mbnas_core-<version>; with stub packages the principal step then fails loudly (exit 1)."""
    root, env = _bare_root(tmp_path), script_env(tmp_path / "t")
    wheelhouse = tmp_path / "wheelhouse"
    wheels = [make_wheel(root / "wheels", d) for d in PUBLIC_DISTS]
    core = make_wheel(root / "wheels", "mbnas-" + "core")
    for w in wheels + [core]:
        write(wheelhouse / w.name, "")
        (wheelhouse / w.name).write_bytes(w.read_bytes())
    write(root / "requirements-cpu.txt", "".join(f"./wheels/{w.name}\n" for w in wheels))
    for name in ("gateway", "runner"):  # shipped in every package
        src = REPO / "config" / "examples" / f"{name}.env.example"
        write(root / "config" / "examples" / f"{name}.env.example", src.read_text())
    # core.env.example is owner-only (not in the public ZIP that also runs these tests): an inline stand-in
    write(root / "config" / "examples" / "core.env.example", "# MBNAS_CORE_API_TOKEN=<GENERATED>\n"
          "# MBNAS_CORE_ARTIFACT_SIGNING_KEY=<GENERATED>\n# MBNAS_CORE_TICKET_KEY=<GENERATED>\n")
    proc = run(root, "install", "--profile", "owner", "--offline", "--wheelhouse", str(wheelhouse), "--requirements",
               "requirements-cpu.txt", "--python", sys.executable, cwd=tmp_path, env=env, timeout=600)
    out = proc.stdout + proc.stderr
    assert f"adding ./wheels/{core.name}" in out
    assert "mbnas-core 0.1.0: inside ROOT" in out
    assert proc.returncode == 1 and "writing config/owner failed" in proc.stderr  # stub packages: no principal CLI
    core_env = _active(root / "config" / "owner" / "core.env")
    assert {"MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY", "MBNAS_CORE_TICKET_KEY"} <= set(core_env)
    assert all(v not in out for v in core_env.values())
