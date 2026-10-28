"""Recipe resolution, run-local dataset views and training-handler failure paths (no GPU)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mbnas_model_runtime.adapters.base import CapabilityError
from mbnas_model_runtime.datasets.manifest import build_manifest
from mbnas_core.protocol.presets import pilot_smoke_protocol
from mbnas_contracts.schemas.jobs import JobKind, JobSpec, JobStatus
from mbnas_model_runtime.datasets.yolo_view import DatasetDrift, build_yolo_view
from mbnas_runner.training.handlers import candidate_dir, run_training_job
from mbnas_runner.training.recipe import input_shape_for, recipe_from_protocol, stride_ceil


def test_recipe_from_pilot_protocol() -> None:
    proto = pilot_smoke_protocol()
    r = recipe_from_protocol(proto, [640, 640], fixed_horizon=6)
    assert r["batch"] == 8 and r["workers"] == 0 and r["amp"] is True and r["cos_lr"] is True
    assert r["lr0"] == proto["training"]["lr_initial"] and r["momentum"] == 0.9 and r["fixed_horizon"] == 6
    bad = pilot_smoke_protocol()
    bad["training"]["precision"] = "int8"
    with pytest.raises(CapabilityError):
        recipe_from_protocol(bad, [640, 640], fixed_horizon=6)


def test_input_shape_rules() -> None:
    proto = pilot_smoke_protocol()
    proto["training"]["requested_input_size"] = [600, 500]
    assert stride_ceil([600, 500]) == [608, 512]
    assert input_shape_for(proto, None) == [608, 512]
    assert input_shape_for(proto, None, rule={"kind": "stride_ceil", "stride": 32, "square": True}) == [608, 608]
    assert input_shape_for(proto, None, rule={"kind": "fixed", "shape": [384, 384]}) == [384, 384]
    assert input_shape_for(proto, None, override=[320, 320]) == [320, 320]


def test_yolo_view_links_images_and_copies_labels(tmp_path: Path, make_dataset) -> None:
    paths = make_dataset("v", counts={"train": 3, "val": 2})
    train = build_manifest(paths["train_root"], names={0: "drone"})
    val = build_manifest(paths["search_val_root"], names={0: "drone"})
    yaml_path = build_yolo_view(tmp_path / "views", {"train": train, "val": val}, {0: "drone"})
    root = yaml_path.parent
    img = next((root / "images" / "train").iterdir())
    assert img.is_symlink() and os.path.realpath(img).startswith(os.path.realpath(paths["root"]))
    lbl = root / "labels" / "train" / (img.stem + ".txt")
    assert lbl.is_file() and not lbl.is_symlink()
    assert "path: " + str(root) in yaml_path.read_text()
    assert build_yolo_view(tmp_path / "views", {"train": train, "val": val}, {0: "drone"}) == yaml_path  # reused
    Path(train.samples[0].label).write_text("0 0.4 0.4 0.1 0.1\n")  # drift after the manifest froze
    with pytest.raises(DatasetDrift):
        build_yolo_view(tmp_path / "views2", {"train": train, "val": val}, {0: "drone"})


def _spec(**params) -> JobSpec:
    params.setdefault("stage_epochs_added", 2)  # first stage (L0: 0 -> 2)
    return JobSpec(job_id="j1", run_id="r1", kind=JobKind.CANDIDATE_TRAIN, protocol_hash="sha256:x", candidate_id="c1",
                   rung="L0", cumulative_target=2, fixed_horizon=6, training_seed=1, params=params)


def test_handler_rejects_bad_params_and_hash(runner_settings, tmp_roots) -> None:  # A11: runner handler settings
    msgs = []
    res = run_training_job(_spec(run_dir=str(tmp_roots["runs"] / "r1"), unknown=1), msgs.append, runner_settings)
    assert res.status == JobStatus.FAILED and res.reason_code == "JOB_PARAMS_INVALID"
    res = run_training_job(_spec(run_dir=str(tmp_roots["runs"] / "r1"), adapter_id="yolo11n",
                                 resolved_protocol=pilot_smoke_protocol()), msgs.append, runner_settings)
    assert res.status == JobStatus.FAILED and res.reason_code == "PROTOCOL_HASH_MISMATCH"
    res = run_training_job(_spec(run_dir="/etc/mbnas", adapter_id="yolo11n"), msgs.append, runner_settings)
    assert res.reason_code == "PROTOCOL_MISSING"
    with pytest.raises(ValueError):
        run_training_job(JobSpec(job_id="j", run_id="r", kind=JobKind.EVALUATE, protocol_hash="h"), msgs.append,
                         runner_settings)
    assert msgs == []


def test_candidate_dir_is_a_single_component(tmp_path: Path) -> None:
    assert candidate_dir(tmp_path, "c-1") == tmp_path / "candidates" / "c-1"
    for bad in ("../x", "a/b", "..", ""):
        with pytest.raises(Exception):
            candidate_dir(tmp_path, bad)


def test_run_dir_outside_allowlist_and_progress_bridge(runner_settings, tmp_roots) -> None:  # A11
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_runner.training.context import progress_bridge

    proto = pilot_smoke_protocol()
    spec = _spec(run_dir="/etc/mbnas-run", adapter_id="yolo11n", resolved_protocol=proto).model_copy(
        update={"protocol_hash": protocol_hash(proto)})
    res = run_training_job(spec, lambda m: None, runner_settings)
    assert res.status == JobStatus.FAILED and res.reason_code.startswith("PATH")
    msgs = []
    bridge = progress_bridge(spec, msgs.append)
    bridge({"type": "epoch", "epoch": 1, "epochs_done": 2, "epoch_target_total": 2, "global_step": 8,
            "samples_seen": 64, "lr": 0.001, "native_metrics": {"m": float("nan")}})
    bridge({"type": "checkpoint", "kind": "training_full", "epoch": 2, "path": "/x", "sha256": "a", "bytes": 1,
            "hashes": {}, "rung": "L0"})
    assert [m.type for m in msgs] == ["progress", "checkpoint"]
    assert msgs[0].epoch == 2 and msgs[0].payload["native_metrics"]["m"] is None  # NaN never reaches JSON lines
    assert msgs[1].payload["candidate_id"] == "c1" and msgs[1].payload["kind"] == "training_full"
    assert all(m.to_line() for m in msgs)


@pytest.mark.parametrize("kind", [JobKind.CANDIDATE_TRAIN, JobKind.BASELINE_TRAIN])
def test_search_training_job_without_stage_hint_is_params_invalid(runner_settings, tmp_roots, kind) -> None:  # A11
    """S5-F8: a hint-less (later-)stage job is refused before anything is resolved or trained."""
    params = {"run_dir": str(tmp_roots["runs"] / "r1"), "adapter_id": "yolo11n"}
    spec = _spec(**params).model_copy(update={"kind": kind, "cumulative_target": 4})
    spec.params.pop("stage_epochs_added")
    res = run_training_job(spec, lambda m: None, runner_settings)
    assert res.status == JobStatus.FAILED and res.reason_code == "JOB_PARAMS_INVALID"
    assert "stage_epochs_added" in res.message
    assert not (tmp_roots["runs"] / "r1" / "candidates").exists()


def test_final_retrain_stage_hint_defaults_to_the_whole_target() -> None:
    from mbnas_runner.training.handlers import TrainJobParams

    p = TrainJobParams.model_validate({"run_dir": "/x", "adapter_id": "yolo11n"},
                                      context={"kind": JobKind.FINAL_RETRAIN, "cumulative_target": 5})
    assert p.stage_epochs_added == 5
    with pytest.raises(Exception):
        TrainJobParams.model_validate({"run_dir": "/x", "adapter_id": "yolo11n"},
                                      context={"kind": JobKind.CANDIDATE_TRAIN, "cumulative_target": 5})
