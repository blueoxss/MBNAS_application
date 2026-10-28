"""schemas.jobs: JobSpec/JobResult/ProgressMessage contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mbnas_contracts.schemas.jobs import DB_JOB_STATUSES, JobKind, JobResult, JobSpec, JobStatus, ProgressMessage


def test_job_kinds() -> None:
    assert [k.value for k in JobKind] == ["BASELINE_TRAIN", "CANDIDATE_TRAIN", "EVALUATE", "BENCHMARK",
                                          "FINAL_RETRAIN", "PROPOSE_GENERATION"]
    assert [s.value for s in JobStatus] == ["QUEUED", "LEASED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED",
                                            "BLOCKED"]
    assert DB_JOB_STATUSES == ("QUEUED", "LEASED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")  # no BLOCKED column value


def test_training_job_requires_rung_fields() -> None:
    ok = JobSpec(job_id="j", run_id="r", kind="CANDIDATE_TRAIN", protocol_hash="sha256:p", candidate_id="c",
                 rung="L0", cumulative_target=2, fixed_horizon=6)
    assert ok.attempt == 1
    with pytest.raises(ValidationError):
        JobSpec(job_id="j", run_id="r", kind="CANDIDATE_TRAIN", protocol_hash="p", candidate_id="c", rung="L0")
    with pytest.raises(ValidationError):
        JobSpec(job_id="j", run_id="r", kind="BASELINE_TRAIN", protocol_hash="p", candidate_id="c", rung="L2",
                cumulative_target=8, fixed_horizon=6)
    JobSpec(job_id="j", run_id="r", kind="PROPOSE_GENERATION", protocol_hash="p", params={"generation": 1})
    with pytest.raises(ValidationError):
        JobSpec(job_id="j", run_id="r", kind="EVALUATE", protocol_hash="p", unknown=1)


def test_job_result_failure_needs_reason() -> None:
    JobResult(job_id="j", status="SUCCEEDED", gpu_seconds=1.5)
    with pytest.raises(ValidationError):
        JobResult(job_id="j", status="FAILED")
    JobResult(job_id="j", status="FAILED", reason_code="TRAIN_FAILED", exit_code=1)
    with pytest.raises(ValidationError):
        JobResult(job_id="j", status="RUNNING", reason_code="x")
    assert JobResult(job_id="j", status="BLOCKED", reason_code="TARGET_BLOCKED").status == JobStatus.BLOCKED
    with pytest.raises(ValidationError):
        JobResult(job_id="j", status="BLOCKED")  # BLOCKED needs its reason


def test_progress_line_roundtrip() -> None:
    msg = ProgressMessage(type="progress", job_id="j", ts="2026-10-06T00:00:00.000000Z", epoch=1,
                          epoch_target_total=2, global_step=4, learning_rate=0.001)
    line = msg.to_line()
    assert "\n" not in line and ProgressMessage.parse_line(line) == msg
    with pytest.raises(ValueError):
        ProgressMessage.parse_line("not json")
    with pytest.raises(ValidationError):
        ProgressMessage.parse_line('{"type":"bogus","job_id":"j","ts":"t"}')
