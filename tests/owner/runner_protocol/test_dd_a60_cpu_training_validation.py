"""DD-A60 (4) with the REAL ultralytics backend on this CPU: an explicit training job with validation enabled on a CPU
device (what a CPU-only runner executes after mapping ``cuda:0`` to ``cpu``) SUCCEEDS and keeps its trained checkpoint;
its rung-end evaluation is NOT_EVALUATED (``latency_source`` pending, no metric) instead of the former
``AssertionError: Torch not compiled with CUDA enabled`` -> EVAL_FAILED.

The CUDA API behaves like a CPU-only torch build (``is_available`` False, ``device_count`` 0, ``get_device_name`` /
``synchronize`` / ``get_device_properties`` raise that AssertionError), so a CUDA call on the evaluation path would fail
the job exactly as in T5. The resolved protocol comes from the core's training selection (owner test); the job runs
in-process through ``mbnas_runner.training.handlers.run_training_job``. Tiny synthetic detector and dataset; CPU only.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.runner_protocol, pytest.mark.slow]
NOT_COMPILED = "Torch not compiled with CUDA enabled"


def test_cpu_training_with_validation_succeeds_not_evaluated_and_keeps_the_checkpoint(settings, runner_settings,
                                                                                     make_dataset, ctx_factory,
                                                                                     tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_contracts.schemas.jobs import JobSpec, JobStatus
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.training_runs.selection import TrainingRunRequest, prepare_protocol
    from mbnas_model_runtime import checkpoint as ck
    from mbnas_runner.training.handlers import run_training_job

    def not_compiled(name: str):
        def trap(*_a, **_k):  # one function object per attribute (torch._dynamo rejects shared torch objects)
            raise AssertionError(f"{NOT_COMPILED} ({name})")
        return trap

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    for name in ("get_device_name", "synchronize", "get_device_properties", "current_device", "set_device"):
        monkeypatch.setattr(torch.cuda, name, not_compiled(name))
    torch.set_num_threads(2)

    ds = make_dataset("cpu_train_val", counts={"train": 4, "val": 2})
    policy = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": settings.output_root})
    policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                              warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
    policy["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": False}
    arch = {"model_id": "tiny-cpu-val", "family": "YOLO11", "backend": "ultralytics", "variant": "detect", "config": {
        "nc": 1, "backbone": [[-1, 1, "Conv", [8, 3, 2]], [-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]]],
        "head": [[[0, 1, 2], 1, "Detect", ["nc"]]]}}
    req = TrainingRunRequest(source={"kind": "user", "model_path": "/unused/fixture.yaml"}, protocol=policy,
                             validation_enabled=True, idempotency_key="dd-a60-cpu-val")
    _, resolved = prepare_protocol(req, {"arch": arch, "kind": "user", "name": "tiny-cpu-val"},
                                   SimpleNamespace(settings=settings, validation_context=ctx_factory))
    params = {"run_dir": str(Path(settings.output_root) / "dd-a60-cpu-val"), "adapter_id": "tiny-cpu-val",
              "arch": arch, "resolved_protocol": resolved, "init": "scratch", "device": "cpu", "evaluate": True,
              "stage_epochs_added": 1, "stop_request_path": str(tmp_path / "no.stop")}
    spec = JobSpec(job_id="cpu-val-1", run_id="dd-a60-cpu-val", kind="BASELINE_TRAIN", candidate_id="tiny-model",
                   rung="TRAIN", protocol_hash=protocol_hash(resolved), cumulative_target=1, fixed_horizon=1,
                   training_seed=41, arm="Baseline", params=params)
    messages: list = []
    result = run_training_job(spec, messages.append, runner_settings)

    assert result.status == JobStatus.SUCCEEDED, (result.reason_code, result.message)
    ev = result.outputs["evaluation"]
    assert ev["evaluation_status"] == "NOT_EVALUATED" and ev["reason_code"] == "EVALUATION_REQUIRES_CUDA"
    assert ev["latency_source"] == "pending" and ev["ap"] is None and ev["latency_ms"] is None and ev["size_mb"] is None
    rung = result.outputs["rung_result"]
    full = Path(rung["rung_end_training_ckpt"])
    assert full.is_file() and ev["checkpoint_path"] == str(full)  # the trained full state is kept and referenced
    assert ck.verify_file(full)["sha256"] == rung["checkpoint_sha256"]["training_full"]
    kinds = {m.payload["kind"] for m in messages if m.type == "checkpoint"}
    assert {"training_full", "eval_ema"} <= kinds, kinds  # reported to the core as usual
    assert result.outputs["epochs_done"] == 1
