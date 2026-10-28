"""protocol.validator: real-pilot happy path (real data, synthetic registry) and one fixture per rule/code."""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from mbnas_core.protocol.presets import default_protocol, pilot_smoke_protocol
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_core.protocol.validator import validate
from conftest_common import A100  # SYNTHETIC host device of the owner fixtures (C05 D8)


# WP-1 contract §9: the PILOT subset lives under the data root of this test run ($MBNAS_TEST_DATA_ROOT, an allow root of
# the conftest settings); the pilot_required tests are host_data. The presets carry placeholder paths since WP-1
# (DD-A19), so the real-subset tests point the preset at this root explicitly (_real_pilot).
_DATA_ROOT = os.environ["MBNAS_TEST_DATA_ROOT"]
_PILOT = f"{_DATA_ROOT}/pilot_visdrone_v1"


def _real_pilot(proto: dict[str, Any], out: str = "pilot_smoke") -> dict[str, Any]:
    proto["console"]["paths"] = {"dataset_yaml": f"{_PILOT}/data.yaml", "train_root": f"{_PILOT}/images/train",
                                 "search_val_root": f"{_PILOT}/images/search_val",
                                 "final_eval_root": f"{_PILOT}/images/final_eval",
                                 "output_root": f"{os.environ['MBNAS_TEST_RUNS_ROOT']}/{out}"}
    return proto


def codes(result) -> set[str]:
    return {e["code"] for e in result.errors}


def warn_codes(result) -> set[str]:
    return {w["code"] for w in result.warnings}


