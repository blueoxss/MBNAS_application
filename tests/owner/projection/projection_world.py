"""Canary-seeded core + gateway world of the WP-4C projection suite (WP-4 contract section 3; DD-A38/A42/A45).

SYNTHETIC test plumbing only. One world per test session:

1. a real core app + gateway app (``harness.combined_app``: the gateway relays ``/api/v1`` to ``/core/v1/console`` with a
   ``server`` principal through an in-process ASGI transport; ``admin`` / ``runner`` principals for the owner routes);
2. a PILOT_SMOKE-shaped GA + IndependentRandom + baseline run created through the gateway and drained by the in-process
   owner worker with the stub handlers of ``tests/_shared/stub_handlers.py`` (no model, no GPU);
3. canary seeding: a unique marker ``MBNAS-CANARY-OWNER_DTO_<nn>-<16 hex>`` (SECURITY_BASELINE section 5.1 token shape)
   in EVERY owner-only member of ``docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.json`` (48 OWNER_ONLY entries = 58
   concrete paths, docs/internal/evidence/WP4/4A/CHANGES_1.5.0.md section 1) that the core can emit, plus the owner-only
   non-schema rows (jobs spec/result internals, ledger ids, lease owner) and the value-mapped members 1.5.0 removes
   (resolution realpath, checkpoint path, execution configuration path). Integer members (pid, child_pid, ledger id) get
   unique 12-digit integers. The seven ``Archive*`` paths are excluded: they are gateway-local archive documents that
   DD-A19/DD-A42 keep, and the core never stores them;
4. public markers ``MBNAS-PUBLIC-<name>-<hex>`` (and fixed numbers) in PUBLIC members, the golden list that a
   projection must preserve;
5. a runner with the DEFAULT runner id of ``mbnas_runner.config.RunnerSettings`` (resolved below a runtime root of this
   world; C06-D1, fix round 1): its runner principal is bound to that id, it registers through the real E1 route and
   the runner API's own writer publishes it busy on one of the run's jobs, so the id - which the projection keeps for
   runner rows - reaches the snapshot, the SSE replay and the export.

Seeding writes the stored rows directly with SQL after the run finished (rows as a 1.4.x producer stored them: the
projection must clean what is already in a database, stored rows are never rewritten by core) and through the
injectable providers (registry entries, versions); two hard-coded producers are wrapped only while the responses are
captured (``routes_meta._capabilities``: health search_space.root; ``mbnas_core.registry.roots_skipped`` -- and the
1.4.x ``routes_meta.registry_roots_skipped`` binding when it exists -- for the DD-26 diagnostic, which 1.5.0 serves
only on the owner registry route); a seed whose producer no longer exists is recorded as ``planted=False``.

Every public response (gateway ``/api/v1`` with the user bearer AND core ``/core/v1/console`` with the server
principal) is captured once: health, presets, registry, validate, runs list, run, snapshot, every candidate detail,
a proposed candidate's configuration revisions, SSE replay from seq 0 to ``last_seq`` (real HTTP), export zip
(default profile and ``?profile=public``) and, last, the canary candidate's explicit structure check; the console mount
with the admin principal; the owner routes (audit, export, registry) with admin / server / runner / no token, and the
gateway's (absent) owner path. While capturing, a tap records the raw input of every projection call (positive
control). ``MBNAS_TEST_WP4C_MUTATION`` (negative control for evidence, never set in a gate) disables the projection
of SSE and/or export. Tests only read :class:`World`; its repr never prints a token.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / "_shared"
for _p in (str(SHARED), str(HERE)):
    if _p not in sys.path:  # appended, never inserted at 0 (tests/_shared/datasets.py must not shadow site-packages)
        sys.path.append(_p)

import harness  # noqa: E402

REPO_ROOT = HERE.parents[2]
DTO_ALLOWLIST = REPO_ROOT / "docs" / "internal" / "analysis" / "DTO_ALLOWLIST_PROPOSAL.json"
REGISTRY_EXAMPLE = REPO_ROOT / "config" / "examples" / "model_registry.example.json"
LIVE_CONTRACT = REPO_ROOT / "mbnas_contracts" / "src" / "mbnas_contracts" / "openapi" / "openapi.json"
DRAFT_CONTRACT = LIVE_CONTRACT.parent / "drafts" / "openapi-1.5.0.draft.json"
PREFIX = "MBNAS-" + "CANARY-"  # composed (the token prefix is never one literal in a source file)
PUBLIC_PREFIX = "MBNAS-PUBLIC-"
SEED_AREA = "OWNER_DTO"
ARCHIVE_COMPONENTS = ("ArchiveRun", "ArchiveGeneration", "ArchiveLineage", "ArchiveLineageEntry", "ArchiveCandLineage")
CORE_CONSOLE = "/core/v1/console"
CORE_OWNER = "/core/v1/owner"
GATEWAY = "/api/v1"
HOST = socket.gethostname()

# A small but real YOLO configuration: the canary candidate's StructureReport is a saved, parseable document.
CANARY_MODEL = {"nc": 1, "scales": {"n": [0.33, 0.25, 1024]}, "scale": "n",
                "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]], [-1, 1, "C2f", [32, True]],
                             [-1, 1, "Conv", [64, 3, 2]], [-1, 1, "C2f", [64, True]], [-1, 1, "Conv", [128, 3, 2]],
                             [-1, 1, "SPPF", [128, 5]]],
                "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1, 4], 1, "Concat", [1]],
                         [-1, 1, "C2f", [64]], [[9, 6], 1, "Detect", ["nc"]]]}


# -- contract documents --------------------------------------------------------------------------------------------
def contract_150() -> tuple[dict[str, Any], str]:
    """The 1.5.0 contract: the live ``openapi.json`` once WP-4B promoted it, else the WP-4A draft."""
    live = json.loads(LIVE_CONTRACT.read_text(encoding="utf-8"))
    if live["info"]["version"] == "1.5.0":
        return live, "live"
    return json.loads(DRAFT_CONTRACT.read_text(encoding="utf-8")), "draft"


def promoted() -> bool:
    return contract_150()[1] == "live"


def validator_for(doc: dict[str, Any], name: str) -> Any:
    """jsonschema Draft 2020-12 validator of component ``name`` of ``doc`` (local ``$ref`` resolution)."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    registry = Registry().with_resource("urn:mbnas-c150", Resource(contents=doc, specification=DRAFT202012))
    return Draft202012Validator({"$ref": f"urn:mbnas-c150#/components/schemas/{name}"}, registry=registry)


def contract_errors_150(doc: dict[str, Any], name: str, value: Any) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
            for e in validator_for(doc, name).iter_errors(value)]


