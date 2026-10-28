"""schemas/* mirror contracts/openapi.json: every component exists, every contract-required field is a required
model field, enums equal the contract enums, and the conditional rules are enforced."""
from __future__ import annotations

import enum
import importlib.resources
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mbnas_contracts import schemas
from mbnas_contracts.constants import DEFAULT_BASELINE14
from mbnas_contracts.errors import ErrorLocation

CONTRACT = json.loads((importlib.resources.files("mbnas_contracts.openapi") / "openapi.json")
                      .read_text(encoding="utf-8"))  # package data of mbnas_contracts (DD-A10)
COMPONENTS: dict[str, Any] = CONTRACT["components"]["schemas"]


def _contract_required(name: str) -> tuple[set[str], set[str]]:
    comp = COMPONENTS[name]
    parts = comp.get("allOf", [comp])
    required, props = set(), set()
    for part in parts:
        if "$ref" in part:
            r, p = _contract_required(part["$ref"].rsplit("/", 1)[1])
            required |= r
            props |= p
        else:
            required |= set(part.get("required", []))
            props |= set(part.get("properties", {}))
    return required, props


def test_every_component_has_a_model() -> None:
    assert set(COMPONENTS) == set(schemas.CONTRACT_MODELS)


# 1.5.0 adds the first component that is not an object: RuntimeManifestStatus (a string enum, mirrored by a StrEnum)
NON_OBJECT_COMPONENTS = sorted(n for n, c in COMPONENTS.items() if "allOf" not in c and c.get("type", "object") != "object")
OBJECT_COMPONENTS = sorted(set(COMPONENTS) - set(NON_OBJECT_COMPONENTS))


def test_non_object_components_are_string_enums_mirrored_by_enum_classes() -> None:
    assert NON_OBJECT_COMPONENTS == ["RuntimeManifestStatus"]
    for name in NON_OBJECT_COMPONENTS:
        comp, mirror = COMPONENTS[name], schemas.CONTRACT_MODELS[name]
        assert comp["type"] == "string" and isinstance(mirror, type) and issubclass(mirror, enum.Enum), name
        assert [e.value for e in mirror] == comp["enum"], name
    assert COMPONENTS["Worker"]["properties"]["runtime_manifest_status"] == {"$ref": "#/components/schemas/RuntimeManifestStatus"}


@pytest.mark.parametrize("name", OBJECT_COMPONENTS)
def test_required_fields_exist_and_are_required(name: str) -> None:
    model = schemas.CONTRACT_MODELS[name]
    js = model.model_json_schema()
    required, props = _contract_required(name)
    assert required <= set(js["properties"]), f"{name} missing {required - set(js['properties'])}"
    assert required <= set(js.get("required", [])), f"{name} not required: {required - set(js.get('required', []))}"
    assert props <= set(js["properties"]), f"{name} lacks contract properties {props - set(js['properties'])}"


# 1.5.0 (WP-4): every projected public component is closed (Run, CandidateEvaluation and RegistryEntry were open up to
# 1.4.3); the user-authored Protocol stays open. tests/owner/projection/test_contract_150.py checks every component.
@pytest.mark.parametrize("name,closed", [("ValidateRequest", True), ("RunRequest", True), ("Run", True),
                                         ("CandidateEvaluation", True), ("Protocol", False),
                                         ("FinalRetrainRequest", True), ("RegistryEntry", True)])
def test_additional_properties_policy(name: str, closed: bool) -> None:
    assert COMPONENTS[name].get("additionalProperties") is (not closed)
    assert schemas.CONTRACT_MODELS[name].model_config.get("extra") == ("forbid" if closed else "allow")


def test_registry_entry_is_the_named_component_of_registry_lists() -> None:
    """Contract 1.2.0 promoted the RegistryList/Health item to the named component RegistryEntry (no field change)."""
    for container, prop in (("RegistryList", "registry"), ("Health", "registry")):
        assert COMPONENTS[container]["properties"][prop]["items"] == {"$ref": "#/components/schemas/RegistryEntry"}
    item = COMPONENTS["RegistryEntry"]
    js = schemas.RegistryEntry.model_json_schema()
    assert set(item["required"]) <= set(js["required"])
    assert set(item["properties"]["status"]["enum"]) == {s.value for s in schemas.RegistryStatus}


def test_lineage_operation_enum_equals_contract() -> None:
    lineage = COMPONENTS["CandidateDetail"]["properties"]["lineage"]["properties"]["operation"]
    assert {e.value for e in schemas.LineageOperation} == set(lineage["enum"])
    with pytest.raises(ValidationError):
        schemas.CandidateDetail.model_validate({"candidate_id": "c", "run_id": "r", "name": "n", "arm": "GA",
                                                "family": "f", "architecture_hash": "h", "blocks": [], "parents": [],
                                                "evaluations": [], "candidate_status": "EVALUATED",
                                                "lineage": {"operation": "teleport"}})


def test_artifact_matching_keys_follow_contract() -> None:
    props = COMPONENTS["Artifact"]["properties"]["matching"]["properties"]
    js = schemas.Artifact.model_json_schema()
    matching = js["$defs"]["ArtifactMatching"]["properties"]
    assert set(props) <= set(matching)
    assert set(props["counts"]["required"]) == {"tp", "fp", "fn", "gt"}
    ok = {"kind": "gt", "url": "/x", "matching": {"sample_id": "s", "split": "final_eval", "checkpoint_sha256": "a",
                                                  "counts": {"tp": 0, "fp": 1, "fn": 0, "gt": 0},
                                                  "sample_recall": None, "rule": "r"}}
    assert schemas.Artifact.model_validate(ok).matching.sample_recall is None  # N/A when gt == 0
    with pytest.raises(ValidationError):
        schemas.Artifact.model_validate({**ok, "matching": {"counts": {"tp": 1}}})


def test_final_retrain_request_and_run_created() -> None:
    req = schemas.FinalRetrainRequest.model_validate({"idempotency_key": "k", "budget_gpu_hours": 0.25})
    assert req.top_k_per_arm_per_seed is None
    for bad in ({"idempotency_key": "", "budget_gpu_hours": 1}, {"idempotency_key": "k", "budget_gpu_hours": 0},
                {"idempotency_key": "k", "budget_gpu_hours": 1, "top_k_per_arm_per_seed": 0},
                {"idempotency_key": "k", "budget_gpu_hours": 1, "extra": 1}, {"budget_gpu_hours": 1}):
        with pytest.raises(ValidationError):
            schemas.FinalRetrainRequest.model_validate(bad)
    created = schemas.RunCreated.model_validate({"run_id": "c", "status": "QUEUED", "parent_run_id": "p",
                                                 "finalist_count": 2})
    assert created.finalist_count == 2
    run = schemas.Run.model_validate({"id": "c", "status": "QUEUED", "protocol_hash": "sha256:x", "kind": "FINAL_RETRAIN",
                                      "parent_run_id": "p", "finalists": [
                                          {"candidate_id": "a", "source_evaluation_id": "a.L2.eval", "arm": "GA",
                                           "search_seed": 101, "rung": "L2", "ap": 0.3, "latency_ms": 4.0,
                                           "selection_rule": "rule"}]})
    assert run.kind == "FINAL_RETRAIN"
    with pytest.raises(ValidationError):
        schemas.Run.model_validate({"id": "c", "status": "QUEUED", "protocol_hash": "h", "kind": "OTHER"})
    with pytest.raises(ValidationError):  # selection_rule is required for every finalist
        schemas.Run.model_validate({"id": "c", "status": "QUEUED", "protocol_hash": "h", "finalists": [
            {"candidate_id": "a", "source_evaluation_id": "e", "arm": "GA", "rung": "L2"}]})


