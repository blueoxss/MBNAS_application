"""T2 core equivalence (owner only, WORK_PACKAGES/T2_REFERENCE_PLAN.md): scenario code shared by both sides.

This module is loaded by file path (never through ``sys.path``) by ``reference_runner.py`` (side ``ref``: the ORIGINAL
monolith ``mbnas_server`` of ``build/source_snapshot`` + the snapshot's ``yolo_ga_runtime``) and by ``new_runner.py``
(side ``new``: ``mbnas_core`` + ``mbnas_model_runtime`` + ``mbnas_contracts``). Both sides execute exactly the same
scenario functions below on the same fixed inputs; the only side-specific parts are the module map
(:data:`REF_MODULES` / :data:`NEW_MODULES`), the settings class and the mixed-pool registration call (:class:`Side`).

Harness measures (identical on both sides, recorded in every output's ``meta.harness``):

* ``uuid.uuid4`` is replaced by a deterministic monotonic sequence before any MBNAS import, so id-based tie-breaks
  (``ORDER BY priority, created_at, id`` of the job lease) are reproducible; ids are additionally normalised by the
  comparator;
* ``pynvml`` is blocked (``sys.modules['pynvml'] = None``) and ``CUDA_VISIBLE_DEVICES`` is empty: no GPU is touched;
* ``adapters.common.structural_hash`` is wrapped by a recorder that returns the unchanged value and records
  ``raw -> normalised`` where the normalised hash maps the ETRI operator module path (``yolo_ga_runtime.mixed_blocks``
  / ``mbnas_model_runtime.operators.etri_mixed_blocks``) to ``<etri_mixed_blocks>``: ``structural_hash`` hashes
  ``type(m).__module__`` (DD-A29), so a raw architecture hash may differ ONLY through that module path.

Nothing here writes outside the work directory given by the runner.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import hashlib
import importlib
import itertools
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Callable

SCENARIOS: tuple[str, ...] = ("T2-1", "T2-2", "T2-3", "T2-4", "T2-5", "T2-6", "T2-7", "T2-8", "T2-9", "T2-10")
FIXED_NOW = "2026-10-06T00:00:00.000000Z"  # ValidationContext.now (same value as the owner ctx_factory fixture)
REF_ETRI_PREFIX = "yolo_ga_runtime.mixed_blocks."
NEW_ETRI_PREFIX = "mbnas_model_runtime.operators.etri_mixed_blocks."
NORM_ETRI_PREFIX = "<etri_mixed_blocks>."
DIGEST_KEYS = frozenset({"internal_layers"})  # large per-block listings: compared through a canonical digest

REF_MODULES: dict[str, str] = {
    "config": "mbnas_server.config", "canon": "mbnas_server.util.canon", "atomic": "mbnas_server.util.atomic",
    "common": "mbnas_server.adapters.common", "genotype": "mbnas_server.search.genotype",
    "structure": "mbnas_server.search.structure", "space": "mbnas_server.search.space",
    "random": "mbnas_server.search.random", "ga": "mbnas_server.search.ga", "streams": "mbnas_server.search.streams",
    "lineage": "mbnas_server.search.lineage", "handlers": "mbnas_server.search.handlers",
    "generation": "mbnas_server.search.generation", "bridge": "mbnas_server.search.etri_bridge",
    "fitness": "mbnas_server.protocol.fitness", "promotion": "mbnas_server.protocol.promotion",
    "fitness_gate": "mbnas_server.evaluation.handlers", "presets": "mbnas_server.protocol.presets",
    "validator": "mbnas_server.protocol.validator", "phash": "mbnas_server.protocol.validator",
    "admission": "mbnas_server.scheduling.admission", "costs": "mbnas_server.scheduling.costs",
    "jobs": "mbnas_server.scheduling.jobs", "planner": "mbnas_server.scheduling.planner",
    "engine": "mbnas_server.orchestration.engine", "final_retrain": "mbnas_server.orchestration.final_retrain",
    "views": "mbnas_server.orchestration.views", "bus": "mbnas_server.events.bus", "repo": "mbnas_server.db.repo",
    "dbcore": "mbnas_server.db.core", "jobs_schema": "mbnas_server.schemas.jobs",
    "api_schema": "mbnas_server.schemas.api", "manifest": "mbnas_server.datasets.manifest",
    "artifacts": "mbnas_server.artifacts.registry",
}
NEW_MODULES: dict[str, str] = {
    "config": "mbnas_core.config", "canon": "mbnas_contracts.util.canon", "atomic": "mbnas_contracts.util.atomic",
    "common": "mbnas_model_runtime.adapters.common", "genotype": "mbnas_model_runtime.genotype",
    "structure": "mbnas_core.search.structure", "space": "mbnas_core.search.space",
    "random": "mbnas_core.search.random", "ga": "mbnas_core.search.ga", "streams": "mbnas_core.search.streams",
    "lineage": "mbnas_core.search.lineage", "handlers": "mbnas_core.search.handlers",
    "generation": "mbnas_core.search.generation", "bridge": "mbnas_core.search.mixed_pool.bridge",
    "fitness": "mbnas_core.protocol.fitness", "promotion": "mbnas_core.protocol.promotion",
    "fitness_gate": "mbnas_core.protocol.fitness_gate", "presets": "mbnas_core.protocol.presets",
    "validator": "mbnas_core.protocol.validator", "phash": "mbnas_contracts.protocol_hash",
    "admission": "mbnas_core.scheduling.admission", "costs": "mbnas_core.scheduling.costs",
    "jobs": "mbnas_core.scheduling.jobs", "planner": "mbnas_core.scheduling.planner",
    "engine": "mbnas_core.orchestration.engine", "final_retrain": "mbnas_core.orchestration.final_retrain",
    "views": "mbnas_core.orchestration.views", "bus": "mbnas_core.events.bus", "repo": "mbnas_core.db.repo",
    "dbcore": "mbnas_core.db.core", "jobs_schema": "mbnas_contracts.schemas.jobs",
    "api_schema": "mbnas_contracts.schemas.api", "manifest": "mbnas_model_runtime.datasets.manifest",
    "artifacts": "mbnas_core.artifacts.registry",
}
A100 = {"id": "host-gpu-0", "uuid": "GPU-ded3a086-5c2f-f305-e4d5-4a18f272b905", "name": "NVIDIA A100 80GB PCIe",
        "aliases": ["GPU-ded3a086-5c2f-f305-e4d5-4a18f272b905", "NVIDIA A100 80GB PCIe", "cuda:0"],
        "precisions": ["FP32", "FP16"]}  # static device of the owner test fixtures (synthetic, no NVML)


# -- harness measures (installed by the runners BEFORE any MBNAS import) ---------------------------------------------
def install_determinism() -> dict[str, Any]:
    """Deterministic ``uuid.uuid4`` (monotonic; ``hex[:12]`` and ``hex[:16]`` unique) and a blocked ``pynvml``."""
    import uuid

    counter = itertools.count(1)

    def uuid4() -> uuid.UUID:
        n = next(counter)
        return uuid.UUID(hex=f"{n:012x}40008000{n:012x}")

    uuid.uuid4 = uuid4  # type: ignore[assignment]
    sys.modules["pynvml"] = None  # type: ignore[assignment]  # import pynvml -> ImportError: NVML never initialised
    return {"uuid4": "deterministic monotonic sequence (t2_core.install_determinism)",
            "pynvml": "blocked (sys.modules['pynvml'] = None)",
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES")}


def normalise_module_name(qualified: str) -> str:
    return qualified.replace(REF_ETRI_PREFIX, NORM_ETRI_PREFIX).replace(NEW_ETRI_PREFIX, NORM_ETRI_PREFIX)


# -- canonical JSON ---------------------------------------------------------------------------------------------------
def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def canon(value: Any, *, compact: bool = True) -> Any:
    """JSON-safe canonical copy: non-finite floats tagged, tuples -> lists, enums -> values, dataclasses -> dicts,
    pydantic models -> ``model_dump(mode='json')``; ``internal_layers`` lists -> ``{count, sha256}`` when ``compact``."""
    if isinstance(value, enum.Enum):
        return canon(value.value, compact=compact)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k.value if isinstance(k, enum.Enum) else k)
            if compact and key in DIGEST_KEYS and isinstance(v, list):
                full = canon(v, compact=False)
                out[key] = {"$digest": _digest(full), "count": len(v)}
            else:
                out[key] = canon(v, compact=compact)
        return out
    if isinstance(value, (list, tuple)):
        return [canon(v, compact=compact) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canon(v, compact=compact) for v in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return canon(value.model_dump(mode="json"), compact=compact)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canon({f.name: getattr(value, f.name) for f in dataclasses.fields(value)
                      if f.name not in ("model",)}, compact=compact)
    return {"$repr": f"{type(value).__name__}"}


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(dumps(canon(value)).encode("utf-8")).hexdigest()


def err(exc: BaseException) -> dict[str, Any]:
    """Exception as data (type, message, code attributes); never a traceback."""
    out: dict[str, Any] = {"$error": type(exc).__name__, "message": str(exc)[:2000]}
    for attr in ("code", "reason_code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, (str, int)) and not isinstance(val, bool):
            out[attr] = val
    return out


def rng_digest(rng: random.Random) -> str:
    """Digest of a Random state (equal digests = the same number and kind of draws were consumed)."""
    return "sha256:" + hashlib.sha256(repr(rng.getstate()).encode()).hexdigest()


# -- side abstraction ----------------------------------------------------------------------------------------------
class Side:
    """One side of the comparison: module map, settings, mixed-pool registration and recorded hash map."""

    def __init__(self, name: str, *, repo_root: Path, snapshot_root: Path, work: Path) -> None:
        if name not in ("ref", "new"):
            raise ValueError(name)
        self.name = name
        self.repo_root, self.snapshot_root, self.work = Path(repo_root), Path(snapshot_root), Path(work)
        self.modules = REF_MODULES if name == "ref" else NEW_MODULES
        self.hash_map: dict[str, str] = {}
        self.hash_conflicts: list[dict[str, str]] = []
        self._recorder_installed = False

    def mod(self, key: str) -> Any:
        return importlib.import_module(self.modules[key])

    @property
    def etri_root(self) -> str | None:
        """ETRI root argument: the snapshot root on the reference side; the vendored pool ignores it (None)."""
        return str(self.snapshot_root) if self.name == "ref" else None

    def protocol_hash(self, resolved: dict[str, Any]) -> str:
        return self.mod("phash").protocol_hash(resolved)

    def register_mixed(self) -> dict[str, Any]:
        """Register the ETRI mixed operator pool in this process (ref: snapshot yolo_ga_runtime; new: vendored)."""
        return self.mod("bridge").load_etri_mixed(self.etri_root).provenance()

    def harness_dir(self) -> Path:
        """Directory of the side's own owner test harness (InlineWorker + stub handlers)."""
        if self.name == "ref":
            return self.snapshot_root / "application" / "MBNAS_server" / "tests" / "worker"
        return self.repo_root / "tests" / "_shared"

    def harness(self) -> Any:
        if str(self.harness_dir()) not in sys.path:
            sys.path.append(str(self.harness_dir()))
        import harness  # noqa: PLC0415 - the side's own harness module (snapshot tests/worker or tests/_shared)

        if not Path(harness.__file__).resolve().is_relative_to(self.harness_dir().resolve()):
            raise RuntimeError(f"harness imported from {harness.__file__}, expected {self.harness_dir()}")
        return harness

    def settings(self, **over: Any) -> Any:
        w = self.work
        kw: dict[str, Any] = dict(db_path=str(w / "runs" / "db" / "mbnas.sqlite3"), output_root=str(w / "runs"),
                                  output_roots=[str(w / "runs")], dataset_roots=[str(w / "data")],
                                  weights_dir=str(w / "weights"), log_dir=str(w / "runs" / "logs"), lease_ttl_s=30,
                                  heartbeat_s=1, max_replay_events=50, min_free_disk_gb=0.0,
                                  worker_python=sys.executable)
        if self.name == "ref":
            kw.update(etri_nas_root=str(self.snapshot_root), archive_roots=[], archive_driver_dirs=[])
            cls = self.mod("config").Settings
        else:
            kw.update(model_yaml_roots=[str(w / "runs")], sweeper_enabled=False)
            cls = self.mod("config").CoreSettings
        kw.update(over)
        return cls(**kw)

    def handler_env(self) -> dict[str, str]:
        """Settings environment the real PROPOSE_GENERATION handler loads (``Settings.load()``) in this process."""
        w = self.work
        roots = json.dumps([str(w / "runs")])
        if self.name == "ref":
            return {"MBNAS_DB_PATH": str(w / "runs" / "db" / "mbnas.sqlite3"), "MBNAS_OUTPUT_ROOT": str(w / "runs"),
                    "MBNAS_OUTPUT_ROOTS": roots, "MBNAS_DATASET_ROOTS": json.dumps([str(w / "data")]),
                    "MBNAS_WEIGHTS_DIR": str(w / "weights"), "MBNAS_LOG_DIR": str(w / "runs" / "logs"),
                    "MBNAS_ETRI_NAS_ROOT": str(self.snapshot_root), "MBNAS_ARCHIVE_ROOTS": "[]",
                    "MBNAS_ARCHIVE_DRIVER_DIRS": "[]", "MBNAS_WORKER_PYTHON": sys.executable}
        return {"MBNAS_HOME": str(w / "mbnas_home"), "MBNAS_CORE_DB_PATH": str(w / "runs" / "db" / "mbnas.sqlite3"),
                "MBNAS_CORE_OUTPUT_ROOT": str(w / "runs"), "MBNAS_CORE_OUTPUT_ROOTS": roots,
                "MBNAS_CORE_DATASET_ROOTS": json.dumps([str(w / "data")]), "MBNAS_CORE_WEIGHTS_DIR": str(w / "weights"),
                "MBNAS_CORE_LOG_DIR": str(w / "runs" / "logs"), "MBNAS_CORE_MODEL_YAML_ROOTS": roots,
                "MBNAS_CORE_WORKER_PYTHON": sys.executable}

    # -- structural hash recorder -------------------------------------------------------------------------------
    def install_hash_recorder(self) -> None:
        """Wrap ``adapters.common.structural_hash``: unchanged return value, records ``raw -> normalised``."""
        if self._recorder_installed:
            return
        common, canon_mod = self.mod("common"), self.mod("canon")
        original = common.structural_hash
        side = self

        def structural_hash(module: Any) -> str:
            raw = original(module)
            types = [normalise_module_name(f"{type(m).__module__}.{type(m).__qualname__}") for m in module.modules()]
            state = [[k, list(v.shape), str(v.dtype)] for k, v in module.state_dict().items()]
            norm = canon_mod.canonical_sha256({"scheme": common.ARCH_HASH_SCHEME, "modules": types, "state": state})
            prev = side.hash_map.get(raw)
            if prev is not None and prev != norm:
                side.hash_conflicts.append({"raw": raw, "normalised_before": prev, "normalised_now": norm})
            side.hash_map[raw] = norm
            return raw

        structural_hash.__wrapped__ = original  # type: ignore[attr-defined]
        common.structural_hash = structural_hash
        self._recorder_installed = True


