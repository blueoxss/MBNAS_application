"""Copy the F2 (DD-A75) evidence into docs/internal/evidence/WP7/FIX7_DD_A75/F2 with host paths replaced by
placeholders (text files; the mapping is P2's copy_redacted.py scheme). Never copies a GPU uuid: the core-DB facts are
re-derived here with the resolved device's uuid reduced to present/None."""
import json, shutil, sqlite3, sys
from pathlib import Path

SP = "<SP>"
ROOT = "<ROOT>"
REPL = [(SP + "/f2", "<W>"), (SP, "<SP>"), (ROOT, "<ROOT>"), ("<PY310_ENV>", "<PY310_ENV>"),
        ("<CONDA>", "<CONDA>"), ("<HOME>", "<HOME>"), ("<user>", "<user>")]
DST = Path(ROOT) / "docs/internal/evidence/WP7/FIX7_DD_A75/F2"
W = Path(SP) / "f2"
RUNS = {"a_cpu_only_sandbox": ("a", "20261028T050528Z-owner", "verify_a"),
        "b_gpu_nodes_visible_sandbox": ("b", "20261028T050649Z-owner", "verify_b"),
        "baseline_original_smoke_cpu_only_sandbox": ("dev", "20261028T045517Z-owner", "verify_dev_baseline")}


def redact(text: str) -> str:
    for a, b in REPL:
        text = text.replace(a, b)
    return text


def put(src: Path, rel: str) -> None:
    d = DST / rel
    d.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    try:
        d.write_text(redact(data.decode("utf-8")), encoding="utf-8")
    except UnicodeDecodeError:
        shutil.copyfile(src, d)


def db_facts(db: Path) -> str:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    for kind, status, res, owner in c.execute("SELECT kind, status, result_json, lease_owner FROM jobs ORDER BY created_at"):
        o = (json.loads(res or "null") or {}).get("outputs") or {}
        out.append(f"job {kind} {status} lease_owner={owner} device_used={o.get('device_used')}")
    for rid, doc in c.execute("SELECT id, resolved_protocol_json FROM runs ORDER BY created_at"):
        b = ((json.loads(doc or "null") or {}).get("console") or {}).get("benchmark") or {}
        r = b.get("device_resolved")
        r = {"id": r.get("id"), "name": r.get("name"), "uuid": "present (not copied)" if r.get("uuid") else None} if r else None
        out.append(f"run {rid} console.benchmark.device={b.get('device')} device_resolved={r}")
    for row in c.execute("SELECT id, device, device_uuid FROM runners"):
        out.append(f"runner id={row[0]} device={row[1]} device_uuid={row[2]}")
    c.close()
    return "\n".join(out) + "\n"


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for name, (tree, stamp, console) in RUNS.items():
        t = W / tree
        e = t / "zip/mbnas_application/runtime/logs/verify" / stamp
        put(t / "install_owner_offline.txt", f"{name}/install_owner_offline.txt")
        if (t / "swap.txt").is_file():
            put(t / "swap.txt", f"{name}/swap.txt")
        put(t / f"{console}.txt", f"{name}/verify_console.txt")
        put(e / "verify.json", f"{name}/verify.json")
        put(e / "integration.log", f"{name}/integration.log")
        put(e / "integration_smoke.txt", f"{name}/integration_smoke.txt")
        lines = [l for l in (e / "integration_smoke.txt").read_text().splitlines()
                 if l.startswith(("# fleet mode:", "# device discovery:")) or "FLEET MODE" in l or "device discovery {" in l]
        (DST / name / "device_discovery.txt").write_text(
            redact("# device discovery (lines of integration_smoke.txt: evidence header + step log)\n" + "\n".join(lines)
                   + ("\n" if lines else "(none: the original smoke has no device discovery)\n")))
        (DST / name / "core_db_jobs.txt").write_text(redact(
            "# read-only facts of the smoke's core DB (integration_root/core/db/mbnas.sqlite3); GPU uuids not copied\n"
            + db_facts(e / "integration_root/core/db/mbnas.sqlite3")))
    put(W / "b/gpu_compute_apps_monitor.txt", "b_gpu_nodes_visible_sandbox/gpu_compute_apps_monitor.txt")
    for f in ("sbx_f2.sh", "nvml_probe.py", "gpu_monitor.sh", "step_table.py", "mutation_control.sh", "assemble_evidence.py"):
        put(W / "harness" / f, f"harness/{f}")
    put(W / "dev/mutation_control.txt", "pytest/mutation_control.txt")
    for f in sorted((W / "pytest").glob("*.txt")):
        put(f, f"pytest/{f.name}")
    for f in ("f2_source_tree_hash_before.json", "f2_source_tree_hash_after.json", "f2_source_tree_manifest_diff.json"):
        put(Path(SP) / f, f"source_tree_hash/{f[3:]}")
    print("assembled", sum(1 for p in DST.rglob('*') if p.is_file()), "files under", DST)


main()
