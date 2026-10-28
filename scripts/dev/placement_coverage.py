#!/usr/bin/env python3
"""PLACEMENT_MANIFEST coverage of the five package source trees (C01 ``call_graph`` mandatory check, EVAL_1 D1).

Every file under ``<package>/src/`` of mbnas_contracts, mbnas_model_runtime, mbnas_core, mbnas_runner and mbnas_server
(``.py`` and data files; ``__pycache__``, ``*.egg-info``, ``*.pyc``/``*.pyo`` excluded) must have a placement record in
``docs/internal/provenance/PLACEMENT_MANIFEST.jsonl``, the append-only placement log of this repository.

Row semantics (one JSON object per line, file order = chronological order):

* a placement event of path P: a row whose ``target_relpath`` is P (``kind`` moved | new | split | modified |
  reference_copy); ``target_sha256`` is the sha256 of P when that row was written. Later edits do not rewrite earlier
  rows: a WP that changes P appends a ``modified`` row (some did not, so the current sha256 of P may differ from the
  last recorded one; reported as ``sha_drift``, informational);
* a deletion event of P: a ``deleted`` row with ``target_relpath`` null and ``source_relpath`` P. For a path of the
  original checkout (``application/...``, ``yolo_ga...``) it means "not ported"; for a path of this repository it
  records the removal of a placed file.

Findings (each fails the check):

* ``missing``: a package file without any placement event;
* ``deleted_but_present``: a package file whose last event is a deletion;
* ``unrecorded_deletions``: a package-source path whose last event is a placement but which no longer exists;
* ``errors``: malformed rows (missing keys, unknown kind, sha256 not 64 hex, a target path that is absolute or
  contains ``..``).

Output: one JSON document (``--format text`` for a summary). Exit 0 = no finding; 1 = findings; 2 = usage error.
Importable: ``run(root, manifest=None, packages=PACKAGES) -> dict`` (also used by tests/public/infra).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "mbnas.placement_coverage/1"
ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = "docs/internal/provenance/PLACEMENT_MANIFEST.jsonl"
PACKAGES = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner", "mbnas_server")
PLACEMENT_KINDS = {"moved", "new", "split", "modified", "reference_copy"}
KINDS = PLACEMENT_KINDS | {"deleted"}
REQUIRED_KEYS = ("kind", "notes", "owner_wp", "source_relpath", "source_sha256", "target_relpath", "target_sha256")
RE_SHA = re.compile(r"^[0-9a-f]{64}$")


def package_files(root: Path, packages: Iterable[str]) -> list[str]:
    out = []
    for pkg in packages:
        base = root / pkg / "src"
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(root)
            if any(part == "__pycache__" or part.endswith(".egg-info") for part in rel.parts):
                continue
            if p.is_file() and not p.is_symlink() and p.suffix not in (".pyc", ".pyo"):
                out.append(rel.as_posix())
    return out


def row_errors(n: int, row: Any) -> list[str]:
    if not isinstance(row, dict):
        return [f"line {n}: not an object"]
    errs = [f"line {n}: missing key {k!r}" for k in REQUIRED_KEYS if k not in row]
    kind = row.get("kind")
    if kind not in KINDS:
        errs.append(f"line {n}: unknown kind {kind!r}")
    for k in ("source_sha256", "target_sha256"):
        v = row.get(k)
        if v is not None and not (isinstance(v, str) and RE_SHA.match(v)):
            errs.append(f"line {n}: {k} is not 64 lowercase hex")
    tgt = row.get("target_relpath")
    if kind in PLACEMENT_KINDS and not (isinstance(tgt, str) and tgt):
        errs.append(f"line {n}: {kind} row without target_relpath")
    if kind in PLACEMENT_KINDS and not row.get("target_sha256"):
        errs.append(f"line {n}: {kind} row without target_sha256")
    if kind == "deleted" and tgt is not None:
        errs.append(f"line {n}: deleted row with a target_relpath")
    for k in ("source_relpath", "target_relpath"):
        v = row.get(k)
        if isinstance(v, str) and (v.startswith("/") or ".." in Path(v).parts):
            errs.append(f"line {n}: {k} must be a relative path without '..'")
    return errs


def run(root: Path | str = ROOT, manifest: Path | str | None = None, packages: Iterable[str] = PACKAGES) -> dict[str, Any]:
    root = Path(root)
    packages = tuple(packages)
    mpath = Path(manifest) if manifest is not None else root / MANIFEST_REL
    errors: list[str] = []
    events: dict[str, list[tuple[int, str, dict]]] = {}  # path -> [(line, "place" | "delete", row)]
    lines = mpath.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {n}: invalid JSON ({exc.msg})")
            continue
        errs = row_errors(n, row)
        errors.extend(errs)
        if errs:
            continue
        if row["kind"] in PLACEMENT_KINDS:
            events.setdefault(row["target_relpath"], []).append((n, "place", row))
        elif row["kind"] == "deleted" and row.get("source_relpath"):
            events.setdefault(row["source_relpath"], []).append((n, "delete", row))
    missing_src = [f"{pkg}/src" for pkg in packages if not (root / pkg / "src").is_dir()]
    files = package_files(root, packages)
    present = set(files)
    missing, deleted_but_present, drift = [], [], []
    for rel in files:
        ev = events.get(rel)
        placed = [e for e in ev or [] if e[1] == "place"]
        if not placed:
            missing.append(rel)
            continue
        if ev[-1][1] == "delete":
            deleted_but_present.append({"path": rel, "deleted_at_line": ev[-1][0]})
            continue
        last = ev[-1][2]
        current = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        if current != last["target_sha256"]:
            drift.append({"path": rel, "last_line": ev[-1][0], "recorded": last["target_sha256"], "current": current})
    prefixes = tuple(f"{pkg}/src/" for pkg in packages)
    unrecorded = sorted(p for p, ev in events.items()
                        if p.startswith(prefixes) and ev[-1][1] == "place" and p not in present and not (root / p).exists())
    recorded_deleted = sorted(p for p, ev in events.items() if p.startswith(prefixes) and ev[-1][1] == "delete" and p not in present)
    py = [f for f in files if f.endswith(".py")]
    by_pkg = {pkg: sum(1 for f in files if f.startswith(pkg + "/")) for pkg in packages}
    findings = len(missing) + len(deleted_but_present) + len(unrecorded) + len(errors) + len(missing_src)
    return {
        "schema": SCHEMA, "manifest": mpath.relative_to(root).as_posix() if mpath.is_relative_to(root) else str(mpath),
        "manifest_sha256": hashlib.sha256(mpath.read_bytes()).hexdigest(), "manifest_rows": sum(1 for l in lines if l.strip()),
        "packages": list(packages), "missing_package_src": missing_src,
        "scanned": {"files": len(files), "py": len(py), "data": len(files) - len(py), "by_package": by_pkg},
        "covered": {"files": len(files) - len(missing), "py": len(py) - sum(1 for m in missing if m.endswith(".py"))},
        "missing": missing, "deleted_but_present": deleted_but_present, "unrecorded_deletions": unrecorded,
        "recorded_deletions_of_package_files": recorded_deleted,
        "sha_drift_count": len(drift), "sha_drift": drift,
        "errors": errors, "ok": findings == 0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--manifest", help=f"default: <root>/{MANIFEST_REL}")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    mpath = Path(args.manifest) if args.manifest else root / MANIFEST_REL
    if not mpath.is_file():
        print(f"no manifest at {mpath}", file=sys.stderr)
        return 2
    rep = run(root, mpath)
    if args.format == "json":
        print(json.dumps(rep, indent=1))
    else:
        s, c = rep["scanned"], rep["covered"]
        print(f"manifest {rep['manifest']} ({rep['manifest_rows']} rows, sha256 {rep['manifest_sha256'][:12]}...)")
        print(f"package files {s['files']} (py {s['py']}, data {s['data']}) {s['by_package']}")
        print(f"covered {c['files']}/{s['files']} (py {c['py']}/{s['py']}); missing {len(rep['missing'])}, deleted but present "
              f"{len(rep['deleted_but_present'])}, unrecorded deletions {len(rep['unrecorded_deletions'])}, row errors "
              f"{len(rep['errors'])}; recorded deletions {len(rep['recorded_deletions_of_package_files'])}; "
              f"sha drift (informational) {rep['sha_drift_count']}")
        for key in ("missing", "deleted_but_present", "unrecorded_deletions", "errors", "missing_package_src"):
            for item in rep[key]:
                print(f"  {key}: {item}")
        print("ok" if rep["ok"] else "FINDINGS")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
