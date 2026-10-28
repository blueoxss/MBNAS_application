"""DD-A75 F1b (fix round 7, C02 addendum D9): a raw_metrics string VALUE that is a path is replaced whole.

F1 scrubbed raw_metrics with ``scrub_text``, whose ``_ABS_PATH`` match stops at the first blank: on a spaced install
path (a supported deployment, ``docs/public/INSTALL.md``) ``"/srv/mbnas owner r2c/.../train-x/data.yaml"`` became
``"<path> owner r2c/.../train-x/data.yaml"`` on every public surface, and the F1 tests could not see it because their
detector was the same regex. F1b: inside the raw_metrics projection a string whose first non-blank character is ``/``
(and that is not a public ``/api/v1`` path) becomes ``<path>`` as a whole - spaces, unicode whitespace and unicode
names included - at any depth (dict values and list items; keys unchanged); every other string keeps the ``scrub_text``
rule (a path embedded in longer text: ``_ABS_PATH``).

NON-circular detector: the fixture plants directory-name fragments (``owner r2c``, ``김철수``, ``train-x``, the sandbox
root basename, ...) and the assertions search them - literally and as JSON ``\\u`` escapes (the SSE frames and the export
use ``ensure_ascii``) - in the JSON text of the four surfaces: (a) Snapshot (``candidates[]`` + ``events[].payload``),
(b) CandidateDetail, (c) the SSE frames of ``routes_stream.format_event``, (d) the export row. Also asserted: the four
recipe keys are present with ``<path>``, ``/api/v1`` strings and non-path strings (with blanks) survive, an embedded
blank-free path keeps the regex replacement, the bodies stay contract-valid. Positive controls: the fixture holds every
fragment, the pass-through ``_raw_metrics`` (before DD-A75) leaks every fragment and the F1 ``_raw_metrics``
(``scrub_deep`` only) leaks the tails - on all four surfaces. SYNTHETIC.
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
NOW = "2026-10-28T05:30:00.000000Z"
GPU_UUID = "GPU-00000000-1111-2222-3333-555555555555"

SPACED_ROOT = "/srv/mbnas owner r2c"                         # spaced install root (the C02 D9 example)
UNICODE_ROOT = "/home/김철수/mbnas"                          # unicode user directory, no blank
SPACED_UNICODE_ROOT = "/home/mbnas user/김철수"              # a blank BEFORE the unicode name
SANDBOX_BASENAME = "f1b 샌드박스 root"                        # sandbox-style root basename: blank + unicode word
SANDBOX_ROOT = f"/tmp/mbnas sandbox/{SANDBOX_BASENAME}"
RUN_DIR = "runtime/runner/runs/runs/train-x"
RECIPE_PATHS = {
    "data": f"{SPACED_ROOT}/{RUN_DIR}/data.yaml",
    "model": f"{UNICODE_ROOT}/{RUN_DIR}/model.yaml",
    "project": f"  {SANDBOX_ROOT}/{RUN_DIR}",                # leading blanks before the path
    "save_dir": f"{SPACED_UNICODE_ROOT}/{RUN_DIR}/TRAIN",
}
FRAGMENTS = ("owner r2c", "김철수", "train-x", SANDBOX_BASENAME, "샌드박스", "작업", "emb-owner", "mbnas user",
             "mbnas sandbox")
F1_TAILS = ("owner r2c", "김철수", "train-x", SANDBOX_BASENAME, "샌드박스", "작업")  # what scrub_deep alone leaves
API_URL = f"/api/v1/runs/{RUN}/artifacts/art-0123456789ab"
KEPT = {"cfg_name": "yolo11n.yaml", "precision": "FP16", "search_space": "mbnas-mixed-space/3", "report_url": API_URL,
        "snapshot_url_blank": f" /api/v1/runs/{RUN}/snapshot", "relative": "weights/last.pt",
        "precision_mode": "torch.autocast(float16) over FP32 weights",
        "size_note": "weights_only fp16 state_dict bytes / 1e6", "note": "trained 1 epoch on cpu"}
SCALARS = {"imgsz": 64, "batch": 2, "lr0": 0.001, "amp": False, "cfg": None}


def _escaped(fragment: str) -> str:
    return json.dumps(fragment)[1:-1]  # the ensure_ascii form (\uXXXX) the SSE frames and the export bundle write


def fragment_hits(*texts: str) -> list[str]:
    """Planted fragments present in any of ``texts`` (literal or JSON-escaped)."""
    return [f for f in FRAGMENTS if any(f in t or _escaped(f) in t for t in texts)]


def stored_row() -> dict[str, Any]:
    recipe = {**RECIPE_PATHS, **KEPT, **SCALARS,
              "native_housekeeping": {
                  "removed": [f"{SPACED_ROOT}/{RUN_DIR}/weights/last.pt (removed after epoch 1)", "weights/best.pt"],
                  "cache": {"labels": f"{SANDBOX_ROOT}/{RUN_DIR}/labels.cache stale since epoch 1", "count": 2},
                  "workdir": f"{SPACED_UNICODE_ROOT} 작업/{RUN_DIR}"}}         # unicode whitespace (NBSP)
    raw = {"validation_enabled": True, "device_used": "cpu", "evaluation_status": "NOT_EVALUATED",
           "reason_code": "EVALUATION_REQUIRES_CUDA", "eval_split": "search_val", "epochs": 1, "train_batch": 2,
           "invalid_reason": "no predictions: /home/emb-owner/val.cache missing",          # embedded, no blank
           "size_definition": "weights_only fp16 state_dict bytes / 1e6",
           "resolved_recipe": recipe, "device_uuid": GPU_UUID,
           "latency": {"manifest": {"uuid": GPU_UUID, "engine": f"{SPACED_ROOT}/{RUN_DIR}/model.engine"}}}
    return {"id": f"{CAND}.TRAIN.eval", "run_id": RUN, "candidate_id": CAND, "name": "yolo11n-user", "family": "yolo11",
            "arm": "Baseline", "generation": 0, "rung": "TRAIN", "epochs": 1, "status": "SEARCH_COMPLETE",
            "candidate_status": "SEARCH_COMPLETE", "ap": None, "recall": None, "latency_ms": None, "size_mb": None,
            "fitness": None, "device": "cuda:0", "precision": "FP16", "latency_source": "pending", "parents": [],
            "blocks": [], "architecture_path": f"/srv/core/runs/{RUN}/{CAND}/model.yaml",
            "checkpoint_path": f"/srv/runner/work/runs/{RUN}/{CAND}/full_e0001.pt", "artifacts": [],
            "objective_hash": "sha256:" + "1" * 64, "evaluation_hash": "pending", "split_hash": None,
            "latency_scope": "end_to_end", "comparison_group": "explicit_training", "performance_status": "NOT_EVALUATED",
            "reason_code": "EVALUATION_REQUIRES_CUDA", "training_mode": "scratch", "source_kind": "user",
            "revision": 1, "raw_metrics": raw}


def _events(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"seq": 7, "type": "candidate.updated", "run_id": RUN, "payload": row, "created_at": NOW},
            {"seq": 8, "type": "candidate.evaluated", "run_id": RUN, "payload": row, "created_at": NOW}]


def project_surfaces(row: dict[str, Any]) -> dict[str, tuple[Any, list[str], list[dict[str, Any]]]]:
    """name -> (projected body, its JSON texts (UTF-8 and ASCII-escaped / the SSE frames as sent), raw_metrics)."""
    snapshot = projection.project_public("Snapshot", {
        "run": {"id": RUN, "status": "COMPLETED", "protocol_hash": "sha256:" + "0" * 64}, "candidates": [row],
        "generations": [], "workers": [], "events": _events(row), "last_seq": 8}, run_id=RUN)
    detail = projection.project_public("CandidateDetail", {
        "candidate_id": CAND, "run_id": RUN, "name": row["name"], "arm": "Baseline", "family": row["family"],
        "architecture_hash": "sha256:" + "2" * 64, "blocks": [], "parents": [], "evaluations": [row],
        "candidate_status": "SEARCH_COMPLETE", "architecture_path": row["architecture_path"]}, run_id=RUN)
    frames = "".join(format_event(e) for e in _events(row))
    sse = [json.loads(line[len("data: "):]) for line in frames.splitlines() if line.startswith("data: ")]
    export = projection.project_public("CandidateEvaluation", row, run_id=RUN)

    def texts(body: Any) -> list[str]:
        return [json.dumps(body, ensure_ascii=False), json.dumps(body)]

    return {
        "snapshot": (snapshot, texts(snapshot), [snapshot["candidates"][0]["raw_metrics"],
                                                 *(e["payload"]["raw_metrics"] for e in snapshot["events"])]),
        "candidate_detail": (detail, texts(detail), [detail["evaluations"][0]["raw_metrics"]]),
        "sse": (sse, [frames, json.dumps(sse, ensure_ascii=False)], [e["payload"]["raw_metrics"] for e in sse]),
        "export": (export, texts(export), [export["raw_metrics"]]),
    }


def _pass_through_raw_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """``_raw_metrics`` before DD-A75: device uuids mapped, every other member passed through."""
    raw = dict(raw)
    uuid = raw.pop("device_uuid", None)
    if uuid and not raw.get("device_id"):
        raw["device_id"] = projection.public_ids().device_id(uuid)
    if isinstance(raw.get("latency"), dict):
        raw["latency"] = projection._uuid_to_device_id(raw["latency"])
    return raw


def _f1_raw_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """``_raw_metrics`` of DD-A75 F1 (tree 26e02259...): the pass-through plus ``scrub_deep`` (regex only)."""
    return projection.scrub_deep(_pass_through_raw_metrics(raw))


SURFACES = ("snapshot", "candidate_detail", "sse", "export")


def test_probe_strings_are_not_this_hosts_name() -> None:
    clash = [(h, s) for h in projection._host_names() for s in KEPT.values() if h in s]
    assert clash == [], f"pick other probe strings on this host: {clash}"


def test_positive_control_fixture_holds_every_fragment() -> None:
    row = stored_row()
    assert fragment_hits(json.dumps(row, ensure_ascii=False)) == list(FRAGMENTS)
    assert fragment_hits(json.dumps(row)) == list(FRAGMENTS)  # the escaped forms are found too


@pytest.mark.parametrize("surface", SURFACES)
def test_positive_control_pass_through_leaks_every_fragment(monkeypatch: pytest.MonkeyPatch, surface: str) -> None:
    monkeypatch.setattr(projection, "_raw_metrics", _pass_through_raw_metrics)
    _, texts, _ = project_surfaces(stored_row())[surface]
    assert fragment_hits(*texts) == list(FRAGMENTS), surface


@pytest.mark.parametrize("surface", SURFACES)
def test_positive_control_f1_regex_scrub_leaks_the_tails(monkeypatch: pytest.MonkeyPatch, surface: str) -> None:
    # C02 D9: what F1 shipped - the regex stops at the first blank, the tail of a spaced path stays
    monkeypatch.setattr(projection, "_raw_metrics", _f1_raw_metrics)
    _, texts, raws = project_surfaces(stored_row())[surface]
    assert set(F1_TAILS) <= set(fragment_hits(*texts)), (surface, fragment_hits(*texts))
    assert all(r["resolved_recipe"]["data"] == f"<path> owner r2c/{RUN_DIR}/data.yaml" for r in raws), surface


@pytest.mark.parametrize("surface", SURFACES)
def test_no_planted_fragment_in_any_public_surface(surface: str) -> None:
    _, texts, raws = project_surfaces(stored_row())[surface]
    assert fragment_hits(*texts) == [], (surface, fragment_hits(*texts))
    assert len(raws) == {"snapshot": 3, "candidate_detail": 1, "sse": 2, "export": 1}[surface]


@pytest.mark.parametrize("surface", SURFACES)
def test_path_values_become_path_token_and_the_rest_survives(surface: str) -> None:
    row = stored_row()
    stored = copy.deepcopy(row)
    _, _, raws = project_surfaces(row)[surface]
    assert row == stored, "the stored row is never rewritten by the projection"
    recipe = stored["raw_metrics"]["resolved_recipe"]
    for raw in raws:
        rr = raw["resolved_recipe"]
        assert set(rr) == set(recipe), "keys unchanged"
        assert {k: rr[k] for k in RECIPE_PATHS} == {k: "<path>" for k in RECIPE_PATHS}
        assert {k: rr[k] for k in KEPT} == KEPT, "api paths and non-path strings (with blanks) are unchanged"
        assert {k: rr[k] for k in SCALARS} == SCALARS and rr["amp"] is False and rr["cfg"] is None
        assert rr["native_housekeeping"] == {"removed": ["<path>", "weights/best.pt"],
                                             "cache": {"labels": "<path>", "count": 2}, "workdir": "<path>"}
        assert raw["invalid_reason"] == "no predictions: <path> missing"  # embedded path: the _ABS_PATH rule
        assert raw["size_definition"] == "weights_only fp16 state_dict bytes / 1e6"
        assert raw["latency"]["manifest"] == {"device_id": projection.public_ids().device_id(GPU_UUID), "engine": "<path>"}
        assert raw["device_id"] == projection.public_ids().device_id(GPU_UUID) and "device_uuid" not in raw


def test_projected_bodies_stay_contract_valid(contract150: dict) -> None:
    surfaces = project_surfaces(stored_row())
    errors = {"snapshot": pw.contract_errors_150(contract150, "Snapshot", surfaces["snapshot"][0]),
              "candidate_detail": pw.contract_errors_150(contract150, "CandidateDetail", surfaces["candidate_detail"][0]),
              "export": pw.contract_errors_150(contract150, "CandidateEvaluation", surfaces["export"][0]),
              "sse": [err for e in surfaces["sse"][0] for err in pw.contract_errors_150(contract150, "Event", e)]}
    assert errors == {k: [] for k in errors}, errors
