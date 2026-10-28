"""Explicit, single-model training runs over the existing StageTrainer worker contract.

The source run is immutable. A selected baseline, user YAML or search/generated candidate becomes a new trial
with a frozen source binding. Pausing/resuming continues this trial through the existing full-state machinery;
selecting weights for transfer is never described as full-state resume.

When the gateway sent its host inputs (``gateway``: :class:`mbnas_core.protocol.gateway_inputs.GatewayInputs`)
the dataset YAML path check, the dataset YAML summary and the split manifests come from them instead of the core
filesystem (a split deployment keeps the data and ``output_root`` on the customer host). The new trial records its
configuration revisions (``original`` for a user YAML, ``selected`` for the bound configuration;
:mod:`mbnas_core.orchestration.config_revisions`).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal

from yaml import YAMLError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mbnas_core.artifacts.registry import register_file, run_dir
from mbnas_core.db import repo
from mbnas_core.events.bus import emit
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_core.protocol.validator import validate
from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobStatus
from mbnas_core.scheduling import jobs, planner
from mbnas_contracts.util.atomic import atomic_write_json, atomic_write_bytes, sha256_file
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.paths import check_server_path
from mbnas_contracts.util.time import utcnow_iso


class TrainingSelectionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, errors: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.errors = code, message, status, errors or []


class TrainingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["baseline", "user", "search", "generated"]
    run_id: str | None = None
    candidate_id: str | None = None
    baseline_id: str | None = None
    model_path: str | None = None
    model_yaml_text: str | None = None
    """The uploaded YAML text the gateway carries instead of a core-host path (with ``source_sha256``)."""
    source_sha256: str | None = None
    model_name: str | None = None

    @model_validator(mode="after")
    def _binding(self):
        if self.kind == "user" and self.model_yaml_text is not None:
            if self.model_path:
                raise ValueError("user source takes model_path or model_yaml_text, not both")
            needed: tuple[str, ...] = ("model_yaml_text", "source_sha256")
        else:
            needed = ("baseline_id",) if self.kind == "baseline" else ("model_path",) if self.kind == "user" else (
                "run_id", "candidate_id")
        if any(not getattr(self, key) for key in needed):
            raise ValueError(f"{self.kind} source requires {', '.join(needed)}")
        if self.kind != "user" and any(v is not None for v in (self.model_yaml_text, self.source_sha256, self.model_name)):
            raise ValueError("model_yaml_text/source_sha256/model_name are only valid for a user source")
        return self

    def binding_dump(self) -> dict[str, Any]:
        """The source binding recorded in protocols, audits and responses (never the carried YAML text)."""
        return self.model_dump(exclude_none=True, exclude={"model_yaml_text"})


class TrainingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: TrainingSource
    protocol: dict[str, Any]
    mode: Literal["scratch", "pretrained", "resume", "evaluate_only"] = "scratch"
    validation_enabled: bool = True
    checkpoint: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=256)


def explicit_training(run: dict[str, Any]) -> dict[str, Any] | None:
    cfg = run.get("resolved_protocol", {}).get("console", {}).get("extra", {}).get("training")
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") is True else None


def _path(path: str, settings: Any, *, yaml_only: bool = False) -> Path:
    """A server file: user YAMLs from ``model_yaml_roots`` + ``output_roots`` only; checkpoints also
    from ``weights_dir``."""
    roots = [*settings.model_yaml_roots, *settings.output_roots] if yaml_only else \
        [*settings.output_roots, settings.weights_dir, *settings.model_yaml_roots]
    chk = check_server_path(path, kind="file", allow_roots=roots, must_exist=True)
    if not chk.ok:
        raise TrainingSelectionError(chk.code or "PATH_INVALID", chk.message or "server path rejected")
    p = Path(chk.realpath)
    if yaml_only and (p.suffix.lower() not in (".yaml", ".yml") or p.stat().st_size > 1024 * 1024):
        raise TrainingSelectionError("MODEL_CONFIG_INVALID", "model configuration must be a YAML file up to 1 MiB")
    return p


def resolve_source(conn: sqlite3.Connection, req: TrainingRunRequest, svc: Any) -> dict[str, Any]:
    """Resolve one source without mutating it; architecture text is copied into the child run on creation."""
    src = req.source
    source = src.binding_dump()
    if src.run_id or src.candidate_id:
        if not src.run_id or not src.candidate_id:
            raise TrainingSelectionError("SOURCE_BINDING_INCOMPLETE", "source run and candidate identifiers must be provided together")
        bound_run, bound_candidate = repo.get_run(conn,src.run_id), repo.get_candidate(conn,src.candidate_id)
        if bound_run is None or bound_candidate is None or bound_candidate["run_id"] != src.run_id:
            raise TrainingSelectionError("SOURCE_NOT_FOUND", "candidate does not belong to the selected source run", 404)
        if src.kind == "baseline":
            recorded = (bound_candidate.get("audit", {}).get("orchestration") or {}).get("baseline_id") or bound_candidate["name"]
            if recorded != src.baseline_id:
                raise TrainingSelectionError("SOURCE_KIND_MISMATCH", "baseline identifier differs from the recorded source model",409)
    if src.kind == "baseline":
        from mbnas_model_runtime.registry import SPEC_BY_ID, arch_spec
        entry = svc.registry_lookup(src.baseline_id)
        if not entry or entry.get("status") != "AVAILABLE" or src.baseline_id not in SPEC_BY_ID:
            raise TrainingSelectionError("BASELINE_UNAVAILABLE", "selected baseline has no verified available registry entry")
        arch = arch_spec(src.baseline_id, svc.settings.weights_dir, entry).as_dict()
        source.update(name=src.baseline_id, family=arch["family"], arch=arch, blocks=[], architecture_yaml=None,
                      architecture_hash=(entry.get("evidence") or {}).get("architecture_hash"), entry=entry)
        if req.mode == "evaluate_only" and arch["backend"] != "ultralytics":
            raise TrainingSelectionError("EVAL_ONLY_UNSUPPORTED", "official evaluation-only is supported for verified ultralytics baselines")
    else:
        from mbnas_model_runtime.genotype import Genotype
        if src.kind == "user":
            from mbnas_model_runtime.genotype import text_diagnostics
            if src.model_yaml_text is not None:
                from mbnas_core.console.model_services import StructureRequestError, carried_yaml_text
                try:
                    text, digest, name = carried_yaml_text(src.model_yaml_text, src.source_sha256, src.model_name)
                except StructureRequestError as exc:
                    raise TrainingSelectionError(exc.code, exc.message, exc.status) from exc
                stem, file_sha, raw_text = Path(name).stem, digest, text  # same hex form as sha256_file
            else:
                p = _path(src.model_path, svc.settings, yaml_only=True)
                try:
                    text = p.read_text(encoding="utf-8")
                    raw_text = p.read_bytes().decode("utf-8")  # the exact bytes (no newline translation)
                except (OSError, UnicodeDecodeError) as exc:
                    raise TrainingSelectionError("MODEL_CONFIG_INVALID", "invalid detector YAML configuration") from exc
                stem, file_sha = p.stem, sha256_file(p)
            original = {"text": raw_text, "sha256": file_sha,  # the bytes the user gave (revision kind original)
                        "artifact_id": f"upl-{file_sha}" if src.model_yaml_text is not None else None,
                        "model_name": name if src.model_yaml_text is not None else Path(src.model_path).name}
            pre = text_diagnostics(text)  # size / alias bounds before the YAML is constructed
            if pre:
                code = "MODEL_CONFIG_INVALID" if any(d["code"] == "MODEL_CONFIG_INVALID" for d in pre) else "STRUCTURE_INVALID"
                raise TrainingSelectionError(code, "invalid detector YAML configuration", errors=pre)
            try:
                genotype = Genotype.from_yaml_text(text, family=stem, template_id="user:" + stem)
            except (ValueError, TypeError, YAMLError) as exc:
                raise TrainingSelectionError("MODEL_CONFIG_INVALID", "invalid detector YAML configuration") from exc
            source.update(name=stem, family=genotype.family, architecture_yaml=genotype.yaml_text(), blocks=[],
                          yaml_hash=genotype.yaml_hash(), source_file_sha256=file_sha)
            source["_original_revision"] = {**original, "yaml_hash": genotype.yaml_hash(),
                                            "canonical_text": genotype.yaml_text()}
        else:
            parent = repo.get_run(conn, src.run_id)
            cand = repo.get_candidate(conn, src.candidate_id)
            if parent is None or cand is None or cand["run_id"] != src.run_id:
                raise TrainingSelectionError("SOURCE_NOT_FOUND", "candidate does not belong to the selected run", 404)
            generated = parent["resolved_protocol"]["console"]["search"].get("evaluation_enabled", True) is False
            if generated != (src.kind == "generated"):
                raise TrainingSelectionError("SOURCE_KIND_MISMATCH", "source kind does not match the recorded generation/evaluation mode")
            body = ((cand.get("audit") or {}).get("proposal") or {}).get("genotype")
            if body:
                genotype = Genotype.from_json(body)
            elif cand.get("architecture_yaml"):
                genotype = Genotype.from_yaml_text(cand["architecture_yaml"], family=cand["family"], template_id=cand["id"])
            else:
                raise TrainingSelectionError("ARCHITECTURE_MISSING", "the selected candidate has no executable configuration")
            source.update(name=cand["name"], family=cand["family"], architecture_yaml=genotype.yaml_text(),
                          architecture_hash=cand.get("architecture_hash"), yaml_hash=genotype.yaml_hash(),
                          blocks=copy.deepcopy(cand.get("blocks") or []), genotype=genotype.to_json())
        if req.mode == "evaluate_only":
            raise TrainingSelectionError("EVAL_ONLY_UNSUPPORTED", "evaluation-only requires a verified official baseline; candidate checkpoints are evaluated from their run")
        from mbnas_model_runtime.operators import yaml_uses_mixed_modules
        source["arch"] = {"model_id": source["name"], "family": "YOLO26" if "yolo26" in genotype.family.lower() else "YOLO11",
                          "backend": "ultralytics", "variant": "detect", "config": genotype.yaml,
                          "register_hook": "mbnas_model_runtime.operators:ensure_registered" if yaml_uses_mixed_modules(genotype.yaml) else None}
        source["genotype"] = genotype.to_json()
    if req.mode == "pretrained" and req.checkpoint:
        checkpoint = _path(req.checkpoint, svc.settings)
        source["arch"].update(weights_path=str(checkpoint), weights_sha256=sha256_file(checkpoint))
    if req.mode in ("pretrained", "evaluate_only") and not source["arch"].get("weights_path"):
        raise TrainingSelectionError("INITIAL_WEIGHTS_REQUIRED", "select an existing server weights file for pretrained transfer")
    return source


def prepare_protocol(req: TrainingRunRequest, source: dict[str, Any], svc: Any,
                     gateway: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the training recipe/data independently of the original candidate-generation evaluation switch.

    ``gateway``: the gateway's host inputs; the dataset YAML path check and summary and the validation then
    use them instead of the core filesystem. The dataset class count is kept in ``source["dataset_class_count"]``."""
    requested = copy.deepcopy(req.protocol)
    if req.mode == "resume":
        raise TrainingSelectionError("RESUME_REQUIRES_PAUSED_RUN", "resume continues an existing paused trial through its run Resume action", 409)
    missing = [key for key in ("training", "fitness", "hard_constraints") if not isinstance(requested.get(key), dict)]
    if missing:
        raise TrainingSelectionError("TRAINING_RECIPE_MISSING", "a generation-only protocol has no training recipe; configure or load an explicit training policy", errors=[
            {"field": key, "code": "FIELD_MISSING", "message": "explicit training policy section required"} for key in missing])
    needed = ("optimizer", "lr_initial", "lr_final_ratio", "weight_decay", "precision", "requested_input_size", "batch_per_gpu")
    missing = [key for key in needed if key not in requested["training"]]
    if missing:
        raise TrainingSelectionError("TRAINING_RECIPE_MISSING", "complete the explicit training recipe", errors=[
            {"field": "training." + key, "code": "FIELD_MISSING", "message": "training setting required"} for key in missing])
    c = requested.setdefault("console", {})
    if not isinstance(c, dict) or not isinstance(c.get("paths"), dict):
        raise TrainingSelectionError("TRAINING_PATHS_MISSING", "select server dataset YAML and output paths")
    if req.mode == "evaluate_only" and req.validation_enabled is False:
        raise TrainingSelectionError("VALIDATION_REQUIRED", "evaluation-only mode requires validation_enabled=true")
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml, DatasetYamlError
    ds_path = c["paths"].get("dataset_yaml")
    measured = gateway.check_path("dataset_yaml", ds_path) if gateway is not None and gateway.path_checks is not None else None
    if measured is not None and (measured[0] or measured[2] not in ("PATH_CHECK_MISSING", "PATH_CHECK_MISMATCH")):
        ok, ds_real, code, message = measured  # checked on the gateway (customer) host
        if not ok:
            raise TrainingSelectionError(code or "PATH_INVALID", message or "dataset YAML path rejected by the gateway host")
    else:
        chk = check_server_path(ds_path, kind="file", allow_roots=svc.settings.dataset_roots, must_exist=True)
        if not chk.ok:
            raise TrainingSelectionError(chk.code or "PATH_INVALID", chk.message or "dataset YAML server path rejected")
        ds_real = chk.realpath
    required_splits = ("val",) if req.mode == "evaluate_only" else ("train", "val") if req.validation_enabled else ("train",)
    try:
        dataset_yaml = (gateway.dataset_yaml(required_splits) if gateway is not None and gateway.has_dataset else
                        load_dataset_yaml(ds_real, required_splits=required_splits))
    except DatasetYamlError as exc:
        raise TrainingSelectionError("DATASET_YAML_INVALID", str(exc)) from exc
    source["dataset_class_count"] = len(dataset_yaml.names)
    c["paths"]["dataset_yaml"] = ds_real
    for key, role in (("train_root", "train"), ("search_val_root", "val"), ("final_eval_root", "test")):
        if role in dataset_yaml.splits:
            c["paths"][key] = dataset_yaml.splits[role]
        else:
            c["paths"].pop(key, None)
    cfg = c.pop("training", {})
    if not cfg:
        cfg = copy.deepcopy((c.get("extra") or {}).get("training") or {})
    provided = cfg.get("epoch_target_total", c.get("baseline_epoch_target"))
    if req.mode == "evaluate_only":
        # official evaluation creates no training epochs. No epoch target is required; whatever was given is
        # ignored, the run records target 0 and keeps the supplied value only as provenance. The protocol validator
        # still needs a positive rung target/horizon, so the validated protocol carries the structural value 1 below.
        target = horizon = 0
    else:
        target = provided
        if not isinstance(target, int) or isinstance(target, bool) or target < 1:
            raise TrainingSelectionError("EPOCH_TARGET_INVALID", "console.training.epoch_target_total must be a positive integer")
        horizon = cfg.get("scheduler_horizon_epochs", target)
        if horizon != target:
            raise TrainingSelectionError("HORIZON_UNSUPPORTED", "single-stage training requires scheduler_horizon_epochs == epoch_target_total")
    protocol_target = max(1, target)  # rung/horizon fields of the validated protocol must be >= 1 (placeholder for evaluate_only)
    seed = cfg.get("seed", (c.get("search", {}).get("seeds") or [0])[0])
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise TrainingSelectionError("SEED_INVALID", "training seed must be a nonnegative integer")
    weights = (requested.get("rungs") or [{}])[0].get("fitness_weights")
    if not isinstance(weights, dict):
        raise TrainingSelectionError("FITNESS_PROTOCOL_MISSING", "retain an explicit metric weight configuration for validation")
    c.setdefault("search", {}).update(arms=[], evaluation_enabled=True)
    c["baselines"] = [{"id": req.source.baseline_id, "enabled": True,
                       "init": "pretrained_native" if req.mode in ("pretrained", "evaluate_only") else "scratch"}] if req.source.kind == "baseline" else []
    c.update(baseline_epoch_target=protocol_target, baseline_capture_rungs=True,
             baseline_override_reason="explicit single-model training selection")
    requested["rungs"] = [{"id": "TRAIN", "epoch_target_total": protocol_target, "fitness_weights": weights, "promotion": None}]
    requested["training"].update(initialization="pretrained_native" if req.mode in ("pretrained", "evaluate_only") else "scratch",
                                  scheduler_horizon_epochs=protocol_target)
    # The explicit training extension is part of the immutable protocol hash and selects split-aware validation.
    metadata = {"enabled": True, "mode": req.mode, "validation_enabled": req.validation_enabled,
                "epoch_target_total": target, "scheduler_horizon_epochs": target, "seed": seed,
                "source": req.source.binding_dump(), "source_architecture_hash": source.get("architecture_hash"),
                "source_yaml_hash": source.get("yaml_hash"), "arch": source["arch"],
                "device": cfg.get("device", "cuda:0"), "custom_initial_weights": bool(req.mode == "pretrained" and req.checkpoint),
                "native_image_artifacts": bool(cfg.get("native_image_artifacts", True))}
    if req.mode == "evaluate_only":
        metadata.update(epoch_target_total=0, scheduler_horizon_epochs=0,
                        ignored_requested_epoch_target_total=provided if isinstance(provided, int) and not isinstance(provided, bool) else None,
                        epoch_target_note="official evaluation trains no epochs; protocol rung/horizon value 1 is a structural placeholder")
    if metadata["device"] != "cuda:0":
        raise TrainingSelectionError("DEVICE_UNSUPPORTED", "worker exposes its assigned GPU as cuda:0; select its live benchmark device")
    if not req.validation_enabled and source["arch"]["backend"] != "ultralytics":
        raise TrainingSelectionError("VALIDATION_DISABLED_UNSUPPORTED", "validation-disabled training is implemented for the ultralytics backend only")
    requested["console"].setdefault("extra", {})["training"] = copy.deepcopy(metadata)
    # the gateway inputs only when sent: duck-typed services keep the zero-argument call
    result = validate(requested, svc.validation_context() if gateway is None else
                      svc.validation_context(gateway, local_fallback=True))
    if not result.valid:
        raise TrainingSelectionError("TRAINING_PROTOCOL_INVALID", "training data/recipe validation failed", errors=result.errors)
    resolved = result.resolved
    # Candidate class counts are frozen. Transfer to a different label set requires an explicit user model config.
    if req.source.kind in ("search", "generated"):
        nc = len(dataset_yaml.names)
        if int(source["genotype"]["yaml"]["nc"]) != nc:
            raise TrainingSelectionError("CLASS_COUNT_MISMATCH", "selected candidate class count differs from the training dataset; create an explicit adapted model configuration")
    resolved["console"].setdefault("extra", {})["training"] = metadata
    requested["console"].setdefault("extra", {})["training"] = copy.deepcopy(metadata)
    return requested, resolved


