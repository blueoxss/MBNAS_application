"""DB rows -> JobEnvelope v1 (WP-2 contract section 1.3, design A3 section 4) and the job-context helpers moved here
from ``owner_worker.child`` (``search_arch``, ``baseline_arch``, ``native_params``, ``latest_live_full_state``,
``load_context``; bodies unchanged, R7).

The owner worker child (PROPOSE_GENERATION, and stub-handler test runs) still builds its JobSpec with
:func:`load_context` from a read-only DB connection. A remote runner never sees the DB: ``claim`` answers with
:func:`build_envelope`, which reads the same rows (job, run, candidate, checkpoints) inside the caller's read
transaction and serialises them as :class:`mbnas_contracts.runner_protocol.JobEnvelope`:

* ``job.spec`` is the stored JobSpec without ``attempt`` and with ``params`` filtered (design 4.3): sent keys are
  ``candidate, evaluate_inline, stage{id, epoch_target_total, epochs_added}, baseline, eval_split, final,
  training_job_id`` (+ ``arch``, below); ``admission``, ``stage.fitness_weights`` and ``training_outputs`` are never
  sent (DD-A1(a)/DD-A5). A BENCHMARK job (not created by the core today) keeps its params minus those three keys.
* DD-A16: for a ``Baseline``-arm candidate the core resolves the registry entry (``BASELINE_UNKNOWN`` /
  ``BASELINE_UNAVAILABLE`` unless AVAILABLE) and injects ``params.arch`` = ``arch_spec(...)``; the runner never reads
  the core registry. Explicit-training runs and the official ``evaluate_only`` path keep their protocol ArchSpec
  (native_params decides, exactly as in the child).
* ``resume`` mirrors the child's D4/D5/D6 reads (``recorded_latest`` = latest ``training_full`` incl. PRUNED,
  ``live_latest`` = latest non-PRUNED ``training_full``, ``eval_ema``); ``path`` is the runner-local location the
  registering runner reported. ``resume.location`` names the runner holding the bytes (affinity is not applied, D5).
* ``data_refs`` copies the protocol's ``console.paths`` roots and frozen manifest hashes (the runner re-maps them with
  its ``dataset_mounts``); ids are the internal ids (design 4.4 / D1).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.schemas.jobs import TRAINING_KINDS, JobKind, JobSpec
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.config import CoreSettings
from mbnas_core.db import repo

MIXED_HOOK = "mbnas_model_runtime.operators:ensure_registered"
TRAIN_PARAM_KEYS = ("run_dir", "adapter_id", "resolved_protocol", "arch", "init", "device", "stop_request_path",
                    "evaluate")
ENVELOPE_VERSION = 1
PROGRESS_VERSION = 1
CHECKPOINT_FORMAT = "mbnas.full_state.v1"
REQUIRED_RUNTIME = {"mbnas_model_runtime": ">=0.1,<0.2", "mbnas_contracts": ">=0.1,<0.2"}
SENT_PARAM_KEYS = ("candidate", "evaluate_inline", "stage", "baseline", "eval_split", "final", "training_job_id")
SENT_STAGE_KEYS = ("id", "epoch_target_total", "epochs_added")
NEVER_SENT_PARAM_KEYS = ("admission", "training_outputs")


def safe_name(candidate_id: str) -> str:
    """Filesystem-safe directory name of a candidate id (``:``/``/`` replaced)."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in candidate_id)


def search_arch(cand: dict[str, Any]) -> dict[str, Any]:
    """ArchSpec dict of a search candidate: its genotype YAML on the ultralytics backend (+ ETRI ops hook)."""
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


def baseline_arch(out: dict[str, Any], cand: dict[str, Any] | None, settings: Any = None) -> dict[str, Any]:
    """DD-A16 (WP-1 contract section 7): ``out`` with ``arch`` = the registry ArchSpec of ``out["adapter_id"]`` when the
    candidate is a ``Baseline``-arm candidate without an ``arch``; otherwise ``out`` unchanged.

    The registry status is checked first (messages of the former runner ``training.context.resolve_model``): an
    unknown id raises ``JobFailure("BASELINE_UNKNOWN")``, a non-AVAILABLE entry ``JobFailure("BASELINE_UNAVAILABLE")``.
    ``settings`` defaults to :meth:`CoreSettings.load` (the worker passes its settings through the child env).
    """
    if out.get("arch") is not None or (cand or {}).get("arm") != "Baseline":
        return out
    from mbnas_core.registry import SPEC_BY_ID, arch_spec, load_registry

    settings = settings or CoreSettings.load()
    adapter_id = out.get("adapter_id")
    if adapter_id not in SPEC_BY_ID:
        raise JobFailure("BASELINE_UNKNOWN", f"{adapter_id!r} is not a registry id")
    entry = load_registry(settings.output_root)[adapter_id]
    if entry["status"] != "AVAILABLE":
        raise JobFailure("BASELINE_UNAVAILABLE", f"{adapter_id} is {entry['status']}: {entry['reason']}")
    return {**out, "arch": arch_spec(adapter_id, settings.weights_dir, entry).as_dict()}


