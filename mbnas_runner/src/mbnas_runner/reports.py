"""What the runner daemon verifies and reports about files its child wrote (A3 sections 6.3 and 9.3).

The daemon is the supervising process (not the child), so the DD-8 trust boundary stays where the owner worker had it:

* :func:`verify_checkpoint` - the owner worker's ``_checkpoint`` checks (inside the run dir, regular file, size equals
  the reported ``bytes``, kind in the four checkpoint kinds, a candidate) plus a streaming ``sha256_file`` recompute
  compared with the reported digest. A rejected report is never sent to the core; it becomes a
  ``CHECKPOINT_REPORT_REJECTED`` log message of the next progress batch;
* :func:`artifact_registration` - an ``outputs.evaluation.artifacts[*]`` entry of the child's result turned into an
  ``POST /artifacts`` item (run-relative ``relpath``, recomputed ``sha256`` and ``bytes``); entries that leave the
  run dir, are missing or whose reported digest disagrees with the bytes are not registered (the core then records
  them as artifact problems, as before);
* :func:`execution_configuration` / :func:`native_images` - the ``candidates/<cid>/architecture.yaml`` and trainer
  plots that ``engine.job_finished`` used to read from the core's disk (``record_model_configuration`` /
  ``record_native_images``), reported as pre-registered artifacts with the same metadata rules.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.paths import is_within

CHECKPOINT_KINDS = ("training_full", "eval_ema", "weights_only", "best")
MAX_CONFIG_BYTES = 1 << 20
MAX_IMAGE_BYTES = 32 << 20
MAX_NATIVE_IMAGES = 256
NATIVE_IMAGE = re.compile(r"^(?:train_batch\d+\.(?:jpg|png)|val_batch\d+_(?:pred|labels)\.(?:jpg|png)|"
                          r"confusion_matrix(?:_normalized)?\.png|results\.png|labels(?:_correlogram)?\.jpg|"
                          r"(?:Box)?(?:PR|P|R|F1)_curve\.png)$", re.IGNORECASE)


def normal_sha(value: Any) -> str:
    """Lower-case hex digest (an optional ``sha256:`` prefix is tolerated); "" when absent."""
    text = str(value or "").strip().lower()
    return text[7:] if text.startswith("sha256:") else text


def relpath_in(path: str, run_dir: str | Path) -> str | None:
    """POSIX path of ``path`` relative to ``run_dir`` when it is inside it (realpaths), else None."""
    real, base = os.path.realpath(path), os.path.realpath(str(run_dir))
    if not is_within(real, base) or real == base:
        return None
    return Path(os.path.relpath(real, base)).as_posix()


def verify_checkpoint(p: dict[str, Any], run_dir: str | Path, candidate_id: str | None,
                      default_rung: str | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(``POST /checkpoints`` body without fence/``checkpoint_id``, or None; detail) for a child ``checkpoint`` payload.

    ``uri`` is the runner-local ABSOLUTE path of the file (its realpath on this host). The core stores it in its DB
    (``checkpoints.path``, ``artifact_locations.uri``) for the owner only and echoes it back to this runner as
    ``resume.*.path``; it is never projected into a public response (C08 EVAL_1 D3). ``relpath`` (run-relative) is the
    key both sides share."""
    path = os.path.realpath(str(p.get("path", "")))
    rel = relpath_in(path, run_dir)
    ok = bool(rel and os.path.isfile(path) and os.path.getsize(path) == p.get("bytes")
              and p.get("kind") in CHECKPOINT_KINDS and candidate_id)
    problem = "outside run dir, missing or size mismatch"
    actual = reported = ""
    if ok:
        reported = normal_sha(p.get("sha256"))
        try:
            actual = sha256_file(path)
        except OSError:
            ok, problem = False, "unreadable file"
        else:
            if not reported or actual != reported:
                ok, problem = False, "sha256 mismatch (reported digest does not match the file)"
    detail = {"path": path, "reported_sha256": reported or None, "computed_sha256": actual or None,
              "problem": None if ok else problem}
    if not ok:
        return None, detail
    # "uri": runner-local absolute path -> core DB (owner-only), never projected publicly (see docstring)
    return {"candidate_id": candidate_id, "kind": p["kind"], "epoch": int(p.get("epoch") or 0), "uri": path,
            "relpath": rel, "sha256": actual, "bytes": int(p["bytes"]), "rung": p.get("rung") or default_rung,
            "hashes": p.get("hashes") or {}}, detail


