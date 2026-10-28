"""Shared space: templates, CPU build shape guard, architecture_hash semantics, repair records, audits, fallback."""
from __future__ import annotations

import importlib.util
import random

import pytest

from mbnas_core.search import random as random_arm
from mbnas_model_runtime.genotype import Genotype, inspect_genotype
from mbnas_core.search.lineage import audit_for, blocks_for
from mbnas_core.search.space import Choice, Proposal, RowOrigin, SearchSpace


def test_space_uses_etri_pool_and_records_template_hashes(space) -> None:
    d = space.describe()
    assert d["variant"] == "etri_mixed" and d["fallback_reason"] is None
    assert [t["id"] for t in d["templates"]] == [f"{f}:{v}" for f in ("yolo11n", "yolo26n")
                                                for v in ("cnn_backbone", "native", "transformer_backbone")]
    for t in d["templates"]:
        assert t["source"]["base"]["sha256"] and t["source"]["base"]["path"].endswith(".yaml")
    assert d["etri"]["registration"]["installed"] is True
    # S1-F4 under mbnas-mixed-space/3 (contract §6.5): every vendored mixed-pool module is content-hashed
    assert {k.rsplit("/", 1)[-1] for k in d["etri"]["files_sha256"]} == {"etri_mixed_blocks.py", "seed_variants.py",
                                                                     "runtime_constants.py"}
    assert d["rejected_templates"] == []
    assert "NASWindowTransformer" in d["ops_feature"] and "NASPatchMerge" in d["ops_down"]


def test_ten_random_proposals_build_on_cpu_and_roundtrip(space) -> None:
    props = random_arm.propose_generation(space, random.Random(7), 10)
    assert len(props) == 10
    for p in props:
        assert p.report.ok and p.report.architecture_hash, p.report.error
        assert space.violations(p.genotype, p.report) == []
        assert p.parents == [] and p.operation == "random"
        assert {o.kind for o in p.origins} <= {"random", "repair"}
        again = Genotype.from_yaml_text(p.genotype.yaml_text(), family=p.genotype.family,
                                        template_id=p.genotype.template_id)
        assert again.yaml == p.genotype.yaml
        rebuilt = inspect_genotype(again, 640, count_flops=False)
        assert rebuilt.architecture_hash == p.report.architecture_hash
        assert [li.c_out for li in rebuilt.layers] == [li.c_out for li in p.report.layers]


def test_architecture_hash_is_the_training_adapters_structural_hash(space) -> None:
    from mbnas_model_runtime.adapters.common import structural_hash
    from mbnas_model_runtime.genotype import build_detection_model

    g = space.templates["yolo11n:transformer_backbone"].genotype
    assert space.inspect(g).architecture_hash == structural_hash(build_detection_model(g.yaml, 1))


def test_noop_mutation_keeps_architecture_hash(space) -> None:
    tpl = space.templates["yolo11n:native"].genotype
    slot = next(s for s in space.slots["yolo11n"] if s.row == 2)
    assert slot.gene_of(tpl.rows()[2]) == Choice("C3k2", 2, 1.0)
    rows = tpl.rows()
    rows[2] = slot.render(Choice("C3k2", 1, 1.0))  # depth 0.5: repeats 2 and 1 both build one block
    noop = tpl.with_rows(rows)
    assert noop.yaml_hash() != tpl.yaml_hash()
    assert space.inspect(noop).architecture_hash == space.inspect(tpl).architecture_hash
    assert space.inspect(noop).signature_hash == space.inspect(tpl).signature_hash
    rows[2] = slot.render(Choice("C2f", 2, 1.0))
    real = tpl.with_rows(rows)
    assert space.inspect(real).architecture_hash != space.inspect(tpl).architecture_hash


