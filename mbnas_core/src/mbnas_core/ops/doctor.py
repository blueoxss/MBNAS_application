"""``mbnas-core-doctor``: one JSON report of what this core host/configuration would serve (no secrets printed).

Sections: versions (installed distributions + recorded environment lock), GPU inventory (NVML), allowlists, DB
(path, existence, schema version, row counts), presets, registry summary (source, warning, status counts),
secrets configured (booleans only), output-root free disk, worker interpreter. ``--strict`` exits 1 when a
start-blocking problem is found (no API token, DB parent not writable, worker python missing).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from typing import Any

from mbnas_core.config import CoreSettings as Settings
from mbnas_core.protocol.presets import preset_summaries
from mbnas_model_runtime.provenance.env_lock import host_gpu_inventory
from mbnas_contracts.util.paths import free_disk_gb
from mbnas_core.registry import roots_skipped as registry_roots_skipped
from mbnas_core.console.services import installed_versions, load_registry

TABLES = ("validations", "runs", "candidates", "evaluations", "generations", "jobs", "workers", "events", "artifacts",
          "cost_ledger", "checkpoints")


def _db(settings: Settings) -> dict[str, Any]:
    info: dict[str, Any] = {"path": settings.db_path, "exists": os.path.isfile(settings.db_path)}
    if not info["exists"]:
        return info
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    try:
        info["schema_version"] = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        info["rows"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        info["runs_by_status"] = dict(conn.execute("SELECT status, COUNT(*) FROM runs GROUP BY status").fetchall())
        info["jobs_by_status"] = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    finally:
        conn.close()
    return info


def _registry_roots_skipped(settings: Settings) -> list[dict[str, str]]:
    """DD-26: registry roots that exist but this process cannot read (evidence there is ignored, entries stay
    UNVERIFIED); ``[]`` when all are readable."""
    try:
        return registry_roots_skipped(settings.output_root)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never break the doctor
        return [{"path": settings.output_root, "reason": f"{type(exc).__name__}: {exc}"}]


def report(settings: Settings) -> tuple[dict[str, Any], list[str]]:
    """``(report, blocking_problems)``."""
    entries, source, warning = load_registry(settings)
    problems = []
    if settings.api_token is None:
        problems.append("MBNAS_CORE_API_TOKEN is not set (the API refuses to start)")
    if not os.path.isfile(settings.worker_python):
        problems.append(f"worker python missing: {settings.worker_python}")
    parent = os.path.dirname(settings.db_path)
    if os.path.exists(parent) and not os.access(parent, os.W_OK):
        problems.append(f"DB directory not writable: {parent}")
    doc = {
        "versions": installed_versions(), "gpus": host_gpu_inventory(),
        "allowlists": {"dataset_roots": settings.dataset_roots, "output_roots": settings.output_roots},
        "settings": {"host": settings.host, "port": settings.port, "output_root": settings.output_root,
                     "weights_dir": settings.weights_dir, "log_dir": settings.log_dir,
                     "cors_origins": settings.cors_origins, "lease_ttl_s": settings.lease_ttl_s,
                     "heartbeat_s": settings.heartbeat_s, "worker_python": settings.worker_python},
        "secrets_configured": {"MBNAS_CORE_API_TOKEN": settings.api_token is not None,
                               "MBNAS_CORE_ARTIFACT_SIGNING_KEY": settings.artifact_signing_key is not None},
        "output_root_free_gb": round(free_disk_gb(settings.output_root), 1), "db": _db(settings),
        "presets": preset_summaries(),
        "registry": {"source": source, "warning": warning, "entries": len(entries),
                     "by_status": dict(Counter(e.get("status") for e in entries)),
                     "roots_skipped": _registry_roots_skipped(settings)},
        "problems": problems,
    }
    return doc, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MBNAS core host/configuration diagnostics (JSON, no secrets)")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--strict", action="store_true", help="exit 1 on start-blocking problems")
    args = parser.parse_args(argv)
    doc, problems = report(Settings.load(args.env_file))
    print(json.dumps(doc, indent=1, sort_keys=True, default=str))
    return 1 if args.strict and problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