def _enum(component: str, prop: str) -> set[str]:
    p = COMPONENTS[component]["properties"][prop]
    if "enum" in p:
        return set(p["enum"])
    if "items" in p:
        return set(p["items"]["enum"])
    return {v for alt in p["anyOf"] for v in alt.get("enum", [])}


@pytest.mark.parametrize("enum_cls,component,prop", [
    (schemas.RunStatus, "Run", "status"), (schemas.CandidateStatus, "CandidateEvaluation", "status"),
    (schemas.CandidateStatus, "CandidateEvaluation", "candidate_status"), (schemas.Arm, "CandidateEvaluation", "arm"),
    (schemas.Precision, "CandidateEvaluation", "precision"),
    (schemas.LatencySource, "CandidateEvaluation", "latency_source"), (schemas.EventType, "Event", "type"),
    (schemas.ArtifactKind, "Artifact", "kind"), (schemas.ArtifactState, "Artifact", "state"),
    (schemas.AuthMode, "Artifact", "auth_mode"), (schemas.BlockSourceKind, "Block", "source_kind"),
    (schemas.ExecutionMode, "Run", "execution_mode"), (schemas.SearchArm, "Generation", "arm"),
    (schemas.RunKind, "Run", "kind"), (schemas.WorkerStatus, "Worker", "status"),
    # contract 1.5.0 (WP-4): training_status (DD-A23) and the public error location (DD-A20 D10)
    (schemas.TrainingStatus, "CandidateEvaluation", "training_status"),
    (schemas.TrainingStatus, "CandidateDetail", "training_status"),
    (schemas.TrainingStatus, "GeneratedCandidate", "training_status"),
    (ErrorLocation, "Error", "location"),
])
def test_enums_equal_contract(enum_cls, component: str, prop: str) -> None:
    assert {e.value for e in enum_cls} == _enum(component, prop)


def test_1_5_0_folds_repair_and_types_the_revision_kind() -> None:
    """DD-A19: a repaired block row reports the source it was repaired from, so ``repair`` left BlockSourceKind and
    the contract enum; ModelConfigRevision.kind is the closed set original/selected/executed/generated."""
    assert "repair" not in {e.value for e in schemas.BlockSourceKind} and "repair" not in _enum("Block", "source_kind")
    mirror = schemas.ModelConfigRevision.model_json_schema()["properties"]["kind"]["enum"]
    assert mirror == COMPONENTS["ModelConfigRevision"]["properties"]["kind"]["enum"] == [
        "original", "selected", "executed", "generated"]


def test_status_vocabulary_and_default_baselines() -> None:
    vocab = CONTRACT["x-status-vocabulary"]
    assert [s.value for s in schemas.RunStatus] == vocab["run"]
    assert [s.value for s in schemas.CandidateStatus] == vocab["candidate"]
    assert sorted(s.value for s in schemas.TERMINAL_RUN_STATUSES) == sorted(vocab["terminal_run"])
    assert sorted(s.value for s in schemas.POLICY_LOCKED_RUN_STATUSES) == sorted(vocab["nonterminal_run_policy_locked"])
    assert list(DEFAULT_BASELINE14) == CONTRACT["x-required-default-baselines"]


def test_canonicalize_status() -> None:
    assert schemas.canonicalize_status("CANCELED") == "CANCELLED"
    assert schemas.canonicalize_status("canceled") == "CANCELLED"
    assert schemas.canonicalize_status("EVALUATED") == "EVALUATED"
    with pytest.raises(ValueError):
        schemas.canonicalize_status("DONE")
    with pytest.raises(ValidationError):
        schemas.Run(id="r", status="CANCELED", protocol_hash="sha256:x")


def evaluation_row(**over: Any) -> dict[str, Any]:
    row = {"id": "ev1", "candidate_id": "c1", "name": "cand-1", "family": "GA-mixed", "arm": "GA", "generation": 0,
           "rung": "L0", "epochs": 2, "status": "EVALUATED", "ap": 0.1, "recall": 0.2, "latency_ms": 3.5,
           "size_mb": 6.0, "fitness": 0.5, "device": "host-gpu-0", "precision": "FP16", "latency_source": "measured",
           "parents": ["p1", "p2"], "blocks": [], "architecture_path": "/a.yaml", "checkpoint_path": "/c.pt",
           "artifacts": [], "objective_hash": "sha256:o", "evaluation_hash": "sha256:e", "split_hash": "sha256:s",
           "latency_scope": "forward_and_postprocessing", "comparison_group": "common_scratch",
           "protocol_hash": "sha256:p", "batch": 1, "resolved_input_shape": [640, 640], "search_seed": 101}
    row.update(over)
    return row


def test_evaluated_row_valid() -> None:
    assert schemas.CandidateEvaluation.model_validate(evaluation_row()).status == "EVALUATED"


@pytest.mark.parametrize("over", [{"ap": None}, {"fitness": None}, {"latency_source": "pending"},
                                  {"checkpoint_path": None}, {"protocol_hash": None}, {"search_seed": None},
                                  {"ap": 1.2}, {"latency_ms": 0}, {"size_mb": -1}, {"parents": ["a", "a"]},
                                  {"parents": ["a", "b", "c"]}, {"status": "CANCELED"}])
