#!/usr/bin/env python
"""CPU dry run of the PROPOSE_GENERATION handler for both arms on the PILOT_SMOKE preset.

Generation 0 (GA and IndependentRandom, shared frozen-prior sample), then generation 1 of both arms. The GA parent
pool for generation 1 uses **synthetic L0 fitness values** (a fixed function of the candidate index, labelled
``synthetic`` in the output) because no training happens here; the IndependentRandom arm is run twice with two
different synthetic pools to show its proposals do not change. Writes
``/root/detection/MBNAS_runs/evidence/search_dry_run_<ts>.json`` and the candidate YAMLs under
``/root/detection/MBNAS_runs/search_dry_run/<ts>/architectures``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

RUNS = Path("/root/detection/MBNAS_runs")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=101)
    ap.add_argument("--population", type=int, default=4)
    args = ap.parse_args(argv)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(RUNS / "ultralytics_cfg" / "search_dry_run"))
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from mbnas_core.config import CoreSettings as Settings
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_contracts.protocol_hash import protocol_hash
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec
    from mbnas_core.search.handlers import propose_generation

    ts = time.strftime("%Y%m%d_%H%M%S")
    proto = pilot_smoke_protocol()
    proto["console"]["search"]["population"] = args.population
    phash = protocol_hash(proto)
    settings = Settings(output_root=str(RUNS), output_roots=[str(RUNS)])
    arch_dir = RUNS / "search_dry_run" / ts / "architectures"

    def run(arm: str, gen: int, pool: list[dict[str, Any]] | None = None) -> Any:
        params = {"protocol": proto, "generation": gen, "architecture_dir": str(arch_dir)}
        if pool is not None:
            params["parent_pool"] = pool
        spec = JobSpec(job_id=f"dry-{arm}-g{gen}", run_id=f"dryrun-{ts}", kind=JobKind.PROPOSE_GENERATION,
                       protocol_hash=phash, arm=arm, search_seed=args.seed, params=params)
        res = propose_generation(spec, settings=settings)
        if res.status != "SUCCEEDED":
            raise SystemExit(f"{arm} g{gen}: {res.reason_code} {res.message}")
        return res

    def pool(res: Any, fit: list[float]) -> list[dict[str, Any]]:
        return [{"candidate_id": c["row"]["id"], "evaluation_id": f"synthetic-ev-{i}", "rung": "L0",
                 "status": "EVALUATED", "fitness": f, "latency_ms": 10.0 + i, "genotype": c["genotype"],
                 "synthetic": True} for i, (c, f) in enumerate(zip(res.outputs["candidates"], fit))]

    g0 = {arm: run(arm, 0) for arm in ("GA", "IndependentRandom")}
    fit_a = [round(0.1 + 0.15 * i, 3) for i in range(args.population)]
    fit_b = list(reversed(fit_a))
    ga1 = run("GA", 1, pool(g0["GA"], fit_a))
    rnd1a = run("IndependentRandom", 1, pool(g0["IndependentRandom"], fit_a))
    rnd1b = run("IndependentRandom", 1, pool(g0["IndependentRandom"], fit_b))

    def summary(res: Any) -> list[dict[str, Any]]:
        out = []
        for c in res.outputs["candidates"]:
            r = c["row"]
            out.append({"name": r["name"], "id": r["id"], "operation": r["lineage"]["operation"],
                        "parents": r["parents"], "template": r["lineage"]["template"]["id"],
                        "architecture_hash": r["architecture_hash"], "training_seed": r["training_seed"],
                        "label": r["audit"]["label"], "config_backbone": r["audit"]["config_estimate"]["backbone_family"],
                        "params": c["params"], "gflops_at_imgsz": round(c["flops"] / 1e9, 3) if c["flops"] else None,
                        "repairs": len(r["lineage"]["repair_events"]), "mutations": len(r["lineage"]["mutation_events"]),
                        "architecture_path": r["architecture_path"]})
        return out

    same_random = [c["row"]["architecture_hash"] for c in rnd1a.outputs["candidates"]] == \
        [c["row"]["architecture_hash"] for c in rnd1b.outputs["candidates"]]
    evidence = {
        "kind": "search_dry_run", "created_at": ts, "command": [sys.executable, *sys.argv],
        "evidence_type": "synthetic fitness / real CPU builds (no training)", "protocol_hash": phash,
        "space": {k: g0["GA"].outputs["space"][k] for k in ("version", "variant", "space_hash", "prior_hash",
                                                             "templates", "ops_feature", "ops_down", "fallback_reason")},
        "shared_space_hash_across_arms": g0["GA"].outputs["space"]["space_hash"] ==
        g0["IndependentRandom"].outputs["space"]["space_hash"],
        "random_g1_identical_under_different_synthetic_fitness": same_random,
        "synthetic_l0_fitness": {"GA_pool": fit_a, "Random_pool_a": fit_a, "Random_pool_b": fit_b},
        "generations": {"GA_g0": summary(g0["GA"]), "IndependentRandom_g0": summary(g0["IndependentRandom"]),
                        "GA_g1": summary(ga1), "IndependentRandom_g1": summary(rnd1a)},
        "ga_g1_selection": ga1.outputs["ga"],
        "cpu_seconds": {k: v.outputs.get("cpu_seconds") for k, v in
                        {"GA_g0": g0["GA"], "IndependentRandom_g0": g0["IndependentRandom"], "GA_g1": ga1,
                         "IndependentRandom_g1": rnd1a}.items()},
    }
    path = RUNS / "evidence" / f"search_dry_run_{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=1))
    for gen, rows in evidence["generations"].items():
        print(gen)
        for r in rows:
            print(f"  {r['name']:<14} {r['operation']:<9} parents={len(r['parents'])} {r['template']:<28} "
                  f"{r['architecture_hash'][:19]} {r['label']} params={r['params']} repairs={r['repairs']}")
    print(f"shared space hash: {evidence['shared_space_hash_across_arms']}; random g1 independent of fitness: "
          f"{same_random}")
    print(f"evidence: {path}")
    return 0 if same_random and evidence["shared_space_hash_across_arms"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
