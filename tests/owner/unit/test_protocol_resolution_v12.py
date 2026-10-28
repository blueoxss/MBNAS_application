"""Contract 1.2.0 resolution rules of protocol.validator: GA crossover_rate, benchmark.postprocess (latency operating
point), console.retention and the pinned final_retraining recipe. Synthetic tiny dataset (tmp dirs), CPU only."""
from __future__ import annotations

import copy
from typing import Any

import pytest

from mbnas_core.protocol.presets import default_protocol, pilot_smoke_protocol
from mbnas_contracts.constants import DEFAULT_POSTPROCESS
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_core.protocol.validator import validate


@pytest.fixture
def tiny_proto(make_dataset, tmp_roots) -> dict[str, Any]:
    paths = make_dataset()
    p = pilot_smoke_protocol()
    p["console"]["paths"] = {k: paths[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    p["console"]["paths"]["output_root"] = str(tmp_roots["runs"] / "out")
    return p


def _diff(result, field: str) -> dict[str, Any] | None:
    return next((d for d in result.resolved_diff if d["field"] == field), None)


def _codes(result) -> set[str]:
    return {e["code"] for e in result.errors}


def test_crossover_rate_defaults_to_half_with_a_defaulted_diff(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["search"].pop("crossover_rate")
    r = validate(tiny_proto, ctx_factory())
    assert r.valid, r.errors
    assert r.resolved["console"]["search"]["crossover_rate"] == 0.5
    assert _diff(r, "console.search.crossover_rate") == {"field": "console.search.crossover_rate", "requested": None,
                                                         "resolved": 0.5, "reason": "defaulted"}


def test_crossover_rate_requested_value_kept_and_hashed(tiny_proto, ctx_factory) -> None:
    a = validate(tiny_proto, ctx_factory())
    assert a.valid and a.resolved["console"]["search"]["crossover_rate"] == 1.0
    assert _diff(a, "console.search.crossover_rate") is None
    other = copy.deepcopy(tiny_proto)
    other["console"]["search"]["crossover_rate"] = 0.3
    b = validate(other, ctx_factory())
    assert b.valid and b.protocol_hash != a.protocol_hash  # part of the immutable protocol shared by both arms


@pytest.mark.parametrize("value", [-0.1, 1.5, "0.5", True, float("nan")])
def test_crossover_rate_out_of_range(tiny_proto, ctx_factory, value) -> None:
    tiny_proto["console"]["search"]["crossover_rate"] = value
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid and any(e["field"] == "console.search.crossover_rate" for e in r.errors)


def test_legacy_crossover_alias_is_read_with_a_warning(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["search"].pop("crossover_rate")
    tiny_proto["console"]["extra"] = {"ga": {"crossover_rate": 0.8}}
    r = validate(tiny_proto, ctx_factory())
    assert r.valid, r.errors
    assert r.resolved["console"]["search"]["crossover_rate"] == 0.8
    assert {w["code"] for w in r.warnings} >= {"LEGACY_ALIAS"}
    assert "legacy alias" in _diff(r, "console.search.crossover_rate")["reason"]
    again = validate(r.resolved, ctx_factory())  # fixed point: the resolved value now sits in console.search
    assert again.valid and again.protocol_hash == r.protocol_hash


def test_conflicting_legacy_alias_is_rejected(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["extra"] = {"ga": {"crossover_rate": 0.2}}  # search says 1.0
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid and "FIELD_INVALID" in _codes(r)


def test_postprocess_defaults_recorded_and_hashed(tiny_proto, ctx_factory) -> None:
    r = validate(tiny_proto, ctx_factory())
    assert r.valid, r.errors
    assert r.resolved["console"]["benchmark"]["postprocess"] == DEFAULT_POSTPROCESS == {"conf": 0.25, "iou": 0.7,
                                                                                         "max_det": 300}
    for key, value in DEFAULT_POSTPROCESS.items():
        assert _diff(r, f"console.benchmark.postprocess.{key}") == {
            "field": f"console.benchmark.postprocess.{key}", "requested": None, "resolved": value, "reason": "defaulted"}
    other = copy.deepcopy(tiny_proto)
    other["console"]["benchmark"]["postprocess"] = {"conf": 0.4}
    o = validate(other, ctx_factory())
    assert o.valid and o.resolved["console"]["benchmark"]["postprocess"] == {"conf": 0.4, "iou": 0.7, "max_det": 300}
    assert _diff(o, "console.benchmark.postprocess.conf") is None
    assert o.protocol_hash != r.protocol_hash  # the latency operating point is inside the protocol hash scope
    again = validate(r.resolved, ctx_factory())
    assert again.protocol_hash == r.protocol_hash and not any(
        d["field"].startswith("console.benchmark.postprocess") for d in again.resolved_diff)


@pytest.mark.parametrize("pp,field", [({"conf": 1.5}, "conf"), ({"iou": -0.1}, "iou"), ({"max_det": 0}, "max_det"),
                                      ({"max_det": 2.5}, "max_det"), ({"nms": True}, "nms")])
def test_postprocess_invalid_values(tiny_proto, ctx_factory, pp, field) -> None:
    tiny_proto["console"]["benchmark"]["postprocess"] = pp
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid and any(e["field"] == f"console.benchmark.postprocess.{field}" for e in r.errors)


def test_retention_preset_value_frozen_and_outside_the_hash(tiny_proto, ctx_factory) -> None:
    r = validate(tiny_proto, ctx_factory())
    assert r.valid and r.resolved["console"]["retention"] == {"full_state": "last_rung_end_only"}
    keep = copy.deepcopy(tiny_proto)
    keep["console"]["retention"] = {"full_state": "keep_all"}
    k = validate(keep, ctx_factory())
    assert k.valid and k.protocol_hash == r.protocol_hash  # housekeeping never splits a cohort
    assert "console.retention" in r.resolved["resolution"]["hash_excluded"]


def test_retention_defaults_from_the_server_setting(tiny_proto, ctx_factory, settings) -> None:
    tiny_proto["console"].pop("retention")
    r = validate(tiny_proto, ctx_factory())
    assert r.resolved["console"]["retention"] == {"full_state": "keep_all"}
    assert "MBNAS_FULL_STATE_RETENTION" in _diff(r, "console.retention.full_state")["reason"]
    s2 = settings.model_copy(update={"full_state_retention": "rung_end_only"})
    r2 = validate(tiny_proto, ctx_factory(settings=s2))
    assert r2.resolved["console"]["retention"] == {"full_state": "rung_end_only"}
    bad = copy.deepcopy(tiny_proto)
    bad["console"]["retention"] = {"full_state": "delete_everything"}
    assert "FIELD_INVALID" in _codes(validate(bad, ctx_factory()))


@pytest.mark.parametrize("final,code", [({"epoch_target_total": 0}, "EPOCHS_NOT_INT"),
                                        ({"epoch_target_total": 2.5}, "EPOCHS_NOT_INT"),
                                        ({"epoch_target_total": 4, "scheduler_horizon_epochs": 3}, "HORIZON_LT_LAST_RUNG"),
                                        ({"top_k_per_arm_per_seed": 0}, "FIELD_INVALID"),
                                        ({"budget_GPU_hours_separate": -1}, "BUDGET_INVALID")])
def test_final_recipe_checked(tiny_proto, ctx_factory, final, code) -> None:
    tiny_proto["final_retraining"].update(final)
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid and code in _codes(r)
    assert any(e["field"].startswith("final_retraining.") for e in r.errors)


def test_default_protocol_resolves_new_defaults_without_errors_from_them(ctx_factory) -> None:
    r = validate(default_protocol(), ctx_factory())
    fields = {e["field"] for e in r.errors}
    assert not any(f.startswith(("console.search.crossover_rate", "console.benchmark.postprocess",
                                 "console.retention", "final_retraining")) for f in fields), r.errors


def test_old_resolved_protocols_keep_their_hash_function(tiny_proto, ctx_factory) -> None:
    """protocol_hash() of a stored resolved document is unchanged by the new exclusion when it has no retention key."""
    r = validate(tiny_proto, ctx_factory())
    doc = copy.deepcopy(r.resolved)
    doc["console"].pop("retention")
    assert protocol_hash(doc) == r.protocol_hash