@pytest.fixture
def tiny_proto(make_dataset, tmp_roots) -> dict[str, Any]:
    """PILOT_SMOKE preset pointed at a tiny synthetic dataset (fast; tmp dirs)."""
    paths = make_dataset()
    p = pilot_smoke_protocol()
    p["console"]["paths"] = {k: paths[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    p["console"]["paths"]["output_root"] = str(tmp_roots["runs"] / "out")
    return p


def run(proto: dict[str, Any], ctx_factory, **ctx_over: Any):
    r = validate(proto, ctx_factory(**ctx_over))
    # S4-F4: an invalid result makes no resolution claim -- checked for every invalid case in this module.
    if not r.valid:
        assert r.resolved_diff == [], r.resolved_diff
    return r


# -- happy paths ------------------------------------------------------------------------------------------------
def test_pilot_smoke_real_subset_resolves(pilot_required, ctx_factory) -> None:
    requested = _real_pilot(pilot_smoke_protocol())
    snapshot = copy.deepcopy(requested)
    r = run(requested, ctx_factory)
    assert r.valid, r.errors
    assert requested == snapshot, "validator must not mutate the request"
    res = r.resolved
    assert r.protocol_hash.startswith("sha256:") and r.protocol_hash == protocol_hash(res)
    assert res["status"] == "RESOLVED_PILOT" and res["original_status"] == "PRESET_PILOT_SMOKE"
    assert [x["epochs_added"] for x in res["rungs"]] == [2, 2, 2]
    t = res["training"]
    assert (t["fixed_horizon_epochs"], t["effective_batch"], t["nbs"]) == (6, 8, 8)
    c = res["console"]
    assert (c["execution_mode"], c["preset"], c["purpose"], c["run_namespace"]) == ("PILOT", "PILOT_SMOKE", "SMOKE", "pilot_smoke")
    assert c["benchmark"]["device_resolved"] == {"id": "host-gpu-0", "uuid": A100["uuid"],
                                                 "name": "NVIDIA A100 80GB PCIe"}
    by_id = {b["id"]: b["resolved"] for b in c["baselines"]}
    assert by_id["yolo11n"]["comparison_group"] == "common_scratch" and by_id["yolo11n"]["resolved_input_shape"] == [640, 640]
    assert {b["comparison_group"] for b in by_id.values()} == {"common_scratch"}  # PLAN §8.1: smoke is all-scratch
    assert by_id["rf-detr-nano"]["init"] == "scratch" and by_id["rf-detr-nano"]["resolved_input_shape"] == [640, 640]
    assert by_id["yolo26n"]["inference_head"] == "e2e_one2one_nms_false" and by_id["yolo11n"]["inference_head"] == "native"
    ul = res["training"]["backend_resolved"]["ultralytics"]
    assert (ul["batches_per_epoch"], ul["nbs"], ul["accumulate"], ul["warmup_iterations"], ul["optimizer_iterations"]) == (
        4, 8, 1, 0, 24)
    assert ul["optimizer"] == {"name": "AdamW", "lr": 0.001, "rule": "as requested"}
    assert by_id["etri-controlled-cnn-transformer"]["resolved_input_shape"] == [384, 384]
    assert c["baseline_override"]["reason"] and "yolo11s" in c["baseline_override"]["missing_from_default"]
    ds = res["dataset"]
    assert ds["counts"]["train"]["images"] == 32 and ds["counts"]["search_val"]["images"] == 16
    assert ds["counts"]["final_eval"]["images"] == 16
    assert all(ds[k].startswith("sha256:") for k in ("train_manifest_hash", "search_validation_manifest_hash",
                                                      "final_evaluation_manifest_hash", "class_mapping_hash"))
    assert res["resolution"]["canonicalization"] == "mbnas-canon/1"
    assert res["resolution"]["hash_excluded"] == ["console.label", "resolution.validated_at", "console.retention"]
    assert res["resolution"]["paths"]["train_root"]["realpath"] == os.path.realpath(f"{_PILOT}/images/train")
    fields = {d["field"] for d in r.resolved_diff}
    assert {"training.fixed_horizon_epochs", "dataset.train_manifest_hash", "status", "rungs[0].epochs_added"} <= fields
    assert r.capabilities["device"]["id"] == "host-gpu-0" and "yolo26n" in r.capabilities["baselines"]
    assert json.loads(json.dumps(r.to_response("val-1")))["validation_id"] == "val-1"


def test_resolution_is_a_fixed_point_and_hash_is_stable(pilot_required, ctx_factory) -> None:
    first = run(_real_pilot(pilot_smoke_protocol()), ctx_factory)
    second = run(first.resolved, ctx_factory, now=lambda: "2030-01-01T00:00:00.000000Z")
    assert second.valid and second.protocol_hash == first.protocol_hash
    relabelled = copy.deepcopy(_real_pilot(pilot_smoke_protocol()))
    relabelled["console"]["label"] = "another label"
    assert run(relabelled, ctx_factory).protocol_hash == first.protocol_hash
    changed = _real_pilot(pilot_smoke_protocol())
    changed["console"]["search"]["population"] = 6
    assert run(changed, ctx_factory).protocol_hash != first.protocol_hash


def test_default_example_reports_blockers_not_silently_passing(pilot_required, ctx_factory) -> None:
    r = run(_real_pilot(default_protocol(), "default"), ctx_factory)
    assert not r.valid and r.resolved is None and r.protocol_hash is None
    assert codes(r) == {"DEVICE_UNAVAILABLE"}  # example device "Orin NX" is not on this host
    p = _real_pilot(default_protocol(), "default")
    p["console"]["benchmark"]["device"] = "cuda:0"
    ok = run(p, ctx_factory)
    assert ok.valid, ok.errors
    assert ok.resolved["original_status"] == "DESIGN_EXAMPLE_NOT_EXECUTABLE" and ok.resolved["status"] == "RESOLVED_PILOT"
    assert ok.resolved["training"]["fixed_horizon_epochs"] == 90
    groups = {b["id"]: b["resolved"]["comparison_group"] for b in ok.resolved["console"]["baselines"]}
    assert set(groups.values()) == {"common_scratch"}
    assert "BASELINE_OVERRIDE" not in warn_codes(ok) and "baseline_override" not in ok.resolved["console"]
    next(b for b in p["console"]["baselines"] if b["id"] == "rf-detr-large")["init"] = "pretrained_native"
    opted = run(p, ctx_factory)
    by_id = {b["id"]: b["resolved"] for b in opted.resolved["console"]["baselines"]}
    assert by_id["rf-detr-large"]["comparison_group"] == "native_pretrained"  # explicit opt-in, separate group
    assert opted.protocol_hash != ok.protocol_hash


def test_tiny_dataset_happy_path(tiny_proto, ctx_factory) -> None:
    r = run(tiny_proto, ctx_factory)
    assert r.valid, r.errors
    assert r.resolved["dataset"]["counts"]["train"]["boxes"] == 4


# -- weights / epochs / ranges ----------------------------------------------------------------------------------
def test_legacy_weight_sum_043_not_normalised(tiny_proto, ctx_factory) -> None:
    tiny_proto["rungs"][0]["fitness_weights"] = {"ap": 0.2, "recall": 0.1, "latency_reward": 0.1, "size_reward": 0.03}
    r = run(tiny_proto, ctx_factory)
    assert "WEIGHTS_SUM_NOT_ONE" in codes(r) and r.resolved is None


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p["rungs"][0]["fitness_weights"].update(ap=-0.2, recall=0.5), "WEIGHTS_NEGATIVE"),
    (lambda p: p["rungs"][1].update(epoch_target_total=2), "EPOCHS_NOT_INCREASING"),
    (lambda p: p["rungs"][1].update(epoch_target_total=4.0), "EPOCHS_NOT_INT"),
    (lambda p: p["rungs"][1].update(epoch_target_total="4"), "EPOCHS_NOT_INT"),
    (lambda p: p["training"].update(warmup_epochs=2), "WARMUP_GE_L0"),
    (lambda p: p["training"].update(scheduler_horizon_epochs=5), "HORIZON_LT_LAST_RUNG"),
    (lambda p: p["rungs"][0]["promotion"].update(score_ge=1.5), "THRESHOLD_RANGE"),
    (lambda p: p["rungs"][0]["promotion"].update(ap_ge=-0.1), "AP_GUARD_RANGE"),
    (lambda p: p["rungs"][0]["promotion"].update(ap_ge=float("nan")), "AP_GUARD_RANGE"),
    (lambda p: p["fitness"].update(latency_bounds_ms=[200, 20]), "ANCHORS_INVALID"),
    (lambda p: p["fitness"].update(size_bounds_MB=[0, 100]), "ANCHORS_INVALID"),
    (lambda p: p["hard_constraints"].update(latency_le_ms=0), "HARD_CONSTRAINT_INVALID"),
    (lambda p: p["hard_constraints"].update(weights_size_le_MB=float("inf")), "HARD_CONSTRAINT_INVALID"),
    (lambda p: p["console"]["search"].pop("budget_gpu_hours"), "BUDGET_MISSING"),
    (lambda p: p["console"]["search"].update(budget_gpu_hours=0), "BUDGET_INVALID"),
    (lambda p: p["console"]["search"].update(budget_gpu_hours=float("inf")), "BUDGET_INVALID"),
    (lambda p: p["console"].pop("baseline_budget_gpu_hours"), "BUDGET_MISSING"),
    (lambda p: p["console"]["search"].update(seeds=[]), "SEEDS_INVALID"),
    (lambda p: p["console"]["search"].update(seeds=[101, 101]), "SEEDS_INVALID"),
    (lambda p: p["console"]["search"].update(seeds=[1.5]), "SEEDS_INVALID"),
    (lambda p: p["training"].update(precision="bf16"), "PRECISION_UNSUPPORTED"),
    (lambda p: p["console"]["benchmark"].update(precision="INT8"), "PRECISION_UNSUPPORTED"),
    (lambda p: p["training"].update(gradient_accumulation=2), "ACCUMULATION_RESUME_UNSUPPORTED"),
    (lambda p: p.update(surprise=1), "UNKNOWN_FIELD"),
    (lambda p: p["console"].update(colour="green"), "UNKNOWN_FIELD"),
    (lambda p: p["training"].update(warmup_epoch=1), "UNKNOWN_FIELD"),
    (lambda p: p["rungs"][0]["fitness_weights"].update(loss=0.0), "UNKNOWN_FIELD"),
    (lambda p: p["console"].update(execution_mode="FORMAL"), "EXECUTION_MODE_INVALID"),
    (lambda p: p["console"].update(execution_mode="HISTORICAL_REPRODUCTION"), "EXECUTION_MODE_INVALID"),
    (lambda p: p["console"].update(execution_mode="REGISTERED_SEARCH"), "EXECUTION_MODE_INVALID"),
    (lambda p: p["console"].update(run_namespace="default"), "FIELD_INVALID"),
    (lambda p: p["rungs"][2].update(promotion={"to": "L3", "score_ge": 0.1, "ap_ge": 0.0}), "FIELD_INVALID"),
    (lambda p: p["rungs"][0]["promotion"].update(to="L2"), "FIELD_INVALID"),
    (lambda p: p["dataset"].update(test_access_during_search=True), "FIELD_INVALID"),
    (lambda p: p.pop("hard_constraints"), "FIELD_MISSING"),
])
def test_rule_codes(tiny_proto, ctx_factory, mutate, code: str) -> None:
    mutate(tiny_proto)
    r = run(tiny_proto, ctx_factory)
    assert code in codes(r), r.errors
    assert not r.valid and r.resolved is None and r.protocol_hash is None
    assert all(e["field"] and e["message"] for e in r.errors)


