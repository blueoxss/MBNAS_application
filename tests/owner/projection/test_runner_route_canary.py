"""C02-D2 (C02 EVAL_1; SECURITY_BASELINE section 5.3 item 3): ``/core/v1/runner`` responses are in ``must_not_in``.

A customer root that holds the RUNNER token sees exactly what the runner routes return. Owner-only data is seeded into
the DB rows the envelope builder reads (``jobs.spec_json`` params of every queued job, ``candidates.lineage_json`` and
``candidates.audit_json`` beside ``proposal.genotype``); every ``/core/v1/runner`` response body of a GA + Random search
run (register, claim envelopes, start, progress, artifacts, result, ack) is recorded and must hold no seeded marker and
no distinctive owner-only member name. Positive control: the same markers are readable through ``/core/v1/owner/*`` with
the admin token. The policy job (``PROPOSE_GENERATION``, the only job whose params carry the parent pool / search state /
proposal policy) is never leased to a runner, by default or when asked for explicitly.

Ported from the C02 evaluator probe (scratchpad ``wp3/c02/probe_rp``, R13). The fixtures are the T3 runner-protocol
fixtures (``tests/owner/runner_protocol/conftest.py``), loaded by path under a private module name and bound in this
module only, so they apply to the tests below and nowhere else (``db`` here is the connection of the runner-protocol
core, which the seeding must write to). SYNTHETIC data only.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import harness
import runner_sim as rs
from mbnas_core.db.core import tx


def _load_runner_protocol_fixtures() -> Any:
    name = "_mbnas_rp_conftest_for_runner_route_canary"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[1] / "runner_protocol" / "conftest.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_rp = _load_runner_protocol_fixtures()
# the T3 fixtures this module needs (bound here, so they are module-scoped registrations, not global ones)
stub = _rp.stub
rp_settings = _rp.rp_settings
services = _rp.services
core_app = _rp.core_app
core = _rp.core
db = _rp.db
tokens = _rp.tokens
runner_sim = _rp.runner_sim
owner_worker = _rp.owner_worker
search_proto = _rp.search_proto

MARK = "C02OWNERONLY"
OWNER_ONLY_KEYS = ("old_name", "repair_reason", "slot_kind", "group_key", "child_pid", "etri_root", "realpath",
                   "parent_rows", "mutation_events", "repair_events", "prior_draw", "space_hash", "prior_hash",
                   "etri_audit_model", "proposal_job_id", "estimate_s", "estimate_source", "reserved_s",
                   "parent_pool", "previous_search_state", "proposal_policy", "search_state", "rng_stream",
                   "policy_trace", "admission", "training_outputs", "host_row_id")
#: the runner routes the search run must exercise, so the scan below can never pass on an empty set of bodies
REQUIRED_ROUTES = {"POST /register", "POST /claim", "POST /jobs/{job_id}/start", "POST /jobs/{job_id}/progress",
                   "POST /jobs/{job_id}/checkpoints", "POST /jobs/{job_id}/artifacts", "POST /jobs/{job_id}/result",
                   "POST /jobs/{job_id}/ack"}


def _seed(conn: Any, run_id: str) -> tuple[int, int]:
    """Plant owner-only markers into every queued job's params and every candidate's lineage / audit (idempotent)."""
    n_jobs = n_cands = 0
    with tx(conn):
        for j in conn.execute("SELECT id, spec_json FROM jobs WHERE run_id = ? AND status = 'QUEUED'", (run_id,)).fetchall():
            spec = json.loads(j["spec_json"])
            p = spec.setdefault("params", {})
            if any(str(v).startswith(MARK) for v in p.values()):
                continue
            p.update({"parent_pool": f"{MARK}-parent_pool", "previous_search_state": f"{MARK}-search_state",
                      "proposal_policy": f"{MARK}-proposal_policy", "training_outputs": f"{MARK}-training_outputs",
                      "admission": {"estimate_s": 1.5, "estimate_source": f"{MARK}-estimate_source",
                                    "policy": f"{MARK}-admit_if_remaining_ge_estimate"}})
            if isinstance(p.get("stage"), dict):
                p["stage"]["rules"] = f"{MARK}-stage_rules"
            conn.execute("UPDATE jobs SET spec_json = ? WHERE id = ?", (json.dumps(spec), j["id"]))
            n_jobs += 1
        for c in conn.execute("SELECT id, lineage_json, audit_json FROM candidates WHERE run_id = ?", (run_id,)).fetchall():
            lin = json.loads(c["lineage_json"] or "{}")
            if lin.get("prior_draw") == f"{MARK}-prior_draw":
                continue
            lin.update({"mutation_events": [{"reason": f"{MARK}-mutation"}], "repair_events": [{"reason": f"{MARK}-repair"}],
                        "prior_draw": f"{MARK}-prior_draw", "selection": {"winner": f"{MARK}-selection"},
                        "crossover": {"mask": f"{MARK}-crossover"}, "parent_rows": [{"fitness": f"{MARK}-parent_rows"}]})
            aud = json.loads(c["audit_json"] or "{}")
            aud.setdefault("proposal", {})["policy_trace"] = f"{MARK}-policy_trace"  # sibling of proposal.genotype
            aud["etri_audit_model"] = f"{MARK}-etri_audit_model"
            # members the engine reads (e.g. audit.orchestration) stay untouched: seeding must not change behaviour
            conn.execute("UPDATE candidates SET lineage_json = ?, audit_json = ? WHERE id = ?",
                         (json.dumps(lin), json.dumps(aud), c["id"]))
            n_cands += 1
    return n_jobs, n_cands


def _owner_only_names(doc: Any, found: list[str], path: str = "$") -> None:
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in OWNER_ONLY_KEYS:
                found.append(f"{path}.{k}")
            _owner_only_names(v, found, f"{path}.{k}")
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            _owner_only_names(v, found, f"{path}[{i}]")


def _route(method: str, url: str) -> str:
    path = str(url).split("?")[0].split(rs.PREFIX, 1)[-1]
    return f"{method} " + re.sub(r"^/jobs/[^/]+/", "/jobs/{job_id}/", path)


def test_runner_route_bodies_hold_no_owner_only_data(core, runner_sim, search_proto, owner_worker, db, tokens):
    bodies: list[tuple[str, str, int, bytes]] = []
    orig = core.request

    def recording(method, url, *a, **kw):
        r = orig(method, url, *a, **kw)
        if rs.PREFIX in str(url):
            bodies.append((method, str(url), r.status_code, r.content))
        return r

    core.request = recording
    try:
        run_id, _ = harness.validate_and_run(core, search_proto)
        runner_sim.register()
        seeded_jobs = seeded_cands = 0
        kinds: list[str] = []
        for _ in range(200):
            harness.drain(owner_worker)
            nj, nc = _seed(db, run_id)
            seeded_jobs, seeded_cands = seeded_jobs + nj, seeded_cands + nc
            lease = runner_sim.claim()
            if lease is None:
                break
            kinds.append(lease.kind)
            runner_sim.run_attempt(lease)
    finally:
        core.request = orig

    hits = [(m, _route(m, u), s) for m, u, s, b in bodies if MARK.encode() in b]
    names: list[str] = []
    for m, u, s, b in bodies:
        try:
            doc = json.loads(b)
        except ValueError:
            continue
        found: list[str] = []
        _owner_only_names(doc, found)
        names += [f"{_route(m, u)}: {p}" for p in found]
    routes = {_route(m, u) for m, u, s, b in bodies}
    # positive control: the owner route still serves the seeded owner-only data to the admin principal
    cid = db.execute("SELECT id FROM candidates WHERE run_id = ? AND arm != 'Baseline' LIMIT 1", (run_id,)).fetchone()["id"]
    owner = core.get(f"/core/v1/owner/runs/{run_id}/candidates/{cid}/audit", headers=rs.H(tokens["admin"]))
    print(json.dumps({"claims": len(kinds), "kind_counts": {k: kinds.count(k) for k in sorted(set(kinds))},
                      "seeded_jobs": seeded_jobs, "seeded_candidates": seeded_cands, "runner_route_bodies": len(bodies),
                      "runner_bytes": sum(len(b) for *_, b in bodies), "routes": sorted(routes), "marker_hits": len(hits),
                      "owner_only_member_names": names[:20], "owner_route_status": owner.status_code,
                      "owner_route_has_marker": MARK in owner.text}, indent=1))
    assert len(kinds) >= 3 and {"BASELINE_TRAIN", "CANDIDATE_TRAIN"} <= set(kinds), kinds
    assert seeded_jobs >= len(kinds) and seeded_cands >= 2, (seeded_jobs, seeded_cands)
    assert REQUIRED_ROUTES <= routes, sorted(REQUIRED_ROUTES - routes)
    assert owner.status_code == 200 and MARK in owner.text  # positive control: the markers are really in the DB
    assert hits == [], hits[:10]
    assert names == [], names[:20]


def test_runner_token_never_leases_the_policy_job(core, runner_sim, search_proto, db):
    """PROPOSE_GENERATION (the only job whose params carry parent_pool / previous_search_state / proposal_policy) is
    never handed to a runner: neither by default nor when the runner asks for that kind explicitly."""
    run_id, _ = harness.validate_and_run(core, search_proto)
    runner_sim.register()
    queued = [r["kind"] for r in db.execute("SELECT kind FROM jobs WHERE run_id = ? AND status = 'QUEUED'", (run_id,))]
    explicit = runner_sim.claim_raw(["PROPOSE_GENERATION"])
    default = runner_sim.claim_raw()
    after = [r["kind"] for r in db.execute("SELECT kind FROM jobs WHERE run_id = ? AND status = 'QUEUED'", (run_id,))]
    explicit_kind = explicit.json()["envelope"]["job"]["kind"] if explicit.status_code == 200 else None
    default_kind = default.json()["envelope"]["job"]["kind"] if default.status_code == 200 else None
    print(json.dumps({"queued_before": queued, "explicit_kind_claim": [explicit.status_code, explicit.text[:160]],
                      "default_claim": [default.status_code, default_kind or default.text[:160]],
                      "queued_after": after}, indent=1))
    assert "PROPOSE_GENERATION" in queued
    assert explicit.status_code == 400 and explicit.json().get("code") == "KIND_NOT_ALLOWED", explicit.text
    assert explicit_kind is None
    assert default.status_code == 200 and default_kind != "PROPOSE_GENERATION", default.text[:300]
    assert after.count("PROPOSE_GENERATION") == queued.count("PROPOSE_GENERATION")
