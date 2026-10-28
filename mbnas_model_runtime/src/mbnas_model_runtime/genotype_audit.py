"""Architecture audits: a YAML **config estimate** and a separate **built-model operator audit**.

Neither audit drives the search (no family quotas); they are reported per candidate so the UI can state the exact
scope. Wording rule: a model whose detection head is the convolutional ultralytics ``Detect`` is never called a
"full Transformer"; the label is ``"<backbone_family> with CNN detection head"`` (for example
``"transformer_backbone with CNN detection head"``).

* :func:`config_estimate` ports the ETRI ``mixed_search_space.audit_config`` rules (backbone rows; ``_attention`` row
  test; transformer_backbone only when every backbone row is a convolution-free NAS operator) and adds the head/neck
  rows plus ``head_preserved`` (head rows equal the native template's head rows).
* :func:`built_audit` counts modules of the CPU-built model per scope (backbone rows, neck rows, Detect): Conv2d,
  Linear, MultiheadAttention, attention markers (same marker set as ETRI ``mixed_blocks._scope_audit``), ETRI custom
  NAS blocks, parameters; the backbone family is classified from the counted modules.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

ATTENTION_ROW_NAMES = frozenset({"PSA", "C2PSA", "C2fPSA", "C2fAttn", "C3TR", "NASWindowTransformer",
                                 "NASGlobalTransformer", "TransformerBlock"})
PURE_ROW_NAMES = frozenset({"NASWindowTransformer", "NASGlobalTransformer", "NASPatchMerge"})
NAS_CUSTOM = ("NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer")
NATIVE_ATTENTION_MODULES = frozenset({"PSA", "C2PSA", "C2fPSA", "PSABlock", "AAttn", "ABlock", "C3TR"})
DETECT_HEAD_LABEL = "CNN detection head"


def _name(row: list[Any]) -> str:
    return str(row[2]).split(".")[-1]


def row_has_attention(row: list[Any]) -> bool:
    """ETRI ``_attention`` rule: C3k2 attends iff args[3]; A2C2f iff args[1] (default True); else by name."""
    name, args = _name(row), row[3] if len(row) > 3 else []
    if name == "C3k2":
        return bool(args[3]) if len(args) > 3 else False
    if name == "A2C2f":
        return bool(args[1]) if len(args) > 1 else True
    return name in ATTENTION_ROW_NAMES


def _rows_family(rows: list[list[Any]]) -> str:
    if rows and all(_name(r) in PURE_ROW_NAMES for r in rows):
        return "transformer_backbone"
    return "hybrid_backbone" if any(row_has_attention(r) for r in rows) else "cnn_backbone"


def config_estimate(model_yaml: dict[str, Any], native_head: list[list[Any]] | None = None) -> dict[str, Any]:
    """Config-level estimate (not proof of model family); see module docstring."""
    backbone = list(model_yaml.get("backbone", []))
    head = list(model_yaml.get("head", []))
    neck = [r for r in head if _name(r) != "Detect"]
    family = _rows_family(backbone)
    return {
        "scope": "config_estimate", "proof_of_model_family": False,
        "backbone_family": family,
        "backbone_operator_counts": dict(sorted(Counter(_name(r) for r in backbone).items())),
        "backbone_attention_rows": sum(row_has_attention(r) for r in backbone),
        "neck_operator_counts": dict(sorted(Counter(_name(r) for r in neck).items())),
        "neck_attention_rows": sum(row_has_attention(r) for r in neck),
        "detect_rows": sum(_name(r) == "Detect" for r in head),
        "head_preserved": (head == native_head) if native_head is not None else None,
        "label": f"{family} with {DETECT_HEAD_LABEL}",
        "rules": "ETRI mixed_search_space.audit_config (ported) + neck rows",
        "selection_policy": "single mixed pool; no family quotas",
    }


def _count(modules: list[Any]) -> dict[str, Any]:
    import torch.nn as nn

    conv_types = (nn.Conv1d, nn.Conv2d, nn.Conv3d, nn.ConvTranspose1d, nn.ConvTranspose2d, nn.ConvTranspose3d)
    seen: dict[int, Any] = {}
    params: dict[int, Any] = {}
    for root in modules:
        for m in root.modules():
            seen[id(m)] = m
        for p in root.parameters():
            params[id(p)] = p
    mods = list(seen.values())
    names = Counter(type(m).__name__ for m in mods)
    attention = sum(isinstance(m, nn.MultiheadAttention) or "attention" in type(m).__name__.lower()
                    or "transformer" in type(m).__name__.lower() or type(m).__name__ in NATIVE_ATTENTION_MODULES
                    for m in mods)
    return {"conv_modules": sum(isinstance(m, conv_types) for m in mods),
            "linear_modules": sum(isinstance(m, nn.Linear) for m in mods),
            "multihead_attention": sum(isinstance(m, nn.MultiheadAttention) for m in mods),
            "attention_markers": attention,
            "nas_custom_blocks": {n: names[n] for n in NAS_CUSTOM if names[n]},
            "native_attention_blocks": {n: names[n] for n in sorted(NATIVE_ATTENTION_MODULES) if names[n]},
            "params": sum(p.numel() for p in params.values())}


def classify_backbone(counts: dict[str, Any]) -> str:
    """cnn_backbone (no attention), transformer_backbone (attention and zero conv), else hybrid_backbone."""
    if not counts["attention_markers"]:
        return "cnn_backbone"
    return "transformer_backbone" if counts["conv_modules"] == 0 else "hybrid_backbone"


def built_audit(model: Any, n_backbone: int, *, flops: int | None = None, imgsz: int | None = None) -> dict[str, Any]:
    """Operator audit of a built ultralytics DetectionModel (scopes: backbone rows, neck rows, Detect)."""
    layers = list(model.model)
    detect = [m for m in layers if "detect" in type(m).__name__.lower()]
    backbone = layers[:n_backbone]
    neck = [m for m in layers[n_backbone:] if m not in detect]
    scopes = {"backbone": _count(backbone), "neck": _count(neck), "detect": _count(detect), "full_model": _count([model])}
    backbone_family = classify_backbone(scopes["backbone"])
    neck_family = classify_backbone(scopes["neck"]).replace("_backbone", "_neck")
    detect_conv = scopes["detect"]["conv_modules"]
    head_label = DETECT_HEAD_LABEL if detect_conv else "non-convolutional detection head"
    d0 = detect[0] if detect else None
    return {
        "scope": "built_model_operator_audit",
        "backbone_family": backbone_family, "neck_family": neck_family,
        "detect_head": {"class": type(d0).__name__ if d0 is not None else None, "conv_modules": detect_conv,
                        "end2end": bool(getattr(d0, "end2end", False)) if d0 is not None else None,
                        "reg_max": getattr(d0, "reg_max", None) if d0 is not None else None},
        "label": f"{backbone_family} with {head_label}",
        "full_transformer": False if detect_conv or scopes["full_model"]["conv_modules"] else None,
        "scopes": scopes, "params": scopes["full_model"]["params"],
        "flops": flops, "flops_input": [1, 3, imgsz, imgsz] if imgsz else None,
        "flops_method": "torch.utils.flop_counter.FlopCounterMode, eval, MHA fast path disabled" if flops else None,
    }
