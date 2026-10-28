"""Shared job plumbing for the training and evaluation handlers.

* :func:`load_protocol` — the resolved protocol from ``params.resolved_protocol`` or ``params.protocol_path`` whose
  ``protocol_hash`` must equal ``spec.protocol_hash`` (``PROTOCOL_HASH_MISMATCH`` otherwise);
* :func:`prepare_backend_env` — ultralytics settings dir under the output root, ``RF_HOME`` under the weights dir;
* :func:`split_manifests` — manifests of the protocol's split roots, checked against the resolved manifest hashes;
  every read is appended to the run's split access log (:class:`SplitAccess`, ``<run_dir>/logs/split_access.jsonl``:
  job id/kind/arm, split, root, hash, purpose) so audits can prove ``final_eval`` is read only by FINAL_RETRAIN
  (or arm Final) evaluation;
* :func:`local_data_path` — ``data_refs`` remapping: a dataset path frozen in the resolved protocol
  (the core host's ``console.paths.*``) becomes this host's path through ``RunnerSettings.dataset_mounts``
  (``{protocol_root: local_root}``, longest matching root wins). With no mounts configured the protocol paths are used
  as they are (single-host / owner profile). A path no mount covers, or a mapped path that does not exist, fails with
  ``DATASET_UNAVAILABLE``; a remapped split whose manifest hash differs from the frozen one fails with
  ``DATASET_DRIFT`` (the split hash is location independent, so a faithful copy passes). Nothing proceeds silently;
* :func:`resolve_model` — ArchSpec from ``params.arch`` (required: the core resolves registry baselines) and
  the adapter built from it;
* :func:`progress_bridge` — adapter progress dicts -> :class:`ProgressMessage` for the worker's ``emit``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from mbnas_model_runtime.adapters.base import ArchSpec, CapabilityError
from mbnas_runner.config import RunnerSettings
from mbnas_model_runtime.datasets.manifest import DatasetService, Manifest
from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
from mbnas_contracts.schemas.jobs import JobSpec, ProgressMessage
from mbnas_contracts.util.paths import check_server_path
from mbnas_contracts.util.time import utcnow_iso

PICKLE_RESUME_ENV = "MBNAS_RUNNER_ALLOW_PICKLE_RESUME"  # read by the RF-DETR adapter (pickle_resume_allowed)
SPLIT_KEYS = {"train": ("train_root", "train_manifest_hash"),
              "search_val": ("search_val_root", "search_validation_manifest_hash"),
              "final_eval": ("final_eval_root", "final_evaluation_manifest_hash")}


class JobInputError(RuntimeError):
    """A job's inputs are inconsistent with the immutable protocol (``code`` is the JobResult reason_code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def load_protocol(params: Any, spec: JobSpec) -> dict[str, Any]:
    from mbnas_contracts.protocol_hash import protocol_hash

    if params.resolved_protocol is not None:
        proto = params.resolved_protocol
    elif params.protocol_path:
        proto = json.loads(Path(params.protocol_path).read_text())
    else:
        raise JobInputError("PROTOCOL_MISSING", "job params need resolved_protocol or protocol_path")
    proto = proto.get("resolved", proto) if isinstance(proto, dict) and "training" not in proto else proto
    digest = protocol_hash(proto)
    if digest != spec.protocol_hash:
        raise JobInputError("PROTOCOL_HASH_MISMATCH", f"job protocol_hash {spec.protocol_hash} != resolved {digest}")
    return proto


