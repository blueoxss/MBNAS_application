#!/usr/bin/env python3
"""WP-1A scaffold placement: copy the source snapshot into the five src-layout packages and rewrite imports (reproducible).

Inputs (read-only): ``build/source_snapshot/`` (the ONLY source of bytes; every source file is checked against the sha256
recorded in ``docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl`` + ``SOURCE_SNAPSHOT_DELTA_*.jsonl``) and the
placement table ``docs/internal/analysis/MODULE_MAP.json`` (``target_module_path`` of every ``src/mbnas_server`` module).
Binding rules: ``docs/internal/WORK_PACKAGES/WP-1_SPLIT.md`` (§3 placement + overrides, §4 symbol table, §6.3/§6.7 mixed
pool symbols and register hook, §11 scripts) and MODULE_MAP §8.1 (dotted prefix table) / §8.2 (string references).

What it does, per placed file:

* package modules (139 ``.py``): copied to ``<pkg>/src/<module path>``; ALL relative imports become absolute; every
  ``mbnas_server.*`` import (module level and function level) is mapped to its new module. A ``from X import a, b`` whose
  names moved to different modules (contract §4 symbol table) is split into one statement per new module; a submodule whose
  leaf name changed keeps its local binding with ``as`` (``from mbnas_model_runtime import genotype_audit as audit``), so
  code bodies are not edited. Host-specific symbols (``install``/``error_response``, ``bearer_ok``/``require_bearer``,
  ``contract_json``, ``resolve_handler``...) map by the package the file lands in.
* string module references (MODULE_MAP §8.2): non-docstring string literals that are exactly a dotted module reference
  (``mbnas_server.x.y``, ``mbnas_server.x.y:attr``, ``mbnas_server.x.Class``, prefix ``mbnas_server.x.``) or a module file
  path (``mbnas_server/x/y.py...``) are mapped the same way (handler tables, ``-m`` argv, uvicorn factory, registry
  ``import_class``, block-catalog paths, logger names, the services registry module name, the register-hook prefix).
  The register hook string ``mbnas_server.search.etri_bridge:ensure_mixed_registered`` becomes
  ``mbnas_model_runtime.operators:ensure_registered`` (contract §6.7).
* scripts (contract §11): same rewriting with the host class of the script (owner/dev → core view, runner, public,
  public-tool → gateway view); ``Settings`` becomes the host settings class of contract §5 (``CoreSettings`` /
  ``RunnerSettings`` / ``GatewaySettings``, imported ``as Settings`` so bodies stay byte-identical);
  ``scripts/runner/resume_equivalence.py`` gets ``--protocol-json`` instead of the core presets import (§11).
* data: ``db/schema.sql``, ``protocol/handoff_protocol_20261006.json``, ``contracts/openapi.json`` + ``HISTORY.json``,
  ``config/model_registry.example.json`` (package data + ``config/examples``), the two protocol examples
  (``config/examples``), the contract history (DD-A10: ``docs/public/contracts/history``) and the four ETRI reference
  files (contract §6.9: ``docs/internal/provenance/vendored_reference/yolo_ga_runtime`` + generated README).
* ``__init__.py`` for every package directory that has no snapshot ``__init__`` mapped to it (empty; the three package
  roots without one get a one-line docstring).

Not done here (other WP-1 sub-tasks): symbol moves themselves (§4: new modules such as ``mbnas_contracts.constants`` are
NOT created — imports point at them and fail until their owner creates them), settings split (§5), vendoring (§6.1-6.5),
transitional runtime changes (§7), other code changes (§8), test placement (§9).

Usage (TARGET_ROOT is the parent of ``scripts/``)::

    runtime/venv/bin/python -I scripts/dev/rewrite_imports.py [--check | --preview DIR] [--force] [--report PATH]

Default: refuse to overwrite an existing target whose bytes differ (owners edit placed files after the scaffold), then
write all targets and ``docs/internal/provenance/PLACEMENT_MANIFEST.jsonl``. ``--check`` writes nothing and exits 1 when
any target differs from what this tool would produce; ``--preview DIR`` writes the outputs and the manifest under DIR
only (diff against the tree). Exit 2 on a rule violation (unmapped module, sha mismatch...).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOOL = "scripts/dev/rewrite_imports.py"
SCHEMA = "mbnas.placement_manifest/1"
PACKAGES = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner", "mbnas_server")
OWNER_WP = {"mbnas_contracts": "WP-1B", "mbnas_model_runtime": "WP-1C", "mbnas_core": "WP-1D",
            "mbnas_runner": "WP-1E", "mbnas_server": "WP-1F"}
SERVER = "application/MBNAS_server"                      # snapshot-relative root of the old single package
SRC_PREFIX = f"{SERVER}/src/mbnas_server/"
OLD = "mbnas_server"
MAX_LINE = 120

# --------------------------------------------------------------------------------------------------------------------
# Placement overrides on top of MODULE_MAP.json target_module_path (contract §3.1)
# --------------------------------------------------------------------------------------------------------------------
MODULE_OVERRIDES = {
    "mbnas_server.api.routes_files": "mbnas_core.console.routes_files",       # §3.1(a): whole file to core in WP-1
    "mbnas_server.worker.main": "mbnas_core.owner_worker.main",               # §3.1(c), §7
    "mbnas_server.worker.child": "mbnas_core.owner_worker.child",             # §3.1(c), §7
    "mbnas_server.search.etri_bridge": "mbnas_core.search.mixed_pool.bridge",  # §3.1(e), §6.4 (owner rewrites)
}
# files placed more than once (first target = default mapping for other importers)
EXTRA_TARGETS = {
    "mbnas_server.api.app": ["mbnas_server.api.app", "mbnas_core.api.app"],                       # §3.1(b)
    "mbnas_server.worker.contract": ["mbnas_contracts.jobs_protocol", "mbnas_runner.contract",
                                     "mbnas_core.owner_worker.contract"],                         # §3.1(d)
    # MODULE_MAP places the 9-line ``python -m`` shim in mbnas_runner (WP-1E rewrites it into the spec-file entry, §2/§7);
    # the same shim is also placed next to the owner worker loop it starts, so ``python -m mbnas_core.owner_worker`` keeps
    # the meaning of ``python -m mbnas_server.worker`` (DD-A14: the owner worker runs every JobKind in WP-1).
    "mbnas_server.worker.__main__": ["mbnas_runner.__main__", "mbnas_core.owner_worker.__main__"],
}
# modules that become packages although their source is not an __init__ (package data lives in the directory)
FORCED_PACKAGES = {"mbnas_contracts.openapi"}

# --------------------------------------------------------------------------------------------------------------------
# Import mapping tables
# --------------------------------------------------------------------------------------------------------------------
# (context package, old module) -> new module: applies to symbol fallbacks, module objects and string references
CTX_MODULE_OVERRIDES = {
    ("mbnas_core", "mbnas_server.api.app"): "mbnas_core.api.app",                 # §3.1(b) console app on the core host
    ("mbnas_core", "mbnas_server.worker"): "mbnas_core.owner_worker",             # `-m mbnas_server.worker` = owner loop
    ("mbnas_core", "mbnas_server.worker.__main__"): "mbnas_core.owner_worker.__main__",
}
# (context package, old module) -> new module, only when the module object itself is imported / referenced by string
MODULE_OBJECT_OVERRIDES = {
    ("mbnas_core", "mbnas_server.api.errors"): "mbnas_core.api.errors",           # `errors.install(app)` (§4)
    ("mbnas_server", "mbnas_server.api.errors"): "mbnas_server.api.errors",
}
_JOBS = "mbnas_contracts.jobs_protocol"
_HANDLER_TABLE = {"mbnas_core": "mbnas_core.owner_worker.contract", "mbnas_runner": "mbnas_runner.contract",
                  "mbnas_contracts": "mbnas_contracts.jobs_protocol"}
_HOST_ERRORS = {"mbnas_core": "mbnas_core.api.errors", "mbnas_server": "mbnas_server.api.errors"}
_HOST_AUTH = {"mbnas_core": "mbnas_core.api.auth", "mbnas_server": "mbnas_server.api.auth"}
_HOST_DEPS = {"mbnas_core": "mbnas_core.console.deps", "mbnas_server": "mbnas_server.api.deps"}


def _sym(module: str, names: str, target: Any) -> dict[tuple[str, str], Any]:
    return {(module, n): target for n in names.split()}


# contract §4 symbol table (+ §6.3 for the mixed-pool helpers that live in mbnas_model_runtime.operators)
SYMBOL_MOVES: dict[tuple[str, str], Any] = {
    **_sym("mbnas_server.worker.contract",
           "JobFailure JobPaused Emitter Handler STOP_ENV PAUSED_REASON PAUSE_REASONS HANDLERS_ENV", _JOBS),
    **_sym("mbnas_server.worker.contract", "DEFAULT_HANDLERS resolve_handler handler_override _load", _HANDLER_TABLE),
    **_sym("mbnas_server.protocol.validator", "protocol_hash RESOLUTION_HASH_EXCLUDED", "mbnas_contracts.protocol_hash"),
    **_sym("mbnas_server.protocol.validator", "DEFAULT_POSTPROCESS", "mbnas_contracts.constants"),
    **_sym("mbnas_server.benchmark.capability", "DEFAULT_POSTPROCESS", "mbnas_contracts.constants"),
    **_sym("mbnas_server.protocol.presets", "DEFAULT_BASELINE14 BASELINE_FAMILIES", "mbnas_contracts.constants"),
    **_sym("mbnas_server.search.structure", "SCHEMA BACKEND REPRESENTATION CHECK_NAMES EVIDENCE_LEVELS CHECK_EVIDENCE",
           "mbnas_contracts.structure_doc"),
    **_sym("mbnas_server.api.contract", "contract component prune CONTRACT_PATH", "mbnas_contracts.openapi"),
    **_sym("mbnas_server.api.contract", "validator contract_errors", "mbnas_server.api.contract_check"),
    **_sym("mbnas_server.api.errors", "ApiError pydantic_errors _HTTP_CODES", "mbnas_contracts.errors"),
    **_sym("mbnas_server.api.errors", "install error_response", _HOST_ERRORS),
    **_sym("mbnas_server.api.limits", "BodyLimitMiddleware", "mbnas_contracts.asgi"),
    **_sym("mbnas_server.api.auth", "RequestIdMiddleware", "mbnas_contracts.asgi"),
    **_sym("mbnas_server.api.auth", "bearer_ok require_bearer", _HOST_AUTH),
    **_sym("mbnas_server.api.deps", "contract_json", _HOST_DEPS),
    **_sym("mbnas_server.registry", "SPEC_BY_ID adapter_for arch_spec", "mbnas_model_runtime.registry"),
    **_sym("mbnas_server.training.selection", "run_official_evaluation", "mbnas_runner.evaluation.official_evaluation"),
    **_sym("mbnas_server.evaluation.handlers", "fitness_and_gate _fitness_rung", "mbnas_core.protocol.fitness_gate"),
    **_sym("mbnas_server.search.etri_bridge", "yaml_uses_mixed_modules MIXED_MODULES", "mbnas_model_runtime.operators"),
}
# (script host class, old module, name) -> (new module, new name); imported `as <old name>` so bodies stay unchanged
SCRIPT_SYMBOL_OVERRIDES = {
    ("owner", "mbnas_server.config", "Settings"): ("mbnas_core.config", "CoreSettings"),
    ("dev", "mbnas_server.config", "Settings"): ("mbnas_core.config", "CoreSettings"),
    ("runner", "mbnas_server.config", "Settings"): ("mbnas_runner.config", "RunnerSettings"),
    ("public-tool", "mbnas_server.config", "Settings"): ("mbnas_server.config", "GatewaySettings"),
}
STRING_EXACT = {  # contract §6.7
    "mbnas_server.search.etri_bridge:ensure_mixed_registered": "mbnas_model_runtime.operators:ensure_registered",
}
RE_DOTTED = re.compile(r"^(mbnas_server(?:\.[A-Za-z_]\w*)*)(\.?)(:[A-Za-z_]\w*)?$")
RE_PATH = re.compile(r"^(mbnas_server(?:/[A-Za-z_]\w*)+)\.py((?:\s.*)?)$", re.S)
RE_FROM_HEAD = re.compile(r"from(\s+)((?:\.\s*)*(?:[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)?)(\s+)import\b")

# --------------------------------------------------------------------------------------------------------------------
# Scripts (contract §11, MODULE_MAP §7)
# --------------------------------------------------------------------------------------------------------------------
SCRIPT_CTX_PKG = {"owner": "mbnas_core", "dev": "mbnas_core", "runner": "mbnas_runner", "public": "mbnas_model_runtime",
                  "public-tool": "mbnas_server"}
SCRIPTS = [  # (snapshot file under application/MBNAS_server/scripts, target, host class)
    ("probe_registry.py", "scripts/owner/probe_registry.py", "owner"),
    ("search_dry_run.py", "scripts/owner/search_dry_run.py", "owner"),
    ("smoke_end_to_end.py", "scripts/owner/smoke_end_to_end.py", "owner"),
    ("bench_host_gpu.py", "scripts/runner/bench_host_gpu.py", "runner"),
    ("download_weights.py", "scripts/runner/download_weights.py", "runner"),
    ("resume_equivalence.py", "scripts/runner/resume_equivalence.py", "runner"),
    ("make_pilot_subset.py", "scripts/public/make_pilot_subset.py", "public"),
    ("dev_stack.py", "scripts/dev/dev_stack.py", "dev"),
    ("source_tree_hash.py", "scripts/dev/source_tree_hash.py", "dev"),
    ("api_bearer_only.py", "tests/public/tools/api_bearer_only.py", "public-tool"),
    ("smoke_api_only.py", "tests/public/tools/smoke_api_only.py", "public-tool"),
]
SCRIPT_NOTES = {
    "probe_registry.py": "owner script (core registry + model_runtime probe; train-smoke path also imports mbnas_runner)",
    "search_dry_run.py": "owner script; Settings -> CoreSettings (contract §5, WP-1D)",
    "smoke_end_to_end.py": "owner script; contract_errors -> mbnas_server.api.contract_check (WP-1F); "
                           "SERVER_ROOT=Path(__file__).parents[1] (dev_stack/.env paths) now resolves to scripts/ (WP-7)",
    "bench_host_gpu.py": "runner-host script; Settings -> RunnerSettings (WP-1E, constructed with output_root/output_roots/"
                         "dataset_roots kwargs); source_hash.default_roots is removed by WP-1B (§4) -> body needs "
                         "the roots API; SERVER_ROOT paths relocate (WP-7); GPU-only, not executed in WP-1",
    "download_weights.py": "runner-host script; stdlib only; optional read-only ETRI_NAS cross-check unchanged "
                           "(MODULE_MAP §7 suggests removing it; not required by contract §11)",
    "resume_equivalence.py": "runner-host script; core presets import replaced by --protocol-json (contract §11); "
                             "registry symbols -> mbnas_model_runtime.registry (WP-1C)",
    "make_pilot_subset.py": "public script (model_runtime only); sys.path fallback points at the old checkout layout",
    "dev_stack.py": "dev/owner script (superseded by WP-7 start/stop); API factory -> mbnas_core.api.app, worker -> "
                    "`-m mbnas_core.owner_worker`, guardian -> mbnas_runner.gpu_guard; WEB_ROOT still points at "
                    "MBNAS_web (WP-7)",
    "source_tree_hash.py": "dev script; source_hash.default_roots/_entries are reworked by WP-1B (§4) -> CLI needs the "
                           "roots API; defaults use the old server/web roots (WP-7)",
    "api_bearer_only.py": "public tool (gateway); Settings -> GatewaySettings (WP-1F); body still reads worker_python "
                          "and MBNAS_* env names of the old single Settings -> rework with the gateway relay (WP-3)",
    "smoke_api_only.py": "public tool (gateway + worker live smoke); uvicorn factory stays mbnas_server.api.app (gateway "
                         "skeleton in WP-1), worker -> `-m mbnas_runner`; STUB_DIR points at tests/worker (WP-1G "
                         "moves stub_handlers to tests/_shared); live-only, not runnable until WP-2/WP-3",
}
# exact-text patches applied to the snapshot text BEFORE import rewriting: (old, new, expected count, reason)
SCRIPT_PATCHES: dict[str, list[tuple[str, str, int, str]]] = {
    "resume_equivalence.py": [
        ("def _setup(workdir: Path) -> tuple[Any, Any, Any, dict[str, Any], str]:\n",
         "def _setup(workdir: Path, protocol_json: str) -> tuple[Any, Any, Any, dict[str, Any], str]:\n", 1,
         "§11: protocol comes from a JSON file"),
        ("    from mbnas_server.protocol.presets import pilot_smoke_protocol\n", "", 1,
         "§11: runner host must not import core presets"),
        ("    proto = pilot_smoke_protocol()\n",
         "    proto = json.loads(Path(protocol_json).read_text(encoding=\"utf-8\"))  # WP-1 §11: owner-generated file\n",
         1, "§11"),
        ("_setup(workdir)\n", "_setup(workdir, args.protocol_json)\n", 2, "§11 (stage + negative cases)"),
        ('           "--imgsz", str(args.imgsz), "--warmup", str(args.warmup)]\n',
         '           "--imgsz", str(args.imgsz), "--warmup", str(args.warmup), "--protocol-json", str(args.protocol_json)]\n',
         1, "§11: stage children get the same protocol file"),
        ('        p.add_argument("--workdir", default=None)\n',
         '        p.add_argument("--workdir", default=None)\n'
         '        p.add_argument("--protocol-json", default=None,\n'
         '                       help="resolved protocol JSON (e.g. the PILOT_SMOKE preset written by the owner host); '
         'required")\n', 1, "§11"),
        ("    args = ap.parse_args()\n    if args.cmd == \"stage\":\n",
         "    args = ap.parse_args()\n"
         "    if not args.protocol_json:\n"
         "        ap.error(\"--protocol-json is required (WP-1 contract §11: the runner host receives the protocol as a "
         "file)\")\n"
         "    if args.cmd == \"stage\":\n", 1, "§11"),
    ],
}

# --------------------------------------------------------------------------------------------------------------------
# Data, references, deletions
# --------------------------------------------------------------------------------------------------------------------
DATA = [  # (snapshot relpath, target relpath, kind, owner, notes)
    (f"{SRC_PREFIX}db/schema.sql", "mbnas_core/src/mbnas_core/db/schema.sql", "moved", "WP-1D",
     "package data (db/core.py reads it with importlib.resources)"),
    (f"{SRC_PREFIX}protocol/handoff_protocol_20261006.json",
     "mbnas_core/src/mbnas_core/protocol/handoff_protocol_20261006.json", "moved", "WP-1D",
     "package data; bytes unchanged (contract §8 presets: default_protocol() substitutes console.paths)"),
    (f"{SERVER}/contracts/openapi.json", "mbnas_contracts/src/mbnas_contracts/openapi/openapi.json", "moved", "WP-1B",
     "package data; contract 1.4.2 (delta 20261023T220106); info.description contains the forbidden string "
     "'MBNAS_server' (byte-identical to the 1.4.2 history snapshot, see HISTORY.json) -> WP-1B/designer decision"),
    (f"{SERVER}/contracts/history/HISTORY.json", "mbnas_contracts/src/mbnas_contracts/openapi/HISTORY.json", "moved",
     "WP-1B", "package data (contract §2)"),
    (f"{SERVER}/config/model_registry.example.json", "mbnas_core/src/mbnas_core/registry/model_registry.example.json",
     "moved", "WP-1D", "package data (contract §4 last row); EXAMPLE_REGISTRY switches to it in WP-1D (§8)"),
    (f"{SERVER}/config/model_registry.example.json", "config/examples/model_registry.example.json", "moved", "WP-1A",
     "second copy (contract §4 last row)"),
    (f"{SERVER}/config/protocol.example.json", "config/examples/protocol.example.json", "moved", "WP-1A",
     "example config referenced by tests (test_protocol_presets) and contract §10; contains host example paths (WP-7)"),
    (f"{SERVER}/config/protocol.pilot_smoke.example.json", "config/examples/protocol.pilot_smoke.example.json", "moved",
     "WP-1A", "example config (contract §10 preset parity option); contains host example paths (WP-7)"),
]
HISTORY_SRC = f"{SERVER}/contracts/history"
HISTORY_DST = "docs/public/contracts/history"
REFERENCE_DIR = "docs/internal/provenance/vendored_reference/yolo_ga_runtime"
REFERENCE_FILES = ["mixed_blocks.py", "mixed_search_space.py", "mixed_hooks.py", "runtime.py"]  # contract §6.9
ETRI_ALL = ["__init__.py", "mixed_blocks.py", "mixed_search_space.py", "mixed_hooks.py", "runtime.py", "crossover_v22.py",
            "pipeline.py", "ga_hooks.py", "mixed_cli.py", "runtime.py.before_smoke_nc_20261006_043702_dd7da4de.bak"]
ETRI_V2_INPUTS = ["__init__.py", "crossover_v22.py", "runtime.py", "pipeline.py", "ga_hooks.py", "mixed_hooks.py",
                  "mixed_search_space.py", "mixed_blocks.py"]  # modules loaded by the old bridge (ETRI_RUNTIME_SCOPE §3.1)
DELETED = [  # (snapshot relpath, notes)
    *[(f"{SERVER}/src/mbnas_server.egg-info/{n}", "build artifact of the old single package; regenerated per package "
       "by the new pyproject files (MODULE_MAP §6)")
      for n in ("PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt")],
    (f"{SERVER}/scripts/doctor.py", "superseded by the mbnas-core-doctor console script (contract §11, MODULE_MAP §7)"),
    (f"{SERVER}/scripts/setup_venv.sh", "superseded: online pip install; wheelhouse --no-index install (DD-A7, WP-7)"),
    (f"{SERVER}/scripts/run_tests_host.sh", "superseded: root pyproject pytest config (contract §9) + WP-7 scripts"),
    ("application/scripts/console_stack.sh", "superseded: API 8000 + vite 5173 launcher -> WP-7 start/stop (§11)"),
    *[(f"yolo_ga_runtime/{n}", "not vendored (DD-A12 Option A, contract §6); sha256 kept here and in "
       f"{REFERENCE_DIR}/README.md")
      for n in ETRI_ALL if n not in REFERENCE_FILES],
]
NEW_HANDWRITTEN = [  # files written by hand in WP-1A (recorded, not generated here)
    ("pyproject.toml", "WP-1A", "workspace root: no [project]; [tool.pytest.ini_options] per contract §9"),
    *[(f"{p}/pyproject.toml", OWNER_WP[p], "contract §2 (literal version 0.1.0, src layout, package data, scripts)")
      for p in PACKAGES],
    ("scripts/dev/rewrite_imports.py", "WP-1A", "this placement/rewrite tool (contract §3.2, §11)"),
    ("scripts/dev/check_import_closure.py", "WP-1A", "import-closure + forbidden-string checker (contract §13.2, DD-A25)"),
]
ROOT_DOCSTRINGS = {
    "mbnas_contracts": '"""MBNAS Research Console shared contracts (public): DTO schemas, OpenAPI contract data, job-protocol '
                       'types and stdlib helpers."""\n',
    "mbnas_model_runtime": '"""MBNAS model runtime (public): backend adapters, custom operators, genotype observation, '
                           'checkpoints, datasets, evaluation, benchmark."""\n',
    "mbnas_server": '"""MBNAS gateway (public): user-facing ``/api/v1`` gateway and the read-only archive."""\n',
}
SPLIT_NOTES = {
    "mbnas_server.adapters.structure_native": "SCHEMA/CHECK_NAMES now imported from mbnas_contracts.structure_doc "
                                              "(created by WP-1B)",
    "mbnas_server.api.auth": "WP-1F keeps bearer_ok/require_bearer (gateway); RequestIdMiddleware -> mbnas_contracts.asgi "
                             "(WP-1B); core copy of the bearer helpers is mbnas_core.api.auth (WP-1D) (§4)",
    "mbnas_server.api.candidate_views": "whole file to core in WP-1 (§3.1(a)); signed-URL split is WP-5",
    "mbnas_server.api.contract": "WP-1B keeps contract/component/prune/CONTRACT_PATH (importlib.resources); "
                                 "validator/contract_errors -> mbnas_server.api.contract_check (WP-1F) (§4)",
    "mbnas_server.api.deps": "contract_json is duplicated as mbnas_server.api.deps (WP-1F); prune from "
                             "mbnas_contracts.openapi (§4)",
    "mbnas_server.api.errors": "WP-1B keeps ApiError/pydantic_errors/_HTTP_CODES; install/error_response -> "
                               "mbnas_core.api.errors (WP-1D) and mbnas_server.api.errors (WP-1F) (§4)",
    "mbnas_server.api.routes_files": "whole file to core in WP-1 (§3.1(a)); gateway download/relay is WP-3/WP-5",
    "mbnas_server.api.views": "whole file to core in WP-1 (§3.1(a)); signed-URL split is WP-5",
    "mbnas_server.artifacts.registry": "whole file to core in WP-1 (§3.1(a)); sign/verify -> gateway in WP-5",
    "mbnas_server.config": "WP-1D derives CoreSettings here (§5); GatewaySettings/RunnerSettings are new modules of "
                           "WP-1F/WP-1E; SERVER_ROOT/etri_nas_root/archive_* removed",
    "mbnas_server.evaluation.handlers": "WP-1E removes fitness_and_gate/_fitness_rung and the protocol.fitness/promotion "
                                        "imports (§8, DD-A15); the functions move to mbnas_core.protocol.fitness_gate "
                                        "(WP-1D)",
    "mbnas_server.protocol.presets": "DEFAULT_BASELINE14/BASELINE_FAMILIES -> mbnas_contracts.constants (WP-1B); "
                                     "preset_paths(settings) (§8, WP-1D)",
    "mbnas_server.protocol.validator": "protocol_hash/RESOLUTION_HASH_EXCLUDED -> mbnas_contracts.protocol_hash, "
                                       "DEFAULT_POSTPROCESS -> mbnas_contracts.constants (WP-1B); probe via "
                                       "mixed_pool.bridge (§6.6, WP-1D)",
    "mbnas_server.provenance.source_hash": "WP-1B: compute_source_tree_hash(roots, labels); default_roots and the "
                                           "monorepo path constants are removed (§4)",
    "mbnas_server.registry": "WP-1D keeps evidence/status functions and re-exports SPEC_BY_ID/adapter_for/arch_spec from "
                             "mbnas_model_runtime.registry (WP-1C) (§4)",
    "mbnas_server.search.etri_bridge": "placed for WP-1D to rewrite as the mixed-pool bridge (§6.4); still contains the "
                                       "yolo_ga_runtime sys.path import until then",
    "mbnas_server.search.structure": "SCHEMA/BACKEND/REPRESENTATION/CHECK_NAMES/EVIDENCE_LEVELS/CHECK_EVIDENCE -> "
                                     "mbnas_contracts.structure_doc (WP-1B) (§4)",
    "mbnas_server.training.context": "WP-1E: resolve_model requires params.arch, adapter_for from "
                                     "mbnas_model_runtime.registry, no core registry import (§7, DD-A16)",
    "mbnas_server.training.handlers": "WP-1E drops the fitness/gate computation in _evaluate (§8, DD-A15)",
    "mbnas_server.training.selection": "run_official_evaluation -> mbnas_runner.evaluation.official_evaluation (WP-1E) "
                                       "(§4); the rest stays (WP-1D)",
    "mbnas_server.worker.child": "owner child kept whole (§3.1(c), §7); the thin mbnas_runner.child is new (WP-1E)",
    "mbnas_server.worker.main": "owner worker loop kept whole (§3.1(c), §7)",
}
TARGET_NOTES = {
    "mbnas_server.api.app": {
        "mbnas_core.api.app": "copy for the core console app (§3.1(b)): WP-1D removes the archive mount, CoreSettings",
        "mbnas_server.api.app": "copy for the gateway skeleton (§3.1(b)): WP-1F keeps bearer/CORS/request-id/body "
                                "limit/log redaction + archive_router + GET /api/v1/health",
    },
    "mbnas_server.worker.contract": {
        "mbnas_contracts.jobs_protocol": "WP-1B keeps the protocol types only (§4)",
        "mbnas_runner.contract": "WP-1E keeps DEFAULT_HANDLERS (5 kinds)/resolve_handler/handler_override/_load (§4, §7)",
        "mbnas_core.owner_worker.contract": "WP-1D keeps DEFAULT_HANDLERS (6 kinds)/resolve_handler/handler_override/"
                                            "_load (§4, §7)",
    },
    "mbnas_server.worker.__main__": {
        "mbnas_runner.__main__": "MODULE_MAP placement; still starts the owner loop (runner->core) until WP-1E makes it "
                                 "the --help/--spec-file entry (§2, §7)",
        "mbnas_core.owner_worker.__main__": "extra copy by WP-1A: keeps `python -m mbnas_core.owner_worker` == old "
                                            "`python -m mbnas_server.worker` (DD-A14); WP-1D may drop it",
    },
}


