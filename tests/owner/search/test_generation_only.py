"""Evaluation-free protocol and orchestration tests (isolated DB, no GPU or dataset)."""
import pytest
from mbnas_core.protocol.validator import validate
from mbnas_contracts.schemas.protocol import SearchSettings
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration import engine


def request(tmp_roots):
    return {"schema_version":"nas-policy/1", "console":{"paths":{"output_root":str(tmp_roots["runs"] / "out")},
        "search":{"evaluation_enabled":False,"nc":1,"input_size":64,"families":["yolo11n"],"seeds":[7],
                  "candidate_count":2,"max_attempts":6,"allow_duplicates":False,"arms":["GA"]}}}


def test_off_validation_without_dataset_gpu_and_fixed_point(ctx_factory,tmp_roots,monkeypatch):
    ctx=ctx_factory(devices=[])
    monkeypatch.setattr(ctx.dataset_service,"manifest_for",lambda *a:pytest.fail("OFF accessed dataset"))
    p=request(tmp_roots);p["rungs"]=[{"saved":"disabled settings"}]
    out=validate(p,ctx)
    assert out.valid,out.errors
    assert out.resolved["console"]["search"]["effective_method"]=="RandomGeneration"
    assert out.resolved["console"]["search"]["requested_method"]==["GA"]
    assert out.resolved["rungs"]==p["rungs"]
    again=validate(out.resolved,ctx)
    assert again.valid and again.protocol_hash==out.protocol_hash
    assert "dataset" not in out.resolved


def test_new_schema_defaults_evaluation_on():
    assert SearchSettings().evaluation_enabled is True


@pytest.mark.parametrize("field,value",[("nc",0),("candidate_count",0),("max_attempts",1),("allow_duplicates","yes"),
    ("seeds",[7,7]),("input_size",33)])
def test_off_controls_invalid(ctx_factory,tmp_roots,field,value):
    p=request(tmp_roots);p["console"]["search"][field]=value
    r=validate(p,ctx_factory(devices=[]))
    assert not r.valid and any(e["field"]=="console.search."+field for e in r.errors)


def test_plan_is_only_generation_and_forbidden_calls_never_occur(db,ctx_factory,tmp_roots,monkeypatch):
    out=validate(request(tmp_roots),ctx_factory(devices=[]));assert out.valid,out.errors
    def forbidden(*a,**kw):pytest.fail("OFF called training/evaluation/fitness/promotion")
    for name in ("_schedule_stage","_queue_evaluation","commit_evaluation","fitness","evaluate_gate","_parent_pool","_plan_baseline"):
        monkeypatch.setattr(engine,name,forbidden)
    with tx(db):
        run,_=repo.create_run(db,id="off-run",namespace="default",protocol_hash=out.protocol_hash,resolved_protocol=out.resolved,
            requested_protocol=request(tmp_roots),execution_mode="PILOT",idempotency_key="off-key",payload_sha256="off-payload")
        planned=engine.plan_run(db,run["id"])
        replay=engine.plan_run(db,run["id"])
    assert len(planned)==1 and replay==[]
    job=planned[0]
    assert job["kind"]=="PROPOSE_GENERATION" and job["spec"]["params"]["generation_only"] is True
    assert job["spec"]["params"]["effective_method"]=="RandomGeneration"
    assert db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==0
    assert db.execute("SELECT COUNT(*) FROM jobs WHERE kind != 'PROPOSE_GENERATION'").fetchone()[0]==0
    # CPU filter cannot acquire a regular GPU training/proposal job; OFF job is leased only once.
    leased=repo.acquire_lease(db,"cpu-worker","2026-10-21T00:00:00Z",30,generation_only=True)
    assert leased["id"]==job["id"]
    assert repo.acquire_lease(db,"other-cpu","2026-10-21T00:00:01Z",30,generation_only=True) is None


