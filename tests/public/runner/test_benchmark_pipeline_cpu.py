"""``run_benchmark`` record assembly on the CPU (DD-A49 c, T7 item g2): the inherited ``manifest.root`` access
(``'Manifest' object has no attribute 'root'``, benchmark/pipeline.py:124) is gone and the record's ``split`` block is
built from ``Manifest.roots``.

Real parts: a randomly initialised yolo11n (no weights) on the CPU, the real ultralytics adapter, the real common
evaluator (``pytorch:FP32`` accuracy on a SYNTHETIC 3-image split) and the record writer. Mocked parts: the ONNX export
(a failed ``ExportReport``, so ONNX Runtime CUDA and TensorRT are never attempted and are recorded as FAILED) and the
device manifest (NVML/CUDA facts of a host GPU; this CPU test has none). Latency is CUDA-only by design, so the
``pytorch:FP32`` latency is refused here and recorded as FAILED; no proxy number is produced.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")  # the ml extras (torch, ultralytics) are needed for the real model


def _subject(model, adapter):
    from mbnas_model_runtime.benchmark.onnx_export import ExportReport
    from mbnas_model_runtime.benchmark.subjects import BenchSubject

    return BenchSubject(name="y11-cpu-random", backend="ultralytics", adapter=adapter, model=model, imgsz=64,
                        device="cpu", unpack=lambda outs: outs[0],
                        export=lambda out: ExportReport(ok=False, backend="ultralytics", method="mocked (CPU test)",
                                                        error="ONNX export not run in the CPU unit test"))


def test_record_split_block_uses_manifest_roots_on_the_pytorch_fp32_cpu_path(tmp_path, make_dataset, monkeypatch):
    from ultralytics.nn.tasks import DetectionModel

    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter
    from mbnas_model_runtime.benchmark import pipeline
    from mbnas_model_runtime.datasets.manifest import build_manifest

    paths = make_dataset("bench_cpu", counts={"val": 3})
    manifest = build_manifest(paths["search_val_root"], names={0: "drone"})
    assert not hasattr(manifest, "root") and len(manifest.roots) == 1  # the field the old line 124 missed
    calls: list[str] = []

    def fake_manifest(device):
        calls.append(device)
        return {"name": "SYNTHETIC cpu test device", "uuid": None, "torch_device": device}

    monkeypatch.setattr(pipeline, "device_manifest", fake_manifest)
    torch.manual_seed(0)
    model = DetectionModel("yolo11n.yaml", nc=1, verbose=False).eval()
    rec = pipeline.run_benchmark(_subject(model, UltralyticsAdapter("YOLO11")), out_dir=tmp_path / "bench",
                                 manifest=manifest, sample_image=manifest.samples[0].image, device="cpu",
                                 runtimes=("pytorch:FP32", "onnxruntime:FP32", "tensorrt:FP16"), warmup=1, repeats=2)

    root = str(manifest.roots[0])
    expected = {"roots": [root], "root": root, "split_hash": manifest.split_hash, "images": 3}
    assert rec["split"] == expected
    on_disk = json.loads((tmp_path / "bench" / "benchmark.json").read_text())
    assert on_disk["split"] == expected and on_disk["schema"] == "mbnas-benchmark/1"
    assert calls == ["cpu", "cpu"] and rec["device"]["name"].startswith("SYNTHETIC")  # before and after the matrix

    ref = rec["runtimes"]["pytorch:FP32"]
    assert ref["accuracy"]["counts"]["images"] == 3 and ref["accuracy"]["evaluation_hash"].startswith("sha256:")
    assert ref["status"] == "FAILED" and "CUDA device only" in ref["error"]  # no CPU proxy latency
    assert set(ref["accuracy_delta_vs_reference"]) == {"ap_delta", "ap_50_delta", "recall_delta"}
    for key in ("onnxruntime:FP32", "tensorrt:FP16"):
        assert rec["runtimes"][key]["status"] == "FAILED"
        assert "ONNX export failed; runtime not available" in rec["runtimes"][key]["error"]
    assert rec["engines"] == {} and rec["export"]["ok"] is False  # no TensorRT build without an ONNX file
    assert rec["latency_table"] == [] and rec["latency_source"] == "measured"
    assert not torch.cuda.is_initialized()


def test_split_record_of_a_multi_root_manifest_keeps_every_root():
    from mbnas_model_runtime.benchmark.pipeline import split_record

    multi = SimpleNamespace(roots=["/abs/a/images/val", "/abs/b/images/val"], split_hash="sha256:" + "1" * 64,
                            samples=[object()] * 5)
    assert split_record(multi) == {"roots": ["/abs/a/images/val", "/abs/b/images/val"], "root": None,
                                   "split_hash": "sha256:" + "1" * 64, "images": 5}
