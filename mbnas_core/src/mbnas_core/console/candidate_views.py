"""Read-only candidate views: generation results never masquerade as evaluation rows."""
from __future__ import annotations

import sqlite3
from typing import Any

from mbnas_core.db import repo
from mbnas_core.console.services import AppServices
from mbnas_core.console.views import artifact_overlay, render_artifact, signing_key


def candidate_artifacts(conn: sqlite3.Connection, run_id: str, candidate_id: str, *,
                        base_url: str, svc: AppServices, overlay: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """``Artifact`` views of a candidate's registry rows with their effective state (recorded locations: a runner object
    is NOT_READY, without a signed URL, until the gateway verified it in its store)."""
    overlay = artifact_overlay(conn, run_id) if overlay is None else overlay
    return [render_artifact(a, overlay, base_url=base_url, key=signing_key(svc), ttl_s=svc.settings.signed_url_ttl_s)
            for a in repo.list_artifacts(conn, run_id) if a.get('candidate_id') == candidate_id]


def generation_candidates(conn: sqlite3.Connection, run_id: str, *, base_url: str,
                          svc: AppServices) -> list[dict[str, Any]]:
    """Generated candidates in deterministic proposal order, with unmeasured metrics null."""
    out = []
    overlay = artifact_overlay(conn, run_id)
    for cand in repo.list_candidates(conn, run_id):
        audit = cand.get('audit') or {}
        if audit.get('origin') != 'random_generation':
            continue
        structure = audit.get('structure') or {}
        generation = audit.get('generation') or {}
        out.append({**{k: cand.get(k) for k in ('name', 'family', 'arm', 'generation', 'blocks', 'parents',
                    'architecture_hash', 'architecture_path', 'candidate_status', 'search_seed', 'training_seed')},
                    'id': cand['id'], 'candidate_id': cand['id'], 'run_id': run_id,
                    'origin': 'random_generation', 'structure_status': structure.get('status', 'UNVERIFIED'),
                    'evaluation_status': 'NOT_EVALUATED', 'generation_order': generation.get('index'),
                    'attempt': generation.get('attempt'), 'seed': generation.get('seed', cand.get('search_seed')),
                    'requested_method': generation.get('requested_method'),
                    'effective_method': generation.get('effective_method', 'RandomGeneration'),
                    'duplicate_scope': generation.get('duplicate_scope', 'per_seed'),  # the policy that always applied
                    'execution': generation.get('execution'),  # 'CPU' for every generation job; null before it was recorded
                    'parameters': structure.get('parameters'),
                    'ap': None, 'recall': None, 'latency_ms': None, 'fitness': None,
                    'artifacts': candidate_artifacts(conn, run_id, cand['id'], base_url=base_url, svc=svc,
                                                     overlay=overlay)})
    run = repo.get_run(conn, run_id) or {}
    seeds = (run.get('resolved_protocol') or {}).get('console', {}).get('search', {}).get('seeds', [])
    seed_order = {seed: index for index, seed in enumerate(seeds)}
    return sorted(out, key=lambda c: (seed_order.get(c['seed'], len(seed_order)),
                                      c['generation_order'] if c['generation_order'] is not None else 0, c['id']))
