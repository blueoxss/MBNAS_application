"""WP-4C requirement (2) and the positive controls of the canary suite.

* ``GET /core/v1/owner/runs/{run}/candidates/{cid}/audit`` returns the raw owner members (lineage trace, audit
  internals, block repair/mutation trace, saved structure trace, raw evaluation rows, checkpoint paths) ONLY to an
  admin principal: the gateway's server-scope token and a runner token get 403 ``ACCESS_DENIED``, no token 401
  ``AUTH_REQUIRED``, and the gateway has no owner route at all (404).
* ``GET /core/v1/owner/runs/{run}/export`` (raw profile) carries every DB-stored seed of the export members.
* Every schema-level seed reaches the projection boundary (recorded raw input of ``project_public`` /
  ``project_member`` while the public responses were built): the zero-marker results of test_canary_projection.py
  are therefore removals by the projection, not seeds that never left the database.
"""
from __future__ import annotations

import json

import pytest

import projection_world as pw

CANDIDATE_LEVEL = ("lin_", "audit_", "block_", "sr_")
CANDIDATE_EXTRA = ("exec_config_path", "ckpt_path", "rm_weights_only_path", "rm_eval_checkpoint_path", "rm_rung_result",
                   "rm_artifact_problems", "rm_contract_error", "rm_row_error", "rm_reported")
# what the raw (owner) export must carry: every DB-stored seed of its members
OWNER_EXPORT = ("rm_", "block_", "lin_", "gen_group_key", "event_log_path", "worker_pid", "worker_child_pid",
                "worker_lease_expires_at", "run_budget_policy", "ledger_id", "ledger_host_row_id", "ckpt_path",
                "run_etri_root", "run_realpath", "health_env_lock_path")
NEVER_EMITTED = ("job_parent_pool", "job_previous_search_state", "job_proposal_policy", "job_out_space",
                 "job_out_rng_stream", "job_out_ga", "job_out_search_state", "job_lease_owner")


def _keys(world: pw.World, prefixes: tuple[str, ...]) -> list[pw.Seed]:
    return [s for s in world.seed_list() if s.key.startswith(prefixes) or s.key in prefixes]


def test_owner_audit_returns_the_raw_owner_members_to_an_admin(world: pw.World) -> None:
    cap = world.captures["core-admin GET /owner/runs/{run}/candidates/{canary}/audit"]
    assert cap.status == 200, cap.body[:400]
    body = cap.json()
    assert body.get("candidate_id", body.get("id")) == world.canary_candidate
    expected = _keys(world, CANDIDATE_LEVEL + CANDIDATE_EXTRA)
    assert len(expected) == 35
    missing = [s.path for s in expected if s.needle not in cap.text]
    assert missing == [], missing


@pytest.mark.parametrize("which", ["searched", "baseline", "generated"])
def test_owner_audit_of_every_kind_of_candidate_carries_its_trace(world: pw.World, which: str) -> None:
    labels = [label for label in world.captures if label.startswith("core-admin GET /owner/runs/{run}/candidates/")]
    pick = {"searched": lambda lb: ".ga" in lb or ".rnd" in lb, "baseline": lambda lb: ".base." in lb,
            "generated": lambda lb: "{generated}" in lb}[which]
    chosen = [world.captures[lb] for lb in labels if pick(lb) and "{canary}" not in lb]
    assert chosen, (which, labels)
    for cap in chosen:
        assert cap.status == 200, (cap.label, cap.body[:300])
        missing = [s.path for s in _keys(world, ("lin_", "audit_", "block_")) if s.needle not in cap.text]
        assert missing == [], (cap.label, missing)


@pytest.mark.parametrize("who,status,code", [("server", 403, "ACCESS_DENIED"), ("runner", 403, "ACCESS_DENIED"),
                                             ("none", 401, "AUTH_REQUIRED")])
@pytest.mark.parametrize("route", ["/candidates/{canary}/audit", "/export"])
def test_owner_routes_refuse_every_principal_but_admin(world: pw.World, who: str, status: int, code: str,
                                                       route: str) -> None:
    cap = world.captures[f"core-{who} GET /owner/runs/{{run}}{route}"]
    assert cap.status == status, (cap.label, cap.body[:300])
    assert cap.json()["code"] == code
    assert pw.leaks(world, cap.body) == []


def test_the_gateway_has_no_owner_route(world: pw.World) -> None:
    cap = world.captures["gateway GET /api/v1/owner/runs/{run}/candidates/{canary}/audit"]
    assert cap.status == 404 and cap.json()["code"] == "NOT_FOUND", cap.body[:300]
    assert pw.leaks(world, cap.body) == []


def test_owner_export_carries_the_raw_rows(world: pw.World) -> None:
    cap = world.captures["core-admin GET /owner/runs/{run}/export"]
    assert cap.status == 200 and cap.members, cap.body[:300]
    blob = b"\n".join(cap.members.values())
    expected = _keys(world, OWNER_EXPORT)
    assert len(expected) == 34
    missing = [s.path for s in expected if s.needle not in blob.decode("utf-8", "replace")]
    assert missing == [], missing


def test_owner_registry_route_keeps_the_dd26_diagnostic(world: pw.World) -> None:
    """1.5.0 moved the DD-26 host diagnostic (registry_roots_skipped) off the public RegistryList: the admin-only
    GET /core/v1/owner/registry serves it raw; the gateway's server token gets 403."""
    seed = world.seeds["reglist_roots_skipped"]
    assert seed.planted, seed.note
    admin = world.captures["core-admin GET /owner/registry"]
    assert admin.status == 200 and seed.needle in admin.text, admin.body[:300]
    server = world.captures["core-server GET /owner/registry"]
    assert server.status == 403 and server.json()["code"] == "ACCESS_DENIED"
    assert pw.leaks(world, server.body) == []


def test_every_schema_level_seed_reaches_the_projection_boundary(world: pw.World) -> None:
    """Positive control of the public scan: the raw input of the projection held every seed except the job internals
    (never read by a public route) and the two ledger ids (the export CSV writer drops them itself; the owner export
    test above proves they exist). The DD-26 diagnostic reaches it only while a public route still produces it; since
    1.5.0 its producer feeds only the owner registry route (checked above)."""
    assert world.tap, "the projection tap recorded nothing (projection entry points renamed?)"
    reached: dict[str, set[str]] = {}
    for _fn, component, raw in world.tap:
        for path in pw.leaks(world, raw):
            reached.setdefault(path, set()).add(component)
    expected = {s.path for s in world.seed_list() if s.key not in NEVER_EMITTED + ("ledger_id", "ledger_host_row_id")}
    assert len(expected) == 54
    owner_only_now = {world.seeds["reglist_roots_skipped"].path}
    missing = sorted(expected - set(reached) - owner_only_now)
    assert missing == [], missing
    detail = {path: sorted(comps) for path, comps in sorted(reached.items())}
    assert all(detail.values()), json.dumps(detail, indent=1)
