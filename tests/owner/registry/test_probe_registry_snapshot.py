"""S3-F6: the registry snapshot written by ``scripts/probe_registry.py --rebuild-only`` equals ``load_registry()``.

The snapshot used to be built without the TensorRT evidence, so every entry reported ``export_trt`` / host-gpu-0
``NOT_RUN`` with a false reason while the live ``/registry`` said ``VERIFIED_GPU``. CPU only (no GPU is touched).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[3]  # TARGET_ROOT (WP-1 layout)
SCRIPT = SERVER / "scripts" / "owner" / "probe_registry.py"  # contract §11


def _trt_doc(model_name: str, created: str) -> dict:
    """Minimal bench_host_gpu evidence with a BUILT + MEASURED FP16 engine for one official-weights subject."""
    return {"kind": "bench_host_gpu", "created_at": created, "subjects": [{"weights": model_name, "record": {
        "subject": {"name": model_name, "backend": "ultralytics", "imgsz": 640,
                    "source": {"path": f"/qa/weights/{model_name}"}},
        "engines": {"FP16": {"status": "BUILT", "precision": "FP16", "sha256": "sha256:" + "a" * 64, "bytes": 1,
                             "tensorrt_version": "10.0-qa"}},
        "runtimes": {"tensorrt:FP16": {"status": "MEASURED"}},
        "device": {"name": "QA-GPU", "uuid": "GPU-qa"}}}]}


@pytest.mark.slow
@pytest.mark.host_data  # needs the canonical registry probe evidence of a real host
def test_rebuild_only_snapshot_equals_load_registry_including_trt(tmp_path):
    from mbnas_core.registry import latest_probe_path, load_registry

    # WP-1 contract §9: canonical runs root = MBNAS_TEST_RUNS_ROOT of this test run; unreadable -> skip
    canonical_root = os.environ["MBNAS_TEST_RUNS_ROOT"]
    try:
        canonical_probe = latest_probe_path(canonical_root)
    except OSError:
        canonical_probe = None
    if canonical_probe is None:
        pytest.skip("no registry probe evidence on this host")
    root = tmp_path / "runs"
    (root / "registry").mkdir(parents=True)
    (root / "evidence").mkdir()
    shutil.copy(canonical_probe, root / "registry" / canonical_probe.name)
    # newest-by-created_at wins over the canonical bench files, so yolo11n's TRT row must come from this file
    trt_file = root / "evidence" / "bench_host_gpu_qa_29991231T000000.json"
    trt_file.write_text(json.dumps(_trt_doc("yolo11n.pt", "2999-12-31T00:00:00Z")))

    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONPATH": os.pathsep.join(
        str(SERVER / p / "src") for p in ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner"))}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--rebuild-only", "--output-root", str(root)],
                          capture_output=True, text=True, env=env, timeout=600)
    assert proc.returncode in (0, 2), proc.stderr[-4000:]
    snaps = sorted((root / "registry").glob("registry_*.json"))
    assert len(snaps) == 1, proc.stdout[-2000:]
    snapshot = {e["id"]: e for e in json.loads(snaps[0].read_text())["registry"]}
    live = json.loads(json.dumps(load_registry(str(root)), default=str))
    for entry in live.values():  # DD-26 host diagnostic (unreadable canonical root), not part of the probe snapshot
        (entry.get("evidence") or {}).pop("registry_roots_skipped", None)
        entry["reason"] = (entry.get("reason") or "").split("; registry roots skipped")[0]

    assert set(snapshot) == set(live)
    for mid, entry in live.items():
        for key in ("export_trt", "devices"):
            assert snapshot[mid]["capabilities"][key] == entry["capabilities"][key], (mid, key)
    assert snapshot == live
    y = snapshot["yolo11n"]["capabilities"]
    assert y["export_trt"]["status"] == "VERIFIED_GPU" and y["export_trt"]["evidence"] == str(trt_file)
    assert y["devices"]["host-gpu-0"]["status"] == "VERIFIED_TRT"
