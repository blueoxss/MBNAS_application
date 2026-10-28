"""C02 EVAL_2 evaluator probe: the runner protocol 1.1.0 prune channel (heartbeat control.prune orders, E11
/checkpoints/{id}/pruned) carries no owner-only data. Owner-only markers are seeded into the queued job params, the
candidates' lineage/audit and the owner-internal prune_requests.planned_by column; every /core/v1/runner body of a
T3-34-style round trip is recorded and scanned. Positive control: the admin owner route shows the candidate markers."""
import json
import re

import harness
import runner_sim as rs
from mbnas_contracts.runner_protocol import HeartbeatResponse
from mbnas_core.db.core import tx
from mbnas_runner.daemon import execute_prune_order

MARK = "C02OWNERONLY"
OWNER_ONLY_KEYS = ("old_name", "repair_reason", "slot_kind", "group_key", "child_pid", "etri_root", "realpath",
                   "parent_rows", "mutation_events", "repair_events", "prior_draw", "space_hash", "prior_hash",
                   "etri_audit_model", "proposal_job_id", "estimate_s", "estimate_source", "reserved_s",
                   "parent_pool", "previous_search_state", "proposal_policy", "search_state", "rng_stream",
                   "policy_trace", "admission", "training_outputs", "host_row_id", "planned_by", "report_json")


def _seed(conn, run_id):
    with tx(conn):
        for j in conn.execute("SELECT id, spec_json FROM jobs WHERE run_id = ? AND status = 'QUEUED'", (run_id,)).fetchall():
            spec = json.loads(j["spec_json"]); p = spec.setdefault("params", {})
            p.update({"parent_pool": f"{MARK}-parent_pool", "previous_search_state": f"{MARK}-search_state",
                      "proposal_policy": f"{MARK}-proposal_policy", "training_outputs": f"{MARK}-training_outputs",
                      "admission": {"estimate_s": 1.5, "estimate_source": f"{MARK}-estimate_source"}})
            conn.execute("UPDATE jobs SET spec_json = ? WHERE id = ?", (json.dumps(spec), j["id"]))
        for c in conn.execute("SELECT id, lineage_json, audit_json FROM candidates WHERE run_id = ?", (run_id,)).fetchall():
            lin = json.loads(c["lineage_json"] or "{}")
            lin.update({"mutation_events": [{"reason": f"{MARK}-mutation"}], "prior_draw": f"{MARK}-prior_draw"})
            aud = json.loads(c["audit_json"] or "{}")
            aud.setdefault("proposal", {})["policy_trace"] = f"{MARK}-policy_trace"
            aud["etri_audit_model"] = f"{MARK}-etri_audit_model"
            conn.execute("UPDATE candidates SET lineage_json = ?, audit_json = ? WHERE id = ?",
                         (json.dumps(lin), json.dumps(aud), c["id"]))


def _names(doc, found, path="$"):
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in OWNER_ONLY_KEYS:
                found.append(f"{path}.{k}")
            _names(v, found, f"{path}.{k}")
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            _names(v, found, f"{path}[{i}]")


