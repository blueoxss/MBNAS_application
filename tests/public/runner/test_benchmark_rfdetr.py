"""RF-DETR benchmark subject, latency operating point, postprocess provenance and BLOCKED status.

CPU tests export the official RF-DETR Nano checkpoint to ONNX and run ``RfdetrAdapter.predict`` through the runtime
view (ONNX Runtime CPU stand-in for the CUDA runner) against PyTorch. GPU-marked tests run the full
``run_benchmark`` matrix (PyTorch, ONNX Runtime CUDA EP, TensorRT) on the host GPU. Large files (ONNX ~110 MB, engines)
go under ``$MBNAS_TEST_RUNS_ROOT/tmp`` and are deleted afterwards, never under /tmp.
"""
from __future__ import annotations

import hashlib
import os
import inspect
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

from mbnas_model_runtime.benchmark import capability
from mbnas_runner.benchmark.handlers import blocked_job_status, run_benchmark_job
from mbnas_model_runtime.benchmark.pipeline import PostprocessError, resolve_postprocess
from mbnas_model_runtime.benchmark.profile import latency_postprocess_kwargs
from mbnas_model_runtime.benchmark.subjects import (SubjectError, class_for_rfdetr_weights, rfdetr_raw_view,
                                             rfdetr_runtime_view, rfdetr_unpack, runner_output_names,
                                             verify_rfdetr_official_weights)
from mbnas_contracts.schemas.jobs import JobKind, JobSpec, JobStatus

# WP-1 (A4/A8): host roots of this test run (tests/_shared/conftest_common.py sets the MBNAS_TEST_* defaults)
RF_DIR = Path(os.environ["MBNAS_TEST_WEIGHTS_DIR"]) / "rfdetr"
NANO = RF_DIR / "rf-detr-nano.pth"
PILOT_VAL = Path(os.environ["MBNAS_TEST_DATA_ROOT"]) / "pilot_visdrone_v1/images/search_val"
RUNS_TMP = Path(os.environ["MBNAS_TEST_RUNS_ROOT"]) / "tmp"
needs_nano = pytest.mark.skipif(not os.access(NANO, os.R_OK) or not NANO.is_file(), reason="official rf-detr-nano.pth not in the weights cache",
                                host_data=True)
GPU = pytest.mark.skipif(not torch.cuda.is_available(), reason="host CUDA GPU not available")


def _spec(params: dict) -> JobSpec:
    return JobSpec(job_id="b-rf", run_id="r1", kind=JobKind.BENCHMARK, protocol_hash="sha256:test", params=params)


@pytest.fixture
def runs_tmp(monkeypatch):
    """Scratch dir on the runs disk (not /tmp); TMPDIR points there for rfdetr/onnx temporaries."""
    import tempfile

    path = RUNS_TMP / f"pytest_bench_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(path))
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    monkeypatch.setenv("RF_HOME", str(RF_DIR))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    yield path
    shutil.rmtree(path, ignore_errors=True)


# -- capability / operating point ---------------------------------------------------------------------------------
def test_rfdetr_capability_is_current_and_declares_resolutions() -> None:
    rf = capability.BACKENDS["rfdetr"]
    assert "not present" not in rf["accuracy_delta"] and "rfdetr_adapter.py" in rf["accuracy_delta"]
    assert capability.RFDETR_RESOLUTION == {"RFDETRNano": 384, "RFDETRSmall": 512, "RFDETRMedium": 576,
                                            "RFDETRLarge": 704}
    assert rf["export_onnx"]["opset"] == 17 and rf["export_onnx"]["dynamic"] is False
    assert rf["export_onnx"]["outputs"] == ["dets", "labels"]
    assert capability.DEFAULT_POSTPROCESS == {"conf": 0.25, "iou": 0.7, "max_det": 300}
    # torch-free: this CPU test must never initialise CUDA (host_runtime() enumerates the CUDA devices, see the
    # gpu-marked test below), whatever order the suite runs in.
    from importlib.metadata import version

    assert version("rfdetr") == "1.11.2"


