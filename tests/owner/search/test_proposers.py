"""GA / IndependentRandom proposers and the PROPOSE_GENERATION handler (synthetic fitness values, real CPU builds)."""
from __future__ import annotations

import importlib.resources
import importlib.util
import copy
import inspect
import json
import random
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from mbnas_core.console.projection import fold_block, project_public
from mbnas_core.protocol.presets import pilot_smoke_protocol
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_core.protocol.validator import validate
from mbnas_contracts.schemas.jobs import JobKind, JobSpec
from mbnas_core.search import ga
from mbnas_core.search import random as random_arm
from mbnas_core.search.handlers import propose_generation
from mbnas_contracts.util.canon import canonical_sha256

CONTRACT = json.loads((importlib.resources.files("mbnas_contracts.openapi") / "openapi.json")
                      .read_text(encoding="utf-8"))  # package data of mbnas_contracts (DD-A10)


def _protocol(**search: Any) -> dict[str, Any]:
    p = pilot_smoke_protocol()
    p["console"]["search"].update(search)
    return p


def _spec(protocol: dict[str, Any], arm: str, generation: int, seed: int = 101, **params: Any) -> JobSpec:
    return JobSpec(job_id=f"job-{arm}-{generation}", run_id="run-test", kind=JobKind.PROPOSE_GENERATION,
                   protocol_hash=protocol_hash(protocol), arm=arm, search_seed=seed,
                   params={"protocol": protocol, "generation": generation, "nc": 1, **params})


def _pool(result_outputs: dict[str, Any], fitness: list[float | None], rung: str = "L0") -> list[dict[str, Any]]:
    rows = []
    for i, (cand, fit) in enumerate(zip(result_outputs["candidates"], fitness)):
        rows.append({"candidate_id": cand["row"]["id"], "evaluation_id": f"ev-{i}-{rung}", "rung": rung,
                     "status": "EVALUATED", "fitness": fit, "latency_ms": 10.0 + i, "genotype": cand["genotype"]})
    return rows


def _hashes(res) -> list[str]:
    return [c["row"]["architecture_hash"] for c in res.outputs["candidates"]]


def test_random_proposer_has_no_fitness_input() -> None:
    assert list(inspect.signature(random_arm.propose_generation).parameters) == ["space", "rng", "population"]


# 2026-10-21 (D4): tests that genuinely build real ultralytics models / need the ETRI pool skip (not fail/error) on a
# host without the ml extras. Torch-free tests in this module keep running.
needs_ml = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("ultralytics") is None,
    reason="needs torch + ultralytics (ml extras)")


@needs_ml
def test_random_sequence_independent_of_observed_fitness() -> None:
    proto = _protocol(population=4)
    g0 = propose_generation(_spec(proto, "GA", 0))
    assert g0.status == "SUCCEEDED", g0.message
    seqs = []
    for fit in ([0.9, 0.1, 0.5, 0.3], [0.0, 0.0, 0.0, 1.0], [None, 0.2, 0.7, 0.4]):
        res = propose_generation(_spec(proto, "IndependentRandom", 1, parent_pool=_pool(g0.outputs, fit)))
        assert res.status == "SUCCEEDED", res.message
        seqs.append(_hashes(res))
    no_pool = propose_generation(_spec(proto, "IndependentRandom", 1))
    assert seqs[0] == seqs[1] == seqs[2] == _hashes(no_pool)
    assert all(c["row"]["parents"] == [] for c in no_pool.outputs["candidates"])
    assert all(b["source_kind"] in {"random", "repair"} for c in no_pool.outputs["candidates"] for b in c["row"]["blocks"])


@needs_ml
def test_same_seed_reproducible_and_seed_changes_sequence() -> None:
    proto = _protocol(population=4)
    a = propose_generation(_spec(proto, "IndependentRandom", 2))
    b = propose_generation(_spec(proto, "IndependentRandom", 2))
    c = propose_generation(_spec(proto, "IndependentRandom", 2, seed=202))
    assert _hashes(a) == _hashes(b) and _hashes(a) != _hashes(c)