def test_evaluated_row_rules(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schemas.CandidateEvaluation.model_validate(evaluation_row(**over))


def test_provisional_row_allows_null_metrics() -> None:
    row = evaluation_row(status="TRAINING", ap=None, recall=None, latency_ms=None, size_mb=None, fitness=None,
                         latency_source="pending", checkpoint_path=None)
    assert schemas.CandidateEvaluation.model_validate(row).fitness is None


def gen_row(**over: Any) -> dict[str, Any]:
    row = {"arm": "GA", "generation": 0, "rung": "L0", "complete": True, "representative_candidate_id": "c1",
           "comparison_group": "common_scratch", "search_seed": 101, "planning_closed": True, "planned_count": 4,
           "evaluated_count": 2, "failed_count": 1, "not_admitted_count": 1, "pending_count": 0}
    row.update(over)
    return row


def test_generation_rules() -> None:
    schemas.Generation.model_validate(gen_row())
    for over in ({"pending_count": 1, "evaluated_count": 1}, {"planning_closed": False}, {"evaluated_count": 3},
                 {"search_seed": None}):
        with pytest.raises(ValidationError):
            schemas.Generation.model_validate(gen_row(**over))
    schemas.Generation.model_validate(gen_row(complete=False, planning_closed=False, pending_count=1, evaluated_count=1))


def test_event_payload_typed_by_type() -> None:
    schemas.Event.model_validate({"seq": 1, "type": "candidate.evaluated", "run_id": "r", "payload": evaluation_row()})
    schemas.Event.model_validate({"seq": 2, "type": "log", "run_id": "r", "payload": {"message": "x", "level": "info"}})
    for payload in ({"msg": "x"}, {"message": "x", "path": "/abs/ckpt.pt"}):  # 1.5.0: log payload key allowlist
        with pytest.raises(ValidationError):
            schemas.Event.model_validate({"seq": 2, "type": "log", "run_id": "r", "payload": payload})
    with pytest.raises(ValidationError):
        schemas.Event.model_validate({"seq": 3, "type": "candidate.evaluated", "run_id": "r", "payload": {"id": "x"}})
    with pytest.raises(ValidationError):
        schemas.Event.model_validate({"seq": 0, "type": "log", "run_id": "r", "payload": {}})


def test_closed_requests_reject_unknown_fields() -> None:
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    proto = pilot_smoke_protocol()
    schemas.RunRequest.model_validate({"protocol": proto, "protocol_hash": "sha256:x", "idempotency_key": "k"})
    with pytest.raises(ValidationError):
        schemas.RunRequest.model_validate({"protocol": proto, "protocol_hash": "h", "idempotency_key": "k", "x": 1})
    with pytest.raises(ValidationError):
        schemas.ValidateRequest.model_validate({"protocol": proto, "extra": True})


def test_presets_validate_as_contract_protocol() -> None:
    from mbnas_core.protocol.presets import presets

    listing = schemas.PresetList.model_validate({"presets": presets()})
    assert [p.id for p in listing.presets] == ["DEFAULT_BASELINE14", "PILOT_SMOKE"]


# ---------------------------------------------------------------- contract 1.4.0 (integration round 1) ----------------------------
def _contract_ok(name: str, value: Any) -> None:
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_contracts.openapi import prune

    assert contract_errors(name, value) == [], contract_errors(name, value)
    assert prune(name, value) == value, f"prune() dropped a null the {name} contract does not admit"


TRAINING_BODIES = [
    ({"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": {}, "idempotency_key": "k"}, True),
    ({"source": {"kind": "search", "run_id": "r", "candidate_id": "c"}, "protocol": {"a": 1}, "mode": "resume",
      "checkpoint": "/x.pt", "validation_enabled": False, "idempotency_key": "k" * 256}, True),
    ({"source": {"kind": "user", "model_path": "/m.yaml"}, "protocol": {}, "mode": "evaluate_only", "idempotency_key": "k"}, True),
    ({"source": {"kind": "baseline"}, "protocol": {}, "idempotency_key": "k"}, False),          # needs baseline_id
    ({"source": {"kind": "generated", "run_id": "r"}, "protocol": {}, "idempotency_key": "k"}, False),
    ({"source": {"kind": "other", "baseline_id": "b"}, "protocol": {}, "idempotency_key": "k"}, False),
    ({"source": {"kind": "baseline", "baseline_id": "b"}, "protocol": {}, "idempotency_key": ""}, False),
    ({"source": {"kind": "baseline", "baseline_id": "b"}, "protocol": {}, "idempotency_key": "k" * 257}, False),
    ({"source": {"kind": "baseline", "baseline_id": "b"}, "protocol": {}, "mode": "fast", "idempotency_key": "k"}, False),
    ({"source": {"kind": "baseline", "baseline_id": "b"}, "protocol": {}, "idempotency_key": "k", "extra": 1}, False),
    ({"source": {"kind": "baseline", "baseline_id": "b"}, "idempotency_key": "k"}, False),       # protocol missing
]


@pytest.mark.parametrize("body,accepted", TRAINING_BODIES)
def test_training_request_mirror_matches_route_model_and_contract(body: dict[str, Any], accepted: bool) -> None:
    """schemas.TrainingRequest, the route's training.selection.TrainingRunRequest and the contract component agree."""
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_core.training_runs.selection import TrainingRunRequest

    def verdict(model: Any) -> bool:
        try:
            model.model_validate(body)
            return True
        except ValidationError:
            return False

    assert verdict(schemas.TrainingRequest) is accepted
    assert verdict(TrainingRunRequest) is accepted
    assert (contract_errors("TrainingRequest", body) == []) is accepted


def test_training_request_mirror_has_the_route_model_schema() -> None:
    from mbnas_core.training_runs.selection import TrainingRunRequest

    # Contract 1.5.0 declares the upload fields (upload_id, model_yaml_text, source_sha256, model_name) on the public
    # TrainingRequest.source. The browser sends upload_id; the gateway replaces it with model_yaml_text + source_sha256
    # (+ model_name) before relaying, so the CORE route model has every public field except upload_id. The delta is
    # pinned exactly, so any other drift between the public mirror and the route model still fails.
    gateway_only = {"upload_id"}

    def shape(model: Any, drop: set[str] = frozenset()) -> Any:
        js = model.model_json_schema()
        src = js["$defs"]["TrainingSource"]
        return (sorted(js["required"]), sorted(js["properties"]), js["properties"]["idempotency_key"],
                js["properties"]["mode"]["enum"], sorted(set(src["properties"]) - drop), src["properties"]["kind"]["enum"],
                js["additionalProperties"])

    route_src = set(TrainingRunRequest.model_json_schema()["$defs"]["TrainingSource"]["properties"])
    mirror_src = set(schemas.TrainingRequest.model_json_schema()["$defs"]["TrainingSource"]["properties"])
    assert mirror_src - route_src == gateway_only and not route_src - mirror_src, (route_src, mirror_src)
    assert shape(schemas.TrainingRequest, gateway_only) == shape(TrainingRunRequest)
    assert set(COMPONENTS["TrainingRequest"]["properties"]["source"]["properties"]) == mirror_src  # 1:1 with the contract
    comp = COMPONENTS["TrainingRequest"]
    assert comp["properties"]["idempotency_key"] == {"maxLength": 256, "minLength": 1, "type": "string"}
    assert comp["additionalProperties"] is False and set(comp["required"]) == {"source", "protocol", "idempotency_key"}


def test_training_run_created_and_post_training_runs_wiring() -> None:
    body = {"run_id": "r", "status": "QUEUED", "protocol_hash": "sha256:p", "created": True, "candidate_id": "c",
            "source": {"kind": "baseline", "baseline_id": "yolo11n"}, "mode": "scratch"}
    _contract_ok("TrainingRunCreated", body)
    schemas.TrainingRunCreated.model_validate(body)
    for missing in body:
        with pytest.raises(ValidationError):
            schemas.TrainingRunCreated.model_validate({k: v for k, v in body.items() if k != missing})
    post = CONTRACT["paths"]["/api/v1/training-runs"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/TrainingRequest"}
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/TrainingRunCreated"}


def test_candidate_detail_evaluation_status_and_structure_are_typed() -> None:
    props = COMPONENTS["CandidateDetail"]["properties"]
    assert props["evaluation_status"]["enum"] == ["EVALUATED", "NOT_EVALUATED"]
    assert props["structure"] == {"$ref": "#/components/schemas/StructureReport"}
    base = {"candidate_id": "c", "run_id": "r", "name": "n", "arm": "GA", "family": "f", "architecture_hash": "h",
            "blocks": [], "parents": [], "evaluations": [], "candidate_status": "EVALUATED"}
    for status in ("EVALUATED", "NOT_EVALUATED"):
        schemas.CandidateDetail.model_validate({**base, "evaluation_status": status})
    with pytest.raises(ValidationError):
        schemas.CandidateDetail.model_validate({**base, "evaluation_status": "PENDING"})
    with pytest.raises(ValidationError):  # a structure without schema/status is not a StructureReport
        schemas.CandidateDetail.model_validate({**base, "structure": {"blocks": []}})


def test_duplicate_scope_is_resolved_only_per_seed_and_generated_candidates_carry_it() -> None:
    search = COMPONENTS["Protocol"]["properties"]["console"]["properties"]["search"]["properties"]["duplicate_scope"]
    assert search["enum"] == ["per_seed"] and "Resolved-only" in search["description"]
    gen = COMPONENTS["GeneratedCandidate"]["properties"]
    assert gen["duplicate_scope"]["enum"] == ["per_seed", None] and "execution" in gen
    generated = {"id": "i", "candidate_id": "c", "run_id": "r", "name": "n", "family": "f", "evaluation_status": "NOT_EVALUATED",
                 "structure_status": "PASS", "duplicate_scope": "per_seed", "execution": "CPU", "training_status": "UNTRAINED"}
    schemas.GeneratedCandidate.model_validate(generated)
    with pytest.raises(ValidationError):
        schemas.GeneratedCandidate.model_validate({**generated, "duplicate_scope": "global"})
    with pytest.raises(ValidationError):  # 1.5.0: training_status is required on a generated candidate (DD-A23)
        schemas.GeneratedCandidate.model_validate({k: v for k, v in generated.items() if k != "training_status"})
    assert "training_status" in COMPONENTS["GeneratedCandidate"]["required"]
    assert COMPONENTS["CandidateEvaluation"]["properties"]["epoch_target_total"]["minimum"] == 1  # DD-35b: never 0
    assert schemas.CandidateEvaluation.model_json_schema()["properties"]["epoch_target_total"]["anyOf"][0]["minimum"] == 1


def test_structure_report_accepts_documents_of_every_shape_without_pruning_nulls() -> None:
    v1 = {"schema": "mbnas-structure/1", "status": "UNVERIFIED", "blocks": [{"id": "block-0000", "name": "Conv"}],
          "edges": [{"source": -1, "target": 0, "from_ref": -1}], "diagnostics": [], "checks": [{"name": "yaml_rows", "status": "NOT_CHECKED"}]}
    _contract_ok("StructureReport", v1)
    declared = {"schema": "mbnas-structure/2", "status": "UNVERIFIED", "backend": "rfdetr", "representation": "native_modules",
                "observation": None, "flops": None, "flops_status": "NOT_MEASURED", "flops_source": None, "lineage": None,
                "lineage_status": "NOT_APPLICABLE", "edges_unobserved": [{"module_path": "transformer.decoder", "reason": "inside decoder"}],
                "blocks": [{"id": "block-0000", "block_id": "block-0000", "stable_id": "rf:backbone@abcdef012345", "gene_index": None,
                            "yaml_path": None, "from": None, "from_resolved": None, "repeats": None, "args": None,
                            "module": None, "module_class": "Dinov2", "wrapper": None, "wrapped_module": None,
                            "connection_source": "native_fixed", "repeats_detail": {"declared": 12, "scaled": None, "observed": 12},
                            "effective": {"c_in": None, "inputs": [], "outputs": [[3, 4]], "repeats": None},
                            "internal_layers": [{"path": "0.attn", "module": "Linear", "parameters": [], "attributes": {}, "shared": False}]}],
                "edges": [{"source": 0, "target": 1, "from_ref": 0, "connection_source": "observed"}],
                "checks": [{"name": "yaml_rows", "status": "NOT_CHECKED", "evidence_level": None}],
                "diagnostics": [{"code": "GENE_REPRESENTATION_NOT_APPLICABLE", "position": None, "path": None, "cause": "c",
                                 "suggestion": "s"}]}
    _contract_ok("StructureReport", declared)
    for doc in (v1, declared):
        schemas.StructureReport.model_validate(doc)
    registry = {**declared, "model_id": "rfdetr-nano", "evidence_source": "declared_configuration"}
    from mbnas_server.api.contract_check import contract_errors

    sch = CONTRACT["paths"]["/api/v1/registry/{model_id}/structure"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert [part.get("$ref") for part in sch["allOf"]][0] == "#/components/schemas/StructureReport"
    assert contract_errors("StructureReport", registry) == []
    for bad in ({"status": "PASS"}, {"schema": "mbnas-structure/2"}, {"schema": "x", "status": "NOT_CHECKED"}):
        with pytest.raises(ValidationError):
            schemas.StructureReport.model_validate(bad)
        assert contract_errors("StructureReport", bad)


def test_structure_report_contract_accepts_real_cpu_documents() -> None:
    """Real mbnas-structure/2 documents from the CPU checker and the candidate view (PASS, FAIL, declaration-only, saved)
    satisfy the contract unchanged: no null is pruned, nothing is rejected."""
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    import yaml

    from mbnas_core.console.model_services import describe_candidate
    from mbnas_model_runtime.genotype import Genotype
    from mbnas_core.search.structure import check_structure

    cfg = {"nc": 2, "scales": {"n": [0.33, 0.25, 1024]}, "scale": "n",
           "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]], [-1, 1, "C2f", [32, True]],
                        [-1, 1, "Conv", [64, 3, 2]], [-1, 1, "C2f", [64, True]], [-1, 1, "Conv", [128, 3, 2]],
                        [-1, 1, "SPPF", [128, 5]]],
           "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1, 4], 1, "Concat", [1]], [-1, 1, "C2f", [64]],
                    [[9, 2], 1, "Detect", ["nc"]]]}  # Detect fed by two inconsistent strides -> FAIL
    ok_cfg = {**cfg, "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1, 4], 1, "Concat", [1]], [-1, 1, "C2f", [64]],
                              [[9, 6], 1, "Detect", ["nc"]]]}
    docs = {}
    for label, conf in (("fail", cfg), ("pass", ok_cfg)):
        genotype = Genotype.from_yaml_dict(conf, family="contract-test", template_id="contract-test")
        docs[label] = check_structure(genotype, input_size=64, class_count=2)
    candidate = {"id": "c1", "name": "c", "family": "contract-test", "arm": "IndependentRandom", "parents": [],
                 "architecture_yaml": yaml.safe_dump(ok_cfg), "lineage": None, "blocks": [], "architecture_hash": None, "audit": {}}
    run = {"id": "r1", "resolved_protocol": {"training": {"requested_input_size": [64, 64]}}}
    docs["declared"] = describe_candidate(candidate, run)
    docs["saved"] = describe_candidate({**candidate, "audit": {"structure": docs["pass"]}}, run)
    assert {k: v["status"] for k, v in docs.items()} == {"fail": "FAIL", "pass": "PASS", "declared": "UNVERIFIED", "saved": "PASS"}
    assert docs["declared"]["evidence_source"] == "declared_configuration" and docs["saved"]["evidence_source"] == "saved_structure_check"
    for label, doc in docs.items():
        _contract_ok("StructureReport", doc)
        schemas.StructureReport.model_validate(doc)