@pytest.mark.gpu
def test_host_runtime_reports_rfdetr_and_the_cuda_devices() -> None:
    rt = capability.host_runtime()
    assert rt["rfdetr"]["available"] is True and rt["rfdetr"]["version"] == "1.11.2"
    assert rt["torch"]["cuda_available"] is True and rt["torch"]["devices"]


def test_host_runtime_probe_does_not_switch_on_tf32_matmuls() -> None:
    """Regression: importing rfdetr sets float32 matmul precision 'high' process-wide; the version probe that runs
    before every benchmark must not do that (it would change the ultralytics/ETRI PyTorch FP32 conditions)."""
    import subprocess
    import sys

    code = ("import torch; from mbnas_model_runtime.benchmark.capability import host_runtime; r = host_runtime(); "
            "print(torch.get_float32_matmul_precision(), r['rfdetr']['version'], r['rfdetr']['imported'])")
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=300, check=True)
    assert out.stdout.split()[-3:] == ["highest", "1.11.2", "False"], out.stdout + out.stderr


def test_resolve_postprocess_defaults_overrides_and_rejects() -> None:
    d = resolve_postprocess(None)
    assert (d["conf"], d["iou"], d["max_det"]) == (0.25, 0.7, 300) and d["source"].startswith("default")
    o = resolve_postprocess({"conf": 0.4, "max_det": 100})
    assert (o["conf"], o["iou"], o["max_det"]) == (0.4, 0.7, 100) and "params.postprocess" in o["source"]
    for bad in ({"conf": 1.5}, {"iou": -0.1}, {"max_det": 0}, {"max_det": 2.5}, {"conf": True}, {"nms": True}, [0.2]):
        with pytest.raises(PostprocessError):
            resolve_postprocess(bad)


def test_latency_operating_point_passed_only_when_the_adapter_takes_it() -> None:
    class Takes:
        def latency_fns(self, model, shape, precision, device, sample_image, postprocess=None):  # noqa: ANN001
            return {}

    class Ignores:
        def latency_fns(self, model, shape, precision, device, sample_image):  # noqa: ANN001
            return {}

    op = resolve_postprocess({"conf": 0.3})
    kwargs, prov = latency_postprocess_kwargs(Takes(), op)
    assert kwargs == {"postprocess": {"conf": 0.3, "iou": 0.7, "max_det": 300}} and prov["applied_by_adapter"]
    kwargs, prov = latency_postprocess_kwargs(Ignores(), op)
    assert kwargs == {} and prov["applied_by_adapter"] is False and "Ignores" in prov["note"]


def test_handler_rejects_invalid_postprocess(settings) -> None:
    res = run_benchmark_job(_spec({"device": "host-gpu-0", "postprocess": {"conf": 2.0}}), settings=settings)
    assert res.status == "FAILED" and res.reason_code == "INVALID_JOB" and "conf" in res.message
    res = run_benchmark_job(_spec({"device": "host-gpu-0", "benchmark": {"postprocess": {"max_det": 0}}}),
                            settings=settings)
    assert res.status == "FAILED" and res.reason_code == "INVALID_JOB" and "max_det" in res.message


def test_blocked_job_status_follows_the_job_schema() -> None:
    status, is_blocked = blocked_job_status()
    if hasattr(JobStatus, "BLOCKED"):
        assert status == JobStatus.BLOCKED and is_blocked
    else:
        assert status == JobStatus.FAILED and not is_blocked


# -- RF-DETR subject plumbing (no weights) -------------------------------------------------------------------------
class _Runner:
    def __init__(self, names: list[str], outs: list[torch.Tensor]) -> None:
        self.output_names, self.outs, self.calls = names, outs, 0

    def __call__(self, x: torch.Tensor) -> list[torch.Tensor]:
        self.calls += 1
        return self.outs


