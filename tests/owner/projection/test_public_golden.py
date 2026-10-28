"""WP-4C: PUBLIC members survive the projection (golden list). The seeded world (``projection_world.py``) puts
``MBNAS-PUBLIC-*`` markers and fixed numbers next to every owner-only member; the projection must drop the owner-only
members and keep these, with their values, in the gateway relay and in the core console responses alike.

Golden members (DTO allowlist PUBLIC, all declared by contract 1.5.0): lineage operation / method / parents /
template / template_detail / search_space.{version, variant}; audit label / hash scheme / signature / variant /
params / flops / config_estimate public counters / admission decision + cap / billed / remaining /
execution_configuration ids; Block name / source / source_kind / parent_block_id / mutated / position / operation /
params.{from, repeats, module, args}; saved StructureReport status and block names; raw_metrics ap_50 / counts /
evaluation_signature / device_name / eval_split and the evaluation measurements; checkpoint id / kind / sha256 /
bytes / epoch / state; run budget (minus policy) and the full resolved protocol (minus etri_root / realpath);
generation rows (minus group_key); worker telemetry; generated candidate view; log message / level / reason_code;
health versions and registry evidence (minus the denied paths); registry warning; export members.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

import pytest

import projection_world as pw

SIDES = ("gateway", "core-server")


def _cap(world: pw.World, side: str, suffix: str) -> pw.Capture:
    matches = [c for c in world.by_side(side) if c.label.endswith(suffix)]
    assert len(matches) == 1, (side, suffix, [c.label for c in matches])
    assert matches[0].status == 200, (matches[0].label, matches[0].body[:400])
    return matches[0]


def _db(world: pw.World, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{world.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql, args))
    finally:
        conn.close()


def _no_none(value: Any) -> Any:
    """``value`` without None members (the projection prunes nulls where the contract does not admit them)."""
    if isinstance(value, dict):
        return {k: _no_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_no_none(v) for v in value]
    return value


def _subset(expected: dict[str, Any], actual: dict[str, Any], where: str) -> list[str]:
    return [f"{where}.{k}: expected {v!r}, got {actual.get(k, '<absent>')!r}" for k, v in expected.items()
            if actual.get(k, "<absent>") != v]


def _expected_blocks(world: pw.World) -> list[dict[str, Any]]:
    p = world.public
    out = []
    for pos in range(2):
        row = pw.CANARY_MODEL["backbone"][pos]
        out.append({"name": p["block_name"], "source": p["block_source"], "source_kind": "parent_a",
                    "parent_block_id": p["block_parent_block_id"], "mutated": True, "position": pos,
                    "operation": "crossover",
                    "params": {"from": row[0], "repeats": row[1], "module": row[2], "args": row[3]}})
    return out


def _check_blocks(world: pw.World, blocks: list[dict[str, Any]], where: str) -> list[str]:
    bad = []
    expected = _expected_blocks(world)
    if len(blocks) != len(expected):
        return [f"{where}: {len(blocks)} blocks, expected {len(expected)}"]
    for i, (exp, got) in enumerate(zip(expected, blocks)):
        params = exp.pop("params")
        bad += _subset(exp, got, f"{where}[{i}]")
        bad += _subset(params, got.get("params") or {}, f"{where}[{i}].params")
    return bad


@pytest.mark.parametrize("side", SIDES)
def test_candidate_detail_keeps_public_lineage_audit_blocks_and_structure(world: pw.World, side: str) -> None:
    p = world.public
    d = _cap(world, side, "/candidates/{canary}").json()
    bad: list[str] = []
    lin = d.get("lineage") or {}
    bad += _subset({"operation": "crossover", "method": p["lin_method"], "template": p["lin_template"],
                    "parent_evaluation_ids": []}, lin, "lineage")
    bad += _subset({"id": p["lin_template"], "family": "canary-family", "variant": "canary-variant"},
                   lin.get("template_detail") or {}, "lineage.template_detail")
    if lin.get("search_space") != {"version": p["lin_space_version"], "variant": p["lin_space_variant"]}:
        bad.append(f"lineage.search_space: {lin.get('search_space')!r}")
    src_parents = json.loads(_db(world, "SELECT parents_json FROM candidates WHERE id = ?", (world.canary_candidate,))[0][0])
    if lin.get("parents") != src_parents or d.get("parents") != src_parents:
        bad.append(f"parents: lineage {lin.get('parents')!r} detail {d.get('parents')!r} stored {src_parents!r}")
    audit = d.get("audit") or {}
    bad += _subset({"label": p["audit_label"], "architecture_hash_scheme": p["audit_scheme"],
                    "structure_signature_sha256": p["audit_signature"], "search_space_variant": p["audit_space_variant"],
                    "params": 123456, "flops": 7.5}, audit, "audit")
    bad += _subset({"backbone_family": p["audit_backbone_family"], "label": p["audit_estimate_label"],
                    "scope": p["audit_scope"], "detect_rows": 1, "head_preserved": True},
                   audit.get("config_estimate") or {}, "audit.config_estimate")
    rung = ((audit.get("orchestration") or {}).get("rungs") or {}).get(world.canary_rung) or {}
    bad += _subset({"admitted": True, "reason_code": p["audit_admission_reason"], "arm": "GA", "search_seed": 101,
                    "cap_s": 2700.0, "billed_s": 12.5, "remaining_s": 2687.5}, rung.get("admission") or {},
                   f"audit.orchestration.rungs.{world.canary_rung}.admission")
    bad += _subset({"artifact_id": p["audit_exec_artifact"], "sha256": p["audit_exec_sha"],
                    "yaml_hash": p["audit_exec_yaml_hash"], "class_count": 1},
                   audit.get("execution_configuration") or {}, "audit.execution_configuration")
    bad += _check_blocks(world, d.get("blocks") or [], "blocks")
    st = d.get("structure") or {}
    bad += _subset({"status": "PASS", "evidence_source": "saved_structure_check"}, st, "structure")
    names = {b.get("name") for b in st.get("blocks") or []}
    if names != {p["sr_block_name"]}:
        bad.append(f"structure.blocks names {names!r}")
    if (st.get("lineage") or {}).get("method") != p["lin_method"]:
        bad.append(f"structure.lineage.method {(st.get('lineage') or {}).get('method')!r}")
    assert bad == [], bad


@pytest.mark.parametrize("side", SIDES)
def test_candidate_detail_keeps_measurements_checkpoints_and_raw_metrics(world: pw.World, side: str) -> None:
    p = world.public
    d = _cap(world, side, "/candidates/{canary}").json()
    stored = {r["id"]: json.loads(r["row_json"]) for r in _db(world, "SELECT id, row_json FROM evaluations "
                                                                       "WHERE candidate_id = ?", (world.canary_candidate,))}
    got = {e["id"]: e for e in d["evaluations"]}
    assert set(got) == set(stored)
    bad: list[str] = []
    for eid, row in stored.items():
        e = got[eid]
        bad += _subset({k: row[k] for k in ("ap", "recall", "latency_ms", "size_mb", "fitness", "rung", "epochs",
                                            "latency_source", "split_hash", "evaluation_hash", "objective_hash",
                                            "protocol_hash", "search_seed")}, e, eid)
        bad += _subset({"ap_50": 0.4242, "evaluation_signature": p["rm_evaluation_signature"],
                        "device_name": p["rm_device_name"], "eval_split": p["rm_eval_split"],
                        "counts": {"tp": 3, "fp": 1, "fn": 2, "gt": 5}}, e.get("raw_metrics") or {}, f"{eid}.raw_metrics")
        bad += _check_blocks(world, e.get("blocks") or [], f"{eid}.blocks")
    ck = [c for c in d.get("checkpoints") or [] if c.get("id", "").startswith("ck-canary")]
    if len(ck) != 1:
        bad.append(f"checkpoints: {d.get('checkpoints')!r}")
    else:
        # state is the view's file-existence overlay: the seeded row's path is no file on this host -> MISSING
        bad += _subset({"kind": "training_full", "sha256": "0" * 64, "bytes": 10, "epoch": 1, "state": "MISSING",
                        "rung": world.canary_rung}, ck[0], "checkpoints[canary]")
    assert bad == [], bad


@pytest.mark.parametrize("side", SIDES)
def test_snapshot_keeps_run_budget_and_the_resolved_protocol(world: pw.World, side: str) -> None:
    """Run.budget is open with the deny list [policy]; Protocol is the user's resolved protocol (open): every member
    other than the two owner-only ones (etri_root, resolution realpath) survives unchanged."""
    snap = _cap(world, side, "/runs/{run}/snapshot").json()
    run = _cap(world, side, "GET /runs/{run}").json()
    row = _db(world, "SELECT budget_json, resolved_protocol_json, protocol_hash FROM runs WHERE id = ?", (world.run_id,))[0]
    budget = json.loads(row["budget_json"])
    budget.pop("policy")
    resolved = json.loads(row["resolved_protocol_json"])
    resolved["console"]["search"]["search_space_resolved"].pop("etri_root")
    for v in resolved["resolution"]["paths"].values():
        v.pop("realpath")
    for where, body in (("snapshot.run", snap["run"]), ("run", run)):
        assert body["protocol_hash"] == row["protocol_hash"], where
        assert _no_none(body["budget"]) == _no_none(budget), (where, body["budget"], budget)
        assert _no_none(body["resolved_protocol"]) == _no_none(resolved), where
    assert snap["last_seq"] == world.events_last_seq


@pytest.mark.parametrize("side", SIDES)
def test_snapshot_keeps_rows_generations_workers_and_generated_candidates(world: pw.World, side: str) -> None:
    p = world.public
    snap = _cap(world, side, "/runs/{run}/snapshot").json()
    stored_ids = {r[0] for r in _db(world, "SELECT id FROM evaluations WHERE run_id = ?", (world.run_id,))}
    assert {r["id"] for r in snap["candidates"]} == stored_ids
    canary = [r for r in snap["candidates"] if r["candidate_id"] == world.canary_candidate]
    assert canary and all((r.get("raw_metrics") or {}).get("evaluation_signature") == p["rm_evaluation_signature"]
                          for r in canary)
    gens = {(g["arm"], g["search_seed"], g["generation"], g["rung"]): g for g in snap["generations"]}
    stored_gens = [json.loads(r[0]) for r in _db(world, "SELECT row_json FROM generations WHERE run_id = ?", (world.run_id,))]
    assert len(gens) == len(stored_gens)
    bad = []
    for row in stored_gens:
        row.pop("group_key")
        got = gens[(row["arm"], row["search_seed"], row["generation"], row["rung"])]
        bad += _subset(_no_none(row), got, f"generation {row['arm']} g{row['generation']} {row['rung']}")
    workers = [w for w in snap["workers"] if w.get("device") == p["worker_device"]]
    assert len(workers) == 1, snap["workers"]
    bad += _subset({"status": "busy", "device_name": p["worker_device_name"], "epoch": 1, "epoch_target_total": 2,
                    "learning_rate": 0.001, "utilization_percent": 37.0, "run_id": world.run_id,
                    "candidate_id": world.canary_candidate}, workers[0], "worker")
    gen = [g for g in snap["generated_candidates"] if g["candidate_id"] == world.generated_candidate]
    assert len(gen) == 1
    bad += _subset({"name": "GEN-CANARY", "structure_status": "PASS", "evaluation_status": "NOT_EVALUATED",
                    "parameters": 4242, "generation_order": 0, "seed": 7, "attempt": 1}, gen[0], "generated")
    bad += _check_blocks(world, gen[0].get("blocks") or [], "generated.blocks")
    logs = [e for e in snap["events"] if e["type"] == "log" and e["payload"].get("message") == p["log_message"]]
    assert len(logs) == 1, "seeded log event"
    bad += _subset({"level": "warning", "reason_code": p["log_reason_code"], "reported_sha256": "a" * 64,
                    "computed_sha256": "b" * 64}, logs[0]["payload"], "log payload")
    assert bad == [], bad


@pytest.mark.parametrize("side", SIDES)
def test_sse_replay_keeps_public_payload_members(world: pw.World, side: str) -> None:
    p = world.public
    events = pw.sse_events(_cap(world, side, "/events?after_seq=0").body)
    assert [e["seq"] for e in events] == list(range(1, world.events_last_seq + 1))
    stored = {r["seq"]: r["type"] for r in _db(world, "SELECT seq, type FROM events WHERE run_id = ? AND seq <= ?",
                                               (world.run_id, world.events_last_seq))}  # later captures add events
    assert {e["seq"]: e["type"] for e in events} == stored
    log = [e for e in events if e["type"] == "log" and e["payload"].get("message") == p["log_message"]]
    assert len(log) == 1 and log[0]["payload"]["reason_code"] == p["log_reason_code"]
    worker = [e for e in events if e["type"] == "worker.updated" and e["payload"].get("device") == p["worker_device"]]
    assert len(worker) == 1 and worker[0]["payload"]["device_name"] == p["worker_device_name"]
    canary = [e for e in events if e["type"] == "candidate.evaluated" and e["payload"]["candidate_id"] == world.canary_candidate]
    assert canary and all(e["payload"]["raw_metrics"]["evaluation_signature"] == p["rm_evaluation_signature"] for e in canary)
    bad = []
    for e in canary:
        bad += _check_blocks(world, e["payload"].get("blocks") or [], f"seq {e['seq']} blocks")
    assert bad == [], bad


@pytest.mark.parametrize("side", SIDES)
def test_health_and_registry_keep_public_evidence_and_versions(world: pw.World, side: str) -> None:
    p = world.public
    health = _cap(world, side, "GET /health").json()
    reg = _cap(world, side, "GET /registry").json()
    if side == "gateway" and isinstance(health.get("core"), dict):  # the gateway merges the core sections
        assert health["core"].get("reachable") is True
    lock = (health.get("versions") or {}).get("environment_lock") or {}
    assert lock.get("present") is True and lock.get("generated_at") == p["health_env_lock_generated_at"], lock
    assert health["versions"].get("python") == "test"
    ids = sorted(json.loads(pw.REGISTRY_EXAMPLE.read_text())["registry"], key=lambda e: e["id"])
    for where, entries in (("health.registry", health["registry"]), ("registry", reg["registry"])):
        assert sorted(e["id"] for e in entries) == [e["id"] for e in ids], where
        for e in entries:
            ev = e.get("evidence") or {}
            bad = _subset({"probe_status": p["reg_probe_status"]}, ev, f"{where}[{e['id']}].evidence")
            for k, sub, val in (("config_ref", "sha256", p["reg_config_ref_sha"]), ("weights", "sha256", p["reg_weights_sha"]),
                                ("build", "status", p["reg_build_status"]),
                                ("pretrained_load", "status", p["reg_pretrained_status"]),
                                ("source", "package", p["reg_source_package"])):
                bad += _subset({sub: val}, ev.get(k) or {}, f"{where}[{e['id']}].evidence.{k}")
            assert e["status"] == "AVAILABLE" and bad == [], bad
    assert reg.get("warning") == p["reglist_warning"]


@pytest.mark.parametrize("side", SIDES)
def test_validate_keeps_the_resolved_protocol_it_stored(world: pw.World, side: str) -> None:
    """ValidateResponse.resolved is the resolved protocol of the stored validation: no public member dropped or changed.
    The two owner-only members (etri_root, resolution realpath) are left out on both sides here; their absence is
    test_canary_projection.py's job."""
    v = _cap(world, side, "POST /protocols/validate").json()
    assert v["valid"] is True and v["validation_id"], v.get("errors")
    row = _db(world, "SELECT resolved_json, protocol_hash FROM validations WHERE id = ?", (v["validation_id"],))[0]
    assert v["protocol_hash"] == row["protocol_hash"]

    def public_part(doc: dict) -> dict:
        doc = copy.deepcopy(doc)
        (doc.get("console", {}).get("search", {}).get("search_space_resolved") or {}).pop("etri_root", None)
        for val in ((doc.get("resolution") or {}).get("paths") or {}).values():
            if isinstance(val, dict):
                val.pop("realpath", None)
        return _no_none(doc)

    assert public_part(v["resolved"]) == public_part(json.loads(row["resolved_json"]))


