"""Real CPU structural proof at a short reference input; no training or datasets."""
from __future__ import annotations

import copy

import pytest

from mbnas_core.console.model_services import describe_candidate, run_structure_check
from mbnas_model_runtime.genotype import Genotype, inspect_genotype
from mbnas_core.search import structure


@pytest.fixture
def branch_genotype():
    return Genotype.from_yaml_dict({
        "nc": 2, "depth_multiple": 0.5, "width_multiple": 0.5,
        "backbone": [[-1, 1, "Conv", [32, 3, 2]], [-1, 2, "C2f", [64]]],
        "head": [[[0, 1], 1, "Concat", [1]], [-1, 1, "Conv", [64, 3, 2]], [[3], 1, "Detect", ["nc"]]],
    }, family="cpu-branch", template_id="cpu-branch:native")


def test_real_cpu_build_forward_and_row_expansion(branch_genotype):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        result = structure.check_structure(branch_genotype, input_size=64, class_count=2)
    finally:
        torch.set_num_threads(old)
    assert result["status"] == "PASS", result["diagnostics"]
    assert result["parameters"] > 0
    assert result["flops"] is None  # the structural path did not request a benchmark/FLOPs calculation
    assert result["architecture_hash"]
    assert result["signature_hash"]
    assert result["blocks"][0]["declared"]["channels"] == 32
    assert result["blocks"][0]["effective"]["c_out"] == 16  # actual parser width scaling
    assert result["blocks"][1]["repeats"] == 2
    assert result["blocks"][1]["effective"]["repeats"] == 1  # depth-scaled request
    assert any(layer["path"] == "1.m.0" for layer in result["blocks"][1]["internal_layers"])
    assert result["blocks"][2]["effective"]["c_out"] == 48
    assert result["blocks"][2]["declared"]["channels"] is None
    # v1 edge keys are unchanged; mbnas-structure/2 only adds ``connection_source`` to every edge
    assert [{k: e[k] for k in ("source", "target", "from_ref")} for e in result["edges"][2:4]] == [
        {"source": 0, "target": 2, "from_ref": 0}, {"source": 1, "target": 2, "from_ref": 1}]
    assert all(e["connection_source"] == "yaml_from" for e in result["edges"])
    # mbnas-structure/2: observed repeats replace the formula (kept beside it), explicit check measures no FLOPs
    assert result["schema"] == "mbnas-structure/2"
    assert result["blocks"][1]["repeats_detail"] == {"declared": 2, "scaled": 1, "observed": 1}
    assert result["blocks"][1]["effective"]["repeats_scaled"] == 1
    assert result["flops_status"] == "NOT_MEASURED" and result["flops_source"] is None
    assert result["observation"]["device"] == "cpu" and result["observation"]["imgsz"] == 64


def test_real_cpu_merge_shape_failure_is_not_pass(branch_genotype):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    rows = branch_genotype.rows()
    rows[1] = [-1, 1, "Conv", [64, 3, 2]]
    try:
        result = structure.check_structure(branch_genotype.with_rows(rows), input_size=64, class_count=2)
    finally:
        torch.set_num_threads(old)
    assert result["status"] == "FAIL"
    assert result["parameters"] is None
    assert result["diagnostics"][0]["code"] == "SHAPE_MISMATCH"
    assert result["diagnostics"][0]["position"] == 2
    # the same failure is also attributed to the rule it violates (mbnas-structure/2), at the merge row
    assert any(d["code"] == "MERGE_INPUT_MISMATCH" and d["position"] == 2 for d in result["diagnostics"])
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["merge_conditions"]["status"] == "FAIL"
    # the failing forward is the constructor stride probe (stage build): the reference-size forward never ran
    assert by_name["cpu_build"]["status"] == "FAIL"
    assert by_name["reference_forward"]["status"] == "NOT_CHECKED" and by_name["reference_forward"]["evidence_level"] is None
    assert by_name["module_support"]["rows_checked"] == len(branch_genotype.rows())
    assert by_name["merge_conditions"]["rows_checked"] == 1
    # the raw forward diagnostic carries the same yaml path as the attributed rule diagnostic
    assert result["diagnostics"][0]["path"] == "head[0]"  # row 2 = first head row (2 backbone rows)


