"""Independent journal/retry tests: real generation/proposer code, explicitly stubbed structure/model space."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_contracts.schemas.jobs import JobKind, JobSpec
from mbnas_core.search.generation import generate
from mbnas_model_runtime.genotype import BuildReport, Genotype, LayerInfo
from mbnas_core.search.space import Proposal, RowOrigin
from mbnas_contracts.jobs_protocol import JobFailure, JobPaused


class StubSpace:
    """No model-runtime claim; sample values depend only on the actual random RNG passed by generation."""
    variant = "unit_stub"
    fallback_reason = None
    imgsz = 32
    nc = 1
    slots = {"yolo11n": []}
    native_head = {"yolo11n": [[[-1], 1, "Detect", ["nc"]]]}
    templates = {}

    def __init__(self, constant=False):
        self.constant = constant
        self.calls = 0

    def describe(self):
        return {"version": "unit", "variant": self.variant, "space_hash": "unit-space", "prior_hash": "unit-prior"}

    def sample_prior(self, rng, method):
        self.calls += 1
        n = 1 if self.constant else rng.randrange(10_000_000)
        g = Genotype.from_yaml_dict({"nc": 1, "backbone": [[-1, 1, "Conv", [8+n, 3, 2]]],
                                    "head": self.native_head["yolo11n"]}, family="yolo11n", template_id="unit")
        rep = BuildReport(ok=True, error=None, imgsz=32, architecture_hash=f"stub-{n}", signature_hash=f"sig-{n}",
                          params=n, layers=[LayerInfo(0,"Conv",-1,3,8,32,32,16,16,2.0,n), LayerInfo(1,"Detect",[-1],8,None,16,16,None,None,None,n)], yaml_hash=g.yaml_hash(), built_audit={"built": {"backbone_family": "cnn", "label": "unit stub"}})
        return Proposal(method=method, operation="random", genotype=g, report=rep,
                        origins=[RowOrigin(kind="random", source="unit", operation="random") for _ in g.rows()])


def spec(settings, directory: str, *, seed=91, count=3, attempts=10, duplicates=False):
    protocol = {"console": {"search": {"evaluation_enabled": False, "candidate_count": count,
                    "max_attempts": attempts, "allow_duplicates": duplicates, "nc": 1, "input_size": 32,
                    "families": ["yolo11n"], "arms": ["GA"], "requested_method": ["GA"]}}}
    return JobSpec(job_id="job-generated", run_id="run-generated", kind=JobKind.PROPOSE_GENERATION,
                   protocol_hash=protocol_hash(protocol), arm="RandomGeneration", search_seed=seed,
                   params={"protocol": protocol, "generation_only": True,
                           "architecture_dir": str(Path(settings.output_root)/directory)})


def pass_structure(*args, **kwargs):
    return {"status": "PASS", "diagnostics": [], "test_boundary": "stub"}


def hashes(result):
    return [c["row"]["architecture_hash"] for c in result["candidates"]]


def test_saved_candidate_survives_notification_crash_without_resampling(settings):
    request = spec(settings, "crash")
    first = StubSpace()
    def crash(msg):
        if msg.payload.get("generated_candidate"):
            raise RuntimeError("simulated connection loss after durable journal write")
    with pytest.raises(RuntimeError, match="connection loss"):
        generate(request, crash, settings=settings, space_factory=lambda **kw: first, structure_check=pass_structure)
    assert first.calls == 1
    journal = Path(request.params["architecture_dir"])/"generation-91.json"
    committed = json.loads(journal.read_text())
    assert len(committed["candidates"]) == 1
    second = StubSpace()
    received = []
    resumed = generate(request, received.append, settings=settings, space_factory=lambda **kw: second, structure_check=pass_structure)
    assert second.calls == 2
    assert resumed["candidates"][0] == committed["candidates"][0]
    assert len({c["row"]["id"] for c in resumed["candidates"]}) == 3
    uninterrupted = generate(spec(settings, "clean"), lambda m: None, settings=settings,
                             space_factory=lambda **kw: StubSpace(), structure_check=pass_structure)
    assert hashes(resumed) == hashes(uninterrupted)
    assert resumed["generation_summary"]["attempts"] == 3
    assert all(c["evaluation_status"] == "NOT_EVALUATED" for c in resumed["candidates"])
    assert all(all(v is None for k,v in c["row"]["audit"]["performance"].items() if k != "status") for c in resumed["candidates"])


def test_completed_retry_replays_without_instantiating_runtime(settings):
    request=spec(settings, "retry", count=1)
    initial=generate(request, lambda m: None, settings=settings, space_factory=lambda **kw: StubSpace(), structure_check=pass_structure)
    def forbidden(**kwargs):
        raise AssertionError("completed retry must not instantiate the runtime or sample again")
    replay=generate(request, lambda m: None, settings=settings, space_factory=forbidden, structure_check=pass_structure)
    assert replay["candidates"] == initial["candidates"]
    assert replay["generation_summary"]["attempts"] == 1


def test_duplicate_cap_returns_durable_partial_candidates(settings):
    request=spec(settings, "duplicates", count=3, attempts=4)
    model_space=StubSpace(constant=True)
    result=generate(request, lambda m: None, settings=settings, space_factory=lambda **kw:model_space, structure_check=pass_structure)
    assert len(result["candidates"]) == 1
    assert result["generation_summary"]["status"] == "PARTIAL"
    assert result["generation_summary"]["attempts"] == 4
    assert result["generation_summary"]["duplicates"] == 3
    again=generate(request,lambda m:None,settings=settings,space_factory=lambda **kw:model_space,structure_check=pass_structure)
    assert model_space.calls == 4
    assert hashes(again) == hashes(result)


@pytest.mark.parametrize("state", ["FAIL", "UNVERIFIED"])
def test_failed_or_unverified_checks_never_accept_candidate(settings,state):
    request=spec(settings,state,count=2,attempts=3)
    result=generate(request,lambda m:None,settings=settings,space_factory=lambda **kw:StubSpace(),
                    structure_check=lambda *a,**k:{"status":state,"diagnostics":[{"code":"UNIT_DIAGNOSTIC"}]})
    assert result["candidates"] == []
    assert result["generation_summary"]["failure_counts"] == {"STRUCTURE_UNVERIFIED" if state=="UNVERIFIED" else "STRUCTURE_FAILED":3}
    assert result["generation_summary"]["attempts"] == 3


def test_pause_before_next_draw_preserves_completed_candidate(settings):
    request=spec(settings,"pause",count=3)
    stop=Path(settings.output_root)/"stop.json"
    request.params["stop_request_path"]=str(stop)
    def pause_after_first(msg):
        if msg.payload.get("generated_candidate"):
            stop.write_text('{"action":"pause"}')
    with pytest.raises(JobPaused) as paused:
        generate(request,pause_after_first,settings=settings,space_factory=lambda **kw:StubSpace(),structure_check=pass_structure)
    assert len(paused.value.outputs["candidates"])==1
    stop.unlink()
    result=generate(request,lambda m:None,settings=settings,space_factory=lambda **kw:StubSpace(),structure_check=pass_structure)
    assert len(result["candidates"])==3
    assert result["generation_summary"]["attempts"]==3


def test_unavailable_runtime_is_explicit_failure_with_no_passes(settings):
    request=spec(settings,"missing")
    def unavailable(**kwargs):
        raise ModuleNotFoundError("No module named runtime")
    with pytest.raises(JobFailure) as err:
        generate(request,lambda m:None,settings=settings,space_factory=unavailable)
    assert err.value.reason_code=="STRUCTURE_RUNTIME_UNAVAILABLE"
    assert err.value.outputs["candidates"]==[]
    assert err.value.outputs["generation_summary"]["structure_status"]=="UNVERIFIED"
