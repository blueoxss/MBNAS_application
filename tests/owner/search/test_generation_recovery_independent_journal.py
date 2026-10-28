"""Journal guards of the evaluation-free generation handler (fix round 1, C04-D1).

``mbnas_core.search.generation.generate`` holds an exclusive non-blocking ``flock`` on ``generation-<seed>.lock`` and
accepts a saved journal only when it belongs to the job's frozen configuration (``protocol_hash`` and ``run_id``). The
two refusals - ``GENERATION_BUSY`` (retryable) and ``GENERATION_STATE_INVALID`` - had no executed test (C04 EVAL_1 D1).
Same plumbing as ``test_generation_recovery_independent.py``: the real generation code with an explicitly stubbed
structure check and model space (nothing is built, trained or evaluated).
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_contracts.schemas.jobs import JobKind, JobSpec
from mbnas_core.search.generation import generate
from mbnas_core.search.space import Proposal, RowOrigin
from mbnas_model_runtime.genotype import BuildReport, Genotype, LayerInfo

SEED = 91


class StubSpace:
    """Unit stub of the model space (no model-runtime claim); values depend only on the generation RNG."""
    variant = "unit_stub"
    fallback_reason = None
    imgsz = 32
    nc = 1
    slots = {"yolo11n": []}
    native_head = {"yolo11n": [[[-1], 1, "Detect", ["nc"]]]}
    templates: dict = {}

    def describe(self):
        return {"version": "unit", "variant": self.variant, "space_hash": "unit-space", "prior_hash": "unit-prior"}

    def sample_prior(self, rng, method):
        n = rng.randrange(10_000_000)
        g = Genotype.from_yaml_dict({"nc": 1, "backbone": [[-1, 1, "Conv", [8 + n, 3, 2]]],
                                     "head": self.native_head["yolo11n"]}, family="yolo11n", template_id="unit")
        rep = BuildReport(ok=True, error=None, imgsz=32, architecture_hash=f"stub-{n}", signature_hash=f"sig-{n}",
                          params=n, layers=[LayerInfo(0, "Conv", -1, 3, 8, 32, 32, 16, 16, 2.0, n),
                                            LayerInfo(1, "Detect", [-1], 8, None, 16, 16, None, None, None, n)],
                          yaml_hash=g.yaml_hash(), built_audit={"built": {"backbone_family": "cnn", "label": "unit"}})
        return Proposal(method=method, operation="random", genotype=g, report=rep,
                        origins=[RowOrigin(kind="random", source="unit", operation="random") for _ in g.rows()])


def _spec(settings, directory: str, *, count: int = 2, run_id: str = "run-generated") -> JobSpec:
    protocol = {"console": {"search": {"evaluation_enabled": False, "candidate_count": count, "max_attempts": 10,
                                       "allow_duplicates": False, "nc": 1, "input_size": 32, "families": ["yolo11n"],
                                       "arms": ["GA"], "requested_method": ["GA"]}}}
    return JobSpec(job_id="job-generated", run_id=run_id, kind=JobKind.PROPOSE_GENERATION,
                   protocol_hash=protocol_hash(protocol), arm="RandomGeneration", search_seed=SEED,
                   params={"protocol": protocol, "generation_only": True,
                           "architecture_dir": str(Path(settings.output_root) / directory)})


def _pass(*args, **kwargs):
    return {"status": "PASS", "diagnostics": [], "test_boundary": "stub"}


def _no_runtime(**kwargs):
    raise AssertionError("a refused attempt must not instantiate the model space")


@pytest.mark.parametrize("variant", ["other_protocol_hash", "other_run_id", "corrupt_journal"])
def test_journal_of_another_configuration_is_refused_without_side_effects(settings, variant: str) -> None:
    """``GENERATION_STATE_INVALID``: a journal written for another frozen configuration (different ``protocol_hash`` or
    ``run_id``), or an unreadable one, is never resumed, rewritten or announced."""
    original = _spec(settings, "identity")
    done = generate(original, lambda m: None, settings=settings, space_factory=lambda **kw: StubSpace(),
                    structure_check=_pass)
    assert len(done["candidates"]) == 2
    journal = Path(original.params["architecture_dir"]) / f"generation-{SEED}.json"
    if variant == "corrupt_journal":
        journal.write_bytes(b'{"version": 1, "run_id": "run-generated", "protocol_hash": ')
        request = original
    elif variant == "other_protocol_hash":
        request = _spec(settings, "identity", count=3)  # same run, seed and directory; another protocol
        assert request.protocol_hash != original.protocol_hash
    else:
        request = _spec(settings, "identity", run_id="run-other")
        assert request.protocol_hash == original.protocol_hash
    before = journal.read_bytes()
    emitted: list = []
    with pytest.raises(JobFailure) as err:
        generate(request, emitted.append, settings=settings, space_factory=_no_runtime, structure_check=_pass)
    assert err.value.reason_code == "GENERATION_STATE_INVALID"
    assert err.value.retryable is False
    assert emitted == []  # no replayed notification of a foreign journal's candidates
    assert journal.read_bytes() == before  # neither the RNG state nor the candidates were rewritten
    if variant != "corrupt_journal":
        assert json.loads(before)["protocol_hash"] == original.protocol_hash
        assert json.loads(before)["run_id"] == original.run_id


def test_concurrent_attempt_holding_the_journal_lock_is_busy_and_retryable(settings) -> None:
    """``GENERATION_BUSY``: while another attempt holds ``generation-<seed>.lock``, a second attempt of the same seed
    fails retryable before it reads or creates the journal; once the lock is released the same request succeeds."""
    request = _spec(settings, "busy")
    arch = Path(request.params["architecture_dir"])
    arch.mkdir(parents=True, exist_ok=True)
    journal = arch / f"generation-{SEED}.json"
    emitted: list = []
    with (arch / f"generation-{SEED}.lock").open("a") as other_attempt:
        fcntl.flock(other_attempt, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(JobFailure) as err:
            generate(request, emitted.append, settings=settings, space_factory=_no_runtime, structure_check=_pass)
        assert err.value.reason_code == "GENERATION_BUSY"
        assert err.value.retryable is True
        assert emitted == [] and not journal.exists()
    # control: the refusal was the lock, nothing else - the released journal lock lets the same request complete
    result = generate(request, emitted.append, settings=settings, space_factory=lambda **kw: StubSpace(),
                      structure_check=_pass)
    assert len(result["candidates"]) == 2 and journal.exists()
    assert json.loads(journal.read_text())["run_id"] == request.run_id
