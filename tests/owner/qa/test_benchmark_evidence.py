"""QA: the REAL host-GPU benchmark evidence files and one fresh ETRI-hybrid run (criterion set 8).

* ``test_recorded_*`` read ``/root/detection/MBNAS_runs/evidence/bench_host_gpu_*.json`` (written by
  ``scripts/bench_host_gpu.py`` on the A100) and check that every record carries the full condition provenance, engine
  checksums, accuracy deltas against the PyTorch reference and a BLOCKED, proxy-free Jetson target. They skip when no
  evidence exists on the host.
* ``test_etri_hybrid_*_gpu`` trains a tiny ETRI hybrid for one epoch, exports ONNX and builds TensorRT FP32/FP16
  engines on the A100 through ``run_benchmark`` (the ETRI family had no TensorRT evidence before this test).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path

import pytest

# WP-1 contract §9: runs root of this test run (conftest_common sets MBNAS_TEST_RUNS_ROOT); unreadable => skip
_RUNS = Path(os.environ["MBNAS_TEST_RUNS_ROOT"])
EVIDENCE = _RUNS / "evidence"
DEVICE_FIELDS = ("uuid", "name", "driver", "cuda_runtime", "power_limit_w", "sm_clock_mhz", "mem_clock_mhz", "torch")
SCOPES = {"forward_only", "forward_and_postprocess", "end_to_end"}
ETRI_RECORD = EVIDENCE / "qa_etri_hybrid_tensorrt_record.json"
EVIDENCE_WORK = _RUNS / "evidence_work"


def _write_evidence_opt_in(env) -> bool:
    return env.get("MBNAS_WRITE_EVIDENCE") == "1"


# captured at import (collection) time: the autouse ``clean_env`` fixture strips every MBNAS_* variable per test
WRITE_EVIDENCE = _write_evidence_opt_in(os.environ)


def _etri_evidence_destination(tmp_path: Path, opt_in: bool) -> tuple[Path, Path]:
    """``(work_dir, record_path)`` for the ETRI TensorRT run: tmp_path unless ``MBNAS_WRITE_EVIDENCE=1`` (S3-F9), in
    which case the dataset / checkpoint / ONNX / engines live under ``MBNAS_runs/evidence_work`` (they outlive the
    test, so the record's paths exist) and the canonical registry evidence record is (re)written."""
    if not opt_in:
        return tmp_path, tmp_path / ETRI_RECORD.name
    import time

    return EVIDENCE_WORK / f"qa_etri_hybrid_tensorrt_{time.strftime('%Y%m%dT%H%M%S')}", ETRI_RECORD


RECORD_FIELDS = ("batch", "input_shape", "warmup", "repeats", "latency_source", "reference", "split", "limitations",
                 "device", "device_after", "export", "engines", "runtimes", "latency_table", "subject")


def _evidence_files() -> list[Path]:
    try:
        return sorted(EVIDENCE.glob("bench_host_gpu_*.json"))
    except OSError:  # PermissionError when the original host's /root is unreadable
        return []


def _records() -> list[tuple[str, dict]]:
    out = []
    for path in _evidence_files():
        doc = json.loads(path.read_text())
        out += [(f"{path.name}:{s['weights']}", s["record"]) for s in doc.get("subjects", []) if s.get("record")]
    return out


# an early reduced run (3 warm-ups, no TensorRT, AP delta unavailable) is superseded by the full matrix runs
RECORDS = [(n, r) for n, r in _records() if any(k.startswith("tensorrt:") for k in r["runtimes"])]
needs_evidence = pytest.mark.skipif(not RECORDS, reason="no bench_host_gpu evidence on this host",
                                    host_data=True)  # contract §9 host_data (conftest_common adds the marker)


def _finite_numbers(obj) -> bool:
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_finite_numbers(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_finite_numbers(v) for v in obj)
    return True


def check_record(rec: dict, *, tensorrt: bool = True) -> None:
    for field in RECORD_FIELDS:
        assert field in rec, field
    dev = rec["device"]
    for field in DEVICE_FIELDS:
        assert dev.get(field) not in (None, ""), field
    assert re.fullmatch(r"GPU-[0-9a-f-]{36}", dev["uuid"])
    assert rec["latency_source"] == "measured" and rec["batch"] == 1 and rec["warmup"] >= 1 and rec["repeats"] >= 3
    assert rec["input_shape"][0] == 1 and len(rec["input_shape"]) == 4
    assert rec["export"]["ok"] and re.fullmatch(r"[0-9a-f]{64}", rec["export"]["sha256"]) and rec["export"]["opset"] == 17
    assert rec["reference"] in rec["runtimes"] and rec["runtimes"][rec["reference"]]["status"] == "MEASURED"
    for key, runtime in rec["runtimes"].items():
        assert runtime["status"] == "MEASURED", (key, runtime.get("error"))
        lat = runtime["latency"]
        assert lat["timer"].lower().count("synchronize") and lat["repeats"] == rec["repeats"] and lat["warmup"] == rec["warmup"]
        assert set(lat["scopes"]) == SCOPES, key  # preprocess is only inside end_to_end; forward_and_postprocess is separate
        for scope, stats in lat["scopes"].items():
            assert 0 < stats["min_ms"] <= stats["p50_ms"] <= stats["p95_ms"] <= stats["max_ms"] and stats["mean_ms"] > 0
            assert stats["fps_batch1"] == pytest.approx(1000 / stats["p50_ms"], rel=0.01)  # FPS is derived, never mixed in
        assert lat["scopes"]["forward_only"]["p50_ms"] <= lat["scopes"]["forward_and_postprocess"]["p50_ms"] <= lat["scopes"]["end_to_end"]["p50_ms"]
        if lat.get("version", "") >= "mbnas-latency/4":  # S8-F4/F6 fields (records written before /4 lack them)
            assert lat["preprocess_ms_method"] == "derived" and lat["preprocess_ms"] == pytest.approx(
                lat["scopes"]["end_to_end"]["p50_ms"] - lat["scopes"]["forward_and_postprocess"]["p50_ms"])
            assert lat["throughput_fps"] is None and lat["throughput_fps_note"] == "not measured (batch 1 only)"
            ds = lat["device_sampling"]
            assert ds["error"] is None and ds["power_draw_w"]["n"] == ds["sm_clock_mhz"]["n"] == rec["repeats"] + 2
            assert 0 < ds["power_draw_w"]["min"] <= ds["power_draw_w"]["max"] and ds["sm_clock_mhz"]["min"] > 0
        if key != rec["reference"]:
            assert set(runtime["accuracy_delta_vs_reference"]) == {"ap_delta", "ap_50_delta", "recall_delta"}, key
            assert all(isinstance(v, float) and math.isfinite(v) for v in runtime["accuracy_delta_vs_reference"].values())
        assert runtime["accuracy"]["evaluation_hash"].startswith("sha256:") and runtime["accuracy"]["counts"]["images"] == rec["split"]["images"]
    assert {row["scope"] for row in rec["latency_table"]} == SCOPES
    assert {row["key"] for row in rec["latency_table"]} == set(rec["runtimes"])
    if tensorrt:
        assert any(k.startswith("tensorrt:") for k in rec["runtimes"])
        for prec, eng in rec["engines"].items():
            assert eng["status"] == "BUILT" and eng["error"] is None and eng["bytes"] > 0, prec
            assert re.fullmatch(r"[0-9a-f]{64}", eng["sha256"]) and eng["precision"] == prec
            assert re.fullmatch(r"[0-9a-f]{64}", eng["onnx_sha256"]) and eng["onnx_sha256"] == rec["export"]["sha256"]
            assert eng["tensorrt_version"] and eng["builder_config"]["batch"] == 1
    assert rec["split"]["split_hash"].startswith("sha256:")
    assert _finite_numbers(rec)


@needs_evidence
@pytest.mark.parametrize("name,rec", RECORDS, ids=[n for n, _ in RECORDS])
def test_recorded_host_gpu_benchmark_carries_full_provenance(name, rec):
    check_record(rec)


@needs_evidence
def test_recorded_runs_cover_the_ultralytics_yolo_and_rtdetr_families():
    names = {n.split(":")[1] for n, _ in RECORDS}
    assert {"yolo11n.pt", "rtdetr-l.pt"} <= names
    for n, rec in RECORDS:
        assert rec["subject"]["source"]["sha256"] and rec["subject"]["source"]["kind"] == "official_weights", n


def _recorded_before_blocked_status(path, b) -> bool:
    """Evidence written before JobResult had BLOCKED (contract 1.2.0, 2026-10-07) stored TARGET_BLOCKED as FAILED."""
    stamp = path.stem.rsplit("_", 2)[-2]
    return stamp < "20261007" or "job_status_fallback" in b.get("outputs", {})


def test_recorded_jetson_target_is_blocked_with_no_proxy_numbers():
    docs = [(p, json.loads(p.read_text())) for p in _evidence_files()]
    blocked = [(p, b) for p, d in docs for b in d.get("blocked_targets", [])]
    if not blocked:
        pytest.skip("no blocked-target evidence")
    for path, b in blocked:
        assert b["status"] == "BLOCKED" or (b["status"] == "FAILED" and _recorded_before_blocked_status(path, b)), path
        assert b["reason_code"] == "TARGET_BLOCKED" and b["outputs"]["status"] == "BLOCKED"
        assert "no proxy latency is substituted" in b["outputs"]["target"]["reason"]
        assert not {"latency_table", "benchmark_path", "engines"} & set(b["outputs"])


def test_blocked_target_and_proxy_flag_in_code():
    """Live code check (no GPU): any target other than the host GPU is BLOCKED and a proxy can never certify."""
    from mbnas_model_runtime.benchmark.capability import target_status
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    for target in ("jetson-orin-nx", "jetson-agx", "cuda:3", "tpu-v4"):
        status = target_status(target)
        assert status["status"] == "BLOCKED" and "proxy" in status["reason"]
    assert target_status("host-gpu-0")["status"] == "AVAILABLE"
    assert pilot_smoke_protocol()["hard_constraints"]["proxy_may_certify_final_deployment"] is False


def test_etri_evidence_destination_is_tmp_unless_explicitly_opted_in(tmp_path):
    """S3-F9: a normal suite run never rewrites the canonical ETRI TensorRT record (registry evidence)."""
    assert _write_evidence_opt_in({}) is False and _write_evidence_opt_in({"MBNAS_WRITE_EVIDENCE": "0"}) is False
    assert _write_evidence_opt_in({"MBNAS_WRITE_EVIDENCE": "true"}) is False
    assert _write_evidence_opt_in({"MBNAS_WRITE_EVIDENCE": "1"}) is True
    work, record = _etri_evidence_destination(tmp_path, False)
    assert work == tmp_path and record == tmp_path / ETRI_RECORD.name
    assert EVIDENCE not in record.parents and "evidence_work" not in str(work)
    work, record = _etri_evidence_destination(tmp_path, True)
    assert record == ETRI_RECORD and work.parent == EVIDENCE_WORK and work.name.startswith("qa_etri_hybrid_tensorrt_")
    try:
        created = work.exists()
    except OSError:  # PermissionError when the original host's /root is unreadable (2026-10-15)
        created = False
    assert not created  # only created by the deliberate evidence run itself


def _abs_paths(obj) -> list[str]:
    if isinstance(obj, dict):
        return [p for v in obj.values() for p in _abs_paths(v)]
    if isinstance(obj, list):
        return [p for v in obj for p in _abs_paths(v)]
    return [obj] if isinstance(obj, str) and obj.startswith("/") else []


@pytest.mark.gpu
@pytest.mark.slow
def test_etri_hybrid_onnx_and_tensorrt_engines_on_the_host_gpu(tmp_path, make_dataset):
    """Tiny ETRI hybrid (1 epoch on the A100) -> eval snapshot -> ONNX -> TensorRT FP32+FP16 -> full provenance record."""
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")
    from mbnas_model_runtime.adapters.base import ArchSpec, DataSpec
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter
    from mbnas_model_runtime.benchmark.pipeline import run_benchmark
    from mbnas_model_runtime.benchmark.subjects import etri_from_snapshot
    from mbnas_model_runtime.datasets.manifest import build_manifest
    from mbnas_model_runtime import checkpoint as ck
    from mbnas_model_runtime.datasets.yolo_view import build_yolo_view
    from mbnas_runner.training.stage_trainer import StageTrainer

    work, record_path = _etri_evidence_destination(tmp_path, WRITE_EVIDENCE)
    canonical_before = ETRI_RECORD.stat().st_mtime_ns if ETRI_RECORD.exists() else None
    paths = make_dataset("etri-bench", counts={"train": 6, "val": 3}, label="0 0.5 0.5 0.3 0.3\n")
    if work != tmp_path:  # deliberate evidence run: inputs + artefacts persist next to the record they back
        shutil.copytree(paths["root"], work / "data" / "etri-bench")
        paths = {k: v.replace(paths["root"], str(work / "data" / "etri-bench")) for k, v in paths.items()}
    train = build_manifest(paths["train_root"], names={0: "drone"})
    val = build_manifest(paths["search_val_root"], names={0: "drone"})
    view = build_yolo_view(work / "view", {"train": train, "val": val}, {0: "drone"})
    data = DataSpec({0: "drone"}, train, val, view_root=view.parent)
    arch = ArchSpec("etri-controlled-cnn-transformer", "Hybrid", "etri_hybrid",
                    {"imgsz": 64, "patch": 16, "dim": 32, "depth": 1, "heads": 4})
    recipe = {"optimizer": "AdamW", "lr0": 1e-3, "lrf": 0.01, "weight_decay": 1e-4, "momentum": 0.9, "beta2": 0.999,
              "warmup_epochs": 0, "batch": 4, "gradient_accumulation": 1, "workers": 0, "amp": False,
              "input_shape": [64, 64], "ema": True}
    trainer = StageTrainer(adapter=EtriHybridAdapter(), candidate_dir=work / "cand", arch=arch, data=data,
                           recipe=recipe, init="scratch", training_seed=3, device="cuda:0")
    result = trainer.run_until("etri-bench", 1, 1, "sha256:qa", prior_epochs=0, expected_resume=None)
    snapshot = ck.eval_snapshot_path(work / "cand" / "ckpt", result.epochs_done)
    assert snapshot.is_file()
    subject = etri_from_snapshot(snapshot, device="cuda:0")
    rec = run_benchmark(subject, out_dir=work / "bench", manifest=val, sample_image=val.samples[0].image,
                        device="cuda:0", runtimes=("pytorch:FP32", "pytorch:FP16", "onnxruntime:FP32", "tensorrt:FP16", "tensorrt:FP32"),
                        warmup=20, repeats=30, timing_cache=work / "trt.cache")
    assert rec["export"]["ok"], rec["export"].get("error")
    check_record(rec)
    assert rec["subject"]["backend"] == "etri_hybrid" and {e["precision"] for e in rec["engines"].values()} == {"FP16", "FP32"}
    assert json.loads((work / "bench" / "benchmark.json").read_text())["schema"] == "mbnas-benchmark/1"
    doc = {"kind": "qa_etri_hybrid_tensorrt", "note": "tiny synthetic ETRI hybrid, 1 epoch; provenance/engine evidence "
           "only, not a model-quality claim", "record": rec}
    if record_path == ETRI_RECORD:
        doc["work_dir"] = str(work)
        doc["command"] = ("MBNAS_WRITE_EVIDENCE=1 pytest tests/owner/qa/test_benchmark_evidence.py::"
                          "test_etri_hybrid_onnx_and_tensorrt_engines_on_the_host_gpu")
        missing = [p for p in _abs_paths(rec) if not Path(p).exists()]
        assert not missing, missing  # every artefact the canonical record names outlives the test
    record_path.write_text(json.dumps(doc, indent=1, default=str))
    if record_path != ETRI_RECORD:  # normal suite run: canonical registry evidence untouched (S3-F9)
        now = ETRI_RECORD.stat().st_mtime_ns if ETRI_RECORD.exists() else None
        assert now == canonical_before


@pytest.mark.gpu
def test_export_torch_handles_the_cuda_transformer_fast_path_and_restores_the_flag(tmp_path):
    """Regression: on CUDA, eval + no_grad routes nn.TransformerEncoderLayer to ``aten::_transformer_encoder_layer_fwd``,
    which has no ONNX symbolic (CPU exports were fine, so the CPU registry probe never saw it)."""
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")
    from mbnas_model_runtime.benchmark.onnx_export import export_torch

    class Tiny(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.enc = torch.nn.TransformerEncoderLayer(32, 4, 64, batch_first=True)

        def forward(self, x):
            return self.enc(x)

    before = torch.backends.mha.get_fastpath_enabled()
    rep = export_torch(Tiny().to("cuda:0"), tmp_path, input_shape=(1, 16, 32), stem="tiny", output_names=["out"])
    assert rep.ok, rep.error
    assert torch.backends.mha.get_fastpath_enabled() is before  # global switch restored
