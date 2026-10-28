"""Single-model API/worker contract tests. All datasets/registry entries are synthetic; no GPU training claimed."""
from __future__ import annotations

import copy
import json

import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_contracts.schemas.jobs import JobResult
from mbnas_core.training_runs.selection import TrainingRunRequest, TrainingSource
from mbnas_core.owner_worker.child import native_params


def auth():
    import harness
    return harness.auth()


def request(protocol, *, key="training-selection-1", kind="baseline", **more):
    p = copy.deepcopy(protocol)
    p["console"]["training"] = {"epoch_target_total": 3, "seed": 13}
    p["console"]["search"]["evaluation_enabled"] = False
    return {"source": {"kind":kind,"baseline_id":"yolo11n"}, "protocol":p,
            "mode":"scratch", "validation_enabled":True, "idempotency_key":key, **more}


def test_authenticated_and_strict_request(client, protocol):
    req = request(protocol)
    assert client.post("/api/v1/training-runs",json=req).status_code == 401
    req["mode"] = "weights_only_resume"
    assert client.post("/api/v1/training-runs",json=req,headers=auth()).status_code == 422


def _available_registry(monkeypatch, registry):
    """DD-A16: the owner child resolves a baseline's ArchSpec from the core registry (AVAILABLE required); the test
    registry is the synthetic all-AVAILABLE ``registry`` fixture instead of host probe evidence."""
    import mbnas_core.registry as core_registry

    monkeypatch.setattr(core_registry, "load_registry", lambda output_root=None: registry)


def test_baseline_single_stage_creation_idempotency_and_native_params(client, protocol, api_settings, registry, monkeypatch):
    _available_registry(monkeypatch, registry)
    req = request(protocol)
    r = client.post("/api/v1/training-runs",json=req,headers=auth())
    assert r.status_code == 200,r.text
    result = r.json()
    replay = client.post("/api/v1/training-runs",json=req,headers=auth())
    assert replay.status_code == 200,replay.text
    assert replay.json()["run_id"] == result["run_id"] and replay.json()["created"] is False
    conn = connect(api_settings,init=False)
    run = repo.get_run(conn,result["run_id"])
    cand = repo.get_candidate(conn,result["candidate_id"])
    jobs = repo.list_jobs(conn,run["id"])
    assert len(jobs)==1 and jobs[0]["kind"]=="BASELINE_TRAIN"
    assert run["resolved_protocol"]["console"]["search"]["evaluation_enabled"] is True
    assert run["resolved_protocol"]["console"]["extra"]["training"]["seed"]==13
    assert req["protocol"]["console"]["search"]["evaluation_enabled"] is False
    assert cand["audit"]["training_source"]["kind"]=="baseline"
    assert jobs[0]["cumulative_target"]==3 and jobs[0]["rung"]=="TRAIN"
    generic={**jobs[0]["spec"]["params"],"run_dir":"/tmp/run","stop_request_path":None,"live_resume_from":None}
    native=native_params(jobs[0],run,cand,generic,None,settings=api_settings)
    from mbnas_core.registry import arch_spec  # DD-A16: was `native["arch"] is None` (runner resolved the baseline)
    assert native["evaluate"] is True and native["arch"] == arch_spec("yolo11n",api_settings.weights_dir,registry["yolo11n"]).as_dict()
    assert native["adapter_id"]=="yolo11n" and native["stage_epochs_added"]==3
    conn.close()
    req["validation_enabled"]=False
    conflict=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert conflict.status_code==409 and conflict.json()["code"]=="IDEMPOTENCY_CONFLICT"


def test_validation_disabled_does_not_schedule_evaluation(client, protocol, api_settings, registry, monkeypatch):
    _available_registry(monkeypatch, registry)  # DD-A16 baseline ArchSpec lookup
    req=request(protocol,validation_enabled=False)
    response=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    cand=repo.get_candidate(conn,response.json()["candidate_id"])
    job=repo.list_jobs(conn,run["id"])[0]
    native=native_params(job,run,cand,{**job["spec"]["params"],"run_dir":"/tmp/run","stop_request_path":None},None,settings=api_settings)
    assert native["evaluate"] is False
    from mbnas_core.orchestration import engine
    with tx(conn):
        conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job["id"],))
        engine.job_finished(conn,job,JobResult(job_id=job["id"],status="SUCCEEDED",outputs={"epochs_done":3,
            "rung_result":{"rung_end_training_ckpt":"/tmp/last_full.pt"},"architecture_hash":"actual-structure"}))
    assert len(repo.list_jobs(conn,run["id"]))==1
    assert repo.get_run(conn,run["id"])["status"]=="COMPLETED"
    rows=repo.list_evaluations(conn,run["id"])
    assert len(rows)==1 and rows[0]["status"]!="EVALUATED"
    assert all(rows[0]["row"][k] is None for k in ("ap","recall","latency_ms","fitness"))
    assert rows[0]["row"]["checkpoint_path"]=="/tmp/last_full.pt"
    conn.close()


