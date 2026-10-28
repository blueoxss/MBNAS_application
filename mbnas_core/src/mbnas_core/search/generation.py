"""Evaluation-free random generation, distinct from the evaluated IndependentRandom arm.

Only the existing frozen-prior proposer and structure service are used. A durable, atomically replaced journal
records each completed draw before a progress notification. Replaying the same job resumes its RNG stream and
candidate IDs. No trainer, evaluator, benchmark, fitness, promotion or final-retrain module is imported here.

Duplicate policy: ``duplicate_scope`` is ``per_seed``. A repeated architecture hash is rejected against the
candidates of the *same* seed journal only; seeds never read each other's journals (seed independence, replay).

Test-only hook: when the environment variable ``MBNAS_TEST_GENERATION_SLEEP_S`` is set in the generation child (a
number of seconds, capped at 30), the loop sleeps that long after every durable save and progress notification. It exists
solely so tests can SIGKILL a real worker in the middle of a generation job at a deterministic point; production runs
never set it and its value never influences sampling, the journal or the result.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable

from mbnas_contracts.schemas.jobs import JobSpec, ProgressMessage
from mbnas_contracts.util.atomic import atomic_write_bytes
from mbnas_contracts.util.time import utcnow_iso
from mbnas_contracts.jobs_protocol import JobFailure, JobPaused

METHOD = "RandomGeneration"
DUPLICATE_SCOPE = "per_seed"
TEST_SLEEP_ENV = "MBNAS_TEST_GENERATION_SLEEP_S"


def _test_sleep() -> None:
    """Env-gated test hook (see module docstring); a no-op unless the variable holds a positive number."""
    raw = os.environ.get(TEST_SLEEP_ENV)
    if not raw:
        return
    try:
        seconds = min(float(raw), 30.0)
    except ValueError:
        return
    if seconds > 0:
        time.sleep(seconds)


def _worker_id(spec: JobSpec) -> str | None:
    """Id of the worker running this child: the spec when it names one, else the worker-provided environment."""
    value = spec.params.get("worker_id") or os.environ.get("MBNAS_WORKER_ID")
    return str(value) if value else None


def _tuple(value: Any) -> Any:
    return tuple(_tuple(v) for v in value) if isinstance(value, list) else value


def _summary(state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    count = len(state["candidates"])
    return {"evaluation_enabled": False, "requested_method": cfg.get("requested_method", cfg.get("arms", [])),
            "effective_method": METHOD, "seed": state["seed"], "requested_count": cfg["candidate_count"],
            "generated_count": count, "attempts": state["attempts"], "duplicates": state["duplicates"],
            "max_attempts": cfg["max_attempts"], "allow_duplicates": cfg["allow_duplicates"],
            "duplicate_scope": DUPLICATE_SCOPE, "failure_counts": state["failure_counts"], "diagnostics": state["diagnostics"],
            "complete": count == cfg["candidate_count"], "partial": count < cfg["candidate_count"],
            "status": "COMPLETE" if count == cfg["candidate_count"] else "PARTIAL",
            "performance_status": "NOT_EVALUATED"}


def _notify(emit: Callable[[ProgressMessage], None], spec: JobSpec, state: dict[str, Any], cfg: dict[str, Any],
            candidate: dict[str, Any] | None = None) -> None:
    emit(ProgressMessage(type="progress", job_id=spec.job_id, ts=utcnow_iso(),
                         payload={"generation_summary": _summary(state, cfg), "generated_candidate": candidate}))


def _stopping(spec: JobSpec) -> bool:
    path = spec.params.get("stop_request_path")
    return isinstance(path, str) and Path(path).exists()


def generate(spec: JobSpec, emit: Callable[[ProgressMessage], None], *, settings: Any = None,
             space_factory: Any = None, structure_check: Any = None) -> dict[str, Any]:
    """Generate in deterministic draw order; injectable boundaries are used only by CPU unit tests."""
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_core.search.handlers import _architecture_dir, _candidate, shared_space
    from mbnas_core.search.random import propose_generation
    from mbnas_core.search.space import RepairFailed
    from mbnas_core.search.streams import training_seed_policy
    from mbnas_core.search.structure import check_structure

    protocol = spec.params.get("protocol") or {}
    cfg = ((protocol.get("console") or {}).get("search") or {})
    if cfg.get("evaluation_enabled", True) is not False or spec.params.get("generation_only") is not True:
        raise JobFailure("INVALID_GENERATION_MODE", "generation-only handler requires evaluation_enabled=false")
    if protocol_hash(protocol) != spec.protocol_hash:
        raise JobFailure("PROTOCOL_HASH_MISMATCH", "generation job protocol hash does not match frozen configuration")
    if spec.search_seed is None:
        raise JobFailure("INVALID_JOB", "generation seed is required")
    arch_dir = _architecture_dir(spec.params, settings)
    if arch_dir is None:
        raise JobFailure("INVALID_JOB", "generation requires a durable architecture directory")
    arch_dir.mkdir(parents=True, exist_ok=True)
    state_path = arch_dir / f"generation-{spec.search_seed}.json"
    lock_path = arch_dir / f"generation-{spec.search_seed}.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise JobFailure("GENERATION_BUSY", "another attempt owns the generation journal", retryable=True) from exc
        rng = random.Random(int(spec.search_seed))
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if state["protocol_hash"] != spec.protocol_hash or state["run_id"] != spec.run_id:
                    raise ValueError("journal identity mismatch")
                rng.setstate(_tuple(state["rng_state"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise JobFailure("GENERATION_STATE_INVALID", "generation journal is corrupt or belongs to another configuration") from exc
        else:
            state = {"version": 1, "run_id": spec.run_id, "protocol_hash": spec.protocol_hash,
                     "seed": int(spec.search_seed), "attempts": 0, "duplicates": 0, "failure_counts": {},
                     "diagnostics": [], "candidates": [], "rng_state": rng.getstate()}
        def save() -> None:
            state["rng_state"] = rng.getstate()
            atomic_write_bytes(state_path, json.dumps(state, allow_nan=False, ensure_ascii=False).encode())
        # Replay notifications too: a crashed worker may have saved the journal before receiving the event.
        for candidate in state["candidates"]:
            _notify(emit, spec, state, cfg, candidate)
        save()
        if len(state["candidates"]) >= cfg["candidate_count"] or state["attempts"] >= cfg["max_attempts"]:
            return {"candidates": state["candidates"], "generation_summary": _summary(state, cfg)}
        if _stopping(spec):
            raise JobPaused(outputs={"candidates": state["candidates"], "generation_summary": _summary(state, cfg)})
        factory = space_factory or shared_space
        try:
            space = factory(nc=cfg["nc"], imgsz=cfg["input_size"], families=tuple(cfg["families"]),
                            etri_root=spec.params.get("etri_root"), allow_native_fallback=cfg.get("allow_native_fallback") is True)
        except Exception as exc:
            # Capability failure is distinct from an architecture FAIL; never produce PASS candidates.
            summary = _summary(state, cfg)
            summary.update(status="UNVERIFIED", structure_status="UNVERIFIED",
                           diagnostics=[{"code": "STRUCTURE_RUNTIME_UNAVAILABLE", "message": "model structure runtime or search space is unavailable"}])
            raise JobFailure("STRUCTURE_RUNTIME_UNAVAILABLE", "cannot execute structure checks in this worker environment",
                             outputs={"candidates": state["candidates"], "generation_summary": summary}) from exc
        desc = space.describe()
        checker = structure_check or check_structure
        policy = training_seed_policy(protocol, int(spec.search_seed))
        seen = {c["row"]["architecture_hash"] for c in state["candidates"]}
        while len(state["candidates"]) < cfg["candidate_count"] and state["attempts"] < cfg["max_attempts"]:
            if _stopping(spec):
                save()
                raise JobPaused(outputs={"candidates": state["candidates"], "generation_summary": _summary(state, cfg)})
            state["attempts"] += 1
            candidate = None
            try:
                # Preserve the existing frozen-prior random proposer; this path never requests GA parents.
                prop = propose_generation(space, rng, 1)[0]
                report = (checker(prop.genotype, input_size=cfg["input_size"], class_count=cfg["nc"], report=prop.report)
                          if structure_check is None else checker(prop.genotype, input_size=cfg["input_size"], class_count=cfg["nc"]))
                status = report.get("status", "UNVERIFIED")
                if status != "PASS":
                    code = "STRUCTURE_UNVERIFIED" if status == "UNVERIFIED" else "STRUCTURE_FAILED"
                    state["failure_counts"][code] = state["failure_counts"].get(code, 0) + 1
                    state["diagnostics"] = (state["diagnostics"] + [{"attempt": state["attempts"], "status": status,
                        "diagnostics": report.get("diagnostics", [])}])[-50:]
                elif not cfg["allow_duplicates"] and prop.report.architecture_hash in seen:
                    state["duplicates"] += 1
                else:
                    index = len(state["candidates"])
                    candidate = _candidate(spec, prop, space, desc, 0, index, policy, arch_dir, structure=report)
                    audit = candidate["row"]["audit"]
                    audit.update(origin="random_generation", structure=report,
                                 performance={"status": "NOT_EVALUATED", "ap": None, "recall": None,
                                              "latency_ms": None, "fitness": None},
                                 generation={"index": index, "attempt": state["attempts"], "seed": spec.search_seed,
                                             "requested_method": cfg.get("requested_method", cfg.get("arms", [])),
                                             "effective_method": METHOD, "duplicate_scope": DUPLICATE_SCOPE,
                                             "execution": "CPU", "worker_id": _worker_id(spec)})
                    candidate["row"]["name"] = f"Random candidate {index + 1}"
                    candidate["row"]["training_seed"] = None
                    candidate["evaluation_status"] = "NOT_EVALUATED"
                    state["candidates"].append(candidate)
                    seen.add(prop.report.architecture_hash)
            except RepairFailed:
                state["failure_counts"]["REPAIR_FAILED"] = state["failure_counts"].get("REPAIR_FAILED", 0) + 1
                state["diagnostics"] = (state["diagnostics"] + [{"attempt": state["attempts"], "status": "FAIL",
                    "diagnostics": [{"code": "REPAIR_FAILED", "message": "sample could not satisfy existing structure rules"}]}])[-50:]
            save()  # RNG and accepted YAML identities are durable before any consumer sees this draw.
            _notify(emit, spec, state, cfg, candidate)
            _test_sleep()
        return {"candidates": state["candidates"], "generation_summary": _summary(state, cfg), "space": desc}
