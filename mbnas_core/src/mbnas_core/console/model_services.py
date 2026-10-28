"""Import-light model detail views and an explicit CPU structure-check operation.

Normal GET requests use immutable saved data only: refreshing a model page cannot
schedule inference, training, or evaluation.  The explicit check operation is the
only entry point here that builds a model.

Every explicit check first runs the static allowlist / argument-schema check
:func:`mbnas_model_runtime.genotype.validate_declared` (a violation is a ``FAIL`` document built from the declaration;
nothing is imported or built), then builds the model in the structure sandbox
(:mod:`mbnas_core.structure.sandbox`) when the request bound one (the console routes do); without a bound sandbox
(the sandbox worker itself, library callers) the unchanged in-process service runs. Sandbox failures become
``UNVERIFIED`` documents with ``STRUCTURE_CHECK_TIMEOUT`` / ``STRUCTURE_RUNTIME_UNAVAILABLE`` /
``STRUCTURE_CHECK_RESOURCE_LIMIT``; a busy sandbox is 429 ``STRUCTURE_CHECK_BUSY``. User YAMLs come from
``model_yaml_roots`` + ``output_roots`` only, or as text carried by the gateway (``model_yaml_text`` +
``source_sha256``, never touching the filesystem).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from mbnas_model_runtime.genotype import Genotype, load_declared_text, text_diagnostics, validate_declared
from mbnas_contracts.structure_doc import CHECK_NAMES, SCHEMA
from mbnas_core.search.structure import check_structure, describe_genotype
from mbnas_core.structure import sandbox as _sandbox

BACKEND_UL = "ultralytics"
MAX_MODEL_YAML_BYTES = 1024 * 1024
NOT_CHECKED = [{"name": name, "status": "NOT_CHECKED", "evidence_level": None} for name in CHECK_NAMES]


class StructureRequestError(Exception):
    """A structure request the caller must fix (path/size/config); ``status`` is the HTTP status the route answers."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _not_checked() -> list[dict[str, Any]]:
    return [dict(item) for item in NOT_CHECKED]


MIXED_NAMES = frozenset({"NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer"})


def declared_fail(genotype: Genotype | None, diagnostics: list[dict[str, Any]], *, ident: str,
                  input_size: int | None = None) -> dict[str, Any]:
    """FAIL document of a configuration rejected by :func:`validate_declared` (nothing was built)."""
    if genotype is not None:
        try:
            doc = describe_genotype(genotype)
        except Exception:  # noqa: BLE001 - the declaration itself is malformed: fall back to the empty document
            doc = _unavailable({"architecture_hash": None, "id": ident}, "", "ARG_SCHEMA_INVALID")
    else:
        doc = _unavailable({"architecture_hash": None, "id": ident}, "", "ARG_SCHEMA_INVALID")
    checks = _not_checked()
    for item in checks:
        if item["name"] == "yaml_rows":
            item.update(status="FAIL", evidence_level="declared")
    doc.update(status="FAIL", diagnostics=[dict(d) for d in diagnostics], checks=checks, input_size=input_size)
    return doc


def _sandbox_failure_doc(ident: str, exc: "_sandbox.SandboxFailure", input_size: int | None) -> dict[str, Any]:
    doc = {**_unavailable({"architecture_hash": None, "id": ident}, exc.cause, exc.code), "input_size": input_size,
           "sandbox": exc.sandbox}
    doc["diagnostics"][0]["path"] = None
    doc["diagnostics"][0]["suggestion"] = ("Reduce the model or input size, or raise the sandbox limits, and rerun."
                                           if exc.code != _sandbox.RUNTIME_UNAVAILABLE else
                                           "Restore the configured model runtime and rerun the structure check.")
    return doc


def _execute(op: str, payload: dict[str, Any], inprocess: Any, *, ident: str, input_size: int | None) -> dict[str, Any]:
    """Run ``op`` in the request's structure sandbox, or ``inprocess()`` when no sandbox is bound."""
    box = _sandbox.active()
    if box is None:
        return inprocess()
    try:
        return box.run(op, payload)
    except _sandbox.SandboxBusy as exc:
        raise StructureRequestError("STRUCTURE_CHECK_BUSY", str(exc), 429) from exc
    except _sandbox.SandboxRequestError as exc:
        raise StructureRequestError(exc.code, exc.message, exc.status) from exc
    except _sandbox.SandboxFailure as exc:
        return _sandbox_failure_doc(ident, exc, input_size)


