"""DD-A62 (b): the default device source ``MBNAS_CORE_DEVICE_SOURCE=local`` on a core host without a GPU.

The host's NVML list (``provenance.env_lock.validation_devices``) is replaced by a static list here - empty for a GPU-less
host, one SYNTHETIC GPU entry for a GPU host - so no test reads NVML. The services are the production
``AppServices._default_devices_fn`` (no injected ``devices_fn``); runners register over the real
``/core/v1/runner/register`` route (``runner_sim``). Before DD-A62 an empty NVML list made every explicit training
validation DEVICE_UNAVAILABLE although a CPU runner was registered (T4/T5 owner host); now the registered CPU runners'
``cpu`` entry is used when the local list is empty, and only then.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import harness
import pytest

_SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
if _SHARED not in sys.path:
    sys.path.append(_SHARED)

import runner_sim as rs  # noqa: E402

CPU = {"device": "cpu", "device_uuid": None, "device_name": "CPU (CUDA_VISIBLE_DEVICES='')"}
HOST_GPU = {"id": "host-gpu-0", "uuid": "GPU-host-0", "name": "SYNTHETIC host GPU",
            "aliases": ["GPU-host-0", "SYNTHETIC host GPU"], "precisions": ["FP16", "FP32"], "memory_total_mb": 1024}


@pytest.fixture
def local_core(tmp_roots, registry, make_dataset, tmp_path, monkeypatch, stub_free_env):
    """Core app with the DEFAULT device provider (``device_source=local``); ``local_core.nvml(list)`` sets what NVML
    reports, ``local_core.runner(id, **device)`` registers a runner."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_core.console.services import AppServices
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth
    from mbnas_model_runtime.provenance import env_lock

    nvml: list[dict[str, Any]] = []
    monkeypatch.setattr(env_lock, "validation_devices", lambda inventory=None: copy.deepcopy(nvml))
    settings = harness.make_settings(tmp_roots, sweeper_enabled=False)
    assert settings.device_source == "local"
    entries = list(registry.values())
    svc = AppServices(settings, registry_fn=lambda: (entries, "synthetic-test-registry", None),
                      versions_fn=lambda: {"python": "test", "packages": {}},
                      source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0})
    rs.write_synthetic_probe(settings.output_root)
    ds = make_dataset("dd_a62_local", counts={"train": 4, "val": 2, "test": 2})
    rs.runtime_manifest()
    with TestClient(create_app(settings, svc)) as core:
        def runner(runner_id: str, **device: Any) -> None:
            conn = connect(settings)
            try:
                _row, token = auth.create_principal(conn, "runner", runner_id=runner_id)
            finally:
                conn.close()
            rs.RunnerSim(core, token, runner_id, tmp_path / f"runner-{runner_id}").register(**device)

        yield SimpleNamespace(core=core, svc=svc, settings=settings, runner=runner, nvml=nvml.extend,
                              proto=lambda: harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=(),
                                                                   baselines=("yolo11n",), targets=(2,)))


@pytest.fixture
def stub_free_env(monkeypatch):
    monkeypatch.delenv("MBNAS_CORE_DEVICE_SOURCE", raising=False)


def _codes(body: dict[str, Any]) -> dict[str, str]:
    return {e["code"]: e["field"] for e in body["errors"]}


def _train(core, proto: dict[str, Any], device: str, key: str):
    proto = copy.deepcopy(proto)
    proto["console"]["benchmark"]["device"] = device
    proto["console"]["training"] = {"epoch_target_total": 2, "seed": 3}
    return core.post("/api/v1/training-runs", headers=harness.auth(), json={
        "source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "scratch",
        "validation_enabled": True, "idempotency_key": key})


def _validate(core, proto: dict[str, Any], device: str | None = None) -> dict[str, Any]:
    proto = copy.deepcopy(proto)
    if device is not None:
        proto["console"]["benchmark"]["device"] = device
    r = core.post("/api/v1/protocols/validate", json={"protocol": proto}, headers=harness.auth())
    assert r.status_code == 200, r.text
    return r.json()


def test_gpu_less_host_uses_the_registered_cpu_runner_entry(local_core):
    local_core.runner("cpu-a", **CPU)
    devices = local_core.svc.devices()
    assert [(d["id"], d["uuid"], d.get("kind")) for d in devices] == [("cpu", None, "cpu")]
    assert devices[0]["runners"] == ["cpu-a"]
    created = _train(local_core.core, local_core.proto(), "cpu", "dd-a62-b-train")
    assert created.status_code == 200, created.text  # before DD-A62 (b): 422 DEVICE_UNAVAILABLE
    body = _validate(local_core.core, local_core.proto(), "cpu")  # evaluation ON on that inventory
    assert body["valid"] is False and _codes(body) == {"EVALUATION_REQUIRES_CUDA": "console.search.evaluation_enabled"}


def test_a_local_gpu_list_is_used_unchanged(local_core):
    local_core.nvml([HOST_GPU])
    local_core.runner("cpu-a", **CPU)
    assert [d["id"] for d in local_core.svc.devices()] == ["host-gpu-0"]  # no CPU entry merged into a GPU host
    body = _validate(local_core.core, local_core.proto(), "GPU-host-0")
    assert "EVALUATION_REQUIRES_CUDA" not in _codes(body) and "DEVICE_UNAVAILABLE" not in _codes(body), body["errors"]


def test_gpu_runner_entries_are_not_merged_into_the_local_source(local_core):
    local_core.runner("gpu-a")  # SYNTHETIC GPU runner: its device belongs to MBNAS_CORE_DEVICE_SOURCE=runners
    assert local_core.svc.devices() == []
    created = _train(local_core.core, local_core.proto(), "GPU-sim-gpu-a", "dd-a62-b-gpu")
    assert created.status_code == 422, created.text
    assert "DEVICE_UNAVAILABLE" in {e["code"] for e in created.json()["errors"]}


def test_gpu_less_host_without_runners_stays_device_unavailable(local_core):
    assert local_core.svc.devices() == []
    created = _train(local_core.core, local_core.proto(), "cpu", "dd-a62-b-none")
    assert created.status_code == 422, created.text
    assert "DEVICE_UNAVAILABLE" in {e["code"] for e in created.json()["errors"]}