@pytest.mark.parametrize("idx,value", [
    (1, 5),      # S4-F1: targets 2->4 give 2 added epochs; a conflicting manual value is rejected
    (0, 1),
    (2, 6),      # cumulative target entered as "added" is still a conflict
    (1, 0),
    (1, -2),
    (1, "2"),
    (1, 2.0),    # non-integer echo is not the computed integer
    (1, None),
])
def test_manual_epochs_added_conflict_rejected(tiny_proto, ctx_factory, idx: int, value: Any) -> None:
    tiny_proto["rungs"][idx]["epochs_added"] = value
    r = run(tiny_proto, ctx_factory)
    assert not r.valid and r.resolved is None and r.protocol_hash is None
    errs = [e for e in r.errors if e["field"] == f"rungs[{idx}].epochs_added"]
    assert [e["code"] for e in errs] == ["EPOCHS_ADDED_MANUAL"], r.errors
    assert "computed from the cumulative targets" in errs[0]["message"]
    # S4-F4: no resolution of the rejected field (nor any other) is advertised next to the error
    assert r.resolved_diff == []
    assert r.to_response(None)["resolved_diff"] == []


def test_invalid_result_has_empty_resolved_diff_valid_keeps_it(tiny_proto, ctx_factory) -> None:
    """S4-F4: the same document resolves with a diff when valid; once invalid, resolved_diff is empty."""
    ok = run(tiny_proto, ctx_factory)
    assert ok.valid and ok.resolved_diff
    assert any(d["field"] == "rungs[1].epochs_added" and d["resolved"] == 2 for d in ok.resolved_diff)
    assert ok.to_response(None)["resolved_diff"] == ok.resolved_diff
    tiny_proto["rungs"][1]["epochs_added"] = 5
    bad = validate(tiny_proto, ctx_factory())
    assert not bad.valid and bad.resolved_diff == []
    assert bad.to_response(None)["resolved_diff"] == []


