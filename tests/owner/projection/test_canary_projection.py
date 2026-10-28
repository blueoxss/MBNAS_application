"""WP-4C canary projection suite (WP-4 contract section 3, DD-A19/A38/A42/A45; SECURITY_BASELINE section 5.3 item 3).

Every owner-only member of the DTO allowlist that the core can emit carries a unique marker in the seeded world
(``projection_world.py``). Public responses -- the gateway ``/api/v1`` relay with the user bearer AND
``/core/v1/console`` with the server principal -- must contain none of them: run snapshot, candidate detail,
generations (snapshot rows, generated candidates, generation events), SSE events replay, export (public profile),
health, registry, validate, runs, presets and the explicit candidate structure check (configuration revisions:
test_config_revisions.py). The admin principal on the console mount is projected too (WP-4B: raw data exists only
under ``/core/v1/owner/*``). Every public JSON body must also validate against the 1.5.0 contract component of its
route (closed objects and propertyNames deny lists are the structural half of the same guarantee).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import projection_world as pw

PUBLIC_SIDES = ("gateway", "core-server")


def _group(label: str) -> str | None:
    if "/owner/" in label:
        return None
    if label.endswith("/health"):
        return "health"
    if label.endswith("/registry"):
        return "registry"
    if "/protocols/validate" in label:
        return "validate"
    if "/protocols/presets" in label:
        return "presets"
    if "/events" in label:
        return "events_replay"
    if "/export" in label:
        return "export_public"
    if "/snapshot" in label:
        return "snapshot"
    if label.endswith("/structure-check"):
        return "structure_check"
    if "/candidates/" in label:
        return "candidate_detail"
    if label.endswith("GET /runs") or label.endswith("/runs/{run}"):
        return "runs"
    return None


GROUPS = ("snapshot", "candidate_detail", "events_replay", "export_public", "health", "registry", "validate", "runs",
          "presets", "structure_check")


def _captures(world: pw.World, group: str, side: str) -> list[pw.Capture]:
    caps = [c for c in world.by_side(side) if _group(c.label) == group]
    assert caps, f"no {side} capture for {group}"
    for c in caps:
        assert c.status == 200, (c.label, c.status, c.body[:400])
    return caps


def _hits(world: pw.World, caps: list[pw.Capture]) -> dict[str, list[str]]:
    out = {}
    for c in caps:
        for loc, blob in pw.capture_blobs(c):
            found = pw.leaks(world, blob)
            if found:
                out[loc] = found
    return out


# -- seeding is complete and real (positive controls on the seed side) ------------------------------------------------
def test_every_owner_only_allowlist_path_has_a_seed() -> None:
    """48 OWNER_ONLY allowlist entries -> 58 concrete schema paths (CHANGES_1.5.0.md section 1) + the non-schema rows;
    every concrete path except the seven Archive* ones (kept, gateway-local, DD-A19) has exactly one seed."""
    rows = pw.dto_owner_only_paths()
    schema_rows = [r for r in rows if r["path"] is not None]
    assert len({(r["component"], r["path"]) for r in schema_rows}) == 58
    archive = {(r["component"], r["path"]) for r in schema_rows if r["component"] in pw.ARCHIVE_COMPONENTS}
    assert len(archive) == 7
    seeded = {s.path for s in pw.make_seeds().values() if s.category == "OWNER_ONLY"}
    expected = {f"{c} :: {p}" for c, p in {(r["component"], r["path"]) for r in schema_rows} - archive}
    assert len(expected) == pw.SCHEMA_PATHS_EXPECTED
    assert seeded == expected, {"missing": sorted(expected - seeded), "extra": sorted(seeded - expected)}
    non_schema = {r["entry"] for r in rows if r["path"] is None}
    assert len(non_schema) == 3  # ExportBundle jobs internals, PublicIdentifiers group_key / ids
    tokens = [s.value for s in pw.make_seeds().values()]
    assert len(tokens) == len(set(tokens))


def test_seeds_are_planted_where_the_core_reads_them(world: pw.World) -> None:
    """Each DB seed is really stored in the core DB, each provider seed is really served by its provider."""
    import sqlite3

    conn = sqlite3.connect(f"file:{world.db_path}?mode=ro", uri=True)
    try:
        dump = "\n".join(conn.iterdump()).encode()
    finally:
        conn.close()
    not_planted = {s.key: s.note for s in world.seed_list() if not s.planted}
    assert set(not_planted) <= {"health_search_space_root", "reglist_roots_skipped"}, not_planted
    provider_seeds = {"health_env_lock_path", "health_search_space_root", "reglist_roots_skipped"} | {
        k for k in world.seeds if k.startswith("reg_")}
    missing = [s.path for s in world.seed_list() if s.key not in provider_seeds and s.needle.encode() not in dump]
    assert missing == [], missing
    assert all(s.planted_in for s in world.seed_list() if s.planted), [s.key for s in world.seed_list() if not s.planted_in]


def test_the_scanner_sees_public_markers_in_the_same_responses(world: pw.World) -> None:
    """The scan is real: public markers seeded next to the owner-only ones are found in the public bodies."""
    body = b"".join(blob for c in world.by_side(*PUBLIC_SIDES) for _, blob in pw.capture_blobs(c))
    for name in ("lin_method", "audit_label", "rm_evaluation_signature", "log_message", "reg_config_ref_sha"):
        assert world.public[name].encode() in body, name


# -- zero markers in every public response -------------------------------------------------------------------------
@pytest.mark.parametrize("side", PUBLIC_SIDES)
@pytest.mark.parametrize("group", GROUPS)
def test_no_owner_only_marker_in_public_responses(world: pw.World, group: str, side: str) -> None:
    hits = _hits(world, _captures(world, group, side))
    assert hits == {}, json.dumps(hits, indent=1)


@pytest.mark.parametrize("side", PUBLIC_SIDES)
def test_no_owner_only_marker_in_generations(world: pw.World, side: str) -> None:
    """Snapshot generation rows, generated (UNTRAINED) candidates and the generation.* events."""
    snap = next(c for c in _captures(world, "snapshot", side)).json()
    events = pw.sse_events(next(c for c in _captures(world, "events_replay", side)).body)
    parts = {"snapshot.generations": snap.get("generations"), "snapshot.generated_candidates": snap.get("generated_candidates"),
             "events generation.*": [e for e in events if str(e.get("type", "")).startswith("generation.")]}
    assert snap.get("generations") and snap.get("generated_candidates"), "the seeded world has both kinds"
    hits = {k: pw.leaks(world, json.dumps(v).encode()) for k, v in parts.items()}
    assert {k: v for k, v in hits.items() if v} == {}, hits


def test_admin_principal_on_the_console_mount_is_projected_too(world: pw.World) -> None:
    """WP-4B: raw owner data exists only under /core/v1/owner/*; the console mount projects every principal."""
    caps = [c for c in world.by_side("core-admin-console")]
    assert caps
    hits = _hits(world, caps)
    assert hits == {}, json.dumps(hits, indent=1)


def test_snapshot_replay_and_detail_carry_the_seeded_rows(world: pw.World) -> None:
    """Coverage: the responses scanned above really contain the seeded rows (projected), not empty lists."""
    for side in PUBLIC_SIDES:
        snap = next(c for c in _captures(world, "snapshot", side)).json()
        ids = {r["candidate_id"] for r in snap["candidates"]}
        assert world.canary_candidate in ids, side
        assert any(w["id"] for w in snap["workers"]), side
        assert {g["candidate_id"] for g in snap["generated_candidates"]} >= {world.generated_candidate}, side
        events = pw.sse_events(next(c for c in _captures(world, "events_replay", side)).body)
        assert events and events[-1]["seq"] == world.events_last_seq, (side, len(events))
        types = {e["type"] for e in events}
        assert {"log", "worker.updated", "generation.updated", "run.updated", "candidate.evaluated"} <= types, types
        detail = next(c for c in _captures(world, "candidate_detail", side) if "{canary}" in c.label).json()
        assert detail["candidate_id"] == world.canary_candidate and detail["evaluations"] and detail["blocks"], side
        export = next(c for c in _captures(world, "export_public", side) if "profile" not in c.label)
        assert export.members and {"evaluations.jsonl", "lineage.json", "events.jsonl"} <= set(export.members), side


# -- structural half: the bodies are exactly 1.5.0 contract documents --------------------------------------------------
ROUTE_COMPONENT = {"health": "Health", "registry": "RegistryList", "validate": "ValidateResponse", "presets": "PresetList",
                   "snapshot": "Snapshot", "candidate_detail": "CandidateDetail", "structure_check": "StructureReport"}


@pytest.mark.parametrize("side", PUBLIC_SIDES)
@pytest.mark.parametrize("group", sorted(ROUTE_COMPONENT) + ["runs", "events_replay"])
def test_public_bodies_validate_against_the_1_5_0_contract(world: pw.World, contract150: dict, group: str,
                                                            side: str) -> None:
    errors: dict[str, list[str]] = {}
    for c in _captures(world, group, side):
        if group == "events_replay":
            for ev in pw.sse_events(c.body):
                errs = pw.contract_errors_150(contract150, "Event", ev)
                if errs:
                    errors[f"{c.label} seq {ev.get('seq')} {ev.get('type')}"] = errs[:5]
            continue
        comp = ROUTE_COMPONENT.get(group) or ("RunList" if c.label.endswith("GET /runs") else "Run")
        errs = pw.contract_errors_150(contract150, comp, c.json())
        if errs:
            errors[c.label] = errs[:8]
    assert errors == {}, json.dumps(errors, indent=1)[:6000]


# member names that exist only as owner-only members (distinctive: never a public key anywhere in 1.5.0)
OWNER_ONLY_KEYS = ("old_name", "repair_reason", "slot_kind", "group_key", "child_pid", "lease_expires_at", "etri_root",
                   "realpath", "parent_rows", "mutation_events", "repair_events", "prior_draw", "space_hash",
                   "prior_hash", "etri_audit_model", "proposal_job_id", "estimate_s", "estimate_source", "reserved_s",
                   "probe_file", "loaded_from", "registry_roots_skipped", "weights_only_path", "eval_checkpoint_path",
                   "rung_result", "artifact_problems", "contract_error", "row_error", "host_row_id")


def _json_bodies(world: pw.World, sides: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in world.by_side(*sides):
        if c.status != 200:
            continue
        if "event-stream" in c.content_type:
            out[c.label] = pw.sse_events(c.body)
        elif c.members is not None:
            for name, data in c.members.items():
                if name.endswith(".json"):
                    out[f"{c.label}!{name}"] = json.loads(data)
                elif name.endswith(".jsonl"):
                    out[f"{c.label}!{name}"] = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
        elif "json" in c.content_type:
            out[c.label] = c.json()
    return out


def test_no_owner_only_member_name_in_public_bodies(world: pw.World) -> None:
    """Key-level check (independent of marker values): no distinctive owner-only member name appears anywhere in a
    public JSON body, SSE event or JSON export member."""
    found = {}
    for label, doc in _json_bodies(world, PUBLIC_SIDES).items():
        names = {k: pw.find_paths(doc, k)[:3] for k in OWNER_ONLY_KEYS}
        names = {k: v for k, v in names.items() if v}
        if names:
            found[label] = names
    assert found == {}, json.dumps(found, indent=1)[:6000]


def _instances(doc: Any, pred) -> list[dict[str, Any]]:
    out = []
    if isinstance(doc, dict):
        if pred(doc):
            out.append(doc)
        for v in doc.values():
            out.extend(_instances(v, pred))
    elif isinstance(doc, list):
        for v in doc:
            out.extend(_instances(v, pred))
    return out


def test_generic_owner_only_members_are_absent_where_they_lived(world: pw.World) -> None:
    """Owner-only members with generic names, checked at their own place: Block before/after, Worker pid, lineage
    elite/selection/crossover/notes, config_estimate rules/selection_policy, budget policy, search_space root,
    environment_lock path, checkpoints[] path, execution_configuration path, log payload path, registry evidence
    trace/record/notebook and config_ref/weights path."""
    bad: list[str] = []
    for label, doc in _json_bodies(world, PUBLIC_SIDES).items():
        for b in _instances(doc, lambda d: "mutated" in d and "name" in d and ("source" in d or "source_kind" in d)):
            bad += [f"{label}: block.{k}" for k in ("before", "after") if k in b]
        for w in _instances(doc, lambda d: "heartbeat_at" in d and "device" in d and "status" in d):
            bad += [f"{label}: worker.pid" for _ in [0] if "pid" in w]
        for lin in _instances(doc, lambda d: "operation" in d and "parents" in d and "parent_evaluation_ids" in d):
            bad += [f"{label}: lineage.{k}" for k in ("elite", "selection", "crossover", "notes") if k in lin]
            bad += [f"{label}: lineage.search_space.{k}" for k in ("space_hash", "prior_hash")
                    if k in (lin.get("search_space") or {})]
        for est in _instances(doc, lambda d: "backbone_family" in d):
            bad += [f"{label}: config_estimate.{k}" for k in ("rules", "selection_policy") if k in est]
        for run in _instances(doc, lambda d: "protocol_hash" in d and "budget" in d and isinstance(d.get("budget"), dict)):
            bad += [f"{label}: budget.policy" for _ in [0] if "policy" in run["budget"]]
        for ss in _instances(doc, lambda d: "variant" in d and ("fallback_policy" in d or "checked" in d)):
            bad += [f"{label}: search_space.root" for _ in [0] if "root" in ss]
        for lock in _instances(doc, lambda d: "environment_lock" in d and isinstance(d.get("environment_lock"), dict)):
            bad += [f"{label}: environment_lock.path" for _ in [0] if "path" in lock["environment_lock"]]
        for ck in _instances(doc, lambda d: "kind" in d and "epoch" in d and "sha256" in d and "candidate_id" in d):
            bad += [f"{label}: checkpoints[].path" for _ in [0] if "path" in ck]
        for ec in _instances(doc, lambda d: "execution_configuration" in d and isinstance(d.get("execution_configuration"), dict)):
            bad += [f"{label}: execution_configuration.path" for _ in [0] if "path" in ec["execution_configuration"]]
        for ev in _instances(doc, lambda d: d.get("type") == "log" and isinstance(d.get("payload"), dict)):
            bad += [f"{label}: log payload.path" for _ in [0] if "path" in ev["payload"]]
        for evid in _instances(doc, lambda d: "config_ref" in d or "pretrained_load" in d):
            for k, sub in (("config_ref", "path"), ("weights", "path"), ("build", "trace"), ("source", "record"),
                           ("source", "notebook")):
                if isinstance(evid.get(k), dict) and sub in evid[k]:
                    bad.append(f"{label}: evidence.{k}.{sub}")
    assert bad == [], bad[:80]
