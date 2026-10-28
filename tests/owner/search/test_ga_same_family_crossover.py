"""GA crossover pairs parents of one template family only (fix round 1, C03-D4; ``mbnas_core/search/ga.py`` ``_child``).

The partner pool of a drawn crossover is the parent rows of parent A's template family other than A (``ga.py`` partner
filter); when that pool is empty the child degrades to a clone of A and says so in its notes (``ga.py`` clone
fallback). Until now only T2-3 (comparison with the original package) showed this indirectly (C03 EVAL_1 D4). Real
shared space (session fixture ``space``: the frozen yolo11n / yolo26n templates, CPU builds), SYNTHETIC L0 fitness
values; crossover is forced (rate 1.0) and mutation is off, so every non-elite child takes the crossover branch.
"""
from __future__ import annotations

import random

from mbnas_core.search import ga

DEGRADED_NOTE = "crossover drawn but no same-family partner in the pool; degraded to clone"


def _rows(space, per_family: dict[str, int]) -> list[ga.ParentRow]:
    """Committed L0 parent rows built from the space's templates, ``per_family[f]`` rows of family ``f``."""
    by_family: dict[str, list[str]] = {}
    for tid in space.template_ids():
        by_family.setdefault(space.templates[tid].family, []).append(tid)
    rows: list[ga.ParentRow] = []
    for fam in sorted(per_family):
        tids = by_family[fam]
        assert len(tids) >= per_family[fam], (fam, tids)
        for i, tid in enumerate(tids[:per_family[fam]]):
            rows.append(ga.ParentRow(f"{fam}-p{i}", f"ev-{fam}-p{i}", "L0", 0.40 + 0.05 * len(rows), 5.0 + i,
                                     space.templates[tid].genotype))
    return rows


def test_crossover_partners_share_the_template_family_in_a_mixed_pool(space) -> None:
    rows = _rows(space, {"yolo11n": 2, "yolo26n": 2})
    family = {r.candidate_id: r.genotype.family for r in rows}
    assert set(family.values()) == {"yolo11n", "yolo26n"}  # a mixed-family pool
    cfg = ga.GAConfig(population=4, mutation_rate=0.0, crossover_rate=1.0)
    parent_a_families = set()
    for seed in range(6):
        children, record = ga.propose_generation(space, rows, random.Random(seed), cfg)
        assert children[0].elite and children[0].operation == "clone"
        for child in children[1:]:
            assert child.operation == "crossover", (seed, child.operation, child.notes)
            a, b = child.crossover["parent_a"], child.crossover["parent_b"]
            assert a != b and family[a] == family[b] == child.genotype.family, (seed, a, b, child.genotype.family)
            assert [p["candidate_id"] for p in child.parents] == [a, b]
            assert child.selection["parent_a"]["winner"] == a and child.selection["parent_b"]["winner"] == b
            same_family_partners = {c for c, f in family.items() if f == family[a] and c != a}
            assert set(child.selection["parent_b"]["contenders"]) <= same_family_partners, (seed, child.selection)
            assert DEGRADED_NOTE not in child.notes
            parent_a_families.add(family[a])
    assert parent_a_families == {"yolo11n", "yolo26n"}  # the draw really mixed families as parent A


def test_crossover_without_a_same_family_partner_degrades_to_a_clone_that_says_so(space) -> None:
    rows = _rows(space, {"yolo11n": 2, "yolo26n": 1})  # the yolo26n row has no same-family partner
    family = {r.candidate_id: r.genotype.family for r in rows}
    by_id = {r.candidate_id: r for r in rows}
    lone = next(c for c, f in family.items() if f == "yolo26n")
    cfg = ga.GAConfig(population=4, mutation_rate=0.0, crossover_rate=1.0)
    degraded = crossed = 0
    for seed in range(8):
        children, _ = ga.propose_generation(space, rows, random.Random(seed), cfg)
        for child in children[1:]:
            a = child.selection["parent_a"]["winner"]
            if a == lone:
                assert child.operation == "clone" and child.crossover is None, (seed, child.operation)
                assert [p["candidate_id"] for p in child.parents] == [lone]
                assert "parent_b" not in child.selection
                assert DEGRADED_NOTE in child.notes, child.notes
                assert child.genotype.yaml_hash() == by_id[lone].genotype.yaml_hash()  # the clone keeps A's structure
                degraded += 1
            else:
                assert child.operation == "crossover", (seed, child.operation, child.notes)
                assert family[a] == family[child.crossover["parent_b"]] == "yolo11n"
                assert DEGRADED_NOTE not in child.notes
                crossed += 1
    assert degraded > 0 and crossed > 0, (degraded, crossed)  # both branches were taken
