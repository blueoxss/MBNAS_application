#!/usr/bin/env python3
"""Selective provenance copy of the original checkout into ``build/source_snapshot`` (read-only on the source).

Provenance of this file (C01 fix round 1b, EVAL_1 D2, 2026-10-28): adapted from the designer's session-scratchpad tool
``tools/snapshot_copy.py`` (sha256 eb568449356a2cf208a38334cf27f39f9f1b5a76fa9962a0814acca50ca943f8, 8869 bytes,
2026-10-23 21:07 KST), which produced ``docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl`` and
``SOURCE_SNAPSHOT_SUMMARY.json`` on 2026-10-23 (run with ``python3.12 -I``). The verbatim original is kept at
``docs/internal/evidence/PROVENANCE/historical/tools/snapshot_copy.py``. The selection tables (INCLUDE_TREES,
INCLUDE_FILES, EXCLUDE_*), the per-file record and the status rule are unchanged. Changes: the source root is a
required argument (no host default); every git call goes through :func:`git`, which allows read-only subcommands only
and always passes ``--no-optional-locks``; writing needs ``--write`` and never overwrites an existing snapshot or
manifest (the snapshot of this repository is write-once; later changes go through ``snapshot_delta.py``); the helpers
are importable by ``snapshot_delta.py`` / ``snapshot_compare.py``.

Read-only guarantee towards the source checkout: files are opened ``rb`` only, git runs only ``rev-parse``,
``ls-tree``, ``ls-files``, ``status``, ``rev-list`` and ``log`` with ``--no-optional-locks`` (no index refresh, no
lock file) and ``core.fsmonitor=false``; destinations under the source root are refused.

Per-file record: source relpath, snapshot relpath, role group, bytes, sha256, git blob sha1, HEAD blob, index blob,
status (tracked_clean | tracked_modified | tracked_staged_modified | untracked), source mtime, copied_at, license note.

Usage::

    python -I scripts/dev/snapshot_copy.py --source-root /path/to/original            # dry run: counts only
    python -I scripts/dev/snapshot_copy.py --source-root SRC --write \\
        --snapshot-dir /tmp/x/snap --provenance-dir /tmp/x/prov                          # copy into empty dirs

Exit: 0 ok, 2 usage error / refused write, 3 git or I/O failure.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Iterator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SNAPSHOT_DIR = os.path.join(REPO_ROOT, "build", "source_snapshot")
DEFAULT_PROVENANCE_DIR = os.path.join(REPO_ROOT, "docs", "internal", "provenance")
MAX_BYTES = 20 * 1024 * 1024

INCLUDE_TREES = {
    "application/MBNAS_server/src": "server-source",
    "application/MBNAS_server/tests": "server-tests",
    "application/MBNAS_server/contracts": "contracts",
    "application/MBNAS_server/config": "config-examples",
    "application/MBNAS_server/deploy": "deploy-examples",
    "application/MBNAS_server/docs": "server-docs",
    "application/MBNAS_server/scripts": "server-scripts",
    "application/MBNAS_web/src": "web-source",
    "application/MBNAS_web/tests": "web-tests",
    "application/MBNAS_web/e2e": "web-e2e",
    "application/MBNAS_web/public": "web-public",
    "application/MBNAS_web/scripts": "web-scripts",
    "application/MBNAS_Server_Handoff_20261006": "handoff-docs(owner)",
    "application/docs": "application-docs(owner)",
    "yolo_ga_runtime": "etri-mixed-pool-runtime",
    "yolo_ga_full_runtime": "etri-full-runtime(reference)",
}
INCLUDE_FILES = {
    "application/MBNAS_server/pyproject.toml": "server-manifest",
    "application/MBNAS_server/requirements.lock.txt": "server-lock",
    "application/MBNAS_server/environment.lock.json": "server-envlock",
    "application/MBNAS_server/README.md": "server-readme",
    "application/MBNAS_server/.env.example": "server-env-example",
    "application/MBNAS_web/index.html": "web-entry",
    "application/MBNAS_web/package.json": "web-manifest",
    "application/MBNAS_web/package-lock.json": "web-lock",
    "application/MBNAS_web/playwright.config.ts": "web-e2e-config",
    "application/MBNAS_web/playwright.real.config.ts": "web-e2e-config",
    "application/MBNAS_web/playwright.archive-real.config.ts": "web-e2e-config",
    "application/MBNAS_web/tsconfig.json": "web-tsconfig",
    "application/MBNAS_web/vite.config.ts": "web-vite-config",
    "application/MBNAS_web/README.md": "web-readme",
    "application/scripts/console_stack.sh": "stack-script",
    "application/README.md": "application-readme",
    "application/docs_console_etri/README_NAS_ARCHIVE.md": "archive-design(owner)",
    "application/docs_console_etri/DESIGN_nas_archive_console_20261015.md": "archive-design(owner)",
    "_2026_IVC_/main.tex": "paper-manuscript(owner reference)",
    "_2026_IVC_/manifest.txt": "paper-manifest(owner reference)",
}
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "dist", "test-results", ".vitest", "evidence",
                     "evidence_I1", "evidence_I3", "evidence_I4", "evidence_impl", "live_harness_out"}
EXCLUDE_FILE_GLOBS = ["*.pyc", "*.pyo", ".env", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.pt", "*.pth", "*.onnx", "*.engine",
                      "*.sqlite3", "*.db", "*.zip", "*.tar", "*.gz"]
ALLOW_IMAGE_UNDER = ("application/MBNAS_web/public",)
LICENSE_CODE = "no LICENSE file in etri_nas root; ETRI_NAS research code, rights review pending"
LICENSE_PAPER = "paper manuscript; owner reference only"
# git subcommands that never write to the repository (with --no-optional-locks: no index refresh / lock file)
READ_ONLY_GIT = frozenset({"rev-parse", "ls-tree", "ls-files", "status", "rev-list", "log"})


class ToolError(RuntimeError):
    """git / I/O failure (exit 3)."""


def git(src: str, *args: str) -> str:
    """Run a read-only git subcommand in ``src``; anything else is refused."""
    if not args or args[0] not in READ_ONLY_GIT:
        raise ValueError(f"refused git subcommand {args[:1]!r}: only {sorted(READ_ONLY_GIT)} are allowed")
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    proc = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", src, *args],
                          capture_output=True, env=env)
    if proc.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace')[:500]}")
    return proc.stdout.decode("utf-8", "replace")


def z_entries(out: str) -> Iterator[str]:
    for entry in out.split("\0"):
        if entry:
            yield entry


def blob_sha1(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def classify_status(blob: str, head_blob: str | None, index_blob: str | None) -> str:
    """The status rule of the original tool (blob comparison, not ``git status``)."""
    if head_blob is None and index_blob is None:
        return "untracked"
    if blob == head_blob:
        return "tracked_clean"
    if index_blob is not None and blob == index_blob and index_blob != head_blob:
        return "tracked_staged_modified"
    return "tracked_modified"


def license_of(rel: str) -> str:
    return LICENSE_CODE if rel.startswith(("yolo_ga", "application")) else LICENSE_PAPER


def role_of(rel: str) -> str | None:
    """Role group of a source relpath under the selection tables (None = not selected by tree or file)."""
    if rel in INCLUDE_FILES:
        return INCLUDE_FILES[rel]
    for tree, role in INCLUDE_TREES.items():
        if rel.startswith(tree + "/"):
            return role
    return None


def walk_no_follow(base: str, skip_dir=lambda name: False) -> Iterator[tuple[str, str, bool]]:
    """Top-down walk in the order of ``os.walk`` with sorted names (files of a directory, then its subdirectories),
    yielding ``(directory, name, is_symlink)`` for every non-directory entry. Unlike ``os.walk`` it never stats a
    symlink target (``os.walk`` does, to classify the entry), so nothing outside ``base`` is ever touched; a symlink
    (to a file or a directory) is yielded as an entry, never entered."""
    try:
        with os.scandir(base) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return
    subdirs = []
    for e in entries:
        if e.is_symlink():
            yield base, e.name, True
        elif e.is_dir(follow_symlinks=False):
            if not skip_dir(e.name):
                subdirs.append(e.path)
        else:
            yield base, e.name, False
    for d in subdirs:
        yield from walk_no_follow(d, skip_dir)


def _skip_selected_dir(name: str) -> bool:
    return name in EXCLUDE_DIR_NAMES or name.startswith("evidence")


def selected(src: str, missing: list[str] | None = None) -> Iterator[tuple[str, str]]:
    """(relpath, role group) of every file the allowlist selects in ``src`` (same order as the original tool, which
    used ``os.walk`` with sorted names; symlinks are yielded and then skipped by the copy, as before)."""
    for rel, role in INCLUDE_FILES.items():
        if os.path.isfile(os.path.join(src, rel)):
            yield rel, role
        elif missing is not None:
            missing.append(rel)
    for tree, role in INCLUDE_TREES.items():
        base = os.path.join(src, tree)
        if os.path.islink(base) or not os.path.isdir(base):
            if missing is not None:
                missing.append(tree + "/")
            continue
        for root, f, _link in walk_no_follow(base, _skip_selected_dir):
            rel = os.path.relpath(os.path.join(root, f), src)
            if any(fnmatch.fnmatch(f, g) for g in EXCLUDE_FILE_GLOBS) and not rel.startswith(ALLOW_IMAGE_UNDER):
                continue
            yield rel, role


def git_state(src: str) -> dict:
    """HEAD / branch / origin/main, HEAD and index blob maps and the porcelain status entries (all read-only)."""
    head_blobs: dict[str, str] = {}
    for line in z_entries(git(src, "ls-tree", "-r", "-z", "HEAD")):
        meta, path = line.split("\t", 1)
        head_blobs[path] = meta.split()[2]
    index_blobs: dict[str, str] = {}
    for line in z_entries(git(src, "ls-files", "-s", "-z")):
        meta, path = line.split("\t", 1)
        index_blobs[path] = meta.split()[1]
    try:
        origin_main = git(src, "rev-parse", "origin/main").strip()
    except ToolError:
        origin_main = None
    return {"head": git(src, "rev-parse", "HEAD").strip(), "branch": git(src, "rev-parse", "--abbrev-ref", "HEAD").strip(),
            "origin_main": origin_main, "head_blobs": head_blobs, "index_blobs": index_blobs,
            "status": list(z_entries(git(src, "status", "--porcelain", "-z")))}


def utc_iso(ts: float | None = None) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) if ts is None else datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return dt.isoformat()


def file_record(src: str, rel: str, role: str, data: bytes, st: os.stat_result, state: dict, snapshot_dir: str,
                target_root: str) -> dict:
    b = blob_sha1(data)
    hb, ib = state["head_blobs"].get(rel), state["index_blobs"].get(rel)
    return {"source_relpath": rel, "snapshot_relpath": os.path.relpath(os.path.join(snapshot_dir, rel), target_root),
            "role_group": role, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "git_blob_sha1": b,
            "head_blob_sha1": hb, "index_blob_sha1": ib, "git_status": classify_status(b, hb, ib),
            "source_mtime": utc_iso(st.st_mtime), "copied_at": utc_iso(), "license": license_of(rel)}


def counts_of(records: list[dict]) -> dict:
    """files / bytes / by_status / by_role_group of manifest rows (the SUMMARY top-level count block)."""
    return {"files": len(records), "bytes": sum(r["bytes"] for r in records),
            "by_status": {s: sum(1 for r in records if r["git_status"] == s) for s in sorted({r["git_status"] for r in records})},
            "by_role_group": {g: sum(1 for r in records if r["role_group"] == g) for g in sorted({r["role_group"] for r in records})}}


def under(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source-root", required=True, help="the original checkout (read only)")
    ap.add_argument("--target-root", default=REPO_ROOT)
    ap.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    ap.add_argument("--provenance-dir", default=DEFAULT_PROVENANCE_DIR)
    ap.add_argument("--write", action="store_true", help="copy files and write MANIFEST/SUMMARY (default: dry run)")
    args = ap.parse_args(argv)
    src = os.path.abspath(args.source_root)
    if not os.path.isdir(os.path.join(src, ".git")) and not os.path.isfile(os.path.join(src, ".git")):
        print(f"not a git checkout: {src}", file=sys.stderr)
        return 2
    manifest_path = os.path.join(args.provenance_dir, "SOURCE_SNAPSHOT_MANIFEST.jsonl")
    summary_path = os.path.join(args.provenance_dir, "SOURCE_SNAPSHOT_SUMMARY.json")
    if args.write:
        for d in (args.snapshot_dir, args.provenance_dir):
            if under(d, src):
                print(f"refused: destination {d} is inside the source checkout", file=sys.stderr)
                return 2
        if os.path.exists(manifest_path) or (os.path.isdir(args.snapshot_dir) and os.listdir(args.snapshot_dir)):
            print("refused: the snapshot is write-once (manifest or snapshot directory already present); "
                  "use snapshot_delta.py for later changes or point --snapshot-dir/--provenance-dir at empty locations",
                  file=sys.stderr)
            return 2
    try:
        started = utc_iso()
        state = git_state(src)
        missing: list[str] = []
        records: list[dict] = []
        skipped: list[dict] = []
        seen: set[str] = set()
        for rel, role in selected(src, missing):
            if rel in seen:
                continue
            seen.add(rel)
            path = os.path.join(src, rel)
            st = os.lstat(path)
            if os.path.islink(path):
                skipped.append({"path": rel, "reason": "symlink"})
                continue
            if st.st_size > MAX_BYTES:
                skipped.append({"path": rel, "reason": f"size {st.st_size} > {MAX_BYTES}"})
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            if args.write:
                dst = os.path.join(args.snapshot_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as out:
                    out.write(data)
                shutil.copystat(path, dst)
            records.append(file_record(src, rel, role, data, st, state, args.snapshot_dir, args.target_root))
        status_after = list(z_entries(git(src, "status", "--porcelain", "-z")))
    except (ToolError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    summary = {"schema": "mbnas.source_snapshot/1", "source_root": src, "target_root": args.target_root,
               "snapshot_dir": args.snapshot_dir,
               "git": {"head": state["head"], "branch": state["branch"], "origin_main": state["origin_main"],
                       "status_entries_before": len(state["status"]), "status_entries_after": len(status_after),
                       "status_changed_during_copy": state["status"] != status_after},
               "started_at": started, "finished_at": utc_iso(), **counts_of(records), "skipped": skipped,
               "missing_includes": missing,
               "tool": {"python": sys.version.split()[0], "script": "scripts/dev/snapshot_copy.py", "flags": "-I"},
               "note": "selective allowlist copy (no whole-repo copy); source untouched; a concurrent session edits the source, "
                       "so per-file sha256 is the authority, not an atomic snapshot"}
    if args.write:
        os.makedirs(args.provenance_dir, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
    out = {k: v for k, v in summary.items() if k not in ("skipped",)}
    out["mode"] = "write" if args.write else "dry-run"
    out["skipped_count"] = len(skipped)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