def test_user_model_allowed_yaml_and_no_source_code(client, protocol, api_settings, tmp_roots):
    path=tmp_roots["runs"] / "user.yaml"
    path.write_text("nc: 1\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [[0], 1, Detect, [nc]]\n")
    req=request(protocol,source={"kind":"user","model_path":str(path)})
    response=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    cand=repo.get_candidate(conn,response.json()["candidate_id"])
    assert cand["architecture_yaml"]==path.read_text() or "Conv" in cand["architecture_yaml"]
    assert cand["audit"]["training_source"]["kind"]=="user"
    assert cand["parents"]==[]
    artifacts=repo.list_artifacts(conn,run["id"])
    assert len(artifacts)==1 and artifacts[0]["kind"]=="architecture"
    bad=request(protocol,key="bad-python",source={"kind":"user","model_path":"/etc/passwd"})
    assert client.post("/api/v1/training-runs",json=bad,headers=auth()).status_code==422
    conn.close()


def test_training_failure_is_run_failure(client, protocol, api_settings):
    response=client.post("/api/v1/training-runs",json=request(protocol),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    rid=response.json()["run_id"]
    job=repo.list_jobs(conn,rid)[0]
    job["attempt"]=1
    from mbnas_core.orchestration import engine
    with tx(conn):
        conn.execute("UPDATE jobs SET status='FAILED' WHERE id=?",(job["id"],))
        engine.job_finished(conn,job,JobResult(job_id=job["id"],status="FAILED",reason_code="TRAIN_FAILED"))
    assert repo.get_run(conn,rid)["status"]=="FAILED"
    conn.close()


def test_evaluate_only_schedules_no_training(client, protocol, api_settings):
    response=client.post("/api/v1/training-runs",json=request(protocol,mode="evaluate_only"),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    rid=response.json()["run_id"]
    job=repo.list_jobs(conn,rid)[0]
    assert job["kind"]=="EVALUATE" and len(repo.list_jobs(conn,rid))==1
    run=repo.get_run(conn,rid)
    cand=repo.get_candidate(conn,response.json()["candidate_id"])
    native=native_params(job,run,cand,{**job["spec"]["params"],"run_dir":"/tmp/run"},None)
    assert "eval_ckpt" not in native
    assert run["resolved_protocol"]["console"]["extra"]["training"]["mode"]=="evaluate_only"
    conn.close()


def test_resume_refuses_weights_only_checkpoint_and_recipe_change(client, protocol, api_settings):
    response=client.post("/api/v1/training-runs",json=request(protocol),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    request_body={"source":{"kind":"search","run_id":run["id"],"candidate_id":response.json()["candidate_id"]},
                  "protocol":run["resolved_protocol"],"mode":"resume","checkpoint":"/tmp/weights_only.pt",
                  "idempotency_key":"resume-check"}
    bad=client.post("/api/v1/training-runs",json=request_body,headers=auth())
    # DD-9/DD-36 path 2: a checkpoint path was sent but the candidate has no recorded training_full -> external import.
    assert bad.status_code==409 and bad.json()["code"]=="EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED"
    assert "existing paused run" in bad.json()["message"] and "/resume" in bad.json()["message"]
    request_body.pop("checkpoint")
    request_body["protocol"]["training"]["lr_initial"]=0.19
    bad=client.post("/api/v1/training-runs",json=request_body,headers=auth())
    assert bad.status_code==409 and bad.json()["code"]=="RESUME_RECIPE_MISMATCH"
    conn.close()


def test_generated_source_child_preserves_original_and_rejects_wrong_binding(client, protocol, api_settings):
    from mbnas_model_runtime.genotype import Genotype
    geno=Genotype.from_yaml_dict({"nc":1,"backbone":[[-1,1,"Conv",[16,3,2]]],"head":[[[0],1,"Detect",["nc"]]]},family="yolo11n",template_id="fixture")
    parent_protocol=copy.deepcopy(protocol)
    parent_protocol["console"]["search"]["evaluation_enabled"]=False
    conn=connect(api_settings,init=False)
    with tx(conn):
        repo.create_run(conn,id="generation-fixture",namespace="test",protocol_hash="fixture-protocol",
            resolved_protocol=parent_protocol,requested_protocol=parent_protocol,execution_mode="PILOT",
            idempotency_key="parent-fixture",payload_sha256="fixture-payload",status="COMPLETED")
        repo.insert_candidate(conn,id="generated-fixture-model",run_id="generation-fixture",name="generated-001",arm="IndependentRandom",
            family="yolo11n",architecture_hash="source-structure-fixture",architecture_yaml=geno.yaml_text(),
            audit={"proposal":{"genotype":geno.to_json()}},candidate_status="BUILD_VALID")
    req=request(protocol,source={"kind":"generated","run_id":"generation-fixture","candidate_id":"generated-fixture-model"})
    created=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert created.status_code==200,created.text
    child=repo.get_run(conn,created.json()["run_id"])
    candidate=repo.get_candidate(conn,created.json()["candidate_id"])
    assert child["parent_run_id"]=="generation-fixture"
    assert candidate["parents"]==["generated-fixture-model"]
    assert candidate["audit"]["training_source"]["architecture_hash"]
    assert child["resolved_protocol"]["console"]["extra"]["training"]["source_architecture_hash"]=="source-structure-fixture"
    assert repo.get_run(conn,"generation-fixture")["resolved_protocol"]==parent_protocol
    assert repo.list_evaluations(conn,"generation-fixture")==[]
    wrong=request(protocol,key="wrong-source",source={"kind":"generated","run_id":child["id"],"candidate_id":"generated-fixture-model"})
    response=client.post("/api/v1/training-runs",json=wrong,headers=auth())
    assert response.status_code==404 and response.json()["code"]=="SOURCE_NOT_FOUND"
    conn.close()


def test_single_model_pause_resume_preserves_trial_and_protocol(client, protocol, api_settings):
    created=client.post("/api/v1/training-runs",json=request(protocol),headers=auth())
    assert created.status_code==200,created.text
    rid=created.json()["run_id"]
    before=client.get(f"/api/v1/runs/{rid}",headers=auth()).json()
    paused=client.post(f"/api/v1/runs/{rid}/pause",json={},headers=auth())
    assert paused.status_code==200 and paused.json()["status"]=="PAUSED",paused.text
    resumed=client.post(f"/api/v1/runs/{rid}/resume",json={},headers=auth())
    assert resumed.status_code==200 and resumed.json()["status"]=="RUNNING",resumed.text
    after=client.get(f"/api/v1/runs/{rid}",headers=auth()).json()
    assert before["protocol_hash"]==after["protocol_hash"]
    conn=connect(api_settings,init=False)
    assert len(repo.list_jobs(conn,rid))==1
    assert repo.list_candidates(conn,rid)[0]["id"]==created.json()["candidate_id"]
    conn.close()


def test_unsafe_user_activation_is_rejected(client, protocol, tmp_roots):
    path=tmp_roots["runs"] / "unsafe.yaml"
    path.write_text("nc: 1\nactivation: __import__('os').system('touch /tmp/unsafe-fixture')\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [[0], 1, Detect, [nc]]\n")
    req=request(protocol,source={"kind":"user","model_path":str(path)})
    response=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert response.status_code==422 and response.json()["code"]=="STRUCTURE_INVALID",response.text
    assert any(e["code"]=="ACTIVATION_REFUSED" for e in response.json()["errors"])


def test_validation_disabled_needs_only_train_dataset(client, protocol, api_settings, make_dataset):
    ds=make_dataset("train_only",counts={"train":4})
    from pathlib import Path
    yaml=Path(ds["dataset_yaml"])
    yaml.write_text("\n".join(line for line in yaml.read_text().splitlines() if not line.startswith(("val:","test:")))+"\n")
    req=request(protocol,validation_enabled=False)
    req["protocol"]["console"]["paths"]={"dataset_yaml":str(yaml),"output_root":api_settings.output_root}
    response=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    assert set(run["resolved_protocol"]["dataset"]["counts"])=={"train"}
    assert "search_validation_manifest_hash" not in run["resolved_protocol"]["dataset"]
    assert "final_evaluation_manifest_hash" not in run["resolved_protocol"]["dataset"]
    assert run["resolved_protocol"]["console"]["paths"]["train_root"]==ds["train_root"]
    snapshot=client.get(f"/api/v1/runs/{run['id']}/snapshot",headers=auth())
    assert snapshot.status_code==200,snapshot.text
    conn.close()


def test_minimal_generation_policy_without_recipe_is_actionable_422(client, api_settings):
    req={"source":{"kind":"baseline","baseline_id":"yolo11n"},
         "protocol":{"schema_version":"nas-console/1.0","console":{"paths":{"output_root":api_settings.output_root},
                     "search":{"evaluation_enabled":False,"nc":1},"training":{"epoch_target_total":2}}},
         "mode":"scratch","validation_enabled":True,"idempotency_key":"missing-recipe"}
    response=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert response.status_code==422 and response.json()["code"]=="TRAINING_RECIPE_MISSING",response.text
    assert {e["field"] for e in response.json()["errors"]}=={"training","fitness","hard_constraints"}


def test_official_evaluation_commit_has_zero_training_epochs(client, protocol, api_settings):
    response=client.post("/api/v1/training-runs",json=request(protocol,mode="evaluate_only"),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    job=repo.list_jobs(conn,run["id"])[0]
    job["attempt"]=1
    ev={"ap":0.25,"recall":0.4,"latency_ms":5.0,"size_mb":12.0,"latency_source":"measured",
        "latency_scope":"forward_and_postprocess","device":"host-gpu-0","precision":"FP16","evaluation_hash":"fixture-eval",
        "split_hash":run["resolved_protocol"]["dataset"]["search_validation_manifest_hash"],"checkpoint_path":"/fixture/official.pt",
        "checkpoint_sha256":"fixture-checksum","architecture_hash":repo.get_candidate(conn,response.json()["candidate_id"]).get("architecture_hash") or "fixture-structure","batch":1,
        "requested_input_shape":[64,64],"resolved_input_shape":[64,64],"epochs":0,"training_mode":"evaluate_only"}
    from mbnas_core.orchestration import engine
    with tx(conn):
        conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job["id"],))
        engine.job_finished(conn,job,JobResult(job_id=job["id"],status="SUCCEEDED",outputs={"epochs_done":0,"evaluation":ev}))
    row=repo.list_evaluations(conn,run["id"])[0]["row"]
    assert row["status"]=="EVALUATED" and row["epochs"]==0
    assert row["raw_metrics"]["training_mode"]=="evaluate_only"
    assert repo.get_run(conn,run["id"])["status"]=="COMPLETED"
    conn.close()


def test_selected_baseline_source_is_validated_without_fabricating_gene_parents(client, protocol):
    first=client.post("/api/v1/training-runs",json=request(protocol,key="base-parent"),headers=auth())
    assert first.status_code==200,first.text
    req=request(protocol,key="base-child",source={"kind":"baseline","baseline_id":"yolo11n",
        "run_id":first.json()["run_id"],"candidate_id":first.json()["candidate_id"]})
    child=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert child.status_code==200,child.text
    rid,cid=child.json()["run_id"],child.json()["candidate_id"]
    detail=client.get(f"/api/v1/runs/{rid}/candidates/{cid}",headers=auth())
    assert detail.status_code==200,detail.text
    assert detail.json()["origin"]=="baseline" and detail.json()["parents"]==[]
    assert detail.json()["structure"]["lineage_status"]=="NOT_APPLICABLE"
    assert detail.json()["structure_status"]=="PASS"
    req["idempotency_key"]="missing-baseline-parent"
    req["source"]["candidate_id"]="unrelated-model"
    invalid=client.post("/api/v1/training-runs",json=req,headers=auth())
    assert invalid.status_code==404 and invalid.json()["code"]=="SOURCE_NOT_FOUND"


# -- WP-S3 additions: DD-5, DD-9, DD-10 --------------------------------------------------------------------------
def _finish_validation_off(client, protocol, api_settings, *, key, trained_hash):
    from mbnas_core.orchestration import engine
    response=client.post("/api/v1/training-runs",json=request(protocol,key=key,validation_enabled=False),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    cand=repo.get_candidate(conn,response.json()["candidate_id"])
    job=repo.list_jobs(conn,run["id"])[0]
    job["attempt"]=1
    if trained_hash == "candidate":
        trained_hash = cand["architecture_hash"]
    with tx(conn):
        conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job["id"],))
        engine.job_finished(conn,job,JobResult(job_id=job["id"],status="SUCCEEDED",outputs={"epochs_done":3,
            "rung_result":{"rung_end_training_ckpt":"/tmp/last_full.pt"},"architecture_hash":trained_hash}))
    return conn,run,cand


def test_validation_off_completion_row_keeps_search_complete_with_training_mode_and_not_evaluated(client, protocol, api_settings):
    conn,run,cand=_finish_validation_off(client,protocol,api_settings,key="dd5-validation-off",trained_hash="candidate")
    assert cand["architecture_hash"], "baseline source must carry the structural hash recorded by bind_structure"
    row=repo.list_evaluations(conn,run["id"])[0]["row"]
    # DD-5: SEARCH_COMPLETE + performance_status NOT_EVALUATED (+ training_mode) is the 'trained, not evaluated' state.
    assert row["status"]=="SEARCH_COMPLETE" and row["candidate_status"]=="SEARCH_COMPLETE"
    assert row["training_mode"]=="scratch" and row["performance_status"]=="NOT_EVALUATED"
    assert row["source_kind"]=="baseline"
    assert all(row[k] is None for k in ("ap","recall","latency_ms","fitness"))
    assert row.get("reason_code") in (None,"")
    assert repo.get_candidate(conn,cand["id"])["audit"]["training_source"]["architecture_hash_verified"] is True
    conn.close()


def test_validation_off_hash_mismatch_is_flagged_not_silent(client, protocol, api_settings):
    conn,run,cand=_finish_validation_off(client,protocol,api_settings,key="dd10-validation-off",trained_hash="actual-structure")
    row=repo.list_evaluations(conn,run["id"])[0]["row"]
    assert row["status"]=="SEARCH_COMPLETE" and row["reason_code"]=="ARCHITECTURE_HASH_MISMATCH"
    assert repo.get_candidate(conn,cand["id"])["audit"]["training_source"]["architecture_hash_verified"] is False
    errors=[e["payload"] for e in repo.get_events_after(conn,run["id"],0,200) if e["type"]=="log" and e["payload"].get("level")=="error"]
    assert [e["reason_code"] for e in errors]==["ARCHITECTURE_HASH_MISMATCH"]
    conn.close()


def _evaluate_only_commit(client, protocol, api_settings, *, key, trainer_hash):
    from mbnas_core.orchestration import engine
    response=client.post("/api/v1/training-runs",json=request(protocol,key=key,mode="evaluate_only"),headers=auth())
    assert response.status_code==200,response.text
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,response.json()["run_id"])
    cand=repo.get_candidate(conn,response.json()["candidate_id"])
    job=repo.list_jobs(conn,run["id"])[0]
    job["attempt"]=1
    ev={"ap":0.25,"recall":0.4,"latency_ms":5.0,"size_mb":12.0,"latency_source":"measured",
        "latency_scope":"forward_and_postprocess","device":"host-gpu-0","precision":"FP16","evaluation_hash":"fixture-eval",
        "split_hash":run["resolved_protocol"]["dataset"]["search_validation_manifest_hash"],"checkpoint_path":"/fixture/official.pt",
        "checkpoint_sha256":"fixture-checksum","architecture_hash":trainer_hash(cand),"batch":1,
        "requested_input_shape":[64,64],"resolved_input_shape":[64,64],"epochs":0,"training_mode":"evaluate_only"}
    with tx(conn):
        conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job["id"],))
        engine.job_finished(conn,job,JobResult(job_id=job["id"],status="SUCCEEDED",outputs={"epochs_done":0,"evaluation":ev}))
    return conn,run,cand


