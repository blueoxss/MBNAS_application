"""Remote runner protocol v1 (``/core/v1/runner/*``): wire models shared by the core (server side) and the runner (client).

Rules enforced by every model here: ``extra="forbid"``; files are named by a run-relative ``relpath`` (validated:
relative, no ``..``) and no field of a runner -> core model is called ``path``/``file``/``dir``. Runner-local
ABSOLUTE paths still reach the core on two channels, by design: the checkpoint
``uri`` of :class:`CheckpointReport` (the daemon's realpath of the file) and the free-form ``ResultCommit.result.outputs``
(rung results, resolved recipe paths). The core stores those runner-local absolute paths in its DB (``checkpoints.path``,
``artifact_locations.uri``, ``jobs.result_json``) for the owner only (owner routes / owner export) and never projects
them into a public response (``mbnas_core.console.projection``: checkpoint views carry an id instead of a path, row
paths become run-relative). The :class:`JobEnvelope` carries runner-side locations
(``resume.*.path``, ``data_refs.*.protocol_root``, ...); they are read-only inputs to the runner and never echoed back.

1.1.0: the retention prune round trip. ``HeartbeatControl.prune`` is a
list of :class:`PruneOrder` (was ``list[str]``, never populated in 1.0.0) - each order is handed out at most once per
lease (job, attempt) - and E11 ``POST /checkpoints/{checkpoint_id}/pruned`` (:class:`PruneReport` ->
:class:`PruneAck`) reports the outcome; the core marks the checkpoint ``PRUNED`` only on that report.

Every job-scoped write carries the fence ``(runner_id, attempt)`` and the claim ``ticket`` (HMAC issued by ``claim``).
This module imports only pydantic and ``mbnas_contracts`` itself.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from mbnas_contracts.schemas.enums import StrEnum
from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec, ProgressMessage
from mbnas_contracts.util.canon import canonical_sha256

ENVELOPE_VERSION = 1
RUNNER_API_VERSION = "1.1.0"
RUNNER_BASE_PATH = "/core/v1/runner"
ALLOWED_RUNNER_KINDS: tuple[JobKind, ...] = (
    JobKind.BASELINE_TRAIN, JobKind.CANDIDATE_TRAIN, JobKind.FINAL_RETRAIN, JobKind.EVALUATE, JobKind.BENCHMARK)
"""``scheduling.jobs.GPU_KINDS``: the kinds a runner may claim (PROPOSE_GENERATION is owner-worker only)."""

SHA256_HEX = r"^[0-9a-f]{64}$"
SHA256_PREFIXED = r"^sha256:[0-9a-f]{64}$"


class RunnerProtocolError(StrEnum):
    """Machine codes of the ``Error{code, message, request_id, errors}`` body specific to the runner protocol."""

    KIND_NOT_ALLOWED = "KIND_NOT_ALLOWED"      # 400  claim requested a kind outside ALLOWED_KINDS
    LEASE_LOST = "LEASE_LOST"                  # 409  fence failed (lease_owner / attempt / status)
    RESULT_CONFLICT = "RESULT_CONFLICT"        # 409  second, different result for the same (job, attempt)
    PROCESSING = "PROCESSING"                  # 202  the same result is still being committed
    COMMIT_FAILED = "COMMIT_FAILED"            # 500  commit transaction rolled back; retry
    RUNNER_MISMATCH = "RUNNER_MISMATCH"        # 403  body/path runner_id differs from the token's runner
    VERSION_MISMATCH = "VERSION_MISMATCH"      # 409  runtime/schema incompatible (register)
    TICKET_INVALID = "TICKET_INVALID"          # 403  ticket missing, forged, expired or for another job/attempt
    CORE_URL_INSECURE = "CORE_URL_INSECURE"    # client side: non-loopback http:// core_url refused
    AUTH_REQUIRED = "AUTH_REQUIRED"            # 401
    ACCESS_DENIED = "ACCESS_DENIED"            # 403


class RunnerAuxCode(StrEnum):
    """Other codes the design names for runner endpoints (kept apart from the contract-mandated enum above)."""

    RUNNER_BUSY = "RUNNER_BUSY"                                  # 429 claim while a RUNNING job is held
    CHECKPOINT_REPORT_REJECTED = "CHECKPOINT_REPORT_REJECTED"    # 422 E6
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"                      # 413
    VALIDATION_ERROR = "VALIDATION_ERROR"                        # 422 body failed the model
    PRUNE_REQUEST_NOT_FOUND = "PRUNE_REQUEST_NOT_FOUND"          # 404 E11: no such prune request for this checkpoint
    PRUNE_REPORT_CONFLICT = "PRUNE_REPORT_CONFLICT"              # 409 E11: report disagrees with the request


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _relpath(v: str) -> str:
    if not v or "\x00" in v or v.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", v) \
            or ".." in v.replace("\\", "/").split("/"):
        raise ValueError(f"relpath must be a non-empty run-relative path without '..': {v!r}")
    return v


def _component(v: str) -> str:
    """One safe path component (a run / request id the runner turns into a directory or file name): the core id
    alphabet (``runner_api.tickets.ID_PATTERN``) with a leading alphanumeric, so never ``.``, ``..`` or a separator."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", v or ""):
        raise ValueError(f"must be one safe path component ([A-Za-z0-9._:@-], at most 128 characters): {v!r}")
    return v


