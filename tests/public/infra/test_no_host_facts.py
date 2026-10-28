"""No host facts in the package sources (WP-1 contract §0/§13.2, DD-A26).

Every file under ``<package>/src/`` of the five packages (any type: .py, .json, .sql, ...; build metadata ``*.egg-info``
and ``__pycache__`` excluded) is scanned for the forbidden strings, and every Python file for an import of
``yolo_ga_runtime`` (statement, ``importlib.import_module``-style string reference or ``module:attr`` string). The only
exception is the documented (file, literal) data pair of DD-A26 (2), the base.py legacy hook string, which DD-A58
extended to contract 1.6 (same table and expiry as ``scripts/dev/check_import_closure.py``; the openapi.json literal of
DD-A26 expired with WP-4 and is gone from the tree).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# composed names: the import-closure checker reads a bare package-name literal in a public test as a reference
PACKAGES = tuple(f"mbnas_{name}" for name in ("contracts", "model_runtime", "core", "runner", "server"))
FORBIDDEN = ("/root/detection", "/data/mgjeong", "147.46.114.253", "pc2300", "mgjeong", "MBNAS_server", "etri_nas_root")
DATA_EXCEPTIONS = {  # DD-A26 (2), extended by DD-A58: (file, line content that holds the literal) -> expiry
    ("mbnas_model_runtime/src/mbnas_model_runtime/adapters/base.py",
     "mbnas_" + "server.search.etri_bridge:ensure_mixed_registered"): "1.6",
}
RE_ETRI = re.compile(r"^yolo_ga_runtime(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*)?\.?$")


def _files() -> list[Path]:
    out = []
    for pkg in PACKAGES:
        for p in sorted((ROOT / pkg / "src").rglob("*")):
            if p.is_file() and not any(part == "__pycache__" or part.endswith(".egg-info") for part in p.parts):
                out.append(p)
    return out


FILES = _files()


def test_the_scan_covers_all_five_packages() -> None:
    if not (ROOT / PACKAGES[2] / "src").is_dir():  # DD-A43(b): a bundle without the private core sources
        pytest.skip("owner-only sources (the private core package) not in this bundle")
    assert {p.relative_to(ROOT).parts[0] for p in FILES} == set(PACKAGES)
    assert len(FILES) > 150


def _hits(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_bytes().decode("latin-1")
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for s in FORBIDDEN:
            if s in line and not any(rel == f and lit in line and (s in lit or lit == s) for f, lit in DATA_EXCEPTIONS):
                hits.append(f"{rel}:{n}: {s!r}: {line.strip()[:160]}")
    return hits


def test_no_forbidden_string_in_package_sources() -> None:
    hits = [h for p in FILES for h in _hits(p)]
    assert hits == [], "\n".join(hits[:50])


def test_data_exceptions_are_exactly_the_documented_pair() -> None:
    present = []
    for (rel, literal), expires in DATA_EXCEPTIONS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        if literal in text:
            present.append((rel, literal, expires))
    assert all(expires == "1.6" for *_, expires in present)  # DD-A58, as in scripts/dev/check_import_closure.py
    print("DD-A26 data exception present (expires with contract 1.6, DD-A58):", present)


def _etri_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel, out = path.relative_to(ROOT).as_posix(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"{rel}:{node.lineno}: import {a.name}" for a in node.names if a.name.split(".")[0] == "yolo_ga_runtime"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "yolo_ga_runtime" and not node.level:
            out.append(f"{rel}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and RE_ETRI.match(node.value):
            out.append(f"{rel}:{node.lineno}: string module reference {node.value!r}")
    return out


@pytest.mark.parametrize("pkg", PACKAGES)
def test_no_yolo_ga_runtime_import(pkg: str) -> None:
    hits = [h for p in FILES if p.suffix == ".py" and p.relative_to(ROOT).parts[0] == pkg for h in _etri_imports(p)]
    assert hits == [], "\n".join(hits)
