"""Static contract of the five operation scripts (WP-7 contract section 1): bash header, self-located ROOT, LF only,
syntax, --help, documented options == implemented options, no eval / pkill / killall, the shared process-identity
block of start.sh and stop.sh, embedded Python run isolated (-I), and no original-host string in the shipped text."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from opstest_support import REPO, SCRIPTS

ROOT_LINE = 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"'
IDENTITY_START = "# ---- process identity (same block in start.sh and stop.sh)"
IDENTITY_END = "# ---- end of the shared block"
# shipped in every bundle (clean distribution, DD-A76): the five scripts, deploy/** and .dockerignore carry no decision /
# work-package / criterion / test-run tag and name no internal record or unshipped test folder
SHIPPED_OPS = [f"scripts/{n}.sh" for n in SCRIPTS] + sorted(
    p.relative_to(REPO).as_posix() for p in (REPO / "deploy").rglob("*") if p.is_file()) + [".dockerignore"]
TRAIL = re.compile(r"DD-A\d|WP-\d|\bC(?:0\d|1\d)\b|\bT[2-8]\b|fix round|\bround \d|라운드|설계자|\bdesigner\b|"
                   r"snapshot HEAD|source snapshot|docs/internal|tests/(?:public|owner|_shared)|REVIEW_CHECKLIST|"
                   r"RIGHTS_REVIEW|LICENSE_STATUS")


def _text(name: str) -> str:
    return (REPO / "scripts" / f"{name}.sh").read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Bash code without heredoc bodies (help text, embedded Python) and without comment lines; backslash-continued
    lines are joined."""
    out, skip_until = [], None
    for line in re.sub(r"\\\n\s*", " ", text).splitlines():
        if skip_until is not None:
            if line.strip() == skip_until:
                skip_until = None
            continue
        m = re.search(r"<<'([A-Z]+)'", line)
        if m:
            skip_until = m.group(1)
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("name", SCRIPTS)
def test_header_and_root_detection(name: str) -> None:
    raw = (REPO / "scripts" / f"{name}.sh").read_bytes()
    assert b"\r" not in raw, "CRLF line ending"
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "set -u -o pipefail" in lines[:8]
    assert ROOT_LINE in lines[:10]


@pytest.mark.parametrize("name", SCRIPTS)
def test_bash_syntax_and_help(name: str) -> None:
    path = REPO / "scripts" / f"{name}.sh"
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    proc = subprocess.run(["bash", str(path), "--help"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith(f"Usage: bash scripts/{name}.sh")
    assert "Exit codes:" in proc.stdout
    bad = subprocess.run(["bash", str(path), "--no-such-option"], capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2 and "unknown option" in bad.stderr


def _parsed_options(text: str) -> set[str]:
    opts = set()
    for line in text.splitlines():
        m = re.match(r"^\s+((?:-h\|)?--[a-z][a-z-]*|--)(?:=\*)?\)", line)
        if m:
            opts.add(m.group(1).replace("-h|", ""))
    return opts


def _documented_options(help_text: str) -> set[str]:
    opts = set()
    for line in help_text.splitlines():
        m = re.match(r"^  (?:-h, )?(--[a-z][a-z-]*|--)(?=[\s=]|$)", line)
        if m:
            opts.add(m.group(1))
    return opts


@pytest.mark.parametrize("name", SCRIPTS)
def test_documented_options_equal_implemented_options(name: str) -> None:
    help_text = subprocess.run(["bash", str(REPO / "scripts" / f"{name}.sh"), "--help"], capture_output=True,
                               text=True, timeout=30).stdout
    parsed, documented = _parsed_options(_text(name)), _documented_options(help_text)
    assert "--profile" in parsed and "--help" in parsed
    assert parsed == documented, f"parsed only: {parsed - documented}; documented only: {documented - parsed}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_eval_no_pkill_no_killall(name: str) -> None:
    code = _code_only(_text(name))
    assert not re.search(r"\b(eval|pkill|killall)\b", code)


@pytest.mark.parametrize("name", SCRIPTS)
def test_embedded_python_runs_isolated(name: str) -> None:
    for line in _code_only(_text(name)).splitlines():
        if line.lstrip().startswith("read "):  # read -r -d '' PYHELPER <<'PY' stores the program, it runs nothing
            continue
        if "<<'PY'" in line or '-c "$PYHELPER"' in line:
            assert " -I " in line, line


def test_identity_block_is_shared_by_start_and_stop() -> None:
    def block(name: str) -> str:
        text = _text(name)
        return text[text.index(IDENTITY_START):text.index(IDENTITY_END)]

    assert block("start") == block("stop")


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_original_host_strings(name: str) -> None:
    text = _text(name)
    needles = ("/root" + "/detection", "/etri" + "_nas/", "/data/" + "mg" + "jeong", "mg" + "jeong", "pc" + "2300",
               "147.46" + ".114.253")
    assert [n for n in needles if n in text] == []


@pytest.mark.parametrize("rel", SHIPPED_OPS)
def test_no_process_trail_in_the_shipped_ops_files(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    hits = [f"{n}: {line.strip()[:120]}" for n, line in enumerate(text.splitlines(), 1) if TRAIL.search(line)]
    assert hits == []


def test_profiles_and_roles_are_validated(tmp_path: Path) -> None:
    for name in SCRIPTS:
        proc = subprocess.run(["bash", str(REPO / "scripts" / f"{name}.sh"), "--profile", "registration"],
                              capture_output=True, text=True, cwd=str(tmp_path), timeout=30)
        assert proc.returncode == 2, (name, proc.stderr)
    for name in ("start", "stop"):
        proc = subprocess.run(["bash", str(REPO / "scripts" / f"{name}.sh"), "--profile", "public", "--role", "db"],
                              capture_output=True, text=True, cwd=str(tmp_path), timeout=30)
        assert proc.returncode == 2 and "--role must be" in proc.stderr
    proc = subprocess.run(["bash", str(REPO / "scripts" / "verify.sh"), "--profile", "public", "--suite", "smoke"],
                          capture_output=True, text=True, cwd=str(tmp_path), timeout=30)
    assert proc.returncode == 2 and "unknown suite" in proc.stderr
