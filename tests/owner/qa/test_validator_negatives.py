"""QA: validator negatives driven through ``POST /protocols/validate`` (the path the browser uses).

Each case must answer 200 ``valid=false`` (or a contract 422 for schema-level garbage) with the documented code on the
documented field, never a resolved protocol, hash or validation id, and never a 500.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import harness
import pytest
from conftest import codes_of
from harness import auth


def mutated(proto: dict, fn) -> dict:
    out = copy.deepcopy(proto)
    fn(out)
    return out


def expect_rejected(validate, proto: dict, *codes: str) -> dict:
    r = validate(proto)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["valid"] is False and body.get("resolved") is None and not body.get("protocol_hash") and not body.get("validation_id")
    assert set(codes) <= codes_of(body), (codes, body["errors"])
    assert all(e["field"] and e["message"] for e in body["errors"])
    return body


def test_the_unmodified_protocol_is_valid(validate, protocol):
    body = validate(protocol).json()
    assert body["valid"] is True, body["errors"]
    assert body["resolved"] and body["protocol_hash"].startswith("sha256:") and body["validation_id"]


# -- legacy weights, epochs, warmup -----------------------------------------------------------------------------
@pytest.mark.parametrize("rung", [0, 1])
@pytest.mark.parametrize("weights", [
    {"ap": 0.2, "recall": 0.1, "latency_reward": 0.1, "size_reward": 0.03},     # the legacy .43 set
    {"ap": 0.4, "recall": 0.4, "latency_reward": 0.4, "size_reward": 0.4},
    {"ap": 0.3, "recall": 0.3, "latency_reward": 0.2, "size_reward": 0.201},    # 1.001: outside tolerance
    {"ap": 0.0, "recall": 0.0, "latency_reward": 0.0, "size_reward": 0.0},
])
def test_weights_not_summing_to_one_are_blocked_never_normalised(validate, protocol, rung, weights):
    p = mutated(protocol, lambda x: x["rungs"][rung].update(fitness_weights=weights))
    body = expect_rejected(validate, p, "WEIGHTS_SUM_NOT_ONE")
    assert any(e["field"].startswith(f"rungs[{rung}]") or f"rungs.{rung}" in e["field"] or f"[{rung}]" in e["field"]
               for e in body["errors"] if e["code"] == "WEIGHTS_SUM_NOT_ONE")


def test_weights_within_tolerance_are_accepted_unchanged(validate, protocol):
    w = {"ap": 0.3, "recall": 0.3, "latency_reward": 0.2, "size_reward": 0.2 + 5e-7}
    body = validate(mutated(protocol, lambda x: x["rungs"][0].update(fitness_weights=w))).json()
    assert body["valid"] is True
    assert body["resolved"]["rungs"][0]["fitness_weights"] == w  # echoed verbatim, not rescaled


@pytest.mark.parametrize("label,fn,code", [
    ("decreasing", lambda p: p["rungs"][1].update(epoch_target_total=1), "EPOCHS_NOT_INCREASING"),
    ("equal", lambda p: p["rungs"][1].update(epoch_target_total=p["rungs"][0]["epoch_target_total"]), "EPOCHS_NOT_INCREASING"),
    ("fractional", lambda p: p["rungs"][1].update(epoch_target_total=2.5), "EPOCHS_NOT_INT"),
    ("string", lambda p: p["rungs"][1].update(epoch_target_total="2"), "EPOCHS_NOT_INT"),
    ("bool", lambda p: p["rungs"][1].update(epoch_target_total=True), "EPOCHS_NOT_INT"),
])
def test_epoch_targets_must_be_increasing_integers(validate, protocol, label, fn, code):
    expect_rejected(validate, mutated(protocol, fn), code)


def test_zero_and_negative_epochs_are_blocked(validate, protocol):
    for value in (0, -3):
        r = validate(mutated(protocol, lambda p: p["rungs"][0].update(epoch_target_total=value)))
        assert r.status_code in (200, 422), value
        assert r.status_code == 422 or (r.json()["valid"] is False and not r.json().get("resolved")), value


@pytest.mark.parametrize("extra", [0, 1, 5])
def test_warmup_at_or_above_the_first_rung_is_blocked(validate, protocol, extra):
    first = protocol["rungs"][0]["epoch_target_total"]
    expect_rejected(validate, mutated(protocol, lambda x: x["training"].update(warmup_epochs=first + extra)), "WARMUP_GE_L0")


def test_warmup_equal_to_l0_is_blocked_and_one_below_is_not(validate, protocol):
    l0 = protocol["rungs"][0]["epoch_target_total"]
    expect_rejected(validate, mutated(protocol, lambda x: x["training"].update(warmup_epochs=l0)), "WARMUP_GE_L0")
    ok = validate(mutated(protocol, lambda x: x["training"].update(warmup_epochs=max(l0 - 1, 0)))).json()
    assert ok["valid"] is True, ok["errors"]


# -- non-finite values on the wire --------------------------------------------------------------------------------
NAN_CASES = [
    ('"latency_bounds_ms": [', '"latency_bounds_ms": [NaN,'),
    ('"ap_ge": 0.0', '"ap_ge": NaN'),
    ('"score_ge": 0.0', '"score_ge": Infinity'),
    ('"latency_le_ms": 1000', '"latency_le_ms": Infinity'),
    ('"weights_size_le_MB": 500', '"weights_size_le_MB": -Infinity'),
    ('"budget_gpu_hours": 0.75', '"budget_gpu_hours": NaN'),
    ('"ap": ', '"ap": NaN, "ap_unused": '),
]


@pytest.mark.parametrize("needle,replacement", NAN_CASES)
def test_nan_and_infinity_on_the_wire_never_validate_or_crash(client, protocol, needle, replacement):
    raw = json.dumps({"protocol": protocol})
    assert needle in raw, needle
    r = client.post("/api/v1/protocols/validate", content=raw.replace(needle, replacement, 1),
                    headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code in (200, 422), r.text[:300]
    if r.status_code == 200:
        body = r.json()
        assert body["valid"] is False and body.get("resolved") is None and not body.get("validation_id"), body.get("errors")
    assert "NaN" not in r.text and "Infinity" not in r.text  # the answer itself is strict JSON


def test_nan_epochs_on_the_wire_are_blocked(client, protocol):
    raw = json.dumps({"protocol": protocol}).replace('"epoch_target_total": 1', '"epoch_target_total": NaN', 1)
    r = client.post("/api/v1/protocols/validate", content=raw, headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code in (200, 422) and (r.status_code == 422 or r.json()["valid"] is False)


# -- budgets, anchors, constraints --------------------------------------------------------------------------------
@pytest.mark.parametrize("label,fn,code", [
    ("search budget missing", lambda p: p["console"]["search"].pop("budget_gpu_hours"), "BUDGET_MISSING"),
    ("baseline budget missing", lambda p: p["console"].pop("baseline_budget_gpu_hours"), "BUDGET_MISSING"),
    ("search budget zero", lambda p: p["console"]["search"].update(budget_gpu_hours=0), "BUDGET_INVALID"),
    ("search budget negative", lambda p: p["console"]["search"].update(budget_gpu_hours=-1.5), "BUDGET_INVALID"),
    ("search budget string", lambda p: p["console"]["search"].update(budget_gpu_hours="lots"), "BUDGET_INVALID"),
    ("search budget null", lambda p: p["console"]["search"].update(budget_gpu_hours=None), "BUDGET_MISSING"),
    ("anchors reversed", lambda p: p["fitness"].update(latency_bounds_ms=[200, 20]), "ANCHORS_INVALID"),
    ("anchors equal", lambda p: p["fitness"].update(latency_bounds_ms=[50, 50]), "ANCHORS_INVALID"),
    ("size anchor zero", lambda p: p["fitness"].update(size_bounds_MB=[0, 100]), "ANCHORS_INVALID"),
    ("latency constraint zero", lambda p: p["hard_constraints"].update(latency_le_ms=0), "HARD_CONSTRAINT_INVALID"),
])
def test_budget_anchor_and_constraint_negatives(validate, protocol, label, fn, code):
    r = validate(mutated(protocol, fn))
    if r.status_code == 422:  # schema-level rejection is also a block
        assert r.json()["code"] == "VALIDATION_FAILED"
        return
    expect_rejected(validate, mutated(protocol, fn), code)


# -- device / baselines ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("device", ["Jetson Orin NX", "cuda:7", "GPU-00000000-0000-0000-0000-000000000000", "tpu", "A100 "])
def test_unavailable_devices_are_blocked_with_the_available_list(validate, protocol, device):
    body = expect_rejected(validate, mutated(protocol, lambda p: p["console"]["benchmark"].update(device=device)), "DEVICE_UNAVAILABLE")
    assert "host-gpu-0" in next(e["message"] for e in body["errors"] if e["code"] == "DEVICE_UNAVAILABLE")


def test_unknown_unavailable_and_duplicate_baselines(validate, protocol, registry):
    ids = [b["id"] for b in protocol["console"]["baselines"] if b.get("enabled")]
    assert ids
    unknown = mutated(protocol, lambda p: p["console"]["baselines"].append({"id": "yolo99-imaginary", "enabled": True}))
    expect_rejected(validate, unknown, "BASELINE_UNKNOWN")
    registry[ids[0]].update(status="UNSUPPORTED", reason="no CUDA kernel for this op")
    body = expect_rejected(validate, protocol, "BASELINE_UNAVAILABLE")
    assert "no CUDA kernel for this op" in next(e["message"] for e in body["errors"] if e["code"] == "BASELINE_UNAVAILABLE")
    registry[ids[0]].update(status="AVAILABLE", reason="ok")
    dup = mutated(protocol, lambda p: p["console"]["baselines"].append(dict(p["console"]["baselines"][0])))
    expect_rejected(validate, dup, "BASELINE_DUPLICATE")


@pytest.mark.parametrize("status", ["UNSUPPORTED", "BLOCKED_SOURCE", "UNVERIFIED"])
def test_every_non_available_registry_status_blocks_an_enabled_baseline(validate, protocol, registry, status):
    bid = next(b["id"] for b in protocol["console"]["baselines"] if b.get("enabled"))
    registry[bid].update(status=status, reason=f"{status} in this test")
    r = validate(protocol).json()
    assert r["valid"] is False and "BASELINE_UNAVAILABLE" in codes_of(r)


def test_scratch_policy_for_a_model_without_scratch_support_is_blocked(validate, protocol, registry):
    bid = next(b["id"] for b in protocol["console"]["baselines"] if b.get("enabled"))
    registry[bid]["capabilities"]["init_modes"] = ["pretrained_native"]
    body = expect_rejected(validate, protocol, "INIT_UNSUPPORTED")
    assert any(e["field"].endswith(".init") for e in body["errors"])


# -- splits ---------------------------------------------------------------------------------------------------
def _split_dirs(proto: dict) -> dict[str, Path]:
    paths = proto["console"]["paths"]
    return {"train": Path(paths["train_root"]), "search_val": Path(paths["search_val_root"]),
            "final_eval": Path(paths["final_eval_root"])}


@pytest.mark.parametrize("src,dst", [("train", "search_val"), ("train", "final_eval"), ("search_val", "final_eval"),
                                     ("search_val", "train"), ("final_eval", "train")])
def test_duplicated_image_across_splits_is_split_overlap(validate, protocol, src, dst):
    dirs = _split_dirs(protocol)
    first = sorted(dirs[src].iterdir())[0]
    shutil.copyfile(first, dirs[dst] / f"dup_of_{src}_{first.name}")  # same bytes, different file name
    body = expect_rejected(validate, protocol, "SPLIT_OVERLAP")
    msg = next(e["message"] for e in body["errors"] if e["code"] == "SPLIT_OVERLAP")
    assert "1 identical image" in msg and f"{src}" in msg and f"{dst}" in msg


def test_overlap_is_by_content_not_by_name(validate, protocol):
    dirs = _split_dirs(protocol)
    src = sorted(dirs["train"].iterdir())[0]
    shutil.copyfile(src, dirs["search_val"] / "totally_different_name.png")
    expect_rejected(validate, protocol, "SPLIT_OVERLAP")
    (dirs["search_val"] / "totally_different_name.png").unlink()
    assert validate(protocol).json()["valid"] is True  # removing the duplicate clears the error


def test_same_name_but_different_content_is_not_an_overlap(validate, protocol):
    dirs = _split_dirs(protocol)
    # distinct content, identical file stem in two splits: legitimate
    assert (dirs["train"] / "train_000.png").exists()
    from PIL import Image

    Image.new("RGB", (32, 24), (1, 2, 3)).save(dirs["search_val"] / "train_000.png")
    (dirs["search_val"].parent.parent / "labels" / "val").mkdir(parents=True, exist_ok=True)
    (dirs["search_val"].parent.parent / "labels" / "val" / "train_000.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    assert validate(protocol).json()["valid"] is True


@pytest.mark.parametrize("a,b", [("train_root", "search_val_root"), ("search_val_root", "final_eval_root"),
                                 ("train_root", "final_eval_root")])
def test_same_root_for_two_splits_is_blocked(validate, protocol, a, b):
    p = mutated(protocol, lambda x: x["console"]["paths"].update({b: x["console"]["paths"][a]}))
    r = validate(p).json()
    assert r["valid"] is False and codes_of(r) & {"SPLIT_SAME_ROOT", "DATASET_YAML_PATH_MISMATCH"}, r["errors"]
    assert not r.get("resolved")


def test_split_root_nested_in_another_split_is_blocked(validate, protocol):
    dirs = _split_dirs(protocol)
    nested = dirs["train"] / "val_inside_train"
    shutil.copytree(dirs["search_val"], nested)
    p = mutated(protocol, lambda x: x["console"]["paths"].update(search_val_root=str(nested)))
    r = validate(p).json()
    assert r["valid"] is False, "a split nested inside another split's root must never validate"
    assert codes_of(r) & {"SPLIT_OVERLAP", "DATASET_YAML_PATH_MISMATCH", "SPLIT_SAME_ROOT", "SPLIT_NESTED"}


def test_empty_split_and_class_map_mismatch(validate, protocol):
    for f in _split_dirs(protocol)["final_eval"].iterdir():
        f.unlink()
    expect_rejected(validate, protocol, "DATASET_EMPTY")


# -- run creation never accepts what validation refused ---------------------------------------------------------
def test_an_invalid_protocol_cannot_be_run_even_with_a_valid_looking_hash(client, protocol):
    good = client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth()).json()
    assert good["valid"]
    bad = mutated(good["resolved"], lambda p: p["rungs"][0].update(fitness_weights={"ap": 0.2, "recall": 0.1,
                                                                                    "latency_reward": 0.1, "size_reward": 0.03}))
    r = client.post("/api/v1/runs", headers=auth(), json={"protocol": bad, "protocol_hash": good["protocol_hash"],
                                                          "idempotency_key": "bad-weights", "validation_id": good["validation_id"]})
    assert r.status_code in (409, 422), r.text
    assert client.get("/api/v1/runs", headers=auth()).json()["runs"] == []