# -- fixed inputs (pure data, identical on both sides) ---------------------------------------------------------------
def ultralytics_cfg_dir() -> Path:
    import importlib.util

    spec = importlib.util.find_spec("ultralytics")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("ultralytics is not installed")
    return Path(list(spec.submodule_search_locations)[0]) / "cfg" / "models"


def _base_yaml(family: str) -> dict[str, Any]:
    import yaml

    rel = {"yolo11": "11/yolo11.yaml", "yolo26": "26/yolo26.yaml"}[family[:6]]
    data = yaml.safe_load((ultralytics_cfg_dir() / rel).read_text(encoding="utf-8"))
    data["nc"], data["scale"] = 1, family[6:]
    return data


def t2_1_inputs() -> dict[str, Any]:
    """Ten model YAMLs (T2-1): 2 native, 4 variants, 4 user errors (+ the huge row), x input sizes 64/160."""
    y11, y26 = _base_yaml("yolo11n"), _base_yaml("yolo26n")
    yamls: dict[str, dict[str, Any]] = {}

    def add(name: str, family: str, cfg: dict[str, Any], note: str) -> None:
        yamls[name] = {"family": family, "yaml": cfg, "note": note}

    add("yolo11n_native", "yolo11n", copy.deepcopy(y11), "installed ultralytics yolo11.yaml, nc=1, scale n")
    add("yolo26n_native", "yolo26n", copy.deepcopy(y26), "installed ultralytics yolo26.yaml, nc=1, scale n")
    v = copy.deepcopy(y11)
    v["backbone"][2] = [-1, 2, "C2f", [256]]
    v["backbone"][8] = [-1, 1, "C2f", [1024]]
    add("yolo11n_variant_c2f", "yolo11n", v, "native feature slots 2 and 8 replaced by C2f")
    v = copy.deepcopy(y26)
    v["backbone"][3] = [-1, 1, "NASPatchMerge", [256, 2]]
    v["backbone"][6] = [-1, 2, "NASWindowTransformer", [512]]
    add("yolo26n_variant_mixed", "yolo26n", v, "ETRI mixed operators NASPatchMerge (row 3) + NASWindowTransformer (row 6)")
    v = copy.deepcopy(y11)
    v["backbone"][3] = [-1, 1, "NASPatchMerge", [256, 2]]
    v["backbone"][10] = [-1, 1, "NASGlobalTransformer", [1024]]
    add("yolo11n_variant_global", "yolo11n", v, "ETRI NASPatchMerge (row 3) + NASGlobalTransformer replacing C2PSA (row 10)")
    v = copy.deepcopy(y11)
    v["head"][1] = [[-1, 4], 1, "Concat", [1]]
    add("err_concat_mismatch", "yolo11n", v, "head Concat of the upsampled P5 (stride 16) with backbone row 4 (stride 8)")
    v = copy.deepcopy(y11)
    v["head"][-1] = [list(reversed(v["head"][-1][0])), 1, "Detect", ["nc"]]
    add("err_head_stride_inverted", "yolo11n", v, "Detect sources in reverse order (strides 32, 16, 8)")
    v = copy.deepcopy(y11)
    v["backbone"][2] = [-1, 2, "FooBlockT2", [256, False, 0.25]]
    add("err_unknown_module", "yolo11n", v, "backbone row 2 names an unknown module")
    v = copy.deepcopy(y11)
    v["head"][-1] = [v["head"][-1][0], 1, "Detect", [3]]
    add("err_nc_mismatch", "yolo11n", v, "Detect declares 3 classes, model nc is 1")
    v = copy.deepcopy(y11)
    v["backbone"][2] = [-1, 64, "C3k2", [65536, False, 0.25]]
    add("err_huge_row", "yolo11n", v, "row with 64 repeats and 65536 output channels (clamped by max_channels)")
    return {"yamls": yamls, "sizes": [64, 160], "class_count": 1,
            "ultralytics_cfg_sha256": {k: hashlib.sha256((ultralytics_cfg_dir() / k).read_bytes()).hexdigest()
                                       for k in ("11/yolo11.yaml", "26/yolo26.yaml")}}


POOL_TABLE: tuple[tuple[float | None, float | None], ...] = (  # (L0 fitness, latency_ms) of the 8 synthetic parents
    (0.61, 12.0), (0.42, 9.5), (None, 7.0), (0.61, 10.0),   # seed-101 genotypes: tie on fitness -> latency decides
    (0.55, 11.0), (0.30, 6.0), (0.55, 11.0), (0.12, 30.0),  # seed-202 genotypes: full tie -> candidate_id decides
)
W_L0 = {"ap": 0.2, "recall": 0.1, "latency_reward": 0.4, "size_reward": 0.3}
W_L1 = {"ap": 0.45, "recall": 0.15, "latency_reward": 0.25, "size_reward": 0.15}
W_L2 = {"ap": 0.6, "recall": 0.1, "latency_reward": 0.2, "size_reward": 0.1}


