"""Contract history invariants (D2): every snapshot matches HISTORY.json, the snapshot of info.version equals the contract
byte for byte, the changelog starts with that version, and the migration scripts replay link by link."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mbnas_contracts.openapi import CONTRACT_PATH, HISTORY_PATH

# WP-1 (A6, DD-A10): the contract and its version index are package data of mbnas_contracts; the snapshots and migration
# scripts live in docs/public/contracts/history
CONTRACT = CONTRACT_PATH
HISTORY = Path(__file__).resolve().parents[3] / "docs" / "public" / "contracts" / "history"
INDEX = json.loads(HISTORY_PATH.read_text())["versions"]
# The first three migration scripts (1.0.0 -> 1.2.1) read the handoff file and openapi.json from a fixed absolute path of
# the original host (extracted from the commander transcript; the path now reads /ABSOLUTE/PATH/TO/..., DD-A41) and cannot
# run on another host; every later link is self-contained.
REPLAYABLE = [v for v in INDEX if v["migration"] and tuple(map(int, v["version"].split("."))) >= (1, 2, 2)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("entry", INDEX, ids=[v["version"] for v in INDEX])
def test_every_snapshot_matches_its_recorded_sha256(entry: dict) -> None:
    assert _sha(HISTORY / entry["file"]) == entry["sha256"]
    assert json.loads((HISTORY / entry["file"]).read_text())["info"]["version"] == entry["version"]
    if entry["migration"]:
        assert (HISTORY / entry["migration"]).is_file()
    assert entry["author"] and entry["created"]


def test_current_contract_is_the_latest_snapshot_byte_for_byte() -> None:
    contract = json.loads(CONTRACT.read_text())
    latest = INDEX[-1]
    assert latest["version"] == contract["info"]["version"]
    assert CONTRACT.read_bytes() == (HISTORY / latest["file"]).read_bytes()
    assert _sha(CONTRACT) == latest["sha256"]
    assert contract["x-contract-changelog"][0]["version"] == contract["info"]["version"]
    assert all(c.get("author") and c.get("changes") for c in contract["x-contract-changelog"])


@pytest.mark.parametrize("entry", REPLAYABLE, ids=[v["version"] for v in REPLAYABLE])
def test_migration_replays_its_predecessor_snapshot_byte_for_byte(entry: dict, tmp_path: Path) -> None:
    previous = INDEX[INDEX.index(entry) - 1]
    work = tmp_path / "openapi.json"
    shutil.copy(HISTORY / previous["file"], work)
    done = subprocess.run([sys.executable, "-B", str(HISTORY / entry["migration"]), str(work)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-800:]
    assert work.read_bytes() == (HISTORY / entry["file"]).read_bytes(), f"{entry['migration']} does not reproduce {entry['file']}"


def _changes(version: str) -> list[str]:
    log = json.loads(CONTRACT.read_text())["x-contract-changelog"]
    return next(entry["changes"] for entry in log if entry["version"] == version)


def test_1_4_0_migration_records_pr1_additions_and_the_round_separately() -> None:
    changes = _changes("1.4.0")
    assert any(c.startswith("PR #1") for c in changes) and any(c.startswith("Round 1") for c in changes)


def test_1_4_1_is_a_documentation_round_that_adds_no_enum_value_path_or_required_member() -> None:
    """Round 2 (1.4.1) only types/documents what the servers emit: same paths, same required lists, same enums as 1.4.0."""
    old = json.loads((HISTORY / "openapi-1.4.0.json").read_text())
    new = json.loads((HISTORY / "openapi-1.4.1.json").read_text())
    assert all(c.startswith("Round 2") for c in _changes("1.4.1"))
    assert set(new["paths"]) == set(old["paths"]) and set(new["components"]["schemas"]) == set(old["components"]["schemas"])

    def walk(node: object, path: str = ""):  # yields (path, required list or enum list) of every schema node
        if isinstance(node, dict):
            for key in ("required", "enum"):
                if isinstance(node.get(key), list) and (key != "required" or isinstance(node.get("properties"), dict)):
                    yield f"{path}/{key}", node[key]
            for k, v in node.items():
                yield from walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}/{i}")

    before, after = dict(walk(old["components"])), dict(walk(new["components"]))
    assert before.items() <= after.items(), "a required list or enum of 1.4.0 changed"
    added = {k for k in after if k not in before}
    # only two brand-new closed value sets (the new members scale_source / native_kind) and the {path, reason} items of the
    # DD-26 diagnostic; every enum and required list that existed in 1.4.0 is unchanged (checked above)
    assert added == {"/schemas/StructureReport/properties/scale_source/enum",
                     "/schemas/StructureReport/properties/blocks/items/properties/native_kind/enum",
                     "/schemas/RegistryList/properties/registry_roots_skipped/items/required"}, added


SELECTION_422_CODES = ("DEVICE_UNSUPPORTED", "EVAL_ONLY_UNSUPPORTED", "VALIDATION_DISABLED_UNSUPPORTED", "INITIAL_WEIGHTS_REQUIRED",
                       "RESUME_SOURCE_REQUIRED")


def test_1_4_3_names_the_five_training_selection_422_codes_and_changes_nothing_else() -> None:
    """S05 fix round 1: wording/vocabulary only over 1.4.2; each named code is really raised by the server with status 422."""
    old = json.loads((HISTORY / "openapi-1.4.2.json").read_text())
    new = json.loads((HISTORY / "openapi-1.4.3.json").read_text())
    assert new["info"]["version"] == "1.4.3" and new["x-contract-changelog"][0]["version"] == "1.4.3"
    assert all(c.startswith("S05 fix round 1") for c in new["x-contract-changelog"][0]["changes"])
    assert new["x-contract-changelog"][1:] == old["x-contract-changelog"]
    vocab = new["x-status-vocabulary"]
    assert {k: v for k, v in vocab["error_codes_added"].items() if k in SELECTION_422_CODES} == {c: 422 for c in SELECTION_422_CODES}
    assert {k: v for k, v in vocab["error_codes_added"].items() if k not in SELECTION_422_CODES} == old["x-status-vocabulary"]["error_codes_added"]
    resp = new["paths"]["/api/v1/training-runs"]["post"]["responses"]["422"]["description"]
    for code in SELECTION_422_CODES:
        assert code in resp and code in vocab["error_codes_note"], code
    # nothing but descriptions, the changelog, the version and the vocabulary differ from 1.4.2
    def strip(doc: dict) -> dict:
        doc = json.loads(json.dumps(doc))
        for key in ("x-contract-changelog", "x-status-vocabulary"):
            doc.pop(key)
        doc["info"].pop("version")
        doc["paths"]["/api/v1/training-runs"]["post"]["responses"]["422"].pop("description")
        return doc
    assert strip(old) == strip(new)
    # SYNC-1: the drift guard over the server source (training/selection.py, api/routes_training.py) moved to the owner suite
    # (tests/owner/unit/test_schemas_contract.py::test_1_4_3_selection_422_codes_are_raised_by_core_with_default_status): those
    # modules are mbnas_core owner code (mbnas_core.training_runs.selection, mbnas_core.console.routes_training), not public.


NEW_150_COMPONENTS = {"ModelConfigRevision", "ModelConfigRevisionList", "RuntimeManifestStatus", "UploadResponse"}
NEW_150_PATHS = {"/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions",
                 "/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml",
                 "/api/v1/uploads/model-config"}
# declared 1.4.3 members that 1.5.0 removes as owner-only (DTO allowlist OWNER_ONLY; CHANGES_1.5.0.md section 2)
REMOVED_150 = {("Block", "old_name"), ("Block", "before"), ("Block", "after"), ("Block", "repair_reason"),
               ("Generation", "group_key"), ("Worker", "pid"), ("Worker", "lease_expires_at"),
               ("RegistryList", "registry_roots_skipped")}


def test_1_5_0_is_the_wp4_projection_contract_over_1_4_3() -> None:
    """WP-4 (DD-A17/A38/A42): one bump over 1.4.3 that removes the owner-only members, closes every projected public
    component, adds the configuration-revision / upload components and paths, and keeps every 1.4.3 path and verb."""
    entry = next(v for v in INDEX if v["version"] == "1.5.0")
    predecessor = next(v for v in INDEX if v["version"] == "1.4.3")
    assert INDEX.index(entry) == INDEX.index(predecessor) + 1
    assert entry["migration"] == "migrate_1.4.3_to_1.5.0.py" and entry in REPLAYABLE  # replayed byte for byte above
    if "predecessor" in entry:
        assert entry["predecessor"]["sha256"] == predecessor["sha256"]
    old = json.loads((HISTORY / "openapi-1.4.3.json").read_text())
    new = json.loads((HISTORY / "openapi-1.5.0.json").read_text())
    assert new["info"]["version"] == "1.5.0" and new["x-contract-changelog"][0]["version"] == "1.5.0"
    assert new["x-contract-changelog"][1:] == old["x-contract-changelog"]
    old_c, new_c = old["components"]["schemas"], new["components"]["schemas"]
    assert set(new_c) - set(old_c) == NEW_150_COMPONENTS and set(old_c) <= set(new_c)
    assert set(new["paths"]) - set(old["paths"]) == NEW_150_PATHS and set(old["paths"]) <= set(new["paths"])
    for path, item in old["paths"].items():  # no path loses a verb
        assert set(item) <= set(new["paths"][path]), path
    for comp, member in REMOVED_150:
        assert member in old_c[comp]["properties"] and member not in new_c[comp]["properties"], (comp, member)
    projected = new["x-public-projection"]["projected_components"]
    assert len(projected) == 26 and all(new_c[name]["additionalProperties"] is False for name in projected)
    assert all(old_c[name].get("additionalProperties") is not False for name in ("Run", "CandidateEvaluation", "RegistryEntry"))
    text = (HISTORY / "openapi-1.5.0.json").read_text()
    assert "MBNAS_" + "server" not in text  # the DD-A26 data exception expired with this bump
    assert new["servers"][0]["url"] == "/"  # DD-A41/DD-A42: relative same-origin gateway root (no port 8000)
