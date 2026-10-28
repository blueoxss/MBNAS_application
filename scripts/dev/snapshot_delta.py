#!/usr/bin/env python3
"""Incremental provenance re-copy of the source snapshot (plan: read-only everywhere; apply: designer only).

Provenance of this file (C01 fix round 1b, EVAL_1 D2, 2026-10-28): merges three designer session-scratchpad scripts,
kept verbatim under ``docs/internal/evidence/PROVENANCE/historical/tools/``:

* ``tools/snapshot_delta.py`` (sha256 57108374f457954a2ae11aceb48d8b0c3daa95c051210a8faea24a6a6b02ccec) - wrote
  ``SOURCE_SNAPSHOT_DELTA_20261023T220106.jsonl`` (delta 2: 11 files, contract 1.4.2);
* ``delta.py`` (sha256 94a2fc09bf9ce2d4afc861fe139ea9068e5282f71eebbd9d4ea4ffca16044fc3) - the comparison that produced
  ``delta.json`` (changed 40 + new 6) before delta 3 and ``delta2.json`` (0/0/0) right after it;
* ``apply_delta.py`` (sha256 adc19f2b6ff59cfe4752ad3a7db9bc982a1eec80e47b38d2f1932cdaf3169342) - applied ``delta.json``
  as ``SOURCE_SNAPSHOT_DELTA_20261026T194059.jsonl`` (delta 3: 46 files).

Modes:

* ``--plan`` (default) compares every manifest row with the live file (sha256) and lists files the allowlist of
  ``snapshot_copy.py`` selects that the manifest lacks: ``{"changed": [[rel, manifest_sha, live_sha, role]], "missing":
  [rel], "new": [rel], "new_docs": [rel]}`` (the format of ``delta.json`` plus ``new_docs``: new files in the owner
  documentation trees, listed but not part of a default apply). Writes nothing except ``--plan-out``.
* ``--apply PLAN --reason TEXT --write`` re-copies ``changed`` + ``new`` of PLAN (each live sha256 must still equal the
  planned one, otherwise nothing is written), writes ``SOURCE_SNAPSHOT_DELTA_<KST tag>.jsonl``, replaces / appends the
  manifest rows and appends ``deltas[]`` to the summary, recomputing its top-level counts from the new manifest. This
  changes ``build/source_snapshot`` and the provenance files: only the designer applies a delta; agents run ``--plan``.

Read-only towards the source checkout: the helpers of ``snapshot_copy.py`` (``git`` allows read-only subcommands only,
always ``--no-optional-locks``; files opened ``rb``); blob sha1 is computed in Python (the old ``git hash-object`` call
is gone). Exit: 0 ok (plan: also when differences are found; see the counts), 2 usage / refused, 3 git / I/O failure.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_copy_module():
    spec = importlib.util.spec_from_file_location("mbnas_dev_snapshot_copy", os.path.join(_HERE, "snapshot_copy.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


sc = _load_copy_module()
# code trees whose new files belong to a delta by default (the six directories of the historical delta.py plus the
# remaining code-like trees of the allowlist); new files in the documentation trees are listed as new_docs
DOC_ROLES = {"server-docs", "handoff-docs(owner)", "application-docs(owner)"}
KST = datetime.timezone(datetime.timedelta(hours=9))


def load_manifest(provenance_dir: str) -> list[dict]:
    with open(os.path.join(provenance_dir, "SOURCE_SNAPSHOT_MANIFEST.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(src: str, rows: list[dict]) -> dict:
    """The read-only comparison of the historical ``delta.py`` (sha256 of every manifest row vs the live file)."""
    known = {r["source_relpath"] for r in rows}
    changed, missing = [], []
    for r in rows:
        p = os.path.join(src, r["source_relpath"])
        if not os.path.isfile(p):
            missing.append(r["source_relpath"])
            continue
        h = sha256_file(p)
        if h != r["sha256"]:
            changed.append([r["source_relpath"], r["sha256"], h, r["role_group"]])
    new, new_docs = [], []
    for rel, role in sc.selected(src):
        if rel in known or os.path.islink(os.path.join(src, rel)):
            continue
        (new_docs if role in DOC_ROLES else new).append(rel)
    return {"changed": changed, "missing": missing, "new": sorted(set(new)), "new_docs": sorted(set(new_docs))}


def apply(src: str, plan_doc: dict, reason: str, snapshot_dir: str, provenance_dir: str, target_root: str) -> dict:
    rows = load_manifest(provenance_dir)
    idx = {r["source_relpath"]: i for i, r in enumerate(rows)}
    items = [(c[0], c[1], c[2]) for c in plan_doc.get("changed", [])] + [(n, None, None) for n in plan_doc.get("new", [])]
    if not items:
        raise SystemExit("empty plan: nothing to apply")
    payload = []
    for rel, sup, want in items:  # read and verify everything before writing anything
        path = os.path.join(src, rel)
        st = os.lstat(path)
        if os.path.islink(path) or not os.path.isfile(path):
            raise SystemExit(f"refused: {rel} is not a regular file")
        with open(path, "rb") as fh:
            data = fh.read()
        h = hashlib.sha256(data).hexdigest()
        if want is not None and h != want:
            raise SystemExit(f"refused: {rel} changed since the plan (live {h[:12]}, planned {want[:12]}); plan again")
        if rel in idx and sup is not None and rows[idx[rel]]["sha256"] != sup:
            raise SystemExit(f"refused: manifest row of {rel} is not the planned base {sup[:12]}")
        payload.append((rel, sup, data, st))
    state = sc.git_state(src)
    previous = [r.get("delta") for r in rows if isinstance(r.get("delta"), int)]
    number = max(previous) + 1 if previous else 2  # delta numbers continue the historical 2, 3
    now = datetime.datetime.now(datetime.timezone.utc)
    tag = now.astimezone(KST).strftime("%Y%m%dT%H%M%S")
    out = []
    for rel, sup, data, st in payload:
        role = rows[idx[rel]]["role_group"] if rel in idx else sc.role_of(rel)
        if role is None:
            raise SystemExit(f"refused: {rel} is outside the snapshot allowlist")
        row = sc.file_record(src, rel, role, data, st, state, snapshot_dir, target_root)
        row.update({"copied_at": now.isoformat(), "delta": number, "supersedes_sha256": sup})
        out.append(row)
    for row, (rel, _sup, data, st) in zip(out, payload):
        dst = os.path.join(snapshot_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        shutil.copystat(os.path.join(src, rel), dst)
        if sha256_file(dst) != row["sha256"]:
            raise SystemExit(f"copy verification failed for {rel}")
        if rel in idx:
            rows[idx[rel]] = row
        else:
            idx[rel] = len(rows)
            rows.append(row)
    with open(os.path.join(provenance_dir, f"SOURCE_SNAPSHOT_DELTA_{tag}.jsonl"), "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with open(os.path.join(provenance_dir, "SOURCE_SNAPSHOT_MANIFEST.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    spath = os.path.join(provenance_dir, "SOURCE_SNAPSHOT_SUMMARY.json")
    with open(spath, encoding="utf-8") as fh:
        summary = json.load(fh)
    summary.setdefault("deltas", []).append({"tag": tag, "at": now.isoformat(), "head": state["head"],
                                             "files": [r["source_relpath"] for r in out], "reason": reason})
    summary.update(sc.counts_of(rows))  # top-level counts = the current manifest (initial values: initial_copy)
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    return {"delta_tag": tag, "delta": number, "head": state["head"], "files": [r["source_relpath"] for r in out],
            "manifest_rows": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source-root", required=True, help="the original checkout (read only)")
    ap.add_argument("--target-root", default=sc.REPO_ROOT)
    ap.add_argument("--snapshot-dir", default=sc.DEFAULT_SNAPSHOT_DIR)
    ap.add_argument("--provenance-dir", default=sc.DEFAULT_PROVENANCE_DIR)
    ap.add_argument("--plan-out", help="also write the plan JSON here (never inside the source checkout)")
    ap.add_argument("--apply", metavar="PLAN", help="apply a plan written by --plan-out (designer only)")
    ap.add_argument("--reason", help="reason recorded in SUMMARY deltas[] (required with --apply)")
    ap.add_argument("--write", action="store_true", help="required with --apply")
    args = ap.parse_args(argv)
    src = os.path.abspath(args.source_root)
    for d in filter(None, (args.plan_out, args.snapshot_dir if args.apply else None, args.provenance_dir if args.apply else None)):
        if sc.under(d, src):
            print(f"refused: {d} is inside the source checkout", file=sys.stderr)
            return 2
    try:
        if args.apply:
            if not (args.write and args.reason):
                print("refused: --apply needs --write and --reason", file=sys.stderr)
                return 2
            with open(args.apply, encoding="utf-8") as fh:
                plan_doc = json.load(fh)
            try:
                result = apply(src, plan_doc, args.reason, args.snapshot_dir, args.provenance_dir, args.target_root)
            except SystemExit as exc:  # refusals of apply(): all but a copy-verification failure (which aborts mid-copy)
                # happen before anything is written (stale plan, base mismatch, not a regular file, outside the allowlist)
                if isinstance(exc.code, str):
                    print(exc.code, file=sys.stderr)
                    return 2
                raise
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0
        doc = plan(src, load_manifest(args.provenance_dir))
    except (sc.ToolError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.plan_out:
        with open(args.plan_out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
    print(len(doc["changed"]), len(doc["missing"]), len(doc["new"]), len(doc["new_docs"]))
    for c in doc["changed"]:
        print("CH", c[0])
    for n in doc["new"]:
        print("NEW", n)
    for n in doc["new_docs"]:
        print("NEW_DOC", n)
    for m in doc["missing"]:
        print("MISS", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
