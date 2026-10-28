"""Protocol validation and resolution.

``validate(requested, ctx)`` never mutates ``requested``. It collects *all* problems as
``{field, message, code}`` errors (warnings use the same shape), and only when there is no error returns the
immutable resolved protocol plus ``protocol_hash`` = canonical SHA-256 of the resolved document minus
``RESOLUTION_HASH_EXCLUDED`` fields. Re-validating a resolved protocol is a fixed point (same hash), so the Run
endpoint can re-validate whatever the client echoes back and compare hashes.

Codes: the base validation codes plus FIELD_MISSING / FIELD_INVALID (structure and type), DATASET_YAML_PATH_MISMATCH,
DATASET_EMPTY, LABEL_INVALID, IMAGE_INVALID (data content), EVALUATION_REQUIRES_CUDA (an evaluation-ON
protocol on a CPU-only device inventory or with a CPU benchmark device); warning-only codes: HORIZON_ADJUSTED,
BASELINE_OVERRIDE, DUPLICATE_SAMPLE, DATASET_HASH_RECOMPUTED, BUDGET_INCONSISTENT, DATASET_YAML_NO_TEST,
DATASET_YAML_IGNORED_KEYS, INPUT_RULE_MISSING.

Resolution rules worth knowing:
* fixed schedule horizon = last rung's cumulative epoch target (``training.fixed_horizon_epochs``); a larger
  requested ``scheduler_horizon_epochs`` is kept for provenance but produces HORIZON_ADJUSTED.
* each baseline resolves ``init`` (entry ``init`` or ``training.initialization``) against the registry's declared
  ``capabilities.init_modes``: scratch -> comparison_group ``common_scratch``; ``pretrained_native`` (only when the
  entry asks for it explicitly or the protocol's policy is pretrained_native) -> ``native_pretrained``. An init the
  adapter cannot do is INIT_UNSUPPORTED — never silently replaced.
* resolved input shape follows ``capabilities.input_size_rule``: ``{"kind":"stride_ceil","stride":s}`` rounds each
  side up to a multiple of ``s`` (``"square": true`` first takes the longer side for both); ``{"kind":"fixed",
  "shape":[h,w]}`` uses that shape. No rule -> requested shape kept with INPUT_RULE_MISSING warning.
* execution_mode defaults to PILOT; HISTORICAL_REPRODUCTION is not supported; REGISTERED_SEARCH needs a
  readable ``console.extra.pilot_calibration_ref`` file under an output root and a non-example, non-smoke policy.
* ``training.backend_resolved.ultralytics`` records what ultralytics 8.4.173 will actually do with this recipe on
  the train split: batches/epoch = ceil(n/batch), warmup_iterations =
  round(min(warmup, horizon-1) * nb), optimizer iterations = ceil(n/max(batch, nbs)) * horizon and, for
  ``optimizer=auto``, the chosen optimizer (AdamW lr=round(0.002*5/(4+nc), 6), or MuSGD 0.01 above 10000
  iterations; warmup_bias_lr 0) with ``training.lr_initial`` reported as ignored in ``resolved_diff``.
* each resolved baseline carries the registry's ``inference_head`` (e.g. YOLO26 ``e2e_one2one_nms_false``).
* ``console.search.crossover_rate`` (GA crossover probability per child, [0, 1]) defaults to
  :data:`DEFAULT_CROSSOVER_RATE` (``resolved_diff`` reason ``defaulted``); the legacy alias
  ``console.extra.ga.crossover_rate`` is still read when the search field is absent (warning LEGACY_ALIAS; a
  conflicting pair is FIELD_INVALID).
* ``console.search.allow_native_fallback`` (bool, default false, hashed): when the ETRI mixed operator pool cannot be
  loaded (torch-free probe :func:`~mbnas_core.search.mixed_pool.bridge.probe_mixed_pool`) a search run is
  SEARCH_SPACE_UNAVAILABLE unless this is true; with it the run resolves to the ``native_fallback`` space (warning
  SEARCH_SPACE_FALLBACK). The resolved variant is frozen in ``console.search.search_space_resolved``
  (``etri_mixed`` | ``native_fallback`` | ``not_required`` for a baseline-only run) and reported in
  ``capabilities.search_space``.
* ``console.benchmark.postprocess`` = the latency operating point of the ``forward_and_postprocess`` scope
  (:data:`DEFAULT_POSTPROCESS` conf 0.25 / iou 0.7 / max_det 300 per missing key, ``resolved_diff`` reason
  ``defaulted``); it is part of the protocol hash. The AP evaluation keeps its own floor (conf 0.001, max_det 300).
* ``console.retention.full_state`` (keep_all | rung_end_only | last_rung_end_only) defaults to the server setting
  ``MBNAS_FULL_STATE_RETENTION`` and is frozen in the resolved protocol; it is a disk-housekeeping policy, so it is
  excluded from the protocol hash (two protocols that differ only in retention share a cohort).
* ``final_retraining`` epochs (``epoch_target_total`` int >= 1, ``scheduler_horizon_epochs`` >= it) and
  ``top_k_per_arm_per_seed`` (int >= 1) are checked when present (the final recipe is pinned at validation).
"""
from __future__ import annotations

import copy
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from mbnas_core.config import Settings
from mbnas_model_runtime.datasets.manifest import DatasetService, Manifest, cross_split_overlap
from mbnas_model_runtime.provenance.env_lock import VALIDATION_PRECISIONS
from mbnas_model_runtime.datasets.yaml_safe import DatasetYamlError, load_dataset_yaml
from mbnas_contracts.schemas.api import RegistryEntry
from mbnas_contracts.schemas.enums import RegistryStatus
from mbnas_contracts.util.canon import CANONICALIZATION_ID, canonical_json
from mbnas_contracts.util.paths import check_server_path, free_disk_gb
from mbnas_contracts.util.time import utcnow_iso
from mbnas_contracts.constants import DEFAULT_BASELINE14, DEFAULT_POSTPROCESS
from mbnas_contracts.protocol_hash import RESOLUTION_HASH_EXCLUDED, protocol_hash
from mbnas_core.protocol.presets import PILOT_SMOKE_ID

VALIDATOR_VERSION = "mbnas-validator/1"
DEFAULT_CROSSOVER_RATE = 0.5
RETENTION_POLICIES = ("keep_all", "rung_end_only", "last_rung_end_only")
WEIGHT_KEYS = ("ap", "recall", "latency_reward", "size_reward")
WEIGHT_TOLERANCE = 1e-6
SEARCH_ARMS = ("GA", "IndependentRandom")
EXECUTION_MODES = ("PILOT", "REGISTERED_SEARCH", "HISTORICAL_REPRODUCTION")
TRAIN_PRECISIONS = ("amp_fp16_fp32", "fp32")
INIT_GROUPS = {"scratch": "common_scratch", "pretrained_native": "native_pretrained"}
DEFAULT_DEVICE_PRECISIONS = ("FP32", "FP16")
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DATASET_PATH_FIELDS = (("dataset_yaml", "file"), ("train_root", "dir"), ("search_val_root", "dir"),
                       ("final_eval_root", "dir"))
SPLIT_FIELDS = {"train": ("train_root", "train", "train_manifest_hash"),
                "search_val": ("search_val_root", "val", "search_validation_manifest_hash"),
                "final_eval": ("final_eval_root", "test", "final_evaluation_manifest_hash")}