def test_report_reuse_is_yaml_bound(branch_genotype, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        report = inspect_genotype(branch_genotype, 64, count_flops=False)
    finally:
        torch.set_num_threads(old)
    def forbidden(*args, **kwargs):
        raise AssertionError("matching report must be reused")
    monkeypatch.setattr(structure, "inspect_genotype", forbidden)
    assert structure.check_structure(branch_genotype, input_size=64, report=report)["status"] == "PASS"
    changed = copy.deepcopy(branch_genotype.yaml)
    changed["nc"] = 3
    other = Genotype.from_yaml_dict(changed, family=branch_genotype.family, template_id=branch_genotype.template_id)
    # A report from a different YAML cannot validate this configuration.
    assert structure.check_structure(other, input_size=64, report=report)["status"] == "UNVERIFIED"


def test_get_model_detail_never_executes_runtime(branch_genotype, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("GET detail must not run inference")
    monkeypatch.setattr(structure, "inspect_genotype", forbidden)
    candidate = {"id": "candidate-1", "name": "baseline", "arm": "Baseline", "architecture_yaml": branch_genotype.yaml_text(),
                 "architecture_hash": None, "lineage": {}, "parents": [], "blocks": [], "audit": {}}
    result = describe_candidate(candidate, {"id": "run-1", "resolved_protocol": {}})
    assert result["status"] == "UNVERIFIED"
    assert len(result["blocks"]) == 5
    assert result["lineage_status"] == "NOT_APPLICABLE"
    assert result["lineage"] is None
    assert result["parameters"] is None


def test_saved_configuration_proof_must_match_hashes(branch_genotype):
    saved = {**structure.describe_genotype(branch_genotype), "status": "PASS", "parameters": 12,
             "architecture_hash": "sha256:expected"}
    candidate = {"id": "candidate-1", "name": "saved", "arm": "GA", "architecture_yaml": branch_genotype.yaml_text(),
                 "architecture_hash": "sha256:expected", "lineage": {}, "parents": [], "blocks": [], "audit": {"structure": saved}}
    run = {"id": "run-1", "resolved_protocol": {}}
    assert describe_candidate(candidate, run)["status"] == "PASS"
    candidate["architecture_hash"] = "sha256:other"
    assert describe_candidate(candidate, run)["status"] == "UNVERIFIED"
    candidate["architecture_yaml"] = "nc: ["
    assert describe_candidate(candidate, run)["status"] == "FAIL"


def test_generation_recheck_uses_its_frozen_structure_input(branch_genotype, monkeypatch):
    from mbnas_core.console import model_services

    called = []
    def check(genotype, *, input_size, class_count):
        called.append(input_size)
        return {"status": "UNVERIFIED", "input_size": input_size}
    monkeypatch.setattr(model_services, "check_structure", check)
    candidate = {"id": "model-1", "architecture_yaml": branch_genotype.yaml_text()}
    run = {"id": "generate-1", "resolved_protocol": {"console": {"search": {"evaluation_enabled": False,
                                                                                     "input_size": 64}},
                                                        "training": {"requested_input_size": [640, 640]}}}
    assert run_structure_check(candidate, run)["input_size"] == 64
    assert called == [64]


def test_saved_structure_merges_only_actual_matching_row_provenance(branch_genotype):
    view = structure.describe_genotype(branch_genotype)
    saved = {**view, "status": "PASS", "architecture_hash": "sha256:expected"}
    row = branch_genotype.rows()[0]
    old = {"position": 0, "name": row[2], "source": "yolo11n:native", "source_kind": "random",
           "operation": "inherit", "parent_block_id": "yolo11n:native#0",
           "params": dict(zip(("from", "repeats", "module", "args"), row))}
    candidate = {"id": "c1", "name": "random", "arm": "IndependentRandom", "architecture_yaml": branch_genotype.yaml_text(),
                 "architecture_hash": "sha256:expected", "lineage": {"operation": "random"}, "parents": [],
                 "blocks": [old], "audit": {"structure": saved}}
    result = describe_candidate(candidate, {"id": "run-1"})
    assert result["blocks"][0]["source"] == "yolo11n:native"
    assert result["blocks"][0]["operation"] == "inherit"
    assert "source" not in result["blocks"][1]
    assert result["lineage_status"] == "RECORDED"
    old["params"]["args"] = [999, 3, 2]
    assert "source" not in describe_candidate(candidate, {"id": "run-1"})["blocks"][0]
