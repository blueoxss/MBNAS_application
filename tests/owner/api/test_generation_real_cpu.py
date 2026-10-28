"""Real CPU backend integration: no mock structure checker, trainer or evaluator; no dataset/GPU required."""
from __future__ import annotations

import sys

import pytest

from harness import auth
from mbnas_core.db import repo
from mbnas_server.api.contract_check import contract_errors
from mbnas_model_runtime.genotype import Genotype
from mbnas_core.db.core import connect
from mbnas_core.owner_worker.main import Worker, WorkerConfig


@pytest.mark.owner_worker  # A15: PROPOSE_GENERATION runs on the owner worker (DD-A4)
def test_api_worker_generation_real_cpu(client,api_settings,tmp_roots,monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    monkeypatch.delenv("MBNAS_JOB_HANDLERS_MODULE",raising=False)
    # Use the executable available to this isolated test, independent of a host-specific .venv location.
    api_settings.worker_python=sys.executable
    p={"schema_version":"nas-policy/1","console":{"paths":{"output_root":str(tmp_roots["runs"] / "out")},
       "search":{"evaluation_enabled":False,"nc":1,"input_size":64,"families":["yolo11n"],"seeds":[42],
                 "candidate_count":2,"max_attempts":6,"allow_duplicates":False,"arms":["GA"]}}}
    r=client.post("/api/v1/protocols/validate",json={"protocol":p},headers=auth())
    assert r.status_code==200,r.text
    v=r.json();assert v["valid"],v
    body={"protocol":v["resolved"],"protocol_hash":v["protocol_hash"],"validation_id":v["validation_id"],"idempotency_key":"real-generation"}
    created=client.post("/api/v1/runs",json=body,headers=auth())
    assert created.status_code==200,created.text
    run_id=created.json()["run_id"]
    worker=Worker(api_settings,WorkerConfig(cpu=True,once=True),worker_id="isolated-cpu-generator")
    try:
        assert worker.tick() is True
        assert worker.tick() is False
    finally:
        worker.conn.close()
    snap=client.get(f"/api/v1/runs/{run_id}/snapshot",headers=auth())
    assert snap.status_code==200,snap.text
    snap=snap.json();assert snap["run"]["status"]=="COMPLETED",snap
    assert contract_errors("Snapshot",snap)==[]
    assert snap["candidates"]==[]  # no fabricated evaluation identities or measured rows
    generated=snap["generated_candidates"]
    assert len(generated)==2 and [c["generation_order"] for c in generated]==[0,1]
    assert all(c["structure_status"]=="PASS" and c["evaluation_status"]=="NOT_EVALUATED" for c in generated)
    assert all(c[k] is None for c in generated for k in ("ap","recall","latency_ms","fitness"))
    from mbnas_core.search import structure
    monkeypatch.setattr(structure,"inspect_genotype",lambda *a,**kw:pytest.fail("GET rebuilt a model"))
    for candidate in generated:
        url=f"/api/v1/runs/{run_id}/candidates/{candidate['candidate_id']}"
        response=client.get(url,headers=auth());assert response.status_code==200,response.text
        detail=response.json();assert contract_errors("CandidateDetail",detail)==[]
        assert detail["structure"]["status"]=="PASS"
        assert detail["structure"]["evidence_source"]=="saved_structure_check"
        assert detail["structure"]["architecture_hash"]==detail["architecture_hash"]==candidate["architecture_hash"]
        assert detail["structure"]["signature_hash"] is not None
        repeated=client.get(url,headers=auth());assert repeated.status_code==200
        assert repeated.json()["structure"]==detail["structure"]
        artifacts=[a for a in detail["artifacts"] if a["kind"]=="architecture"]
        assert len(artifacts)==1
        download=client.get(artifacts[0]["url"],headers=auth());assert download.status_code==200,download.text
        downloaded=Genotype.from_yaml_text(download.text,family="yolo11n",template_id="download")
        displayed=Genotype.from_yaml_text(detail["architecture_yaml"],family="yolo11n",template_id="display")
        assert downloaded.yaml_hash()==displayed.yaml_hash()==detail["structure"]["yaml_hash"]
    conn=connect(api_settings,init=False)
    try:
        candidates=repo.list_candidates(conn,run_id)
        assert len(candidates)==2
        assert all(c["audit"]["structure"]["status"]=="PASS" for c in candidates)
        assert all(c["audit"]["performance"]["status"]=="NOT_EVALUATED" for c in candidates)
        assert all(c["audit"]["performance"]["ap"] is None for c in candidates)
        assert conn.execute("SELECT COUNT(*) FROM evaluations WHERE run_id=?",(run_id,)).fetchone()[0]==0
        kinds=[j["kind"] for j in repo.list_jobs(conn,run_id)]
        assert kinds==["PROPOSE_GENERATION"]
        assert len(repo.list_artifacts(conn,run_id))==2
        assert repo.get_run(conn,run_id)["budget"]["generation_results"]["42"]["attempts"]==2
    finally:
        conn.close()
    replay=client.post("/api/v1/runs",json=body,headers=auth())
    assert replay.status_code==200 and replay.json()["run_id"]==run_id and replay.json()["created"] is False
