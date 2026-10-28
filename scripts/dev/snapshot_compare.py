#!/usr/bin/env python3
"""Provenance comparison of the source snapshot: manifest vs snapshot copy, vs the live original, delta chain, summary.

Provenance of this file (C01 fix round 1b, EVAL_1 D2, 2026-10-28): generalises the designer's session-scratchpad
comparison ``delta.py`` (sha256 94a2fc09bf9ce2d4afc861fe139ea9068e5282f71eebbd9d4ea4ffca16044fc3; its outputs
``delta.json`` / ``delta2.json`` of 2026-10-26 19:40/19:41 KST are kept under
``docs/internal/evidence/PROVENANCE/historical/``) and the independent evaluator's re-run checks of
``docs/internal/evidence/CRITERIA/C01/EVAL_1.md`` (#9 snapshot integrity, #10 delta chain, #11 live comparison).

Checks (all counts; the JSON report lists the offending paths):

* ``snapshot``: every ``SOURCE_SNAPSHOT_MANIFEST.jsonl`` row against its copy under ``build/source_snapshot``
  (sha256 + bytes), files in the snapshot directory that no row names, ``.pyc`` files and ``__pycache__`` directories;
* ``live`` (with ``--source-root``): every row against the live file of the original checkout (sha256 match / differ /
  missing), the row's git fields (``head_blob_sha1``, ``index_blob_sha1``, ``git_status``, ``git_blob_sha1``) against
  the current git state, files the snapshot allowlist (``snapshot_copy.py``) selects that no row names (``new`` in the
  code trees, ``new_docs`` in the owner documentation trees), HEAD / branch / origin/main, commits since the snapshot
  HEAD, porcelain status entries, the newest mtime of the selected files and the number modified after ``--since``
  (default: the time of the last delta); with ``--mtime-scan`` also every regular file of the checkout outside
  ``.git``/``node_modules``/``.venv``/``__pycache__``/``.pytest_cache``;
* ``delta_chain``: the ``SOURCE_SNAPSHOT_DELTA_*.jsonl`` files in tag order: the last delta row of a path equals its
  manifest row, ``supersedes_sha256`` of a later delta row equals the sha256 of the earlier row of the same path, and
  each summary ``deltas[]`` entry names exactly the paths of its delta file;
* ``summary``: the top-level counts of ``SOURCE_SNAPSHOT_SUMMARY.json`` (``files``, ``bytes``, ``by_status``,
  ``by_role_group``) equal the counts recomputed from the manifest.

Read-only everywhere: the helpers of ``snapshot_copy.py`` (git with ``--no-optional-locks`` and read-only
subcommands only; files opened ``rb``; directory walks never follow a symlink, so nothing outside the checkout is
touched). As evidence the report records mtime and size of ``<source>/.git/index`` and ``.git/HEAD`` before and after
the run. The only file written is ``--json-out`` (refused inside the source checkout).

Exit: 0 = every check consistent (live: 0 differ, 0 missing, 0 new code files); 1 = a difference (see the counts;
a live difference is not by itself a defect when the concurrent session changed the original); 2 = usage; 3 = git/I/O.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_copy_module():
    spec = importlib.util.spec_from_file_location("mbnas_dev_snapshot_copy", os.path.join(_HERE, "snapshot_copy.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


sc = _load_copy_module()
SCHEMA = "mbnas.snapshot_compare/1"
DOC_ROLES = {"server-docs", "handoff-docs(owner)", "application-docs(owner)"}
SCAN_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
RE_DELTA = re.compile(r"^SOURCE_SNAPSHOT_DELTA_(\d{8}T\d{6})\.jsonl$")
GIT_FIELDS = ("head_blob_sha1", "index_blob_sha1", "git_status", "git_blob_sha1")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def parse_time(value: str) -> float:
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))  # KST like the rest of the project
    return dt.timestamp()


def iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def stat_marker(path: str) -> dict | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


# ---------------------------------------------------------------------------------------------------------- snapshot
def check_snapshot(rows: list[dict], target_root: str, snapshot_dir: str) -> dict:
    match, differ, missing = 0, [], []
    named = set()
    for r in rows:
        path = os.path.join(target_root, r["snapshot_relpath"])
        named.add(os.path.normpath(path))
        if not os.path.isfile(path):
            missing.append(r["snapshot_relpath"])
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) == r["sha256"] and len(data) == r["bytes"]:
            match += 1
        else:
            differ.append(r["snapshot_relpath"])
    extra, pyc, pycache_dirs = [], 0, []
    if os.path.isdir(snapshot_dir):
        def note_dir(name: str) -> bool:  # counts __pycache__ directories, skips none
            if name == "__pycache__":
                pycache_dirs.append(name)
            return False

        for root, name, _link in sc.walk_no_follow(snapshot_dir, note_dir):
            path = os.path.normpath(os.path.join(root, name))
            if name.endswith((".pyc", ".pyo")):
                pyc += 1
            if path not in named:
                extra.append(os.path.relpath(path, target_root))
    pycache = len(pycache_dirs)
    return {"rows": len(rows), "match": match, "differ": len(differ), "missing": len(missing), "extra_files": len(extra),
            "pyc_files": pyc, "pycache_dirs": pycache, "differ_paths": differ, "missing_paths": missing, "extra_paths": extra[:200],
            "ok": match == len(rows) and not extra and pyc == 0 and pycache == 0}


# -------------------------------------------------------------------------------------------------------------- live
def check_live(rows: list[dict], src: str, summary: dict, since: float | None, mtime_scan: bool) -> dict:
    markers_before = {n: stat_marker(os.path.join(src, ".git", n)) for n in ("index", "HEAD")}
    state = sc.git_state(src)
    match, differ, missing = 0, [], []
    field_mismatch: dict[str, list[str]] = {f: [] for f in GIT_FIELDS}
    by_status = {}
    for r in rows:
        rel = r["source_relpath"]
        path = os.path.join(src, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            missing.append(rel)
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) == r["sha256"]:
            match += 1
        else:
            differ.append(rel)
        blob = sc.blob_sha1(data)
        hb, ib = state["head_blobs"].get(rel), state["index_blobs"].get(rel)
        live = {"head_blob_sha1": hb, "index_blob_sha1": ib, "git_status": sc.classify_status(blob, hb, ib), "git_blob_sha1": blob}
        by_status[live["git_status"]] = by_status.get(live["git_status"], 0) + 1
        for f in GIT_FIELDS:
            if r.get(f) != live[f]:
                field_mismatch[f].append(rel)
    known = {r["source_relpath"] for r in rows}
    new, new_docs, sel_files, newest, newest_path, after_since = [], [], 0, 0.0, None, []
    missing_includes: list[str] = []
    for rel, role in sc.selected(src, missing_includes):
        path = os.path.join(src, rel)
        st = os.lstat(path)
        sel_files += 1
        if st.st_mtime > newest:
            newest, newest_path = st.st_mtime, rel
        if since is not None and st.st_mtime > since:
            after_since.append(rel)
        if rel not in known and not os.path.islink(path):
            (new_docs if role in DOC_ROLES else new).append(rel)
    missing_set = set(missing)
    mismatch_sets = {f: set(v) for f, v in field_mismatch.items()}
    snap_heads = sorted({summary.get("git", {}).get("head")} | {d.get("head") for d in summary.get("deltas", [])} - {None})
    since_head = {}
    for h in snap_heads:
        try:
            since_head[h] = int(sc.git(src, "rev-list", "--count", f"{h}..HEAD").strip())
        except sc.ToolError as exc:
            since_head[h] = f"error: {exc}"[:200]
    head_in_tree = sum(1 for r in rows if r["source_relpath"] in state["head_blobs"])
    result = {
        "git": {"head": state["head"], "branch": state["branch"], "origin_main": state["origin_main"],
                "snapshot_heads": snap_heads, "commits_since_snapshot_head": since_head,
                "head_equals_snapshot_head": state["head"] in snap_heads,
                "status_entries_now": len(state["status"]),
                "status_entries_at_first_copy": summary.get("git", {}).get("status_entries_before")},
        "rows": len(rows), "sha256_match": match, "sha256_differ": len(differ), "missing_live": len(missing),
        "git_fields_all_match": sum(1 for r in rows if r["source_relpath"] not in missing_set and
                                    all(r["source_relpath"] not in mismatch_sets[f] for f in GIT_FIELDS)),
        "git_field_mismatch": {f: len(v) for f, v in field_mismatch.items()},
        "live_status_of_rows": dict(sorted(by_status.items())), "rows_present_in_head_tree": head_in_tree,
        "selected_live_files": sel_files, "new_live_code_files": len(new), "new_live_doc_files": len(new_docs),
        "missing_include_entries": missing_includes,
        "newest_selected_mtime": iso(newest) if newest_path else None, "newest_selected_path": newest_path,
        "since": iso(since) if since is not None else None,
        "selected_modified_after_since": len(after_since),
        "differ_paths": differ, "missing_paths": missing, "new_code_paths": new, "new_doc_paths": new_docs[:500],
        "selected_modified_after_since_paths": after_since[:200],
        "git_field_mismatch_paths": {f: v[:50] for f, v in field_mismatch.items() if v},
    }
    if mtime_scan:
        result["mtime_scan"] = scan_mtimes(src, summary, since)
    markers_after = {n: stat_marker(os.path.join(src, ".git", n)) for n in ("index", "HEAD")}
    result["git_dir_markers"] = {"before": markers_before, "after": markers_after, "unchanged": markers_before == markers_after}
    result["ok"] = not differ and not missing and not new
    return result


def scan_mtimes(src: str, summary: dict, since: float | None) -> dict:
    """Every regular file of the checkout (no symlink followed): files modified after the first copy and after ``since``."""
    first = parse_time(summary["started_at"]) if summary.get("started_at") else None
    total, after_first, after_since, newest, newest_path, links = 0, 0, 0, 0.0, None, 0
    by_top: dict[str, int] = {}
    for root, name, is_link in sc.walk_no_follow(src, lambda n: n in SCAN_SKIP_DIRS):
        if is_link:
            links += 1
            continue
        path = os.path.join(root, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        total += 1
        if st.st_mtime > newest:
            newest, newest_path = st.st_mtime, os.path.relpath(path, src)
        if first is not None and st.st_mtime > first:
            after_first += 1
            top = os.path.relpath(path, src).split(os.sep)[0]
            by_top[top] = by_top.get(top, 0) + 1
        if since is not None and st.st_mtime > since:
            after_since += 1
    return {"files": total, "symlinks_not_followed": links, "first_copy_started_at": summary.get("started_at"),
            "modified_after_first_copy": after_first, "modified_after_first_copy_by_top_dir": dict(sorted(by_top.items())),
            "modified_after_since": after_since, "newest_mtime": iso(newest) if newest_path else None,
            "newest_path": newest_path, "skipped_dir_names": sorted(SCAN_SKIP_DIRS)}


# ------------------------------------------------------------------------------------------------------- delta chain
def check_delta_chain(rows: list[dict], provenance_dir: str, summary: dict) -> dict:
    files = sorted(n for n in os.listdir(provenance_dir) if RE_DELTA.match(n))
    per_path: dict[str, list[tuple[str, dict]]] = {}
    tags = {}
    for name in files:
        tag = RE_DELTA.match(name).group(1)
        drows = read_jsonl(os.path.join(provenance_dir, name))
        tags[tag] = [r["source_relpath"] for r in drows]
        for r in drows:
            per_path.setdefault(r["source_relpath"], []).append((tag, r))
    manifest = {r["source_relpath"]: r for r in rows}
    final_ok, final_bad, chain_ok, chain_bad, new_rows = 0, [], 0, [], 0
    for rel, seq in per_path.items():
        last = seq[-1][1]
        if rel in manifest and manifest[rel] == last:
            final_ok += 1
        else:
            final_bad.append(rel)
        for (_t0, a), (_t1, b) in zip(seq, seq[1:]):
            if b.get("supersedes_sha256") == a["sha256"]:
                chain_ok += 1
            else:
                chain_bad.append(rel)
        new_rows += sum(1 for _t, r in seq if r.get("supersedes_sha256") is None)
    summary_tags = {d["tag"]: d.get("files", []) for d in summary.get("deltas", [])}
    listing_bad = [t for t in set(tags) | set(summary_tags) if sorted(tags.get(t, [])) != sorted(summary_tags.get(t, []))]
    superseded_by_later = sum(len(seq) - 1 for seq in per_path.values())
    return {"delta_files": files, "delta_rows": sum(len(v) for v in tags.values()), "paths": len(per_path),
            "last_row_equals_manifest": final_ok, "last_row_differs": len(final_bad),
            "supersedes_links_ok": chain_ok, "supersedes_links_bad": len(chain_bad),
            "rows_superseded_by_a_later_delta": superseded_by_later, "rows_without_supersedes(new files)": new_rows,
            "summary_deltas_listing_mismatch": sorted(listing_bad), "differ_paths": final_bad, "bad_link_paths": chain_bad,
            "ok": not final_bad and not chain_bad and not listing_bad}


def check_summary(rows: list[dict], summary: dict) -> dict:
    want = sc.counts_of(rows)
    have = {k: summary.get(k) for k in want}
    diffs = {k: {"summary": have[k], "manifest": want[k]} for k in want if have[k] != want[k]}
    return {"manifest_counts": want, "summary_counts": have, "differences": diffs, "ok": not diffs}


# ---------------------------------------------------------------------------------------------------------------- main
def run(target_root: str, snapshot_dir: str, provenance_dir: str, source_root: str | None = None, since: str | None = None,
        mtime_scan: bool = False) -> dict:
    t0 = time.monotonic()
    mpath = os.path.join(provenance_dir, "SOURCE_SNAPSHOT_MANIFEST.jsonl")
    spath = os.path.join(provenance_dir, "SOURCE_SNAPSHOT_SUMMARY.json")
    rows = read_jsonl(mpath)
    with open(spath, encoding="utf-8") as fh:
        summary = json.load(fh)
    inputs = {os.path.relpath(p, target_root): sha256_file(p) for p in
              [mpath, spath] + [os.path.join(provenance_dir, n) for n in sorted(os.listdir(provenance_dir)) if RE_DELTA.match(n)]}
    if since is None and summary.get("deltas"):
        since_ts = max(parse_time(d["at"]) for d in summary["deltas"])
    else:
        since_ts = parse_time(since) if since else None
    report = {"schema": SCHEMA, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "tool": {"python": sys.version.split()[0], "script": "scripts/dev/snapshot_compare.py",
                       "sha256": sha256_file(os.path.abspath(__file__)),
                       "snapshot_copy_sha256": sha256_file(os.path.join(_HERE, "snapshot_copy.py"))},
              "inputs_sha256": inputs, "manifest_rows": len(rows),
              "snapshot": check_snapshot(rows, target_root, snapshot_dir),
              "delta_chain": check_delta_chain(rows, provenance_dir, summary),
              "summary": check_summary(rows, summary)}
    if source_root:
        report["source_root"] = os.path.abspath(source_root)
        report["live"] = check_live(rows, os.path.abspath(source_root), summary, since_ts, mtime_scan)
    report["ok"] = all(report[k]["ok"] for k in ("snapshot", "delta_chain", "summary", "live") if k in report)
    report["elapsed_s"] = round(time.monotonic() - t0, 2)
    return report


def text_summary(rep: dict) -> str:
    s, d, m = rep["snapshot"], rep["delta_chain"], rep["summary"]
    out = [f"manifest rows {rep['manifest_rows']}",
           f"snapshot copy: match {s['match']}, differ {s['differ']}, missing {s['missing']}, extra {s['extra_files']}, "
           f"pyc {s['pyc_files']}, __pycache__ {s['pycache_dirs']}",
           f"delta chain: files {len(d['delta_files'])}, rows {d['delta_rows']}, last row == manifest {d['last_row_equals_manifest']}/"
           f"{d['paths']}, supersedes links ok {d['supersedes_links_ok']} bad {d['supersedes_links_bad']}, "
           f"summary listing mismatch {len(d['summary_deltas_listing_mismatch'])}",
           f"summary counts == manifest: {m['ok']}" + ("" if m["ok"] else f" {json.dumps(m['differences'])}")]
    if "live" in rep:
        lv = rep["live"]
        g = lv["git"]
        out += [f"live: HEAD {g['head'][:12]} (== snapshot HEAD: {g['head_equals_snapshot_head']}), commits since snapshot "
                f"{g['commits_since_snapshot_head']}, branch {g['branch']}, status entries {g['status_entries_now']}",
                f"live sha256: match {lv['sha256_match']}, differ {lv['sha256_differ']}, missing {lv['missing_live']} "
                f"(of {lv['rows']}); git fields all match {lv['git_fields_all_match']}; mismatch {lv['git_field_mismatch']}",
                f"live status of rows {lv['live_status_of_rows']}; rows in HEAD tree {lv['rows_present_in_head_tree']}",
                f"selected live files {lv['selected_live_files']}: new code {lv['new_live_code_files']}, new docs "
                f"{lv['new_live_doc_files']}; newest selected mtime {lv['newest_selected_mtime']}; modified after "
                f"{lv['since']}: {lv['selected_modified_after_since']}",
                f".git/index+HEAD unchanged during the run: {lv['git_dir_markers']['unchanged']}"]
        if "mtime_scan" in lv:
            ms = lv["mtime_scan"]
            out.append(f"mtime scan: {ms['files']} files, after first copy {ms['modified_after_first_copy']} "
                       f"{ms['modified_after_first_copy_by_top_dir']}, after {lv['since']}: {ms['modified_after_since']}, "
                       f"newest {ms['newest_mtime']} ({ms['newest_path']}), symlinks not followed {ms['symlinks_not_followed']}")
    out.append(f"ok {rep['ok']} ({rep['elapsed_s']} s)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source-root", help="the original checkout (read only); omitted: snapshot/delta/summary checks only")
    ap.add_argument("--target-root", default=sc.REPO_ROOT)
    ap.add_argument("--snapshot-dir", default=sc.DEFAULT_SNAPSHOT_DIR)
    ap.add_argument("--provenance-dir", default=sc.DEFAULT_PROVENANCE_DIR)
    ap.add_argument("--since", help="ISO time for the 'modified after' counts (default: the last delta's time)")
    ap.add_argument("--mtime-scan", action="store_true", help="also scan every file of the source checkout (lstat only)")
    ap.add_argument("--json-out", help="write the full JSON report here")
    args = ap.parse_args(argv)
    if args.source_root and not os.path.isdir(args.source_root):
        print(f"no such directory: {args.source_root}", file=sys.stderr)
        return 2
    if args.json_out and args.source_root and sc.under(os.path.abspath(args.json_out), args.source_root):
        print("refused: --json-out inside the source checkout", file=sys.stderr)
        return 2
    try:
        rep = run(args.target_root, args.snapshot_dir, args.provenance_dir, args.source_root, args.since, args.mtime_scan)
    except (sc.ToolError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc!r}", file=sys.stderr)
        return 3
    rep["command"] = ["python", "-I", "scripts/dev/snapshot_compare.py", *(argv if argv is not None else sys.argv[1:])]
    rep["exit_code"] = 0 if rep["ok"] else 1
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print(text_summary(rep))
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
