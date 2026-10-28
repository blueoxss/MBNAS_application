"""Pydantic mirrors of the read-only archive components (contract 1.3.0): ``GET /api/v1/archive/...``.

Every model allows extra fields (``additionalProperties: true``) so artifacts can grow new keys; the fields declared here are
the ones the console relies on. A value that the artifacts do not provide is ``null`` (never 0). Models are listed in
``ARCHIVE_MODELS`` and merged into ``schemas.CONTRACT_MODELS`` (the OpenAPI components are generated from them, see
``contracts/history/migrate_1.2.9_to_1.3.0.py``).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _A(BaseModel):
    model_config = ConfigDict(extra="allow")


Int0 = Annotated[int, Field(ge=0)]


class ArchiveSource(_A):
    """Where the response came from: the run directory and the run-relative files that were read."""

    run_dir: str | None
    files: list[str]


class ArchiveRoot(_A):
    path: str
    family: str
    exists: bool
    kind: str | None = None  # contract 1.4.2: "campaign" on MBNAS_ARCHIVE_CAMPAIGN_ROOTS entries only; plain roots have no key


class ArchiveVerdicts(_A):
    notebook: str | None
    transition: str | None
    confirm: str | None
    smoke_check: str | None


class ArchiveGenCounts(_A):
    planned: Int0 | None
    done: Int0


class ArchiveCandCounts(_A):
    total: Int0
    ok: Int0
    failed: Int0
    by_status: dict[str, int] | None = None


class ArchiveBest(_A):
    map50_95: float | None
    small_target_recall: float | None
    latency_min_ms: float | None


class ArchiveJudgeBaseline(_A):
    name: str
    tier: str | None
    status: str | None = None
    map50_95: float | None
    latency_median_ms: float | None
    params_deploy: float | None


class ArchiveArm(_A):
    space: Any
    method: Any
    seed: Any


class ArchiveHashes(_A):
    protocol_v2_train: str | None
    metric_protocol: str | None
    dataset_manifest: str | None


class ArchiveHas(_A):
    baselines: bool
    generations: bool
    pareto: bool
    fidelity: bool
    confirm: bool
    plots: bool
    live: bool


class ArchiveCampaignCandidates(_A):
    """Candidate counts of a campaign arm from the ledger rows of kind cand (contract 1.4.2)."""

    total: int | None
    ok: int | None
    eval_fail: int | None
    degenerate: int | None
    cache_hit: int | None
    by_status: dict[str, int] | None


class ArchiveCampaignProgress(_A):
    """Ledger-derived progress of a campaign arm (contract 1.4.2); a missing ledger gives null, never 0."""

    generations_seen: int | None
    last_generation: int | None
    candidates: ArchiveCampaignCandidates | None
    last_eval_at: str | None
    last_event_at: str | None
    ledger_rows: int | None
    cost_s: float | None
    cost_search_s: float | None
    search_budget_s: float | None
    arm_budget_s: float | None
    source: str | None


class ArchiveCampaignBestTerminal(_A):
    """arm_result.json best_terminal (contract 1.4.2)."""

    candidate_id: str | None
    val_map50_95: float | None
    small_recall: float | None
    latency_v2: float | None
    params_deploy: float | None


class ArchiveCampaignResult(_A):
    """arm_result.json of a finished campaign arm (contract 1.4.2); the first five members are the file values as written."""

    stop_reason: str | None
    final_stop_reason: str | None
    n_eval: int | None
    terminal_count: int | None
    flags: list[Any] | None
    cost_s: float | None
    best_terminal: ArchiveCampaignBestTerminal | None


class ArchiveRunSummary(_A):
    id: str
    family: str
    path: str
    dataset_id: str | None
    scale: str | None
    status: Literal["running", "done", "failed", "unknown"]
    status_detail: str | None
    started_at: str | None
    ended_at: str | None
    duration_s: float | None
    verdicts: ArchiveVerdicts
    generations: ArchiveGenCounts
    candidates: ArchiveCandCounts
    best: ArchiveBest
    judge_baseline: ArchiveJudgeBaseline | None
    arm: ArchiveArm | None
    hashes: ArchiveHashes
    has: ArchiveHas
    # contract 1.4.2: campaign arms only (archive/campaign.py); a plain run has none of these keys
    campaign_id: str | None = None
    campaign_arm: str | None = None
    space: str | None = None
    method: str | None = None
    seed: int | None = None
    pair: int | None = None
    pair_done: bool | None = None
    queue_state: str | None = None
    campaign_arms_total: int | None = None
    arm_status: str | None = None
    progress: ArchiveCampaignProgress | None = None
    result: ArchiveCampaignResult | None = None


class ArchiveRunList(_A):
    roots: list[ArchiveRoot]
    runs: list[ArchiveRunSummary]
    scanned_at: str
    source: ArchiveSource
    warnings: list[str]


class ArchiveStage(_A):
    label: str
    started_at: str | None
    ended_at: str | None
    duration_s: float | None


class ArchiveGate(_A):
    name: str
    status: str | None
    hard: bool | None
    measured: str | None
    criterion: str | None


class ArchiveRole(_A):
    role: str
    candidate_id: str | None
    metric: str | None
    value: float | None
    values: dict[str, float | None] | None = None


class ArchiveFidelitySummary(_A):
    spearman: float | None
    kendall: float | None
    n: int | None
    snr: float | None = None


class ArchiveGpuGuardEpisode(_A):
    gpu: str
    waited_s: float | None
    external_mib_max: float | None
    start: str | None
    end: str | None
    outcome: Literal["proceed", "giveup", "open"]


class ArchiveGpuGuard(_A):
    records: Int0
    episodes: list[ArchiveGpuGuardEpisode]
    giveups: Int0


class ArchivePlotRef(_A):
    kind: str
    exists: bool


class ArchiveDriver(_A):
    path: str
    status_json: dict[str, Any] | None
    pipeline_log_tail: list[str]


class ArchiveRunningRun(_A):
    id: str
    stage: str | None
    nb_pid_alive: bool
    kernel_pid_alive: bool
    confirm_pid_alive: bool | None = None
    budget_s: float | None
    elapsed_s: float | None
    watchdog_last: str | None


class ArchiveGpu(_A):
    index: int
    name: str
    used_mib: float
    total_mib: float
    util_percent: float | None


class ArchiveLive(_A):
    drivers: list[ArchiveDriver]
    running_runs: list[ArchiveRunningRun]
    gpus: list[ArchiveGpu] | None
    server_time: str
    source: ArchiveSource
    warnings: list[str]


class ArchiveRun(ArchiveRunSummary):
    stages: list[ArchiveStage]
    gates: list[ArchiveGate]
    budget: dict[str, Any] | None
    preset: dict[str, Any] | None
    dataset: dict[str, Any] | None
    accuracy_floor: dict[str, Any] | None
    roles: list[ArchiveRole]
    final_val: list[dict[str, Any]]
    fidelity_summary: ArchiveFidelitySummary | None
    gpu_guard: ArchiveGpuGuard
    stop_rule: dict[str, Any] | None
    transition: dict[str, Any] | None
    orphan_check: dict[str, Any] | None
    live: ArchiveLive | None
    plots: list[ArchivePlotRef]
    source: ArchiveSource
    warnings: list[str]


class ArchiveConfirmRef(_A):
    val_map50_95: float | None
    test_tiles_map50_95: float | None
    test_native_map50_95: float | None
    latency_median_ms: float | None
    n_success: int | None = None
    val_map50_95_std: float | None = None
    tier: str | None = None


class ArchiveBaseline(_A):
    name: str
    kind: Literal["baseline", "anchor"]
    cfg_name: str | None
    tier: str | None
    epochs: int | None
    is_judge: bool
    status: str | None
    map50_95: float | None
    map50: float | None
    small_target_recall: float | None
    latency_median_ms: float | None
    latency_exclusive_ms: float | None
    forward_only_median_ms: float | None
    t_post_ms: float | None
    params_deploy: float | None
    params_train: float | None
    peak_mem_mb: float | None
    train_time_s: float | None
    head_mode: str | None
    best_ckpt: str | None
    checkpoint_sha256: str | None
    confirm: ArchiveConfirmRef | None


class ArchiveAnchor(_A):
    candidate_id: str | None
    fingerprint: str | None
    metrics: dict[str, Any]
    tier: str | None = None
    epochs: int | None = None


class ArchiveBaselineList(_A):
    judge_baseline: ArchiveJudgeBaseline | None
    baselines: list[ArchiveBaseline]
    anchor: ArchiveAnchor | None
    notes: list[str]
    source: ArchiveSource
    warnings: list[str]


class ArchiveObjective(_A):
    key: str
    direction: Literal["max", "min"]
    label: str


class ArchiveSpread(_A):
    min: float | None
    med: float | None
    max: float | None


class ArchiveDepth(_A):
    n_backbone: ArchiveSpread
    n_head: ArchiveSpread
    stage_count: ArchiveSpread


class ArchiveModuleDelta(_A):
    module: str
    count: int
    count_prev: int
    delta: int
    delta_per_candidate: float | None


class ArchiveCharacteristics(_A):
    n: Int0
    n_cfg: Int0
    n_cfg_missing: Int0
    n_prev: Int0
    n_prev_cfg: Int0
    module_histogram: dict[str, int]
    module_histogram_prev: dict[str, int]
    module_candidates: dict[str, int]
    head_mode: dict[str, int]
    depth: ArchiveDepth
    params_deploy: ArchiveSpread
    latency_median_ms: ArchiveSpread
    top_modules_delta: list[ArchiveModuleDelta]


class ArchiveDeltaPrev(_A):
    hypervolume: float | None
    best_map50_95: float | None
    best_small_recall: float | None
    best_latency: float | None


class ArchiveDeltaBaseline(_A):
    best_map50_95: float | None
    best_latency: float | None
    cum_best_map50_95: float | None = None
    baseline_name: str | None = None
    baseline_tier: str | None = None
    note: str | None = None


class ArchiveGeneration(_A):
    generation: Int0
    has_summary: bool
    phase: str | None
    population: int | None
    valid: int | None
    train_fail: int | None
    degenerate: int | None
    unique_fingerprints: int | None
    archive_size: int | None
    hypervolume: float | None
    hv_improved: bool | None
    stagnation: int | None
    best_map50_95: float | None
    best_small_recall: float | None
    best_latency: float | None
    mutation: dict[str, Any] | None
    n_rows: Int0
    status_counts: dict[str, int]
    new_fingerprints: Int0
    new_fingerprint_ids: list[str]
    lineage_modes: dict[str, int]
    delta_vs_prev: ArchiveDeltaPrev
    delta_vs_baseline: ArchiveDeltaBaseline
    cum_best: dict[str, float | None]
    front2d: list[str]
    front4d: list[str]
    front_added: list[str]
    front_removed: list[str]
    front4d_added: list[str] | None = None
    front4d_removed: list[str] | None = None
    characteristics: ArchiveCharacteristics


class ArchiveGenerationList(_A):
    objectives: list[ArchiveObjective]
    generations: list[ArchiveGeneration]
    hv_history: list[float]
    stagnation_rule: str
    notes: list[str]
    source: ArchiveSource
    warnings: list[str]


class ArchiveLineage(_A):
    mode: str | None
    parents: list[str]
    mutation: dict[str, Any] | None
    cache_hit: str | None
    notes_parse: Literal["ok", "truncated", "missing"] | None = None


class ArchiveCandHashes(_A):
    config: str | None
    genotype: str | None
    metric_protocol: str | None
    training_protocol: str | None


class ArchiveDeltaCand(_A):
    map50_95: float | None
    latency_median_ms: float | None
    params_deploy: float | None
    baseline: str | None = None
    baseline_tier: str | None = None


class ArchiveCandidateRow(_A):
    candidate_id: str
    generation: Int0
    fingerprint: str | None
    tier: str | None
    status: str
    error: str | None
    seed: int | None
    epochs: int | None
    metrics: dict[str, Any]
    lineage: ArchiveLineage
    roles: list[str]
    is_final: bool
    on_front2d: bool
    on_front4d: bool
    new_fingerprint: bool | None = None
    delta_vs_baseline: ArchiveDeltaCand
    hashes: ArchiveCandHashes
    has_cfg: bool
    has_checkpoint: bool


class ArchiveCandidateList(_A):
    total: Int0
    candidates: list[ArchiveCandidateRow]
    source: ArchiveSource
    warnings: list[str]


class ArchiveImprovement(_A):
    objective: str
    prev_best: float | None
    new_best: float
    candidate_id: str
    delta: float | None
    kind: Literal["initial", "improved"] | None = None


class ArchiveFrontPoint(_A):
    candidate_id: str
    map50_95: float | None
    latency_median_ms: float | None
    small_target_recall: float | None
    params_deploy: float | None


class ArchiveLineageEntry(_A):
    candidate_id: str
    mode: str | None
    parents: list[str]
    mutation: dict[str, Any] | None
    cache_hit: str | None
    notes_parse: str | None = None


class ArchiveGenerationDetail(_A):
    generation: ArchiveGeneration
    candidates: list[ArchiveCandidateRow]
    improvements: list[ArchiveImprovement]
    front2d_points: list[ArchiveFrontPoint]
    prev_front2d_points: list[ArchiveFrontPoint]
    lineage: list[ArchiveLineageEntry]
    characteristics: ArchiveCharacteristics
    source: ArchiveSource
    warnings: list[str]


class ArchiveEvaluation(_A):
    stage: Literal["search", "final", "exclusive", "confirm"]
    tier: str | None
    epochs: int | None
    seed: int | None
    status: str | None
    metrics: dict[str, Any]
    source: str


class ArchiveStageRow(_A):
    stage_index: int
    global_start: int
    modules: list[str]
    downsamples: int


class ArchiveArchSource(_A):
    kind: Literal["own", "fingerprint_twin"]
    candidate_id: str


class ArchiveArchitecture(_A):
    yaml_text: str | None
    cfg: dict[str, Any]
    n_backbone: Int0
    n_head: Int0
    stage_count: Int0
    stages: list[ArchiveStageRow]
    stages_source: str
    module_histogram: dict[str, int]
    source: ArchiveArchSource


class ArchiveParent(_A):
    candidate_id: str
    kind: Literal["candidate", "external_config"]
    fingerprint: str | None
    generation: int | None
    metrics_summary: dict[str, Any] | None
    has_cfg: bool


class ArchiveStageDiff(_A):
    stage_index: int
    parent_modules: list[str]
    child_modules: list[str]


class ArchiveParentDiff(_A):
    parent: str
    modules_added: list[str]
    modules_removed: list[str]
    module_delta: dict[str, int] | None = None
    stage_diff: list[ArchiveStageDiff]
    n_rows_parent: Int0
    n_rows_child: Int0


class ArchiveCandLineage(_A):
    mode: str | None
    parents: list[ArchiveParent]
    mutation: dict[str, Any] | None
    cache_hit: str | None
    notes_parse: str | None = None
    diff_vs_parents: list[ArchiveParentDiff]


class ArchiveLatency(_A):
    median: float | None
    p90: float | None
    mean: float | None
    std: float | None
    cv: float | None
    forward_only: float | None
    t_post: float | None
    head_mode: str | None
    protocol_hash: str | None
    exclusive: dict[str, Any] | None


class ArchiveCheckpoint(_A):
    tier: str | None
    path: str
    exists: bool | None
    sha256: str | None


class ArchiveProvenance(_A):
    cfg_path: str | None
    yaml_path: str | None
    jsonl_path: str


class ArchiveCandidateDetail(_A):
    row: ArchiveCandidateRow
    evaluations: list[ArchiveEvaluation]
    architecture: ArchiveArchitecture | None
    lineage: ArchiveCandLineage
    latency: ArchiveLatency
    checkpoints: list[ArchiveCheckpoint]
    roles: list[str]
    confirm: dict[str, Any] | None
    provenance: ArchiveProvenance
    source: ArchiveSource
    warnings: list[str]


class ArchivePareto(_A):
    objectives: list[ArchiveObjective]
    archive_entries: list[dict[str, Any]]
    front4d_ids: list[str]
    front2d_ids: list[str]
    hv_history: list[float]
    runtime_archive_ids: list[str] | None = None
    source: ArchiveSource
    warnings: list[str]


class ArchiveFidelity(_A):
    rank_correlation: dict[str, Any] | None
    noise: dict[str, Any] | None
    corr_by_head_mode: dict[str, Any] | None
    bootstrap_ci: dict[str, Any] | None
    evidence_note: str
    source: ArchiveSource
    warnings: list[str]


class ArchiveConfirm(_A):
    final_verdict: str | None
    seeds: list[int] | None
    baseline_name: str | None
    reference: str | None
    summary: dict[str, Any] | None
    verdicts: dict[str, Any] | None
    max_det_observed: Any
    test_unlock_used: bool | None
    final_report_md: str | None
    source: ArchiveSource
    warnings: list[str]


class ArchiveCurvePoint(_A):
    generation: int
    cum_train_time_s: float
    value: float
    candidate_id: str
    cum_train_time_s_no_cache: float | None = None


class ArchiveHvPoint(_A):
    generation: int
    value: float | None


class ArchiveCurves(_A):
    best_so_far_map50_95: list[ArchiveCurvePoint]
    hypervolume: list[ArchiveHvPoint]


class ArchiveFinalist(_A):
    candidate_id: str
    val_map50_95: float | None
    val_map50: float | None
    small_target_recall: float | None
    latency_median_ms: float | None
    params_deploy: float | None
    roles: list[str]
    tier: str | None = None


class ArchiveCompareBaseline(_A):
    name: str
    kind: str
    tier: str | None
    map50_95: float | None
    small_target_recall: float | None
    latency_median_ms: float | None
    params_deploy: float | None
    is_judge: bool


class ArchiveCompareConditions(_A):
    """Conditions of one compared run (contract 1.4.0). Every member is read from the run artifacts or null; never estimated."""

    dataset: str | None
    split: str | None
    stage: str | None
    epochs_total: int | None
    protocol_hash: str | None
    device: str | None
    precision: str | None
    checkpoint: str | None


class ArchiveCompareRun(_A):
    id: str
    family: str
    arm: ArchiveArm | None
    dataset_id: str | None
    judge_baseline: ArchiveJudgeBaseline | None
    curves: ArchiveCurves
    finalists: list[ArchiveFinalist]
    baselines: list[ArchiveCompareBaseline] | None = None
    comparable: bool
    incomparable_reason: str | None
    metric_protocol_hash: str | None = None
    conditions: ArchiveCompareConditions | None = None


class ArchiveCompare(_A):
    runs: list[ArchiveCompareRun]
    comparable: bool
    incomparable_reason: str | None
    notes: list[str]
    source: ArchiveSource
    warnings: list[str]


def _collect() -> dict[str, type[BaseModel]]:
    return {name: obj for name, obj in globals().items()
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == __name__
            and name.startswith("Archive")}


ARCHIVE_MODELS: dict[str, type[BaseModel]] = _collect()
"""Component name -> model, every ``Archive*`` model of this module (nested ones are components too)."""
