"""DD-A60 (1): CPU-only runners in the validator's device inventory (``MBNAS_CORE_DEVICE_SOURCE=runners``).

Real core app, runners registered over the real ``/core/v1/runner/register`` route (``runner_sim``), the real validator
with :func:`mbnas_core.protocol.validator.devices_from_runners` as the device provider (what ``AppServices`` uses for
``device_source=runners``), and for the training row the real ``RunnerDaemon`` with the SYNTHETIC stub handlers on this
CPU host. Asserted: CPU runners form one ``cpu`` entry (uuid None) instead of being left out; on a CPU-only fleet an
evaluation-ON protocol is refused at validation with EVALUATION_REQUIRES_CUDA while explicit training and the
evaluation-OFF generation validate (DD-A32 scope) and the training job runs on the CPU runner; on a mixed fleet the GPU
device still validates and only a benchmark device that names the CPU entry is refused. No GPU, no NVML.
"""
from __future__ import annotations

import copy
from typing import Any

import harness
import pytest
import runner_sim as rs

from mbnas_core.db import repo
from mbnas_core.protocol.validator import devices_from_runners

pytestmark = pytest.mark.runner_protocol

CPU = {"device": "cpu", "device_uuid": None, "device_name": "CPU (CUDA_VISIBLE_DEVICES='')"}