def test_architecture_hash_mismatch_invalidates_fitness_with_error_event(client, protocol, api_settings, monkeypatch):
    # The trainer-reported hash differs from the candidate's recorded hash (forced here; no real build is involved).
    conn,run,cand=_evaluate_only_commit(client,protocol,api_settings,key="dd10-mismatch",
                                        trainer_hash=lambda c:"sha256:"+"f"*64)
    assert cand["architecture_hash"]
    rows=repo.list_evaluations(conn,run["id"])
    assert len(rows)==1 and rows[0]["status"]=="EVAL_FAILED"
    row=rows[0]["row"]
    assert row["reason_code"]=="ARCHITECTURE_HASH_MISMATCH"
    assert all(row[k] is None for k in ("ap","recall","latency_ms","fitness")), "metrics must not survive a hash mismatch"
    assert not any(r["status"]=="EVALUATED" for r in rows)
    errors=[e["payload"] for e in repo.get_events_after(conn,run["id"],0,200) if e["type"]=="log" and e["payload"].get("level")=="error"]
    mismatch=[e for e in errors if e.get("reason_code")=="ARCHITECTURE_HASH_MISMATCH"]
    assert len(mismatch)==1 and mismatch[0]["candidate_id"]==cand["id"]
    assert mismatch[0]["trainer_architecture_hash"]=="sha256:"+"f"*64
    assert repo.get_candidate(conn,cand["id"])["audit"]["training_source"]["architecture_hash_verified"] is False
    conn.close()


