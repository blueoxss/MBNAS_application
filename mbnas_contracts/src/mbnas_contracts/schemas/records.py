"""Pydantic mirrors of the contract record components: Artifact, Block, CandidateEvaluation, Generation, Run,
Worker, Event, Snapshot, CandidateDetail, GeneratedCandidate and StructureReport (contract 1.5.0).

Field names, required sets, enums and numeric bounds follow contracts/openapi.json exactly. Contract 1.5.0 closes every
component the core console projection emits (``additionalProperties: false``, ``x-public-projection``): their mirrors
forbid unknown fields (``_Closed``), so a response the projection did not reduce to the public DTO fails validation
(500 RESPONSE_CONTRACT_VIOLATION) instead of leaving core. Removed owner-only members: Block old_name / before /
after / repair_reason (and params.slot_kind), Generation.group_key, Worker pid / lease_expires_at, the lineage trace keys.
The contract's conditional (``if/then``) rules are enforced by ``model_validator``s:

* CandidateEvaluation with ``status=EVALUATED`` must carry finite in-range ap/recall/fitness, positive
  latency/size, a non-pending latency_source, a checkpoint_path and the cohort fields protocol_hash, batch,
  resolved_input_shape, comparison_group (and search_seed for GA/IndependentRandom).
* Generation with ``complete=True`` requires planning_closed=True, pending_count=0 and all counts, and the
  counts must satisfy evaluated+failed+not_admitted+pending == planned whenever all are present.
* Event payloads are validated against the component the contract binds to their ``type``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mbnas_contracts.schemas.enums import (
    Arm, ArtifactKind, ArtifactState, AuthMode, BlockSourceKind, CandidateStatus, EventType, ExecutionMode,
    LatencySource, LineageOperation, Precision, RunKind, RunStatus, RuntimeManifestStatus, SearchArm, TrainingStatus,
    WorkerStatus,
)
from mbnas_contracts.schemas.protocol import NonEmptyStr, Protocol

Fraction = Annotated[float, Field(ge=0, le=1)]
Positive = Annotated[float, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]
NonNegFloat = Annotated[float, Field(ge=0)]


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Closed(BaseModel):
    """A component (or nested object) the contract closes with ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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


class Artifact(_Closed):
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


YamlFrom = int | list[int]


class BlockParams(_Closed):
    """``Block.params``: the YAML row (from, repeats, module, args), its scope and the observed effective shapes.

    1.5.0: the search-space slot kind is owner-only (never sent)."""

    from_: YamlFrom | None = Field(None, alias="from")
    repeats: int | None = None
    module: str | None = None
    args: list[Any] | None = None
    scope: str | None = None
    effective: dict[str, Any] | None = None


class Block(_Closed):
    """One model row with its provenance (repair folded into its source, DD-A19). ``module``/``from``/``args``/``repeats``
    at the top level are the rows of a user model copied from its structure check (DD-A42); searched rows carry them in
    ``params``."""

    name: NonEmptyStr
    source: str | None
    mutated: bool
    position: NonNegInt | None = None
    source_kind: BlockSourceKind | None = None
    parent_block_id: str | None = None
    operation: str | None = None
    params: BlockParams | None = None
    module: str | None = None
    from_: YamlFrom | None = Field(None, alias="from")
    repeats: int | None = None
    args: list[Any] | None = None


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


class CandidateEvaluation(_Closed):
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
    epoch_target_total: Annotated[int, Field(ge=1)] | None = None  # >= 1 or absent: evaluate_only rows carry epochs 0 and none
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
    raw_metrics: dict[str, Any] | None = None  # closed allowlist object in the contract (checked by contract_errors)
    rewards: Rewards | None = None
    fitness_weights: dict[str, Any] | None = None
    checkpoint_state: Literal["PRESENT", "PRUNED", "MISSING"] | None = None
    postprocess_runtime: Literal["torchvision_cuda_nms", "python_loop", "no_nms"] | None = None
    training_status: TrainingStatus | None = None
    immutable: bool | None = None
    source_kind: str | None = None
    training_mode: str | None = None
    performance_status: str | None = None

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


class Generation(_Closed):
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


class FinalRetrainChild(_Closed):
    """``Run.final_retrain_runs[]``: one FINAL_RETRAIN child of a search run."""

    id: str
    status: str
    created_at: str | None = None
    finalist_count: NonNegInt | None = None