def test_epochs_added_equal_echo_accepted_and_fixed_point(tiny_proto, ctx_factory) -> None:
    base = run(tiny_proto, ctx_factory)
    assert base.valid, base.errors
    echoed = copy.deepcopy(tiny_proto)
    for rung, added in zip(echoed["rungs"], [2, 2, 2]):
        rung["epochs_added"] = added
    r = run(echoed, ctx_factory)
    assert r.valid, r.errors
    assert r.protocol_hash == base.protocol_hash
    assert not [d for d in r.resolved_diff if d["field"].endswith(".epochs_added")]
    again = run(base.resolved, ctx_factory, now=lambda: "2030-01-01T00:00:00.000000Z")
    assert again.valid, again.errors
    assert again.protocol_hash == base.protocol_hash
    assert [x["epochs_added"] for x in again.resolved["rungs"]] == [2, 2, 2]


def test_horizon_larger_than_last_rung_warns(tiny_proto, ctx_factory) -> None:
    tiny_proto["training"]["scheduler_horizon_epochs"] = 10
    r = run(tiny_proto, ctx_factory)
    assert r.valid and "HORIZON_ADJUSTED" in warn_codes(r) and r.resolved["training"]["fixed_horizon_epochs"] == 6


def test_console_extra_is_free_form(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["extra"] = {"anything": {"goes": [1, 2]}}
    assert run(tiny_proto, ctx_factory).valid


def test_execution_mode_defaults_to_pilot_with_diff(tiny_proto, ctx_factory) -> None:
    del tiny_proto["console"]["execution_mode"]
    r = run(tiny_proto, ctx_factory)
    assert r.valid and r.resolved["console"]["execution_mode"] == "PILOT"
    assert {"field": "console.execution_mode", "requested": None, "resolved": "PILOT",
            "reason": "no pilot calibration: execution is PILOT"} in r.resolved_diff


def test_example_without_paths_not_executable(ctx_factory) -> None:
    p = default_protocol()
    p["console"]["paths"]["train_root"] = ""
    r = run(p, ctx_factory)
    assert codes(r) == {"EXAMPLE_NOT_EXECUTABLE"}


def test_registered_search_requires_calibration_file(tiny_proto, ctx_factory, tmp_roots) -> None:
    for key in ("preset", "purpose", "run_namespace"):
        tiny_proto["console"].pop(key)
    tiny_proto["status"] = "USER_POLICY"
    tiny_proto["console"]["execution_mode"] = "REGISTERED_SEARCH"
    assert "EXECUTION_MODE_INVALID" in codes(run(tiny_proto, ctx_factory))
    calib = tmp_roots["runs"] / "calibration.json"
    calib.write_text("{}")
    tiny_proto["console"]["extra"] = {"pilot_calibration_ref": str(calib)}
    r = run(tiny_proto, ctx_factory)
    assert r.valid, r.errors
    assert r.resolved["status"] == "RESOLVED_REGISTERED" and r.resolved["console"]["run_namespace"] == "default"


# -- baselines / registry / device ------------------------------------------------------------------------------
def test_baseline_unknown_duplicate_unavailable(tiny_proto, ctx_factory, registry) -> None:
    bl = tiny_proto["console"]["baselines"]
    bl.append({"id": "yolo99z", "enabled": True})
    bl.append(dict(bl[0]))
    registry["rt-detr-l"].update(status="BLOCKED_SOURCE", reason="weights hash mismatch")
    r = run(tiny_proto, ctx_factory)
    assert {"BASELINE_UNKNOWN", "BASELINE_DUPLICATE", "BASELINE_UNAVAILABLE"} <= codes(r)
    msg = next(e["message"] for e in r.errors if e["code"] == "BASELINE_UNAVAILABLE")
    assert "weights hash mismatch" in msg


def test_disabled_unavailable_baseline_is_fine(tiny_proto, ctx_factory, registry) -> None:
    registry["rt-detr-l"].update(status="UNSUPPORTED", reason="x")
    next(b for b in tiny_proto["console"]["baselines"] if b["id"] == "rt-detr-l")["enabled"] = False
    r = run(tiny_proto, ctx_factory)
    assert r.valid and r.capabilities["baselines"]["rt-detr-l"]["enabled"] is False


def test_init_unsupported_for_scratch_policy(tiny_proto, ctx_factory, registry) -> None:
    registry["rf-detr-nano"]["capabilities"]["init_modes"] = ["pretrained_native"]
    r = run(tiny_proto, ctx_factory)
    err = next(e for e in r.errors if e["code"] == "INIT_UNSUPPORTED")
    assert err["field"].endswith(".init") and "native_pretrained" in err["message"]


def test_baseline14_override_without_reason_warns(tiny_proto, ctx_factory) -> None:
    del tiny_proto["console"]["baseline_override_reason"]
    r = run(tiny_proto, ctx_factory)
    assert r.valid and "BASELINE_OVERRIDE" in warn_codes(r)
    assert r.resolved["console"]["baseline_override"]["added"] == ["yolo11n", "yolo26n", "rf-detr-nano"]


def test_device_unavailable_and_alias_resolution(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["benchmark"]["device"] = "Orin NX"
    r = run(tiny_proto, ctx_factory)
    assert "DEVICE_UNAVAILABLE" in codes(r) and "host-gpu-0" in r.errors[0]["message"]
    tiny_proto["console"]["benchmark"]["device"] = A100["uuid"]
    assert run(tiny_proto, ctx_factory).resolved["console"]["benchmark"]["device_resolved"]["id"] == "host-gpu-0"


def test_input_shape_rules(tiny_proto, ctx_factory, registry) -> None:
    tiny_proto["training"]["requested_input_size"] = [600, 500]
    del registry["yolo26n"]["capabilities"]["input_size_rule"]
    r = run(tiny_proto, ctx_factory)
    by_id = {b["id"]: b["resolved"] for b in r.resolved["console"]["baselines"]}
    assert by_id["yolo11n"]["resolved_input_shape"] == [608, 512]
    assert by_id["rf-detr-nano"]["resolved_input_shape"] == [608, 608]
    assert by_id["yolo26n"]["resolved_input_shape"] == [600, 500] and "INPUT_RULE_MISSING" in warn_codes(r)


# -- paths / dataset --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,value,code", [
    ("dataset_yaml", "data.yaml", "PATH_NOT_ABSOLUTE"),
    ("train_root", "/ABSOLUTE/path/train", "PATH_PLACEHOLDER"),
    ("search_val_root", "/root/detection/MBNAS_data/../MBNAS_data/x", "PATH_TRAVERSAL"),
    ("final_eval_root", "/etc", "PATH_OUTSIDE_ALLOWLIST"),
    ("train_root", f"{_DATA_ROOT}/does-not-exist", "PATH_NOT_FOUND"),
    ("output_root", "/root/detection/ETRI_NAS/application/MBNAS_server/runs", "PATH_OUTSIDE_ALLOWLIST"),
])
def test_path_codes_via_validator(tiny_proto, ctx_factory, key: str, value: str, code: str) -> None:
    tiny_proto["console"]["paths"][key] = value
    r = run(tiny_proto, ctx_factory)
    assert {"field": f"console.paths.{key}", "code": code} in [{"field": e["field"], "code": e["code"]} for e in r.errors]


