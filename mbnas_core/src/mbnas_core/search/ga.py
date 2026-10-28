"""GA proposer over the shared space: tournament parents from immutable L0 evaluations only, elitism 1.

Contract:

* Parent pool rows (:class:`ParentRow`) must all be committed (``status == "EVALUATED"``) evaluations at the
  protocol's parent-selection rung (``proposal.GA.parent_selection_fidelity``, default ``L0``). A row from any other
  rung or a provisional row raises :class:`ParentPoolError` (promoted candidates contribute their immutable L0 row).
  Rows without a finite fitness are excluded and listed. Pool order does not matter (sorted by candidate id).
* Ranking: higher L0 fitness, then lower latency, then candidate id. Tournament size 2 draws two distinct rows from
  the search stream.
* Generation layout: child 0 is the elite clone of the best row (1 parent). Each other child: with probability
  ``crossover_rate`` one-point row crossover of tournament parent A with a tournament partner B from the same
  template family (2 parents; if no partner exists it degrades to clone and says so), else a clone of A; then with
  probability ``mutation_rate`` one operator mutation. Shared repair runs after each operator.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from mbnas_model_runtime.genotype import Genotype
from mbnas_core.search.space import Proposal, RowOrigin, SearchSpace

METHOD = "GA"
DEFAULT_CROSSOVER_RATE = 0.5
TOURNAMENT_SIZE = 2


class ParentPoolError(ValueError):
    """The parent pool violates the L0-only / committed-evaluation contract."""


@dataclass(frozen=True)
class ParentRow:
    """One immutable parent-fidelity evaluation of a candidate, with that candidate's genotype."""

    candidate_id: str
    evaluation_id: str
    rung: str
    fitness: float | None
    latency_ms: float | None
    genotype: Genotype = field(repr=False)
    status: str = "EVALUATED"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ParentRow":
        """Accepts ``genotype`` (Genotype.to_json) or ``architecture_yaml`` + ``family`` + ``template_id``."""
        if "genotype" in d:
            g = Genotype.from_json(d["genotype"])
        else:
            g = Genotype.from_yaml_text(d["architecture_yaml"], family=d["family"], template_id=d["template_id"])
        return cls(candidate_id=str(d["candidate_id"]), evaluation_id=str(d["evaluation_id"]), rung=str(d["rung"]),
                   fitness=d.get("fitness"), latency_ms=d.get("latency_ms"), genotype=g,
                   status=str(d.get("status", "EVALUATED")))


@dataclass(frozen=True)
class GAConfig:
    population: int
    mutation_rate: float
    crossover_rate: float = DEFAULT_CROSSOVER_RATE
    parent_rung: str = "L0"
    elitism: int = 1
    tournament_size: int = TOURNAMENT_SIZE

    def __post_init__(self) -> None:
        if self.population < 1 or not 0 <= self.mutation_rate <= 1 or not 0 <= self.crossover_rate <= 1:
            raise ValueError("population >= 1 and rates in [0, 1] required")
        if self.elitism != 1 or self.tournament_size != 2:
            raise ValueError("this GA implements elitism 1 and tournament size 2")


def rank_key(row: ParentRow) -> tuple[float, float, str]:
    lat = row.latency_ms if isinstance(row.latency_ms, (int, float)) and math.isfinite(row.latency_ms) else math.inf
    return (-float(row.fitness), lat, row.candidate_id)  # type: ignore[arg-type]


def eligible_parents(rows: list[ParentRow], parent_rung: str) -> tuple[list[ParentRow], list[dict[str, Any]]]:
    """Enforce the L0-only contract; return (eligible sorted by candidate id, excluded with reasons)."""
    seen: set[str] = set()
    eligible, excluded = [], []
    for row in rows:
        if row.rung != parent_rung:
            raise ParentPoolError(f"GA parent selection uses only immutable {parent_rung} evaluations; "
                                  f"{row.evaluation_id} is rung {row.rung}")
        if row.status != "EVALUATED":
            raise ParentPoolError(f"parent evaluation {row.evaluation_id} is not committed (status {row.status})")
        if row.candidate_id in seen:
            raise ParentPoolError(f"candidate {row.candidate_id} appears twice in the {parent_rung} pool")
        seen.add(row.candidate_id)
        if not isinstance(row.fitness, (int, float)) or isinstance(row.fitness, bool) or not math.isfinite(row.fitness):
            excluded.append({"candidate_id": row.candidate_id, "evaluation_id": row.evaluation_id,
                             "reason": "no valid fitness at the parent rung"})
            continue
        eligible.append(row)
    return sorted(eligible, key=lambda r: r.candidate_id), excluded


