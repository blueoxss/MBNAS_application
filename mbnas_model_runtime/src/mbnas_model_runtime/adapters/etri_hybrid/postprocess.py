"""ETRI common postprocess: foreground softmax, class-wise pure-torch NMS, box restoration.

Provenance: extracted VERBATIM (top-level definitions only, no edits to their bodies) from
``ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb`` (git blob sha1 629ce856b4c6ea337edc8943b9e65ed91ef60e27, ETRI_NAS HEAD
1d3a459aa6356e11b43c0e58bc778030ba78f950), cell indices [14]: _xyxy, _iou, _class_nms, postprocess, _scalar, _original_boxes. The notebook was read, never executed or modified.
Only the module imports below were added. Family label: ETRI controlled CNN+Transformer hybrid (not SOTA,
not pure Transformer).
"""
# ruff: noqa
import torch


def _xyxy(boxes):
    center, size = boxes[..., :2], boxes[..., 2:]
    return torch.cat((center - size / 2, center + size / 2), dim=-1)


def _iou(a, b):
    intersection = (torch.minimum(a[:, None, 2:], b[None, :, 2:]) -
                    torch.maximum(a[:, None, :2], b[None, :, :2])).clamp(min=0).prod(-1)
    area_a = (a[:, 2:] - a[:, :2]).clamp(min=0).prod(-1)
    area_b = (b[:, 2:] - b[:, :2]).clamp(min=0).prod(-1)
    return intersection / (area_a[:, None] + area_b[None, :] - intersection).clamp(min=1e-9)


def _class_nms(boxes, scores, labels, threshold):
    """CPU pure-Torch NMS, avoiding compiled torchvision operators."""
    keep = []
    for label in labels.unique():
        order = torch.where(labels == label)[0]
        order = order[scores[order].argsort(descending=True)]
        while order.numel():
            current = order[0]
            keep.append(current)
            remaining = order[1:]
            order = remaining[_iou(boxes[current].unsqueeze(0), boxes[remaining])[0] <= threshold]
    if not keep:
        return torch.empty(0, dtype=torch.long)
    keep = torch.stack(keep)
    return keep[scores[keep].argsort(descending=True)]


def postprocess(outputs, threshold=.001, nms_iou=.5, max_det=300):
    """Foreground softmax scores; return CPU normalized xyxy boxes."""
    logits = outputs['pred_logits'].detach().float().cpu()
    boxes = _xyxy(outputs['pred_boxes'].detach().float().cpu()).clamp(0, 1)
    scores, labels = logits.softmax(-1)[..., :-1].max(-1)
    results = []
    for box, score, label in zip(boxes, scores, labels):
        valid = (score >= threshold) & torch.isfinite(box).all(-1)
        valid &= torch.isfinite(score) & (box[:, 2:] > box[:, :2]).all(-1)
        box, score, label = box[valid], score[valid], label[valid]
        keep = _class_nms(box, score, label, nms_iou)[:max_det]
        results.append({'boxes': box[keep], 'scores': score[keep], 'labels': label[keep]})
    return results


def _scalar(value):
    return value.item() if hasattr(value, 'item') else value


def _original_boxes(boxes, target):
    h, w = [float(_scalar(v)) for v in target['orig_size']]
    ih, iw = [float(_scalar(v)) for v in target['input_size']]
    left, top = [float(_scalar(v)) for v in target['pad']]
    scale = float(_scalar(target['scale']))
    restored = (boxes.cpu().float() * torch.tensor([iw, ih, iw, ih]) -
                torch.tensor([left, top, left, top])) / scale
    restored[:, 0::2] = restored[:, 0::2].clamp(0, w)
    restored[:, 1::2] = restored[:, 1::2].clamp(0, h)
    return restored
