"""Pydantic mirror of the contract component ``Protocol`` (requested or resolved policy body).

Only the fields the contract names are typed; every other section of the nas-policy body (training, fitness,
rungs, ...) is retained through ``extra='allow'`` and checked semantically by ``protocol.validator``.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from mbnas_contracts.schemas.enums import ExecutionMode, Precision, SearchArm

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ConsolePaths(_Open):
    """Absolute Linux server paths; existence and allowlist are checked by the server, not the browser."""

    dataset_yaml: NonEmptyStr | None = None
    train_root: NonEmptyStr | None = None
    search_val_root: NonEmptyStr | None = None
    final_eval_root: NonEmptyStr | None = None
    output_root: NonEmptyStr


class BaselineSelection(_Open):
    id: NonEmptyStr
    family: str | None = None
    enabled: bool
    name: str | None = None  # 1.5.0: display label supplied by the preset (e.g. YOLO11N)
    source: str | None = None  # 1.5.0: origin label supplied by the preset (e.g. Ultralytics)


class SearchSettings(_Open):
    evaluation_enabled: bool = True
    candidate_count: Annotated[int, Field(ge=1)] | None = None
    max_attempts: Annotated[int, Field(ge=1)] | None = None
    allow_duplicates: bool = False
    nc: Annotated[int, Field(ge=1)] | None = None
    input_size: Annotated[int, Field(ge=32)] | None = None
    families: list[str] | None = None
    requested_method: list[str] | None = None
    effective_method: str | None = None
    duplicate_scope: str | None = None  # resolved-only, "per_seed"; the validator rejects any other value (FIELD_INVALID)
    arms: list[SearchArm] | None = None
    population: Annotated[int, Field(ge=2)] | None = None
    generations: Annotated[int, Field(ge=1)] | None = None
    mutation_rate: Annotated[float, Field(ge=0, le=1)] | None = None
    seeds: Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1)] | None = None
    budget_gpu_hours: Annotated[float, Field(gt=0)] | None = None
    crossover_rate: Annotated[float, Field(ge=0, le=1)] | None = None


class BenchmarkPostprocess(_Open):
    """Latency operating point of forward_and_postprocess (separate from the AP evaluation floor 0.001)."""

    conf: Annotated[float, Field(ge=0, le=1)] | None = None
    iou: Annotated[float, Field(ge=0, le=1)] | None = None
    max_det: Annotated[int, Field(ge=1)] | None = None


class BenchmarkSettings(_Open):
    device: NonEmptyStr | None = None
    precision: Precision | None = None
    batch: Annotated[int, Field(ge=1)] | None = None
    warmup: Annotated[int, Field(ge=0)] | None = None
    repeats: Annotated[int, Field(ge=1)] | None = None
    postprocess: BenchmarkPostprocess | None = None


class Console(_Open):
    paths: ConsolePaths
    baselines: list[BaselineSelection] = Field(default_factory=list)
    search: SearchSettings
    benchmark: BenchmarkSettings = Field(default_factory=BenchmarkSettings)
    execution_mode: ExecutionMode | None = None
    preset: str | None = None
    purpose: str | None = None
    run_namespace: str | None = None
    label: str | None = None


class Protocol(_Open):
    """Requested policy body + console extension; Validate turns it into an immutable resolved protocol.

    ``resolution`` (contract 1.5.0, server-written in a resolved protocol only) is a closed object whose ``paths.<key>``
    carry only ``requested`` (no realpath of the validating host; ``resolution.paths`` is hash-excluded). It is kept a
    plain mapping here so that a request echoing an older resolved protocol is not refused by the mirror; the
    contract document (``contract_errors``) types it."""

    schema_version: NonEmptyStr
    status: NonEmptyStr | None = None
    value_provenance: NonEmptyStr | None = None
    console: Console
    resolution: dict[str, Any] | None = None
