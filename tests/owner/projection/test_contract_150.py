"""WP-4C item (3): contract 1.5.0 schema rules (WP-4 contract section 1; DD-A38/A42; CHANGES_1.5.0.md).

* the live contract is 1.5.0 (promoted by WP-4B) and declares the projection rules (``x-public-projection``);
* every projected public component is closed (``additionalProperties: false``) and every closed component's pydantic
  mirror rejects an unknown member (``extra="forbid"``, checked by behaviour, not only by configuration);
* the four new components and the three new paths exist, with mirrors registered in ``CONTRACT_MODELS``;
* document-level exclusion: every OWNER_ONLY member of the DTO allowlist (58 concrete paths) is impossible in a valid
  1.5.0 document -- its parent object is closed without declaring it, or denies its name (``propertyNames``) -- except
  the seven Archive* members DD-A19 keeps; the golden PUBLIC members of test_public_golden.py stay declared.
"""
from __future__ import annotations

import enum
import re
from typing import Any

import pytest
from pydantic import ValidationError

from mbnas_contracts import schemas

import projection_world as pw

DOC, WHERE = pw.contract_150()
COMPS: dict[str, Any] = DOC["components"]["schemas"]
PROJECTION = DOC.get("x-public-projection") or {}
PROJECTED = list(PROJECTION.get("projected_components") or [])
CLOSED = sorted(n for n, c in COMPS.items() if c.get("additionalProperties") is False)
NEW_COMPONENTS = {"ModelConfigRevision", "ModelConfigRevisionList", "RuntimeManifestStatus", "UploadResponse"}
NEW_PATHS = {("get", "/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions"),
             ("get", "/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml"),
             ("post", "/api/v1/uploads/model-config")}


def test_the_live_contract_is_1_5_0_with_the_projection_rules() -> None:
    assert WHERE == "live" and DOC["info"]["version"] == "1.5.0"
    assert PROJECTION["since"] == "1.5.0" and "/core/v1/owner/*" in PROJECTION["authority"]
    assert len(PROJECTED) == len(set(PROJECTED)) == 26 and set(PROJECTED) <= set(COMPS)
    assert set(PROJECTION["value_rules"]) == {"scrub", "ids", "paths", "urls", "repair"}
    assert DOC["x-contract-changelog"][0]["version"] == "1.5.0"


@pytest.mark.parametrize("name", PROJECTED)
def test_every_projected_component_is_closed(name: str) -> None:
    assert COMPS[name].get("additionalProperties") is False, name


@pytest.mark.parametrize("name", CLOSED)
def test_closed_component_mirrors_reject_unknown_members(name: str) -> None:
    model = schemas.CONTRACT_MODELS[name]
    assert model.model_config.get("extra") == "forbid", name
    with pytest.raises(ValidationError) as err:
        model.model_validate({"zz_not_a_contract_member": 1})
    assert any(e["type"] == "extra_forbidden" and tuple(e["loc"]) == ("zz_not_a_contract_member",)
               for e in err.value.errors()), err.value.errors()


def test_new_components_and_paths_are_present_and_mirrored() -> None:
    assert NEW_COMPONENTS <= set(COMPS) and NEW_COMPONENTS <= set(schemas.CONTRACT_MODELS)
    have = {(verb, path) for path, item in DOC["paths"].items() for verb in item if verb in ("get", "post")}
    assert NEW_PATHS <= have
    rms = schemas.CONTRACT_MODELS["RuntimeManifestStatus"]
    assert issubclass(rms, enum.Enum) and [e.value for e in rms] == ["verified", "mismatch", "unknown"]
    rev = COMPS["ModelConfigRevision"]
    assert rev["properties"]["kind"]["enum"] == ["original", "selected", "executed", "generated"]
    assert set(rev["required"]) == {"revision_id", "source_sha256", "canonical_hash", "architecture_hash", "kind",
                                    "created_at", "parent_revision_id", "artifact_id"}
    ok = {"revision_id": "rev-1", "source_sha256": "sha256:" + "a" * 64, "canonical_hash": None, "architecture_hash": None,
          "kind": "generated", "created_at": "2026-10-27T00:00:00Z", "parent_revision_id": None, "artifact_id": None}
    schemas.CONTRACT_MODELS["ModelConfigRevision"].model_validate(ok)
    assert pw.contract_errors_150(DOC, "ModelConfigRevision", ok) == []
    for bad in ({**ok, "kind": "edited"}, {**ok, "source_sha256": "abc"}):
        assert pw.contract_errors_150(DOC, "ModelConfigRevision", bad)
    up = {"upload_id": "upl-" + "b" * 64, "source_sha256": "sha256:" + "b" * 64, "bytes": 10}
    schemas.CONTRACT_MODELS["UploadResponse"].model_validate(up)
    assert pw.contract_errors_150(DOC, "UploadResponse", up) == []
    assert pw.contract_errors_150(DOC, "UploadResponse", {**up, "path": "/srv/uploads/x.yaml"})  # never a server path
    for comp in ("CandidateEvaluation", "CandidateDetail", "GeneratedCandidate"):
        assert COMPS[comp]["properties"]["training_status"]["enum"] == ["UNTRAINED", "TRAINED"], comp
    assert {"runner_id", "device_id", "runtime_manifest_status"} <= set(COMPS["Worker"]["properties"])
    assert COMPS["Error"]["properties"]["location"]["enum"] == ["gateway", "core", "runner", "store"]


