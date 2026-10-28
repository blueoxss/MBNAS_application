"""DD-A75 F1 (fix round 7): no host absolute path in the public ``raw_metrics`` of a validation-ON training row.

P2 (T4/T5 attempt_dd_a73) found the runner's absolute paths of ``raw_metrics.resolved_recipe.{data, model, project,
save_dir}`` of validation-ON (NOT_EVALUATED) training rows in the public snapshot, candidate detail and SSE payloads:
``projection._raw_metrics`` mapped the device uuids only and passed every other member through, against
``docs/public/API.md`` section 8 ("paths": no host absolute paths). The fix scrubs every string of the projected
``raw_metrics`` with the module's existing rule (:func:`projection.scrub_deep` / ``scrub_text``: ``_ABS_PATH`` ->
``<path>``, ``/api/v1`` paths kept, keys unchanged).

SYNTHETIC fixture: a stored validation-ON row shaped like the one ``engine.commit_not_evaluated`` writes for a CPU
runner (DD-A62 (a)), with runner paths of an owner host (``/home/...``) and of a container (``/work/runs/...``) in
``resolved_recipe`` - also nested in a dict and a list - projected through every public surface that carries it:

(a) ``Snapshot`` (``candidates[]`` and ``events[].payload``), (b) ``CandidateDetail`` (``evaluations[]``), (c) the SSE
frames of ``routes_stream.format_event`` (the stream route's serializer; ``candidate.updated`` / ``candidate.evaluated``)
and, as a fourth surface, the export row (``project_public("CandidateEvaluation")``): no ``projection._ABS_PATH`` match
outside the kept ``/api/v1`` paths anywhere in the JSON text, and every projected body stays contract-valid;
(d) ``/api/v1/...`` strings and non-path strings (``yolo11n.yaml``, ``FP16``, ``mbnas-mixed-space/3``, a relative path),
numbers, booleans and null survive unchanged, keys are unchanged, device uuids still become ``device_id`` and the stored
row is not modified; (e) negative controls: the fixture holds the paths, and the pre-DD-A75 ``_raw_metrics`` (device-id
mapping only) leaks them on every surface through the same assertions - so the test cannot pass vacuously.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

import projection_world as pw
from mbnas_core.console import projection
from mbnas_core.console.routes_stream import format_event

RUN = "train-0123456789ab"
CAND = "model-0123456789ab"
NOW = "2026-10-28T04:40:00.000000Z"
GPU_UUID = "GPU-00000000-1111-2222-3333-444444444444"
API_URL = f"/api/v1/runs/{RUN}/artifacts/art-0123456789ab"

# runner absolute paths: owner-host shape (T4) and container shape (T5); none may reach a public body
RECIPE_PATHS = {
    "data": "/home/mbnas-owner/datasets/pilot_smoke/data.yaml",
    "model": f"/home/mbnas-owner/mbnas/runtime/runner/work/{RUN}/{CAND}/model.yaml",
    "project": f"/work/runs/{RUN}/{CAND}/ultralytics",
    "save_dir": f"/work/runs/{RUN}/{CAND}/ultralytics/TRAIN",
}
NESTED_PATHS = (f"/work/runs/{RUN}/{CAND}/ultralytics/TRAIN/weights/last.pt", "/tmp/mbnas-runner-cache/labels.cache")
ALL_PATHS = (*RECIPE_PATHS.values(), *NESTED_PATHS)
# strings that are not host paths: kept byte for byte
KEPT = {"cfg_name": "yolo11n.yaml", "precision": "FP16", "search_space": "mbnas-mixed-space/3", "report_url": API_URL,
        "weights_rel": "weights/last.pt", "optimizer": "SGD"}
SCALARS = {"imgsz": 64, "batch": 2, "epochs": 1, "lr0": 0.001, "amp": False, "cfg": None}


def _leaks(text: str) -> list[str]:
    """``projection._ABS_PATH`` matches of ``text`` that are not the kept public ``/api/v1`` paths."""
    return [m.group(0) for m in projection._ABS_PATH.finditer(text)
            if not m.group(0).startswith(projection._PUBLIC_PATH_PREFIXES)]


def stored_row() -> dict[str, Any]:
    """A validation-ON explicit-training row as stored for a CPU runner (NOT_EVALUATED, EVALUATION_REQUIRES_CUDA)."""
    recipe = {**RECIPE_PATHS, **KEPT, **SCALARS,
              "native_housekeeping": {"removed": [NESTED_PATHS[0], "weights/best.pt"], "cache": NESTED_PATHS[1],
                                      "count": 2}}
    raw = {"validation_enabled": True, "device_used": "cpu", "evaluation_status": "NOT_EVALUATED",
           "reason_code": "EVALUATION_REQUIRES_CUDA", "eval_split": "search_val", "epochs": 1, "train_batch": 2,
           "global_step": 2, "samples_seen": 4, "epoch_target_total": 1, "timings": {"val_s": 0.0, "profile_s": 0.0},
           "checkpoint_sha256s": {"training_full": "a" * 64}, "resolved_recipe": recipe,
           "device_uuid": GPU_UUID, "latency": {"manifest": {"uuid": GPU_UUID, "precision": "FP16"}},
           "rung_result": {"rung_end_training_ckpt": f"/work/runs/{RUN}/{CAND}/full_e0001.pt"}}  # owner-only member
    return {"id": f"{CAND}.TRAIN.eval", "run_id": RUN, "candidate_id": CAND, "name": "yolo11n-user", "family": "yolo11",
            "arm": "Baseline", "generation": 0, "rung": "TRAIN", "epochs": 1, "status": "SEARCH_COMPLETE",
            "candidate_status": "SEARCH_COMPLETE", "ap": None, "recall": None, "latency_ms": None, "size_mb": None,
            "fitness": None, "device": "cuda:0", "precision": "FP16", "latency_source": "pending", "parents": [],
            "blocks": [], "architecture_path": f"/home/mbnas-owner/mbnas/runtime/core/runs/{RUN}/{CAND}/model.yaml",
            "checkpoint_path": f"/work/runs/{RUN}/{CAND}/full_e0001.pt", "artifacts": [],
            "objective_hash": "sha256:" + "1" * 64, "evaluation_hash": "pending", "split_hash": None,
            "latency_scope": "end_to_end", "comparison_group": "explicit_training", "performance_status": "NOT_EVALUATED",
            "reason_code": "EVALUATION_REQUIRES_CUDA", "training_mode": "scratch", "source_kind": "user",
            "revision": 1, "raw_metrics": raw}


def _events(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"seq": 7, "type": "candidate.updated", "run_id": RUN, "payload": row, "created_at": NOW},
            {"seq": 8, "type": "candidate.evaluated", "run_id": RUN, "payload": row, "created_at": NOW}]


def _sse_data(frames: str) -> list[dict[str, Any]]:
    return [json.loads(line[len("data: "):]) for line in frames.splitlines() if line.startswith("data: ")]


def project_surfaces(row: dict[str, Any]) -> dict[str, tuple[Any, str, list[dict[str, Any]]]]:
    """Every public body that carries the row's ``raw_metrics``: name -> (projected body, its text, raw_metrics)."""
    snapshot = projection.project_public("Snapshot", {
        "run": {"id": RUN, "status": "COMPLETED", "protocol_hash": "sha256:" + "0" * 64}, "candidates": [row],
        "generations": [], "workers": [], "events": _events(row), "last_seq": 8}, run_id=RUN)
    detail = projection.project_public("CandidateDetail", {
        "candidate_id": CAND, "run_id": RUN, "name": row["name"], "arm": "Baseline", "family": row["family"],
        "architecture_hash": "sha256:" + "2" * 64, "blocks": [], "parents": [], "evaluations": [row],
        "candidate_status": "SEARCH_COMPLETE", "architecture_path": row["architecture_path"]}, run_id=RUN)
    sse_text = "".join(format_event(e) for e in _events(row))  # what GET /runs/{id}/events writes per stored event
    sse = _sse_data(sse_text)
    export = projection.project_public("CandidateEvaluation", row, run_id=RUN)  # export bundle candidates rows
    return {
        "snapshot": (snapshot, json.dumps(snapshot), [snapshot["candidates"][0]["raw_metrics"],
                                                      *(e["payload"]["raw_metrics"] for e in snapshot["events"])]),
        "candidate_detail": (detail, json.dumps(detail), [detail["evaluations"][0]["raw_metrics"]]),
        "sse": (sse, sse_text, [e["payload"]["raw_metrics"] for e in sse]),
        "export": (export, json.dumps(export), [export["raw_metrics"]]),
    }