def test_matching_architecture_hash_is_recorded_as_verified(client, protocol, api_settings):
    conn,run,cand=_evaluate_only_commit(client,protocol,api_settings,key="dd10-match",
                                        trainer_hash=lambda c:c["architecture_hash"])
    rows=repo.list_evaluations(conn,run["id"])
    assert len(rows)==1 and rows[0]["status"]=="EVALUATED"
    assert repo.get_candidate(conn,cand["id"])["audit"]["training_source"]["architecture_hash_verified"] is True
    conn.close()


def test_missing_hash_is_unverified_not_verified(client, protocol, api_settings):
    conn,run,cand=_evaluate_only_commit(client,protocol,api_settings,key="dd10-missing",trainer_hash=lambda c:None)
    assert repo.list_evaluations(conn,run["id"])[0]["status"]=="EVALUATED"
    assert repo.get_candidate(conn,cand["id"])["audit"]["training_source"]["architecture_hash_verified"] is None
    conn.close()


def test_dd36_resume_with_other_path_than_latest_recorded_full_state_keeps_mismatch_code(client, protocol, api_settings, tmp_path):
    response=client.post("/api/v1/training-runs",json=request(protocol,key="dd9-mismatch"),headers=auth())
    assert response.status_code==200,response.text
    rid,cid=response.json()["run_id"],response.json()["candidate_id"]
    conn=connect(api_settings,init=False)
    with tx(conn):
        repo.insert_checkpoint(conn,id="ck-dd9",run_id=rid,candidate_id=cid,epoch=1,kind="training_full",
                               path=str(tmp_path/"full_e0001.pt"),sha256="a"*64,bytes=10,rung="TRAIN",hashes={})
    run=repo.get_run(conn,rid)
    body={"source":{"kind":"search","run_id":rid,"candidate_id":cid},"protocol":run["resolved_protocol"],
          "mode":"resume","checkpoint":str(tmp_path/"somewhere_else.pt"),"idempotency_key":"dd9-wrong-path"}
    wrong=client.post("/api/v1/training-runs",json=body,headers=auth())
    assert wrong.status_code==409 and wrong.json()["code"]=="RESUME_CHECKPOINT_MISMATCH",wrong.text
    body.update(checkpoint=str(tmp_path/"full_e0001.pt"),idempotency_key="dd9-right-path")
    right=client.post("/api/v1/training-runs",json=body,headers=auth())
    # the recorded path passes both DD-9 guards; whatever the run state allows is decided by the ordinary Resume rules
    assert right.json().get("code") not in ("RESUME_CHECKPOINT_MISMATCH","EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED"),right.text
    conn.close()


