"""Set 3 fixes S3-F1 (per-model device/TensorRT capability + source identity) and S3-F2 (family adapter classes).

All CPU / torch-free: the registry must load without importing torch, and the adapter classes resolve without
building a model.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mbnas_model_runtime.benchmark import capability
from mbnas_model_runtime.registry import adapter_for
from mbnas_core.registry import build_entry, load_registry, scan_trt_evidence
from mbnas_model_runtime.registry.specs import ETRI_ID, SPEC_BY_ID, SPECS
from mbnas_contracts.schemas.api import RegistryEntry

REAL_ROOT = os.environ["MBNAS_TEST_RUNS_ROOT"]  # WP-1 contract §9: runs root of this test run (bench evidence = host data)
EVIDENCE = Path(REAL_ROOT) / "evidence"
DECLARED_ABSENT = ("jetson-orin-nx", "jetson-orin-nano", "jetson-agx-orin")
META = {"path": "/p.json", "created_at": "t", "versions": {"ultralytics": "8.4.173", "rfdetr": "1.11.2"}}


def _bench_doc(created: str, weights: str, name: str, *, engines: dict | None, backend: str = "ultralytics") -> dict:
    runtimes = {"pytorch:FP32": {"status": "MEASURED"}}
    for p, e in (engines or {}).items():
        runtimes[f"tensorrt:{p}"] = {"status": "MEASURED" if e["status"] == "BUILT" else "FAILED"}
    return {"kind": "bench_host_gpu", "created_at": created, "evidence_type": "real_gpu (host A100), measured",
            "subjects": [{"weights": weights, "job_result": {"status": "SUCCEEDED"}, "record": {
                "subject": {"name": name, "backend": backend,
                            "source": {"kind": "official_weights", "path": f"/w/{weights}"}},
                "device": {"name": "NVIDIA A100 80GB PCIe", "uuid": "GPU-x"},
                "engines": engines or {}, "runtimes": runtimes}}]}


# -- S3-F1 ------------------------------------------------------------------------------------------------------
def test_trt_evidence_merges_newest_per_model_and_skips_files_without_engines(tmp_path: Path) -> None:
    ev = tmp_path / "evidence"
    ev.mkdir()
    built = {"FP16": {"status": "BUILT", "sha256": "a" * 64, "tensorrt_version": "10.13.3.9"},
             "FP32": {"status": "BUILT", "sha256": "b" * 64, "tensorrt_version": "10.13.3.9"}}
    (ev / "bench_host_gpu_1.json").write_text(json.dumps(_bench_doc("20261006_100000", "yolo11s.pt", "yolo11s",
                                                                    engines=built)))
    (ev / "bench_host_gpu_2.json").write_text(json.dumps(_bench_doc("20261006_200000", "yolo11s.pt", "yolo11s",
                                                                    engines=None)))  # newer, but no TRT run
    found = scan_trt_evidence(str(tmp_path), include_canonical=False)
    assert found["yolo11s"]["path"].endswith("bench_host_gpu_1.json")
    assert found["yolo11s"]["engines"]["FP16"]["status"] == "BUILT"


def _real_bench_evidence_present() -> bool:
    try:
        return any(EVIDENCE.glob("bench_host_gpu_*.json"))
    except OSError:  # unreadable /root fallback root
        return False


@pytest.mark.skipif(not _real_bench_evidence_present(),
                    reason="no real bench_host_gpu evidence under $MBNAS_TEST_RUNS_ROOT/evidence on this host",
                    host_data=True)  # contract §9 host_data
def test_entry_device_capability_and_export_trt_from_evidence() -> None:
    trt = scan_trt_evidence(REAL_ROOT)
    for mid in ("yolo11n", "yolo26n", "rt-detr-l", "rf-detr-nano"):
        assert mid in trt, f"bench evidence for {mid} not merged"
    entries = {s.id: build_entry(s, None, META, {}, trt) for s in SPECS}
    for mid, e in entries.items():
        caps = e["capabilities"]
        assert caps["export_trt"]["status"] != "NOT_RUN_BY_REGISTRY"
        devices = caps["devices"]
        # DD-A49 (c): the host GPU target is named after this host's GPU (NVML/torch), never a fixed product name
        assert devices["host-gpu-0"]["name"] == capability.declared_targets()["host-gpu-0"]["name"]
        for t in DECLARED_ABSENT:
            assert devices[t]["status"] == "UNAVAILABLE" and devices[t]["reason"], (mid, t)
        RegistryEntry.model_validate(e)
    y = entries["yolo11n"]["capabilities"]
    assert y["export_trt"]["status"] == "VERIFIED_GPU" and Path(y["export_trt"]["evidence"]).is_file()
    assert set(y["export_trt"]["engines"]) == {"FP16", "FP32"}
    assert y["devices"]["host-gpu-0"]["status"] == "VERIFIED_TRT"
    assert entries["rt-detr-l"]["capabilities"]["export_trt"]["status"] == "VERIFIED_GPU"
    # no proxy: a model without its own TRT evidence is NOT_RUN with a reason (no family representative)
    x = entries["yolo11x"]["capabilities"]
    assert x["export_trt"]["status"] == "NOT_RUN" and x["export_trt"]["reason"]
    assert x["devices"]["host-gpu-0"]["status"] == "NOT_RUN" and x["export_trt"].get("evidence") is None
    etri = entries[ETRI_ID]["capabilities"]["export_trt"]
    assert etri["status"] == "VERIFIED_ADAPTER_PATH" and "qa_etri_hybrid_tensorrt_record" in etri["evidence"]


def test_source_identity_per_entry() -> None:
    for s in SPECS:
        src = build_entry(s, None, META, {}, {})["evidence"]["source"]
        if s.backend in ("ultralytics", "rfdetr"):
            assert src["kind"] == "pip"
            assert src["package"] == s.backend and src["version"]
            assert len(src["record_sha256"]) == 64 and src["record"].endswith("RECORD")
        else:
            assert src["kind"] == "etri_notebook"
            assert src["notebook_blob_sha1"] == "629ce856b4c6ea337edc8943b9e65ed91ef60e27"
            assert src["etri_audited_head"] == "1d3a459aa6356e11b43c0e58bc778030ba78f950"


def test_registry_load_stays_torch_free(tmp_path: Path) -> None:
    # 2026-10-22: use the real evidence root when it is readable (the original scope: real probe/evidence parsing must
    # stay torch-free); only where it is unreadable (e.g. /root on this host) fall back to an empty temp dir. The registry
    # always appends the canonical root (DEFAULT_OUTPUT_ROOT, a src constant that raises PermissionError when unreadable),
    # so the child overrides that constant only when it is not accessible. (src is untouched.)
    root = REAL_ROOT if os.access(REAL_ROOT, os.R_OK | os.X_OK) else str(tmp_path)
    code = ("import os, sys; import mbnas_core.registry as r; "
            "r.DEFAULT_OUTPUT_ROOT = r.DEFAULT_OUTPUT_ROOT if os.access(r.DEFAULT_OUTPUT_ROOT, os.R_OK | os.X_OK) "
            "else sys.argv[1]; e=r.load_registry(sys.argv[1]); "
            "assert e['yolo11n']['capabilities']['devices']; "
            "bad=[m for m in ('torch','ultralytics','rfdetr','tensorrt') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code, root], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# -- S3-F2 ------------------------------------------------------------------------------------------------------
def test_four_families_resolve_to_distinct_adapter_classes() -> None:
    classes = {mid: type(adapter_for(SPEC_BY_ID[mid])) for mid in ("yolo11s", "yolo26s", "rt-detr-l", "rf-detr-small",
                                                                   ETRI_ID)}
    assert classes["yolo11s"] is classes["yolo26s"]  # one YOLO detect class (see its docstring)
    fams = [classes["yolo11s"], classes["rt-detr-l"], classes["rf-detr-small"], classes[ETRI_ID]]
    assert len(set(fams)) == 4, [c.__name__ for c in fams]
    assert classes["rt-detr-l"].__name__ == "RtdetrAdapter"
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    assert issubclass(classes["rt-detr-l"], UltralyticsAdapter) and issubclass(classes["yolo11s"], UltralyticsAdapter)
    assert classes["rt-detr-l"] is not UltralyticsAdapter and classes["yolo11s"] is not UltralyticsAdapter


def test_capabilities_expose_adapter_class() -> None:
    for s in SPECS:
        caps = build_entry(s, None, META, {}, {})["capabilities"]
        assert caps["adapter_class"] == type(adapter_for(s)).__qualname__
        assert caps["adapter_module"] == type(adapter_for(s)).__module__


@pytest.mark.parametrize("family, variant, name", [("YOLO11", "detect", "YoloDetectAdapter"),
                                                   ("YOLO26", "detect", "YoloDetectAdapter"),
                                                   ("Hybrid", "rtdetr", "RtdetrAdapter")])
def test_legacy_constructor_dispatches_to_family_class(family: str, variant: str, name: str) -> None:
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    a = UltralyticsAdapter(family, variant)
    assert type(a).__name__ == name and a.variant == variant and a.family == family
    with pytest.raises(ValueError):
        UltralyticsAdapter(family, "nope")
