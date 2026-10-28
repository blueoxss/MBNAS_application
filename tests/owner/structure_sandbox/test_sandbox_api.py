"""T3-35 at the API level (REMOTE_PROTOCOL_DESIGN 10.2 / table T3-35; WP-3 contract 1.4): a user structure check sent
through the GATEWAY (``/api/v1``, combined in-process harness) is executed by the core's real sandbox child, and a
sandbox failure comes back as data: HTTP 200, ``status: UNVERIFIED`` and the sandbox failure code - never a 5xx and
never a model build in the API process. CPU only; synthetic YAML; nothing is stubbed.

Design names covered here: ``test_structure_check_subprocess_timeout_returns_unverified`` and
``test_structure_check_result_over_1mib_is_rejected`` (the 1 MiB default is asserted on ``CoreSettings``; the over-cap
behaviour is exercised with a small cap so the row does not need a >1 MiB model report). The third design name,
``test_structure_check_subprocess_env_has_no_secrets_and_no_gpu``, is ``test_sandbox.py::test_worker_env_carries_no_
mbnas_secret`` plus the env row below, which reads the environment the REAL child received.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import harness
from harness import auth

TINY = "nc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\n  - [-1, 1, Conv, [32, 3, 2]]\nhead:\n  - [[-1], 1, Detect, [nc]]\n"


@pytest.fixture
def through_gateway(tmp_roots, registry, host_devices):
    """``through_gateway(**core_overrides)`` -> TestClient over the gateway of a fresh combined app."""
    opened: list[Any] = []

    def build(**core_overrides: Any) -> TestClient:
        cs = harness.core_settings_for_gateway(tmp_roots, **core_overrides)
        combined = harness.combined_app(tmp_roots, registry, host_devices, core_settings=cs)
        core = TestClient(combined.core_app)
        core.__enter__()
        gw = TestClient(combined.gateway_app)
        gw.__enter__()
        opened.extend([gw, core])
        return gw

    yield build
    for c in opened:
        c.__exit__(None, None, None)


def _check(client: TestClient, text: str = TINY) -> Any:
    up = client.post("/api/v1/uploads/model-config", content=text.encode(),
                     headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": "m.yaml"})
    assert up.status_code == 200, up.text
    return client.post("/api/v1/structure-check", json={"upload_id": up.json()["upload_id"], "input_size": 64},
                       headers=auth())


def test_structure_check_passes_through_the_gateway_in_the_sandbox(through_gateway):
    r = _check(through_gateway())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS", doc.get("diagnostics")
    assert doc["sandbox"]["exit_code"] == 0
    assert doc["sandbox"]["isolation"] in ("unshare-rn+setsid+rlimits", "setsid+rlimits")


def test_structure_check_subprocess_timeout_returns_unverified(through_gateway):
    r = _check(through_gateway(structure_check_timeout_s=0.3))
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "UNVERIFIED", doc
    assert [d["code"] for d in doc["diagnostics"]] == ["STRUCTURE_CHECK_TIMEOUT"], doc["diagnostics"]
    assert doc["sandbox"]["wall_timeout_s"] == pytest.approx(0.3)


def test_structure_check_result_over_1mib_is_rejected(through_gateway, tmp_roots):
    assert harness.make_settings(tmp_roots).structure_check_max_output_bytes == 1 << 20  # the design's 1 MiB default
    r = _check(through_gateway(structure_check_max_output_bytes=64))
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "UNVERIFIED", doc
    assert [d["code"] for d in doc["diagnostics"]] == ["STRUCTURE_CHECK_RESOURCE_LIMIT"], doc["diagnostics"]


def test_structure_check_subprocess_env_has_no_secrets_and_no_gpu(tmp_path, monkeypatch):
    """The environment a REAL child process receives from ``worker_env`` (the env the sandbox passes to Popen):
    no ``MBNAS_*`` variable even when the API process holds secrets, and no visible GPU."""
    from mbnas_core.structure import sandbox as sb

    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "secret-should-not-leak")
    monkeypatch.setenv("MBNAS_GW_CORE_TOKEN", "secret-should-not-leak-2")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    code = "import json, os; print(json.dumps(dict(os.environ)))"
    p = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60,
                       cwd=str(tmp_path), env=sb.worker_env(str(tmp_path)))
    assert p.returncode == 0, p.stderr
    env = json.loads(p.stdout)
    assert not [k for k in env if k.startswith("MBNAS_")], sorted(env)
    assert "secret-should-not-leak" not in p.stdout
    assert env.get("CUDA_VISIBLE_DEVICES") == ""
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"  # the parent really had a GPU id set


def test_model_build_never_happens_in_the_api_process(through_gateway, monkeypatch, tmp_path):
    """The PASS above came from the child: the in-process builder is booby-trapped in THIS process."""
    marker = tmp_path / "BUILT_IN_API_PROCESS"
    from mbnas_core.console import model_services as ms

    def trap(*a: Any, **k: Any) -> Any:
        Path(marker).write_text("built")
        raise AssertionError("in-process structure check reached")

    monkeypatch.setattr(ms, "check_genotype_inprocess", trap)
    monkeypatch.setattr(ms, "check_structure", trap)
    r = _check(through_gateway())
    assert r.status_code == 200 and r.json()["status"] == "PASS", r.text
    assert not marker.exists()