def test_dd36_resume_without_a_path_takes_the_ordinary_resume_path(client, protocol, api_settings):
    """DD-36 path 1: no checkpoint path -> state.resume_checks decides, even with no recorded training_full (a run
    paused before its first epoch must be resumable); no EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED."""
    response=client.post("/api/v1/training-runs",json=request(protocol,key="dd36-no-path"),headers=auth())
    rid,cid=response.json()["run_id"],response.json()["candidate_id"]
    conn=connect(api_settings,init=False)
    assert repo.latest_checkpoint(conn,cid,"training_full") is None
    paused=client.post(f"/api/v1/runs/{rid}/pause",json={},headers=auth())
    assert paused.status_code==200 and paused.json()["status"]=="PAUSED",paused.text
    run=repo.get_run(conn,rid)
    body={"source":{"kind":"search","run_id":rid,"candidate_id":cid},"protocol":run["resolved_protocol"],
          "mode":"resume","idempotency_key":"dd36-no-path-resume"}
    r=client.post("/api/v1/training-runs",json=body,headers=auth())
    assert r.status_code==200 and r.json()["status"]=="RUNNING" and r.json()["candidate_id"]==cid,r.text
    assert repo.get_run(conn,rid)["status"]=="RUNNING"
    conn.close()


def test_dd36_resume_without_a_path_on_a_queued_run_follows_the_ordinary_resume_rules(client, protocol, api_settings):
    """DD-36 path 1 (second case): a still-QUEUED run goes through state.request_resume unchanged (no-op 200), not
    through the DD-9 external-import guard."""
    response=client.post("/api/v1/training-runs",json=request(protocol,key="dd36-not-paused"),headers=auth())
    rid,cid=response.json()["run_id"],response.json()["candidate_id"]
    conn=connect(api_settings,init=False)
    run=repo.get_run(conn,rid)
    body={"source":{"kind":"search","run_id":rid,"candidate_id":cid},"protocol":run["resolved_protocol"],
          "mode":"resume","idempotency_key":"dd36-not-paused-resume"}
    r=client.post("/api/v1/training-runs",json=body,headers=auth())
    assert r.status_code==200 and r.json()["status"]=="QUEUED" and r.json()["created"] is False,r.text
    assert repo.get_run(conn,rid)["status"]=="QUEUED"  # state.request_resume: a not-paused run is left as it is
    conn.close()


