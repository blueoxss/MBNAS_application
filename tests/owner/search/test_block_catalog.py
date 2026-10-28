"""WP-S6 (DD-38): the static block design-origin catalog.  CPU only; no GPU, no dataset, no network.

Covers: table honesty (introduced_in needs a reference, unknown is said), completeness against the config pools and the
installed cfg tree, source_path equals what ``inspect`` finds in the installed packages, the GET path stays import-light,
catalog attach on YAML and native structure documents, and the summary arithmetic.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from mbnas_model_runtime import block_catalog as bc  # noqa: E402

KEYS = {"origin_family", "introduced_in", "role", "reference", "notes"}
SRC = os.pathsep.join(str(Path(__file__).resolve().parents[3] / p / "src")  # WP-1 package sources
                      for p in ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core"))


def _site_relative(path: str) -> str | None:
    resolved = Path(path).resolve()
    for base in bc._site_dirs():
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


# -- the table itself -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("table_name", ["ENTRIES", "NATIVE_ENTRIES"])
def test_every_entry_has_the_fields_and_no_unreferenced_introduction(table_name):
    table = getattr(bc, table_name)
    assert table
    for key, entry in table.items():
        assert set(entry) == KEYS, key
        assert isinstance(entry["role"], str) and entry["role"].strip(), key
        if entry["introduced_in"] is not None:
            assert entry["origin_family"], f"{key}: an introduction version needs a family"
            assert entry["reference"], f"{key}: introduced_in is set without a reference (no guesses)"
        if entry["reference"] is not None:
            assert isinstance(entry["reference"], str) and entry["reference"].strip(), key


def test_unknown_parts_are_said_out_loud():
    """A null family or a null introduction is written down as 'unknown' in notes (torch layers have no introduction to name)."""
    for table in (bc.ENTRIES, bc.NATIVE_ENTRIES):
        for key, entry in table.items():
            if entry["origin_family"] == bc.TORCH_FAMILY:
                continue
            if entry["origin_family"] is None or entry["introduced_in"] is None:
                assert entry["notes"] and "unknown" in entry["notes"], f"{key}: null field without the word 'unknown' in notes"


def test_references_are_urls_or_the_etri_source_path():
    for key, entry in {**bc.ENTRIES, **bc.NATIVE_ENTRIES}.items():
        ref = entry["reference"]
        if ref is None:
            continue
        assert ref.startswith("https://") or ref.startswith("mbnas_model_runtime/"), (key, ref)  # contract §6.8


def test_etri_mixed_blocks_are_catalogued_with_their_source_file():
    for name, role_word in (("NASPatchMerge", "patch merging"), ("NASWindowTransformer", "window attention"),
                            ("NASGlobalTransformer", "global attention")):
        entry = bc.ENTRIES[name]
        assert entry["origin_family"] == "ETRI mixed operator pool"
        assert entry["reference"] == bc.ETRI_MIXED_SOURCE  # contract §6.8: the vendored operator file
        assert bc.ETRI_MIXED_SOURCE.startswith("mbnas_model_runtime/operators/etri_mixed_blocks.py")
        assert role_word in entry["role"].lower()
        assert entry["introduced_in"] is None  # IMPLEMENTATION_VERSION is the current version, not an introduction


def test_normalised_lookup_and_unknown_module():
    assert bc.lookup("nn.Upsample")[0] == "Upsample" and bc.lookup("torch.nn.Identity")[0] == "Identity"
    assert bc.lookup("C2f")[1]["origin_family"] == "YOLOv8"
    assert bc.lookup("NoSuchBlock") is None and bc.lookup(None) is None and bc.lookup("") is None
    doc = bc.catalog_for_yaml_block("NoSuchBlock", resolve_source=True)
    assert doc["origin_family"] is None and doc["role"] is None and doc["source_path"] is None
    assert "unknown" in doc["notes"] and doc["catalog_key"] is None


def test_summary_counts_families_and_unknown():
    blocks = [{"catalog": bc.catalog_for_yaml_block(n)} for n in ("Conv", "Conv", "C3k2", "NASPatchMerge", "Mystery", "C2")]
    blocks.append({})  # a block without a catalog is unknown too
    s = bc.catalog_summary(blocks)
    assert s["by_origin_family"] == {"YOLOv5": 2, "ETRI mixed operator pool": 1, "YOLO11": 1}
    assert s["unknown"] == 3 and sum(s["by_origin_family"].values()) + s["unknown"] == len(blocks)


# -- completeness ---------------------------------------------------------------------------------------------------
def test_pool_constants_are_in_the_table():
    from mbnas_core.search import space, structure

    wanted = (set(space.FEATURE_OPS) | set(space.NATIVE_FEATURE_OPS) | set(space.DOWN_OPS) | set(space.SLOT_NATIVE_FEATURE)
              | set(structure.DETECTION_MODULES) | set(structure.CHANNEL_MODULES) | {"Concat", "SPPF", "Conv"})
    assert wanted - set(bc.ENTRIES) == set()


def _cfg_modules(names: list[str] | None = None) -> dict[str, set[str]]:
    ultralytics = pytest.importorskip("ultralytics")
    import yaml

    root = Path(ultralytics.__file__).parent / "cfg" / "models"
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.yaml")):
        if names is not None and path.stem not in names:
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for section in ("backbone", "head"):
            for row in doc.get(section) or []:
                found.setdefault(str(row[2]), set()).add(path.stem)
    return found


def test_config_pool_cfg_modules_are_in_the_table():
    """yolo11 / yolo26 (the n-x scales share one cfg) and rtdetr-l/-x, the families of DD-13 (a) and (b)."""
    found = _cfg_modules(["yolo11", "yolo26", "rtdetr-l", "rtdetr-x"])
    assert {"Conv", "C3k2", "C2PSA", "SPPF", "Detect", "RTDETRDecoder", "HGBlock", "AIFI", "RepC3"} <= set(found)
    missing = {name: sorted(cfgs) for name, cfgs in found.items() if bc.lookup(name) is None}
    assert missing == {}


def test_every_module_of_the_installed_cfg_tree_is_in_the_table():
    import ultralytics

    found = _cfg_modules()
    missing = {name: sorted(cfgs)[:3] for name, cfgs in found.items() if bc.lookup(name) is None}
    assert missing == {}, f"ultralytics {ultralytics.__version__}: add these modules to search/block_catalog.py (or leave a null-family entry)"


# -- source_path ----------------------------------------------------------------------------------------------------
def test_source_path_matches_the_installed_ultralytics_modules():
    pytest.importorskip("ultralytics")
    checked = 0
    for key, impl in bc._IMPL.items():
        if not impl or not impl.startswith("ultralytics."):
            continue
        module_name, attr = impl.split(":")
        expected_file = inspect.getsourcefile(getattr(importlib.import_module(module_name), attr))
        expected = _site_relative(expected_file)
        got = bc.resolve_source_path(impl)
        assert got is not None and got == expected, key
        assert got.startswith("ultralytics/nn/modules/") and got.endswith(".py")
        doc = bc.catalog_for_yaml_block(key, resolve_source=True)
        assert doc["source_path"] == expected
        assert bc.catalog_for_yaml_block(key)["source_path"] is None  # the GET path never resolves
        checked += 1
    assert checked >= 50


def test_etri_blocks_and_the_ultralytics_missing_silence_have_no_source_path():
    for name in ("NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer", "Silence"):
        assert bc.catalog_for_yaml_block(name, resolve_source=True)["source_path"] is None
    assert bc.resolve_source_path(None) is None and bc.resolve_source_path("no.such.package:X") is None


def test_torch_layers_resolve_to_site_packages():
    pytest.importorskip("torch")
    assert bc.catalog_for_yaml_block("nn.Upsample", resolve_source=True)["source_path"] == "torch/nn/modules/upsampling.py"
    assert bc.catalog_for_yaml_block("nn.MaxPool2d", resolve_source=True)["source_path"] == "torch/nn/modules/pooling.py"


# -- the GET path stays import-light --------------------------------------------------------------------------------------
def test_importing_the_catalog_and_serving_the_static_table_imports_no_heavy_runtime():
    code = (
        "import sys, json\n"
        "from mbnas_model_runtime import block_catalog as bc\n"
        "t = bc.static_table(); json.dumps(t)\n"
        "d = bc.catalog_for_yaml_block('C3k2'); n = bc.catalog_for_native_block('rfdetr', 'DinoV2')\n"
        "assert d['source_path'] is None and n['source_path'] is None\n"
        "bc.catalog_summary([{'catalog': d}, {'catalog': n}])\n"
        "print(json.dumps(sorted(m for m in ('torch','ultralytics','rfdetr','transformers') if m in sys.modules)))\n")
    env = {**os.environ, "PYTHONPATH": str(SRC), "CUDA_VISIBLE_DEVICES": ""}
    out = subprocess.run([sys.executable, "-I", "-B", "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_declared_genotype_document_is_import_light_and_carries_the_static_catalog():
    code = (
        "import sys, json\n"
        "from mbnas_model_runtime.genotype import Genotype\n"
        "from mbnas_core.search.structure import describe_genotype\n"
        "g = Genotype.from_yaml_dict({'nc': 2, 'depth_multiple': 0.5, 'width_multiple': 0.5, "
        "'backbone': [[-1, 1, 'Conv', [32, 3, 2]], [-1, 2, 'C2f', [64]]], "
        "'head': [[[0, 1], 1, 'Concat', [1]], [[2], 1, 'Detect', ['nc']]]}, family='f', template_id='t')\n"
        "doc = describe_genotype(g)\n"
        "print(json.dumps({'heavy': sorted(m for m in ('torch','ultralytics','rfdetr') if m in sys.modules), "
        "'cat': [b['catalog']['origin_family'] for b in doc['blocks']], 'src': [b['catalog']['source_path'] for b in doc['blocks']], "
        "'sum': doc['catalog_summary']}))\n")
    env = {**os.environ, "PYTHONPATH": str(SRC), "CUDA_VISIBLE_DEVICES": ""}
    out = subprocess.run([sys.executable, "-I", "-B", "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["cat"] == ["YOLOv5", "YOLOv8", "YOLOv5", "YOLOv8"]
    assert got["src"] == [None, None, None, None]
    assert got["sum"] == {"by_origin_family": {"YOLOv5": 2, "YOLOv8": 2}, "unknown": 0}
    if got["heavy"]:  # Genotype itself may import a runtime on this host; the catalog must not be the reason
        pytest.skip(f"genotype import pulls {got['heavy']} before the catalog is used")


# -- YAML documents -------------------------------------------------------------------------------------------------------
@pytest.fixture
def branch_genotype():
    from mbnas_model_runtime.genotype import Genotype

    return Genotype.from_yaml_dict({
        "nc": 2, "depth_multiple": 0.5, "width_multiple": 0.5,
        "backbone": [[-1, 1, "Conv", [32, 3, 2]], [-1, 2, "C2f", [64]]],
        "head": [[[0, 1], 1, "Concat", [1]], [-1, 1, "Conv", [64, 3, 2]], [[3], 1, "Detect", ["nc"]]],
    }, family="cpu-branch", template_id="cpu-branch:native")


def test_declared_document_has_catalog_without_source_path(branch_genotype):
    from mbnas_core.search.structure import describe_genotype

    doc = describe_genotype(branch_genotype)
    assert [b["catalog"]["origin_family"] for b in doc["blocks"]] == ["YOLOv5", "YOLOv8", "YOLOv5", "YOLOv5", "YOLOv8"]
    assert all(b["catalog"]["source_path"] is None for b in doc["blocks"])
    assert doc["catalog_summary"] == {"by_origin_family": {"YOLOv5": 3, "YOLOv8": 2}, "unknown": 0}


def test_explicit_check_resolves_source_path_and_keeps_the_contract(branch_genotype):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_core.search import structure

    old = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        doc = structure.check_structure(branch_genotype, input_size=64, class_count=2)
    finally:
        torch.set_num_threads(old)
    assert doc["status"] == "PASS", doc["diagnostics"]
    paths = {b["module"]: b["catalog"]["source_path"] for b in doc["blocks"]}
    assert paths == {"Conv": "ultralytics/nn/modules/conv.py", "C2f": "ultralytics/nn/modules/block.py",
                     "Concat": "ultralytics/nn/modules/conv.py", "Detect": "ultralytics/nn/modules/head.py"}
    assert doc["catalog_summary"]["unknown"] == 0
    assert contract_errors("StructureReport", doc) == []


def test_failed_and_unverified_documents_also_carry_the_catalog(branch_genotype):
    from mbnas_core.search import structure

    doc = structure.check_structure(branch_genotype, input_size=65)  # INPUT_SIZE_INVALID: declared document, status FAIL
    assert doc["status"] == "FAIL"
    assert doc["catalog_summary"]["by_origin_family"] and all("catalog" in b for b in doc["blocks"])


def test_mixed_pool_rows_map_to_the_etri_family_and_the_summary_counts_them():
    """Declared rows of the ETRI mixed pool (no parser needed): NAS* blocks are the ETRI family, not 'unknown'."""
    from mbnas_model_runtime.genotype import Genotype
    from mbnas_core.search.structure import describe_genotype

    genotype = Genotype.from_yaml_dict({
        "nc": 1, "depth_multiple": 0.5, "width_multiple": 0.5,
        "backbone": [[-1, 1, "Conv", [32, 3, 2]], [-1, 1, "NASPatchMerge", [64, 2]],
                     [-1, 2, "NASWindowTransformer", [64, 4, 7, 2.0]], [-1, 1, "NASPatchMerge", [128, 2]],
                     [-1, 1, "NASGlobalTransformer", [128, 4, 1024, 2.0]], [-1, 1, "SPPF", [128, 5]]],
        "head": [[[2, 4, 5], 1, "Detect", ["nc"]]],
    }, family="mixed", template_id="mixed:test")
    doc = describe_genotype(genotype)
    families = [b["catalog"]["origin_family"] for b in doc["blocks"]]
    assert families.count("ETRI mixed operator pool") == 4
    assert doc["catalog_summary"] == {"by_origin_family": {"ETRI mixed operator pool": 4, "YOLOv5": 2, "YOLOv8": 1}, "unknown": 0}
    assert all(b["catalog"]["source_path"] is None for b in doc["blocks"])


# -- native documents -----------------------------------------------------------------------------------------------------
def _arch(spec):
    from mbnas_model_runtime.adapters.base import ArchSpec

    return ArchSpec(spec.id, spec.family, spec.backend, spec.config)


def test_etri_hybrid_document_blocks_are_catalogued_with_path_rules():
    pytest.importorskip("torch")
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_model_runtime.registry import SPEC_BY_ID

    doc = EtriHybridAdapter().describe_structure(_arch(SPEC_BY_ID["etri-controlled-cnn-transformer"]), 1, None)
    assert doc["status"] == "PASS"
    by_path = {b["native_path"]: b["catalog"] for b in doc["blocks"]}
    assert by_path["embedding.layers.0"]["catalog_key"] == "etri_hybrid:CNNStem"
    assert by_path["position"]["catalog_key"] == "etri_hybrid:position"
    assert by_path["class_head"]["catalog_key"] == by_path["box_head"]["catalog_key"] == "etri_hybrid:head"
    assert by_path["spatial_encoder.layers.0"]["catalog_key"] == "etri_hybrid:TransformerEncoderLayer"
    assert by_path["spatial_encoder.layers.0"]["source_path"] == "torch/nn/modules/transformer.py"
    assert by_path["norm"]["origin_family"] == "PyTorch torch.nn"
    assert doc["catalog_summary"]["unknown"] == 0
    assert set(doc["catalog_summary"]["by_origin_family"]) == {"ETRI controlled hybrid", "PyTorch torch.nn"}
    assert contract_errors("StructureReport", doc) == []


def test_rfdetr_document_blocks_are_catalogued():
    pytest.importorskip("torch")
    pytest.importorskip("rfdetr")
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for

    spec = SPEC_BY_ID["rf-detr-nano"]
    doc = adapter_for(spec).describe_structure(_arch(spec), 1, None)
    assert doc["status"] == "PASS"
    by_path = {b["native_path"]: b["catalog"] for b in doc["blocks"]}
    assert by_path["backbone.0.encoder"]["origin_family"] == "DINOv2"
    assert by_path["backbone.0.encoder"]["source_path"] == "rfdetr/models/backbone/dinov2.py"
    assert by_path["backbone.0.projector"]["origin_family"] == "LW-DETR"
    assert by_path["transformer.decoder.layers.0"]["origin_family"] == "LW-DETR"
    assert by_path["transformer.decoder.layers.0"]["source_path"] == "rfdetr/models/transformer.py"
    summary = doc["catalog_summary"]
    assert summary["unknown"] == 0 and sum(summary["by_origin_family"].values()) == len(doc["blocks"])
    assert contract_errors("StructureReport", doc) == []


def test_declared_native_document_has_an_empty_summary():
    from mbnas_model_runtime.adapters.structure_native import declared_native_document

    doc = declared_native_document("rfdetr", "rf-detr-nano", {"num_classes": 1}, diagnostics=[])
    assert doc["blocks"] == [] and doc["catalog_summary"] == {"by_origin_family": {}, "unknown": 0}
