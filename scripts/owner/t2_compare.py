#!/usr/bin/env python3
"""T2 core equivalence: run every scenario on the ORIGINAL monolith and on the split packages, compare, report.

Owner only (docs/internal/WORK_PACKAGES/T2_REFERENCE_PLAN.md). For each scenario T2-1..T2-10:

1. the fixed inputs are written to ``<out>/inputs/<scenario>.input.json`` (canonical JSON, sha256 recorded);
2. the work directory ``<work-root>/<scenario>`` is wiped and ``tests/owner/t2/reference_runner.py`` runs the scenario
   on the snapshot code (``build/source_snapshot``) -> ``<out>/<scenario>.ref.json``;
3. the work directory is wiped again (same absolute path, so paths inside both outputs are identical) and
   ``tests/owner/t2/new_runner.py`` runs it on ``mbnas_core`` + ``mbnas_model_runtime`` -> ``<out>/<scenario>.new.json``
   (two-phase scenarios read inputs the reference produced, e.g. the T2-3 parent-pool genotypes or the resolved
   protocol of T2-7/T2-8/T2-10);
4. the two ``result`` trees are compared: ``EXACT_MATCH`` (identical), ``ALLOWED_DIFF`` (every difference is covered by
   a documented allowed-difference rule; each path is listed) or ``MISMATCH`` (anything else, with the differing
   values). A runner failure or an isolation violation is ``RUNNER_ERROR``.

The snapshot tree (every file, directory and symlink, including any ``__pycache__``) is hashed before and after the
whole run; any change makes the script fail loudly (exit 4). Both runners use the same interpreter (``--python``,
default this one), CPU only (``CUDA_VISIBLE_DEVICES=""``), ``PYTHONDONTWRITEBYTECODE=1``, ``PYTHONHASHSEED=0`` and a
HOME/TMPDIR/cache tree inside the work directory.

Usage: t2_compare.py [--scenario T2-1 ...] [--out-dir DIR] [--work-root DIR] [--keep-work] [--compare-only]
Exit: 0 all EXACT_MATCH/ALLOWED_DIFF, 1 any MISMATCH or RUNNER_ERROR, 4 snapshot changed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
T2_DIR = REPO / "tests" / "owner" / "t2"
DEFAULT_OUT = REPO / "docs" / "internal" / "evidence" / "T2"
DEFAULT_SNAPSHOT = REPO / "build" / "source_snapshot"
MISSING = "<absent>"
MAX_VALUE_CHARS = 1500


def _load_core() -> Any:
    spec = importlib.util.spec_from_file_location("t2_core", T2_DIR / "t2_core.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t2_core"] = mod
    spec.loader.exec_module(mod)
    return mod


CORE = _load_core()


# == allowed-difference rules ========================================================================================
class Rule:
    """``kind``: ``record`` (any value, also absent: provenance recorded, never compared), ``expect`` (exactly the
    documented ref -> new pair), ``timestamp`` (both ISO-8601 instants), ``timing`` (both numbers measured from the
    wall clock). ``patterns`` are dotted paths over the NORMALISED keys, ``*`` = one token, ``**`` = any tokens."""

    def __init__(self, name: str, kind: str, patterns: list[str], basis: str, *, ref: Any = None, new: Any = None,
                 ancestor: tuple[int, str, Any] | None = None):
        self.name, self.kind, self.basis, self.ref, self.new = name, kind, basis, ref, new
        self.patterns = [tuple(p.split(".")) for p in patterns]
        self.raw_patterns = patterns
        self.ancestor = ancestor  # (levels up from the differing node, key, required value) on BOTH sides

    def matches(self, path: tuple[Any, ...], parents: tuple[tuple[Any, Any], ...] = ()) -> bool:
        if not any(_glob(p, tuple(str(t) for t in path)) for p in self.patterns):
            return False
        if self.ancestor is None:
            return True
        level, key, value = self.ancestor
        if len(parents) < level:
            return False
        pa, pb = parents[-level]
        return isinstance(pa, dict) and isinstance(pb, dict) and pa.get(key) == value and pb.get(key) == value


def _glob(pat: tuple[str, ...], path: tuple[str, ...]) -> bool:
    memo: dict[tuple[int, int], bool] = {}

    def m(i: int, j: int) -> bool:
        key = (i, j)
        if key in memo:
            return memo[key]
        if i == len(pat):
            res = j == len(path)
        elif pat[i] == "**":
            res = any(m(i + 1, k) for k in range(j, len(path) + 1))
        elif j == len(path):
            res = False
        else:
            res = (pat[i] == "*" or pat[i] == path[j]) and m(i + 1, j + 1)
        memo[key] = res
        return res

    return m(0, 0)


ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
HASH_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
ID_RE = re.compile(r"\b(?:job|run|val|train|model)-[0-9a-f]{12,16}\b|\bck-[0-9a-f]{16}\b")
TASK = "task brief (known allowed differences)"
PLAN = "T2_REFERENCE_PLAN.md section 2 (allowed-difference column)"

GLOBAL_RULES = [
    Rule("timestamp", "timestamp", ["**"], f"{TASK}: timestamps; {PLAN} T2-7 '시각'"),
    Rule("parser_source_sha256", "record", ["**.parser_source_sha256"], f"{TASK}; {PLAN} T2-1"),
    Rule("catalog.source_path", "record", ["**.catalog.source_path"], f"{TASK}; {PLAN} T2-1"),
    Rule("search_space_version", "expect", ["**.version"], f"{TASK}: search_space /2 -> /3 (compare version only); "
         "DD-A12 hash definition mbnas-mixed-space/3", ref="mbnas-mixed-space/2", new="mbnas-mixed-space/3"),
    Rule("search_space_hash", "record", ["**.space_hash", "**.prior_hash", "**.space_hash_definition"],
         f"{TASK}: record hashes only; {PLAN} T2-2 lineage.search_space.{{space_hash,prior_hash}}"),
    Rule("mixed_pool_provenance", "record", ["**.etri", "**.source.<POOL_FILES_SHA256>"],
         "DD-A12: the /2 identity hashed the executed ETRI yolo_ga_runtime files, /3 hashes the three vendored files "
         f"(provenance of the search-space describe(); {PLAN} T2-2 'origin kind 문자열')"),
]
TIMING_PATTERNS = ["**.gpu_seconds", "**.costs.*", "**.cost.*", "**.admission.billed_s", "**.admission.reserved_s",
                   "**.admission.remaining_s", "**.admission.estimate_s", "**.meta.attempt_gpu_seconds",
                   "**.meta.inline_profile_gpu_seconds", "**.cpu_seconds", "**.timings.*"]
TIMING = Rule("wall_clock_duration", "timing", TIMING_PATTERNS,
              f"{PLAN} T2-7 '시각' (GPU-seconds / CPU-seconds measured from the wall clock of each attempt; the "
              "decision fields admitted/reason_code/estimate_source/cap_s stay strict)")
WORKER = Rule("worker_telemetry", "record", ["**.worker_events"],
              f"{PLAN} T2-7/T2-10 worker_id; worker.updated rows carry worker id, pid and device samples "
              "(DD-A27: the owner worker no longer samples NVML)")
WORKER_DEVICE = Rule("worker_device_identity", "record", ["**.costs.*.device_uuid"],
                     f"{PLAN} T2-7/T2-10 worker_id: the ledger row stores the executing worker's device identity "
                     "(original: NVML uuid, None without NVML; new owner worker: synthetic 'gpu-index-N', DD-A27; "
                     "DD-A19 runner-issued ids); stored only, never read by a decision")
RESOLUTION = [
    Rule("etri_root_resolution", "record", ["**.search_space_resolved.etri_root",
                                            "validation.capabilities.search_space.etri_root",
                                            "validation.capabilities.search_space.detail",
                                            "validation.capabilities.search_space.probe.checked"],
         "DD-A12: search_space_resolved.etri_root is null for the vendored pool; probe/detail text names the pool"),
    Rule("etri_root_resolution", "record", ["validation.resolved_diff.*.reason"],
         "DD-A12 (resolved_diff entry of console.search.search_space_resolved only)",
         ancestor=(1, "field", "console.search.search_space_resolved")),
    Rule("etri_root_resolution", "record", ["validation.resolved_diff.*.resolved.etri_root"],
         "DD-A12 (resolved_diff entry of console.search.search_space_resolved only)",
         ancestor=(2, "field", "console.search.search_space_resolved")),
    Rule("own_resolution_hash", "record", ["validation.protocol_hash"],
         "derived: hash of each side's own resolution, which differs only by the etri_root rule above (the engine "
         "itself runs on the reference resolution on both sides and its protocol_hash is compared strictly)"),
]
# DD-A53 (c): contract 1.5.0 adds resolution.paths to RESOLUTION_HASH_EXCLUDED and no longer records realpath /
# etri_root, so the protocol_hash DEFINITION changed. Only differences derived from that are allowed, each strictly:
# * "phash": a protocol hash value (both sides full sha256 hashes) under ONE consistent one-to-one ref -> new mapping
#   over the whole document (a ref hash mapped to two new hashes, or two ref hashes to one, is a MISMATCH);
# * "phash_file": the sha256 of a checkpoint file that embeds the protocol hash, only when the same checkpoint row's
#   hashes.protocol differs by exactly that mapping;
# * "realpath_dropped": resolution.paths.<key>.realpath present on the reference side and absent on the new side;
# * "hash_excluded_extended": the new hash_excluded list is the reference list plus "resolution.paths".
# Every other difference (any other field, any other value) stays a MISMATCH.
PHASH_BASIS = ("DD-A53 (c): RESOLUTION_HASH_EXCLUDED += resolution.paths and no realpath/etri_root recorded (contract "
               "1.5.0) -> protocol_hash definition changed; derived differences only")
PROTOCOL_HASH = [
    Rule("protocol_hash_definition", "phash", ["protocol_hash", "**.protocol_hash", "**.hashes.protocol"], PHASH_BASIS),
    Rule("protocol_hash_embedding_file", "phash_file", ["**.checkpoints.*.sha256"],
         PHASH_BASIS + "; the stub checkpoint file embeds the protocol hash (allowed only when the row's "
         "hashes.protocol differs by the same mapping)"),
    Rule("realpath_dropped", "absent_new", ["validation.resolved.resolution.paths.*.realpath"], PHASH_BASIS),
    Rule("hash_excluded_extended", "list_plus", ["validation.resolved.resolution.hash_excluded"], PHASH_BASIS,
         new=["resolution.paths"]),
]
SCENARIO_RULES: dict[str, list[Rule]] = {
    "T2-2": [Rule("preset_paths", "record", ["presets.*.console.paths"],
                  f"{TASK}: paths; DD-A28 preset_paths() placeholders")],
    "T2-7": [TIMING, WORKER, WORKER_DEVICE, *RESOLUTION, *PROTOCOL_HASH],
    "T2-8": [TIMING, WORKER, WORKER_DEVICE, *RESOLUTION, *PROTOCOL_HASH],
    "T2-10": [TIMING, WORKER, WORKER_DEVICE, *RESOLUTION, *PROTOCOL_HASH],
}

# module path strings that moved with the split (ref string, new string) -> one token each (task: module path strings).
# The original monolith's names are composed (``ORIG``) so the import-closure checker does not read these data strings
# as references into the new gateway package (same convention as tests/public/infra/test_no_host_facts.py).
ORIG = "mbnas_" + "server"
MODULE_SUBS: list[tuple[str, str, str]] = [
    ("yolo_ga_runtime/mixed_blocks.py",
     "mbnas_model_runtime/operators/etri_mixed_blocks.py (vendored from yolo_ga_runtime/mixed_blocks.py sha256 "
     "4233dd32\u2026)", "<ETRI_OPS_SOURCE>"),
    (f"{ORIG}/adapters/etri_hybrid/model.py", "mbnas_model_runtime/adapters/etri_hybrid/model.py",
     "<ETRI_HYBRID_SOURCE>"),
    (f"{ORIG}.search.etri_bridge:ensure_mixed_registered", "mbnas_model_runtime.operators:ensure_registered",
     "<MIXED_REGISTER_HOOK>"),
    ("mixed_search_space.make_mixed_seed_variants",
     "mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants", "<SEED_VARIANTS_FN>"),
    ("etri_mixed_seed_variant", "vendored_mixed_seed_variant", "<SEED_VARIANT_KIND>"),
    ("etri_files_sha256", "mixed_pool_files_sha256", "<POOL_FILES_SHA256>"),
    ("yolo_ga_runtime.mixed_blocks", "mbnas_model_runtime.operators.etri_mixed_blocks", "<ETRI_OPS_MODULE>"),
    (f"{ORIG}.adapters.etri_hybrid.model", "mbnas_model_runtime.adapters.etri_hybrid.model", "<ETRI_HYBRID_MODULE>"),
]


class Normaliser:
    """Per-side string normalisation: module path strings, absolute roots, generated ids, raw architecture hashes."""

    def __init__(self, doc: dict[str, Any], side: str) -> None:
        meta = doc.get("meta") or {}
        self.side = side
        self.hash_map: dict[str, str] = meta.get("arch_hash_map") or {}
        roots = [(meta.get("work_root"), "<WORK>"), (meta.get("snapshot_root"), "<SNAPSHOT>"),
                 (meta.get("repo_root"), "<REPO>")]
        self.roots = sorted(((r, t) for r, t in roots if r), key=lambda x: -len(x[0]))
        subs = [(ref if side == "ref" else new, token) for ref, new, token in MODULE_SUBS]
        self.subs = sorted(subs, key=lambda x: -len(x[0]))
        self.ids: dict[str, str] = {}
        counters: Counter = Counter()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for k in sorted(value, key=str):
                    collect(k)
                    collect(value[k])
            elif isinstance(value, list):
                for v in value:
                    collect(v)
            elif isinstance(value, str):
                for m in ID_RE.finditer(value):
                    tok = m.group(0)
                    if tok not in self.ids:
                        prefix = tok.split("-", 1)[0]
                        counters[prefix] += 1
                        self.ids[tok] = f"<{prefix}#{counters[prefix]}>"

        collect(doc.get("result"))

    def norm(self, s: str) -> tuple[str, set[str]]:
        rules: set[str] = set()
        out = s
        for raw, token in self.subs:
            if raw in out:
                out = out.replace(raw, token)
                rules.add("module_path_string")
        for raw, token in self.roots:
            if raw in out:
                out = out.replace(raw, token)
                rules.add("path")
        if ID_RE.search(out):
            new = ID_RE.sub(lambda m: self.ids.get(m.group(0), m.group(0)), out)
            if new != out:
                rules.add("generated_id")
                out = new
        if self.hash_map and HASH_RE.search(out):
            def sub(m: re.Match) -> str:
                tok = m.group(0)
                for cand in (tok, "sha256:" + tok if not tok.startswith("sha256:") else tok[7:]):
                    if cand in self.hash_map:
                        norm = self.hash_map[cand]
                        if norm != cand:
                            rules.add("module_path:structural_hash")
                        return f"<arch:{norm}>"
                return tok
            out = HASH_RE.sub(sub, out)
        return out, rules

    def key(self, k: str) -> str:
        return self.norm(k)[0]


def _short(value: Any) -> Any:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value if len(text) <= MAX_VALUE_CHARS else {"$truncated": text[:MAX_VALUE_CHARS], "chars": len(text)}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(path: tuple[Any, ...]) -> str:
    out = ""
    for t in path:
        out += f"[{t}]" if isinstance(t, int) else (("." if out else "") + str(t))
    return out


def _pattern(path: tuple[Any, ...]) -> str:
    return _fmt(tuple("*" if isinstance(t, int) else t for t in path)).replace("[*]", "[*]")


def _rule_table() -> dict[str, Any]:
    table: dict[str, Any] = {}
    seen: set[int] = set()
    for scope, rs in [("all", GLOBAL_RULES), *SCENARIO_RULES.items()]:
        for r in rs:
            entry = table.setdefault(r.name, {"kind": r.kind, "patterns": [], "basis": r.basis, "scenarios": []})
            if id(r) not in seen:
                seen.add(id(r))
                cond = f" (when {r.ancestor[1]} == {r.ancestor[2]!r}, {r.ancestor[0]} level(s) up)" if r.ancestor else ""
                entry["patterns"] += [p + cond for p in r.raw_patterns]
            if scope not in entry["scenarios"]:
                entry["scenarios"].append(scope)
    return table


def compare_docs(ref_doc: dict[str, Any], new_doc: dict[str, Any], scenario: str) -> dict[str, Any]:
    nr, nn = Normaliser(ref_doc, "ref"), Normaliser(new_doc, "new")
    rules = GLOBAL_RULES + SCENARIO_RULES.get(scenario, [])
    allowed: list[dict[str, Any]] = []
    mism: list[dict[str, Any]] = []
    fwd: dict[str, str] = {}  # protocol hash ref -> new (one-to-one, DD-A53 c)
    back: dict[str, str] = {}

    def full_hash(v: Any) -> bool:
        return isinstance(v, str) and HASH_RE.fullmatch(v) is not None

    def phash_ok(a: Any, b: Any) -> bool:
        if not (full_hash(a) and full_hash(b)) or fwd.get(a, b) != b or back.get(b, a) != a:
            return False
        fwd[a], back[b] = b, a
        return True

    def rule_for(path: tuple[Any, ...], a: Any, b: Any, parents: tuple[tuple[Any, Any], ...] = ()) -> Rule | None:
        for r in rules:
            if r.kind == "phash" and r.matches(path, parents) and phash_ok(a, b):
                return r
            if r.kind == "phash_file" and r.matches(path, parents) and full_hash(a) and full_hash(b) and parents:
                pa, pb = parents[-1]
                ha = ((pa.get("hashes") or {}) if isinstance(pa, dict) else {}).get("protocol")
                hb = ((pb.get("hashes") or {}) if isinstance(pb, dict) else {}).get("protocol")
                if ha != hb and phash_ok(ha, hb):
                    return r
            if r.kind == "absent_new" and r.matches(path, parents) and isinstance(a, str) and b is MISSING:
                return r
            if r.kind == "list_plus" and r.matches(path, parents) and isinstance(a, list) and b == a + list(r.new):
                return r
            if r.kind == "record" and r.matches(path, parents):
                return r
            if r.kind == "expect" and r.matches(path, parents) and a == r.ref and b == r.new:
                return r
            if r.kind == "timing" and r.matches(path, parents) and _is_num(a) and _is_num(b):
                return r
            if r.kind == "timestamp" and isinstance(a, str) and isinstance(b, str) and ISO_RE.match(a) \
                    and ISO_RE.match(b):
                return r
        return None

    def walk(path: tuple[Any, ...], a: Any, b: Any, parents: tuple[tuple[Any, Any], ...] = ()) -> None:
        if a == b and type(a) is type(b):
            return
        r = rule_for(path, a, b, parents)
        if r is not None:
            allowed.append({"path": _fmt(path), "pattern": _pattern(path), "rule": r.name, "ref": _short(a),
                            "new": _short(b)})
            return
        if isinstance(a, dict) and isinstance(b, dict):
            ka: dict[str, str] = {}
            kb: dict[str, str] = {}
            for k in a:
                ka.setdefault(nr.key(k), k)
            for k in b:
                kb.setdefault(nn.key(k), k)
            for nk in sorted(set(ka) | set(kb)):
                p = path + (nk,)
                if nk in ka and nk in kb:
                    if ka[nk] != kb[nk]:
                        _, rules_a = nr.norm(ka[nk])
                        _, rules_b = nn.norm(kb[nk])
                        allowed.append({"path": _fmt(p), "pattern": _pattern(p), "rule": "key:" + "+".join(
                            sorted(rules_a | rules_b)), "ref": ka[nk], "new": kb[nk]})
                    walk(p, a[ka[nk]], b[kb[nk]], parents + ((a, b),))
                else:
                    va = a[ka[nk]] if nk in ka else MISSING
                    vb = b[kb[nk]] if nk in kb else MISSING
                    rr = rule_for(p, va, vb, parents + ((a, b),))
                    if rr is not None and rr.kind in ("record", "absent_new"):
                        allowed.append({"path": _fmt(p), "pattern": _pattern(p), "rule": rr.name, "ref": _short(va),
                                        "new": _short(vb)})
                    else:
                        mism.append({"path": _fmt(p), "ref": _short(va), "new": _short(vb)})
            return
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                mism.append({"path": _fmt(path), "ref_len": len(a), "new_len": len(b),
                             "note": "list length differs; common prefix compared element-wise"})
            for i in range(min(len(a), len(b))):
                walk(path + (i,), a[i], b[i], parents + ((a, b),))
            return
        if isinstance(a, str) and isinstance(b, str):
            na, ra = nr.norm(a)
            nb, rb = nn.norm(b)
            if na == nb:
                allowed.append({"path": _fmt(path), "pattern": _pattern(path), "rule": "+".join(sorted(ra | rb)),
                                "ref": _short(a), "new": _short(b)})
                return
        mism.append({"path": _fmt(path), "ref": _short(a), "new": _short(b)})

    walk((), ref_doc.get("result"), new_doc.get("result"))
    by_rule: dict[str, dict[str, Any]] = {}
    for item in allowed:
        slot = by_rule.setdefault(item["rule"], {"count": 0, "patterns": Counter()})
        slot["count"] += 1
        slot["patterns"][item["pattern"]] += 1
    summary = {rule: {"count": v["count"], "patterns": dict(v["patterns"].most_common())} for rule, v in by_rule.items()}
    verdict = "MISMATCH" if mism else ("ALLOWED_DIFF" if allowed else "EXACT_MATCH")
    return {"verdict": verdict, "allowed_by_rule": summary, "allowed": allowed, "mismatches": mism,
            "hash_map_sizes": {"ref": len(nr.hash_map), "new": len(nn.hash_map)},
            "hash_conflicts": {"ref": (ref_doc.get("meta") or {}).get("arch_hash_conflicts"),
                               "new": (new_doc.get("meta") or {}).get("arch_hash_conflicts")}}


# == snapshot guard ==================================================================================================
def tree_listing(root: Path) -> dict[str, Any]:
    """Every directory, file (sha256 + size) and symlink under ``root`` (nothing excluded, unlike source_tree_hash)."""
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        entries[f"d:{rel_dir}"] = "dir"
        for name in dirnames + sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                entries[f"l:{rel}"] = os.readlink(full)
            elif name in filenames:
                h = hashlib.sha256()
                with open(full, "rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        h.update(block)
                entries[f"f:{rel}"] = f"{h.hexdigest()}:{os.path.getsize(full)}"
    digest = hashlib.sha256("".join(f"{k}\0{v}\n" for k, v in sorted(entries.items())).encode()).hexdigest()
    return {"root": str(root), "sha256": digest, "files": sum(1 for k in entries if k.startswith("f:")),
            "dirs": sum(1 for k in entries if k.startswith("d:")), "links": sum(1 for k in entries if k.startswith("l:")),
            "entries": entries}


def listing_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    a, b = before["entries"], after["entries"]
    return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}


def source_tree_digest(*, packages: tuple[str, ...] = (), roots: tuple[tuple[str, str], ...] = ()) -> str | None:
    """artifact_ref of ``mbnas_contracts.util.source_hash`` (the algorithm of scripts/dev/source_tree_hash.py; it
    excludes __pycache__/pyc, so the snapshot guard uses :func:`tree_listing`; this digest is recorded alongside)."""
    try:
        from mbnas_contracts.util.source_hash import compute_source_tree_hash, package_roots
    except ImportError:
        return None
    pairs = (package_roots(packages) if packages else []) + list(roots)
    return compute_source_tree_hash(pairs)["sha256"]


# == running the sides ===============================================================================================
def _base_env_common() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "PIP_CONFIG_FILE": os.devnull, "CUDA_VISIBLE_DEVICES": "", "PYTHONNOUSERSITE": "1"}


def side_env(work: Path) -> dict[str, str]:
    home = work / "home"
    for d in (home, home / "yolo", home / "mpl", home / ".cache", home / ".config", work / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    return {**_base_env_common(), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
            "YOLO_CONFIG_DIR": str(home / "yolo"), "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "false",
            "MPLCONFIGDIR": str(home / "mpl"), "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"), "TORCH_HOME": str(home / "torch"), "HF_HOME": str(home / "hf"),
            "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4", "TMPDIR": str(work / "tmp")}


def run_side(side: str, scenario: str, *, python: str, snapshot: Path, work: Path, out: Path,
             inputs: Path | None, ref: Path | None, timeout: int) -> dict[str, Any]:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    runner = T2_DIR / ("reference_runner.py" if side == "ref" else "new_runner.py")
    cmd = [python, "-B", "-s", str(runner), "--snapshot", str(snapshot), "--scenario", scenario, "--work", str(work),
           "--out", str(out)]
    if inputs is not None:
        cmd += ["--inputs", str(inputs)]
    if ref is not None:
        cmd += ["--ref", str(ref)]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(work), env=side_env(work), capture_output=True, text=True, timeout=timeout)
        code, stderr = proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, stderr = -9, f"timeout after {timeout}s\n{exc.stderr or ''}"
    subs = CORE.host_substitutions(work, snapshot, REPO) + [(str(out.parent), "<OUT>")]
    subs.sort(key=lambda x: -len(x[0]))
    text = CORE.scrub(stderr or "", subs)
    log = out.with_suffix(".log")
    log.write_text(text[-200_000:], encoding="utf-8")
    return {"cmd": CORE.scrub(" ".join(cmd), subs), "exit_code": code, "elapsed_s": round(time.monotonic() - started, 1),
            "stderr_tail": text[-3000:], "log": _rel(log)}


def _rel(p: Path | str) -> str:
    for cand in (Path(p).absolute(), Path(p).resolve()):
        try:
            return str(cand.relative_to(REPO))
        except ValueError:
            continue
    return CORE.scrub(str(p), CORE.host_substitutions(Path("/nonexistent-work"), DEFAULT_SNAPSHOT, REPO))


def _display_cmd(cmd: str, work: Path) -> str:
    return cmd.replace("<REPO>/", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", action="append", choices=CORE.SCENARIOS, help="repeatable; default: all ten")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--work-root", default=None, help="default: a new temporary directory")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    ap.add_argument("--python", default=sys.executable, help="interpreter of BOTH sides (same venv)")
    ap.add_argument("--timeout", type=int, default=2400, help="seconds per runner")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--compare-only", action="store_true", help="re-compare existing <out>/<scenario>.{ref,new}.json")
    ap.add_argument("--no-report", action="store_true", help="write T2_SUMMARY.json only")
    args = ap.parse_args(argv)
    scenarios = list(dict.fromkeys(args.scenario or CORE.SCENARIOS))
    out_dir = Path(args.out_dir).resolve()
    (out_dir / "inputs").mkdir(parents=True, exist_ok=True)
    snapshot = Path(args.snapshot).resolve()
    if not (snapshot / "application" / "MBNAS_server" / "src" / "mbnas_server").is_dir():
        print(f"reference snapshot not found under {snapshot}", file=sys.stderr)
        return 2
    work_root = Path(args.work_root).resolve() if args.work_root else Path(tempfile.mkdtemp(prefix="mbnas_t2_"))
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    snap_before = tree_listing(snapshot)
    new_pkgs = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core")
    new_tree_before = source_tree_digest(packages=new_pkgs)
    snap_sth_before = source_tree_digest(roots=(("snapshot", str(snapshot)),))
    registry_doc = json.loads((snapshot / "application" / "MBNAS_server" / "config" /
                               "model_registry.example.json").read_text(encoding="utf-8"))
    results: dict[str, Any] = OrderedDict()
    for scenario in scenarios:
        ref_out, new_out = out_dir / f"{scenario}.ref.json", out_dir / f"{scenario}.new.json"
        work = work_root / scenario
        entry: dict[str, Any] = {"scenario": scenario}
        inputs = CORE.scenario_inputs(scenario, registry_doc)
        input_path = None
        if inputs:
            input_path = out_dir / "inputs" / f"{scenario}.input.json"
            input_path.write_text(CORE.dumps(CORE.canon(inputs)) + "\n", encoding="utf-8")
            entry["fixed_inputs_sha256"] = CORE.sha256_json(inputs)
            entry["fixed_inputs_file"] = _rel(input_path)
        if not args.compare_only:
            print(f"[t2] {scenario}: reference side ...", flush=True)
            entry["ref_run"] = run_side("ref", scenario, python=args.python, snapshot=snapshot, work=work, out=ref_out,
                                        inputs=input_path, ref=None, timeout=args.timeout)
            print(f"[t2] {scenario}: ref exit {entry['ref_run']['exit_code']} ({entry['ref_run']['elapsed_s']} s); "
                  "new side ...", flush=True)
            entry["new_run"] = run_side("new", scenario, python=args.python, snapshot=snapshot, work=work, out=new_out,
                                        inputs=input_path, ref=ref_out if scenario in CORE.NEEDS_REF else None,
                                        timeout=args.timeout)
            print(f"[t2] {scenario}: new exit {entry['new_run']['exit_code']} ({entry['new_run']['elapsed_s']} s)",
                  flush=True)
            if not args.keep_work and work.exists():
                shutil.rmtree(work)
        if not ref_out.is_file() or not new_out.is_file():
            entry.update(verdict="RUNNER_ERROR", reason="missing output JSON")
            results[scenario] = entry
            continue
        ref_doc, new_doc = json.loads(ref_out.read_text()), json.loads(new_out.read_text())
        entry["outputs"] = {"ref": _rel(ref_out), "new": _rel(new_out),
                            "ref_sha256": hashlib.sha256(ref_out.read_bytes()).hexdigest(),
                            "new_sha256": hashlib.sha256(new_out.read_bytes()).hexdigest()}
        entry["inputs_sha256"] = {"ref": ref_doc.get("inputs_sha256"), "new_consumed_ref_inputs":
                                  new_doc.get("ref_inputs_sha256"), "fixed_ref": ref_doc.get("fixed_inputs_sha256"),
                                  "fixed_new": new_doc.get("fixed_inputs_sha256")}
        entry["isolation"] = {"ref": ref_doc["meta"]["isolation"]["violations"],
                              "new": new_doc["meta"]["isolation"]["violations"]}
        entry["side_modules"] = {"ref": ref_doc["meta"]["isolation"]["side_module_origins"],
                                 "new": new_doc["meta"]["isolation"]["side_module_origins"]}
        entry["elapsed_s"] = {"ref": ref_doc["meta"].get("elapsed_s"), "new": new_doc["meta"].get("elapsed_s")}
        entry["versions"] = {"ref": ref_doc["meta"].get("versions"), "new": new_doc["meta"].get("versions")}
        runner_error = [s for s, d in (("ref", ref_doc), ("new", new_doc)) if d.get("status") != "ok"]
        two_phase_ok = (scenario not in CORE.NEEDS_REF
                        or new_doc.get("ref_inputs_sha256") == ref_doc.get("inputs_sha256"))
        cmp = compare_docs(ref_doc, new_doc, scenario)
        entry.update(cmp)
        if runner_error or entry["isolation"]["ref"] or entry["isolation"]["new"] or not two_phase_ok:
            entry["verdict"] = "RUNNER_ERROR"
            entry["runner_error"] = {"sides": runner_error, "two_phase_inputs_consistent": two_phase_ok,
                                     "ref_error": (ref_doc.get("result") or {}).get("$runner_error"),
                                     "new_error": (new_doc.get("result") or {}).get("$runner_error")}
        entry["info"] = {"ref": ref_doc.get("info"), "new": new_doc.get("info")}
        try:
            entry["coverage"] = coverage(scenario, ref_doc.get("result") or {})
        except Exception as exc:  # noqa: BLE001 - the digest is descriptive only
            entry["coverage"] = {"error": f"{type(exc).__name__}: {exc}"}
        results[scenario] = entry
        print(f"[t2] {scenario}: {entry['verdict']} (allowed {len(cmp['allowed'])}, mismatches "
              f"{len(cmp['mismatches'])})", flush=True)
    snap_after = tree_listing(snapshot)
    snap_sth_after = source_tree_digest(roots=(("snapshot", str(snapshot)),))
    new_tree_after = source_tree_digest(packages=new_pkgs)
    snap_before["root"] = snap_after["root"] = _rel(snapshot)
    guard = {"root": _rel(snapshot), "before_sha256": snap_before["sha256"], "after_sha256": snap_after["sha256"],
             "files": snap_after["files"], "dirs": snap_after["dirs"], "links": snap_after["links"],
             "unchanged": snap_before["sha256"] == snap_after["sha256"], "diff": listing_diff(snap_before, snap_after),
             "source_tree_hash_py": {"before": snap_sth_before, "after": snap_sth_after,
                                     "note": "mbnas-source-tree/2 (scripts/dev/source_tree_hash.py algorithm) over the "
                                             "snapshot; excludes __pycache__/pyc, the full listing above does not"}}
    summary = {"generated_at": started_at, "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "script": "scripts/owner/t2_compare.py", "python": _rel(args.python), "work_root": "<WORK_ROOT>",
               "snapshot_guard": guard, "new_source_tree": {"before": new_tree_before, "after": new_tree_after,
                                                            "changed_during_run": new_tree_before != new_tree_after},
               "rules": _rule_table(),
               "module_substitutions": [{"ref": a, "new": b, "token": t} for a, b, t in MODULE_SUBS],
               "harness_files": {_rel(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in (
                   T2_DIR / "t2_core.py", T2_DIR / "reference_runner.py", T2_DIR / "new_runner.py",
                   Path(__file__).resolve())},
               "scenarios": results}
    previous = out_dir / "T2_SUMMARY.json"
    if args.scenario and previous.is_file():  # partial run: keep the other scenarios' entries of the last full run
        try:
            old = json.loads(previous.read_text())
            merged = OrderedDict((s, old["scenarios"][s]) for s in CORE.SCENARIOS if s in old.get("scenarios", {}))
            merged.update(results)
            summary["scenarios"] = OrderedDict((s, merged[s]) for s in CORE.SCENARIOS if s in merged)
            summary["partial_rerun"] = scenarios
        except (ValueError, KeyError):
            pass
    previous.write_text(json.dumps(summary, indent=1, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    (out_dir / "T2_snapshot_guard.json").write_text(json.dumps(
        {"before": {k: v for k, v in snap_before.items() if k != "entries"},
         "after": {k: v for k, v in snap_after.items() if k != "entries"}, **guard}, indent=1) + "\n", encoding="utf-8")
    if not args.no_report:
        write_report(out_dir / "T2_REPORT.md", summary)
    if not guard["unchanged"]:
        print(f"SNAPSHOT CHANGED: {guard['diff']}", file=sys.stderr)
        return 4
    if not args.keep_work and not args.work_root:
        shutil.rmtree(work_root, ignore_errors=True)
    bad = [s for s, e in summary["scenarios"].items() if e.get("verdict") not in ("EXACT_MATCH", "ALLOWED_DIFF")]
    return 1 if bad else 0


# == coverage digest (what each comparison exercised; read from the reference output) =================================
def _tail(cid: Any) -> str:
    return str(cid).rsplit(".", 2)[-2] + "." + str(cid).rsplit(".", 1)[-1] if "." in str(cid) else str(cid)


def coverage(scenario: str, r: dict[str, Any]) -> dict[str, Any]:
    if scenario == "T2-1":
        return {k: f"{d.get('status')} {[x['code'] for x in d.get('diagnostics') or []]}" for k, d in r["docs"].items()}
    if scenario == "T2-2":
        return {"templates": len(r["space"]["templates"]), "rejected_templates": len(r["space"]["rejected_templates"]),
                **{f"seed {sd}": [f"{p['lineage']['template']['id']} draws={p['prior_draw']['num_draws']} "
                                  f"repairs={len(p['repair_events'])}" for p in props]
                   for sd, props in r["proposals"].items()}}
    if scenario == "T2-3":
        out: dict[str, Any] = {"gen0_shared_with_random_arm": r["gen0_shared_with_random_arm"],
                               "negatives": {k: v.get("$error") for k, v in r["negatives"].items()}}
        for sd, g in r["gen1"].items():
            if "children" not in g:
                out[f"gen1 seed {sd}"] = g.get("error")
                continue
            kids = []
            for c in g["children"]:
                txt = c["operation"] + ("(elite)" if c["elite"] else "")
                if c.get("crossover"):
                    txt += f" cut={c['crossover']['cut_index']}"
                for m in c["mutation_events"]:
                    txt += f" mut[{m['row']}] {m['old_name']}->{m['new_name']}{' noop' if m.get('structural_noop') else ''}"
                kids.append(txt)
            out[f"gen1 seed {sd}"] = {"elite": g["selection"]["elite"], "children": kids}
        return out
    if scenario == "T2-4":
        return {arm: {"status": v["status"], "row9_c3tr_to_sppf": v["expectation_row9_c3tr_to_sppf"],
                      "repairs": [[f"row {e['row']} {e['before'][2]}->{e['after'][2]} (violation row {e['violation_row']})"
                                   for e in c["repair_events"]] for c in v["candidates"]]} for arm, v in r["arms"].items()}
    if scenario == "T2-5":
        fit = Counter((x["result"].get("reason") or "VALID") if "$error" not in x["result"] else x["result"]["$error"]
                      for x in r["fitness"])
        gate = Counter(x["result"].get("reason_code") or x["result"].get("$error") or x["result"].get("skipped")
                       for x in r["gate"])
        return {"fitness_results": len(r["fitness"]), "fitness_reasons": dict(fit), "gate_results": len(r["gate"]),
                "gate_codes": dict(gate), "fitness_and_gate": len(r["fitness_and_gate"]),
                "misc_errors": {k: v.get("$error") for k, v in r["misc"].items() if isinstance(v, dict)}}
    if scenario == "T2-6":
        return {"admission": {k: f"{v['admitted']} {v['reason_code']} est={v['estimate_s']} ({v['estimate_source']}) "
                                 f"remaining={v['remaining_s']}" for k, v in r["admission"].items()},
                "settle": {k: f"{v.get('status')} {v.get('reason_code')}" for k, v in r["settle"].items()
                           if isinstance(v, dict) and "status" in v}}
    if scenario in ("T2-7", "T2-8"):
        db = r["db"] if scenario == "T2-7" else None
        if db is not None:
            evs = [e for e in db["evaluations"] if e["status"] == "EVALUATED"]
            return {"run": f"{db['run']['status']} {db['run'].get('reason_code')}", "jobs_executed": r["executed"],
                    "jobs_by_kind": dict(Counter(j["kind"] for j in db["jobs"])), "evaluated_rows": len(evs),
                    "gate_codes": dict(Counter(str((e["row"].get("gate") or {}).get("reason_code")) for e in evs)),
                    "candidate_status": dict(Counter(c["candidate_status"] for c in db["candidates"])),
                    "generations": f"{len(db['generations'])} rows, complete {sum(1 for g in db['generations'] if g['complete'])}",
                    "ledger_rows_by_kind": dict(Counter(c["kind"] for c in db["costs"])),
                    "events": len(db["events"])}
        reqs = {}
        for k, v in r["requests"].items():
            if v.get("finalists") is not None:
                reqs[k] = {"created": v.get("created"), "finalists": [f"{f['arm']} s{f['search_seed']} "
                                                                      f"{_tail(f['candidate_id'])} rank {f['rank']}"
                                                                      for f in v["finalists"]]}
            else:
                reqs[k] = v.get("code") or v.get("$error") or v.get("request_error")
        return {"requests": reqs, "child_runs": {k: f"{v['run']['status']} candidates "
                                                    f"{dict(Counter(c['candidate_status'] for c in v['candidates']))}"
                                                 for k, v in r["children_after"].items()},
                "child_jobs_executed": r["child_executed"],
                "fixed_table_top1": [f"{f['arm']} s{f['search_seed']} {f['candidate_id']}" for f in r["final_table"]["top1"]],
                "fixed_table_top3": [f"{f['arm']} s{f['search_seed']} {f['candidate_id']} #{f['rank']}"
                                     for f in r["final_table"]["top3"]]}
    if scenario == "T2-9":
        return {"rows": r["count"], "first_key": r["rows"][0]["key"]}
    if scenario == "T2-10":
        db = r["db"]
        return {"run": f"{db['run']['status']} {db['run'].get('reason_code')}", "jobs_executed": r["executed"],
                "journals": {k: f"attempts {j['attempts']} duplicates {j['duplicates']} failures {j['failure_counts']} "
                                f"accepted {len(j['candidates'])}" for k, j in r["journals"].items()},
                "yaml_files": len(r["yaml_files"]), "candidates_in_db": len(db["candidates"]),
                "generation_results": {k: f"{v.get('status')} generated {v.get('generated_count')}/{v.get('requested_count')}"
                                       for k, v in ((db["run"].get("budget") or {}).get("generation_results") or {}).items()}}
    return {}


# == report ==========================================================================================================
SCENARIO_TITLES = {
    "T2-1": "구조 gate (check_structure, 10 YAML x input 64/160)",
    "T2-2": "Random gen0 (seeds 101/202/303, population 4)",
    "T2-3": "GA gen0/gen1 (8-row synthetic L0 pool, crossover 0.5, mutation 0.15)",
    "T2-4": "repair seed 116 (PROPOSE_GENERATION handler)",
    "T2-5": "fitness / gate (24 rows x 3 rung cfg x 2 hard constraints)",
    "T2-6": "admission (15 decisions) / settle_run (12 transitions)",
    "T2-7": "engine flow (InlineWorker + stub handlers, 2 rungs, 2 seeds, population 2)",
    "T2-8": "final selection (T2-7 run + final-retrain requests; fixed-table rule)",
    "T2-9": "seed streams (100 fixed inputs)",
    "T2-10": "generation-only OFF run (seeds 2, candidate_count 2, max_attempts 6)",
}


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _out_display(out_dir: Path) -> str:
    """The report's output directory as written in its header: relative to the repository when inside it, else a token
    (an outside --out-dir is usually a scratch path that may carry host facts)."""
    try:
        return str(out_dir.resolve().relative_to(REPO))
    except ValueError:
        return "<OUT_DIR> (--out-dir outside the repository)"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    guard = summary["snapshot_guard"]
    core_sha = (summary.get("harness_files") or {}).get("tests/owner/t2/t2_core.py", "")
    out = _out_display(path.parent)  # the --out-dir of this run (C03 EVAL_2 D2': no fixed evidence/T2 path)
    lines = ["# T2 core 동등성 보고 (owner 전용)", "",
             f"생성: {summary['generated_at']} ~ {summary['finished_at']} (UTC), `{summary['script']}`. 계획: "
             "`docs/internal/WORK_PACKAGES/T2_REFERENCE_PLAN.md`. 산출(`--out-dir`): `" + out + "/{scenario}.{ref,new}.json`"
             ", 고정 입력 `inputs/`, 기계 판독 요약 `T2_SUMMARY.json`, 스냅샷 보호 `T2_snapshot_guard.json`.", "",
             f"- reference = 원본 monolith `mbnas_server` + `yolo_ga_runtime` (`{guard['root']}`, 서브프로세스, "
             "sys.path=[snapshot/application/MBNAS_server/src, snapshot], 새 패키지 src 제거, MBNAS_ETRI_NAS_ROOT=snapshot).",
             "- new = `mbnas_core` + `mbnas_model_runtime` + `mbnas_contracts` (같은 venv 서브프로세스).",
             f"- 스냅샷 트리(파일 {guard['files']}, 디렉터리 {guard['dirs']}, 링크 {guard['links']}) sha256 전 "
             f"`{guard['before_sha256'][:16]}…` 후 `{guard['after_sha256'][:16]}…` → "
             f"**{'변경 없음' if guard['unchanged'] else '변경됨 (실패)'}**.",
             f"- 명령의 토큰: `<SNAPSHOT>` = {guard['root']}, `<OUT>` = {out}, `<WORK>` = "
             "`--work-root`(없으면 임시 디렉터리) 아래의 시나리오별 작업 디렉터리(양쪽 동일 절대 경로, 측 사이에 삭제). "
             "산출 JSON의 `<REPO>`/`<HOME>`/`<HOSTNAME>`은 호스트 경로·이름을 지운 토큰.",
             f"- 새 패키지 source tree(contracts+model_runtime+core) 전 `{summary['new_source_tree']['before']}` 후 "
             f"`{summary['new_source_tree']['after']}`"
             + (" — **실행 중 변경됨: 결과가 두 버전에 걸칠 수 있음**" if summary["new_source_tree"]["changed_during_run"]
                else " (실행 중 변경 없음)") + ".", ""]
    first = next(iter(summary["scenarios"].values()), {})
    vers = ((first.get("versions") or {}).get("ref") or {})
    lines += [f"환경: python {vers.get('python')}, torch {vers.get('torch')}, ultralytics {vers.get('ultralytics')}, "
              "CPU 전용(CUDA_VISIBLE_DEVICES=\"\", pynvml 차단), PYTHONHASHSEED=0, PYTHONDONTWRITEBYTECODE=1.", "",
              "## 요약", "",
              "| 시나리오 | 판정 | 허용 차이 (규칙: 건수) | 불일치 | 고정 입력 sha256 | 소요 ref/new (s) |",
              "|---|---|---|---|---|---|"]
    for sid, e in summary["scenarios"].items():
        allowed = ", ".join(f"{r}: {v['count']}" for r, v in sorted((e.get("allowed_by_rule") or {}).items())) or "-"
        mm = len(e.get("mismatches") or [])
        inp = e.get("fixed_inputs_sha256")
        inp_txt = f"`{str(inp)[:23]}…` (inputs/{sid}.input.json)" if inp else f"t2_core.py 내 고정 (`{core_sha[:12]}…`)"
        el = e.get("elapsed_s") or {}
        lines.append(f"| {sid} {SCENARIO_TITLES.get(sid, '')} | **{e.get('verdict')}** | {_md(allowed)} | {mm} | "
                     f"{inp_txt} | {el.get('ref')} / {el.get('new')} |")
    lines += ["", "## 시나리오별 상세", ""]
    for sid, e in summary["scenarios"].items():
        lines += [f"### {sid} — {SCENARIO_TITLES.get(sid, '')}", "", f"판정: **{e.get('verdict')}**", ""]
        for side in ("ref_run", "new_run"):
            run = e.get(side)
            if run:
                lines.append(f"- {side} (exit {run['exit_code']}, {run['elapsed_s']} s): "
                             f"`{_md(_display_cmd(run['cmd'], Path(summary['work_root']) / sid))}`")
        ins = e.get("inputs_sha256") or {}
        two_phase = sid in CORE.NEEDS_REF
        lines.append(f"- 입력: 고정 입력 {('`' + str(e.get('fixed_inputs_sha256')) + '` (`' + str(e.get('fixed_inputs_file')) + '`)') if e.get('fixed_inputs_sha256') else 't2_core.py 코드 내 고정'}"
                     + (f"; 2단계 입력(ref 산출) `{ins.get('ref')}` = new가 소비한 입력 `{ins.get('new_consumed_ref_inputs')}` "
                        f"→ {'일치' if ins.get('ref') == ins.get('new_consumed_ref_inputs') else '불일치'}" if two_phase else ""))
        out = e.get("outputs") or {}
        lines.append(f"- 산출: `{out.get('ref')}` (sha256 `{str(out.get('ref_sha256'))[:16]}…`), `{out.get('new')}` "
                     f"(sha256 `{str(out.get('new_sha256'))[:16]}…`)")
        iso = e.get("isolation") or {}
        lines.append(f"- 격리 위반: ref {len(iso.get('ref') or [])}건, new {len(iso.get('new') or [])}건; 구조 해시 "
                     f"기록 ref {(e.get('hash_map_sizes') or {}).get('ref')} / new {(e.get('hash_map_sizes') or {}).get('new')}")
        if e.get("runner_error"):
            lines.append(f"- **RUNNER_ERROR**: {_md(json.dumps(e['runner_error'], ensure_ascii=False)[:1500])}")
        cov = e.get("coverage") or {}
        if cov:
            lines += ["", "실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):", ""]
            for k, v in cov.items():
                lines.append(f"- {_md(k)}: {_md(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)[:700]}")
        rules = e.get("allowed_by_rule") or {}
        if rules:
            lines += ["", "허용 차이 경로 (규칙 → 경로 패턴 × 건수):", ""]
            for rule, v in sorted(rules.items()):
                pats = "; ".join(f"`{p}` ×{n}" for p, n in list(v["patterns"].items())[:25])
                more = f" (+{len(v['patterns']) - 25} patterns, T2_SUMMARY.json)" if len(v["patterns"]) > 25 else ""
                lines.append(f"- `{rule}` ({v['count']}): {pats}{more}")
        mism = e.get("mismatches") or []
        if mism:
            lines += ["", f"불일치 {len(mism)}건 (처음 40건; 전체는 T2_SUMMARY.json):", "",
                      "| 경로 | ref | new |", "|---|---|---|"]
            for m in mism[:40]:
                ref = m.get("ref", m.get("ref_len"))
                new = m.get("new", m.get("new_len"))
                lines.append(f"| `{_md(m['path'])}` | `{_md(json.dumps(ref, ensure_ascii=False)[:300])}` | "
                             f"`{_md(json.dumps(new, ensure_ascii=False)[:300])}` |")
        info = e.get("info") or {}
        if sid == "T2-1" and (info.get("new") or {}).get("validate_declared"):
            rejected = {k: [d.get("code") for d in v] for k, v in info["new"]["validate_declared"].items() if v}
            lines += ["", f"참고(new 전용, DD-A20 콘솔 선검사 `validate_declared`; check_structure 비교 대상 아님): "
                          f"빌드 전 거부되는 YAML = {_md(json.dumps(rejected, ensure_ascii=False))}"]
        lines.append("")
    lines += ["## 허용 차이 규칙 정의", "", "| 규칙 | 종류 | 경로 패턴 | 근거 |", "|---|---|---|---|"]
    for name, r in summary["rules"].items():
        lines.append(f"| `{name}` | {r['kind']} | {_md(', '.join(f'`{p}`' for p in r['patterns']))} | "
                     f"{_md(r['basis'])} (scope: {', '.join(r.get('scenarios') or [])}) |")
    lines += ["| `module_path_string` | normaliser | 문자열 치환표(아래) | task: module path strings (DD-A2/DD-A12 이동) |",
              "| `module_path:structural_hash` | normaliser | 원시 architecture hash → 정규화 hash | DD-A29: structural_hash"
              "는 `type(m).__module__`을 해시 — ETRI 연산자 모듈 경로만 `<etri_mixed_blocks>`로 바꾼 정규화 hash가 같아야 허용 |",
              "| `path` | normaliser | work/snapshot/repo 루트 → `<WORK>`/`<SNAPSHOT>`/`<REPO>` | task: paths (normalise) |",
              "| `generated_id` | normaliser | job/run/val/ck id → 첫 등장 순서 라벨 | plan T2-7 job_id |", "",
              "모듈 경로 문자열 치환표:", ""]
    for s in summary["module_substitutions"]:
        lines.append(f"- `{s['ref']}` ↔ `{s['new']}` → `{s['token']}`")
    lines += ["", "## 방법·하네스 조치", "",
              "- 양쪽 runner는 모든 MBNAS import 전에 `uuid.uuid4`를 결정적 단조 수열로 교체(작업 lease의 `ORDER BY priority, "
              "created_at, id` 동률 해소를 재현 가능하게; id는 비교기에서도 정규화), `pynvml` 차단, CUDA 비활성.",
              "- `adapters.common.structural_hash`를 반환값 불변 래퍼로 감싸 원시→정규화 hash 쌍을 기록(`meta.arch_hash_map`).",
              "- 2단계 입력: T2-3 부모 pool 유전자형, T2-4 프로토콜, T2-7/T2-8/T2-10 요청·resolved 프로토콜은 ref 산출을 new가 "
              "그대로 소비(입력 sha256 일치 확인). 각 측의 자체 검증 결과(validation)는 별도로 비교.",
              "- T2-7/T2-8 엔진 흐름: HTTP 계층 없이 `POST /runs`의 생성 트랜잭션(create_run → plan_run → settle_run)을 그대로 "
              "재현하고, 각 측 자기 하네스의 InlineWorker(원본: snapshot tests/worker/harness.py, new: tests/_shared/harness.py)"
              "와 stub 핸들러(MBNAS_STUB_EPOCH_S=0)로 drain.",
              "- 하네스 파일 sha256: " + ", ".join(f"`{k}` `{v[:12]}…`" for k, v in (summary.get("harness_files") or {}).items()),
              "", "## 관찰 사항 (설계자 판단 요청)", ""]
    lines += OBSERVATIONS
    lines += ["", "## 한계 (확인 못 함)", ""] + LIMITATIONS
    lines += ["", "## 재현", "",
              "- 전체: `CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 runtime/venv/bin/python -B scripts/owner/t2_compare.py`"
              " (기본 출력 `docs/internal/evidence/T2/`, `--scenario T2-n` 반복 지정 가능, `--work-root DIR`, `--keep-work`).",
              "- pytest: `runtime/venv/bin/python -m pytest tests/owner/t2 -q` (빠른 T2-5/T2-6/T2-9만; 전체는 "
              "`MBNAS_TEST_T2=1`).", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


OBSERVATIONS = [
    "- `worker_device_identity` (T2-7/T2-8/T2-10의 `costs[*].device_uuid`): 원본 worker는 NVML uuid(여기서는 NVML 없음 → "
    "null)를, 새 owner worker는 합성 값 `gpu-index-N`을 원장에 기록한다(DD-A27: core에서 NVML 제거). 결정에 쓰이지 않는 "
    "저장 필드라 plan의 worker_id 허용으로 분류했지만, 원장의 장치 식별 의미가 바뀐 것이므로 설계자 확인을 요청한다. 같은 "
    "worker의 `worker.updated` 이벤트에는 `device` = `<hostname>-gpu-N`(원본 `host-gpu-N`), `device_name` = \"unverified "
    "device (owner worker has no NVML)\"가 실린다(호스트명이 owner DB 이벤트에 기록됨 — 공개 투영(WP-4)에서 제거되는지 "
    "확인 필요).",
    "- T2-1 `err_huge_row`(repeats 64, 출력 채널 65536)는 양쪽 `check_structure`에서 PASS(채널은 scale의 max_channels로 "
    "잘림)이며, `err_unknown_module`은 양쪽 모두 BLOCK_UNSUPPORTED FAIL. 새 콘솔 계층의 DD-A20 선검사 `validate_declared`는 "
    "이 두 YAML을 빌드 전에 거부한다(정보 기록: `T2-1.new.json` info.validate_declared) — check_structure 결정 자체는 동일.",
    "- 원시 architecture_hash는 ETRI 연산자(NASPatchMerge/NASWindowTransformer/NASGlobalTransformer)를 포함하는 모델에서만 "
    "달라지고, 모듈 경로를 정규화한 hash는 모든 경우 같다(기존 run의 architecture_hash와 새 run의 hash는 직접 비교 불가 — "
    "DD-A29에 이미 기록된 성질).",
]
LIMITATIONS = [
    "- GPU 경로(실제 학습·평가·latency)는 T2 범위 밖: T2-7/T2-8의 학습·평가는 stub 핸들러(결정적 합성 metric)이며 실제 "
    "trainer/evaluator 동등성은 T7/WP-G2(GPU 창) 대상. CPU 빌드·forward·구조 검사·제안기는 실제 코드로 실행.",
    "- 원격 runner 경로(`/core/v1/runner`, envelope, ticket)는 실행하지 않았다: T2-7/T2-8은 owner InlineWorker로 실행되며 "
    "새 측 결과 반영은 owner worker `_finalize` → `runner_api.settle.settle_job`(원격 결과와 같은 함수)을 거친다.",
    "- HTTP 계층(validate/runs 라우트, 공개 투영, gateway)은 비교하지 않았다: run 생성은 라우트의 트랜잭션을 직접 재현.",
    "- ETRI mixed pool은 DD-A12 Option A 이식 범위만 비교(seed variants, 연산자, parse_model 확장). 이식되지 않은 "
    "`mutate_operators/_legal_choices/symbolic_shape_propagate`는 서버 호출자가 없어 비교 대상 없음.",
    "- 결정성 조치(uuid4 결정화)는 양쪽 동일 적용이며, 원본 자체의 무작위 id 동률 해소(같은 created_at)에 의한 비결정성은 "
    "이 시험이 다루지 않는다. `ck-` checkpoint id는 os.urandom이라 정규화로 비교.",
    "- 새 패키지는 다른 에이전트가 동시에 수정 중이다: 결과는 보고서 머리의 새 패키지 source tree hash와 각 산출 JSON의 "
    "`meta.isolation.side_module_origins`(모듈별 sha256) 시점의 코드에 대한 것이다.",
]


if __name__ == "__main__":
    raise SystemExit(main())
