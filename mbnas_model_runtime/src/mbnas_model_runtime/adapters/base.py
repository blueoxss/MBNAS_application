"""Backend adapter contract (PLAN.md §3) shared by every training backend.

An adapter turns one *architecture* (a registry baseline or a search candidate) into a trainable model on its native
framework and implements the fixed-horizon ``run_until`` semantics for that framework:

* ``capability()`` — what the adapter can do and how well it was verified (train/val/full-state resume/export/init
  modes/native loss/precisions/determinism profile). Values are evidence-backed declarations, never aspirations.
* ``build(arch, num_classes, init, seed)`` — CPU build for registry probes and candidate validation.
* ``train_until(ctx)`` — train from the last full-state checkpoint (or from scratch) until ``ctx.cumulative_target``
  epochs with an LR schedule fixed to ``ctx.fixed_horizon`` epochs; writes one full-state checkpoint per epoch, the
  exact rung-end FP32 EMA evaluation snapshot and the weights-only size artifact.
* ``load_eval_model(eval_ckpt, device)`` / ``predict(model, images, cfg)`` — common prediction format
  ``{"sample_id", "boxes_xyxy" (source-image pixels), "scores", "classes"}``.
* ``latency_fns(model, shape, precision, device)`` — callables per latency scope for :mod:`evaluation.latency`.
* ``weights_only(eval_ckpt, path)`` — FP16 contiguous state_dict file (size artifact), returns its sha256.

This module is import-light (no torch) so the API process can use the dataclasses and exceptions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

LATENCY_SCOPES = ("forward_only", "forward_and_postprocess", "end_to_end")
DEFAULT_LATENCY_SCOPE = "forward_and_postprocess"
#: register_hook strings a job spec may name -> the function they run. Exact strings
#: only; the legacy string stored in existing runs (``resolved.console.extra.training.arch.register_hook``) is an
#: explicit alias of the same target. This is data: the alias module does not exist and is never imported.
REGISTER_HOOKS: dict[str, str] = {
    "mbnas_model_runtime.operators:ensure_registered": "mbnas_model_runtime.operators:ensure_registered",
    "mbnas_server.search.etri_bridge:ensure_mixed_registered": "mbnas_model_runtime.operators:ensure_registered",
}


class CapabilityError(RuntimeError):
    """The adapter cannot do what was requested (unsupported init/precision/recipe); never silently substituted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ResumeBlocked(RuntimeError):
    """A full-state resume is impossible (missing state key, hash mismatch, corrupt/stripped file).

    ``reason`` is a stable machine code (``MISSING_STATE:<key>``, ``HASH_MISMATCH:<name>``, ``CHECKSUM_MISMATCH``,
    ``SIDECAR_MISSING``, ``FORMAT_UNKNOWN``, ``UNREADABLE``, ``PICKLE_RESUME_DISABLED``). There is never a weights-only
    fallback: the caller marks the candidate ``RESUME_BLOCKED`` and a weights-only restart would be a new trial.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"RESUME_BLOCKED {reason}: {detail}" if detail else f"RESUME_BLOCKED {reason}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ArchSpec:
    """What to build.

    ``config`` is backend specific: an ultralytics model yaml path or yaml dict (``variant`` ``detect``/``rtdetr``),
    an RF-DETR class name (``RFDETRNano`` …), or the ETRI ``make_detector`` keyword dict. ``weights_path`` is used only
    for ``init == "pretrained_native"``. ``register_hook`` (a ``module:function`` string allowlisted in
    :data:`REGISTER_HOOKS`) runs before the build, e.g. to register search-space operators with ultralytics' parser.
    """

    model_id: str
    family: str
    backend: str
    config: Any
    variant: str = "detect"
    weights_path: str | None = None
    weights_sha256: str | None = None
    inference_head: str = "native"
    register_hook: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BuildReport:
    model_id: str
    backend: str
    params: int
    architecture_hash: str
    init: str
    module_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataSpec:
    """Training/validation data resolved from the split manifests (never the raw protocol paths)."""

    names: dict[int, str]
    train_manifest: Any  # datasets.manifest.Manifest
    val_manifest: Any  # datasets.manifest.Manifest
    view_root: Path | None = None  # run-local YOLO view (symlinked images, copied labels) built by training.dataset_view

    @property
    def hashes(self) -> dict[str, str]:
        return {"dataset_train": self.train_manifest.split_hash, "dataset_val": self.val_manifest.split_hash}


@dataclass
class TrainContext:
    """Everything ``train_until`` needs for one stage (one ``run_until`` call)."""

    candidate_id: str
    candidate_dir: Path
    arch: ArchSpec
    data: DataSpec
    recipe: dict[str, Any]
    init: str
    training_seed: int
    cumulative_target: int
    fixed_horizon: int
    protocol_hash: str
    device: str = "cuda:0"
    rung: str | None = None
    resume_state: dict[str, Any] | None = None  # verified full state (training.checkpoint.load_full_state)
    resume_path: str | None = None
    stop_requested: Callable[[], bool] | None = None
    progress: Callable[[dict[str, Any]], None] | None = None


@dataclass
class RungResult:
    """Outcome of one ``run_until`` call (PLAN §2 ``training/stage_trainer.py``)."""

    candidate_id: str
    epochs_done: int
    cumulative_target: int
    fixed_horizon: int
    stopped_reason: str  # TARGET_REACHED | PAUSED_AT_EPOCH
    rung_end_training_ckpt: str | None
    rung_end_ema_eval_ckpt: str | None
    weights_only_path: str | None
    size_mb: float | None
    size_dtype: str | None
    lr_history: list[dict[str, Any]]
    step_count: int
    samples_seen: int
    checkpoint_sha256: dict[str, str]
    resumed_from: str | None
    resolved_recipe: dict[str, Any]
    hashes: dict[str, str]
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class MbnasAdapter(Protocol):
    """Structural type every backend adapter satisfies (see module docstring)."""

    backend: str

    def capability(self) -> dict[str, Any]: ...

    def build(self, arch: ArchSpec, num_classes: int, init: str, seed: int) -> BuildReport: ...

    def train_until(self, ctx: TrainContext) -> RungResult: ...

    def required_state_keys(self) -> tuple[str, ...]: ...

    def load_eval_model(self, eval_ckpt: str | Path, device: str) -> Any: ...

    def predict(self, model: Any, images: list[tuple[str, str]], cfg: Mapping[str, Any]) -> list[dict[str, Any]]: ...

    def latency_fns(self, model: Any, shape: tuple[int, int], precision: str, device: str,
                    sample_image: str) -> dict[str, Callable[[], Any]]: ...

    def postprocess_description(self, model: Any) -> dict[str, Any]: ...

    def weights_only(self, eval_ckpt: str | Path, path: str | Path) -> str: ...


def check_init(init: str, supported: tuple[str, ...], model_id: str) -> None:
    """Raise ``CapabilityError(INIT_UNSUPPORTED)`` unless ``init`` is one of the adapter's declared modes."""
    if init not in supported:
        raise CapabilityError("INIT_UNSUPPORTED", f"{model_id} supports init modes {list(supported)}, not {init!r}")


def resolve_register_hook(spec: str | None) -> Callable[[], Any] | None:
    """Import the function of an allowlisted register hook (:data:`REGISTER_HOOKS`); every other string is refused
    (no arbitrary code from job specs)."""
    if not spec:
        return None
    target = REGISTER_HOOKS.get(spec) if isinstance(spec, str) else None
    if target is None:
        raise CapabilityError("REGISTER_HOOK_REFUSED", f"register hook must be one of {sorted(REGISTER_HOOKS)}, "
                                                       f"got {spec!r}")
    module, _, func = target.partition(":")
    import importlib

    return getattr(importlib.import_module(module), func)


def default_weights_dir() -> Path:
    """``$MBNAS_HOME/weights``: the hosts' default ``weights_dir``, used when a caller
    passes no weights directory (``registry.arch_spec``, the RF-DETR ``RF_HOME`` default)."""
    from mbnas_contracts.util.home import mbnas_home

    return mbnas_home() / "weights"