def tournament(pool: list[ParentRow], rng: random.Random) -> tuple[ParentRow, dict[str, Any]]:
    """Size-2 tournament (single row: returned without a draw)."""
    if len(pool) == 1:
        return pool[0], {"contenders": [pool[0].candidate_id], "winner": pool[0].candidate_id}
    contenders = rng.sample(pool, TOURNAMENT_SIZE)
    winner = min(contenders, key=rank_key)
    return winner, {"contenders": [c.candidate_id for c in contenders], "winner": winner.candidate_id}


def _parent_ref(row: ParentRow) -> dict[str, Any]:
    return {"candidate_id": row.candidate_id, "evaluation_id": row.evaluation_id, "rung": row.rung,
            "fitness": row.fitness, "latency_ms": row.latency_ms}


def _clone(space: SearchSpace, parent: ParentRow, *, elite: bool) -> Proposal:
    rep = space.inspect(parent.genotype)
    origins = [RowOrigin("parent_a", parent.candidate_id, "inherit") for _ in parent.genotype.rows()]
    return Proposal(method=METHOD, operation="clone", genotype=parent.genotype, report=rep, origins=origins,
                    parents=[_parent_ref(parent)], elite=elite)


def _child(space: SearchSpace, pool: list[ParentRow], rng: random.Random, cfg: GAConfig) -> Proposal:
    a, sel_a = tournament(pool, rng)
    selection = {"parent_a": sel_a}
    if rng.random() < cfg.crossover_rate:
        partners = [r for r in pool if r.genotype.family == a.genotype.family and r.candidate_id != a.candidate_id]
        if partners:
            b, sel_b = tournament(partners, rng)
            selection["parent_b"] = sel_b
            rows, cut = space.crossover_rows(a.genotype, b.genotype, rng)
            origins = [RowOrigin("parent_a" if i < cut else "parent_b", a.candidate_id if i < cut else b.candidate_id,
                                 "inherit") for i in range(len(rows))]
            g, rep, repairs = space.repair(a.genotype.with_rows(rows), a.genotype, f"parent_a:{a.candidate_id}",
                                           origins, [a.candidate_id] * len(rows))
            prop = Proposal(method=METHOD, operation="crossover", genotype=g, report=rep, origins=origins,
                            parents=[_parent_ref(a), _parent_ref(b)], repair_events=repairs,
                            crossover={"cut_index": cut, "rows": len(rows), "parent_a": a.candidate_id,
                                       "parent_b": b.candidate_id,
                                       "mask": ["A" if i < cut else "B" for i in range(len(rows))]})
        else:
            prop = _clone(space, a, elite=False)
            prop.notes.append("crossover drawn but no same-family partner in the pool; degraded to clone")
    else:
        prop = _clone(space, a, elite=False)
    prop.selection = selection
    if rng.random() < cfg.mutation_rate:
        pre, pre_sources = prop.genotype, [o.source for o in prop.origins]
        mutated, event = space.mutate(pre, rng, prop.origins)
        if event is not None:
            g, rep, repairs = space.repair(mutated, pre, "pre_mutation", prop.origins, pre_sources)
            event["post_repair_architecture_hash"] = rep.architecture_hash
            event["structural_noop"] = rep.architecture_hash == event["pre_mutation_architecture_hash"]
            prop.genotype, prop.report = g, rep
            prop.mutation_events.append(event)
            prop.repair_events.extend(repairs)
            if prop.operation == "clone":
                prop.operation = "mutation"
    return prop


def propose_generation(space: SearchSpace, parents: list[ParentRow], rng: random.Random,
                       cfg: GAConfig) -> tuple[list[Proposal], dict[str, Any]]:
    """Children of one GA generation plus a selection record ``{eligible, excluded, elite}``."""
    pool, excluded = eligible_parents(parents, cfg.parent_rung)
    if not pool:
        raise ParentPoolError(f"no {cfg.parent_rung} evaluation with a valid fitness to select parents from")
    elite = min(pool, key=rank_key)
    children = [_clone(space, elite, elite=True)]
    while len(children) < cfg.population:
        children.append(_child(space, pool, rng, cfg))
    record = {"eligible": [_parent_ref(r) for r in pool], "excluded": excluded, "elite": elite.candidate_id,
              "parent_rung": cfg.parent_rung, "ranking": "fitness desc, latency asc, candidate_id asc"}
    return children, record
