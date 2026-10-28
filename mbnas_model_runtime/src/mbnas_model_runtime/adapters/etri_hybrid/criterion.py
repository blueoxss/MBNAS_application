"""``SetCriterion-mbnas``: the notebook ``SetCriterion`` (cell 12, :mod:`.loss`) with one numerical-determinism change.

The notebook computes the classification term as ``cross_entropy(logits.transpose(1, 2), assigned, weight=...)`` on a
3-D input, which CUDA routes to ``nll_loss2d`` — a kernel without a deterministic implementation (weighted-mean
reduction via atomicAdd), so two identical runs drift apart (measured: max|dw| 3.8e-3 after 2 epochs). Here the same
weighted mean is computed on flattened ``(B*S, C)`` logits / ``(B*S,)`` targets, which CUDA routes to the
deterministic ``nll_loss`` kernel. ``sum_i w_i*l_i / sum_i w_i`` over the same elements is mathematically identical;
matching, L1 and GIoU terms are unchanged (the body below is the notebook's, only the ``loss_cls`` line differs).
"""
# ruff: noqa
import torch
from scipy.optimize import linear_sum_assignment
from torch import nn

from mbnas_model_runtime.adapters.etri_hybrid.boxes import cxcywh_to_xyxy, generalized_box_iou
from mbnas_model_runtime.adapters.etri_hybrid.loss import SetCriterion

CRITERION_NAME = "SetCriterion-mbnas"


class MbnasSetCriterion(SetCriterion):
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
        # MBNAS change: flattened 2-D cross entropy (deterministic CUDA nll_loss), same weighted mean.
        loss_cls = nn.functional.cross_entropy(logits.reshape(-1, logits.shape[-1]).float(), assigned.reshape(-1),
                                               weight=self.class_weights)
        loss_l1, loss_giou = loss_l1 / max(1, total_matches), loss_giou / max(1, total_matches)
        return {"loss": loss_cls + 5 * loss_l1 + 2 * loss_giou,
                "loss_cls": loss_cls, "loss_l1": loss_l1, "loss_giou": loss_giou}