def bind_structure(source: dict[str, Any], resolved: dict[str, Any], mode: str) -> None:
    """Persist the actual executable detector YAML and the structure-check result for every supported source."""
    if source["arch"]["backend"] != "ultralytics":
        # RF-DETR / ETRI hybrid have no YAML genes: record the native-module observation at the backend's own
        # resolution (training may request another size; that is the adapter's decision, not a structure property).
        # One CPU build happens here; architecture_hash of the source is deliberately left as the registry recorded it
        # (the trainer's hash is built at the training resolution; the hash check must not compare against this one).
        from mbnas_core.console.model_services import native_structure_for_arch
        from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
        try:
            nc = source.get("dataset_class_count") or len(load_dataset_yaml(
                resolved["console"]["paths"]["dataset_yaml"],
                required_splits=("val",) if mode == "evaluate_only" else ("train",)).names)
            report = native_structure_for_arch(source["arch"], nc, None)
        except TrainingSelectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - the real cause is recorded; training is not blocked by a checker failure
            report = {"schema": "mbnas-structure/2", "backend": source["arch"]["backend"], "representation": "native_modules",
                      "status": "UNVERIFIED", "blocks": [], "edges": [], "edges_unobserved": [],
                      "diagnostics": [{"code": "STRUCTURE_CHECK_FAILED", "position": None, "path": None,
                                       "cause": f"native structure observation did not complete: {type(exc).__name__}: {exc}"[:600],
                                       "suggestion": "Rerun the explicit structure check once the runtime is available."}]}
        if report.get("status") == "FAIL":
            raise TrainingSelectionError("STRUCTURE_INVALID", "selected model failed its structural check",
                                         errors=report["diagnostics"])
        source["structure"] = report
        return
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
    from mbnas_model_runtime.genotype import Genotype
    from mbnas_core.search.structure import check_structure
    config = source["arch"]["config"]
    scale_source: str | None = None
    if not isinstance(config, dict):
        try:
            from ultralytics.nn.tasks import yaml_model_load
            from mbnas_core.console.model_services import derive_model_scale
            config_name = config
            config = yaml_model_load(config_name)
            # a registry config file name decides the scale like ultralytics' guess_model_scale (rt-detr-l -> 'l').
            config, scale_source = derive_model_scale(config, str(config_name))
        except (ImportError, ModuleNotFoundError, OSError):
            # API-only installations can queue a verified registry model; the worker writes its executed YAML.
            source["structure"] = {"status": "UNVERIFIED", "diagnostics": [{"code": "STRUCTURE_RUNTIME_UNAVAILABLE",
                                      "cause": "model configuration runtime is unavailable"}], "blocks": [], "edges": []}
            return
    else:
        config = copy.deepcopy(config)
    nc = source.get("dataset_class_count") or len(load_dataset_yaml(
        resolved["console"]["paths"]["dataset_yaml"], required_splits=("val",) if mode == "evaluate_only" else ("train",)).names)
    if source["kind"] == "baseline" and mode != "evaluate_only":
        config["nc"] = nc  # The existing native trainer also binds nc to the dataset before model construction.
    if source["kind"] != "baseline" and config.get("nc") != nc:
        raise TrainingSelectionError("CLASS_COUNT_MISMATCH", "model configuration nc differs from the selected dataset")
    from mbnas_model_runtime.genotype import validate_declared
    declared = validate_declared(config)  # allowlist / argument schema before anything is built
    if declared:
        raise TrainingSelectionError("STRUCTURE_INVALID", "selected model failed its structural check", errors=declared)
    genotype = Genotype.from_yaml_dict(config, family=source["family"], template_id=source.get("baseline_id") or source["name"])
    from mbnas_core.console.model_services import MIXED_NAMES
    names = {str(row[2]).rsplit(".", 1)[-1] for row in genotype.rows()}
    if names.intersection(MIXED_NAMES):
        # search/generated candidates carry the ETRI mixed blocks. Register them in this process exactly as the
        # explicit candidate check does (model_services.run_structure_check, in-process or in the sandbox worker);
        # without this the parser raises KeyError for the block and the selection would be wrongly rejected as
        # STRUCTURE_INVALID in the API process.
        try:
            from mbnas_core.search.mixed_pool.bridge import load_etri_mixed

            load_etri_mixed()
        except Exception as exc:  # noqa: BLE001 - the backend is unavailable here; the worker builds the model itself
            source.update(architecture_yaml=genotype.yaml_text(), yaml_hash=genotype.yaml_hash(), genotype=genotype.to_json(),
                          structure={"status": "UNVERIFIED", "blocks": [], "edges": [],
                                     "diagnostics": [{"code": "STRUCTURE_RUNTIME_UNAVAILABLE", "position": None, "path": None,
                                                      "cause": f"configured mixed-block backend is unavailable: {type(exc).__name__}"[:300],
                                                      "suggestion": "Run the explicit structure check once the mixed-block runtime is available."}]})
            return
    report = check_structure(genotype, input_size=max(resolved["training"]["requested_input_size"]))
    if scale_source is not None:
        report["scale_source"] = scale_source
    if report["status"] == "FAIL":
        raise TrainingSelectionError("STRUCTURE_INVALID", "selected model failed its structural check", errors=report["diagnostics"])
    source.update(architecture_yaml=genotype.yaml_text(), yaml_hash=genotype.yaml_hash(), genotype=genotype.to_json(),
                  structure=report, architecture_hash=report.get("architecture_hash") or source.get("architecture_hash"))
    if not source.get("blocks"):
        source["blocks"] = [{**b, "source": None, "mutated": False, "old_name": None} for b in report["blocks"]]
    if source["kind"] != "baseline":
        source["arch"]["config"] = genotype.yaml
    resolved["console"]["extra"]["training"]["arch"] = source["arch"]
    resolved["console"]["extra"]["training"]["architecture_hash"] = source.get("architecture_hash")


