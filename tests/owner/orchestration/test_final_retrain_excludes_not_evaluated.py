"""C03-D6 (C03 EVAL_2): a NOT_EVALUATED search row never becomes a final-retrain finalist.

DD-A62 (a) / DD-A63 (a): a CANDIDATE_TRAIN result whose evaluation payload says NOT_EVALUATED (the runner's job device is
not a CUDA device, reason ``EVALUATION_REQUIRES_CUDA``) is stored as ``SEARCH_COMPLETE`` + ``performance_status =
NOT_EVALUATED`` with no metric, latency ``pending`` and a non-immutable row. The frozen finalist rule
(``mbnas_core.orchestration.final_retrain``, ``mbnas-final-select/1``) only takes immutable EVALUATED rows of the LAST
search rung with measured latency, so such a row must never be selected - and with no eligible row the request is
``409 NO_FINALIST`` (no lower rung, baseline or unevaluated row is substituted).

The protocol has ONE rung, so the NOT_EVALUATED rows sit at the last rung itself: the exclusion is exercised by the status
/ immutability / latency filters, not merely by the rung filter. Positive control: in the same setup the measured arm's
rows are selected (the test cannot pass on an empty selection). SYNTHETIC stub handlers, in-process owner worker, the
WP-3 gateway -> core harness (``tests/owner/orchestration/conftest.py``). No GPU.
"""
from __future__ import annotations

from typing import Any

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.orchestration import final_retrain

REASON = "EVALUATION_REQUIRES_CUDA"
FINAL_URL = "/api/v1/runs/{}/final-retrain"
LAST_RUNG = "L0"  # the only (hence last) search rung of the one-rung protocol below


def _not_evaluated(spec: Any, ev: dict[str, Any]) -> dict[str, Any]:
    """The runner's real NOT_EVALUATED payload for this stub snapshot (CPU job device, DD-A60 (4))."""
    from mbnas_runner.evaluation.handlers import latency_device_problem, not_evaluated_payload

    bench = spec.params["protocol"]["console"]["benchmark"]
    sha = ev["checkpoint_sha256"]
    return not_evaluated_payload(reason=latency_device_problem("cpu"), device="cpu", device_entry="cpu",
                                 precision="FP16", eval_batch=8, input_shape=[64, 64], benchmark=bench,
                                 split="search_val", epochs=int(spec.cumulative_target or 1),
                                 eval_ckpt=ev["checkpoint_path"], eval_sha256=sha, checkpoint_path=ev["checkpoint_path"],
                                 checkpoint_sha256=sha)


def _arms_not_evaluated(monkeypatch: pytest.MonkeyPatch, arms: set[str]) -> None:
    """CANDIDATE_TRAIN of ``arms`` returns the NOT_EVALUATED payload (device_used cpu, as the CPU daemon reports)."""
    import stub_handlers

    original = stub_handlers.HANDLERS["CANDIDATE_TRAIN"]

    def train(spec, emit, profile_default_s=None):
        out = original(spec, emit, profile_default_s)
        if spec.arm in arms:
            out = {**out, "evaluation": _not_evaluated(spec, out["evaluation"]), "device_used": "cpu"}
        return out

    monkeypatch.setitem(stub_handlers.HANDLERS, "CANDIDATE_TRAIN", train)


@pytest.fixture
def one_rung_protocol(make_dataset, tmp_roots, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "measured")  # the measured arm satisfies the finalist rule
    ds = make_dataset("fr", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), targets=(1,))
    assert [r["id"] for r in proto["rungs"]] == [LAST_RUNG] and proto["rungs"][0]["promotion"] is None
    return proto


def _completed_parent(client, inline_worker, api_settings, proto, key: str) -> tuple[str, list[dict[str, Any]]]:
    run_id, _ = harness.validate_and_run(client, proto, key=key)
    harness.drain(inline_worker)
    conn = connect(api_settings, init=False)
    try:
        assert repo.get_run(conn, run_id)["status"] == "COMPLETED"  # else the request would fail with INVALID_STATE
        rows = [{**e["row"], "status": e["status"], "immutable": bool(e["immutable"])}
                for e in repo.list_evaluations(conn, run_id, include_superseded=False)]
    finally:
        conn.close()
    return run_id, rows