def test_generation_progress_persists_once_and_only_real_artifact(db,ctx_factory,tmp_roots):
    out=validate(request(tmp_roots),ctx_factory(devices=[]));assert out.valid,out.errors
    with tx(db):
        run,_=repo.create_run(db,id="off-run",namespace="default",protocol_hash=out.protocol_hash,resolved_protocol=out.resolved,
            requested_protocol=request(tmp_roots),execution_mode="PILOT",idempotency_key="off-key",payload_sha256="off-payload")
        job=engine.plan_run(db,run["id"])[0]
    path=tmp_roots["runs"] / "out/off-run/architectures/model.yaml";path.parent.mkdir(parents=True);path.write_text("nc: 1\n")
    row={"id":"off-run:0","name":"Random candidate 1","arm":"IndependentRandom","family":"yolo11n",
         "architecture_hash":"a"*64,"architecture_yaml":path.read_text(),"architecture_path":str(path),
         "candidate_status":"BUILD_VALID","audit":{"origin":"random_generation","structure":{"status":"PASS"},
         "performance":{"status":"NOT_EVALUATED","fitness":None,"ap":None},"generation":{"index":0,"seed":7}}}
    payload={"generated_candidate":{"row":row,"yaml_hash":"b"*64},"generation_summary":{"seed":7,"generated_count":1,"attempts":1}}
    with tx(db):
        engine.generation_progress(db,job,payload);engine.generation_progress(db,job,payload)
    assert len(repo.list_candidates(db,run["id"]))==1
    assert len(repo.list_artifacts(db,run["id"]))==1
    assert db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==0
    assert repo.get_run(db,run["id"])["budget"]["generation_results"]["7"]["generated_count"]==1
    assert any(e["type"]=="snapshot.required" for e in repo.get_events_after(db,run["id"],0,100))


def test_cancel_recovers_journal_candidate_saved_before_notification(db,ctx_factory,tmp_roots):
    import json
    from mbnas_contracts.schemas.jobs import JobResult
    out=validate(request(tmp_roots),ctx_factory(devices=[]));assert out.valid,out.errors
    with tx(db):
        run,_=repo.create_run(db,id="cancel-run",namespace="default",protocol_hash=out.protocol_hash,resolved_protocol=out.resolved,
            requested_protocol=request(tmp_roots),execution_mode="PILOT",idempotency_key="cancel-key",payload_sha256="cancel-payload")
        job=engine.plan_run(db,run["id"])[0]
    path=tmp_roots["runs"] / "out/cancel-run/architectures/model.yaml";path.parent.mkdir(parents=True);path.write_text("nc: 1\n")
    row={"id":"cancel-run:0","name":"Random candidate 1","arm":"IndependentRandom","family":"yolo11n",
         "architecture_hash":"c"*64,"architecture_yaml":path.read_text(),"architecture_path":str(path),
         "candidate_status":"BUILD_VALID","audit":{"origin":"random_generation","structure":{"status":"PASS"},
         "performance":{"status":"NOT_EVALUATED","fitness":None,"ap":None},"generation":{"index":0,"seed":7}}}
    state={"run_id":"cancel-run","protocol_hash":out.protocol_hash,"seed":7,"attempts":1,"duplicates":0,
           "failure_counts":{},"diagnostics":[],"candidates":[{"row":row,"yaml_hash":"d"*64}]}
    (path.parent / "generation-7.json").write_text(json.dumps(state))
    with tx(db):
        leased=repo.acquire_lease(db,"cpu-worker","2026-10-21T00:00:00Z",30,generation_only=True)
        repo.mark_job_running(db,leased["id"],"cpu-worker")
        repo.update_run_status(db,run["id"],"CANCEL_REQUESTED")
        repo.finish_job(db,leased["id"],"cpu-worker","CANCELLED",error={"reason_code":"RUN_CANCELLED"})
        engine.job_finished(db,repo.get_job(db,leased["id"]),JobResult(job_id=leased["id"],status="CANCELLED",reason_code="RUN_CANCELLED"))
    assert repo.get_run(db,run["id"])["status"]=="CANCELLED"
    assert len(repo.list_candidates(db,run["id"]))==1
    assert len(repo.list_artifacts(db,run["id"]))==1
    assert db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==0


def test_unavailable_structure_runtime_never_returns_internal_source(ctx_factory,tmp_roots):
    from mbnas_contracts.schemas.jobs import JobSpec
    from mbnas_core.search.generation import generate
    from mbnas_contracts.jobs_protocol import JobFailure
    out=validate(request(tmp_roots),ctx_factory(devices=[]));assert out.valid,out.errors
    spec=JobSpec(job_id="safe-failure",run_id="safe-run",kind="PROPOSE_GENERATION",protocol_hash=out.protocol_hash,
                 arm="IndependentRandom",search_seed=7,params={"protocol":out.resolved,"generation_only":True,
                 "architecture_dir":str(tmp_roots["runs"] / "out/safe-run/architectures")})
    def backend_failure(**kwargs):
        raise RuntimeError("Traceback (most recent call last): def secret_search_algorithm(): private source")
    with pytest.raises(JobFailure) as caught:
        generate(spec,lambda _:None,settings=ctx_factory().settings,space_factory=backend_failure)
    failure=caught.value
    assert failure.reason_code=="STRUCTURE_RUNTIME_UNAVAILABLE"
    assert "Traceback" not in failure.message and "secret_search_algorithm" not in failure.message
    assert "secret_search_algorithm" not in str(failure.outputs)
    assert failure.outputs["generation_summary"]["structure_status"]=="UNVERIFIED"
    assert failure.outputs["candidates"]==[]


