"""protocol.presets: baseline14 order, verbatim handoff example, PILOT_SMOKE values, example config files in sync."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mbnas_core.protocol import presets as P

# WP-1: the original handoff document is part of the owner source snapshot (build/source_snapshot, read-only);
# MBNAS_TEST_HANDOFF_FILE points elsewhere. Absent -> skip (as before on a host without the handoff directory).
_REPO = Path(__file__).resolve().parents[3]
HANDOFF = Path(os.environ.get("MBNAS_TEST_HANDOFF_FILE") or _REPO / (
    "build/source_snapshot/application/MBNAS_Server_Handoff_20261006/MBNAS_Protocol_20261006.json"))
CONFIG = _REPO / "config" / "examples"  # contract §4 / §10: the example configs live in config/examples


def test_baseline14_order_and_families() -> None:
    assert P.DEFAULT_BASELINE14 == (
        "yolo11s", "yolo11m", "yolo11l", "yolo11x", "yolo26s", "yolo26m", "yolo26l", "yolo26x",
        "rf-detr-small", "rf-detr-medium", "rf-detr-large", "rt-detr-l", "rt-detr-x", "etri-controlled-cnn-transformer")
    assert {P.BASELINE_FAMILIES[b] for b in P.DEFAULT_BASELINE14} == {"YOLO11", "YOLO26", "Transformer", "Hybrid"}
    assert P.BASELINE_FAMILIES["rt-detr-x"] == "Hybrid" and P.BASELINE_FAMILIES["rf-detr-large"] == "Transformer"


def test_bundled_handoff_is_byte_identical() -> None:
    try:
        present = HANDOFF.is_file()
    except OSError:  # PermissionError when the original host's /root is unreadable
        present = False
    if not present:
        pytest.skip("handoff directory not present")
    bundled = Path(P.__file__).with_name("handoff_protocol_20261006.json")
    assert hashlib.sha256(bundled.read_bytes()).hexdigest() == hashlib.sha256(HANDOFF.read_bytes()).hexdigest()


def test_default_protocol_only_changes_paths() -> None:
    original, proto = P.handoff_protocol(), P.default_protocol()
    assert proto["status"] == "DESIGN_EXAMPLE_NOT_EXECUTABLE"
    assert proto["value_provenance"] == original["value_provenance"]
    assert proto["console"]["paths"] == {  # DD-A19 / contract §8: placeholders unless settings name preset roots
        "dataset_yaml": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/data.yaml",
        "train_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/train",
        "search_val_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/search_val",
        "final_eval_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/final_eval",
        "output_root": "/ABSOLUTE/PATH/TO/MBNAS_runs/default"}
    proto["console"]["paths"] = original["console"]["paths"]
    assert proto == original
    assert [b["id"] for b in original["console"]["baselines"]] == list(P.DEFAULT_BASELINE14)
    assert [r["epoch_target_total"] for r in original["rungs"]] == [10, 30, 90]


def test_default_protocol_returns_fresh_copies() -> None:
    a = P.default_protocol()
    a["rungs"][0]["epoch_target_total"] = 999
    assert P.default_protocol()["rungs"][0]["epoch_target_total"] == 10


def test_pilot_smoke_values() -> None:
    p = P.pilot_smoke_protocol()
    c = p["console"]
    assert (c["preset"], c["purpose"], c["run_namespace"], c["execution_mode"]) == ("PILOT_SMOKE", "SMOKE", "pilot_smoke", "PILOT")
    assert c["paths"] == {  # DD-A19 / contract §8: placeholders unless settings name preset roots
        "dataset_yaml": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/data.yaml",
        "train_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/train",
        "search_val_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/search_val",
        "final_eval_root": "/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/images/final_eval",
        "output_root": "/ABSOLUTE/PATH/TO/MBNAS_runs/pilot_smoke"}
    assert [b["id"] for b in c["baselines"]] == ["yolo11n", "yolo26n", "rt-detr-l", "rf-detr-nano",
                                                 "etri-controlled-cnn-transformer"]
    assert c["baseline_override_reason"]
    assert [(r["id"], r["epoch_target_total"]) for r in p["rungs"]] == [("L0", 2), ("L1", 4), ("L2", 6)]
    assert [r["fitness_weights"] for r in p["rungs"]] == [r["fitness_weights"] for r in P.handoff_protocol()["rungs"]]
    assert p["rungs"][0]["promotion"] == {"to": "L1", "score_ge": 0.0, "ap_ge": 0.0} and p["rungs"][2]["promotion"] is None
    assert p["hard_constraints"]["latency_le_ms"] == 1000 and p["hard_constraints"]["weights_size_le_MB"] == 500
    t = p["training"]
    assert (t["warmup_epochs"], t["batch_per_gpu"], t["gradient_accumulation"], t["workers"],
            t["requested_input_size"], t["scheduler_horizon_epochs"], t["precision"]) == (
        0, 8, 1, 0, [640, 640], 6, "amp_fp16_fp32")
    assert p["final_retraining"]["epoch_target_total"] == 2 and p["final_retraining"]["scheduler_horizon_epochs"] == 2
    assert c["search"] == {"arms": ["GA", "IndependentRandom"], "population": 4, "generations": 2,
                           "mutation_rate": 0.35, "seeds": [101], "budget_gpu_hours": 0.75, "crossover_rate": 1.0}
    assert "extra" not in c  # the legacy console.extra.ga.crossover_rate alias is no longer used by the preset
    assert c["retention"] == {"full_state": "last_rung_end_only"}
    assert c["benchmark"] == {"device": "host-gpu-0", "precision": "FP16", "batch": 1, "warmup": 20, "repeats": 100}
    assert p["comparison"]["independent_search_seed_pairs"] == [101]
    assert "not_research" in p["value_provenance"] and "smoke" in c["label"].lower()


def test_example_config_files_in_sync() -> None:
    assert json.loads((CONFIG / "protocol.example.json").read_text()) == P.default_protocol()
    assert json.loads((CONFIG / "protocol.pilot_smoke.example.json").read_text()) == P.pilot_smoke_protocol()
    reg = json.loads((CONFIG / "model_registry.example.json").read_text())
    ids = [e["id"] for e in reg["registry"]]
    assert ids[:14] == list(P.DEFAULT_BASELINE14) and {"yolo11n", "yolo26n", "rf-detr-nano"} <= set(ids)
    assert {e["status"] for e in reg["registry"]} == {"UNVERIFIED"}


def test_preset_summaries_shape() -> None:
    rows = P.presets()
    assert [r["namespace"] for r in rows] == ["default", "pilot_smoke"]
    assert rows[1]["protocol"] == P.pilot_smoke_protocol()