def test_output_not_writable_and_disk_low(tiny_proto, ctx_factory, monkeypatch) -> None:
    assert "DISK_LOW" in codes(run(tiny_proto, ctx_factory, disk_free_gb=lambda p: 1.0))
    import os
    real = os.access
    monkeypatch.setattr(os, "access", lambda p, m: False if m & os.W_OK else real(p, m))
    assert "OUTPUT_NOT_WRITABLE" in codes(run(tiny_proto, ctx_factory))


def test_dataset_yaml_invalid(tiny_proto, ctx_factory) -> None:
    Path(tiny_proto["console"]["paths"]["dataset_yaml"]).write_text("train: [a, b]\nval: v\nnames: [x]\n")
    assert "DATASET_YAML_INVALID" in codes(run(tiny_proto, ctx_factory))
    Path(tiny_proto["console"]["paths"]["dataset_yaml"]).write_text("!!python/object:os.system 'echo hi'\n")
    assert "DATASET_YAML_INVALID" in codes(run(tiny_proto, ctx_factory))


def test_dataset_yaml_path_mismatch_lists_both(tiny_proto, ctx_factory, make_dataset) -> None:
    other = make_dataset("other", seed0=100)
    tiny_proto["console"]["paths"]["search_val_root"] = other["search_val_root"]
    r = run(tiny_proto, ctx_factory)
    err = next(e for e in r.errors if e["code"] == "DATASET_YAML_PATH_MISMATCH")
    assert other["search_val_root"] in err["message"] and "/ds/images/val" in err["message"]