Digest = Annotated[str, Field(pattern=SHA256_PREFIXED)]
Hex64 = Annotated[str, Field(pattern=SHA256_HEX)]
NonEmpty = Annotated[str, Field(min_length=1)]


# ---------------------------------------------------------------------------------------------------- error body
class ErrorBody(_Model):
    """Error body of every runner endpoint (same shape as the console API ``Error``)."""

    code: NonEmpty
    message: NonEmpty
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


# --------------------------------------------------------------------------------------------- JobEnvelope v1 (4.1)
class EnvelopeJob(_Model):
    job_id: NonEmpty
    attempt: Annotated[int, Field(ge=1)]
    kind: JobKind
    lease_expires_at: NonEmpty
    leased_at: NonEmpty
    spec: dict[str, Any] = Field(description="JobSpec (schemas/jobs.py) without ``attempt``; params already filtered (4.3)")
    max_attempts: Annotated[int, Field(ge=1)]
    priority: int


class EnvelopeRun(_Model):
    run_id: NonEmpty
    kind: NonEmpty
    protocol_hash: NonEmpty
    resolved_protocol: dict[str, Any]
    status_at_claim: NonEmpty
    execution_mode: NonEmpty


class EnvelopeCandidate(_Model):
    candidate_id: NonEmpty
    name: str | None = None
    arm: str | None = None
    family: str | None = None
    generation: Annotated[int, Field(ge=0)] | None = None
    search_seed: Annotated[int, Field(ge=0)] | None = None
    training_seed: Annotated[int, Field(ge=0)] | None = None
    architecture_hash: str | None = None
    architecture_yaml: str | None = None
    architecture_path: str | None = None
    genotype: dict[str, Any] | None = None
    candidate_status: str | None = None
    current_rung: str | None = None


class CheckpointRef(_Model):
    id: NonEmpty
    path: NonEmpty = Field(description="runner-local location of the checkpoint file (read-only input)")
    sha256: Hex64
    epoch: Annotated[int, Field(ge=0)]
    rung: str | None = None
    hashes: dict[str, Any] | None = None
    state: str | None = None


class ResumeLocation(_Model):
    runner_id: str | None = None
    store: Literal["runner_local", "server_store", "core_local"]
    uri: str | None = None


class EnvelopeResume(_Model):
    recorded_latest: CheckpointRef | None = None
    live_latest: CheckpointRef | None = None
    eval_ema: CheckpointRef | None = None
    location: ResumeLocation | None = None


class DatasetYamlRef(_Model):
    protocol_path: NonEmpty
    names: dict[str, str]


class SplitRef(_Model):
    protocol_root: NonEmpty
    manifest_hash: NonEmpty
    allowed: bool | None = Field(default=None, description="final_eval only: whether the final split may be read")


class EnvelopeDataRefs(_Model):
    dataset_yaml: DatasetYamlRef | None = None
    train: SplitRef | None = None
    search_val: SplitRef | None = None
    final_eval: SplitRef | None = None


class EnvelopeVersions(_Model):
    core_contract: NonEmpty
    progress_version: int
    checkpoint_format: NonEmpty
    required_runtime: dict[str, str]


class EnvelopeFence(_Model):
    runner_id: NonEmpty
    attempt: Annotated[int, Field(ge=1)]
    claim_id: NonEmpty
    issued_at: NonEmpty


class EnvelopeControl(_Model):
    lease_ttl_s: Annotated[float, Field(gt=0)]
    heartbeat_s: Annotated[float, Field(gt=0)]
    progress_interval_s: Annotated[float, Field(gt=0)]
    kill_grace_s: Annotated[float, Field(ge=0)]


