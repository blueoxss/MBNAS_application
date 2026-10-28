"""Configuration linkage uses actual saved files and preserves run boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from mbnas_core.console.model_services import describe_candidate
from mbnas_core.artifacts.registry import ArtifactError, run_dir
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration.model_artifacts import record_model_configuration, record_native_images


def _run_and_candidate(conn, settings):
    protocol = {"console": {"paths": {"output_root": settings.output_root}}}
    run, _ = repo.create_run(conn, id="run-config", namespace="test", protocol_hash="sha256:test",
                             resolved_protocol=protocol, requested_protocol=protocol, execution_mode="PILOT",
                             idempotency_key="config-test", payload_sha256="test")
    candidate = repo.insert_candidate(conn, id="baseline-config", run_id=run["id"], name="baseline",
                                       arm="Baseline", family="YOLO11")
    return run, candidate


def test_actual_adapter_yaml_is_model_detail_and_download_target(db, settings):
    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        path = run_dir(run) / "candidates" / candidate["id"] / "architecture.yaml"
        path.parent.mkdir(parents=True)
        text = yaml.safe_dump({"nc": 2, "backbone": [[-1, 1, "Conv", [8, 3, 2]]],
                               "head": [[[-1], 1, "Detect", ["nc"]]]})
        path.write_text(text)
        saved = record_model_configuration(db, run, candidate)
        assert saved["architecture_yaml"] == text
        assert saved["architecture_path"] == str(path)
        artifact = repo.get_artifact(db, run["id"], saved["audit"]["execution_configuration"]["artifact_id"])
        assert artifact["candidate_id"] == candidate["id"]
        assert artifact["relpath"] == f"candidates/{candidate['id']}/architecture.yaml"
        detail = describe_candidate(saved, run)
        assert detail["class_count"] == 2
        assert detail["status"] == "UNVERIFIED"  # ingestion is not a CPU shape check
        assert detail["lineage"] is None


def test_missing_file_does_not_fabricate_configuration(db, settings):
    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        assert record_model_configuration(db, run, candidate) == candidate
        assert repo.list_artifacts(db, run["id"]) == []


def test_configuration_symlink_cannot_leave_run(db, settings, tmp_path):
    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        path = run_dir(run) / "candidates" / candidate["id"] / "architecture.yaml"
        path.parent.mkdir(parents=True)
        outside = tmp_path / "private.yaml"
        outside.write_text("secret: 1")
        path.symlink_to(outside)
        with pytest.raises(ArtifactError, match="leaves its run"):
            record_model_configuration(db, run, candidate)
        assert repo.get_candidate(db, candidate["id"])["architecture_yaml"] is None


def test_written_baseline_config_records_actual_dataset_nc(tmp_path):
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    original = {"nc": 80, "backbone": [[-1, 1, "Conv", [8, 3, 2]]], "head": [[[-1], 1, "Detect", ["nc"]]]}
    context = SimpleNamespace(candidate_dir=tmp_path, data=SimpleNamespace(names={0: "drone", 1: "bird"}))
    UltralyticsAdapter("YOLO11", "detect")._write_arch_yaml(context, original)
    assert yaml.safe_load((tmp_path / "architecture.yaml").read_text())["nc"] == 2
    assert original["nc"] == 80  # source config is immutable


def test_native_images_have_exact_run_binding_and_no_invented_checkpoint(db, settings):
    from PIL import Image

    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        native = run_dir(run) / "candidates" / candidate["id"] / "native" / "stage_e0000_e0012"
        native.mkdir(parents=True)
        Image.new("RGB", (16, 16), "green").save(native / "train_batch0.jpg")
        Image.new("RGB", (16, 16), "blue").save(native / "val_batch1_pred.jpg")
        Image.new("RGB", (16, 16), "gray").save(native / "confusion_matrix.png")
        (native / "algorithm.py").write_text("source must never become an image artifact")
        (native / "train_batch99.jpg").write_text("not an image")
        artifacts = record_native_images(db, run, candidate)
        assert len(artifacts) == 3
        assert all(a["run_id"] == run["id"] and a["candidate_id"] == candidate["id"] for a in artifacts)
        batch = next(a for a in artifacts if a["meta"]["filename"] == "train_batch0.jpg")
        assert batch["mime_type"] == "image/jpeg"
        assert batch["meta"]["batch_index"] == 0
        assert batch["meta"]["stage_epoch_target"] == 12
        assert batch["meta"]["epoch"] is None
        assert batch["meta"]["checkpoint_id"] is None
        # An exact refresh is idempotent and does not generate another image.
        assert [a["id"] for a in record_native_images(db, run, candidate)] == [a["id"] for a in artifacts]


def test_native_image_symlink_escape_is_not_registered(db, settings, tmp_path):
    from PIL import Image

    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        native = run_dir(run) / "candidates" / candidate["id"] / "native" / "stage_e0000_e0001"
        native.mkdir(parents=True)
        outside = tmp_path / "private.jpg"
        Image.new("RGB", (16, 16)).save(outside)
        (native / "train_batch0.jpg").symlink_to(outside)
        assert record_native_images(db, run, candidate) == []


def test_native_images_cannot_alias_another_candidate(db, settings):
    from PIL import Image

    with tx(db):
        run, candidate = _run_and_candidate(db, settings)
        candidates = run_dir(run) / "candidates"
        other = candidates / "another-model" / "native" / "stage_e0000_e0001"
        other.mkdir(parents=True)
        Image.new("RGB", (16, 16)).save(other / "train_batch0.jpg")
        (candidates / candidate["id"]).symlink_to(other.parent.parent, target_is_directory=True)
        with pytest.raises(ArtifactError, match="leave their run"):
            record_native_images(db, run, candidate)


def test_new_native_plots_do_not_change_legacy_recipe_fingerprints():
    from mbnas_runner.training.recipe import recipe_from_protocol

    protocol = {"training": {"precision": "fp32", "optimizer": "SGD", "lr_initial": 0.01,
                               "lr_final_ratio": 0.01, "weight_decay": 0.0005, "batch_per_gpu": 1}}
    legacy = recipe_from_protocol(protocol, [64, 64], fixed_horizon=2)
    assert "validation_enabled" not in legacy
    assert "native_image_artifacts" not in legacy
    protocol["console"] = {"extra": {"training": {"enabled": True, "validation_enabled": False,
                                                   "native_image_artifacts": True}}}
    selected = recipe_from_protocol(protocol, [64, 64], fixed_horizon=2)
    assert selected["validation_enabled"] is False
    assert selected["native_image_artifacts"] is True
    assert {k: v for k, v in selected.items() if k not in ("validation_enabled", "native_image_artifacts")} == legacy