# -- document-level exclusion of every OWNER_ONLY member ------------------------------------------------------------
def _ref(node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        node = COMPS[node["$ref"].rsplit("/", 1)[1]]
    return node


def _object(node: Any) -> dict[str, Any]:
    node = _ref(node)
    if isinstance(node, dict) and "properties" not in node:
        for key in ("anyOf", "oneOf"):
            for branch in node.get(key) or []:
                b = _ref(branch)
                if isinstance(b, dict) and (b.get("type") == "object" or "properties" in b):
                    return b
    return node if isinstance(node, dict) else {}


def _event_payload(event_type: str) -> dict[str, Any]:
    for part in COMPS["Event"].get("allOf") or []:
        cond = part["if"]["properties"]["type"]
        if cond.get("const") == event_type or event_type in (cond.get("enum") or []):
            return part["then"]["properties"]["payload"]
    raise AssertionError(f"Event has no payload branch for {event_type}")


def member_status(component: str, path: str) -> str:
    """``declared`` (allowed by name), ``closed`` (parent closed, name not declared), ``denied`` (parent's
    propertyNames deny list) or ``open`` (an open parent would accept it) for ``Component :: dotted.path``."""
    m = re.match(r"payload\[type=([a-z.]+)\]\.(.*)$", path)
    if m:
        node: Any = _event_payload(m.group(1))
        path = m.group(2)
    else:
        node = COMPS[component]
    parts = []
    for tok in path.split("."):
        name, brackets = re.match(r"^([^\[]*)((?:\[\])*)$", tok).groups()
        if name:
            parts.append(name)
        parts += ["[]"] * (len(brackets) // 2)
    for part in parts:
        if part == "[]":
            node = _ref(_object(node).get("items") if "items" not in _ref(node) else _ref(node)["items"])
            continue
        obj = _object(node)
        if part == "{}":
            node = obj.get("additionalProperties")
            if not isinstance(node, dict):
                return "closed" if node is False else "open"
            continue
        if part in (obj.get("properties") or {}):
            node = obj["properties"][part]
            continue
        if part in (((obj.get("propertyNames") or {}).get("not") or {}).get("enum") or []):
            return "denied"
        return "closed" if obj.get("additionalProperties") is False else "open"
    return "declared"


OWNER_ROWS = [r for r in pw.dto_owner_only_paths() if r["path"] is not None]


@pytest.mark.parametrize("row", OWNER_ROWS, ids=[f"{r['component']}::{r['path']}" for r in OWNER_ROWS])
def test_owner_only_member_cannot_appear_in_a_valid_1_5_0_document(row: dict[str, Any]) -> None:
    status = member_status(row["component"], row["path"])
    if row["component"] in pw.ARCHIVE_COMPONENTS:  # DD-A19/DD-A42: the gateway-local archive keeps these documents
        assert status == "declared", (row, status)
    else:
        assert status in ("closed", "denied"), (row, status)


GOLDEN_DECLARED = [("CandidateDetail", "lineage.method"), ("CandidateDetail", "lineage.template"),
                   ("CandidateDetail", "lineage.template_detail.id"), ("CandidateDetail", "lineage.search_space.version"),
                   ("CandidateDetail", "lineage.search_space.variant"), ("CandidateDetail", "audit.label"),
                   ("CandidateDetail", "audit.config_estimate.backbone_family"),
                   ("CandidateDetail", "audit.orchestration.rungs.{}.admission.cap_s"),
                   ("CandidateDetail", "audit.orchestration.rungs.{}.admission.billed_s"),
                   ("CandidateDetail", "audit.orchestration.rungs.{}.admission.remaining_s"),
                   ("CandidateDetail", "audit.execution_configuration.artifact_id"),
                   ("CandidateDetail", "checkpoints[].artifact_id"), ("CandidateDetail", "checkpoints[].sha256"),
                   ("Block", "source_kind"), ("Block", "parent_block_id"), ("Block", "params.module"),
                   ("Block", "module"), ("Block", "from"), ("Block", "args"), ("Block", "repeats"),
                   ("CandidateEvaluation", "raw_metrics.ap_50"), ("CandidateEvaluation", "raw_metrics.evaluation_signature"),
                   ("CandidateEvaluation", "training_status"), ("Worker", "runner_id"), ("Worker", "device_id"),
                   ("Event", "payload[type=log].message"), ("Event", "payload[type=log].reason_code"),
                   ("Health", "versions.environment_lock.generated_at"), ("Health", "capabilities.search_space.variant"),
                   ("RegistryList", "warning"), ("GeneratedCandidate", "training_status"),
                   ("Run", "resolved_protocol.console.search.search_space_resolved.variant"),
                   ("Run", "resolved_protocol.resolution.paths.{}.requested")]


@pytest.mark.parametrize("component,path", GOLDEN_DECLARED, ids=[f"{c}::{p}" for c, p in GOLDEN_DECLARED])
def test_golden_public_members_are_declared(component: str, path: str) -> None:
    assert member_status(component, path) == "declared"


@pytest.mark.parametrize("component,path", [("RegistryEntry", "evidence.config_ref.sha256"),
                                            ("RegistryEntry", "evidence.weights.sha256"), ("Run", "budget.search_scope"),
                                            ("Run", "budget.unit"), ("Run", "budget.seeds")])
def test_open_parents_keep_their_public_members(component: str, path: str) -> None:
    """Open objects with deny lists (registry evidence, run budget) accept every member but the denied names."""
    assert member_status(component, path) == "open"