class JobEnvelope(_Model):
    """Everything a runner needs to execute one leased job without DB access (envelope_version 1)."""

    envelope_version: Literal[1] = 1
    job: EnvelopeJob
    run: EnvelopeRun
    candidate: EnvelopeCandidate | None = Field(default=None, description="null for jobs without a candidate")
    resume: EnvelopeResume
    data_refs: EnvelopeDataRefs
    versions: EnvelopeVersions
    fence: EnvelopeFence
    control: EnvelopeControl

    @model_validator(mode="after")
    def _consistent(self) -> "JobEnvelope":
        if self.fence.attempt != self.job.attempt:
            raise ValueError("fence.attempt must equal job.attempt")
        spec = JobSpec.model_validate({**self.job.spec, "attempt": self.job.attempt})
        if spec.job_id != self.job.job_id or spec.kind != self.job.kind or spec.run_id != self.run.run_id:
            raise ValueError("job.spec must match job.job_id / job.kind / run.run_id")
        return self

    def job_spec(self, native_params: dict[str, Any] | None = None) -> JobSpec:
        """``JobSpec.model_validate({**spec, "attempt": attempt, "params": native})`` (design 4.2 D1)."""
        data = {**self.job.spec, "attempt": self.job.attempt}
        if native_params is not None:
            data["params"] = native_params
        return JobSpec.model_validate(data)


# ------------------------------------------------------------------------------------------- common write envelope
class JobWrite(_Model):
    """Fence + ticket carried by every job-scoped write."""

    runner_id: NonEmpty
    attempt: Annotated[int, Field(ge=1)]
    ticket: NonEmpty


# -------------------------------------------------------------------------------------------- E1 register (2.5)
class RuntimeManifest(_Model):
    """Runtime manifest reported at register (WP-2 section 2.5, R6)."""

    mbnas_runner: NonEmpty
    mbnas_model_runtime: NonEmpty
    mbnas_contracts: NonEmpty
    torch: str | None = None
    cuda: str | None = None
    python: NonEmpty
    envelope_version: int
    operator_files_sha256: dict[str, Hex64] = Field(description="operator file name -> sha256 of its bytes")
    parser_source_sha256: Hex64


class RegisterRequest(_Model):
    runner_id: NonEmpty
    runtime: RuntimeManifest
    kinds: list[JobKind] = Field(default_factory=lambda: list(ALLOWED_RUNNER_KINDS))
    host: str | None = None
    device: str | None = None
    device_uuid: str | None = None
    device_name: str | None = None
    data_mounts: dict[str, bool] = Field(default_factory=dict, description="protocol_root -> mounted (display only)")


class RegisterResponse(_Model):
    runner_id: NonEmpty
    core_contract: NonEmpty
    envelope_version: int
    lease_ttl_s: Annotated[float, Field(gt=0)]
    heartbeat_s: Annotated[float, Field(gt=0)]
    progress_interval_s: Annotated[float, Field(gt=0)]
    max_batch_messages: Annotated[int, Field(ge=1)]
    max_body_bytes: Annotated[int, Field(ge=1)]
    allowed_kinds: list[JobKind]
    artifact_store: NonEmpty
    runtime_verification: Literal["verified", "mismatch", "unknown"] = "unknown"
    server_time: str | None = None


class DeregisterRequest(_Model):
    runner_id: NonEmpty


# ---------------------------------------------------------------------------------------------------- E2 claim
class ClaimRequest(_Model):
    runner_id: NonEmpty
    kinds: list[JobKind] = Field(default_factory=lambda: list(ALLOWED_RUNNER_KINDS), min_length=1)


class ClaimResponse(_Model):
    """200 body of claim (204 = nothing to do): the envelope plus the write ticket and the core clock."""

    envelope: JobEnvelope
    ticket: NonEmpty
    server_time: NonEmpty


# --------------------------------------------------------------------------------------------------- E3 start
class StartRequest(JobWrite):
    device_uuid: str | None = None


class StartResponse(_Model):
    started_at: NonEmpty
    lease_expires_at: NonEmpty
    server_time: str | None = None


# ----------------------------------------------------------------------------------------------- E4 heartbeat
class HeartbeatRequest(JobWrite):
    progress: dict[str, Any] = Field(default_factory=dict, description="latest progress snapshot shown on the worker row")
    device_sample: dict[str, Any] | None = None


