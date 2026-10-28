"""S10-F5: the real PROPOSE_GENERATION handler (CPU builds, no fitness) produces a shape repair for search seed 116.

Generation 0 of both arms is the shared frozen-prior stream; for seed 116 its candidate index 1 draws C3TR(768) at
row 9, so the C2PSA at row 10 sees c1=192 != c2=256 and the shared repair reverts row 9 to the template SPPF. That
proposal is stored by the engine as a real candidate (live evidence run recorded in docs/ACCEPTANCE_RESULTS.md §15);
this test pins the handler output so the evidence stays reproducible.
"""
from __future__ import annotations

import importlib.resources
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

from mbnas_core.console.projection import fold_block, project_public
from mbnas_core.protocol.presets import pilot_smoke_protocol
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_contracts.schemas.jobs import JobKind, JobSpec
from mbnas_core.search.handlers import propose_generation

CONTRACT = json.loads((importlib.resources.files("mbnas_contracts.openapi") / "openapi.json")
                      .read_text(encoding="utf-8"))  # package data of mbnas_contracts (DD-A10)
SEED = 116


def _gen0(arm: str):
    proto = pilot_smoke_protocol()
    proto["console"]["search"].update(population=2)
    spec = JobSpec(job_id=f"job-{arm}-0", run_id="run-test", kind=JobKind.PROPOSE_GENERATION,
                   protocol_hash=protocol_hash(proto), arm=arm, search_seed=SEED,
                   params={"protocol": proto, "generation": 0, "nc": 1})
    res = propose_generation(spec)
    assert res.status == "SUCCEEDED", res.message
    return res.outputs["candidates"]


# 2026-10-21 (D4): tests that genuinely build real ultralytics models / need the ETRI pool skip (not fail/error) on a
# host without the ml extras. Torch-free tests in this module keep running.
needs_ml = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("ultralytics") is None,
    reason="needs torch + ultralytics (ml extras)")


@needs_ml
def test_seed_116_generation_0_contains_a_c2psa_shape_repair() -> None:
    block_schema = {**CONTRACT["components"]["schemas"]["Block"], "components": CONTRACT["components"]}
    for arm in ("GA", "IndependentRandom"):
        cands = _gen0(arm)
        row = cands[1]["row"]
        events = row["lineage"]["repair_events"]
        assert len(events) == 1, events
        ev = events[0]
        assert ev["row"] == 9 and ev["violation_row"] == 10 and "C2PSA requires c1 == c2" in ev["reason"]
        assert ev["before"][2] == "C3TR" and ev["after"][2] == "SPPF"
        repaired = [b for b in row["blocks"] if b["source_kind"] == "repair"]
        assert len(repaired) == 1, [b["source_kind"] for b in row["blocks"]]
        blk = repaired[0]
        # contract 1.5.0 (DD-A19 D1): the stored row keeps the repair trace (owner data, /core/v1/owner/.../audit);
        # the public Block is its projection: repair folded into its source, the trace members dropped
        public = project_public("Block", fold_block(blk, row["parents"]))
        jsonschema.validate(public, block_schema)
        assert public["source_kind"] in ("parent_a", "parent_b", "random") and public["name"] == "SPPF"
        assert not {"before", "after", "old_name", "repair_reason"} & set(public)
        assert blk["position"] == 9 and blk["name"] == "SPPF" and "C2PSA" in blk["repair_reason"]
        assert blk["before"] is not None and blk["after"] is not None and blk["before"] != blk["after"]
        assert not cands[0]["row"]["lineage"]["repair_events"]