# -- seeds -----------------------------------------------------------------------------------------------------------
@dataclass
class Seed:
    """One planted owner-only value. ``path`` names the DTO allowlist member (``Component :: member``)."""

    key: str            # stable short key used by the seeding code
    path: str           # "<Component> :: <concrete member path>"
    category: str       # OWNER_ONLY (schema path) | OWNER_ONLY_NON_SCHEMA | MAPPED_REMOVED
    value: Any          # str token, or an int for integer members
    area: str = ""      # manifest area (string tokens only)
    planted_in: list[str] = field(default_factory=list)
    planted: bool = True
    note: str = ""

    @property
    def needle(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:  # never print the token (test logs may end up in owner documents / bundles)
        return f"Seed({self.key!r}, {self.path!r}, planted={self.planted})"


# (key, "Component :: member", category). The 51 OWNER_ONLY schema paths below are the 58 concrete paths of
# CHANGES_1.5.0.md section 1 minus the seven Archive* paths (kept by DD-A19, never produced by core).
SEED_TABLE: list[tuple[str, str, str]] = [
    ("block_slot_kind", "Block :: params.slot_kind", "OWNER_ONLY"),
    ("block_old_name", "Block :: old_name", "OWNER_ONLY"),
    ("block_before", "Block :: before", "OWNER_ONLY"),
    ("block_after", "Block :: after", "OWNER_ONLY"),
    ("block_repair_reason", "Block :: repair_reason", "OWNER_ONLY"),
    ("rm_weights_only_path", "CandidateEvaluation :: raw_metrics.weights_only_path", "OWNER_ONLY"),
    ("rm_eval_checkpoint_path", "CandidateEvaluation :: raw_metrics.eval_checkpoint_path", "OWNER_ONLY"),
    ("rm_rung_result", "CandidateEvaluation :: raw_metrics.rung_result", "OWNER_ONLY"),
    ("rm_artifact_problems", "CandidateEvaluation :: raw_metrics.artifact_problems", "OWNER_ONLY"),
    ("rm_contract_error", "CandidateEvaluation :: raw_metrics.contract_error", "OWNER_ONLY"),
    ("rm_row_error", "CandidateEvaluation :: raw_metrics.row_error", "OWNER_ONLY"),
    ("rm_reported", "CandidateEvaluation :: raw_metrics.reported", "OWNER_ONLY"),
    ("gen_group_key", "Generation :: group_key", "OWNER_ONLY"),
    ("run_budget_policy", "Run :: budget.policy", "OWNER_ONLY"),
    ("run_etri_root", "Run :: resolved_protocol.console.search.search_space_resolved.etri_root", "OWNER_ONLY"),
    ("worker_pid", "Worker :: pid", "OWNER_ONLY"),
    ("worker_child_pid", "Worker :: child_pid", "OWNER_ONLY"),
    ("worker_lease_expires_at", "Worker :: lease_expires_at", "OWNER_ONLY"),
    ("event_log_path", "Event :: payload[type=log].path", "OWNER_ONLY"),
    ("health_search_space_root", "Health :: capabilities.search_space.root", "OWNER_ONLY"),
    ("health_env_lock_path", "Health :: versions.environment_lock.path", "OWNER_ONLY"),
    ("lin_elite", "CandidateDetail :: lineage.elite", "OWNER_ONLY"),
    ("lin_parent_rows", "CandidateDetail :: lineage.parent_rows[]", "OWNER_ONLY"),
    ("lin_selection", "CandidateDetail :: lineage.selection", "OWNER_ONLY"),
    ("lin_crossover", "CandidateDetail :: lineage.crossover", "OWNER_ONLY"),
    ("lin_mutation_events", "CandidateDetail :: lineage.mutation_events[]", "OWNER_ONLY"),
    ("lin_repair_events", "CandidateDetail :: lineage.repair_events[]", "OWNER_ONLY"),
    ("lin_prior_draw", "CandidateDetail :: lineage.prior_draw", "OWNER_ONLY"),
    ("lin_space_hash", "CandidateDetail :: lineage.search_space.space_hash", "OWNER_ONLY"),
    ("lin_prior_hash", "CandidateDetail :: lineage.search_space.prior_hash", "OWNER_ONLY"),
    ("lin_notes", "CandidateDetail :: lineage.notes[]", "OWNER_ONLY"),
    ("audit_rules", "CandidateDetail :: audit.config_estimate.rules", "OWNER_ONLY"),
    ("audit_selection_policy", "CandidateDetail :: audit.config_estimate.selection_policy", "OWNER_ONLY"),
    ("audit_etri_audit_model", "CandidateDetail :: audit.etri_audit_model", "OWNER_ONLY"),
    ("audit_proposal_job_id", "CandidateDetail :: audit.orchestration.proposal_job_id", "OWNER_ONLY"),
    ("audit_estimate_s", "CandidateDetail :: audit.orchestration.rungs.{}.admission.estimate_s", "OWNER_ONLY"),
    ("audit_estimate_source", "CandidateDetail :: audit.orchestration.rungs.{}.admission.estimate_source", "OWNER_ONLY"),
    ("audit_reserved_s", "CandidateDetail :: audit.orchestration.rungs.{}.admission.reserved_s", "OWNER_ONLY"),
    ("reg_config_ref_path", "RegistryEntry :: evidence.config_ref.path", "OWNER_ONLY"),
    ("reg_weights_path", "RegistryEntry :: evidence.weights.path", "OWNER_ONLY"),
    ("reg_build_trace", "RegistryEntry :: evidence.build.trace", "OWNER_ONLY"),
    ("reg_loaded_from", "RegistryEntry :: evidence.pretrained_load.loaded_from", "OWNER_ONLY"),
    ("reg_probe_file", "RegistryEntry :: evidence.probe_file", "OWNER_ONLY"),
    ("reg_source_record", "RegistryEntry :: evidence.source.record", "OWNER_ONLY"),
    ("reg_source_notebook", "RegistryEntry :: evidence.source.notebook", "OWNER_ONLY"),
    ("reg_entry_roots_skipped", "RegistryEntry :: evidence.registry_roots_skipped[]", "OWNER_ONLY"),
    ("reglist_roots_skipped", "RegistryList :: registry_roots_skipped", "OWNER_ONLY"),
    ("sr_before", "StructureReport :: blocks[].before", "OWNER_ONLY"),
    ("sr_after", "StructureReport :: blocks[].after", "OWNER_ONLY"),
    ("sr_repair_reason", "StructureReport :: blocks[].repair_reason", "OWNER_ONLY"),
    ("sr_old_name", "StructureReport :: blocks[].old_name", "OWNER_ONLY"),
    # owner-only rows outside the schemas (DTO allowlist ExportBundle / PublicIdentifiers entries)
    ("job_parent_pool", "ExportBundle :: jobs.spec.params.parent_pool", "OWNER_ONLY_NON_SCHEMA"),
    ("job_previous_search_state", "ExportBundle :: jobs.spec.params.previous_search_state", "OWNER_ONLY_NON_SCHEMA"),
    ("job_proposal_policy", "ExportBundle :: jobs.spec.params.proposal_policy", "OWNER_ONLY_NON_SCHEMA"),
    ("job_out_space", "ExportBundle :: jobs.result.outputs.space", "OWNER_ONLY_NON_SCHEMA"),
    ("job_out_rng_stream", "ExportBundle :: jobs.result.outputs.rng_stream", "OWNER_ONLY_NON_SCHEMA"),
    ("job_out_ga", "ExportBundle :: jobs.result.outputs.ga", "OWNER_ONLY_NON_SCHEMA"),
    ("job_out_search_state", "ExportBundle :: jobs.result.outputs.search_state", "OWNER_ONLY_NON_SCHEMA"),
    ("ledger_id", "PublicIdentifiers :: cost_ledger.id", "OWNER_ONLY_NON_SCHEMA"),
    ("ledger_host_row_id", "PublicIdentifiers :: meta.host_row_id", "OWNER_ONLY_NON_SCHEMA"),
    ("job_lease_owner", "PublicIdentifiers :: lease_owner", "OWNER_ONLY_NON_SCHEMA"),
    # value-mapped members that 1.5.0 removes from the public schemas (CHANGES_1.5.0.md section 2, INTENDED_DROPS)
    ("run_realpath", "Run :: resolved_protocol.resolution.paths.{}.realpath", "MAPPED_REMOVED"),
    ("ckpt_path", "CandidateDetail :: checkpoints[].path", "MAPPED_REMOVED"),
    ("exec_config_path", "CandidateDetail :: audit.execution_configuration.path", "MAPPED_REMOVED"),
]
INT_SEEDS = ("worker_pid", "worker_child_pid", "ledger_id")
SCHEMA_PATHS_EXPECTED = 51  # 58 concrete OWNER_ONLY paths - 7 Archive* paths


def make_seeds() -> dict[str, Seed]:
    out: dict[str, Seed] = {}
    used: set[int] = set()
    for i, (key, path, category) in enumerate(SEED_TABLE, 1):
        if key in INT_SEEDS:
            value = 0
            while value in used or value == 0:
                value = 100_000_000_000 + secrets.randbelow(899_999_999_999)
            used.add(value)
            out[key] = Seed(key, path, category, value)
        else:
            area = f"{SEED_AREA}_{i:02d}"
            out[key] = Seed(key, path, category, f"{PREFIX}{area}-{secrets.token_hex(8)}", area=area)
    return out


def make_public_marks() -> dict[str, str]:
    names = ("block_name", "block_source", "block_parent_block_id", "block_module", "lin_method", "lin_template",
             "lin_space_version", "lin_space_variant", "audit_label", "audit_scheme", "audit_signature",
             "audit_space_variant", "audit_backbone_family", "audit_estimate_label", "audit_scope",
             "audit_admission_reason", "audit_exec_artifact", "audit_exec_sha", "audit_exec_yaml_hash",
             "rm_evaluation_signature", "rm_device_name", "rm_eval_split", "worker_device", "worker_device_name",
             "log_message", "log_reason_code", "reg_config_ref_sha", "reg_weights_sha", "reg_build_status",
             "reg_pretrained_status", "reg_source_package", "reg_probe_status", "ledger_meta_job_kind",
             "sr_block_name", "health_env_lock_generated_at", "reglist_warning")
    return {n: f"{PUBLIC_PREFIX}{n.upper().replace('_', '-')}-{secrets.token_hex(4)}" for n in names}


def dto_owner_only_paths() -> list[dict[str, Any]]:
    """The OWNER_ONLY entries of the DTO allowlist JSON expanded to concrete paths (same rules as WP-4A's checker:
    ``a | b`` alternatives, ``x.{a,b}`` sets; ``{rung}`` -> ``{}``; non-schema entries kept as one row)."""
    doc = json.loads(DTO_ALLOWLIST.read_text(encoding="utf-8"))
    rows = []
    for comp, fields in doc.items():
        for entry, info in fields.items():
            if info.get("class") != "OWNER_ONLY" or comp == "_meta":
                continue
            if comp in ("ExportBundle", "PublicIdentifiers"):
                rows.append({"component": comp, "entry": entry, "path": None})
                continue
            for alt in [a.strip() for a in entry.split(" | ")]:
                alt = alt.replace("{rung}", "{}")
                group = re.search(r"\{([^{}]*,[^{}]*)\}", alt)  # one {a,b,...} alternative set
                if group:
                    for n in group.group(1).split(","):
                        rows.append({"component": comp, "entry": entry,
                                     "path": alt[:group.start()] + n.strip() + alt[group.end():]})
                else:
                    rows.append({"component": comp, "entry": entry, "path": alt})
    return rows


# -- small helpers ---------------------------------------------------------------------------------------------------
def _load(text: str | None, default: Any) -> Any:
    return default if text in (None, "") else json.loads(text)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _append_event_raw(conn: sqlite3.Connection, run_id: str, type_: str, payload: dict[str, Any]) -> int:
    """Store one event row exactly as a 1.4.x producer stored it (no write-time validation; stored rows are kept)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    seq = conn.execute("UPDATE runs SET last_seq = last_seq + 1 WHERE id = ? RETURNING last_seq", (run_id,)).fetchone()[0]
    conn.execute("INSERT INTO events (run_id, seq, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                 (run_id, int(seq), type_, _dump(payload), now))
    return int(seq)


def owner_blocks(s: dict[str, Seed], pub: dict[str, str], *, positions: int = 2) -> list[dict[str, Any]]:
    """Block rows with every owner-only Block member (params.slot_kind, old_name, before, after, repair_reason)
    and public provenance members (name, source, source_kind, parent_block_id, mutated, position, params)."""
    rows = []
    for pos in range(positions):
        row = CANARY_MODEL["backbone"][pos]
        rows.append({"name": pub["block_name"], "source": pub["block_source"], "source_kind": "parent_a",
                     "parent_block_id": pub["block_parent_block_id"], "mutated": True, "position": pos,
                     "operation": "crossover",
                     "params": {"from": row[0], "repeats": row[1], "module": row[2], "args": row[3],
                                "slot_kind": s["block_slot_kind"].value},
                     "old_name": s["block_old_name"].value, "before": {"module": s["block_before"].value},
                     "after": {"module": s["block_after"].value}, "repair_reason": s["block_repair_reason"].value})
    return rows


def owner_lineage(s: dict[str, Seed], pub: dict[str, str], base: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(base or {})
    out.update({"method": pub["lin_method"],
                "template": {"id": pub["lin_template"], "family": "canary-family", "variant": "canary-variant"},
                "search_space": {"version": pub["lin_space_version"], "variant": pub["lin_space_variant"],
                                 "space_hash": s["lin_space_hash"].value, "prior_hash": s["lin_prior_hash"].value},
                "elite": s["lin_elite"].value, "parent_rows": [{"candidate_id": s["lin_parent_rows"].value}],
                "selection": {"winner": s["lin_selection"].value},
                "crossover": {"cut_index": 1, "mask": s["lin_crossover"].value},
                "mutation_events": [{"row": 0, "choice": s["lin_mutation_events"].value}],
                "repair_events": [{"row": 0, "reason": s["lin_repair_events"].value}],
                "prior_draw": {"template": s["lin_prior_draw"].value}, "notes": [s["lin_notes"].value]})
    out.setdefault("operation", "crossover")
    out.setdefault("parents", [])
    out.setdefault("parent_evaluation_ids", [])
    return out


def owner_audit(s: dict[str, Seed], pub: dict[str, str], base: dict[str, Any], rung: str) -> dict[str, Any]:
    audit = copy.deepcopy(base or {})
    audit.update({"label": pub["audit_label"], "architecture_hash_scheme": pub["audit_scheme"],
                  "structure_signature_sha256": pub["audit_signature"], "search_space_variant": pub["audit_space_variant"],
                  "params": 123456, "flops": 7.5,
                  "config_estimate": {"backbone_family": pub["audit_backbone_family"], "label": pub["audit_estimate_label"],
                                      "scope": pub["audit_scope"], "detect_rows": 1, "head_preserved": True,
                                      "rules": s["audit_rules"].value,
                                      "selection_policy": s["audit_selection_policy"].value},
                  "etri_audit_model": {"report": s["audit_etri_audit_model"].value},
                  "execution_configuration": {"artifact_id": pub["audit_exec_artifact"], "sha256": pub["audit_exec_sha"],
                                              "yaml_hash": pub["audit_exec_yaml_hash"], "class_count": 1,
                                              "path": s["exec_config_path"].value}})
    orch = dict(audit.get("orchestration") or {})
    orch["proposal_job_id"] = s["audit_proposal_job_id"].value
    rungs = {k: dict(v) for k, v in (orch.get("rungs") or {}).items()}
    target = dict(rungs.get(rung) or {"state": "EVALUATED"})
    target["admission"] = {"admitted": True, "reason_code": pub["audit_admission_reason"], "arm": "GA",
                           "search_seed": 101, "cap_s": 2700.0, "billed_s": 12.5, "remaining_s": 2687.5,
                           "estimate_s": s["audit_estimate_s"].value, "estimate_source": s["audit_estimate_source"].value,
                           "reserved_s": s["audit_reserved_s"].value}
    rungs[rung] = target
    orch["rungs"] = rungs
    audit["orchestration"] = orch
    return audit


def owner_structure(s: dict[str, Seed], pub: dict[str, str], lineage: dict[str, Any], arch_hash: str | None) -> dict[str, Any]:
    """A saved StructureReport (what an explicit structure check stores in ``audit.structure``) for CANARY_MODEL,
    whose block rows carry the four owner-only StructureReport block members."""
    from mbnas_core.search.structure import describe_genotype
    from mbnas_model_runtime.genotype import Genotype

    genotype = Genotype.from_yaml_text(yaml.safe_dump(CANARY_MODEL), family="canary-family", template_id="canary")
    doc = describe_genotype(genotype)
    doc = json.loads(json.dumps(doc))
    doc.update(status="PASS", yaml_hash=genotype.yaml_hash(), architecture_hash=arch_hash, diagnostics=[], checks=[],
               lineage=copy.deepcopy(lineage), lineage_status="RECORDED")
    for block in doc.get("blocks") or []:
        block.update(name=pub["sr_block_name"], before={"module": s["sr_before"].value},
                     after={"module": s["sr_after"].value}, repair_reason=s["sr_repair_reason"].value,
                     old_name=s["sr_old_name"].value)
    return doc


# -- providers ---------------------------------------------------------------------------------------------------------
def canary_registry(s: dict[str, Seed], pub: dict[str, str]) -> dict[str, dict[str, Any]]:
    """The example registry with every status AVAILABLE (synthetic) and owner-only evidence members on every entry."""
    doc = json.loads(REGISTRY_EXAMPLE.read_text(encoding="utf-8"))
    out = {}
    for entry in doc["registry"]:
        e = copy.deepcopy(entry)
        e["status"], e["reason"] = "AVAILABLE", "synthetic test registry (example entries forced AVAILABLE)"
        ev = dict(e.get("evidence") or {})
        ev.update({"probe_file": s["reg_probe_file"].value, "probe_status": pub["reg_probe_status"],
                   "registry_roots_skipped": [{"path": s["reg_entry_roots_skipped"].value, "reason": "PermissionError"}],
                   "config_ref": {"path": s["reg_config_ref_path"].value, "sha256": pub["reg_config_ref_sha"]},
                   "weights": {"path": s["reg_weights_path"].value, "sha256": pub["reg_weights_sha"]},
                   "build": {"trace": s["reg_build_trace"].value, "status": pub["reg_build_status"]},
                   "pretrained_load": {"loaded_from": s["reg_loaded_from"].value, "status": pub["reg_pretrained_status"]},
                   "source": {"record": s["reg_source_record"].value, "notebook": s["reg_source_notebook"].value,
                              "package": pub["reg_source_package"]}})
        e["evidence"] = ev
        out[e["id"]] = e
    for key in ("reg_config_ref_path", "reg_weights_path", "reg_build_trace", "reg_loaded_from", "reg_probe_file",
                "reg_source_record", "reg_source_notebook", "reg_entry_roots_skipped"):
        s[key].planted_in = ["services.registry_fn (every RegistryEntry.evidence)"]
    return out


def canary_versions(s: dict[str, Seed], pub: dict[str, str]) -> dict[str, Any]:
    s["health_env_lock_path"].planted_in = ["services.versions_fn (versions.environment_lock.path)"]
    return {"python": "test", "packages": {}, "api": "mbnas-core test",
            "environment_lock": {"path": s["health_env_lock_path"].value, "present": True,
                                 "generated_at": pub["health_env_lock_generated_at"], "torch_build": None, "nvidia": None}}


# -- the world ---------------------------------------------------------------------------------------------------------
@dataclass
class Capture:
    label: str
    method: str
    path: str
    status: int
    content_type: str
    body: bytes
    side: str            # gateway | core-server | core-admin | core-runner | core-none
    members: dict[str, bytes] | None = None  # zip members (export)

    def json(self) -> Any:
        return json.loads(self.body)

    def __repr__(self) -> str:  # bodies of owner captures hold tokens: never printed whole
        return f"Capture({self.label!r}, status={self.status}, {len(self.body)} bytes)"

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass
class World:
    seeds: dict[str, Seed]
    public: dict[str, str]
    run_id: str
    candidate_ids: list[str]
    canary_candidate: str
    generated_candidate: str
    canary_rung: str
    proposed_candidate: str
    captures: dict[str, Capture]
    tokens: dict[str, str]
    settings: Any
    gateway_settings: Any
    protocol: dict[str, Any]
    db_path: str
    events_last_seq: int
    timings: dict[str, float]
    notes: list[str] = field(default_factory=list)
    tap: list[tuple[str, str, bytes]] = field(default_factory=list)  # (function, component, raw input JSON)
    runner_id: str = ""  # the default-id runner of item 5 of the module docstring

    def __repr__(self) -> str:  # seeds and captures hold tokens: never printed in a traceback
        return f"World(run={self.run_id!r}, seeds={len(self.seeds)}, captures={len(self.captures)})"

    def by_side(self, *sides: str) -> list[Capture]:
        return [c for c in self.captures.values() if c.side in sides]

    def seed_list(self, *categories: str) -> list[Seed]:
        return [x for x in self.seeds.values() if not categories or x.category in categories]


SCRUB_PID = 4_242_424  # pid=<n> in free text (value rule scrub)


def _seed_db(conn: sqlite3.Connection, run_id: str, s: dict[str, Seed], pub: dict[str, str],
             abs_dir: str) -> dict[str, Any]:
    """Plant the DB-stored owner-only members (see the module docstring); returns ids of the seeded rows."""
    from mbnas_core.db import repo
    from mbnas_core.orchestration.views import run_payload, worker_payload
    from mbnas_model_runtime.genotype import Genotype

    conn.execute("BEGIN IMMEDIATE")
    try:
        cands = repo.list_candidates(conn, run_id)
        searched = [c for c in cands if c["arm"] in ("GA", "IndependentRandom")]
        src = next(c for c in searched if c["arm"] == "GA")
        evals = [e for e in repo.list_evaluations(conn, run_id, candidate_id=src["id"]) if e["status"] == "EVALUATED"]
        if not evals:
            raise RuntimeError(f"no EVALUATED row for {src['id']}: the drained run did not evaluate")
        rung = evals[0]["row"]["rung"]
        # (1) every existing candidate: lineage / audit / blocks gain the owner-only members (mutable columns)
        for c in cands:
            lin = owner_lineage(s, pub, c.get("lineage") or {})
            audit = owner_audit(s, pub, c.get("audit") or {}, rung)
            conn.execute("UPDATE candidates SET lineage_json = ?, audit_json = ?, blocks_json = ? WHERE id = ?",
                         (_dump(lin), _dump(audit), _dump(owner_blocks(s, pub)), c["id"]))
        # (2) the canary candidate: a copy of a GA candidate with a parseable model, a saved structure report, its
        #     own committed evaluation rows (raw_metrics owner members) and a checkpoint row
        cid = f"{src['id']}-canary"
        text = yaml.safe_dump(CANARY_MODEL)
        arch_hash = Genotype.from_yaml_text(text, family="canary-family", template_id="canary").structural_hash() \
            if hasattr(Genotype, "structural_hash") else src.get("architecture_hash")
        lineage = owner_lineage(s, pub, {"operation": "crossover", "parents": list(src["parents"] or [])[:2],
                                         "parent_evaluation_ids": []})
        audit = owner_audit(s, pub, src.get("audit") or {}, rung)
        audit["structure"] = owner_structure(s, pub, lineage, arch_hash)
        now = evals[0]["created_at"]
        conn.execute(
            "INSERT INTO candidates (id, run_id, name, arm, search_seed, training_seed, generation, family, "
            "architecture_hash, architecture_yaml, architecture_path, parents_json, parent_evaluation_ids_json, "
            "lineage_json, blocks_json, candidate_status, current_rung, audit_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, run_id, "GA-CANARY", "GA", src["search_seed"], src["training_seed"], src["generation"], "canary-family",
             arch_hash, text, None, _dump(list(src["parents"] or [])[:2]), _dump([]), _dump(lineage),
             _dump(owner_blocks(s, pub)), "EVALUATED", rung, _dump(audit), now, now))
        canary_rows = []
        for e in evals:
            row = copy.deepcopy(e["row"])
            row.update(id=f"{cid}.{row['rung']}.eval", candidate_id=cid, name="GA-CANARY", family="canary-family",
                       blocks=owner_blocks(s, pub), architecture_hash=arch_hash, architecture_id=arch_hash)
            rm = dict(row.get("raw_metrics") or {})
            rm.update({"ap_50": 0.4242, "evaluation_signature": pub["rm_evaluation_signature"],
                       "device_name": pub["rm_device_name"], "eval_split": pub["rm_eval_split"],
                       "counts": {"tp": 3, "fp": 1, "fn": 2, "gt": 5},
                       "weights_only_path": s["rm_weights_only_path"].value,
                       "eval_checkpoint_path": s["rm_eval_checkpoint_path"].value,
                       "rung_result": {"rung_end_training_ckpt": s["rm_rung_result"].value},
                       "artifact_problems": [s["rm_artifact_problems"].value],
                       "contract_error": s["rm_contract_error"].value, "row_error": s["rm_row_error"].value,
                       "reported": {"note": s["rm_reported"].value}})
            row["raw_metrics"] = rm
            conn.execute("INSERT INTO evaluations (id, run_id, candidate_id, rung, epochs, status, revision, "
                         "supersedes_evaluation_id, row_json, immutable, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, 1, ?, ?)",
                         (row["id"], run_id, cid, row["rung"], row["epochs"], "EVALUATED", _dump(row), now, now))
            canary_rows.append(row)
        conn.execute("INSERT INTO checkpoints (id, run_id, candidate_id, rung, epoch, kind, path, sha256, bytes, "
                     "hashes_json, created_at, updated_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (f"ck-canary{secrets.token_hex(4)}", run_id, cid, rung, 1, "training_full", s["ckpt_path"].value,
                      "0" * 64, 10, _dump({"protocol": "sha256:" + "1" * 64}), now, now, "PRESENT"))
        for key in ("block_slot_kind", "block_old_name", "block_before", "block_after", "block_repair_reason"):
            s[key].planted_in = ["candidates.blocks_json (every candidate)", "evaluations.row_json.blocks (canary)",
                                 "events candidate.evaluated (canary)"]
        for key in ("rm_weights_only_path", "rm_eval_checkpoint_path", "rm_rung_result", "rm_artifact_problems",
                    "rm_contract_error", "rm_row_error", "rm_reported"):
            s[key].planted_in = ["evaluations.row_json.raw_metrics (canary candidate, committed rows)",
                                 "events candidate.evaluated (canary)"]
        for key in [k for k in s if k.startswith("lin_")]:
            s[key].planted_in = ["candidates.lineage_json (every candidate)", "StructureReport.lineage (canary)"]
        for key in ("audit_rules", "audit_selection_policy", "audit_etri_audit_model", "audit_proposal_job_id",
                    "audit_estimate_s", "audit_estimate_source", "audit_reserved_s", "exec_config_path"):
            s[key].planted_in = ["candidates.audit_json (every candidate)"]
        for key in ("sr_before", "sr_after", "sr_repair_reason", "sr_old_name"):
            s[key].planted_in = ["candidates.audit_json.structure.blocks[] (canary candidate, saved StructureReport)"]
        s["ckpt_path"].planted_in = ["checkpoints.path (canary candidate)"]
        # (3) a generated (generation-only) candidate: the snapshot generated_candidates view
        gid = f"{run_id}.gen7.g0.c900"
        gen_audit = owner_audit(s, pub, {"origin": "random_generation",
                                         "generation": {"index": 0, "seed": 7, "attempt": 1,
                                                        "requested_method": "RandomGeneration",
                                                        "effective_method": "RandomGeneration",
                                                        "duplicate_scope": "per_seed", "execution": "CPU"},
                                         "structure": {**owner_structure(s, pub, owner_lineage(s, pub, {"operation": "random"}),
                                                                         "sha256:" + "2" * 64), "parameters": 4242}},
                                rung)
        conn.execute(
            "INSERT INTO candidates (id, run_id, name, arm, search_seed, training_seed, generation, family, "
            "architecture_hash, architecture_yaml, architecture_path, parents_json, parent_evaluation_ids_json, "
            "lineage_json, blocks_json, candidate_status, current_rung, audit_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gid, run_id, "GEN-CANARY", "IndependentRandom", 7, 11, 0, "canary-family", "sha256:" + "2" * 64, text,
             None, _dump([]), _dump([]), _dump(owner_lineage(s, pub, {"operation": "random"})),
             _dump(owner_blocks(s, pub)), "BUILD_VALID", None, _dump(gen_audit), now, now))
        # (4) generations: group_key in every stored generation row (the PK column stays the real key)
        for g in repo.list_generations(conn, run_id):
            row = dict(g["row"])
            row["group_key"] = s["gen_group_key"].value
            conn.execute("UPDATE generations SET row_json = ? WHERE run_id = ? AND group_key = ?",
                         (_dump(row), run_id, g["group_key"]))
        s["gen_group_key"].planted_in = ["generations.row_json.group_key (every generation)", "events generation.updated"]
        # (5) the run: budget policy, etri_root and the resolution realpath (hash-included server fields of 1.4.x)
        run = repo.get_run(conn, run_id)
        budget = dict(run.get("budget") or {})
        budget["policy"] = s["run_budget_policy"].value
        resolved = copy.deepcopy(run["resolved_protocol"])
        search = resolved.setdefault("console", {}).setdefault("search", {})
        ssr = dict(search.get("search_space_resolved") or {"variant": "etri_mixed"})
        ssr["etri_root"] = s["run_etri_root"].value
        search["search_space_resolved"] = ssr
        paths = (resolved.setdefault("resolution", {})).setdefault("paths", {})
        for k, v in list(paths.items()):
            paths[k] = {**(v if isinstance(v, dict) else {"requested": v}), "realpath": s["run_realpath"].value}
        if not paths:
            paths["output_root"] = {"requested": "/ABSOLUTE/PATH/TO/out", "realpath": s["run_realpath"].value}
        conn.execute("UPDATE runs SET budget_json = ?, resolved_protocol_json = ? WHERE id = ?",
                     (_dump(budget), _dump(resolved), run_id))
        s["run_budget_policy"].planted_in = ["runs.budget_json.policy", "events run.updated"]
        s["run_etri_root"].planted_in = ["runs.resolved_protocol_json console.search.search_space_resolved.etri_root"]
        s["run_realpath"].planted_in = ["runs.resolved_protocol_json resolution.paths.*.realpath"]
        # (6) a worker attached to the run (hostname:pid id, owner-only pid / child_pid / lease_expires_at)
        wid = f"{HOST}:{s['worker_pid'].value}"
        heartbeat = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
        wrow = {"epoch": 1, "epoch_target_total": 2, "learning_rate": 0.001, "utilization_percent": 37.0,
                "device_name": pub["worker_device_name"], "pid": s["worker_pid"].value,
                "child_pid": s["worker_child_pid"].value, "lease_expires_at": s["worker_lease_expires_at"].value}
        conn.execute("INSERT OR REPLACE INTO workers (id, status, device, device_uuid, device_name, job_id, run_id, "
                     "candidate_id, heartbeat_at, row_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                     "?, ?, ?)", (wid, "busy", pub["worker_device"], "GPU-canary-0000", pub["worker_device_name"],
                                  None, run_id, cid, heartbeat, _dump(wrow), heartbeat, heartbeat))
        for key in ("worker_pid", "worker_child_pid", "worker_lease_expires_at"):
            s[key].planted_in = ["workers.row_json (worker attached to the run)", "events worker.updated"]
        # (7) jobs: owner-only spec/result internals and the lease owner of one proposal job
        job = next((j for j in repo.list_jobs(conn, run_id) if j["kind"] == "PROPOSE_GENERATION"), None) \
            or repo.list_jobs(conn, run_id)[0]
        spec = dict(job.get("spec") or {})
        params = dict(spec.get("params") or {})
        params.update(parent_pool=[{"candidate_id": s["job_parent_pool"].value}],
                      previous_search_state={"state": s["job_previous_search_state"].value},
                      proposal_policy={"policy": s["job_proposal_policy"].value})
        spec["params"] = params
        result = dict(job.get("result") or {})
        outputs = dict(result.get("outputs") or {})
        outputs.update(space={"describe": s["job_out_space"].value}, rng_stream=s["job_out_rng_stream"].value,
                       ga={"tournament": s["job_out_ga"].value}, search_state={"state": s["job_out_search_state"].value})
        result["outputs"] = outputs
        conn.execute("UPDATE jobs SET spec_json = ?, result_json = ?, lease_owner = ? WHERE id = ?",
                     (_dump(spec), _dump(result), s["job_lease_owner"].value, job["id"]))
        for key in ("job_parent_pool", "job_previous_search_state", "job_proposal_policy"):
            s[key].planted_in = [f"jobs.spec_json.params ({job['id']})"]
        for key in ("job_out_space", "job_out_rng_stream", "job_out_ga", "job_out_search_state"):
            s[key].planted_in = [f"jobs.result_json.outputs ({job['id']})"]
        s["job_lease_owner"].planted_in = [f"jobs.lease_owner ({job['id']})"]
        # (8) cost ledger: an internal row id and meta.host_row_id
        conn.execute("INSERT INTO cost_ledger (id, run_id, arm, candidate_id, job_id, attempt, kind, gpu_seconds, physical, "
                     "billed_arm, device_uuid, started_at, finished_at, meta_json, created_at, updated_at) VALUES "
                     "(?, ?, 'GA', ?, ?, 1, 'train', 1.5, 1, 'GA', NULL, ?, ?, ?, ?, ?)",
                     (s["ledger_id"].value, run_id, cid, job["id"], now, now,
                      _dump({"host_row_id": s["ledger_host_row_id"].value, "job_kind": pub["ledger_meta_job_kind"]}),
                      now, now))
        s["ledger_id"].planted_in = ["cost_ledger.id"]
        s["ledger_host_row_id"].planted_in = ["cost_ledger.meta_json.host_row_id"]
        # (9) events as the engine / worker store them, carrying the seeded rows
        worker = repo.get_worker(conn, wid)
        _append_event_raw(conn, run_id, "log", {"message": pub["log_message"], "level": "warning", "job_id": job["id"],
                                                "reason_code": pub["log_reason_code"], "path": s["event_log_path"].value,
                                                "reported_sha256": "a" * 64, "computed_sha256": "b" * 64})
        # value rule "scrub": free text with this host's name, a pid and absolute host paths (DD-A48 (b), D13)
        _append_event_raw(conn, run_id, "log", {
            "message": f"checkpoint rejected on {HOST} pid={SCRUB_PID} at {abs_dir}/rejected.pt", "level": "error",
            "message_detail": f"RESUME_BLOCKED: {abs_dir}/ckpt unreadable on {HOST} (pid={SCRUB_PID})",
            "reason_code": "SCRUB_PROBE", "job_id": job["id"]})
        _append_event_raw(conn, run_id, "worker.updated", worker_payload(worker))
        gens = repo.list_generations(conn, run_id)
        if gens:
            _append_event_raw(conn, run_id, "generation.updated", dict(gens[0]["row"]))
        _append_event_raw(conn, run_id, "run.updated", run_payload(repo.get_run(conn, run_id)))
        for row in canary_rows:
            _append_event_raw(conn, run_id, "candidate.evaluated", row)
        s["event_log_path"].planted_in = ["events log payload.path"]
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"canary_candidate": cid, "generated_candidate": gid, "rung": rung, "worker_id": wid, "job_id": job["id"],
            "proposed_candidate": src["id"]}


# -- capture -------------------------------------------------------------------------------------------------------
class _Server:
    """uvicorn serving ``app`` on 127.0.0.1:<ephemeral> in a thread (lifespan off: the TestClient entered it)."""

    def __init__(self, app: Any) -> None:
        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        harness.wait_for(lambda: self.server.started, timeout=20)
        return f"http://127.0.0.1:{self.port}"

    def __exit__(self, *exc: Any) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)


def read_sse(url: str, headers: dict[str, str], last_seq: int, timeout: float = 30.0) -> tuple[int, bytes]:
    """Raw SSE bytes from ``url`` until the frame with ``id: last_seq`` was received (or ``timeout``)."""
    import httpx

    buf = bytearray()
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=timeout) as c, c.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 200:
            resp.read()
            return resp.status_code, resp.content
        marker = f"id: {last_seq}\n".encode()
        for chunk in resp.iter_bytes():
            buf.extend(chunk)
            start = buf.find(marker)
            end = buf.find(b"\n\n", start) if start >= 0 else -1
            if end >= 0:
                del buf[end + 2:]  # exactly seq 1..last_seq (later frames of the same chunk are not part of the capture)
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"SSE replay did not reach seq {last_seq} within {timeout}s ({len(buf)} bytes)")
    return 200, bytes(buf)


def _zip_members(data: bytes) -> dict[str, bytes] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return {n: zf.read(n) for n in zf.namelist()}
    except zipfile.BadZipFile:
        return None


def capture_all(gw: Any, core: Any, world: dict[str, Any], tokens: dict[str, str], protocol: dict[str, Any],
                core_app: Any, gateway_app: Any) -> dict[str, Capture]:
    """Every public response through the gateway (user bearer) and directly from core (server principal), plus the
    owner responses (admin principal) used as positive controls."""
    run_id = world["run_id"]
    caps: dict[str, Capture] = {}
    user = harness.auth()
    server = {"Authorization": f"Bearer {tokens['server']}"}
    admin = {"Authorization": f"Bearer {tokens['admin']}"}

    def put(label: str, side: str, method: str, path: str, resp: Any) -> None:
        body = resp.content
        ctype = resp.headers.get("content-type", "")
        caps[label] = Capture(label, method, path, resp.status_code, ctype, body, side,
                              _zip_members(body) if "zip" in ctype else None)

    routes = [("GET", "/health", None), ("GET", "/protocols/presets", None), ("GET", "/registry", None),
              ("POST", "/protocols/validate", {"protocol": protocol}), ("GET", "/runs", None),
              ("GET", f"/runs/{run_id}", None), ("GET", f"/runs/{run_id}/snapshot", None),
              ("GET", f"/runs/{run_id}/export", None), ("GET", f"/runs/{run_id}/export?profile=public", None)]
    routes += [("GET", f"/runs/{run_id}/candidates/{cid}", None) for cid in world["candidate_ids"]]
    def tag(cid: str) -> str:
        return "{" + ("canary" if cid == world["canary_candidate"] else "generated" if cid == world["generated_candidate"]
                      else cid) + "}"

    for method, sub, body in routes:
        name = sub
        for cid in sorted(world["candidate_ids"], key=len, reverse=True):  # candidate ids contain the run id
            name = name.replace(f"/candidates/{cid}", f"/candidates/{tag(cid)}")
        name = name.replace(f"/runs/{run_id}", "/runs/{run}")
        put(f"gateway {method} {name}", "gateway", method, GATEWAY + sub,
            gw.request(method, GATEWAY + sub, json=body, headers=user))
        put(f"core-server {method} {name}", "core-server", method, CORE_CONSOLE + sub,
            core.request(method, CORE_CONSOLE + sub, json=body, headers=server))
    # configuration revisions of a proposed (search) candidate: the generated revision (F5d)
    listing = f"/runs/{run_id}/candidates/{world['proposed_candidate']}/config-revisions"
    for side, client, prefix, headers in (("gateway", gw, GATEWAY, user), ("core-server", core, CORE_CONSOLE, server)):
        resp = client.get(prefix + listing, headers=headers)
        put(f"{side} GET /runs/{{run}}/candidates/{{proposed}}/config-revisions", side + "-revisions", "GET",
            prefix + listing, resp)
        revs = (resp.json().get("revisions") or []) if resp.status_code == 200 else []
        if revs:
            y = client.get(f"{prefix}{listing}/{revs[0]['revision_id']}/yaml", headers=headers)
            put(f"{side} GET /runs/{{run}}/candidates/{{proposed}}/config-revisions/{{generated}}/yaml",
                side + "-revisions", "GET", f"{prefix}{listing}/{revs[0]['revision_id']}/yaml", y)
    # the console mount with the admin principal (projected like every other principal since WP-4B)
    for method, sub in (("GET", f"/runs/{run_id}/snapshot"), ("GET", f"/runs/{run_id}/candidates/{world['canary_candidate']}"),
                        ("GET", f"/runs/{run_id}/export"), ("GET", "/health"), ("GET", "/registry")):
        name = sub.replace(f"/candidates/{world['canary_candidate']}", "/candidates/{canary}").replace(
            f"/runs/{run_id}", "/runs/{run}")
        put(f"core-admin-console {method} {name}", "core-admin-console", method, CORE_CONSOLE + sub,
            core.request(method, CORE_CONSOLE + sub, headers=admin))
    # owner routes (admin): positive controls
    put("core-admin GET /owner/runs/{run}/export", "core-admin", "GET", f"{CORE_OWNER}/runs/{run_id}/export",
        core.get(f"{CORE_OWNER}/runs/{run_id}/export", headers=admin))
    for cid in world["candidate_ids"]:
        put(f"core-admin GET /owner/runs/{{run}}/candidates/{tag(cid)}/audit", "core-admin", "GET",
            f"{CORE_OWNER}/runs/{run_id}/candidates/{cid}/audit",
            core.get(f"{CORE_OWNER}/runs/{run_id}/candidates/{cid}/audit", headers=admin))
    audit = f"/runs/{run_id}/candidates/{world['canary_candidate']}/audit"
    runner = {"Authorization": f"Bearer {tokens['runner']}"}
    for label, headers in (("server", server), ("runner", runner), ("none", {})):
        put(f"core-{label} GET /owner/runs/{{run}}/candidates/{{canary}}/audit", f"core-{label}-owner", "GET",
            CORE_OWNER + audit, core.get(CORE_OWNER + audit, headers=headers))
        put(f"core-{label} GET /owner/runs/{{run}}/export", f"core-{label}-owner", "GET",
            f"{CORE_OWNER}/runs/{run_id}/export", core.get(f"{CORE_OWNER}/runs/{run_id}/export", headers=headers))
    for label, headers in (("admin", admin), ("server", server)):
        put(f"core-{label} GET /owner/registry", f"core-{label}-owner-registry", "GET", f"{CORE_OWNER}/registry",
            core.get(f"{CORE_OWNER}/registry", headers=headers))
    put("gateway GET /api/v1/owner/runs/{run}/candidates/{canary}/audit", "gateway-owner", "GET", GATEWAY + "/owner" + audit,
        gw.get(GATEWAY + "/owner" + audit, headers=user))
    # SSE replay from seq 0 through the gateway relay and directly from core (real HTTP: the streams never end)
    last = world["last_seq"]
    with _Server(gateway_app) as gw_url, _Server(core_app) as core_url:
        for label, side, url, headers in (
                ("gateway GET /runs/{run}/events?after_seq=0", "gateway", f"{gw_url}{GATEWAY}/runs/{run_id}/events?after_seq=0", user),
                ("core-server GET /runs/{run}/events?after_seq=0", "core-server",
                 f"{core_url}{CORE_CONSOLE}/runs/{run_id}/events?after_seq=0", server)):
            status, raw = read_sse(url, headers, last)
            caps[label] = Capture(label, "GET", url.split("127.0.0.1:", 1)[1].split("/", 1)[1], status,
                                  "text/event-stream", raw, side)
    # explicit structure check of the canary candidate, last of all: it archives the saved report into
    # structure_history and appends log events after the replayed last_seq
    sc = f"/runs/{run_id}/candidates/{world['canary_candidate']}/structure-check"
    put("gateway POST /runs/{run}/candidates/{canary}/structure-check", "gateway", "POST", GATEWAY + sc,
        gw.post(GATEWAY + sc, headers=user))
    put("core-server POST /runs/{run}/candidates/{canary}/structure-check", "core-server", "POST", CORE_CONSOLE + sc,
        core.post(CORE_CONSOLE + sc, headers=server))
    return caps


def sse_events(raw: bytes) -> list[dict[str, Any]]:
    """Parsed ``data:`` JSON of every SSE frame that has an ``id`` (replayed events)."""
    out = []
    for frame in raw.decode("utf-8", "replace").split("\n\n"):
        fields: dict[str, str] = {}
        for line in frame.splitlines():
            if line.startswith(":"):
                continue
            k, _, v = line.partition(":")
            fields[k] = v[1:] if v.startswith(" ") else v
        if "data" in fields and "id" in fields:
            out.append(json.loads(fields["data"]))
    return out


TAPPED = ("project_public", "project_member")


@contextlib.contextmanager
def projection_tap(records: list[tuple[str, str, bytes]]):
    """Record the raw input of every projection call (``project_public`` / ``project_member`` of
    :mod:`mbnas_core.console.projection`, wherever a core module bound them) while responses are captured: the positive
    control that a seed really reached the projection boundary. Behaviour is unchanged (the original is called)."""
    import mbnas_core.console.projection as projection

    mp = pytest.MonkeyPatch()
    try:
        originals = {name: getattr(projection, name) for name in TAPPED if callable(getattr(projection, name, None))}
        wrappers = {}
        for name, fn in originals.items():
            def wrapper(component: Any, payload: Any, *a: Any, _fn: Any = fn, _name: str = name, **k: Any) -> Any:
                try:
                    records.append((_name, str(component), json.dumps(payload, default=str).encode()))
                except (TypeError, ValueError):
                    records.append((_name, str(component), repr(payload).encode()))
                return _fn(component, payload, *a, **k)
            wrappers[name] = wrapper
        for mod_name, mod in list(sys.modules.items()):
            if not (mod_name == "mbnas_core" or mod_name.startswith("mbnas_core.")) or mod is None:
                continue
            for name, fn in originals.items():
                if getattr(mod, name, None) is fn:
                    mp.setattr(mod, name, wrappers[name])
        yield sorted(originals)
    finally:
        mp.undo()


MUTATION_ENV = "MBNAS_TEST_WP4C_MUTATION"  # negative control for evidence only; never set in a gate run


@contextlib.contextmanager
def _mutation(spec: str):
    """Negative control (``MBNAS_TEST_WP4C_MUTATION=sse,export``): replace the projection of the SSE stream and/or of
    the export members by the identity while responses are captured, so the suite can be shown to FAIL on a broken
    projection. Empty spec = no change (the normal case)."""
    import mbnas_core.console.projection as projection
    import mbnas_core.console.routes_stream as routes_stream

    kinds = {k.strip() for k in spec.split(",") if k.strip()}
    unknown = kinds - {"sse", "export"}
    if unknown:
        raise ValueError(f"{MUTATION_ENV}: unknown mutation(s) {sorted(unknown)}")
    mp = pytest.MonkeyPatch()
    try:
        if "sse" in kinds:
            mp.setattr(routes_stream, "project_public", lambda _c, payload, *a, **k: payload)
        if "export" in kinds:  # the export writer imports these from the module at call time
            mp.setattr(projection, "project_public", lambda _c, payload, *a, **k: payload)
            if hasattr(projection, "project_member"):
                mp.setattr(projection, "project_member", lambda _c, payload, *a, **k: payload)
        yield sorted(kinds)
    finally:
        mp.undo()


@contextlib.contextmanager
def _seeded_producers(s: dict[str, Seed]):
    """Wrap the two hard-coded producers of owner-only health / registry members while responses are captured."""
    from mbnas_core.console import routes_meta

    mp = pytest.MonkeyPatch()
    try:
        cap = getattr(routes_meta, "_capabilities", None)
        if callable(cap):
            def seeded_capabilities(*a: Any, **k: Any) -> dict[str, Any]:
                out = cap(*a, **k)
                out["search_space"] = {**(out.get("search_space") or {}), "root": s["health_search_space_root"].value}
                return out
            mp.setattr(routes_meta, "_capabilities", seeded_capabilities)
            s["health_search_space_root"].planted_in = ["mbnas_core.console.routes_meta._capabilities (wrapped)"]
        else:
            s["health_search_space_root"].planted = False
            s["health_search_space_root"].note = "routes_meta._capabilities no longer exists: producer removed"
        # DD-26 diagnostic: the producer is mbnas_core.registry.roots_skipped (1.4.x: also bound in routes_meta for the
        # public GET /registry; 1.5.0: read only by the owner route GET /core/v1/owner/registry at request time)
        import mbnas_core.registry as core_registry

        token = s["reglist_roots_skipped"].value
        fake = lambda *_a, **_k: [{"path": token, "reason": token}]  # noqa: E731
        planted = []
        if hasattr(core_registry, "roots_skipped"):
            mp.setattr(core_registry, "roots_skipped", fake)
            planted.append("mbnas_core.registry.roots_skipped (wrapped)")
        if hasattr(routes_meta, "registry_roots_skipped"):
            mp.setattr(routes_meta, "registry_roots_skipped", fake)
            planted.append("mbnas_core.console.routes_meta.registry_roots_skipped (wrapped)")
        s["reglist_roots_skipped"].planted_in = planted
        if not planted:
            s["reglist_roots_skipped"].planted = False
            s["reglist_roots_skipped"].note = "no registry_roots_skipped producer exists any more"
        yield
    finally:
        mp.undo()


def default_runner_id(base: Path) -> str:
    """The runner id a runner host gets when its runner.env names none: the real ``RunnerSettings`` default (C06-D1,
    fix round 1), resolved with ``MBNAS_HOME=<base>/runner_runtime`` (a runtime root of this world, never the
    operator's) and no ``MBNAS_RUNNER_RUNNER_ID``."""
    from mbnas_runner.config import RunnerSettings

    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("MBNAS_HOME", str(base / "runner_runtime"))
        mp.delenv("MBNAS_RUNNER_RUNNER_ID", raising=False)
        return str(RunnerSettings().runner_id)
    finally:
        mp.undo()


def _attach_default_runner(core: Any, conn: sqlite3.Connection, run_id: str, runner_id: str, token: str,
                           work_root: Path) -> None:
    """Item 5 of the module docstring: register the default-id runner through the real E1 route (runner principal)
    and publish it busy on one of the run's training jobs with the runner API's own writer
    (``runner_api.routes._publish``: workers row + ``worker.updated`` event of the run)."""
    import runner_sim
    from mbnas_core.db import repo
    from mbnas_core.db.core import tx
    from mbnas_core.runner_api import routes as runner_routes

    runner_sim.RunnerSim(core, token, runner_id, work_root).register()
    job = next(j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN")
    with tx(conn):
        runner_routes._publish(conn, runner_routes._runner(conn, runner_id), "busy", job, event_run=run_id)


def build_world(base: Path) -> World:
    """Build, seed and capture (see the module docstring). ``base`` is an empty directory."""
    from fastapi.testclient import TestClient

    from mbnas_core.console.services import AppServices
    from conftest_common import A100, _datasets

    t0 = time.monotonic()
    timings: dict[str, float] = {}
    seeds, pub = make_seeds(), make_public_marks()
    roots = {"data": base / "data", "runs": base / "runs"}
    for p in roots.values():
        p.mkdir(parents=True, exist_ok=True)
    mp = pytest.MonkeyPatch()
    try:
        for key in list(os.environ):  # the operator's MBNAS_* environment never leaks into this world
            if key.upper().startswith("MBNAS_") and not key.upper().startswith("MBNAS_TEST_"):
                mp.delenv(key, raising=False)
        harness.stub_env(mp, epoch_s=0.0)
        cs = harness.core_settings_for_gateway(roots, max_replay_events=100_000)
        registry = canary_registry(seeds, pub)
        entries = list(registry.values())
        devices = [copy.deepcopy(A100)]
        versions = canary_versions(seeds, pub)
        svc = AppServices(cs, registry_fn=lambda: (entries, "synthetic-test-registry", pub["reglist_warning"]),
                          devices_fn=lambda: copy.deepcopy(devices), versions_fn=lambda: copy.deepcopy(versions),
                          source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0},
                          sse_heartbeat_s=0.3, sse_poll_s=0.05)
        combined = harness.combined_app(roots, registry, devices, core_settings=cs, services=svc)
        tokens = dict(combined.tokens)
        from mbnas_core.db.core import connect
        from mbnas_core.runner_api import auth as core_auth

        runner_id = default_runner_id(base)  # C06-D1: the principal is bound to the DEFAULT runner id (item 5)
        conn = connect(cs)
        try:
            row, tok = core_auth.create_principal(conn, "runner", runner_id=runner_id)
            tokens["runner"] = tok
        finally:
            conn.close()
        ds = _datasets.build_dataset(roots["data"], "ds", {"train": 4, "val": 2, "test": 2}, "0 0.5 0.5 0.25 0.25\n",
                                     None, 1)
        protocol = harness.small_protocol(ds, str(roots["runs"] / "out"))
        with TestClient(combined.core_app) as core, TestClient(combined.gateway_app) as gw:
            run_id, _ = harness.validate_and_run(gw, protocol)
            timings["create_run_s"] = round(time.monotonic() - t0, 2)
            worker = harness.make_inline_worker(cs)
            try:
                harness.drain(worker)
            finally:
                worker.conn.close()
            timings["drain_s"] = round(time.monotonic() - t0, 2)
            status = gw.get(f"{GATEWAY}/runs/{run_id}", headers=harness.auth()).json()["status"]
            if status != "COMPLETED":
                raise RuntimeError(f"seed run {run_id} ended {status}, not COMPLETED")
            conn = connect(cs, init=False)
            try:
                ids = _seed_db(conn, run_id, seeds, pub, str(roots["runs"]))
                _attach_default_runner(core, conn, run_id, runner_id, tokens["runner"], base / "runner_work")
                last_seq = int(conn.execute("SELECT last_seq FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
                cand_ids = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE run_id = ? ORDER BY id", (run_id,))]
            finally:
                conn.close()
            timings["seed_s"] = round(time.monotonic() - t0, 2)
            meta = {"run_id": run_id, "candidate_ids": cand_ids, "canary_candidate": ids["canary_candidate"],
                    "generated_candidate": ids["generated_candidate"], "last_seq": last_seq,
                    "proposed_candidate": ids["proposed_candidate"]}
            tap: list[tuple[str, str, bytes]] = []
            mutation = os.environ.get(MUTATION_ENV, "")
            with _seeded_producers(seeds), projection_tap(tap), _mutation(mutation):
                caps = capture_all(gw, core, meta, tokens, protocol, combined.core_app, combined.gateway_app)
            if mutation:
                timings["NEGATIVE_CONTROL_MUTATION"] = mutation  # type: ignore[assignment]
            timings["capture_s"] = round(time.monotonic() - t0, 2)
        return World(seeds=seeds, public=pub, run_id=run_id, candidate_ids=cand_ids,
                     canary_candidate=ids["canary_candidate"], generated_candidate=ids["generated_candidate"],
                     canary_rung=ids["rung"], proposed_candidate=ids["proposed_candidate"], captures=caps,
                     tokens=tokens, settings=cs,
                     gateway_settings=combined.gateway_settings, protocol=protocol, db_path=str(cs.db_path),
                     events_last_seq=last_seq, timings=timings, tap=tap, runner_id=runner_id)
    finally:
        mp.undo()


# -- scanning ------------------------------------------------------------------------------------------------------
def leaks(world: World, data: bytes, categories: tuple[str, ...] = ()) -> list[str]:
    """Seed paths whose value occurs in ``data`` (string tokens as raw bytes; integers as a decimal word)."""
    import re

    out = []
    for seed in world.seed_list(*categories):
        if isinstance(seed.value, int):
            if re.search(rb"(?<![0-9])" + str(seed.value).encode() + rb"(?![0-9])", data):
                out.append(seed.path)
        elif seed.needle.encode() in data:
            out.append(seed.path)
    return out


def capture_blobs(cap: Capture) -> list[tuple[str, bytes]]:
    """(location, bytes) of a capture: the body, and every zip member for an export."""
    if cap.members is not None:
        return [(f"{cap.label}!{name}", data) for name, data in sorted(cap.members.items())]
    return [(cap.label, cap.body)]


def find_paths(node: Any, key: str, prefix: str = "$") -> list[str]:
    """JSON paths of every object member named ``key`` below ``node``."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{prefix}.{k}"
            if k == key:
                out.append(here)
            out.extend(find_paths(v, key, here))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(find_paths(v, key, f"{prefix}[{i}]"))
    return out


def write_manifest(world: World, path: Path) -> Path:
    """A ``mbnas.canary_manifest/1`` document of the string seeds (scripts/dev/canary_scan.py input)."""
    areas = []
    for seed in world.seed_list():
        if not seed.area:
            continue
        areas.append({"area": seed.area, "value": seed.value, "description": seed.path,
                      "expected_in": ["owner"], "planted_in": [{"path": p, "status": "planted" if seed.planted else "absent"}
                                                                for p in seed.planted_in],
                      "must_not_in": ["public", "registration", "public_api_responses"]})
    doc = {"schema": "mbnas.canary_manifest/1", "generator": "tests/owner/projection/projection_world.py",
           "token_format": "MBNAS-CANARY-<AREA>-<16 hex>", "note": "WP-4C test world (SYNTHETIC, temporary)",
           "areas": areas}
    path.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    return path