def test_ga_parent_pool_accepts_only_committed_l0_rows(space) -> None:
    props = random_arm.propose_generation(space, random.Random(5), 3)
    rows = [ga.ParentRow(f"c{i}", f"e{i}", "L0", 0.1 * i, 5.0, p.genotype) for i, p in enumerate(props)]
    cfg = ga.GAConfig(population=3, mutation_rate=0.0, crossover_rate=0.0)
    with pytest.raises(ga.ParentPoolError, match="only immutable L0"):
        ga.propose_generation(space, rows + [ga.ParentRow("c9", "e9-L1", "L1", 0.99, 1.0, props[0].genotype)],
                              random.Random(1), cfg)
    with pytest.raises(ga.ParentPoolError, match="not committed"):
        ga.propose_generation(space, rows + [ga.ParentRow("c8", "e8", "L0", 0.5, 1.0, props[0].genotype,
                                                          status="TRAINING")], random.Random(1), cfg)
    children, record = ga.propose_generation(space, rows + [ga.ParentRow("c7", "e7", "L0", None, None,
                                                                          props[0].genotype)], random.Random(1), cfg)
    assert record["excluded"][0]["candidate_id"] == "c7"
    assert {p["candidate_id"] for ch in children for p in ch.parents} <= {"c0", "c1", "c2"}
    assert children[0].elite and children[0].parents[0]["candidate_id"] == "c2"  # highest L0 fitness


@needs_ml
def test_ga_handler_rejects_l1_rows_in_parent_pool() -> None:
    proto = _protocol(population=3)
    g0 = propose_generation(_spec(proto, "GA", 0))
    res = propose_generation(_spec(proto, "GA", 1, parent_pool=_pool(g0.outputs, [0.3, 0.2, 0.1], rung="L1")))
    assert res.status == "FAILED" and res.reason_code == "PARENT_POOL_INVALID" and "L0" in res.message


def test_parent_counts_crossover_two_clone_one_random_zero(space) -> None:
    props = [space.sample_prior(random.Random(s), method="GA") for s in (11, 12, 13, 14)]
    by_family: dict[str, list] = {}
    for p in props:
        by_family.setdefault(p.genotype.family, []).append(p)
    fam_props = max(by_family.values(), key=len)
    if len(fam_props) < 2:
        fam_props = [space.sample_prior(random.Random(100 + i), method="GA") for i in range(6)]
        fam_props = [p for p in fam_props if p.genotype.family == fam_props[0].genotype.family]
    rows = [ga.ParentRow(f"p{i}", f"e{i}", "L0", 0.5 + 0.01 * i, 4.0, p.genotype) for i, p in enumerate(fam_props)]
    cross, _ = ga.propose_generation(space, rows, random.Random(3),
                                     ga.GAConfig(population=4, mutation_rate=0.0, crossover_rate=1.0))
    assert cross[0].operation == "clone" and cross[0].elite and len(cross[0].parents) == 1
    for child in cross[1:]:
        assert child.operation == "crossover"
        ids = [p["candidate_id"] for p in child.parents]
        assert len(ids) == 2 and len(set(ids)) == 2
        cut = child.crossover["cut_index"]
        kinds = [o.kind for o in child.origins]
        assert all(k in ("parent_a", "repair") for k in kinds[:cut])
        assert all(k in ("parent_b", "repair") for k in kinds[cut:])
    clones, _ = ga.propose_generation(space, rows, random.Random(3),
                                      ga.GAConfig(population=3, mutation_rate=0.0, crossover_rate=0.0))
    assert all(c.operation == "clone" and len(c.parents) == 1 for c in clones)
    by_id = {r.candidate_id: r for r in rows}
    for c in clones:  # a clone keeps its parent's built structure
        assert c.report.architecture_hash == space.inspect(by_id[c.parents[0]["candidate_id"]].genotype).architecture_hash
    muts, _ = ga.propose_generation(space, rows, random.Random(4),
                                    ga.GAConfig(population=4, mutation_rate=1.0, crossover_rate=0.0))
    for child in muts[1:]:
        assert child.operation == "mutation" and len(child.parents) == 1
        ev = child.mutation_events[0]
        assert ev["before"] != ev["after"] and isinstance(ev["structural_noop"], bool)
        assert child.origins[ev["row"]].kind in {"mutation", "repair"}