def test_split_same_root(tiny_proto, ctx_factory) -> None:
    yaml_path = Path(tiny_proto["console"]["paths"]["dataset_yaml"])
    yaml_path.write_text(yaml_path.read_text().replace("test: images/test", "test: images/val"))
    tiny_proto["console"]["paths"]["final_eval_root"] = tiny_proto["console"]["paths"]["search_val_root"]
    assert "SPLIT_SAME_ROOT" in codes(run(tiny_proto, ctx_factory))


def test_split_overlap_by_content(tiny_proto, ctx_factory) -> None:
    paths = tiny_proto["console"]["paths"]
    src = Path(paths["train_root"]) / "train_000.png"
    shutil.copyfile(src, Path(paths["final_eval_root"]) / "leaked.png")
    r = run(tiny_proto, ctx_factory)
    err = next(e for e in r.errors if e["code"] == "SPLIT_OVERLAP")
    assert "final_eval/train" in err["message"] and "leaked" in err["message"]


def test_class_map_and_label_errors(tiny_proto, ctx_factory) -> None:
    lbl = Path(tiny_proto["console"]["paths"]["train_root"].replace("/images/", "/labels/"))
    (lbl / "train_000.txt").write_text("3 0.5 0.5 0.2 0.2\n")
    (lbl / "train_001.txt").write_text("0 0.5 0.5 1.4 0.2\n0 nan 0.5 0.1 0.1\n")
    r = run(tiny_proto, ctx_factory)
    assert {"CLASS_MAP_MISMATCH", "LABEL_INVALID"} <= codes(r)