def native_params(job: dict[str, Any], run: dict[str, Any], cand: dict[str, Any] | None, generic: dict[str, Any],
                  eval_ckpt: str | None, *, settings: Any = None) -> dict[str, Any]:
    """Translate the orchestrator's params into the real handler's parameter model (see contract docstring); a
    registry baseline gets its ArchSpec from :func:`baseline_arch` (not for the official evaluate_only path, whose
    ArchSpec is frozen in the resolved protocol)."""
    kind, mine = job["kind"], generic
    cmeta = (mine.get("candidate") or {})
    training_cfg = (run.get("resolved_protocol", {}).get("console", {}).get("extra") or {}).get("training") or {}
    explicit = training_cfg.get("enabled") is True
    if kind in {k.value for k in TRAINING_KINDS} or kind == JobKind.EVALUATE.value:
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
            # arm Final evaluates the final split (the handler refuses it for any other arm)
            return {**baseline_arch(out, cand, settings), "eval_ckpt": eval_ckpt,
                    "eval_split": mine.get("eval_split") or "search_val"}
        added = (mine.get("stage") or {}).get("epochs_added")
        return {**baseline_arch(out, cand, settings), "init": cmeta.get("init"), "stop_request_path": mine["stop_request_path"], "evaluate": training_cfg.get("validation_enabled", True) if explicit else True,
                "resume_from": mine.get("live_resume_from"),
                "stage_epochs_added": int(added) if isinstance(added, int) and added >= 1 else None}
    if kind == JobKind.PROPOSE_GENERATION.value:
        return {k: v for k, v in mine.items() if k not in ("work_dir", "resume_from")}
    return {**{k: v for k, v in job["spec"].get("params", {}).items()}, "run_dir": mine["run_dir"]}


def latest_live_full_state(conn: sqlite3.Connection, run_id: str, candidate_id: str) -> dict[str, Any] | None:
    """Latest non-PRUNED ``training_full`` row of a candidate (the real handler's expected resume source)."""
    rows = [r for r in repo.list_checkpoints(conn, run_id, candidate_id=candidate_id, kind="training_full")
            if r.get("state") != "PRUNED"]
    if not rows:
        return None
    ck = max(rows, key=lambda r: (int(r["epoch"]), r["created_at"], r["id"]))
    return {"id": ck["id"], "path": ck["path"], "sha256": ck["sha256"], "epoch": ck["epoch"], "rung": ck["rung"],
            "hashes": ck.get("hashes") or {}}


def _read_only(db_path: str) -> sqlite3.Connection:
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = _sqlite3.Row
    return conn


def load_context(db_path: str, job_id: str, stop_path: str | None, *, native: bool = False,
                 settings: Any = None) -> JobSpec:
    """Load the job and return its spec with handler parameters (generic keys, or native ones for real handlers);
    ``settings`` (CoreSettings, default loaded from the environment when needed) resolves baseline ArchSpecs."""
    from mbnas_core.artifacts.registry import run_dir

    conn = _read_only(db_path)
    try:
        job = repo.get_job(conn, job_id)
        if job is None:
            raise JobFailure("JOB_NOT_FOUND", f"job {job_id} does not exist")
        run = repo.get_run(conn, job["run_id"])
        if run is None:
            raise JobFailure("RUN_NOT_FOUND", f"run {job['run_id']} does not exist")
        base = run_dir(run)
        cid = job.get("candidate_id")
        cand = repo.get_candidate(conn, cid) if cid else None
        params: dict[str, Any] = dict(job["spec"].get("params") or {})
        params.update({
            "protocol": run["resolved_protocol"], "run_dir": str(base),
            "resolved_protocol_path": str(base / "protocol" / "resolved.json"),
            "work_dir": str(base / "candidates" / safe_name(cid)) if cid else str(base / "jobs" / job_id),
            "stop_request_path": stop_path, "attempt": int(job["attempt"] or 1)})
        if job["kind"] == JobKind.PROPOSE_GENERATION.value:
            params.setdefault("architecture_dir", str(base / "architectures"))
        if job["kind"] in {k.value for k in TRAINING_KINDS} and cid:
            ck = repo.latest_checkpoint(conn, cid, "training_full")
            params["resume_from"] = None if ck is None else {
                "id": ck["id"], "path": ck["path"], "sha256": ck["sha256"], "epoch": ck["epoch"], "rung": ck["rung"],
                "hashes": ck.get("hashes") or {}}
        if native:
            if job["kind"] in {k.value for k in TRAINING_KINDS} and cid:
                params["live_resume_from"] = latest_live_full_state(conn, job["run_id"], cid)
            ev = repo.latest_checkpoint(conn, cid, "eval_ema") if cid else None
            params = native_params(job, run, cand, params, ev["path"] if ev else None, settings=settings)
        return JobSpec.model_validate({**job["spec"], "attempt": int(job["attempt"] or 1), "params": params})
    finally:
        conn.close()