class RuleError(Exception):
    """A placement/rewrite rule was violated; nothing is written."""


@dataclass
class Ctx:
    pkg: str                    # mapping context: the package the file lands in (scripts: their host view)
    script_class: str | None    # owner|dev|runner|public|public-tool for scripts, None for package modules
    old_module: str | None      # old dotted module (relative-import base), None for scripts
    is_pkg_init: bool
    self_new: str | None        # new module of this copy (self references map here)


@dataclass
class Output:
    target: str
    data: bytes
    mode: int
    kind: str
    owner_wp: str
    source: str | None = None   # snapshot relpath
    notes: str = ""
    changes: dict[str, Any] = field(default_factory=dict)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------------------------------------------------
class Mapper:
    def __init__(self, module_map: dict[str, str], universe: set[str]) -> None:
        self.map = module_map
        self.universe = universe

    def module(self, old: str, ctx: Ctx, *, module_object: bool = False) -> str:
        if ctx.old_module is not None and old == ctx.old_module and ctx.self_new:
            return ctx.self_new
        if module_object and (ctx.pkg, old) in MODULE_OBJECT_OVERRIDES:
            return MODULE_OBJECT_OVERRIDES[(ctx.pkg, old)]
        if (ctx.pkg, old) in CTX_MODULE_OVERRIDES:
            return CTX_MODULE_OVERRIDES[(ctx.pkg, old)]
        if old in self.map:
            return self.map[old]
        raise RuleError(f"unmapped module {old!r}")

    def symbol(self, old_mod: str, name: str, ctx: Ctx) -> tuple[str, str]:
        if ctx.script_class and (ctx.script_class, old_mod, name) in SCRIPT_SYMBOL_OVERRIDES:
            return SCRIPT_SYMBOL_OVERRIDES[(ctx.script_class, old_mod, name)]
        move = SYMBOL_MOVES.get((old_mod, name))
        if move is None:
            return self.module(old_mod, ctx), name
        if isinstance(move, dict):
            if ctx.pkg not in move:
                raise RuleError(f"no host mapping for {old_mod}.{name} in context {ctx.pkg}")
            return move[ctx.pkg], name
        return move, name

    def string(self, value: str, ctx: Ctx) -> tuple[str, str] | None:
        """``(old substring, new substring)`` for a string module reference, else None.

        Dotted references are replaced whole; path references only in their ``mbnas_server/....py`` prefix (the rest of
        the literal, possibly an implicit concatenation over several source lines, is left as written).
        """
        if value in STRING_EXACT:
            return value, STRING_EXACT[value]
        m = RE_DOTTED.match(value)
        if m:
            parts, dot, attr = m.group(1).split("."), m.group(2), m.group(3) or ""
            for i in range(len(parts), 0, -1):
                cand = ".".join(parts[:i])
                if cand == OLD:
                    if i < len(parts):
                        raise RuleError(f"string {value!r}: no module {'.'.join(parts[:2])!r} in the snapshot")
                    return value, ctx.pkg + dot + attr
                if cand in self.universe:
                    return value, ".".join([self.module(cand, ctx, module_object=True), *parts[i:]]) + dot + attr
        m = RE_PATH.match(value)
        if m:
            dotted = m.group(1).replace("/", ".")
            if dotted not in self.universe:
                raise RuleError(f"string {value!r}: path does not name a snapshot module")
            return m.group(1) + ".py", self.module(dotted, ctx, module_object=True).replace(".", "/") + ".py"
        return None


