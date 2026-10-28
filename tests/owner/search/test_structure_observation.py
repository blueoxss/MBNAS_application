"""WP-S1 (mbnas-structure/2): observed-shape rules, per-block internal layers, wrappers, repeats and environment.

Real CPU cases (tiny models at imgsz 64/128, no data, no GPU, no training) except where a docstring says
"rule-level": stock ultralytics registers no ``Add`` module and always builds a head with ``nl == len(from)``, so those
two negative cases feed the rule a hand-written observation through the public ``report=`` argument.
"""
from __future__ import annotations

import copy
import os

import pytest

pytest.importorskip("torch")
pytest.importorskip("ultralytics")

from mbnas_core.search import structure
from mbnas_model_runtime.genotype import BuildReport, Genotype, LayerInfo, inspect_genotype
from mbnas_core.search.structure import check_structure, describe_genotype

TINY_MIXED = {
    "nc": 1, "scales": {"t": [0.5, 0.5, 256]}, "scale": "t",
    "backbone": [
        [-1, 1, "Conv", [32, 3, 2]], [-1, 1, "Conv", [64, 3, 2]], [-1, 2, "C2f", [64, True]],
        [-1, 1, "NASPatchMerge", [128, 2]], [-1, 4, "NASWindowTransformer", [128, 4, 4, 2.0]],
        [-1, 1, "Conv", [256, 3, 2]], [-1, 2, "NASGlobalTransformer", [256, 4, 1024, 2.0]],
        [-1, 1, "SPPF", [256, 5]], [-1, 3, "Conv", [256, 3, 1]]],
    "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1, 4], 1, "Concat", [1]], [-1, 2, "C3k2", [128, False]],
             [[11, 8], 1, "Detect", ["nc"]]],
}


def make(config: dict) -> Genotype:
    return Genotype.from_yaml_dict(config, family="obs-test", template_id="obs-test:native")


def three_scale(**changes) -> Genotype:
    """Conv x3 (strides 2/4/8) -> Upsample (back to stride 2... concat with row 0) -> Detect on rows 3 and 1."""
    config = {"nc": 2, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [16, 3, 2]]],
              "head": [[-1, 1, "nn.Upsample", [None, 4, "nearest"]], [[0, 3], 1, "Concat", [1]],
                       [[4, 1], 1, "Detect", ["nc"]]]}
    config.update(changes)
    return make(config)


def checks(result: dict) -> dict[str, dict]:
    return {c["name"]: c for c in result["checks"]}


# -- schema / identity ------------------------------------------------------------------------------------------------
def test_schema_v2_top_level_and_block_identity_fields():
    result = check_structure(three_scale(), input_size=64, class_count=2)
    assert result["status"] == "PASS", result["diagnostics"]
    assert result["schema"] == "mbnas-structure/2"
    assert (result["backend"], result["representation"]) == ("ultralytics", "yaml_genes")
    assert result["edges_unobserved"] == []
    assert all(e["connection_source"] == "yaml_from" for e in result["edges"])
    assert [(e["source"], e["target"], e["from_ref"]) for e in result["edges"]][:3] == [(-1, 0, -1), (0, 1, -1), (1, 2, -1)]
    tag = result["yaml_hash"][7:19]
    for i, block in enumerate(result["blocks"]):
        assert block["id"] == block["block_id"] == f"block-{i:04d}"  # v1 identity untouched
        assert block["stable_id"] == f"ul:{block['yaml_path']}@{tag}"
        assert block["native_path"] == f"model.{i}"
        assert block["connection_source"] == "yaml_from"
        assert block["checks_local"] == []


def test_section_basis_follows_dd15():
    result = check_structure(three_scale(), input_size=64, class_count=2)
    by = {b["yaml_path"]: (b["section"], b["section_basis"]) for b in result["blocks"]}
    assert by["backbone[0]"] == ("backbone", "yaml:backbone")
    assert by["head[0]"] == ("neck", "derived:non-detection head row -> neck")
    assert by["head[1]"] == ("neck", "derived:non-detection head row -> neck")
    assert by["head[2]"] == ("head", "yaml:head+detection_module")
    assert structure.describe_genotype(three_scale())["blocks"][5]["section_basis"] == "yaml:head+detection_module"


