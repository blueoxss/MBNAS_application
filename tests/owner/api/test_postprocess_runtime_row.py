"""S8-F5: the evaluator's ``postprocess_runtime`` (torchvision_cuda_nms | python_loop | no_nms) is carried onto the
CandidateEvaluation row so the API (snapshot, candidate detail) can show which NMS implementation the latency used.
Stub numbers are SYNTHETIC; the field is carried verbatim from the handler output."""
from __future__ import annotations

import harness
import pytest
from harness import auth

from mbnas_server.api.contract_check import contract_errors
from mbnas_core.orchestration import engine


def test_postprocess_runtime_is_a_row_field_not_a_raw_metric() -> None:
    assert "postprocess_runtime" in engine.METRIC_KEYS
    assert "postprocess_runtime" in engine.RAW_EXCLUDED


@pytest.mark.parametrize("runtime", ["python_loop", "torchvision_cuda_nms", "no_nms"])
def test_api_rows_carry_postprocess_runtime(client, protocol, inline_worker, monkeypatch, runtime: str) -> None:
    monkeypatch.setenv("MBNAS_STUB_POSTPROCESS_RUNTIME", runtime)
    run_id, _ = harness.validate_and_run(client, protocol, key=f"pp-{runtime}")
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    rows = [c for c in snap["candidates"] if c["status"] == "EVALUATED"]
    assert rows and all(r["postprocess_runtime"] == runtime for r in rows), [r.get("postprocess_runtime") for r in rows]
    assert all("postprocess_runtime" not in (r.get("raw_metrics") or {}) for r in rows)
    for r in rows:
        assert contract_errors("CandidateEvaluation", r) == []
    detail = client.get(f"/api/v1/runs/{run_id}/candidates/{rows[0]['candidate_id']}", headers=auth()).json()
    assert contract_errors("CandidateDetail", detail) == []
    evaluated = [e for e in detail["evaluations"] if e["status"] == "EVALUATED"]
    assert evaluated and all(e["postprocess_runtime"] == runtime for e in evaluated)


def test_rows_without_a_postprocess_runtime_omit_the_field(client, protocol, inline_worker, monkeypatch) -> None:
    monkeypatch.delenv("MBNAS_STUB_POSTPROCESS_RUNTIME", raising=False)
    run_id, _ = harness.validate_and_run(client, protocol, key="pp-none")
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    rows = [c for c in snap["candidates"] if c["status"] == "EVALUATED"]
    assert rows and all(r.get("postprocess_runtime") is None for r in rows)
