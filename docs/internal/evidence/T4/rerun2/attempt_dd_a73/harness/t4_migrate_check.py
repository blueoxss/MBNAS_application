#!/usr/bin/env python3
"""v5 -> v6 migration check on COPIES of a kept v5 core DB (DD-A66/A68 prune_requests; additive, no data loss).
Copy A: mbnas_core.db.core.migrate() on a connect(init=False) connection (what the migration itself changes) and a
second migrate() (idempotent). Copy B: connect(init=True), the core's own startup path (schema.sql + migrate).
Prints only counts, digests and equality flags (no row values, no meta values)."""
import hashlib, json, sqlite3, sys
from mbnas_core.db import core as dbcore


def snapshot(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {}
    for t in tables:
        rows = c.execute(f"SELECT * FROM {t}").fetchall()
        h = hashlib.sha256()
        for r in sorted(repr(x).encode() for x in rows):
            h.update(r)
        out[t] = {"rows": len(rows), "sha256": h.hexdigest()}
    meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
    integ = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    return {"tables": out, "schema_version": meta.get("schema_version"), "integrity": integ,
            "meta_digest_without_schema_version": hashlib.sha256(repr(sorted((k, v) for k, v in meta.items()
                                                                              if k != "schema_version")).encode()).hexdigest()}


def compare(before, after):
    pre = before["tables"]
    post = after["tables"]
    # 'meta' necessarily changes (schema_version 5 -> 6); its other keys are compared separately below
    changed = [t for t in pre if t != "meta" and (t not in post or pre[t] != post[t])]
    new = sorted(set(post) - set(pre))
    return {"pre_existing_tables": len(pre), "pre_existing_tables_changed": changed, "new_tables": new,
            "new_table_rows": {t: post[t]["rows"] for t in new},
            "schema_version": [before["schema_version"], after["schema_version"]],
            "integrity": [before["integrity"], after["integrity"]],
            "meta_other_keys_unchanged": before["meta_digest_without_schema_version"] == after["meta_digest_without_schema_version"],
            "rows_total": [sum(v["rows"] for v in pre.values()), sum(v["rows"] for k, v in post.items() if k in pre)]}


a, b = sys.argv[1], sys.argv[2]
res = {"schema_version_target": dbcore.SCHEMA_VERSION}
before_a = snapshot(a)
res["before"] = {"schema_version": before_a["schema_version"], "integrity": before_a["integrity"],
                 "tables": {t: v["rows"] for t, v in before_a["tables"].items()}}
conn = dbcore.connect(db_path=a, init=False)
res["copy_A_migrate_added"] = dbcore.migrate(conn)
res["copy_A_second_migrate_added"] = dbcore.migrate(conn)
conn.close()
res["copy_A_compare"] = compare(before_a, snapshot(a))
before_b = snapshot(b)
conn = dbcore.connect(db_path=b, init=True)
conn.close()
res["copy_B_startup_path_compare"] = compare(before_b, snapshot(b))
ok = all(x["schema_version"][1] == "6" and not x["pre_existing_tables_changed"] and x["new_tables"] == ["prune_requests"]
         and x["new_table_rows"] == {"prune_requests": 0} and x["integrity"] == ["ok", "ok"] and x["meta_other_keys_unchanged"]
         for x in (res["copy_A_compare"], res["copy_B_startup_path_compare"])) and res["copy_A_second_migrate_added"] == []
res["verdict"] = "PASS" if ok else "FAIL"
print(json.dumps(res, indent=1))
sys.exit(0 if ok else 1)
