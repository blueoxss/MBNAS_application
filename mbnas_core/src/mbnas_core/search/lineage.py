"""Lineage records and contract ``Block`` lists for proposals.

* :func:`blocks_for` emits one Block per YAML row (backbone then head) with ``name`` (operator), ``old_name`` (operator
  before a mutation/repair/sample changed it), ``mutated``, ``source`` (parent candidate id or template id),
  ``source_kind`` (``parent_a``/``parent_b``/``random``/``mutation``/``repair``), ``position``, ``parent_block_id``
  (``<source>#<position>``), ``operation``, ``params`` (row + observed effective shapes), ``before``/``after`` and
  ``repair_reason``.
* :func:`lineage_for` emits the contract ``CandidateDetail.lineage`` object: operation (crossover | clone | mutation |
  random), elite flag, parents, crossover cut/mask, mutation events, repair events, source template, prior draw,
  selection record and the shared space/prior hashes.
"""
from __future__ import annotations

from typing import Any

from mbnas_model_runtime.genotype_audit import config_estimate
from mbnas_core.search.space import Proposal, RowOrigin, SearchSpace


def _row_dict(row: list[Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {"from": row[0], "repeats": row[1], "module": row[2], "args": row[3]}


def blocks_for(proposal: Proposal, space: SearchSpace) -> list[dict[str, Any]]:
    """Contract Block rows for every model row of the proposal."""
    g, rep = proposal.genotype, proposal.report
    slots = {s.row: s for s in space.slots[g.family]}
    out = []
    for i, (row, origin) in enumerate(zip(g.rows(), proposal.origins)):
        origin: RowOrigin
        changed = origin.before is not None and origin.after is not None and origin.before != origin.after
        old = str(origin.before[2]) if changed and str(origin.before[2]) != str(row[2]) else None
        layer = rep.layers[i] if rep.ok and i < len(rep.layers) else None
        slot = slots.get(i)
        out.append({
            "name": str(row[2]), "old_name": old, "mutated": origin.kind == "mutation",
            "source": origin.source, "source_kind": origin.kind, "position": i,
            "parent_block_id": f"{origin.source}#{i}", "operation": origin.operation,
            "params": {**_row_dict(row), "scope": "backbone" if i < g.n_backbone else "head",
                       "slot_kind": slot.kind if slot else "fixed",
                       "effective": ({"c_in": layer.c_in, "c_out": layer.c_out, "h_in": layer.h_in,
                                      "w_in": layer.w_in, "stride": layer.stride, "params": layer.params}
                                     if layer else None)},
            "before": _row_dict(origin.before) if changed else {},
            "after": _row_dict(origin.after) if changed else {},
            "repair_reason": origin.reason if origin.kind == "repair" else None,
        })
    return out


def lineage_for(proposal: Proposal, space: SearchSpace, desc: dict[str, Any]) -> dict[str, Any]:
    """Contract lineage object (see module docstring); ``desc`` is ``space.describe()``."""
    tpl = space.templates.get(proposal.genotype.template_id)
    return {
        "operation": proposal.operation, "method": proposal.method, "elite": proposal.elite,
        "parents": [p["candidate_id"] for p in proposal.parents],
        "parent_evaluation_ids": [p["evaluation_id"] for p in proposal.parents],
        "parent_rows": proposal.parents, "selection": proposal.selection,
        "crossover": proposal.crossover, "mutation_events": proposal.mutation_events,
        "repair_events": proposal.repair_events, "prior_draw": proposal.prior_draw,
        "template": {"id": proposal.genotype.template_id, "family": proposal.genotype.family,
                     "variant": tpl.variant if tpl else None},
        "search_space": {"version": desc["version"], "variant": desc["variant"], "space_hash": desc["space_hash"],
                         "prior_hash": desc["prior_hash"]},
        "notes": proposal.notes,
    }


def audit_for(proposal: Proposal, space: SearchSpace) -> dict[str, Any]:
    """Config estimate + built-model audit + which operator space produced the candidate."""
    g = proposal.genotype
    built = (proposal.report.built_audit or {}).get("built")
    return {
        "config_estimate": config_estimate(g.yaml, space.native_head.get(g.family)),
        "built": built, "etri_audit_model": (proposal.report.built_audit or {}).get("etri_audit_model"),
        "label": built["label"] if built else None,
        "search_space_variant": space.variant, "fallback_reason": space.fallback_reason,
        "params": proposal.report.params, "flops": proposal.report.flops, "imgsz": space.imgsz,
        "architecture_hash_scheme": "mbnas-structural/1 (adapters.common.structural_hash)",
        "structure_signature_sha256": proposal.report.signature_hash,
    }
