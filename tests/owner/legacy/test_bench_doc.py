"""docs/BENCHMARK_HOST_GPU.md must be rendered from the newest full-matrix record of every subject (criterion set 2,
finding S2-F8 b).

Two gap-closing agents worked on the RF-DETR latency path in parallel: the bench implementer rendered the document from
``bench_host_gpu_rfdetr_20261007_003229.json`` (``postprocess_operating_point.latency.applied_by_adapter = false``,
written before ``RfdetrAdapter.latency_fns(postprocess=)`` existed); the integration re-measured on the final code
(``..._021900.json``, ``applied_by_adapter = true``) without re-rendering, because ``--write-doc`` rendered only the new
file and would have dropped the YOLO/RT-DETR subjects. The document kept the superseded numbers.

* ``test_select_latest_*`` / ``test_render_latest_*`` (unit): synthetic evidence files in ``tmp_path``.
* ``test_doc_*`` (docs): the real document against the real evidence directory (skipped when there is none).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy_records  # DD-A22: record test of an earlier integration round; excluded by default

SERVER = Path(__file__).resolve().parents[3]  # TARGET_ROOT (WP-1 layout)
SCRIPT = SERVER / "scripts" / "runner" / "bench_host_gpu.py"  # contract §11
DOC = SERVER / "docs" / "BENCHMARK_HOST_GPU.md"
# 2026-10-15: host root injectable via MBNAS_TEST_ROOT
EVIDENCE = Path(os.environ["MBNAS_TEST_RUNS_ROOT"]) / "evidence"  # WP-1 contract §9


def _load_script():
    spec = importlib.util.spec_from_file_location("bench_host_gpu_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record(weights: str, *, applied: bool | None, tensorrt: bool = True, p50: float = 1.0) -> dict:
    runtimes = {"pytorch:FP32": {"status": "MEASURED", "accuracy": {"ap_for_delta": 0.5, "ap_50": 0.7, "recall": 0.6}}}
    if tensorrt:
        runtimes["tensorrt:FP16"] = {"status": "MEASURED", "accuracy": {"ap_for_delta": 0.5, "ap_50": 0.7, "recall": 0.6},
                                     "accuracy_delta_vs_reference": {"ap_delta": 0.0, "ap_50_delta": 0.0,
                                                                     "recall_delta": 0.0}}
    rec = {"device": {"name": "GPU", "uuid": "GPU-0", "host_runtime": {"torch": {"version": "2.6.0"}}},
           "subject": {"backend": "ultralytics", "postprocess": {"nms": True},
                       "source": {"path": f"/w/{weights}", "sha256": "0" * 64, "kind": "official_weights",
                                  "family": "YOLO11"}},
           "batch": 1, "input_shape": [1, 3, 64, 64], "warmup": 1, "repeats": 3, "sample_image": "/s/a.jpg",
           "split": {"root": "/s", "images": 1, "split_hash": "sha256:" + "1" * 64},
           "export": {"path": f"/o/{weights}.onnx", "sha256": "2" * 64, "opset": 17}, "engines": {},
           "latency_table": [{"runtime": "pytorch", "precision": "FP32", "scope": "forward_only", "p50_ms": p50,
                              "p95_ms": p50, "mean_ms": p50, "fps_batch1": round(1000 / p50, 2)}],
           "runtimes": runtimes, "limitations": ["synthetic"]}
    if applied is not None:
        rec["postprocess_operating_point"] = {"latency": {"applied_by_adapter": applied, "conf": 0.25}}
    return rec


def _write(dirpath: Path, name: str, created_at: str, subjects: list[tuple[str, dict | None, str]]) -> Path:
    path = dirpath / name
    path.write_text(json.dumps({
        "kind": "bench_host_gpu", "created_at": created_at, "subjects": [
            {"weights": w, "record": rec, "job_result": {"status": status, "reason_code": None, "message": None}}
            for w, rec, status in subjects],
        "blocked_targets": [{"status": "BLOCKED", "reason_code": "TARGET_BLOCKED", "message": "not attached",
                             "outputs": {"target": {"device": "jetson-orin-nx"}}}]}))
    return path


@pytest.fixture()
def evidence_dir(tmp_path: Path) -> Path:
    d = tmp_path / "evidence"
    d.mkdir()
    # t1: A (latency operating point NOT applied) + B; t2: A re-measured on new code; t3: a reduced A (no TensorRT);
    # t4: a FAILED A. Expected: B from t1, A from t2.
    _write(d, "bench_host_gpu_20261007_000001.json", "20261007_000001",
           [("a.pt", _record("a.pt", applied=False, p50=9.0), "SUCCEEDED"),
            ("b.pt", _record("b.pt", applied=None, p50=3.0), "SUCCEEDED")])
    _write(d, "bench_host_gpu_rfdetr_20261007_000002.json", "20261007_000002",
           [("a.pt", _record("a.pt", applied=True, p50=7.0), "SUCCEEDED")])
    _write(d, "bench_host_gpu_20261007_000003.json", "20261007_000003",
           [("a.pt", _record("a.pt", applied=True, tensorrt=False, p50=5.0), "SUCCEEDED")])
    _write(d, "bench_host_gpu_20261007_000004.json", "20261007_000004", [("a.pt", None, "FAILED")])
    (d / "qa_etri_hybrid_tensorrt_record.json").write_text("{}")  # not a bench_host_gpu evidence file
    return d


def test_select_latest_keeps_the_newest_full_matrix_record_per_subject(evidence_dir: Path) -> None:
    mod = _load_script()
    selected, superseded = mod.select_latest(evidence_dir)
    assert [(p.name, [s["weights"] for s in ev["subjects"]]) for ev, p in selected] == [
        ("bench_host_gpu_20261007_000001.json", ["b.pt"]),
        ("bench_host_gpu_rfdetr_20261007_000002.json", ["a.pt"])]
    a = selected[1][0]["subjects"][0]["record"]
    assert a["postprocess_operating_point"]["latency"]["applied_by_adapter"] is True
    got = {(s["path"].name, s["weights"]): s["superseded_by"].name for s in superseded}
    assert got == {("bench_host_gpu_20261007_000001.json", "a.pt"): "bench_host_gpu_rfdetr_20261007_000002.json",
                   ("bench_host_gpu_20261007_000003.json", "a.pt"): "bench_host_gpu_rfdetr_20261007_000002.json",
                   ("bench_host_gpu_20261007_000004.json", "a.pt"): "bench_host_gpu_rfdetr_20261007_000002.json"}
    # the selection never mutates what is on disk
    on_disk = json.loads((evidence_dir / "bench_host_gpu_20261007_000001.json").read_text())
    assert [s["weights"] for s in on_disk["subjects"]] == ["a.pt", "b.pt"]


def test_render_latest_lists_superseded_files_and_never_renders_their_numbers(evidence_dir: Path, tmp_path: Path) -> None:
    mod = _load_script()
    doc = tmp_path / "BENCHMARK_HOST_GPU.md"
    assert mod.main(["--render-latest", "--evidence-dir", str(evidence_dir), "--doc", str(doc)]) == 0
    text = doc.read_text()
    assert text.count("## a.pt — job status SUCCEEDED") == 1 and text.count("## b.pt — job status SUCCEEDED") == 1
    assert "| 7.000 |" in text and "| 3.000 |" in text  # newest full-matrix A, and B
    assert "| 9.000 |" not in text and "| 5.000 |" not in text  # superseded A and the reduced A are not rendered
    head, _, rest = text.partition("## a.pt")
    assert "bench_host_gpu_rfdetr_20261007_000002.json" in head and "bench_host_gpu_20261007_000001.json" in head
    sup = text.split("## Superseded evidence", 1)[1]
    assert re.search(r"bench_host_gpu_20261007_000001\.json.*a\.pt.*bench_host_gpu_rfdetr_20261007_000002\.json", sup)
    assert "applied_by_adapter false" in sup  # the reason a reader would care is shown
    # the render is a pure function of the evidence directory
    assert mod.main(["--render-latest", "--evidence-dir", str(evidence_dir), "--doc", str(tmp_path / "again.md")]) == 0
    assert (tmp_path / "again.md").read_text() == text


def test_write_doc_after_a_single_subject_run_keeps_the_other_subjects(evidence_dir: Path, tmp_path: Path) -> None:
    """``--write-doc`` (after a GPU run) used to render only the new evidence file, dropping every other subject."""
    mod = _load_script()
    doc = tmp_path / "doc.md"
    new = _write(evidence_dir, "bench_host_gpu_rfdetr_20261007_000009.json", "20261007_000009",
                 [("a.pt", _record("a.pt", applied=True, p50=6.0), "SUCCEEDED")])
    mod.write_latest_doc(evidence_dir, doc, must_include=new)
    text = doc.read_text()
    assert "| 6.000 |" in text and "| 3.000 |" in text and "| 7.000 |" not in text
    with pytest.raises(ValueError, match="not selected"):
        mod.write_latest_doc(evidence_dir, doc, must_include=evidence_dir / "bench_host_gpu_20261007_000003.json")


def _cited(text: str) -> list[Path]:
    head = text.split("\n## ", 1)[0]
    return [Path(p) for p in re.findall(r"^- `(/[^`]+\.json)`", head, flags=re.M)]


def _full_matrix(rec: dict | None) -> bool:
    return bool(rec) and any(k.startswith("tensorrt:") for k in rec.get("runtimes", {}))


def _evidence_files() -> list[Path]:
    try:
        return sorted(EVIDENCE.glob("bench_host_gpu_*.json"))
    except OSError:  # PermissionError when the original host's /root is unreadable (2026-10-15)
        return []


needs_evidence = pytest.mark.skipif(not _evidence_files() or not DOC.is_file(),
                                    reason="no bench_host_gpu evidence or document on this host")


@needs_evidence
def test_doc_cites_the_newest_full_matrix_record_for_every_subject() -> None:
    """Reproduces S2-F8 b without the new helper: no cited subject may have a newer successful full-matrix record."""
    cited = _cited(DOC.read_text())
    assert cited, "document lists no evidence file"
    newest: dict[str, tuple[str, Path, dict]] = {}
    for path in _evidence_files():
        ev = json.loads(path.read_text())
        for s in ev["subjects"]:
            if s["job_result"]["status"] == "SUCCEEDED" and _full_matrix(s.get("record")):
                if s["weights"] not in newest or ev["created_at"] > newest[s["weights"]][0]:
                    newest[s["weights"]] = (ev["created_at"], path, s["record"])
    stale = []
    for path in cited:
        ev = json.loads(path.read_text())
        for s in ev["subjects"]:
            _, best, rec = newest.get(s["weights"], (None, path, None))
            if best != path:
                old = ((s.get("record") or {}).get("postprocess_operating_point") or {}).get("latency", {})
                new = (rec.get("postprocess_operating_point") or {}).get("latency", {})
                stale.append(f"{path.name}:{s['weights']} (applied_by_adapter {old.get('applied_by_adapter')}) is "
                             f"superseded by {best.name} (applied_by_adapter {new.get('applied_by_adapter')})")
    assert not stale, stale
    assert {s for p in cited for s in (x["weights"] for x in json.loads(p.read_text())["subjects"])} >= set(newest)


@needs_evidence
def test_doc_is_exactly_the_render_of_the_selected_evidence() -> None:
    mod = _load_script()
    selected, superseded = mod.select_latest(EVIDENCE)
    assert DOC.read_text() == mod.render_docs(selected, superseded=superseded)
    for ev, _ in selected:
        for s in ev["subjects"]:
            lat = (s["record"].get("postprocess_operating_point") or {}).get("latency")
            if s["record"]["subject"]["backend"] == "rfdetr":  # RF-DETR has had the operating point since contract 1.2.0
                assert lat and lat["applied_by_adapter"] is True, s["weights"]


def _with_pytorch_latency(rec: dict, fp32: float, fp16: float) -> dict:
    for key, p50 in (("pytorch:FP32", fp32), ("pytorch:FP16", fp16)):
        rt = rec["runtimes"].setdefault(key, {"status": "MEASURED", "accuracy": {"ap_for_delta": 0.5}})
        rt["latency"] = {"scopes": {"forward_only": {"p50_ms": p50}}}
    return rec


def test_render_notes_explain_autocast_fp16_and_the_etri_cpu_nms(tmp_path: Path) -> None:
    """S8-F6: the Notes say pytorch:FP16 is torch.autocast (with the measured FP16-vs-FP32 numbers of the rendered
    records) and that the ETRI hybrid latency is dominated by the notebook's CPU (python_loop) NMS."""
    mod = _load_script()
    rec = _with_pytorch_latency(_record("a.pt", applied=True), fp32=5.39, fp16=7.49)
    path = _write(tmp_path, "bench_host_gpu_20261007_000001.json", "2026-10-07T00:00:01Z", [("a.pt", rec, "SUCCEEDED")])
    text = mod.render_docs([(json.loads(path.read_text()), path)])
    notes = text.split("## Notes", 1)[1]
    assert "pytorch:FP16 is `torch.autocast(float16)` over FP32 weights" in notes
    assert "a.pt 7.490 vs 5.390 ms" in notes and "slower than pytorch:FP32" in notes
    assert "python_loop" in notes and "ETRI" in notes and "CPU" in notes
    assert "preprocess_ms" in notes and "throughput_fps" in notes and "device_sampling" in notes