def test_empty_split(tiny_proto, ctx_factory) -> None:
    for f in Path(tiny_proto["console"]["paths"]["final_eval_root"]).iterdir():
        f.unlink()
    assert "DATASET_EMPTY" in codes(run(tiny_proto, ctx_factory))


def test_requested_dataset_hash_recomputed(tiny_proto, ctx_factory) -> None:
    tiny_proto["dataset"]["train_manifest_hash"] = "sha256:stale"
    r = run(tiny_proto, ctx_factory)
    assert r.valid and "DATASET_HASH_RECOMPUTED" in warn_codes(r)
    assert r.resolved["dataset"]["train_manifest_hash"] != "sha256:stale"


def test_non_object_protocol() -> None:
    r = validate([], None)  # type: ignore[arg-type]
    assert not r.valid and r.errors[0]["code"] == "FIELD_INVALID"
    assert r.resolved_diff == []


def test_optimizer_auto_resolution_and_warmup_iterations(tiny_proto, ctx_factory) -> None:
    tiny_proto["training"].update(optimizer="auto", warmup_epochs=1, batch_per_gpu=3)
    r = run(tiny_proto, ctx_factory)
    assert r.valid, r.errors
    ul = r.resolved["training"]["backend_resolved"]["ultralytics"]
    # 4 train images, batch 3 -> 2 batches/epoch; warmup round(min(1, 6-1) * 2) = 2; iterations ceil(4/3)*6 = 12
    assert (ul["batches_per_epoch"], ul["warmup_iterations"], ul["optimizer_iterations"]) == (2, 2, 12)
    assert ul["optimizer"] == {"name": "AdamW", "lr": round(0.002 * 5 / (4 + 1), 6), "momentum": 0.9,
                               "warmup_bias_lr": 0.0, "rule": "ultralytics 8.4.173 optimizer=auto"}
    lr_diff = next(d for d in r.resolved_diff if d["field"] == "training.lr_initial")
    assert lr_diff["requested"] == 0.001 and lr_diff["resolved"].startswith("ignored (optimizer=auto -> AdamW")


def test_ultralytics_scratch_files_are_ignored_by_manifests(tiny_proto, ctx_factory) -> None:
    first = run(tiny_proto, ctx_factory)
    train = Path(tiny_proto["console"]["paths"]["train_root"])
    (train.parent.parent / "labels" / "train.cache").write_bytes(b"ultralytics cache")
    (train / "x.cache").write_bytes(b"cache")
    assert run(tiny_proto, ctx_factory).protocol_hash == first.protocol_hash


def _leaf_paths(node: Any, prefix: tuple = ()) -> list[tuple]:
    out = [prefix] if prefix else []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _leaf_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _leaf_paths(v, prefix + (i,))
    return out


