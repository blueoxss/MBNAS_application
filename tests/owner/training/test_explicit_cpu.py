"""Actual CPU backend smoke: isolated tiny detector, no pretrained download, no GPU, no NAS campaign."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_actual_cpu_train_pause_full_state_resume_without_validation(settings, runner_settings, make_dataset, ctx_factory,
                                                                    tmp_path):  # A11: core + runner settings
    torch=pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_contracts.schemas.jobs import JobSpec,JobStatus
    from mbnas_runner.training.handlers import run_training_job
    from mbnas_core.training_runs.selection import TrainingRunRequest,prepare_protocol
    from mbnas_model_runtime.checkpoint import load_full_state
    torch.set_num_threads(2)
    ds=make_dataset("cpu_train_only",counts={"train":4})
    yaml=Path(ds["dataset_yaml"])
    yaml.write_text("\n".join(line for line in yaml.read_text().splitlines() if not line.startswith(("val:","test:")))+"\n")
    policy=pilot_smoke_protocol({"dataset_yaml":str(yaml),"output_root":settings.output_root})
    policy["training"].update(requested_input_size=[64,64],batch_per_gpu=2,workers=0,precision="fp32",warmup_epochs=0,
                                optimizer="SGD",lr_initial=0.001,close_mosaic_epochs=0)
    policy["console"]["training"]={"epoch_target_total":2,"seed":41,"native_image_artifacts":True}
    arch={"model_id":"tiny-cpu","family":"YOLO11","backend":"ultralytics","variant":"detect", "config":{
        "nc":1,"backbone":[[-1,1,"Conv",[8,3,2]],[-1,1,"Conv",[16,3,2]],[-1,1,"Conv",[32,3,2]]],
        "head":[[[0,1,2],1,"Detect",["nc"]]]}}
    req=TrainingRunRequest(source={"kind":"user","model_path":"/unused/fixture.yaml"},protocol=policy,
                           validation_enabled=False,idempotency_key="actual-cpu")
    source={"arch":arch,"kind":"user","name":"tiny-cpu"}
    svc=SimpleNamespace(settings=settings,validation_context=ctx_factory)
    _,resolved=prepare_protocol(req,source,svc)
    phash=protocol_hash(resolved)
    stop=tmp_path/"stop.request"
    stop.write_text("pause")
    params={"run_dir":str(Path(settings.output_root)/"actual-cpu"),"adapter_id":"tiny-cpu", "arch":arch,
            "resolved_protocol":resolved,"init":"scratch","device":"cpu","evaluate":False,
            "stage_epochs_added":2,"stop_request_path":str(stop)}
    spec=JobSpec(job_id="cpu-stage-1",run_id="actual-cpu",kind="BASELINE_TRAIN",candidate_id="tiny-model",rung="TRAIN",
                 protocol_hash=phash,cumulative_target=2,fixed_horizon=2,training_seed=41,arm="Baseline",params=params)
    messages=[]
    paused=run_training_job(spec,messages.append,runner_settings)
    assert paused.status==JobStatus.CANCELLED and paused.reason_code=="PAUSED_AT_EPOCH",paused.message
    assert paused.outputs["epochs_done"]==1 and "evaluation" not in paused.outputs
    full=next(m.payload for m in messages if m.type=="checkpoint" and m.payload["kind"]=="training_full")
    state=load_full_state(full["path"])
    for key in ("model_fp32","optimizer","scheduler","scaler","ema_fp32","rng","loader_generator_state"):
        assert key in state
    assert state["adapter_meta"]["fitness"] is None
    stop.unlink()
    resumed=run_training_job(spec.model_copy(update={"job_id":"cpu-stage-2","params":{**params,
        "resume_from":{"path":full["path"],"sha256":full["sha256"],"epoch":1}}}),messages.append,runner_settings)
    assert resumed.status==JobStatus.SUCCEEDED,resumed.message
    assert resumed.outputs["epochs_done"]==2 and "evaluation" not in resumed.outputs
    assert resumed.outputs["rung_result"]["resumed_from"].endswith("full_e0001.pt")
    assert Path(resumed.outputs["rung_result"]["rung_end_training_ckpt"]).is_file()
    assert all(h.get("native_fitness") is None for h in resumed.outputs["rung_result"]["lr_history"])
    assert all(h.get("native_metrics")=={} for h in resumed.outputs["rung_result"]["lr_history"])

    # Register only files the native trainer actually generated; the API can fetch them after the run completes.
    from mbnas_core.db.core import connect, tx
    from mbnas_core.db import repo
    from mbnas_core.orchestration.model_artifacts import record_native_images
    conn=connect(settings)
    with tx(conn):
        run,_=repo.create_run(conn,id="actual-cpu",namespace="test",protocol_hash=phash,
            resolved_protocol=resolved,requested_protocol=resolved,execution_mode="PILOT",
            idempotency_key="actual-cpu-run",payload_sha256="cpu-smoke-fixture")
        candidate=repo.insert_candidate(conn,id="tiny-model",run_id=run["id"],name="tiny-cpu",family="YOLO11",arm="Baseline")
        images=record_native_images(conn,run,candidate)
    assert any(a["meta"]["category"]=="training_batch" for a in images)
    assert all(a["meta"]["candidate_id"]=="tiny-model" for a in images)
    assert not any(a["meta"]["category"].startswith("validation") for a in images)
    import os,shutil,json
    evidence=os.environ.get("EVIDENCE_DIR_EXPLICIT_CPU")
    if evidence:
        out=Path(evidence)
        out.mkdir(parents=True,exist_ok=True)
        copied=[]
        for artifact in images:
            source_path=Path(params["run_dir"])/artifact["relpath"]
            destination=out/(source_path.parent.name+"_"+source_path.name)
            shutil.copyfile(source_path,destination)
            copied.append({"file":destination.name,"sha256":artifact["sha256"],"meta":artifact["meta"]})
        (out/"cpu_smoke.json").write_text(json.dumps({"evidence_type":"actual_cpu_training_on_synthetic_four_image_dataset",
            "backend":"Ultralytics8.4.173/PyTorch2.6CPU","images":copied,"epochs":2,"pause_epoch":1,
            "resume_checkpoint":"full_e0001.pt","validation_enabled":False,"metrics":"not_evaluated",
            "restored_state_keys":["model_fp32","optimizer","scheduler","scaler","ema_fp32","rng","loader_generator_state"]},indent=2))
    conn.close()
