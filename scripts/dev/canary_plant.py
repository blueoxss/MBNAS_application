#!/usr/bin/env python3
"""Plant T8 canary tokens into owner-only locations of a canary *test build tree* (SECURITY_BASELINE §5.2/§5.4).

Markers exist only in test builds (SECURITY_BASELINE decision D9 (a), DD-A20): this tool never plants into the project
tree. ``--fresh`` creates the tree as a copy of the project (default source: the project root of this script) without
``runtime/``, ``dist/``, ``build/`` (except ``build/allowlists``), ``node_modules``, VCS and cache directories,
compiled files, dotenv files (``.env``, ``.env.*``, ``*.env``; never copied, never read) and ``docs/internal/canary``,
and marks it with ``.mbnas-canary-tree``. Planting, stripping and re-creation refuse any directory without that mark,
the project root itself and any ancestor of the source.

Token ``MBNAS-CANARY-<AREA>-<16 hex>`` (``secrets.token_hex(8)``), one per area (``PLANTS``): core policy modules
(search-space prior, repair seed variants, fitness/promotion), core DB schema, owner tests and shared test plumbing,
internal documents, owner env examples (secret-like keys), owner deployment/install files. A missing env/deploy file
is created in the tree (``created: true``) so the allowlist rule for that path is still exercised. ``MANIFEST_SELF``
exists only in the manifest: it must not be in any bundle (``docs/internal/canary/**`` is denied by every allowlist).

Idempotent: a planted block is delimited (``>>> MBNAS canary`` ... ``<<< MBNAS canary``) and replaced on every run;
with an existing manifest the same tokens are reused, so re-planting gives identical bytes. ``--rotate`` draws new
tokens. ``--strip`` removes every block and created file and checks the recorded original sha256.

Manifest (owner-only; never in any ZIP): default ``<project>/docs/internal/canary/CANARY_MANIFEST.json`` (schema
``mbnas.canary_manifest/1``); a copy is written into the tree at the same relative path.

Usage: canary_plant.py --tree DIR [--fresh] [--source DIR] [--manifest PATH] [--rotate]
       canary_plant.py --tree DIR --strip [--manifest PATH]
Exit status: 0 ok, 1 strip mismatch, 2 usage / refused directory.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "mbnas.canary_manifest/1"
MARK = ".mbnas-canary-tree"
PREFIX = "MBNAS-" + "CANARY-"
MANIFEST_REL = "docs/internal/canary/CANARY_MANIFEST.json"
PUBLIC_TARGETS = ["public", "registration", "web_dist", "public_openapi", "public_api_responses", "gateway_logs",
                  "runner_logs"]
# area, path in the tree, block kind, create when missing, description
PLANTS: tuple[tuple[str, str, str, bool, str], ...] = (
    ("POLICY_PRIOR", "mbnas_core/src/mbnas_core/search/space.py", "py", False, "search-space prior tables (S-PRIOR)"),
    ("POLICY_REPAIR", "mbnas_core/src/mbnas_core/search/mixed_pool/seed_variants.py", "py", False,
     "mixed-pool repair / seed variants (S-GATE)"),
    ("FITNESS", "mbnas_core/src/mbnas_core/protocol/fitness.py", "py", False, "fitness rules (S-PROMO)"),
    ("FITNESS", "mbnas_core/src/mbnas_core/protocol/promotion.py", "py", False, "promotion rules (S-PROMO)"),
    ("CORE_STATE", "mbnas_core/src/mbnas_core/db/schema.sql", "sql", False, "authoritative core DB schema (S-STATE)"),
    ("OWNER_TEST", "tests/owner/conftest.py", "py", False, "owner test suite"),
    ("OWNER_TEST", "tests/_shared/harness.py", "py", False, "shared test plumbing (owner bundle only)"),
    ("OWNER_DOC", "docs/internal/DECISIONS.md", "md", False, "internal design decisions"),
    ("OWNER_DOC", "docs/internal/TASK_STATE.md", "md", False, "internal task state"),
    ("OWNER_DOC", "docs/internal/provenance/vendored_reference/README.md", "md", False, "owner-only provenance copy"),
    ("OWNER_SECRET", "config/examples/core.env.example", "env", True, "core env example secret keys (S-CRED)"),
    ("OWNER_SECRET", "config/examples/owner-all-in-one.env.example", "env", True, "owner env example secret keys"),
    ("OWNER_DEPLOY", "deploy/owner/mbnas-core.service.example", "hash", True, "owner deployment unit"),
    ("OWNER_DEPLOY", "requirements-owner.txt", "hash", True, "owner install requirements"),
)
AREA_DESCRIPTIONS = {
    "POLICY_PRIOR": "core search-space prior module", "POLICY_REPAIR": "core repair / seed-variant module",
    "FITNESS": "core fitness and promotion modules", "CORE_STATE": "core DB schema",
    "OWNER_TEST": "owner tests and shared test plumbing", "OWNER_DOC": "internal documents",
    "OWNER_SECRET": "owner env example secrets", "OWNER_DEPLOY": "owner deployment and install files",
    "MANIFEST_SELF": "the manifest itself (must not be in any bundle)",
}
EXCLUDED_ROOT_DIRS = ("runtime", "dist")
EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv",
                 ".vite", "test-results", "playwright-report"}
EXCLUDED_FILE_GLOBS = ("*.pyc", "*.pyo", ".env", ".env.*", "*.env")
BLOCK_RE = re.compile(r"[^\n]*>>> MBNAS canary.*?<<< MBNAS canary <<<[^\n]*\n?", re.S)


class Refused(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel_to_project(p: Path) -> str:
    try:
        return p.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return "<outside the project>"


def _check_tree(tree: Path, source: Path, must_be_marked: bool) -> Path:
    tree = tree.resolve()
    source = source.resolve()
    if tree == PROJECT_ROOT or tree == source:
        raise Refused(f"refusing to use the project/source root itself as canary tree: {tree}")
    if tree in source.parents:
        raise Refused(f"refusing a canary tree that contains the source tree: {tree}")
    if must_be_marked and not (tree / MARK).is_file():
        raise Refused(f"{tree} is not a canary build tree (no {MARK}); create it with --fresh")
    return tree


def make_tree(tree: Path, source: Path = PROJECT_ROOT) -> dict:
    """(Re)create ``tree`` as a filtered copy of ``source``; returns copy statistics."""
    source = source.resolve()
    tree = _check_tree(tree, source, must_be_marked=False)
    if tree.exists():
        if not (tree / MARK).is_file():
            raise Refused(f"{tree} exists and is not a canary build tree (no {MARK}); not deleting it")
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    files = links = 0
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        rel_dir = Path(dirpath).relative_to(source)
        (tree / rel_dir).mkdir(parents=True, exist_ok=True)
        keep = []
        for d in sorted(dirnames):
            rel = (rel_dir / d).as_posix()
            full = Path(dirpath) / d
            if d in EXCLUDED_DIRS or d.endswith(".egg-info") or rel in EXCLUDED_ROOT_DIRS:
                continue
            if rel == "docs/internal/canary" or full.resolve() == tree or tree in full.resolve().parents:
                continue
            if rel == "build" or rel.startswith("build/"):
                if not ("build/allowlists".startswith(rel) or rel.startswith("build/allowlists")):
                    continue
            if full.is_symlink():
                (tree / rel).symlink_to(os.readlink(full))
                links += 1
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            if any(fnmatch.fnmatchcase(fn, g) for g in EXCLUDED_FILE_GLOBS):
                continue
            rel = (rel_dir / fn).as_posix()
            if rel_dir.as_posix() == "build":
                continue  # loose files under build/ (only build/allowlists/** is copied)
            src, dst = Path(dirpath) / fn, tree / rel
            if src.is_symlink():
                dst.symlink_to(os.readlink(src))
                links += 1
            elif src.is_file():
                shutil.copy2(src, dst)
                files += 1
    (tree / MARK).write_text("MBNAS canary test build tree (scripts/dev/canary_plant.py); never package it as a "
                             "release.\n", encoding="utf-8")
    return {"files": files, "symlinks": links}


def _block(kind: str, area: str, token: str) -> str:
    if kind == "py":
        return (f"# >>> MBNAS canary (test build only; scripts/dev/canary_plant.py) >>>\n"
                f"_MBNAS_CANARY_{area} = \"{token}\"\n# <<< MBNAS canary <<<\n")
    if kind == "md":
        return f"<!-- >>> MBNAS canary (test build only) >>> {token} <<< MBNAS canary <<< -->\n"
    if kind == "env":
        return (f"# >>> MBNAS canary (test build only; test values, not secrets) >>>\n"
                f"MBNAS_OWNER_TOKEN={token}\nMBNAS_CORE_TICKET_KEY={token}\n# <<< MBNAS canary <<<\n")
    if kind == "sql":
        return f"-- >>> MBNAS canary (test build only) >>> {token} <<< MBNAS canary <<<\n"
    if kind == "hash":
        return f"# >>> MBNAS canary (test build only) >>> {token} <<< MBNAS canary <<<\n"
    raise ValueError(kind)


def _new_token(area: str) -> str:
    return f"{PREFIX}{area}-{secrets.token_hex(8)}"


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp-canary")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def plant(tree: Path, manifest_path: Path, rotate: bool = False, source: Path = PROJECT_ROOT) -> dict:
    tree = _check_tree(tree, source, must_be_marked=True)
    old = None
    if manifest_path.is_file():  # read also with --rotate: its records say which files this tool created
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old.get("schema") != SCHEMA:
            raise Refused(f"{manifest_path}: unexpected schema {old.get('schema')!r}")
    tokens = {} if rotate else {a["area"]: a["value"] for a in (old or {}).get("areas", [])}
    drawn_at = None if rotate else (old or {}).get("tokens_drawn_at")
    areas_order = list(dict.fromkeys([p[0] for p in PLANTS] + ["MANIFEST_SELF"]))
    if any(a not in tokens for a in areas_order):
        drawn_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for a in areas_order:
            tokens.setdefault(a, _new_token(a))
    planted: dict[str, list[dict]] = {a: [] for a in areas_order}
    for area, rel, kind, create, _desc in PLANTS:
        path = tree / rel
        if path.is_symlink():
            raise Refused(f"{rel}: is a symbolic link in the tree; not planting through it")
        created = False
        if path.is_file():
            text = path.read_bytes().decode("utf-8")  # exact bytes (no newline translation)
        elif create:
            path.parent.mkdir(parents=True, exist_ok=True)
            text, created = "", True
        else:
            planted[area].append({"path": rel, "kind": kind, "status": "missing (not planted)"})
            continue
        base = BLOCK_RE.sub("", text)
        prior = next((e for e in ((old or {}).get("areas") or []) if e["area"] == area), None)
        prior_entry = next((p for p in (prior or {}).get("planted_in", []) if p.get("path") == rel), None)
        # a file this tool created in an earlier run of the same tree holds nothing but the block
        created = created or (bool((prior_entry or {}).get("created")) and base == "")
        sep = "" if (base == "" or base.endswith("\n")) else "\n"
        new = base + sep + _block(kind, area, tokens[area])
        data = new.encode("utf-8")
        if not path.is_file() or path.read_bytes() != data:
            _write_atomic(path, data)
        planted[area].append({"path": rel, "kind": kind, "created": created, "appended_separator": bool(sep),
                              "original_sha256": None if created else _sha(base.encode("utf-8")),
                              "planted_sha256": _sha(data), "status": "planted"})
    areas = []
    for a in areas_order:
        entry = {"area": a, "value": tokens[a], "description": AREA_DESCRIPTIONS.get(a, a)}
        if a == "MANIFEST_SELF":
            entry.update(expected_in=[], must_not_in=["owner"] + PUBLIC_TARGETS,
                         planted_in=[{"path": MANIFEST_REL, "kind": "manifest", "status": "planted"}])
        else:
            entry.update(expected_in=["owner"], must_not_in=list(PUBLIC_TARGETS), planted_in=planted[a])
        areas.append(entry)
    doc = {"schema": SCHEMA, "generator": "scripts/dev/canary_plant.py", "token_format": "MBNAS-CANARY-<AREA>-<16 hex>",
           "tokens_drawn_at": drawn_at, "tree": _rel_to_project(tree),
           "note": "owner-only; test builds only (SECURITY_BASELINE D9 (a)); never part of any ZIP", "areas": areas}
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(manifest_path, text.encode("utf-8"))
    tree_copy = tree / MANIFEST_REL
    tree_copy.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(tree_copy, text.encode("utf-8"))
    return doc


def strip(tree: Path, manifest_path: Path, source: Path = PROJECT_ROOT) -> list[str]:
    """Remove every planted block / created file; returns the list of mismatches (empty = restored)."""
    tree = _check_tree(tree, source, must_be_marked=True)
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for a in doc["areas"]:
        for p in a.get("planted_in", []):
            if p.get("status") != "planted" or p.get("kind") == "manifest":
                continue
            path = tree / p["path"]
            if not path.is_file():
                problems.append(f"{p['path']}: missing")
                continue
            text = path.read_bytes().decode("utf-8")
            base = BLOCK_RE.sub("", text)
            if p.get("created"):
                if base.strip():
                    problems.append(f"{p['path']}: created file holds other content; left in place")
                else:
                    path.unlink()
                continue
            if p.get("appended_separator") and base.endswith("\n"):
                base = base[:-1]
            _write_atomic(path, base.encode("utf-8"))
            if p.get("original_sha256") and _sha(base.encode("utf-8")) != p["original_sha256"]:
                problems.append(f"{p['path']}: sha256 after strip differs from the recorded original")
    copy = tree / MANIFEST_REL
    if copy.is_file():
        copy.unlink()
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, help="canary build tree (created with --fresh)")
    ap.add_argument("--fresh", action="store_true", help="(re)create the tree as a filtered copy of --source first")
    ap.add_argument("--source", default=str(PROJECT_ROOT), help="source tree for --fresh (default: project root)")
    ap.add_argument("--manifest", default=str(PROJECT_ROOT / MANIFEST_REL), help="manifest path (owner-only)")
    ap.add_argument("--rotate", action="store_true", help="draw new tokens instead of reusing the manifest's")
    ap.add_argument("--strip", action="store_true", help="remove the planted blocks and created files")
    a = ap.parse_args(argv)
    tree, source, manifest = Path(a.tree), Path(a.source), Path(a.manifest)
    try:
        if a.strip:
            problems = strip(tree, manifest, source)
            for p in problems:
                print(f"canary_plant: strip: {p}", file=sys.stderr)
            print(f"canary_plant: stripped {tree} ({'ok' if not problems else f'{len(problems)} problem(s)'})")
            return 1 if problems else 0
        if a.fresh:
            stats = make_tree(tree, source)
            print(f"canary_plant: tree {tree}: copied {stats['files']} files, {stats['symlinks']} symlinks")
        doc = plant(tree, manifest, rotate=a.rotate, source=source)
    except Refused as exc:
        print(f"canary_plant: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"canary_plant: error: {exc}", file=sys.stderr)
        return 2
    for area in doc["areas"]:
        locs = ", ".join(f"{p['path']}{' (created)' if p.get('created') else ''}"
                         if p.get("status") == "planted" else f"{p['path']} [missing]" for p in area["planted_in"])
        print(f"canary_plant: {area['area']:<14} -> {locs}")
    print(f"canary_plant: manifest {manifest} (copy in tree: {MANIFEST_REL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
