"""WP-S2: registry / user-model structure routes (contract 1.4.0).  CPU only; synthetic settings; no GPU, no /root."""
from __future__ import annotations

import json
import os
import pathlib

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
pytest.importorskip("torch")

from mbnas_core.console import model_services  # noqa: E402
from mbnas_server.api.contract_check import contract_errors  # noqa: E402

ETRI = "etri-controlled-cnn-transformer"
TINY = "nc: 1\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [[0], 1, Detect, [nc]]\n"


def auth():
    import harness
    return harness.auth()


def get(client, model_id):
    return client.get(f"/api/v1/registry/{model_id}/structure", headers=auth())


def post(client, model_id, body=None):
    return client.post(f"/api/v1/registry/{model_id}/structure-check", json=body, headers=auth())


@pytest.fixture
def no_build(monkeypatch):
    """Any model construction/inspection attempted by a GET fails the test."""
    def boom(*a, **k):
        raise AssertionError("a GET must never build or inspect a model")

    monkeypatch.setattr(model_services, "check_structure", boom)
    monkeypatch.setattr(model_services, "check_registry_yaml", boom)
    monkeypatch.setattr(model_services, "native_structure_for_arch", boom)
    monkeypatch.setattr("mbnas_core.search.structure.inspect_genotype", boom)
    monkeypatch.setattr("mbnas_model_runtime.genotype.inspect_genotype", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.structure_native.observe_native_structure", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.etri_hybrid.adapter.EtriHybridAdapter.describe_structure", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.etri_hybrid.adapter.EtriHybridAdapter._make", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.rfdetr_adapter.RfdetrAdapter.describe_structure", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.rfdetr_adapter.RfdetrAdapter._instance", boom)
    # WP-3C: since WP-3 every check runs in the structure sandbox CHILD, where the patches above cannot reach; starting
    # the sandbox at all is therefore guarded too (a GET must not even start a check)
    monkeypatch.setattr("mbnas_core.structure.sandbox.StructureSandbox.run", boom)


def test_all_three_paths_require_the_bearer_token(client):
    assert client.get("/api/v1/registry/yolo11n/structure").status_code == 401
    assert client.post("/api/v1/registry/yolo11n/structure-check", json={}).status_code == 401
    assert client.post("/api/v1/structure-check", json={"model_path": "/x.yaml"}).status_code == 401
    assert client.get("/api/v1/registry/yolo11n/structure", headers={"Authorization": "Bearer wrong"}).status_code in (401, 403)


def test_get_never_builds_and_is_declaration_only_before_any_check(client, api_settings, no_build):
    for model_id, backend, representation in (("rf-detr-nano", "rfdetr", "native_modules"),
                                              (ETRI, "etri_hybrid", "native_modules"),
                                              ("yolo11n", "ultralytics", "yaml_genes")):
        r = get(client, model_id)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["status"] == "UNVERIFIED" and doc["model_id"] == model_id
        assert doc["evidence_source"] == "declared_configuration"
        assert doc["backend"] == backend and doc["representation"] == representation
        assert [d["code"] for d in doc["diagnostics"]] == ["STRUCTURE_CHECK_NOT_RECORDED"]
        assert doc["observation"] is None and doc["architecture_hash"] is None
        assert all(c["status"] == "NOT_CHECKED" and c["evidence_level"] is None for c in doc["checks"])
        assert doc["flops_status"] == "NOT_MEASURED" and doc["edges_unobserved"] == []
        assert contract_errors("StructureReport", doc) == []
    assert get(client, "yolo11n").json()["blocks"] and get(client, "yolo11n").json()["scale"] == "n"
    nano = get(client, "rf-detr-nano").json()
    assert nano["blocks"] == [] and nano["native_config"]["class_name"] == "RFDETRNano"
    assert get(client, ETRI).json()["native_config"]["depth"] == 3
    import pathlib
    assert not (pathlib.Path(api_settings.output_root) / "registry_structure").exists()


def test_unknown_model_is_404_on_both_registry_paths(client):
    for r in (get(client, "no-such-model"), post(client, "no-such-model")):
        assert r.status_code == 404 and r.json()["code"] == "MODEL_NOT_FOUND"


def test_post_builds_saves_and_second_get_returns_the_saved_check(client, api_settings, monkeypatch):
    import pathlib

    r = post(client, ETRI)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS" and doc["parameters"] == 922_870 and doc["model_id"] == ETRI
    assert doc["evidence_source"] == "executed_structure_check" and doc["observation"]["device"] == "cpu"
    assert doc["representation"] == "native_modules" and doc["lineage_status"] == "NOT_APPLICABLE"
    assert contract_errors("StructureReport", doc) == []
    folder = pathlib.Path(api_settings.output_root) / "registry_structure" / ETRI
    saved = list(folder.glob("*.json"))
    assert [p.name for p in saved] == [doc["architecture_hash"].replace(":", "-") + ".json"]
    assert not list(folder.glob("*.tmp")) and json.loads(saved[0].read_text())["status"] == "PASS"
    # nothing may be rebuilt for the GET that follows
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt"))  # noqa: E731
    monkeypatch.setattr(model_services, "native_structure_for_arch", boom)
    monkeypatch.setattr("mbnas_model_runtime.adapters.etri_hybrid.adapter.EtriHybridAdapter._make", boom)
    again = get(client, ETRI)
    assert again.status_code == 200
    body = again.json()
    assert body["evidence_source"] == "saved_structure_check" and body["status"] == "PASS"
    assert body["architecture_hash"] == doc["architecture_hash"] and body["observation"]["checked_at"] == doc["observation"]["checked_at"]
    assert body["model_id"] == ETRI and contract_errors("StructureReport", body) == []


def test_post_yaml_registry_model_with_input_size_and_derived_scale(client, api_settings):
    pytest.importorskip("ultralytics")
    r = post(client, "yolo11n", {"input_size": 64})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS" and doc["input_size"] == 64 and doc["scale"] == "n"
    assert doc["representation"] == "yaml_genes" and doc["evidence_source"] == "executed_structure_check"
    saved = get(client, "yolo11n").json()
    assert saved["evidence_source"] == "saved_structure_check" and saved["yaml_hash"] == doc["yaml_hash"]
    assert saved["blocks"][0]["effective"]["c_out"] == doc["blocks"][0]["effective"]["c_out"]


def test_post_native_wrong_resolution_is_422_input_size_invalid_and_does_not_overwrite_evidence(client):
    """CONTRACTS_v2 section 2: a multiple of 32 that is not the backend's fixed resolution is a request error (WP-S8), not a 200 document."""
    ok = post(client, ETRI).json()
    bad = post(client, ETRI, {"input_size": 320})
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "INPUT_SIZE_INVALID" and "384" in bad.json()["message"]
    assert get(client, ETRI).json()["observation"]["checked_at"] == ok["observation"]["checked_at"]
    same = post(client, ETRI, {"input_size": 384})  # the fixed resolution itself is accepted
    assert same.status_code == 200 and same.json()["status"] == "PASS" and same.json()["input_size"] == 384


@pytest.mark.parametrize("model_id,size", [("rf-detr-nano", 448), ("rf-detr-nano", 512), ("rf-detr-small", 384),
                                           ("rf-detr-large", 640), (ETRI, 416)])
def test_post_native_fixed_resolution_mismatch_is_422_before_anything_is_built(client, api_settings, no_build, model_id, size):
    r = post(client, model_id, {"input_size": size})
    assert r.status_code == 422, r.text  # no_build: reaching the adapter would raise AssertionError (500)
    assert r.json()["code"] == "INPUT_SIZE_INVALID"
    import pathlib
    assert not (pathlib.Path(api_settings.output_root) / "registry_structure").exists()


def test_post_rfdetr_fixed_resolution_is_accepted_by_the_size_rule(client, monkeypatch):
    """The rule compares with the static preset resolution (Nano 384): the exact size reaches the adapter."""
    seen = {}
    doc = {"schema": "mbnas-structure/2", "backend": "rfdetr", "representation": "native_modules", "status": "UNVERIFIED",
           "blocks": [], "edges": [], "edges_unobserved": [], "diagnostics": [], "checks": []}

    def spy(arch, nc, input_size=None):
        seen["size"] = input_size
        return dict(doc)

    def sandbox_spy(self, op, payload):  # WP-3C: the adapter call happens in the sandbox child; spy on the hand-off
        seen["op"], seen["size"] = op, payload.get("input_size")
        return dict(doc)

    monkeypatch.setattr(model_services, "native_structure_for_arch", spy)
    monkeypatch.setattr("mbnas_core.structure.sandbox.StructureSandbox.run", sandbox_spy)
    assert post(client, "rf-detr-nano", {"input_size": 384}).status_code == 200
    assert seen["size"] == 384
    assert seen.get("op") in (None, "native_structure"), seen


@pytest.mark.parametrize("value", [0, 31, 33, 100, "64", 64.0, True])
def test_post_bad_input_size_is_422(client, value):
    r = post(client, ETRI, {"input_size": value})
    assert r.status_code == 422 and r.json()["code"] in ("INPUT_SIZE_INVALID", "VALIDATION_FAILED")
    if value != 64.0:
        assert r.json()["code"] == "INPUT_SIZE_INVALID"


def test_post_unknown_body_field_is_rejected(client):
    r = post(client, ETRI, {"input_size": 384, "extra": 1})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"


def test_user_yaml_inside_allowed_root_is_checked_not_stored(client, api_settings, tmp_roots):
    pytest.importorskip("ultralytics")
    path = tmp_roots["runs"] / "user.yaml"
    path.write_text(TINY)
    r = client.post("/api/v1/structure-check", json={"model_path": str(path), "input_size": 64, "class_count": 1}, headers=auth())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS" and doc["backend"] == "ultralytics" and doc["input_size"] == 64
    assert doc["evidence_source"] == "executed_structure_check" and contract_errors("StructureReport", doc) == []
    mismatch = client.post("/api/v1/structure-check", json={"model_path": str(path), "input_size": 64, "class_count": 5}, headers=auth())
    assert mismatch.status_code == 200 and mismatch.json()["status"] == "FAIL"
    assert any(d["code"] == "CLASS_COUNT_INVALID" for d in mismatch.json()["diagnostics"])
    assert not (tmp_roots["runs"] / "registry_structure").exists()


@pytest.mark.parametrize("make,code", [
    ("dataset_root", "PATH_OUTSIDE_ALLOWLIST"),
    ("missing", "PATH_NOT_FOUND"),
    ("relative", "PATH_NOT_ABSOLUTE"),
    ("traversal", "PATH_TRAVERSAL"),
    ("not_yaml", "MODEL_CONFIG_INVALID"),
    ("too_big", "MODEL_CONFIG_INVALID"),
    ("not_mapping", "MODEL_CONFIG_INVALID"),
])
def test_user_yaml_path_and_content_rejections(client, tmp_roots, make, code):
    runs, data = tmp_roots["runs"], tmp_roots["data"]
    path = {"dataset_root": str(data / "m.yaml"), "missing": str(runs / "nope.yaml"), "relative": "m.yaml",
            "traversal": str(runs / ".." / "m.yaml"), "not_yaml": str(runs / "m.txt"), "too_big": str(runs / "big.yaml"),
            "not_mapping": str(runs / "list.yaml")}[make]
    if make == "dataset_root":
        (data / "m.yaml").write_text(TINY)
    elif make == "not_yaml":
        (runs / "m.txt").write_text(TINY)
    elif make == "too_big":
        (runs / "big.yaml").write_text(TINY + "# " + "x" * (1024 * 1024))
    elif make == "not_mapping":
        (runs / "list.yaml").write_text("- 1\n- 2\n")
    r = client.post("/api/v1/structure-check", json={"model_path": path}, headers=auth())
    assert r.status_code == 422 and r.json()["code"] == code, r.text


def test_user_yaml_body_validation(client):
    for body in ({}, {"model_path": ""}, {"model_path": "/x.yaml", "bogus": 1}, {"model_path": 5}):
        r = client.post("/api/v1/structure-check", json=body, headers=auth())
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED", body
    r = client.post("/api/v1/structure-check", json={"model_path": "/x.yaml", "input_size": 50}, headers=auth())
    assert r.status_code == 422


def test_weights_dir_and_model_yaml_roots_are_allowed_roots(tmp_roots, tmp_path):
    pytest.importorskip("ultralytics")
    import harness

    # WP-1 (DD-A13): CoreSettings.model_yaml_roots replaces the removed etri_nas_root allow root
    # WP-3 contract section 1.6 (WP-3A adaptation): user YAML roots = model_yaml_roots + output_roots only; weights_dir
    # (a checkpoint root) is no longer a YAML root and is rejected like any other outside path
    weights, uploads = tmp_path / "weights", tmp_path / "uploads"
    weights.mkdir()
    uploads.mkdir()
    settings = harness.make_settings(tmp_roots, weights_dir=str(weights), model_yaml_roots=[str(uploads)])
    for root in (uploads, pathlib.Path(settings.output_roots[0])):
        p = root / "m.yaml"
        p.write_text(TINY)
        doc = model_services.check_user_model(settings, str(p), 64, 1)
        assert doc["status"] == "PASS"
    for outside in (tmp_path / "other.yaml", weights / "m.yaml"):
        outside.write_text(TINY)
        with pytest.raises(model_services.StructureRequestError) as exc:
            model_services.check_user_model(settings, str(outside), 64, 1)
        assert exc.value.code == "PATH_OUTSIDE_ALLOWLIST"


def test_the_no_build_guard_really_trips_on_a_check(client, core_client, combined, no_build, caplog):
    """Negative control: the same guard that GET passes under must fail the explicit POST.

    WP-3C: through the gateway the core's exception becomes a relayed 500 (two ASGI apps; the gateway TestClient never
    sees the core's exception object), so the row checks that answer AND, on the core app directly (core-only
    behaviour, ``core_client`` with the gateway's server principal), that the guard's AssertionError is what ran."""
    import logging

    with caplog.at_level(logging.ERROR, logger="mbnas_core.api"):
        r = post(client, ETRI)
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR" and r.json()["location"] == "core", r.text
    assert any("never build" in (rec.exc_text or "") or "never build" in str(rec.exc_info[1] if rec.exc_info else "")
               for rec in caplog.records), [rec.getMessage() for rec in caplog.records]
    with pytest.raises(AssertionError, match="never build"):
        core_client.post(f"/core/v1/console/registry/{ETRI}/structure-check", json=None,
                         headers={"Authorization": f"Bearer {combined.tokens['server']}"})


# -- WP-S8 / DD-41: GET /registry/{id}/structure is import-light (no rfdetr, no CUDA initialisation) -----------------------
# The subprocess runs the real GET path with the CUDA environment of the calling process (visible in the standard gate, hidden in
# CUDA-free hosts) and performs NO CUDA operation itself: it only reads torch.cuda.is_initialized().  It also records every call of
# torch.cuda._lazy_init (the entry point rfdetr's accelerator probe used, evidence_impl/F/cuda_init_trace_rfdetr_declared_config.txt),
# so the check also bites on a host where CUDA is hidden and the call fails instead of initialising.
_GET_DRIVER = r'''
import copy, json, os, sys, tempfile
from pathlib import Path
tests_dir, server_root = sys.argv[1], sys.argv[2]  # server_root = TARGET_ROOT (WP-1 layout)
sys.path.append(str(Path(tests_dir) / "_shared"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import torch
lazy_calls = []
if hasattr(torch.cuda, "_lazy_init"):
    _orig = torch.cuda._lazy_init
    def _spy():
        lazy_calls.append(1)
        return _orig()
    torch.cuda._lazy_init = _spy
import harness
from fastapi.testclient import TestClient
from mbnas_core.api.app import create_app
tmp = Path(tempfile.mkdtemp(prefix="s8_get_"))
roots = {"data": tmp / "data", "runs": tmp / "runs"}
for p in roots.values():
    p.mkdir()
settings = harness.make_settings(roots)
doc = json.loads((Path(server_root) / "config" / "examples" / "model_registry.example.json").read_text())
registry = {}
for entry in doc["registry"]:
    e = copy.deepcopy(entry)
    e["status"], e["reason"] = "AVAILABLE", "synthetic test registry"
    registry[e["id"]] = e
devices = [{"id": "host-gpu-0", "uuid": "GPU-synthetic", "name": "synthetic", "aliases": ["cuda:0"]}]
services = harness.make_services(settings, registry, devices)
out = {"responses": {}}
with TestClient(create_app(settings, services)) as c:
    for model_id in ("rf-detr-nano", "rf-detr-small", "rf-detr-medium", "rf-detr-large", "etri-controlled-cnn-transformer", "yolo11n"):
        r = c.get(f"/api/v1/registry/{model_id}/structure", headers=harness.auth())
        body = r.json()
        out["responses"][model_id] = {"status_code": r.status_code, "status": body.get("status"), "blocks": len(body.get("blocks") or []),
                                      "backend": body.get("backend"), "evidence_source": body.get("evidence_source"),
                                      "native_config": body.get("native_config"), "observation": body.get("observation"),
                                      "diagnostics": [d["code"] for d in body.get("diagnostics") or []]}
out["modules_present"] = sorted(m for m in ("rfdetr", "ultralytics", "transformers", "pytorch_lightning", "lightning") if m in sys.modules)
out["cuda_initialised"] = bool(torch.cuda.is_initialized())
out["cuda_lazy_init_calls"] = len(lazy_calls)
out["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
print("S8_RESULT " + json.dumps(out))
'''


def _run_get_driver(tmp_path_factory):
    import subprocess
    import sys
    from pathlib import Path

    tests_dir = Path(__file__).resolve().parents[2]
    script = tmp_path_factory.mktemp("s8_get_driver") / "driver.py"
    script.write_text(_GET_DRIVER)
    env = dict(os.environ)
    proc = subprocess.run([sys.executable, str(script), str(tests_dir), str(tests_dir.parent)], capture_output=True, text=True,
                          env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("S8_RESULT ")]
    assert line, (proc.stdout[-2000:], proc.stderr[-2000:])
    return json.loads(line[-1][len("S8_RESULT "):])


@pytest.fixture(scope="module")
def get_driver_result(tmp_path_factory):
    return _run_get_driver(tmp_path_factory)


def test_get_does_not_initialise_cuda_when_cuda_visible(get_driver_result):
    """DD-41: the GET path must not touch the accelerator.  The subprocess keeps the caller's CUDA_VISIBLE_DEVICES (unset/visible in
    the standard gate) and runs no CUDA operation, so torch.cuda.is_initialized() can only be True if library code initialised it."""
    res = get_driver_result
    assert all(r["status_code"] == 200 for r in res["responses"].values()), res["responses"]
    assert res["cuda_initialised"] is False
    assert res["cuda_lazy_init_calls"] == 0, "the GET path probed the accelerator (torch.cuda._lazy_init)"
    # rfdetr's import-time device probe is the known initialiser (DD-41); with CUDA hidden it fails silently, so also check its import
    assert "rfdetr" not in res["modules_present"]


def test_get_rfdetr_presets_come_from_the_static_table_without_importing_rfdetr(get_driver_result):
    from mbnas_model_runtime.adapters.rfdetr_adapter import RFDETR_PRESETS

    res = get_driver_result
    assert "rfdetr" not in res["modules_present"], res["modules_present"]  # (the yolo11n GET of the same run legitimately loads ultralytics)
    for model_id, cls, dec, resolution in (("rf-detr-nano", "RFDETRNano", 2, 384), ("rf-detr-small", "RFDETRSmall", 3, 512),
                                           ("rf-detr-medium", "RFDETRMedium", 4, 576), ("rf-detr-large", "RFDETRLarge", 4, 704)):
        r = res["responses"][model_id]
        assert r["status"] == "UNVERIFIED" and r["backend"] == "rfdetr" and r["evidence_source"] == "declared_configuration"
        assert r["blocks"] == 0 and r["observation"] is None and r["diagnostics"] == ["STRUCTURE_CHECK_NOT_RECORDED"]
        cfg = r["native_config"]  # declared-only document: the preset defaults, never an observation
        assert cfg["class_name"] == cls and cfg["dec_layers"] == dec and cfg["resolution"] == resolution
        assert cfg["patch_size"] == 16 and cfg["hidden_dim"] == 256 and cfg["out_feature_indexes"] == [3, 6, 9, 12]
        assert cfg["projector_scale"] == ["P4"] and cfg["num_queries"] == 300
        assert {k: (tuple(v) if isinstance(v, list) else v) for k, v in cfg.items() if k != "class_name"} == RFDETR_PRESETS[cls]
    etri = res["responses"]["etri-controlled-cnn-transformer"]
    assert etri["backend"] == "etri_hybrid" and etri["native_config"]["depth"] == 3 and etri["blocks"] == 0


_TABLE_DRIVER = r'''
import json, os, sys
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import rfdetr.config as rc
from mbnas_model_runtime.adapters.rfdetr_adapter import RFDETR_PRESETS, RfdetrAdapter
diff = {}
for cls in RFDETR_PRESETS:
    cfg_cls = getattr(rc, cls + "Config")
    live = {}
    for key in RfdetrAdapter._CONFIG_KEYS:
        field = cfg_cls.model_fields.get(key)
        if field is not None and not field.is_required():
            val = field.default
            live[key] = list(val) if isinstance(val, (list, tuple)) else val
    static = RfdetrAdapter(cls).declared_config()
    static.pop("class_name")
    if live != static:
        diff[cls] = {"live": live, "static": static}
print("S8_TABLE " + json.dumps({"diff": diff, "version": getattr(__import__("rfdetr"), "__version__", None)}))
'''


def test_rfdetr_preset_table_matches_installed_library(tmp_path):
    """The static table is a copy of rfdetr's preset defaults; compare it with the installed package (CUDA hidden in the subprocess)."""
    import subprocess
    import sys

    pytest.importorskip("rfdetr")
    script = tmp_path / "table_driver.py"
    script.write_text(_TABLE_DRIVER)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("S8_TABLE ")]
    assert line, proc.stdout[-1500:]
    assert json.loads(line[-1][len("S8_TABLE "):])["diff"] == {}