def test_repair_reverts_upstream_width_that_breaks_c2psa(space) -> None:
    tpl = space.templates["yolo11n:native"].genotype
    sppf = next(s for s in space.slots["yolo11n"] if s.row == 9)
    rows = tpl.rows()
    rows[9] = sppf.render(Choice("SPPF", 1, 0.75))  # SPPF c2 192 -> C2PSA(row 10) c1 != c2
    child = tpl.with_rows(rows)
    bad = space.violations(child, space.inspect(child))  # C2PSA's own c1 == c2 assert fires while parsing row 10
    assert bad and bad[0]["row"] == 10
    assert bad[0]["reason"].startswith("C2PSA requires c1 == c2 (c1=192, c2=256); CPU build at row 10 (C2PSA)")
    origins = [RowOrigin("random", tpl.template_id, "inherit") for _ in rows]
    fixed, rep, events = space.repair(child, tpl, "template:yolo11n:native", origins, [tpl.template_id] * len(rows))
    assert fixed.yaml == tpl.yaml and rep.ok
    assert events == [{"row": 9, "violation_row": 10, "reason": bad[0]["reason"], "before": rows[9],
                       "after": tpl.rows()[9], "reverted_to": "template:yolo11n:native"}]
    prop = Proposal(method="test", operation="random", genotype=fixed, report=rep, origins=origins,
                    repair_events=events)
    block = blocks_for(prop, space)[9]
    assert block["source_kind"] == "repair" and "C2PSA" in block["repair_reason"]
    assert block["source"] == "yolo11n:native" and block["parent_block_id"] == "yolo11n:native#9"
    assert space.illegal_reason_for(child, 10) == "C2PSA requires c1 == c2 (c1=192, c2=256)"
    assert block["before"]["args"] == [768, 5] and block["after"]["args"] == [1024, 5]


def test_repair_handles_forward_failure_token_budget(space) -> None:
    tpl = space.templates["yolo11n:native"].genotype
    rows = tpl.rows()
    rows[2] = [-1, 1, "NASGlobalTransformer", [256]]  # 160x160 tokens at 640 > max_tokens 1024
    child = tpl.with_rows(rows)
    rep = space.inspect(child)  # fails in DetectionModel's own 256 stride forward (64x64 tokens)
    assert not rep.ok and rep.error_row == 2 and "token budget" in rep.error
    origins = [RowOrigin("random", tpl.template_id, "inherit") for _ in rows]
    fixed, _, events = space.repair(child, tpl, "template", origins, [tpl.template_id] * len(rows))
    assert fixed.yaml == tpl.yaml and events[0]["row"] == 2 and "row 2 (NASGlobalTransformer)" in events[0]["reason"]


@pytest.mark.parametrize("tid, backbone, config_family", [
    ("yolo11n:transformer_backbone", "transformer_backbone", "transformer_backbone"),
    ("yolo11n:cnn_backbone", "cnn_backbone", "cnn_backbone"),
    ("yolo11n:native", "hybrid_backbone", "hybrid_backbone"),
    ("yolo26n:cnn_backbone", "cnn_backbone", "cnn_backbone"),
])
def test_audit_classification(space, tid: str, backbone: str, config_family: str) -> None:
    g = space.templates[tid].genotype
    prop = Proposal(method="t", operation="random", genotype=g, report=space.inspect(g),
                    origins=[RowOrigin("random", tid, "inherit") for _ in g.rows()])
    a = audit_for(prop, space)
    assert a["built"]["backbone_family"] == backbone
    assert a["config_estimate"]["backbone_family"] == config_family
    assert a["label"] == f"{backbone} with CNN detection head"
    assert a["built"]["full_transformer"] is False and "full transformer" not in a["label"].lower()
    assert a["built"]["detect_head"]["conv_modules"] > 0
    assert a["search_space_variant"] == "etri_mixed"
    if tid == "yolo11n:transformer_backbone":
        assert a["built"]["scopes"]["backbone"]["conv_modules"] == 0
        assert a["built"]["scopes"]["backbone"]["nas_custom_blocks"]["NASPatchMerge"] == 5
        assert a["config_estimate"]["head_preserved"] is True
    if tid == "yolo26n:cnn_backbone":  # head row 22 is C3k2(attn=True): config backbone vs built neck differ
        assert a["built"]["neck_family"] == "hybrid_neck"


# 2026-10-21 (D4): tests that genuinely build real ultralytics models / need the ETRI pool skip (not fail/error) on a
# host without the ml extras. Torch-free tests in this module keep running.
needs_ml = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("ultralytics") is None,
    reason="needs torch + ultralytics (ml extras)")


@needs_ml
def test_native_fallback_space_has_no_custom_operators() -> None:
    fb = SearchSpace.create(nc=1, imgsz=640, families=("yolo11n",), use_etri=False)
    d = fb.describe()
    assert d["variant"] == "native_fallback" and "disabled" in d["fallback_reason"]
    assert sorted(fb.templates) == ["yolo11n:cnn_backbone", "yolo11n:native"]
    for p in random_arm.propose_generation(fb, random.Random(3), 4):
        assert p.report.ok
        assert not any(str(r[2]).startswith("NAS") for r in p.genotype.rows())
        assert audit_for(p, fb)["search_space_variant"] == "native_fallback"