def t2_5_inputs() -> dict[str, Any]:
    nan, inf = float("nan"), float("inf")
    rows = [  # (ap, recall, latency_ms, size_mb, latency_source)
        (0.45, 0.60, 20.0, 5.0, "measured"), (0.0, 0.0, 200.0, 60.0, "measured"), (1.0, 1.0, 5.0, 2.0, "measured"),
        (0.3, 0.4, 4.0, 1.0, "measured"), (0.3, 0.4, 250.0, 80.0, "measured"), (0.3, 0.4, 102.5, 31.0, "measured"),
        (None, 0.4, 20.0, 5.0, "measured"), (0.3, None, 20.0, 5.0, "measured"), (0.3, 0.4, None, 5.0, "pending"),
        (0.3, 0.4, 20.0, None, "measured"), (nan, 0.4, 20.0, 5.0, "measured"), (0.3, 0.4, inf, 5.0, "measured"),
        (1.0000001, 0.4, 20.0, 5.0, "measured"), (0.3, -1e-07, 20.0, 5.0, "measured"), (0.3, 0.4, 0.0, 5.0, "measured"),
        (0.3, 0.4, 20.0, -1.0, "measured"), (True, 0.4, 20.0, 5.0, "measured"), ("0.3", 0.4, 20.0, 5.0, "measured"),
        (0.3, 0.4, 20.0, 5.0, "calibrated_proxy"), (0.3, 0.4, 20.0, 5.0, "unverified_proxy"),
        (0.3, 0.4, 20.0, 5.0, None), (0.5, 0.5, 999.999, 499.999, "measured"), (0.5, 0.5, 1000.0, 500.0, "measured"),
        (0.5, 0.5, 1000.0001, 500.0, "measured"),
    ]
    rungs = [{"id": "L0", "fitness_weights": W_L0, "promotion": {"to": "L1", "score_ge": 0.35, "ap_ge": 0.2}},
             {"id": "L1", "fitness_weights": W_L1, "promotion": {"to": "L2", "score_ge": 0.4, "ap_ge": 0.3,
                                                                  "latency_le_ms": 150.0, "weights_size_le_MB": 40.0}},
             {"id": "L2", "fitness_weights": W_L2, "promotion": None}]
    hcs = {"HC_A": {"latency_le_ms": 1000, "weights_size_le_MB": 500, "proxy_may_certify_final_deployment": False},
           "HC_B": {"latency_le_ms": 100.0, "weights_size_le_MB": 50.0, "proxy_may_certify_final_deployment": True}}
    return {"rows": rows, "rungs": rungs, "hard_constraints": hcs,
            "fitness": {"latency_bounds_ms": [5.0, 200.0], "size_bounds_MB": [2.0, 60.0]}}


def resolved_minimal(work: Path, *, search_h: float | None = 1.0, baseline_h: float | None = 0.25,
                     final_h: float | None = 0.5, seeds: list[int] | None = None,
                     generation_only: bool = False) -> dict[str, Any]:
    """Fixed minimal resolved protocol for synthetic DB runs (T2-6); pure data, identical on both sides."""
    search: dict[str, Any] = {"arms": ["GA", "IndependentRandom"], "seeds": seeds or [1], "population": 2,
                              "generations": 1, "budget_gpu_hours": search_h}
    if generation_only:
        search.update(evaluation_enabled=False, candidate_count=2, max_attempts=6, allow_duplicates=False)
    return {"schema_version": "nas-policy/1",
            "rungs": [{"id": "L0", "epoch_target_total": 2, "fitness_weights": W_L0,
                       "promotion": {"to": "L1", "score_ge": 0.0, "ap_ge": 0.0}},
                      {"id": "L1", "epoch_target_total": 4, "fitness_weights": W_L1, "promotion": None}],
            "training": {"initialization": "scratch", "requested_input_size": [64, 64]},
            "fitness": {"formula_version": "named_weighted_reward_v1", "latency_bounds_ms": [5.0, 200.0],
                        "size_bounds_MB": [2.0, 60.0], "latency_scope": "synthetic"},
            "hard_constraints": {"latency_le_ms": 1000, "weights_size_le_MB": 500,
                                 "proxy_may_certify_final_deployment": False},
            "dataset": {"search_validation_manifest_hash": "sha256:" + "a" * 64,
                        "final_evaluation_manifest_hash": "sha256:" + "b" * 64},
            "final_retraining": {"epoch_target_total": 2, "scheduler_horizon_epochs": 2,
                                 "initialization": "scratch_with_pinned_recipe", "budget_GPU_hours_separate": final_h},
            "console": {"paths": {"output_root": str(work / "runs" / "out")}, "search": search,
                        "baseline_budget_gpu_hours": baseline_h,
                        "benchmark": {"device": "host-gpu-0", "precision": "FP16", "batch": 1}, "baselines": []}}


def make_dataset(root: Path, name: str = "ds", counts: dict[str, int] | None = None,
                 label: str = "0 0.5 0.5 0.25 0.25\n", names: list[str] | None = None, seed0: int = 1) -> dict[str, str]:
    """Synthetic YOLO dataset; same construction as the owner ``make_dataset`` fixture (deterministic PNG bytes)."""
    from PIL import Image

    base = root / name
    seed = seed0
    for split, n in (counts or {"train": 4, "val": 2, "test": 2}).items():
        for i in range(n):
            img = base / "images" / split / f"{split}_{i:03d}.png"
            img.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 24), ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)).save(img)
            (base / "labels" / split).mkdir(parents=True, exist_ok=True)
            (base / "labels" / split / f"{split}_{i:03d}.txt").write_text(label)
            seed += 1
    (base / "data.yaml").write_text(f"path: {base}\ntrain: images/train\nval: images/val\ntest: images/test\n"
                                    f"names: {json.dumps(names or ['drone'])}\n")
    return {"root": str(base), "dataset_yaml": str(base / "data.yaml"), "train_root": str(base / "images/train"),
            "search_val_root": str(base / "images/val"), "final_eval_root": str(base / "images/test")}


# -- shared record builders ------------------------------------------------------------------------------------------
def report_record(rep: Any) -> dict[str, Any]:
    return {k: getattr(rep, k) for k in ("ok", "error", "error_row", "imgsz", "architecture_hash", "signature_hash",
                                         "params", "flops", "stage", "failure_kind", "yaml_hash")}


def proposal_record(side: Side, prop: Any, space: Any, desc: dict[str, Any]) -> dict[str, Any]:
    lineage = side.mod("lineage")
    g = prop.genotype
    return {"method": prop.method, "operation": prop.operation, "elite": prop.elite,
            "genotype": g.to_json(), "yaml_text": g.yaml_text(), "yaml_hash": g.yaml_hash(),
            "report": report_record(prop.report),
            "origins": [dataclasses.asdict(o) for o in prop.origins], "parents": prop.parents,
            "crossover": prop.crossover, "mutation_events": prop.mutation_events,
            "repair_events": prop.repair_events, "prior_draw": prop.prior_draw, "selection": prop.selection,
            "notes": prop.notes, "lineage": lineage.lineage_for(prop, space, desc),
            "blocks": lineage.blocks_for(prop, space), "audit": lineage.audit_for(prop, space)}


def prior_tables(space: Any) -> dict[str, Any]:
    """The frozen prior tables (hashed into prior_hash, which differs by the /2 -> /3 definition): compared directly."""
    return {tid: {str(r): {op: [c.as_dict() for c in cs] for op, cs in sorted(by_op.items())}
                  for r, by_op in sorted(t.items())} for tid, t in sorted(space.prior.items())}


def make_space(side: Side, *, nc: int = 1, imgsz: int = 64) -> Any:
    return side.mod("space").SearchSpace.create(nc=nc, imgsz=imgsz, families=("yolo11n", "yolo26n"),
                                                etri_root=side.etri_root)


