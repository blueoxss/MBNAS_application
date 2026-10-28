"""Structure-check sandbox (WP-3 contract 1.4, SECURITY_BASELINE section 4 items 5-6, T3-35): real subprocesses, CPU.

Every row starts the real ``mbnas_core.structure.check_worker`` through :class:`StructureSandbox`; nothing is stubbed.
"""
from __future__ import annotations

import os
import threading
import time

import pytest


YOLO_TINY = {"nc": 2, "backbone": [[-1, 1, "Conv", [16, 3, 2]], [-1, 1, "Conv", [32, 3, 2]]],
             "head": [[-1, 1, "nn.Upsample", [None, 2, "nearest"]], [[-1], 1, "Detect", ["nc"]]]}


def _genotype_payload():
    from mbnas_model_runtime.genotype import Genotype

    g = Genotype.from_yaml_dict(dict(YOLO_TINY), family="tiny", template_id="user:tiny")
    return {"genotype": g.to_json(), "input_size": 64, "class_count": 2}


def test_check_runs_in_child_and_records_isolation(make_sandbox):
    sb = make_sandbox()
    doc = sb.run("check_structure", _genotype_payload())
    assert doc["sandbox"]["exit_code"] == 0
    assert doc["sandbox"]["isolation"] in ("unshare-rn+setsid+rlimits", "setsid+rlimits")
    assert doc.get("status") in ("PASS", "FAIL", "UNVERIFIED")


def test_worker_env_carries_no_mbnas_secret(monkeypatch, tmp_path):
    from mbnas_core.structure import sandbox as sb

    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "secret-should-not-leak")
    env = sb.worker_env(str(tmp_path))
    assert not [k for k in env if k.startswith("MBNAS_")]
    assert env["ULTRALYTICS_SAFE_LOAD"] == "1" and env["YOLO_AUTOINSTALL"] == "false"
    assert env["YOLO_OFFLINE"] == "1" and env["CUDA_VISIBLE_DEVICES"] == ""


def test_cpu_rlimit_breach_maps_to_resource_limit_and_api_rss_unchanged(make_sandbox, rss_kib):
    from mbnas_core.structure import sandbox as sb

    before = rss_kib()
    with pytest.raises(sb.SandboxFailure) as ei:
        make_sandbox(cpu_s=1).run("check_structure", _genotype_payload())
    after = rss_kib()
    assert ei.value.code == sb.RESOURCE_LIMIT, (ei.value.code, ei.value.cause)
    # the import of torch/ultralytics happened in the child; the API process did not grow by a model build
    assert after - before < 64 * 1024, (before, after)


def test_address_space_breach_is_unverified_code(make_sandbox):
    from mbnas_core.structure import sandbox as sb

    with pytest.raises(sb.SandboxFailure) as ei:
        make_sandbox(as_bytes=96 << 20, wall_s=30.0).run("check_structure", _genotype_payload())
    assert ei.value.code in (sb.RESOURCE_LIMIT, sb.RUNTIME_UNAVAILABLE), ei.value.code


def test_wall_timeout_kills_group(make_sandbox):
    from mbnas_core.structure import sandbox as sb

    t0 = time.monotonic()
    with pytest.raises(sb.SandboxFailure) as ei:
        make_sandbox(wall_s=0.3).run("check_structure", _genotype_payload())
    assert ei.value.code == sb.TIMEOUT
    assert time.monotonic() - t0 < 30


def test_output_cap(make_sandbox):
    from mbnas_core.structure import sandbox as sb

    with pytest.raises(sb.SandboxFailure) as ei:
        make_sandbox(max_output_bytes=64).run("check_structure", _genotype_payload())
    assert ei.value.code == sb.RESOURCE_LIMIT


def test_concurrent_checks_are_serialised(make_sandbox):
    from mbnas_core.structure import sandbox as sb

    spans: list[tuple[float, float]] = []
    errors: list[str] = []

    def one() -> None:
        s = make_sandbox(wall_s=1.0, queue_s=60.0)
        t0 = time.monotonic()
        try:
            s.run("check_structure", _genotype_payload())
        except sb.SandboxFailure as exc:
            errors.append(exc.code)
        spans.append((t0, time.monotonic()))

    threads = [threading.Thread(target=one) for _ in range(3)]
    t_all = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    total = time.monotonic() - t_all
    assert len(spans) == 3
    # each run holds the semaphore >= ~1 s (wall limit or a real check): three runs cannot finish in parallel time
    assert total >= 2.5, (total, errors)


def test_busy_when_queue_timeout_expires(make_sandbox):
    from mbnas_core.structure import sandbox as sb

    holder = threading.Thread(target=lambda: _swallow(make_sandbox(wall_s=3.0), sb))
    holder.start()
    time.sleep(0.5)
    with pytest.raises(sb.SandboxBusy):
        make_sandbox(queue_s=0.1).run("check_structure", _genotype_payload())
    holder.join(timeout=60)


def _swallow(s, sb) -> None:
    try:
        s.run("check_structure", _genotype_payload())
    except sb.SandboxFailure:
        pass


def test_pdeathsig_child_dies_with_parent_pid_check():
    """The worker exits at once when its recorded parent is gone (PR_SET_PDEATHSIG + parent_pid check)."""
    import json
    import subprocess
    import sys

    from mbnas_core.structure import sandbox as sb

    req = json.dumps({"op": "check_structure", "payload": {}, "limits": {"cpu_s": 30, "as_bytes": 16 << 30,
                      "nofile": 256, "fsize_bytes": 1 << 20, "core": 0}, "parent_pid": 999999999}).encode()
    p = subprocess.run([sys.executable, "-I", "-B", "-m", sb.WORKER_MODULE], input=req, capture_output=True,
                       timeout=60, env=sb.worker_env(os.getcwd()))
    assert p.returncode != 0 or b'"ok": true' not in p.stdout
