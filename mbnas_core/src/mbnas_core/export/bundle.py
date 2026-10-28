"""Run export bundle (contract ``exportRun``): one zip with everything needed to audit a run, no secrets.

Contents (all text, UTF-8)::

    README.txt                      what each file is
    protocol/requested.json         the protocol the user submitted for validation (or the Run payload)
    protocol/resolved.json          the frozen resolved protocol; protocol/protocol_hash.txt its canonical hash
    manifests/hashes.json           train/search_val/final_eval/class-mapping hashes + counts (resolved + validation)
    manifests/<split_hash>.json     cached split manifests (sample ids, image/label sha256), when present
    evaluations.csv / .jsonl        every evaluation row incl. superseded history and failures (CSV = key columns)
    lineage.json                    candidates: parents, parent evaluation ids, lineage ops, blocks, architecture hash
    generations.json                generation rows (counts, closure, representative)
    cost_ledger.csv                 every ledger row (physical vs billed arm, failed/retry/profile)
    checkpoints.json                checkpoint rows (path, sha256, bytes, hashes, state PRESENT/PRUNED + prune record)
    artifacts.json                  artifact registry rows (run-relative paths + sha256)
    final_retrain.json              FINAL_RETRAIN run only: parent_run_id, frozen finalists + selection rule, budget
    final_retrain_children.json     SEARCH run with final-retrain children: child run ids, status, costs, finalists
                                    and the final results summary (rung FINAL rows on the final split)
    jobs.json                       job attempts/status/reason codes (no outputs bodies)
    events.jsonl                    the full run event log
    provenance.json                 source tree hash, canonicalization, validator, run identity, export time
    environment.lock.json           package/driver/GPU versions
    SHA256SUMS                      sha256 of every file above

Entries carry a fixed timestamp so identical content gives an identical archive. Settings and secrets are never
read here; :data:`SECRET_ENV_NAMES` only names what must never appear (tests scan for the values).

Profiles (WP-4 section 1.5, DD-A19): ``public`` (default; the console ``GET /runs/{id}/export`` and the gateway's export
manifest) projects every member onto the public contract 1.5.0 (:mod:`mbnas_core.console.projection`; DTO allowlist
section 3.9): resolved protocol without realpath / pool root, evaluation rows and events as their public components
(run-relative checkpoint paths, raw_metrics allowlist, scrubbed log text, opaque worker / device ids), lineage and
blocks as the public lineage subset (repair folded), checkpoint rows without paths, artifact rows with public metadata
only, the cost ledger without internal row ids / device UUIDs / host row ids, provenance and final-retrain budgets
without the admission policy text, split manifests without host paths (sample ids + digests), the environment lock
without host name, interpreter path and GPU UUIDs. ``owner`` (``/core/v1/owner/runs/{id}/export``, scope admin) is
the raw bundle below, unchanged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from mbnas_core.db import repo
from mbnas_contracts.util.time import utcnow_iso

SECRET_ENV_NAMES = ("MBNAS_API_TOKEN", "MBNAS_ARTIFACT_SIGNING_KEY")
PROFILES = ("public", "owner")
PUBLIC_COST_COLUMNS = ("arm", "billed_arm", "physical", "kind", "gpu_seconds", "candidate_id", "job_id", "attempt",
                       "device_id", "started_at", "finished_at", "meta")
PUBLIC_ARTIFACT_KEYS = ("id", "run_id", "evaluation_id", "candidate_id", "kind", "relpath", "sha256", "mime_type", "bytes",
                        "sample_id", "split_hash", "state", "created_at", "updated_at")
PUBLIC_ARTIFACT_META_KEYS = ('origin', 'category', 'filename', 'stage', 'candidate_id', 'batch_index', 'stage_epoch_start',
                             'stage_epoch_target', 'epoch', 'checkpoint_id', 'identifier_note', 'configuration_role',
                             'configuration_kind', 'yaml_hash', 'architecture_hash')
EVAL_COLUMNS = ("id", "candidate_id", "name", "arm", "family", "generation", "rung", "epochs", "status",
                "candidate_status", "revision", "supersedes_evaluation_id", "ap", "recall", "latency_ms", "size_mb",
                "fitness", "device", "precision", "latency_source", "latency_scope", "search_seed", "training_seed",
                "batch", "resolved_input_shape", "comparison_group", "objective_hash", "evaluation_hash", "split_hash",
                "protocol_hash", "architecture_hash", "checkpoint_path", "checkpoint_sha256", "reason_code", "job_id",
                "attempt")
COST_COLUMNS = ("id", "arm", "billed_arm", "physical", "kind", "gpu_seconds", "candidate_id", "job_id", "attempt",
                "device_uuid", "started_at", "finished_at", "meta")
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else (json.dumps(row[c], sort_keys=True)
                                                        if isinstance(row.get(c), (dict, list)) else row[c])
                         for c in columns])
    return buf.getvalue()


def _manifest_files(manifest_dir: Path, hashes: list[str]) -> dict[str, str]:
    out = {}
    for h in hashes:
        if not isinstance(h, str) or not h.startswith("sha256:"):
            continue
        path = manifest_dir / f"{h.split(':', 1)[1]}.json"
        if path.is_file():
            out[f"manifests/{path.name}"] = path.read_text(encoding="utf-8")
    return out


def bundle_files(conn: sqlite3.Connection, run_id: str, *, manifest_dir: str, environment: dict[str, Any],
                 provenance: dict[str, Any], requested_protocol: dict[str, Any] | None = None,
                 profile: str = "public") -> dict[str, str]:
    """Name -> text of every bundle member (inside one read transaction for a consistent view); ``profile`` public
    (default) projects every member onto the public contract, owner keeps the raw rows (module docstring)."""
    if profile not in PROFILES:
        raise ValueError(f"export profile must be one of {PROFILES}")
    if profile == "public":
        return _public_files(conn, run_id, manifest_dir=manifest_dir, environment=environment, provenance=provenance,
                             requested_protocol=requested_protocol)
    return _raw_files(conn, run_id, manifest_dir=manifest_dir, environment=environment, provenance=provenance,
                      requested_protocol=requested_protocol)


def _raw_files(conn: sqlite3.Connection, run_id: str, *, manifest_dir: str, environment: dict[str, Any],
               provenance: dict[str, Any], requested_protocol: dict[str, Any] | None = None) -> dict[str, str]:
    """The owner (raw) bundle members."""
    run = repo.get_run(conn, run_id)
    if run is None:
        raise repo.NotFound(run_id)
    resolved = run["resolved_protocol"]
    dataset = resolved.get("dataset", {})
    hash_keys = ("train_manifest_hash", "search_validation_manifest_hash", "final_evaluation_manifest_hash",
                 "class_mapping_hash")
    validation = repo.get_validation(conn, run["validation_id"]) if run.get("validation_id") else None
    evals = repo.list_evaluations(conn, run_id, include_superseded=True)
    cands = repo.list_candidates(conn, run_id)
    files: dict[str, str] = {
        "protocol/requested.json": _json(requested_protocol or run["requested_protocol"]),
        "protocol/resolved.json": _json(resolved),
        "protocol/protocol_hash.txt": run["protocol_hash"] + "\n",
        "manifests/hashes.json": _json({"resolved": {k: dataset.get(k) for k in hash_keys},
                                        "counts": dataset.get("counts"),
                                        "validation": (validation or {}).get("dataset_manifest_hashes")}),
        "evaluations.csv": _csv([{**e["row"], "status": e["status"], "revision": e["revision"]} for e in evals],
                                EVAL_COLUMNS),
        "evaluations.jsonl": "".join(json.dumps({**e["row"], "immutable": bool(e["immutable"]),
                                                 "created_at": e["created_at"]}, sort_keys=True) + "\n" for e in evals),
        "lineage.json": _json([{k: c.get(k) for k in ("id", "name", "arm", "family", "generation", "search_seed",
                                                      "training_seed", "architecture_hash", "architecture_path",
                                                      "parents", "parent_evaluation_ids", "lineage", "blocks",
                                                      "candidate_status", "current_rung")} for c in cands]),
        "generations.json": _json([g["row"] | {"complete": bool(g["complete"])} for g in repo.list_generations(conn, run_id)]),
        "cost_ledger.csv": _csv(repo.list_costs(conn, run_id), COST_COLUMNS),
        "checkpoints.json": _json(repo.list_checkpoints(conn, run_id)),
        "artifacts.json": _json(repo.list_artifacts(conn, run_id)),
        "jobs.json": _json([{k: j.get(k) for k in ("id", "kind", "status", "attempt", "max_attempts", "candidate_id",
                                                    "rung", "cumulative_target", "fixed_horizon", "created_at",
                                                    "started_at", "finished_at")}
                            | {"reason_code": ((j.get("error") or {}).get("reason_code"))} for j in
                            repo.list_jobs(conn, run_id)]),
        "events.jsonl": "".join(json.dumps(e, sort_keys=True) + "\n" for e in _all_events(conn, run_id)),
        "provenance.json": _json({**provenance, "run_id": run_id, "kind": run.get("kind") or "SEARCH",
                                  "parent_run_id": run.get("parent_run_id"),
                                  "final_retrain_run_ids": [c["id"] for c in repo.list_child_runs(conn, run_id)],
                                  "namespace": run["namespace"],
                                  "execution_mode": run["execution_mode"], "purpose": run.get("purpose"),
                                  "preset": run.get("preset"), "run_status": run["status"],
                                  "source_commit": run.get("source_commit"), "protocol_hash": run["protocol_hash"],
                                  "canonicalization": (resolved.get("resolution") or {}).get("canonicalization"),
                                  "validator": (resolved.get("resolution") or {}).get("validator"),
                                  "costs": run.get("costs"), "budget": run.get("budget"),
                                  "exported_at": utcnow_iso()}),
        "environment.lock.json": _json(environment),
    }
    files.update(_final_retrain_files(conn, run))
    files.update(_manifest_files(Path(manifest_dir), [dataset.get(k) for k in hash_keys[:3]]))
    files["README.txt"] = _readme("owner")
    files["SHA256SUMS"] = _sums(files)
    return files


def _readme(profile: str) -> str:
    text = (__doc__ or "").split("Contents (all text, UTF-8)::", 1)[-1]
    contents, _, rest = text.partition("Entries carry a fixed timestamp")
    note = rest.split("Profiles (WP-4 section 1.5, DD-A19): ", 1)[-1].strip()
    return contents.strip() + "\n\nProfile: " + profile + ". " + note + "\n"


def _sums(files: dict[str, str]) -> str:
    return "".join(f"{hashlib.sha256(t.encode()).hexdigest()}  {n}\n" for n, t in sorted(files.items()))


# -- public profile ---------------------------------------------------------------------------------------------------
def _no_policy(budget: Any) -> Any:
    """A budget without the admission policy text and with generation diagnostics reduced to their codes."""
    from mbnas_core.console.projection import public_budget

    return public_budget(budget)


def _public_manifest(text: str) -> str:
    """A cached split manifest without host paths: sample ids, digests, sizes and boxes; issue texts scrubbed."""
    from mbnas_core.console.projection import scrub_text

    doc = json.loads(text)
    doc.pop("roots", None)
    doc["samples"] = [{k: v for k, v in s.items() if k not in ("image", "label")}
                      for s in doc.get("samples") or [] if isinstance(s, dict)]
    doc["issues"] = [{**i, "message": scrub_text(i.get("message"))} if isinstance(i, dict) else i
                     for i in doc.get("issues") or []]
    return _json(doc)


def _public_environment(environment: dict[str, Any]) -> dict[str, Any]:
    from mbnas_core.console.projection import project_member, scrub_deep

    api = environment.get("api_process")
    if isinstance(api, dict):
        api = project_member("Health", ("versions",), api)
    lock = environment.get("recorded_environment_lock")
    if isinstance(lock, dict):
        lock = {k: v for k, v in lock.items() if k != "host"}
        if isinstance(lock.get("python"), dict):
            lock["python"] = {k: v for k, v in lock["python"].items() if k != "executable"}
        if isinstance(lock.get("gpus"), list):
            lock["gpus"] = [{k: v for k, v in g.items() if k != "uuid"} if isinstance(g, dict) else g
                            for g in lock["gpus"]]
        lock = scrub_deep(lock)
    return {"api_process": api, "recorded_environment_lock": lock}


def _public_cost_row(row: dict[str, Any]) -> dict[str, Any]:
    from mbnas_core.console.projection import public_ids

    out = {k: row.get(k) for k in PUBLIC_COST_COLUMNS if k not in ("device_id", "meta")}
    out["device_id"] = public_ids().device_id(row.get("device_uuid"))
    meta = row.get("meta")
    out["meta"] = ({k: v for k, v in meta.items() if k != "host_row_id"} if isinstance(meta, dict) else meta)
    return out


def _public_artifact_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in PUBLIC_ARTIFACT_KEYS}
    meta = row.get("meta") or {}
    out["metadata"] = {k: meta[k] for k in PUBLIC_ARTIFACT_META_KEYS
                       if k in meta and (meta[k] is None or isinstance(meta[k], (str, int, float, bool)))}
    return out


def _public_files(conn: sqlite3.Connection, run_id: str, *, manifest_dir: str, environment: dict[str, Any],
                  provenance: dict[str, Any], requested_protocol: dict[str, Any] | None = None) -> dict[str, str]:
    """The public bundle members: the raw members projected (module docstring)."""
    from mbnas_core.console.projection import project_member, project_public, public_path
    from mbnas_core.orchestration.retention import checkpoint_views
    from mbnas_core.orchestration.views import lineage_view

    run = repo.get_run(conn, run_id)
    if run is None:
        raise repo.NotFound(run_id)
    raw = _raw_files(conn, run_id, manifest_dir=manifest_dir, environment=environment, provenance=provenance,
                     requested_protocol=requested_protocol)
    resolved = project_public("Protocol", run["resolved_protocol"], run_id=run_id)
    evals = repo.list_evaluations(conn, run_id, include_superseded=True)
    rows = [project_public("CandidateEvaluation", {**e["row"], "status": e["status"], "revision": e["revision"],
                                                   "immutable": bool(e["immutable"])}, run_id=run_id) for e in evals]
    files = dict(raw)
    files["protocol/resolved.json"] = _json(resolved)
    files["evaluations.csv"] = _csv(rows, EVAL_COLUMNS)
    files["evaluations.jsonl"] = "".join(json.dumps({**r, "created_at": e["created_at"]}, sort_keys=True) + "\n"
                                         for r, e in zip(rows, evals))
    lineage = []
    for c in repo.list_candidates(conn, run_id):
        item = project_public("CandidateDetail", {
            "candidate_id": c["id"], "run_id": run_id, "name": c["name"], "arm": c["arm"], "family": c["family"],
            "architecture_hash": c.get("architecture_hash") or "unresolved", "blocks": c.get("blocks") or [],
            "parents": c.get("parents") or [], "parent_evaluation_ids": c.get("parent_evaluation_ids") or [],
            "lineage": lineage_view(c.get("lineage")), "evaluations": [], "candidate_status": c["candidate_status"],
            "current_rung": c.get("current_rung"), "generation": c.get("generation"),
            "search_seed": c.get("search_seed"), "training_seed": c.get("training_seed"),
            "architecture_path": c.get("architecture_path"), "training_status": "UNTRAINED"}, run_id=run_id)
        lineage.append({"id": c["id"], **{k: item.get(k) for k in (
            "name", "arm", "family", "generation", "search_seed", "training_seed", "architecture_hash",
            "architecture_path", "parents", "parent_evaluation_ids", "lineage", "blocks", "candidate_status",
            "current_rung")}})
    files["lineage.json"] = _json(lineage)
    files["generations.json"] = _json([project_public("Generation", g["row"] | {"complete": bool(g["complete"])})
                                       for g in repo.list_generations(conn, run_id)])
    files["cost_ledger.csv"] = _csv([_public_cost_row(r) for r in repo.list_costs(conn, run_id)], PUBLIC_COST_COLUMNS)
    from mbnas_core.artifacts.locations import checkpoint_states

    files["checkpoints.json"] = _json([project_member("CandidateDetail", ("checkpoints", "[]"), ck, run_id=run_id)
                                       for ck in checkpoint_views(repo.list_checkpoints(conn, run_id),
                                                                  checkpoint_states(conn, run_id))])
    files["artifacts.json"] = _json([_public_artifact_row(a) for a in repo.list_artifacts(conn, run_id)])
    files["events.jsonl"] = "".join(json.dumps(project_public("Event", e, run_id=run_id), sort_keys=True) + "\n"
                                    for e in _all_events(conn, run_id))
    prov = json.loads(raw["provenance.json"])
    prov["budget"] = _no_policy(prov.get("budget"))
    files["provenance.json"] = _json(prov)
    files["environment.lock.json"] = _json(_public_environment(environment))
    for name in ("final_retrain.json", "final_retrain_children.json"):
        if name not in raw:
            continue
        doc = json.loads(raw[name])
        for item in doc if isinstance(doc, list) else [doc]:
            item["budget"] = _no_policy(item.get("budget"))
            for res in item.get("results") or []:
                res["checkpoint_path"] = public_path(res.get("checkpoint_path"), {run_id, item.get("run_id") or run_id})
        files[name] = _json(doc)
    for name in [n for n in raw if n.startswith("manifests/") and n != "manifests/hashes.json"]:
        files[name] = _public_manifest(raw[name])
    files["README.txt"] = _readme("public")
    files.pop("SHA256SUMS", None)
    files["SHA256SUMS"] = _sums(files)
    return files


FINAL_RESULT_KEYS = ("id", "candidate_id", "name", "status", "rung", "epochs", "ap", "recall", "latency_ms", "size_mb",
                     "fitness", "latency_source", "latency_scope", "split_hash", "evaluation_hash", "comparison_group",
                     "checkpoint_path", "parents", "reason_code")


def _final_results(conn: sqlite3.Connection, child: dict[str, Any]) -> list[dict[str, Any]]:
    cands = {c["id"]: c for c in repo.list_candidates(conn, child["id"])}
    out = []
    for e in repo.list_evaluations(conn, child["id"], include_superseded=False):
        row = {k: e["row"].get(k) for k in FINAL_RESULT_KEYS}
        cand = cands.get(row["candidate_id"]) or {}
        row.update(status=e["status"], candidate_status=cand.get("candidate_status"),
                   source_candidate_id=(cand.get("lineage") or {}).get("source_candidate_id"),
                   source_evaluation_id=(cand.get("lineage") or {}).get("source_evaluation_id"))
        out.append(row)
    return out


def _final_retrain_files(conn: sqlite3.Connection, run: dict[str, Any]) -> dict[str, str]:
    """``final_retrain.json`` for a child run, ``final_retrain_children.json`` for a parent with children."""
    out: dict[str, str] = {}
    if (run.get("kind") or "SEARCH") == "FINAL_RETRAIN":
        finalists = list(run.get("finalists") or [])
        out["final_retrain.json"] = _json({
            "run_id": run["id"], "kind": "FINAL_RETRAIN", "parent_run_id": run.get("parent_run_id"),
            "selection_rule": finalists[0]["selection_rule"] if finalists else None, "finalists": finalists,
            "budget": run.get("budget"), "final_recipe": (run["resolved_protocol"] or {}).get("final_retraining"),
            "results": _final_results(conn, run)})
    children = repo.list_child_runs(conn, run["id"])
    if children:
        out["final_retrain_children.json"] = _json([
            {"run_id": c["id"], "status": c["status"], "reason_code": c.get("reason_code"), "created_at": c["created_at"],
             "costs": c.get("costs"), "budget": c.get("budget"), "finalists": c.get("finalists") or [],
             "results": _final_results(conn, c)} for c in children])
    return out


def _all_events(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    out, cursor = [], 0
    while True:
        batch = repo.get_events_after(conn, run_id, cursor, 1000)
        out.extend(batch)
        if len(batch) < 1000:
            return out
        cursor = batch[-1]["seq"]


def build_zip(files: dict[str, str]) -> bytes:
    """Deterministic zip (sorted names, fixed timestamps, deflate)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name].encode("utf-8"))
    return buf.getvalue()
