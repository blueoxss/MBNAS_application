#!/usr/bin/env python3
"""Request-id linkage report (T4/T5): which harness request ids (X-Request-ID sent to the gateway) appear in which
gateway-side and core-side records. Prints counts and samples only (request ids are not secrets).

  --ids GLOB...            harness *_request_ids.jsonl files
  --steps GLOB...          harness *_steps.jsonl files (error bodies relayed from the core carry request_id + location)
  --gw-file LABEL=PATH     gateway-side text records (audit.jsonl, gateway logs)
  --core-file LABEL=PATH   core-side text records (core log)
  --core-db PATH           core sqlite DB, read-only: every TEXT cell is searched
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--steps", nargs="*", default=[])
    ap.add_argument("--gw-file", action="append", default=[])
    ap.add_argument("--core-file", action="append", default=[])
    ap.add_argument("--core-db")
    a = ap.parse_args()
    ids: dict[str, dict] = {}
    for pat in a.ids:
        for f in sorted(glob.glob(pat)):
            for line in open(f, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    ids.setdefault(r["rid"], r)
    echoed = sum(1 for r in ids.values() if r.get("echoed"))
    out: dict = {"harness_request_ids": len(ids), "echoed_by_gateway": echoed, "sources": {}}
    seen_gw: set[str] = set()
    seen_core: set[str] = set()
    for spec, bucket in [(s, "gateway") for s in a.gw_file] + [(s, "core") for s in a.core_file]:
        label, path = spec.split("=", 1)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            out["sources"][label] = f"확인 못 함 ({exc.__class__.__name__})"
            continue
        hits = sorted(r for r in ids if r in text)
        (seen_gw if bucket == "gateway" else seen_core).update(hits)
        out["sources"][label] = {"side": bucket, "ids_found": len(hits), "sample": hits[:4]}
    if a.core_db:
        con = sqlite3.connect(f"file:{a.core_db}?mode=ro", uri=True)
        found: dict[str, set[str]] = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})").fetchall()]
            for row in con.execute(f"SELECT * FROM {t}").fetchall():
                blob = " ".join(str(x) for x in row if isinstance(x, str))
                for r in ids:
                    if r in blob:
                        found.setdefault(t, set()).add(r)
        allc = set().union(*found.values()) if found else set()
        seen_core.update(allc)
        out["sources"]["core_db"] = {"side": "core", "ids_found": len(allc),
                                     "by_table": {t: len(v) for t, v in found.items()},
                                     "sample": sorted(allc)[:4]}
    relayed = []
    for pat in a.steps:
        for f in sorted(glob.glob(pat)):
            for line in open(f, encoding="utf-8"):
                s = json.loads(line)
                body = s.get("body") if isinstance(s.get("body"), dict) else None
                rid = s.get("request_id")
                if body and body.get("location") == "core" and rid and body.get("request_id") == rid:
                    relayed.append({"step": s["step"], "request_id": rid, "code": body.get("code")})
                if s.get("error_request_id") and s.get("error_request_id") == s.get("request_id"):
                    relayed.append({"step": s["step"], "request_id": rid, "code": s.get("code")})
                vb = s.get("validate_body") if isinstance(s.get("validate_body"), dict) else None
                if vb and vb.get("request_id") in ids:
                    relayed.append({"step": s["step"], "request_id": vb["request_id"], "code": vb.get("code"),
                                    "location": vb.get("location")})
    out["error_bodies_carrying_the_same_request_id"] = relayed
    both = sorted(seen_gw & seen_core)
    out["linked_gateway_and_core_records"] = {"count": len(both), "sample": both[:6]}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