def create_training_run(conn: sqlite3.Connection, req: TrainingRunRequest, svc: Any,
                        request_id: str | None = None, gateway: Any = None) -> tuple[dict[str, Any], bool, str]:
    """Caller holds a transaction; idempotency is checked before current source/registry lookups. ``gateway``: the
    gateway's host inputs (never part of the idempotency payload)."""
    from mbnas_core.orchestration import engine
    key = "training|" + req.idempotency_key
    payload = canonical_sha256(req.model_dump(mode="json"))
    existing = repo.get_run_by_idempotency_key(conn, key)
    if existing is not None:
        if existing["payload_sha256"] != payload:
            raise TrainingSelectionError("IDEMPOTENCY_CONFLICT", "training idempotency key already has a different request", 409)
        return existing, False, repo.list_candidates(conn, existing["id"])[0]["id"]
    if req.mode == "resume":
        raise TrainingSelectionError("RESUME_REQUIRES_PAUSED_RUN", "resume must continue its existing trial through the run Resume control", 409)
    source = resolve_source(conn, req, svc)
    requested, resolved = prepare_protocol(req, source, svc, gateway)
    bind_structure(source, resolved, req.mode)
    requested["console"]["extra"]["training"] = copy.deepcopy(resolved["console"]["extra"]["training"])
    rid, cid = "train-" + uuid.uuid4().hex[:12], "model-" + uuid.uuid4().hex[:12]
    run, created = repo.create_run(conn, id=rid, namespace=resolved["console"]["run_namespace"],
        protocol_hash=protocol_hash(resolved), resolved_protocol=resolved, requested_protocol=requested,
        execution_mode=resolved["console"]["execution_mode"], idempotency_key=key, payload_sha256=payload,
        purpose="MODEL_EVALUATION" if req.mode == "evaluate_only" else "MODEL_TRAINING",
        preset=resolved["console"].get("preset"), source_commit=svc.source().get("sha256"),
        parent_run_id=req.source.run_id, budget={"scope": "explicit_training", "unit": "gpu_seconds"})
    base = run_dir(run)
    arch_path = None
    try:
        atomic_write_json(base / "protocol" / "requested.json", requested)
        atomic_write_json(base / "protocol" / "resolved.json", resolved)
        if source.get("architecture_yaml"):
            path = base / "architectures" / (cid + ".yaml")
            atomic_write_bytes(path, source["architecture_yaml"].encode("utf-8"))
            arch_path = str(path)
    except OSError:
        # with gateway host inputs the output_root is a customer-host path this core host may not be able to
        # create; the database keeps the protocols and the selected configuration (config revision) instead
        if gateway is None:
            raise
        arch_path = None
    source_binding = {**req.source.binding_dump(), "architecture_hash": source.get("architecture_hash"),
                      "yaml_hash": source.get("yaml_hash"), "mode": req.mode}
    lineage = {"operation": "baseline" if req.source.kind in ("baseline", "user") else "clone",
               "source_run_id": req.source.run_id, "source_candidate_id": req.source.candidate_id,
               "source_architecture_hash": source.get("architecture_hash"), "training_source": source_binding}
    audit = {"origin": req.source.kind, "training_source": source_binding, "source": source_binding, "structure": source.get("structure"),
             "orchestration": {"baseline_id": req.source.baseline_id, "init": resolved["training"]["initialization"],
                               "comparison_group": "official_evaluation" if req.mode == "evaluate_only" else
                                   ("native_pretrained" if req.mode == "pretrained" else "common_scratch")},
             "proposal": {"genotype": source.get("genotype"), "yaml_hash": source.get("yaml_hash")}}
    repo.insert_candidate(conn, id=cid, run_id=rid, name=source["name"], family=source["family"], arm="Baseline",
        architecture_hash=source.get("architecture_hash"), architecture_yaml=source.get("architecture_yaml"),
        architecture_path=arch_path, parents=[req.source.candidate_id] if req.source.kind in ("search", "generated") and req.source.candidate_id else [],
        lineage=lineage, blocks=source.get("blocks") or [], audit=audit, training_seed=explicit_training(run)["seed"],
        candidate_status="QUEUED", current_rung="TRAIN")
    selected_artifact = None
    if arch_path:
        selected_artifact = register_file(conn, run, kind="architecture", path=arch_path, candidate_id=cid,
                                          meta={"configuration_role": "selected", "yaml_hash": source.get("yaml_hash")})
    _record_revisions(conn, rid, cid, req, source, selected_artifact)
    emit(conn, rid, "run.updated", engine.run_payload(run))
    engine.log(conn, rid, "explicit model run created", source=source_binding, mode=req.mode, request_id=request_id)
    plan_training_run(conn, rid)
    return repo.get_run(conn, rid), created, cid


