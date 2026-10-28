"""Protocol identity: ``protocol_hash`` = canonical SHA-256 of a resolved protocol minus the excluded fields.

``protocol_hash(resolved)`` hashes (``mbnas-canon/1``, ``"sha256:<hex>"``) a deep copy of ``resolved`` from which the
dotted ``RESOLUTION_HASH_EXCLUDED`` fields are removed: the free-text ``console.label``, the validation timestamp
``resolution.validated_at``, the disk-housekeeping policy ``console.retention`` and, since contract 1.5.0 (WP-4,
DD-A46), ``resolution.paths`` - the per-path record of the validating host (two protocols that differ only in these
share a cohort; the requested paths themselves stay hashed under ``console.paths``). ``resolved`` itself is never
mutated, and re-validating a resolved protocol is a fixed point (same hash). The protocol validator, the job handlers
and the generation proposer all use this one definition.

Compatibility (recorded in docs/internal/evidence/WP4/4B): a resolved protocol stored by a 1.4.x core carries
``resolution.paths.*.realpath`` and ``search_space_resolved.etri_root``; its stored ``protocol_hash`` was computed with
``resolution.paths`` included, so recomputing it under this definition gives another value (and re-validating it with a
1.5.0 validator, which records neither member, gives another resolved document). Runs created before 1.5.0 therefore
fail the resume hash checks; requested protocols (presets, user input) have no ``resolution`` and hash as before.

Source: ``mbnas_server.protocol.validator`` (``RESOLUTION_HASH_EXCLUDED`` L71, ``protocol_hash`` L172-183; monolithic
package, snapshot HEAD 18c4815), moved per WP-1 contract §4 / DD-A10. The function body is the moved one, but the hash
definition is not: contract 1.5.0 added ``resolution.paths`` to ``RESOLUTION_HASH_EXCLUDED`` (DD-A53 c), which changes
the hash of every resolved protocol that carries that member (see Compatibility above).
"""
from __future__ import annotations

import copy
from typing import Any

from mbnas_contracts.util.canon import canonical_sha256

RESOLUTION_HASH_EXCLUDED = ("console.label", "resolution.validated_at", "console.retention", "resolution.paths")


def protocol_hash(resolved: dict[str, Any]) -> str:
    """Canonical SHA-256 of ``resolved`` without the RESOLUTION_HASH_EXCLUDED fields."""
    doc = copy.deepcopy(resolved)
    for dotted in RESOLUTION_HASH_EXCLUDED:
        *parents, leaf = dotted.split(".")
        node: Any = doc
        for p in parents:
            node = node.get(p) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    return canonical_sha256(doc)