# -- U5 list-from inputs / U4 detect head --------------------------------------------------------------------------------
def test_list_from_inputs_are_recorded_per_source_with_shapes():
    result = check_structure(three_scale(), input_size=64, class_count=2)
    assert result["status"] == "PASS", result["diagnostics"]
    out = {b["position"]: b["effective"] for b in result["blocks"]}
    concat = out[4]["inputs"]
    assert [i["source"] for i in concat] == [0, 3]
    assert [(i["c"], i["h"], i["w"]) for i in concat] == [(8, 32, 32), (16, 32, 32)]
    assert (out[4]["c_in"], out[4]["c_out"]) == (24, 24)  # v1 semantics kept: c_in is the sum over sources
    assert (out[4]["h_in"], out[4]["w_in"]) == (32, 32)
    head = out[5]["inputs"]
    assert [i["source"] for i in head] == [4, 1]
    assert [(i["c"], i["h"], i["w"]) for i in head] == [(24, 32, 32), (16, 16, 16)]
    # every recorded input equals the source block's recorded output
    for block in result["blocks"]:
        for item in block["effective"]["inputs"]:
            if item["source"] >= 0:
                src = out[item["source"]]
                assert (item["c"], item["h"], item["w"]) == (src["c_out"], src["h_out"], src["w_out"])
    assert out[0]["inputs"] == [{"source": -1, "c": 3, "h": 64, "w": 64}]