def test_rfdetr_unpack_maps_outputs_by_name_not_position() -> None:
    dets, labels = torch.zeros(1, 300, 4), torch.ones(1, 300, 91)
    unpack = rfdetr_unpack(["labels", "dets"])
    assert unpack([labels, dets]) == (dets, labels)
    with pytest.raises(SubjectError) as err:
        rfdetr_unpack(["output0"])
    assert err.value.code == "EXPORT_OUTPUTS_UNEXPECTED"
    ort_like = SimpleNamespace(outputs=[("dets", [1, 300, 4], "tensor(float)"), ("labels", [1, 300, 91], "x")])
    assert runner_output_names(ort_like) == ["dets", "labels"]


def test_rfdetr_raw_view_aligns_pytorch_dict_and_runtime_tuple() -> None:
    b, lg = torch.rand(1, 300, 4), torch.rand(1, 300, 91)
    torch_out = {"pred_boxes": b, "pred_logits": lg, "aux_outputs": [], "enc_outputs": {}}
    assert set(rfdetr_raw_view(torch_out)) == {"pred_boxes", "pred_logits"}
    from mbnas_model_runtime.benchmark.accuracy import max_abs_diff

    assert max_abs_diff(rfdetr_raw_view((b + 0.5, lg)), rfdetr_raw_view(torch_out)) == pytest.approx(0.5)


def test_rfdetr_runtime_view_replaces_only_the_network_and_leaves_the_original() -> None:
    net = torch.nn.Linear(2, 2)
    post = object()
    ctx = SimpleNamespace(model=net, postprocess=post, resolution=384, device=torch.device("cpu"))
    rf = SimpleNamespace(model=ctx, means=[0.4], stds=[0.2], class_names=["a"])
    dets, labels = torch.zeros(1, 300, 4), torch.ones(1, 300, 3)
    runner = _Runner(["dets", "labels"], [dets, labels])
    view = rfdetr_runtime_view(rf, runner, "cpu")
    assert rf.model.model is net and view.model is not ctx  # original untouched
    assert view.model.postprocess is post and view.model.resolution == 384 and view.means == [0.4]
    out = view.model.model(torch.zeros(1, 3, 384, 384))
    assert out == (dets, labels) and runner.calls == 1
    assert view.model.model.weight is net.weight  # attribute reads fall through to the PyTorch module


def test_rfdetr_weights_class_from_file_name() -> None:
    assert class_for_rfdetr_weights("/x/rf-detr-large-2026.pth") == "RFDETRLarge"
    with pytest.raises(SubjectError):
        class_for_rfdetr_weights("/x/model.pth")


@pytest.mark.gpu  # verify_rfdetr_official_weights imports rfdetr, and importing rfdetr initialises CUDA
def test_rfdetr_official_weights_name_and_digest_checks(tmp_path) -> None:
    with pytest.raises(SubjectError) as err:
        verify_rfdetr_official_weights(tmp_path / "rf-detr-small.pth", "RFDETRNano")
    assert err.value.code == "WEIGHTS_UNVERIFIED"
    fake = tmp_path / "rf-detr-nano.pth"
    fake.write_bytes(b"not the official checkpoint")
    with pytest.raises(SubjectError, match="published"):
        verify_rfdetr_official_weights(fake, "RFDETRNano")
    with pytest.raises(SubjectError) as err:
        verify_rfdetr_official_weights(fake, "RFDETRHuge")
    assert err.value.code == "INVALID_SUBJECT"
    assert class_for_rfdetr_weights("/x/rf-detr-large-2026.pth") == "RFDETRLarge"
    with pytest.raises(SubjectError):
        class_for_rfdetr_weights("/x/model.pth")


@needs_nano
@pytest.mark.gpu  # imports rfdetr (initialises CUDA)
def test_rfdetr_official_nano_checkpoint_matches_published_md5() -> None:
    ev = verify_rfdetr_official_weights(NANO, "RFDETRNano")
    assert ev["md5"] == ev["published_md5"] and len(ev["sha256"]) == 64
    assert ev["sha256"] == hashlib.sha256(NANO.read_bytes()).hexdigest()


