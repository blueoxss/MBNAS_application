"""SYNTHETIC job handlers of the configuration-revision tests (``MBNAS_JOB_HANDLERS_MODULE=revision_handlers``).

The stub handlers of ``tests/_shared/stub_handlers.py`` (no model, no GPU), except that training also writes the
configuration it "ran" to ``<run_dir>/candidates/<candidate_id>/architecture.yaml`` -- the file
``mbnas_core.orchestration.model_artifacts.record_model_configuration`` registers as the ``executed`` revision. The text
is ``$MBNAS_TEST_REVISION_EXECUTED_YAML`` (set by the test; a parseable model YAML that differs from the selected one).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import stub_handlers

EXECUTED_ENV = "MBNAS_TEST_REVISION_EXECUTED_YAML"


def train(spec: Any, emit: Any, profile_default_s: float | None = None) -> dict[str, Any]:
    out = stub_handlers.train(spec, emit, profile_default_s)
    text = os.environ.get(EXECUTED_ENV)
    if text and spec.candidate_id:
        target = Path(spec.params["run_dir"]) / "candidates" / spec.candidate_id / "architecture.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return out


HANDLERS = {**stub_handlers.HANDLERS, "BASELINE_TRAIN": train, "CANDIDATE_TRAIN": train, "FINAL_RETRAIN": train}
