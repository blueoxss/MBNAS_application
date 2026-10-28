"""WP-2B: runner protocol models (REMOTE_PROTOCOL_DESIGN 3/4/7), error enum and the generated runner OpenAPI document."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from conftest_common import owner_tool  # DD-A43(b)
from mbnas_contracts import runner_protocol as rp
from mbnas_contracts.schemas.jobs import JobResult, ProgressMessage

ROOT = Path(__file__).resolve().parents[3]
COMMITTED = ROOT / "mbnas_contracts" / "src" / "mbnas_contracts" / "openapi" / "openapi_runner.json"
H = "a" * 64


def _gen():
    spec = importlib.util.spec_from_file_location("gen_runner_openapi", owner_tool("scripts/dev/gen_runner_openapi.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def envelope_dict(attempt: int = 2) -> dict[str, Any]:
    return {
        "envelope_version": 1,
        "job": {"job_id": "job-1", "attempt": attempt, "kind": "CANDIDATE_TRAIN", "lease_expires_at": "2026-10-26T00:01:00Z",
                "leased_at": "2026-10-26T00:00:00Z", "max_attempts": 4, "priority": 100,
                "spec": {"job_id": "job-1", "run_id": "run-1", "kind": "CANDIDATE_TRAIN", "protocol_hash": "sha256:" + H,
                         "candidate_id": "run-1.ga0.g1.c003", "rung": "L0", "cumulative_target": 2, "fixed_horizon": 4,
                         "arm": "GA", "search_seed": 0, "training_seed": 123, "params": {"stage": {"id": "L0"}}}},
        "run": {"run_id": "run-1", "kind": "SEARCH", "protocol_hash": "sha256:" + H, "resolved_protocol": {"a": 1},
                "status_at_claim": "RUNNING", "execution_mode": "PILOT"},
        "candidate": {"candidate_id": "run-1.ga0.g1.c003", "name": "c", "arm": "GA", "family": "YOLO26", "generation": 1,
                      "search_seed": 0, "training_seed": 123, "architecture_hash": "sha256:" + H, "architecture_yaml": "nc: 1",
                      "architecture_path": None, "genotype": {"yaml": {}, "family": "yolo26"},
                      "candidate_status": "PROMOTION_ADMITTED", "current_rung": "L1"},
        "resume": {"recorded_latest": {"id": "ck-1", "path": "/w/ck.pt", "sha256": H, "epoch": 2, "rung": "L0",
                                       "hashes": {}, "state": "PRESENT"},
                   "live_latest": None, "eval_ema": {"id": "ck-2", "path": "/w/ema.pt", "sha256": H, "epoch": 2, "rung": None, "hashes": None, "state": None},
                   "location": {"runner_id": "runner-A", "store": "runner_local", "uri": None}},
        "data_refs": {"dataset_yaml": {"protocol_path": "/srv/x.yaml", "names": {"0": "person"}},
                      "train": {"protocol_root": "/srv/train", "manifest_hash": "sha256:" + H, "allowed": None},
                      "search_val": {"protocol_root": "/srv/val", "manifest_hash": "sha256:" + H, "allowed": None},
                      "final_eval": {"protocol_root": "/srv/test", "manifest_hash": "sha256:" + H, "allowed": False}},
        "versions": {"core_contract": "1.4.3", "progress_version": 1, "checkpoint_format": "mbnas.full_state.v1",
                     "required_runtime": {"mbnas_model_runtime": ">=0.1,<0.2", "mbnas_contracts": ">=0.1,<0.2"}},
        "fence": {"runner_id": "runner-A", "attempt": attempt, "claim_id": "clm-1", "issued_at": "2026-10-26T00:00:00Z"},
        "control": {"lease_ttl_s": 60, "heartbeat_s": 10, "progress_interval_s": 1.0, "kill_grace_s": 10},
    }


def result_commit_dict() -> dict[str, Any]:
    result = JobResult(job_id="job-1", status="SUCCEEDED", attempt=2, outputs={"epochs_done": 2})
    ids = ["art-1"]
    return {"runner_id": "runner-A", "attempt": 2, "ticket": "t", "result": result.model_dump(mode="json"),
            "started_at": "2026-10-26T00:00:01Z", "finished_at": "2026-10-26T00:05:00Z", "cancel_observed": False,
            "runner_shutting_down": False, "artifact_ids": ids, "device_uuid": None,
            "payload_digest": rp.compute_payload_digest(result, "2026-10-26T00:00:01Z", "2026-10-26T00:05:00Z", False, ids)}


def samples() -> dict[type[BaseModel], dict[str, Any]]:
    jw = {"runner_id": "runner-A", "attempt": 2, "ticket": "t"}
    runtime = {"mbnas_runner": "0.1.0", "mbnas_model_runtime": "0.1.0", "mbnas_contracts": "0.1.0", "torch": "2.4", "cuda": None,
               "python": "3.10.12", "envelope_version": 1, "operator_files_sha256": {"ops.py": H}, "parser_source_sha256": H}
    msg = ProgressMessage(type="progress", job_id="job-1", ts="2026-10-26T00:00:02Z", epoch=1).model_dump(mode="json")
    return {
        rp.ErrorBody: {"code": "LEASE_LOST", "message": "x", "request_id": "r", "errors": [{"a": 1}]},
        rp.JobEnvelope: envelope_dict(),
        rp.RuntimeManifest: runtime,
        rp.RegisterRequest: {"runner_id": "runner-A", "runtime": runtime, "kinds": ["EVALUATE"], "host": "h", "device": "cuda:0",
                             "device_uuid": "GPU-1", "device_name": "n", "data_mounts": {"/srv/train": True}},
        rp.RegisterResponse: {"runner_id": "runner-A", "core_contract": "1.4.3", "envelope_version": 1, "lease_ttl_s": 60,
                              "heartbeat_s": 10, "progress_interval_s": 1.0, "max_batch_messages": 200,
                              "max_body_bytes": 33554432, "allowed_kinds": ["EVALUATE"], "artifact_store": "runner_local",
                              "runtime_verification": "verified", "server_time": "t"},
        rp.DeregisterRequest: {"runner_id": "runner-A"},
        rp.ClaimRequest: {"runner_id": "runner-A", "kinds": ["EVALUATE", "BENCHMARK"]},
        rp.ClaimResponse: {"envelope": envelope_dict(), "ticket": "t", "server_time": "t"},
        rp.StartRequest: {**jw, "device_uuid": "GPU-1"},
        rp.StartResponse: {"started_at": "a", "lease_expires_at": "b", "server_time": "c"},
        rp.HeartbeatRequest: {**jw, "progress": {"epoch": 1}, "device_sample": {"util": 3}},
        rp.HeartbeatControl: {"action": "pause", "run_status": "PAUSE_REQUESTED", "stop_requested": True,
                              "prune": [prune_order()]},
        rp.HeartbeatResponse: {"lease_expires_at": "b", "control": {"action": None, "run_status": "RUNNING",
                                                                    "stop_requested": False, "prune": None}, "server_time": "c"},
        rp.ProgressBatch: {**jw, "seq_start": 5, "messages": [msg]},
        rp.ProgressResponse: {"accepted_seq": 5},
        rp.CheckpointReport: {**jw, "checkpoint_id": "ck-1", "candidate_id": "c", "kind": "training_full", "epoch": 1,
                              "rung": "L0", "relpath": "candidates/c/checkpoints/a.pt",
                              "uri": "/w/runs/run-1/candidates/c/checkpoints/a.pt",  # the runner's realpath, no scheme
                              "sha256": H, "bytes": 10, "hashes": {"architecture": H}},
        rp.CheckpointResponse: {"checkpoint_id": "ck-1", "location_id": "loc-1", "state": "PRESENT"},
        rp.ArtifactItem: {"kind": "evaluation", "relpath": "e/evaluation.json", "sha256": H, "bytes": 1, "mime_type": "application/json",
                          "sample_id": None, "split_hash": None, "candidate_id": "c", "evaluation_id": None,
                          "state": "NOT_READY", "meta": {}},
        rp.ArtifactReport: {**jw, "artifacts": [{"kind": "evaluation", "relpath": "e/evaluation.json", "sha256": H, "bytes": 1,
                                                 "mime_type": None, "sample_id": None, "split_hash": None,
                                                 "candidate_id": None, "evaluation_id": None, "state": "NOT_READY", "meta": {}}]},
        rp.ArtifactRegistered: {"relpath": "e/evaluation.json", "artifact_id": "art-1", "state": "NOT_READY"},
        rp.ArtifactResponse: [{"relpath": "e/evaluation.json", "artifact_id": "art-1", "state": "NOT_READY"}],
        rp.ResultCommit: result_commit_dict(),
        rp.ResultResponse: {"outcome": "SUCCEEDED", "job_status": "SUCCEEDED", "run_status": "RUNNING",
                            "response_digest": "sha256:" + H},
        rp.AckRequest: jw,
        rp.PruneOrder: prune_order(),
        rp.PrunedItem: {"relpath": "candidates/c/checkpoints/native_e0001.ckpt", "bytes": 5, "sha256": None},
        rp.PruneReport: prune_report(),
        rp.PruneAck: {"checkpoint_id": "ck-1", "request_id": "prq-1", "state": "PRUNED", "replayed": False},
    }


def prune_order() -> dict[str, Any]:
    return {"request_id": "prq-1", "checkpoint_id": "ck-1", "run_id": "run-1",
            "relpath": "candidates/c/checkpoints/full_e0001.pt", "sha256": H, "bytes": 10,
            "policy": "last_rung_end_only", "reason": "RETENTION_LAST_RUNG_END_ONLY"}


def prune_report() -> dict[str, Any]:
    return {"runner_id": "runner-A", "request_id": "prq-1", "run_id": "run-1",
            "relpath": "candidates/c/checkpoints/full_e0001.pt", "sha256": H, "bytes": 10, "already_absent": False,
            "bytes_freed": 15, "removed": [{"relpath": "candidates/c/checkpoints/full_e0001.pt", "bytes": 10, "sha256": H},
                                           {"relpath": "candidates/c/checkpoints/native_e0001.ckpt", "bytes": 5,
                                            "sha256": None}],
            "detail": None, "pruned_at": "2026-10-28T00:00:00Z"}


def _norm(v: Any) -> Any:
    return json.loads(json.dumps(v))


def test_samples_cover_every_registered_model() -> None:
    assert set(samples()) == set(rp.MODELS)


@pytest.mark.parametrize("model", rp.MODELS, ids=lambda m: m.__name__)
def test_round_trip(model: type[BaseModel]) -> None:
    data = samples()[model]
    obj = model.model_validate(data)
    dumped = obj.model_dump(mode="json")
    assert dumped == _norm(data)
    assert model.model_validate_json(obj.model_dump_json()) == obj
    assert model.model_validate(dumped) == obj


def _all_models() -> set[type[BaseModel]]:
    seen: set[type[BaseModel]] = set()

    def walk(m: type[BaseModel]) -> None:
        if m in seen:
            return
        seen.add(m)
        for f in m.model_fields.values():
            stack = [f.annotation]
            while stack:
                a = stack.pop()
                if isinstance(a, type) and issubclass(a, BaseModel):
                    walk(a)
                stack.extend(getattr(a, "__args__", ()) or ())
    for m in rp.MODELS:
        walk(m)
    return seen


@pytest.mark.parametrize(
    "model", sorted((m for m in _all_models() if m.__module__ == rp.__name__ and m is not rp.ArtifactResponse),
                    key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_extra_is_forbidden_on_every_model(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_artifact_response_items_forbid_extra() -> None:
    with pytest.raises(ValidationError):
        rp.ArtifactResponse.model_validate([{**samples()[rp.ArtifactResponse][0], "surprise": 1}])


@pytest.mark.parametrize("model", [m for m in rp.MODELS if m is not rp.ArtifactResponse], ids=lambda m: m.__name__)
def test_unknown_top_level_field_rejected(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**samples()[model], "surprise": 1})


def test_unknown_nested_field_rejected() -> None:
    d = envelope_dict()
    d["job"]["surprise"] = 1
    with pytest.raises(ValidationError):
        rp.JobEnvelope.model_validate(d)
    d = envelope_dict()
    d["resume"]["eval_ema"]["extra"] = 1
    with pytest.raises(ValidationError):
        rp.JobEnvelope.model_validate(d)


def test_envelope_consistency_and_helpers() -> None:
    env = rp.JobEnvelope.model_validate(envelope_dict(attempt=3))
    spec = env.job_spec({"x": 1})
    assert spec.attempt == 3 and spec.params == {"x": 1} and spec.job_id == "job-1"
    d = envelope_dict()
    d["fence"]["attempt"] = 9
    with pytest.raises(ValidationError):
        rp.JobEnvelope.model_validate(d)
    d = envelope_dict()
    d["job"]["spec"]["job_id"] = "job-other"
    with pytest.raises(ValidationError):
        rp.JobEnvelope.model_validate(d)
    d = envelope_dict()
    d["envelope_version"] = 2
    with pytest.raises(ValidationError):
        rp.JobEnvelope.model_validate(d)


def test_request_models_have_no_path_fields() -> None:
    envelope_side = {"JobEnvelope", "EnvelopeJob", "EnvelopeRun", "EnvelopeCandidate", "CheckpointRef", "ResumeLocation",
                     "EnvelopeResume", "DatasetYamlRef", "SplitRef", "EnvelopeDataRefs", "EnvelopeVersions", "EnvelopeFence",
                     "EnvelopeControl", "ClaimResponse"}
    banned = re.compile(r"(^|_)(path|paths|file|filename|dir|directory)$")
    for model in _all_models():
        if model.__name__ in envelope_side or not model.__module__.endswith("runner_protocol"):
            continue
        for name in model.model_fields:
            assert not banned.search(name), (model.__name__, name)


@pytest.mark.parametrize("bad", ["/abs/x.pt", "../x.pt", "a/../../x", "", "C:\\x", "a\x00b"])
def test_relpath_rejected(bad: str) -> None:
    d = dict(samples()[rp.CheckpointReport], relpath=bad)
    with pytest.raises(ValidationError):
        rp.CheckpointReport.model_validate(d)
    a = samples()[rp.ArtifactReport]
    a["artifacts"][0]["relpath"] = bad
    with pytest.raises(ValidationError):
        rp.ArtifactReport.model_validate(a)


def test_digest_and_hash_formats() -> None:
    d = result_commit_dict()
    d["cancel_observed"] = True  # payload changed, digest stale
    with pytest.raises(ValidationError):
        rp.ResultCommit.model_validate(d)
    d = result_commit_dict()
    d["payload_digest"] = "abc"
    with pytest.raises(ValidationError):
        rp.ResultCommit.model_validate(d)
    ck = dict(samples()[rp.CheckpointReport], sha256="xyz")
    with pytest.raises(ValidationError):
        rp.CheckpointReport.model_validate(ck)
    ck = dict(samples()[rp.CheckpointReport], bytes=-1)
    with pytest.raises(ValidationError):
        rp.CheckpointReport.model_validate(ck)


def test_fence_fields_are_required_on_job_writes() -> None:
    for model in (rp.StartRequest, rp.HeartbeatRequest, rp.ProgressBatch, rp.CheckpointReport, rp.ArtifactReport,
                  rp.ResultCommit, rp.AckRequest):
        for missing in ("runner_id", "attempt", "ticket"):
            d = dict(samples()[model])
            d.pop(missing)
            with pytest.raises(ValidationError):
                model.model_validate(d)
    with pytest.raises(ValidationError):
        rp.StartRequest.model_validate({**samples()[rp.StartRequest], "attempt": 0})


def test_progress_batch_rules() -> None:
    d = samples()[rp.ProgressBatch]
    with pytest.raises(ValidationError):
        rp.ProgressBatch.model_validate({**d, "messages": []})
    bad = ProgressMessage(type="log", job_id="job-1", ts="t", payload={"generation_summary": {"x": 1}}).model_dump(mode="json")
    with pytest.raises(ValidationError):
        rp.ProgressBatch.model_validate({**d, "messages": [bad]})
    with pytest.raises(ValidationError):
        rp.ProgressBatch.model_validate({**d, "messages": [{"type": "nope", "job_id": "j", "ts": "t"}]})


def test_prune_report_consistency() -> None:
    """DD-A66 (1.1.0): an already_absent report deletes nothing; a deletion report lists the ordered payload."""
    ok = prune_report()
    rp.PruneReport.model_validate(ok)
    for bad in ({**ok, "already_absent": True}, {**ok, "removed": []}, {**ok, "bytes_freed": 1},
                {**ok, "run_id": ".."}, {**ok, "relpath": "../x"}, {**ok, "request_id": "a/b"}):
        with pytest.raises(ValidationError):
            rp.PruneReport.model_validate(bad)
    absent = {**ok, "already_absent": True, "removed": [], "bytes_freed": 0}
    assert rp.PruneReport.model_validate(absent).already_absent is True
    with pytest.raises(ValidationError):
        rp.PruneOrder.model_validate({**prune_order(), "relpath": "/abs/x.pt"})


def test_heartbeat_control_actions() -> None:
    for action in (None, "pause", "cancel"):
        rp.HeartbeatControl(action=action, run_status="RUNNING", stop_requested=False)
    with pytest.raises(ValidationError):
        rp.HeartbeatControl(action="resume", run_status="RUNNING", stop_requested=False)


def test_claim_kinds_and_register_defaults() -> None:
    assert set(rp.ClaimRequest(runner_id="r").kinds) == set(rp.ALLOWED_RUNNER_KINDS)
    assert "PROPOSE_GENERATION" not in {k.value for k in rp.ALLOWED_RUNNER_KINDS}
    with pytest.raises(ValidationError):
        rp.ClaimRequest(runner_id="r", kinds=[])
    with pytest.raises(ValidationError):
        rp.ClaimRequest(runner_id="r", kinds=["NOT_A_KIND"])


def test_error_enum_coverage() -> None:
    expected = {"KIND_NOT_ALLOWED", "LEASE_LOST", "RESULT_CONFLICT", "PROCESSING", "COMMIT_FAILED", "RUNNER_MISMATCH",
                "VERSION_MISMATCH", "TICKET_INVALID", "CORE_URL_INSECURE", "AUTH_REQUIRED", "ACCESS_DENIED"}
    assert {e.value for e in rp.RunnerProtocolError} == expected
    assert all(e.name == e.value for e in rp.RunnerProtocolError)
    assert str(rp.RunnerProtocolError.LEASE_LOST) == "LEASE_LOST"
    assert json.dumps(rp.RunnerProtocolError.LEASE_LOST) == '"LEASE_LOST"'
    assert not ({e.value for e in rp.RunnerAuxCode} & expected)


# ------------------------------------------------------------------------------------------------ generated OpenAPI
def test_generated_openapi_equals_committed() -> None:
    assert _gen().render() == COMMITTED.read_text(encoding="utf-8")
    assert _gen().main(["--check"]) == 0


def test_openapi_structure() -> None:
    doc = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert doc["openapi"] == "3.1.0" and doc["info"]["version"] == "1.1.0"
    assert doc["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http", "scheme": "bearer", "description": doc["components"]["securitySchemes"]["BearerAuth"]["description"]}
    assert set(doc["paths"]) == {rp.RUNNER_BASE_PATH + p for p in (
        "/register", "/claim", "/jobs/{job_id}/start", "/jobs/{job_id}/heartbeat", "/jobs/{job_id}/progress",
        "/jobs/{job_id}/checkpoints", "/jobs/{job_id}/artifacts", "/jobs/{job_id}/result", "/jobs/{job_id}/ack", "/deregister",
        "/checkpoints/{checkpoint_id}/pruned")}
    assert sorted(op["x-endpoint"] for p in doc["paths"].values() for op in p.values()) == sorted(f"E{i}" for i in range(1, 12))
    schemas = doc["components"]["schemas"]
    assert set(schemas["RunnerProtocolError"]["enum"]) == {e.value for e in rp.RunnerProtocolError}
    for m in rp.MODELS:
        assert m.__name__ in schemas
    refs = set(re.findall(r'"\$ref": "#/components/schemas/([^"]+)"', COMMITTED.read_text(encoding="utf-8")))
    assert refs <= set(schemas)
    for p in doc["paths"].values():
        for op in p.values():
            assert op["security"] == [{"BearerAuth": []}]
    assert schemas["JobEnvelope"]["additionalProperties"] is False
    assert not any(k.startswith("/api/") for k in doc["paths"])


def test_openapi_is_package_data() -> None:
    toml = (ROOT / "mbnas_contracts" / "pyproject.toml").read_text(encoding="utf-8")
    assert "openapi_runner.json" in toml


def test_module_imports_only_allowed_third_party() -> None:
    src = (ROOT / "mbnas_contracts" / "src" / "mbnas_contracts" / "runner_protocol.py").read_text(encoding="utf-8")
    mods = set(re.findall(r"^(?:from|import)\s+([A-Za-z_][\w]*)", src, re.M))
    assert mods <= {"__future__", "re", "typing", "pydantic", "mbnas_contracts"}