def _assert_not_evaluated_at_last_rung(rows: list[dict[str, Any]], arm: str) -> set[str]:
    mine = [r for r in rows if r["arm"] == arm]
    assert mine, f"no {arm} rows"
    for r in mine:
        assert r["rung"] == LAST_RUNG and r["status"] == "SEARCH_COMPLETE" and r["immutable"] is False
        assert r["performance_status"] == "NOT_EVALUATED" and r["reason_code"] == REASON
        assert r["ap"] is None and r["latency_source"] == "pending"
        assert r["checkpoint_path"] and r["checkpoint_sha256"]  # trained: a real candidate, just not measured
    return {r["candidate_id"] for r in mine}


def test_not_evaluated_rows_are_never_finalists_but_measured_rows_are(client, inline_worker, api_settings,
                                                                     one_rung_protocol, monkeypatch, stub):
    _arms_not_evaluated(monkeypatch, {"GA"})
    run_id, rows = _completed_parent(client, inline_worker, api_settings, one_rung_protocol, "fr-mixed")
    ga_ids = _assert_not_evaluated_at_last_rung(rows, "GA")
    rnd = [r for r in rows if r["arm"] == "IndependentRandom"]
    assert rnd and all(r["status"] == "EVALUATED" and r["immutable"] and r["rung"] == LAST_RUNG
                       and r["latency_source"] == "measured" for r in rnd)

    conn = connect(api_settings, init=False)
    try:
        parent = repo.get_run(conn, run_id)
        eligible = final_retrain.eligible_rows(conn, parent)
        assert eligible and not {r["candidate_id"] for r in eligible} & ga_ids
        assert {r["arm"] for r in eligible} == {"IndependentRandom"}
    finally:
        conn.close()

    r = client.post(FINAL_URL.format(run_id), json={"idempotency_key": "fr-mixed", "budget_gpu_hours": 0.25},
                    headers=auth())
    assert r.status_code == 200, r.text
    child = client.get(f"/api/v1/runs/{r.json()['run_id']}", headers=auth()).json()
    finalists = child["finalists"]
    # positive control: the measured arm is selected (top 1 per arm and seed) ...
    assert [f["arm"] for f in finalists] == ["IndependentRandom"], finalists
    assert finalists[0]["candidate_id"] in {x["candidate_id"] for x in rnd} and finalists[0]["rung"] == LAST_RUNG
    # ... and no NOT_EVALUATED GA row is a finalist, not even as a substitute for the missing GA measurement
    assert not {f["candidate_id"] for f in finalists} & ga_ids
    assert r.json()["finalist_count"] == 1


def test_only_not_evaluated_search_rows_is_no_finalist(client, inline_worker, api_settings, one_rung_protocol,
                                                       monkeypatch, stub):
    _arms_not_evaluated(monkeypatch, {"GA", "IndependentRandom"})
    run_id, rows = _completed_parent(client, inline_worker, api_settings, one_rung_protocol, "fr-none")
    _assert_not_evaluated_at_last_rung(rows, "GA")
    _assert_not_evaluated_at_last_rung(rows, "IndependentRandom")
    # the baseline is still measured: it is not a search arm and is never substituted either
    assert [r["status"] for r in rows if r["arm"] == "Baseline"] and all(
        r["status"] == "EVALUATED" for r in rows if r["arm"] == "Baseline")

    r = client.post(FINAL_URL.format(run_id), json={"idempotency_key": "fr-none", "budget_gpu_hours": 0.25},
                    headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "NO_FINALIST", r.text
    assert r.json()["message"] == final_retrain.NO_FINALIST_MESSAGE
    conn = connect(api_settings, init=False)
    try:
        assert repo.list_child_runs(conn, run_id) == []
        assert final_retrain.eligible_rows(conn, repo.get_run(conn, run_id)) == []
    finally:
        conn.close()