# -- DD-31: evaluate_only needs no epoch target and records 0 ---------------------------------------------------------
def _without_epoch_target(protocol, **more):
    req = request(protocol, **more)
    req["protocol"]["console"].pop("training", None)
    req["protocol"]["console"].pop("baseline_epoch_target", None)
    return req


def test_evaluate_only_accepts_missing_epoch_target_and_records_zero(client, protocol, api_settings):
    from mbnas_core.orchestration import engine
    req = _without_epoch_target(protocol, mode="evaluate_only", key="dd31-missing")
    created = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert created.status_code == 200, created.text
    conn = connect(api_settings, init=False)
    run = repo.get_run(conn, created.json()["run_id"])
    training = run["resolved_protocol"]["console"]["extra"]["training"]
    assert training["mode"] == "evaluate_only" and training["epoch_target_total"] == 0
    assert training["scheduler_horizon_epochs"] == 0 and training["ignored_requested_epoch_target_total"] is None
    job = repo.list_jobs(conn, run["id"])[0]
    assert job["kind"] == "EVALUATE" and job["cumulative_target"] is None and job["spec"]["params"]["stage"]["epochs_added"] == 0
    queued = repo.list_evaluations(conn, run["id"])[0]["row"]
    assert queued["epochs"] == 0 and queued.get("epoch_target_total") is None  # contract: >= 1 or null, never 0
    # committed official evaluation keeps 0 as well
    cand = repo.get_candidate(conn, created.json()["candidate_id"])
    job["attempt"] = 1
    ev = {"ap": 0.25, "recall": 0.4, "latency_ms": 5.0, "size_mb": 12.0, "latency_source": "measured",
          "latency_scope": "forward_and_postprocess", "device": "host-gpu-0", "precision": "FP16", "evaluation_hash": "fixture-eval",
          "split_hash": run["resolved_protocol"]["dataset"]["search_validation_manifest_hash"],
          "checkpoint_path": "/fixture/official.pt", "checkpoint_sha256": "fixture-checksum",
          "architecture_hash": cand.get("architecture_hash") or "fixture-structure", "batch": 1,
          "requested_input_shape": [64, 64], "resolved_input_shape": [64, 64]}
    with tx(conn):
        conn.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (job["id"],))
        engine.job_finished(conn, job, JobResult(job_id=job["id"], status="SUCCEEDED", outputs={"epochs_done": 0, "evaluation": ev}))
    row = repo.list_evaluations(conn, run["id"])[0]["row"]
    assert row["status"] == "EVALUATED" and row["epochs"] == 0 and row.get("epoch_target_total") is None
    conn.close()


