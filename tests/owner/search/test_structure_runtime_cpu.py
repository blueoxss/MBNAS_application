"""Real CPU model construction/reference-forward checks; no data, training or performance benchmark."""
from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("ultralytics")

from mbnas_model_runtime.genotype import Genotype
from mbnas_core.search.structure import check_structure


def make(config):
    return Genotype.from_yaml_dict(config,family="tiny-cpu-test",template_id="isolated-cpu-test")


def test_cpu_build_records_actual_params_shapes_and_multiscale_head_edges():
    model=make({"nc":1,"backbone":[[-1,1,"Conv",[8,3,2]],[-1,1,"Conv",[8,3,2]]],
                "head":[[[0,1],1,"Detect",["nc"]]]})
    result=check_structure(model,input_size=32,class_count=1)
    assert result["status"]=="PASS",result["diagnostics"]
    assert result["parameters"]>0
    assert result["architecture_hash"].startswith("sha256:")
    assert result["signature_hash"].startswith("sha256:")
    assert result["blocks"][0]["effective"]["c_in"]==3
    assert result["blocks"][0]["effective"]["h_out"]==16
    assert result["blocks"][1]["effective"]["h_out"]==8
    assert {(e["source"],e["target"]) for e in result["edges"]}=={(-1,0),(0,1),(0,2),(1,2)}
    assert all(key not in result for key in ("ap","recall","latency_ms","fitness"))
    head=result["blocks"][2]["effective"]
    assert [i["source"] for i in head["inputs"]]==[0,1]  # list-from rows record every source (mbnas-structure/2)
    assert [(i["h"],i["w"]) for i in head["inputs"]]==[(16,16),(8,8)]
    assert head["head_strides"]==[2.0,4.0] and head["nl"]==2
    assert {c["name"]:c["status"] for c in result["checks"]}["head_connection"]=="PASS"


def test_cpu_rejects_concat_spatial_mismatch_in_actual_forward():
    model=make({"nc":1,"backbone":[[-1,1,"Conv",[8,3,2]],[-1,1,"Conv",[8,3,2]],[[0,1],1,"Concat",[1]]],
                "head":[[[-1],1,"Detect",["nc"]]]})
    result=check_structure(model,input_size=32,class_count=1)
    assert result["status"]=="FAIL"
    assert result["parameters"] is None
    assert any(d["code"]=="SHAPE_MISMATCH" for d in result["diagnostics"])
    assert any(d["position"]==2 for d in result["diagnostics"])
    assert result["diagnostics"][0]["position"]==2 and result["diagnostics"][0]["path"]=="backbone[2]"  # raw forward diagnostic is positioned
    merge_diag=next(d for d in result["diagnostics"] if d["code"]=="MERGE_INPUT_MISMATCH")
    assert result["diagnostics"][0]["path"]==merge_diag["path"]
    assert any(d["code"]=="MERGE_INPUT_MISMATCH" and d["position"]==2 for d in result["diagnostics"])


def test_repeated_gene_retains_internal_layer_mapping_without_one_to_one_claim():
    model=make({"nc":1,"backbone":[[-1,1,"Conv",[16,3,2]],[-1,2,"C2f",[16]]],
                "head":[[[-1],1,"Detect",["nc"]]]})
    result=check_structure(model,input_size=32,class_count=1)
    assert result["status"]=="PASS",result["diagnostics"]
    repeated=result["blocks"][1]
    assert repeated["repeats"]==2
    assert repeated["yaml_path"]=="backbone[1]"
    assert repeated["internal_mapping_status"]=="RECORDED"
    paths=[layer["path"] for layer in repeated["internal_layers"]]
    assert any(path.startswith("1.m.0") for path in paths)
    assert any(path.startswith("1.m.1") for path in paths)
    assert len(paths)>2
    assert repeated["repeats_detail"]=={"declared":2,"scaled":2,"observed":2}  # no depth scale: observed len(m.m)
    assert repeated["effective"]["repeats"]==2
    assert any(layer["path"]=="1.cv1.act" and layer["shared"] for layer in repeated["internal_layers"])
