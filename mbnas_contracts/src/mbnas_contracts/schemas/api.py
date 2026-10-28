"""Pydantic mirrors of the request/response components: Health, ValidateRequest/Response, RunRequest,
RunCreated, Error, PresetSummary, Preset, PresetList, RunList, RegistryEntry, RegistryList, TrainingRequest,
TrainingRunCreated (contract 1.4.0, explicit single-model training runs) and, since contract 1.5.0, UploadResponse,
ModelConfigRevision and ModelConfigRevisionList.

``RegistryEntry`` is a named component since contract 1.2.0 (``RegistryList.registry`` / ``Health.registry`` items).
Request components (``ValidateRequest``, ``RunRequest``, ``FinalRetrainRequest``, ``TrainingRequest``) and, since 1.5.0,
every response component the core projection emits forbid unknown fields (``additionalProperties: false``).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mbnas_contracts.errors import ErrorLocation
from mbnas_contracts.schemas.enums import RegistryStatus
from mbnas_contracts.schemas.protocol import NonEmptyStr, Protocol
from mbnas_contracts.schemas.records import Run

UPLOAD_ID_PATTERN = r"^upl-[0-9a-f]{64}$"
SHA256_PREFIXED_PATTERN = r"^sha256:[0-9a-f]{64}$"
SHA256_OPT_PREFIX_PATTERN = r"^(sha256:)?[0-9a-f]{64}$"


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistryEntry(_Closed):
    """Baseline registry row with capability + evidence (version/commit/import class/hashes/init/train/export)."""

    id: NonEmptyStr
    family: NonEmptyStr
    status: RegistryStatus
    reason: str | None = None
    capabilities: dict[str, Any]
    evidence: dict[str, Any] | None = None


class RegistryList(_Closed):
    """1.5.0: ``source`` is a label (live | example), never a path; the DD-26 unreadable-root diagnostic is owner-only."""

    registry: list[RegistryEntry]
    source: str | None = None
    warning: str | None = None


class Allowlists(_Closed):
    dataset_roots: list[str] | None = None
    output_roots: list[str] | None = None
    archive_roots: list[str] | None = None


class PresetSummary(_Closed):
    id: str
    name: str
    purpose: str
    execution_mode: str
    namespace: str
    description: str | None = None


class Preset(PresetSummary):
    protocol: Protocol


class PresetList(_Closed):
    presets: list[Preset]


class SourceTree(_Closed):
    algorithm: str | None = None
    sha256: str | None = None
    files: int | None = None


class CoreReachability(_Closed):
    """``Health.core`` object form (gateway merge): reachability of core."""

    status: str | None = None
    reachable: bool | None = None
    error: str | None = None


class GatewayStatus(_Closed):
    """``Health.gateway`` (gateway merge): gateway self status (DD-A30 web dist, artifact store)."""

    status: str | None = None
    version: str | None = None
    core_url_configured: bool | None = None
    web_dist: bool | None = None
    store_writable: bool | None = None


class Health(_Closed):
    status: NonEmptyStr
    capabilities: dict[str, Any]
    versions: dict[str, Any]
    devices: list[dict[str, Any]]
    registry: list[RegistryEntry]
    commit_sha: NonEmptyStr
    server_time: str | None = None
    allowlists: Allowlists | None = None
    presets: list[PresetSummary] | None = None
    worker_count: Annotated[int, Field(ge=0)] | None = None
    warnings: list[str] | None = None
    source_tree: SourceTree | None = None
    core: Literal["unreachable"] | CoreReachability | None = None
    gateway: GatewayStatus | None = None
    request_id: str | None = None


class ValidationIssue(_Open):
    field: NonEmptyStr
    message: NonEmptyStr
    code: str | None = None


class ValidationWarning(_Open):
    field: str
    message: str
    code: str | None = None


class ResolvedDiffEntry(_Open):
    field: str
    requested: Any = None
    resolved: Any = None
    reason: str | None = None


class ValidateRequest(_Closed):
    protocol: Protocol


class ValidateResponse(_Closed):
    valid: bool
    errors: list[ValidationIssue]
    protocol_hash: NonEmptyStr | None
    resolved: dict[str, Any] | None
    capabilities: dict[str, Any]
    warnings: list[ValidationWarning] | None = None
    resolved_diff: list[ResolvedDiffEntry] | None = None
    validation_id: str | None = None


class RunRequest(_Closed):
    protocol: Protocol
    protocol_hash: NonEmptyStr
    idempotency_key: NonEmptyStr
    validation_id: NonEmptyStr | None = None


class RunCreated(_Closed):
    run_id: NonEmptyStr
    status: NonEmptyStr
    parent_run_id: str | None = None
    finalist_count: Annotated[int, Field(ge=0)] | None = None
    protocol_hash: NonEmptyStr | None = None
    created: bool | None = None
    kind: Literal["SEARCH", "FINAL_RETRAIN"] | None = None


USER_SOURCE_FIELDS = ("upload_id", "model_yaml_text", "source_sha256", "model_name")


class TrainingSource(_Closed):
    """Immutable source of an explicit training trial (baseline | user YAML | search or generated candidate).

    A user source is exactly one of ``upload_id`` (browser to gateway), ``model_yaml_text`` + ``source_sha256`` (gateway to
    core) or the legacy owner-local ``model_path``; the four upload members are valid for a user source only."""

    kind: Literal["baseline", "user", "search", "generated"]
    run_id: str | None = None
    candidate_id: str | None = None
    baseline_id: str | None = None
    model_path: str | None = None
    upload_id: Annotated[str, Field(pattern=UPLOAD_ID_PATTERN)] | None = None
    model_yaml_text: Annotated[str, Field(min_length=1)] | None = None
    source_sha256: Annotated[str, Field(pattern=SHA256_OPT_PREFIX_PATTERN)] | None = None
    model_name: Annotated[str, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def _binding(self) -> "TrainingSource":
        if self.kind != "user":
            if any(getattr(self, key) is not None for key in USER_SOURCE_FIELDS):
                raise ValueError("upload_id/model_yaml_text/source_sha256/model_name are only valid for a user source")
            needed = ("baseline_id",) if self.kind == "baseline" else ("run_id", "candidate_id")
            if any(not getattr(self, key) for key in needed):
                raise ValueError(f"{self.kind} source requires {', '.join(needed)}")
            return self
        given = [bool(self.upload_id), self.model_yaml_text is not None, bool(self.model_path)]
        if sum(given) != 1:
            raise ValueError("user source requires exactly one of upload_id, model_yaml_text (+ source_sha256) or "
                             "model_path")
        if self.model_yaml_text is not None and not self.source_sha256:
            raise ValueError("model_yaml_text requires source_sha256")
        return self


class TrainingSourceBinding(_Closed):
    """``TrainingRunCreated.source``: the recorded source binding (never the carried YAML text)."""

    kind: Literal["baseline", "user", "search", "generated"]
    run_id: str | None = None
    candidate_id: str | None = None
    baseline_id: str | None = None
    model_path: str | None = None
    source_sha256: str | None = None
    model_name: str | None = None


class TrainingRequest(_Closed):
    """Body of ``POST /training-runs``. Same fields and rules as ``training.selection.TrainingRunRequest`` (that model is the
    one the route parses; ``tests/unit/test_schemas_contract.py`` pins the two to the same JSON schema and verdicts). It is a
    separate class only because ``schemas`` must not import the training package."""

    source: TrainingSource
    protocol: dict[str, Any]
    mode: Literal["scratch", "pretrained", "resume", "evaluate_only"] = "scratch"
    validation_enabled: bool = True
    checkpoint: str | None = None
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)]


class TrainingRunCreated(_Closed):
    run_id: NonEmptyStr
    status: NonEmptyStr
    protocol_hash: NonEmptyStr
    created: bool
    candidate_id: NonEmptyStr
    source: TrainingSourceBinding
    mode: Literal["scratch", "pretrained", "resume", "evaluate_only"]


class FinalRetrainRequest(_Closed):
    """Explicit request for the separate final retraining phase of a COMPLETED search run (contract 1.2.0)."""

    idempotency_key: NonEmptyStr
    top_k_per_arm_per_seed: Annotated[int, Field(ge=1)] | None = None
    budget_gpu_hours: Annotated[float, Field(gt=0)]
    reason: str | None = None


class Error(_Closed):
    code: NonEmptyStr
    message: NonEmptyStr
    errors: list[dict[str, Any]] | None = None
    request_id: str | None = None
    location: ErrorLocation | None = None
    core_request_id: str | None = None


class RunList(_Closed):
    runs: list[Run]


class UploadResponse(_Closed):
    """``POST /uploads/model-config`` answer (gateway store, content addressed; no server path)."""

    upload_id: Annotated[str, Field(pattern=UPLOAD_ID_PATTERN)]
    source_sha256: Annotated[str, Field(pattern=SHA256_PREFIXED_PATTERN)]
    bytes: Annotated[int, Field(ge=1)]


class ModelConfigRevision(_Closed):
    """One recorded revision of a candidate model configuration (F5d)."""

    revision_id: NonEmptyStr
    source_sha256: Annotated[str, Field(pattern=SHA256_PREFIXED_PATTERN)]
    canonical_hash: str | None
    architecture_hash: str | None
    kind: Literal["original", "selected", "executed", "generated"]
    created_at: str
    parent_revision_id: str | None
    artifact_id: str | None


class ModelConfigRevisionList(_Closed):
    """``GET /runs/{run_id}/candidates/{candidate_id}/config-revisions`` (oldest first)."""

    run_id: NonEmptyStr
    candidate_id: NonEmptyStr
    revisions: list[ModelConfigRevision]
