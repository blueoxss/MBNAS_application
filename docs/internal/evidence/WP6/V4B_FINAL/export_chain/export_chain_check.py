"""C07 evaluator check (scratch only): on the real stack, the run trained from the uploaded YAML is exported through the
gateway (GET /api/v1/runs/{run}/export) and the zip's architectures/*.yaml bytes are compared with the bytes of the
candidate's config revisions (GET .../config-revisions/{rev}/yaml). Token from the environment (CHECK_TOKEN)."""
import hashlib, io, json, os, sys, zipfile
import httpx

url, run_id, cid, out = sys.argv[1:5]
h = {"Authorization": f"Bearer {os.environ['CHECK_TOKEN']}"}
res = {"run_id": run_id, "candidate_id": cid}
revs = httpx.get(f"{url}/api/v1/runs/{run_id}/candidates/{cid}/config-revisions", headers=h, timeout=60).json()["revisions"]
rev_bytes = {}
for r in revs:
    y = httpx.get(f"{url}/api/v1/runs/{run_id}/candidates/{cid}/config-revisions/{r['revision_id']}/yaml", headers=h, timeout=60)
    rev_bytes[r["kind"]] = y.content
    res.setdefault("revisions", []).append({"kind": r["kind"], "revision_id": r["revision_id"], "source_sha256": r["source_sha256"],
                                            "http": y.status_code, "body_sha256": hashlib.sha256(y.content).hexdigest(),
                                            "x_artifact_sha256": y.headers.get("x-artifact-sha256")})
e = httpx.get(f"{url}/api/v1/runs/{run_id}/export", headers=h, timeout=120)
res["export_http"] = e.status_code
res["export_bytes"] = len(e.content)
res["x_bundle_sha256_matches_body"] = e.headers.get("x-bundle-sha256") == hashlib.sha256(e.content).hexdigest()
zf = zipfile.ZipFile(io.BytesIO(e.content))
names = zf.namelist()
res["members"] = len(names)
res["architecture_members"] = []
for n in names:
    if n.startswith("architectures/"):
        b = zf.read(n)
        res["architecture_members"].append({"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(),
                                            "equals_revision": [k for k, v in rev_bytes.items() if v == b]})
sums = dict(reversed(line.split("  ", 1)) for line in zf.read("SHA256SUMS").decode().splitlines())
res["sha256sums_all_match"] = all(hashlib.sha256(zf.read(n)).hexdigest() == d for n, d in sums.items()) and set(sums) == set(names) - {"SHA256SUMS"}
objs = json.loads(zf.read("export_objects.json")) if "export_objects.json" in names else []
res["export_objects"] = [{k: o.get(k) for k in ("id", "kind", "name", "included", "reason", "origin")} for o in objs]
res["host_path_in_zip"] = any(s.encode() in e.content for s in ("/tmp/claude", "<HOME>"))
open(out, "w").write(json.dumps(res, indent=1))
print(json.dumps({k: res[k] for k in ("export_http", "members", "architecture_members", "sha256sums_all_match", "x_bundle_sha256_matches_body", "host_path_in_zip")}))