@pytest.fixture
def fleet(rp_settings, registry, tmp_path, stub):
    """Core app whose validation devices are read from the runners table; ``fleet.register(runner_id, **device)``
    issues a runner principal and registers that runner (call it before the first validation: the services cache the
    inventory for 30 s, exactly as in production)."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_core.console.services import AppServices
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth

    settings = rp_settings.model_copy(update={"device_source": "runners"})

    def live_devices() -> list[dict[str, Any]]:
        conn = connect(settings, init=False)
        try:
            return devices_from_runners(conn)
        finally:
            conn.close()

    entries = list(registry.values())
    svc = AppServices(settings, registry_fn=lambda: (entries, "synthetic-test-registry", None), devices_fn=live_devices,
                      versions_fn=lambda: {"python": "test", "packages": {}},
                      source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0})
    rs.write_synthetic_probe(settings.output_root)
    app = create_app(settings, svc)
    tokens: dict[str, str] = {}

    with TestClient(app) as core:
        def register(runner_id: str, **device: Any) -> dict[str, Any]:
            conn = connect(settings)
            try:
                _row, tokens[runner_id] = auth.create_principal(conn, "runner", runner_id=runner_id)
            finally:
                conn.close()
            sim = rs.RunnerSim(core, tokens[runner_id], runner_id, tmp_path / f"runner-{runner_id}")
            return sim.register(**device)

        yield SimpleNamespace(core=core, settings=settings, register=register, devices=live_devices, tokens=tokens,
                              tmp_path=tmp_path)


def _with_device(proto: dict[str, Any], device: str) -> dict[str, Any]:
    out = copy.deepcopy(proto)
    out["console"]["benchmark"]["device"] = device
    return out


def _validate(core, proto: dict[str, Any]) -> dict[str, Any]:
    r = core.post("/api/v1/protocols/validate", json={"protocol": proto}, headers=harness.auth())
    assert r.status_code == 200, r.text
    return r.json()


def _codes(body: dict[str, Any]) -> dict[str, str]:
    return {e["code"]: e["field"] for e in body["errors"]}


def _training_request(base_proto, device: str, key: str) -> dict[str, Any]:
    proto = _with_device(base_proto(), device)
    proto["console"]["training"] = {"epoch_target_total": 2, "seed": 13}
    return {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "scratch",
            "validation_enabled": True, "idempotency_key": key}


OFF_POLICY = {"schema_version": "nas-policy/1",
              "console": {"paths": {"output_root": None},
                          "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                     "seeds": [7], "candidate_count": 2, "max_attempts": 12, "allow_duplicates": False,
                                     "arms": ["GA"]}}}


def test_cpu_runners_form_one_cpu_device_entry_instead_of_being_left_out(fleet):
    fleet.register("cpu-a", **CPU)
    fleet.register("cpu-b", **CPU)
    fleet.register("gpu-a")  # runner_sim default: device sim-dev0 with uuid GPU-sim-gpu-a
    fleet.register("nouuid", device="sim-dev9", device_uuid=None, device_name="no uuid, not a CPU registration")
    inv = fleet.devices()
    by_id = {d["id"]: d for d in inv}
    assert set(by_id) == {"sim-dev0", "cpu"}, inv  # the uuid-less non-CPU runner stays out (cannot be pinned)
    cpu = by_id["cpu"]
    assert cpu["uuid"] is None and cpu["kind"] == "cpu" and cpu["name"] == "cpu"
    assert cpu["runners"] == ["cpu-a", "cpu-b"] and cpu["aliases"] == ["cpu", "cpu-a", "cpu-b"]
    assert inv[-1] is cpu  # listed after the GPU entries
    gpu = by_id["sim-dev0"]
    assert gpu["uuid"] == "GPU-sim-gpu-a" and "kind" not in gpu and gpu["runners"] == ["gpu-a"]


def test_cpu_only_fleet_refuses_evaluation_on_and_validates_training_and_generation(fleet, search_proto, base_proto,
                                                                                    monkeypatch):
    fleet.register("cpu-a", **CPU)
    on_cpu = _validate(fleet.core, _with_device(search_proto, "cpu"))
    assert on_cpu["valid"] is False and on_cpu["resolved"] is None
    assert _codes(on_cpu) == {"EVALUATION_REQUIRES_CUDA": "console.search.evaluation_enabled"}, on_cpu["errors"]
    preset_device = _validate(fleet.core, search_proto)  # the preset names host-gpu-0, which this fleet does not have
    assert _codes(preset_device) == {"DEVICE_UNAVAILABLE": "console.benchmark.device",
                                     "EVALUATION_REQUIRES_CUDA": "console.search.evaluation_enabled"}, preset_device["errors"]
    assert "available: ['cpu']" in next(e["message"] for e in preset_device["errors"] if e["code"] == "DEVICE_UNAVAILABLE")

    # explicit training validates on the CPU entry (named "cpu" or by the CPU runner's id) and runs on the CPU runner
    by_alias = fleet.core.post("/api/v1/training-runs", json=_training_request(base_proto, "cpu-a", "dd-a60-alias"),
                               headers=harness.auth())
    assert by_alias.status_code == 200, by_alias.text
    created = fleet.core.post("/api/v1/training-runs", json=_training_request(base_proto, "cpu", "dd-a60-train"),
                              headers=harness.auth())
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    from mbnas_core.db.core import connect

    conn = connect(fleet.settings, init=False)
    try:
        run = repo.get_run(conn, run_id)
        assert run["resolved_protocol"]["console"]["benchmark"]["device_resolved"] == {"id": "cpu", "uuid": None,
                                                                                      "name": "cpu"}
        assert [j["kind"] for j in repo.list_jobs(conn, run_id)] == ["BASELINE_TRAIN"]
    finally:
        conn.close()

    # evaluation OFF (CPU structure generation) is not affected
    policy = copy.deepcopy(OFF_POLICY)
    policy["console"]["paths"]["output_root"] = str(fleet.settings.output_root)
    off = _validate(fleet.core, policy)
    assert off["valid"] is True, off["errors"]

    # both training jobs (stub handlers: synthetic metrics) are claimed and finished by the CPU runner (DD-A32 mapping);
    # ``run(once=True)`` runs one job and closes its client, so one daemon instance per job
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    for i in range(2):
        settings = rs.daemon_settings(fleet.tmp_path / f"daemon-cpu-a-{i}", fleet.tokens["cpu-a"], "cpu-a")
        daemon = rs.make_daemon(fleet.core, settings)
        assert daemon.cpu_only and daemon.device["id"] == "cpu" and daemon.device["uuid"] is None
        try:
            assert daemon.run(once=True) == 0
        finally:
            daemon.stopping = True
    conn = connect(fleet.settings, init=False)
    try:
        for rid in (run_id, by_alias.json()["run_id"]):
            jobs = repo.list_jobs(conn, rid)
            assert [j["status"] for j in jobs] == ["SUCCEEDED"], [(j["kind"], j["status"], j.get("error")) for j in jobs]
            assert jobs[0]["result"]["outputs"]["device_used"] == "cpu"
            assert repo.get_run(conn, rid)["status"] == "COMPLETED"
    finally:
        conn.close()


def test_mixed_fleet_keeps_the_gpu_and_refuses_only_the_cpu_benchmark_device(fleet, search_proto):
    fleet.register("cpu-a", **CPU)
    fleet.register("gpu-a")
    on_gpu = _validate(fleet.core, _with_device(search_proto, "GPU-sim-gpu-a"))
    assert "EVALUATION_REQUIRES_CUDA" not in _codes(on_gpu), on_gpu["errors"]
    assert "DEVICE_UNAVAILABLE" not in _codes(on_gpu), on_gpu["errors"]
    on_cpu = _validate(fleet.core, _with_device(search_proto, "cpu-a"))  # the CPU runner's id is an alias of "cpu"
    assert on_cpu["valid"] is False
    assert _codes(on_cpu).get("EVALUATION_REQUIRES_CUDA") == "console.benchmark.device", on_cpu["errors"]


def test_empty_inventory_is_not_a_cpu_fleet(fleet, search_proto):
    """No runner registered: DEVICE_UNAVAILABLE alone, as before DD-A60 (no CPU-only claim about an empty fleet)."""
    body = _validate(fleet.core, search_proto)
    assert "DEVICE_UNAVAILABLE" in _codes(body) and "EVALUATION_REQUIRES_CUDA" not in _codes(body), body["errors"]