# -- WP-S3 additions: DD-1 (no second build, explicit INVALID), DD-2, DD-6, G6 -----------------------------------
def test_off_validation_resolves_per_seed_scope_and_contract_structure_status(ctx_factory,tmp_roots):
    out=validate(request(tmp_roots),ctx_factory(devices=[]));assert out.valid,out.errors
    assert out.resolved["console"]["search"]["duplicate_scope"]=="per_seed"
    assert out.capabilities["generation"]["structure_status"]=="UNVERIFIED"  # G6: contract enum value, never NOT_CHECKED
    bad=request(tmp_roots);bad["console"]["search"]["duplicate_scope"]="global"
    r=validate(bad,ctx_factory(devices=[]))
    assert not r.valid and any(e["field"]=="console.search.duplicate_scope" for e in r.errors)


class _Space:
    """Stub model space (no runtime claim); proposals carry a successful or failed BuildReport of the stated size."""
    variant="unit_stub";fallback_reason=None;nc=1
    slots={"yolo11n":[]};native_head={"yolo11n":[[[-1],1,"Detect",["nc"]]]};templates={}

    def __init__(self,imgsz=32):
        self.imgsz=imgsz

    def describe(self):
        return {"version":"unit","variant":self.variant,"space_hash":"unit-space","prior_hash":"unit-prior"}

    def sample_prior(self,rng,method):
        from mbnas_model_runtime.genotype import BuildReport,Genotype,LayerInfo
        from mbnas_core.search.space import Proposal,RowOrigin
        n=rng.randrange(10_000_000)
        g=Genotype.from_yaml_dict({"nc":1,"backbone":[[-1,1,"Conv",[8+n%50,3,2]]],"head":self.native_head["yolo11n"]},
                                  family="yolo11n",template_id="unit")
        rep=BuildReport(ok=True,error=None,imgsz=self.imgsz,architecture_hash=f"stub-{n}",signature_hash=f"sig-{n}",params=n,
            layers=[LayerInfo(0,"Conv",-1,3,8,32,32,16,16,2.0,n),LayerInfo(1,"Detect",[-1],8,None,16,16,None,None,None,n)],
            yaml_hash=g.yaml_hash(),built_audit={"built":{"backbone_family":"cnn","label":"unit stub"}})
        return Proposal(method=method,operation="random",genotype=g,report=rep,
                        origins=[RowOrigin(kind="random",source="unit",operation="random") for _ in g.rows()])


def _job_spec(settings,directory,*,count=2,attempts=6,seed=5):
    from pathlib import Path
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_contracts.schemas.jobs import JobSpec
    protocol={"console":{"search":{"evaluation_enabled":False,"candidate_count":count,"max_attempts":attempts,
              "allow_duplicates":False,"nc":1,"input_size":32,"families":["yolo11n"],"arms":["GA"],"requested_method":["GA"]}}}
    return JobSpec(job_id="job-dd1",run_id="run-dd1",kind="PROPOSE_GENERATION",protocol_hash=protocol_hash(protocol),
                   arm="RandomGeneration",search_seed=seed,params={"protocol":protocol,"generation_only":True,
                   "architecture_dir":str(Path(settings.output_root)/directory)})


def _proposal(imgsz=32,ok=True):
    import random
    prop=_Space(imgsz).sample_prior(random.Random(3),"random")
    if not ok:
        prop.report.ok=False;prop.report.error="forward failed at row 0: RuntimeError: shape mismatch";prop.report.error_row=0
    return prop


def _candidate_for(settings,prop,space):
    from mbnas_core.search.handlers import _candidate
    from mbnas_core.search.streams import training_seed_policy
    spec=_job_spec(settings,"cand")
    return _candidate(spec,prop,space,space.describe(),0,0,training_seed_policy(spec.params["protocol"],5),None)


