"""Benchmark module: capability/BLOCKED targets, path guards, ONNX export (CPU), TensorRT import failure, deltas.

GPU-marked tests run ONNX Runtime (CUDA EP) and a TensorRT FP32 engine of a tiny module on the host GPU.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path

import pytest
torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

from mbnas_model_runtime.benchmark import accuracy, capability
from mbnas_runner.benchmark.handlers import run_benchmark_job
from mbnas_model_runtime.benchmark.onnx_export import export_torch, export_ultralytics
from mbnas_model_runtime.benchmark.subjects import SubjectError, verify_official_weights
from mbnas_model_runtime.benchmark.trt_build import build_engine
from mbnas_contracts.schemas.jobs import JobKind, JobSpec

PILOT_VAL = os.environ["MBNAS_TEST_DATA_ROOT"] + "/pilot_visdrone_v1/images/search_val"  # WP-1 A4/A8: MBNAS_TEST_* host roots
GPU = pytest.mark.skipif(not torch.cuda.is_available(), reason="host CUDA GPU not available")


class Tiny(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        torch.manual_seed(0)
        self.conv = torch.nn.Conv2d(3, 4, 3, padding=1)
        self.head = torch.nn.Linear(4, 6)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.conv(x).mean((2, 3)))[:, None, :]


def _spec(params: dict) -> JobSpec:
    return JobSpec(job_id="b1", run_id="r1", kind=JobKind.BENCHMARK, protocol_hash="sha256:test", params=params)


def test_targets_other_than_host_gpu_are_blocked() -> None:
    assert capability.target_status("host-gpu-0")["status"] == "AVAILABLE"
    blocked = capability.target_status("jetson-orin-nx")
    assert blocked["status"] == "BLOCKED" and "proxy" in blocked["reason"]
    assert set(capability.BACKENDS) == {"ultralytics", "etri_hybrid", "rfdetr"}


def test_handler_returns_blocked_result_without_numbers(settings) -> None:
    from mbnas_contracts.schemas.jobs import JobStatus

    res = run_benchmark_job(_spec({"device": "jetson-orin-nx"}), settings=settings)
    assert res.reason_code == "TARGET_BLOCKED"
    if hasattr(JobStatus, "BLOCKED"):  # schema with the BLOCKED job status: blocked targets use it
        assert res.status == "BLOCKED" and "job_status_fallback" not in res.outputs
    else:  # older schema: FAILED, and the record says why it is not BLOCKED
        assert res.status == "FAILED" and "BLOCKED" in res.outputs["job_status_fallback"]
    assert res.outputs["status"] == "BLOCKED" and "latency_table" not in res.outputs


@pytest.mark.skipif(not os.path.isdir(PILOT_VAL), reason="PILOT_VAL search_val images not present on this host", host_data=True)  # 2026-10-21: absent data -> skip
def test_handler_rejects_output_outside_allowlist(settings, tmp_path) -> None:
    res = run_benchmark_job(_spec({"device": "host-gpu-0", "split": {"root": PILOT_VAL, "names": {"0": "0"}},
                                   "output_dir": "/etc/mbnas_bench"}), settings=settings)
    assert res.status == "FAILED" and res.reason_code == "PATH_OUTSIDE_ALLOWLIST"


def test_handler_rejects_wrong_job_kind(settings) -> None:
    spec = JobSpec(job_id="b2", run_id="r1", kind=JobKind.EVALUATE, protocol_hash="sha256:test", params={})
    res = run_benchmark_job(spec, settings=settings)
    assert res.status == "FAILED" and res.reason_code == "INVALID_JOB"


def test_export_torch_static_onnx_matches_torch(tmp_path) -> None:
    import onnxruntime as ort

    m = Tiny().eval()
    rep = export_torch(m, tmp_path, input_shape=(1, 3, 32, 32), stem="tiny", output_names=["out"])
    assert rep.ok, rep.error
    assert rep.sha256 == hashlib.sha256(Path(rep.path).read_bytes()).hexdigest()
    assert rep.opset == 17 and rep.inputs[0]["shape"] == [1, 3, 32, 32] and rep.outputs[0]["shape"] == [1, 1, 6]
    x = torch.rand(1, 3, 32, 32)
    y = ort.InferenceSession(rep.path, providers=["CPUExecutionProvider"]).run(None, {"images": x.numpy()})[0]
    assert abs(y - m(x).detach().numpy()).max() < 1e-5


def test_export_ultralytics_raw_head_and_restores_pt_path(tmp_path) -> None:
    from ultralytics.nn.tasks import DetectionModel

    model = DetectionModel("yolo11n.yaml", nc=1, verbose=False).eval()
    model.pt_path = "/somewhere/original.pt"
    rep = export_ultralytics(model, tmp_path, imgsz=320, stem="y11")
    assert rep.ok, rep.error
    assert Path(rep.path).parent == tmp_path and rep.opset == 17
    assert rep.flags["nms"] is False and rep.flags["simplify"] is False and rep.flags["dynamic"] is False
    assert rep.outputs[0]["shape"] == [1, 5, 2100]  # 4 box + 1 class, 40^2+20^2+10^2 anchors at 320
    assert model.pt_path == "/somewhere/original.pt"


def test_trt_build_blocked_when_tensorrt_unimportable(tmp_path, monkeypatch) -> None:
    rep = export_torch(Tiny().eval(), tmp_path, input_shape=(1, 3, 32, 32), stem="tiny", output_names=["out"])
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "tensorrt":
            raise ImportError("libnvinfer.so.10: cannot open shared object file")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    out = build_engine(rep.path, tmp_path / "e.engine")
    assert out.status == "BLOCKED" and "libnvinfer" in out.error and not (tmp_path / "e.engine").exists()


def test_accuracy_deltas_and_raw_diff() -> None:
    ref = {"ap_for_delta": 0.30, "ap_50": 0.5, "recall": 0.4}
    d = accuracy.deltas({"ap_for_delta": 0.29, "ap_50": 0.5, "recall": None}, ref)
    assert d["ap_delta"] == pytest.approx(-0.01) and d["ap_50_delta"] == 0.0 and d["recall_delta"] is None
    a = torch.zeros(2, 3)
    assert accuracy.max_abs_diff((a + 1, [a]), a) == 1.0
    assert accuracy.max_abs_diff({"x": a, "y": a + 2}, {"x": a, "y": a}) == 2.0
    assert accuracy.max_abs_diff(a, torch.zeros(3, 2)) is None


def test_official_weights_must_match_manifest(tmp_path) -> None:
    w = tmp_path / "m.pt"
    w.write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    (tmp_path / "weights_manifest.json").write_text(json.dumps({"m.pt": {"sha256": digest, "bytes": 7}}))
    assert verify_official_weights(w, tmp_path)["sha256"] == digest
    (tmp_path / "weights_manifest.json").write_text(json.dumps({"m.pt": {"sha256": "0" * 64, "bytes": 7}}))
    with pytest.raises(SubjectError) as err:
        verify_official_weights(w, tmp_path)
    assert err.value.code == "WEIGHTS_UNVERIFIED"


@GPU
@pytest.mark.gpu
def test_ort_cuda_and_trt_fp32_engine_match_torch(tmp_path) -> None:
    from mbnas_model_runtime.benchmark.onnx_runtime import OrtRunner
    from mbnas_model_runtime.benchmark.subjects import RuntimeModule
    from mbnas_model_runtime.benchmark.trt_runtime import TrtRunner

    m = Tiny().eval()
    rep = export_torch(m, tmp_path, input_shape=(1, 3, 32, 32), stem="tiny", output_names=["out"])
    x = torch.rand(1, 3, 32, 32, device="cuda")
    ref = m.cuda()(x)
    ort_out = OrtRunner(rep.path)(x)[0]
    assert OrtRunner(rep.path).providers[0] == "CUDAExecutionProvider"
    assert (ort_out - ref).abs().max().item() < 1e-3
    built = build_engine(rep.path, tmp_path / "tiny.engine", precision="FP32", timing_cache_path=tmp_path / "tc.bin")
    assert built.status == "BUILT", built.error
    assert built.sha256 == hashlib.sha256((tmp_path / "tiny.engine").read_bytes()).hexdigest()
    assert built.timing_cache["saved_bytes"] > 0 and built.builder_config["flags"]["FP16"] is False
    runner = TrtRunner(built.engine_path, expect_sha256=built.sha256)
    wrapped = RuntimeModule(runner, m, lambda outs: outs[0], "cuda:0")
    out = wrapped(x)
    torch.cuda.synchronize()
    assert (out - ref).abs().max().item() < 1e-2  # TF32 allowed by the default builder flags
    assert next(wrapped.parameters()).device.type == "cuda" and wrapped.head is m.head
    with pytest.raises(ValueError, match="checksum"):
        TrtRunner(built.engine_path, expect_sha256="0" * 64)


@GPU
@pytest.mark.gpu
@pytest.mark.slow
def test_pipeline_pytorch_vs_onnxruntime_record(tmp_path, make_dataset) -> None:
    """run_benchmark end to end without TensorRT (engine builds are exercised above and by bench_host_gpu.py)."""
    import copy

    from ultralytics.nn.tasks import DetectionModel

    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter
    from mbnas_model_runtime.benchmark.pipeline import run_benchmark
    from mbnas_model_runtime.benchmark.subjects import BenchSubject
    from mbnas_model_runtime.datasets.manifest import build_manifest

    paths = make_dataset("bench", counts={"val": 3})
    manifest = build_manifest(paths["search_val_root"], names={0: "drone"})
    torch.manual_seed(0)
    cpu_model = DetectionModel("yolo11n.yaml", nc=1, verbose=False).eval()
    model = copy.deepcopy(cpu_model).fuse(verbose=False).to("cuda:0").eval()
    subject = BenchSubject(name="y11-rand", backend="ultralytics", adapter=UltralyticsAdapter("YOLO11"), model=model,
                           imgsz=64, device="cuda:0", unpack=lambda outs: outs[0],
                           export=lambda out: export_ultralytics(cpu_model, out, imgsz=64, stem="y11"))
    rec = run_benchmark(subject, out_dir=tmp_path / "bench", manifest=manifest, sample_image=manifest.samples[0].image,
                        runtimes=("pytorch:FP32", "onnxruntime:FP32"), warmup=1, repeats=3)
    assert json.loads((tmp_path / "bench" / "benchmark.json").read_text())["schema"] == "mbnas-benchmark/1"
    assert {k: v["status"] for k, v in rec["runtimes"].items()} == {"pytorch:FP32": "MEASURED",
                                                                     "onnxruntime:FP32": "MEASURED"}
    assert len(rec["latency_table"]) == 6 and rec["latency_source"] == "measured" and rec["batch"] == 1
    ort_rec = rec["runtimes"]["onnxruntime:FP32"]
    assert ort_rec["describe"]["providers"][0] == "CUDAExecutionProvider"
    assert set(ort_rec["accuracy_delta_vs_reference"]) == {"ap_delta", "ap_50_delta", "recall_delta"}
    assert rec["device"]["uuid"] and rec["engines"] == {}
    assert rec["postprocess_runtime"]["ultralytics_nms"] == "torchvision.ops.nms"