def check_run_dir(run_dir: str, settings: RunnerSettings) -> Path:
    chk = check_server_path(run_dir, kind="dir", allow_roots=settings.output_roots, must_exist=False, writable=True)
    if not chk.ok:
        raise JobInputError(chk.code or "OUTPUT_NOT_WRITABLE", f"run_dir {run_dir}: {chk.message}")
    path = Path(chk.realpath)
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_backend_env(settings: RunnerSettings) -> None:
    from mbnas_model_runtime.adapters.ultralytics_adapter import prepare_ultralytics_env

    prepare_ultralytics_env(Path(settings.output_root) / "ultralytics_cfg", settings.weights_dir)
    os.environ.setdefault("RF_HOME", str(Path(settings.weights_dir) / "rfdetr"))
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    # mbnas_model_runtime cannot import RunnerSettings (layering), so the effective flag reaches the RF-DETR adapter
    # through the environment; duck-typed settings without the field (owner tests) leave the variable untouched
    allow_pickle = getattr(settings, "allow_pickle_resume", None)
    if allow_pickle is not None:
        os.environ[PICKLE_RESUME_ENV] = "1" if allow_pickle else "0"


SPLIT_ACCESS_LOG = "logs/split_access.jsonl"


@dataclass(frozen=True)
class SplitAccess:
    """Who reads a split (appended to ``<run_dir>/logs/split_access.jsonl`` before the manifest is built)."""

    run_dir: str
    job_id: str
    job_kind: str
    arm: str | None
    candidate_id: str | None
    purpose: str

    @classmethod
    def for_job(cls, run_dir: str | Path, spec: JobSpec, *, purpose: str) -> "SplitAccess":
        return cls(str(run_dir), spec.job_id, str(spec.kind.value), spec.arm, spec.candidate_id, purpose)

    def record(self, split: str, root: str) -> None:
        line = {"ts": utcnow_iso(), "job_id": self.job_id, "job_kind": self.job_kind, "arm": self.arm,
                "candidate_id": self.candidate_id, "split": split, "root": root, "purpose": self.purpose,
                "pid": os.getpid()}
        path = Path(self.run_dir) / SPLIT_ACCESS_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:  # one short O_APPEND line per read
            fh.write(json.dumps(line, sort_keys=True) + "\n")


def read_split_access_log(run_dir: str | Path) -> list[dict[str, Any]]:
    """Parsed lines of a run's split access log ([] when nothing was read)."""
    path = Path(run_dir) / SPLIT_ACCESS_LOG
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def local_data_path(path: str, settings: Any) -> tuple[str, bool]:
    """(local path, remapped?) of a protocol dataset path; see the module docstring (``DATASET_UNAVAILABLE``)."""
    mounts: Mapping[str, str] = getattr(settings, "dataset_mounts", None) or {}
    if not mounts:
        return path, False
    norm = os.path.normpath(path)
    best: tuple[str, str] | None = None
    for proto_root, local_root in mounts.items():
        root = os.path.normpath(proto_root)
        if norm == root or norm.startswith(root.rstrip(os.sep) + os.sep):
            if best is None or len(root) > len(best[0]):
                best = (root, local_root)
    if best is None:
        raise JobInputError("DATASET_UNAVAILABLE", f"no dataset mount covers protocol path {path} (configured "
                            f"protocol roots: {sorted(mounts)})")
    rel = os.path.relpath(norm, best[0])
    local = os.path.normpath(os.path.join(best[1], rel)) if rel != "." else best[1]
    if not os.path.exists(local):
        raise JobInputError("DATASET_UNAVAILABLE", f"protocol path {path} maps to {local}, which does not exist")
    return local, True