# -- scenarios -------------------------------------------------------------------------------------------------------
def scenario_t2_1(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Structure gate: ``check_structure`` documents of the fixed YAML set at input 64 / 160."""
    inputs = ctx["inputs"]
    registration = side.register_mixed()
    G, S = side.mod("genotype"), side.mod("structure")
    docs: dict[str, Any] = {}
    for name, spec in inputs["yamls"].items():
        for size in inputs["sizes"]:
            key = f"{name}@{size}"
            try:
                g = G.Genotype.from_yaml_dict(copy.deepcopy(spec["yaml"]), family=spec["family"],
                                              template_id=f"t2-1:{name}")
            except Exception as exc:  # noqa: BLE001 - a rejected declaration is data
                docs[key] = {"genotype_error": err(exc)}
                continue
            try:
                docs[key] = S.check_structure(g, input_size=size, class_count=inputs["class_count"])
            except Exception as exc:  # noqa: BLE001
                docs[key] = {"check_error": err(exc)}
    info: dict[str, Any] = {"registration": registration}
    if side.name == "new":  # DD-A20 pre-build gate of the console layer (new only; not part of check_structure)
        info["validate_declared"] = {name: G.validate_declared(copy.deepcopy(spec["yaml"]))
                                     for name, spec in inputs["yamls"].items()}
    return {"result": {"docs": docs}, "info": info}


def scenario_t2_2(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Random gen0: frozen-prior proposals of seeds 101/202/303 (population 4, nc=1, imgsz=64, default families)."""
    space = make_space(side)
    desc = space.describe()
    streams, rnd = side.mod("streams"), side.mod("random")
    proposals, rng_after = {}, {}
    for seed in (101, 202, 303):
        rng = streams.search_rng(seed, "IndependentRandom", 0)
        props = rnd.propose_generation(space, rng, 4)
        proposals[str(seed)] = [proposal_record(side, p, space, desc) for p in props]
        rng_after[str(seed)] = rng_digest(rng)
    presets = side.mod("presets")
    result = {"space": desc, "prior_tables": prior_tables(space), "proposals": proposals, "rng_after": rng_after,
              "presets": {"pilot_smoke": presets.pilot_smoke_protocol(), "default": presets.default_protocol(),
                          "summaries": presets.preset_summaries()}}
    return {"result": result, "info": {}}


def _pool_from_gen0(gen0: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for seed in ("101", "202"):
        for i, rec in enumerate(gen0[seed]):
            fit, lat = POOL_TABLE[len(rows)]
            cid = f"t2-3:s{seed}:c{i:03d}"
            rows.append({"candidate_id": cid, "evaluation_id": f"{cid}.L0.eval", "rung": "L0", "status": "EVALUATED",
                         "fitness": fit, "latency_ms": lat, "genotype": rec["genotype"]})
    return rows


def scenario_t2_3(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """GA gen0 / gen1 with a fixed synthetic L0 parent pool (8 rows), crossover 0.5, mutation 0.15."""
    space = make_space(side)
    desc = space.describe()
    streams, rnd, ga = side.mod("streams"), side.mod("random"), side.mod("ga")
    gen0 = {}
    for seed in (101, 202, 303):
        rng = streams.search_rng(seed, "GA", 0)
        gen0[str(seed)] = [proposal_record(side, p, space, desc) for p in rnd.propose_generation(space, rng, 4)]
    pool = _pool_from_gen0(gen0) if side.name == "ref" else ctx["ref"]["inputs"]["parent_pool"]
    rows = [ga.ParentRow.from_dict(r) for r in pool]
    gen1 = {}
    for seed in (101, 202, 303):
        rng = streams.search_rng(seed, "GA", 1)
        cfg = ga.GAConfig(population=4, mutation_rate=0.15, crossover_rate=0.5, parent_rung="L0")
        try:
            children, record = ga.propose_generation(space, rows, rng, cfg)
            gen1[str(seed)] = {"selection": record, "children": [proposal_record(side, c, space, desc) for c in children],
                               "rng_after": rng_digest(rng)}
        except Exception as exc:  # noqa: BLE001
            gen1[str(seed)] = {"error": err(exc)}
    negatives = {}
    base = rows[0]
    cfg = ga.GAConfig(population=2, mutation_rate=0.0, crossover_rate=0.0)
    for label, extra in (("l1_row", ga.ParentRow("x-l1", "x-l1.L1.eval", "L1", 0.9, 1.0, base.genotype)),
                         ("uncommitted", ga.ParentRow("x-tr", "x-tr.L0.eval", "L0", 0.9, 1.0, base.genotype,
                                                      status="TRAINING")),
                         ("duplicate", ga.ParentRow(base.candidate_id, "dup.L0.eval", "L0", 0.1, 1.0, base.genotype))):
        try:
            ga.propose_generation(space, rows + [extra], random.Random(1), cfg)
            negatives[label] = {"raised": False}
        except Exception as exc:  # noqa: BLE001
            negatives[label] = {"raised": True, **err(exc)}
    try:
        ga.propose_generation(space, [ga.ParentRow("x-none", "x-none.L0.eval", "L0", None, None, base.genotype)],
                              random.Random(1), cfg)
        negatives["no_valid_fitness"] = {"raised": False}
    except Exception as exc:  # noqa: BLE001
        negatives["no_valid_fitness"] = {"raised": True, **err(exc)}
    shared = {}  # generation 0 is the arm-independent frozen-prior stream: GA gen0 == IndependentRandom gen0
    for seed in (101, 202, 303):
        rnd_props = rnd.propose_generation(space, streams.search_rng(seed, "IndependentRandom", 0), 4)
        shared[str(seed)] = [p.genotype.yaml_hash() for p in rnd_props] == [r["yaml_hash"] for r in gen0[str(seed)]]
    return {"inputs": {"parent_pool": pool, "ga": {"population": 4, "mutation_rate": 0.15, "crossover_rate": 0.5,
                                                    "parent_rung": "L0"}},
            "result": {"space": desc, "prior_tables": prior_tables(space), "gen0": gen0, "gen1": gen1,
                       "negatives": negatives,
                       "gen0_shared_with_random_arm": shared}, "info": {}}


def scenario_t2_4(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Repair evidence seed 116 (``test_repair_evidence_seed`` inputs) through the PROPOSE_GENERATION handler."""
    if side.name == "ref":
        proto = side.mod("presets").pilot_smoke_protocol()
        proto["console"]["search"].update(population=2)
    else:
        proto = copy.deepcopy(ctx["ref"]["inputs"]["protocol"])
    J, H = side.mod("jobs_schema"), side.mod("handlers")
    arms = {}
    for arm in ("GA", "IndependentRandom"):
        spec = J.JobSpec(job_id=f"job-{arm}-0", run_id="run-test", kind=J.JobKind.PROPOSE_GENERATION,
                         protocol_hash=side.protocol_hash(proto), arm=arm, search_seed=116,
                         params={"protocol": proto, "generation": 0, "nc": 1})
        res = H.propose_generation(spec)
        out = res.outputs or {}
        cands = []
        for c in out.get("candidates") or []:
            row = c["row"]
            cands.append({"id": row["id"], "name": row["name"], "operation": row["lineage"]["operation"],
                          "prior_draw": row["lineage"]["prior_draw"], "parents": row["parents"],
                          "repair_events": row["lineage"]["repair_events"],
                          "repaired_blocks": [b for b in row["blocks"] if b["source_kind"] == "repair"],
                          "architecture_yaml": row["architecture_yaml"], "candidate_status": row["candidate_status"],
                          "training_seed": row["training_seed"]})
        ev = (cands[1]["repair_events"] if len(cands) > 1 else [])
        expectation = (len(ev) == 1 and ev[0]["row"] == 9 and ev[0]["violation_row"] == 10
                       and "C2PSA requires c1 == c2" in ev[0]["reason"] and ev[0]["before"][2] == "C3TR"
                       and ev[0]["after"][2] == "SPPF" and not cands[0]["repair_events"])
        arms[arm] = {"status": res.status, "reason_code": res.reason_code, "message": res.message,
                     "protocol_hash_matches_spec": out.get("protocol_hash") == spec.protocol_hash,
                     "planned_count": out.get("planned_count"), "rng_stream": out.get("rng_stream"),
                     "candidates": cands, "expectation_row9_c3tr_to_sppf": expectation}
    return {"inputs": {"protocol": proto, "search_seed": 116, "population": 2, "nc": 1}, "result": {"arms": arms},
            "info": {"protocol_hash": side.protocol_hash(proto)}}


def scenario_t2_5(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """fitness() / evaluate_gate() / fitness_and_gate() over the fixed evaluation table."""
    inp = ctx["inputs"]
    F, P, FG = side.mod("fitness"), side.mod("promotion"), side.mod("fitness_gate")
    anchors = F.FitnessAnchors.from_protocol(inp["fitness"])
    rungs = inp["rungs"]
    out: dict[str, Any] = {"fitness": [], "gate": [], "fitness_and_gate": [], "misc": {}}
    for i, (ap, rec, lat, size, src) in enumerate(inp["rows"]):
        for rung in rungs:
            try:
                fr = F.fitness(ap, rec, lat, size, rung["fitness_weights"], anchors)
                frd = fr.as_dict()
            except Exception as exc:  # noqa: BLE001
                fr, frd = None, err(exc)
            out["fitness"].append({"row": i, "rung": rung["id"], "result": frd})
            for hc_id, hc in inp["hard_constraints"].items():
                if fr is None:
                    gate = {"skipped": "fitness raised"}
                else:
                    try:
                        gate = P.evaluate_gate(fr, ap, lat, size, rung, hc, latency_source=src).as_dict()
                    except Exception as exc:  # noqa: BLE001
                        gate = err(exc)
                out["gate"].append({"row": i, "rung": rung["id"], "hc": hc_id, "result": gate})
                proto = {"rungs": rungs, "fitness": inp["fitness"], "hard_constraints": hc}
                payload = {"ap": ap, "recall": rec, "latency_ms": lat, "size_mb": size, "latency_source": src}
                try:
                    fg = FG.fitness_and_gate(proto, rung["id"], payload)
                except Exception as exc:  # noqa: BLE001
                    fg = err(exc)
                out["fitness_and_gate"].append({"row": i, "rung": rung["id"], "hc": hc_id, "result": fg})
    misc = out["misc"]
    fr0 = F.fitness(0.45, 0.6, 20.0, 5.0, rungs[0]["fitness_weights"], anchors)
    for label, call in (
            ("gate_with_other_rung_weights", lambda: P.evaluate_gate(fr0, 0.45, 20.0, 5.0, rungs[1],
                                                                     inp["hard_constraints"]["HC_A"],
                                                                     latency_source="measured")),
            ("gate_with_other_metrics", lambda: P.evaluate_gate(fr0, 0.46, 20.0, 5.0, rungs[0],
                                                                inp["hard_constraints"]["HC_A"],
                                                                latency_source="measured")),
            ("weights_sum_not_one", lambda: F.check_weights({"ap": 0.5, "recall": 0.5, "latency_reward": 0.5,
                                                             "size_reward": 0.0})),
            ("weights_unknown_key", lambda: F.check_weights({"ap": 1.0})),
            ("weights_negative", lambda: F.check_weights({"ap": 1.5, "recall": -0.5, "latency_reward": 0.0,
                                                          "size_reward": 0.0})),
            ("anchors_inverted", lambda: F.FitnessAnchors(latency_ms=(10.0, 5.0), size_mb=(1.0, 2.0))),
            ("anchors_zero", lambda: F.FitnessAnchors(latency_ms=(0.0, 5.0), size_mb=(1.0, 2.0))),
            ("unknown_rung", lambda: FG.fitness_and_gate({"rungs": rungs, "fitness": inp["fitness"],
                                                          "hard_constraints": inp["hard_constraints"]["HC_A"]},
                                                         "L9", {"ap": 0.3})),
    ):
        try:
            misc[label] = {"value": call()}
        except Exception as exc:  # noqa: BLE001
            misc[label] = err(exc)
    misc["rewards"] = [[F.reward_latency(v, (5.0, 200.0)), F.reward_size(v, (2.0, 60.0))]
                       for v in (-1.0, 0.0, 2.0, 5.0, 31.0, 60.0, 102.5, 200.0, 1e9)]
    misc["certifying"] = [P.certifying_latency_sources(hc) for hc in inp["hard_constraints"].values()]
    misc["formula_version"] = F.FORMULA_VERSION
    return {"result": out, "info": {}}


def _ev_row(cid: str, arm: str, seed: int | None, rung: str, epochs: int, run_hash: str = "sha256:t2") -> dict[str, Any]:
    return {"id": f"{cid}.{rung}.eval", "candidate_id": cid, "name": cid, "family": "f", "arm": arm, "generation": 0,
            "rung": rung, "epochs": epochs, "status": "EVALUATED", "ap": 0.4, "recall": 0.3, "latency_ms": 20.0,
            "size_mb": 10.0, "fitness": 0.5, "device": "d", "precision": "FP16", "latency_source": "measured",
            "parents": [], "blocks": [], "architecture_path": None, "checkpoint_path": "/c.pt", "artifacts": [],
            "objective_hash": "o", "evaluation_hash": "e", "split_hash": "s", "latency_scope": "synthetic",
            "comparison_group": "common_scratch", "protocol_hash": run_hash, "batch": 1,
            "resolved_input_shape": [64, 64], "search_seed": seed}


def scenario_t2_6(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Admission decisions (12 cap/billed/reserved combinations) and settle_run transitions on a synthetic DB."""
    settings = side.settings()
    D, R, A, C, J, E = (side.mod(k) for k in ("dbcore", "repo", "admission", "costs", "jobs", "engine"))
    conn = D.connect(settings)
    w = side.work
    t0, t1 = "2026-10-06T00:00:00.000000Z", "2026-10-06T01:00:00.000000Z"

    def cost(run_id: str, arm: str, kind: str, secs: float, *, billed: str | None, cand: str | None,
             job_kind: str = "CANDIDATE_TRAIN", physical: bool = True) -> None:
        R.append_cost(conn, run_id=run_id, arm=arm, kind=kind, gpu_seconds=secs, physical=physical, billed_arm=billed,
                      candidate_id=cand, job_id=None, attempt=1, started_at=t0, finished_at=t1,
                      meta={"job_kind": job_kind, "outcome": "succeeded"}, now=t1)

    def cand(run_id: str, cid: str, arm: str, seed: int | None, lineage: dict[str, Any] | None = None) -> dict[str, Any]:
        return R.insert_candidate(conn, id=cid, run_id=run_id, name=cid, arm=arm, family="f", generation=0,
                                  search_seed=seed, candidate_status="QUEUED", lineage=lineage or {}, now=t0)

    def queued(run_id: str, cid: str, est: float, key: str, status: str = "QUEUED") -> None:
        job, _ = J.ensure_job(conn, run_id=run_id, kind="CANDIDATE_TRAIN", protocol_hash="sha256:t2", candidate_id=cid,
                              rung="L0", cumulative_target=2, fixed_horizon=4,
                              params={"admission": {"estimate_s": est}}, key_extra=key, now=t0)
        if status != "QUEUED":
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job["id"]))

    run_id, nocap = "run-t2-6-adm", "run-t2-6-nocap"
    resolved = resolved_minimal(w, seeds=list(range(11, 23)))
    with D.tx(conn):
        R.create_run(conn, id=run_id, namespace="t2", protocol_hash="sha256:t2", resolved_protocol=resolved,
                     requested_protocol={}, execution_mode="PILOT", idempotency_key="t2-6-adm", payload_sha256="x",
                     budget=A.frozen_budget(resolved), status="RUNNING", now=t0)
        nores = resolved_minimal(w, search_h=None, baseline_h=None, final_h=None)
        R.create_run(conn, id=nocap, namespace="t2", protocol_hash="sha256:t2", resolved_protocol=nores,
                     requested_protocol={}, execution_mode="PILOT", idempotency_key="t2-6-nocap", payload_sha256="y",
                     budget=A.frozen_budget(nores), status="RUNNING", now=t0)
        # case seeds 11..22 keep every GA/IR case independent (search caps are per arm per seed)
        cand(run_id, "c11", "GA", 11); cost(run_id, "GA", "train", 100.0, billed="GA", cand="c11")
        R.insert_evaluation(conn, run_id, _ev_row("c11", "GA", 11, "L0", 2), now=t1)            # rate 50 s/epoch
        cand(run_id, "c12a", "GA", 12); cost(run_id, "GA", "train", 120.0, billed="GA", cand="c12a")
        R.insert_evaluation(conn, run_id, _ev_row("c12a", "GA", 12, "L0", 3), now=t1)            # rate 40
        cand(run_id, "c12b", "GA", 12)                                                            # no own rate
        cand(run_id, "c13", "GA", 13)                                                             # nothing measured
        cand(run_id, "c14", "GA", 14); cost(run_id, "GA", "failed", 3600.0, billed="GA", cand="c14")
        cand(run_id, "c15", "GA", 15); cost(run_id, "GA", "train", 3400.0, billed="GA", cand="c15")
        R.insert_evaluation(conn, run_id, _ev_row("c15", "GA", 15, "L0", 2), now=t1)            # rate 1700; rem 200
        cand(run_id, "c16", "GA", 16); cost(run_id, "GA", "train", 3400.0, billed="GA", cand="c16")
        R.insert_evaluation(conn, run_id, _ev_row("c16", "GA", 16, "L0", 34), now=t1)           # rate 100; rem 200
        cand(run_id, "c17", "GA", 17); cost(run_id, "GA", "train", 400.0, billed="GA", cand="c17")
        R.insert_evaluation(conn, run_id, _ev_row("c17", "GA", 17, "L0", 4), now=t1)            # rate 100
        cand(run_id, "c17q", "GA", 17); queued(run_id, "c17q", 1500.0, "q1")
        cand(run_id, "c17r", "GA", 17); queued(run_id, "c17r", 1000.0, "q2", status="LEASED")
        cand(run_id, "c17s", "GA", 17); queued(run_id, "c17s", 999.0, "q3", status="SUCCEEDED")  # not reserved
        cand(run_id, "c18", "GA", 18)
        cost(run_id, "GA", "retry", 200.0, billed="GA", cand="c18")
        cost(run_id, "GA", "profile", 900.0, billed=None, cand="c18", job_kind="BENCHMARK")      # never billed
        cand(run_id, "c19", "IndependentRandom", 19); cost(run_id, "GA", "train", 3600.0, billed="GA", cand="c19")
        cand(run_id, "b1", "Baseline", None)
        cost(run_id, "Baseline", "train", 500.0, billed=None, cand="b1", job_kind="BASELINE_TRAIN")
        cost(run_id, "Baseline", "profile", 300.0, billed=None, cand="b1", job_kind="BASELINE_TRAIN")
        cost(run_id, "Baseline", "val", 50.0, billed=None, cand="b1", job_kind="EVALUATE")
        R.insert_evaluation(conn, run_id, _ev_row("b1", "Baseline", None, "L0", 5), now=t1)       # rate 100
        cand(run_id, "b1q", "Baseline", None); queued(run_id, "b1q", 100.0, "q4")
        cand(run_id, "src1", "GA", 21); cost(run_id, "GA", "train", 90.0, billed="GA", cand="src1")
        R.insert_evaluation(conn, run_id, _ev_row("src1", "GA", 21, "L0", 3), now=t1)           # rate 30
        cand(run_id, "fin1", "Final", 21, lineage={"source_candidate_id": "src1"})
        cost(run_id, "Final", "train", 600.0, billed=None, cand="fin1", job_kind="FINAL_RETRAIN")
        cand(run_id, "fin2", "Final", 22, lineage={"source_candidate_id": "missing"})
        cand(nocap, "n1", "GA", 1); cand(nocap, "n2", "Baseline", None); cand(nocap, "n3", "Final", 1)
    cases = [("A1_own_rate", run_id, "c11", 2),                   # est 100 <= rem 3500 -> ADMITTED
             ("A2_arm_seed_mean", run_id, "c12b", 3),             # arm/seed mean rate 40 -> est 120
             ("A3_unmeasured", run_id, "c13", 1),                 # unknown estimate, rem 3600 > 0
             ("A4_zero_remaining_unknown", run_id, "c14", 1),     # failed row billed 3600 -> rem 0, unknown -> EXHAUSTED
             ("A5_remaining_eq_estimate", run_id, "c16", 2),      # est 200 == rem 200 -> ADMITTED (>=)
             ("A6_remaining_below_estimate", run_id, "c15", 1),   # est 1700 > rem 200 -> EXHAUSTED
             ("A7_reserved", run_id, "c17", 8),                   # rem 3600-400-2500=700 < est 800 (QUEUED+LEASED only)
             ("A8_retry_billed_profile_not", run_id, "c18", 1),   # retry 200 billed, profile 900 never billed
             ("A9_baseline", run_id, "b1", 2),                    # 900-(500+50)-100=250 >= est 200 (profile excluded)
             ("A10_final_source_rate", run_id, "fin1", 4),        # finalist source rate 30 -> est 120
             ("A10b_final_unmeasured", run_id, "fin2", 4),        # source unknown, Final arm unmeasured
             ("A11_ir_isolated_from_ga", run_id, "c19", 1),       # GA-billed row of an IR candidate: IR rem 3600
             ("A12_no_caps_ga", nocap, "n1", 1), ("A12_no_caps_baseline", nocap, "n2", 1),
             ("A12_no_caps_final", nocap, "n3", 1)]
    admissions = {label: A.admit(conn, R.get_run(conn, rid), R.get_candidate(conn, cid), epochs).as_dict()
                  for label, rid, cid, epochs in cases}
    remaining = {f"{arm}|{seed}": A.remaining_seconds(conn, R.get_run(conn, run_id), arm, seed)
                 for arm in ("GA", "IndependentRandom") for seed in (11, 12, 13, 14, 15, 16, 17, 18, 19, 21)}
    remaining["nocap"] = A.remaining_seconds(conn, R.get_run(conn, nocap), "GA", 1)
    budgets = {"frozen": A.frozen_budget(resolved), "frozen_nocap": A.frozen_budget(nores)}
    rates = {cid: C.seconds_per_epoch(conn, cid) for cid in ("c11", "c12a", "c12b", "c15", "b1", "src1", "fin1")}
    ledger = {"billed": {f"{arm}|{seed}": C.billed_seconds(conn, run_id, arm, seed)
                         for arm in ("GA", "IndependentRandom") for seed in (11, 17, 18, 19)},
              "baseline_budget_s": C.arm_budget_seconds(conn, run_id, "Baseline"),
              "baseline_physical_s": C.arm_physical_seconds(conn, run_id, "Baseline"),
              "aggregate": R.aggregate_costs(conn, run_id), "run_costs": C.run_costs(conn, run_id)}
    settle = _settle_cases(side, conn, w)
    conn.close()
    return {"result": {"admission": admissions, "remaining": remaining, "budgets": budgets, "rates": rates,
                       "ledger": ledger, "settle": settle}, "info": {}}


def _settle_cases(side: Side, conn: Any, w: Path) -> dict[str, Any]:
    D, R, J, E, P = (side.mod(k) for k in ("dbcore", "repo", "jobs", "engine", "planner"))
    t0 = "2026-10-06T00:00:00.000000Z"
    out: dict[str, Any] = {}

    def run(rid: str, status: str, resolved: dict[str, Any], **kw: Any) -> None:
        R.create_run(conn, id=rid, namespace="t2", protocol_hash="sha256:t2", resolved_protocol=resolved,
                     requested_protocol={}, execution_mode="PILOT", idempotency_key=rid, payload_sha256=rid,
                     status=status, now=t0, **kw)

    def job(rid: str, key: str, status: str, cid: str | None = None, kind: str | None = None,
            params: dict[str, Any] | None = None) -> str:
        kind = kind or ("CANDIDATE_TRAIN" if cid else "EVALUATE")
        train = {"cumulative_target": 2, "fixed_horizon": 4} if kind == "CANDIDATE_TRAIN" else {}
        j, _ = J.ensure_job(conn, run_id=rid, kind=kind, protocol_hash="sha256:t2", candidate_id=cid,
                            rung="L0" if cid else None, params=params or {}, key_extra=key, now=t0, **train)
        if status != "QUEUED":
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, j["id"]))
        return j["id"]

    res = resolved_minimal(w)
    gen = resolved_minimal(w, generation_only=True, seeds=[7, 8])
    with D.tx(conn):
        run("s1-open", "RUNNING", res); job("s1-open", "a", "QUEUED")
        run("s2-done", "RUNNING", res); job("s2-done", "a", "SUCCEEDED")
        R.insert_candidate(conn, id="s2c", run_id="s2-done", name="s2c", arm="GA", family="f", search_seed=1, now=t0)
        R.insert_evaluation(conn, "s2-done", _ev_row("s2c", "GA", 1, "L0", 2), now=t0)
        run("s3-failed", "RUNNING", res); job("s3-failed", "a", "FAILED")
        run("s4-pause-active", "PAUSE_REQUESTED", res); job("s4-pause-active", "a", "LEASED")
        run("s5-pause-idle", "PAUSE_REQUESTED", res); job("s5-pause-idle", "a", "QUEUED")
        run("s6-cancel", "CANCEL_REQUESTED", res, budget={})
        R.insert_candidate(conn, id="s6c", run_id="s6-cancel", name="s6c", arm="GA", family="f", search_seed=1,
                           candidate_status="QUEUED", current_rung="L0", now=t0)
        jid = job("s6-cancel", "a", "QUEUED", cid="s6c")
        P.set_rung_state(conn, "s6c", "L0", P.PENDING, job_id=jid)
        run("s7-gen-failed", "RUNNING", gen); job("s7-gen-failed", "a", "FAILED", kind="PROPOSE_GENERATION",
                                                  params={"generation_only": True})
        run("s8-gen-complete", "RUNNING", gen, budget={"generation_results": {
            "7": {"generated_count": 2, "requested_count": 2}, "8": {"generated_count": 2, "requested_count": 2}}})
        job("s8-gen-complete", "a", "SUCCEEDED", kind="PROPOSE_GENERATION", params={"generation_only": True})
        run("s9-gen-partial", "RUNNING", gen, budget={"generation_results": {
            "7": {"generated_count": 2, "requested_count": 2}, "8": {"generated_count": 1, "requested_count": 2}}})
        job("s9-gen-partial", "a", "SUCCEEDED", kind="PROPOSE_GENERATION", params={"generation_only": True})
        run("s10-gen-empty", "RUNNING", gen, budget={"generation_results": {}})
        run("s11-queued-nojobs", "QUEUED", res)
        run("s12-parent", "COMPLETED", res)
        run("s12-child", "RUNNING", res, kind="FINAL_RETRAIN", parent_run_id="s12-parent")
        job("s12-child", "a", "SUCCEEDED")
        R.insert_candidate(conn, id="s12c", run_id="s12-child", name="s12c", arm="Final", family="f", search_seed=1,
                           now=t0)
        R.insert_evaluation(conn, "s12-child", _ev_row("s12c", "Final", 1, "FINAL", 2), now=t0)
    for rid in ("s1-open", "s2-done", "s3-failed", "s4-pause-active", "s5-pause-idle", "s6-cancel", "s7-gen-failed",
                "s8-gen-complete", "s9-gen-partial", "s10-gen-empty", "s11-queued-nojobs", "s12-child"):
        try:
            with D.tx(conn):
                after = E.settle_run(conn, rid)
            out[rid] = {"status": after["status"], "reason_code": after.get("reason_code"),
                        "terminal": after["terminal"], "cancel": after.get("cancel"),
                        "jobs": [{"key": j["idempotency_key"], "status": j["status"]} for j in R.list_jobs(conn, rid)],
                        "candidates": [{"id": c["id"], "status": c["candidate_status"], "rung": c["current_rung"],
                                        "audit": c.get("audit")} for c in R.list_candidates(conn, rid)],
                        "evaluations": [{"id": e["id"], "status": e["status"], "row": e["row"]}
                                        for e in R.list_evaluations(conn, rid)],
                        "events": [{"type": e["type"], "payload": e["payload"]}
                                   for e in R.get_events_after(conn, rid, 0, 1000)]}
        except Exception as exc:  # noqa: BLE001
            out[rid] = {"error": err(exc)}
    out["s12-parent-events"] = [{"type": e["type"], "payload": e["payload"]}
                                for e in R.get_events_after(conn, "s12-parent", 0, 1000)]
    out["shortfall"] = {rid: E.generation_shortfall(R.get_run(conn, rid))
                        for rid in ("s8-gen-complete", "s9-gen-partial", "s10-gen-empty")}
    return out


