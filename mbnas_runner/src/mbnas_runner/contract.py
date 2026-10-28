"""Handler registry of the runner: which module implements which job kind, and the test/smoke override.

The handler contract itself (``Handler`` signature, :class:`Emitter`, :class:`JobFailure`, :class:`JobPaused`, the
stop-request and pause constants, the outputs the orchestrator consumes) lives in
:mod:`mbnas_contracts.jobs_protocol`; handler authors import those names from there. They are re-exported here because
``mbnas_runner.contract`` is where the original ``worker.contract`` was imported from. Nothing in this module touches
the DB.

A runner runs exactly five job kinds: the three training kinds, ``EVALUATE`` and ``BENCHMARK``.
``PROPOSE_GENERATION`` is owner work (core host) and has no runner handler; it resolves only through the stub override.
"""
from __future__ import annotations

import importlib
import os

from mbnas_contracts.jobs_protocol import (
    HANDLERS_ENV, PAUSE_REASONS, PAUSED_REASON, STOP_ENV, Emitter, Handler, JobFailure, JobPaused,
)
from mbnas_contracts.schemas.jobs import JobKind

__all__ = ["DEFAULT_HANDLERS", "HANDLERS_ENV", "PAUSE_REASONS", "PAUSED_REASON", "STOP_ENV", "Emitter", "Handler",
           "JobFailure", "JobPaused", "handler_override", "resolve_handler"]

DEFAULT_HANDLERS: dict[str, str] = {
    JobKind.BASELINE_TRAIN.value: "mbnas_runner.training.handlers:run_training_job",
    JobKind.CANDIDATE_TRAIN.value: "mbnas_runner.training.handlers:run_training_job",
    JobKind.FINAL_RETRAIN.value: "mbnas_runner.training.handlers:run_training_job",
    JobKind.EVALUATE.value: "mbnas_runner.evaluation.handlers:run_evaluation_job",
    JobKind.BENCHMARK.value: "mbnas_runner.benchmark.handlers:run_benchmark_job",
}


def _load(ref: str) -> Handler:
    module, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module), attr)


def handler_override() -> str | None:
    """Module named by ``$MBNAS_JOB_HANDLERS_MODULE`` (test/smoke stubs), or None for the real handlers."""
    return os.environ.get(HANDLERS_ENV) or None


def resolve_handler(kind: str) -> Handler:
    """Handler for ``kind``: ``$MBNAS_JOB_HANDLERS_MODULE.HANDLERS[kind]`` when set (tests), else the default."""
    override = handler_override()
    if override:
        handlers = getattr(importlib.import_module(override), "HANDLERS")
        if kind not in handlers:
            raise JobFailure("HANDLER_MISSING", f"{override}.HANDLERS has no handler for {kind}")
        return handlers[kind]
    ref = DEFAULT_HANDLERS.get(kind)
    if ref is None:
        raise JobFailure("HANDLER_MISSING", f"no handler registered for job kind {kind}")
    try:
        return _load(ref)
    except (ImportError, AttributeError) as exc:
        raise JobFailure("HANDLER_UNAVAILABLE", f"cannot import {ref}: {exc}") from exc