def test_prune_channel_bodies_hold_no_owner_only_data(core, runner_sim, base_proto, db, tokens):
    bodies = []
    orig = core.request

    def recording(method, url, *a, **kw):
        r = orig(method, url, *a, **kw)
        if rs.PREFIX in str(url):
            bodies.append((method, str(url).split("?")[0].split(rs.PREFIX, 1)[-1], r.status_code, r.content))
        return r

    core.request = recording
    try:
        proto = base_proto(targets=(3,))
        proto["console"]["retention"] = {"full_state": "last_rung_end_only"}
        run_id, _ = harness.validate_and_run(core, proto)
        runner_sim.register()
        _seed(db, run_id)
        lease = runner_sim.claim()
        runner_sim.start(lease)
        result, lines = runner_sim.execute_stub(lease)
        runner_sim.report_lines(lease, lines)
        with tx(db):
            n_req = db.execute("UPDATE prune_requests SET planned_by = ? WHERE run_id = ?", (f"{MARK}-planned_by", run_id)).rowcount
        _seed(db, run_id)
        hb = runner_sim.heartbeat(lease)
        orders = list(HeartbeatResponse.model_validate(hb.json()).control.prune or [])
        acks = []
        for o in orders:
            report, refusal = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
            assert refusal is None, refusal
            acks.append(runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report).status_code)
        result, ids = runner_sim.preregister(lease, result)
        commit = runner_sim.result(lease, runner_sim.commit_body(lease, result, artifact_ids=ids))
        assert commit.status_code == 200, commit.text
        assert runner_sim.ack(lease).status_code == 204
    finally:
        core.request = orig
    routes = sorted({m + " " + re.sub(r"^/(jobs|checkpoints)/[^/]+/", r"/\1/{id}/", u) for m, u, s, b in bodies})
    hits = [(m, u, s) for m, u, s, b in bodies if MARK.encode() in b]
    names = []
    for m, u, s, b in bodies:
        try:
            doc = json.loads(b)
        except ValueError:
            continue
        f = []
        _names(doc, f)
        names += [f"{m} {u}: {x}" for x in f]
    cid = db.execute("SELECT candidate_id FROM jobs WHERE id = ?", (lease.job_id,)).fetchone()["candidate_id"]
    owner = core.get(f"/core/v1/owner/runs/{run_id}/candidates/{cid}/audit", headers=rs.H(tokens["admin"]))
    planned = db.execute("SELECT COUNT(*) FROM prune_requests WHERE run_id = ? AND planned_by = ?", (run_id, f"{MARK}-planned_by")).fetchone()[0]
    print(json.dumps({"prune_requests_seeded": n_req, "orders": len(orders), "order_keys": sorted({k for o in orders for k in o.model_dump()}),
                      "e11_status": acks, "runner_route_bodies": len(bodies), "routes": routes, "marker_hits": len(hits),
                      "owner_only_member_names": names[:10], "owner_route_status": owner.status_code,
                      "owner_route_has_marker": MARK in owner.text, "planned_by_marker_rows_in_db": planned}, indent=1))
    assert len(orders) >= 1 and all(s == 200 for s in acks)
    assert "POST /jobs/{id}/heartbeat" in routes and "POST /checkpoints/{id}/pruned" in routes, routes
    assert owner.status_code == 200 and MARK in owner.text and planned >= 1   # positive controls
    assert hits == [], hits
    assert names == [], names


def test_runner_absolute_paths_stay_in_the_core_db(core, runner_sim, base_proto, db):
    """Fix 5 / SECURITY_MODEL 8-6: the runner host's absolute paths are stored in the core DB (owner-only) and never
    projected: after a T3-34 round trip the public candidate detail, snapshot and export hold 0 occurrences of the
    runner's work root, while the DB rows do (positive control)."""
    proto = base_proto(targets=(3,))
    proto["console"]["retention"] = {"full_state": "last_rung_end_only"}
    run_id, _ = harness.validate_and_run(core, proto)
    runner_sim.register()
    lease = runner_sim.claim()
    runner_sim.start(lease)
    result, lines = runner_sim.execute_stub(lease)
    runner_sim.report_lines(lease, lines)
    for o in list(HeartbeatResponse.model_validate(runner_sim.heartbeat(lease).json()).control.prune or []):
        report, refusal = execute_prune_order(runner_sim.work_root, o.model_dump(mode="json"), runner_id="R1")
        assert refusal is None
        assert runner_sim.post(f"/checkpoints/{o.checkpoint_id}/pruned", report).status_code == 200
    result, ids = runner_sim.preregister(lease, result)
    assert runner_sim.result(lease, runner_sim.commit_body(lease, result, artifact_ids=ids)).status_code == 200
    assert runner_sim.ack(lease).status_code == 204
    root = str(runner_sim.work_root).encode()
    import sqlite3
    dump = "\n".join(db.iterdump()).encode()
    cid = db.execute("SELECT candidate_id FROM jobs WHERE id = ?", (lease.job_id,)).fetchone()["candidate_id"]
    public = {"candidate_detail": core.get(f"/api/v1/runs/{run_id}/candidates/{cid}", headers=harness.auth()),
              "snapshot": core.get(f"/api/v1/runs/{run_id}/snapshot", headers=harness.auth()),
              "export": core.get(f"/api/v1/runs/{run_id}/export", headers=harness.auth())}
    import io, zipfile
    counts = {}
    for k, r in public.items():
        assert r.status_code == 200, (k, r.status_code, r.text[:200])
        blob = r.content
        if k == "export":
            z = zipfile.ZipFile(io.BytesIO(blob))
            blob = b"\n".join(z.read(n) for n in z.namelist())
        counts[k] = blob.count(root)
    print(json.dumps({"db_occurrences_of_runner_root": dump.count(root), "public_occurrences": counts}, indent=1))
    assert dump.count(root) >= 1      # positive control: the core DB stores the runner's absolute paths
    assert counts == {"candidate_detail": 0, "snapshot": 0, "export": 0}
