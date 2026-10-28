"""Constants shared by the hosts: the default baseline set and the default latency operating point.

* ``DEFAULT_BASELINE14`` - the 14 baseline model ids of the ``DEFAULT_BASELINE14`` preset, in their fixed order (the
  model registry declares its first 14 specs in exactly this order; the contract lists the same ids under
  ``x-required-default-baselines``). ``BASELINE_FAMILIES`` maps each of them to its comparison family.
* ``DEFAULT_POSTPROCESS`` - the latency operating point (conf / iou / max_det) used when the protocol or job gives no
  ``benchmark.postprocess`` (contract v1.2.0); the AP evaluation keeps its own floor. Read-only by convention: callers
  copy it (``{**DEFAULT_POSTPROCESS, ...}``) and never mutate it.
"""
from __future__ import annotations

from typing import Any

DEFAULT_BASELINE14: tuple[str, ...] = (
    "yolo11s", "yolo11m", "yolo11l", "yolo11x",
    "yolo26s", "yolo26m", "yolo26l", "yolo26x",
    "rf-detr-small", "rf-detr-medium", "rf-detr-large",
    "rt-detr-l", "rt-detr-x",
    "etri-controlled-cnn-transformer",
)
BASELINE_FAMILIES: dict[str, str] = {
    **{f"yolo11{s}": "YOLO11" for s in "smlx"},
    **{f"yolo26{s}": "YOLO26" for s in "smlx"},
    **{f"rf-detr-{s}": "Transformer" for s in ("small", "medium", "large")},
    "rt-detr-l": "Hybrid", "rt-detr-x": "Hybrid", "etri-controlled-cnn-transformer": "Hybrid",
}

#: Latency operating point used when the protocol/job gives no ``benchmark.postprocess`` (contract v1.2.0).
DEFAULT_POSTPROCESS: dict[str, Any] = {"conf": 0.25, "iou": 0.7, "max_det": 300}
