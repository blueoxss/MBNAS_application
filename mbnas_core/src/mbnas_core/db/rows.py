"""Storage-row models of the core database (owner side; WP-4 / contract 1.5.0 split).

Until contract 1.5.0 the repository validated stored rows with the public pydantic mirrors of
:mod:`mbnas_contracts.schemas.records`. Contract 1.5.0 closes those mirrors to the public DTO (owner-only members such as
Block ``old_name``/``before``/``after``/``repair_reason``, Generation ``group_key`` or Worker ``pid`` are not public), but
the stored rows keep every member: the public view is produced by :mod:`mbnas_core.console.projection` when a response
is built, never by rewriting a row. These are therefore the 1.4.x permissive (``extra="allow"``) shapes, copied
verbatim from ``mbnas_contracts.schemas.records`` as of contract 1.4.3 (field names, required sets, enums incl. block
``source_kind`` ``repair``, bounds and the ``if/then`` validators unchanged), used by :mod:`mbnas_core.db.repo` for
``insert_evaluation`` / ``upsert_generation`` / ``emit_event`` validation and normalisation. Stored row bytes are identical
to what the repository wrote before the split.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mbnas_contracts.schemas.enums import (
    Arm, ArtifactKind, ArtifactState, AuthMode, CandidateStatus, EventType, ExecutionMode, LatencySource, Precision,
    RunKind, RunStatus, SearchArm, StrEnum, WorkerStatus,
)
from mbnas_contracts.schemas.protocol import NonEmptyStr, Protocol


class BlockSourceKind(StrEnum):
    """Stored block provenance (1.4.x vocabulary incl. ``repair``; the public projection folds ``repair``)."""

    PARENT_A = "parent_a"
    PARENT_B = "parent_b"
    BASELINE = "baseline"
    RANDOM = "random"
    MUTATION = "mutation"
    REPAIR = "repair"
    NEW = "new"


Fraction = Annotated[float, Field(ge=0, le=1)]
Positive = Annotated[float, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]
NonNegFloat = Annotated[float, Field(ge=0)]


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class MatchingCounts(_Open):
    tp: int
    fp: int
    fn: int
    gt: int


class ArtifactMatching(_Open):
    """Deterministic one-to-one matching metadata of a gt/tp/fn/fp panel (same sample, same evaluation)."""

    sample_id: str | None = None
    split: str | None = None
    checkpoint_sha256: str | None = None
    counts: MatchingCounts | None = None
    sample_recall: float | None = None  # None (N/A) when gt == 0
    sample_precision: float | None = None
    rule: str | None = None


class Artifact(_Open):
    id: NonEmptyStr | None = None
    kind: ArtifactKind
    url: str
    auth_mode: AuthMode | None = None
    expires_at: str | None = None
    sample_id: str | None = None
    split_hash: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    state: ArtifactState | None = None
    refresh_url: str | None = None
    evaluation_id: str | None = None
    matching: ArtifactMatching | None = None
    metadata: dict[str, Any] | None = None


class Block(_Open):
    name: NonEmptyStr
    source: str | None
    mutated: bool
    old_name: str | None
    position: NonNegInt | None = None
    source_kind: BlockSourceKind | None = None
    parent_block_id: str | None = None
    operation: str | None = None
    params: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    repair_reason: str | None = None


class LatencyStatistics(_Open):
    p50_ms: float | None = None
    p95_ms: float | None = None
    mean_ms: float | None = None


class EvaluationProgress(_Open):
    epoch: NonNegInt | None = None
    epoch_target_total: NonNegInt | None = None
    learning_rate: float | None = None
    global_step: NonNegInt | None = None
    samples_seen: NonNegInt | None = None


class Rewards(_Open):
    latency_reward: float | None = None
    size_reward: float | None = None


class CandidateEvaluation(_Open):
    """One immutable measurement row (``id`` = evaluation_id); ``candidate_id`` is stable across promotions."""

    id: NonEmptyStr
    candidate_id: NonEmptyStr
    name: NonEmptyStr
    family: NonEmptyStr
    arm: Arm
    generation: NonNegInt
    rung: NonEmptyStr
    epochs: NonNegInt
    status: CandidateStatus
    ap: Fraction | None
    recall: Fraction | None
    latency_ms: Positive | None
    size_mb: Positive | None
    fitness: Fraction | None
    device: NonEmptyStr
    precision: Precision
    latency_source: LatencySource
    parents: Annotated[list[NonEmptyStr], Field(max_length=2)]
    blocks: list[Block]
    architecture_path: str | None
    checkpoint_path: str | None
    artifacts: list[Artifact]
    objective_hash: NonEmptyStr
    evaluation_hash: NonEmptyStr
    split_hash: NonEmptyStr | None
    latency_scope: NonEmptyStr
    comparison_group: NonEmptyStr
    search_seed: NonNegInt | None = None
    training_seed: NonNegInt | None = None
    batch: Annotated[int, Field(ge=1)] | None = None
    requested_input_shape: list[Annotated[int, Field(ge=1)]] | None = None
    resolved_input_shape: list[Annotated[int, Field(ge=1)]] | None = None
    epoch_target_total: Annotated[int, Field(ge=1)] | None = None  # >= 1 or absent (DD-35b): evaluate_only rows carry epochs 0 and none
    protocol_hash: NonEmptyStr | None = None
    checkpoint_sha256: str | None = None
    architecture_hash: NonEmptyStr | None = None
    metric_definitions: dict[str, Any] | None = None
    latency_statistics: LatencyStatistics | None = None
    gate: dict[str, Any] | None = None
    cost: dict[str, Any] | None = None
    reason_code: str | None = None
    supersedes_evaluation_id: str | None = None
    candidate_status: CandidateStatus | None = None
    progress: EvaluationProgress | None = None
    attempt: Annotated[int, Field(ge=1)] | None = None
    job_id: str | None = None
    revision: Annotated[int, Field(ge=1)] | None = None
    architecture_id: str | None = None
    namespace: str | None = None
    execution_mode: ExecutionMode | None = None
    source_kind_legend: dict[str, Any] | None = None
    raw_metrics: dict[str, Any] | None = None
    rewards: Rewards | None = None
    fitness_weights: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _evaluated_rules(self) -> "CandidateEvaluation":
        if len(set(self.parents)) != len(self.parents):
            raise ValueError("parents must be unique")
        if self.status != CandidateStatus.EVALUATED:
            return self
        missing = [f for f in ("ap", "recall", "latency_ms", "size_mb", "fitness", "checkpoint_path",
                               "protocol_hash", "batch", "resolved_input_shape", "split_hash") if getattr(self, f) in (None, "")]
        if self.arm in (Arm.GA, Arm.INDEPENDENT_RANDOM) and self.search_seed is None:
            missing.append("search_seed")
        if missing:
            raise ValueError(f"EVALUATED row requires non-null {missing}")
        if self.latency_source == LatencySource.PENDING:
            raise ValueError("EVALUATED row cannot have latency_source=pending")
        return self


class Generation(_Open):
    arm: SearchArm
    generation: NonNegInt
    rung: NonEmptyStr
    complete: bool
    representative_candidate_id: NonEmptyStr | None
    representative_evaluation_id: NonEmptyStr | None = None
    comparison_group: NonEmptyStr
    search_seed: NonNegInt | None = None
    planning_closed: bool | None = None
    planned_count: NonNegInt | None = None
    evaluated_count: NonNegInt | None = None
    failed_count: NonNegInt | None = None
    not_admitted_count: NonNegInt | None = None
    pending_count: NonNegInt | None = None
    representative_selection_rule: str | None = None
    group_key: str | None = None
    representative_rule: str | None = None

    @model_validator(mode="after")
    def _completion_rules(self) -> "Generation":
        counts = (self.evaluated_count, self.failed_count, self.not_admitted_count, self.pending_count)
        if self.planned_count is not None and all(c is not None for c in counts):
            if sum(counts) != self.planned_count:  # type: ignore[arg-type]
                raise ValueError("evaluated+failed+not_admitted+pending must equal planned_count")
        if self.complete:
            if self.planning_closed is not True or self.pending_count != 0:
                raise ValueError("complete requires planning_closed=true and pending_count=0")
            if self.planned_count is None or any(c is None for c in counts) or self.search_seed is None:
                raise ValueError("complete generation requires all counts and search_seed")
        return self


class RunCosts(_Open):
    """Measured GPU seconds; a key is null/absent until measured (never 0 as a placeholder)."""

    ga_billed_gpu_seconds: NonNegFloat | None = None
    random_billed_gpu_seconds: NonNegFloat | None = None
    actual_gpu_seconds: NonNegFloat | None = None
    baseline_gpu_seconds: NonNegFloat | None = None
    final_gpu_seconds: NonNegFloat | None = None
    failed_gpu_seconds: NonNegFloat | None = None
    retry_gpu_seconds: NonNegFloat | None = None
    profile_gpu_seconds: NonNegFloat | None = None


class Finalist(_Open):
    """One frozen finalist of a FINAL_RETRAIN run (the rule that selected it is recorded verbatim)."""

    candidate_id: str
    source_evaluation_id: str
    arm: str
    search_seed: int | None = None
    rung: str
    ap: float | None = None
    latency_ms: float | None = None
    selection_rule: str


class Run(_Open):
    id: NonEmptyStr
    status: RunStatus
    protocol_hash: NonEmptyStr
    created_at: str | None = None
    reason_code: str | None = None
    costs: RunCosts | None = None
    resolved_protocol: Protocol | None = None
    namespace: str | None = None
    execution_mode: ExecutionMode | None = None
    purpose: str | None = None
    preset: str | None = None
    updated_at: str | None = None
    terminal: bool | None = None
    source_commit: str | None = None
    protocol_canonicalization: str | None = None
    budget: dict[str, Any] | None = None
    cancel: dict[str, Any] | None = None
    kind: RunKind | None = None
    parent_run_id: str | None = None
    finalists: list[Finalist] | None = None


class Worker(_Open):
    id: NonEmptyStr
    status: WorkerStatus
    device: NonEmptyStr
    candidate_id: str | None = None
    heartbeat_at: str | None = None
    utilization_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    vram_mb: float | None = None
    epoch: NonNegInt | None = None
    epoch_target_total: NonNegInt | None = None
    learning_rate: float | None = None
    device_uuid: str | None = None
    device_name: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    attempt: int | None = None
    global_step: int | None = None
    rung: str | None = None
    pid: int | None = None
    lease_expires_at: str | None = None
    power_w: float | None = None
    sm_clock_mhz: float | None = None


class Event(_Open):
    """SSE data JSON; ``seq`` is run-local and monotonic (starts at 1)."""

    seq: Annotated[int, Field(ge=1)]
    type: EventType
    run_id: NonEmptyStr
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _typed_payload(self) -> "Event":
        model = _EVENT_PAYLOAD_MODELS.get(self.type)
        if model is not None:
            model.model_validate(self.payload)
        return self


_EVENT_PAYLOAD_MODELS: dict[EventType, type[BaseModel]] = {
    EventType.CANDIDATE_UPDATED: CandidateEvaluation,
    EventType.CANDIDATE_EVALUATED: CandidateEvaluation,
    EventType.GENERATION_UPDATED: Generation,
    EventType.GENERATION_COMPLETED: Generation,
    EventType.RUN_UPDATED: Run,
    EventType.WORKER_UPDATED: Worker,
}
