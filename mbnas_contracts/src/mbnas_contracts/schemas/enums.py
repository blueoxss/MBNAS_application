"""Status and category vocabularies of contracts/openapi.json (single spelling ``CANCELLED``).

Legacy spellings are accepted only by :func:`canonicalize_status` (importer path); API responses always use the
canonical members below.
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``str`` enum whose JSON form is its value."""

    def __str__(self) -> str:
        return str(self.value)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})
POLICY_LOCKED_RUN_STATUSES: frozenset[RunStatus] = frozenset(set(RunStatus) - TERMINAL_RUN_STATUSES)


class CandidateStatus(StrEnum):
    PROPOSED = "PROPOSED"
    BUILD_VALID = "BUILD_VALID"
    QUEUED = "QUEUED"
    TRAINING = "TRAINING"
    EVALUATING = "EVALUATING"
    EVALUATED = "EVALUATED"
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    PROMOTION_ADMITTED = "PROMOTION_ADMITTED"
    SEARCH_COMPLETE = "SEARCH_COMPLETE"
    FINALIST = "FINALIST"
    FINAL_RETRAINING = "FINAL_RETRAINING"
    FINAL_COMPLETE = "FINAL_COMPLETE"
    PAUSED = "PAUSED"
    INVALID = "INVALID"
    TRAIN_FAILED = "TRAIN_FAILED"
    EVAL_FAILED = "EVAL_FAILED"
    REJECTED_BY_GATE = "REJECTED_BY_GATE"
    ELIGIBLE_BUDGET_EXHAUSTED = "ELIGIBLE_BUDGET_EXHAUSTED"
    RESUME_BLOCKED = "RESUME_BLOCKED"
    CANCELLED = "CANCELLED"


LEGACY_STATUS_ALIASES: dict[str, str] = {"CANCELED": "CANCELLED"}


def canonicalize_status(value: str) -> str:
    """Map a legacy status spelling (e.g. ``CANCELED``) to the canonical one; unknown values raise ``ValueError``."""
    text = str(value).strip().upper()
    text = LEGACY_STATUS_ALIASES.get(text, text)
    if text in RunStatus.__members__ or text in CandidateStatus.__members__:
        return text
    raise ValueError(f"unknown status {value!r}")


class Arm(StrEnum):
    BASELINE = "Baseline"
    GA = "GA"
    INDEPENDENT_RANDOM = "IndependentRandom"
    FINAL = "Final"


class RunKind(StrEnum):
    """SEARCH = baseline + GA/Random search run; FINAL_RETRAIN = child run of POST /runs/{id}/final-retrain."""

    SEARCH = "SEARCH"
    FINAL_RETRAIN = "FINAL_RETRAIN"


class WorkerStatus(StrEnum):
    """``lost`` = heartbeat older than the liveness window or process SIGKILLed (reported by API readers)."""

    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    LOST = "lost"


class LineageOperation(StrEnum):
    """crossover -> 2 parents, clone/mutation -> 1, random/baseline -> 0, final -> the finalist source candidate."""

    CROSSOVER = "crossover"
    CLONE = "clone"
    MUTATION = "mutation"
    RANDOM = "random"
    BASELINE = "baseline"
    FINAL = "final"


class SearchArm(StrEnum):
    GA = "GA"
    INDEPENDENT_RANDOM = "IndependentRandom"


class Precision(StrEnum):
    FP32 = "FP32"
    FP16 = "FP16"
    INT8 = "INT8"


class LatencySource(StrEnum):
    MEASURED = "measured"
    CALIBRATED_PROXY = "calibrated_proxy"
    UNVERIFIED_PROXY = "unverified_proxy"
    PENDING = "pending"


class ExecutionMode(StrEnum):
    PILOT = "PILOT"
    REGISTERED_SEARCH = "REGISTERED_SEARCH"
    HISTORICAL_REPRODUCTION = "HISTORICAL_REPRODUCTION"


class EventType(StrEnum):
    CANDIDATE_UPDATED = "candidate.updated"
    CANDIDATE_EVALUATED = "candidate.evaluated"
    GENERATION_UPDATED = "generation.updated"
    GENERATION_COMPLETED = "generation.completed"
    RUN_UPDATED = "run.updated"
    WORKER_UPDATED = "worker.updated"
    LOG = "log"
    SNAPSHOT_REQUIRED = "snapshot.required"


class ArtifactKind(StrEnum):
    GT = "gt"
    TP = "tp"
    FN = "fn"
    FP = "fp"
    PR_CURVE = "pr_curve"
    CONFUSION_MATRIX = "confusion_matrix"
    ARCHITECTURE = "architecture"
    CHECKPOINT = "checkpoint"
    ONNX = "onnx"
    TENSORRT_ENGINE = "tensorrt_engine"
    OTHER = "other"


class ArtifactState(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    EXPIRED = "EXPIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    MISSING = "MISSING"
    LOAD_FAILED = "LOAD_FAILED"


class AuthMode(StrEnum):
    SIGNED_URL = "signed_url"
    BEARER_FETCH = "bearer_fetch"


class BlockSourceKind(StrEnum):
    """Public block provenance (contract 1.5.0): a repaired row is reported with the source it was repaired from
    (repair folded by the core projection, DD-A19), so ``repair`` is not a public value."""

    PARENT_A = "parent_a"
    PARENT_B = "parent_b"
    BASELINE = "baseline"
    RANDOM = "random"
    MUTATION = "mutation"
    NEW = "new"


class RuntimeManifestStatus(StrEnum):
    """Contract component ``RuntimeManifestStatus`` (1.5.0): the runner runtime-manifest check at registration."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class TrainingStatus(StrEnum):
    """Display vocabulary ``training_status`` (DD-A23, contract 1.5.0); the status enums are unchanged."""

    UNTRAINED = "UNTRAINED"
    TRAINED = "TRAINED"


class RegistryStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BLOCKED_SOURCE = "BLOCKED_SOURCE"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
