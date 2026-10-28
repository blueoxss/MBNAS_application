"""Independent adversarial checks of the public structure boundary (no model-runtime claims)."""
from __future__ import annotations

import pytest

from mbnas_model_runtime.genotype import BuildReport, Genotype
from mbnas_core.search import structure


def genotype(**changes):
    config = {"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 2, "C2f", [8]],
                                       [[-1, 0], 1, "Concat", [1]]],
              "head": [[[-1], 1, "Detect", ["nc"]]]}
    config.update(changes)
    return Genotype.from_yaml_dict(config, family="test", template_id="test:native")


def refuse_runtime(*args, **kwargs):
    raise AssertionError("invalid declarations must fail before CPU execution")


@pytest.mark.parametrize("scales", ["bad", [1, 2, 3], 42, {"n": "bad"}, {"n": [0.5, 0.25]}])
def test_malformed_scales_fail_with_diagnostic(monkeypatch, scales):
    monkeypatch.setattr(structure, "inspect_genotype", refuse_runtime)
    result = structure.check_structure(genotype(scales=scales, scale="n"), input_size=32)
    assert result["status"] == "FAIL"
    assert "SCALE_INVALID" in {d["code"] for d in result["diagnostics"]}


def test_activation_expression_never_reaches_parser(monkeypatch):
    monkeypatch.setattr(structure, "inspect_genotype", refuse_runtime)
    result = structure.check_structure(genotype(activation="__import__('os').system('touch /tmp/forbidden')"), input_size=32)
    assert result["status"] == "FAIL"
    assert "ACTIVATION_REFUSED" in {d["code"] for d in result["diagnostics"]}
    row_check = {c["name"]: c for c in result["checks"]}["yaml_rows"]
    assert row_check["status"] == "FAIL" and row_check["evidence_level"] == "declared"  # executed-check table agrees with the diagnostic
    assert {c["name"]: c["status"] for c in result["checks"]}["module_support"] == "NOT_CHECKED"


def test_unsafe_module_and_future_reference_never_build(monkeypatch):
    monkeypatch.setattr(structure, "inspect_genotype", refuse_runtime)
    result = structure.check_structure(genotype(backbone=[[12, 1, "x.__import__('os')", [8]]]), input_size=32)
    assert result["status"] == "FAIL"
    assert {"BLOCK_NAME_INVALID", "FROM_OUT_OF_RANGE"} <= {d["code"] for d in result["diagnostics"]}
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["yaml_rows"]["status"] == "FAIL" and by_name["yaml_rows"]["evidence_level"] == "declared"
    assert by_name["references"]["status"] == "FAIL"
    assert by_name["module_support"]["status"] == "NOT_CHECKED"


def test_bad_module_name_alone_fails_yaml_rows_check(monkeypatch):
    monkeypatch.setattr(structure, "inspect_genotype", refuse_runtime)
    result = structure.check_structure(genotype(backbone=[[-1, 1, "x.__import__('os')", [8]]]), input_size=32)
    assert result["status"] == "FAIL"
    assert {d["code"] for d in result["diagnostics"]} == {"BLOCK_NAME_INVALID"}
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["yaml_rows"]["status"] == "FAIL" and by_name["yaml_rows"]["evidence_level"] == "declared"
    assert by_name["references"]["status"] == "PASS"


def test_valid_rows_keep_yaml_rows_pass_when_only_other_rules_fail(monkeypatch):
    monkeypatch.setattr(structure, "inspect_genotype", refuse_runtime)
    result = structure.check_structure(genotype(), input_size=33)  # request defect only: the YAML rows themselves are fine
    assert result["status"] == "FAIL"
    assert {c["name"]: c["status"] for c in result["checks"]}["yaml_rows"] == "PASS"


def test_runtime_absence_is_not_pass(monkeypatch):
    def missing(*args, **kwargs):
        raise ModuleNotFoundError("No module named torch")
    monkeypatch.setattr(structure, "inspect_genotype", missing)
    result = structure.check_structure(genotype(), input_size=32)
    assert result["status"] == "UNVERIFIED"
    assert result["architecture_hash"] is None
    assert result["parameters"] is None
    assert all(check["status"] != "PASS" for check in result["checks"] if check["name"] in {"cpu_build", "reference_forward"})
    assert result["observation"] is None  # nothing ran, so no environment is claimed
    assert all(check["evidence_level"] is None for check in result["checks"] if check["status"] == "NOT_CHECKED")
    assert result["blocks"][1]["effective"]["repeats"] is None


def test_parser_failure_diagnostic_omits_source_path_and_traceback(monkeypatch):
    monkeypatch.setattr(structure, "inspect_genotype", lambda *a, **k: BuildReport(
        ok=False, imgsz=32, error="shape mismatch /root/private/search/algorithm.py Traceback (most recent call last) SECRET", error_row=2))
    result = structure.check_structure(genotype(), input_size=32)
    assert result["status"] == "FAIL"
    diagnostic = result["diagnostics"][0]
    assert diagnostic["position"] == 2
    assert "/root/private" not in diagnostic["cause"]
    assert "Traceback" not in diagnostic["cause"]
    assert "SECRET" not in diagnostic["cause"]
    assert diagnostic["code"] in {"SHAPE_MISMATCH", "STRUCTURE_BUILD_FAILED", "BLOCK_UNSUPPORTED", "HEAD_INVALID"}
    assert {c["name"]: c["status"] for c in result["checks"]}["cpu_build"] == "FAIL"  # unknown failure origin: v1 mapping


def test_real_from_edges_are_not_replaced_by_sequential_gene_order():
    result = structure.describe_genotype(genotype())
    assert [(e["source"], e["target"], e["from_ref"]) for e in result["edges"]] == [
        (-1, 0, -1), (0, 1, -1), (1, 2, -1), (0, 2, 0), (2, 3, -1)]
    assert result["blocks"][1]["repeats"] == 2
    assert result["blocks"][2]["yaml_path"] == "backbone[2]"
    assert result["blocks"][3]["section"] == "head"
    assert result["lineage_status"] == "NOT_APPLICABLE"
    assert result["architecture_hash"] is None
    # declared-only document (mbnas-structure/2): nothing observed, nothing derived is presented as observed
    assert result["schema"] == "mbnas-structure/2" and result["observation"] is None
    assert result["edges_unobserved"] == [] and all(e["connection_source"] == "yaml_from" for e in result["edges"])
    assert result["blocks"][1]["effective"]["repeats"] is None
    assert result["blocks"][1]["effective"]["repeats_scaled"] == 2  # no scale declared -> multiplier 1.0
    assert result["blocks"][1]["repeats_detail"] == {"declared": 2, "scaled": 2, "observed": None}
    assert result["blocks"][1]["module_class"] is None and result["blocks"][1]["internal_layers"] == []
    assert result["flops_status"] == "NOT_MEASURED"


def test_merge_argument_is_not_declared_channel_count():
    result = structure.describe_genotype(genotype())
    assert result["blocks"][2]["declared"]["channels"] is None


def test_structure_diff_does_not_invent_ancestry():
    left = structure.describe_genotype(genotype())
    right = structure.describe_genotype(genotype(backbone=[[-1, 1, "Conv", [16, 3, 2]], [-1, 2, "C2f", [8]],
                                                           [[-1, 1], 1, "Concat", [1]]]))
    result = structure.compare_structures(left, right)
    assert result["comparison"] == "structure"
    assert result["gene_ancestry_inferred"] is False
    assert [(d["position"], d["fields"]) for d in result["changed"]] == [(0, ["args"]), (2, ["from"])]
    assert result["edges_added"]
    assert result["edges_removed"]
