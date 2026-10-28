"""Worker job contract: what the API/scheduler enqueues (JobSpec), what a worker child reports while running
(ProgressMessage, one JSON object per stdout line) and what it returns at exit (JobResult).

Job kinds follow PLAN.md §2 ``scheduling/jobs.py``. Job status vocabulary matches the ``jobs.status`` DB column.
The child process writes only ``ProgressMessage`` lines to stdout; anything else belongs on stderr.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mbnas_contracts.schemas.enums import StrEnum


class JobKind(StrEnum):
    BASELINE_TRAIN = "BASELINE_TRAIN"
    CANDIDATE_TRAIN = "CANDIDATE_TRAIN"
    EVALUATE = "EVALUATE"
    BENCHMARK = "BENCHMARK"
    FINAL_RETRAIN = "FINAL_RETRAIN"
    PROPOSE_GENERATION = "PROPOSE_GENERATION"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"  # JobResult only (target device unavailable); never a ``jobs.status`` value


DB_JOB_STATUSES: tuple[str, ...] = ("QUEUED", "LEASED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
"""Values of the ``jobs.status`` column (a BLOCKED attempt is stored as FAILED with the BLOCKED result in error_json)."""
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
TRAINING_KINDS: frozenset[JobKind] = frozenset({JobKind.BASELINE_TRAIN, JobKind.CANDIDATE_TRAIN, JobKind.FINAL_RETRAIN})


class JobSpec(BaseModel):
    """Immutable description of one unit of GPU/CPU work, stored in ``jobs.spec_json``.

    Training kinds must name the candidate, rung, cumulative epoch target and fixed schedule horizon
    (``cumulative_target <= fixed_horizon``); every job carries the immutable ``protocol_hash`` it runs under.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    kind: JobKind
    protocol_hash: Annotated[str, Field(min_length=1)]
    candidate_id: str | None = None
    rung: str | None = None
    cumulative_target: Annotated[int, Field(ge=1)] | None = None
    fixed_horizon: Annotated[int, Field(ge=1)] | None = None
    arm: str | None = None
    search_seed: Annotated[int, Field(ge=0)] | None = None
    training_seed: Annotated[int, Field(ge=0)] | None = None
    attempt: Annotated[int, Field(ge=1)] = 1
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _training_fields(self) -> "JobSpec":
        if self.kind in TRAINING_KINDS:
            missing = [f for f in ("candidate_id", "rung", "cumulative_target", "fixed_horizon")
                       if getattr(self, f) is None]
            if missing:
                raise ValueError(f"{self.kind} job requires {missing}")
            if self.cumulative_target > self.fixed_horizon:  # type: ignore[operator]
                raise ValueError("cumulative_target must not exceed fixed_horizon")
        return self


class JobResult(BaseModel):
    """Final outcome of one job attempt, stored in ``jobs.result_json`` (or ``error_json`` when failed).

    ``BLOCKED`` (internal) means the job could not run on its target at all (e.g. ``TARGET_BLOCKED``: the target
    device is unavailable); it is never retried and never produces a proxy measurement.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: Annotated[str, Field(min_length=1)]
    status: Literal[JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.BLOCKED]
    attempt: Annotated[int, Field(ge=1)] = 1
    exit_code: int | None = None
    reason_code: str | None = None
    message: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    gpu_seconds: Annotated[float, Field(ge=0)] | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @model_validator(mode="after")
    def _failure_has_reason(self) -> "JobResult":
        if self.status != JobStatus.SUCCEEDED and not self.reason_code:
            raise ValueError("FAILED/CANCELLED/BLOCKED results require reason_code")
        return self


ProgressType = Literal["progress", "log", "metric", "checkpoint", "heartbeat", "result"]


class ProgressMessage(BaseModel):
    """One JSONL line from a worker child (``type=result`` carries the JobResult in ``payload``)."""

    model_config = ConfigDict(extra="forbid")

    type: ProgressType
    job_id: Annotated[str, Field(min_length=1)]
    ts: str
    epoch: Annotated[int, Field(ge=0)] | None = None
    epoch_target_total: Annotated[int, Field(ge=0)] | None = None
    global_step: Annotated[int, Field(ge=0)] | None = None
    samples_seen: Annotated[int, Field(ge=0)] | None = None
    learning_rate: float | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_line(self) -> str:
        """Serialise as one compact JSON line (no trailing newline, NaN rejected)."""
        return json.dumps(self.model_dump(mode="json", exclude_none=True), separators=(",", ":"), allow_nan=False)

    @classmethod
    def parse_line(cls, line: str) -> "ProgressMessage":
        """Parse one stdout line; raises ``ValueError`` for non-JSON or schema violations."""
        return cls.model_validate(json.loads(line))