class PruneOrder(_Model):
    """One retention prune request of the core (DD-A66; WP-5 section 2 R11) for a checkpoint this runner registered.

    The runner deletes ``<work_root>/runs/<run_id>/<relpath>`` - and, for a ``full_eNNNN.pt`` payload, the RF-DETR
    ``native_eNNNN.ckpt`` beside it; the ``.sha256.json`` sidecar is kept - only when it is a regular file (no symlink)
    inside its own work root whose bytes hash to ``sha256``, then reports with E11. Nothing is deleted for an order it
    cannot verify. ``request_id`` is the idempotency key of the round trip."""

    request_id: NonEmpty
    checkpoint_id: NonEmpty
    run_id: NonEmpty
    relpath: NonEmpty = Field(description="run_dir-relative location of the full-state payload on this runner")
    sha256: Hex64
    bytes: Annotated[int, Field(ge=0)]
    policy: NonEmpty = Field(description="retention policy of the run (keep_all never produces an order)")
    reason: NonEmpty

    _rel = field_validator("relpath")(lambda cls, v: _relpath(v))
    _ids = field_validator("request_id", "run_id")(lambda cls, v: _component(v))


class HeartbeatControl(_Model):
    action: Literal["pause", "cancel"] | None = None
    run_status: NonEmpty
    stop_requested: bool
    prune: list[PruneOrder] | None = Field(
        default=None, description="retention prune orders for checkpoints this runner holds (DD-A66); an order is "
                                  "handed out at most once per lease (job, attempt) until the runner reports it (E11)")


class HeartbeatResponse(_Model):
    lease_expires_at: NonEmpty
    control: HeartbeatControl
    server_time: str | None = None


# ---------------------------------------------------------------------------------------------- E5 progress
class ProgressBatch(JobWrite):
    seq_start: Annotated[int, Field(ge=1)] = Field(description="sequence number of messages[0]; the rest follow densely")
    messages: Annotated[list[ProgressMessage], Field(min_length=1)]

    @field_validator("messages")
    @classmethod
    def _no_generation_summary(cls, v: list[ProgressMessage]) -> list[ProgressMessage]:
        for m in v:
            if m.payload.get("kind") == "generation_summary" or m.payload.get("generation_summary") is not None:
                raise ValueError("generation_summary payloads are owner-worker only")
        return v


class ProgressResponse(_Model):
    accepted_seq: Annotated[int, Field(ge=0)]


# ------------------------------------------------------------------------------------------- E6 checkpoints
CheckpointKind = Literal["training_full", "eval_ema", "weights_only", "best"]


class CheckpointReport(JobWrite):
    """A checkpoint the runner daemon verified (size, inside run_dir, sha256 recomputed) before reporting."""

    checkpoint_id: NonEmpty
    candidate_id: NonEmpty
    kind: CheckpointKind
    epoch: Annotated[int, Field(ge=0)]
    rung: str | None = None
    relpath: NonEmpty = Field(description="run_dir-relative location (both sides' common key)")
    uri: str | None = Field(default=None, description=(
        "runner-local ABSOLUTE path of the file (the daemon's realpath). The core stores it in its DB "
        "(checkpoints.path, artifact_locations.uri; owner-only) and echoes it to this runner as resume.*.path; it is "
        "never projected into a public response"))
    sha256: Hex64
    bytes: Annotated[int, Field(ge=0)]
    hashes: dict[str, Any] = Field(default_factory=dict)

    _rel = field_validator("relpath")(lambda cls, v: _relpath(v))


class CheckpointResponse(_Model):
    checkpoint_id: NonEmpty
    location_id: NonEmpty
    state: NonEmpty


# --------------------------------------------------------------------------------------------- E7 artifacts
class ArtifactItem(_Model):
    kind: NonEmpty
    relpath: NonEmpty
    sha256: Hex64
    bytes: Annotated[int, Field(ge=0)]
    mime_type: str | None = None
    sample_id: str | None = None
    split_hash: str | None = None
    candidate_id: str | None = None
    evaluation_id: str | None = None
    state: str = "NOT_READY"
    meta: dict[str, Any] = Field(default_factory=dict)

    _rel = field_validator("relpath")(lambda cls, v: _relpath(v))


class ArtifactReport(JobWrite):
    artifacts: Annotated[list[ArtifactItem], Field(min_length=1)]


class ArtifactRegistered(_Model):
    relpath: NonEmpty
    artifact_id: NonEmpty
    state: NonEmpty


class ArtifactResponse(RootModel[list[ArtifactRegistered]]):
    """E7 response: one entry per reported artifact, in request order."""


