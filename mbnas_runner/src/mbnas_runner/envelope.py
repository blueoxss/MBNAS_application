"""JobEnvelope (``envelope_version`` 1) -> the JobSpec file the runner child runs.

The core sends what the owner child used to read from the DB as one JSON envelope; this module rebuilds the
same handler parameters on the runner host without a database:

* generic params = ``job.spec.params`` + ``protocol``, ``run_dir`` (``<work_root>/runs/<run_id>``, a runner-local path:
  the envelope carries no path), ``resolved_protocol_path``, ``work_dir``, ``stop_request_path``, ``attempt`` and, for
  training kinds, ``resume_from`` (= ``resume.recorded_latest``) - exactly the keys of the owner child's
  ``load_context``;
* native params = :func:`native_params` (the owner child's ``native_params``/``search_arch`` bodies, unchanged except
  that the baseline ArchSpec is not resolved here: the core injects it as ``job.spec.params.arch`` and a
  Baseline candidate without it reaches ``resolve_model``, which fails with ``ARCH_REQUIRED`` - never silently).

Nothing here imports the core (the runner never does).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobSpec

ENVELOPE_VERSION = 1
MIXED_HOOK = "mbnas_model_runtime.operators:ensure_registered"
CHECKPOINT_KEYS = ("id", "path", "sha256", "epoch", "rung", "hashes")
_TRAINING = {k.value for k in TRAINING_KINDS}


def safe_name(candidate_id: str) -> str:
    """Filesystem-safe directory name of a candidate id (same rule as the child's)."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in candidate_id)


def run_dir_for(work_root: str | Path, run_id: str) -> Path:
    """``<work_root>/runs/<run_id>``; the run id must be one safe path component."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", "..") or "\x00" in run_id:
        raise JobFailure("RUN_ID_INVALID", f"run id {run_id!r} is not a safe path component")
    return Path(work_root) / "runs" / run_id


def stop_path_for(run_dir: Path, job_id: str) -> Path:
    """``<run_dir>/control/<job_id>.stop`` (the file the child's Emitter polls)."""
    return run_dir / "control" / f"{safe_name(job_id)}.stop"


def _checkpoint(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or not entry.get("path"):
        return None
    return {"id": entry.get("id"), "path": entry["path"], "sha256": entry.get("sha256"), "epoch": entry.get("epoch"),
            "rung": entry.get("rung"), "hashes": entry.get("hashes") or {}}


def candidate_row(env: dict[str, Any]) -> dict[str, Any] | None:
    """The candidate fields ``search_arch``/``native_params`` read, in the owner child's row shape."""
    cand = env.get("candidate")
    if not isinstance(cand, dict) or not cand.get("candidate_id"):
        return None
    return {"id": cand["candidate_id"], "arm": cand.get("arm"), "family": cand.get("family"),
            "architecture_yaml": cand.get("architecture_yaml"),
            "audit": {"proposal": {"genotype": cand.get("genotype") or {}}}}


def search_arch(cand: dict[str, Any]) -> dict[str, Any]:
    """ArchSpec dict of a search candidate (owner child ``search_arch``, unchanged)."""
    import yaml

    from mbnas_model_runtime.operators import yaml_uses_mixed_modules

    geno = ((cand.get("audit") or {}).get("proposal") or {}).get("genotype") or {}
    body = geno.get("yaml")
    config = body if isinstance(body, dict) else yaml.safe_load(body or cand.get("architecture_yaml") or "")
    if not isinstance(config, dict):
        raise JobFailure("ARCHITECTURE_MISSING", f"candidate {cand['id']} has no architecture YAML")
    family = "YOLO26" if str(geno.get("family") or cand.get("family", "")).lower().find("yolo26") >= 0 else "YOLO11"
    return {"model_id": cand["id"], "family": family, "backend": "ultralytics", "config": config, "variant": "detect",
            "register_hook": MIXED_HOOK if yaml_uses_mixed_modules(config) else None}


def _baseline_arch(out: dict[str, Any], cand: dict[str, Any] | None, injected: Any) -> dict[str, Any]:
    """The ArchSpec the core resolved for a Baseline candidate (``job.spec.params.arch``)."""
    if out.get("arch") is not None or (cand or {}).get("arm") != "Baseline":
        return out
    return {**out, "arch": injected} if isinstance(injected, dict) else out


def native_params(job: dict[str, Any], run: dict[str, Any], cand: dict[str, Any] | None, generic: dict[str, Any],
                  eval_ckpt: str | None, *, injected_arch: Any = None) -> dict[str, Any]:
    """Owner child ``native_params`` with the baseline ArchSpec taken from the envelope (see module docstring)."""
    kind, mine = job["kind"], generic
    cmeta = (mine.get("candidate") or {})
    training_cfg = (run.get("resolved_protocol", {}).get("console", {}).get("extra") or {}).get("training") or {}
    explicit = training_cfg.get("enabled") is True
    if kind in _TRAINING or kind == JobKind.EVALUATE.value:
        out: dict[str, Any] = {"run_dir": mine["run_dir"], "resolved_protocol": run["resolved_protocol"],
                               "adapter_id": cmeta.get("baseline_id") or (cand or {}).get("id") or job.get("candidate_id"),
                               "arch": None if (cand or {}).get("arm") == "Baseline" or cand is None else search_arch(cand),
                               "device": "cuda:0"}
        if explicit:
            out["arch"] = training_cfg.get("arch") if training_cfg.get("source", {}).get("kind") != "baseline" or training_cfg.get("custom_initial_weights") else None
            out["device"] = training_cfg.get("device", "cuda:0")
        if kind == JobKind.EVALUATE.value:
            if explicit and training_cfg.get("mode") == "evaluate_only":
                return out
            if eval_ckpt is None:
                raise JobFailure("EVAL_CHECKPOINT_MISSING", "no eval_ema checkpoint recorded for this candidate")
            return {**_baseline_arch(out, cand, injected_arch), "eval_ckpt": eval_ckpt,
                    "eval_split": mine.get("eval_split") or "search_val"}
        added = (mine.get("stage") or {}).get("epochs_added")
        return {**_baseline_arch(out, cand, injected_arch), "init": cmeta.get("init"), "stop_request_path": mine["stop_request_path"], "evaluate": training_cfg.get("validation_enabled", True) if explicit else True,
                "resume_from": mine.get("live_resume_from"),
                "stage_epochs_added": int(added) if isinstance(added, int) and added >= 1 else None}
    if kind == JobKind.PROPOSE_GENERATION.value:
        raise JobFailure("KIND_NOT_ALLOWED", "PROPOSE_GENERATION is owner work and never reaches a runner (DD-A4)")
    return {**{k: v for k, v in job["spec"].get("params", {}).items()}, "run_dir": mine["run_dir"]}


def check_envelope(env: Any) -> dict[str, Any]:
    """The envelope when it validates as ``mbnas_contracts.runner_protocol.JobEnvelope`` (version 1, extra fields
    forbidden, fence attempt == job attempt, spec consistent with job/run); ``JobFailure`` otherwise
    (``VERSION_MISMATCH`` for another ``envelope_version``, ``ENVELOPE_INVALID`` for anything else)."""
    from mbnas_contracts.runner_protocol import JobEnvelope

    if not isinstance(env, dict):
        raise JobFailure("ENVELOPE_INVALID", "claim response carries no envelope object")
    if env.get("envelope_version") != ENVELOPE_VERSION:
        raise JobFailure("VERSION_MISMATCH", f"envelope_version {env.get('envelope_version')!r} is not "
                         f"{ENVELOPE_VERSION}")
    try:
        JobEnvelope.model_validate(env)
    except ValidationError as exc:
        raise JobFailure("ENVELOPE_INVALID", f"envelope does not match JobEnvelope v1: {exc}"[:2000]) from exc
    return env


def spec_from_envelope(env: dict[str, Any], work_root: str | Path, *, native: bool = True) -> JobSpec:
    """The child's JobSpec (generic or native params) for envelope ``env`` on a runner whose work root is
    ``work_root``; raises ``JobFailure`` exactly where the owner child's ``load_context`` would."""
    env = check_envelope(env)
    job_row, run_row = env["job"], env["run"]
    job_id, kind, attempt = str(job_row["job_id"]), str(job_row["kind"]), int(job_row["attempt"] or 1)
    spec = dict(job_row["spec"])
    run_id = str(run_row["run_id"])
    base = run_dir_for(work_root, run_id)
    cand = candidate_row(env)
    cid = spec.get("candidate_id") or (cand or {}).get("id")
    resume = env.get("resume") or {}
    params: dict[str, Any] = dict(spec.get("params") or {})
    injected_arch = params.pop("arch", None) if native and (cand or {}).get("arm") == "Baseline" else None
    params.update({
        "protocol": run_row["resolved_protocol"], "run_dir": str(base),
        "resolved_protocol_path": str(base / "protocol" / "resolved.json"),
        "work_dir": str(base / "candidates" / safe_name(cid)) if cid else str(base / "jobs" / job_id),
        "stop_request_path": str(stop_path_for(base, job_id)), "attempt": attempt})
    if kind in _TRAINING and cid:
        params["resume_from"] = _checkpoint(resume.get("recorded_latest"))
    if native:
        if kind in _TRAINING and cid:
            params["live_resume_from"] = _checkpoint(resume.get("live_latest"))
        ev = resume.get("eval_ema") if cid else None
        job = {"id": job_id, "kind": kind, "candidate_id": cid, "spec": spec}
        params = native_params(job, {"resolved_protocol": run_row["resolved_protocol"]}, cand, params,
                               ev.get("path") if isinstance(ev, dict) and ev.get("path") else None,
                               injected_arch=injected_arch)
    try:
        return JobSpec.model_validate({**spec, "attempt": attempt, "params": params})
    except ValidationError as exc:
        raise JobFailure("SPEC_INVALID", f"envelope job spec is invalid: {exc}"[:2000]) from exc