def _record_revisions(conn: sqlite3.Connection, run_id: str, cid: str, req: TrainingRunRequest, source: dict[str, Any],
                      selected_artifact: dict[str, Any] | None) -> None:
    """``original`` (a user YAML: the bytes as given) and ``selected`` (the configuration bound to the new trial;
    derived from the original, or from the source candidate's latest revision for a search/generated source)."""
    from mbnas_core.orchestration import config_revisions, engine

    parent = None
    orig = source.get("_original_revision")
    if orig:
        row = config_revisions.try_record(
            conn, engine.log, run_id=run_id, candidate_id=cid, kind="original", text=orig["text"],
            source_sha256=orig["sha256"], canonical_hash=orig.get("yaml_hash"), canonical_text=orig.get("canonical_text"),
            artifact_id=orig.get("artifact_id"), parent_revision_id=None,
            meta={"source_kind": "user", "model_name": orig.get("model_name")})
        parent = (row or {}).get("id")
    elif req.source.kind in ("search", "generated") and req.source.candidate_id:
        parent = (config_revisions.latest_revision(conn, req.source.candidate_id, req.source.run_id) or {}).get("id")
    text = source.get("architecture_yaml")
    if isinstance(text, str) and text:
        config_revisions.try_record(
            conn, engine.log, run_id=run_id, candidate_id=cid, kind="selected", text=text,
            canonical_hash=source.get("yaml_hash"), canonical_text=text, architecture_hash=source.get("architecture_hash"),
            artifact_id=(selected_artifact or {}).get("id"), parent_revision_id=parent,
            meta={"source_kind": req.source.kind, "mode": req.mode})