KNOWN_KEYS: dict[str, frozenset[str]] = {
    "$": frozenset({
        "schema_version", "protocol_id", "status", "value_provenance", "git_commit", "backend_versions",
        "backend_capabilities_verified", "schedule_scope", "dataset", "training", "continuation", "fitness", "rungs",
        "hard_constraints", "allocation", "comparison", "proposal", "final_retraining", "console",
        "unresolved_before_real_execution", "original_status", "resolution"}),
    "console": frozenset({
        "execution_mode", "paths", "baselines", "search", "benchmark", "baseline_epoch_target",
        "baseline_capture_rungs", "baseline_budget_gpu_hours", "preset", "purpose", "run_namespace", "label", "extra",
        "baseline_override_reason", "baseline_override", "retention"}),
    "console.paths": frozenset({"dataset_yaml", "train_root", "search_val_root", "final_eval_root", "output_root"}),
    "console.search": frozenset({"arms", "population", "generations", "mutation_rate", "seeds", "budget_gpu_hours",
                                 "crossover_rate", "allow_native_fallback", "search_space_resolved", "evaluation_enabled",
                                 "candidate_count", "max_attempts", "allow_duplicates", "nc", "input_size",
                                 "families", "requested_method", "effective_method", "duplicate_scope"}),
    "console.benchmark": frozenset({"device", "precision", "batch", "warmup", "repeats", "device_resolved",
                                    "postprocess"}),
    "console.benchmark.postprocess": frozenset(DEFAULT_POSTPROCESS),
    "console.retention": frozenset({"full_state"}),
    "training": frozenset({
        "initialization", "weight_inheritance", "optimizer", "lr_initial", "lr_final_ratio", "weight_decay", "betas",
        "momentum", "scheduler", "scheduler_horizon_epochs", "warmup_epochs", "repeat_warmup_on_promotion",
        "batch_per_gpu", "gradient_accumulation", "gpus_per_candidate", "precision", "requested_input_size",
        "resolved_input_size_required", "automatic_batch_fallback", "augmentation_profile", "close_mosaic_epochs",
        "loss_profile", "change_loss_weights_at_promotion", "candidate_early_stop", "validation_interval_epochs",
        "workers", "deterministic", "seed_policy", "fixed_horizon_epochs", "effective_batch", "nbs",
        "backend_resolved"}),
    "rung": frozenset({"id", "epoch_target_total", "fitness_weights", "promotion", "epochs_added"}),
    "promotion": frozenset({"to", "score_ge", "ap_ge", "latency_le_ms", "weights_size_le_MB"}),
    "hard_constraints": frozenset({"latency_le_ms", "weights_size_le_MB", "proxy_may_certify_final_deployment"}),
}


@dataclass
class ValidationContext:
    """Everything the validator may consult; injectable for tests.

    ``registry_lookup(id)`` returns a RegistryEntry (or dict of that shape) or None; ``devices`` are dicts with
    ``id``, ``uuid``, ``name`` and optional ``aliases`` / ``precisions`` (local NVML, or the registered runners'
    devices: :func:`devices_from_runners`, ``MBNAS_CORE_DEVICE_SOURCE=runners``, whose CPU-only runners form one
    ``{"id": "cpu", "uuid": None, "kind": "cpu"}`` entry; :func:`is_cpu_device`).

    ``manifest_provider`` (optional): ``provider(role, root, names) -> Manifest`` supplies
    the split manifest (samples, counts, class mapping, split hash) of ``role`` in {train, search_val, final_eval}
    instead of ``dataset_service.manifest_for`` reading the files (the gateway computes it on the customer host).

    ``gateway`` (optional): :class:`mbnas_core.protocol.gateway_inputs.GatewayInputs`
    built from the validate request's ``path_checks`` / ``dataset_yaml`` / ``dataset_manifests``. When it carries
    ``path_checks`` the path rules use them instead of ``check_server_path``/``free_disk_gb``; when it carries
    ``dataset_yaml`` the dataset YAML summary, the per-split manifest summaries and the cross-split overlap come from it
    (a split without a summary is a ``MANIFEST_MISSING`` error) instead of the core filesystem.

    ``gateway_local_fallback`` (explicit training runs only): the training selection rewrites the split roots of
    ``console.paths`` from the dataset YAML before validating, so the gateway may not have measured those exact paths;
    a path the gateway sent no matching check for (``PATH_CHECK_MISSING`` / ``PATH_CHECK_MISMATCH``) is then checked on
    this host instead of being refused (in a split deployment that local check fails with the core's own path code).
    """

    settings: Settings
    registry_lookup: Callable[[str], RegistryEntry | dict[str, Any] | None]
    devices: list[dict[str, Any]]
    dataset_service: DatasetService
    now: Callable[[], str] = utcnow_iso
    disk_free_gb: Callable[[str], float] = free_disk_gb
    manifest_provider: Callable[[str, str, dict[int, str]], Manifest] | None = None
    gateway: Any = None
    gateway_local_fallback: bool = False

    @property
    def gateway_paths(self) -> bool:
        return self.gateway is not None and self.gateway.path_checks is not None

    @property
    def gateway_dataset(self) -> bool:
        return self.gateway is not None and self.gateway.has_dataset

    def free_gb(self, key: str, real: str) -> float:
        """Free disk of the checked path: the gateway's measurement when it sent one, else this host's."""
        if self.gateway_paths:
            value = self.gateway.free_gb(key)
            if value is not None:
                return float(value)
        return self.disk_free_gb(real)

    def read_dataset_yaml(self, path: str, required_splits: tuple[str, ...] = ("train", "val")) -> Any:
        if self.gateway_dataset:
            return self.gateway.dataset_yaml(required_splits)
        return load_dataset_yaml(path, required_splits=required_splits)

    def split_realpath(self, path: str) -> str:
        """``os.path.realpath`` on this host, or the gateway's already-resolved path as is."""
        return path if self.gateway_dataset else os.path.realpath(path)

    def split_manifest(self, role: str, root: str, names: dict[int, str]) -> Any:
        if self.gateway_dataset:
            return self.gateway.manifest(role, root, names)
        if self.manifest_provider is not None:
            return self.manifest_provider(role, root, names)
        return self.dataset_service.manifest_for(root, names)

    def overlap(self, manifests: dict[str, Any]) -> list[dict[str, Any]]:
        if self.gateway_dataset:
            return self.gateway.overlap(list(manifests))
        return cross_split_overlap(manifests)


def sample_count(manifest: Any) -> int:
    """Number of samples of a full :class:`Manifest` or of a gateway summary view."""
    count = getattr(manifest, "sample_count", None)
    return int(count) if count is not None else len(manifest.samples)


CPU_DEVICE = "cpu"
"""Inventory id and alias of the device of the CPU-only runners (registered ``device = "cpu"``, no uuid)."""


def is_cpu_device(dev: Any) -> bool:
    """True for the CPU entry of :func:`devices_from_runners` (``kind == "cpu"``) and for a resolved benchmark device
    ``{"id": "cpu", "uuid": None}``; NVML / CUDA entries always carry a uuid."""
    if not isinstance(dev, dict):
        return False
    return dev.get("kind") == "cpu" or (dev.get("uuid") is None and str(dev.get("id") or "").lower() == CPU_DEVICE)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    resolved: dict[str, Any] | None
    protocol_hash: str | None
    capabilities: dict[str, Any]
    resolved_diff: list[dict[str, Any]]

    def to_response(self, validation_id: str | None = None) -> dict[str, Any]:
        """ValidateResponse body (contract component)."""
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings, "resolved": self.resolved,
                "protocol_hash": self.protocol_hash, "capabilities": self.capabilities,
                "resolved_diff": self.resolved_diff, "validation_id": validation_id}


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _dict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _group(init: Any) -> str | None:
    """Comparison group of an initialization mode, or None for unsupported/non-string values."""
    return INIT_GROUPS.get(init) if isinstance(init, str) else None