def test_detect_row_records_outputs_head_strides_nl_end2end():
    result = check_structure(three_scale(), input_size=64, class_count=2)
    detect = result["blocks"][5]["effective"]
    assert detect["c_out"] is None and detect["stride"] is None  # v1 values unchanged for the multi-output head
    assert detect["head_strides"] == [2.0, 4.0]
    assert detect["nl"] == 2
    assert detect["end2end"] is False
    assert detect["nc"] == 2
    assert detect["outputs"] and all(isinstance(s, list) and s for s in detect["outputs"])
    assert [64 // s for s in (2, 4)] == [d[1] for d in detect["outputs"][-2:]]  # the per-scale feature maps (c, h, w)
    assert "head_strides" not in result["blocks"][0]["effective"]


def test_detect_with_repeats_is_head_input_invalid_in_real_forward():
    config = three_scale().yaml
    config["head"][2] = [[4, 1], 2, "Detect", ["nc"]]
    result = check_structure(make(config), input_size=64, class_count=2)
    assert result["status"] == "FAIL"
    codes = [d["code"] for d in result["diagnostics"]]
    assert "HEAD_INPUT_INVALID" in codes
    head = next(d for d in result["diagnostics"] if d["code"] == "HEAD_INPUT_INVALID")
    assert head["position"] == 5 and head["path"] == "head[2]" and head["evidence_level"] == "forward"
    assert checks(result)["head_connection"]["status"] == "FAIL"
    assert "HEAD_INPUT_INVALID" in {c["code"] for c in result["blocks"][5]["checks_local"]}
    assert result["parameters"] is None and result["architecture_hash"] is None


@pytest.mark.parametrize("sources, fragment", [([[4, 4]], "not strictly increasing"), ([[1, 4]], "not strictly increasing")])
def test_head_sources_must_have_strictly_increasing_strides(sources, fragment):
    config = three_scale().yaml
    config["head"][2] = [sources[0], 1, "Detect", ["nc"]]
    result = check_structure(make(config), input_size=64, class_count=2)
    assert result["status"] == "FAIL"
    diag = next(d for d in result["diagnostics"] if d["code"] == "HEAD_INPUT_INVALID")
    assert fragment in diag["cause"] and diag["position"] == 5
    assert checks(result)["head_connection"]["status"] == "FAIL"
    assert checks(result)["channels_spatial_stride"]["status"] == "PASS"  # only the head rule is violated


def _observed(genotype: Genotype, imgsz: int = 64) -> BuildReport:
    return inspect_genotype(genotype, imgsz, count_flops=False)


def test_rule_level_head_with_wrong_number_of_sources_is_head_input_invalid():
    """rule-level: ultralytics builds ``nl`` from ``from``, so a count mismatch needs a hand-edited observation."""
    genotype = three_scale()
    report = _observed(genotype)
    head = report.layers[5]
    head.inputs = head.inputs[:1]  # the head built for nl=2 observed with a single delivered source
    result = check_structure(genotype, input_size=64, report=report)
    assert result["status"] == "FAIL"
    diag = next(d for d in result["diagnostics"] if d["code"] == "HEAD_INPUT_INVALID")
    assert "nl=2" in diag["cause"] and "received 1" in diag["cause"] and diag["position"] == 5
    assert checks(result)["head_connection"] == {**checks(result)["head_connection"], "status": "FAIL", "evidence_level": "forward"}


def test_rule_level_head_nc_must_equal_model_nc():
    genotype = three_scale()
    report = _observed(genotype)
    report.layers[5].head["nc"] = 7
    result = check_structure(genotype, input_size=64, report=report)
    assert result["status"] == "FAIL"
    assert any(d["code"] == "HEAD_INPUT_INVALID" and "nc=7" in d["cause"] for d in result["diagnostics"])


# -- merge rules ------------------------------------------------------------------------------------------------------
def test_concat_of_mismatched_spatial_sources_fails_at_the_merge_row():
    genotype = make({"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [8, 3, 2]], [[0, 1], 1, "Concat", [1]]],
                     "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    assert result["status"] == "FAIL"
    merge = next(d for d in result["diagnostics"] if d["code"] == "MERGE_INPUT_MISMATCH")
    assert merge["position"] == 2 and merge["path"] == "backbone[2]" and merge["evidence_level"] == "forward"
    assert "height and width" in merge["cause"] and "row 0=" in merge["cause"] and "row 1=" in merge["cause"]
    assert any(d["code"] == "SHAPE_MISMATCH" and d["position"] == 2 for d in result["diagnostics"])  # v1 raw forward diagnostic stays
    assert checks(result)["merge_conditions"]["status"] == "FAIL"
    assert checks(result)["merge_conditions"]["evidence_level"] == "forward"
    # failing documents report how many rows each check covered, like PASS documents do
    assert checks(result)["merge_conditions"]["rows_checked"] == 1
    assert checks(result)["module_support"]["status"] == "PASS" and checks(result)["module_support"]["rows_checked"] == 4
    row = result["blocks"][2]
    assert "MERGE_INPUT_MISMATCH" in {c["code"] for c in row["checks_local"]}
    assert [i["source"] for i in row["effective"]["inputs"]] == [0, 1]  # observed sources of the failing merge
    assert row["effective"]["observed_at_failure"] is True
    assert result["observation"]["device"] == "cpu"  # a runtime FAIL still records its environment


def test_concat_channel_axis_other_than_one_requires_equal_channels():
    """Concat along the height axis (dim=2) may differ in height but not in channels."""
    genotype = make({"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [16, 3, 2]], [[0, 1], 1, "Concat", [2]]],
                     "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    assert result["status"] == "FAIL"
    assert any(d["code"] == "MERGE_INPUT_MISMATCH" and d["position"] == 2 for d in result["diagnostics"])


def _synthetic_add_case(second_channels: int) -> tuple[Genotype, BuildReport]:
    """rule-level: stock ultralytics has no ``Add``; a hand-written observation of ``Add`` over two Conv outputs."""
    genotype = make({"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [0, 1, "Conv", [8, 3, 1]], [0, 1, "Conv", [second_channels, 3, 1]],
                                           [[1, 2], 1, "Add", []]], "head": [[[3], 1, "Detect", ["nc"]]]})

    def layer(i, frm, c_in, c_out, h, sources):
        inputs = [{"source": s, "c": c, "h": hh, "w": hh} for s, c, hh in sources]
        return LayerInfo(index=i, module="Conv", frm=frm, c_in=c_in, c_out=c_out, h_in=sources[0][2], w_in=sources[0][2],
                         h_out=h, w_out=h, stride=64 / h, params=1, inputs=inputs)

    layers = [layer(0, -1, 3, 8, 32, [(-1, 3, 64)]), layer(1, 0, 8, 8, 32, [(0, 8, 32)]),
              layer(2, 0, 8, second_channels, 32, [(0, 8, 32)]),
              LayerInfo(index=3, module="Add", frm=[1, 2], c_in=8 + second_channels, c_out=8, h_in=32, w_in=32, h_out=32,
                        w_out=32, stride=2.0, params=0,
                        inputs=[{"source": 1, "c": 8, "h": 32, "w": 32}, {"source": 2, "c": second_channels, "h": 32, "w": 32}]),
              LayerInfo(index=4, module="Detect", frm=[3], c_in=8, c_out=None, h_in=32, w_in=32, h_out=None, w_out=None,
                        stride=None, params=1, inputs=[{"source": 3, "c": 8, "h": 32, "w": 32}], outputs=[[5, 1]],
                        head={"head_strides": [2.0], "nl": 1, "end2end": False, "nc": 1})]
    report = BuildReport(ok=True, error=None, imgsz=64, layers=layers, signature={"modules": []}, signature_hash="sha256:s",
                         architecture_hash="sha256:a", params=10, yaml_hash=genotype.yaml_hash())
    return genotype, report


def test_rule_level_add_with_channel_mismatch_fails_and_equal_channels_pass():
    genotype, report = _synthetic_add_case(second_channels=16)
    result = check_structure(genotype, input_size=64, report=report)
    assert result["status"] == "FAIL"
    diag = next(d for d in result["diagnostics"] if d["code"] == "MERGE_INPUT_MISMATCH")
    assert diag["position"] == 3 and "channels" in diag["cause"] and "Add" in diag["cause"]
    assert checks(result)["merge_conditions"]["status"] == "FAIL"
    ok_genotype, ok_report = _synthetic_add_case(second_channels=8)
    assert check_structure(ok_genotype, input_size=64, report=ok_report)["status"] == "PASS"


def test_individual_rules_carry_rows_checked_and_legacy_reports_are_derived_from_forward():
    genotype = three_scale()
    report = _observed(genotype)
    result = check_structure(genotype, input_size=64, report=report)
    assert result["status"] == "PASS"
    assert checks(result)["merge_conditions"]["rows_checked"] == 1
    assert checks(result)["head_connection"]["rows_checked"] == 1
    assert checks(result)["channels_spatial_stride"]["rows_checked"] >= 6
    assert {c["name"]: c["evidence_level"] for c in result["checks"]} == {
        "yaml_rows": "declared", "references": "declared", "class_count": "declared", "scales": "declared",
        "module_support": "built", "cpu_build": "built", "reference_forward": "forward",
        "channels_spatial_stride": "forward", "merge_conditions": "forward", "head_connection": "forward"}
    for li in report.layers:  # a report from an older producer: the forward succeeded but nothing per source was kept
        li.inputs = []
    legacy = check_structure(genotype, input_size=64, report=report)
    assert legacy["status"] == "PASS"
    assert {checks(legacy)[n]["evidence_level"] for n in ("channels_spatial_stride", "merge_conditions", "head_connection")} == {"derived_from_forward"}
    assert checks(legacy)["reference_forward"]["evidence_level"] == "forward"


def test_failed_supplied_report_is_never_upgraded_by_the_new_rules():
    genotype = three_scale()
    failed = BuildReport(ok=False, error="forward@64 at row 5 (Detect): RuntimeError: boom", imgsz=64, error_row=5,
                         yaml_hash=genotype.yaml_hash(), stage="forward", failure_kind="forward")
    result = check_structure(genotype, input_size=64, report=failed)
    assert result["status"] == "FAIL"
    assert checks(result)["reference_forward"]["status"] == "FAIL"
    assert result["observation"]["report_origin"] == "supplied"


# -- U6 wrapper / U2 repeats / U1 shared ---------------------------------------------------------------------------------
def test_non_repeat_module_with_n_gt_1_records_sequential_wrapper_and_wrapped_module():
    genotype = make({"nc": 1, "depth_multiple": 1.0, "width_multiple": 0.25,
                     "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 2, "Conv", [16, 3, 1]], [-1, 2, "C2f", [16]]],
                     "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    assert result["status"] == "PASS", result["diagnostics"]
    wrapped = result["blocks"][1]
    assert wrapped["module"] == "Conv"  # declared operator name is untouched
    assert (wrapped["module_class"], wrapped["wrapper"], wrapped["wrapped_module"]) == ("Sequential", "nn.Sequential", "Conv")
    assert wrapped["repeats_detail"] == {"declared": 2, "scaled": 2, "observed": 2}
    assert wrapped["effective"]["repeats"] == 2
    assert wrapped["effective"]["repeats_source"] == "sequential_wrapper"
    plain = result["blocks"][0]
    assert (plain["module_class"], plain["wrapper"], plain["wrapped_module"]) == ("Conv", None, None)
    repeat_module = result["blocks"][2]
    assert (repeat_module["wrapper"], repeat_module["repeats_detail"]["observed"]) == (None, 2)
    assert repeat_module["effective"]["repeats_source"] == ".m"


def test_observed_repeats_replace_the_formula_and_keep_it_beside():
    genotype = make({"nc": 1, "depth_multiple": 0.5, "width_multiple": 0.25,
                     "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 4, "C2f", [16]]],
                     "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    block = result["blocks"][1]
    assert block["repeats"] == 4 and block["declared"]["repeats"] == 4  # v1 declared values
    assert block["repeats_detail"] == {"declared": 4, "scaled": 2, "observed": 2}
    assert block["effective"]["repeats"] == 2 and block["effective"]["repeats_scaled"] == 2
    assert len([layer for layer in block["internal_layers"] if layer["path"].count(".") == 2 and layer["path"].startswith("1.m.")]) == 2


def test_shared_activation_instance_appears_with_shared_true_in_every_conv_block():
    genotype = make({"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [8, 3, 2]], [-1, 1, "C2f", [8]]],
                     "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    for index in (0, 1):  # v1 listed ``0.act`` only (named_modules de-duplicated the shared SiLU)
        layers = {layer["path"]: layer for layer in result["blocks"][index]["internal_layers"]}
        assert layers[f"{index}.act"]["shared"] is True
        assert layers[f"{index}.act"]["module"] == "SiLU"
        assert layers[f"{index}.conv"]["shared"] is False and layers[f"{index}.bn"]["shared"] is False
    inner = [layer for layer in result["blocks"][2]["internal_layers"] if layer["path"].endswith(".act")]
    assert len(inner) >= 3 and all(layer["shared"] for layer in inner)
    assert all(set(layer) >= {"path", "module", "parameters", "attributes", "shared"} for b in result["blocks"] for layer in b["internal_layers"])


# -- UNVERIFIED / observation / flops ------------------------------------------------------------------------------------
def test_unverified_leaves_observed_repeats_null_but_keeps_declared_and_scaled(monkeypatch):
    def missing(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'torch'")
    monkeypatch.setattr(structure, "inspect_genotype", missing)
    genotype = make({"nc": 1, "depth_multiple": 0.5, "width_multiple": 0.25,
                     "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 4, "C2f", [16]]], "head": [[[-1], 1, "Detect", ["nc"]]]})
    result = check_structure(genotype, input_size=64, class_count=1)
    assert result["status"] == "UNVERIFIED"
    block = result["blocks"][1]
    assert block["effective"]["repeats"] is None
    assert block["repeats_detail"] == {"declared": 4, "scaled": 2, "observed": None}
    assert block["effective"]["repeats_scaled"] == 2
    assert block["module_class"] is None and block["wrapper"] is None
    assert block["internal_mapping_status"] == "UNVERIFIED" and block["internal_layers"] == []
    assert result["observation"] is None
    assert all(c["status"] != "PASS" for c in result["checks"] if c["name"] in {"module_support", "cpu_build", "reference_forward",
                                                                                   "channels_spatial_stride", "merge_conditions", "head_connection"})


def test_observation_block_present_on_pass_and_runtime_fail_absent_on_declared_only():
    ok = check_structure(three_scale(), input_size=64, class_count=2)
    obs = ok["observation"]
    assert obs["method"] == "ultralytics_parse_model+forward_hook" and obs["device"] == "cpu" and obs["imgsz"] == 64
    assert obs["python"].count(".") == 2 and obs["torch"] and obs["ultralytics"]
    assert obs["checked_at"].endswith("+00:00") and obs["report_origin"] == "executed"
    bad = make({"nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [8, 3, 2]], [[0, 1], 1, "Concat", [1]]],
                "head": [[[-1], 1, "Detect", ["nc"]]]})
    assert check_structure(bad, input_size=64, class_count=1)["observation"]["imgsz"] == 64
    assert describe_genotype(three_scale())["observation"] is None
    static = check_structure(three_scale(), input_size=48, class_count=2)  # INPUT_SIZE_INVALID: nothing ran
    assert static["status"] == "FAIL" and static["observation"] is None
    assert static["blocks"][0]["effective"]["repeats"] is None
    # a reused proposer report keeps the environment of the run that produced it
    report = _observed(three_scale())
    reused = check_structure(three_scale(), input_size=64, class_count=2, report=report)
    assert reused["observation"]["checked_at"] == report.observation["checked_at"]
    assert reused["observation"]["report_origin"] == "supplied"


def test_flops_policy_dd14_explicit_check_not_measured_proposer_value_preserved():
    genotype = three_scale()
    explicit = check_structure(genotype, input_size=64, class_count=2)
    assert (explicit["flops"], explicit["flops_status"], explicit["flops_source"]) == (None, "NOT_MEASURED", None)
    measured = inspect_genotype(genotype, 64, count_flops=True)
    reused = check_structure(genotype, input_size=64, class_count=2, report=measured)
    assert reused["flops"] == measured.flops and reused["flops"] > 0
    assert (reused["flops_status"], reused["flops_source"]) == ("MEASURED", "proposer_flop_counter")


def test_diagnostics_keep_the_v1_shape_and_add_evidence_level():
    result = check_structure(three_scale(), input_size=48, class_count=2)
    assert result["status"] == "FAIL"
    for diag in result["diagnostics"]:
        assert {"code", "position", "path", "cause", "suggestion"} <= set(diag)
        assert diag["evidence_level"] == "declared"


def test_v2_documents_still_compare_edge_by_edge():
    left = check_structure(three_scale(), input_size=64, class_count=2)
    right = check_structure(three_scale(backbone=[[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]]]),
                            input_size=64, class_count=2)
    diff = structure.compare_structures(left, right)
    assert diff["gene_ancestry_inferred"] is False
    assert [c["fields"] for c in diff["changed"]] == [["args"]]
    assert diff["edges_added"] == [] and diff["edges_removed"] == []


# -- ETRI mixed pool (U2/U6 on NAS transformers; parser identity) ---------------------------------------------------------
@pytest.fixture
def etri_mixed():
    """WP-1 (DD-A12): the vendored mixed pool is always registered here; a failure is an error, not a skip."""
    from mbnas_core.search.mixed_pool.bridge import load_etri_mixed

    return load_etri_mixed()


def test_etri_tiny_mixed_records_nas_repeats_wrapper_and_parser_hash(etri_mixed):
    result = check_structure(make(copy.deepcopy(TINY_MIXED)), input_size=128, class_count=1)
    assert result["status"] == "PASS", result["diagnostics"]
    assert len(result["observation"]["parser_source_sha256"]) == 64
    blocks = result["blocks"]
    assert blocks[4]["module"] == "NASWindowTransformer"
    assert blocks[4]["repeats_detail"] == {"declared": 4, "scaled": 2, "observed": 2}
    assert blocks[4]["effective"]["repeats_source"] == ".blocks"
    assert blocks[6]["repeats_detail"] == {"declared": 2, "scaled": 1, "observed": 1}
    assert (blocks[8]["wrapper"], blocks[8]["wrapped_module"], blocks[8]["repeats_detail"]) == (
        "nn.Sequential", "Conv", {"declared": 3, "scaled": 2, "observed": 2})
    assert [i["source"] for i in blocks[10]["effective"]["inputs"]] == [9, 4]
    detect = blocks[12]["effective"]
    assert (detect["head_strides"], detect["nl"], detect["end2end"]) == ([8.0, 16.0], 2, False)
    assert [i["source"] for i in detect["inputs"]] == [11, 8]
    assert checks(result)["merge_conditions"]["rows_checked"] == 1
