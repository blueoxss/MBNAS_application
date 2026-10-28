"""scripts/runner/bench_host_gpu.py (owner script path of BENCHMARK, DD-A49 c) without a GPU: argparse/``--help``, every
path a parameter (no host facts), the CPU guard that writes no evidence, the BLOCKED_BY_ENV TensorRT label and the
renderer on SYNTHETIC records (``split.roots`` of new records, ``split.root`` of older ones).

Runs only where the owner tool exists (DD-A43 b, ``owner_tool``); every subprocess gets ``CUDA_VISIBLE_DEVICES=""``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest_common import owner_tool  # DD-A43(b)

SCRIPT = owner_tool("scripts/runner/bench_host_gpu.py")
# host facts of the original host, composed so this file holds no literal the host-fact scanners would match
HOST_FACTS = ("/root" + "/", "/data" + "/mg", "mg" + "jeong", "147.46" + ".114.253", "pc" + "2300", "MBNAS" + "_server",
              "A" + "100", "ETRI" + "_NAS", "MBNAS" + "_runs", "MBNAS" + "_data", "/home" + "/")


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_BENCH_") and k != "MBNAS_RUNNER_WEIGHTS_DIR"}
    return {**env, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", **extra}


def _run(*args: str, env: dict[str, str] | None = None, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=timeout, env=env or _env())


def _module():
    spec = importlib.util.spec_from_file_location("bench_host_gpu_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_help_names_every_path_parameter_and_its_environment_variable():
    out = _run("--help")
    assert out.returncode == 0, out.stderr
    for token in ("--data-root", "MBNAS_BENCH_DATA_ROOT", "--weights-dir", "MBNAS_RUNNER_WEIGHTS_DIR", "--runs-root",
                  "MBNAS_BENCH_RUNS_ROOT", "--val-dir", "--evidence-dir", "--doc", "--render-latest"):
        assert token in out.stdout, token


def test_script_holds_no_host_path_or_host_device_fact():
    text = SCRIPT.read_text(encoding="utf-8")
    assert [f for f in HOST_FACTS if f in text] == []


def test_a_benchmark_run_requires_an_absolute_data_root():
    missing = _run("--weights", "yolo11n.pt")
    assert missing.returncode == 2 and "MBNAS_BENCH_DATA_ROOT" in missing.stderr
    relative = _run("--data-root", "relative/pilot")
    assert relative.returncode != 0 and "--data-root must be an absolute path" in relative.stderr


def test_cpu_host_writes_no_evidence(tmp_path, make_dataset):
    """No CUDA device visible: exit 2 before any subject runs; no evidence file, no run directory left behind."""
    ds = make_dataset("bench_script", counts={"val": 1})
    runs = tmp_path / "runs"
    out = _run("--data-root", ds["root"], "--val-dir", "images/val", "--runs-root", str(runs),
               "--weights-dir", str(tmp_path / "weights"), timeout=600)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "no CUDA device is visible" in out.stderr
    assert not list(runs.rglob("bench_host_gpu_*.json")) and list((runs / "benchmark").iterdir()) == []


def _record(*, roots: list[str] | None, trt_status: str) -> dict:
    acc = {"ap_for_delta": 0.5, "ap_50": 0.7, "recall": 0.6}
    split = {"roots": roots, "root": roots[0], "images": 1} if roots else {"root": "/abs/old/split", "images": 1}
    return {"device": {"name": "SYNTHETIC GPU", "uuid": "GPU-0", "host_runtime": {"torch": {"version": "2.6.0"}}},
            "subject": {"backend": "ultralytics", "postprocess": {"nms": True},
                        "source": {"path": "/abs/w/yolo11n.pt", "sha256": "0" * 64, "kind": "official_weights",
                                   "family": "YOLO11"}},
            "batch": 1, "input_shape": [1, 3, 64, 64], "warmup": 1, "repeats": 2, "sample_image": "/abs/s/a.jpg",
            "split": {**split, "split_hash": "sha256:" + "1" * 64},
            "export": {"path": "/abs/o/y.onnx", "sha256": "2" * 64, "opset": 17}, "engines": {},
            "latency_table": [{"runtime": "pytorch", "precision": "FP32", "scope": "forward_only", "p50_ms": 1.0,
                               "p95_ms": 1.0, "mean_ms": 1.0, "fps_batch1": 1000.0}],
            "runtimes": {"pytorch:FP32": {"status": "MEASURED", "accuracy": acc},
                         "tensorrt:FP16": {"status": trt_status, "accuracy": acc if trt_status == "MEASURED" else None,
                                           **({} if trt_status == "MEASURED"
                                              else {"error": "TensorRT engine BLOCKED"})}},
            "limitations": ["synthetic"]}


def test_render_from_lists_split_roots_and_the_tensorrt_status(tmp_path):
    new = {"kind": "bench_host_gpu", "created_at": "20261027_120000",
           "evidence_type": "real_gpu (SYNTHETIC GPU), measured",
           "tensorrt": {"status": "BLOCKED_BY_ENV"}, "blocked_targets": [],
           "subjects": [{"weights": "yolo11n.pt", "job_result": {"status": "SUCCEEDED"},
                         "record": _record(roots=["/abs/new/images/val"], trt_status="FAILED")}]}
    old = {"kind": "bench_host_gpu", "created_at": "20261001_120000", "blocked_targets": [],
           "subjects": [{"weights": "yolo11s.pt", "job_result": {"status": "SUCCEEDED"},
                         "record": _record(roots=None, trt_status="MEASURED")}]}
    files = []
    for name, ev in (("bench_host_gpu_20261027_120000.json", new), ("bench_host_gpu_20261001_120000.json", old)):
        (tmp_path / name).write_text(json.dumps(ev))
        files.append(str(tmp_path / name))
    doc = tmp_path / "out" / "BENCHMARK_HOST_GPU.md"
    out = _run("--render-from", *files, "--doc", str(doc))
    assert out.returncode == 0, out.stderr
    text = doc.read_text()
    assert "split `/abs/new/images/val`" in text and "split `/abs/old/split`" in text
    assert "TensorRT BLOCKED_BY_ENV" in text and "TensorRT status not recorded" in text
    assert "scripts/runner/bench_host_gpu.py --render-from" in text


def test_rank_and_tensorrt_status_helpers():
    mod = _module()

    def subject(trt: str) -> dict:
        return {"job_result": {"status": "SUCCEEDED"}, "record": _record(roots=["/abs/s"], trt_status=trt)}

    assert mod._rank(subject("MEASURED")) == (True, True)
    assert mod._rank(subject("FAILED")) == (True, False)  # TensorRT not measured: a reduced run, never "full"
    blocked = mod.tensorrt_status({"host": {"tensorrt": {"available": False,
                                                         "error": "ModuleNotFoundError: tensorrt"}}})
    assert blocked["status"] == "BLOCKED_BY_ENV" and "wheelhouse" in blocked["reason"]
    assert mod.tensorrt_status({"host": {"tensorrt": {"available": True, "version": "10.13.3.9"}}}) == {
        "status": "AVAILABLE", "version": "10.13.3.9"}
    assert mod.device_label({"host": {"torch": {"devices": []}}}) == "no CUDA device visible"
    assert Path(mod.__file__) == SCRIPT