def test_corrupted_inputs_never_crash(tiny_proto, ctx_factory) -> None:
    """Input corruption: every field replaced by a wrong type/NaN yields errors (or a valid result), never an exception."""
    ctx = ctx_factory()
    baseline = validate(tiny_proto, ctx)
    assert baseline.valid
    checked = 0
    for path in _leaf_paths(tiny_proto):
        for bad in (None, [], {}, "x", float("nan"), -1):
            doc = copy.deepcopy(tiny_proto)
            node = doc
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = bad
            result = validate(doc, ctx)
            assert isinstance(result.valid, bool)
            if not result.valid:
                assert result.errors and result.resolved is None and result.protocol_hash is None
                assert result.resolved_diff == [], (path, bad, result.resolved_diff)
            checked += 1
    assert checked > 500


def test_non_finite_anywhere_is_a_field_error(tiny_proto, ctx_factory) -> None:
    tiny_proto["protocol_id"] = float("nan")
    tiny_proto["allocation"]["note"] = float("inf")
    r = run(tiny_proto, ctx_factory)
    assert not r.valid and r.errors[0]["code"] == "FIELD_INVALID" and r.errors[0]["field"] in ("protocol_id", "allocation.note")


def test_client_comparison_group_is_overridden(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["baselines"][0]["comparison_group"] = "native_pretrained"
    r = run(tiny_proto, ctx_factory)
    assert r.valid and r.resolved["console"]["baselines"][0]["comparison_group"] == "common_scratch"
    assert any(d["field"] == "console.baselines[0].comparison_group" for d in r.resolved_diff)


def test_baseline_only_run_needs_no_search_budget(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["search"] = {"arms": []}
    assert run(tiny_proto, ctx_factory).valid
    tiny_proto["console"]["search"] = {"arms": "GA"}
    r = run(tiny_proto, ctx_factory)
    assert {"FIELD_INVALID", "BUDGET_MISSING", "SEEDS_INVALID"} <= codes(r)


# -- S8-F3: hard_constraints.proxy_may_certify_final_deployment is a boolean with a documented effect ---------------
@pytest.mark.parametrize("value", ["yes", 1, 0, "true", None, [True], 1.0])
def test_proxy_may_certify_must_be_boolean(tiny_proto, ctx_factory, value: Any) -> None:
    tiny_proto["hard_constraints"]["proxy_may_certify_final_deployment"] = value
    r = run(tiny_proto, ctx_factory)
    assert not r.valid and r.resolved is None and r.protocol_hash is None
    errs = [e for e in r.errors if e["field"] == "hard_constraints.proxy_may_certify_final_deployment"]
    assert [e["code"] for e in errs] == ["FIELD_INVALID"], r.errors


def test_proxy_may_certify_false_or_absent_is_valid_without_note(tiny_proto, ctx_factory) -> None:
    for present in (True, False):
        proto = copy.deepcopy(tiny_proto)
        if present:
            proto["hard_constraints"]["proxy_may_certify_final_deployment"] = False
        else:
            proto["hard_constraints"].pop("proxy_may_certify_final_deployment", None)
        r = run(proto, ctx_factory)
        assert r.valid, r.errors
        assert "PROXY_PIPELINE_UNAVAILABLE" not in warn_codes(r)


def test_proxy_may_certify_true_is_valid_and_notes_no_calibrated_proxy_pipeline(tiny_proto, ctx_factory) -> None:
    tiny_proto["hard_constraints"]["proxy_may_certify_final_deployment"] = True
    r = run(tiny_proto, ctx_factory)
    assert r.valid, r.errors
    assert r.resolved["hard_constraints"]["proxy_may_certify_final_deployment"] is True
    notes = [w for w in r.warnings if w["code"] == "PROXY_PIPELINE_UNAVAILABLE"]
    assert len(notes) == 1 and notes[0]["field"] == "hard_constraints.proxy_may_certify_final_deployment"
    assert "no calibrated proxy pipeline" in notes[0]["message"]
