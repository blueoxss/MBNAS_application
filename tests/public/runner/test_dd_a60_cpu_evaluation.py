"""DD-A60 (4) / DD-A32: the evaluation step on a CPU device (a CPU-only runner maps the core's ``cuda:0`` to ``cpu``).

Before: ``evaluate_checkpoint`` called ``device_identity`` -> ``torch.cuda.get_device_name`` first, so a CPU-only torch
build raised ``AssertionError: Torch not compiled with CUDA enabled``; the training job FAILED (EVAL_FAILED) and its
trained checkpoint was not committed. Now nothing is measured on a non-CUDA device and no CUDA API is touched: the
payload is NOT_EVALUATED (``latency_source`` pending, every metric null, reason EVALUATION_REQUIRES_CUDA) and still names
the rung-end checkpoint, the training job's evaluation step returns it, and EVALUATE / official evaluation jobs on such
a device SUCCEED with it. Every CUDA entry point and every GPU-bound step is trapped (a call fails the test, like the
CPU-only torch build would). SYNTHETIC checkpoints (real checkpoint/sidecar writer); CPU only.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from mbnas_contracts.schemas.jobs import JobKind, JobSpec  # noqa: E402
from mbnas_model_runtime import checkpoint as ck  # noqa: E402
from mbnas_model_runtime.adapters.base import RungResult  # noqa: E402

BENCH = {"device": "cpu", "precision": "FP16", "batch": 1, "warmup": 1, "repeats": 1,
         "postprocess": {"conf": 0.25, "iou": 0.7, "max_det": 300}}
NOT_COMPILED = "Torch not compiled with CUDA enabled"


@pytest.fixture
def no_cuda(monkeypatch):
    """Every CUDA entry point and GPU-bound evaluation step raises (the CPU-only torch build behaviour)."""
    from mbnas_model_runtime.evaluation import evaluator, images
    from mbnas_runner.evaluation import handlers

    def boom(name: str):
        def trap(*_a, **_k):  # one function object per attribute (torch._dynamo rejects shared torch objects)
            raise AssertionError(f"{NOT_COMPILED} ({name})")
        return trap

    for name in ("get_device_name", "synchronize", "get_device_properties", "is_available", "device_count",
                 "current_device", "set_device"):
        monkeypatch.setattr(torch.cuda, name, boom(name))
    for mod, name in ((handlers, "device_identity"), (handlers, "measure_latency"), (handlers, "gpu_manifest"),
                      (evaluator, "evaluate"), (images, "render_fixed_samples")):
        monkeypatch.setattr(mod, name, boom(name))
    return handlers


def _snapshot(ckpt_dir: Path, epochs: int = 2) -> tuple[Path, Path, dict]:
    hashes = {"architecture": "sha256:" + "a" * 64, "recipe": "r", "protocol": "p"}
    full_side = None
    for e in range(epochs):
        state = {"format": ck.FORMAT, "epoch": e, "epochs_done": e + 1, "global_step": e + 1, "samples_seen": 4 * (e + 1),
                 "rng": ck.capture_rng(include_cuda=False), "hashes": hashes, "model_fp32": {"w": torch.ones(2) * e}}
        full_side = ck.save_full_state(ck.full_state_path(ckpt_dir, e + 1), state, rung_end=e + 1 == epochs)
    ev = ck.eval_snapshot_path(ckpt_dir, epochs)
    ck.save_eval_snapshot(ev, {"w": torch.ones(2)}, model_meta={}, epochs_done=epochs, hashes=hashes)
    return ev, ck.full_state_path(ckpt_dir, epochs), full_side


def _assert_not_evaluated(payload: dict, *, epochs: int, full: Path | None, full_sha: str | None) -> None:
    assert payload["evaluation_status"] == payload["performance_status"] == "NOT_EVALUATED"
    assert payload["reason_code"] == "EVALUATION_REQUIRES_CUDA" and "CUDA" in payload["not_evaluated_reason"]
    assert payload["latency_source"] == "pending"
    for key in ("ap", "ap_50", "recall", "latency_ms", "size_mb", "evaluation_hash", "split_hash", "device_uuid"):
        assert payload[key] is None, key  # nothing made up for a measurement that did not happen
    assert payload["latency_scope"] == "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"
    assert payload["epochs"] == epochs and payload["timings"] == {"val_s": 0.0, "profile_s": 0.0}
    assert payload["artifacts"] == [] and payload["raw_metrics"]["reason_code"] == "EVALUATION_REQUIRES_CUDA"
    assert payload["checkpoint_path"] == (str(full) if full else None)
    assert payload["checkpoint_sha256"] == full_sha


def test_evaluate_checkpoint_on_cpu_is_not_evaluated_and_touches_no_cuda(no_cuda, tmp_path):
    ev, full, side = _snapshot(tmp_path / "cand" / "ckpt")
    payload = no_cuda.evaluate_checkpoint("SYNTHETIC adapter", ev, "SYNTHETIC manifest", run_dir=tmp_path,
                                          out_dir=tmp_path / "cand" / "eval" / "x", device="cpu", precision="FP16",
                                          eval_batch=4, input_shape=[64, 64], benchmark=BENCH,
                                          scopes=["forward_only"], image_samples=2, device_entry="cpu",
                                          split="search_val")
    _assert_not_evaluated(payload, epochs=2, full=full, full_sha=side["sha256"])
    assert payload["eval_checkpoint_path"] == str(ev) and payload["eval_checkpoint_sha256"] == ck.read_sidecar(ev)["sha256"]
    assert payload["device"] == "cpu" and payload["eval_split"] == "search_val"
    assert not (tmp_path / "cand" / "eval").exists()  # nothing written: no evaluation took place
    assert no_cuda.latency_device_problem("cuda:0") is None and no_cuda.latency_device_problem("cuda") is None


def test_a_corrupt_snapshot_on_cpu_still_fails_like_before(no_cuda, tmp_path):
    from mbnas_model_runtime.adapters.base import ResumeBlocked

    ev, _full, _side = _snapshot(tmp_path / "ckpt")
    ev.write_bytes(b"tampered")
    with pytest.raises(ResumeBlocked):
        no_cuda.evaluate_checkpoint(None, ev, None, run_dir=tmp_path, out_dir=tmp_path / "o", device="cpu",
                                    precision="FP16", eval_batch=1, input_shape=[64, 64], benchmark=BENCH, scopes=[],
                                    image_samples=0)


def test_training_job_evaluation_step_on_cpu_returns_the_not_evaluated_payload_with_the_checkpoint(no_cuda, tmp_path):
    from mbnas_runner.training import handlers as training

    ckpt_dir = tmp_path / "run" / "candidates" / "c1" / "ckpt"
    ev, full, side = _snapshot(ckpt_dir)
    rung = RungResult("c1", 2, 2, 2, "TARGET_REACHED", str(full), str(ev), None, None, None, [], 2, 8,
                      {"training_full": side["sha256"]}, None, {"recipe": "r"}, {"architecture": "sha256:" + "a" * 64})
    spec = JobSpec(job_id="t1", run_id="r1", kind=JobKind.CANDIDATE_TRAIN, protocol_hash="sha256:p", candidate_id="c1",
                   rung="L0", cumulative_target=2, fixed_horizon=2, arm="GA", search_seed=1, params={})
    params = training.TrainJobParams.model_validate(
        {"run_dir": str(tmp_path / "run"), "adapter_id": "c1", "device": "cpu", "stage_epochs_added": 2},
        context={"kind": "CANDIDATE_TRAIN", "cumulative_target": 2})
    protocol = {"console": {"benchmark": BENCH}, "training": {"requested_input_size": [64, 64], "batch_per_gpu": 2}}
    sent: list = []
    payload, timings = training._evaluate(spec, params, protocol, "SYNTHETIC adapter", rung,
                                          {"search_val": "SYNTHETIC manifest"}, tmp_path / "run", [64, 64], sent.append)
    _assert_not_evaluated(payload, epochs=2, full=full, full_sha=side["sha256"])
    assert payload["checkpoint_sha256s"] == {"training_full": side["sha256"]}  # the trained snapshot is kept
    assert timings == {"val_s": 0.0, "profile_s": 0.0}
    assert [m.type for m in sent] == ["metric"] and sent[0].payload["ap"] is None


def test_evaluate_job_on_cpu_succeeds_with_the_not_evaluated_payload(no_cuda, tmp_path, monkeypatch):
    from mbnas_runner.config import RunnerSettings
    from mbnas_runner.training import context

    ev, full, side = _snapshot(tmp_path / "run" / "candidates" / "c1" / "ckpt")
    protocol = {"console": {"benchmark": BENCH}, "training": {"requested_input_size": [64, 64], "batch_per_gpu": 2}}
    monkeypatch.setattr(context, "load_protocol", lambda params, spec: protocol)
    monkeypatch.setattr(context, "prepare_backend_env", lambda settings: None)
    monkeypatch.setattr(context, "resolve_model", lambda params, settings: (
        None, SimpleNamespace(capability=lambda: {}), None))
    monkeypatch.setattr(context, "split_manifests", lambda p, s, splits, access=None: ({0: "x"},
                                                                                        {"search_val": "SYNTHETIC"}))
    spec = JobSpec(job_id="e1", run_id="r1", kind=JobKind.EVALUATE, protocol_hash="sha256:p", candidate_id="c1",
                   rung="L0", arm="GA", params={"run_dir": str(tmp_path / "run"), "adapter_id": "c1",
                                                "eval_ckpt": str(ev), "device": "cpu", "resolved_protocol": protocol})
    sent: list = []
    res = no_cuda.run_evaluation_job(spec, sent.append, RunnerSettings(output_roots=[str(tmp_path)]))
    assert res.status.value == "SUCCEEDED", (res.reason_code, res.message)
    assert res.outputs["epochs_done"] == 2
    _assert_not_evaluated(res.outputs["evaluation"], epochs=2, full=full, full_sha=side["sha256"])
    assert res.gpu_seconds == 0.0


def test_official_evaluation_on_cpu_succeeds_without_loading_weights(no_cuda, tmp_path, tmp_roots, monkeypatch,
                                                                    settings):
    from mbnas_model_runtime.benchmark import subjects
    from mbnas_runner.evaluation.official_evaluation import run_official_evaluation

    def no_weights(*_a, **_k):
        raise AssertionError("official weights must not be loaded on a CPU device")

    monkeypatch.setattr(subjects, "ultralytics_from_weights", no_weights)
    weights = tmp_path / "weights" / "yolo11n.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"SYNTHETIC official weights")
    proto = {"console": {"extra": {"training": {"enabled": True, "mode": "evaluate_only",
                                                "arch": {"weights_path": str(weights), "family": "YOLO11"},
                                                "architecture_hash": "sha256:" + "b" * 64}},
                         "benchmark": BENCH},
             "training": {"requested_input_size": [64, 64], "batch_per_gpu": 2}}
    run_dir = tmp_roots["runs"] / "runs" / "run-official-cpu"
    spec = JobSpec(job_id="ev-cpu", run_id="run-official-cpu", kind=JobKind.EVALUATE, protocol_hash="sha256:p",
                   candidate_id="cand-1", rung="TRAIN", attempt=3,
                   params={"resolved_protocol": proto, "run_dir": str(run_dir), "device": "cpu"})
    res = run_official_evaluation(spec, lambda _m: None, settings)
    assert res.status == "SUCCEEDED" and res.attempt == 3, (res.reason_code, res.message)
    ev = res.outputs["evaluation"]
    _assert_not_evaluated(ev, epochs=0, full=None, full_sha=None)
    assert ev["source"] == "official_weights" and ev["training_mode"] == "evaluate_only"
    assert ev["architecture_hash"] == "sha256:" + "b" * 64 and res.outputs["epochs_done"] == 0