# ---------------------------------------------------------------------------------------------- E8 result
class ResultCommit(JobWrite):
    result: JobResult
    started_at: NonEmpty
    finished_at: NonEmpty
    cancel_observed: bool = False
    runner_shutting_down: bool = False
    payload_digest: Digest
    artifact_ids: list[str] = Field(default_factory=list)
    device_uuid: str | None = None

    def expected_digest(self) -> str:
        """Design 7.1: canonical_sha256 of {result, started_at, finished_at, cancel_observed, artifact_ids}."""
        return compute_payload_digest(self.result, self.started_at, self.finished_at, self.cancel_observed,
                                      self.artifact_ids)

    @model_validator(mode="after")
    def _digest_matches(self) -> "ResultCommit":
        if self.payload_digest != self.expected_digest():
            raise ValueError("payload_digest does not match the canonical digest of the payload")
        return self


def compute_payload_digest(result: JobResult, started_at: str, finished_at: str, cancel_observed: bool,
                           artifact_ids: list[str]) -> str:
    """``result`` is dumped with ``model_dump(mode="json")`` (all fields, None kept)."""
    return canonical_sha256({"result": result.model_dump(mode="json"), "started_at": started_at,
                             "finished_at": finished_at, "cancel_observed": cancel_observed,
                             "artifact_ids": list(artifact_ids)})


class ResultResponse(_Model):
    outcome: NonEmpty
    job_status: NonEmpty
    run_status: str | None = None
    response_digest: Digest


class AckRequest(JobWrite):
    pass


# ------------------------------------------------------------------------------------- E11 checkpoint pruned (1.1.0)
class PrunedItem(_Model):
    """A file the runner unlinked for a prune order (run-relative; ``sha256`` known for the payload only)."""

    relpath: NonEmpty
    bytes: Annotated[int, Field(ge=0)]
    sha256: Hex64 | None = None

    _rel = field_validator("relpath")(lambda cls, v: _relpath(v))


class PruneReport(_Model):
    """E11 body: the outcome of one :class:`PruneOrder`, sent only after the unlink succeeded (``removed`` lists what was
    deleted, ``bytes_freed`` their size) or when no file with the ordered digest is at ``relpath`` any more
    (``already_absent``: e.g. the stage trainer pruned it, or other bytes now live under that name - nothing deleted).
    Not job-scoped (no ticket): the order may be reported after its lease ended; only the runner that holds the copy
    may report it (token binding + the request's runner)."""

    runner_id: NonEmpty
    request_id: NonEmpty
    run_id: NonEmpty
    relpath: NonEmpty
    sha256: Hex64
    bytes: Annotated[int, Field(ge=0)]
    already_absent: bool
    bytes_freed: Annotated[int, Field(ge=0)] = 0
    removed: list[PrunedItem] = Field(default_factory=list)
    detail: str | None = Field(default=None, max_length=500)
    pruned_at: NonEmpty

    _rel = field_validator("relpath")(lambda cls, v: _relpath(v))
    _ids = field_validator("request_id", "run_id")(lambda cls, v: _component(v))

    @model_validator(mode="after")
    def _consistent(self) -> "PruneReport":
        if self.already_absent:
            if self.removed or self.bytes_freed:
                raise ValueError("already_absent reports delete nothing (removed empty, bytes_freed 0)")
            return self
        payload = [i for i in self.removed if i.relpath == self.relpath]
        if len(payload) != 1 or payload[0].sha256 != self.sha256 or payload[0].bytes != self.bytes:
            raise ValueError("a deletion report lists the ordered payload (relpath, sha256, bytes) in removed")
        if self.bytes_freed != sum(i.bytes for i in self.removed):
            raise ValueError("bytes_freed must be the total size of the removed files")
        return self


class PruneAck(_Model):
    """E11 answer: the checkpoint row is PRUNED (``replayed``: the request was already reported; nothing changed)."""

    checkpoint_id: NonEmpty
    request_id: NonEmpty
    state: Literal["PRUNED"]
    replayed: bool = False


