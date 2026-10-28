#!/usr/bin/env python3
"""WP-5C real-process check of ``artifact_transfer=local`` (scratch wrapper, not a repository script).

Runs the unchanged WP-2 two-process smoke (``scripts/dev/wp2_two_process_smoke.py``: core app under uvicorn + owner
worker + ``python -m mbnas_runner run`` as real processes, a REAL ultralytics CPU training of yolo11n 1 epoch 64 px with
native trainer plots, then a SYNTHETIC stub-handler runner with evaluation) with two changes: every runner process gets
``MBNAS_RUNNER_ARTIFACT_TRANSFER=local`` and ``MBNAS_RUNNER_ARTIFACT_STORE_DIR=<smoke root>/gateway/artifacts``, and the
core gets ``MBNAS_CORE_EXPOSE_LEGACY_API=true`` (WP-3A switched the core's own ``/api/v1`` off by default; the WP-2
smoke drives that API directly).
Afterwards it verifies, from the core DB (read-only) and the store directory, that every checkpoint and artifact the
core registered is in the store under its sha256 (bytes re-hashed here) with its DD-A39 meta, and that each committed
runner result carries ``outputs.artifact_transfer`` with ok=true. CPU only (CUDA_VISIBLE_DEVICES=''), ephemeral ports.

usage: runtime/venv/bin/python -B <this file> --root <dir> --evidence <smoke evidence file> [--verify-out <file>]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
META_KEYS = {"run_id", "artifact_id", "relpath", "kind", "bytes", "mime", "registered_by", "job_id", "attempt",
             "received_at", "source"}


def load_smoke():
    spec = importlib.util.spec_from_file_location("wp2_two_process_smoke", ROOT / "scripts" / "dev" /
                                                  "wp2_two_process_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(root: Path) -> tuple[bool, list[str]]:
    store = root / "gateway" / "artifacts"
    lines: list[str] = []
    ok = True
    conn = sqlite3.connect(f"file:{root / 'core' / 'db' / 'mbnas.sqlite3'}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        cks = [dict(r) for r in conn.execute("SELECT id, run_id, kind, sha256, bytes FROM checkpoints")]
        arts = [dict(r) for r in conn.execute("SELECT id, run_id, kind, relpath, sha256, bytes FROM artifacts")]
        jobs = [dict(r) for r in conn.execute("SELECT id, run_id, kind, status, lease_owner, result_json FROM jobs "
                                              "WHERE kind != 'PROPOSE_GENERATION'")]
        locs = [dict(r) for r in conn.execute("SELECT object_kind, object_id, runner_id, relpath, uri FROM "
                                              "artifact_locations")]
    finally:
        conn.close()
    lines.append(f"core rows: {len(cks)} checkpoints, {len(arts)} artifacts, {len(jobs)} runner jobs, "
                 f"{len(locs)} artifact_locations")
    uri = {(loc["object_kind"], loc["object_id"]): loc for loc in locs if loc.get("runner_id")}
    for obj, rows in (("checkpoint", cks), ("artifact", arts)):
        for row in rows:
            if (obj, row["id"]) not in uri:  # registered by the core itself, not by a runner: not a transfer subject
                lines.append(f"SKIP {obj:10s} {row['id']:28s} kind={row['kind']} (no runner artifact_location)")
                continue
            sha = str(row["sha256"]).removeprefix("sha256:")
            path = store / sha[:2] / sha
            meta_path = store / sha[:2] / f"{sha}.json"
            present = path.is_file()
            good = present and sha256(path) == sha and path.stat().st_size == int(row["bytes"])
            meta = json.loads(meta_path.read_text()) if meta_path.is_file() else None
            meta_ok = bool(meta) and set(meta) == META_KEYS and meta["source"] == "local" and meta["run_id"] == row["run_id"]
            how = "-"
            loc = uri.get((obj, row["id"]))
            src = None
            if loc is not None:
                runner_dir = {"smoke-runner-1": "runner", "smoke-runner-stub": "runner_stub"}.get(loc["runner_id"])
                if runner_dir:
                    src = root / runner_dir / "work" / "runs" / row["run_id"] / loc["relpath"]
            if present and src is not None and src.is_file():
                how = "link" if os.stat(path).st_ino == os.stat(src).st_ino else "copy"
            ok = ok and good and meta_ok
            kind = row["kind"]
            lines.append(f"{'OK  ' if good and meta_ok else 'FAIL'} {obj:10s} {row['id']:28s} kind={kind:16s} "
                         f"sha={sha[:16]} bytes={row['bytes']:>9} stored={present} sha_ok={good} meta_ok={meta_ok} "
                         f"inode={how} meta.artifact_id={(meta or {}).get('artifact_id')} "
                         f"registered_by={(meta or {}).get('registered_by')}")
    for job in jobs:
        res = json.loads(job["result_json"] or "null") or {}
        at = (res.get("outputs") or {}).get("artifact_transfer")
        good = isinstance(at, dict) and at.get("mode") == "local" and at.get("ok") is True and at.get("failed") == []
        ok = ok and good and job["status"] == "SUCCEEDED"
        lines.append(f"{'OK  ' if good else 'FAIL'} job {job['id']} {job['kind']} {job['status']} "
                     f"runner={job['lease_owner']} outputs.artifact_transfer={json.dumps(at, sort_keys=True)}")
    stray = [str(p.relative_to(store)) for p in store.rglob(".*")] if store.is_dir() else []
    ok = ok and not stray
    lines.append(f"temp names left in the store: {stray}")
    objects = sorted(p.name for p in store.rglob("*") if p.is_file() and not p.name.endswith(".json")) \
        if store.is_dir() else []
    runner_rows = [r for r in cks if ("checkpoint", r["id"]) in uri] + [r for r in arts if ("artifact", r["id"]) in uri]
    lines.append(f"store objects: {len(objects)} (distinct digests registered by runners: "
                 f"{len({str(r['sha256']).removeprefix('sha256:') for r in runner_rows})}, rows: {len(runner_rows)})")
    return ok, lines


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--verify-out", default=None)
    args = p.parse_args()
    root = Path(args.root).resolve()
    smoke = load_smoke()
    orig = smoke.Smoke.runner_env

    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        env = orig(self, name, runner_id)
        env.update(MBNAS_RUNNER_ARTIFACT_TRANSFER="local",
                   MBNAS_RUNNER_ARTIFACT_STORE_DIR=str(self.root / "gateway" / "artifacts"))
        return env

    smoke.Smoke.runner_env = runner_env
    orig_core = smoke.Smoke.core_env

    def core_env(self) -> dict[str, str]:  # WP-3A turned the core's own /api/v1 off by default; the WP-2 smoke drives it
        env = orig_core(self)
        env["MBNAS_CORE_EXPOSE_LEGACY_API"] = "true"
        return env

    smoke.Smoke.core_env = core_env
    rc = smoke.main(["--root", str(root), "--evidence", args.evidence])
    ok, lines = verify(root)
    text = "\n".join([f"# WP-5C local transfer verification of smoke root {root} (smoke exit {rc})", *lines,
                      f"# verification result: {'PASS' if ok else 'FAIL'}"]) + "\n"
    print(text)
    if args.verify_out:
        Path(args.verify_out).write_text(text)
    return 0 if (rc == 0 and ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