def test_on_path_candidate_reuses_proposal_build_report_when_input_size_matches(settings,monkeypatch):
    from mbnas_core.search import structure
    builds=[]
    monkeypatch.setattr(structure,"inspect_genotype",lambda *a,**k:builds.append(a) or pytest.fail("second CPU build"))
    cand=_candidate_for(settings,_proposal(32),_Space(32))
    assert builds==[] and cand["row"]["candidate_status"]=="BUILD_VALID"
    assert cand["row"]["audit"]["structure"]["status"]=="PASS" and "reason_code" not in cand["row"]["audit"]["structure"]


def test_on_path_candidate_rebuilds_only_when_input_size_differs(settings,monkeypatch):
    from mbnas_core.search import structure
    prop=_proposal(32);builds=[]
    def rebuild(genotype,imgsz,**kw):
        builds.append(imgsz)
        import dataclasses
        return dataclasses.replace(prop.report,imgsz=imgsz)
    monkeypatch.setattr(structure,"inspect_genotype",rebuild)
    cand=_candidate_for(settings,prop,_Space(64))  # the space asks for 64 but the proposal was built at 32
    assert builds==[64] and cand["row"]["candidate_status"]=="BUILD_VALID"


def test_on_path_invalid_candidate_records_structure_failed_with_diagnostics(settings,monkeypatch):
    from mbnas_core.search import structure
    monkeypatch.setattr(structure,"inspect_genotype",lambda *a,**k:pytest.fail("a failed proposal report must be reused"))
    cand=_candidate_for(settings,_proposal(32,ok=False),_Space(32))
    row=cand["row"];st=row["audit"]["structure"]
    assert row["candidate_status"]=="INVALID" and st["status"]=="FAIL"
    assert st["reason_code"]=="STRUCTURE_FAILED"
    assert st["diagnostics"] and all("code" in d and "cause" in d for d in st["diagnostics"])


def test_generation_only_checks_each_draw_once_and_records_cpu_execution(settings,monkeypatch):
    from mbnas_core.search import structure
    from mbnas_core.search.generation import generate
    calls=[]
    real=structure.check_structure
    def counting(*a,**k):
        calls.append(k.get("report") is not None)
        return real(*a,**k)
    monkeypatch.setattr(structure,"check_structure",counting)
    monkeypatch.setattr(structure,"inspect_genotype",lambda *a,**k:pytest.fail("proposal build must be reused"))
    monkeypatch.setenv("MBNAS_WORKER_ID","worker-under-test:7")
    out=generate(_job_spec(settings,"once",count=2),lambda m:None,settings=settings,space_factory=lambda **kw:_Space(32))
    assert len(out["candidates"])==2 and len(calls)==out["generation_summary"]["attempts"]==2 and all(calls)
    for c in out["candidates"]:
        g=c["row"]["audit"]["generation"]
        assert g["execution"]=="CPU" and g["worker_id"]=="worker-under-test:7" and g["duplicate_scope"]=="per_seed"
    assert out["generation_summary"]["duplicate_scope"]=="per_seed"


def test_generation_worker_id_is_null_without_a_worker(settings):
    from mbnas_core.search.generation import generate
    out=generate(_job_spec(settings,"noworker",count=1),lambda m:None,settings=settings,space_factory=lambda **kw:_Space(32))
    assert out["candidates"][0]["row"]["audit"]["generation"]["worker_id"] is None
    assert out["candidates"][0]["row"]["audit"]["generation"]["execution"]=="CPU"


def test_duplicates_are_suppressed_per_seed_only(settings):
    """DD-2: two seeds with the same RNG outcome both keep their candidate; no cross-seed journal is consulted."""
    from mbnas_core.search.generation import generate
    class Same(_Space):
        def sample_prior(self,rng,method):
            import random
            return super().sample_prior(random.Random(11),method)  # identical architecture on every draw and seed
    a=generate(_job_spec(settings,"scope-a",count=1,seed=1),lambda m:None,settings=settings,space_factory=lambda **kw:Same(32))
    b=generate(_job_spec(settings,"scope-b",count=1,seed=2),lambda m:None,settings=settings,space_factory=lambda **kw:Same(32))
    assert a["candidates"][0]["row"]["architecture_hash"]==b["candidates"][0]["row"]["architecture_hash"]
    assert len(a["candidates"])==len(b["candidates"])==1