class Run(_Closed):
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
    last_seq: NonNegInt | None = None
    final_retrain_runs: list[FinalRetrainChild] | None = None


class Worker(_Closed):
    """A runner (``id`` = core-issued runner id) or the core-host proposal worker (opaque id; never hostname:pid)."""

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
    device_uuid: str | None = Field(None, deprecated=True)  # 1.5.0: replaced by device_id (never sent by core)
    device_name: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    attempt: int | None = None
    global_step: int | None = None
    rung: str | None = None
    power_w: float | None = None
    sm_clock_mhz: float | None = None
    runner_id: str | None = None
    device_id: str | None = None
    runtime_manifest_status: RuntimeManifestStatus | None = None
    loss: float | None = None
    train_loss: float | None = None
    images_per_second: float | None = None
    samples_per_second: float | None = None
    throughput: float | None = None
    epoch_seconds: float | None = None
    batch_seconds: float | None = None
    native_metrics: dict[str, Any] | None = None
    losses: dict[str, Any] | None = None


LOG_PAYLOAD_KEYS = (
    "reason_code", "candidate_id", "job_id", "attempt", "paused", "request_id", "arm", "search_seed", "generation",
    "child_run_id", "child_status", "structure_status", "policy", "forced_policy", "bytes_freed", "gpu_wait_s",
    "parent_run_id", "mode", "validation_id", "candidate_architecture_hash", "trainer_architecture_hash",
    "reported_sha256", "computed_sha256", "cap_s", "billed_before_s", "billed_s", "overshoot_s", "finalists",
    "selection_rule", "rank", "source", "recipe", "reason", "runner_id")
"""Allowlisted members of a ``log`` event payload besides message / level / message_detail / checks (contract 1.5.0)."""


class LogPayload(_Closed):
    """``Event.payload`` of type ``log`` (1.5.0 allowlist; text scrubbed by the core projection)."""

    model_config = ConfigDict(extra="forbid")
    message: str
    level: str | None = None
    message_detail: str | None = None
    checks: Any = None
    reason_code: Any = None
    candidate_id: Any = None
    job_id: Any = None
    attempt: Any = None
    paused: Any = None
    request_id: Any = None
    arm: Any = None
    search_seed: Any = None
    generation: Any = None
    child_run_id: Any = None
    child_status: Any = None
    structure_status: Any = None
    policy: Any = None
    forced_policy: Any = None
    bytes_freed: Any = None
    gpu_wait_s: Any = None
    parent_run_id: Any = None
    mode: Any = None
    validation_id: Any = None
    candidate_architecture_hash: Any = None
    trainer_architecture_hash: Any = None
    reported_sha256: Any = None
    computed_sha256: Any = None
    cap_s: Any = None
    billed_before_s: Any = None
    billed_s: Any = None
    overshoot_s: Any = None
    finalists: Any = None
    selection_rule: Any = None
    rank: Any = None
    source: Any = None
    recipe: Any = None
    reason: Any = None
    runner_id: Any = None


class Event(_Closed):
    """SSE data JSON; ``seq`` is run-local and monotonic (starts at 1)."""

    seq: Annotated[int, Field(ge=1)]
    type: EventType
    run_id: NonEmptyStr
    payload: dict[str, Any]
    created_at: str | None = None

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
    EventType.LOG: LogPayload,
}


class GeneratedCandidate(_Closed):
    """A structural proposal, deliberately independent from any evaluation record (always UNTRAINED)."""
    id: str
    candidate_id: str
    run_id: str
    name: str
    family: str
    evaluation_status: Literal['NOT_EVALUATED']
    structure_status: Literal['PASS', 'FAIL', 'UNVERIFIED']
    arm: str | None = None
    origin: str | None = None
    candidate_status: str | None = None
    requested_method: list[str] | str | None = None
    effective_method: str | None = None
    generation_order: NonNegInt | None = None
    parameters: NonNegInt | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    duplicate_scope: Literal['per_seed'] | None = None
    execution: str | None = None
    ap: None = None
    recall: None = None
    latency_ms: None = None
    fitness: None = None
    training_status: Literal["UNTRAINED", "TRAINED"]
    generation: NonNegInt | None = None
    parents: list[str] | None = None
    architecture_hash: str | None = None
    search_seed: int | None = None
    training_seed: int | None = None
    seed: int | None = None
    attempt: int | None = None
    blocks: list[Block] | None = None
    architecture_path: str | None = None