def check_genotype_inprocess(genotype: Genotype, input_size: int, class_count: int | None) -> dict[str, Any]:
    """In-process ``check_structure`` of a YAML genotype (the sandbox worker runs exactly this)."""
    names = {str(row[2]).rsplit(".", 1)[-1] for row in genotype.rows()}
    if names.intersection(MIXED_NAMES):
        # Existing mixed blocks are registered through the same server-only boundary.
        try:
            from mbnas_core.search.mixed_pool.bridge import load_etri_mixed

            load_etri_mixed()
        except Exception:  # noqa: BLE001
            return {**_unavailable({"architecture_hash": None, "id": genotype.template_id},
                                   "configured mixed-block backend is unavailable", "STRUCTURE_RUNTIME_UNAVAILABLE"),
                    "input_size": input_size}
    return check_structure(genotype, input_size=input_size, class_count=class_count)


def candidate_genotype(candidate: dict[str, Any]) -> Genotype | None:
    """Read the candidate's saved model configuration, never a browser-local path."""
    text = candidate.get("architecture_yaml")
    if not text:
        return None
    lineage = candidate.get("lineage") or {}
    lineage = lineage if isinstance(lineage, dict) else {}
    template = lineage.get("template") or {}
    template = template if isinstance(template, dict) else {"id": str(template)}
    family = template.get("family") or candidate.get("family") or "user"
    return Genotype.from_yaml_text(text, family=str(family), template_id=str(template.get("id") or candidate["id"]))


def _unavailable(candidate: dict[str, Any], cause: str, code: str = "CONFIGURATION_NOT_RECORDED") -> dict[str, Any]:
    return {"schema": SCHEMA, "backend": BACKEND_UL, "representation": "yaml_genes", "status": "UNVERIFIED",
            "parameters": None, "flops": None, "flops_status": "NOT_MEASURED", "flops_source": None, "observation": None,
            "yaml_hash": None, "architecture_hash": candidate.get("architecture_hash"), "signature_hash": None,
            "blocks": [], "edges": [], "edges_unobserved": [], "lineage": candidate.get("lineage") or None,
            "lineage_status": "RECORDED" if candidate.get("lineage") else "NOT_APPLICABLE",
            "diagnostics": [{"code": code, "position": None, "path": "architecture_yaml", "cause": cause,
                             "suggestion": "Record this model's exact executed configuration before checking it."}],
            "checks": _not_checked()}