def artifact_registration(item: Any, run_dir: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    """(``POST /artifacts`` item or None, problem) for one result artifact entry."""
    if not isinstance(item, dict) or not item.get("path") or not item.get("kind"):
        return None, f"malformed artifact entry: {item!r}"[:300]
    path = os.path.realpath(str(item["path"]))
    rel = relpath_in(path, run_dir)
    if rel is None or not os.path.isfile(path):
        return None, f"{item.get('kind')}: {item['path']} is missing or outside the run dir"
    try:
        actual = sha256_file(path)
    except OSError as exc:
        return None, f"{item.get('kind')}: unreadable ({exc})"
    reported = normal_sha(item.get("sha256"))
    if reported and reported != actual:
        return None, f"{item.get('kind')}: sha256 mismatch (reported digest does not match the file)"
    meta = dict(item.get("meta") or {})
    if item.get("matching"):
        meta["matching"] = item["matching"]
    return {"kind": item["kind"], "relpath": rel, "sha256": actual, "bytes": os.path.getsize(path),
            "mime_type": item.get("mime_type"), "sample_id": item.get("sample_id"),
            "split_hash": item.get("split_hash"), "candidate_id": item.get("candidate_id"),
            "state": item.get("state") or "READY", "meta": meta}, None


def candidate_root(run_dir: str | Path, candidate_id: str) -> Path | None:
    if not candidate_id or "/" in candidate_id or "\\" in candidate_id or candidate_id in (".", "..") \
            or "\x00" in candidate_id:
        return None
    return Path(os.path.realpath(str(run_dir))) / "candidates" / candidate_id


def execution_configuration(run_dir: str | Path, candidate_id: str | None, family: str | None,
                            template_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(artifact item, report) for the executed ``candidates/<cid>/architecture.yaml``; (None, None) when absent.

    The report is ``{yaml_text, yaml_hash, sha256, class_count}`` (``artifact_id`` is added after registration); a file
    that is not a supported model YAML yields (None, {"error": ...}) - the core records it, nothing is guessed."""
    root = candidate_root(run_dir, candidate_id or "")
    if root is None:
        return None, None
    path = root / "architecture.yaml"
    if path.is_symlink() or not path.is_file():
        return None, None
    if path.stat().st_size > MAX_CONFIG_BYTES:
        return None, {"error": "model configuration exceeds size limit"}
    text = path.read_text(encoding="utf-8")
    try:
        from mbnas_model_runtime.genotype import Genotype

        genotype = Genotype.from_yaml_text(text, family=str(family or ""), template_id=str(template_id or candidate_id))
    except Exception as exc:  # noqa: BLE001 - ValueError/KeyError/TypeError/yaml.YAMLError: reported, not raised
        return None, {"error": f"saved model configuration is not a supported model YAML ({type(exc).__name__})"}
    sha = sha256_file(path)
    item = {"kind": "architecture", "relpath": Path(os.path.relpath(path, os.path.realpath(str(run_dir)))).as_posix(),
            "sha256": sha, "bytes": path.stat().st_size, "mime_type": "application/x-yaml", "sample_id": None,
            "split_hash": None, "candidate_id": candidate_id, "state": "READY",
            "meta": {"configuration_kind": "execution", "yaml_hash": genotype.yaml_hash()}}
    return item, {"yaml_text": text, "yaml_hash": genotype.yaml_hash(), "sha256": sha,
                  "class_count": genotype.yaml.get("nc")}


def native_images(run_dir: str | Path, candidate_id: str | None) -> list[dict[str, Any]]:
    """Artifact items of the trainer's own plots under ``candidates/<cid>/native`` (``record_native_images`` rules:
    file-name allowlist, size cap, PIL verification, at most 256, metadata without guessed identifiers)."""
    root = candidate_root(run_dir, candidate_id or "")
    if root is None:
        return []
    native = root / "native"
    if not native.is_dir() or native.resolve() != native:
        return []
    base = os.path.realpath(str(run_dir))
    out: list[dict[str, Any]] = []
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
            continue
        if len(out) >= MAX_NATIVE_IMAGES:
            break
        name = path.name.lower()
        category = "training_batch" if name.startswith("train_batch") else "validation_predictions" if "_pred." in name else (
            "validation_labels" if "_labels." in name else "confusion_matrix" if name.startswith("confusion_matrix") else "training_plot")
        stage_match = re.fullmatch(r"stage_e(\d+)_e(\d+)", path.parent.name)
        batch = re.search(r"(?:train|val)_batch(\d+)", name)
        meta = {"origin": "native_training", "category": category, "filename": path.name, "stage": path.parent.name,
                "candidate_id": candidate_id, "batch_index": int(batch[1]) if batch else None,
                "stage_epoch_start": int(stage_match[1]) if stage_match else None,
                "stage_epoch_target": int(stage_match[2]) if stage_match else None, "epoch": None,
                "checkpoint_id": None,
                "identifier_note": "native batch/plot artifact; exact sample and checkpoint are not recorded"}
        kind = "confusion_matrix" if category == "confusion_matrix" else "pr_curve" if "pr_curve" in name else "other"
        out.append({"kind": kind, "relpath": Path(os.path.relpath(resolved, base)).as_posix(),
                    "sha256": sha256_file(resolved), "bytes": resolved.stat().st_size,
                    "mime_type": "image/png" if path.suffix.lower() == ".png" else "image/jpeg", "sample_id": None,
                    "split_hash": None, "candidate_id": candidate_id, "state": "READY", "meta": meta})
    return out


def runtime_manifest() -> dict[str, Any]:
    """R6 runtime manifest for ``POST /register`` (versions, torch/cuda/python, operator and parser digests)."""
    import hashlib
    import inspect
    import platform
    from importlib import metadata

    def version(dist: str) -> str | None:
        try:
            return metadata.version(dist)
        except metadata.PackageNotFoundError:
            return None

    from mbnas_runner.envelope import ENVELOPE_VERSION

    out: dict[str, Any] = {"mbnas_runner": version("mbnas-runner"),
                           "mbnas_model_runtime": version("mbnas-model-runtime"),
                           "mbnas_contracts": version("mbnas-contracts"), "python": platform.python_version(),
                           "torch": None, "cuda": None, "envelope_version": ENVELOPE_VERSION,
                           "operator_files_sha256": None, "parser_source_sha256": None}
    try:
        import torch

        out["torch"], out["cuda"] = torch.__version__, torch.version.cuda
    except Exception:  # noqa: BLE001 - absent/broken torch is reported as unknown, the core labels it
        pass
    try:
        from mbnas_model_runtime.operators import operator_files_sha256

        out["operator_files_sha256"] = operator_files_sha256()
    except Exception:  # noqa: BLE001
        pass
    try:
        from ultralytics.nn import tasks

        original = getattr(tasks.parse_model, "_nas_mixed_original", tasks.parse_model)
        out["parser_source_sha256"] = hashlib.sha256(inspect.getsource(original).encode()).hexdigest()
    except Exception:  # noqa: BLE001
        pass
    return out