class _Run:
    """One validation pass; methods append to errors/warnings/diff and fill ``self.out`` (the resolved doc)."""

    def __init__(self, requested: dict[str, Any], ctx: ValidationContext) -> None:
        self.req = requested
        self.ctx = ctx
        self.out: dict[str, Any] = copy.deepcopy(requested)
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.diff: list[dict[str, Any]] = []
        self.caps: dict[str, Any] = {}
        self.path_real: dict[str, str] = {}

    # -- recording -------------------------------------------------------------------------------------------
    def err(self, fld: str, code: str, message: str) -> None:
        self.errors.append({"field": fld, "message": message, "code": code})

    def warn(self, fld: str, code: str, message: str) -> None:
        self.warnings.append({"field": fld, "message": message, "code": code})

    def resolve(self, fld: str, requested: Any, resolved: Any, reason: str) -> None:
        if requested != resolved:
            self.diff.append({"field": fld, "requested": requested, "resolved": resolved, "reason": reason})

    def section(self, key: str, parent: dict[str, Any] | None = None, prefix: str = "") -> dict[str, Any] | None:
        src = self.req if parent is None else parent
        value = src.get(key)
        if not isinstance(value, dict):
            code = "FIELD_MISSING" if value is None else "FIELD_INVALID"
            self.err(prefix + key, code, f"'{prefix + key}' must be an object")
            return None
        return value

    def unknown(self, obj: dict[str, Any], known: str, prefix: str) -> None:
        for key in sorted(set(obj) - KNOWN_KEYS[known]):
            self.err(f"{prefix}{key}", "UNKNOWN_FIELD",
                     f"unknown field '{prefix}{key}'; put free-form data under console.extra")

    # -- structure -------------------------------------------------------------------------------------------
    def structure(self) -> bool:
        if not isinstance(self.req.get("schema_version"), str) or not self.req.get("schema_version"):
            self.err("schema_version", "FIELD_MISSING", "schema_version is required")
        original = self.req.get("original_status", self.req.get("status"))
        console = self.req.get("console")
        paths = console.get("paths") if isinstance(console, dict) else None
        if isinstance(original, str) and original.startswith("DESIGN_EXAMPLE") and not (
                isinstance(paths, dict) and all(isinstance(paths.get(k), str) and paths.get(k)
                                                for k in KNOWN_KEYS["console.paths"])):
            self.err("console.paths", "EXAMPLE_NOT_EXECUTABLE",
                     "design example without server paths is not executable; enter all five server paths")
            return False
        self.unknown(self.req, "$", "")
        console = self.section("console")
        if console is None:
            return False
        self.unknown(console, "console", "console.")
        ok = True
        if "extra" in console and not isinstance(console["extra"], dict):
            self.err("console.extra", "FIELD_INVALID", "console.extra must be an object")
            ok = False
        for key in ("paths", "search", "benchmark"):
            sub = self.section(key, console, "console.")
            ok &= sub is not None
            if sub is not None:
                self.unknown(sub, f"console.{key}", f"console.{key}.")
        if not isinstance(console.get("baselines"), list):
            self.err("console.baselines", "FIELD_MISSING", "console.baselines must be a list")
            ok = False
        for key in ("training", "fitness", "hard_constraints"):
            ok &= self.section(key) is not None
        for key in ("dataset", "comparison", "final_retraining"):
            if key in self.req and not isinstance(self.req[key], dict):
                self.err(key, "FIELD_INVALID", f"'{key}' must be an object")
                ok = False
        if not isinstance(self.req.get("rungs"), list) or not self.req["rungs"]:
            self.err("rungs", "FIELD_MISSING", "rungs must be a non-empty list")
            ok = False
        return ok

    # -- console mode / namespace -----------------------------------------------------------------------------
    def console_mode(self) -> None:
        c_req, c_out = self.req["console"], self.out["console"]
        preset = c_req.get("preset")
        mode = c_req.get("execution_mode")
        if mode is None:
            mode = "PILOT"
            self.resolve("console.execution_mode", None, mode, "no pilot calibration: execution is PILOT")
        if mode not in EXECUTION_MODES:
            self.err("console.execution_mode", "EXECUTION_MODE_INVALID", f"execution_mode must be one of {EXECUTION_MODES}")
        elif mode == "HISTORICAL_REPRODUCTION":
            self.err("console.execution_mode", "EXECUTION_MODE_INVALID",
                     "HISTORICAL_REPRODUCTION is not supported by this server (historical recipes are not reproduced)")
        elif mode == "REGISTERED_SEARCH":
            self.registered_search_gate(preset)
        if preset == PILOT_SMOKE_ID and mode != "PILOT":
            self.err("console.execution_mode", "EXECUTION_MODE_INVALID", "PILOT_SMOKE runs must use execution_mode PILOT")
        smoke = preset == PILOT_SMOKE_ID
        defaults = {"preset": None, "purpose": "SMOKE" if smoke else "SEARCH",
                    "run_namespace": "pilot_smoke" if smoke else "default"}
        for key, default in defaults.items():
            value = c_req.get(key)
            if value is None and default is not None:
                self.resolve(f"console.{key}", None, default, "defaulted from preset")
            c_out[key] = default if value is None else value
        c_out["execution_mode"] = mode
        ns, purpose = c_out["run_namespace"], c_out["purpose"]
        if not isinstance(ns, str) or not NAMESPACE_RE.match(ns):
            self.err("console.run_namespace", "FIELD_INVALID", "run_namespace must match [a-z0-9][a-z0-9_-]{0,63}")
        elif smoke and (ns != "pilot_smoke" or purpose != "SMOKE"):
            self.err("console.run_namespace", "FIELD_INVALID", "PILOT_SMOKE is isolated: namespace pilot_smoke, purpose SMOKE")
        elif not smoke and ns == "pilot_smoke":
            self.err("console.run_namespace", "FIELD_INVALID", "namespace pilot_smoke is reserved for the PILOT_SMOKE preset")

    def registered_search_gate(self, preset: Any) -> None:
        original = str(self.req.get("original_status", self.req.get("status", "")))
        extra = self.req["console"].get("extra") or {}
        ref = extra.get("pilot_calibration_ref") if isinstance(extra, dict) else None
        if original.startswith("DESIGN_EXAMPLE") or preset == PILOT_SMOKE_ID:
            self.err("console.execution_mode", "EXECUTION_MODE_INVALID",
                     "REGISTERED_SEARCH needs a non-example, non-smoke policy")
            return
        check = check_server_path(ref, kind="file", allow_roots=self.ctx.settings.output_roots) if ref else None
        if check is None or not check.ok:
            self.err("console.extra.pilot_calibration_ref", "EXECUTION_MODE_INVALID",
                     "REGISTERED_SEARCH requires console.extra.pilot_calibration_ref: a readable pilot calibration "
                     "file under an output root" + (f" ({check.message})" if check else ""))

    # -- training / rungs / fitness ----------------------------------------------------------------------------
    def rungs(self) -> list[int]:
        targets: list[int] = []
        ids: list[str] = []
        rungs = self.req["rungs"]
        for i, rung in enumerate(rungs):
            pre = f"rungs[{i}]"
            if not isinstance(rung, dict):
                self.err(pre, "FIELD_INVALID", "rung must be an object")
                ids.append(f"#{i}")
                continue
            self.unknown(rung, "rung", pre + ".")
            rid = rung.get("id")
            if not isinstance(rid, str) or not rid or rid in ids:
                self.err(pre + ".id", "FIELD_INVALID", "rung id must be a unique non-empty string")
            ids.append(rid if isinstance(rid, str) and rid else f"#{i}")
            target = rung.get("epoch_target_total")
            if not _int(target):
                self.err(pre + ".epoch_target_total", "EPOCHS_NOT_INT", "cumulative epoch target must be an integer")
            elif target < 1 or (targets and target <= targets[-1]):
                self.err(pre + ".epoch_target_total", "EPOCHS_NOT_INCREASING",
                         "cumulative epoch targets must be >=1 and strictly increasing")
            else:
                prev = targets[-1] if targets else 0
                manual = "epochs_added" in rung and not (_int(rung["epochs_added"]) and rung["epochs_added"] == target - prev)
                if manual:
                    # A manual value is never allowed to coexist with the cumulative targets; only an exact echo of
                    # the computed value (re-validating a resolved protocol) is accepted, keeping validate() a fixed point.
                    self.err(pre + ".epochs_added", "EPOCHS_ADDED_MANUAL",
                             f"added epochs are computed from the cumulative targets (epoch_target_total {target} - "
                             f"previous target {prev} = {target - prev}); remove the manual value "
                             f"{rung['epochs_added']!r}")
                else:
                    # a rejected manual value is never "resolved" -- no diff entry, no resolved write.
                    self.resolve(pre + ".epochs_added", rung.get("epochs_added"), target - prev,
                                 "cumulative target minus previous rung target (computed, never entered)")
                    self.out["rungs"][i]["epochs_added"] = target - prev
                targets.append(target)
            self.weights(rung.get("fitness_weights"), pre + ".fitness_weights")
        for i, rung in enumerate(rungs):
            if isinstance(rung, dict):
                self.promotion(rung.get("promotion"), i, ids)
        return targets if len(targets) == len(rungs) else []

    def weights(self, w: Any, fld: str) -> None:
        if not isinstance(w, dict):
            self.err(fld, "FIELD_MISSING", f"named weights {{{', '.join(WEIGHT_KEYS)}}} are required")
            return
        for key in sorted(set(w) - set(WEIGHT_KEYS)):
            self.err(f"{fld}.{key}", "UNKNOWN_FIELD", f"unknown fitness weight '{key}'")
        values = []
        for key in WEIGHT_KEYS:
            v = w.get(key)
            if not _num(v):
                self.err(f"{fld}.{key}", "FIELD_MISSING" if v is None else "FIELD_INVALID", "weight must be a finite number")
            elif v < 0:
                self.err(f"{fld}.{key}", "WEIGHTS_NEGATIVE", "weights must be >= 0")
            else:
                values.append(v)
        if len(values) == len(WEIGHT_KEYS) and abs(sum(values) - 1.0) > WEIGHT_TOLERANCE:
            self.err(fld, "WEIGHTS_SUM_NOT_ONE",
                     f"weights sum to {sum(values):.6g}, must be 1 within {WEIGHT_TOLERANCE} (no auto-normalisation)")

    def promotion(self, promo: Any, i: int, ids: list[str]) -> None:
        pre = f"rungs[{i}].promotion"
        last = i == len(ids) - 1
        if last:
            if promo is not None:
                self.err(pre, "FIELD_INVALID", "the last search rung must have promotion null")
            return
        if not isinstance(promo, dict):
            self.err(pre, "FIELD_MISSING", "non-final rungs need a promotion rule")
            return
        self.unknown(promo, "promotion", pre + ".")
        if promo.get("to") != ids[i + 1]:
            self.err(pre + ".to", "FIELD_INVALID", f"promotion.to must name the next rung '{ids[i + 1]}'")
        for key, code in (("score_ge", "THRESHOLD_RANGE"), ("ap_ge", "AP_GUARD_RANGE")):
            v = promo.get(key)
            if not _num(v) or not 0.0 <= v <= 1.0:
                self.err(f"{pre}.{key}", code, f"{key} must be a finite fraction in [0, 1]")
        for key in ("latency_le_ms", "weights_size_le_MB"):
            if key in promo and (not _num(promo[key]) or promo[key] <= 0):
                self.err(f"{pre}.{key}", "HARD_CONSTRAINT_INVALID", f"{key} must be a finite number > 0")

    def training(self, targets: list[int]) -> None:
        t, t_out = self.req["training"], self.out["training"]
        self.unknown(t, "training", "training.")
        warm = t.get("warmup_epochs", 0)
        if not _num(warm) or warm < 0:
            self.err("training.warmup_epochs", "FIELD_INVALID", "warmup_epochs must be a finite number >= 0")
        elif targets and warm >= targets[0]:
            self.err("training.warmup_epochs", "WARMUP_GE_L0", f"warmup_epochs {warm} must be < first rung target {targets[0]}")
        for key in ("batch_per_gpu", "gradient_accumulation", "gpus_per_candidate"):
            v = t.get(key, 1 if key != "batch_per_gpu" else None)
            if not _int(v) or v < 1:
                self.err(f"training.{key}", "FIELD_INVALID", f"{key} must be an integer >= 1")
        if t.get("precision") not in TRAIN_PRECISIONS:
            self.err("training.precision", "PRECISION_UNSUPPORTED", f"training precision must be one of {TRAIN_PRECISIONS}")
        size = t.get("requested_input_size")
        if not (isinstance(size, list) and len(size) == 2 and all(_int(s) and s >= 32 for s in size)):
            self.err("training.requested_input_size", "FIELD_INVALID", "requested_input_size must be [h, w] integers >= 32")
        if "workers" in t and (not _int(t["workers"]) or t["workers"] < 0):
            self.err("training.workers", "FIELD_INVALID", "workers must be an integer >= 0")
        if _group(t.get("initialization")) is None:
            self.err("training.initialization", "INIT_UNSUPPORTED", f"initialization must be one of {sorted(INIT_GROUPS)}")
        horizon = t.get("scheduler_horizon_epochs")
        if horizon is None:
            self.err("training.scheduler_horizon_epochs", "FIELD_MISSING", "scheduler_horizon_epochs is required")
        elif not _int(horizon):
            self.err("training.scheduler_horizon_epochs", "EPOCHS_NOT_INT", "scheduler_horizon_epochs must be an integer")
        elif targets and horizon < targets[-1]:
            self.err("training.scheduler_horizon_epochs", "HORIZON_LT_LAST_RUNG",
                     f"horizon {horizon} < last rung target {targets[-1]}")
        elif targets:
            if horizon != targets[-1]:
                self.warn("training.scheduler_horizon_epochs", "HORIZON_ADJUSTED",
                          f"fixed schedule horizon is the last rung target {targets[-1]}, not {horizon}")
            self.set_resolved(t_out, "training", "fixed_horizon_epochs", targets[-1], "last search rung cumulative target")
        batch, accum, gpus = t.get("batch_per_gpu"), t.get("gradient_accumulation", 1), t.get("gpus_per_candidate", 1)
        if all(_int(v) and v >= 1 for v in (batch, accum, gpus)):
            self.set_resolved(t_out, "training", "effective_batch", batch * accum * gpus, "batch_per_gpu*accumulation*gpus")
            self.set_resolved(t_out, "training", "nbs", batch * accum, "ultralytics nominal batch = batch*accumulation")

    def set_resolved(self, target: dict[str, Any], prefix: str, key: str, value: Any, reason: str) -> None:
        self.resolve(f"{prefix}.{key}", self.req_get(prefix, key), value, reason)
        target[key] = value

    def req_get(self, prefix: str, key: str) -> Any:
        node: Any = self.req
        for part in prefix.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node.get(key) if isinstance(node, dict) else None

    def fitness_and_constraints(self) -> None:
        fit = self.req["fitness"]
        for key in ("latency_bounds_ms", "size_bounds_MB"):
            b = fit.get(key)
            if not (isinstance(b, list) and len(b) == 2 and all(_num(x) for x in b) and 0 < b[0] < b[1]):
                self.err(f"fitness.{key}", "ANCHORS_INVALID", f"{key} must be [min, max] with 0 < min < max, finite")
        hc = self.req["hard_constraints"]
        self.unknown(hc, "hard_constraints", "hard_constraints.")
        for key in ("latency_le_ms", "weights_size_le_MB"):
            if not _num(hc.get(key)) or hc[key] <= 0:
                self.err(f"hard_constraints.{key}", "HARD_CONSTRAINT_INVALID", f"{key} must be a finite number > 0")
        fld = "hard_constraints.proxy_may_certify_final_deployment"
        if "proxy_may_certify_final_deployment" in hc:
            flag = hc["proxy_may_certify_final_deployment"]
            if not isinstance(flag, bool):
                self.err(fld, "FIELD_INVALID", "proxy_may_certify_final_deployment must be a boolean (true/false)")
            elif flag:
                self.warn(fld, "PROXY_PIPELINE_UNAVAILABLE",
                          "no calibrated proxy pipeline exists on this host: every latency is measured on the host GPU, "
                          "so this flag has no effect here. When true, the promotion gate lets latency_source="
                          "calibrated_proxy satisfy hard_constraints.latency_le_ms (unverified_proxy never does); "
                          "final selection still requires latency_source=measured.")
        if _dict(self.req.get("dataset")).get("test_access_during_search") is True:
            self.err("dataset.test_access_during_search", "FIELD_INVALID", "final evaluation data must not be used during search")

    # -- search / budget / seeds -----------------------------------------------------------------------------
    def search(self) -> list[str]:
        s = self.req["console"]["search"]
        arms = s.get("arms")
        baseline_only = isinstance(arms, list) and not arms
        if not isinstance(arms, list) or any(a not in SEARCH_ARMS for a in arms) or len(set(arms)) != len(arms):
            self.err("console.search.arms", "FIELD_MISSING" if arms is None else "FIELD_INVALID",
                     f"arms must be a list of distinct values from {SEARCH_ARMS} ([] for a baseline-only run)")
            arms = []
        if not baseline_only:
            for key, lo in (("population", 2), ("generations", 1)):
                if not _int(s.get(key)) or s[key] < lo:
                    self.err(f"console.search.{key}", "FIELD_INVALID", f"{key} must be an integer >= {lo}")
            mr = s.get("mutation_rate")
            if not _num(mr) or not 0 <= mr <= 1:
                self.err("console.search.mutation_rate", "FIELD_INVALID", "mutation_rate must be in [0, 1]")
            self.budget(s.get("budget_gpu_hours"), "console.search.budget_gpu_hours")
            seeds = s.get("seeds")
            if not (isinstance(seeds, list) and seeds and all(_int(x) and x >= 0 for x in seeds)
                    and len(set(seeds)) == len(seeds)):
                self.err("console.search.seeds", "SEEDS_INVALID", "seeds must be a non-empty list of unique integers >= 0")
            per_arm = _dict(self.req.get("comparison")).get("search_budget_GPU_hours_per_arm_per_seed")
            if per_arm is not None and per_arm != s.get("budget_gpu_hours"):
                self.warn("comparison.search_budget_GPU_hours_per_arm_per_seed", "BUDGET_INCONSISTENT",
                          "differs from console.search.budget_gpu_hours, which is the enforced cap")
        self.crossover_rate()
        self.search_space(arms)
        return arms

    def search_space(self, arms: list[str]) -> None:
        """Resolve ``console.search.allow_native_fallback`` and the search-space variant (see module docstring)."""
        from mbnas_core.search.mixed_pool.bridge import probe_mixed_pool

        s, out = self.req["console"]["search"], self.out["console"]["search"]
        fld = "console.search.allow_native_fallback"
        allow = s.get("allow_native_fallback")
        if allow is None:
            allow = False
            self.resolve(fld, None, False, "defaulted (the native_fallback space is opt-in)")
        elif not isinstance(allow, bool):
            self.err(fld, "FIELD_INVALID", "allow_native_fallback must be a boolean")
            return
        out["allow_native_fallback"] = allow
        probe = probe_mixed_pool()
        if not arms:
            variant, detail = "not_required", "baseline-only run (no search arms)"
        elif probe["available"]:
            variant, detail = "etri_mixed", f"vendored ETRI mixed operator pool ({probe['checked']})"
        elif allow:
            variant, detail = "native_fallback", f"ETRI mixed pool unavailable: {probe['reason']}"
            self.warn(fld, "SEARCH_SPACE_FALLBACK",
                      f"ETRI mixed pool unavailable ({probe['reason']}); the search uses the native_fallback space "
                      "(CNN + native attention operators only, no convolution-free operators)")
        else:
            self.err(fld, "SEARCH_SPACE_UNAVAILABLE",
                     f"ETRI mixed operator pool unavailable ({probe['reason']}); set "
                     "console.search.allow_native_fallback=true to search the native_fallback space instead")
            return
        # vendored pool: no external root; contract 1.5.0 records {variant} only (no etri_root)
        resolved = {"variant": variant}
        self.resolve("console.search.search_space_resolved", s.get("search_space_resolved"), resolved, detail)
        out["search_space_resolved"] = resolved
        self.caps["search_space"] = {**resolved, "allow_native_fallback": allow, "detail": detail,
                                     "probe": {k: probe[k] for k in ("available", "reason", "checked")}}

    def crossover_rate(self) -> None:
        """Resolve ``console.search.crossover_rate`` (default / legacy ``console.extra.ga.crossover_rate`` alias)."""
        s = self.req["console"]["search"]
        extra = self.req["console"].get("extra")
        legacy = _dict(_dict(extra).get("ga")).get("crossover_rate")
        requested = s.get("crossover_rate")
        legacy_field = "console.extra.ga.crossover_rate"
        if legacy is not None:
            self.warn(legacy_field, "LEGACY_ALIAS", "legacy alias of console.search.crossover_rate; set the search "
                                                   "field instead (the alias is read only when it is absent)")
            if not _num(legacy) or not 0 <= legacy <= 1:
                self.err(legacy_field, "FIELD_INVALID", "crossover_rate must be a finite number in [0, 1]")
                return
        if requested is not None:
            if not _num(requested) or not 0 <= requested <= 1:
                self.err("console.search.crossover_rate", "FIELD_INVALID", "crossover_rate must be a finite number in [0, 1]")
                return
            if legacy is not None and legacy != requested:
                self.err("console.search.crossover_rate", "FIELD_INVALID",
                         f"conflicts with the legacy alias {legacy_field}={legacy}; keep one value")
                return
            value = requested
        elif legacy is not None:
            value = legacy
            self.resolve("console.search.crossover_rate", None, value, f"legacy alias {legacy_field}")
        else:
            value = DEFAULT_CROSSOVER_RATE
            self.resolve("console.search.crossover_rate", None, value, "defaulted")
        self.out["console"]["search"]["crossover_rate"] = value

    def budget(self, value: Any, fld: str) -> None:
        if value is None:
            self.err(fld, "BUDGET_MISSING", "a GPU-hour budget is required (absent budget is never unlimited)")
        elif not _num(value) or value <= 0:
            self.err(fld, "BUDGET_INVALID", "budget must be a finite number of GPU hours > 0")

    # -- baselines -------------------------------------------------------------------------------------------
    def baselines(self, arms: list[str]) -> list[str]:
        console, c_out = self.req["console"], self.out["console"]
        seen: set[str] = set()
        backends: list[str] = []
        caps: dict[str, Any] = {}
        enabled_ids: list[str] = []
        for i, b in enumerate(console["baselines"]):
            pre = f"console.baselines[{i}]"
            if not isinstance(b, dict) or not isinstance(b.get("id"), str) or not isinstance(b.get("enabled"), bool):
                self.err(pre, "FIELD_INVALID", "baseline entries need a string id and boolean enabled")
                continue
            bid = b["id"]
            if bid in seen:
                self.err(pre + ".id", "BASELINE_DUPLICATE", f"baseline '{bid}' listed twice")
                continue
            seen.add(bid)
            raw = self.ctx.registry_lookup(bid)
            if raw is None:
                self.err(pre + ".id", "BASELINE_UNKNOWN", f"'{bid}' is not in the server registry")
                continue
            entry = raw if isinstance(raw, RegistryEntry) else RegistryEntry.model_validate(raw)
            resolved = self.baseline_entry(b, entry, pre)
            c_out["baselines"][i]["resolved"] = resolved
            if "comparison_group" in b:  # a client-sent group never overrides the one implied by the resolved init
                c_out["baselines"][i]["comparison_group"] = resolved["comparison_group"]
            caps[bid] = {**resolved, "enabled": b["enabled"], "reason": entry.reason, "capabilities": entry.capabilities}
            if b["enabled"]:
                enabled_ids.append(bid)
                backends.append(str(entry.capabilities.get("backend", "")))
        self.caps["baselines"] = caps
        if enabled_ids:
            self.budget(console.get("baseline_budget_gpu_hours"), "console.baseline_budget_gpu_hours")
        self.override_record(enabled_ids)
        accum = self.req["training"].get("gradient_accumulation", 1)
        if accum != 1 and (arms or "ultralytics" in backends):
            self.err("training.gradient_accumulation", "ACCUMULATION_RESUME_UNSUPPORTED",
                     "ultralytics-based training (search space, YOLO/RT-DETR baselines) requires gradient_accumulation=1")
        return backends

    def baseline_entry(self, b: dict[str, Any], entry: RegistryEntry, pre: str) -> dict[str, Any]:
        caps = entry.capabilities
        init = b.get("init") or self.req["training"].get("initialization")
        modes = list(caps.get("init_modes") or [])
        resolved = {"registry_status": str(entry.status), "comparison_group": _group(init), "init": init,
                    "backend": caps.get("backend"), "inference_head": caps.get("inference_head"),
                    "version": (entry.evidence or {}).get("version", caps.get("version")),
                    "requested_input_shape": self.req["training"].get("requested_input_size"),
                    "resolved_input_shape": None}
        if not b["enabled"]:
            return resolved
        if entry.status != RegistryStatus.AVAILABLE:
            self.err(pre + ".id", "BASELINE_UNAVAILABLE",
                     f"'{entry.id}' is {entry.status}: {entry.reason or 'no reason recorded'}; disable it or fix the registry")
        if _group(init) is None or init not in modes:
            hint = (" — set this baseline's init to 'pretrained_native' to run it in the separate native_pretrained "
                    "comparison group" if "pretrained_native" in modes and init != "pretrained_native" else "")
            self.err(pre + ".init", "INIT_UNSUPPORTED", f"'{entry.id}' supports init modes {modes}, not '{init}'{hint}")
        if b.get("comparison_group") not in (None, resolved["comparison_group"]):
            self.resolve(pre + ".comparison_group", b.get("comparison_group"), resolved["comparison_group"],
                         "comparison group follows the resolved initialization")
        resolved["resolved_input_shape"] = self.input_shape(caps, resolved["requested_input_shape"], pre)
        bench = self.req["console"]["benchmark"].get("precision")
        if caps.get("precisions") is not None and bench not in caps["precisions"]:
            self.err(pre + ".id", "PRECISION_UNSUPPORTED", f"'{entry.id}' does not support benchmark precision {bench}")
        return resolved

    def input_shape(self, caps: dict[str, Any], requested: Any, pre: str) -> list[int] | None:
        rule = caps.get("input_size_rule")
        if not (isinstance(requested, list) and len(requested) == 2 and all(_int(x) for x in requested)):
            return None
        if isinstance(rule, dict) and rule.get("kind") == "fixed" and isinstance(rule.get("shape"), list):
            shape = list(rule["shape"])
        elif isinstance(rule, dict) and rule.get("kind") == "stride_ceil" and _int(rule.get("stride")):
            s = rule["stride"]
            sides = [max(requested)] * 2 if rule.get("square") else requested
            shape = [-(-x // s) * s for x in sides]
        else:
            self.warn(pre, "INPUT_RULE_MISSING", "registry declares no input_size_rule; requested shape kept")
            shape = list(requested)
        self.resolve(pre + ".resolved.resolved_input_shape", requested, shape, f"adapter input rule {rule}")
        return shape

    def override_record(self, enabled_ids: list[str]) -> None:
        console, c_out = self.req["console"], self.out["console"]
        missing = [b for b in DEFAULT_BASELINE14 if b not in enabled_ids]
        added = [b for b in enabled_ids if b not in DEFAULT_BASELINE14]
        reason = console.get("baseline_override_reason")
        if missing or added:
            c_out["baseline_override"] = {"missing_from_default": missing, "added": added, "reason": reason}
            if not reason:
                self.warn("console.baselines", "BASELINE_OVERRIDE",
                          f"enabled baselines differ from baseline14 (missing {missing}, added {added}) "
                          "without console.baseline_override_reason")
        else:
            c_out.pop("baseline_override", None)

    # -- device ----------------------------------------------------------------------------------------------
    def device(self) -> None:
        bench = self.req["console"]["benchmark"]
        want = bench.get("device")
        match = None
        for dev in self.ctx.devices:
            names = {dev.get("id"), dev.get("uuid"), dev.get("name"), *dev.get("aliases", [])}
            if isinstance(want, str) and want.lower() in {str(n).lower() for n in names if n}:
                match = dev
                break
        if match is None:
            from mbnas_model_runtime.benchmark.capability import declared_target_for, device_targets

            avail = [d.get("id") for d in self.ctx.devices]
            declared = declared_target_for(want)
            why = next((t["reason"] for t in device_targets(self.ctx.devices) if t["id"] == declared), None)
            self.err("console.benchmark.device", "DEVICE_UNAVAILABLE", f"device '{want}' is not available; available: {avail}"
                     + (f" (declared target {declared}: {why})" if why else ""))
            return
        resolved = {"id": match.get("id"), "uuid": match.get("uuid"), "name": match.get("name")}
        self.out["console"]["benchmark"]["device_resolved"] = resolved
        self.resolve("console.benchmark.device_resolved", self.req_get("console.benchmark", "device_resolved"),
                     resolved, f"'{want}' resolved against the live device inventory")
        self.caps["device"] = resolved
        precision = bench.get("precision")
        if precision not in match.get("precisions", DEFAULT_DEVICE_PRECISIONS):
            self.err("console.benchmark.precision", "PRECISION_UNSUPPORTED",
                     f"{precision} is not supported on {resolved['name']} (no calibrated INT8 pipeline)")
        for key, lo in (("batch", 1), ("warmup", 0), ("repeats", 1)):
            if not _int(bench.get(key)) or bench[key] < lo:
                self.err(f"console.benchmark.{key}", "FIELD_INVALID", f"{key} must be an integer >= {lo}")

    def evaluation_device(self) -> None:
        """An evaluation-ON protocol needs a CUDA device (latency is measured on CUDA only, and the
        fitness needs it), so it is refused here with EVALUATION_REQUIRES_CUDA when the device inventory holds only the
        CPU runners' ``cpu`` device (a CPU-only fleet; field ``console.search.evaluation_enabled``) or when
        ``console.benchmark.device`` resolved to that device (field ``console.benchmark.device``) - instead of a run
        whose candidates all end EVAL_FAILED. Explicit training and the evaluation-OFF generation stay available on
        such a fleet: their validation never calls this. An empty inventory is not a CPU-only fleet (DEVICE_UNAVAILABLE
        alone, as before)."""
        devices = [d for d in (self.ctx.devices or []) if isinstance(d, dict)]
        cpu_fleet = bool(devices) and all(is_cpu_device(d) for d in devices)
        if cpu_fleet:
            self.err("console.search.evaluation_enabled", "EVALUATION_REQUIRES_CUDA",
                     "evaluation ON measures latency on a CUDA device only and the registered device inventory is "
                     "CPU-only; set console.search.evaluation_enabled=false (CPU structure generation), use explicit "
                     "training, or register a GPU runner")
        elif is_cpu_device(self.caps.get("device")):
            self.err("console.benchmark.device", "EVALUATION_REQUIRES_CUDA",
                     f"evaluation ON measures latency on a CUDA device only; console.benchmark.device resolves to the "
                     f"CPU device '{CPU_DEVICE}' - name a GPU device of the inventory or turn evaluation off")

    def postprocess(self) -> None:
        """Latency operating point ``console.benchmark.postprocess`` (defaults per missing key; in the hash)."""
        bench = self.req["console"]["benchmark"]
        pp = bench.get("postprocess")
        pre = "console.benchmark.postprocess"
        if pp is not None and not isinstance(pp, dict):
            self.err(pre, "FIELD_INVALID", "postprocess must be an object {conf, iou, max_det}")
            return
        pp = pp or {}
        self.unknown(pp, pre, pre + ".")
        out: dict[str, Any] = {}
        for key, default in DEFAULT_POSTPROCESS.items():
            value = pp.get(key)
            if value is None:
                self.resolve(f"{pre}.{key}", None, default, "defaulted")
                value = default
            elif key == "max_det" and (not _int(value) or value < 1):
                self.err(f"{pre}.{key}", "FIELD_INVALID", "max_det must be an integer >= 1")
            elif key != "max_det" and (not _num(value) or not 0 <= value <= 1):
                self.err(f"{pre}.{key}", "FIELD_INVALID", f"{key} must be a finite number in [0, 1]")
            out[key] = value
        self.out["console"]["benchmark"]["postprocess"] = out

    def retention(self) -> None:
        """``console.retention.full_state`` (default: server setting); excluded from the protocol hash."""
        console = self.req["console"]
        ret = console.get("retention")
        if ret is not None and not isinstance(ret, dict):
            self.err("console.retention", "FIELD_INVALID", "retention must be an object {full_state}")
            return
        ret = ret or {}
        self.unknown(ret, "console.retention", "console.retention.")
        policy = ret.get("full_state")
        if policy is None:
            policy = getattr(self.ctx.settings, "full_state_retention", "keep_all")
            self.resolve("console.retention.full_state", None, policy,
                         "defaulted from the server setting MBNAS_FULL_STATE_RETENTION")
        elif policy not in RETENTION_POLICIES:
            self.err("console.retention.full_state", "FIELD_INVALID", f"full_state must be one of {RETENTION_POLICIES}")
            return
        self.out["console"]["retention"] = {**ret, "full_state": policy}

    def final_retraining(self) -> None:
        """Pinned final recipe: integer epochs, horizon >= epochs, top_k >= 1 (checked when present)."""
        final = self.req.get("final_retraining")
        if not isinstance(final, dict):
            return
        pre = "final_retraining"
        target, horizon, top_k = (final.get(k) for k in ("epoch_target_total", "scheduler_horizon_epochs",
                                                          "top_k_per_arm_per_seed"))
        if target is not None and (not _int(target) or target < 1):
            self.err(f"{pre}.epoch_target_total", "EPOCHS_NOT_INT", "final epoch_target_total must be an integer >= 1")
        if horizon is not None and not _int(horizon):
            self.err(f"{pre}.scheduler_horizon_epochs", "EPOCHS_NOT_INT", "final scheduler_horizon_epochs must be an integer")
        elif _int(horizon) and _int(target) and horizon < target:
            self.err(f"{pre}.scheduler_horizon_epochs", "HORIZON_LT_LAST_RUNG",
                     f"final horizon {horizon} < final epoch_target_total {target}")
        if top_k is not None and (not _int(top_k) or top_k < 1):
            self.err(f"{pre}.top_k_per_arm_per_seed", "FIELD_INVALID", "top_k_per_arm_per_seed must be an integer >= 1")
        budget = final.get("budget_GPU_hours_separate")
        if budget is not None and (not _num(budget) or budget <= 0):
            self.err(f"{pre}.budget_GPU_hours_separate", "BUDGET_INVALID", "final budget must be a finite number of GPU hours > 0")

    # -- paths and dataset -----------------------------------------------------------------------------------
    def paths(self) -> bool:
        paths = self.req["console"]["paths"]
        st = self.ctx.settings
        ok = True
        for key, kind in DATASET_PATH_FIELDS:
            ok &= self.one_path(key, paths.get(key), kind, st.dataset_roots, must_exist=True, writable=False)
        out_ok = self.one_path("output_root", paths.get("output_root"), "dir", st.output_roots,
                               must_exist=False, writable=True)
        if out_ok:
            free = self.ctx.free_gb("output_root", self.path_real["output_root"])
            if free < st.min_free_disk_gb:
                self.err("console.paths.output_root", "DISK_LOW",
                         f"{free:.1f} GB free < required {st.min_free_disk_gb} GB")
        return ok

    def one_path(self, key: str, value: Any, kind: str, roots: list[str], *, must_exist: bool, writable: bool) -> bool:
        if self.ctx.gateway_paths:
            ok, real, code, message = self.ctx.gateway.check_path(key, value)
            unmeasured = code in ("PATH_CHECK_MISSING", "PATH_CHECK_MISMATCH")
            if not ok and not (unmeasured and self.ctx.gateway_local_fallback):
                self.err(f"console.paths.{key}", str(code), str(message))
                return False
            if ok:
                self.path_real[key] = str(real)
                return True
        check = check_server_path(value, kind=kind, allow_roots=roots, must_exist=must_exist,  # type: ignore[arg-type]
                                  writable=writable)
        if not check.ok:
            self.err(f"console.paths.{key}", str(check.code), str(check.message))
            return False
        self.path_real[key] = str(check.realpath)
        return True

    def dataset(self) -> None:
        try:
            dy = self.ctx.read_dataset_yaml(self.path_real["dataset_yaml"])
        except DatasetYamlError as exc:
            self.err("console.paths.dataset_yaml", "DATASET_YAML_INVALID", str(exc))
            return
        if dy.ignored_keys:
            self.warn("console.paths.dataset_yaml", "DATASET_YAML_IGNORED_KEYS", f"ignored (never executed): {list(dy.ignored_keys)}")
        consistent = True
        for role, (path_key, yaml_key, _) in SPLIT_FIELDS.items():
            if yaml_key not in dy.splits:
                self.warn("console.paths.dataset_yaml", "DATASET_YAML_NO_TEST",
                          f"yaml has no '{yaml_key}' split; {path_key} is used only by the server evaluator")
                continue
            yaml_real = self.ctx.split_realpath(dy.splits[yaml_key])
            if yaml_real != self.path_real[path_key]:
                consistent = False
                self.err(f"console.paths.{path_key}", "DATASET_YAML_PATH_MISMATCH",
                         f"dataset yaml '{yaml_key}' resolves to {yaml_real} but console.paths.{path_key} resolves to "
                         f"{self.path_real[path_key]}")
        reals = {role: self.path_real[f[0]] for role, f in SPLIT_FIELDS.items()}
        if len(set(reals.values())) != len(reals):
            self.err("console.paths", "SPLIT_SAME_ROOT", f"train/search_val/final_eval must be distinct roots: {reals}")
            return
        if consistent:
            self.manifests(reals, dy.names)

    def manifests(self, reals: dict[str, str], names: dict[int, str]) -> None:
        manifests: dict[str, Manifest] = {}
        for role, root in reals.items():
            m = self.ctx.split_manifest(role, root, names)
            manifests[role] = m
            fld = f"console.paths.{SPLIT_FIELDS[role][0]}"
            if not sample_count(m):
                self.err(fld, "DATASET_EMPTY", f"no images found under {root}")
            by_code: dict[str, list[str]] = {}
            for issue in m.issues:
                by_code.setdefault(issue.code, []).append(f"{issue.sample_id}:{issue.line or '-'}")
            for code, where in sorted(by_code.items()):
                self.err(fld, code, f"{len(where)} problem(s) in {role}, e.g. {where[:5]}")
            if m.duplicates:
                self.warn(fld, "DUPLICATE_SAMPLE", f"{len(m.duplicates)} duplicate image group(s) in {role}: {m.duplicates[:3]}")
        overlap = self.ctx.overlap(manifests)
        if overlap:
            pairs = sorted({"/".join(o["splits"]) for o in overlap})
            self.err("console.paths", "SPLIT_OVERLAP", f"{len(overlap)} identical image(s) across splits {pairs}: "
                                                        f"{[o['sample_ids'] for o in overlap[:3]]}")
        ds = self.out.setdefault("dataset", {})
        for role, (_, _, hash_key) in SPLIT_FIELDS.items():
            self.dataset_hash(ds, hash_key, manifests[role].split_hash)
        self.dataset_hash(ds, "class_mapping_hash", manifests["train"].class_mapping_hash)
        ds["counts"] = {role: m.counts for role, m in manifests.items()}
        self.caps["dataset"] = {"names": {str(k): v for k, v in names.items()}, "counts": ds["counts"]}
        self.ultralytics_recipe(sample_count(manifests["train"]), len(names))

    def ultralytics_recipe(self, n_train: int, nc: int) -> None:
        t, t_out = self.req["training"], self.out["training"]
        batch, accum, horizon = t.get("batch_per_gpu"), t.get("gradient_accumulation", 1), t_out.get("fixed_horizon_epochs")
        warm = t.get("warmup_epochs", 0)
        if not (all(_int(v) and v >= 1 for v in (batch, accum, horizon)) and _num(warm) and n_train > 0):
            return
        nbs = batch * accum
        nb = math.ceil(n_train / batch)
        warm_eff = min(warm, max(horizon - 1, 0))
        iterations = math.ceil(n_train / max(batch, nbs)) * horizon
        name = t.get("optimizer")
        if str(name).lower() == "auto":
            opt = ({"name": "MuSGD", "lr": 0.01} if iterations > 10000 else
                   {"name": "AdamW", "lr": round(0.002 * 5 / (4 + nc), 6)})
            opt.update(momentum=0.9, warmup_bias_lr=0.0, rule="ultralytics 8.4.173 optimizer=auto")
            self.resolve("training.lr_initial", t.get("lr_initial"), f"ignored (optimizer=auto -> {opt['name']} lr={opt['lr']})",
                         "ultralytics optimizer=auto replaces lr0")
        else:
            opt = {"name": name, "lr": t.get("lr_initial"), "rule": "as requested"}
        resolved = {"rule": "ultralytics 8.4.173 trainer", "train_images": n_train, "batches_per_epoch": nb, "nbs": nbs,
                    "accumulate": max(round(nbs / batch), 1), "warmup_iterations": round(warm_eff * nb) if warm_eff > 0 else 0,
                    "optimizer_iterations": iterations, "optimizer": opt}
        self.set_resolved(t_out, "training", "backend_resolved", {"ultralytics": resolved},
                          "what the ultralytics trainer derives from this recipe and train split")

    def dataset_hash(self, ds: dict[str, Any], key: str, value: str) -> None:
        requested = _dict(self.req.get("dataset")).get(key)
        if requested not in (None, value):
            self.warn(f"dataset.{key}", "DATASET_HASH_RECOMPUTED", f"requested {requested} differs from server manifest {value}")
        self.resolve(f"dataset.{key}", requested, value, "computed from the server-side split manifest")
        ds[key] = value

    # -- assembly --------------------------------------------------------------------------------------------
    def canonical_check(self) -> None:
        """NaN/Infinity (or non-JSON types) in fields no rule inspects would make the hash impossible."""
        try:
            canonical_json(self.req)
        except (ValueError, TypeError) as exc:
            where = str(exc).rsplit(" at ", 1)[-1].removeprefix("$").lstrip(".") or "$"
            if not any(e["field"] == where for e in self.errors):
                self.err(where, "FIELD_INVALID", f"not canonical JSON: {exc}")

    def finish(self) -> ValidationResult:
        self.canonical_check()
        if self.ctx.gateway_paths or self.ctx.gateway_dataset:
            # recorded beside, not inside, the hashed resolution: protocol_hash covers ``resolution`` (only
            # ``validated_at`` is excluded), and a re-validation on another host must stay a fixed point
            self.caps["validation_host"] = "gateway"
        if self.errors:
            # an invalid result makes no resolution claim; resolved_diff is empty (contract ValidateResponse).
            return ValidationResult(False, self.errors, self.warnings, None, None, self.caps, [])
        original = self.req.get("original_status", self.req.get("status"))
        status = "RESOLVED_REGISTERED" if self.out["console"]["execution_mode"] == "REGISTERED_SEARCH" else "RESOLVED_PILOT"
        self.out["original_status"] = original
        self.resolve("status", self.req.get("status"), status, "validated and frozen by the server")
        self.out["status"] = status
        self.out["resolution"] = {
            "canonicalization": CANONICALIZATION_ID, "hash_excluded": list(RESOLUTION_HASH_EXCLUDED),
            "validated_at": self.ctx.now(), "validator": VALIDATOR_VERSION,
            # contract 1.5.0: the requested path only - the realpath on the validating host is never recorded
            # (it differs between the gateway and core hosts of a split deployment; resolution.paths is hash-excluded)
            "paths": {k: {"requested": self.req["console"]["paths"][k]} for k in sorted(self.path_real)},
        }

        return ValidationResult(True, [], self.warnings, self.out, protocol_hash(self.out), self.caps, self.diff)


def devices_from_runners(conn: Any) -> list[dict[str, Any]]:
    """Validation devices (``ValidationContext.devices`` shape) of the registered runners that are not OFFLINE/LOST:
    one entry per distinct device uuid, aliases = uuid, name and the runner id.

    The CPU-only runners (registered with ``device = "cpu"`` and no uuid) are not left out:
    together they form ONE entry ``{"id": "cpu", "uuid": None, "name": "cpu", "kind": "cpu"}`` (aliases ``cpu`` and
    their runner ids), listed after the GPU entries. Explicit training then validates on a GPU-less fleet (the runner
    maps the assigned ``cuda:0`` to ``cpu``), while an evaluation-ON protocol is refused with EVALUATION_REQUIRES_CUDA
    (:meth:`_Run.evaluation_device`). Its precisions are the GPU ones: ``console.benchmark.precision`` only matters for
    a latency measurement, which never happens on that device. A runner without a uuid that did not register as
    ``cpu`` is still skipped (its device cannot be pinned)."""
    out: dict[str, dict[str, Any]] = {}
    cpu: dict[str, Any] | None = None
    for r in conn.execute("SELECT id, device, device_uuid, device_name, runtime_json FROM runners "
                          "WHERE status IN ('REGISTERED', 'ONLINE') ORDER BY id"):
        uuid_ = r["device_uuid"]
        if uuid_ is None:
            if str(r["device"] or "").strip().lower() != CPU_DEVICE:
                continue
            if cpu is None:
                cpu = {"id": CPU_DEVICE, "uuid": None, "name": CPU_DEVICE, "kind": "cpu", "aliases": [CPU_DEVICE],
                       "precisions": list(VALIDATION_PRECISIONS), "memory_total_mb": None, "runners": []}
            cpu["runners"].append(r["id"])
            if r["id"] not in cpu["aliases"]:
                cpu["aliases"].append(r["id"])
            continue
        entry = out.setdefault(uuid_, {"id": r["device"] or uuid_, "uuid": uuid_, "name": r["device_name"] or uuid_,
                                       "aliases": [uuid_] + ([r["device_name"]] if r["device_name"] else []),
                                       "precisions": list(VALIDATION_PRECISIONS), "memory_total_mb": None, "runners": []})
        entry["runners"].append(r["id"])
        if r["id"] not in entry["aliases"]:
            entry["aliases"].append(r["id"])
    return list(out.values()) + ([cpu] if cpu is not None else [])


def validate(requested: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    """Validate and resolve ``requested``; see the module docstring for the rules and codes."""
    if not isinstance(requested, dict):
        return ValidationResult(False, [{"field": "$", "message": "protocol must be a JSON object", "code": "FIELD_INVALID"}],
                                [], None, None, {}, [])
    c = _dict(requested.get("console"))
    if _dict(_dict(c.get("extra")).get("training")).get("enabled") is True:
        from mbnas_core.protocol.training_validation import validate_training_protocol
        return validate_training_protocol(requested, ctx)
    run = _Run(requested, ctx)
    search = _dict(_dict(requested.get("console")).get("search"))
    if search.get("evaluation_enabled", True) is False:
        return _validate_generation(run)
    if "evaluation_enabled" in search and not isinstance(search["evaluation_enabled"], bool):
        run.err("console.search.evaluation_enabled", "FIELD_INVALID", "evaluation_enabled must be boolean")
    if not run.structure():
        return run.finish()
    run.out["console"]["search"]["evaluation_enabled"] = True
    run.console_mode()
    targets = run.rungs()
    run.training(targets)
    run.fitness_and_constraints()
    run.final_retraining()
    arms = run.search()
    run.baselines(arms)
    run.device()
    run.evaluation_device()  # evaluation ON needs a CUDA device (CPU-only fleet -> refused here)
    run.postprocess()
    run.retention()
    if run.paths():
        run.dataset()
    return run.finish()



def _validate_generation(run: _Run) -> ValidationResult:
    """Resolve a CPU structure-generation request without consulting datasets, weights or GPU capabilities.

    Disabled evaluation settings remain in the immutable document, so switching ON restores them. This branch
    deliberately never calls training/rungs/fitness/device/dataset validation or a model backend. The actual
    structure service runs in the worker; validation does not claim a model passed before it was built.
    """
    req, out = run.req, run.out
    if not isinstance(req.get("schema_version"), str) or not req.get("schema_version"):
        run.err("schema_version", "FIELD_MISSING", "schema_version is required")
    run.unknown(req, "$", "")
    console = _dict(req.get("console"))
    if not console:
        run.err("console", "FIELD_MISSING", "console must be an object")
        return ValidationResult(False, run.errors, [], None, None, {}, [])
    run.unknown(console, "console", "console.")
    search = _dict(console.get("search"))
    run.unknown(search, "console.search", "console.search.")
    paths = _dict(console.get("paths"))
    run.unknown(paths, "console.paths", "console.paths.")
    if not isinstance(console.get("extra", {}), dict):
        run.err("console.extra", "FIELD_INVALID", "console.extra must be an object")
    c_out = out.setdefault("console", {})
    c_out.setdefault("paths", {})
    c_out.setdefault("baselines", [])
    c_out.setdefault("benchmark", {})
    c_out.setdefault("search", {})
    run.console_mode()
    cfg = c_out["search"]
    cfg["evaluation_enabled"] = False
    count = search.get("candidate_count", search.get("population", 10))
    attempts = search.get("max_attempts", max(100, count * 20) if _int(count) else 100)
    for key, val, lower in (("candidate_count", count, 1), ("max_attempts", attempts, 1), ("nc", search.get("nc"), 1)):
        if not _int(val) or val < lower:
            run.err("console.search." + key, "FIELD_INVALID", key + " must be an integer >= 1")
        else:
            cfg[key] = val
    if _int(count) and _int(attempts) and attempts < count:
        run.err("console.search.max_attempts", "FIELD_INVALID", "max_attempts must be >= candidate_count")
    duplicates = search.get("allow_duplicates", False)
    if not isinstance(duplicates, bool):
        run.err("console.search.allow_duplicates", "FIELD_INVALID", "allow_duplicates must be boolean")
    cfg["allow_duplicates"] = duplicates
    size = search.get("input_size")
    if size is None:
        shape = _dict(req.get("training")).get("requested_input_size")
        size = shape[0] if isinstance(shape, list) and len(shape) == 2 and shape[0] == shape[1] else 640
    if not _int(size) or size < 32 or size % 32:
        run.err("console.search.input_size", "FIELD_INVALID", "input_size must be a positive multiple of 32")
    cfg["input_size"] = size
    families = search.get("families", ["yolo11n", "yolo26n"])
    if not isinstance(families, list) or not families or not all(
            isinstance(f, str) and re.fullmatch(r"yolo(?:11|26)[nslmx]", f) for f in families):
        run.err("console.search.families", "FIELD_INVALID", "families must contain supported yolo11/yolo26 scales")
    cfg["families"] = families
    seeds = search.get("seeds", [0])
    if not isinstance(seeds, list) or not seeds or not all(_int(x) and x >= 0 for x in seeds) or len(set(seeds)) != len(seeds):
        run.err("console.search.seeds", "FIELD_INVALID", "seeds must be distinct non-negative integers")
    cfg["seeds"] = seeds
    if "allow_native_fallback" in search and not isinstance(search["allow_native_fallback"], bool):
        run.err("console.search.allow_native_fallback", "FIELD_INVALID", "allow_native_fallback must be boolean")
    arms = search.get("arms", [])
    if not isinstance(arms, list) or any(arm not in SEARCH_ARMS for arm in arms):
        run.err("console.search.arms", "FIELD_INVALID", "requested arms must be GA or IndependentRandom")
    cfg["requested_method"] = list(arms) if isinstance(arms, list) else []
    cfg["effective_method"] = "RandomGeneration"
    # duplicates are suppressed inside one seed's journal only (seed independence / replay); resolved-only field.
    if search.get("duplicate_scope", "per_seed") != "per_seed":
        run.err("console.search.duplicate_scope", "FIELD_INVALID", "duplicate_scope is resolved-only and fixed to per_seed")
    cfg["duplicate_scope"] = "per_seed"
    cfg.pop("search_space_resolved", None)  # no backend inspection is performed during request validation
    run.one_path("output_root", paths.get("output_root"), "dir", run.ctx.settings.output_roots,
                 must_exist=False, writable=True)
    if "output_root" in run.path_real:
        free = run.ctx.free_gb("output_root", run.path_real["output_root"])
        if free < run.ctx.settings.min_free_disk_gb:
            run.err("console.paths.output_root", "DISK_LOW", "insufficient free disk for generated architectures")
    run.caps["generation"] = {"execution": "CPU", "structure_status": "UNVERIFIED", "evaluation_enabled": False}
    return run.finish()