def _pre_dd_a75_raw_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """``projection._raw_metrics`` before DD-A75: device uuids mapped, every other member passed through."""
    raw = dict(raw)
    uuid = raw.pop("device_uuid", None)
    if uuid and not raw.get("device_id"):
        raw["device_id"] = projection.public_ids().device_id(uuid)
    if isinstance(raw.get("latency"), dict):
        raw["latency"] = projection._uuid_to_device_id(raw["latency"])
    return raw


def test_probe_strings_are_not_this_hosts_name() -> None:
    # precondition of (d): scrub_text also replaces this host's name; a host named like a probe word would make the
    # "kept unchanged" assertions fail for a reason unrelated to paths - name the cause instead
    clash = [(h, s) for h in projection._host_names() for s in KEPT.values() if h in s]
    assert clash == [], f"pick other probe strings on this host: {clash}"


def test_e_fixture_holds_the_runner_paths() -> None:
    row = stored_row()
    found = _leaks(json.dumps(row))
    assert all(p in found for p in ALL_PATHS), (ALL_PATHS, found)
    assert set(row["raw_metrics"]["resolved_recipe"]) >= {"data", "model", "project", "save_dir"}
    assert all(row["raw_metrics"]["resolved_recipe"][k].startswith("/") for k in RECIPE_PATHS)


