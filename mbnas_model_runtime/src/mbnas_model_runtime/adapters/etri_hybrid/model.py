"""ETRI controlled detection encoders (CNN stem / linear patch embedding / Transformer encoder) and audit.

Provenance: extracted VERBATIM (top-level definitions only, no edits to their bodies) from
``ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb`` (git blob sha1 629ce856b4c6ea337edc8943b9e65ed91ef60e27, ETRI_NAS HEAD
1d3a459aa6356e11b43c0e58bc778030ba78f950), cell indices [10]: _conv_block, CNNStem, LinearPatchEmbedding, CNNResidual, ControlledDetector, make_detector, audit_architecture. The notebook was read, never executed or modified.
Only the module imports below were added. Family label: ETRI controlled CNN+Transformer hybrid (not SOTA,
not pure Transformer).
"""
# ruff: noqa
import math

import torch
from torch import nn


def _conv_block(in_channels, out_channels, stride=1):
    return nn.Sequential(nn.Conv2d(in_channels, out_channels, 3, stride, 1, bias=False),
                         nn.GroupNorm(math.gcd(8, out_channels), out_channels), nn.GELU())


class CNNStem(nn.Module):
    def __init__(self, patch, dim):
        super().__init__()
        stages, blocks, channels = int(math.log2(patch)), [], 3
        for i in range(stages):
            out = min(dim, max(16, dim // (2 ** (stages - i - 1))))
            blocks.extend([_conv_block(channels, out, 2), _conv_block(out, out)])
            channels = out
        self.layers = nn.Sequential(*blocks)

    def forward(self, images):
        return self.layers(images).flatten(2).transpose(1, 2)


class LinearPatchEmbedding(nn.Module):
    def __init__(self, patch, dim):
        super().__init__()
        self.patch = patch
        self.projection = nn.Linear(3 * patch * patch, dim)

    def forward(self, images):
        batch, channels, height, width = images.shape
        p = self.patch
        patches = images.reshape(batch, channels, height // p, p, width // p, p)
        patches = patches.permute(0, 2, 4, 1, 3, 5).reshape(batch, -1, channels * p * p)
        return self.projection(patches)


class CNNResidual(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.layers = nn.Sequential(_conv_block(dim, dim), _conv_block(dim, dim))

    def forward(self, features):
        return features + self.layers(features)


class ControlledDetector(nn.Module):
    def __init__(self, family, imgsz, patch, dim, depth, heads, num_classes):
        super().__init__()
        if (family not in {"cnn_only", "cnn_transformer", "transformer_only"}
                or imgsz < 1 or dim < 4 or heads < 1
                or patch < 2 or patch & (patch - 1) or imgsz % patch
                or dim % 4 or dim % heads or depth < 1 or num_classes < 1):
            raise ValueError("Invalid architecture; patch must be a power of 2 and divide imgsz; "
                             "dim must be divisible by heads and be a multiple of 4")
        self.family, self.imgsz, self.grid = family, imgsz, imgsz // patch
        self.token_count = self.grid ** 2
        self.embedding = (LinearPatchEmbedding(patch, dim) if family == "transformer_only"
                          else CNNStem(patch, dim))
        if family == "cnn_only":
            self.spatial_encoder = nn.Sequential(*(CNNResidual(dim) for _ in range(depth)))
        else:
            layer = nn.TransformerEncoderLayer(dim, heads, dim * 4, dropout=0.1,
                                               activation="gelu", batch_first=True, norm_first=True)
            self.spatial_encoder = nn.TransformerEncoder(layer, depth, enable_nested_tensor=False)
        y, x = torch.meshgrid(torch.arange(self.grid), torch.arange(self.grid), indexing="ij")
        frequency = 1 / (10000 ** (torch.arange(dim // 4).float() / max(1, dim // 4 - 1)))
        x, y = x.reshape(-1, 1) * frequency, y.reshape(-1, 1) * frequency
        self.register_buffer("position", torch.cat([x.sin(), x.cos(), y.sin(), y.cos()], dim=1)[None])
        self.norm = nn.LayerNorm(dim)
        self.class_head = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, num_classes + 1))
        self.box_head = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, 4))
        with torch.no_grad():
            self.box_head[-1].bias.zero_()
            self.box_head[-1].bias[2:].fill_(math.log(0.05 / 0.95))

    def forward(self, images):
        if images.ndim != 4 or images.shape[1:] != (3, self.imgsz, self.imgsz):
            raise ValueError(f"Expected BCHW images of size {self.imgsz}")
        tokens = self.embedding(images) + self.position
        if self.family == "cnn_only":
            features = tokens.transpose(1, 2).reshape(images.shape[0], -1, self.grid, self.grid)
            tokens = self.spatial_encoder(features).flatten(2).transpose(1, 2)
        else:
            tokens = self.spatial_encoder(tokens)
        tokens = self.norm(tokens)
        return {"pred_logits": self.class_head(tokens), "pred_boxes": self.box_head(tokens).sigmoid()}


def make_detector(family, imgsz=384, patch=16, dim=128, depth=3, heads=4, num_classes=1):
    return ControlledDetector(family, imgsz, patch, dim, depth, heads, num_classes)


def audit_architecture(model, family):
    conv = sum(isinstance(m, (nn.Conv1d, nn.Conv2d, nn.Conv3d)) for m in model.modules())
    attention = sum(isinstance(m, nn.MultiheadAttention) for m in model.modules())
    valid = {"cnn_only": conv > 0 and attention == 0,
             "cnn_transformer": conv > 0 and attention > 0,
             "transformer_only": conv == 0 and attention > 0}
    if family not in valid or not valid[family]:
        raise AssertionError(f"Family taxonomy mismatch: {family}, conv={conv}, attention={attention}")
    return {"family": family, "params": sum(p.numel() for p in model.parameters()),
            "conv_modules": conv, "attention_modules": attention, "token_count": model.token_count}
