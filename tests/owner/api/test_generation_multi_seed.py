"""Synthetic API snapshot/order/idempotency/export checks; this file does not claim real structure execution."""
import io
import zipfile

from harness import auth
from mbnas_server.api.contract_check import contract_errors
from mbnas_core.db import repo
from mbnas_core.db.core import connect,tx
from mbnas_core.orchestration import engine


def test_generated_snapshot_respects_configured_seed_order_and_idempotency(client,api_settings,tmp_roots):
    p={"schema_version":"nas-policy/1","console":{"paths":{"output_root":str(tmp_roots["runs"] / "out")},
       "search":{"evaluation_enabled":False,"nc":1,"input_size":64,"families":["yolo11n"],"seeds":[42,7],
                 "candidate_count":2,"max_attempts":6,"allow_duplicates":False,"arms":["GA"]}}}
    v=client.post("/api/v1/protocols/validate",json={"protocol":p},headers=auth()).json()
    assert v["valid"],v
    body={"protocol":v["resolved"],"protocol_hash":v["protocol_hash"],"validation_id":v["validation_id"],"idempotency_key":"multi-seed"}
    r=client.post("/api/v1/runs",json=body,headers=auth());assert r.status_code==200,r.text
    rid=r.json()["run_id"]
    conn=connect(api_settings,init=False)
    try:
        jobs=repo.list_jobs(conn,rid);byseed={j["spec"]["search_seed"]:j for j in jobs}
        assert set(byseed)=={42,7}
        # Insert in reverse/shuffled order. API order follows requested seeds then each seed's generation index.
        for seed,index in ((7,1),(7,0),(42,1),(42,0)):
            row={"id":f"{rid}:{seed}:{index}","name":f"mock-{seed}-{index}","arm":"IndependentRandom","family":"mock",
                 "architecture_hash":f"mock-{seed}-{index}","candidate_status":"BUILD_VALID",
                 "audit":{"origin":"random_generation","structure":{"status":"PASS","parameters":None},
                          "performance":{"status":"NOT_EVALUATED"},"generation":{"index":index,"seed":seed,
                          "requested_method":["GA"],"effective_method":"RandomGeneration"}}}
            with tx(conn):
                engine.generation_progress(conn,byseed[seed],{"generated_candidate":{"row":row}})
                engine.generation_progress(conn,byseed[seed],{"generated_candidate":{"row":row}})
        assert len(repo.list_candidates(conn,rid))==4
        assert conn.execute("SELECT COUNT(*) FROM evaluations WHERE run_id=?",(rid,)).fetchone()[0]==0
    finally:
        conn.close()
    snap=client.get(f"/api/v1/runs/{rid}/snapshot",headers=auth()).json()
    assert contract_errors("Snapshot",snap)==[]
    assert [(c["seed"],c["generation_order"]) for c in snap["generated_candidates"]]==[(42,0),(42,1),(7,0),(7,1)]
    assert all(c[k] is None for c in snap["generated_candidates"] for k in ("ap","recall","latency_ms","fitness"))
    repeat=client.post("/api/v1/runs",json=body,headers=auth())
    assert repeat.status_code==200 and repeat.json()["run_id"]==rid and repeat.json()["created"] is False
    changed={**body,"protocol_hash":"sha256:"+"f"*64}
    conflict=client.post("/api/v1/runs",json=changed,headers=auth())
    assert conflict.status_code==409 and conflict.json()["code"]=="IDEMPOTENCY_CONFLICT"
    export=client.get(f"/api/v1/runs/{rid}/export",headers=auth());assert export.status_code==200,export.text
    bundle=zipfile.ZipFile(io.BytesIO(export.content))
    assert not any(n.endswith((".py",".pyc",".map",".js",".ts",".tsx")) for n in bundle.namelist())
    blob=b"\n".join(bundle.read(n) for n in bundle.namelist())
    assert b"def check_structure(" not in blob and b"def propose_generation(" not in blob
    assert b"Traceback (most recent call last)" not in blob


import pytest


def _off_run(client, tmp_roots, key, seeds=(42, 7), count=2):
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": list(seeds),
                    "candidate_count": count, "max_attempts": 6, "allow_duplicates": False, "arms": ["GA"]}}}
    v = client.post("/api/v1/protocols/validate", json={"protocol": p}, headers=auth()).json()
    assert v["valid"], v
    body = {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"], "validation_id": v["validation_id"],
            "idempotency_key": key}
    r = client.post("/api/v1/runs", json=body, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["run_id"], v


def test_resolved_protocol_declares_per_seed_duplicate_scope(client, tmp_roots):
    """DD-2: seeds never share duplicate detection; the scope is part of the resolved (hashed) protocol."""
    _, v = _off_run(client, tmp_roots, "dd2-scope")
    assert v["resolved"]["console"]["search"]["duplicate_scope"] == "per_seed"
    assert v["capabilities"]["generation"]["structure_status"] == "UNVERIFIED"  # G6: contract enum, no NOT_CHECKED
    bad = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
           "search": {"evaluation_enabled": False, "nc": 1, "duplicate_scope": "global"}}}
    r = client.post("/api/v1/protocols/validate", json={"protocol": bad}, headers=auth()).json()
    assert not r["valid"] and any(e["field"] == "console.search.duplicate_scope" for e in r["errors"])


@pytest.mark.parametrize("counts,expected", [
    ({42: 0, 7: 0}, "GENERATION_EMPTY"),
    ({42: 2, 7: 1}, "GENERATION_PARTIAL"),
    ({42: 2, 7: 0}, "GENERATION_PARTIAL"),
    ({42: 2, 7: 2}, None)])
def test_off_run_status_reports_empty_or_partial_generation(client, api_settings, tmp_roots, counts, expected):
    """DD-3: COMPLETED either way (partial results end the run), reason_code tells how complete the generation was."""
    from mbnas_core.scheduling import jobs as jobs_mod
    rid, _ = _off_run(client, tmp_roots, f"dd3-{expected}-{sorted(counts.items())}")
    conn = connect(api_settings, init=False)
    try:
        byseed = {j["spec"]["search_seed"]: j for j in repo.list_jobs(conn, rid)}
        with tx(conn):
            for seed, n in counts.items():
                summary = {"seed": seed, "requested_count": 2, "generated_count": n, "attempts": 6,
                           "status": "COMPLETE" if n == 2 else "PARTIAL", "duplicate_scope": "per_seed"}
                engine.generation_progress(conn, byseed[seed], {"generation_summary": summary})
                conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (byseed[seed]["id"],))
            run = engine.settle_run(conn, rid)
        assert run["status"] == "COMPLETED" and run.get("reason_code") == expected
    finally:
        conn.close()
    body = client.get(f"/api/v1/runs/{rid}", headers=auth()).json()
    assert body["status"] == "COMPLETED" and body.get("reason_code") == expected
    assert contract_errors("Run", body) == []


def test_generation_summary_reports_duplicate_scope():
    from mbnas_core.search.generation import _summary
    s = _summary({"seed": 1, "candidates": [], "attempts": 0, "duplicates": 0, "failure_counts": {}, "diagnostics": []},
                 {"candidate_count": 1, "max_attempts": 2, "allow_duplicates": False, "arms": []})
    assert s["duplicate_scope"] == "per_seed"
