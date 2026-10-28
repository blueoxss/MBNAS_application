#!/usr/bin/env python3
"""Print the deterministic source-tree SHA-256 (artifact_ref) of named MBNAS package trees as JSON.

Algorithm mbnas-source-tree/2 (``mbnas_contracts.util.source_hash``). WP-1 (contract §4, DD-A29): there are no
default monorepo roots any more; the roots are the installed package directories named with ``--package`` (default:
the five MBNAS packages, labelled by import name via ``package_roots``) plus any explicit ``--root LABEL=PATH``.

Usage: source_tree_hash.py [--package NAME ...] [--root LABEL=PATH ...] [--digest-only]
       source_tree_hash.py [--package NAME ...] [--root LABEL=PATH ...] --manifest [--with-docs]
       source_tree_hash.py --diff BEFORE.json AFTER.json

``--manifest`` prints the per-file sha256 of every hashed file (``files``: path -> digest, ``missing`` for a missing
root) with the same digest as artifact_ref; ``--with-docs`` also lists the documentation roots (``docs/``,
``README.md`` of the project root; never ``.env``), so ``sha256`` then differs from ``artifact_ref``. ``--diff``
compares two manifests taken at phase boundaries and prints the added / removed / changed paths: the file-change
record of that phase.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from pathlib import Path

from mbnas_contracts.util.source_hash import ALGORITHM, _entries, compute_source_tree_hash, package_roots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGES = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner", "mbnas_server")


def doc_roots(project_root: str | os.PathLike = PROJECT_ROOT) -> list[tuple[str, str]]:
    """Documentation roots that artifact_ref leaves out on purpose, for change records only."""
    return [("docs", os.path.join(project_root, "docs")), ("README.md", os.path.join(project_root, "README.md"))]


def _parse_root(text: str) -> tuple[str, str]:
    label, sep, path = text.partition("=")
    if not sep or not label or not path:
        raise argparse.ArgumentTypeError(f"--root expects LABEL=PATH, got {text!r}")
    return label, path


def manifest(roots: list[tuple[str, str]], artifact_ref: str) -> dict:
    """Per-file digests from the artifact_ref walk; ``sha256`` uses the artifact_ref formula over ``roots``."""
    files: dict[str, str] = {}
    for label, path in roots:
        present = os.path.isdir(path) or os.path.isfile(path) or os.path.islink(path)
        files.update(_entries(label, path) if present else [(label, "missing")])
    h = hashlib.sha256()
    for rel, digest in sorted(files.items()):
        h.update(f"{rel}\0{digest}\n".encode())
    return {"algorithm": ALGORITHM, "sha256": "sha256:" + h.hexdigest(), "artifact_ref": artifact_ref,
            "roots": [label for label, _ in roots], "files": dict(sorted(files.items()))}


def diff(before: dict, after: dict) -> dict:
    old, new = before["files"], after["files"]
    return {"old_sha256": before["sha256"], "new_sha256": after["sha256"],
            "added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
            "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
            "unchanged": sum(1 for k in set(old) & set(new) if old[k] == new[k])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", action="append", default=None, metavar="NAME",
                    help="installed package to hash (repeatable; default: the five MBNAS packages)")
    ap.add_argument("--root", action="append", default=[], type=_parse_root, metavar="LABEL=PATH",
                    help="additional explicit root (repeatable)")
    ap.add_argument("--digest-only", action="store_true", help="print only sha256:<hex>")
    ap.add_argument("--manifest", action="store_true", help="print the per-file sha256 manifest")
    ap.add_argument("--with-docs", action="store_true", help="with --manifest: also list the documentation roots")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two --manifest outputs")
    a = ap.parse_args(argv)
    if a.diff:
        print(json.dumps(diff(*(json.loads(Path(f).read_text()) for f in a.diff)), indent=1))
        return 0
    roots = package_roots(tuple(a.package) if a.package else DEFAULT_PACKAGES) + list(a.root)
    result = compute_source_tree_hash(roots)
    if a.manifest:
        extra = doc_roots() if a.with_docs else []
        print(json.dumps(manifest(roots + extra, result["sha256"]), indent=1))
        return 0
    print(result["sha256"] if a.digest_only else json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
