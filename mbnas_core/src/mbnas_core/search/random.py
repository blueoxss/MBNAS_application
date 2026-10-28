"""IndependentRandom proposer: samples the shared space's frozen prior with the search-seed stream only.

The function signature has no fitness, evaluation or history argument by construction; the prior tables were frozen
when the space was created from the seed templates alone. Proposals carry ``parents == []`` and per-row
``source_kind == "random"`` (``"repair"`` for rows the shared repair reverted).
"""
from __future__ import annotations

import random

from mbnas_core.search.space import Proposal, SearchSpace

METHOD = "IndependentRandom"


def propose_generation(space: SearchSpace, rng: random.Random, population: int) -> list[Proposal]:
    """``population`` independent frozen-prior samples, drawn in order from ``rng``."""
    if population < 1:
        raise ValueError("population must be >= 1")
    return [space.sample_prior(rng, method=METHOD) for _ in range(population)]