def plan_training_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    from mbnas_core.orchestration import engine
    run = repo.get_run(conn, run_id)
    cfg = explicit_training(run)
    if not cfg or run["terminal"] or run["status"] in ("PAUSED", "PAUSE_REQUESTED", "CANCEL_REQUESTED"):
        return []
    out = []
    for cand in repo.list_candidates(conn, run_id):
        params = {"candidate": {"baseline_id": cfg["source"].get("baseline_id"), "init": run["resolved_protocol"]["training"]["initialization"]},
                  "stage": {"id": "TRAIN", "epoch_target_total": cfg["epoch_target_total"],
                            "epochs_added": cfg["epoch_target_total"]}, "evaluate_inline": cfg["validation_enabled"]}
        kind = "EVALUATE" if cfg["mode"] == "evaluate_only" else "BASELINE_TRAIN"
        job, made = jobs.ensure_job(conn, run_id=run_id, kind=kind, protocol_hash=run["protocol_hash"],
            candidate_id=cand["id"], rung="TRAIN", cumulative_target=cfg["epoch_target_total"] if kind != "EVALUATE" else None,
            fixed_horizon=cfg["scheduler_horizon_epochs"] if kind != "EVALUATE" else None, arm="Baseline",
            training_seed=cfg["seed"], params=params)
        if made:
            planner.set_rung_state(conn, cand["id"], "TRAIN", planner.PENDING, job_id=job["id"])
            ctx = engine.context(conn, run_id)
            engine.put_row(conn, ctx, engine.base_row(ctx, cand, ctx.baseline_stages()[0], "QUEUED",
                                                   job_id=job["id"], source_kind=cfg["source"]["kind"],
                                                   training_mode=cfg["mode"], performance_status="NOT_EVALUATED"))
        out.append(job)
    return out