class Snapshot(_Closed):
    run: Run
    candidates: list[CandidateEvaluation]
    generations: list[Generation]
    workers: list[Worker]
    events: list[Event]
    last_seq: NonNegInt
    generated_candidates: list[GeneratedCandidate] = Field(default_factory=list)


class BlockCatalogEntry(BaseModel):
    """One row of the static block design-origin table (contract 1.4.2; ``search/block_catalog.py``). ``null`` = unknown."""

    model_config = ConfigDict(extra="forbid")

    origin_family: str | None
    introduced_in: str | None
    role: str | None
    reference: str | None
    notes: str | None


class BlockCatalogTable(BaseModel):
    """``GET /structure/block-catalog`` (contract 1.4.2): the whole static table; nothing is imported or built."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["mbnas-block-catalog/1"] = Field(alias="schema")
    entries: dict[str, BlockCatalogEntry]
    native_entries: dict[str, BlockCatalogEntry]
    families: list[str]


class BlockCatalog(_Closed):
    """``StructureReport.blocks[].catalog`` (contract 1.4.2): the table row of one block plus ``catalog_key`` and ``source_path``.

    Every key is present, every value may be ``null`` (unknown, never estimated). ``source_path`` is filled only by documents an
    explicit structure check produced. The nested block records of ``StructureReport`` stay plain dicts; this model is the
    component the contract names and is exercised by the tests against real documents.
    """

    catalog_key: str | None
    origin_family: str | None
    introduced_in: str | None
    role: str | None
    reference: str | None
    notes: str | None
    source_path: str | None


class BlockCatalogSummary(BaseModel):
    """``StructureReport.catalog_summary`` (contract 1.4.2): blocks per known origin family and blocks without one."""

    model_config = ConfigDict(extra="forbid")

    by_origin_family: dict[str, Annotated[int, Field(ge=1)]]
    unknown: NonNegInt


class TemplateDetail(_Closed):
    """``lineage.template_detail``: the stored seed template ``{id, family, variant}``."""

    id: str | None = None
    family: str | None = None
    variant: str | None = None


class SearchSpaceLabel(_Closed):
    """``lineage.search_space``: version and variant label only (1.5.0)."""

    version: str | None = None
    variant: str | None = None


class _PublicLineage(_Closed):
    """DD-A19 public lineage subset shared by ``CandidateDetail.lineage`` and ``StructureReport.lineage``."""

    operation: LineageOperation | None = None
    method: str | None = None
    parents: list[str] | None = None
    parent_evaluation_ids: list[str | None] | None = None
    template_detail: TemplateDetail | None = None
    search_space: SearchSpaceLabel | None = None
    source_candidate_id: str | None = None
    source_evaluation_id: str | None = None
    source_run_id: str | None = None
    source_arm: str | None = None
    source_rung: str | None = None
    source_name: str | None = None
    rank: int | None = None
    selection_rule: str | None = None
    init: Any = None
    recipe: dict[str, Any] | None = None
    source_template: str | None = None
    training_source: dict[str, Any] | None = None
    source_architecture_hash: str | None = None


class StructureLineage(_PublicLineage):
    """``StructureReport.lineage``: the stored lineage copy, so ``template`` may still be the ``{id, family, variant}``."""

    template: str | TemplateDetail | None = None


class StructureSandbox(_Closed):
    """``StructureReport.sandbox``: how the structure-check process was isolated (DD-A20)."""

    isolation: Any = None
    limits: dict[str, Any] | None = None
    wall_timeout_s: float | None = None
    elapsed_s: float | None = None
    exit_code: int | None = None


class StructureReport(_Closed):
    """Structure document ``mbnas-structure/2`` (contract ``StructureReport``; 1.5.0 closes it).

    Deliberately permissive: only ``schema`` and ``status`` are required, so saved ``mbnas-structure/1`` proofs,
    declaration-only documents and the documents of native backends all validate. Declared (D), derived (R) and observed (O)
    values live under separate keys of the document; the nested block/edge/check shapes are described by the contract and
    checked by ``contract_errors("StructureReport", ...)`` in the tests, not re-modelled here. ``parser_source_sha256``
    (``observation``) is the sha256 of the parser source registered in the checking process, not of the YAML.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: NonEmptyStr = Field(alias="schema")
    status: Literal["PASS", "FAIL", "UNVERIFIED"]
    backend: str | None = None
    representation: str | None = None
    family: str | None = None
    template_id: str | None = None
    class_count: int | None = None
    scale: str | None = None
    input_size: int | list[int] | None = None
    fixed_resolution: int | None = None  # contract 1.4.1: native backends, the one input size the model is built for
    evidence_source: str | None = None
    candidate_id: str | None = None
    run_id: str | None = None
    name: str | None = None
    origin: str | None = None
    seed: int | None = None
    training_seed: int | None = None
    source: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None
    native_config: dict[str, Any] | None = None  # contract 1.4.1: native backends, declared model configuration
    yaml_hash: str | None = None
    architecture_hash: str | None = None
    signature_hash: str | None = None
    parameters: int | None = None
    flops: int | None = None
    flops_status: str | None = None
    flops_source: str | None = None
    observation: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] | None = None
    catalog_summary: BlockCatalogSummary | None = None  # contract 1.4.2; blocks[].catalog is checked by contract_errors
    edges: list[dict[str, Any]] | None = None
    edges_unobserved: list[dict[str, Any]] | None = None
    # contract 1.4.1: native-backend members (typed in the contract; nested shapes are checked by contract_errors, not re-modelled)
    observed_scopes: list[dict[str, Any]] | None = None
    repeat_groups: list[dict[str, Any]] | None = None
    parameters_unattributed: int | None = None
    parameter_free_modules_not_listed: list[str] | None = None
    runtime_requirements: dict[str, Any] | None = None
    scale_source: Literal["yaml", "file_name", "file_name_suffix", "first_scale_key"] | None = None
    lineage: StructureLineage | None = None
    lineage_status: str | None = None
    diagnostics: list[dict[str, Any]] | None = None
    checks: list[dict[str, Any]] | None = None
    reason_code: str | None = None
    model_id: str | None = None
    sandbox: StructureSandbox | None = None
    source_sha256: str | None = None


