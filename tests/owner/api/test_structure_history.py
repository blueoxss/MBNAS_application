"""DD-7: explicit structure re-checks keep the latest evidence in audit.structure and up to 5 earlier ones in
audit.structure_history (chronological, newest last). Moved here by WP-C from the WP-S3 evidence file."""
from __future__ import annotations

from harness import auth
from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx


def test_structure_check_keeps_latest_evidence_and_five_previous_in_history(client, api_settings, tmp_roots, monkeypatch):
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
                    "candidate_count": 1, "max_attempts": 2, "arms": ["GA"]}}}
    v = client.post("/api/v1/protocols/validate", json={"protocol": p}, headers=auth()).json()
    rid = client.post("/api/v1/runs", json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                      "validation_id": v["validation_id"], "idempotency_key": "dd7"}, headers=auth()).json()["run_id"]
    cid = f"{rid}:0"
    conn = connect(api_settings, init=False)
    with tx(conn):
        repo.insert_candidate(conn, id=cid, run_id=rid, name="c0", arm="IndependentRandom", family="yolo11n",
            architecture_hash="a" * 64, candidate_status="BUILD_VALID",
            audit={"origin": "random_generation", "structure": {"status": "PASS", "yaml_hash": "h0",
                   "observation": {"checked_at": "t0", "method": "m"}}, "performance": {"status": "NOT_EVALUATED"}})
    # stub check documents carry the members every real mbnas-structure/2 document has (the 1.5.0 route validates its
    # answer through the StructureReport contract: schema + status are required)
    results = iter({"schema": "mbnas-structure/2", "status": "PASS" if i % 2 else "FAIL", "yaml_hash": f"h{i}",
                    "observation": {"checked_at": f"t{i}"}, "blocks": [], "edges": [], "diagnostics": [], "checks": []}
                   for i in range(1, 8))
    monkeypatch.setattr("mbnas_core.console.model_services.run_structure_check", lambda cand, run: next(results))
    for i in range(1, 8):
        r = client.post(f"/api/v1/runs/{rid}/candidates/{cid}/structure-check", headers=auth())
        assert r.status_code == 200 and r.json()["yaml_hash"] == f"h{i}"
        audit = repo.get_candidate(conn, cid)["audit"]
        assert audit["structure"]["yaml_hash"] == f"h{i}"  # latest evidence
        assert [h["yaml_hash"] for h in audit["structure_history"]] == [f"h{j}" for j in range(max(0, i - 5), i)]
    entry = audit["structure_history"][-1]
    assert set(entry) == {"checked_at", "yaml_hash", "status", "observation", "archived_at"}
    assert entry["checked_at"] == "t6" and entry["status"] == "FAIL" and entry["observation"] == {"checked_at": "t6"}
    assert len(audit["structure_history"]) == 5
    detail = client.get(f"/api/v1/runs/{rid}/candidates/{cid}", headers=auth()).json()
    assert contract_errors("CandidateDetail", detail) == [] and detail["audit"]["structure_history"][-1]["yaml_hash"] == "h6"
    conn.close()
