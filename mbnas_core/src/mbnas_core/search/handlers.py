"""PROPOSE_GENERATION job handler (runs in the worker child; never in the API process).

``propose_generation(spec, emit)`` contract:

* ``spec.kind == PROPOSE_GENERATION``; ``spec.arm`` in {GA, IndependentRandom}; ``spec.search_seed`` set.
* ``spec.params``: ``protocol`` (resolved protocol dict; its ``protocol_hash`` must equal ``spec.protocol_hash``),
  ``generation`` (int >= 0); GA generations >= 1 also need ``parent_pool`` (list of committed L0 evaluation rows with
  ``candidate_id, evaluation_id, rung, status, fitness, latency_ms`` and the candidate ``genotype``). Optional:
  ``nc`` (else the class count of ``console.paths.dataset_yaml``), ``imgsz`` (else
  ``training.requested_input_size``), ``families``, ``architecture_dir`` (absolute dir inside the output allowlist;
  each candidate YAML is written there atomically), ``etri_root``.
* Generation 0 of both arms and every IndependentRandom generation sample the frozen prior; the Random path never
  reads ``parent_pool``. GA uses ``console.search.mutation_rate`` and the resolved ``console.search.crossover_rate``
  (legacy alias ``console.extra.ga.crossover_rate`` only when the search field is absent, with a warning; else
  :data:`~mbnas_core.search.ga.DEFAULT_CROSSOVER_RATE`; the source is recorded).
* Emits ProgressMessage lines (``log`` per candidate, then ``result`` carrying the JobResult) and returns the JobResult.
  ``outputs.candidates[i].row`` holds exactly the ``db.repo.insert_candidate`` keyword arguments (minus ``run_id``).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec, JobStatus, ProgressMessage
from mbnas_contracts.util.time import utcnow_iso

Emit = Callable[[ProgressMessage], None]
ARMS = ("GA", "IndependentRandom")


class HandlerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require(cond: bool, code: str, message: str) -> None:
    if not cond:
        raise HandlerError(code, message)


_SPACES: dict[tuple[Any, ...], Any] = {}


def _nc(params: dict[str, Any], protocol: dict[str, Any]) -> int:
    if isinstance(params.get("nc"), int) and params["nc"] >= 1:
        return int(params["nc"])
    from mbnas_model_runtime.datasets.yaml_safe import DatasetYamlError, load_dataset_yaml

    path = ((protocol.get("console") or {}).get("paths") or {}).get("dataset_yaml")
    _require(isinstance(path, str), "INVALID_JOB", "nc not given and protocol has no console.paths.dataset_yaml")
    try:
        return len(load_dataset_yaml(path).names)
    except DatasetYamlError as exc:
        raise HandlerError("DATASET_YAML_INVALID", str(exc)) from exc


def shared_space(*, nc: int, imgsz: int, families: tuple[str, ...], etri_root: str | None,
                 allow_native_fallback: bool = False) -> Any:
    """Process-wide SearchSpace per (nc, imgsz, families, etri_root, allow_native_fallback); immutable once created.

    Without ``allow_native_fallback`` (protocol ``console.search.allow_native_fallback``) an unloadable ETRI pool
    raises ``SearchSpaceUnavailable`` (SEARCH_SPACE_UNAVAILABLE) instead of silently using the native space.
    """
    from mbnas_core.search.space import SearchSpace

    key = (nc, imgsz, families, etri_root, bool(allow_native_fallback))
    if key not in _SPACES:
        _SPACES[key] = SearchSpace.create(nc=nc, imgsz=imgsz, families=families, etri_root=etri_root,
                                          allow_native_fallback=bool(allow_native_fallback))
    return _SPACES[key]


def _imgsz(params: dict[str, Any], protocol: dict[str, Any]) -> int:
    if isinstance(params.get("imgsz"), int):
        return int(params["imgsz"])
    size = (protocol.get("training") or {}).get("requested_input_size")
    _require(isinstance(size, list) and len(size) == 2 and size[0] == size[1], "INVALID_JOB",
             "search space needs a square training.requested_input_size")
    return int(size[0])


def ga_rates(protocol: dict[str, Any]) -> dict[str, Any]:
    """Mutation rate from ``console.search``; crossover rate from the resolved ``console.search.crossover_rate``
    (legacy alias ``console.extra.ga.crossover_rate`` only when absent, with a warning; else the documented default)."""
    from mbnas_core.search.ga import DEFAULT_CROSSOVER_RATE

    def ok(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1

    console = protocol.get("console") or {}
    search = console.get("search") or {}
    cr, legacy = search.get("crossover_rate"), (((console.get("extra") or {}).get("ga") or {}).get("crossover_rate"))
    warning = None
    if ok(cr):
        rate, source = float(cr), "console.search.crossover_rate"
        if legacy is not None and legacy != cr:
            warning = "legacy console.extra.ga.crossover_rate ignored: console.search.crossover_rate is set"
    elif ok(legacy):
        rate, source = float(legacy), "console.extra.ga.crossover_rate (legacy alias)"
        warning = "legacy alias console.extra.ga.crossover_rate used; set console.search.crossover_rate instead"
    else:
        rate, source = DEFAULT_CROSSOVER_RATE, "mbnas default (protocol defines no crossover rate)"
    return {"mutation_rate": float(search.get("mutation_rate", 0.0)),
            "mutation_rate_source": "console.search.mutation_rate",
            "crossover_rate": rate, "crossover_rate_source": source, "crossover_rate_warning": warning,
            "parent_rung": str(((protocol.get("proposal") or {}).get("GA") or {}).get("parent_selection_fidelity", "L0"))}


def _architecture_dir(params: dict[str, Any], settings: Any) -> Path | None:
    target = params.get("architecture_dir")
    if target is None:
        return None
    from mbnas_core.config import Settings
    from mbnas_contracts.util.paths import check_server_path

    settings = settings or Settings.load()
    chk = check_server_path(str(target), kind="dir", allow_roots=list(settings.output_roots), must_exist=False,
                            writable=True)
    _require(chk.ok, chk.code or "PATH_OUTSIDE_ALLOWLIST", chk.message or "architecture_dir rejected")
    return Path(chk.realpath)


def _proposals(spec: JobSpec, protocol: dict[str, Any], space: Any, generation: int) -> tuple[list[Any], dict | None]:
    from mbnas_core.search import ga, random as random_arm
    from mbnas_core.search.streams import search_rng

    population = int(((protocol.get("console") or {}).get("search") or {}).get("population", 0))
    _require(population >= 1, "INVALID_JOB", "console.search.population must be >= 1")
    rng = search_rng(int(spec.search_seed), str(spec.arm), generation)  # type: ignore[arg-type]
    if generation == 0 or spec.arm == "IndependentRandom":
        return random_arm.propose_generation(space, rng, population), None
    pool_raw = spec.params.get("parent_pool")
    _require(isinstance(pool_raw, list), "PARENT_POOL_INVALID", "GA generation >= 1 requires params.parent_pool")
    rates = ga_rates(protocol)
    cfg = ga.GAConfig(population=population, mutation_rate=rates["mutation_rate"],
                      crossover_rate=rates["crossover_rate"], parent_rung=rates["parent_rung"])
    try:
        pool = [ga.ParentRow.from_dict(r) for r in pool_raw]
        children, record = ga.propose_generation(space, pool, rng, cfg)
    except (ga.ParentPoolError, KeyError, ValueError) as exc:
        raise HandlerError("PARENT_POOL_INVALID", f"{type(exc).__name__}: {exc}") from exc
    return children, {**rates, "population": population, "elitism": 1, "tournament_size": 2, "selection": record}


def _candidate(spec: JobSpec, prop: Any, space: Any, desc: dict[str, Any], generation: int, index: int,
               seed_policy: dict[str, Any], arch_dir: Path | None, *, structure: dict[str, Any] | None = None) -> dict[str, Any]:
    """Candidate row + proposal metadata.

    ``structure`` is an already computed structure report of this very proposal (the generation-only path checks it
    before accepting a draw); it is reused instead of checking again. Otherwise ``check_structure`` runs once with the
    proposer's ``BuildReport`` (``report=prop.report``), which it reuses when the input size matches, so the model is
    never built a second time on the CPU. A non-PASS report makes the candidate INVALID and records
    ``reason_code`` (STRUCTURE_FAILED, or STRUCTURE_UNVERIFIED when the check could not run) next to the diagnostics
    inside ``audit.structure``.
    """
    from mbnas_core.search.lineage import audit_for, blocks_for, lineage_for
    from mbnas_core.search.streams import candidate_id, candidate_name, training_seed
    from mbnas_contracts.util.atomic import atomic_write_bytes

    cid = candidate_id(spec.run_id, str(spec.arm), int(spec.search_seed), generation, index)  # type: ignore[arg-type]
    text = prop.genotype.yaml_text()
    path = None
    if arch_dir is not None:
        path = str(atomic_write_bytes(arch_dir / (cid.replace(":", "_") + ".yaml"), text.encode("utf-8")))
    audit = audit_for(prop, space)
    from mbnas_core.search.structure import check_structure
    report = structure if structure is not None else check_structure(
        prop.genotype, input_size=space.imgsz, class_count=space.nc, report=prop.report)
    status = report.get("status", "UNVERIFIED")
    if status != "PASS":
        report = {**report, "diagnostics": list(report.get("diagnostics") or []),
                  "reason_code": "STRUCTURE_FAILED" if status == "FAIL" else "STRUCTURE_UNVERIFIED"}
    audit["structure"] = report
    lineage = lineage_for(prop, space, desc)
    built = audit.get("built") or {}
    row = {"id": cid, "name": candidate_name(str(spec.arm), generation, index), "arm": spec.arm,
           "family": f"NAS:{built.get('backbone_family', 'unknown')}", "generation": generation,
           "search_seed": spec.search_seed, "training_seed": training_seed(seed_policy, generation, index),
           "architecture_hash": prop.report.architecture_hash, "architecture_yaml": text, "architecture_path": path,
           "parents": lineage["parents"], "parent_evaluation_ids": lineage["parent_evaluation_ids"],
           "lineage": lineage, "blocks": blocks_for(prop, space),
           "candidate_status": "BUILD_VALID" if audit["structure"]["status"] == "PASS" else "INVALID",
           "audit": audit}
    return {"row": row, "genotype": prop.genotype.to_json(), "yaml_hash": prop.genotype.yaml_hash(),
            "params": prop.report.params, "flops": prop.report.flops}


def _run(spec: JobSpec, emit: Emit, settings: Any) -> dict[str, Any]:
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_core.search.space import DEFAULT_FAMILIES, RepairFailed
    from mbnas_core.search.streams import search_stream_key, training_seed_policy

    _require(spec.kind == JobKind.PROPOSE_GENERATION, "INVALID_JOB", f"handler got job kind {spec.kind}")
    _require(spec.arm in ARMS, "INVALID_JOB", f"arm must be one of {ARMS}")
    _require(spec.search_seed is not None, "INVALID_JOB", "search_seed is required")
    protocol, generation = spec.params.get("protocol"), spec.params.get("generation")
    _require(isinstance(protocol, dict), "INVALID_JOB", "params.protocol (resolved protocol) is required")
    _require(isinstance(generation, int) and generation >= 0, "INVALID_JOB", "params.generation must be an int >= 0")
    actual = protocol_hash(protocol)
    _require(actual == spec.protocol_hash, "PROTOCOL_HASH_MISMATCH",
             f"job protocol_hash {spec.protocol_hash} != hash of params.protocol {actual}")
    arch_dir = _architecture_dir(spec.params, settings)
    from mbnas_core.search.mixed_pool.bridge import SearchSpaceUnavailable

    search_cfg = (protocol.get("console") or {}).get("search") or {}
    want_variant = (search_cfg.get("search_space_resolved") or {}).get("variant")
    try:
        space = shared_space(nc=_nc(spec.params, protocol), imgsz=_imgsz(spec.params, protocol),
                             families=tuple(spec.params.get("families") or DEFAULT_FAMILIES),
                             etri_root=spec.params.get("etri_root"),
                             allow_native_fallback=search_cfg.get("allow_native_fallback") is True)
        _require(want_variant in (None, space.variant), "SEARCH_SPACE_UNAVAILABLE",
                 f"validated search space variant {want_variant!r} but this worker built {space.variant!r} "
                 f"({space.fallback_reason})")
        proposals, ga_record = _proposals(spec, protocol, space, generation)
    except (RepairFailed, SearchSpaceUnavailable) as exc:
        raise HandlerError("SEARCH_SPACE_UNAVAILABLE", str(exc)) from exc
    desc = space.describe()
    seed_policy = training_seed_policy(protocol, int(spec.search_seed))  # type: ignore[arg-type]
    candidates = []
    for index, prop in enumerate(proposals):
        cand = _candidate(spec, prop, space, desc, generation, index, seed_policy, arch_dir)
        candidates.append(cand)
        emit(ProgressMessage(type="log", job_id=spec.job_id, ts=utcnow_iso(),
                             message=f"{cand['row']['name']} {prop.operation} {cand['row']['architecture_hash']}",
                             payload={"candidate_id": cand["row"]["id"], "index": index}))
    return {"generation": generation, "arm": spec.arm, "search_seed": spec.search_seed,
            "protocol_hash": spec.protocol_hash, "planned_count": len(candidates),
            "rng_stream": search_stream_key(int(spec.search_seed), str(spec.arm), generation),  # type: ignore[arg-type]
            "space": desc, "training_seed_policy": seed_policy, "ga": ga_record,
            "initial_population_policy": "generation 0 of both arms = shared frozen-prior sample per search seed",
            "candidates": candidates}


def propose_generation(spec: JobSpec, emit: Emit | None = None, *, settings: Any = None) -> JobResult:
    """Run one PROPOSE_GENERATION job; failures become FAILED results with a reason code (never raise)."""
    emit = emit or (lambda _msg: None)
    started, t0 = utcnow_iso(), time.monotonic()
    try:
        cfg = (((spec.params.get("protocol") or {}).get("console") or {}).get("search") or {})
        if cfg.get("evaluation_enabled", True) is False:
            from mbnas_core.search.generation import generate
            outputs = generate(spec, emit, settings=settings)
        else:
            outputs = _run(spec, emit, settings)
        result = JobResult(job_id=spec.job_id, status=JobStatus.SUCCEEDED, attempt=spec.attempt, exit_code=0,
                           outputs=outputs, started_at=started, finished_at=utcnow_iso())
    except Exception as exc:
        from mbnas_contracts.jobs_protocol import JobFailure, JobPaused
        if isinstance(exc, (JobPaused, JobFailure)):
            raise
        if not isinstance(exc, HandlerError):
            import traceback
            traceback.print_exc()
            result = JobResult(job_id=spec.job_id, status=JobStatus.FAILED, attempt=spec.attempt, exit_code=1,
                               reason_code="INTERNAL_ERROR", message="search worker failed; inspect server logs",
                               started_at=started, finished_at=utcnow_iso())
        else:
            result = JobResult(job_id=spec.job_id, status=JobStatus.FAILED, attempt=spec.attempt, exit_code=1,
                               reason_code=exc.code, message=str(exc), started_at=started, finished_at=utcnow_iso())
    result.outputs.setdefault("cpu_seconds", round(time.monotonic() - t0, 3))
    emit(ProgressMessage(type="result", job_id=spec.job_id, ts=utcnow_iso(), payload=result.model_dump(mode="json")))
    return result