def test_new_contract_paths_are_bearer_protected_and_reference_the_shared_components() -> None:
    paths = CONTRACT["paths"]
    assert CONTRACT["security"] == [{"BearerAuth": []}]
    for path, method in (("/api/v1/registry/{model_id}/structure", "get"), ("/api/v1/registry/{model_id}/structure-check", "post"),
                         ("/api/v1/structure-check", "post")):
        op = paths[path][method]
        assert "security" not in op and "200" in op["responses"] and "401" in op["responses"] and "403" in op["responses"], path
    assert paths["/api/v1/structure-check"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/StructureReport"}
    body = paths["/api/v1/structure-check"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    # 1.5.0: upload_id (browser) / model_yaml_text + source_sha256 (gateway to core) / legacy model_path; no member is
    # required by the schema any more (exactly one source is the server's rule), the body stays closed
    assert "required" not in body and body["additionalProperties"] is False
    assert set(body["properties"]) == {"upload_id", "model_yaml_text", "source_sha256", "model_name", "model_path",
                                       "input_size", "class_count"}
    assert "404" in paths["/api/v1/registry/{model_id}/structure"]["get"]["responses"]


def test_archive_compare_run_conditions_are_optional_and_all_nullable() -> None:
    run = COMPONENTS["ArchiveCompareRun"]
    assert "conditions" in run["properties"] and "conditions" not in run["required"]
    cond = COMPONENTS["ArchiveCompareConditions"]
    keys = {"dataset", "split", "stage", "epochs_total", "protocol_hash", "device", "precision", "checkpoint"}
    assert set(cond["properties"]) == keys == set(cond["required"])
    assert all({"type": "null"} in cond["properties"][k]["anyOf"] for k in keys)
    from mbnas_contracts.schemas.archive import ArchiveCompareConditions

    ArchiveCompareConditions.model_validate({k: None for k in keys})
    with pytest.raises(ValidationError):
        ArchiveCompareConditions.model_validate({"dataset": None})


# 1.4.0/1.4.1 codes plus the five POST /training-runs 422 selection codes that contract 1.4.3 names (S05 fix round 1)
ERROR_CODES_ADDED_143 = {"FINAL_RETRAIN_NOT_APPLICABLE": 409, "EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED": 409, "DEVICE_UNSUPPORTED": 422,
                         "EVAL_ONLY_UNSUPPORTED": 422, "VALIDATION_DISABLED_UNSUPPORTED": 422, "INITIAL_WEIGHTS_REQUIRED": 422,
                         "RESUME_SOURCE_REQUIRED": 422}
# contract 1.5.0 (WP-4): gateway relay (DD-A44 d), upload (F5a), artifact store (DD-A39) and stored-byte codes
ERROR_CODES_ADDED_150 = {"ARTIFACT_NOT_READY": 404, "CORE_AUTH_FAILED": 502, "CORE_BAD_RESPONSE": 502, "CORE_UNAVAILABLE": 503,
                         "LOAD_FAILED": 409, "PAYLOAD_TOO_LARGE": 413, "RATE_LIMITED": 429, "SOURCE_SHA256_INVALID": 422,
                         "SOURCE_SHA256_MISMATCH": 422, "STORE_UNAVAILABLE": 507, "UPLOAD_CORRUPT": 409, "UPLOAD_EMPTY": 422,
                         "UPLOAD_ID_INVALID": 422, "UPLOAD_INVALID": 422, "UPLOAD_NOT_FOUND": 404, "UPLOAD_NOT_UTF8": 422,
                         "UPLOAD_TYPE_UNSUPPORTED": 422}
ERROR_CODES_ADDED = {**ERROR_CODES_ADDED_143, **ERROR_CODES_ADDED_150}


def test_status_vocabulary_lists_the_1_4_0_codes() -> None:
    vocab = CONTRACT["x-status-vocabulary"]
    assert {"GENERATION_EMPTY", "GENERATION_PARTIAL"} <= set(vocab["run_reason_codes_added"])
    assert "ARCHITECTURE_HASH_MISMATCH" in vocab["evaluation_row_reason_codes_added"]
    assert {"GENE_REPRESENTATION_NOT_APPLICABLE", "MERGE_INPUT_MISMATCH", "HEAD_INPUT_INVALID"} <= set(vocab["structure_diagnostic_codes_added"])
    assert {"STRUCTURE_FAILED", "STRUCTURE_UNVERIFIED"} == set(vocab["candidate_audit_structure_reason_codes_added"])
    assert vocab["error_codes_added"] == ERROR_CODES_ADDED
    assert vocab["validator_capabilities_generation_structure_status"] == ["UNVERIFIED"]
    assert "NOT_CHECKED" not in vocab["validator_capabilities_generation_structure_status"]


# ---------------------------------------------------------------- contract 1.4.1 (integration round 2) ----------------------------
def _native_1_4_1_document() -> dict[str, Any]:
    """Hand-built native document that carries every member 1.4.1 documents (shapes taken from real RF-DETR / ETRI documents)."""
    block = {"id": "block-0001", "block_id": "block-0001", "stable_id": "rf:backbone.0.projector@abcdef012345",
             "gene_index": None, "yaml_path": None, "from": None, "from_resolved": None, "repeats": None, "args": None,
             "module": None, "module_class": "MultiScaleProjector", "wrapper": None, "wrapped_module": None,
             "native_path": "backbone.0.projector", "native_kind": "module", "native_config": {"projector_scale": ["P4"]},
             "note": None, "connection_source": "observed",
             "repeats_detail": {"declared": 1, "scaled": None, "observed": 1, "declared_basis": "len(projector_scale)",
                                "observed_basis": "len(projector.stages)", "group": None, "unit_index": None},
             "effective": {"c_in": 384, "repeats": 1, "repeats_source": None, "calls": 1, "outputs": [[256, 24, 24]],
                           "output_shapes": [[1, 256, 24, 24]], "tokens_out": 576,
                           "inputs": [{"source": -1, "source_path": "model_input", "name": "arg0", "shape": [1, 3, 384, 384],
                                       "c": 3, "h": 384, "w": 384}],
                           "observed_children": [{"path": "q.0", "module": "Linear", "calls": 0, "inputs": None, "outputs": None}],
                           "buffers": [{"name": "refpoint_embed", "shape": [3900, 4]}]}}
    return {"schema": "mbnas-structure/2", "status": "PASS", "backend": "rfdetr", "representation": "native_modules",
            "fixed_resolution": 384, "native_config": {"encoder": "dinov2_windowed_small"}, "evidence_source": "executed_structure_check",
            "observed_scopes": [{"module_path": "backbone", "module_class": "Joiner", "calls": 1, "inputs": [[1, 3, 384, 384]],
                                 "outputs": [[1, 256, 24, 24]]}],
            "repeat_groups": [{"group": "transformer.decoder.layers", "declared": 2, "declared_basis": "dec_layers", "observed": 2,
                               "blocks": ["block-0010", "block-0011"]}],
            "parameters_unattributed": 0, "parameter_free_modules_not_listed": ["backbone.0.drop"],
            "runtime_requirements": {"python": ">=3.10", "packages": ["rfdetr==1.11.2"]}, "scale_source": None,
            "observation": {"method": "forward_hook", "device": "cpu", "imgsz": 384, "fx_traceable": False,
                            "fx_basis": "torch.fx is not used", "source_blob_sha1": "0123456789abcdef", "report_origin": "executed"},
            "blocks": [block],
            "edges": [{"source": 0, "target": 1, "from_ref": None, "connection_source": "observed",
                       "evidence": {"kind": "tensor_identity", "tensors": ["arg0"], "shape": [1, 384, 24, 24]}},
                      {"source": 1, "target": 2, "from_ref": None, "connection_source": "native_fixed",
                       "evidence": {"kind": "code_fixed+shape", "citation": "transformer.py:120", "corroboration": "shapes agree",
                                    "corroborated": True}}],
            "edges_unobserved": [], "diagnostics": [], "checks": []}


def test_structure_report_1_4_1_native_members_are_typed_and_wrong_types_are_rejected() -> None:
    import copy

    from mbnas_server.api.contract_check import contract_errors

    doc = _native_1_4_1_document()
    _contract_ok("StructureReport", doc)
    schemas.StructureReport.model_validate(doc)
    props = COMPONENTS["StructureReport"]["properties"]
    assert {"fixed_resolution", "native_config", "observed_scopes", "repeat_groups", "parameters_unattributed",
            "parameter_free_modules_not_listed", "runtime_requirements", "scale_source"} <= set(props)
    assert {"fx_basis", "source_blob_sha1"} <= set(props["observation"]["properties"]) and "fx_error" not in props["observation"]["properties"]
    assert "evidence" in props["edges"]["items"]["properties"]
    wrong = [("fixed_resolution", "384"), ("native_config", []), ("observed_scopes", {}), ("repeat_groups", "x"),
             ("parameters_unattributed", 1.5), ("parameter_free_modules_not_listed", [1]), ("runtime_requirements", "py"),
             ("scale_source", "guessed")]
    for key, value in wrong:
        bad = {**doc, key: value}
        assert contract_errors("StructureReport", bad), key
    for mutate in (lambda d: d["observation"].update(fx_basis=3), lambda d: d["edges"][0].update(evidence="x"),
                   lambda d: d["edges"][0]["evidence"].update(corroborated="yes"),
                   lambda d: d["blocks"][0].update(native_kind="tensor"), lambda d: d["blocks"][0].update(note=1),
                   lambda d: d["blocks"][0]["repeats_detail"].update(unit_index="0"),
                   lambda d: d["blocks"][0]["effective"].update(calls="1"),
                   lambda d: d["blocks"][0]["effective"]["inputs"][0].update(source="backbone"),
                   lambda d: d["blocks"][0]["effective"]["inputs"][0].update(source_path=0),
                   lambda d: d["blocks"][0]["effective"]["buffers"][0].update(shape=3900)):
        bad = copy.deepcopy(doc)
        mutate(bad)
        assert contract_errors("StructureReport", bad)
    for key in ("scale_source", "native_kind"):  # the two new closed value sets
        node = props["scale_source"] if key == "scale_source" else props["blocks"]["items"]["properties"]["native_kind"]
        assert None in node["enum"] and "enum" in node
    assert set(props["scale_source"]["enum"]) - {None} == {"yaml", "file_name", "file_name_suffix", "first_scale_key"}


def test_structure_report_1_4_1_keeps_repeats_source_closed_and_evidence_source_free() -> None:
    """R-S2-1 and R-S2-4 were not applied: no new repeats_source value (native counts are null + observed_basis) and no
    evidence_source enum (a web fixture and older stored documents carry other strings)."""
    props = COMPONENTS["StructureReport"]["properties"]
    eff = props["blocks"]["items"]["properties"]["effective"]["properties"]
    assert eff["repeats_source"]["enum"] == [".m", ".blocks", "sequential_wrapper", "sequential", "single_module", "unobserved", None]
    assert "enum" not in props["evidence_source"] and props["evidence_source"]["type"] == ["string", "null"]
    for known in ("saved_structure_check", "declared_configuration", "executed_structure_check"):
        assert known in props["evidence_source"]["description"]
    assert "observed_basis" in eff["repeats_source"]["description"]


def test_structure_routes_document_the_statuses_the_server_answers() -> None:
    paths = CONTRACT["paths"]
    chk = paths["/api/v1/registry/{model_id}/structure-check"]["post"]
    assert "200 for PASS, FAIL and UNVERIFIED" in chk["description"] and "never overwrite" in chk["description"]
    assert "VALIDATION_FAILED" in chk["responses"]["422"]["description"] and "INPUT_SIZE_INVALID" in chk["responses"]["422"]["description"]
    usr = paths["/api/v1/structure-check"]["post"]["responses"]["422"]["description"]
    for code in ("PATH_NOT_ABSOLUTE", "PATH_TRAVERSAL", "PATH_OUTSIDE_ALLOWLIST", "PATH_NOT_FOUND", "MODEL_CONFIG_INVALID",
                 "INPUT_SIZE_INVALID", "VALIDATION_FAILED"):
        assert code in usr
    # 1.5.0: the upload_id path adds 404 (UPLOAD_NOT_FOUND), 409 (UPLOAD_CORRUPT) and 507 (STORE_UNAVAILABLE); nothing else
    assert set(paths["/api/v1/structure-check"]["post"]["responses"]) == {"200", "401", "403", "404", "409", "422", "500", "507"}


def test_registry_list_documents_source_warning_and_drops_the_dd26_diagnostic() -> None:
    """1.5.0 (DTO allowlist OWNER_ONLY, DD-A19): the DD-26 host diagnostic registry_roots_skipped (owner-host paths) left
    the public RegistryList (closed) and is denied inside RegistryEntry.evidence; source is a label, never a path."""
    props = COMPONENTS["RegistryList"]["properties"]
    assert set(props) == {"registry", "source", "warning"} and COMPONENTS["RegistryList"]["required"] == ["registry"]
    assert COMPONENTS["RegistryList"]["additionalProperties"] is False
    _contract_ok("RegistryList", {"registry": [], "source": "live", "warning": None})
    from mbnas_server.api.contract_check import contract_errors

    assert contract_errors("RegistryList", {"registry": [], "registry_roots_skipped": [{"path": "/r", "reason": "x"}]})
    assert "registry_roots_skipped" in COMPONENTS["RegistryEntry"]["properties"]["evidence"]["propertyNames"]["not"]["enum"]
    assert "registry_roots_skipped" not in schemas.RegistryList.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        schemas.RegistryList.model_validate({"registry": [], "registry_roots_skipped": []})


def test_dd36_wording_for_resume_checkpoint_codes() -> None:
    """Round 2 reword only: no new code, no new field; the sent-path rule of DD-36 is what the text says."""
    req = COMPONENTS["TrainingRequest"]
    text = req["properties"]["checkpoint"]["description"]
    assert "EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED" in text and "RESUME_CHECKPOINT_MISMATCH" in text
    assert "Omitted or null" in text and "ordinary run Resume path" in text
    assert "a candidate without a recorded training_full state is refused" not in req["description"]  # the 1.4.0 wording
    assert "without checkpoint takes the ordinary run Resume path" in req["description"]
    r409 = CONTRACT["paths"]["/api/v1/training-runs"]["post"]["responses"]["409"]["description"]
    assert "checkpoint path was sent and the candidate has no recorded training_full" in r409
    vocab = CONTRACT["x-status-vocabulary"]
    assert vocab["error_codes_added"] == ERROR_CODES_ADDED
    assert "a request without checkpoint" in vocab["error_codes_note"] and "RESUME_CHECKPOINT_MISMATCH" in vocab["error_codes_note"]
    assert set(req["properties"]) == set(schemas.TrainingRequest.model_json_schema()["properties"])


def test_status_vocabulary_lists_the_1_4_1_diagnostic_codes() -> None:
    codes = CONTRACT["x-status-vocabulary"]["structure_diagnostic_codes_added"]
    assert codes == ["GENE_REPRESENTATION_NOT_APPLICABLE", "MERGE_INPUT_MISMATCH", "HEAD_INPUT_INVALID",
                     "CLASS_COUNT_UNKNOWN", "STRUCTURE_CHECK_FAILED"]
    described = COMPONENTS["StructureReport"]["properties"]["diagnostics"]["items"]["properties"]["code"]["description"]
    assert all(code in described for code in codes[3:])


# ---------------------------------------------------------------- contract 1.4.2 (integration round 3) ----------------------------
HISTORY_DIR = Path(__file__).resolve().parents[3] / "docs" / "public" / "contracts" / "history"  # DD-A10
NEW_142_COMPONENTS = {"BlockCatalogEntry", "BlockCatalogTable", "BlockCatalog", "BlockCatalogSummary", "ArchiveCampaignCandidates",
                      "ArchiveCampaignProgress", "ArchiveCampaignBestTerminal", "ArchiveCampaignResult"}
CAMPAIGN_KEYS = {"campaign_id", "campaign_arm", "space", "method", "seed", "pair", "pair_done", "queue_state", "campaign_arms_total",
                 "arm_status", "progress", "result"}
NEW_142_MEMBERS = {"StructureReport": {"catalog_summary"}, "ArchiveRoot": {"kind"}, "ArchiveRunSummary": CAMPAIGN_KEYS,
                   "ArchiveRun": CAMPAIGN_KEYS}


def _without_descriptions(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _without_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_without_descriptions(v) for v in node]
    return node


def test_1_4_2_is_additive_over_1_4_1_and_changes_wording_only_where_it_says_so() -> None:
    """Round 3 adds one path, eight components and optional members; every other schema equals 1.4.1 apart from description texts."""
    old = json.loads((HISTORY_DIR / "openapi-1.4.1.json").read_text())
    new = json.loads((HISTORY_DIR / "openapi-1.4.2.json").read_text())
    assert new["info"]["version"] == "1.4.2" and all(c.startswith("Round 3") for c in new["x-contract-changelog"][0]["changes"])
    assert new["x-contract-changelog"][0]["author"] == "integration 2026-10-23 (design: claude-fable-5-1; implementation: claude-sonnet-5-5)"
    assert set(new["paths"]) - set(old["paths"]) == {"/api/v1/structure/block-catalog"} and set(old["paths"]) <= set(new["paths"])
    assert set(new["components"]["schemas"]) - set(old["components"]["schemas"]) == NEW_142_COMPONENTS
    for path, item in old["paths"].items():  # no path loses a verb or a status code
        assert set(item) == set(new["paths"][path]), path
        for verb, op in item.items():
            assert set(op["responses"]) == set(new["paths"][path][verb]["responses"]), (path, verb)
            assert _without_descriptions(op["parameters"] if "parameters" in op else None) == \
                _without_descriptions(new["paths"][path][verb].get("parameters")), (path, verb)
    for name, schema in old["components"]["schemas"].items():
        fresh = json.loads(json.dumps(new["components"]["schemas"][name]))
        added = NEW_142_MEMBERS.get(name, set())
        if name == "StructureReport":
            blocks = fresh["properties"]["blocks"]["items"]["properties"]
            assert set(blocks) - set(schema["properties"]["blocks"]["items"]["properties"]) == {"catalog"}
            del blocks["catalog"]
        if added:
            assert set(fresh["properties"]) - set(schema["properties"]) == added, name
            assert not added & set(fresh.get("required", [])), f"{name}: a 1.4.2 member must stay optional"
            for key in added:
                del fresh["properties"][key]
        assert _without_descriptions(fresh) == _without_descriptions(schema), f"{name} changed beyond the 1.4.2 additions"


def test_block_catalog_components_mirror_the_static_table_and_the_documents() -> None:
    from mbnas_server.api.contract_check import contract_errors
    from mbnas_model_runtime import block_catalog

    table = block_catalog.static_table()
    _contract_ok("BlockCatalogTable", table)
    parsed = schemas.BlockCatalogTable.model_validate(table)
    assert set(parsed.entries) == set(table["entries"]) and parsed.schema_ == "mbnas-block-catalog/1"
    get = CONTRACT["paths"]["/api/v1/structure/block-catalog"]["get"]
    assert get["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/BlockCatalogTable"}
    assert set(get["responses"]) == {"200", "401", "403"} and "parameters" not in get and "security" not in get  # default bearer
    for bad in ({**table, "schema": "mbnas-block-catalog/2"}, {k: v for k, v in table.items() if k != "families"},
                {**table, "extra": 1}, {**table, "entries": {"Conv": {"origin_family": "YOLOv5"}}}):
        assert contract_errors("BlockCatalogTable", bad), bad
        with pytest.raises(ValidationError):
            schemas.BlockCatalogTable.model_validate(bad)
    cat = {"catalog_key": None, "origin_family": None, "introduced_in": None, "role": None, "reference": None, "notes": "unknown",
           "source_path": None}
    _contract_ok("BlockCatalog", cat)
    schemas.BlockCatalog.model_validate(cat)
    for missing in cat:
        assert contract_errors("BlockCatalog", {k: v for k, v in cat.items() if k != missing}), missing
        with pytest.raises(ValidationError):
            schemas.BlockCatalog.model_validate({k: v for k, v in cat.items() if k != missing})
    assert contract_errors("BlockCatalog", {**cat, "origin_family": 3}) and contract_errors("BlockCatalogSummary", {"by_origin_family": {"YOLOv5": 0}, "unknown": 0})
    for good in ({"by_origin_family": {}, "unknown": 0}, {"by_origin_family": {"YOLOv5": 2}, "unknown": 1}):
        _contract_ok("BlockCatalogSummary", good)
        schemas.BlockCatalogSummary.model_validate(good)
    for bad in ({"by_origin_family": {"YOLOv5": 0}, "unknown": 0}, {"by_origin_family": {}, "unknown": -1}, {"by_origin_family": {}}):
        with pytest.raises(ValidationError):
            schemas.BlockCatalogSummary.model_validate(bad)
    props = COMPONENTS["StructureReport"]["properties"]
    assert "catalog_summary" not in COMPONENTS["StructureReport"]["required"] and "catalog" not in props["blocks"]["items"].get("required", [])
    assert set(schemas.StructureReport.model_json_schema()["properties"]) >= {"catalog_summary"}


def test_structure_report_contract_accepts_real_documents_with_the_catalog() -> None:
    """Real CPU documents (checked and declared, YAML backend) carry blocks[].catalog and catalog_summary that the contract types."""
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_model_runtime.genotype import Genotype
    from mbnas_core.search.structure import check_structure, describe_genotype

    cfg = {"nc": 2, "scales": {"n": [0.33, 0.25, 1024]}, "scale": "n",
           "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]], [-1, 1, "C2f", [32, True]],
                        [-1, 1, "Conv", [64, 3, 2]], [-1, 1, "C2f", [64, True]], [-1, 1, "Conv", [128, 3, 2]],
                        [-1, 1, "SPPF", [128, 5]]],
           "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1, 4], 1, "Concat", [1]], [-1, 1, "C2f", [64]],
                    [[9, 6], 1, "Detect", ["nc"]]]}
    genotype = Genotype.from_yaml_dict(cfg, family="contract-test", template_id="contract-test")
    checked = check_structure(genotype, input_size=64, class_count=2)
    declared = describe_genotype(genotype)
    assert checked["status"] == "PASS"
    for label, doc in (("checked", checked), ("declared", declared)):
        blocks = doc["blocks"]
        assert blocks and all(set(b["catalog"]) == set(schemas.BlockCatalog.model_json_schema()["properties"]) for b in blocks), label
        summary = doc["catalog_summary"]
        assert sum(summary["by_origin_family"].values()) + summary["unknown"] == len(blocks), label
        schemas.BlockCatalogSummary.model_validate(summary)
        for b in blocks:
            schemas.BlockCatalog.model_validate(b["catalog"])
        if label == "declared":
            assert all(b["catalog"]["source_path"] is None for b in blocks)  # GET documents never resolve a file
        # describe_genotype alone is the declaration part of a report (no status yet); the route/view layer adds it
        full = doc if "status" in doc else doc | {"status": "UNVERIFIED"}
        _contract_ok("StructureReport", full)
        schemas.StructureReport.model_validate(full)
    assert any(b["catalog"]["source_path"] for b in checked["blocks"])  # an explicit check resolves the installed file
    # a document without the new keys (saved before 1.4.2) is still valid
    old = {k: v for k, v in checked.items() if k != "catalog_summary"}
    old["blocks"] = [{k: v for k, v in b.items() if k != "catalog"} for b in checked["blocks"]]
    _contract_ok("StructureReport", old)
    from mbnas_server.api.contract_check import contract_errors

    broken = json.loads(json.dumps(checked))
    broken["blocks"][0]["catalog"].pop("origin_family")
    assert contract_errors("StructureReport", broken)
    broken = json.loads(json.dumps(checked))
    broken["catalog_summary"]["unknown"] = "none"
    assert contract_errors("StructureReport", broken)