def on_training_result(conn: sqlite3.Connection, job: dict[str, Any], result: JobResult) -> None:
    """Commit a finished explicit-training job. ``SEARCH_COMPLETE`` + ``performance_status=NOT_EVALUATED`` on a
    row with ``training_mode`` means "explicit training finished; no metrics measured": either validation_enabled=false,
    or validation_enabled=true on a runner that could not evaluate - its payload says NOT_EVALUATED
    (a CPU-only runner: latency is measured on CUDA only). That row also carries ``reason_code``
    EVALUATION_REQUIRES_CUDA, ``latency_source`` pending, null metrics/fitness and the trained ``checkpoint_path``, and
    the run COMPLETES (:func:`engine.commit_not_evaluated`) instead of failing MODEL_RUN_FAILED on METRIC_MISSING."""
    from mbnas_core.orchestration import engine
    ctx = engine.context(conn, job["run_id"])
    cand = repo.get_candidate(conn, job["candidate_id"])
    cfg = explicit_training(ctx.run)
    stage = ctx.baseline_stages()[0]
    if result.status == JobStatus.SUCCEEDED:
        ev = result.outputs.get("evaluation")
        if isinstance(ev, dict) and engine.is_not_evaluated(ev):
            engine.commit_not_evaluated(conn, ctx, job, engine.with_device_used(ev, result.outputs), result.outputs)
        elif isinstance(ev, dict):
            engine.commit_evaluation(conn, ctx, job, engine.with_device_used(ev, result.outputs), outputs=result.outputs)
        elif cfg["validation_enabled"]:
            engine._candidate_failed(conn, ctx, job, JobResult(job_id=job["id"], status="FAILED", reason_code="EVALUATION_MISSING"))
        else:
            cand = engine.set_candidate(conn, cand["id"], "SEARCH_COMPLETE", "TRAIN")
            rr = result.outputs.get("rung_result") or {}
            trained_hash = result.outputs.get("architecture_hash")
            mismatch = engine.verify_architecture_hash(conn, ctx, cand, trained_hash) is False  # flag, never silent
            extra = {"reason_code": engine.ARCHITECTURE_HASH_MISMATCH} if mismatch else {}
            engine.put_row(conn, ctx, engine.base_row(ctx, cand, stage, "SEARCH_COMPLETE",
                epochs=int(result.outputs.get("epochs_done", stage.target)), checkpoint_path=rr.get("rung_end_training_ckpt"),
                architecture_hash=trained_hash, performance_status="NOT_EVALUATED",
                source_kind=cfg["source"]["kind"], training_mode=cfg["mode"], job_id=job["id"],
                raw_metrics={"validation_enabled": False, "rung_result": rr,
                             **({"device_used": str(result.outputs["device_used"])} if result.outputs.get("device_used") else {})},
                **extra))
            planner.set_rung_state(conn, cand["id"], "TRAIN", planner.EVALUATED, performance_status="NOT_EVALUATED")
    else:
        engine._candidate_failed(conn, ctx, job, result)
    settle_training_run(conn, job["run_id"])


def settle_training_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    from mbnas_core.orchestration import engine
    run = repo.get_run(conn, run_id)
    active = jobs.count_jobs(conn, run_id, jobs.ACTIVE_STATUSES)
    if run["status"] == "PAUSE_REQUESTED" and active == 0:
        return engine.set_run_status(conn, run_id, "PAUSED")
    if run["status"] == "CANCEL_REQUESTED" and active == 0:
        repo.cancel_queued_jobs(conn, run_id)
        for cand in repo.list_candidates(conn, run_id):
            engine.set_candidate(conn, cand["id"], "CANCELLED", "TRAIN")
        return engine.set_run_status(conn, run_id, "CANCELLED", "CANCELLED_BY_USER")
    if run["status"] in ("QUEUED", "RUNNING", "RESUMING") and not jobs.count_jobs(conn, run_id, jobs.OPEN_STATUSES):
        failed = any(c["candidate_status"] in ("TRAIN_FAILED", "EVAL_FAILED", "RESUME_BLOCKED", "INVALID")
                     for c in repo.list_candidates(conn, run_id))
        return engine.set_run_status(conn, run_id, "FAILED" if failed else "COMPLETED",
                                     "MODEL_RUN_FAILED" if failed else None)
    return run

