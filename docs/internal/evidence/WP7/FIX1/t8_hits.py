"""Scratch evidence: the T8 host-fact hit check of tests/owner/projection/test_t8_canary_scan.py (all facts except #5),
on a freshly built world, WITHOUT the fixed count assertion that another agent's canary_scan.py change broke."""
import importlib.util, sys, tempfile
from pathlib import Path
ROOT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
for p in (ROOT / "tests" / "_shared", ROOT / "tests" / "owner" / "projection"):
    sys.path.append(str(p))
import conftest_common  # noqa: F401  (test env: MBNAS_TEST_* roots)
import projection_world as pw
spec = importlib.util.spec_from_file_location("wp4c_canary_scan", ROOT / "scripts/dev/canary_scan.py")
mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
facts = [(l, d) for l, d in zip(mod.HOST_FACT_LABELS, mod.host_facts()) if l != "HOST_FACT#5 owner account name"]
print("facts checked:", [l for l, _ in facts])
world = pw.build_world(Path(tempfile.mkdtemp(dir=sys.argv[1])))
print("world runner id:", world.runner_id)
public = [c for c in world.by_side("gateway", "core-server", "core-admin-console") if c.status == 200]
hits = []
for c in public:
    for loc, blob in pw.capture_blobs(c):
        hits += [f"{label} in {loc}" for label, data in facts if data in blob]
print("public captures:", len(public), "hits:", len(hits))
for h in hits[:20]:
    print("  ", h)