# ------------------------------------------------------------------------------------- generator input registry
# (method, path, operationId, summary, request model | None, [(status, model | None | "array-of", description)], errors)
ENDPOINTS: tuple[dict[str, Any], ...] = (
    {"id": "E1", "method": "post", "path": "/register", "op": "runnerRegister", "summary": "Register or refresh a runner",
     "request": RegisterRequest, "responses": {"200": (RegisterResponse, "Registered"), "409": (ErrorBody, "VERSION_MISMATCH")}},
    {"id": "E2", "method": "post", "path": "/claim", "op": "runnerClaim", "summary": "Lease the next job",
     "request": ClaimRequest, "responses": {"200": (ClaimResponse, "Leased job (also re-delivered after a lost response)"),
                                           "204": (None, "No job available"),
                                           "400": (ErrorBody, "KIND_NOT_ALLOWED"), "429": (ErrorBody, "RUNNER_BUSY")}},
    {"id": "E3", "method": "post", "path": "/jobs/{job_id}/start", "op": "runnerStartJob", "summary": "Start the attempt (billing starts)",
     "request": StartRequest, "responses": {"200": (StartResponse, "Started"), "409": (ErrorBody, "LEASE_LOST")}},
    {"id": "E4", "method": "post", "path": "/jobs/{job_id}/heartbeat", "op": "runnerHeartbeat", "summary": "Extend the lease and read control flags",
     "request": HeartbeatRequest, "responses": {"200": (HeartbeatResponse, "Lease extended"), "409": (ErrorBody, "LEASE_LOST")}},
    {"id": "E5", "method": "post", "path": "/jobs/{job_id}/progress", "op": "runnerProgress", "summary": "Report progress (batch)",
     "request": ProgressBatch, "responses": {"200": (ProgressResponse, "Accepted"), "409": (ErrorBody, "LEASE_LOST"),
                                            "422": (ErrorBody, "Invalid batch")}},
    {"id": "E6", "method": "post", "path": "/jobs/{job_id}/checkpoints", "op": "runnerCheckpoint", "summary": "Register a verified checkpoint",
     "request": CheckpointReport, "responses": {"200": (CheckpointResponse, "Registered"), "409": (ErrorBody, "LEASE_LOST"),
                                               "422": (ErrorBody, "CHECKPOINT_REPORT_REJECTED")}},
    {"id": "E7", "method": "post", "path": "/jobs/{job_id}/artifacts", "op": "runnerArtifacts", "summary": "Register evaluation artifacts (batch)",
     "request": ArtifactReport, "responses": {"200": (ArtifactResponse, "Registered"), "409": (ErrorBody, "LEASE_LOST"),
                                             "422": (ErrorBody, "Invalid report")}},
    {"id": "E8", "method": "post", "path": "/jobs/{job_id}/result", "op": "runnerResult", "summary": "Commit the job result (idempotent)",
     "request": ResultCommit, "responses": {"200": (ResultResponse, "Committed or replayed"),
                                           "202": (ErrorBody, "PROCESSING (Retry-After: 2)"),
                                           "409": (ErrorBody, "RESULT_CONFLICT or LEASE_LOST"),
                                           "500": (ErrorBody, "COMMIT_FAILED")}},
    {"id": "E9", "method": "post", "path": "/jobs/{job_id}/ack", "op": "runnerAck", "summary": "Acknowledge receipt of the result response",
     "request": AckRequest, "responses": {"204": (None, "Acknowledged"), "404": (ErrorBody, "Unknown job/attempt")}},
    {"id": "E10", "method": "post", "path": "/deregister", "op": "runnerDeregister", "summary": "Graceful shutdown",
     "request": DeregisterRequest, "responses": {"204": (None, "Deregistered")}},
    {"id": "E11", "method": "post", "path": "/checkpoints/{checkpoint_id}/pruned", "op": "runnerCheckpointPruned",
     "summary": "Report the outcome of a prune order (the core marks the checkpoint PRUNED)",
     "request": PruneReport, "responses": {"200": (PruneAck, "Recorded, or replayed for an already reported request"),
                                          "404": (ErrorBody, "PRUNE_REQUEST_NOT_FOUND"),
                                          "409": (ErrorBody, "PRUNE_REPORT_CONFLICT")}},
)

MODELS: tuple[type[BaseModel], ...] = (
    ErrorBody, JobEnvelope, RuntimeManifest, RegisterRequest, RegisterResponse, DeregisterRequest, ClaimRequest,
    ClaimResponse, StartRequest, StartResponse, HeartbeatRequest, HeartbeatControl, HeartbeatResponse, ProgressBatch,
    ProgressResponse, CheckpointReport, CheckpointResponse, ArtifactItem, ArtifactReport, ArtifactRegistered,
    ArtifactResponse, ResultCommit, ResultResponse, AckRequest, PruneOrder, PrunedItem, PruneReport, PruneAck)