@pytest.mark.parametrize("surface", ("snapshot", "candidate_detail", "sse", "export"))
def test_e_pre_dd_a75_projection_leaks_on_every_surface(monkeypatch: pytest.MonkeyPatch, surface: str) -> None:
    # mutation control: the same fixture and assertions with the old pass-through detect the F1 leak on each surface
    monkeypatch.setattr(projection, "_raw_metrics", _pre_dd_a75_raw_metrics)
    _, text, raws = project_surfaces(stored_row())[surface]
    found = _leaks(text)
    assert all(p in found for p in ALL_PATHS), (surface, found)
    assert raws and all(r["resolved_recipe"]["data"] == RECIPE_PATHS["data"] for r in raws), surface


@pytest.mark.parametrize("surface", ("snapshot", "candidate_detail", "sse", "export"))
def test_abc_no_absolute_path_in_any_public_surface(surface: str) -> None:
    _, text, raws = project_surfaces(stored_row())[surface]
    assert _leaks(text) == [], (surface, _leaks(text))
    assert all(p not in text for p in ALL_PATHS), surface
    expected = {"snapshot": 3, "candidate_detail": 1, "sse": 2, "export": 1}[surface]
    assert len(raws) == expected and all(isinstance(r.get("resolved_recipe"), dict) for r in raws), (surface, raws)


@pytest.mark.parametrize("surface", ("snapshot", "candidate_detail", "sse", "export"))
def test_d_non_path_values_keys_and_device_ids_survive(surface: str) -> None:
    row = stored_row()
    stored = copy.deepcopy(row)
    _, _, raws = project_surfaces(row)[surface]
    assert row == stored, "the stored row is never rewritten by the projection"
    recipe = stored["raw_metrics"]["resolved_recipe"]
    for raw in raws:
        rr = raw["resolved_recipe"]
        assert set(rr) == set(recipe), "keys unchanged"
        assert {k: rr[k] for k in RECIPE_PATHS} == {k: "<path>" for k in RECIPE_PATHS}
        assert {k: rr[k] for k in KEPT} == KEPT
        assert {k: rr[k] for k in SCALARS} == SCALARS and rr["amp"] is False and rr["cfg"] is None
        assert rr["native_housekeeping"] == {"removed": ["<path>", "weights/best.pt"], "cache": "<path>", "count": 2}
        assert raw["validation_enabled"] is True and raw["device_used"] == "cpu" and raw["eval_split"] == "search_val"
        assert raw["checkpoint_sha256s"] == {"training_full": "a" * 64} and raw["timings"] == {"val_s": 0.0, "profile_s": 0.0}
        assert raw["device_id"] == projection.public_ids().device_id(GPU_UUID) and "device_uuid" not in raw
        assert raw["latency"]["manifest"] == {"device_id": raw["device_id"], "precision": "FP16"}
        assert "rung_result" not in raw and "evaluation_status" not in raw  # owner-only: the allowlist still drops them


def test_projected_bodies_stay_contract_valid(contract150: dict) -> None:
    surfaces = project_surfaces(stored_row())
    errors = {"snapshot": pw.contract_errors_150(contract150, "Snapshot", surfaces["snapshot"][0]),
              "candidate_detail": pw.contract_errors_150(contract150, "CandidateDetail", surfaces["candidate_detail"][0]),
              "export": pw.contract_errors_150(contract150, "CandidateEvaluation", surfaces["export"][0]),
              "sse": [err for e in surfaces["sse"][0] for err in pw.contract_errors_150(contract150, "Event", e)]}
    assert errors == {k: [] for k in errors}, errors
    # the public run-relative path rules of the row itself are unchanged
    row = surfaces["export"][0]
    assert row["checkpoint_path"] == f"{CAND}/full_e0001.pt" and row["architecture_path"] == f"{CAND}/model.yaml"
