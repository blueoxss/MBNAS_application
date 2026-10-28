"""Incremental provenance copy: re-copy the files whose live sha256 differs from the manifest (read-only on source)."""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, datetime
SRC = "/home/mgjeong/Desktop/etri_hd/etri_nas"; TGT = "/home/mgjeong/Desktop/etri_hd/mbnas_application"
SNAP = os.path.join(TGT, "build", "source_snapshot"); PROV = os.path.join(TGT, "docs", "internal", "provenance")
def git(*a): return subprocess.run(["git", "--no-optional-locks", "-C", SRC, *a], check=True, capture_output=True).stdout.decode()
def blob(d): h = hashlib.sha1(); h.update(b"blob %d\0" % len(d)); h.update(d); return h.hexdigest()
head = git("rev-parse", "HEAD").strip()
head_blobs = {l.split("\t",1)[1]: l.split("\t",1)[0].split()[2] for l in git("ls-tree","-r","-z","HEAD").split("\0") if l}
idx = {l.split("\t",1)[1]: l.split("\t",1)[0].split()[1] for l in git("ls-files","-s","-z").split("\0") if l}
base = [json.loads(l) for l in open(os.path.join(PROV,"SOURCE_SNAPSHOT_MANIFEST.jsonl"), encoding="utf-8")]
now = datetime.datetime.now(datetime.timezone.utc).isoformat(); recs = []
for r in base:
    rel = r["source_relpath"]; p = os.path.join(SRC, rel)
    if not os.path.isfile(p): continue
    data = open(p, "rb").read(); h = hashlib.sha256(data).hexdigest()
    if h == r["sha256"]: continue
    dst = os.path.join(SNAP, rel); prev = hashlib.sha256(open(dst,"rb").read()).hexdigest()
    open(dst, "wb").write(data); shutil.copystat(p, dst)
    b = blob(data); hb = head_blobs.get(rel); ib = idx.get(rel)
    status = "untracked" if hb is None and ib is None else "tracked_clean" if b == hb else "tracked_modified"
    recs.append({**r, "sha256": h, "bytes": len(data), "git_blob_sha1": b, "head_blob_sha1": hb, "index_blob_sha1": ib,
                 "git_status": status, "copied_at": now, "supersedes_sha256": prev, "delta": 2,
                 "source_mtime": datetime.datetime.fromtimestamp(os.stat(p).st_mtime, datetime.timezone.utc).isoformat()})
tag = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
with open(os.path.join(PROV, f"SOURCE_SNAPSHOT_DELTA_{tag}.jsonl"), "w", encoding="utf-8") as fh:
    for r in recs: fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
# rewrite the main manifest with updated records (latest state) keeping order
by = {r["source_relpath"]: r for r in recs}
with open(os.path.join(PROV, "SOURCE_SNAPSHOT_MANIFEST.jsonl"), "w", encoding="utf-8") as fh:
    for r in base: fh.write(json.dumps(by.get(r["source_relpath"], r), ensure_ascii=False, sort_keys=True) + "\n")
summ = json.load(open(os.path.join(PROV, "SOURCE_SNAPSHOT_SUMMARY.json"), encoding="utf-8"))
summ.setdefault("deltas", []).append({"tag": tag, "at": now, "head": head, "files": [r["source_relpath"] for r in recs],
    "reason": "live source advanced contract 1.4.1 -> 1.4.2 (DD-38/39) after the first snapshot; re-copied before WP-1"})
json.dump(summ, open(os.path.join(PROV, "SOURCE_SNAPSHOT_SUMMARY.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({"delta_tag": tag, "head": head, "files": [r["source_relpath"] for r in recs]}, ensure_ascii=False, indent=1))
