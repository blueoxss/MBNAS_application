"""Separated seed streams: architecture proposals (search seed) vs. candidate training (training seed policy).

* Search stream: ``random.Random`` seeded from ``sha256("mbnas-search-stream/1|seed=<s>|<scope>")``; one stream per
  (search seed, arm, generation) advanced once per proposal draw. Generation 0 uses the arm-independent scope
  ``gen=0|prior``: both arms of one search seed start from the same frozen-prior sample (paired design).
* Training seed: ``sha256("mbnas-train-seed/1|base=<b>|gen=<g>|idx=<i>") mod 2**31``; ``base`` comes from the protocol
  ``training.seed_policy`` (int, or a dict with ``base_seed``) and defaults to the search seed. It never touches the
  search stream, so changing training seeds cannot change proposals and vice versa.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any

SEARCH_STREAM_ID = "mbnas-search-stream/1"
TRAIN_SEED_ID = "mbnas-train-seed/1"
ARM_CODES = {"GA": "GA", "IndependentRandom": "RAND"}


def _int_seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("ascii")).digest()[:8], "big")


def search_stream_key(search_seed: int, arm: str, generation: int) -> str:
    """Stream key; generation 0 is shared by both arms (frozen-prior initial population)."""
    scope = "gen=0|prior" if generation == 0 else f"arm={arm}|gen={generation}"
    return f"{SEARCH_STREAM_ID}|seed={int(search_seed)}|{scope}"


def search_rng(search_seed: int, arm: str, generation: int) -> random.Random:
    return random.Random(_int_seed(search_stream_key(search_seed, arm, generation)))


def training_seed_policy(protocol: dict[str, Any], search_seed: int) -> dict[str, Any]:
    """Resolve the training-seed base from ``training.seed_policy`` (recorded verbatim with its source)."""
    policy = (protocol.get("training") or {}).get("seed_policy")
    if isinstance(policy, int) and not isinstance(policy, bool):
        base, source = policy, "training.seed_policy"
    elif isinstance(policy, dict) and isinstance(policy.get("base_seed"), int):
        base, source = policy["base_seed"], "training.seed_policy.base_seed"
    else:
        base, source = int(search_seed), "search_seed (training.seed_policy unset)"
    return {"id": TRAIN_SEED_ID, "base": int(base), "base_source": source,
            "derivation": "sha256(id|base|gen|idx)[:8] mod 2**31", "requested": policy}


def training_seed(policy: dict[str, Any], generation: int, index: int) -> int:
    return _int_seed(f"{TRAIN_SEED_ID}|base={policy['base']}|gen={generation}|idx={index}") % (2 ** 31)


def candidate_name(arm: str, generation: int, index: int) -> str:
    """Display name, e.g. ``GA-G03-C007`` / ``RAND-G00-C001`` (index within the generation)."""
    return f"{ARM_CODES.get(arm, arm)}-G{generation:02d}-C{index:03d}"


def candidate_id(run_id: str, arm: str, search_seed: int, generation: int, index: int) -> str:
    """Stable trial id unique per run/arm/search seed (kept across promotions)."""
    return f"{run_id}:{ARM_CODES.get(arm, arm)}:s{search_seed}:g{generation:02d}:c{index:03d}"
