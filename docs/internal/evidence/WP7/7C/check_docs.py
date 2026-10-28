#!/usr/bin/env python3
"""WP-7C self-checks of README.md, docs/public/*.md and docs/registration/** (evidence helper, owner-only).

1. options: every `--option` written next to `scripts/<x>.sh` / an mbnas-* console script in the docs exists in that
   program's real `--help`, and every option of a `--help` is mentioned somewhere in the docs.
2. links: relative markdown links resolve to files; public docs (README, docs/public) never link into
   docs/registration, docs/internal or other paths that the public ZIP does not ship.
3. svg: the structure drawing parses as XML and has the expected subsystem/block ids.
4. needles: no original-host facts, leak-test marker prefix or absolute home paths in the documents.

Usage: runtime/venv/bin/python -I -B docs/internal/evidence/WP7/7C/check_docs.py [--root <ROOT>]   (exit 1 on a failure)
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = ("install", "doctor", "start", "stop", "verify", "package")
# console programs: base name -> subcommands whose --help also defines options of this program
CONSOLE = {"mbnas-gateway": [], "mbnas-gateway-doctor": [], "mbnas-runner": ["run", "register"],
           "mbnas-core-api": [], "mbnas-core-doctor": [], "mbnas-core-owner-worker": [],
           "mbnas-core-principal": ["create", "rotate", "revoke", "list"]}
OPT = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
DEF_LINE = re.compile(r"^\s{0,8}(?:-[A-Za-z], )?(--[a-z][a-z0-9-]*)")
HOST_FACTS_B64 = ("L2RhdGEvbWdqZW9uZw==", "L3Jvb3QvZGV0ZWN0aW9u", "MTQ3LjQ2LjExNC4yNTM=", "cGMyMzAw", "bWdqZW9uZw==")
PUBLIC_SHIPPED_PREFIXES = ("docs/public/", "README.md", "THIRD_PARTY_NOTICES.md", "config/examples/", "deploy/public/",
                           "examples/", "scripts/install.sh", "scripts/doctor.sh", "scripts/start.sh", "scripts/stop.sh",
                           "scripts/verify.sh", "requirements-public.txt", "requirements-cpu.txt", "constraints.txt",
                           "mbnas_web/dist", "VERSION")


def help_text(root: Path, prog: str, sub: str | None = None) -> str:
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1", MBNAS_HOME=str(root / "runtime"))
    if prog in SCRIPTS:
        cmd = ["bash", str(root / "scripts" / f"{prog}.sh"), "--help"]
    else:
        cmd = [str(root / "runtime/venv/bin" / prog), *([sub] if sub else []), "--help"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd="/")
    if out.returncode != 0:
        raise RuntimeError(f"{prog} {sub or ''} --help exit {out.returncode}: {out.stderr[-300:]}")
    return out.stdout


def defined_options(text: str) -> set[str]:
    """Options the help DEFINES: those in the usage line(s) and at the start of option-list lines (not options of other
    tools quoted inside descriptions)."""
    found: set[str] = set()
    in_usage = in_options = False
    for line in text.splitlines():
        if line.lower().startswith("usage:"):
            in_usage = True
        elif in_usage and not line.strip():
            in_usage = False
        if in_usage:
            found.update(OPT.findall(line))
        if re.match(r"^(?:options|optional arguments):\s*$", line, re.I):
            in_options = True
            continue
        if in_options and line.strip() and not line[0].isspace():
            in_options = False  # next section header (Steps:, Checks:, Environment:, ...)
        m = DEF_LINE.match(line) if in_options else None
        if m:
            found.add(m.group(1))
    return found - {"--help"}


def doc_files(root: Path) -> list[Path]:
    files = [root / "README.md"] + sorted((root / "docs/public").glob("*.md"))
    files += sorted(p for p in (root / "docs/registration").rglob("*") if p.is_file())
    return files


def documented_options(root: Path) -> dict[str, set[str]]:
    """Options written on the same line as, and after, the program name (code lines and table rows)."""
    found: dict[str, set[str]] = {}
    for f in doc_files(root):
        if f.suffix != ".md":
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            for prog in SCRIPTS:
                if f"scripts/{prog}.sh" in line:
                    found.setdefault(prog, set()).update(OPT.findall(line.split(f"scripts/{prog}.sh", 1)[1]))
            for prog in sorted(CONSOLE, key=len, reverse=True):
                m = re.search(rf"(?<![\w-]){re.escape(prog)}(?![\w-])", line)
                if m:
                    found.setdefault(prog, set()).update(OPT.findall(line[m.end():]))
                    break
    return {k: v - {"--help"} for k, v in found.items()}


def check_options(root: Path) -> list[str]:
    errors: list[str] = []
    documented = documented_options(root)
    all_docs = "\n".join(f.read_text(encoding="utf-8") for f in doc_files(root) if f.suffix == ".md")
    install_md = (root / "docs/public/INSTALL.md").read_text(encoding="utf-8")
    sec3 = install_md.split("## 3.", 1)[1].split("\n## 4.", 1)[0] if "## 3." in install_md else ""
    install_table_opts = set(re.findall(r"^\| `(--[a-z-]+)", sec3, re.M))  # the install.sh option table (INSTALL §3)
    for prog in list(SCRIPTS) + list(CONSOLE):
        try:
            real = defined_options(help_text(root, prog))
            for sub in CONSOLE.get(prog, []):
                real |= defined_options(help_text(root, prog, sub))
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"[options] {prog}: cannot read --help ({exc})")
            continue
        doc = documented.get(prog, set()) | (install_table_opts if prog == "install" else set())
        foreign = {"--no-index", "--find-links", "--rm", "--build", "--init"} - real  # pip/docker words on the same line
        invented = sorted(doc - real - foreign)
        missing = sorted(o for o in real if o not in all_docs)
        print(f"[options] {prog:24s} defined={sorted(real)} documented={sorted(doc & real)} not_in_docs={missing}")
        if invented:
            errors.append(f"[options] {prog}: documented but not defined by its --help: {invented}")
        if missing and prog in SCRIPTS:
            errors.append(f"[options] {prog}: defined by --help but not mentioned in the docs: {missing}")
    return errors


LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")


def check_links(root: Path) -> list[str]:
    errors = []
    for f in doc_files(root):
        if f.suffix != ".md":
            continue
        rel_doc = f.relative_to(root).as_posix()
        public = rel_doc == "README.md" or rel_doc.startswith("docs/public/")
        for target in LINK.findall(f.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+://", target):
                continue
            path = (f.parent / target).resolve()
            try:
                rel = path.relative_to(root.resolve()).as_posix()
            except ValueError:
                errors.append(f"[links] {rel_doc}: link leaves the tree: {target}")
                continue
            if not path.exists():
                errors.append(f"[links] {rel_doc}: missing target {target}")
            if public and not (rel + ("/" if path.is_dir() else "")).startswith(PUBLIC_SHIPPED_PREFIXES):
                errors.append(f"[links] {rel_doc}: public doc links to a path the public ZIP does not ship: {target}")
    return errors


def check_svg(root: Path) -> list[str]:
    path = root / "docs/registration/diagrams/system_structure.svg"
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return [f"[svg] not well-formed: {exc}"]
    ids = {el.get("id") for el in tree.iter() if el.get("id")}
    want = {f"SS{i}" for i in range(1, 8)} | {"B1_1", "B2_7", "B6_8", "B7_4", "ADMIN", "USER", "ST1", "ST3",
                                               "if-1", "if-2", "if-3", "if-4", "if-5", "legend"}
    missing = sorted(want - ids)
    print(f"[svg] elements={sum(1 for _ in tree.iter())} ids={len(ids)} missing={missing}")
    return [f"[svg] missing ids {missing}"] if missing else []


def check_needles(root: Path) -> list[str]:
    needles = [base64.b64decode(x) for x in HOST_FACTS_B64] + [b"MBNAS-CANARY", b"/home/", b"147.46."]
    errors = []
    files = doc_files(root) + [root / "scripts/dev/gen_registration_docs.py"]
    files += sorted(p for p in (root / "docs/public").rglob("*") if p.is_file())
    for f in sorted(set(files)):
        data = f.read_bytes()
        for i, n in enumerate(needles):
            if n in data:
                errors.append(f"[needles] {f.relative_to(root)}: needle #{i}")
    print(f"[needles] files={len(set(files))} hits={len(errors)}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[5]))
    root = Path(ap.parse_args().root).resolve()
    errors = check_options(root) + check_links(root) + check_svg(root) + check_needles(root)
    for e in errors:
        print("FAIL", e)
    print("RESULT", "FAIL" if errors else "PASS", f"({len(errors)} problem(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