def split_manifests(protocol: Mapping[str, Any], settings: RunnerSettings, splits: tuple[str, ...], *,
                    access: SplitAccess | None = None) -> tuple[dict[int, str], dict[str, Manifest]]:
    """Names + manifests for ``splits``; every manifest hash must equal the resolved protocol's.

    ``final_eval`` is refused unless ``access`` identifies a FINAL_RETRAIN job or an arm-Final job (search,
    baseline and proposal work never read the final evaluation data).
    """
    paths = protocol["console"]["paths"]
    if "final_eval" in splits and not (access is not None and (access.job_kind == "FINAL_RETRAIN" or access.arm == "Final")):
        raise JobInputError("FINAL_SPLIT_FORBIDDEN", "final_eval is read only by FINAL_RETRAIN / arm Final evaluation")
    yaml_splits = tuple({"train": "train", "search_val": "val", "final_eval": "test"}[split] for split in splits)
    names = load_dataset_yaml(local_data_path(paths["dataset_yaml"], settings)[0], required_splits=yaml_splits).names
    service = DatasetService.from_settings(settings)
    out: dict[str, Manifest] = {}
    for split in splits:
        root_key, hash_key = SPLIT_KEYS[split]
        root, remapped = local_data_path(paths[root_key], settings)
        if access is not None:
            access.record(split, paths[root_key])  # the protocol path (the audit key), not the mount
        manifest = service.manifest_for(root, names)
        want = protocol["dataset"].get(hash_key)
        if manifest.split_hash != want:
            raise JobInputError("DATASET_DRIFT" if remapped else "DATASET_HASH_MISMATCH",
                                f"{split}: data now hashes {manifest.split_hash}, protocol froze {want}"
                                + (f" (protocol root {paths[root_key]} mounted at {root})" if remapped else ""))
        if manifest.issues:
            raise JobInputError("DATASET_INVALID", f"{split}: {len(manifest.issues)} manifest issues")
        out[split] = manifest
    return {int(k): str(v) for k, v in names.items()}, out


def resolve_model(params: Any, settings: RunnerSettings) -> tuple[ArchSpec, Any, dict[str, Any] | None]:
    """(ArchSpec, adapter, None) from ``params.arch``, which is required.

    The core decides whether a registry baseline is AVAILABLE and hands its ArchSpec over like any other model, so the
    runner reads no registry evidence. An ``arch`` that equals a registry declaration (same id, backend, family and
    variant) gets that declaration's adapter from ``adapter_for``; any other ``arch`` is a search candidate or user YAML
    on the ultralytics backend. The registry-entry slot of the tuple stays (always ``None``) for the handlers' call
    sites; ``settings`` is unused and kept for the same reason.
    """
    from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for

    if not params.arch:
        raise JobInputError("ARCH_REQUIRED", "params.arch is required: the core resolves registry baselines to an "
                            "ArchSpec before the job reaches a runner")
    arch = ArchSpec(**params.arch)
    spec = SPEC_BY_ID.get(arch.model_id)
    if spec is not None and (arch.backend, arch.family, arch.variant) == (spec.backend, spec.family, spec.variant):
        return arch, adapter_for(spec), None
    if arch.backend != "ultralytics":
        raise CapabilityError("BACKEND_UNSUPPORTED", f"search candidates use the ultralytics backend, got {arch.backend}")
    from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

    return arch, UltralyticsAdapter(arch.family, arch.variant), None


def progress_bridge(spec: JobSpec, emit: Callable[[ProgressMessage], None]) -> Callable[[dict[str, Any]], None]:
    """Translate adapter progress dicts (``type`` epoch/checkpoint/metric/log) into ProgressMessages."""

    def bridge(event: dict[str, Any]) -> None:
        kind = event.get("type")
        rest = {k: v for k, v in event.items() if k != "type"}
        if kind == "epoch":
            emit(ProgressMessage(type="progress", job_id=spec.job_id, ts=utcnow_iso(), epoch=int(event["epochs_done"]),
                                 epoch_target_total=int(event["epoch_target_total"]),
                                 global_step=int(event["global_step"]), samples_seen=int(event["samples_seen"]),
                                 learning_rate=float(event["lr"]), payload=_jsonable(rest)))
        elif kind == "checkpoint":
            emit(ProgressMessage(type="checkpoint", job_id=spec.job_id, ts=utcnow_iso(), epoch=int(event["epoch"]),
                                 payload=_jsonable({**rest, "candidate_id": spec.candidate_id, "run_id": spec.run_id})))
        else:
            emit(ProgressMessage(type="metric" if kind == "metric" else "log", job_id=spec.job_id, ts=utcnow_iso(),
                                 message=event.get("message"), payload=_jsonable(rest)))

    return bridge


def _jsonable(obj: Any) -> Any:
    """Drop NaN/inf (JSON lines forbid them) and coerce numpy scalars."""
    import math

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):
        return _jsonable(obj.item())
    return obj


jsonable = _jsonable
