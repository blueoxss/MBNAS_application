"""Runtime-manifest verification label (C05 D6; design R6, ``mbnas_core.runner_api.routes.runtime_label``). SYNTHETIC.

The operator file hashes a runner reports at ``/register`` are a claim, never trusted: the core labels them
``verified`` (equal to the core's own ``mbnas_model_runtime.operators.operator_files_sha256()``), ``mismatch`` (any
hash, file name or file count differs) or ``unknown`` (nothing reported, or the core cannot compute its own hashes).
Registration is not refused because of the label, and the register answer (``runtime_verification``), the owner
runners row (``runtime.verification``) and the workers row (``runtime_manifest_status``, the public Worker field) carry
the same label; a later registration replaces it.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest
import runner_sim as rs

from mbnas_core.db import repo
from mbnas_core.runner_api import routes

pytestmark = pytest.mark.runner_protocol

EXTRA_FILE = "mbnas_model_runtime/operators/not_shipped.py"


def _own_hashes() -> dict[str, str]:
    from mbnas_model_runtime.operators import operator_files_sha256

    own = operator_files_sha256()
    assert own and all(len(v) == 64 for v in own.values()), own  # precondition: the core hashes its operator files
    return dict(own)


def _variant(hashes: dict[str, str], case: str) -> dict[str, str]:
    out = copy.deepcopy(hashes)
    first = sorted(out)[0]
    if case == "one hash changed":
        out[first] = "0" * 64 if out[first] != "0" * 64 else "1" * 64
    elif case == "one file missing":
        del out[first]
    elif case == "extra file":
        out[EXTRA_FILE] = "a" * 64
    elif case == "nothing reported":
        out = {}
    return out


CASES = [("as reported by the runner", "verified"), ("one hash changed", "mismatch"), ("one file missing", "mismatch"),
         ("extra file", "mismatch"), ("nothing reported", "unknown")]


@pytest.mark.parametrize("case,label", CASES)
def test_runtime_label_unit(case, label):
    assert routes.runtime_label(_variant(_own_hashes(), case)) == label


def test_runtime_label_is_unknown_when_the_core_cannot_hash_its_own_operators(monkeypatch):
    def broken() -> dict[str, str]:
        raise OSError("operator sources unreadable (SYNTHETIC)")

    reported = _own_hashes()
    monkeypatch.setattr("mbnas_model_runtime.operators.operator_files_sha256", broken)
    assert routes.runtime_label(reported) == "unknown"


def _register(core, tokens, db, runtime: dict[str, Any]) -> tuple[str, str, str]:
    """(register answer, owner runners row, workers row) labels of one registration of R1."""
    r = core.post(rs.PREFIX + "/register", json={"runner_id": "R1", "runtime": runtime}, headers=rs.H(tokens["R1"]))
    assert r.status_code == 200, r.text  # the label never refuses a registration
    rows = core.get("/core/v1/owner/runners", headers=rs.H(tokens["admin"]))
    assert rows.status_code == 200, rows.text
    owner = {x["id"]: x for x in rows.json()["runners"]}["R1"]
    assert owner["status"] == "ONLINE" and owner["runtime"]["operator_files_sha256"] == runtime["operator_files_sha256"]
    worker = repo.get_worker(db, "R1")
    assert worker is not None
    return r.json()["runtime_verification"], owner["runtime"]["verification"], worker["row"]["runtime_manifest_status"]


def test_register_labels_the_reported_operator_manifest(core, tokens, db, monkeypatch):
    real = rs.runtime_manifest()  # the runner's real manifest, as mbnas-runner run reports it
    assert real["operator_files_sha256"] == _own_hashes()  # runner and core read the same operator files here
    seen = {}
    for case, label in CASES:
        runtime = {**copy.deepcopy(real), "operator_files_sha256": _variant(real["operator_files_sha256"], case)}
        seen[case] = _register(core, tokens, db, runtime)
        assert seen[case] == (label, label, label), (case, seen[case])
    assert _register(core, tokens, db, real) == ("verified",) * 3  # a later registration replaces the label

    def broken() -> dict[str, str]:
        raise OSError("operator sources unreadable (SYNTHETIC)")

    monkeypatch.setattr("mbnas_model_runtime.operators.operator_files_sha256", broken)
    assert _register(core, tokens, db, real) == ("unknown",) * 3  # the core cannot compare: never "verified"
