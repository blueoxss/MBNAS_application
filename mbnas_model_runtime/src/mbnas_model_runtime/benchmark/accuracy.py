"""Export accuracy delta: the common evaluator scores every runtime's predictions on the same split manifest.

Each runtime predicts through ``adapter.predict`` (same preprocessing, decoding/NMS and scaling code; batch 1), the
predictions go through :func:`mbnas_server.evaluation.evaluator.evaluate_predictions`, and deltas are reported
against the reference runtime (PyTorch FP32): AP@[.5:.95], AP50 and recall (fractions 0-1; delta = runtime -
reference). Absolute values describe the benchmarked weights on this split only.

The same predictions are also scored class-agnostically (``class_agnostic``; deltas under
``accuracy_class_agnostic_delta_vs_reference``), which is the informative comparison when the checkpoint's label
space differs from the split's.

AP@[.5:.95] for the delta is the evaluator's ``ap`` when it is a number; when it is ``None`` it is the mean of the
evaluator's own ``per_class_ap`` (pycocotools ``precision[:, :, k, area=all, maxDets=max_det]``), which equals COCO
AP@[.5:.95] at ``maxDets=max_det``; ``ap_for_delta_source`` records which one was used. (pycocotools ``summarize``
computes ``stats[0]`` with its default ``maxDets=100``, so ``ap`` is ``None`` whenever ``max_det != 100``.)
"""
from __future__ import annotations

from typing import Any

METRICS = {"ap": "ap_for_delta", "ap_50": "ap_50", "recall": "recall"}


def accuracy_operating_point() -> dict[str, Any]:
    """Postprocess operating point of the AP/recall evaluation (the common evaluator's defaults, batch 1)."""
    from mbnas_model_runtime.evaluation.evaluator import EvalConfig

    cfg = EvalConfig(input_shape=(1, 1), batch=1, precision="FP32", device_class="host-gpu")
    return {"conf": cfg.conf_floor, "max_det": cfg.max_det, "recall_iou": cfg.recall_iou,
            "recall_conf": cfg.recall_conf, "source": "common evaluator EvalConfig defaults (conf_floor, max_det); "
                                                      "NMS IoU (when the head uses NMS) from the adapter defaults"}


def class_agnostic(manifest: Any, preds: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    """Single-class copies of ``manifest`` and ``preds`` (every GT box and prediction mapped to class 0)."""
    samples = [s.model_copy(update={"boxes": [(0, cx, cy, w, h) for _, cx, cy, w, h in s.boxes]})
               for s in manifest.samples]
    agnostic = manifest.model_copy(update={"samples": samples, "names": {0: "object"}, "class_ids": [0]})
    return agnostic, [{**p, "classes": [0] * len(p["classes"])} for p in preds]


def _score(preds: list[dict[str, Any]], manifest: Any, cfg: Any) -> dict[str, Any]:
    from mbnas_model_runtime.evaluation.evaluator import evaluate_predictions, public

    res = public(evaluate_predictions(preds, manifest, cfg))
    res.pop("per_sample", None)
    per_class = [v for v in (res.get("per_class_ap") or {}).values() if isinstance(v, (int, float))]
    res["ap_per_class_mean"] = sum(per_class) / len(per_class) if per_class else None
    if isinstance(res.get("ap"), (int, float)):
        res["ap_for_delta"], res["ap_for_delta_source"] = res["ap"], "evaluator.ap"
    else:
        res["ap_for_delta"] = res["ap_per_class_mean"]
        res["ap_for_delta_source"] = "mean(evaluator.per_class_ap) at maxDets=max_det (evaluator.ap is None)"
    return res


def evaluate_runtime(adapter: Any, module: Any, manifest: Any, *, imgsz: int, precision: str,
                     device_class: str = "host-gpu", with_class_agnostic: bool = True) -> dict[str, Any]:
    """Predict all manifest samples with ``module`` (PyTorch or runtime view) and score with the common evaluator.

    The same predictions are also scored class-agnostically (``class_agnostic`` key: GT and predictions mapped to one
    class) — the informative delta when the checkpoint's label space differs from the split's (e.g. COCO-trained
    official weights on the single-class PILOT split).
    """
    import time

    from mbnas_model_runtime.evaluation.evaluator import EvalConfig

    cfg = EvalConfig(input_shape=(imgsz, imgsz), batch=1, precision=precision, device_class=device_class)
    t0 = time.perf_counter()
    preds = adapter.predict(module, [(s.sample_id, s.image) for s in manifest.samples], cfg.predict_cfg())
    seconds = time.perf_counter() - t0
    res = _score(preds, manifest, cfg)
    if with_class_agnostic:
        agn_manifest, agn_preds = class_agnostic(manifest, preds)
        agn = _score(agn_preds, agn_manifest, cfg)
        res["class_agnostic"] = {k: agn.get(k) for k in ("ap", "ap_50", "recall", "ap_for_delta", "ap_for_delta_source",
                                                       "counts")}
    res["predict_seconds"] = round(seconds, 3)
    res["detections"] = sum(len(p["scores"]) for p in preds)
    return res


def deltas(result: dict[str, Any], reference: dict[str, Any]) -> dict[str, float | None]:
    """``<metric>_delta = result - reference`` for AP/AP50/recall (None when either side is not a number)."""
    out: dict[str, float | None] = {}
    for name, key in METRICS.items():
        a, b = result.get(key), reference.get(key)
        out[f"{name}_delta"] = (float(a) - float(b)) if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            else None
    return out


def max_abs_diff(a: Any, b: Any) -> float | None:
    """Largest absolute element difference between two raw outputs (tensor, tuple head or name->tensor dict)."""
    import torch

    def first(x: Any) -> Any:
        if isinstance(x, dict):
            return [x[k] for k in sorted(x)]
        if isinstance(x, (list, tuple)):
            return [x[0]] if x and isinstance(x[0], torch.Tensor) else list(x)
        return [x]

    pairs = list(zip(first(a), first(b)))
    if not pairs or any(not isinstance(u, torch.Tensor) or u.shape != v.shape for u, v in pairs):
        return None
    return max(float((u.float() - v.float()).abs().max()) for u, v in pairs)
