"""REAL GPU: training job handler end to end on the PILOT_SMOKE subset (yolo11n), pause at an epoch boundary,
resume, inline evaluation (AP/recall, 4 panels per fixed sample, measured latency, FP16 size) and an EVALUATE job.

Run inside the GPU lock. With ``EVIDENCE_DIR_TRAINING`` set (deliberately not ``MBNAS_*``: the autouse
``clean_env`` fixture strips those), the results are written there as ``evaluation_pipeline_yolo11n_<ts>.json``
(kind ``evaluation_pipeline``) for the registry/evidence index.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import harness  # tests/_shared (runner_settings_for)
import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu, pytest.mark.slow, pytest.mark.host_data,  # PILOT subset + probe evidence
              pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA GPU required")]

REAL_ROOT = Path(os.environ["MBNAS_TEST_RUNS_ROOT"])  # WP-1 contract §9: runs root of this test run


@pytest.fixture
def gpu_settings(tmp_path: Path, pilot_required):
    from mbnas_core.config import CoreSettings as Settings

    probes = sorted((REAL_ROOT / "registry").glob("probe_*.json"))
    if not probes:
        pytest.skip("no registry probe evidence (run scripts/probe_registry.py)")
    out = tmp_path / "runs"
    (out / "registry").mkdir(parents=True)
    shutil.copy(probes[-1], out / "registry" / probes[-1].name)
    return Settings(output_root=str(out), output_roots=[str(out)], db_path=str(out / "db.sqlite3"),
                    dataset_roots=[os.environ["MBNAS_TEST_DATA_ROOT"]])  # WP-1 contract §9


def _resolved(settings):
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.protocol.validator import ValidationContext, validate
    from mbnas_model_runtime.provenance.env_lock import validation_devices
    from mbnas_core.registry import load_registry

    proto = pilot_smoke_protocol({"output_root": settings.output_root})
    ctx = ValidationContext(settings=settings, registry_lookup=load_registry(settings.output_root).get,
                            devices=validation_devices(), dataset_service=DatasetService.from_settings(settings))
    res = validate(proto, ctx)
    assert res.valid, res.errors
    return res.resolved, res.protocol_hash


def _spec(phash, kind, target, rung, params, job_id):
    from mbnas_contracts.schemas.jobs import JobSpec

    return JobSpec(job_id=job_id, run_id="gpu-test", kind=kind, protocol_hash=phash, candidate_id="yolo11n-base",
                   rung=rung, cumulative_target=target, fixed_horizon=6, arm="Baseline", training_seed=101,
                   params=params)


def test_training_job_pause_resume_and_evaluation(gpu_settings, tmp_path: Path) -> None:
    from mbnas_runner.evaluation.handlers import run_evaluation_job
    from mbnas_contracts.schemas.jobs import JobKind, JobStatus, ProgressMessage
    from mbnas_runner.training.handlers import run_training_job

    resolved, phash = _resolved(gpu_settings)
    from mbnas_core.registry import load_registry
    from mbnas_model_runtime.registry import arch_spec

    runner = harness.runner_settings_for(gpu_settings)  # A11: the runner handlers get RunnerSettings
    entry = load_registry(gpu_settings.output_root)["yolo11n"]  # A2/DD-A16: core resolves the baseline arch
    arch = arch_spec("yolo11n", gpu_settings.weights_dir, entry).as_dict()
    run_dir = Path(gpu_settings.output_root) / "gpu-test"
    stop = tmp_path / "stop.request"
    base = {"run_dir": str(run_dir), "adapter_id": "yolo11n", "arch": arch, "resolved_protocol": resolved,
            "stage_epochs_added": 2}  # L0 0 -> 2 and L1 2 -> 4 both add 2 epochs
    msgs: list[ProgressMessage] = []
    t0 = time.time()
    r0 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 2, "L0", base, "j-l0"), msgs.append, runner)
    assert r0.status == JobStatus.SUCCEEDED, r0.message
    ev0 = r0.outputs["evaluation"]
    assert r0.outputs["epochs_done"] == 2 and 0.0 <= ev0["ap"] <= 1.0 and 0.0 <= ev0["recall"] <= 1.0
    assert ev0["latency_ms"] > 0 and ev0["latency_source"] == "measured" and ev0["size_mb"] > 0
    assert set(ev0["latency"]["scopes"]) == {"forward_only", "forward_and_postprocess", "end_to_end"}
    assert ev0["latency"]["manifest"]["uuid"].startswith("GPU-") and ev0["device_name"]
    pngs = [a for a in ev0["artifacts"] if a.get("mime_type") == "image/png"]
    assert len(pngs) == 8 and all((run_dir / a["relpath"]).is_file() for a in pngs)
    assert not {"fitness", "fitness_result", "gate", "rewards"} & set(ev0)  # DD-A15: the runner reports raw metrics
    kinds = [m.payload.get("kind") for m in msgs if m.type == "checkpoint"]
    assert kinds.count("training_full") == 2 and "eval_ema" in kinds and "weights_only" in kinds

    stop.write_text("pause")
    r1 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 4, "L1",
                                {**base, "stop_request_path": str(stop)}, "j-l1a"), msgs.append, runner)
    assert r1.status == JobStatus.CANCELLED and r1.reason_code == "PAUSED_AT_EPOCH" and r1.outputs["epochs_done"] == 3
    stop.unlink()
    r2 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 4, "L1", base, "j-l1b"), msgs.append, runner)
    assert r2.status == JobStatus.SUCCEEDED and r2.outputs["epochs_done"] == 4
    assert r2.outputs["rung_result"]["resumed_from"].endswith("full_e0003.pt")
    ev2 = r2.outputs["evaluation"]
    lrs = [h["epoch"] for h in r2.outputs["rung_result"]["lr_history"]]
    assert lrs == [0, 1, 2, 3]

    r3 = run_evaluation_job(_spec(phash, JobKind.EVALUATE, None, "L1",
                                  {"run_dir": str(run_dir), "adapter_id": "yolo11n", "arch": arch, "resolved_protocol": resolved,
                                   "eval_ckpt": ev2["eval_checkpoint_path"]}, "j-eval").model_copy(
        update={"cumulative_target": None, "fixed_horizon": None}), msgs.append, runner)
    assert r3.status == JobStatus.SUCCEEDED, r3.message
    ev3 = r3.outputs["evaluation"]
    assert ev3["evaluation_hash"] == ev2["evaluation_hash"] and ev3["ap"] == pytest.approx(ev2["ap"], abs=1e-6)
    assert ev3["checkpoint_sha256"] == ev2["checkpoint_sha256"]

    evidence_dir = os.environ.get("EVIDENCE_DIR_TRAINING")
    if evidence_dir:
        from mbnas_contracts.util.time import utcnow_iso

        summary = (f"yolo11n PILOT job L0(2ep) AP={ev0['ap']:.4f} R={ev0['recall']:.4f} "
                   f"lat_p50={ev0['latency_ms']:.2f}ms size={ev0['size_mb']:.3f}MB; pause@3 -> resume -> L1(4ep) "
                   f"AP={ev2['ap']:.4f}; EVALUATE job reproduces evaluation_hash and AP")
        doc = {"kind": "evaluation_pipeline", "model_id": "yolo11n", "verdict": "PASS", "created_at": utcnow_iso(),
               "summary": summary, "seconds": round(time.time() - t0, 1),
               "jobs": {"L0": r0.model_dump(mode="json"), "L1_paused": r1.model_dump(mode="json"),
                        "L1": r2.model_dump(mode="json"), "EVALUATE": r3.model_dump(mode="json")},
               "progress_messages": len(msgs), "run_dir": str(run_dir)}
        path = Path(evidence_dir) / f"evaluation_pipeline_yolo11n_{time.strftime('%Y%m%dT%H%M%S')}.json"
        path.write_text(json.dumps(doc, indent=1, default=str))


def test_deleted_full_state_payload_is_resume_blocked(gpu_settings, tmp_path: Path) -> None:
    """S5-F1 on the real handler: a later stage whose full-state payload was deleted is FAILED RESUME_BLOCKED (sidecar
    kept -> NO_VALID_CHECKPOINT/FILE_MISSING; whole ckpt dir gone -> MISSING_STATE:checkpoint_file), nothing trained."""
    from mbnas_contracts.schemas.jobs import JobKind, JobStatus
    from mbnas_model_runtime import checkpoint as ck
    from mbnas_runner.training.handlers import run_training_job

    resolved, phash = _resolved(gpu_settings)
    from mbnas_core.registry import load_registry
    from mbnas_model_runtime.registry import arch_spec

    runner = harness.runner_settings_for(gpu_settings)  # A11: the runner handlers get RunnerSettings
    entry = load_registry(gpu_settings.output_root)["yolo11n"]  # A2/DD-A16: core resolves the baseline arch
    arch = arch_spec("yolo11n", gpu_settings.weights_dir, entry).as_dict()
    run_dir = Path(gpu_settings.output_root) / "gpu-test"
    base = {"run_dir": str(run_dir), "adapter_id": "yolo11n", "arch": arch, "resolved_protocol": resolved, "evaluate": False}
    r0 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 2, "L0", {**base, "stage_epochs_added": 2}, "j-l0"),
                          lambda m: None, runner)
    assert r0.status == JobStatus.SUCCEEDED, r0.message
    ckpt_dir = run_dir / "candidates" / "yolo11n-base" / "ckpt"
    full = ck.full_state_path(ckpt_dir, 2)
    expected = {"id": "ck-x", "path": str(full), "epoch": 2, "rung": "L0"}
    full.unlink()  # payload deleted, sidecar kept
    l1 = {**base, "resume_from": expected, "stage_epochs_added": 2}
    r1 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 4, "L1", l1, "j-l1a"), lambda m: None, runner)
    assert r1.status == JobStatus.FAILED and r1.reason_code == "RESUME_BLOCKED", (r1.reason_code, r1.message)
    assert r1.message.startswith("NO_VALID_CHECKPOINT") and "FILE_MISSING" in r1.message
    assert ck.list_full_states(ckpt_dir) == []  # nothing new was written (the trainer pruned e1 at e2)
    shutil.rmtree(ckpt_dir)
    r2 = run_training_job(_spec(phash, JobKind.BASELINE_TRAIN, 4, "L1", l1, "j-l1b"), lambda m: None, runner)
    assert r2.status == JobStatus.FAILED and r2.reason_code == "RESUME_BLOCKED", (r2.reason_code, r2.message)
    assert r2.message.startswith("MISSING_STATE:checkpoint_file") and not ckpt_dir.exists()
