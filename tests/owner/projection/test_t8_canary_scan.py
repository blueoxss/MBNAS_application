"""T8 (SECURITY_BASELINE section 5.3 item 3 / 5.4) on the captured responses with the real scanner.

``scripts/dev/canary_scan.py`` (owner tool) scans the captured public bodies (gateway relay and core console, server
and admin principals; SSE replays; export zips opened recursively) with a ``mbnas.canary_manifest/1`` manifest of the
seeded tokens: profile ``public`` must have 0 failing hits; the owner responses (admin audit, raw export) are the
positive control (``--profile owner --expect-present``: every area expected there has hits). A manifest-free run
(generic token pattern) must also find nothing in the public bodies, and none of the original-host facts may appear.
The host facts are checked separately because the bare account-name needle also matches this test host's temporary
directories; ``MBNAS_TEST_EVIDENCE_DIR`` (optional) receives the JSON reports.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import projection_world as pw
from conftest_common import owner_tool

OWNER_EXPECTED = ("lin_", "audit_", "block_", "sr_", "rm_", "exec_config_path", "ckpt_path", "gen_group_key",
                  "event_log_path", "worker_lease_expires_at", "run_budget_policy", "ledger_host_row_id", "run_etri_root",
                  "run_realpath", "health_env_lock_path")


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")[:150]


def _write(caps: list[pw.Capture], target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    n = 0
    for c in caps:
        ext = ".zip" if c.members is not None else ".txt" if "event-stream" in c.content_type else ".json"
        (target / (_safe(c.label) + ext)).write_bytes(c.body)
        n += 1 + len(c.members or {})
    return n


def _scan(args: list[str], out: Path) -> tuple[int, dict]:
    script = owner_tool("scripts/dev/canary_scan.py")
    proc = subprocess.run([sys.executable, "-B", str(script), *args, "--json", str(out)], capture_output=True, text=True,
                          timeout=600)
    report = json.loads(out.read_text()) if out.is_file() else {}
    return proc.returncode, report | {"_stdout": proc.stdout[-3000:], "_stderr": proc.stderr[-2000:]}


@pytest.fixture(scope="module")
def t8(world: pw.World, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("wp4c_t8")
    public = [c for c in world.by_side("gateway", "core-server", "core-admin-console") if c.status == 200]
    owner = [world.captures["core-admin GET /owner/runs/{run}/export"],
             world.captures["core-admin GET /owner/runs/{run}/candidates/{canary}/audit"]]
    n_public = _write(public, root / "public")
    _write(owner, root / "owner")
    manifest = pw.write_manifest(world, root / "CANARY_MANIFEST.json")
    doc = json.loads(manifest.read_text())
    expected_owner = {s.area for s in world.seed_list() if s.area and (s.key.startswith(OWNER_EXPECTED) or
                                                                        s.key in OWNER_EXPECTED)}
    for area in doc["areas"]:
        area["expected_in"] = ["owner"] if area["area"] in expected_owner else []
    manifest.write_text(json.dumps(doc, indent=1, sort_keys=True))
    runs = {
        "public": _scan([str(root / "public"), "--manifest", str(manifest), "--profile", "public", "--no-host-facts",
                         "--min-files", str(n_public)], root / "public.json"),
        "pattern": _scan([str(root / "public"), "--no-host-facts", "--min-files", str(n_public)], root / "pattern.json"),
        "owner": _scan([str(root / "owner"), "--manifest", str(manifest), "--profile", "owner", "--expect-present",
                        "--no-host-facts"], root / "owner.json"),
    }
    evidence = os.environ.get("MBNAS_TEST_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        for name, (rc, rep) in runs.items():
            Path(evidence, f"t8_canary_scan_{name}.json").write_text(json.dumps({"exit": rc, **rep}, indent=1, sort_keys=True))
        # token values are redacted: the owner ZIP ships docs/internal/** and a release bundle must hold no token
        redacted = json.loads(manifest.read_text())
        for area in redacted["areas"]:
            area["value"] = f"<redacted test token of area {area['area']}>"
        Path(evidence, "t8_canary_manifest.json").write_text(json.dumps(redacted, indent=1, sort_keys=True))
    return {"root": root, "runs": runs, "expected_owner": expected_owner, "n_public": n_public, "public": public}


def test_t8_public_responses_have_zero_canary_hits(t8: dict) -> None:
    rc, rep = t8["runs"]["public"]
    assert rep.get("status") == "PASS" and rc == 0, json.dumps({k: rep.get(k) for k in (
        "status", "failing_hits", "hits_by_label", "scanned", "_stdout")}, indent=1)[:4000]
    assert rep["failing_hits"] == 0 and rep["hits"] == []
    assert rep["scanned"]["files"] >= t8["n_public"] >= 40  # every body, every zip member
    assert len(rep["manifest"]["areas"]) == len([s for s in pw.make_seeds().values() if not isinstance(s.value, int)])


def test_t8_no_token_shaped_string_in_public_responses(t8: dict) -> None:
    rc, rep = t8["runs"]["pattern"]
    assert rc == 0 and rep.get("status") == "PASS" and rep["hits"] == [], rep.get("_stdout")


def test_t8_positive_control_owner_responses_hold_the_markers(t8: dict) -> None:
    rc, rep = t8["runs"]["owner"]
    assert rc == 0 and rep.get("status") == "PASS", json.dumps({k: rep.get(k) for k in (
        "status", "failing_hits", "unmet_expectations", "expectations", "_stdout")}, indent=1)[:4000]
    seen = {e["area"] for e in rep["expectations"] if e["ok"]}
    assert seen == t8["expected_owner"] and len(seen) >= 40, sorted(t8["expected_owner"] - seen)


def test_t8_no_original_host_fact_in_public_responses(t8: dict) -> None:
    spec = importlib.util.spec_from_file_location("wp4c_canary_scan", owner_tool("scripts/dev/canary_scan.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    labels, values = list(mod.HOST_FACT_LABELS), list(mod.host_facts())
    # counts as plain ints: a failing assert must never print the decoded host facts into a test log
    n_labels, n_values = len(labels), len(values)
    assert n_labels == n_values and "HOST_FACT#5 owner account name" in labels
    facts = [(label, data) for label, data in zip(labels, values)
             if label != "HOST_FACT#5 owner account name"]  # the bare account name is part of this host's temp paths
    n_checked = len(facts)
    assert n_checked == n_labels - 1  # every host fact the scanner exports except #5 (no literal count to go stale)
    hits = []
    for c in t8["public"]:
        for loc, blob in pw.capture_blobs(c):
            hits += [f"{label} in {loc}" for label, data in facts if data in blob]
    assert hits == [], hits[:20]
