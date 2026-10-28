"""ETRI set-prediction loss (Hungarian matching; CE + 5*L1 + 2*GIoU).

Provenance: extracted VERBATIM (top-level definitions only, no edits to their bodies) from
``ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb`` (git blob sha1 629ce856b4c6ea337edc8943b9e65ed91ef60e27, ETRI_NAS HEAD
1d3a459aa6356e11b43c0e58bc778030ba78f950), cell indices [12]: SetCriterion. The notebook was read, never executed or modified.
Only the module imports below were added. Family label: ETRI controlled CNN+Transformer hybrid (not SOTA,
not pure Transformer).
"""
# ruff: noqa
import torch
from scipy.optimize import linear_sum_assignment
from torch import nn

from mbnas_model_runtime.adapters.etri_hybrid.boxes import cxcywh_to_xyxy, generalized_box_iou


class SetCriterion(nn.Module):
    def __init__(self, num_classes, eos_coef=0.05):
        super().__init__()
        if num_classes < 1 or not 0 < eos_coef <= 1:
            raise ValueError("num_classes must be positive; eos_coef must be in (0, 1]")
        self.num_classes = num_classes
        weights = torch.ones(num_classes + 1)
        weights[-1] = eos_coef
        self.register_buffer("class_weights", weights)

    def forward(self, outputs, targets):
        logits, boxes = outputs["pred_logits"], outputs["pred_boxes"]
        if len(targets) != len(logits) or logits.shape[-1] != self.num_classes + 1:
            raise ValueError("Batch/class shape mismatch")
        if not torch.isfinite(logits).all() or not torch.isfinite(boxes).all():
            raise FloatingPointError("Nonfinite model outputs")
        assigned = torch.full(logits.shape[:2], self.num_classes, dtype=torch.long, device=logits.device)
        loss_l1, loss_giou = boxes.float().sum() * 0, boxes.float().sum() * 0
        total_matches = 0
        for batch_i, target in enumerate(targets):
            labels, truth = target["labels"].to(logits.device), target["boxes"].to(boxes.device)
            if len(labels) != len(truth) or (len(labels) and (labels.min() < 0 or labels.max() >= self.num_classes)):
                raise ValueError("Invalid target label/box counts or class IDs")
            if len(labels) > boxes.shape[1]:
                raise ValueError("Ground-truth count exceeds prediction slots; increase token count")
            if not len(labels):
                continue
            if not torch.isfinite(truth).all():
                raise ValueError("Nonfinite target boxes")
            with torch.no_grad():
                cost = (-logits[batch_i].float().softmax(-1)[:, labels]
                        + 5 * torch.cdist(boxes[batch_i].float(), truth.float(), p=1)
                        - 2 * generalized_box_iou(cxcywh_to_xyxy(boxes[batch_i].float()),
                                                 cxcywh_to_xyxy(truth.float())))
                rows, columns = linear_sum_assignment(cost.detach().cpu().numpy())
                rows = torch.as_tensor(rows, device=boxes.device, dtype=torch.long)
                columns = torch.as_tensor(columns, device=boxes.device, dtype=torch.long)
            assigned[batch_i, rows] = labels[columns]
            predicted, matched = boxes[batch_i, rows].float(), truth[columns].float()
            loss_l1 = loss_l1 + nn.functional.l1_loss(predicted, matched, reduction="sum")
            loss_giou = loss_giou + (1 - generalized_box_iou(cxcywh_to_xyxy(predicted),
                                                           cxcywh_to_xyxy(matched)).diag()).sum()
            total_matches += len(rows)
        loss_cls = nn.functional.cross_entropy(logits.transpose(1, 2).float(), assigned,
                                               weight=self.class_weights)
        loss_l1, loss_giou = loss_l1 / max(1, total_matches), loss_giou / max(1, total_matches)
        return {"loss": loss_cls + 5 * loss_l1 + 2 * loss_giou,
                "loss_cls": loss_cls, "loss_l1": loss_l1, "loss_giou": loss_giou}
