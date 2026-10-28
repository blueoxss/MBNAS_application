"""REAL GPU: one tiny RF-DETR Nano stage (1 epoch, PILOT_SMOKE subset, no evaluation) — after the stage the native
out_dir keeps only logs; the sha256-referenced native checkpoint copy under ckpt/ is kept and still verifies."""
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


def test_rfdetr_stage_deletes_native_leftovers_but_keeps_the_resume_source(tmp_path: Path, pilot_required, monkeypatch) -> None:
    monkeypatch.setenv("MBNAS_RUNNER_ALLOW_PICKLE_RESUME", "1")  # DD-A20/C-cross-7: owner host allows native (pickle) resume
    from mbnas_core.config import CoreSettings as Settings
    from mbnas_model_runtime.datasets.manifest import DatasetService
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.protocol.validator import ValidationContext, validate
    from mbnas_model_runtime.provenance.env_lock import validation_devices
    from mbnas_core.registry import load_registry
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec, JobStatus
    from mbnas_model_runtime import checkpoint as ck
    from mbnas_runner.training.handlers import run_training_job
    from mbnas_contracts.util.atomic import sha256_file
    from mbnas_model_runtime.registry import arch_spec

    probes = sorted((REAL_ROOT / "registry").glob("probe_*.json"))
    if not probes:
        pytest.skip("no registry probe evidence (run scripts/probe_registry.py)")
    out = tmp_path / "runs"
    (out / "registry").mkdir(parents=True)
    shutil.copy(probes[-1], out / "registry" / probes[-1].name)
    settings = Settings(output_root=str(out), output_roots=[str(out)], db_path=str(out / "db.sqlite3"),
                        dataset_roots=[os.environ["MBNAS_TEST_DATA_ROOT"]])  # WP-1 contract §9
    reg = load_registry(settings.output_root)
    if reg["rf-detr-nano"]["status"] != "AVAILABLE":
        pytest.skip(f"rf-detr-nano is {reg['rf-detr-nano']['status']} in the probe")
    res = validate(pilot_smoke_protocol({"output_root": str(out)}),
                   ValidationContext(settings=settings, registry_lookup=reg.get, devices=validation_devices(),
                                     dataset_service=DatasetService.from_settings(settings)))
    assert res.valid, res.errors
    run_dir = out / "gpu-rfdetr"
    spec = JobSpec(job_id="j-rf", run_id="gpu-rfdetr", kind=JobKind.BASELINE_TRAIN, protocol_hash=res.protocol_hash,
                   candidate_id="rf-nano", rung="L0", cumulative_target=1, fixed_horizon=1, arm="Baseline",
                   training_seed=3, params={"run_dir": str(run_dir), "adapter_id": "rf-detr-nano",
                                            "arch": arch_spec("rf-detr-nano", settings.weights_dir, reg["rf-detr-nano"]).as_dict(),
                                            "resolved_protocol": res.resolved, "evaluate": False,
                                            "stage_epochs_added": 1})
    result = run_training_job(spec, lambda m: None, harness.runner_settings_for(settings))  # A11
    assert result.status == JobStatus.SUCCEEDED, result.message
    record = result.outputs["rung_result"]["resolved_recipe"]["native_housekeeping"]
    native = run_dir / "candidates" / "rf-nano" / "native"
    assert record["skipped_reason"] is None and record["deleted"], record
    assert "last.ckpt" in {Path(d["path"]).name for d in record["deleted"]}
    assert not [p.name for p in native.iterdir() if p.suffix in (".ckpt", ".pth")]  # only logs remain
    assert (native / "housekeeping.jsonl").is_file()
    ref = Path(record["kept_reference"]["path"])
    assert ref.parent.name == "ckpt" and ref.is_file() and sha256_file(ref) == record["kept_reference"]["sha256"]
    state = ck.load_full_state(ck.full_state_path(ref.parent, 1))
    assert state["native_resume_ckpt"]["path"] == str(ref) and state["native_resume_ckpt"]["sha256"] == sha256_file(ref)
