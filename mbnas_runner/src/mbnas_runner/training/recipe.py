"""Resolved protocol -> backend-neutral training recipe (the only place protocol training fields are interpreted).

Recipe keys: optimizer, lr0, lrf, weight_decay, momentum (beta1), beta2, warmup_epochs, cos_lr, close_mosaic,
batch, gradient_accumulation, workers, precision, amp, input_shape, fixed_horizon, augmentation_profile, ema.
Adapters reject what they cannot honour with ``CapabilityError`` (never a silent substitution).
"""
from __future__ import annotations

from typing import Any, Mapping

from mbnas_model_runtime.adapters.base import CapabilityError

DEFAULT_WORKERS = 8


def stride_ceil(shape: list[int], stride: int = 32, square: bool = False) -> list[int]:
    sides = [max(shape)] * 2 if square else list(shape)
    return [-(-int(x) // stride) * stride for x in sides]


def baseline_resolved(protocol: Mapping[str, Any], model_id: str) -> dict[str, Any] | None:
    """``console.baselines[i].resolved`` for ``model_id`` (None when the id is not a protocol baseline)."""
    for b in protocol.get("console", {}).get("baselines", []):
        if b.get("id") == model_id:
            return b.get("resolved") or {}
    return None


def input_shape_for(protocol: Mapping[str, Any], model_id: str | None, override: list[int] | None = None,
                    rule: Mapping[str, Any] | None = None) -> list[int]:
    """Override > the baseline's resolved shape > the adapter rule applied to ``training.requested_input_size``."""
    if override:
        return [int(v) for v in override]
    resolved = baseline_resolved(protocol, model_id) if model_id else None
    if resolved and resolved.get("resolved_input_shape"):
        return [int(v) for v in resolved["resolved_input_shape"]]
    requested = [int(v) for v in protocol["training"]["requested_input_size"]]
    rule = rule or {"kind": "stride_ceil", "stride": 32}
    if rule.get("kind") == "fixed":
        return [int(v) for v in rule["shape"]]
    return stride_ceil(requested, int(rule.get("stride", 32)), bool(rule.get("square")))


def recipe_from_protocol(protocol: Mapping[str, Any], input_shape: list[int], *, fixed_horizon: int) -> dict[str, Any]:
    """Backend-neutral recipe from the resolved ``training`` section (PLAN §8.3-8.4)."""
    t = protocol["training"]
    if t.get("precision") not in ("amp_fp16_fp32", "fp32"):
        raise CapabilityError("PRECISION_UNSUPPORTED", f"training precision {t.get('precision')!r}")
    betas = list(t.get("betas") or [0.9, 0.999])
    recipe = {
        "optimizer": t["optimizer"], "lr0": float(t["lr_initial"]), "lrf": float(t["lr_final_ratio"]),
        "weight_decay": float(t["weight_decay"]), "momentum": float(betas[0]), "beta2": float(betas[1]),
        "warmup_epochs": float(t.get("warmup_epochs", 0)), "cos_lr": str(t.get("scheduler", "")).startswith("cosine"),
        "close_mosaic": int(t.get("close_mosaic_epochs", 0) or 0), "batch": int(t["batch_per_gpu"]),
        "gradient_accumulation": int(t.get("gradient_accumulation", 1)),
        "workers": int(t.get("workers", DEFAULT_WORKERS)), "precision": t["precision"],
        "amp": t["precision"] == "amp_fp16_fp32", "input_shape": [int(v) for v in input_shape],
        "fixed_horizon": int(fixed_horizon), "augmentation_profile": t.get("augmentation_profile"), "ema": True,
    }
    explicit = ((protocol.get("console", {}).get("extra") or {}).get("training") or {})
    if explicit.get("enabled") is True:
        # Preserve historical recipe fingerprints when resuming pre-extension runs.
        recipe["validation_enabled"] = explicit.get("validation_enabled", True)
        if explicit.get("native_image_artifacts") is True:
            recipe["native_image_artifacts"] = True
    return recipe