def _plain_archive_summary() -> dict[str, Any]:
    return {"id": "r1", "family": "visdrone", "path": "/x/r1", "dataset_id": None, "scale": None, "status": "unknown",
            "status_detail": None, "started_at": None, "ended_at": None, "duration_s": None,
            "verdicts": {"notebook": None, "transition": None, "confirm": None, "smoke_check": None},
            "generations": {"planned": None, "done": 0}, "candidates": {"total": 0, "ok": 0, "failed": 0},
            "best": {"map50_95": None, "small_target_recall": None, "latency_min_ms": None}, "judge_baseline": None, "arm": None,
            "hashes": {"protocol_v2_train": None, "metric_protocol": None, "dataset_manifest": None},
            "has": {"baselines": False, "generations": False, "pareto": False, "fidelity": False, "confirm": False, "plots": False,
                    "live": False}}


def _campaign_progress(**kw: Any) -> dict[str, Any]:
    base = {"generations_seen": 3, "last_generation": 2,
            "candidates": {"total": 9, "ok": 7, "eval_fail": 1, "degenerate": 1, "cache_hit": 2, "by_status": {"ok": 7, "eval_fail": 1, "degenerate": 1}},
            "last_eval_at": "2026-10-23T01:02:03Z", "last_event_at": "2026-10-23T01:02:04Z", "ledger_rows": 12, "cost_s": 4000.0,
            "cost_search_s": 3000.0, "search_budget_s": 61200.0, "arm_budget_s": 86400.0, "source": "ledger/eval_timing.jsonl"}
    return base | kw