def test_evaluate_only_forces_zero_even_when_epochs_are_supplied(client, protocol, api_settings):
    req = request(protocol, mode="evaluate_only", key="dd31-supplied")
    req["protocol"]["console"]["training"] = {"epoch_target_total": 7, "scheduler_horizon_epochs": 9, "seed": 13}
    created = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert created.status_code == 200, created.text  # the mismatching horizon is irrelevant when nothing is trained
    conn = connect(api_settings, init=False)
    training = repo.get_run(conn, created.json()["run_id"])["resolved_protocol"]["console"]["extra"]["training"]
    assert training["epoch_target_total"] == 0 and training["ignored_requested_epoch_target_total"] == 7
    conn.close()


@pytest.mark.parametrize("mode", ["scratch", "pretrained"])
def test_training_modes_still_require_a_positive_epoch_target(client, protocol, mode):
    req = _without_epoch_target(protocol, mode=mode, key=f"dd31-required-{mode}")
    if mode == "pretrained":
        req["checkpoint"] = None
    r = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert r.status_code == 422 and r.json()["code"] == "EPOCH_TARGET_INVALID", r.text


# -- WP-S2: baseline RF-DETR / ETRI hybrid sources bind a native structure document ------------------------------------
@pytest.mark.parametrize("baseline,backend,params", [
    ("etri-controlled-cnn-transformer", "etri_hybrid", 922_870),
    ("rf-detr-nano", "rfdetr", 30_147_076),
])
def test_non_ultralytics_baseline_binding_records_native_structure(client, protocol, api_settings, baseline, backend, params):
    pytest.importorskip("torch")
    if backend == "rfdetr":
        pytest.importorskip("rfdetr")
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    req = request(protocol, key="native-" + baseline, source={"kind": "baseline", "baseline_id": baseline})
    response = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert response.status_code == 200, response.text
    rid, cid = response.json()["run_id"], response.json()["candidate_id"]
    conn = connect(api_settings, init=False)
    structure = repo.get_candidate(conn, cid)["audit"]["structure"]
    conn.close()
    assert structure["status"] == "PASS", structure.get("diagnostics")  # the former STRUCTURE_VIEW_UNSUPPORTED stub is gone
    assert structure["backend"] == backend and structure["representation"] == "native_modules"
    assert structure["parameters"] == params
    assert not any(d["code"] == "STRUCTURE_VIEW_UNSUPPORTED" for d in structure["diagnostics"])
    detail = client.get(f"/api/v1/runs/{rid}/candidates/{cid}", headers=auth())
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["structure_status"] == "PASS" and body["structure"]["evidence_source"] == "saved_structure_check"
    assert body["structure"]["lineage_status"] == "NOT_APPLICABLE" and body["structure"]["blocks"]
    assert all(b["gene_index"] is None for b in body["structure"]["blocks"])
    assert body["origin"] == "baseline" and body["parents"] == []


def test_native_candidate_without_saved_structure_says_not_applicable_not_unrecorded(client, protocol, api_settings):
    pytest.importorskip("torch")
    from mbnas_core.console.model_services import describe_candidate

    req = request(protocol, key="native-declared", source={"kind": "baseline", "baseline_id": "etri-controlled-cnn-transformer"})
    response = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert response.status_code == 200, response.text
    conn = connect(api_settings, init=False)
    run, cand = repo.get_run(conn, response.json()["run_id"]), repo.get_candidate(conn, response.json()["candidate_id"])
    conn.close()
    cand["audit"] = {k: v for k, v in cand["audit"].items() if k != "structure"}
    doc = describe_candidate(cand, run)
    assert doc["status"] == "UNVERIFIED" and doc["evidence_source"] == "declared_configuration"
    assert [d["code"] for d in doc["diagnostics"]] == ["GENE_REPRESENTATION_NOT_APPLICABLE"]
    assert "CONFIGURATION_NOT_RECORDED" not in json.dumps(doc) and doc["backend"] == "etri_hybrid"


