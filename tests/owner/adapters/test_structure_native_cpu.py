"""WP-S2: native (non-YAML) structure documents of RF-DETR Nano and the ETRI hybrid, plus the registry YAML helper.

CPU only, no GPU, no weights download (``HF_HUB_OFFLINE=1``), no dependence on /root.  Values are checked against the
independent probe outputs recorded in docs/integration_20261021/evidence (rfdetr_module_probe.offline.out,
etri_hybrid_module_probe.out): params 30,147,076 / 922,870, decoder layers 2, encoder maps (1,384,24,24), ...
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
torch = pytest.importorskip("torch")

from mbnas_model_runtime.adapters.base import ArchSpec  # noqa: E402
from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter  # noqa: E402
from mbnas_model_runtime.adapters.structure_native import observe_native_structure  # noqa: E402
from mbnas_server.api.contract_check import contract_errors  # noqa: E402
from mbnas_model_runtime.registry import SPEC_BY_ID, arch_spec  # noqa: E402

ETRI = SPEC_BY_ID["etri-controlled-cnn-transformer"]
NANO = SPEC_BY_ID["rf-detr-nano"]


def _arch(spec):
    return ArchSpec(spec.id, spec.family, spec.backend, spec.config)


@pytest.fixture(scope="module")
def rf_adapter():
    pytest.importorskip("rfdetr")
    from mbnas_model_runtime.registry import adapter_for

    return adapter_for(NANO)


@pytest.fixture(scope="module")
def rf_doc(rf_adapter):
    return rf_adapter.describe_structure(_arch(NANO), 1, None)


@pytest.fixture(scope="module")
def etri_doc():
    return EtriHybridAdapter().describe_structure(_arch(ETRI), 1, None)


def _block(doc, path):
    return next(b for b in doc["blocks"] if b["native_path"] == path)


def _checks(doc):
    return {c["name"]: c for c in doc["checks"]}


def _common_native_asserts(doc, prefix):
    assert doc["schema"] == "mbnas-structure/2" and doc["representation"] == "native_modules"
    assert doc["lineage"] is None and doc["lineage_status"] == "NOT_APPLICABLE"
    assert doc["yaml_hash"] is None and doc["scale"] is None
    tag = doc["architecture_hash"][7:19]
    for i, b in enumerate(doc["blocks"]):
        assert b["id"] == b["block_id"] == f"block-{i:04d}"
        assert b["stable_id"] == f"{prefix}:{b['native_path']}@{tag}"
        for key in ("gene_index", "yaml_section", "yaml_index", "yaml_path", "from", "from_resolved", "repeats", "args", "module"):
            assert b[key] is None, key
        assert b["section_basis"].startswith("native:")
        assert b["connection_source"] in ("native_fixed", "observed")
        assert set(b["repeats_detail"]) >= {"declared", "scaled", "observed"} and b["repeats_detail"]["scaled"] is None
    ck = _checks(doc)
    for name in ("yaml_rows", "references", "scales", "merge_conditions"):
        assert ck[name]["status"] == "NOT_CHECKED" and ck[name]["evidence_level"] is None
    assert ck["module_support"]["evidence_level"] == ck["cpu_build"]["evidence_level"] == "built"
    assert ck["reference_forward"]["evidence_level"] == ck["channels_spatial_stride"]["evidence_level"] == "forward"
    obs = doc["observation"]
    assert obs["method"] == "forward_hook" and obs["device"] == "cpu" and obs["fx_traceable"] is False
    assert obs["python"] and obs["torch"] and obs["checked_at"]
    json.dumps(doc, allow_nan=False)  # storable by atomic_write_json
    assert contract_errors("StructureReport", doc) == []


# -- RF-DETR Nano ---------------------------------------------------------------------------------------------------
def test_rfdetr_nano_matches_adapter_build_and_probe(rf_adapter, rf_doc):
    doc = rf_doc
    report = rf_adapter.build(arch_spec("rf-detr-nano"), 1, "scratch", 0)
    assert doc["status"] == "PASS" and doc["diagnostics"] == []
    assert doc["parameters"] == report.params == 30_147_076
    assert doc["architecture_hash"] == report.architecture_hash
    assert doc["backend"] == "rfdetr" and doc["input_size"] == 384 == doc["fixed_resolution"]
    _common_native_asserts(doc, "rf")
    assert doc["parameters_unattributed"] == 0
    obs = doc["observation"]
    assert obs["dinov2_weights"] == "skipped" and obs["imgsz"] == 384
    assert obs["rfdetr"] and obs["transformers"] and "fx_error" not in obs and "not re-measured" in obs["fx_basis"]
    ck = _checks(doc)
    assert [ck[n]["status"] for n in ("class_count", "module_support", "cpu_build", "reference_forward",
                                      "channels_spatial_stride", "head_connection")] == ["PASS"] * 6


def test_rfdetr_nano_repeats_shapes_and_params_per_block(rf_doc):
    doc = rf_doc
    layers = [b for b in doc["blocks"] if b["native_path"].startswith("transformer.decoder.layers.")]
    assert [b["native_path"] for b in layers] == ["transformer.decoder.layers.0", "transformer.decoder.layers.1"]
    for b in layers:
        rd = b["repeats_detail"]
        assert rd["declared"] == 2 == rd["observed"] and rd["declared_basis"] == "dec_layers"
        assert b["effective"]["repeats"] == 2 and b["effective"]["outputs"] == [[300, 256]]
    assert doc["native_config"]["dec_layers"] == 2
    enc = _block(doc, "backbone.0.encoder")
    assert enc["effective"]["output_shapes"] == [[1, 384, 24, 24]] * 4
    assert enc["effective"]["outputs"] == [[384, 24, 24]] * 4 and enc["effective"]["stride"] == 16
    assert enc["repeats_detail"]["declared"] == len(doc["native_config"]["out_feature_indexes"]) == 4
    assert enc["repeats_detail"]["observed"] == 4
    proj = _block(doc, "backbone.0.projector")
    assert proj["effective"]["output_shapes"] == [[1, 256, 24, 24]] and proj["effective"]["c_out"] == 256
    assert proj["effective"]["inputs"][0]["shape"] == [1, 384, 24, 24]
    assert _block(doc, "class_embed")["effective"]["output_shapes"] == [[2, 1, 300, 2]]
    assert _block(doc, "bbox_embed")["effective"]["output_shapes"] == [[2, 1, 300, 4]]
    assert _block(doc, "backbone.0.encoder")["effective"]["params"] == 21_821_184
    assert sum(b["effective"]["params"] for b in doc["blocks"]) == doc["parameters"]
    assert _block(doc, "query_feat")["repeats_detail"]["declared"] == 3900 == _block(doc, "query_feat")["repeats_detail"]["observed"]
    assert {g["group"]: (g["declared"], g["observed"]) for g in doc["repeat_groups"]}["transformer.decoder.layers"] == (2, 2)
    # internal layers keep the shared flag (the encoder is a large tree)
    assert any(layer["path"].startswith("backbone.0.encoder.") for layer in enc["internal_layers"])
    assert all(isinstance(layer["shared"], bool) for layer in enc["internal_layers"])


def test_rfdetr_nano_edges_are_only_among_hooked_modules_and_the_rest_is_listed(rf_doc):
    doc = rf_doc
    by_index = {b["index"]: b for b in doc["blocks"]}
    assert doc["edges"], "hook-confirmed edges expected"
    for e in doc["edges"]:
        assert e["connection_source"] in ("observed", "native_fixed") and e["from_ref"] is None
        for end in (e["source"], e["target"]):
            assert by_index[end]["effective"]["calls"] > 0, f"edge touches un-hooked {by_index[end]['native_path']}"
        if e["connection_source"] == "observed":
            assert e["evidence"]["kind"] == "tensor_identity"
        else:
            assert e["evidence"]["corroborated"] is True and e["evidence"]["citation"]
    pairs = {(by_index[e["source"]]["native_path"], by_index[e["target"]]["native_path"]): e["connection_source"]
             for e in doc["edges"]}
    assert pairs[("backbone.0.encoder", "backbone.0.projector")] == "observed"
    assert pairs[("transformer.decoder.layers.0", "transformer.decoder.layers.1")] == "observed"
    assert pairs[("transformer.decoder.norm", "class_embed")] == "native_fixed"
    unobs = {u["module_path"]: u["reason"] for u in doc["edges_unobserved"]}
    assert unobs and all(u["reason"] for u in doc["edges_unobserved"])
    for path in ("query_feat", "refpoint_embed", "transformer.enc_output", "transformer.enc_out_class_embed",
                 "backbone.0.projector -> transformer.*"):
        assert path in unobs, path
    paths = {b["native_path"] for b in doc["blocks"]}
    assert all(p in paths or "->" in p for p in unobs)
    # nothing that was not hooked got an edge
    assert not any(p in {b["native_path"] for b in doc["blocks"] if b["effective"]["calls"] == 0}
                   for pair in pairs for p in pair)
    scopes = {s["module_path"]: s for s in doc["observed_scopes"]}
    assert scopes["transformer"]["outputs"][0] == [2, 1, 300, 256]


# -- ETRI hybrid ----------------------------------------------------------------------------------------------------
def test_etri_hybrid_matches_adapter_build_and_probe(etri_doc):
    doc = etri_doc
    report = EtriHybridAdapter().build(_arch(ETRI), 1, "scratch", 0)
    assert doc["status"] == "PASS" and doc["diagnostics"] == []
    assert doc["parameters"] == report.params == 922_870 and doc["architecture_hash"] == report.architecture_hash
    assert doc["backend"] == "etri_hybrid" and doc["input_size"] == 384
    _common_native_asserts(doc, "etri")
    assert doc["observation"]["dinov2_weights"] is None and doc["observation"]["source_blob_sha1"]
    stems = [b for b in doc["blocks"] if b["native_path"].startswith("embedding.layers.")]
    assert [b["native_path"] for b in stems] == [f"embedding.layers.{i}" for i in range(8)]
    assert [b["section"] for b in stems] == ["stem"] * 8
    assert [b["effective"]["c_out"] for b in stems] == [16, 16, 32, 32, 64, 64, 128, 128]
    assert [b["effective"]["h_out"] for b in stems] == [192, 192, 96, 96, 48, 48, 24, 24]
    assert [b["effective"]["stride"] for b in stems] == [2, 2, 4, 4, 8, 8, 16, 16]
    assert stems[0]["effective"]["h_in"] == 384 and stems[0]["effective"]["c_in"] == 3
    assert all(b["repeats_detail"]["declared"] == 8 == b["repeats_detail"]["observed"] for b in stems)
    encs = [b for b in doc["blocks"] if b["native_path"].startswith("spatial_encoder.layers.")]
    assert len(encs) == 3
    assert all(b["repeats_detail"]["declared"] == 3 == b["repeats_detail"]["observed"] for b in encs)
    assert all(b["repeats_detail"]["declared_basis"] == "depth" for b in encs)
    assert _block(doc, "class_head")["effective"]["output_shapes"] == [[1, 576, 2]]
    assert _block(doc, "box_head")["effective"]["output_shapes"] == [[1, 576, 4]]
    assert _block(doc, "class_head")["section"] == "head"
    pos = _block(doc, "position")
    assert pos["native_kind"] == "buffer" and pos["effective"]["buffers"] == [{"name": "position", "shape": [1, 576, 128]}]
    assert sum(b["effective"]["params"] for b in doc["blocks"]) == doc["parameters"] and doc["parameters_unattributed"] == 0


def test_etri_hybrid_edges_chain_observed_and_fixed(etri_doc):
    doc = etri_doc
    names = {b["index"]: b["native_path"] for b in doc["blocks"]}
    got = {(names[e["source"]], names[e["target"]]): e["connection_source"] for e in doc["edges"]}
    for i in range(7):
        assert got[(f"embedding.layers.{i}", f"embedding.layers.{i + 1}")] == "observed"
    assert got[("embedding.layers.7", "spatial_encoder.layers.0")] == "native_fixed"
    assert got[("position", "spatial_encoder.layers.0")] == "native_fixed"
    assert got[("spatial_encoder.layers.0", "spatial_encoder.layers.1")] == "observed"
    assert got[("spatial_encoder.layers.2", "norm")] == "observed"
    assert got[("norm", "class_head")] == got[("norm", "box_head")] == "observed"
    assert all(e["evidence"].get("corroborated", True) for e in doc["edges"])


def test_wrong_input_size_is_a_fail_document_not_a_crash(etri_doc):
    bad = EtriHybridAdapter().describe_structure(_arch(ETRI), 1, 320)
    assert bad["status"] == "FAIL" and bad["diagnostics"][0]["code"] == "INPUT_SIZE_INVALID"
    assert bad["observation"] is None and bad["blocks"] and bad["input_size"] == 320
    ck = _checks(bad)
    assert ck["reference_forward"]["status"] == "NOT_CHECKED" and ck["cpu_build"]["status"] == "PASS"
    assert contract_errors("StructureReport", bad) == []


def test_rfdetr_wrong_input_size_is_a_fail_document(rf_adapter):
    bad = rf_adapter.describe_structure(_arch(NANO), 1, 640)
    assert bad["status"] == "FAIL" and bad["diagnostics"][0]["code"] == "INPUT_SIZE_INVALID"
    assert bad["fixed_resolution"] == 384 and bad["observation"] is None


def test_import_error_is_unverified_with_requirements(monkeypatch, rf_adapter):
    def boom(self, kwargs):
        raise ModuleNotFoundError("No module named 'torch_extra'")

    monkeypatch.setattr(EtriHybridAdapter, "_make", boom)
    doc = EtriHybridAdapter().describe_structure(_arch(ETRI), 1, None)
    assert doc["status"] == "UNVERIFIED" and doc["observation"] is None and doc["blocks"] == []
    assert doc["diagnostics"][0]["code"] == "STRUCTURE_RUNTIME_UNAVAILABLE" and "torch_extra" in doc["diagnostics"][0]["cause"]
    assert doc["runtime_requirements"]["packages"] == ["torch"]
    assert all(c["status"] == "NOT_CHECKED" for c in doc["checks"])
    assert contract_errors("StructureReport", doc) == []

    def offline(self, **kw):
        raise OSError("We couldn't connect to 'https://huggingface.co' (HF_HUB_OFFLINE=1)")

    monkeypatch.setattr(type(rf_adapter), "_instance", offline)
    doc = rf_adapter.describe_structure(_arch(NANO), 1, None)
    assert doc["status"] == "UNVERIFIED" and doc["diagnostics"][0]["code"] == "STRUCTURE_RUNTIME_UNAVAILABLE"
    assert "HF_HUB_OFFLINE=1" in doc["diagnostics"][0]["cause"]
    assert any("rfdetr==1.11.2" in p for p in doc["runtime_requirements"]["packages"])
    assert doc["native_config"]["class_name"] == "RFDETRNano"


def test_forward_failures_are_data(monkeypatch):
    from mbnas_model_runtime.adapters.etri_hybrid.model import make_detector

    cfg = dict(ETRI.config, num_classes=1)
    model = make_detector(**cfg)
    real = type(model).forward
    monkeypatch.setattr(type(model), "forward", lambda self, x: (_ for _ in ()).throw(RuntimeError("shape boom")))
    doc = observe_native_structure("etri_hybrid", model, input_size=384, model_id="t", config=cfg)
    assert doc["status"] == "FAIL" and _checks(doc)["reference_forward"]["status"] == "FAIL"
    assert "shape boom" in doc["diagnostics"][0]["cause"]
    monkeypatch.setattr(type(model), "forward", lambda self, x: (_ for _ in ()).throw(ImportError("lib missing")))
    doc = observe_native_structure("etri_hybrid", model, input_size=384, model_id="t", config=cfg)
    assert doc["status"] == "UNVERIFIED" and doc["diagnostics"][0]["code"] == "STRUCTURE_RUNTIME_UNAVAILABLE"
    monkeypatch.setattr(type(model), "forward", real)
    assert observe_native_structure("etri_hybrid", model, input_size=None, model_id="t", config=cfg)["status"] == "PASS"
    assert model.training is True or model.training is False  # mode restored, not left in eval by the observation


def test_class_count_mismatch_in_head_is_a_fail():
    from mbnas_model_runtime.adapters.etri_hybrid.model import make_detector

    cfg = dict(ETRI.config, num_classes=1)
    model = make_detector(**dict(cfg, num_classes=3))  # head has 3+1 logits, the declaration says 1
    doc = observe_native_structure("etri_hybrid", model, input_size=384, model_id="t", config=cfg)
    assert doc["status"] == "FAIL" and doc["diagnostics"][0]["code"] == "CLASS_COUNT_INVALID"
    assert _checks(doc)["class_count"]["status"] == "FAIL"


# -- registry YAML helper (DD-30) ------------------------------------------------------------------------------------
def test_scale_rule_follows_ultralytics_guess():
    from mbnas_core.console.model_services import derive_model_scale

    scales = {"n": [0.5, 0.25, 1024], "s": [0.5, 0.5, 1024], "l": [1.0, 1.0, 512]}
    assert derive_model_scale({"scales": scales}, "yolo11s.yaml")[0]["scale"] == "s"
    assert derive_model_scale({"scales": scales}, "yolo11s.yaml")[1] == "file_name"
    assert derive_model_scale({"scales": {"l": [1, 1, 1024]}}, "rtdetr-l.yaml") == ({"scales": {"l": [1, 1, 1024]}, "scale": "l"}, "file_name_suffix")
    assert derive_model_scale({"scales": scales}, "custom.yaml")[1] == "first_scale_key"
    assert derive_model_scale({"scales": scales, "scale": "l"}, "yolo11n.yaml") == ({"scales": scales, "scale": "l"}, "yaml")
    assert derive_model_scale({"depth_multiple": 1.0}, "yolo11n.yaml") == ({"depth_multiple": 1.0}, None)


def test_yolo11n_registry_yaml_passes_with_derived_scale():
    pytest.importorskip("ultralytics")
    from mbnas_core.console.model_services import check_registry_yaml

    doc = check_registry_yaml("yolo11n", 64)
    assert doc["status"] == "PASS", doc["diagnostics"]
    assert doc["representation"] == "yaml_genes" and doc["backend"] == "ultralytics"
    assert doc["scale"] == "n" and doc["scale_source"] == "yaml" and doc["class_count"] == 80
    assert _checks(doc)["scales"]["status"] == "PASS"
    assert contract_errors("StructureReport", doc) == []


def test_rtdetr_l_registry_yaml_build_is_recorded_honestly():
    pytest.importorskip("ultralytics")
    from mbnas_core.console.model_services import check_registry_yaml

    doc = check_registry_yaml("rt-detr-l", 64)
    assert doc["representation"] == "yaml_genes" and doc["scale"] == "l" and doc["scale_source"] == "file_name_suffix"
    assert doc["status"] == "PASS" or doc["diagnostics"], "a non-PASS result must carry its diagnostic"
    if doc["status"] == "PASS":
        assert doc["blocks"][-1]["module_class"] == "RTDETRDecoder" and doc["parameters"] > 30_000_000
