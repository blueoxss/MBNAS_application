"""Runner child entry point ``python -m mbnas_runner.child --spec-file <json>`` (WP-1 contract §7, DD-A14).

The child reads a JobSpec JSON file, resolves the handler (here a SYNTHETIC stub module named by
``MBNAS_JOB_HANDLERS_MODULE``), writes JSONL protocol messages to stdout (handler prints go to stderr) and ends with a
``result`` line; exit 0 = SUCCEEDED, 3 otherwise. No database, no core import. CPU only, no model.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

STUB = '''
from mbnas_contracts.jobs_protocol import JobFailure


def train(spec, emit):
    print("library noise on stdout must not break the JSONL protocol")
    if spec.params.get("fail"):
        raise JobFailure("BUILD_INVALID", "synthetic failure", retryable=False)
    target = int(spec.cumulative_target or 1)
    for epoch in range(1, target + 1):
        emit.progress(epoch=epoch, epoch_target_total=target, global_step=epoch * 4, samples_seen=epoch * 32,
                      learning_rate=0.001)
    return {"epochs_done": target, "evaluation": {"ap": 0.1, "synthetic": True}}


HANDLERS = {"CANDIDATE_TRAIN": train}
'''
SPEC = {"job_id": "job-spec-1", "run_id": "run-spec", "kind": "CANDIDATE_TRAIN", "protocol_hash": "sha256:0123456789abcdef",
        "candidate_id": "run-spec.GA-G00-C000", "rung": "L0", "cumulative_target": 2, "fixed_horizon": 4, "arm": "GA",
        "search_seed": 1, "training_seed": 2, "attempt": 1, "params": {"note": "synthetic"}}


def _run(tmp_path: Path, spec: dict | str, *, stub: bool = True) -> tuple[int, list[dict], str]:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(spec if isinstance(spec, str) else json.dumps(spec))
    (tmp_path / "wp1_stub_handlers.py").write_text(STUB)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update({"CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", "MBNAS_HOME": str(tmp_path / "home"),
                "PYTHONPATH": str(tmp_path)})
    if stub:
        env["MBNAS_JOB_HANDLERS_MODULE"] = "wp1_stub_handlers"
    proc = subprocess.run([sys.executable, "-B", "-m", "mbnas_runner.child", "--spec-file", str(spec_file)],
                          capture_output=True, text=True, timeout=120, env=env, cwd=str(tmp_path))
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, lines, proc.stderr


def test_stub_job_succeeds_with_jsonl_progress_and_a_final_result(tmp_path: Path) -> None:
    code, lines, err = _run(tmp_path, SPEC)
    assert code == 0, err
    assert [m["type"] for m in lines] == ["progress", "progress", "result"]
    assert all(m["job_id"] == "job-spec-1" for m in lines)
    assert [m["epoch"] for m in lines[:2]] == [1, 2]
    result = lines[-1]["payload"]
    assert result["status"] == "SUCCEEDED" and result["job_id"] == "job-spec-1"
    assert result["outputs"]["epochs_done"] == 2 and result["outputs"]["evaluation"]["synthetic"] is True
    assert "library noise" in err  # handler stdout is redirected away from the protocol stream


def test_handler_failure_is_a_failed_result_and_exit_3(tmp_path: Path) -> None:
    code, lines, err = _run(tmp_path, {**SPEC, "params": {"fail": True}})
    assert code == 3, err
    result = lines[-1]
    assert result["type"] == "result" and result["payload"]["status"] == "FAILED"
    assert result["payload"]["reason_code"] == "BUILD_INVALID"


@pytest.mark.parametrize("bad", ["{not json", json.dumps({**SPEC, "kind": "NOT_A_KIND"})])
def test_unreadable_spec_is_a_failed_result_and_exit_3(tmp_path: Path, bad: str) -> None:
    code, lines, err = _run(tmp_path, bad)
    assert code == 3, err
    assert lines and lines[-1]["type"] == "result" and lines[-1]["payload"]["status"] == "FAILED"


def test_propose_generation_is_not_a_runner_kind(tmp_path: Path) -> None:
    code, lines, err = _run(tmp_path, {**SPEC, "kind": "PROPOSE_GENERATION", "rung": None, "cumulative_target": None,
                                       "fixed_horizon": None}, stub=False)
    assert code == 3, err
    assert lines[-1]["payload"]["status"] == "FAILED" and lines[-1]["payload"]["reason_code"] == "HANDLER_MISSING"


def test_the_child_imports_no_core_module() -> None:
    code = ("import sys, mbnas_runner.child; bad = sorted(m for m in sys.modules if m.split('.')[0] == 'mbnas_core'); "
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