# -- T2-7 / T2-8: engine flow with the InlineWorker harness and the stub handlers ------------------------------------
def _validate(side: Side, settings: Any, requested: dict[str, Any], registry: dict[str, Any],
              devices: list[dict[str, Any]]) -> Any:
    V = side.mod("validator")
    vctx = V.ValidationContext(settings=settings, registry_lookup=registry.get, devices=devices,
                               dataset_service=side.mod("manifest").DatasetService.from_settings(settings),
                               now=lambda: FIXED_NOW, disk_free_gb=lambda _path: 1000.0)
    return V.validate(copy.deepcopy(requested), vctx)


def _validation_record(res: Any) -> dict[str, Any]:
    return {"valid": res.valid, "errors": res.errors, "warnings": res.warnings, "resolved": res.resolved,
            "protocol_hash": res.protocol_hash, "capabilities": res.capabilities, "resolved_diff": res.resolved_diff}


def _create_run(side: Side, conn: Any, run_id: str, resolved: dict[str, Any], requested: dict[str, Any],
                key: str) -> dict[str, Any]:
    """The run-creation transaction of ``POST /runs`` (routes_runs.create_run) without the HTTP layer."""
    D, R, E, A = (side.mod(k) for k in ("dbcore", "repo", "engine", "admission"))
    console = resolved["console"]
    atomic, art = side.mod("atomic"), side.mod("artifacts")
    shell = {"id": run_id, "resolved_protocol": resolved}
    atomic.atomic_write_json(art.run_dir(shell) / "protocol" / "resolved.json", resolved)
    atomic.atomic_write_json(art.run_dir(shell) / "protocol" / "requested.json", requested)
    with D.tx(conn):
        run, created = R.create_run(
            conn, id=run_id, namespace=console.get("run_namespace") or "default",
            protocol_hash=side.protocol_hash(resolved), resolved_protocol=resolved, requested_protocol=requested,
            execution_mode=console.get("execution_mode") or "PILOT", idempotency_key=key, payload_sha256=key,
            purpose=console.get("purpose"), preset=console.get("preset"), validation_id=None,
            budget=A.frozen_budget(resolved), source_commit=None)
        if created:
            side.mod("bus").emit(conn, run_id, "run.updated", side.mod("views").run_payload(run))
            E.log(conn, run_id, "run created", validation_id=None, request_id="t2")
            E.plan_run(conn, run_id)
            run = E.settle_run(conn, run_id)
    return run