def test_handler_routes_rfdetr_subjects() -> None:
    from mbnas_runner.benchmark import handlers

    src = inspect.getsource(handlers._subject)
    assert 'backend == "rfdetr" and source == "official_weights"' in src
    assert 'backend == "rfdetr" and source == "eval_snapshot"' in src


# -- real checkpoint: export + predict through the runtime view (CPU) ---------------------------------------------
class _OrtCpuRunner:
    """ONNX Runtime CPU stand-in with the OrtRunner calling convention (torch in, list of torch out)."""

    def __init__(self, path: str) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.outputs = [(o.name, o.shape, o.type) for o in self.session.get_outputs()]

    def __call__(self, x: torch.Tensor) -> list[torch.Tensor]:
        outs = self.session.run(None, {self.session.get_inputs()[0].name: x.detach().cpu().numpy()})
        return [torch.from_numpy(o) for o in outs]


@needs_nano
@pytest.mark.slow
def test_rfdetr_nano_export_and_predict_through_runtime_view_match_pytorch(runs_tmp) -> None:
    from mbnas_model_runtime.benchmark.subjects import rfdetr_from_weights
    from mbnas_model_runtime.datasets.manifest import build_manifest

    subject = rfdetr_from_weights(NANO, device="cpu")
    assert subject.imgsz == 384 and subject.source["sha256"] and subject.source["class_name"] == "RFDETRNano"
    rep = subject.export(runs_tmp / "onnx")
    assert rep.ok, rep.error
    assert rep.opset == 17 and rep.flags["dynamic_batch"] is False and rep.flags["shape"] == [384, 384]
    assert rep.inputs[0]["shape"] == [1, 3, 384, 384]
    assert [(o["name"], o["shape"][:2]) for o in rep.outputs] == [("dets", [1, 300]), ("labels", [1, 300])]
    assert next(subject.net.parameters()).device.type == "cpu"  # export restored the live model

    view = subject.runtime_view(_OrtCpuRunner(rep.path))
    manifest = build_manifest(str(PILOT_VAL), names={0: "0"})
    images = [(s.sample_id, s.image) for s in manifest.samples[:2]]
    cfg = {"conf": 0.001, "max_det": 300, "batch": 1, "precision": "FP32", "shape": [384, 384]}
    ref = subject.adapter.predict(subject.model, images, cfg)
    got = subject.adapter.predict(view, images, cfg)
    assert [p["sample_id"] for p in got] == [p["sample_id"] for p in ref]
    for a, b in zip(got, ref):
        assert len(a["scores"]) == len(b["scores"]) > 0
        top = min(20, len(a["scores"]))
        assert a["classes"][:top] == b["classes"][:top]
        assert max(abs(x - y) for x, y in zip(a["scores"][:top], b["scores"][:top])) < 1e-3
        assert max(abs(u - v) for bu, bv in zip(a["boxes_xyxy"][:top], b["boxes_xyxy"][:top])
                   for u, v in zip(bu, bv)) < 0.5  # source pixels
    assert subject.model.model.model is subject.net  # the view never touched the reference RFDETR