@pytest.mark.parametrize("side", SIDES)
def test_public_export_keeps_public_rows(world: pw.World, side: str) -> None:
    p = world.public
    for cap in (_cap(world, side, "/runs/{run}/export"), _cap(world, side, "/runs/{run}/export?profile=public")):
        m = cap.members or {}
        assert {"evaluations.jsonl", "lineage.json", "generations.json", "events.jsonl", "protocol/resolved.json",
                "SHA256SUMS"} <= set(m), sorted(m)
        rows = [json.loads(line) for line in m["evaluations.jsonl"].decode().splitlines() if line.strip()]
        canary = [r for r in rows if r["candidate_id"] == world.canary_candidate]
        assert canary and all(r["raw_metrics"]["evaluation_signature"] == p["rm_evaluation_signature"] for r in canary)
        stored_ids = {r[0] for r in _db(world, "SELECT id FROM evaluations WHERE run_id = ?", (world.run_id,))}
        assert {r["id"] for r in rows} == stored_ids
        lineage = {c["id"]: c for c in json.loads(m["lineage.json"])}
        assert lineage[world.canary_candidate]["lineage"]["method"] == p["lin_method"]
        assert len(json.loads(m["generations.json"])) == len(_db(world, "SELECT 1 FROM generations WHERE run_id = ?",
                                                                    (world.run_id,)))
        assert p["log_message"].encode() in m["events.jsonl"]
        resolved = json.loads(m["protocol/resolved.json"])
        assert resolved["console"]["search"]["search_space_resolved"]["variant"]
        sums = dict(line.split("  ", 1)[::-1] for line in m["SHA256SUMS"].decode().splitlines())
        import hashlib

        for name, data in m.items():
            if name != "SHA256SUMS":
                assert sums.get(name) == hashlib.sha256(data).hexdigest(), name
