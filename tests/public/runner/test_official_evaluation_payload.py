"""DD-A49 (a), official evaluation (``EVALUATE`` of an ``evaluate_only`` training run): the payload carries the same
device evidence as a training evaluation (``latency`` with its device ``manifest``, ``device_uuid``/``device_name``,
``latency_statistics``, post-processing provenance) in slots the 1.5.0 contract already declares, and every result
names the job's attempt (the core refuses a result whose attempt differs from the fenced one).

The GPU-bound steps are SYNTHETIC fakes (official weights loading, the evaluator, the panels, the device identity and
the latency record); the run directory check, the latency manifest and the evaluation.json writer are the real code.
CPU only: nothing here touches CUDA or NVML.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mbnas_contracts.schemas.jobs import JobKind, JobSpec

IDENT = {"torch_device": "cuda:0", "uuid": "GPU-00000000-1111-2222-3333-444444444444", "name": "SYNTHETIC GPU",
         "manifest": {"torch": "2.6.0", "cuda_runtime": "12.4", "cudnn": 90100, "torch_device_name": "SYNTHETIC GPU",
                      "cuda_index": 0, "uuid": "GPU-00000000-1111-2222-3333-444444444444", "name": "SYNTHETIC GPU"}}
LABEL = "forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)"


def _latency() -> dict:
    return {"scopes": {"forward_and_postprocess": {"p50_ms": 9.9, "p95_ms": 10.5, "mean_ms": 10.0,
                                                   "samples_ms": [9.8, 10.0]}},
            "operating_point": {"conf": 0.25, "iou": 0.7, "max_det": 300}, "latency_scope": LABEL,
            "postprocess_runtime": {"nms": "torchvision_cuda_nms"}, "torchvision": "0.21.0",
            "postprocess": {"nms": True}, "device_sampling": {"power_w": None}, "latency_source": "measured"}


@pytest.fixture
def official(tmp_path, tmp_roots, make_dataset, settings, monkeypatch):
    """``run(attempt, model_names)`` -> (JobResult, run_dir, emitted messages) with the GPU-bound steps faked."""
    from mbnas_model_runtime.benchmark import subjects
    from mbnas_model_runtime.datasets.manifest import build_manifest
    from mbnas_model_runtime.evaluation import evaluator, images
    from mbnas_runner.evaluation import handlers
    from mbnas_runner.evaluation.official_evaluation import run_official_evaluation
    from mbnas_runner.training import context

    paths = make_dataset("official", counts={"val": 2})
    manifest = build_manifest(paths["search_val_root"], names={0: "drone"})
    weights = tmp_path / "weights" / "yolo11n.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"SYNTHETIC official weights")
    proto = {"console": {"extra": {"training": {"enabled": True, "mode": "evaluate_only",
                                                "arch": {"weights_path": str(weights), "family": "YOLO11"},
                                                "architecture_hash": "sha256:" + "a" * 64}},
                         "benchmark": {"precision": "FP16", "device": "host-gpu-0", "batch": 1}},
             "training": {"requested_input_size": [64, 64], "batch_per_gpu": 2}}
    run_dir = tmp_roots["runs"] / "runs" / "run-official"
    monkeypatch.setattr(context, "split_manifests", lambda p, s, splits, access=None: ({0: "drone"},
                                                                                       {"search_val": manifest}))
    monkeypatch.setattr(evaluator, "evaluate", lambda adapter, model, man, cfg: {
        "ap": 0.5, "ap_50": 0.7, "recall": 0.6, "evaluation_hash": "sha256:" + "e" * 64,
        "split_hash": man.split_hash, "signature": {"device_class": cfg.device_class}, "matches": ["dropped"]})
    monkeypatch.setattr(images, "render_fixed_samples", lambda *a, **k: [])
    monkeypatch.setattr(handlers, "device_identity", lambda device: json.loads(json.dumps(IDENT)))
    monkeypatch.setattr(handlers, "measure_latency", lambda *a, **k: _latency())

    def run(attempt: int = 1, names: dict | None = None):
        monkeypatch.setattr(subjects, "ultralytics_from_weights", lambda *a, **k: SimpleNamespace(
            model=SimpleNamespace(names=names or {0: "drone"}), adapter="SYNTHETIC adapter"))
        spec = JobSpec(job_id="ev-1", run_id="run-official", kind=JobKind.EVALUATE, protocol_hash="sha256:p",
                       candidate_id="cand-1", rung="TRAIN", attempt=attempt,
                       params={"resolved_protocol": proto, "run_dir": str(run_dir)})
        sent: list = []
        return run_official_evaluation(spec, sent.append, settings), run_dir, sent

    return run


def test_official_payload_carries_the_latency_device_manifest_and_device_identity(official):
    res, run_dir, sent = official(attempt=2)
    assert res.status == "SUCCEEDED" and res.attempt == 2, res.message
    ev = res.outputs["evaluation"]
    lat = _latency()
    assert ev["latency"]["manifest"] == {
        **IDENT["manifest"], "precision": "FP16", "batch": 1, "input_shape": [64, 64],
        "precision_mode": "torch.autocast(float16) over FP32 weights", "postprocess": lat["postprocess"],
        "postprocess_operating_point": lat["operating_point"], "postprocess_runtime": lat["postprocess_runtime"],
        "torchvision": "0.21.0", "latency_scope": LABEL, "device_entry": "host-gpu-0",
        "device_sampling": lat["device_sampling"]}
    assert ev["device_uuid"] == IDENT["uuid"] and ev["device_name"] == "SYNTHETIC GPU" and ev["device"] == "host-gpu-0"
    assert ev["latency_ms"] == 9.9 and ev["latency_statistics"] == {"p50_ms": 9.9, "p95_ms": 10.5, "mean_ms": 10.0}
    assert ev["postprocess_runtime"] == lat["postprocess_runtime"]
    assert ev["postprocess_operating_point"] == lat["operating_point"] and ev["latency_scope"] == LABEL
    assert ev["latency"]["scopes"]["forward_and_postprocess"]["p50_ms"] == 9.9 and "matches" not in ev
    assert ev["epochs"] == 0 and ev["source"] == "official_weights" and ev["latency_source"] == "measured"
    on_disk = json.loads((Path(run_dir) / "evaluations" / "cand-1" / "evaluation.json").read_text())
    assert on_disk["latency"]["manifest"]["uuid"] == IDENT["uuid"] and on_disk["device_uuid"] == IDENT["uuid"]
    assert [m.type for m in sent] == ["metric"]


def test_the_added_keys_have_declared_contract_slots():
    from mbnas_contracts.openapi import component

    ce = component("CandidateEvaluation")
    raw = ce["properties"]["raw_metrics"]
    assert ce["additionalProperties"] is False and raw["additionalProperties"] is False  # closed objects
    assert {"latency_statistics", "postprocess_runtime", "latency_scope"} <= set(ce["properties"])  # row fields
    declared = set(raw["properties"])
    assert {"latency", "device_uuid", "device_name", "postprocess_operating_point", "device_used"} <= declared


def test_official_failures_name_the_fenced_attempt(official):
    res, _, _ = official(attempt=3, names={0: "cat"})
    assert res.status == "FAILED" and res.reason_code == "CLASS_MAPPING_MISMATCH" and res.attempt == 3


def test_latency_manifest_keeps_the_training_payload_layout():
    from mbnas_runner.evaluation.handlers import latency_manifest

    man = latency_manifest(IDENT, _latency(), precision="FP32", input_shape=(32, 48), device_entry=None)
    assert list(man)[len(IDENT["manifest"]):] == ["precision", "batch", "input_shape", "precision_mode", "postprocess",
                                                  "postprocess_operating_point", "postprocess_runtime", "torchvision",
                                                  "latency_scope", "device_entry", "device_sampling"]
    assert man["precision_mode"] == "fp32" and man["input_shape"] == [32, 48] and man["device_entry"] is None
