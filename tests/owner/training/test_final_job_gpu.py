"""REAL GPU: one tiny FINAL_RETRAIN training job (yolo11n, 1 epoch, PILOT_SMOKE subset) through the real training
handler: scratch training, evaluation on the FINAL split only (split hash, split=final_eval panels), latency at the
resolved operating point with the CUDA torchvision NMS, last-search-rung fitness and no gate, split access log."""
from __future__ import annotations

import shutil
from pathlib import Path

import os

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


def test_final_retrain_job_evaluates_only_the_final_split(gpu_settings) -> None:
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.protocol.validator import ValidationContext, validate
    from mbnas_model_runtime.provenance.env_lock import validation_devices
    from mbnas_core.registry import load_registry
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec, JobStatus
    from mbnas_runner.training.context import read_split_access_log
    from mbnas_runner.training.handlers import run_training_job
    from mbnas_model_runtime.registry import arch_spec

    proto = pilot_smoke_protocol({"output_root": gpu_settings.output_root})
    ctx = ValidationContext(settings=gpu_settings, registry_lookup=load_registry(gpu_settings.output_root).get,
                            devices=validation_devices(), dataset_service=DatasetService.from_settings(gpu_settings))
    res = validate(proto, ctx)
    assert res.valid, res.errors
    resolved = res.resolved
    run_dir = Path(gpu_settings.output_root) / "gpu-final"
    spec = JobSpec(job_id="j-final", run_id="gpu-final", kind=JobKind.FINAL_RETRAIN, protocol_hash=res.protocol_hash,
                   candidate_id="final-yolo11n", rung="FINAL", cumulative_target=1, fixed_horizon=1, arm="Final",
                   training_seed=7, params={"run_dir": str(run_dir), "adapter_id": "yolo11n",
                                            "arch": arch_spec("yolo11n", gpu_settings.weights_dir, load_registry(gpu_settings.output_root)["yolo11n"]).as_dict(),
                                            "resolved_protocol": resolved, "init": "scratch"})
    out = run_training_job(spec, lambda m: None, harness.runner_settings_for(gpu_settings))  # A11
    assert out.status == JobStatus.SUCCEEDED, out.message
    ev = out.outputs["evaluation"]
    final_hash = resolved["dataset"]["final_evaluation_manifest_hash"]
    assert ev["eval_split"] == "final_eval" and ev["split_hash"] == final_hash
    assert ev["split_hash"] != resolved["dataset"]["search_validation_manifest_hash"]
    assert ev["evaluation_signature"]["split_hash"] == final_hash
    assert ev["latency_scope"] == "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"
    assert ev["evaluation_signature"]["latency_scope"] == ev["latency_scope"]
    assert ev["postprocess_runtime"] == "torchvision_cuda_nms" and ev["latency_source"] == "measured"
    assert ev["latency"]["manifest"]["postprocess_operating_point"] == {"conf": 0.25, "iou": 0.7, "max_det": 300}
    # DD-A15: no fitness/gate keys from the runner (the FINAL row's display fitness is computed by core)
    assert not {"fitness", "fitness_result", "gate", "fitness_rung", "fitness_weights", "rewards"} & set(ev)
    pngs = [a for a in ev["artifacts"] if a.get("mime_type") == "image/png"]
    assert len(pngs) == 8 and {a["matching"]["split"] for a in pngs} == {"final_eval"}
    assert {a["split_hash"] for a in pngs} == {final_hash}
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml

    paths = resolved["console"]["paths"]
    final_manifest = DatasetService.from_settings(gpu_settings).manifest_for(
        paths["final_eval_root"], load_dataset_yaml(paths["dataset_yaml"]).names)
    assert final_manifest.split_hash == final_hash
    first_two = sorted(s.sample_id for s in final_manifest.samples)[:2]  # pre-registered panel samples of the final split
    assert sorted({a["sample_id"] for a in pngs}) == first_two
    log = read_split_access_log(run_dir)
    assert {e["split"] for e in log} == {"train", "search_val", "final_eval"}
    assert {(e["job_kind"], e["arm"]) for e in log} == {("FINAL_RETRAIN", "Final")}
