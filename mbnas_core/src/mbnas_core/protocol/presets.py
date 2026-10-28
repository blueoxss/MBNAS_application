"""Protocol presets.

* ``default_protocol()`` — the handoff design example (``MBNAS_Protocol_20261006.json``, bundled verbatim as
  ``handoff_protocol_20261006.json``) with only ``console.paths`` replaced by the preset example paths of
  :func:`preset_paths` (placeholders ``/ABSOLUTE/PATH/TO/...`` unless the core settings name ``preset_data_root`` /
  ``preset_output_root``; the bundled file itself is never changed). Its status ``DESIGN_EXAMPLE_NOT_EXECUTABLE`` and
  value provenance are preserved; its 10/30/90 rungs,
  weights, thresholds and budgets are illustrative, not research or reproduction values. The benchmark device
  stays the example "Orin NX", which this host does not have, so validating it unchanged reports DEVICE_UNAVAILABLE.
* ``pilot_smoke_protocol()`` — the ``PILOT_SMOKE`` preset: tiny real-data subset, 2→4→6 cumulative epochs,
  permissive smoke gates, one search seed, 0.75 GPU-h cap, separate run namespace, scratch initialization for
  every representative model (one ``common_scratch`` comparison group), GA
  ``console.search.crossover_rate`` 1.0, final retraining 2/2 epochs and ``console.retention.full_state =
  last_rung_end_only`` (a terminal candidate keeps only the full state of its last rung end; eval snapshots,
  weights-only files and sidecars are always kept). **Smoke values only**: they exercise the
  train/eval/promotion/resume/benchmark path end to end and are never research results.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from mbnas_contracts.constants import BASELINE_FAMILIES, DEFAULT_BASELINE14  # noqa: F401 - re-exported

PLACEHOLDER_DATA_ROOT = "/ABSOLUTE/PATH/TO/MBNAS_data"
PLACEHOLDER_OUTPUT_ROOT = "/ABSOLUTE/PATH/TO/MBNAS_runs"
PILOT_SUBSET_DIR = "pilot_visdrone_v1"
PILOT_SMOKE_ID = "PILOT_SMOKE"

HANDOFF_RESOURCE = "handoff_protocol_20261006.json"


def preset_paths(settings: Any = None) -> dict[str, Any]:
    """Example ``console.paths`` values of the presets.

    ``settings.preset_data_root`` is the directory holding the PILOT subset directory ``pilot_visdrone_v1`` and
    ``settings.preset_output_root`` the directory under which the preset output roots ``default`` / ``pilot_smoke``
    live; an unset (or missing) value gives the placeholders ``/ABSOLUTE/PATH/TO/MBNAS_data`` /
    ``/ABSOLUTE/PATH/TO/MBNAS_runs``. Returns ``{pilot_data_root, pilot_paths: {dataset_yaml, train_root,
    search_val_root, final_eval_root}, default_output_root, pilot_output_root}``.
    """
    data = str(getattr(settings, "preset_data_root", None) or PLACEHOLDER_DATA_ROOT).rstrip("/")
    out = str(getattr(settings, "preset_output_root", None) or PLACEHOLDER_OUTPUT_ROOT).rstrip("/")
    pilot = f"{data}/{PILOT_SUBSET_DIR}"
    return {"pilot_data_root": pilot,
            "pilot_paths": {"dataset_yaml": f"{pilot}/data.yaml", "train_root": f"{pilot}/images/train",
                            "search_val_root": f"{pilot}/images/search_val",
                            "final_eval_root": f"{pilot}/images/final_eval"},
            "default_output_root": f"{out}/default", "pilot_output_root": f"{out}/pilot_smoke"}


@lru_cache(maxsize=1)
def _handoff() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath(HANDOFF_RESOURCE).read_text(encoding="utf-8"))


def handoff_protocol() -> dict[str, Any]:
    """Unmodified handoff example (fresh deep copy)."""
    return copy.deepcopy(_handoff())


def default_protocol(settings: Any = None) -> dict[str, Any]:
    """Handoff design example with console.paths set to the preset example paths (:func:`preset_paths`)."""
    proto = handoff_protocol()
    paths = preset_paths(settings)
    proto["console"]["paths"] = {**paths["pilot_paths"], "output_root": paths["default_output_root"]}
    return proto


_SMOKE_WEIGHTS = [  # example L0/L1/L2 weights of the handoff (unchanged)
    {"ap": 0.2, "recall": 0.1, "latency_reward": 0.4, "size_reward": 0.3},
    {"ap": 0.45, "recall": 0.15, "latency_reward": 0.25, "size_reward": 0.15},
    {"ap": 0.6, "recall": 0.1, "latency_reward": 0.2, "size_reward": 0.1},
]
PILOT_BASELINES: tuple[dict[str, Any], ...] = (
    {"id": "yolo11n", "name": "YOLO11N", "family": "YOLO11", "enabled": True, "source": "Ultralytics"},
    {"id": "yolo26n", "name": "YOLO26N", "family": "YOLO26", "enabled": True, "source": "Ultralytics"},
    {"id": "rt-detr-l", "name": "RT-DETR-L", "family": "Hybrid", "enabled": True, "source": "CNN + Transformer"},
    {"id": "rf-detr-nano", "name": "RF-DETR Nano", "family": "Transformer", "enabled": True,
     "source": "Roboflow rfdetr"},
    {"id": "etri-controlled-cnn-transformer", "name": "ETRI CNN–Transformer", "family": "Hybrid", "enabled": True,
     "source": "ETRI controlled study (not SOTA)"},
)


def pilot_smoke_protocol(paths: dict[str, str] | None = None, *, settings: Any = None) -> dict[str, Any]:
    """PILOT_SMOKE preset (smoke values, not research values); ``paths`` overrides console.paths entries (the
    defaults are the preset example paths of :func:`preset_paths`)."""
    example = preset_paths(settings)
    proto = handoff_protocol()
    proto["status"] = "PRESET_PILOT_SMOKE"
    proto["value_provenance"] = "pilot_smoke_values_for_pipeline_verification_not_research_values"
    proto["protocol_id"] = "mbnas-pilot-smoke"
    training = proto["training"]
    training.update({
        "scheduler_horizon_epochs": 6, "warmup_epochs": 0, "batch_per_gpu": 8, "gradient_accumulation": 1,
        "workers": 0, "requested_input_size": [640, 640], "precision": "amp_fp16_fp32",
    })
    proto["rungs"] = [
        {"id": rid, "epoch_target_total": epochs, "fitness_weights": dict(w),
         "promotion": ({"to": nxt, "score_ge": 0.0, "ap_ge": 0.0} if nxt else None)}
        for rid, epochs, w, nxt in zip(("L0", "L1", "L2"), (2, 4, 6), _SMOKE_WEIGHTS, ("L1", "L2", None))
    ]
    proto["hard_constraints"] = {"latency_le_ms": 1000, "weights_size_le_MB": 500,
                                 "proxy_may_certify_final_deployment": False}
    proto["comparison"]["independent_search_seed_pairs"] = [101]
    proto["comparison"]["search_budget_GPU_hours_per_arm_per_seed"] = 0.75
    proto["final_retraining"].update({"epoch_target_total": 2, "scheduler_horizon_epochs": 2})
    proto["console"] = {
        "preset": PILOT_SMOKE_ID,
        "purpose": "SMOKE",
        "run_namespace": "pilot_smoke",
        "execution_mode": "PILOT",
        "label": "PILOT_SMOKE (smoke values, not research results)",
        "paths": {**example["pilot_paths"], "output_root": example["pilot_output_root"]},
        "baselines": [dict(b) for b in PILOT_BASELINES],
        "baseline_override_reason": (
            "PILOT_SMOKE uses one small representative per adapter family (yolo11n, yolo26n, rt-detr-l, "
            "rf-detr-nano, ETRI hybrid) instead of baseline14 to keep the smoke run minutes-scale; every "
            "representative uses scratch initialization (single common_scratch comparison group, RF-DETR included "
            "via pretrain_weights=None). The default run keeps baseline14."
        ),
        # crossover_rate 1.0 is a smoke value: every non-elite GA child with a same-family partner is a two-parent
        # crossover, so one smoke run always exercises the crossover (2 parents), elite clone (1) and Random (0)
        # lineage paths; the default protocol resolves the documented GA default 0.5
        "search": {"arms": ["GA", "IndependentRandom"], "population": 4, "generations": 2, "mutation_rate": 0.35,
                   "seeds": [101], "budget_gpu_hours": 0.75, "crossover_rate": 1.0},
        "benchmark": {"device": "host-gpu-0", "precision": "FP16", "batch": 1, "warmup": 20, "repeats": 100},
        "baseline_epoch_target": 6,
        "baseline_capture_rungs": True,
        "baseline_budget_gpu_hours": 0.25,
        # disk housekeeping for the smoke (a PILOT_SMOKE run writes ~8 GB): see orchestration.retention
        "retention": {"full_state": "last_rung_end_only"},
    }
    if paths:
        proto["console"]["paths"].update(paths)
    return proto


def preset_summaries() -> list[dict[str, Any]]:
    """PresetSummary rows (contract component) for the presets this module provides."""
    return [
        {"id": "DEFAULT_BASELINE14", "name": "Default baseline14 design example", "purpose": "RESEARCH_TEMPLATE",
         "execution_mode": "PILOT", "namespace": "default",
         "description": "Handoff 10/30/90 design example (illustrative values, status DESIGN_EXAMPLE_NOT_EXECUTABLE "
                        "kept); paths are example server paths; benchmark device must be set to an available one."},
        {"id": PILOT_SMOKE_ID, "name": "Pilot smoke", "purpose": "SMOKE", "execution_mode": "PILOT",
         "namespace": "pilot_smoke",
         "description": "Deterministic 32/16/16 VisDrone subset, 2→4→6 epochs, one small model per family, "
                        "all scratch-initialised (one comparison group), 0.75 GPU-h cap, GA crossover rate 1.0 so "
                        "the two-parent lineage path is always exercised, final retraining 2/2 epochs, full-state "
                        "retention last_rung_end_only. Smoke values, not research results."},
    ]


def presets(settings: Any = None) -> list[dict[str, Any]]:
    """Preset rows (PresetSummary + protocol) in the order of :func:`preset_summaries`; ``settings`` supplies the
    example path roots (:func:`preset_paths`)."""
    builders = {"DEFAULT_BASELINE14": lambda: default_protocol(settings),
                PILOT_SMOKE_ID: lambda: pilot_smoke_protocol(settings=settings)}
    return [{**s, "protocol": builders[s["id"]]()} for s in preset_summaries()]