# --------------------------------------------------------------------------------------------------------------------
# Python source rewriting
# --------------------------------------------------------------------------------------------------------------------
def _render_from(indent: str, mod: str, names: list[tuple[str, str | None]]) -> str:
    parts = [n if a is None else f"{n} as {a}" for n, a in names]
    one = f"from {mod} import {', '.join(parts)}"
    if len(indent) + len(one) <= MAX_LINE:
        return one
    lines, cur = [f"from {mod} import ("], indent + "    "
    for p in parts:
        if len(cur) + len(p) + 2 > MAX_LINE and cur.strip():
            lines.append(cur.rstrip())
            cur = indent + "    "
        cur += p + ", "
    lines.append(cur.rstrip().rstrip(",") + ",")
    lines.append(f"{indent})")
    return "\n".join(lines)


def _resolve_relative(old_module: str, is_pkg: bool, level: int, module: str | None) -> str:
    base = old_module.split(".") if is_pkg else old_module.split(".")[:-1]
    if level > 1:
        if level - 1 > len(base):
            raise RuleError(f"relative import beyond top level in {old_module}")
        base = base[: len(base) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def _doc_constant_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            ids.add(id(node.value))
    return ids


def rewrite_python(text: str, ctx: Ctx, mapper: Mapper, where: str) -> tuple[str, dict[str, Any]]:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln)

    def off(lineno: int, col: int) -> int:
        return starts[lineno - 1] + len(lines[lineno - 1].encode("utf-8")[:col].decode("utf-8"))

    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def is_lazy(node: ast.AST) -> bool:
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return True
            cur = parents.get(id(cur))
        return False

    edits: list[tuple[int, int, str]] = []
    imports, strings = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if ctx.old_module is None:
                    raise RuleError(f"{where}:{node.lineno}: relative import outside a package")
                old = _resolve_relative(ctx.old_module, ctx.is_pkg_init, node.level, node.module)
            else:
                old = node.module or ""
            if old.split(".")[0] != OLD:
                continue
            groups: dict[str, list[tuple[str, str | None]]] = {}
            for alias in node.names:
                if alias.name == "*":
                    raise RuleError(f"{where}:{node.lineno}: star import from {old}")
                full = f"{old}.{alias.name}"
                if full in mapper.universe:  # a submodule imported as a module object
                    new_full = mapper.module(full, ctx, module_object=True)
                    parent, _, leaf = new_full.rpartition(".")
                    if not parent:
                        raise RuleError(f"{where}:{node.lineno}: {full} maps to top-level {new_full}")
                    asname = alias.asname or (alias.name if leaf != alias.name else None)
                    groups.setdefault(parent, []).append((leaf, asname))
                else:
                    new_mod, new_name = mapper.symbol(old, alias.name, ctx)
                    if new_mod == ctx.self_new:
                        raise RuleError(f"{where}:{node.lineno}: {old}.{alias.name} would import from itself")
                    asname = alias.asname or (alias.name if new_name != alias.name else None)
                    groups.setdefault(new_mod, []).append((new_name, asname))
            start, end = off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset)
            seg = text[start:end]
            flat = [x for v in groups.values() for x in v]
            original = [(a.name, a.asname) for a in node.names]
            replacement = None
            if len(groups) == 1 and flat == original:
                m = RE_FROM_HEAD.match(seg)
                if m:
                    replacement = seg[: m.start(2)] + next(iter(groups)) + seg[m.end(2):]
            if replacement is None:
                indent_src = text[starts[node.lineno - 1]: start]
                joiner = ("\n" + indent_src) if not indent_src.strip() else "; "
                indent = indent_src if not indent_src.strip() else ""
                replacement = joiner.join(_render_from(indent, mod, names) for mod, names in groups.items())
            if replacement != seg:
                edits.append((start, end, replacement))
            imports.append({"line": node.lineno, "lazy": is_lazy(node), "from": "." * node.level + (node.module or ""),
                            "resolved": old, "to": {mod: [n if a is None else f"{n} as {a}" for n, a in names]
                                                    for mod, names in groups.items()}})
        elif isinstance(node, ast.Import):
            if not any(a.name.split(".")[0] == OLD for a in node.names):
                continue
            parts = []
            for alias in node.names:
                if alias.name.split(".")[0] != OLD:
                    parts.append(alias.name if alias.asname is None else f"{alias.name} as {alias.asname}")
                    continue
                if alias.asname is None:
                    raise RuleError(f"{where}:{node.lineno}: `import {alias.name}` without alias binds the old name")
                new = mapper.module(alias.name, ctx, module_object=True)
                parts.append(f"{new} as {alias.asname}")
                imports.append({"line": node.lineno, "lazy": is_lazy(node), "from": alias.name, "resolved": alias.name,
                                "to": {new: [f"as {alias.asname}"]}})
            start, end = off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset)
            edits.append((start, end, "import " + ", ".join(parts)))

    docs = _doc_constant_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            seg_start, seg_end = off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset)
            seg, changed = text[seg_start:seg_end], False
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    sub = mapper.string(part.value, ctx)
                    if sub is not None and sub[0] != sub[1]:
                        if seg.count(sub[0]) != 1:
                            raise RuleError(f"{where}:{node.lineno}: f-string part {sub[0]!r} not unique")
                        seg, changed = seg.replace(sub[0], sub[1]), True
                        strings.append({"line": node.lineno, "old": sub[0], "new": sub[1], "fstring": True})
            if changed:
                edits.append((seg_start, seg_end, seg))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if isinstance(parents.get(id(node)), ast.JoinedStr):
                continue
            sub = mapper.string(node.value, ctx)
            if sub is None or sub[0] == sub[1]:
                continue
            s, e = off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset)
            seg = text[s:e]
            if seg.count(sub[0]) != 1:
                raise RuleError(f"{where}:{node.lineno}: string {sub[0]!r} not found verbatim once")
            edits.append((s, e, seg.replace(sub[0], sub[1])))
            strings.append({"line": node.lineno, "old": sub[0], "new": sub[1]})

    edits.sort(key=lambda t: t[0], reverse=True)
    for (s1, e1, _), (s2, e2, _) in zip(edits, edits[1:]):
        if e2 > s1:
            raise RuleError(f"{where}: overlapping edits at offsets {s2}-{e2} / {s1}-{e1}")
    out = text
    for s, e, rep in edits:
        out = out[:s] + rep + out[e:]
    try:
        compile(out, where, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise RuleError(f"{where}: rewritten source does not compile: {exc}") from exc
    return out, {"imports": imports, "strings": strings}


def postcheck(text: str, where: str, *, package_file: bool, known_server: set[str]) -> list[str]:
    """Remaining relative imports / old-layout references after rewriting (must be empty)."""
    problems, tree = [], ast.parse(text)
    docs = _doc_constant_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and package_file:
            problems.append(f"{where}:{node.lineno}: relative import remains")
        mods = []
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for mod in mods:
            if mod.startswith(OLD + ".") and not any(mod == k or mod.startswith(k + ".") for k in known_server) \
                    and mod.rsplit(".", 1)[0] not in known_server:
                problems.append(f"{where}:{node.lineno}: old-layout import {mod}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            m = RE_DOTTED.match(node.value)
            if m and m.group(1) != OLD and not any(m.group(1) == k or m.group(1).startswith(k + ".")
                                                   for k in known_server):
                problems.append(f"{where}:{node.lineno}: old-layout string {node.value!r}")
    return problems


# --------------------------------------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------------------------------------
def load_provenance(prov_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    files = [prov_dir / "SOURCE_SNAPSHOT_MANIFEST.jsonl", *sorted(prov_dir.glob("SOURCE_SNAPSHOT_DELTA_*.jsonl"))]
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                records[rec["source_relpath"]] = rec  # deltas are read last and supersede
    return records


def target_file(module: str, packages: set[str]) -> str:
    top = module.split(".")[0]
    rel = module.replace(".", "/")
    return f"{top}/src/{rel}/__init__.py" if module in packages else f"{top}/src/{rel}.py"


class Builder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snap = root / "build" / "source_snapshot"
        self.prov = load_provenance(root / "docs" / "internal" / "provenance")
        entries = json.loads((root / "docs" / "internal" / "analysis" / "MODULE_MAP.json").read_text(encoding="utf-8"))
        self.src_entries = [e for e in entries if e["source_path"].startswith(SRC_PREFIX)
                            and e["source_path"].endswith(".py")]
        self.module_map = {e["source_module"]: MODULE_OVERRIDES.get(e["source_module"], e["target_module_path"])
                           for e in self.src_entries}
        for old, targets in EXTRA_TARGETS.items():
            self.module_map[old] = targets[0]
        self.universe = set(self.module_map)
        self.mapper = Mapper(self.module_map, self.universe)
        self.outputs: list[Output] = []
        self.deleted: list[dict[str, Any]] = []

    def read_source(self, rel: str) -> bytes:
        path = self.snap / rel
        data = path.read_bytes()
        rec = self.prov.get(rel)
        if rec is None:
            raise RuleError(f"{rel}: not recorded in the snapshot provenance manifest")
        if sha256(data) != rec["sha256"]:
            raise RuleError(f"{rel}: sha256 {sha256(data)} != provenance {rec['sha256']}")
        return data

    def mode_of(self, rel: str) -> int:
        return 0o755 if os.stat(self.snap / rel).st_mode & 0o111 else 0o644

    def build(self) -> None:
        placements: list[tuple[dict[str, Any], str]] = []
        for e in self.src_entries:
            for tgt in EXTRA_TARGETS.get(e["source_module"], [self.module_map[e["source_module"]]]):
                placements.append((e, tgt))
        packages = {tgt for e, tgt in placements if e["source_path"].endswith("/__init__.py")} | FORCED_PACKAGES
        all_targets = {tgt for _, tgt in placements}
        for tgt in all_targets:  # every parent of a target is a package
            parts = tgt.split(".")
            packages.update(".".join(parts[:i]) for i in range(1, len(parts)))
        clash = sorted({tgt for e, tgt in placements if tgt in packages and tgt not in FORCED_PACKAGES
                        and not e["source_path"].endswith("/__init__.py")})
        if clash:
            raise RuleError(f"module(s) placed as plain files but needed as packages: {clash}")
        self.packages = packages
        self.known_server = {t for t in all_targets | packages if t.split(".")[0] == "mbnas_server"} | {
            "mbnas_server.api.contract_check", "mbnas_server.api.errors", "mbnas_server.api.deps",
            "mbnas_server.config"}  # gateway modules created by WP-1F (contract §4/§5)

        for e, tgt in placements:
            rel = e["source_path"]
            old = e["source_module"]
            data = self.read_source(rel)
            ctx = Ctx(pkg=tgt.split(".")[0], script_class=None, old_module=old,
                      is_pkg_init=rel.endswith("/__init__.py"), self_new=tgt)
            target = target_file(tgt, packages)
            text, changes = rewrite_python(data.decode("utf-8"), ctx, self.mapper, target)
            problems = postcheck(text, target, package_file=True, known_server=self.known_server)
            if problems:
                raise RuleError("; ".join(problems))
            multi = old in EXTRA_TARGETS
            split = bool(e["split_needed"]) or multi
            whole_core = old in ("mbnas_server.api.routes_files", "mbnas_server.api.views",
                                 "mbnas_server.api.candidate_views", "mbnas_server.artifacts.registry")
            kind = "moved" if whole_core or not split else "split"
            note = TARGET_NOTES.get(old, {}).get(tgt) or SPLIT_NOTES.get(old, "")
            if not split and not whole_core:
                note = "module move (MODULE_MAP target_module_path)" if tgt == e["target_module_path"] else note
            self.outputs.append(Output(target=target, data=text.encode("utf-8"), mode=self.mode_of(rel), kind=kind,
                                       owner_wp=OWNER_WP[tgt.split(".")[0]], source=rel,
                                       notes=self._note(note, changes), changes=changes))

        placed = {o.target for o in self.outputs}
        for pkg in sorted(packages):
            init = target_file(pkg, packages)
            if init in placed:
                continue
            body = ROOT_DOCSTRINGS.get(pkg, "")
            self.outputs.append(Output(target=init, data=body.encode("utf-8"), mode=0o644, kind="new",
                                       owner_wp=OWNER_WP[pkg.split(".")[0]],
                                       notes=("package root docstring (the snapshot root __init__ is placed in "
                                              "mbnas_core per MODULE_MAP)" if body else
                                              "empty package marker: no snapshot __init__ maps to this directory")))

        for name, target, cls in SCRIPTS:
            rel = f"{SERVER}/scripts/{name}"
            text = self.read_source(rel).decode("utf-8")
            patches = []
            for old_text, new_text, count, reason in SCRIPT_PATCHES.get(name, []):
                found = text.count(old_text)
                if found != count:
                    raise RuleError(f"{rel}: patch anchor found {found}x, expected {count}x: {old_text!r}")
                text = text.replace(old_text, new_text)
                patches.append({"anchor": old_text.strip()[:80], "count": count, "reason": reason})
            ctx = Ctx(pkg=SCRIPT_CTX_PKG[cls], script_class=cls, old_module=None, is_pkg_init=False, self_new=None)
            text, changes = rewrite_python(text, ctx, self.mapper, target)
            problems = postcheck(text, target, package_file=False, known_server=self.known_server)
            if problems:
                raise RuleError("; ".join(problems))
            changes["patches"] = patches
            self.outputs.append(Output(target=target, data=text.encode("utf-8"), mode=self.mode_of(rel), kind="moved",
                                       owner_wp="WP-1A", source=rel,
                                       notes=self._note(f"{cls} script (contract §11): {SCRIPT_NOTES[name]}", changes),
                                       changes=changes))

        for rel, target, kind, owner, note in DATA:
            self.outputs.append(Output(target=target, data=self.read_source(rel), mode=self.mode_of(rel), kind=kind,
                                       owner_wp=owner, source=rel, notes=note))
        self._history()
        self._reference()
        for rel, note in DELETED:
            data = self.read_source(rel)
            self.deleted.append({"source_relpath": rel, "source_sha256": sha256(data), "target_relpath": None,
                                 "target_sha256": None, "kind": "deleted", "owner_wp": "WP-1A", "notes": note})

        seen: dict[str, Output] = {}
        for o in self.outputs:
            if o.target in seen:
                raise RuleError(f"two placements write {o.target}")
            seen[o.target] = o

    @staticmethod
    def _note(note: str, changes: dict[str, Any]) -> str:
        n_imp = len(changes.get("imports", []))
        n_str = len(changes.get("strings", []))
        n_pat = len(changes.get("patches", []))
        stats = f"imports rewritten {n_imp}, string refs rewritten {n_str}" + (f", patches {n_pat}" if n_pat else "")
        return f"{note}; {stats}" if note else stats

    def _history(self) -> None:
        hist_dir = self.snap / HISTORY_SRC
        index = json.loads(self.read_source(f"{HISTORY_SRC}/HISTORY.json"))
        recorded = {v["file"]: v["sha256"] for v in index["versions"]}
        names = sorted(p.name for p in hist_dir.iterdir() if p.is_file())
        for name in names:
            rel = f"{HISTORY_SRC}/{name}"
            data = self.read_source(rel)
            if name in recorded and sha256(data) != recorded[name]:
                raise RuleError(f"{rel}: sha256 differs from HISTORY.json")
            self.outputs.append(Output(target=f"{HISTORY_DST}/{name}", data=data, mode=self.mode_of(rel), kind="moved",
                                       owner_wp="WP-1A", source=rel,
                                       notes="contract history outside the wheel (DD-A10); public-ZIP host-fact "
                                             "review in WP-7"))
        for name, sha in recorded.items():
            if name in names:
                continue
            rel = f"{SERVER}/contracts/openapi.json"
            data = self.read_source(rel)
            if sha256(data) != sha:
                raise RuleError(f"history file {name} is missing from the snapshot and contracts/openapi.json does "
                                f"not have its recorded sha256 {sha}")
            self.outputs.append(Output(target=f"{HISTORY_DST}/{name}", data=data, mode=0o644, kind="moved",
                                       owner_wp="WP-1A", source=rel,
                                       notes=f"{name} is listed in HISTORY.json but absent from the source snapshot "
                                             f"(the delta copied HISTORY.json only); bytes = contracts/openapi.json, "
                                             f"whose sha256 equals the recorded {sha[:12]}… (verified)"))

    def _reference(self) -> None:
        rows = []
        for name in ETRI_ALL:
            rel = f"yolo_ga_runtime/{name}"
            data = self.read_source(rel)
            rec = self.prov[rel]
            rows.append((name, len(data), sha256(data), rec.get("git_blob_sha1", "")))
            if name in REFERENCE_FILES:
                self.outputs.append(Output(target=f"{REFERENCE_DIR}/{name}", data=data, mode=self.mode_of(rel),
                                           kind="reference_copy", owner_wp="WP-1A", source=rel,
                                           notes="owner-only reference copy (contract §6.9); not imported; excluded "
                                                 "from public ZIPs"))
        self.outputs.append(Output(target=f"{REFERENCE_DIR}/README.md", data=self._readme(rows).encode("utf-8"),
                                   mode=0o644, kind="new", owner_wp="WP-1A",
                                   notes="sha256 table + reasons for not porting (contract §6.9), generated"))

    @staticmethod
    def _readme(rows: list[tuple[str, int, str, str]]) -> str:
        role = {
            "mixed_blocks.py": "reference copy here; vendored whole by WP-1C as "
                               "`mbnas_model_runtime/operators/etri_mixed_blocks.py` (exec-free parse_model, §6.1-6.2)",
            "mixed_search_space.py": "reference copy here; L12-17, 25-62, 154-203 vendored by WP-1D as "
                                     "`mbnas_core/search/mixed_pool/seed_variants.py` (§6.4)",
            "mixed_hooks.py": "reference copy here; only the effective REPEAT_MODULES value (L14, L70) is used, as a "
                              "literal in `mbnas_core/search/mixed_pool/runtime_constants.py` (§6.4)",
            "runtime.py": "reference copy here; only REPEAT_MODULES (L60-63) is used (literal, see above)",
        }
        out = [
            "# yolo_ga_runtime — owner-only reference copies (not executed, excluded from public ZIPs)",
            "",
            "Generated by `scripts/dev/rewrite_imports.py` (WP-1A) from `build/source_snapshot/yolo_ga_runtime/` "
            "(etri_nas HEAD 18c4815; the directory is byte-identical to commit d5028e8, ETRI_RUNTIME_SCOPE §7). "
            "License: no LICENSE file in the etri_nas root; ETRI_NAS research code, rights review pending (DD-A9).",
            "",
            "Nothing imports these files. The server path runs only the vendored Option A subset "
            "(DD-A12, WP-1 contract §6); the copies are kept for provenance and for a possible later port of the "
            "ETRI mutation operator (\"Option B\", ETRI_RUNTIME_SCOPE §5.3).",
            "",
            "| file | bytes | sha256 | git blob sha1 | status |",
            "|---|---:|---|---|---|",
        ]
        for name, size, sha, blob in rows:
            status = role.get(name, "not copied, not vendored (sha256 only)")
            out.append(f"| `{name}` | {size} | `{sha}` | `{blob}` | {status} |")
        out += [
            "",
            "## Not ported, and why",
            "",
            "- `mixed_search_space.mutate_operators`, `_legal_choices`, `_input_shape` (L65-151) and "
            "`runtime.symbolic_shape_propagate` with its helpers (L85-130, L290-472): no caller anywhere in the "
            "server code (ETRI_RUNTIME_SCOPE §2.2); `_legal_choices` is already reimplemented as "
            "`SearchSpace.illegal_reason` (§2.3).",
            "- `mixed_hooks.install_runtime_hooks` (process-wide monkeypatches of runtime/crossover classes): the server "
            "reads only the effective `REPEAT_MODULES` set (17 names, ETRI_RUNTIME_SCOPE §3.3), which is ported as a "
            "literal.",
            "- `__init__.py` (installs the hooks at import time), `crossover_v22.py`, `pipeline.py`, `ga_hooks.py`, "
            "`mixed_cli.py`, the rest of `runtime.py` and the `.bak` file: not used by the server; sha256 only.",
            "- `mixed_search_space.mixed_enabled` (`ETRI_NAS_MIXED` gate) and the `ETRI_NAS_MIXED_IMAGE_SIZE` / "
            "`ETRI_NAS_OPERATOR_RATE` environment variables: not read on the server path (ETRI_RUNTIME_SCOPE §8); the "
            "vendored code has no environment gate.",
            "",
            "## `mbnas-mixed-space/2` inputs (vendor_origin)",
            "",
            "The old bridge loaded these 8 modules and hashed their content into `space_hash`/`prior_hash` "
            "(ETRI_RUNTIME_SCOPE §3.1, §6.1); `/3` hashes the three vendored files instead (contract §6.5, "
            "`docs/internal/provenance/MIXED_SPACE_HASH_MAP.json` by WP-1V):",
            "",
        ]
        by_name = {r[0]: r for r in rows}
        for name in ETRI_V2_INPUTS:
            out.append(f"- `yolo_ga_runtime/{name}`: `{by_name[name][2]}`")
        out.append("")
        return "\n".join(out)


# --------------------------------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------------------------------
def manifest_rows(builder: Builder, *, missing_ok: bool = False) -> list[dict[str, Any]]:
    rows = []
    for o in builder.outputs:
        rows.append({"source_relpath": o.source, "source_sha256": builder.prov[o.source]["sha256"] if o.source else None,
                     "target_relpath": o.target, "target_sha256": sha256(o.data), "kind": o.kind,
                     "owner_wp": o.owner_wp, "notes": o.notes})
    for rel, owner, note in NEW_HANDWRITTEN:
        path = builder.root / rel
        if not path.is_file():
            if missing_ok:
                continue
            raise RuleError(f"hand-written file {rel} is missing")
        rows.append({"source_relpath": None, "source_sha256": None, "target_relpath": rel,
                     "target_sha256": sha256(path.read_bytes()), "kind": "new", "owner_wp": owner, "notes": note})
    rows += builder.deleted
    rows.sort(key=lambda r: (r["target_relpath"] is None, r["target_relpath"] or "", r["source_relpath"] or ""))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]), help="TARGET_ROOT (default: %(default)s)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if any target differs")
    ap.add_argument("--force", action="store_true", help="overwrite targets whose bytes differ (owner edits are lost)")
    ap.add_argument("--report", default=None, help="write the per-file rewrite report (JSON) here")
    ap.add_argument("--manifest", default="docs/internal/provenance/PLACEMENT_MANIFEST.jsonl",
                    help="placement manifest path relative to --root (default: %(default)s)")
    ap.add_argument("--preview", default=None,
                    help="write every output (and the manifest) under this directory instead of --root (inspection)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        builder = Builder(root)
        builder.build()
        rows = manifest_rows(builder, missing_ok=bool(args.check or args.preview))
    except RuleError as exc:
        print(f"RULE VIOLATION: {exc}", file=sys.stderr)
        return 2
    differing, new = [], []
    for o in builder.outputs:
        path = root / o.target
        if not path.exists():
            new.append(o.target)
        elif path.read_bytes() != o.data:
            differing.append(o.target)
    summary = {"tool": TOOL, "root": str(root), "outputs": len(builder.outputs), "new": len(new),
               "differing": differing, "unchanged": len(builder.outputs) - len(new) - len(differing),
               "manifest_rows": len(rows), "kinds": {}}
    for r in rows:
        summary["kinds"][r["kind"]] = summary["kinds"].get(r["kind"], 0) + 1
    report = {"summary": summary, "files": {o.target: {"source": o.source, "kind": o.kind, **o.changes}
                                            for o in builder.outputs if o.changes}}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.check:
        print(json.dumps(summary, indent=1))
        return 1 if (differing or new) else 0
    if args.preview:
        out_root = Path(args.preview).resolve()
        for o in builder.outputs:
            path = out_root / o.target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(o.data)
            os.chmod(path, o.mode)
        (out_root / args.manifest).parent.mkdir(parents=True, exist_ok=True)
        (out_root / args.manifest).write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        print(json.dumps(summary, indent=1))
        return 0
    if differing and not args.force:
        print(json.dumps(summary, indent=1))
        print(f"REFUSED: {len(differing)} existing target(s) differ; rerun with --force to overwrite", file=sys.stderr)
        return 1
    for o in builder.outputs:
        path = root / o.target
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != o.data:
            path.write_bytes(o.data)
        os.chmod(path, o.mode)
    manifest = root / args.manifest
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                        encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