# -- envelope builder ------------------------------------------------------------------------------------------------
def filter_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Design 4.3: the job params a runner may see (see module docstring)."""
    if kind == JobKind.BENCHMARK.value:
        out = {k: v for k, v in params.items() if k not in NEVER_SENT_PARAM_KEYS}
    else:
        out = {k: params[k] for k in SENT_PARAM_KEYS if k in params}
    if isinstance(out.get("stage"), dict):
        out["stage"] = {k: out["stage"][k] for k in SENT_STAGE_KEYS if k in out["stage"]}
    return out


def _ck_ref(ck: dict[str, Any] | None) -> dict[str, Any] | None:
    if ck is None:
        return None
    sha = str(ck.get("sha256") or "").removeprefix("sha256:")
    return {"id": ck["id"], "path": ck["path"], "sha256": sha, "epoch": int(ck.get("epoch") or 0), "rung": ck.get("rung"),
            "hashes": ck.get("hashes") or {}, "state": ck.get("state") or "PRESENT"}


def checkpoint_location(conn: sqlite3.Connection, checkpoint_id: str) -> dict[str, Any] | None:
    """Most recent ``artifact_locations`` row of a checkpoint (None for owner-local rows registered before WP-2)."""
    row = conn.execute("SELECT * FROM artifact_locations WHERE object_kind = 'checkpoint' AND object_id = ? "
                       "ORDER BY created_at DESC, id DESC LIMIT 1", (checkpoint_id,)).fetchone()
    return dict(row) if row is not None else None


def _data_refs(run: dict[str, Any], cand: dict[str, Any] | None, params: dict[str, Any]) -> dict[str, Any]:
    resolved = run.get("resolved_protocol") or {}
    paths = ((resolved.get("console") or {}).get("paths") or {})
    ds = resolved.get("dataset") or {}
    names = ds.get("names") if isinstance(ds.get("names"), dict) else {}
    out: dict[str, Any] = {}
    if paths.get("dataset_yaml"):
        out["dataset_yaml"] = {"protocol_path": str(paths["dataset_yaml"]), "names": {str(k): str(v) for k, v in names.items()}}
    final_allowed = (cand or {}).get("arm") == "Final" or params.get("eval_split") == "final_eval"
    for role, path_key, hash_key in (("train", "train_root", "train_manifest_hash"),
                                     ("search_val", "search_val_root", "search_validation_manifest_hash"),
                                     ("final_eval", "final_eval_root", "final_evaluation_manifest_hash")):
        if paths.get(path_key) and ds.get(hash_key):
            ref: dict[str, Any] = {"protocol_root": str(paths[path_key]), "manifest_hash": str(ds[hash_key])}
            if role == "final_eval":
                ref["allowed"] = bool(final_allowed)
            out[role] = ref
    return out


def build_envelope(conn: sqlite3.Connection, job: dict[str, Any], *, runner_id: str, settings: CoreSettings,
                   claim_id: str, core_contract: str, kill_grace_s: float = 10.0,
                   progress_interval_s: float = 1.0) -> dict[str, Any]:
    """JobEnvelope v1 (as a JSON dict validated by the contracts model) of a job leased by ``runner_id``.

    Raises :class:`JobFailure` (``BASELINE_UNKNOWN`` / ``BASELINE_UNAVAILABLE``) when the baseline cannot run; the
    caller turns that into a failed attempt instead of handing the job out.
    """
    from mbnas_contracts.runner_protocol import JobEnvelope

    run = repo.get_run(conn, job["run_id"])
    if run is None:
        raise JobFailure("RUN_NOT_FOUND", f"run {job['run_id']} does not exist")
    cid = job.get("candidate_id")
    cand = repo.get_candidate(conn, cid) if cid else None
    raw_params = dict(job["spec"].get("params") or {})
    params = filter_params(job["kind"], raw_params)
    # DD-A16: the core resolves a registry baseline (same decision as native_params/baseline_arch in the child)
    if cand is not None and cand.get("arm") == "Baseline" and job["kind"] != JobKind.BENCHMARK.value:
        training_cfg = ((run.get("resolved_protocol") or {}).get("console", {}).get("extra") or {}).get("training") or {}
        explicit = training_cfg.get("enabled") is True
        if not explicit:
            cmeta = params.get("candidate") or {}
            out = baseline_arch({"adapter_id": cmeta.get("baseline_id") or cand.get("id"), "arch": None}, cand, settings)
            params["arch"] = out["arch"]
    spec = {k: v for k, v in job["spec"].items() if k != "attempt"}
    spec["params"] = params
    resume: dict[str, Any] = {}
    if cid and job["kind"] in {k.value for k in TRAINING_KINDS}:
        resume["recorded_latest"] = _ck_ref(repo.latest_checkpoint(conn, cid, "training_full"))
        live = latest_live_full_state(conn, job["run_id"], cid)
        if live is not None:
            row = next((r for r in repo.list_checkpoints(conn, job["run_id"], candidate_id=cid, kind="training_full")
                        if r["id"] == live["id"]), None)
            resume["live_latest"] = _ck_ref(row)
    if cid:
        resume["eval_ema"] = _ck_ref(repo.latest_checkpoint(conn, cid, "eval_ema"))
    src = resume.get("live_latest") or resume.get("recorded_latest") or resume.get("eval_ema")
    if src is not None:
        loc = checkpoint_location(conn, src["id"])
        resume["location"] = ({"runner_id": loc.get("runner_id"), "store": loc["store"], "uri": None} if loc else
                              {"runner_id": None, "store": "core_local", "uri": None})
    candidate = None
    if cand is not None:
        geno = ((cand.get("audit") or {}).get("proposal") or {}).get("genotype")
        candidate = {"candidate_id": cand["id"], "name": cand.get("name"), "arm": cand.get("arm"),
                     "family": cand.get("family"), "generation": cand.get("generation"),
                     "search_seed": cand.get("search_seed"), "training_seed": cand.get("training_seed"),
                     "architecture_hash": cand.get("architecture_hash"),
                     "architecture_yaml": cand.get("architecture_yaml"), "architecture_path": None,
                     "genotype": geno if isinstance(geno, dict) else None,
                     "candidate_status": cand.get("candidate_status"), "current_rung": cand.get("current_rung")}
    now = utcnow_iso()
    env = {
        "envelope_version": ENVELOPE_VERSION,
        "job": {"job_id": job["id"], "attempt": int(job["attempt"]), "kind": job["kind"],
                "lease_expires_at": job["lease_expires_at"], "leased_at": job.get("heartbeat_at") or now,
                "spec": spec, "max_attempts": int(job["max_attempts"]), "priority": int(job["priority"])},
        "run": {"run_id": run["id"], "kind": run.get("kind") or "SEARCH", "protocol_hash": run["protocol_hash"],
                "resolved_protocol": run["resolved_protocol"], "status_at_claim": run["status"],
                "execution_mode": run.get("execution_mode") or "PILOT"},
        "candidate": candidate,
        "resume": resume,
        "data_refs": _data_refs(run, cand, raw_params),
        "versions": {"core_contract": core_contract, "progress_version": PROGRESS_VERSION,
                     "checkpoint_format": CHECKPOINT_FORMAT, "required_runtime": dict(REQUIRED_RUNTIME)},
        "fence": {"runner_id": runner_id, "attempt": int(job["attempt"]), "claim_id": claim_id, "issued_at": now},
        "control": {"lease_ttl_s": float(settings.lease_ttl_s), "heartbeat_s": float(settings.heartbeat_s),
                    "progress_interval_s": float(progress_interval_s), "kill_grace_s": float(kill_grace_s)},
    }
    return JobEnvelope.model_validate(env).model_dump(mode="json")


def stage_of(job: dict[str, Any]) -> str:
    """The ``stage`` component of a job ticket: the job's rung (``-`` for jobs without one)."""
    return str(job.get("rung") or "-")


__all__ = ["MIXED_HOOK", "TRAIN_PARAM_KEYS", "baseline_arch", "build_envelope", "checkpoint_location", "filter_params",
           "latest_live_full_state", "load_context", "native_params", "safe_name", "search_arch", "stage_of"]