def _inline_worker(side: Side, settings: Any, order: list[dict[str, Any]]) -> Any:
    worker = side.harness().make_inline_worker(settings)
    original = worker.execute

    def execute(job: dict[str, Any]) -> None:
        order.append({"job_id": job["id"], "run_id": job["run_id"], "kind": job["kind"],
                      "candidate_id": job.get("candidate_id"), "rung": job.get("rung"), "attempt": job.get("attempt")})
        original(job)

    worker.execute = execute
    return worker


def db_snapshot(side: Side, conn: Any, run_id: str) -> dict[str, Any]:
    R = side.mod("repo")
    return {"run": R.get_run(conn, run_id), "candidates": R.list_candidates(conn, run_id),
            "evaluations": R.list_evaluations(conn, run_id, include_superseded=True),
            "generations": R.list_generations(conn, run_id), "jobs": R.list_jobs(conn, run_id),
            "costs": R.list_costs(conn, run_id), "checkpoints": R.list_checkpoints(conn, run_id),
            "artifacts": R.list_artifacts(conn, run_id),
            "events": [e for e in R.get_events_after(conn, run_id, 0, 1_000_000) if e["type"] != "worker.updated"],
            "worker_events": [e for e in R.get_events_after(conn, run_id, 0, 1_000_000)
                              if e["type"] == "worker.updated"]}


def _engine_requested(side: Side, ctx: dict[str, Any], ds: dict[str, str]) -> dict[str, Any]:
    if side.name != "ref":
        return copy.deepcopy(ctx["ref"]["inputs"]["requested_protocol"])
    proto = side.harness().small_protocol(ds, str(side.work / "runs" / "out"), population=2, generations=2,
                                          targets=(1, 2), score_ge=0.52)
    proto["console"]["search"]["seeds"] = [101, 202]
    proto["comparison"]["independent_search_seed_pairs"] = [101, 202]
    return proto


