"""Link the adapter's actual saved configuration to common model detail/artifacts.

Only the fixed candidate-local architecture.yaml is considered.  It is parsed as
data, never executed, and an absent configuration stays absent.  This applies to
baseline and explicit/source candidates without introducing a second builder.
Each registered executed configuration is also recorded as a ``config_revisions`` row of kind ``executed``
(:mod:`mbnas_core.orchestration.config_revisions`; the same bytes re-reported at a later rung are not recorded twice).
"""
from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Any

import yaml

from mbnas_core.artifacts.registry import ArtifactError, register_file, run_dir
from mbnas_core.db import repo
from mbnas_model_runtime.genotype import Genotype

MAX_CONFIG_BYTES = 1 << 20
MAX_IMAGE_BYTES = 32 << 20
MAX_NATIVE_IMAGES = 256
NATIVE_IMAGE = re.compile(r"^(?:train_batch\d+\.(?:jpg|png)|val_batch\d+_(?:pred|labels)\.(?:jpg|png)|"
                          r"confusion_matrix(?:_normalized)?\.png|results\.png|labels(?:_correlogram)?\.jpg|"
                          r"(?:Box)?(?:PR|P|R|F1)_curve\.png)$", re.IGNORECASE)


def record_model_configuration(conn: sqlite3.Connection, run: dict[str, Any],
                                candidate: dict[str, Any]) -> dict[str, Any]:
    """Record/download the actual adapter output. Must run inside caller transaction."""
    cid = str(candidate["id"])
    if not cid or "/" in cid or "\\" in cid or cid in (".", "..") or "\x00" in cid:
        raise ArtifactError("ACCESS_DENIED", "unsafe model candidate id")
    base = run_dir(run).resolve()
    candidate_root = base / "candidates" / cid
    path = candidate_root / "architecture.yaml"
    resolved = path.resolve()
    if candidate_root.resolve() != candidate_root or path.is_symlink() or not resolved.is_relative_to(candidate_root):
        raise ArtifactError("ACCESS_DENIED", "model configuration leaves its run directory")
    if not resolved.is_file():
        return candidate
    if resolved.stat().st_size > MAX_CONFIG_BYTES:
        raise ArtifactError("LOAD_FAILED", "model configuration exceeds size limit")
    text = resolved.read_text(encoding="utf-8")
    lineage = candidate.get("lineage") or {}
    template = lineage.get("template") if isinstance(lineage, dict) else None
    template = template if isinstance(template, dict) else {}
    try:
        genotype = Genotype.from_yaml_text(text, family=str(template.get("family") or candidate["family"]),
                                           template_id=str(template.get("id") or cid))
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ArtifactError("LOAD_FAILED", "saved model configuration is not a supported model YAML") from exc
    artifact = register_file(conn, run, kind="architecture", path=str(path), candidate_id=cid,
                              meta={"configuration_kind": "execution", "yaml_hash": genotype.yaml_hash(),
                                    "architecture_hash": candidate.get("architecture_hash")})
    audit = dict(candidate.get("audit") or {})
    saved = audit.get("structure")
    if isinstance(saved, dict) and saved.get("yaml_hash") != genotype.yaml_hash():
        audit.pop("structure", None)  # an old proof cannot validate a changed executed configuration
    audit["execution_configuration"] = {"artifact_id": artifact["id"], "path": str(path),
                                         "sha256": artifact["sha256"], "yaml_hash": genotype.yaml_hash(),
                                         "class_count": genotype.yaml.get("nc")}
    _record_executed(conn, run, candidate, text, genotype, artifact["id"])
    return repo.update_candidate(conn, cid, architecture_yaml=text, architecture_path=str(path), audit=audit)


def _record_executed(conn: sqlite3.Connection, run: dict[str, Any], candidate: dict[str, Any], text: str,
                     genotype: Genotype, artifact_id: str) -> None:
    from mbnas_core.orchestration import config_revisions
    from mbnas_core.orchestration.engine import log

    config_revisions.try_record(conn, log, run_id=run["id"], candidate_id=str(candidate["id"]), kind="executed",
                                text=text, canonical_hash=genotype.yaml_hash(), canonical_text=genotype.yaml_text(),
                                architecture_hash=candidate.get("architecture_hash"), artifact_id=artifact_id)


