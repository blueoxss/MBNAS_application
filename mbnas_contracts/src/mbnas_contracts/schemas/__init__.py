"""Pydantic v2 models mirroring contracts/openapi.json components (names identical) plus worker job models."""
from mbnas_contracts.schemas.api import (
    Error, FinalRetrainRequest, Health, ModelConfigRevision, ModelConfigRevisionList, Preset, PresetList, PresetSummary,
    RegistryEntry, RegistryList, RunCreated, RunList, RunRequest, TrainingRequest, TrainingRunCreated, TrainingSource,
    TrainingSourceBinding, UploadResponse, ValidateRequest, ValidateResponse,
)
from mbnas_contracts.schemas.enums import (
    POLICY_LOCKED_RUN_STATUSES, TERMINAL_RUN_STATUSES, Arm, ArtifactKind, ArtifactState, AuthMode, BlockSourceKind,
    CandidateStatus, EventType, ExecutionMode, LatencySource, LineageOperation, Precision, RegistryStatus, RunKind,
    RunStatus, RuntimeManifestStatus, SearchArm, TrainingStatus, WorkerStatus, canonicalize_status,
)
from mbnas_contracts.schemas.archive import ARCHIVE_MODELS
from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec, JobStatus, ProgressMessage
from mbnas_contracts.schemas.protocol import Protocol
from mbnas_contracts.schemas.records import (
    Artifact, Block, BlockCatalog, BlockCatalogEntry, BlockCatalogSummary, BlockCatalogTable, CandidateDetail, CandidateEvaluation, Event,
    Generation, GeneratedCandidate, Run, Snapshot, StructureReport, Worker,
)

CONTRACT_MODELS = {
    "Protocol": Protocol, "Artifact": Artifact, "Block": Block, "CandidateEvaluation": CandidateEvaluation,
    "Generation": Generation, "Run": Run, "Worker": Worker, "Event": Event, "Snapshot": Snapshot, "Health": Health,
    "ValidateRequest": ValidateRequest, "ValidateResponse": ValidateResponse, "RunRequest": RunRequest,
    "RunCreated": RunCreated, "Error": Error, "PresetSummary": PresetSummary, "Preset": Preset,
    "PresetList": PresetList, "RunList": RunList, "CandidateDetail": CandidateDetail, "RegistryList": RegistryList,
    "RegistryEntry": RegistryEntry, "FinalRetrainRequest": FinalRetrainRequest,
    "GeneratedCandidate": GeneratedCandidate, "StructureReport": StructureReport,
    "BlockCatalog": BlockCatalog, "BlockCatalogEntry": BlockCatalogEntry, "BlockCatalogSummary": BlockCatalogSummary,
    "BlockCatalogTable": BlockCatalogTable,  # contract 1.4.2 (DD-38)
    "TrainingRequest": TrainingRequest, "TrainingRunCreated": TrainingRunCreated,  # contract 1.4.0
    # contract 1.5.0 (WP-4): RuntimeManifestStatus is a string-enum component, so its entry is the StrEnum itself
    # (no model_json_schema; non-object components carry no properties to compare)
    "RuntimeManifestStatus": RuntimeManifestStatus, "ModelConfigRevision": ModelConfigRevision,
    "ModelConfigRevisionList": ModelConfigRevisionList, "UploadResponse": UploadResponse,
    **ARCHIVE_MODELS,  # contract 1.3.0: the read-only archive API components (schemas/archive.py)
}
"""Contract component name -> model (contract 1.2.0: RegistryEntry and FinalRetrainRequest are named components)."""

__all__ = [
    "CONTRACT_MODELS", "POLICY_LOCKED_RUN_STATUSES", "TERMINAL_RUN_STATUSES", "Arm", "Artifact", "ArtifactKind",
    "ArtifactState", "AuthMode", "Block", "BlockCatalog", "BlockCatalogEntry", "BlockCatalogSummary", "BlockCatalogTable",
    "BlockSourceKind", "CandidateDetail", "CandidateEvaluation",
    "CandidateStatus", "Error", "Event", "EventType", "ExecutionMode", "FinalRetrainRequest", "Generation", "GeneratedCandidate", "Health",
    "JobKind", "JobResult", "JobSpec", "JobStatus", "LatencySource", "LineageOperation", "ModelConfigRevision",
    "ModelConfigRevisionList", "Precision", "Preset",
    "PresetList", "PresetSummary", "ProgressMessage", "Protocol", "RegistryEntry", "RegistryList", "RegistryStatus",
    "Run", "RunCreated", "RunKind", "RunList", "RunRequest", "RunStatus", "RuntimeManifestStatus", "SearchArm",
    "Snapshot", "StructureReport", "TrainingRequest", "TrainingRunCreated", "TrainingSource", "TrainingSourceBinding",
    "TrainingStatus", "UploadResponse", "ValidateRequest",
    "ValidateResponse", "Worker", "WorkerStatus", "canonicalize_status",
]