def test_explicit_candidate_structure_check_of_native_baseline_runs_the_adapter_and_keeps_history(client, protocol, api_settings):
    pytest.importorskip("torch")
    req = request(protocol, key="native-recheck", source={"kind": "baseline", "baseline_id": "etri-controlled-cnn-transformer"})
    created = client.post("/api/v1/training-runs", json=req, headers=auth()).json()
    rid, cid = created["run_id"], created["candidate_id"]
    checked = client.post(f"/api/v1/runs/{rid}/candidates/{cid}/structure-check", headers=auth())
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "PASS" and checked.json()["backend"] == "etri_hybrid"
    assert checked.json()["class_count"] == 1  # read from the run's dataset YAML, not guessed
    conn = connect(api_settings, init=False)
    audit = repo.get_candidate(conn, cid)["audit"]
    conn.close()
    assert audit["structure"]["status"] == "PASS" and len(audit["structure_history"]) == 1
    assert audit["structure_history"][0]["status"] == "PASS"


# -- WP-S2 fix round 1 (DD-30): the training loader for registry baselines derives the scale like the structure routes ---
@pytest.mark.parametrize("baseline,scale,source", [("rt-detr-l", "l", "file_name_suffix"), ("rt-detr-x", "x", "file_name_suffix"),
                                                    ("yolo11n", "n", "yaml")])  # yaml_model_load already sets scale from the name
def test_registry_baseline_training_derives_scale_from_file_name(client, protocol, api_settings, baseline, scale, source):
    pytest.importorskip("ultralytics")
    req = request(protocol, key="dd30-" + baseline, source={"kind": "baseline", "baseline_id": baseline})
    response = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert response.status_code == 200, response.text  # was 422 STRUCTURE_INVALID (SCALE_INVALID) for the RT-DETR baselines
    conn = connect(api_settings, init=False)
    structure = repo.get_candidate(conn, response.json()["candidate_id"])["audit"]["structure"]
    conn.close()
    assert structure["status"] == "PASS", structure.get("diagnostics")
    assert structure["scale"] == scale and structure["scale_source"] == source


# -- WP-S8 (S2 residual a): the two non-PASS paths of bind_structure's non-ultralytics branch -----------------------------
@pytest.mark.parametrize("baseline,backend", [("etri-controlled-cnn-transformer", "etri_hybrid"), ("rf-detr-nano", "rfdetr")])
def test_non_ultralytics_binding_observation_exception_is_unverified_and_the_run_is_still_created(
        client, protocol, api_settings, monkeypatch, baseline, backend):
    def boom(arch, nc, input_size=None):
        raise RuntimeError("synthetic observation failure: runtime exploded")

    monkeypatch.setattr("mbnas_core.console.model_services.native_structure_for_arch", boom)  # no model is built at all
    req = request(protocol, key="s8-bind-exc-" + baseline, source={"kind": "baseline", "baseline_id": baseline})
    response = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert response.status_code == 200, response.text  # a checker failure never blocks the training request
    conn = connect(api_settings, init=False)
    cand = repo.get_candidate(conn, response.json()["candidate_id"])
    conn.close()
    structure = cand["audit"]["structure"]
    assert structure["status"] == "UNVERIFIED" and structure["backend"] == backend
    assert structure["representation"] == "native_modules" and structure["blocks"] == []
    assert [d["code"] for d in structure["diagnostics"]] == ["STRUCTURE_CHECK_FAILED"]
    assert "RuntimeError" in structure["diagnostics"][0]["cause"] and "runtime exploded" in structure["diagnostics"][0]["cause"]


@pytest.mark.parametrize("baseline,backend", [("etri-controlled-cnn-transformer", "etri_hybrid"), ("rf-detr-nano", "rfdetr")])
def test_non_ultralytics_binding_observation_fail_is_422_structure_invalid_and_creates_no_run(
        client, protocol, api_settings, monkeypatch, baseline, backend):
    diag = {"code": "SHAPE_MISMATCH", "position": None, "path": "head", "cause": "synthetic FAIL document", "suggestion": "none"}

    def failing(arch, nc, input_size=None):
        return {"schema": "mbnas-structure/2", "backend": backend, "representation": "native_modules", "status": "FAIL",
                "blocks": [], "edges": [], "edges_unobserved": [], "diagnostics": [diag]}

    monkeypatch.setattr("mbnas_core.console.model_services.native_structure_for_arch", failing)
    conn = connect(api_settings, init=False)
    before = len(repo.list_runs(conn))
    conn.close()
    req = request(protocol, key="s8-bind-fail-" + baseline, source={"kind": "baseline", "baseline_id": baseline})
    response = client.post("/api/v1/training-runs", json=req, headers=auth())
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "STRUCTURE_INVALID"
    assert "SHAPE_MISMATCH" in json.dumps(response.json())
    conn = connect(api_settings, init=False)
    assert len(repo.list_runs(conn)) == before  # nothing was queued for a structurally invalid model
    conn.close()
