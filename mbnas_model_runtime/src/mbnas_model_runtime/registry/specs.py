"""Static model specifications for the baseline14 registry (+ PILOT_SMOKE representatives).

These are *declarations* (what to build, where the config and official weights come from); availability is decided
only by probe evidence (``registry.probe`` / ``scripts/probe_registry.py``). Order: the 14 default ids in
``DEFAULT_BASELINE14`` order, then the non-default PILOT_SMOKE representatives.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mbnas_contracts.constants import DEFAULT_BASELINE14

ETRI_ID = "etri-controlled-cnn-transformer"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    family: str  # YOLO11 | YOLO26 | Transformer | Hybrid
    backend: str  # ultralytics | rfdetr | etri_hybrid
    variant: str  # detect | rtdetr | rfdetr | etri
    import_class: str
    config: Any  # ultralytics yaml name | RF-DETR class name | ETRI make_detector kwargs
    config_file: str  # package-relative source of the config
    weights_file: str | None
    inference_head: str
    native_loss: str
    init_modes: tuple[str, ...]
    native_recipe_init: str
    input_size_rule: dict[str, Any]
    train_precisions: tuple[str, ...]
    default: bool
    pilot_representative: bool
    determinism_profile: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_V8 = "v8DetectionLoss (CIoU + DFL + BCE, TAL topk 10)"
_E2E = "E2ELoss (one2many v8DetectionLoss topk10 + one2one topk7/1, epoch-decayed one2many weight)"
_RT = "RTDETRDetectionLoss (VFL + L1 + GIoU, Hungarian matching, contrastive denoising)"
_RF = "rfdetr SetCriterion (IA-BCE + L1 + GIoU, HungarianMatcher, group_detr 13)"
_ETRI = "SetCriterion-mbnas (ETRI notebook cell 12: Hungarian, CE + 5*L1 + 2*GIoU, eos 0.05)"
_STRIDE32 = {"kind": "stride_ceil", "stride": 32}
_SQUARE32 = {"kind": "stride_ceil", "stride": 32, "square": True}


def _ul(size: str, gen: str, pilot: bool) -> ModelSpec:
    family = "YOLO11" if gen == "11" else "YOLO26"
    return ModelSpec(
        id=f"yolo{gen}{size}", family=family, backend="ultralytics", variant="detect", import_class="ultralytics.YOLO",
        config=f"yolo{gen}{size}.yaml", config_file=f"ultralytics/cfg/models/{gen}/yolo{gen}.yaml",
        weights_file=f"yolo{gen}{size}.pt",
        inference_head="e2e_one2one_nms_false" if gen == "26" else "native",
        native_loss=_E2E if gen == "26" else _V8, init_modes=("scratch", "pretrained_native"),
        native_recipe_init="pretrained_native", input_size_rule=_STRIDE32, train_precisions=("amp_fp16_fp32", "fp32"),
        default=not pilot, pilot_representative=pilot, determinism_profile="pilot_bitwise_workers0")


def _rt(size: str) -> ModelSpec:
    return ModelSpec(
        id=f"rt-detr-{size}", family="Hybrid", backend="ultralytics", variant="rtdetr", import_class="ultralytics.RTDETR",
        config=f"rtdetr-{size}.yaml", config_file=f"ultralytics/cfg/models/rt-detr/rtdetr-{size}.yaml",
        weights_file=f"rtdetr-{size}.pt", inference_head="native", native_loss=_RT,
        init_modes=("scratch", "pretrained_native"), native_recipe_init="pretrained_native", input_size_rule=_STRIDE32,
        train_precisions=("amp_fp16_fp32", "fp32"), default=True, pilot_representative=size == "l",
        determinism_profile="torch_default_nondeterministic_backward",
        notes=("CNN backbone + Transformer decoder (hybrid); grid_sample backward is nondeterministic",))


_RF_CLASSES = {"nano": ("RFDETRNano", "rf-detr-nano.pth"), "small": ("RFDETRSmall", "rf-detr-small.pth"),
               "medium": ("RFDETRMedium", "rf-detr-medium.pth"), "large": ("RFDETRLarge", "rf-detr-large-2026.pth")}


def _rf(size: str) -> ModelSpec:
    cls, weights = _RF_CLASSES[size]
    return ModelSpec(
        id=f"rf-detr-{size}", family="Transformer", backend="rfdetr", variant="rfdetr", import_class=f"rfdetr.{cls}",
        config=cls, config_file="rfdetr/config.py", weights_file=weights, inference_head="native_no_nms",
        native_loss=_RF, init_modes=("scratch", "pretrained_native"), native_recipe_init="pretrained_native",
        input_size_rule=_SQUARE32, train_precisions=("amp_fp16_fp32",), default=size != "nano",
        pilot_representative=size == "nano", determinism_profile="ptl_nondeterministic_rng_overlay",
        notes=("DINOv2-windowed ViT encoder + LW-DETR decoder; scratch = pretrain_weights=None (true random init, "
               "PLAN §8.1); native recipe is pretrained_native (separate native_pretrained comparison group)",))


ETRI_SPEC = ModelSpec(
    id=ETRI_ID, family="Hybrid", backend="etri_hybrid", variant="etri",
    import_class="mbnas_model_runtime.adapters.etri_hybrid.model.ControlledDetector",
    config={"family": "cnn_transformer", "imgsz": 384, "patch": 16, "dim": 128, "depth": 3, "heads": 4},
    config_file="mbnas_model_runtime/adapters/etri_hybrid/model.py", weights_file=None, inference_head="native",
    native_loss=_ETRI, init_modes=("scratch",), native_recipe_init="scratch",
    input_size_rule={"kind": "fixed", "shape": [384, 384]}, train_precisions=("amp_fp16_fp32", "fp32"), default=True,
    pilot_representative=True, determinism_profile="deterministic_algorithms_warn_only_workers0",
    notes=("ETRI controlled CNN+Transformer hybrid (not SOTA, not pure Transformer); no official pretrained weights",))

SPECS: tuple[ModelSpec, ...] = (
    *(_ul(s, "11", False) for s in "smlx"), *(_ul(s, "26", False) for s in "smlx"),
    *(_rf(s) for s in ("small", "medium", "large")), _rt("l"), _rt("x"), ETRI_SPEC,
    _ul("n", "11", True), _ul("n", "26", True), _rf("nano"),
)
SPEC_BY_ID: dict[str, ModelSpec] = {s.id: s for s in SPECS}
PILOT_REPRESENTATIVE_OF = {"YOLO11": "yolo11n", "YOLO26": "yolo26n", "rtdetr": "rt-detr-l", "rfdetr": "rf-detr-nano",
                           "etri": ETRI_ID}

assert tuple(s.id for s in SPECS[:14]) == DEFAULT_BASELINE14, "registry order must match DEFAULT_BASELINE14"


def representative_for(spec: ModelSpec) -> str:
    """PILOT representative whose GPU evidence covers ``spec``'s adapter path."""
    if spec.variant == "detect":
        return PILOT_REPRESENTATIVE_OF[spec.family]
    return PILOT_REPRESENTATIVE_OF[spec.variant]