class CandidateLineage(_PublicLineage):
    """operation crossover -> 2 parents, clone/mutation -> 1, random/baseline -> 0, final -> the finalist source.

    1.5.0 public subset (DD-A19): crossover cut/mask, mutation / repair events, prior draw, tournament selection, parent
    rows, elitism flag, notes and the space / prior hashes are owner-only (``/core/v1/owner/*``)."""

    template: str | None = None


class CheckpointPrune(_Closed):
    """Retention record of a checkpoint row; ``deleted[]`` lists sizes only (no paths)."""

    policy: str | None = None
    reason: str | None = None
    deleted: list[dict[str, Any]] | None = None
    bytes_freed: NonNegInt | None = None
    kept: str | None = None
    candidate_status: str | None = None
    note: str | None = None


class CheckpointView(_Closed):
    """``CandidateDetail.checkpoints[]`` (1.5.0): a checkpoint row without its host path (path -> id / artifact_id)."""

    id: NonEmptyStr
    run_id: str | None = None
    candidate_id: str | None = None
    rung: str | None = None
    epoch: NonNegInt
    kind: Literal["training_full", "eval_ema", "weights_only", "best"]
    sha256: str
    bytes: NonNegInt | None = None
    hashes: dict[str, Any] | None = None
    state: Literal["PRESENT", "PRUNED", "MISSING"]
    pruned_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    prune: CheckpointPrune | None = None
    artifact_id: str | None = None


class CandidateDetail(_Closed):
    candidate_id: str
    run_id: str
    name: str
    arm: str
    family: str
    architecture_hash: str
    architecture_yaml: str | None = None
    architecture_path: str | None = None
    blocks: list[Block]
    parents: list[str]
    parent_evaluation_ids: list[str] | None = None
    lineage: CandidateLineage | None = None
    evaluations: list[CandidateEvaluation]
    checkpoints: list[CheckpointView] | None = None
    cost: dict[str, Any] | None = None
    gate_history: list[dict[str, Any]] | None = None
    candidate_status: str
    audit: dict[str, Any] | None = None  # closed public evidence subset in the contract (checked by contract_errors)
    structure: StructureReport | None = None
    structure_status: str | None = None
    evaluation_status: Literal["EVALUATED", "NOT_EVALUATED"] | None = None
    origin: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    training_status: TrainingStatus | None = None
    current_rung: str | None = None
    search_seed: int | None = None
    training_seed: int | None = None
    generation: NonNegInt | None = None