# -- non-YAML (native) backends --------------------------------------------------------------------------------------
def _source_arch(candidate: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """ArchSpec dict of the model a candidate was made from, when the record names one (never guessed)."""
    audit = candidate.get("audit") or {}
    for key in ("training_source", "source", "source_model"):
        arch = (audit.get(key) or {}).get("arch") if isinstance(audit.get(key), dict) else None
        if isinstance(arch, dict) and arch.get("backend"):
            return arch
    training = (((run.get("resolved_protocol") or {}).get("console") or {}).get("extra") or {}).get("training")
    if isinstance(training, dict) and training.get("enabled") is True and isinstance((training.get("arch")), dict) \
            and training["arch"].get("backend"):
        return training["arch"]
    baseline = ((audit.get("orchestration") or {}).get("baseline_id")) or (
        candidate.get("name") if candidate.get("arm") == "Baseline" else None)
    if baseline and not candidate.get("architecture_yaml"):
        from mbnas_model_runtime.registry import SPEC_BY_ID, arch_spec

        if baseline in SPEC_BY_ID:
            return arch_spec(baseline).as_dict()
    return None


def native_backend(candidate: dict[str, Any], run: dict[str, Any]) -> str | None:
    """Backend name when the candidate's source model is not an Ultralytics YAML model, else ``None``."""
    arch = _source_arch(candidate, run)
    backend = arch.get("backend") if arch else None
    return backend if backend and backend != BACKEND_UL else None


def _declared_native_config(arch: dict[str, Any] | None, backend: str) -> dict[str, Any] | None:
    """Declared configuration without building (``None`` when the runtime that knows it is missing)."""
    if not arch:
        return None
    try:
        from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for

        spec = SPEC_BY_ID.get(arch.get("model_id"))
        if spec is None:
            return arch.get("config") if isinstance(arch.get("config"), dict) else None
        adapter = adapter_for(spec)
        return adapter.declared_config() if backend == "rfdetr" else adapter.declared_config(None, 1)
    except Exception:  # noqa: BLE001
        return arch.get("config") if isinstance(arch.get("config"), dict) else None


def _native_declared_only(backend: str, model_id: str, arch: dict[str, Any] | None, *, code: str) -> dict[str, Any]:
    from mbnas_model_runtime.adapters.structure_native import declared_native_document

    if code == "GENE_REPRESENTATION_NOT_APPLICABLE":
        diag = {"code": code, "position": None, "path": None,
                "cause": f"the {backend} backend is built from code modules, not from YAML gene rows, so there is "
                         "no gene representation to display",
                "suggestion": "Run the explicit CPU structure check to observe its native modules and data flow."}
    else:
        diag = {"code": code, "position": None, "path": None,
                "cause": "no complete structure-check record exists for this model",
                "suggestion": "Run the explicit CPU structure check."}
    doc = declared_native_document(backend, model_id, _declared_native_config(arch, backend), diagnostics=[diag])
    doc["evidence_source"] = "declared_configuration"
    return doc


def _describe_native_candidate(candidate: dict[str, Any], run: dict[str, Any], backend: str) -> dict[str, Any]:
    audit = candidate.get("audit") or {}
    arch = _source_arch(candidate, run) or {}
    saved = audit.get("structure")
    if isinstance(saved, dict) and saved.get("representation") == "native_modules" and saved.get("backend") == backend:
        out = copy.deepcopy(saved)
        out["evidence_source"] = "saved_structure_check"
    else:
        out = _native_declared_only(backend, str(arch.get("model_id") or candidate.get("name")), arch,
                                    code="GENE_REPRESENTATION_NOT_APPLICABLE")
    protocol = run.get("resolved_protocol") or {}
    source = audit.get("source") or audit.get("source_model") or audit.get("training_source") or {}
    origin = audit.get("origin") or source.get("kind") or ("baseline" if candidate.get("arm") == "Baseline" else "search")
    out.update(candidate_id=candidate["id"], run_id=run["id"], name=candidate.get("name"), origin=origin,
               input_size=out.get("input_size") or (protocol.get("training") or {}).get("requested_input_size"),
               seed=candidate.get("search_seed"), training_seed=candidate.get("training_seed"),
               source=copy.deepcopy(source), lineage=None, lineage_status="NOT_APPLICABLE",
               configuration={"kind": "native_modules", "yaml_hash": None, "path": None,
                              "availability": "RECORDED" if out.get("native_config") else "NOT_APPLICABLE"})
    return out


def _run_class_count(run: dict[str, Any]) -> int | None:
    path = (((run.get("resolved_protocol") or {}).get("console") or {}).get("paths") or {}).get("dataset_yaml")
    if not path:
        return None
    try:
        from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml

        for splits in (("val",), ("train",)):
            try:
                return len(load_dataset_yaml(path, required_splits=splits).names)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def native_structure_for_arch(arch: dict[str, Any], num_classes: int, input_size: int | None = None) -> dict[str, Any]:
    """Adapter-dispatched native structure observation for an ArchSpec dict (CPU build, one forward)."""
    from mbnas_model_runtime.adapters.base import ArchSpec
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for

    spec = SPEC_BY_ID.get(arch.get("model_id"))
    if spec is None:
        raise StructureRequestError("MODEL_NOT_FOUND", f"no registry specification for {arch.get('model_id')!r}", 404)
    return adapter_for(spec).describe_structure(ArchSpec(**arch), int(num_classes), input_size)


def describe_candidate(candidate: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Return saved structural proof, or an explicitly unverified declaration.

    A saved proof is only used when its canonical YAML hash still matches the
    displayed configuration and its architecture identity agrees with the row.
    Legacy BUILD_VALID labels alone do not invent internal layer measurements.
    """
    backend = native_backend(candidate, run)
    if backend:
        return _describe_native_candidate(candidate, run, backend)
    try:
        genotype = candidate_genotype(candidate)
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        return {**_unavailable(candidate, "saved configuration cannot be parsed", "CONFIGURATION_INVALID"), "status": "FAIL"}
    if genotype is None:
        return _unavailable(candidate, "this candidate has no saved architecture configuration")
    audit = candidate.get("audit") or {}
    saved = audit.get("structure")
    if isinstance(saved, dict) and saved.get("yaml_hash") == genotype.yaml_hash() and (
            not candidate.get("architecture_hash") or saved.get("architecture_hash") == candidate["architecture_hash"]):
        out = copy.deepcopy(saved)
        out["evidence_source"] = "saved_structure_check"
    else:
        out = describe_genotype(genotype, lineage=candidate.get("lineage") or None)
        out.update(status="UNVERIFIED", diagnostics=[{
            "code": "STRUCTURE_CHECK_NOT_RECORDED", "position": None, "path": None,
            "cause": "no complete structure-check record matches this exact configuration",
            "suggestion": "Run the explicit CPU structure check; declared rows are available now."}],
                   checks=_not_checked(), evidence_source="declared_configuration")
        # Existing per-row shapes are recorded evidence, but do not assert an
        # absent signature or an unrecorded internal-layer expansion.
        recorded = {b.get("position"): b for b in candidate.get("blocks") or []}
        for block in out["blocks"]:
            old = recorded.get(block["position"], {})
            params = old.get("params") or {}
            effective = params.get("effective")
            if isinstance(effective, dict):
                block["recorded_effective"] = copy.deepcopy(effective)
                block["recorded_effective_source"] = "candidate.blocks"
    # Keep the recorded genetic provenance beside the exact row it describes.
    # Runtime structure checks measure shapes; they do not create lineage events.
    recorded = {b.get("position"): b for b in candidate.get("blocks") or [] if isinstance(b, dict)}
    for block in out.get("blocks", []):
        old = recorded.get(block.get("position"), {})
        declaration = old.get("params") or old
        if not all(declaration.get(key) == block.get(key) for key in ("from", "repeats", "module", "args")):
            continue
        for key in ("source", "source_kind", "parent_block_id", "operation", "before", "after", "repair_reason", "old_name", "mutated"):
            if key in old and old[key] is not None:
                block[key] = copy.deepcopy(old[key])
    protocol = run.get("resolved_protocol") or {}
    training = protocol.get("training") or {}
    source = audit.get("source") or audit.get("source_model") or audit.get("training_source") or {}
    origin = audit.get("origin") or source.get("kind") or ("baseline" if candidate.get("arm") == "Baseline" else "search")
    out["lineage"] = copy.deepcopy(candidate.get("lineage") or None)
    out["lineage_status"] = "RECORDED" if candidate.get("lineage") else "NOT_APPLICABLE"
    out.update(candidate_id=candidate["id"], run_id=run["id"], name=candidate.get("name"),
               origin=origin,
               input_size=out.get("input_size") or training.get("requested_input_size"),
               seed=candidate.get("search_seed"), training_seed=candidate.get("training_seed"),
               source=copy.deepcopy(source),
               configuration={"kind": "execution" if audit.get("execution_configuration") else "normalized" if audit.get("training_source") else "original" if origin in ("baseline", "user") else "generated",
                              "yaml_hash": genotype.yaml_hash(), "path": candidate.get("architecture_path"),
                              "availability": "RECORDED"})
    if origin in ("baseline", "user") and not candidate.get("parents"):
        out["lineage"] = None
        out["lineage_status"] = "NOT_APPLICABLE"
    return out


def run_structure_check(candidate: dict[str, Any], run: dict[str, Any], *, input_size: int | None = None) -> dict[str, Any]:
    """Explicit CPU validation; the caller stores the returned proof in audit.structure.

    Models whose source is not an Ultralytics YAML (RF-DETR, ETRI hybrid) are observed through their adapter's
    ``describe_structure`` at the backend's fixed resolution unless ``input_size`` is given.
    """
    backend = native_backend(candidate, run)
    if backend:
        arch = _source_arch(candidate, run) or {}
        nc = _run_class_count(run)
        if nc is None:
            doc = _native_declared_only(backend, str(arch.get("model_id") or candidate.get("name")), arch,
                                        code="STRUCTURE_CHECK_NOT_RECORDED")
            doc["diagnostics"] = [{"code": "CLASS_COUNT_UNKNOWN", "position": None, "path": "console.paths.dataset_yaml",
                                   "cause": "the dataset class count of this run could not be read",
                                   "suggestion": "Restore the run's dataset YAML, then rerun the structure check."}]
            doc.pop("evidence_source", None)
            return doc
        return _execute("native_structure", {"arch": arch, "nc": nc, "input_size": input_size},
                        lambda: native_structure_for_arch(arch, nc, input_size), ident=str(candidate.get("id")),
                        input_size=input_size)
    try:
        genotype = candidate_genotype(candidate)
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        return {**_unavailable(candidate, "saved configuration cannot be parsed", "CONFIGURATION_INVALID"), "status": "FAIL"}
    if genotype is None:
        return _unavailable(candidate, "this candidate has no saved architecture configuration")
    if input_size is None:
        protocol = run.get("resolved_protocol") or {}
        search = (protocol.get("console") or {}).get("search") or {}
        sizes = search.get("input_size") if search.get("evaluation_enabled", True) is False else None
        sizes = sizes or (protocol.get("training") or {}).get("requested_input_size") or [640, 640]
        if isinstance(sizes, list) and len(sizes) == 2 and sizes[0] != sizes[1]:
            return _unavailable(candidate, "this CPU checker currently requires a square reference input", "INPUT_SHAPE_UNSUPPORTED")
        input_size = sizes[0] if isinstance(sizes, list) else sizes
    diagnostics = validate_declared(genotype.yaml)
    if diagnostics:
        return declared_fail(genotype, diagnostics, ident=str(candidate.get("id")), input_size=input_size)
    names = {str(row[2]).rsplit(".", 1)[-1] for row in genotype.rows()}
    if names.intersection(MIXED_NAMES) and _sandbox.active() is None:
        try:
            from mbnas_core.search.mixed_pool.bridge import load_etri_mixed

            load_etri_mixed()
        except Exception:
            return _unavailable(candidate, "configured mixed-block backend is unavailable", "STRUCTURE_RUNTIME_UNAVAILABLE")
    nc = genotype.yaml.get("nc")
    return _execute("check_structure", {"genotype": genotype.to_json(), "input_size": input_size, "class_count": nc},
                    lambda: check_structure(genotype, input_size=input_size, class_count=nc),
                    ident=str(candidate.get("id")), input_size=input_size)


# -- registry (baseline) models and user models ----------------------------------------------------------------------
def derive_model_scale(config: dict[str, Any], name: str) -> tuple[dict[str, Any], str | None]:
    """Set ``scale`` the way ultralytics does for a config file name; returns ``(config, source)``.

    ``scale`` is kept when it names an entry of ``scales``.  Otherwise it comes from the file name
    (``guess_model_scale``: ``yolo11n.yaml`` -> ``n``), else from a trailing ``-<letter>`` (``rtdetr-l.yaml`` -> ``l``,
    which ultralytics' own guess cannot read), else from the first key of ``scales`` - the fallback ``parse_model``
    itself uses (with a warning) when no scale is given.  Configs without ``scales`` are returned unchanged.
    """
    cfg = copy.deepcopy(config)
    scales = cfg.get("scales")
    if not isinstance(scales, dict) or not scales:
        return cfg, None
    if cfg.get("scale") in scales:
        return cfg, "yaml"
    stem = Path(str(name)).stem
    guess = ""
    try:
        from ultralytics.utils.checks import guess_model_scale

        guess = guess_model_scale(str(name)) or ""
    except Exception:  # noqa: BLE001 - same regex as ultralytics, used when it is not importable
        match = re.search(r"yolo(e-)?[v]?\d+([nslmx])", stem)
        guess = match.group(2) if match else ""
    if guess in scales:
        cfg["scale"], source = guess, "file_name"
    else:
        suffix = re.search(r"[-_]([a-z])$", stem)
        if suffix and suffix.group(1) in scales:
            cfg["scale"], source = suffix.group(1), "file_name_suffix"
        else:
            cfg["scale"], source = next(iter(scales)), "first_scale_key"
    return cfg, source


def registry_genotype(model_id: str) -> tuple[Genotype, str | None]:
    """Genotype of an Ultralytics registry model from its packaged config file (scale: :func:`derive_model_scale`)."""
    from mbnas_model_runtime.registry import SPEC_BY_ID

    spec = SPEC_BY_ID.get(model_id)
    if spec is None or spec.backend != BACKEND_UL:
        raise StructureRequestError("MODEL_NOT_FOUND", f"{model_id!r} is not an Ultralytics registry model", 404)
    from ultralytics.nn.tasks import yaml_model_load

    config, source = derive_model_scale(yaml_model_load(spec.config), spec.config)
    return Genotype.from_yaml_dict(config, family=spec.family, template_id=spec.id), source


def check_registry_yaml(model_id: str, input_size: int = 640) -> dict[str, Any]:
    """CPU structure check of an Ultralytics registry model (existing ``check_structure``; nc is the config's own)."""
    genotype, source = registry_genotype(model_id)
    diagnostics = validate_declared(genotype.yaml)
    if diagnostics:
        doc = declared_fail(genotype, diagnostics, ident=model_id, input_size=input_size)
    else:
        doc = check_structure(genotype, input_size=input_size, class_count=genotype.yaml.get("nc"))
    doc["scale_source"] = source
    return doc


def _spec_or_404(model_id: str) -> Any:
    from mbnas_model_runtime.registry import SPEC_BY_ID

    spec = SPEC_BY_ID.get(model_id)
    if spec is None:
        raise StructureRequestError("MODEL_NOT_FOUND", f"model {model_id!r} is not in the registry", 404)
    return spec


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(text))


def registry_structure_path(settings: Any, model_id: str, arch_hash: str) -> Path:
    """``<output_root>/registry_structure/<model_id>/<arch_hash>.json`` (hash text made filename-safe)."""
    return Path(settings.output_root) / "registry_structure" / _safe_name(model_id) / (_safe_name(arch_hash) + ".json")


def _entry_hash(entry: dict[str, Any] | None) -> str | None:
    ev = (entry or {}).get("evidence") or {}
    return ev.get("architecture_hash") or (ev.get("build") or {}).get("structural_hash_nc1")


def _saved_registry_doc(settings: Any, model_id: str, arch_hash: str | None) -> dict[str, Any] | None:
    folder = registry_structure_path(settings, model_id, "x").parent
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if arch_hash:
        exact = registry_structure_path(settings, model_id, arch_hash)
        files = ([exact] if exact.is_file() else []) + [f for f in files if f != exact]
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and str(doc.get("schema", "")).startswith("mbnas-structure/") and doc.get("status"):
            return doc
    return None


def describe_registry_model(settings: Any, model_id: str, entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET view: the saved structure check of this registry model, else a declared-only document.  Never builds."""
    spec = _spec_or_404(model_id)
    saved = _saved_registry_doc(settings, model_id, _entry_hash(entry))
    if saved is not None:
        out = copy.deepcopy(saved)
        out.update(model_id=model_id, evidence_source="saved_structure_check")
        return out
    if spec.backend == BACKEND_UL:
        try:
            genotype, source = registry_genotype(model_id)
            out = describe_genotype(genotype)
            out["scale_source"] = source
            out.update(status="UNVERIFIED", checks=_not_checked(), diagnostics=[{
                "code": "STRUCTURE_CHECK_NOT_RECORDED", "position": None, "path": None,
                "cause": "no structure-check record exists for this registry model",
                "suggestion": "Run the explicit CPU structure check; declared rows are available now."}])
        except (ImportError, OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            out = {**_unavailable({"architecture_hash": None, "id": model_id}, f"{type(exc).__name__}: {exc}"[:300],
                                  "STRUCTURE_RUNTIME_UNAVAILABLE"), "template_id": spec.id}
        out["evidence_source"] = "declared_configuration"
    else:
        out = _native_declared_only(spec.backend, spec.id, {"model_id": spec.id, "backend": spec.backend,
                                                            "config": spec.config},
                                    code="STRUCTURE_CHECK_NOT_RECORDED")
    out["model_id"] = model_id
    return out


def _valid_input_size(input_size: Any, max_input: int | None = None) -> int | None:
    if input_size is None:
        return None
    if isinstance(input_size, bool) or not isinstance(input_size, int) or input_size < 32 or input_size % 32:
        raise StructureRequestError("INPUT_SIZE_INVALID", "input_size must be an integer multiple of 32 (at least 32)")
    if max_input is not None and input_size > max_input:
        raise StructureRequestError("INPUT_SIZE_INVALID", f"input_size {input_size} exceeds the structure-check limit "
                                    f"{max_input} (CoreSettings.structure_check_max_input)")
    return input_size


def _max_input(settings: Any) -> int | None:
    value = getattr(settings, "structure_check_max_input", None)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _native_fixed_resolution(spec: Any, arch: dict[str, Any]) -> int | None:
    """The backend's fixed square input size from its static declaration (no import of rfdetr, no build); ``None`` if unknown."""
    try:
        from mbnas_model_runtime.registry import adapter_for

        adapter = adapter_for(spec)
        config = adapter.declared_config() if spec.backend == "rfdetr" else adapter.declared_config(None, 1)
        value = config.get("resolution") if spec.backend == "rfdetr" else config.get("imgsz")
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    except Exception:  # noqa: BLE001 - unknown fixed size: let the adapter's own FAIL document speak
        return None


def check_registry_model(settings: Any, model_id: str, input_size: int | None = None) -> dict[str, Any]:
    """POST: explicit CPU observation of a registry model; PASS/FAIL results are saved atomically and returned.

    Ultralytics models use ``check_structure`` at ``input_size`` (default 640); RF-DETR / ETRI hybrid go through their
    adapter at the fixed resolution.  A multiple of 32 that differs from that fixed resolution is a request error:
    422 ``INPUT_SIZE_INVALID``, decided from the static declaration before anything is built.
    Only a check that really ran (``observation`` present: PASS or a runtime FAIL) is saved.  An UNVERIFIED runtime
    failure is returned but never overwrites the recorded evidence, so a later GET still shows the last real check
    (or the declaration).
    """
    from mbnas_contracts.util.atomic import atomic_write_json

    spec = _spec_or_404(model_id)
    size = _valid_input_size(input_size, _max_input(settings))
    if spec.backend == BACKEND_UL:
        doc = _execute("registry_yaml", {"model_id": model_id, "input_size": size or 640},
                       lambda: check_registry_yaml_safe(model_id, size or 640), ident=model_id, input_size=size or 640)
    else:
        from mbnas_model_runtime.registry import arch_spec

        arch = arch_spec(model_id, settings.weights_dir).as_dict()
        fixed = _native_fixed_resolution(spec, arch)
        if size is not None and fixed is not None and size != fixed:
            raise StructureRequestError("INPUT_SIZE_INVALID", f"{spec.backend} model {model_id!r} is fixed at {fixed}x{fixed}; "
                                        f"input_size {size} is not accepted (omit it or send {fixed})")
        doc = _execute("native_structure", {"arch": arch, "nc": 1, "input_size": size},  # registry probes use one class
                       lambda: native_structure_for_arch(arch, 1, size), ident=model_id, input_size=size)
    doc["model_id"] = model_id
    doc["evidence_source"] = "executed_structure_check"
    if doc.get("status") in ("PASS", "FAIL") and doc.get("observation"):
        key = doc.get("architecture_hash") or doc.get("yaml_hash") or f"unbuilt-{doc.get('input_size')}"
        stored = {**doc, "evidence_source": "saved_structure_check"}
        atomic_write_json(registry_structure_path(settings, model_id, key), stored)
    return doc


def check_registry_yaml_safe(model_id: str, input_size: int) -> dict[str, Any]:
    """``check_registry_yaml`` with a missing ultralytics runtime reported as UNVERIFIED data."""
    try:
        return check_registry_yaml(model_id, input_size)
    except (ImportError, OSError) as exc:
        return {**_unavailable({"architecture_hash": None, "id": model_id},
                               f"configuration runtime unavailable: {type(exc).__name__}: {exc}"[:300],
                               "STRUCTURE_RUNTIME_UNAVAILABLE"), "template_id": model_id, "input_size": input_size}


def user_yaml_roots(settings: Any) -> list[str]:
    """Read roots of user model YAMLs: ``model_yaml_roots`` + ``output_roots`` (not ``weights_dir``)."""
    return [*settings.model_yaml_roots, *settings.output_roots]


def _user_yaml_path(settings: Any, model_path: str) -> Path:
    from mbnas_contracts.util.paths import check_server_path

    chk = check_server_path(model_path, kind="file", allow_roots=user_yaml_roots(settings), must_exist=True)
    if not chk.ok:
        raise StructureRequestError(chk.code or "PATH_INVALID", chk.message or "server path rejected")
    path = Path(chk.realpath)
    if path.suffix.lower() not in (".yaml", ".yml") or path.stat().st_size > MAX_MODEL_YAML_BYTES:
        raise StructureRequestError("MODEL_CONFIG_INVALID", "model configuration must be a YAML file up to 1 MiB")
    return path


SHA256_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


def carried_yaml_text(model_yaml_text: Any, source_sha256: Any, model_name: Any = None) -> tuple[str, str, str]:
    """Validate a YAML text carried in the request body by the gateway: ``(text, sha256 hex, file name)``.

    ``source_sha256`` must be the sha256 of the UTF-8 bytes of ``model_yaml_text`` (hex, optionally ``sha256:``-
    prefixed); the text is at most 1 MiB. ``model_name`` (optional) is the uploaded file's base name, used only for
    the :func:`derive_model_scale` rule (``yolo11n.yaml`` -> ``n``); default ``upload-<sha12>.yaml``.
    """
    if not isinstance(model_yaml_text, str) or not model_yaml_text:
        raise StructureRequestError("MODEL_CONFIG_INVALID", "model_yaml_text must be a non-empty string")
    data = model_yaml_text.encode("utf-8")
    if len(data) > MAX_MODEL_YAML_BYTES:
        raise StructureRequestError("MODEL_CONFIG_INVALID", "model configuration must be a YAML file up to 1 MiB")
    match = SHA256_RE.fullmatch(source_sha256) if isinstance(source_sha256, str) else None
    if match is None:
        raise StructureRequestError("SOURCE_SHA256_INVALID", "source_sha256 must be a sha256 hex digest")
    digest = hashlib.sha256(data).hexdigest()
    if digest != match.group(1):
        raise StructureRequestError("SOURCE_SHA256_MISMATCH", "source_sha256 does not match model_yaml_text")
    name = Path(str(model_name)).name if isinstance(model_name, str) and model_name.strip() else ""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)[:128]
    if not name or Path(name).suffix.lower() not in (".yaml", ".yml"):
        name = f"upload-{digest[:12]}.yaml"
    return model_yaml_text, digest, name


def check_user_model(settings: Any, model_path: str | None = None, input_size: int | None = None,
                     class_count: int | None = None, *, model_yaml_text: str | None = None,
                     source_sha256: str | None = None, model_name: str | None = None) -> dict[str, Any]:
    """POST /structure-check: CPU structure check of a user YAML (not saved), read from an allowed server root
    (``model_path``) or carried in the body (``model_yaml_text`` + ``source_sha256``; no filesystem access).

    ``source_sha256`` is the sha256 of the ORIGINAL bytes on both paths: a ``model_path`` file is
    read as bytes (no newline translation: a CRLF file keeps its CR bytes, exactly what the gateway carries and what the
    training ``original`` revision records with ``sha256_file``) and the text checked is those bytes decoded as UTF-8."""
    if model_yaml_text is not None:
        text, digest, name = carried_yaml_text(model_yaml_text, source_sha256, model_name)
    else:
        path = _user_yaml_path(settings, str(model_path))
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StructureRequestError("MODEL_CONFIG_INVALID", "invalid detector YAML configuration") from exc
        digest, name = hashlib.sha256(data).hexdigest(), path.name
    stem = Path(name).stem
    size = _valid_input_size(input_size, _max_input(settings)) or 640
    if class_count is not None and (isinstance(class_count, bool) or not isinstance(class_count, int) or class_count < 1):
        raise StructureRequestError("MODEL_CONFIG_INVALID", "class_count must be a positive integer")
    pre = text_diagnostics(text)
    if any(d["code"] == "MODEL_CONFIG_INVALID" for d in pre):
        raise StructureRequestError("MODEL_CONFIG_INVALID", "invalid detector YAML configuration")
    if pre:
        return declared_fail(None, pre, ident=stem, input_size=size)
    try:
        data, diagnostics = load_declared_text(text)
        if not isinstance(data, dict):
            raise ValueError("model configuration must be a mapping")
        data, source = derive_model_scale(data, name)
        genotype = Genotype.from_yaml_dict(data, family=stem, template_id="user:" + stem)
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise StructureRequestError("MODEL_CONFIG_INVALID", "invalid detector YAML configuration") from exc
    if diagnostics:
        doc = declared_fail(genotype, diagnostics, ident=stem, input_size=size)
    else:
        def inprocess() -> dict[str, Any]:
            try:
                return check_genotype_inprocess(genotype, size, class_count)
            except (ImportError, OSError) as exc:
                return {**_unavailable({"architecture_hash": None, "id": stem}, f"{type(exc).__name__}: {exc}"[:300],
                                       "STRUCTURE_RUNTIME_UNAVAILABLE"), "input_size": size}

        doc = _execute("check_structure", {"genotype": genotype.to_json(), "input_size": size, "class_count": class_count},
                       inprocess, ident=stem, input_size=size)
    doc["scale_source"] = source
    doc["evidence_source"] = "executed_structure_check"
    doc["source_sha256"] = digest
    return doc
