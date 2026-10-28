"""scripts/smoke_end_to_end.py ``--final-retrain`` / ``--prune-after`` phases, driven against the in-process API with
SYNTHETIC stub handlers (the script's HTTP client is a TestClient; its polling sleep drains the in-process worker).

This checks the script's own logic and the API it relies on; the LIVE stack + real GPU run belongs to the Integrate
phase and is not claimed here.
"""
from __future__ import annotations

import argparse
import importlib.util
import time
from pathlib import Path

import harness
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "owner" / "smoke_end_to_end.py"  # contract §11


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location("smoke_end_to_end_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_final_retrain_and_prune_after_phases(smoke, app, client, protocol, inline_worker, api_settings, monkeypatch,
                                              tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "measured")
    run_id, _ = harness.validate_and_run(client, protocol, key="smoke-final")
    harness.drain(inline_worker)
    env_file = tmp_path / "smoke.env"
    env_file.write_text(f"MBNAS_CORE_DB_PATH={api_settings.db_path}\nMBNAS_CORE_OUTPUT_ROOT={api_settings.output_root}\n"
                        f"MBNAS_CORE_OUTPUT_ROOTS={','.join(api_settings.output_roots)}\n")  # DD-A13 core prefix
    monkeypatch.setattr(smoke, "EVIDENCE_DIR", tmp_path / "evidence")
    (tmp_path / "evidence").mkdir()
    monkeypatch.setattr(smoke.time, "sleep", lambda _s: harness.drain(inline_worker))
    e2e = smoke.E2E.__new__(smoke.E2E)
    e2e.args = argparse.Namespace(final_retrain=True, final_budget_gpu_hours=0.25, final_max_wall_s=60,
                                  prune_after="last_rung_end_only", env_file=str(env_file))
    e2e.token, e2e.signing_key = harness.TOKEN, harness.SIGNING_KEY
    e2e.http = TestClient(app, headers=harness.auth())
    e2e.base, e2e.checks, e2e.timings, e2e.t0, e2e.report = "http://testserver", [], {}, time.time(), {}
    e2e.run_id = run_id
    e2e.after_completion("COMPLETED")
    failed = [c for c in e2e.checks if not c["passed"]]
    assert not failed, failed
    names = {c["name"] for c in e2e.checks}
    assert {"final_retrain_created", "final_retrain_idempotent_retry", "final_retrain_child_completed",
            "final_rows_rung_group_split", "final_panels_labelled_final_eval", "final_costs_child_and_parent",
            "final_child_export_bundle", "parent_export_with_children_bundle", "final_exports_link_parent_and_child",
            "prune_after_applied", "pruned_checkpoints_shown_as_pruned"} <= names
    summary = e2e.report["prune_after"]["reports"]
    assert [r["run_id"] for r in summary] == [run_id, e2e.final_child_id]
    pruned_check = next(c for c in e2e.checks if c["name"] == "pruned_checkpoints_shown_as_pruned")
    assert pruned_check["states"]["PRUNED"] > 0 and pruned_check["states"]["MISSING"] == 0  # not vacuous


def test_final_phase_reports_a_non_completed_run_as_failed(smoke):
    e2e = smoke.E2E.__new__(smoke.E2E)
    e2e.args = argparse.Namespace(final_retrain=True, prune_after=None)
    e2e.checks, e2e.t0 = [], time.time()
    e2e.after_completion("FAILED")
    assert [(c["name"], c["passed"]) for c in e2e.checks] == [("final_retrain_created", False)]


def test_final_phase_records_the_no_finalist_409(smoke, app, client, protocol, inline_worker, monkeypatch):
    """A parent without an eligible last-rung row: the script records the exact NO_FINALIST 409 (no child created)
    and still fails ``final_retrain_created`` (the final phase did not run)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MBNAS_STUB_LATENCY_SOURCE", "unverified_proxy")  # no measured row -> no finalist
    run_id, _ = harness.validate_and_run(client, protocol, key="smoke-no-finalist")
    harness.drain(inline_worker)
    e2e = smoke.E2E.__new__(smoke.E2E)
    e2e.args = argparse.Namespace(final_retrain=True, final_budget_gpu_hours=0.25, final_max_wall_s=60,
                                  prune_after=None)
    e2e.http = TestClient(app, headers=harness.auth())
    e2e.checks, e2e.timings, e2e.t0, e2e.report = [], {}, time.time(), {}
    e2e.run_id = run_id
    e2e.after_completion("COMPLETED")
    assert [(c["name"], c["passed"]) for c in e2e.checks] == [("final_retrain_no_finalist_reported", True),
                                                               ("final_retrain_created", False)]
    assert e2e.report["final_retrain"]["http"] == 409
    assert e2e.report["final_retrain"]["response"]["code"] == "NO_FINALIST"


def test_evidence_file_name_names_the_plan(smoke, monkeypatch, tmp_path):
    """``--final-retrain`` evidence is ``smoke_e2e_final_<ts>.json``; ``--plan crash`` is ``smoke_e2e_crash_<ts>.json``."""
    class FakeE2E:
        def __init__(self, args):
            self.checks, self.timings, self.report = [{"name": "stub", "passed": True}], {}, {}

        def flow(self):
            return None

    monkeypatch.setattr(smoke, "E2E", FakeE2E)
    monkeypatch.setattr(smoke, "EVIDENCE_DIR", tmp_path)
    for argv in (["--final-retrain"], ["--plan", "crash"], []):
        assert smoke.main(argv) == 0
    names = sorted(p.name for p in tmp_path.glob("*.json"))
    assert [n for n in names if n.startswith("smoke_e2e_final_")], names
    assert [n for n in names if n.startswith("smoke_e2e_crash_")], names
    assert [n for n in names if n[len("smoke_e2e_"):][:1].isdigit()], names


def test_lineage_records_a_failing_candidate_detail_instead_of_aborting(smoke):
    """A non-200 candidate detail fails ``lineage_parent_counts`` with the HTTP code; it does not raise (Integrate
    run run-a614e049808b aborted every later phase with ``KeyError: 'parents'``)."""
    class Resp:
        status_code = 422

        def json(self):
            return {"code": "VALIDATION_FAILED", "errors": [{"field": "lineage.template"}]}

    class Http:
        def get(self, _url):
            return Resp()

    e2e = smoke.E2E.__new__(smoke.E2E)
    e2e.http, e2e.run_id, e2e.checks, e2e.t0, e2e.report = Http(), "run-x", [], time.time(), {}
    e2e.lineage({"candidates": [{"candidate_id": "run-x:GA:s101:g00:c000"}]})
    check = next(c for c in e2e.checks if c["name"] == "lineage_parent_counts")
    assert not check["passed"] and "HTTP 422 VALIDATION_FAILED lineage.template" in check["problems"][0]