def record_model_configuration_from_report(conn: sqlite3.Connection, run: dict[str, Any], candidate: dict[str, Any],
                                          report: dict[str, Any]) -> dict[str, Any]:
    """The executed configuration reported by a remote runner instead of read from this host.

    ``report`` = ``{yaml_text, yaml_hash, sha256, class_count, artifact_id}`` where ``artifact_id`` is the
    ``architecture`` artifact the runner pre-registered (E7, digest recomputed by the runner daemon). The text is parsed
    as data (never executed); its yaml_hash and the artifact's sha256 must agree with the report. Only the DB is
    updated (same audit fields as :func:`record_model_configuration`; ``path`` is the run-relative artifact path).
    Must run inside the caller transaction.
    """
    import hashlib

    cid = str(candidate["id"])
    text = report.get("yaml_text")
    if not isinstance(text, str) or not text or len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ArtifactError("LOAD_FAILED", "reported model configuration is missing or exceeds the size limit")
    artifact = repo.get_artifact(conn, run["id"], str(report.get("artifact_id") or ""))
    if artifact is None or artifact["kind"] != "architecture":
        raise ArtifactError("MISSING", "reported model configuration artifact is not registered for this run")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (artifact.get("sha256") or "").removeprefix("sha256:") != digest or \
            str(report.get("sha256") or digest).removeprefix("sha256:") != digest:
        raise ArtifactError("LOAD_FAILED", "reported model configuration does not match its registered digest")
    lineage = candidate.get("lineage") or {}
    template = lineage.get("template") if isinstance(lineage, dict) else None
    template = template if isinstance(template, dict) else {}
    try:
        genotype = Genotype.from_yaml_text(text, family=str(template.get("family") or candidate["family"]),
                                           template_id=str(template.get("id") or cid))
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ArtifactError("LOAD_FAILED", "reported model configuration is not a supported model YAML") from exc
    if report.get("yaml_hash") and report["yaml_hash"] != genotype.yaml_hash():
        raise ArtifactError("LOAD_FAILED", "reported yaml_hash differs from the configuration text")
    repo.link_artifact(conn, run["id"], artifact["id"], candidate_id=cid)
    audit = dict(candidate.get("audit") or {})
    saved = audit.get("structure")
    if isinstance(saved, dict) and saved.get("yaml_hash") != genotype.yaml_hash():
        audit.pop("structure", None)  # an old proof cannot validate a changed executed configuration
    audit["execution_configuration"] = {"artifact_id": artifact["id"], "path": artifact["relpath"],
                                         "sha256": digest, "yaml_hash": genotype.yaml_hash(),
                                         "class_count": genotype.yaml.get("nc")}
    _record_executed(conn, run, candidate, text, genotype, artifact["id"])
    return repo.update_candidate(conn, cid, architecture_yaml=text, audit=audit)


def record_native_images(conn: sqlite3.Connection, run: dict[str, Any],
                         candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Register existing trainer plots without synthesis, inference, or checkpoint guesses.

    Native train_batchN is a batch/step artifact, not an epoch or individual sample.
    Stage start/target epochs are recorded only when the native directory names
    contain them. Exact checkpoint and sample identifiers remain null when absent.
    """
    cid = str(candidate["id"])
    if not cid or "/" in cid or "\\" in cid or cid in (".", "..") or "\x00" in cid:
        raise ArtifactError("ACCESS_DENIED", "unsafe model candidate id")
    base = run_dir(run).resolve()
    candidate_root = base / "candidates" / cid
    native = candidate_root / "native"
    if not native.exists():
        return []
    if candidate_root.resolve() != candidate_root or native.resolve() != native:
        raise ArtifactError("ACCESS_DENIED", "native images leave their run directory")
    artifacts = []
    for path in sorted(native.rglob("*")):
        if not NATIVE_IMAGE.fullmatch(path.name) or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != path or not resolved.is_relative_to(native) or resolved.stat().st_size > MAX_IMAGE_BYTES:
            continue
        try:
            from PIL import Image

            with Image.open(resolved) as image:
                image.verify()
        except (OSError, ValueError, ImportError):
            continue  # a filename alone is not evidence that a plot/image exists
        if len(artifacts) >= MAX_NATIVE_IMAGES:
            break
        name = path.name.lower()
        category = "training_batch" if name.startswith("train_batch") else "validation_predictions" if "_pred." in name else (
            "validation_labels" if "_labels." in name else "confusion_matrix" if name.startswith("confusion_matrix") else "training_plot")
        stage_match = re.fullmatch(r"stage_e(\d+)_e(\d+)", path.parent.name)
        batch = re.search(r"(?:train|val)_batch(\d+)", name)
        metadata = {"origin": "native_training", "category": category, "filename": path.name,
                    "stage": path.parent.name, "candidate_id": cid,
                    "batch_index": int(batch[1]) if batch else None,
                    "stage_epoch_start": int(stage_match[1]) if stage_match else None,
                    "stage_epoch_target": int(stage_match[2]) if stage_match else None,
                    "epoch": None, "checkpoint_id": None,
                    "identifier_note": "native batch/plot artifact; exact sample and checkpoint are not recorded"}
        kind = "confusion_matrix" if category == "confusion_matrix" else "pr_curve" if "pr_curve" in name else "other"
        artifacts.append(register_file(conn, run, kind=kind, path=str(path), candidate_id=cid,
                                        mime_type="image/png" if path.suffix.lower() == ".png" else "image/jpeg", meta=metadata))
    return artifacts
