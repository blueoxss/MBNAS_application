"""Baseline registry: 14 default entries + PILOT_SMOKE representatives, with probe- and GPU-evidence-backed status.

``build_entries(probe, evidence)`` merges the static :mod:`.specs` declarations, one probe JSON
(``<output_root>/registry/probe_<ts>.json`` written by ``scripts/probe_registry.py``) and the GPU evidence files under
``<output_root>/evidence`` into contract ``RegistryEntry`` dicts. Status rules (never silently skipped):

* no probe record for the id -> ``UNVERIFIED`` (reason: no probe evidence);
* CPU build failed -> ``UNSUPPORTED`` (reason: the build error);
* official weights missing/mismatched (models that have official weights) -> ``BLOCKED_SOURCE``;
* otherwise ``AVAILABLE``. Train/resume/val capability statuses say whether the evidence is the entry's own GPU run
  (``VERIFIED_GPU``: ``train_smoke`` / ``resume_equivalence`` / ``resume_sanity`` / ``evaluation_pipeline`` files),
  its adapter family's PILOT representative (``VERIFIED_FAMILY_REPRESENTATIVE``), a failed run (``FAILED_GPU``) or
  missing (``NOT_RUN``).

Device / TensorRT capability (S3-F1): ``capabilities.devices`` has one row per declared target
(:data:`~mbnas_model_runtime.benchmark.capability.DECLARED_TARGETS`). ``host-gpu-0`` (host GPU) is ``VERIFIED_TRT`` /
``FAILED_TRT`` from the entry's OWN benchmark evidence (``bench_host_gpu_*.json`` subjects, matched by official
weights file name; ``qa_etri_hybrid_tensorrt_record.json`` for the ETRI hybrid adapter path, reported as
``VERIFIED_TRT_ADAPTER_PATH`` / export ``VERIFIED_ADAPTER_PATH`` because it is a tiny synthetic model), merged
newest-first per model like :func:`scan_evidence`, else ``NOT_RUN`` with a reason (never a family proxy). Every other declared target
(Jetson Orin NX / Orin Nano / AGX Orin) is ``UNAVAILABLE`` with the reason. ``capabilities.export_trt`` is filled from
the same evidence. ``evidence.source`` is the source identity: pip package name + version + sha256 of its dist-info
``RECORD`` (ultralytics / rfdetr), or the recorded ETRI notebook blob sha1 + audited ETRI_NAS HEAD (ETRI hybrid; the
constants of ``adapters.etri_hybrid``, no notebook file is read). ``capabilities.adapter_class`` names the family
adapter class. All of this is read from JSON / metadata files: loading the registry never imports torch.

``load_registry(output_root)`` uses the newest probe file under ``<output_root>/registry`` (falling back to the
canonical core output root :data:`DEFAULT_OUTPUT_ROOT` = ``$MBNAS_HOME/core/runs`` when a run-specific output root has
none); evidence is merged from both roots. A root this process cannot read (PermissionError/OSError) is skipped, not
fatal (DD-26): :func:`roots_skipped` lists it for ``GET /registry`` and the doctor. ``registry_lookup`` is the
validator's lookup. ``SPEC_BY_ID`` / ``adapter_for`` / ``arch_spec`` live in :mod:`mbnas_model_runtime.registry`
(contract section 4) and are re-exported here for existing callers.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from mbnas_contracts.schemas.api import RegistryEntry
from mbnas_contracts.util.home import mbnas_home
from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for, arch_spec
from mbnas_model_runtime.registry.specs import SPECS, ModelSpec, representative_for

log = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = str(mbnas_home() / "core" / "runs")  # == CoreSettings().output_root default (DD-A13)
EVIDENCE_KINDS = ("resume_equivalence", "resume_sanity", "train_smoke", "evaluation_pipeline")
RESUME_KINDS = ("resume_equivalence", "resume_sanity")
HOST_TARGET = "host-gpu-0"
TRT_EVIDENCE_GLOBS = ("bench_host_gpu_*.json", "qa_etri_hybrid_tensorrt_record*.json")


def _candidate_roots(output_root: str) -> list[Path]:
    """``output_root`` first, then the canonical evidence root (a run-specific output root without its own probe
    still sees the host's registry evidence)."""
    roots = [Path(output_root)]
    try:
        other = Path(output_root).resolve() != Path(DEFAULT_OUTPUT_ROOT).resolve()
    except OSError:
        other = True
    if other:
        roots.append(Path(DEFAULT_OUTPUT_ROOT))
    return roots


def _unreadable(path: Path) -> str | None:
    """``None`` when ``path`` can be listed or does not exist (an absent root simply has no evidence); otherwise the
    ``ExceptionType: message`` of the OS error (PermissionError for a root the server user may not read)."""
    try:
        with os.scandir(path):
            return None
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def roots_skipped(output_root: str = DEFAULT_OUTPUT_ROOT) -> list[dict[str, str]]:
    """Diagnostic list ``[{path, reason}]`` of the registry roots (and their ``registry`` / ``evidence`` sub-directories)
    that exist but cannot be read by this process. They are skipped by :func:`load_registry` (DD-26): entries stay
    UNVERIFIED for evidence that lives only there, while readable roots still produce VERIFIED / AVAILABLE entries."""
    out: list[dict[str, str]] = []
    for root in _candidate_roots(output_root):
        why = _unreadable(root)
        if why is not None:
            out.append({"path": str(root), "reason": why})
            continue
        for sub in ("registry", "evidence"):
            why = _unreadable(root / sub)
            if why is not None:
                out.append({"path": str(root / sub), "reason": why})
    return out


def _roots(output_root: str) -> list[Path]:
    """Readable roots of :func:`_candidate_roots` (a root that raises PermissionError/OSError is skipped, never fatal;
    see :func:`roots_skipped` for the diagnostic)."""
    return [root for root in _candidate_roots(output_root) if _unreadable(root) is None]


def _glob_files(root: Path, sub: str, pattern: str) -> list[Path]:
    """``sorted((root / sub).glob(pattern))`` that treats an unreadable directory as empty."""
    try:
        return sorted((root / sub).glob(pattern))
    except OSError:
        return []


def latest_probe_path(output_root: str = DEFAULT_OUTPUT_ROOT) -> Path | None:
    """Newest ``probe_*.json`` of the first readable root (see :func:`_roots`) that has one."""
    for root in _roots(output_root):
        files = _glob_files(root, "registry", "probe_*.json")
        if files:
            return files[-1]
    return None


def scan_evidence(output_root: str = DEFAULT_OUTPUT_ROOT) -> dict[tuple[str, str], dict[str, Any]]:
    """Newest evidence JSON per (model_id, kind) under ``<root>/evidence`` of every readable root."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    paths = sorted(p for root in _roots(output_root) for p in _glob_files(root, "evidence", "*.json"))
    for path in paths:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("kind") not in EVIDENCE_KINDS or not doc.get("model_id"):
            continue
        key = (doc["model_id"], doc["kind"])
        if key not in found or str(doc.get("created_at", "")) >= str(found[key].get("created_at", "")):
            found[key] = {"path": str(path), "verdict": doc.get("verdict"), "created_at": doc.get("created_at"),
                          "summary": doc.get("summary")}
    return found


def _trt_record(doc: Mapping[str, Any], record: Mapping[str, Any], path: Path, created: str,
                scope: str) -> dict[str, Any]:
    engines = {p: {k: e.get(k) for k in ("status", "sha256", "bytes", "tensorrt_version", "error", "onnx_sha256")}
               for p, e in sorted((record.get("engines") or {}).items())}
    runtimes = {k: (v or {}).get("status") for k, v in sorted((record.get("runtimes") or {}).items())
                if k.startswith("tensorrt:")}
    device = record.get("device") or {}
    return {"path": str(path), "created_at": created, "kind": doc.get("kind"), "scope": scope,
            "engines": engines, "tensorrt_runtimes": runtimes,
            "device": {"name": device.get("name") or device.get("torch_device_name"), "uuid": device.get("uuid")},
            "subject": (record.get("subject") or {}).get("name"), "imgsz": (record.get("subject") or {}).get("imgsz")}


def _subject_model_id(subject: Mapping[str, Any]) -> str | None:
    """Registry id of a benchmark subject: official weights file name (per backend), else the subject name."""
    backend = subject.get("backend")
    weights = Path(str((subject.get("source") or {}).get("path") or "")).name
    for spec in SPECS:
        if spec.backend == backend and spec.weights_file and spec.weights_file == weights:
            return spec.id
    name = subject.get("name")
    return name if name in SPEC_BY_ID else None


def scan_trt_evidence(output_root: str = DEFAULT_OUTPUT_ROOT, *,
                      include_canonical: bool = True) -> dict[str, dict[str, Any]]:
    """Newest TensorRT evidence per registry id (files without an engine build for that model are skipped).

    ``bench_host_gpu_*.json`` subjects map to their own registry id (``scope='own_model'``); the ETRI hybrid
    ``qa_etri_hybrid_tensorrt_record.json`` (tiny synthetic hybrid through the ETRI adapter export/engine path) maps to
    the ETRI entry with ``scope='adapter_path_tiny_synthetic'``.
    """
    from mbnas_model_runtime.registry.specs import ETRI_ID

    roots = _roots(output_root) if include_canonical else [r for r in [Path(output_root)] if _unreadable(r) is None]
    found: dict[str, dict[str, Any]] = {}
    paths = sorted({p for root in roots for g in TRT_EVIDENCE_GLOBS for p in _glob_files(root, "evidence", g)})
    for path in paths:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        cands: list[tuple[str, Mapping[str, Any], str, str]] = []
        if doc.get("kind") == "bench_host_gpu":
            for subj in doc.get("subjects") or []:
                rec = (subj or {}).get("record") or {}
                mid = _subject_model_id(rec.get("subject") or {})
                if mid:
                    cands.append((mid, rec, str(doc.get("created_at", "")), "own_model"))
        elif doc.get("kind") == "qa_etri_hybrid_tensorrt" and isinstance(doc.get("record"), dict):
            rec = doc["record"]
            if (rec.get("subject") or {}).get("backend") == "etri_hybrid":
                cands.append((ETRI_ID, rec, str(rec.get("started_at", "")), "adapter_path_tiny_synthetic"))
        for mid, rec, created, scope in cands:
            if not rec.get("engines"):
                continue  # this run measured no TensorRT engine for the model
            if mid not in found or created >= found[mid]["created_at"]:
                found[mid] = _trt_record(doc, rec, path, created, scope)
    return found


def _trt_status(ev: Mapping[str, Any] | None) -> str:
    if ev is None:
        return "NOT_RUN"
    built = [e for e in ev["engines"].values() if e.get("status") == "BUILT"]
    measured = [s for s in ev["tensorrt_runtimes"].values() if s == "MEASURED"]
    return "VERIFIED_TRT" if built and measured and len(built) == len(ev["engines"]) else "FAILED_TRT"


def device_capability(spec: ModelSpec, trt: Mapping[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(capabilities.devices, capabilities.export_trt)`` for one entry (see module docstring)."""
    from mbnas_model_runtime.benchmark.capability import DECLARED_TARGETS, SUPPORTED_TARGETS, target_status

    ev = trt.get(spec.id)
    status = _trt_status(ev)
    devices: dict[str, Any] = {}
    for tid, t in DECLARED_TARGETS.items():
        if tid != HOST_TARGET or tid not in SUPPORTED_TARGETS:
            devices[tid] = {"name": t["name"], "kind": t["kind"], "status": "UNAVAILABLE",
                            "reason": target_status(tid)["reason"], "tensorrt": None, "evidence": None}
            continue
        adapter_path = ev is not None and ev["scope"] != "own_model" and status == "VERIFIED_TRT"
        row: dict[str, Any] = {"name": t["name"], "kind": t["kind"],
                               "status": "VERIFIED_TRT_ADAPTER_PATH" if adapter_path else status, "evidence": None}
        if ev is None:
            row["reason"] = (f"no TensorRT engine build for {spec.id} in the benchmark evidence "
                             f"({', '.join(TRT_EVIDENCE_GLOBS)}); no family proxy is used")
        else:
            row.update(evidence=ev["path"], evidence_scope=ev["scope"], measured_on=ev["device"],
                       tensorrt={p: e.get("status") for p, e in ev["engines"].items()},
                       tensorrt_runtimes=ev["tensorrt_runtimes"])
        devices[tid] = row
    if ev is None:
        export = {"status": "NOT_RUN", "evidence": None, "device": HOST_TARGET, "reason": devices[HOST_TARGET]["reason"],
                  "method": "benchmark module (TensorRT python API from the exported ONNX)"}
    else:
        ok = status == "VERIFIED_TRT"
        own = ev["scope"] == "own_model"
        export = {"status": ("VERIFIED_GPU" if own else "VERIFIED_ADAPTER_PATH") if ok else "FAILED_GPU",
                  "evidence": ev["path"], "evidence_scope": ev["scope"], "device": HOST_TARGET,
                  "measured_on": ev["device"], "engines": ev["engines"], "created_at": ev["created_at"],
                  "method": "benchmark module (TensorRT python API from the exported ONNX)",
                  "tensorrt_version": next((e.get("tensorrt_version") for e in ev["engines"].values()
                                            if e.get("tensorrt_version")), None)}
        if not own:
            export["detail"] = (f"engine evidence is a tiny synthetic {spec.family} model ({ev.get('subject')}, "
                                f"imgsz {ev.get('imgsz')}) through this entry's adapter export/engine path, not this "
                                "entry's registry config")
        export["deployable_to"] = [HOST_TARGET]
    return devices, export


def _record_sha256(dist_name: str) -> dict[str, Any]:
    import hashlib
    from importlib import metadata

    try:
        dist = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError as exc:
        return {"kind": "pip", "package": dist_name, "version": None, "error": f"PackageNotFoundError: {exc}"}
    record = next((f for f in (dist.files or []) if f.name == "RECORD" and f.parent.name.endswith(".dist-info")), None)
    out: dict[str, Any] = {"kind": "pip", "package": dist_name, "version": dist.version}
    if record is None:
        return {**out, "record": None, "record_sha256": None, "error": "no dist-info RECORD"}
    path = Path(str(record.locate()))
    try:
        return {**out, "record": str(path), "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError as exc:
        return {**out, "record": str(path), "record_sha256": None, "error": f"{type(exc).__name__}: {exc}"}


_SOURCE_CACHE: dict[str, dict[str, Any]] = {}


def source_identity(spec: ModelSpec) -> dict[str, Any]:
    """``evidence.source`` (see module docstring); cached per backend for the process."""
    if spec.backend not in _SOURCE_CACHE:
        if spec.backend in ("ultralytics", "rfdetr"):
            _SOURCE_CACHE[spec.backend] = _record_sha256(spec.backend)
        else:
            from mbnas_model_runtime.adapters.etri_hybrid import ETRI_AUDITED_HEAD, NOTEBOOK_BLOB_SHA1, NOTEBOOK_CELLS

            # recorded constants only: the notebook path / on-disk comparison is gone (contract section 8)
            _SOURCE_CACHE[spec.backend] = {
                "kind": "etri_notebook", "notebook_blob_sha1": NOTEBOOK_BLOB_SHA1,
                "notebook_cells": dict(NOTEBOOK_CELLS), "etri_audited_head": ETRI_AUDITED_HEAD}
    return dict(_SOURCE_CACHE[spec.backend])


def _capability_status(spec: ModelSpec, evidence: Mapping[tuple[str, str], dict[str, Any]],
                       kinds: tuple[str, ...]) -> dict[str, Any]:
    for owner, scope in ((spec.id, "VERIFIED_GPU"), (representative_for(spec), "VERIFIED_FAMILY_REPRESENTATIVE")):
        for kind in kinds:
            ev = evidence.get((owner, kind))
            if ev is not None:
                ok = ev["verdict"] in ("PASS", "TOLERANCE")
                return {"status": scope if ok else "FAILED_GPU", "evidence": ev["path"], "verdict": ev["verdict"],
                        "kind": kind, "evidence_model_id": owner, "summary": ev.get("summary")}
    return {"status": "NOT_RUN", "evidence": None}


def _status(rec: Mapping[str, Any] | None, spec: ModelSpec) -> tuple[str, str]:
    if rec is None:
        return "UNVERIFIED", "no probe evidence for this entry (run scripts/probe_registry.py)"
    build = rec.get("build") or {}
    if not build.get("ok"):
        return "UNSUPPORTED", f"CPU build failed: {build.get('error')}"
    weights = rec.get("weights") or {}
    if spec.weights_file and weights.get("status") != "VERIFIED":
        return "BLOCKED_SOURCE", f"official weights {spec.weights_file}: {weights.get('status')}"
    return "AVAILABLE", "probe: CPU build ok" + ("; official weights verified" if spec.weights_file else
                                                  "; scratch-only (no official weights)")


def build_entry(spec: ModelSpec, rec: Mapping[str, Any] | None, probe_meta: Mapping[str, Any],
                evidence: Mapping[tuple[str, str], dict[str, Any]],
                trt: Mapping[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """One contract RegistryEntry dict (``trt``: :func:`scan_trt_evidence` result; None = no TRT evidence)."""
    adapter = adapter_for(spec)
    declared = adapter.capability()
    devices, export_trt = device_capability(spec, trt or {})
    status, reason = _status(rec, spec)
    skipped = list(probe_meta.get("roots_skipped") or [])
    if status == "UNVERIFIED" and skipped:
        reason += ("; registry roots skipped because they are unreadable by this process: "
                   + ", ".join(f"{i['path']} ({i['reason']})" for i in skipped))
    rec = rec or {}
    train = _capability_status(spec, evidence, ("train_smoke", *RESUME_KINDS))
    resume = _capability_status(spec, evidence, RESUME_KINDS)
    evaluation = _capability_status(spec, evidence, ("evaluation_pipeline", "train_smoke"))
    onnx = rec.get("onnx_export")
    caps = {
        "backend": spec.backend, "version": (probe_meta.get("versions") or {}).get(
            {"ultralytics": "ultralytics", "rfdetr": "rfdetr"}.get(spec.backend, "torch")),
        "import_class": spec.import_class, "init_modes": list(spec.init_modes),
        "native_recipe_init": spec.native_recipe_init, "inference_head": spec.inference_head,
        "input_size_rule": spec.input_size_rule, "precisions": ["FP32", "FP16"],
        "train_precisions": list(spec.train_precisions), "native_loss": spec.native_loss,
        "determinism_profile": spec.determinism_profile, "gradient_accumulation": declared.get("gradient_accumulation",
                                                                                            "must_be_1"),
        "default_baseline14": spec.default, "pilot_representative": spec.pilot_representative,
        "train": train, "val": {**evaluation, "evaluator": "mbnas-evaluator/1 (common)"},
        "full_state_resume": {"declared": declared.get("full_state_resume"),
                              "detail": declared.get("full_state_resume_detail"), **resume},
        "export_onnx": ({"status": "VERIFIED_CPU_RANDOM_INIT", "evidence": probe_meta.get("path"),
                         "outputs": (onnx or {}).get("outputs"), "opset": (onnx or {}).get("opset")}
                        if onnx and onnx.get("ok") else
                        {"status": "FAILED" if onnx else "NOT_RUN", "error": (onnx or {}).get("error"),
                         "evidence": probe_meta.get("path") if onnx else None}),
        "export_trt": export_trt, "devices": devices,
        "adapter_class": type(adapter).__qualname__, "adapter_module": type(adapter).__module__,
        "family_label": declared.get("family_label"),
    }
    evidence_out = {
        "version": caps["version"], "import_class": spec.import_class, "config_ref": rec.get("config_ref"),
        "weights": rec.get("weights"), "build": rec.get("build"), "pretrained_load": rec.get("pretrained_load"),
        "probe_file": probe_meta.get("path"), "probed_at": probe_meta.get("created_at"),
        "notes": list(spec.notes), "source": source_identity(spec),
    }
    if skipped:  # DD-26 diagnostic: why evidence under these roots was not considered (absent when all roots readable)
        evidence_out["registry_roots_skipped"] = skipped
    entry = {"id": spec.id, "family": spec.family, "status": status, "reason": reason, "capabilities": caps,
             "evidence": evidence_out}
    RegistryEntry.model_validate(entry)
    return entry


def build_entries(probe: Mapping[str, Any] | None, evidence: Mapping[tuple[str, str], dict[str, Any]] | None = None,
                  probe_path: str | None = None,
                  trt: Mapping[str, dict[str, Any]] | None = None,
                  skipped_roots: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    meta = {"path": probe_path, "created_at": (probe or {}).get("created_at"), "versions": (probe or {}).get("versions"),
            "roots_skipped": skipped_roots or []}
    recs = (probe or {}).get("entries", {})
    return [build_entry(s, recs.get(s.id), meta, evidence or {}, trt or {}) for s in SPECS]


def load_registry(output_root: str = DEFAULT_OUTPUT_ROOT) -> dict[str, dict[str, Any]]:
    """id -> entry using the newest probe file (all UNVERIFIED when none exists). Unreadable roots are skipped and
    logged (:func:`roots_skipped`), never fatal: the readable roots' evidence alone decides the statuses."""
    skipped = roots_skipped(output_root)
    for item in skipped:
        log.warning("registry root skipped (unreadable): %s (%s); entries whose evidence lives only there stay "
                    "UNVERIFIED", item["path"], item["reason"])
    path = latest_probe_path(output_root)
    probe = json.loads(path.read_text()) if path else None
    entries = build_entries(probe, scan_evidence(output_root), str(path) if path else None,
                            scan_trt_evidence(output_root), skipped)
    return {e["id"]: e for e in entries}


def registry_lookup(output_root: str = DEFAULT_OUTPUT_ROOT):  # noqa: ANN201 - returns the validator callable
    """``ValidationContext.registry_lookup`` over :func:`load_registry` (loaded once)."""
    entries = load_registry(output_root)
    return entries.get


__all__ = ["SPECS", "SPEC_BY_ID", "ModelSpec", "adapter_for", "arch_spec", "build_entries", "build_entry",
           "device_capability", "latest_probe_path", "load_registry", "registry_lookup", "roots_skipped", "scan_evidence",
           "scan_trt_evidence", "source_identity"]