def test_archive_campaign_keys_are_optional_and_typed_on_summary_and_run() -> None:
    from mbnas_server.api.contract_check import contract_errors

    for comp in ("ArchiveRunSummary", "ArchiveRun"):
        assert CAMPAIGN_KEYS <= set(COMPONENTS[comp]["properties"]) and not CAMPAIGN_KEYS & set(COMPONENTS[comp]["required"]), comp
        model_props = set(schemas.CONTRACT_MODELS[comp].model_json_schema()["properties"])
        assert CAMPAIGN_KEYS <= model_props
    plain = _plain_archive_summary()
    _contract_ok("ArchiveRunSummary", plain)  # a plain run has none of the campaign keys: response unchanged
    schemas.CONTRACT_MODELS["ArchiveRunSummary"].model_validate(plain)
    campaign = plain | {"family": "visdrone-campaign", "campaign_id": "vd12_x", "campaign_arm": "S2/ga/seed_42", "space": "S2", "method": "ga",
                        "seed": 42, "pair": 0, "pair_done": True, "queue_state": "done", "campaign_arms_total": 4,
                        "arm_status": "ARM_COMPLETED", "progress": _campaign_progress(),
                        "result": {"stop_reason": "budget_search", "final_stop_reason": "reserve_exhausted", "n_eval": 9, "terminal_count": 1,
                                   "flags": ["EXTERNAL_GPU"], "cost_s": 4000.0,
                                   "best_terminal": {"candidate_id": "c1", "val_map50_95": 0.3, "small_recall": None, "latency_v2": 5.0,
                                                     "params_deploy": 1.0e6}}}
    _contract_ok("ArchiveRunSummary", campaign)
    parsed = schemas.CONTRACT_MODELS["ArchiveRunSummary"].model_validate(campaign)
    assert parsed.progress.candidates.by_status["ok"] == 7 and parsed.result.best_terminal.candidate_id == "c1"
    nulls = plain | {"campaign_id": "c", "campaign_arm": "S1/ga/seed_x", "space": "S1", "method": "ga", "seed": None, "pair": None,
                     "pair_done": None, "queue_state": None, "campaign_arms_total": None, "arm_status": None,
                     "progress": _campaign_progress(generations_seen=None, last_generation=None, candidates=None, last_eval_at=None,
                                                    last_event_at=None, ledger_rows=None, cost_s=None, cost_search_s=None,
                                                    search_budget_s=None, arm_budget_s=None, source=None)}
    _contract_ok("ArchiveRunSummary", nulls)  # no ledger / queue: nulls survive (nothing is pruned), never 0
    schemas.CONTRACT_MODELS["ArchiveRunSummary"].model_validate(nulls)
    # the four identity strings are non-null in the contract (the pydantic mirror uses `X | None = None` for "absent", so only the contract
    # document rejects an explicit null; api/contract.prune would drop it)
    assert contract_errors("ArchiveRunSummary", campaign | {"campaign_id": None})
    for key, wrong in (("seed", "42"), ("pair_done", "yes"), ("campaign_arms_total", 1.5), ("progress", [])):  # the contract rejects all four; pydantic (lax mode) only the last two
        assert contract_errors("ArchiveRunSummary", campaign | {key: wrong}), key
        if key in ("campaign_arms_total", "progress"):
            with pytest.raises(ValidationError):
                schemas.CONTRACT_MODELS["ArchiveRunSummary"].model_validate(campaign | {key: wrong})
    with pytest.raises(ValidationError):
        schemas.CONTRACT_MODELS["ArchiveRunSummary"].model_validate(campaign | {"seed": "forty-two"})
    for key in _campaign_progress():
        short = {k: v for k, v in _campaign_progress().items() if k != key}
        with pytest.raises(ValidationError):
            schemas.CONTRACT_MODELS["ArchiveCampaignProgress"].model_validate(short)
        assert contract_errors("ArchiveRunSummary", campaign | {"progress": short}), key


