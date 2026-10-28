"""SYNTHETIC job handlers for the T3 runner-protocol tests: ``stub_handlers`` plus what a real training handler leaves on
the runner host that the stub does not (activate with ``MBNAS_JOB_HANDLERS_MODULE=remote_handlers``, this directory on
PYTHONPATH).

* training kinds also write the executed ``candidates/<cid>/architecture.yaml`` (the ultralytics yolo11 YAML with
  ``nc: 1``), like ``mbnas_runner.training`` does, so the runner daemon reports ``outputs.execution_configuration``
  (design A3 section 9.3, R4);
* ``MBNAS_STUB_PER_SAMPLE_BYTES=<n>``: the evaluation carries a ``per_sample`` list of about ``n`` bytes of JSON (the
  real evaluator's per-image records, evaluation/handlers.py), to exercise the result body limit (D6, T3-33).

Numbers stay SYNTHETIC (``raw_metrics.synthetic=true``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import stub_handlers as base
from mbnas_contracts.jobs_protocol import Emitter
from mbnas_contracts.schemas.jobs import JobSpec


def _yolo11_nc1() -> str:
    import ultralytics

    text = (Path(ultralytics.__file__).parent / "cfg" / "models" / "11" / "yolo11.yaml").read_text(encoding="utf-8")
    return text.replace("nc: 80", "nc: 1")


def _per_sample(n: int) -> list[dict[str, Any]]:
    row = {"sample_id": "sample-000000", "tp": 1, "fp": 0, "fn": 0, "gt": 1, "scores": [0.5] * 8}
    size = len(str(row)) + 8
    return [{**row, "sample_id": f"sample-{i:06d}"} for i in range(max(1, n // size))]


def train(spec: JobSpec, emit: Emitter, profile_default_s: float | None = None) -> dict[str, Any]:
    out = base.train(spec, emit, profile_default_s)
    cid = spec.candidate_id or ""
    if cid:
        path = Path(spec.params["run_dir"]) / "candidates" / cid / "architecture.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_yolo11_nc1(), encoding="utf-8")
    n = int(os.environ.get("MBNAS_STUB_PER_SAMPLE_BYTES") or 0)
    if n and isinstance(out.get("evaluation"), dict):
        out["evaluation"]["per_sample"] = _per_sample(n)
    return out


HANDLERS = {**base.HANDLERS, "BASELINE_TRAIN": train, "CANDIDATE_TRAIN": train, "FINAL_RETRAIN": train}