def _engine_flow(side: Side, ctx: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    settings = side.settings()
    ds = make_dataset(side.work / "data")
    registry = ctx["inputs"]["registry"]
    requested = _engine_requested(side, ctx, ds)
    res = _validate(side, settings, requested, registry, [copy.deepcopy(A100)])
    validation = _validation_record(res)
    if not res.valid:
        raise RuntimeError(f"T2 engine protocol did not validate on side {side.name}: {res.errors[:5]}")
    resolved = res.resolved if side.name == "ref" else copy.deepcopy(ctx["ref"]["inputs"]["resolved_protocol"])
    conn = side.mod("dbcore").connect(settings)
    os.environ["MBNAS_JOB_HANDLERS_MODULE"] = "stub_handlers"
    os.environ["MBNAS_STUB_EPOCH_S"] = "0"
    run_id = "run-t2-engine"
    _create_run(side, conn, run_id, resolved, requested, "t2-engine")
    order: list[dict[str, Any]] = []
    worker = _inline_worker(side, settings, order)
    executed = side.harness().drain(worker, limit=5000)
    inputs = {"requested_protocol": requested, "resolved_protocol": resolved, "dataset": {
        k: v for k, v in ds.items()}}
    flow = {"validation": validation, "protocol_hash": side.protocol_hash(resolved), "executed": executed,
            "execution_order": order, "db": db_snapshot(side, conn, run_id)}
    return inputs, flow, conn, (worker, settings, order)


def scenario_t2_7(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Engine flow: PILOT-like protocol (2 rungs, 2 seeds, population 2) through the InlineWorker + stub handlers."""
    inputs, flow, conn, (worker, _settings, _order) = _engine_flow(side, ctx)
    workers = side.mod("repo").list_workers(conn)
    worker.conn.close()
    conn.close()
    return {"inputs": inputs, "result": flow, "info": {"workers": workers}}


def _final_table(side: Side, conn: Any) -> dict[str, Any]:
    """Frozen finalist rule on the hand-made rows of the original tests/orchestration/test_final_selection.py."""
    D, R, FR = side.mod("dbcore"), side.mod("repo"), side.mod("final_retrain")
    resolved = {"rungs": [{"id": "L0"}, {"id": "L1"}, {"id": "L2"}],
                "hard_constraints": {"latency_le_ms": 1000, "weights_size_le_MB": 500},
                "final_retraining": {"top_k_per_arm_per_seed": 1, "epoch_target_total": 2,
                                     "scheduler_horizon_epochs": 2, "initialization": "scratch_with_pinned_recipe"}}

    def row(cid: str, arm: str, seed: int | None, rung: str, ap: float, lat: float, size: float = 10.0,
            source: str = "measured", **over: Any) -> dict[str, Any]:
        r = _ev_row(cid, arm, seed, rung, 2, "sha256:p")
        r.update(ap=ap, recall=0.1, latency_ms=lat, size_mb=size, latency_source=source, **over)
        return r

    with D.tx(conn):
        R.create_run(conn, id="run-p", namespace="t", protocol_hash="sha256:p", resolved_protocol=resolved,
                     requested_protocol={}, execution_mode="PILOT", idempotency_key="k-final-table",
                     payload_sha256="x", status="COMPLETED")
        rows = [row("ga-c1", "GA", 101, "L2", 0.5, 10.0), row("ga-c2", "GA", 101, "L2", 0.5, 8.0),
                row("ga-c3", "GA", 101, "L2", 0.5, 8.0), row("ga-c0", "GA", 101, "L1", 0.99, 1.0),
                row("ga-c4", "GA", 202, "L2", 0.9, 5.0, source="calibrated_proxy"),
                row("ga-c5", "GA", 202, "L2", 0.2, 50.0), row("rnd-c7", "IndependentRandom", 101, "L2", 0.4, 2000.0),
                row("rnd-c8", "IndependentRandom", 101, "L2", 0.3, 20.0, size=600.0),
                row("base-1", "Baseline", None, "L2", 0.99, 1.0), row("rnd-c9", "IndependentRandom", 202, "L2", 0.97, 4.0)]
        for r in rows:
            R.insert_candidate(conn, id=r["candidate_id"], run_id="run-p", name=r["name"], arm=r["arm"], family="f",
                               search_seed=r["search_seed"], candidate_status="SEARCH_COMPLETE")
            R.insert_evaluation(conn, "run-p", r)
        R.supersede_evaluation(conn, "rnd-c9.L2.eval", row("rnd-c9", "IndependentRandom", 202, "L2", 0.1, 4.0,
                                                            id="rnd-c9.L2.eval.r2"))
    parent = R.get_run(conn, "run-p")
    return {"top1": FR.select_finalists(conn, parent, 1), "top3": FR.select_finalists(conn, parent, 3),
            "eligible": [r["id"] for r in FR.eligible_rows(conn, parent)],
            "rule": FR.selection_rule(resolved, 2), "recipe": FR.final_recipe(resolved)}


def scenario_t2_8(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Final selection: the T2-7 run + final-retrain requests -> finalists (rule, source), child run plan and run."""
    inputs, flow, conn, (worker, settings, order) = _engine_flow(side, ctx)
    FR, api, R = side.mod("final_retrain"), side.mod("api_schema"), side.mod("repo")
    parent_id = "run-t2-engine"
    requests: dict[str, Any] = {}
    children: list[str] = []
    calls = (("k1_default_topk", dict(idempotency_key="t2-final-1", budget_gpu_hours=0.5, reason="t2-8")),
             ("k1_replay", dict(idempotency_key="t2-final-1", budget_gpu_hours=0.5, reason="t2-8")),
             ("k1_conflict", dict(idempotency_key="t2-final-1", budget_gpu_hours=0.25, reason="t2-8")),
             ("k2_top2", dict(idempotency_key="t2-final-2", budget_gpu_hours=0.5, top_k_per_arm_per_seed=2)),
             ("budget_invalid", dict(idempotency_key="t2-final-3", budget_gpu_hours=float("inf"))))
    for label, kw in calls:
        try:
            req = api.FinalRetrainRequest(**kw)
        except Exception as exc:  # noqa: BLE001 - request schema rejection is data
            requests[label] = {"request_error": err(exc)}
            continue
        try:
            child, created = FR.request_final_retrain(conn, parent_id, req, source_commit=None, request_id=label)
            requests[label] = {"child_id": child["id"], "created": created, "status": child["status"],
                               "finalists": child.get("finalists"), "budget": child.get("budget")}
            if created:
                children.append(child["id"])
        except Exception as exc:  # noqa: BLE001
            requests[label] = {"raised": True, **err(exc), **{k: getattr(exc, k) for k in ("status", "code", "errors")
                                                             if hasattr(exc, k)}}
    plans = {cid: db_snapshot(side, conn, cid) for cid in children}
    before = len(order)
    executed = side.harness().drain(worker, limit=5000)
    after = {cid: db_snapshot(side, conn, cid) for cid in children}
    table = _final_table(side, conn)
    parent = db_snapshot(side, conn, parent_id)
    result = {"parent_status": flow["db"]["run"]["status"], "requests": requests, "child_plans": plans,
              "child_execution_order": order[before:], "child_executed": executed, "children_after": after,
              "parent_after": {"run": parent["run"], "events": parent["events"][-12:]}, "final_table": table,
              "selection_inputs": [e for e in flow["db"]["evaluations"]
                                   if e["status"] == "EVALUATED" and e["row"].get("rung") == "L1"]}
    worker.conn.close()
    conn.close()
    return {"inputs": inputs, "result": result, "info": {}}


def scenario_t2_9(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Seed streams: search_rng / training_seed / ids for 100 fixed (seed, arm, generation) inputs."""
    S, E, FR = side.mod("streams"), side.mod("engine"), side.mod("final_retrain")
    policies = [{"training": {"seed_policy": 1234}}, {"training": {"seed_policy": {"base_seed": 77}}}, {"training": {}},
                {"training": {"seed_policy": True}}, {}]
    seeds = (0, 1, 7, 42, 101, 116, 202, 303, 2 ** 31 - 1, 10 ** 12)
    rows = []
    for seed in seeds:
        for arm in ("GA", "IndependentRandom"):
            for gen in (0, 1, 2, 5, 9):
                rng = S.search_rng(seed, arm, gen)
                draws = [rng.random() for _ in range(4)]
                draws += [rng.randrange(1, 1000), rng.sample(range(50), 3), rng.choice("abcdefgh")]
                pols = [S.training_seed_policy(p, seed) for p in policies]
                rows.append({"seed": seed, "arm": arm, "gen": gen, "key": S.search_stream_key(seed, arm, gen),
                             "draws": draws, "rng_after": rng_digest(rng), "policies": pols,
                             "training_seeds": [[S.training_seed(p, gen, idx) for idx in (0, 1, 7, 63)] for p in pols],
                             "names": [S.candidate_name(arm, gen, idx) for idx in (0, 7, 123)],
                             "ids": [S.candidate_id("run-x", arm, seed, gen, idx) for idx in (0, 7)],
                             "derive_seed": [E.derive_seed("baseline", "yolo11n", seed),
                                             E.derive_seed("search-train", seed, gen, 3)],
                             "final_seed": [FR.final_training_seed(p, seed, f"run-x:GA:s{seed}:g{gen:02d}:c000")
                                            for p in policies[:3]]})
    consts = {"SEARCH_STREAM_ID": S.SEARCH_STREAM_ID, "TRAIN_SEED_ID": S.TRAIN_SEED_ID, "ARM_CODES": S.ARM_CODES}
    return {"result": {"rows": rows, "constants": consts, "count": len(rows)}, "info": {}}


def scenario_t2_10(side: Side, ctx: dict[str, Any]) -> dict[str, Any]:
    """Generation-only (OFF) run: seeds 2, candidate_count 2, max_attempts 6, real CPU proposer + structure checks."""
    os.environ.update(side.handler_env())
    os.environ.pop("MBNAS_JOB_HANDLERS_MODULE", None)
    os.environ.pop("MBNAS_WORKER_ID", None)
    settings = side.settings()
    requested = copy.deepcopy(ctx["inputs"]["requested"])
    requested["console"]["paths"]["output_root"] = str(side.work / "runs" / "out")
    res = _validate(side, settings, requested, {}, [])
    validation = _validation_record(res)
    if not res.valid:
        raise RuntimeError(f"T2-10 OFF protocol did not validate on side {side.name}: {res.errors[:5]}")
    resolved = res.resolved if side.name == "ref" else copy.deepcopy(ctx["ref"]["inputs"]["resolved_protocol"])
    conn = side.mod("dbcore").connect(settings)
    run_id = "run-t2-10"
    _create_run(side, conn, run_id, resolved, requested, "t2-10")
    order: list[dict[str, Any]] = []
    worker = _inline_worker(side, settings, order)
    executed = side.harness().drain(worker, limit=100)
    arch_dir = side.work / "runs" / "out" / run_id / "architectures"
    journals = {p.name: json.loads(p.read_text()) for p in sorted(arch_dir.glob("generation-*.json"))}
    yaml_files = {p.name: p.read_text() for p in sorted(arch_dir.glob("*.yaml"))}
    snap = db_snapshot(side, conn, run_id)
    worker.conn.close()
    conn.close()
    return {"inputs": {"requested_protocol": requested, "resolved_protocol": resolved},
            "result": {"validation": validation, "protocol_hash": side.protocol_hash(resolved), "executed": executed,
                       "execution_order": order, "journals": journals, "yaml_files": yaml_files, "db": snap},
            "info": {}}


# seeds: 7 accepts its first two draws; 19 draws a duplicate architecture on attempt 2 (probed with the yolo11n space at
# 64 px: "ADA"), so the per-seed duplicate rule is exercised (DD-2); families as in the original OFF test request
T2_10_REQUESTED = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": "<WORK>/runs/out"},
                   "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64,
                              "families": ["yolo11n"], "seeds": [7, 19], "candidate_count": 2,
                              "max_attempts": 6, "allow_duplicates": False, "arms": ["GA"]}}}

SCENARIO_FUNCS: dict[str, Callable[[Side, dict[str, Any]], dict[str, Any]]] = {
    "T2-1": scenario_t2_1, "T2-2": scenario_t2_2, "T2-3": scenario_t2_3, "T2-4": scenario_t2_4,
    "T2-5": scenario_t2_5, "T2-6": scenario_t2_6, "T2-7": scenario_t2_7, "T2-8": scenario_t2_8,
    "T2-9": scenario_t2_9, "T2-10": scenario_t2_10,
}
NEEDS_REF: frozenset[str] = frozenset({"T2-3", "T2-4", "T2-7", "T2-8", "T2-10"})  # new side reads ref-produced inputs


def scenario_inputs(scenario: str, registry_doc: dict[str, Any] | None) -> dict[str, Any]:
    """Fixed inputs of a scenario (written by the orchestrator to ``inputs/<scenario>.input.json``)."""
    if scenario == "T2-1":
        return t2_1_inputs()
    if scenario == "T2-5":
        return t2_5_inputs()
    if scenario in ("T2-7", "T2-8"):
        reg = {}
        for entry in (registry_doc or {}).get("registry", []):
            e = copy.deepcopy(entry)
            e["status"], e["reason"] = "AVAILABLE", "synthetic test registry (example entries forced AVAILABLE)"
            reg[e["id"]] = e
        return {"registry": reg, "stub_epoch_s": 0, "run_id": "run-t2-engine", "seeds": [101, 202],
                "population": 2, "generations": 2, "rung_targets": [1, 2], "score_ge": 0.52}
    if scenario == "T2-10":
        return {"requested": copy.deepcopy(T2_10_REQUESTED)}
    return {}


def run_scenario(side: Side, scenario: str, ctx: dict[str, Any]) -> dict[str, Any]:
    out = SCENARIO_FUNCS[scenario](side, ctx)
    out.setdefault("inputs", {})
    out.setdefault("info", {})
    return out


# -- runner entry (called by reference_runner.py / new_runner.py after their sys.path set-up) -----------------------
def _module_files() -> dict[str, str]:
    out = {}
    for name, mod in list(sys.modules.items()):
        file = getattr(mod, "__file__", None) if mod is not None else None
        if file:
            out[name] = str(Path(file).resolve())
    return out


def isolation_report(side: Side) -> dict[str, Any]:
    """Which code this process actually executed: the side's packages must come from the expected roots only."""
    files = _module_files()
    snap = str(side.snapshot_root.resolve())
    original = str((side.repo_root.parent / "etri_nas").resolve())  # the live original checkout: never imported
    new_src = [str((side.repo_root / p / "src").resolve()) for p in
               ("mbnas_contracts", "mbnas_core", "mbnas_model_runtime", "mbnas_runner", "mbnas_server")]
    violations = []
    for name, path in sorted(files.items()):
        if path.startswith(original + os.sep):
            violations.append(f"{name}: imported from the live original checkout {path}")
        if side.name == "ref":
            if any(path.startswith(p + os.sep) for p in new_src):
                violations.append(f"{name}: new package imported in the reference process ({path})")
            if (name == "mbnas_server" or name.startswith("mbnas_server.")) and not path.startswith(snap + os.sep):
                violations.append(f"{name}: mbnas_server not from the snapshot ({path})")
            if name.split(".")[0] in ("mbnas_core", "mbnas_contracts", "mbnas_model_runtime", "mbnas_runner"):
                violations.append(f"{name}: new package module loaded in the reference process")
        else:
            if path.startswith(snap + os.sep):
                violations.append(f"{name}: snapshot code imported in the new process ({path})")
            if name.split(".")[0] in ("yolo_ga_runtime", "yolo_ga_full_runtime"):
                violations.append(f"{name}: ETRI runtime imported in the new process")
    used = sorted({m for m in side.modules.values()})
    origins = {}
    for name in used:
        path = files.get(name)
        if path:
            origins[name] = {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    etri = {n: p for n, p in files.items() if n.split(".")[0] == "yolo_ga_runtime"}
    return {"violations": violations, "side_module_origins": origins, "etri_modules": etri,
            "loaded_mbnas_server_files": sum(1 for n in files if n.split(".")[0] == "mbnas_server")}


def versions() -> dict[str, Any]:
    from importlib import metadata

    out: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable}
    for dist in ("torch", "ultralytics", "numpy", "pydantic", "PyYAML", "Pillow"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
    return out


def host_substitutions(work: Path, snapshot_root: Path, repo_root: Path) -> list[tuple[str, str]]:
    """Absolute roots and the host name -> tokens (longest first), applied to everything a runner writes."""
    import pwd
    import socket

    subs = [(str(work), "<WORK>"), (str(snapshot_root), "<SNAPSHOT>"), (str(repo_root), "<REPO>"),
            (pwd.getpwuid(os.getuid()).pw_dir, "<HOME>"), (f"{socket.gethostname()}-gpu-", "<HOSTNAME>-gpu-")]
    return sorted(((a, b) for a, b in subs if a), key=lambda x: -len(x[0]))


def scrub(value: Any, subs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for raw, token in subs:
            if raw in value:
                value = value.replace(raw, token)
        return value
    if isinstance(value, dict):
        return {scrub(k, subs): scrub(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, subs) for v in value]
    return value


def runner_main(side_name: str, argv: list[str], *, repo_root: Path, snapshot_root: Path, harness: dict[str, Any],
                path_note: list[str]) -> int:
    import argparse
    import time
    import traceback

    ap = argparse.ArgumentParser(description=f"T2 {side_name} runner (owner only)")
    ap.add_argument("--scenario", required=True, choices=SCENARIOS)
    ap.add_argument("--work", required=True, help="work directory (wiped by the orchestrator between sides)")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--inputs", default=None, help="fixed input JSON of the scenario")
    ap.add_argument("--ref", default=None, help="reference output JSON (new side, two-phase scenarios)")
    args = ap.parse_args(argv)
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    side = Side(side_name, repo_root=repo_root, snapshot_root=snapshot_root, work=work)
    os.environ.update(side.handler_env())
    side.install_hash_recorder()
    ctx: dict[str, Any] = {"inputs": json.loads(Path(args.inputs).read_text()) if args.inputs else {}, "ref": None}
    if side_name == "new" and args.scenario in NEEDS_REF:
        if not args.ref:
            raise SystemExit(f"{args.scenario} needs --ref (inputs produced by the reference side)")
        # the reference output is written with host roots tokenised (scrub); the work directory is the same absolute
        # path for both sides, so the inverse substitution restores the exact inputs the reference used
        inverse = [(token, raw) for raw, token in host_substitutions(work, snapshot_root, repo_root)]
        ctx["ref"] = scrub(json.loads(Path(args.ref).read_text()), sorted(inverse, key=lambda x: -len(x[0])))
    started = time.monotonic()
    status, out = "ok", {}
    try:
        out = run_scenario(side, args.scenario, ctx)
    except Exception:  # noqa: BLE001 - recorded; the orchestrator reports the scenario as RUNNER_ERROR
        status = "error"
        out = {"inputs": {}, "result": {"$runner_error": traceback.format_exc()[-6000:]}, "info": {}}
    elapsed = round(time.monotonic() - started, 3)
    iso = isolation_report(side)
    inputs = canon(out.get("inputs") or {})
    doc = {"scenario": args.scenario, "side": side_name, "status": status,
           "fixed_inputs_sha256": sha256_json(ctx["inputs"]) if ctx["inputs"] else None,
           "inputs": inputs, "inputs_sha256": sha256_json(inputs),
           "ref_inputs_sha256": sha256_json(canon(ctx["ref"]["inputs"])) if ctx.get("ref") else None,
           "result": canon(out.get("result")), "info": canon(out.get("info") or {}),
           "meta": {"harness": harness, "sys_path_head": path_note, "versions": versions(), "isolation": iso,
                    "work_root": str(work), "snapshot_root": str(snapshot_root), "repo_root": str(repo_root),
                    "arch_hash_map": dict(sorted(side.hash_map.items())), "arch_hash_conflicts": side.hash_conflicts,
                    "elapsed_s": elapsed}}
    doc = scrub(doc, host_substitutions(work, snapshot_root, repo_root))  # no host paths / host name in evidence
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(args.out) + ".tmp")
    tmp.write_text(dumps(doc) + "\n", encoding="utf-8")
    os.replace(tmp, args.out)
    if iso["violations"]:
        print("ISOLATION VIOLATIONS:\n" + "\n".join(iso["violations"]), file=sys.stderr)
        return 3
    return 0 if status == "ok" else 2