# -- host GPU: full matrix ---------------------------------------------------------------------------------------
@needs_nano
@GPU
@pytest.mark.gpu
@pytest.mark.slow
def test_rfdetr_nano_benchmark_matrix_on_the_host_gpu(runs_tmp) -> None:
    """Official RF-DETR Nano: ONNX -> ORT CUDA EP + TensorRT FP16 -> accuracy deltas + latency scopes + provenance."""
    import json

    from mbnas_model_runtime.benchmark.pipeline import run_benchmark
    from mbnas_model_runtime.benchmark.subjects import rfdetr_from_weights
    from mbnas_model_runtime.datasets.manifest import build_manifest

    full = build_manifest(str(PILOT_VAL), names={0: "0"})
    manifest = full.model_copy(update={"samples": full.samples[:4]})
    subject = rfdetr_from_weights(NANO, device="cuda:0")
    rec = run_benchmark(subject, out_dir=runs_tmp / "bench", manifest=manifest, sample_image=manifest.samples[0].image,
                        runtimes=("pytorch:FP32", "onnxruntime:FP32", "tensorrt:FP16"), warmup=2, repeats=5,
                        timing_cache=runs_tmp / "trt.cache", postprocess={"conf": 0.3})
    assert json.loads((runs_tmp / "bench" / "benchmark.json").read_text())["schema"] == "mbnas-benchmark/1"
    assert rec["export"]["ok"], rec["export"]["error"]
    assert rec["input_shape"] == [1, 3, 384, 384] and rec["subject"]["backend"] == "rfdetr"
    assert {k: v["status"] for k, v in rec["runtimes"].items()} == dict.fromkeys(
        ("pytorch:FP32", "onnxruntime:FP32", "tensorrt:FP16"), "MEASURED"), {
        k: v.get("error") for k, v in rec["runtimes"].items()}
    eng = rec["engines"]["FP16"]
    assert eng["status"] == "BUILT" and eng["sha256"] == hashlib.sha256(Path(eng["engine_path"]).read_bytes()).hexdigest()
    ort_rec = rec["runtimes"]["onnxruntime:FP32"]
    assert ort_rec["describe"]["providers"][0] == "CUDAExecutionProvider"
    assert abs(ort_rec["accuracy_delta_vs_reference"]["ap_50_delta"]) < 0.02
    assert abs(ort_rec["accuracy_class_agnostic_delta_vs_reference"]["ap_50_delta"]) < 0.02
    assert ort_rec["raw_output_max_abs_diff_vs_reference"] is not None
    for key, r in rec["runtimes"].items():
        assert set(r["latency"]["scopes"]) == {"forward_only", "forward_and_postprocess", "end_to_end"}, key
        assert r["latency"]["postprocess_operating_point"]["conf"] == 0.3
    pp = rec["postprocess_operating_point"]
    assert (pp["latency"]["conf"], pp["latency"]["iou"], pp["latency"]["max_det"]) == (0.3, 0.7, 300)
    assert pp["accuracy"]["conf"] == 0.001 and pp["accuracy"]["max_det"] == 300
    assert "PostProcess" in rec["postprocess_runtime"]["implementation"]
    assert rec["device"]["uuid"] and rec["device"]["host_runtime"]["rfdetr"]["available"]


@pytest.mark.slow
@pytest.mark.skipif(not os.path.isdir(PILOT_VAL), reason="PILOT_VAL search_val images not present on this host", host_data=True)  # 2026-10-21: absent data -> skip
def test_rfdetr_eval_snapshot_subject_uses_the_trained_resolution(runs_tmp) -> None:
    """An MBNAS RF-DETR rung-end snapshot (scratch Nano, 1 class, trained at 320) becomes a subject at 320."""
    import rfdetr

    from mbnas_model_runtime.benchmark.subjects import rfdetr_from_snapshot
    from mbnas_model_runtime import checkpoint as ck

    torch.manual_seed(0)
    scratch = rfdetr.RFDETRNano(pretrain_weights=None, num_classes=1, resolution=320, device="cpu")
    snap = runs_tmp / "eval_e0002.pt"
    meta = {"class_name": "RFDETRNano", "num_classes": 1, "resolution": 320, "names": {"0": "0"}, "weights": "ema"}
    ck.save_eval_snapshot(snap, scratch.model.model.state_dict(), model_meta=meta, epochs_done=2, hashes={})
    subject = rfdetr_from_snapshot(snap, device="cpu")
    assert subject.imgsz == 320 and subject.source["kind"] == "eval_snapshot"
    assert subject.source["sha256"] == hashlib.sha256(snap.read_bytes()).hexdigest()
    assert subject.source["class_name"] == "RFDETRNano" and subject.postprocess_impl
    preds = subject.adapter.predict(subject.model, [(s.name, str(s)) for s in sorted(PILOT_VAL.iterdir())[:1]],
                                    {"conf": 0.001, "max_det": 300, "batch": 1, "precision": "FP32"})
    assert len(preds) == 1 and len(preds[0]["scores"]) > 0
