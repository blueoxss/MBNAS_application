"""Selective provenance copy of the etri_nas inputs into TARGET_ROOT/build/source_snapshot.

Read-only on the source: git is only queried with --no-optional-locks / plumbing reads. Per-file record:
source relpath, bytes, sha256, git blob sha1, HEAD blob, index blob, status, mtime, copied_at.
"""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, datetime, fnmatch
SRC = "/home/mgjeong/Desktop/etri_hd/etri_nas"
TGT = "/home/mgjeong/Desktop/etri_hd/mbnas_application"
SNAP = os.path.join(TGT, "build", "source_snapshot")
PROV = os.path.join(TGT, "docs", "internal", "provenance")
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
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "dist", "test-results", ".vitest", "evidence", "evidence_I1", "evidence_I3", "evidence_I4", "evidence_impl", "live_harness_out"}
EXCLUDE_FILE_GLOBS = ["*.pyc", "*.pyo", ".env", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.pt", "*.pth", "*.onnx", "*.engine", "*.sqlite3", "*.db", "*.zip", "*.tar", "*.gz"]
ALLOW_IMAGE_UNDER = ("application/MBNAS_web/public",)

def git(*args: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", "-C", SRC, *args], check=True, capture_output=True).stdout.decode("utf-8", "replace")

def z_pairs(out: str):
    for entry in out.split("\0"):
        if entry:
            yield entry

def blob_sha1(data: bytes) -> str:
    h = hashlib.sha1(); h.update(b"blob %d\0" % len(data)); h.update(data); return h.hexdigest()

def selected():
    for rel, role in INCLUDE_FILES.items():
        p = os.path.join(SRC, rel)
        if os.path.isfile(p):
            yield rel, role
        else:
            print("MISSING include file:", rel, file=sys.stderr)
    for tree, role in INCLUDE_TREES.items():
        base = os.path.join(SRC, tree)
        if not os.path.isdir(base):
            print("MISSING include tree:", tree, file=sys.stderr); continue
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIR_NAMES and not d.startswith("evidence"))
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), SRC)
                if any(fnmatch.fnmatch(f, g) for g in EXCLUDE_FILE_GLOBS) and not rel.startswith(ALLOW_IMAGE_UNDER):
                    continue
                yield rel, role

def main() -> int:
    os.makedirs(SNAP, exist_ok=True); os.makedirs(PROV, exist_ok=True)
    head = git("rev-parse", "HEAD").strip(); branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    origin_main = git("rev-parse", "origin/main").strip()
    status_before = list(z_pairs(git("status", "--porcelain", "-z")))
    head_blobs = {}
    for line in z_pairs(git("ls-tree", "-r", "-z", "HEAD")):
        meta, path = line.split("\t", 1); head_blobs[path] = meta.split()[2]
    index_blobs = {}
    for line in z_pairs(git("ls-files", "-s", "-z")):
        meta, path = line.split("\t", 1); index_blobs[path] = meta.split()[1]
    records = []; skipped = []; seen = set()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for rel, role in selected():
        if rel in seen: continue
        seen.add(rel)
        src = os.path.join(SRC, rel); dst = os.path.join(SNAP, rel)
        st = os.lstat(src)
        if os.path.islink(src):
            skipped.append({"path": rel, "reason": "symlink"}); continue
        if st.st_size > MAX_BYTES:
            skipped.append({"path": rel, "reason": f"size {st.st_size} > {MAX_BYTES}"}); continue
        with open(src, "rb") as fh: data = fh.read()
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as out: out.write(data)
        shutil.copystat(src, dst)
        b = blob_sha1(data); hb = head_blobs.get(rel); ib = index_blobs.get(rel)
        if hb is None and ib is None: status = "untracked"
        elif b == hb: status = "tracked_clean"
        elif ib is not None and b == ib and ib != hb: status = "tracked_staged_modified"
        else: status = "tracked_modified"
        records.append({"source_relpath": rel, "snapshot_relpath": os.path.relpath(dst, TGT), "role_group": role,
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "git_blob_sha1": b,
                        "head_blob_sha1": hb, "index_blob_sha1": ib, "git_status": status,
                        "source_mtime": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(),
                        "copied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "license": ("no LICENSE file in etri_nas root; ETRI_NAS research code, rights review pending" if rel.startswith(("yolo_ga", "application")) else "paper manuscript; owner reference only")})
    status_after = list(z_pairs(git("status", "--porcelain", "-z")))
    finished = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(os.path.join(PROV, "SOURCE_SNAPSHOT_MANIFEST.jsonl"), "w", encoding="utf-8") as fh:
        for r in records: fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {"schema": "mbnas.source_snapshot/1", "source_root": SRC, "target_root": TGT, "snapshot_dir": SNAP,
               "git": {"head": head, "branch": branch, "origin_main": origin_main,
                        "status_entries_before": len(status_before), "status_entries_after": len(status_after),
                        "status_changed_during_copy": status_before != status_after},
               "started_at": started, "finished_at": finished, "files": len(records),
               "bytes": sum(r["bytes"] for r in records),
               "by_status": {s: sum(1 for r in records if r["git_status"] == s) for s in sorted({r["git_status"] for r in records})},
               "by_role_group": {g: sum(1 for r in records if r["role_group"] == g) for g in sorted({r["role_group"] for r in records})},
               "skipped": skipped, "tool": {"python": sys.version.split()[0], "script": "snapshot_copy.py", "flags": "-I"},
               "note": "selective allowlist copy (no whole-repo copy); source untouched; a concurrent session edits the source, so per-file sha256 is the authority, not an atomic snapshot"}
    with open(os.path.join(PROV, "SOURCE_SNAPSHOT_SUMMARY.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("skipped",)}, ensure_ascii=False, indent=1))
    print("skipped:", len(skipped)); [print("  ", s) for s in skipped[:20]]
    return 0

if __name__ == "__main__":
    sys.exit(main())
