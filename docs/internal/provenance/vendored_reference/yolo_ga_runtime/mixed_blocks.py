"""Linear patch and attention operators for ONE mixed ETRI_NAS search space.

Custom window attention is not an implementation of a named published model.
The additive parser installer changes only this Python process, never vendor files.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import math
import textwrap
from typing import Dict

import torch
from torch import nn
from torch.nn import functional as F

IMPLEMENTATION_VERSION = "1.0.0"


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, received {value!r}")
    return value


def _position_2d(height, width, channels, device, dtype):
    """Fixed local/global coordinates; arbitrary positive channel count is supported."""
    quarter = math.ceil(channels / 4)
    frequency = torch.exp(torch.arange(quarter, device=device, dtype=torch.float32)
                          * (-math.log(10000) / max(1, quarter - 1)))
    y, x = torch.meshgrid(torch.arange(height, device=device, dtype=torch.float32),
                          torch.arange(width, device=device, dtype=torch.float32), indexing="ij")
    x, y = x.reshape(-1, 1) * frequency, y.reshape(-1, 1) * frequency
    return torch.cat((x.sin(), x.cos(), y.sin(), y.cos()), dim=-1)[:, :channels].to(dtype=dtype)


class NASPatchMerge(nn.Module):
    """BCHW -> B,c2,ceil(H/patch),ceil(W/patch); reshape + Linear, no Conv.

    YAML: [-1, 1, NASPatchMerge, [c2, patch]]. This down operator must not repeat.
    Bottom/right zero padding handles nondivisible and small feature maps.
    """
    def __init__(self, c1, c2, patch=2):
        super().__init__()
        self.c1 = _positive_integer(c1, "c1")
        self.c2 = _positive_integer(c2, "c2")
        self.patch = _positive_integer(patch, "patch")
        self.projection = nn.Linear(c1 * patch * patch, c2)
        self.norm = nn.LayerNorm(c2)

    def forward(self, x):
        if x.ndim != 4 or x.shape[1] != self.c1:
            raise ValueError(f"NASPatchMerge expects BCHW with {self.c1} channels")
        batch, channels, height, width = x.shape
        patch = self.patch
        x = F.pad(x, (0, (-width) % patch, 0, (-height) % patch))
        hp, wp = x.shape[-2] // patch, x.shape[-1] // patch
        x = x.reshape(batch, channels, hp, patch, wp, patch)
        x = x.permute(0, 2, 4, 3, 5, 1).reshape(batch, hp, wp, channels * patch * patch)
        return self.norm(self.projection(x)).permute(0, 3, 1, 2).contiguous()


class _PreNormAttention(nn.Module):
    def __init__(self, channels, heads, mlp_ratio):
        super().__init__()
        if not isinstance(mlp_ratio, (int, float)) or not math.isfinite(mlp_ratio) or mlp_ratio <= 0:
            raise ValueError("mlp_ratio must be finite and positive")
        self.norm1 = nn.LayerNorm(channels)
        self.attention = nn.MultiheadAttention(channels, heads, dropout=0.0, batch_first=True)
        self.norm2 = nn.LayerNorm(channels)
        hidden = max(1, int(channels * mlp_ratio))
        self.mlp = nn.Sequential(nn.Linear(channels, hidden), nn.GELU(), nn.Linear(hidden, channels))

    def forward(self, tokens, padding_mask=None):
        normalized = self.norm1(tokens)
        attended = self.attention(normalized, normalized, normalized,
                                  key_padding_mask=padding_mask, need_weights=False)[0]
        tokens = tokens + attended
        return tokens + self.mlp(self.norm2(tokens))


class NASWindowTransformer(nn.Module):
    """BCHW-preserving unshifted local window attention, Linear channel projection.

    YAML: [-1, repeats, NASWindowTransformer, [c2, heads, window, mlp_ratio]].
    Parser inserts c1 and depth-scaled repeats. Actual heads = gcd(c2, requested).
    Padded keys are masked; outputs for invalid queries are removed when cropped.
    """
    def __init__(self, c1, c2, n=1, heads=4, window=7, mlp_ratio=2.0):
        super().__init__()
        self.c1, self.c2 = _positive_integer(c1, "c1"), _positive_integer(c2, "c2")
        self.n = _positive_integer(n, "n")
        self.requested_heads = _positive_integer(heads, "heads")
        self.actual_heads = math.gcd(c2, heads)
        self.window = _positive_integer(window, "window")
        self.projection = nn.Linear(c1, c2) if c1 != c2 else nn.Identity()
        self.blocks = nn.ModuleList(_PreNormAttention(c2, self.actual_heads, mlp_ratio) for _ in range(n))

    @staticmethod
    def _partition(x, window):
        batch, height, width, channels = x.shape
        return x.reshape(batch, height // window, window, width // window, window, channels).permute(
            0, 1, 3, 2, 4, 5).reshape(-1, window * window, channels)

    def forward(self, x):
        if x.ndim != 4 or x.shape[1] != self.c1:
            raise ValueError(f"NASWindowTransformer expects BCHW with {self.c1} channels")
        batch, _, height, width = x.shape
        if height < 1 or width < 1:
            raise ValueError("Attention feature maps must have positive spatial dimensions")
        window = self.window
        pad_h, pad_w = (-height) % window, (-width) % window
        features = self.projection(x.permute(0, 2, 3, 1))
        features = F.pad(features, (0, 0, 0, pad_w, 0, pad_h))
        valid = torch.ones((batch, height, width, 1), device=x.device, dtype=torch.bool)
        valid = F.pad(valid, (0, 0, 0, pad_w, 0, pad_h), value=False)
        tokens = self._partition(features, window)
        padding_mask = ~self._partition(valid, window).squeeze(-1)
        # Every ceil-partitioned window has valid pixels. Retain an explicit safeguard
        # against fully masked attention rows if partition rules are later extended.
        fully_masked = padding_mask.all(dim=1)
        padding_mask[:, 0] = padding_mask[:, 0] & ~fully_masked
        tokens = tokens + _position_2d(window, window, self.c2, x.device, tokens.dtype)[None]
        for block in self.blocks:
            tokens = block(tokens, padding_mask)
        hp, wp = height + pad_h, width + pad_w
        features = tokens.reshape(batch, hp // window, wp // window, window, window, self.c2)
        features = features.permute(0, 1, 3, 2, 4, 5).reshape(batch, hp, wp, self.c2)
        return features[:, :height, :width].permute(0, 3, 1, 2).contiguous()


class NASGlobalTransformer(nn.Module):
    """BCHW-preserving global attention with an explicit quadratic token budget.

    YAML: [-1, repeats, NASGlobalTransformer, [c2, heads, max_tokens, mlp_ratio]].
    High-resolution inputs fail clearly; no implicit pooling changes their geometry.
    """
    def __init__(self, c1, c2, n=1, heads=4, max_tokens=1024, mlp_ratio=2.0):
        super().__init__()
        self.c1, self.c2 = _positive_integer(c1, "c1"), _positive_integer(c2, "c2")
        self.n = _positive_integer(n, "n")
        self.requested_heads = _positive_integer(heads, "heads")
        self.actual_heads = math.gcd(c2, heads)
        self.max_tokens = _positive_integer(max_tokens, "max_tokens")
        self.projection = nn.Linear(c1, c2) if c1 != c2 else nn.Identity()
        self.blocks = nn.ModuleList(_PreNormAttention(c2, self.actual_heads, mlp_ratio) for _ in range(n))

    def forward(self, x):
        if x.ndim != 4 or x.shape[1] != self.c1:
            raise ValueError(f"NASGlobalTransformer expects BCHW with {self.c1} channels")
        batch, _, height, width = x.shape
        if not 0 < height * width <= self.max_tokens:
            raise ValueError(f"Global-attention token budget exceeded: {height}x{width}; max={self.max_tokens}")
        tokens = self.projection(x.flatten(2).transpose(1, 2))
        tokens = tokens + _position_2d(height, width, self.c2, x.device, tokens.dtype)[None]
        for block in self.blocks:
            tokens = block(tokens)
        return tokens.transpose(1, 2).reshape(batch, self.c2, height, width).contiguous()


_CUSTOM = {cls.__name__: cls for cls in (NASPatchMerge, NASWindowTransformer, NASGlobalTransformer)}
_REPEATED = ("NASWindowTransformer", "NASGlobalTransformer")


def _register_checkpoint_globals():
    """Trust only this extension and the concrete children it actually constructs.

    Retain PyTorch's restricted loading: do not disable weights_only or modify the
    checkpoint loader. MHA's out_proj uses a concrete Linear subclass, included here.
    """
    serialization = getattr(torch, "serialization", None)
    register = getattr(serialization, "add_safe_globals", None)
    if not callable(register):
        return {"checkpoint_safe_globals_available": False, "checkpoint_safe_globals_registered": []}
    trusted = [NASPatchMerge, NASWindowTransformer, NASGlobalTransformer, _PreNormAttention,
               nn.Linear, nn.LayerNorm, nn.MultiheadAttention, nn.Identity,
               nn.ModuleList, nn.Sequential, nn.GELU]
    mha_projection = getattr(nn.modules.linear, "NonDynamicallyQuantizableLinear", None)
    if mha_projection is not None:
        trusted.append(mha_projection)
    register(trusted)
    return {"checkpoint_safe_globals_available": True,
            "checkpoint_safe_globals_registered": [f"{cls.__module__}.{cls.__qualname__}" for cls in trusted]}


def _extend_parser_source(source):
    """Extend existing local membership sets while retaining every native branch."""
    tree = ast.parse(textwrap.dedent(source))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "parse_model"]
    if len(definitions) != 1:
        raise RuntimeError("Unsupported Ultralytics parser: expected one parse_model function")
    changed = set()
    for node in ast.walk(definitions[0]):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        for registry in names & {"base_modules", "repeat_modules"}:
            value = node.value
            elements = value.args[0] if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                                       and value.func.id in {"frozenset", "set"} and len(value.args) == 1) else value
            if not isinstance(elements, (ast.Set, ast.List, ast.Tuple)):
                raise RuntimeError(f"Unsupported parse_model {registry}: expected a literal module collection")
            additions = tuple(_CUSTOM) if registry == "base_modules" else _REPEATED
            existing = {element.id for element in elements.elts if isinstance(element, ast.Name)}
            elements.elts.extend(ast.Name(id=name, ctx=ast.Load()) for name in additions if name not in existing)
            changed.add(registry)
    if changed != {"base_modules", "repeat_modules"}:
        raise RuntimeError("Unsupported Ultralytics parser: base_modules/repeat_modules assignments not found. "
                           "Install a supported Ultralytics build; no vendor files were changed.")
    return ast.fix_missing_locations(tree)


def register_ultralytics_blocks() -> Dict:
    """Idempotently register classes and extend parse_model in this process only."""
    import ultralytics
    from ultralytics.nn import modules, tasks

    original = tasks.parse_model
    if getattr(original, "_nas_mixed_installed", False):
        result = dict(original._nas_mixed_capabilities)
        result.update(_register_checkpoint_globals())
        result["already_installed"] = True
        original._nas_mixed_capabilities = dict(result)
        return result
    try:
        source = inspect.getsource(original)
    except (OSError, TypeError) as exc:
        raise RuntimeError("Cannot inspect Ultralytics parse_model source; mixed NAS registration aborted. "
                           "No vendor files were changed.") from exc
    tree = _extend_parser_source(source)
    namespace = {}
    try:
        exec(compile(tree, "<ETRI_NAS additive parse_model extension>", "exec"), tasks.__dict__, namespace)
    except Exception as exc:
        raise RuntimeError("Ultralytics parser extension failed; vendor files were not modified") from exc
    patched = namespace.get("parse_model")
    if not callable(patched):
        raise RuntimeError("Compiled parser extension did not produce parse_model")
    checkpoint_capabilities = _register_checkpoint_globals()
    for name, cls in _CUSTOM.items():
        setattr(tasks, name, cls)
        setattr(modules, name, cls)
    exported = getattr(modules, "__all__", None)
    if isinstance(exported, (tuple, list)):
        additions = [name for name in _CUSTOM if name not in exported]
        modules.__all__ = type(exported)(list(exported) + additions)
    capabilities = {"installed": True, "already_installed": False,
                    "implementation_version": IMPLEMENTATION_VERSION,
                    "ultralytics_version": getattr(ultralytics, "__version__", "unknown"),
                    "custom_modules": list(_CUSTOM), "repeat_modules": list(_REPEATED),
                    "parser_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "native_modules": {name: hasattr(tasks, name) for name in
                                       ("C2f", "C3TR", "C3k2", "C2PSA", "A2C2f", "SPPF", "Detect")}}
    capabilities.update(checkpoint_capabilities)
    patched.__module__ = original.__module__
    patched.__doc__ = original.__doc__
    patched._nas_mixed_original = original
    patched._nas_mixed_installed = True
    patched._nas_mixed_capabilities = capabilities
    tasks.parse_model = patched
    return dict(capabilities)


def _scope_audit(roots):
    modules, parameters = {}, {}
    for root in roots:
        for module in root.modules():
            modules[id(module)] = module
        for parameter in root.parameters():
            parameters[id(parameter)] = parameter
    conv_types = (nn.Conv1d, nn.Conv2d, nn.Conv3d, nn.ConvTranspose1d, nn.ConvTranspose2d, nn.ConvTranspose3d)
    # A2C2f(a2=False) uses CNN blocks; inspect its actual AAttn/ABlock descendants
    # instead of treating the enclosing A2C2f class name as proof of attention.
    native_attention = {"PSA", "C2PSA", "C2fPSA", "PSABlock", "AAttn", "ABlock", "C3TR"}
    conv = sum(isinstance(module, conv_types) for module in modules.values())
    attention = sum(isinstance(module, nn.MultiheadAttention) or "attention" in type(module).__name__.lower()
                    or "transformer" in type(module).__name__.lower() or type(module).__name__ in native_attention
                    for module in modules.values())
    family = ("cnn_transformer" if conv and attention else "cnn_only" if conv else
              "transformer_only" if attention else "other_or_unknown")
    return {"conv_modules": conv, "attention_modules": attention,
            "params": sum(parameter.numel() for parameter in parameters.values()), "family": family}


def audit_model(model) -> Dict:
    """Observed structural taxonomy; a convolutional Detect head prevents full pure-T.

    Attention counts are module markers (including composite native blocks), not FLOPs
    or a count of unique executed attention operations. No search policy uses these tags.
    """
    core = model
    if not isinstance(getattr(core, "model", None), (nn.Sequential, nn.ModuleList)):
        inner = getattr(core, "model", None)
        if isinstance(inner, nn.Module):
            core = inner
    layers = getattr(core, "model", None)
    layers = list(layers) if isinstance(layers, (nn.Sequential, nn.ModuleList)) else []
    config = getattr(core, "yaml", {})
    backbone_lines = config.get("backbone", []) if isinstance(config, dict) else []
    backbone_count = len(backbone_lines) if isinstance(backbone_lines, list) else 0
    backbone = layers[:backbone_count] if 0 < backbone_count <= len(layers) else []
    terminal_detect = bool(layers and "detect" in type(layers[-1]).__name__.lower())
    features = layers[:-1] if terminal_detect else layers
    audits = {"backbone": _scope_audit(backbone), "features": _scope_audit(features),
              "full_model": _scope_audit([core])}
    full = audits["full_model"]
    return {"conv_modules": full["conv_modules"], "attention_modules": full["attention_modules"],
            "params": full["params"], "backbone_family": audits["backbone"]["family"],
            "feature_family": audits["features"]["family"], "full_model_family": full["family"],
            "terminal_detect": terminal_detect, "scopes": audits,
            "implementation_version": IMPLEMENTATION_VERSION}
