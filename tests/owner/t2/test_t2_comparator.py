"""Sensitivity of the T2 comparator (scripts/owner/t2_compare.py ``compare_docs``) on synthetic documents.

Every allowed-difference rule must accept exactly the documented difference and nothing else: these cases prove that a
changed decision value (a float, a reason code, a list order, a missing key, an unexpected version, an architecture
hash whose normalised form differs, a timing value outside the timing-scoped scenarios) is reported as MISMATCH.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "owner" / "t2_compare.py"

pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason="scripts/owner/t2_compare.py not present (owner tree only)")


@pytest.fixture(scope="module")
def t2():
    spec = importlib.util.spec_from_file_location("t2_compare_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t2_compare_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


RAW_REF, RAW_NEW, NORM = "sha256:" + "1" * 64, "sha256:" + "2" * 64, "sha256:" + "9" * 64


def _doc(result: Any, hash_map: dict[str, str] | None = None) -> dict[str, Any]:
    return {"result": result, "meta": {"arch_hash_map": hash_map or {}, "work_root": "<WORK>",
                                       "snapshot_root": "<SNAPSHOT>", "repo_root": "<REPO>"}}


def _cmp(t2, ref: Any, new: Any, scenario: str = "T2-2", ref_map=None, new_map=None) -> dict[str, Any]:
    return t2.compare_docs(_doc(ref, ref_map), _doc(new, new_map), scenario)


BASE = {"fitness": 0.4390836350877193, "gate": {"reason_code": "PROMOTION_ELIGIBLE", "passed": True},
        "order": ["a", "b", "c"], "lineage": {"search_space": {"version": "mbnas-mixed-space/2", "space_hash": "x"}},
        "created_at": "2026-10-27T05:34:09.655418Z"}


def _with(path: list[Any], value: Any, base: dict[str, Any] = BASE) -> dict[str, Any]:
    out = copy.deepcopy(base)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def test_identical_is_exact(t2) -> None:
    assert _cmp(t2, BASE, copy.deepcopy(BASE))["verdict"] == "EXACT_MATCH"


@pytest.mark.parametrize("path,value", [
    (["fitness"], 0.43908363508771934),                       # 1-ulp-scale change of a decision value
    (["gate", "reason_code"], "REJECTED_BY_GATE"),
    (["gate", "passed"], False),
    (["order"], ["b", "a", "c"]),                              # order is part of the decision record
    (["order"], ["a", "b"]),                                   # length
    (["lineage", "search_space", "version"], "mbnas-mixed-space/4"),  # only the documented /2 -> /3 pair is allowed
    (["created_at"], None),                                    # a timestamp that disappears is not a timestamp diff
])
def test_decision_changes_are_mismatches(t2, path, value) -> None:
    res = _cmp(t2, BASE, _with(path, value))
    assert res["verdict"] == "MISMATCH", res


def test_missing_key_is_mismatch(t2) -> None:
    new = copy.deepcopy(BASE)
    del new["gate"]["passed"]
    assert _cmp(t2, BASE, new)["verdict"] == "MISMATCH"


def test_documented_differences_are_allowed_and_listed(t2) -> None:
    new = _with(["lineage", "search_space", "version"], "mbnas-mixed-space/3")
    new = _with(["lineage", "search_space", "space_hash"], "y", new)
    new = _with(["created_at"], "2026-10-27T05:34:10.228698Z", new)
    res = _cmp(t2, BASE, new)
    assert res["verdict"] == "ALLOWED_DIFF", res
    assert set(res["allowed_by_rule"]) == {"search_space_version", "search_space_hash", "timestamp"}
    assert {a["path"] for a in res["allowed"]} == {"lineage.search_space.version", "lineage.search_space.space_hash",
                                                   "created_at"}


def test_other_version_keys_stay_strict(t2) -> None:
    res = _cmp(t2, {"version": "mbnas-arch-sig/1"}, {"version": "mbnas-arch-sig/2"})
    assert res["verdict"] == "MISMATCH"


def test_architecture_hash_needs_equal_normalised_hash(t2) -> None:
    ok = _cmp(t2, {"architecture_hash": RAW_REF}, {"architecture_hash": RAW_NEW},
              ref_map={RAW_REF: NORM}, new_map={RAW_NEW: NORM})
    assert ok["verdict"] == "ALLOWED_DIFF" and "module_path:structural_hash" in ok["allowed_by_rule"]
    bad = _cmp(t2, {"architecture_hash": RAW_REF}, {"architecture_hash": RAW_NEW},
               ref_map={RAW_REF: NORM}, new_map={RAW_NEW: "sha256:" + "8" * 64})
    assert bad["verdict"] == "MISMATCH"
    unknown = _cmp(t2, {"architecture_hash": RAW_REF}, {"architecture_hash": RAW_NEW})
    assert unknown["verdict"] == "MISMATCH"


def test_module_path_strings_only_by_the_substitution_table(t2) -> None:
    ref = {"reference": "yolo_ga_runtime/mixed_blocks.py"}
    new = {"reference": "mbnas_model_runtime/operators/etri_mixed_blocks.py (vendored from "
                        "yolo_ga_runtime/mixed_blocks.py sha256 4233dd32…)"}
    assert _cmp(t2, ref, new)["verdict"] == "ALLOWED_DIFF"
    assert _cmp(t2, ref, {"reference": "mbnas_model_runtime/operators/other.py"})["verdict"] == "MISMATCH"


def test_timing_only_in_timing_scoped_scenarios(t2) -> None:
    ref, new = {"costs": [{"gpu_seconds": 0.012, "kind": "train"}]}, {"costs": [{"gpu_seconds": 0.013, "kind": "train"}]}
    assert _cmp(t2, ref, new, scenario="T2-7")["verdict"] == "ALLOWED_DIFF"
    assert _cmp(t2, ref, new, scenario="T2-6")["verdict"] == "MISMATCH"   # synthetic ledger: values are decisions
    changed_kind = {"costs": [{"gpu_seconds": 0.013, "kind": "retry"}]}
    assert _cmp(t2, ref, changed_kind, scenario="T2-7")["verdict"] == "MISMATCH"


def test_resolution_reason_allowed_only_for_the_search_space_entry(t2) -> None:
    def entry(field: str, reason: str) -> dict[str, Any]:
        return {"validation": {"resolved_diff": [{"field": field, "reason": reason}]}}

    field = "console.search.search_space_resolved"
    assert _cmp(t2, entry(field, "ETRI pool at X"), entry(field, "vendored pool"), "T2-7")["verdict"] == "ALLOWED_DIFF"
    other = "console.search.crossover_rate"
    assert _cmp(t2, entry(other, "defaulted"), entry(other, "legacy alias"), "T2-7")["verdict"] == "MISMATCH"


# -- DD-A53 (c): protocol_hash-derived differences only ---------------------------------------------------------------
PH_A, PH_B, PH_C, PH_D = ("sha256:" + c * 64 for c in "abcd")


def _engine(ph: str, ck_sha: str, *, realpath: bool, excluded: list[str]) -> dict[str, Any]:
    paths = {"output_root": {"requested": "<WORK>/out", **({"realpath": "<WORK>/out"} if realpath else {})}}
    return {"protocol_hash": ph, "db": {"run": {"protocol_hash": ph, "status": "COMPLETED"},
                                        "checkpoints": [{"hashes": {"protocol": ph}, "sha256": ck_sha, "epoch": 1}]},
            "validation": {"resolved": {"resolution": {"hash_excluded": excluded, "paths": paths}}}}


REF_EXCL = ["console.label", "resolution.validated_at", "console.retention"]


def test_protocol_hash_definition_change_is_allowed_only_as_a_consistent_mapping(t2) -> None:
    ref = _engine(PH_A, "1" * 64, realpath=True, excluded=REF_EXCL)
    new = _engine(PH_B, "2" * 64, realpath=False, excluded=REF_EXCL + ["resolution.paths"])
    res = _cmp(t2, ref, new, "T2-7")
    assert res["verdict"] == "ALLOWED_DIFF", res["mismatches"]
    assert {"protocol_hash_definition", "protocol_hash_embedding_file", "realpath_dropped",
            "hash_excluded_extended"} <= set(res["allowed_by_rule"])
    # outside the engine scenarios the rule does not exist
    assert _cmp(t2, ref, new, "T2-2")["verdict"] == "MISMATCH"


@pytest.mark.parametrize("label", ["two_new_for_one_ref", "file_digest_without_hash_change", "other_field",
                                   "realpath_changed_not_dropped", "hash_excluded_other_entry", "not_a_hash"])
def test_protocol_hash_rule_rejects_everything_else(t2, label: str) -> None:
    ref = _engine(PH_A, "1" * 64, realpath=True, excluded=REF_EXCL)
    new = _engine(PH_B, "2" * 64, realpath=False, excluded=REF_EXCL + ["resolution.paths"])
    if label == "two_new_for_one_ref":
        new["db"]["run"]["protocol_hash"] = PH_C  # the same ref hash maps to PH_B and PH_C
    elif label == "file_digest_without_hash_change":
        new["db"]["checkpoints"][0]["hashes"]["protocol"] = PH_A
        new["db"]["run"]["protocol_hash"] = PH_A
        new["protocol_hash"] = PH_A
    elif label == "other_field":
        new["db"]["run"]["status"] = "FAILED"
    elif label == "realpath_changed_not_dropped":
        new["validation"]["resolved"]["resolution"]["paths"]["output_root"]["realpath"] = "<WORK>/elsewhere"
    elif label == "hash_excluded_other_entry":
        new["validation"]["resolved"]["resolution"]["hash_excluded"] = REF_EXCL + ["console.paths"]
    elif label == "not_a_hash":
        new["protocol_hash"] = "changed"
    assert _cmp(t2, ref, new, "T2-7")["verdict"] == "MISMATCH", label
