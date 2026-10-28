"""ETRI controlled detector box utilities (cxcywh -> xyxy, generalized IoU).

Provenance: extracted VERBATIM (top-level definitions only, no edits to their bodies) from
``ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb`` (git blob sha1 629ce856b4c6ea337edc8943b9e65ed91ef60e27, ETRI_NAS HEAD
1d3a459aa6356e11b43c0e58bc778030ba78f950), cell indices [8]: cxcywh_to_xyxy, generalized_box_iou. The notebook was read, never executed or modified.
Only the module imports below were added. Family label: ETRI controlled CNN+Transformer hybrid (not SOTA,
not pure Transformer).
"""
# ruff: noqa
import torch


def cxcywh_to_xyxy(boxes):
    return torch.cat([boxes[..., :2] - boxes[..., 2:] / 2,
                      boxes[..., :2] + boxes[..., 2:] / 2], dim=-1)


def generalized_box_iou(boxes1, boxes2):
    area1 = (boxes1[:, 2:] - boxes1[:, :2]).clamp(min=0).prod(dim=1)
    area2 = (boxes2[:, 2:] - boxes2[:, :2]).clamp(min=0).prod(dim=1)
    intersection = (torch.minimum(boxes1[:, None, 2:], boxes2[None, :, 2:])
                    - torch.maximum(boxes1[:, None, :2], boxes2[None, :, :2])).clamp(min=0).prod(dim=-1)
    union = area1[:, None] + area2[None, :] - intersection
    iou = intersection / union.clamp(min=1e-7)
    enclosing = (torch.maximum(boxes1[:, None, 2:], boxes2[None, :, 2:])
                 - torch.minimum(boxes1[:, None, :2], boxes2[None, :, :2])).clamp(min=0).prod(dim=-1)
    return iou - (enclosing - union) / enclosing.clamp(min=1e-7)
