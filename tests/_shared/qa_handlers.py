"""QA job handlers: the REAL proposers + synthetic, fully controllable training/evaluation metrics (no GPU).

Activated with ``MBNAS_JOB_HANDLERS_MODULE=qa_handlers`` (tests/owner/qa/conftest.py puts tests/_shared on sys.path).
Metrics are a deterministic function of (candidate NAME, rung, ``MBNAS_QA_SALT_<rung>``): the candidate name is stable
across runs (``GA-G01-C002``), the run-scoped candidate id is not, so two runs with the same protocol produce the
same metrics unless a salt differs. That lets tests change the *observed* metrics of one rung and check what a
proposer is (not) allowed to react to. Knob ``MBNAS_QA_BAD_<NAME-SUBSTRING>`` is not needed; invalid metrics are
injected directly into ``engine.commit_evaluation`` by the tests that need them.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

import stub_handlers

from mbnas_contracts.schemas.jobs import JobSpec
from mbnas_core.search.handlers import propose_generation as _real_propose
from mbnas_contracts.jobs_protocol import Emitter


def unit(name: str, rung: str) -> float:
    salt = os.environ.get(f"MBNAS_QA_SALT_{rung}", "")
    return int(hashlib.sha256(f"{name}|{rung}|{salt}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _retarget(spec: JobSpec, ev: dict[str, Any]) -> dict[str, Any]:
    name = str((spec.params.get("candidate") or {}).get("name") or spec.candidate_id)
    u = unit(name, spec.rung or "")
    ev.update(ap=round(0.05 + 0.4 * u, 6), recall=round(0.1 + 0.5 * u, 6), latency_ms=round(20 + 150 * (1 - u), 4),
              size_mb=round(5 + 50 * u, 4))
    return ev


def propose(spec: JobSpec, emit: Emitter) -> Any:
    return _real_propose(spec, emit)


# SYNTHETIC inline "latency profiling" seconds per evaluation (``MBNAS_STUB_PROFILE_S`` overrides): the real
# evaluator reports ``timings.profile_s`` and the worker must bill it as an unbilled ``profile`` row (S6-F1).
PROFILE_DEFAULT_S = 0.03


def train(spec: JobSpec, emit: Emitter) -> dict[str, Any]:
    out = stub_handlers.train(spec, emit, profile_default_s=PROFILE_DEFAULT_S)
    _retarget(spec, out["evaluation"])
    return out


def evaluate(spec: JobSpec, emit: Emitter) -> dict[str, Any]:
    out = stub_handlers.evaluate(spec, emit, profile_default_s=PROFILE_DEFAULT_S)
    _retarget(spec, out["evaluation"])
    return out


HANDLERS = {"PROPOSE_GENERATION": propose, "BASELINE_TRAIN": train, "CANDIDATE_TRAIN": train,
            "FINAL_RETRAIN": train, "EVALUATE": evaluate}