def test_archive_root_kind_is_optional_and_only_campaign_roots_carry_it() -> None:
    root = COMPONENTS["ArchiveRoot"]
    assert root["required"] == ["path", "family", "exists"] and root["properties"]["kind"]["type"] == "string"
    plain = {"path": "/runs", "family": "visdrone", "exists": True}
    listing = {"roots": [plain, plain | {"family": "visdrone-campaign", "kind": "campaign"}], "runs": [_plain_archive_summary()],
               "scanned_at": "2026-10-23T00:00:00Z", "source": {"run_dir": None, "files": []}, "warnings": []}
    _contract_ok("ArchiveRunList", listing)
    assert schemas.CONTRACT_MODELS["ArchiveRunList"].model_validate(listing).roots[0].kind is None
    assert "MBNAS_ARCHIVE_CAMPAIGN_ROOTS" in CONTRACT["paths"]["/api/v1/archive/runs"]["get"]["description"]


def test_registry_structure_check_documents_the_native_fixed_resolution_as_a_422() -> None:
    """WP-S8 request 1: the 1.4.1 text said a wrong fixed resolution is a 200 FAIL document; the server answers 422 INPUT_SIZE_INVALID."""
    chk = CONTRACT["paths"]["/api/v1/registry/{model_id}/structure-check"]["post"]
    assert "FAIL INPUT_SIZE_INVALID" not in chk["description"] and "for example a native model asked for another resolution" not in chk["description"]
    assert "fixed resolution" in chk["description"] and "no document is returned and nothing is stored" in chk["description"]
    assert "fixed resolution" in chk["responses"]["422"]["description"]
    fixed = COMPONENTS["StructureReport"]["properties"]["fixed_resolution"]["description"]
    assert "422 INPUT_SIZE_INVALID" in fixed and "candidate" in fixed and "A check at another size is a FAIL document" not in fixed
    assert set(chk["responses"]) == {"200", "401", "403", "404", "422", "500"}  # no status code added or removed


def test_1_4_3_selection_422_codes_are_raised_by_core_with_default_status() -> None:
    """Drift guard of live tests/unit/test_contract_history.py (1.4.3, S05 fix round 1), relocated by SYNC-1: the core raises
    exactly the five named selection codes with the default status 422 (no explicit status argument)."""
    import re

    import mbnas_core.console.routes_training as routes_training
    import mbnas_core.training_runs.selection as selection

    src = "".join(Path(m.__file__).read_text() for m in (selection, routes_training))
    for code in ERROR_CODES_ADDED_143:
        if ERROR_CODES_ADDED_143[code] != 422:
            continue
        calls = re.findall(r'TrainingSelectionError\(\s*"%s"\s*,\s*"[^"]*"\s*\)' % code, src)
        assert calls, f"{code} is no longer raised with the default 422 status"