@needs_ml
def test_protocol_hash_shared_by_both_arms(make_dataset, tmp_roots, ctx_factory) -> None:
    paths = make_dataset()
    req = pilot_smoke_protocol()
    req["console"]["paths"] = {k: paths[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    req["console"]["paths"]["output_root"] = str(tmp_roots["runs"] / "out")
    result = validate(req, ctx_factory())
    assert result.valid, result.errors
    resolved = result.resolved
    before = canonical_sha256(resolved)
    outs = {}
    for arm in ("GA", "IndependentRandom"):
        spec = JobSpec(job_id=f"j-{arm}", run_id="run-x", kind=JobKind.PROPOSE_GENERATION,
                       protocol_hash=result.protocol_hash, arm=arm, search_seed=101,
                       params={"protocol": resolved, "generation": 0})
        assert spec.params["protocol"] is resolved
        res = propose_generation(spec)
        assert res.status == "SUCCEEDED", res.message
        outs[arm] = res.outputs
    assert canonical_sha256(resolved) == before  # handler never mutates the resolved protocol
    assert outs["GA"]["protocol_hash"] == outs["IndependentRandom"]["protocol_hash"] == result.protocol_hash
    for key in ("space_hash", "prior_hash"):
        assert outs["GA"]["space"][key] == outs["IndependentRandom"]["space"][key]
    assert _hashes_o(outs["GA"]) == _hashes_o(outs["IndependentRandom"])  # shared generation-0 prior sample
    bad = JobSpec(job_id="j-bad", run_id="run-x", kind=JobKind.PROPOSE_GENERATION, protocol_hash="sha256:" + "0" * 64,
                  arm="GA", search_seed=101, params={"protocol": resolved, "generation": 0})
    res = propose_generation(bad)
    assert res.status == "FAILED" and res.reason_code == "PROTOCOL_HASH_MISMATCH"


def _hashes_o(outputs: dict[str, Any]) -> list[str]:
    return [c["row"]["architecture_hash"] for c in outputs["candidates"]]


@needs_ml
def test_ga_generation_one_end_to_end_and_contract_shapes(tmp_path, settings) -> None:
    proto = _protocol(population=4)
    g0 = propose_generation(_spec(proto, "GA", 0))
    pool = _pool(g0.outputs, [0.2, 0.6, 0.4, 0.1])
    arch_dir = Path(settings.output_root) / "arch"
    res = propose_generation(_spec(proto, "GA", 1, parent_pool=pool, architecture_dir=str(arch_dir)),
                             settings=settings)
    assert res.status == "SUCCEEDED", res.message
    out = res.outputs
    # PILOT_SMOKE pins console.search.crossover_rate 1.0 (smoke value) so every non-elite child is a crossover
    assert out["ga"]["crossover_rate_source"] == "console.search.crossover_rate"
    assert out["ga"]["crossover_rate"] == 1.0 and out["ga"]["crossover_rate_warning"] is None
    assert [c["row"]["lineage"]["operation"] for c in out["candidates"][1:]] == ["crossover"] * 3
    assert out["ga"]["selection"]["elite"] == pool[1]["candidate_id"]
    names = [c["row"]["name"] for c in out["candidates"]]
    assert names == ["GA-G01-C000", "GA-G01-C001", "GA-G01-C002", "GA-G01-C003"]
    block_schema = {**CONTRACT["components"]["schemas"]["Block"], "components": CONTRACT["components"]}
    pool_ids = {r["candidate_id"] for r in pool}
    for cand in out["candidates"]:
        row = cand["row"]
        assert Path(row["architecture_path"]).read_text() == row["architecture_yaml"]
        assert set(row["parents"]) <= pool_ids and len(row["parents"]) == {"crossover": 2}.get(
            row["lineage"]["operation"], 1)
        assert len(row["parent_evaluation_ids"]) == len(row["parents"])
        for block in row["blocks"]:
            # contract 1.5.0: a stored block row is owner data; the public Block is its projection (repair folded into
            # its source; old_name / before / after / repair_reason / params.slot_kind dropped), as every route sends it
            jsonschema.validate(project_public("Block", fold_block(block, row["parents"])), block_schema)
        assert row["audit"]["built"]["label"].endswith("with CNN detection head")
    # training seeds: separate stream, paired across arms at the same (generation, index)
    rnd = propose_generation(_spec(proto, "IndependentRandom", 1))
    assert [c["row"]["training_seed"] for c in out["candidates"]] == \
        [c["row"]["training_seed"] for c in rnd.outputs["candidates"]]
    assert len({c["row"]["training_seed"] for c in out["candidates"]}) == 4


@needs_ml
def test_training_seed_policy_does_not_change_proposals() -> None:
    proto = _protocol(population=3)
    alt = copy.deepcopy(proto)
    alt["training"]["seed_policy"] = {"base_seed": 7}
    a = propose_generation(_spec(proto, "IndependentRandom", 1))
    b = propose_generation(_spec(alt, "IndependentRandom", 1))
    assert _hashes(a) == _hashes(b)
    assert [c["row"]["training_seed"] for c in a.outputs["candidates"]] != \
        [c["row"]["training_seed"] for c in b.outputs["candidates"]]
    assert b.outputs["training_seed_policy"]["base_source"] == "training.seed_policy.base_seed"


@needs_ml
def test_candidate_rows_insert_into_db_unchanged(db) -> None:
    from mbnas_core.db.core import tx
    from mbnas_core.db.repo import create_run, get_candidate, insert_candidate

    proto = _protocol(population=2)
    res = propose_generation(_spec(proto, "GA", 0))
    with tx(db):
        create_run(db, id="run-test", namespace="pilot_smoke", protocol_hash=res.outputs["protocol_hash"],
                   resolved_protocol=proto, requested_protocol=proto, execution_mode="PILOT",
                   idempotency_key="k-1", payload_sha256="sha256:" + "1" * 64)
        for cand in res.outputs["candidates"]:
            insert_candidate(db, run_id="run-test", **cand["row"])
    for cand in res.outputs["candidates"]:
        stored = get_candidate(db, cand["row"]["id"])
        assert stored["architecture_hash"] == cand["row"]["architecture_hash"]
        assert stored["blocks"] == cand["row"]["blocks"] and stored["lineage"] == cand["row"]["lineage"]
        assert stored["parents"] == [] and stored["candidate_status"] == "BUILD_VALID"


def test_ga_crossover_rate_defaults_without_any_setting() -> None:
    """Without console.search.crossover_rate (and no legacy alias) the documented GA default applies, source recorded."""
    from mbnas_core.search.ga import DEFAULT_CROSSOVER_RATE
    from mbnas_core.search.handlers import ga_rates

    proto = _protocol(population=4)
    proto["console"]["search"].pop("crossover_rate")
    rates = ga_rates(proto)
    assert rates["crossover_rate"] == DEFAULT_CROSSOVER_RATE
    assert rates["crossover_rate_source"].startswith("mbnas default")
    assert ga_rates(_protocol(population=4))["crossover_rate"] == 1.0


def test_ga_reads_the_resolved_search_crossover_rate_and_the_legacy_alias_with_a_warning() -> None:
    from mbnas_core.search.handlers import ga_rates

    proto = _protocol(population=4, crossover_rate=0.2)
    assert ga_rates(proto)["crossover_rate"] == 0.2
    proto["console"]["extra"] = {"ga": {"crossover_rate": 0.9}}
    rates = ga_rates(proto)  # the search field wins; the stale alias is reported
    assert rates["crossover_rate"] == 0.2 and "ignored" in rates["crossover_rate_warning"]
    proto["console"]["search"].pop("crossover_rate")
    rates = ga_rates(proto)
    assert rates["crossover_rate"] == 0.9 and rates["crossover_rate_source"].endswith("(legacy alias)")
    assert "legacy alias" in rates["crossover_rate_warning"]

