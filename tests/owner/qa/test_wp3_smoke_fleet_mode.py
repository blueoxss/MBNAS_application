"""DD-A75 F2: the fleet mode of ``scripts/dev/wp3_three_process_smoke.py`` (the owner ``verify.sh --suite integration``
driver).

The smoke asks the running stack for the core's validation device inventory (``GET /api/v1/health``: ``devices``) before
its two device-dependent steps and runs them in the GPU mode (a CUDA device exists: exactly as before, preset device
``host-gpu-0``) or the CPU-only mode (the same steps with the CPU entry and the DD-A60 (1) / DD-A62 semantics asserted as
PASS-by-design). Checked here without processes: the classification of real health answers (the T4 sandbox and T6 host
shapes, mixed, unusable), that the CPU-only verdict of the dataset-manifest step passes only for the by-design answer
(every deviation fails it), and - against the real core app with the production device provider (NVML replaced by a
static list, a CPU runner registered over the real route) - that the smoke classifies the real health projection the
way the validator treats the inventory and that the validator answers the smoke's evaluation-ON protocol on the CPU
entry with exactly the refusal the smoke expects. SYNTHETIC devices and dataset; no GPU, no NVML.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import harness
import pytest

_SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
if _SHARED not in sys.path:
    sys.path.append(_SHARED)

import runner_sim as rs  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "wp3_three_process_smoke.py"
PRESET = "host-gpu-0"
CPU_RUNNER = {"device": "cpu", "device_uuid": None, "device_name": "CPU (CUDA_VISIBLE_DEVICES='')"}
HOST_GPU = {"id": "host-gpu-0", "uuid": "GPU-host-0", "name": "SYNTHETIC host GPU",
            "aliases": ["GPU-host-0", "SYNTHETIC host GPU"], "precisions": ["FP16", "FP32"], "memory_total_mb": 1024}

# GET /api/v1/health ``devices`` / ``capabilities.device_targets`` as recorded by the integration suite (T4 attempt_dd_a73:
# bwrap sandbox without /dev/nvidia*; T6 rerun2 attempt3: host, NVML lists two GPUs although CUDA_VISIBLE_DEVICES="")
T4_DEVICES = [{"aliases": ["cpu", "smoke-runner-1"], "id": "cpu", "kind": "cpu", "memory_total_mb": None, "name": "cpu",
               "precisions": ["FP32", "FP16"], "runners": ["smoke-runner-1"], "worker_id": "smoke-runner-1"}]
T6_DEVICES = [{"aliases": ["NVIDIA RTX A6000"], "device_id": "dev-200923520de90b4f", "id": "host-gpu-0",
               "memory_total_mb": 49140, "name": "NVIDIA RTX A6000", "precisions": ["FP32", "FP16"], "worker_id": None},
              {"aliases": ["NVIDIA RTX A6000"], "device_id": "dev-be89d751aef36f29", "id": "host-gpu-1",
               "memory_total_mb": 49140, "name": "NVIDIA RTX A6000", "precisions": ["FP32", "FP16"], "worker_id": None}]
UNAVAILABLE = {"available": False, "id": PRESET, "kind": "host_gpu", "name": "unknown", "status": "UNAVAILABLE",
               "reason": "host-gpu-0 is a measurable target but is not in the live device inventory (no NVML device / "
                         "CUDA_VISIBLE_DEVICES hides it)"}
AVAILABLE = {"available": True, "id": PRESET, "kind": "host_gpu", "name": "NVIDIA RTX A6000", "status": "AVAILABLE",
             "reason": None}


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("wp3_three_process_smoke_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _health(devices: Any, *targets: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "devices": devices, "capabilities": {"device_targets": list(targets)}}


def test_t4_sandbox_inventory_is_the_cpu_only_mode_with_the_cpu_entry(smoke):
    fleet = smoke.fleet_mode(200, _health(T4_DEVICES, UNAVAILABLE), PRESET)
    assert fleet["mode"] == smoke.CPU_MODE == "CPU-only"
    assert fleet["benchmark_device"] == "cpu" and fleet["cpu_device_ids"] == ["cpu"] and fleet["cuda_device_ids"] == []
    assert fleet["preset_device_in_inventory"] is False and fleet["preset_target"]["status"] == "UNAVAILABLE"
    assert "EVALUATION_REQUIRES_CUDA" in fleet["why"] and "no CUDA device" in fleet["why"]
    assert fleet["inventory"] == [{"id": "cpu", "kind": "cpu", "name": "cpu", "runners": ["smoke-runner-1"],
                                   "worker_id": "smoke-runner-1"}]


def test_t6_host_inventory_is_the_gpu_mode_with_the_unchanged_preset_device(smoke):
    fleet = smoke.fleet_mode(200, _health(T6_DEVICES, AVAILABLE), PRESET)
    assert fleet["mode"] == smoke.GPU_MODE == "GPU"
    assert fleet["benchmark_device"] == PRESET  # exactly as before DD-A75: the preset of pilot_smoke_protocol
    assert fleet["cuda_device_ids"] == ["host-gpu-0", "host-gpu-1"] and fleet["cpu_device_ids"] == []
    assert fleet["preset_device_in_inventory"] is True and "exactly as before" in fleet["why"]


def test_a_mixed_inventory_is_the_gpu_mode(smoke):
    fleet = smoke.fleet_mode(200, _health(T6_DEVICES[:1] + T4_DEVICES, AVAILABLE), PRESET)
    assert fleet["mode"] == "GPU" and fleet["benchmark_device"] == PRESET
    assert fleet["cuda_device_ids"] == ["host-gpu-0"] and fleet["cpu_device_ids"] == ["cpu"]
    # a GPU fleet whose device is not the preset one still runs the GPU-mode steps unchanged (and fails as before)
    other = [dict(T6_DEVICES[1])]
    fleet = smoke.fleet_mode(200, _health(other), PRESET)
    assert fleet["mode"] == "GPU" and fleet["benchmark_device"] == PRESET and fleet["preset_device_in_inventory"] is False


@pytest.mark.parametrize("http,health", [
    (200, _health([])),  # empty inventory: not a CPU-only fleet (DD-A60 (1)), DEVICE_UNAVAILABLE as before
    (503, {"code": "CORE_UNAVAILABLE"}),
    (None, {"error": "ConnectError: refused"}),
    (200, {"devices": "not a list"}),
    (200, "not json"),
    (200, _health([None, "cpu"])),
])
def test_no_usable_inventory_is_unknown_and_keeps_the_gpu_mode_steps(smoke, http, health):
    fleet = smoke.fleet_mode(http, health, PRESET)
    assert fleet["mode"] == smoke.UNKNOWN_MODE and fleet["benchmark_device"] == PRESET
    assert "GPU-mode steps run unchanged" in fleet["why"]


@pytest.mark.parametrize("entry,cpu", [
    ({"id": "cpu", "kind": "cpu"}, True),
    ({"id": "cpu", "name": "cpu"}, True),  # an id-only CPU entry without a public device id
    ({"id": "CPU"}, True),
    ({"id": "cpu", "device_id": "dev-1234"}, False),  # a uuid-bearing device that happens to be called cpu
    ({"id": "host-gpu-0", "device_id": "dev-1"}, False),
    ({"id": "sim-dev0", "device_id": "dev-2", "runners": ["gpu-a"]}, False),
    ("cpu", False), (None, False),
])
def test_is_cpu_device(smoke, entry, cpu):
    assert smoke.is_cpu_device(entry) is cpu


# ---- the CPU-only verdict of validate-dataset-manifests-via-gateway ------------------------------------------------
def _by_design_answer() -> dict[str, Any]:
    """The gateway's validate answer recorded in the CPU-only sandbox run (DD-A75 F2 evidence), messages shortened."""
    counts = {role: {"images": n, "labelled": n, "missing_labels": 0, "empty_labels": 0, "boxes": n,
                     "per_class": {"0": n}, "issues": 0} for role, n in (("train", 3), ("search_val", 2),
                                                                         ("final_eval", 2))}
    return {"valid": False, "resolved": None, "protocol_hash": None, "validation_id": None, "resolved_diff": [],
            "errors": [{"code": "EVALUATION_REQUIRES_CUDA", "field": "console.search.evaluation_enabled",
                        "message": "evaluation ON measures latency on a CUDA device only ..."}],
            "warnings": [], "capabilities": {"validation_host": "gateway", "device": {"id": "cpu", "name": "cpu"},
                                             "dataset": {"names": {"0": "drone"}, "counts": counts}}}


def _gateway_manifests() -> dict[str, Any]:
    splits = {role: {"split_hash": f"sha256:{c * 64}", "class_mapping_hash": "sha256:" + "c" * 64, "images": n}
              for role, n, c in (("train", 3, "1"), ("search_val", 2, "2"), ("final_eval", 2, "3"))}
    return {"cache": "<root>/gateway/cache/manifests", "splits": splits,
            "images": {"train": 3, "search_val": 2, "final_eval": 2}, "all_split_hashes_set": True}


def _checks(smoke, answer=None, *, http=200, rows=(0, 0), gateway=None, device="cpu") -> dict[str, bool]:
    return smoke.cpu_refusal_checks(http, _by_design_answer() if answer is None else answer, device=device,
                                    rows_before=rows[0], rows_after=rows[1],
                                    gateway_manifests=_gateway_manifests() if gateway is None else gateway)


def test_the_by_design_refusal_passes_every_cpu_check(smoke):
    checks = _checks(smoke)
    assert len(checks) == 9 and all(checks.values()), checks


def _mutate(path: str, value: Any) -> dict[str, Any]:
    answer = _by_design_answer()
    *parents, last = path.split(".")
    node = answer
    for p in parents:
        node = node[p]
    node[last] = value
    return answer


@pytest.mark.parametrize("answer,failing", [
    (_mutate("valid", True), "refused_at_validate"),
    (_mutate("errors", []), "only_error_is_EVALUATION_REQUIRES_CUDA"),
    (_mutate("errors", [{"code": "DEVICE_UNAVAILABLE", "field": "console.benchmark.device", "message": "x"},
                        {"code": "EVALUATION_REQUIRES_CUDA", "field": "console.search.evaluation_enabled",
                         "message": "y"}]), "only_error_is_EVALUATION_REQUIRES_CUDA"),
    (_mutate("errors", [{"code": "EVALUATION_REQUIRES_CUDA", "field": "console.benchmark.device", "message": "x"}]),
     "only_error_is_EVALUATION_REQUIRES_CUDA"),  # the mixed-fleet refusal is not the CPU-only one
    (_mutate("errors", [{"code": "EVALUATION_REQUIRES_CUDA", "field": "console.search.evaluation_enabled", "message": "x"},
                        {"code": "MANIFEST_MISSING", "field": "console.paths.final_eval_root", "message": "y"}]),
     "only_error_is_EVALUATION_REQUIRES_CUDA"),
    (_mutate("capabilities.device", {"id": "host-gpu-0"}), "cpu_entry_resolved"),
    (_mutate("validation_id", "val-0123456789abcdef"), "no_resolution_claim"),
    (_mutate("protocol_hash", "sha256:" + "0" * 64), "no_resolution_claim"),
    (_mutate("resolved", {"console": {}}), "no_resolution_claim"),
    (_mutate("capabilities.validation_host", None), "validation_host_gateway"),
    (_mutate("capabilities.dataset.counts", {"train": {"images": 3}, "search_val": {"images": 2}}),
     "core_used_the_3_split_manifests"),
    (_mutate("capabilities.dataset", None), "core_used_the_3_split_manifests"),
])
def test_every_deviation_from_the_by_design_answer_fails(smoke, answer, failing):
    checks = _checks(smoke, answer)
    assert checks[failing] is False and not all(checks.values()), checks


@pytest.mark.parametrize("kw,failing", [
    ({"http": 503}, "http_200"),
    ({"rows": (0, 1)}, "no_validation_stored"),
    ({"rows": ("확인 못 함 (OperationalError)", "확인 못 함 (OperationalError)")}, "no_validation_stored"),
    ({"gateway": "확인 못 함 (FileNotFoundError)"}, "gateway_split_hashes_set"),
    ({"gateway": {**_gateway_manifests(), "all_split_hashes_set": False}}, "gateway_split_hashes_set"),
    ({"gateway": {**_gateway_manifests(), "images": {"train": 3, "search_val": 2}}}, "gateway_split_hashes_set"),
    ({"device": None}, "cpu_entry_resolved"),
])
def test_every_unverifiable_or_wrong_side_fact_fails(smoke, kw, failing):
    checks = _checks(smoke, **kw)
    assert checks[failing] is False and not all(checks.values()), checks


# ---- against the real core app ------------------------------------------------------------------------------------
@pytest.fixture
def local_core(tmp_roots, registry, make_dataset, tmp_path, monkeypatch):
    """Core app with the DEFAULT device provider (``device_source=local``; see tests/owner/api/
    test_dd_a62_device_source_local.py): ``nvml`` is what NVML reports, ``runner(id, **device)`` registers a runner."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_core.console.services import AppServices
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth
    from mbnas_model_runtime.provenance import env_lock

    monkeypatch.delenv("MBNAS_CORE_DEVICE_SOURCE", raising=False)
    nvml: list[dict[str, Any]] = []
    monkeypatch.setattr(env_lock, "validation_devices", lambda inventory=None: copy.deepcopy(nvml))
    settings = harness.make_settings(tmp_roots, sweeper_enabled=False)
    assert settings.device_source == "local"
    entries = list(registry.values())
    svc = AppServices(settings, registry_fn=lambda: (entries, "synthetic-test-registry", None),
                      versions_fn=lambda: {"python": "test", "packages": {}},
                      source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0})
    rs.write_synthetic_probe(settings.output_root)
    with TestClient(create_app(settings, svc)) as core:
        def runner(runner_id: str, **device: Any) -> None:
            conn = connect(settings)
            try:
                _row, token = auth.create_principal(conn, "runner", runner_id=runner_id)
            finally:
                conn.close()
            rs.RunnerSim(core, token, runner_id, tmp_path / f"runner-{runner_id}").register(**device)

        yield SimpleNamespace(core=core, runner=runner, nvml=nvml.extend, make_dataset=make_dataset,
                              output_root=str(tmp_roots["runs"] / "smoke_out"))


def _health_of(core) -> tuple[int, Any]:
    r = core.get("/api/v1/health", headers=harness.auth())
    return r.status_code, r.json()


def _step6_protocol(lc, device: str) -> dict[str, Any]:
    """The protocol of the smoke's step validate-dataset-manifests-via-gateway (same construction), on ``device``."""
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    ds = lc.make_dataset("smoke3split", counts={"train": 3, "val": 2, "test": 2})
    paths = {k: ds[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    paths["output_root"] = lc.output_root
    proto = pilot_smoke_protocol(paths)
    for b in proto["console"].get("baselines") or []:
        b["enabled"] = False
    proto["console"]["baseline_override_reason"] = "wp3 smoke: dataset-manifest validation only (no baseline run)"
    proto["console"]["benchmark"]["device"] = device
    return proto


def test_cpu_only_core_health_gives_the_cpu_mode_and_the_validator_refuses_exactly_as_expected(smoke, local_core):
    local_core.runner("smoke-runner-1", **CPU_RUNNER)  # before the first inventory read (30 s cache, as in production)
    http, health = _health_of(local_core.core)
    fleet = smoke.fleet_mode(http, health, PRESET)
    assert fleet["mode"] == "CPU-only" and fleet["benchmark_device"] == "cpu", fleet
    r = local_core.core.post("/api/v1/protocols/validate", headers=harness.auth(),
                             json={"protocol": _step6_protocol(local_core, fleet["benchmark_device"])})
    assert r.status_code == 200, r.text
    body = r.json()
    assert tuple((e["code"], e["field"]) for e in body["errors"]) == smoke.CPU_EVALUATION_REFUSAL, body["errors"]
    assert body["valid"] is False and body["resolved"] is None and body["validation_id"] is None
    assert body["capabilities"]["device"]["id"] == "cpu"
    images = {role: c["images"] for role, c in body["capabilities"]["dataset"]["counts"].items()}
    assert images == smoke.SPLIT3_IMAGES  # (no gateway here: core read the splits itself, so no validation_host)


def test_gpu_core_health_gives_the_gpu_mode_and_the_preset_device_still_validates(smoke, local_core):
    local_core.nvml([HOST_GPU])
    local_core.runner("smoke-runner-1", **CPU_RUNNER)  # never merged into a non-empty local list (DD-A62 (b))
    http, health = _health_of(local_core.core)
    fleet = smoke.fleet_mode(http, health, PRESET)
    assert fleet["mode"] == "GPU" and fleet["benchmark_device"] == PRESET and fleet["cpu_device_ids"] == [], fleet
    r = local_core.core.post("/api/v1/protocols/validate", headers=harness.auth(),
                             json={"protocol": _step6_protocol(local_core, fleet["benchmark_device"])})
    assert r.status_code == 200 and r.json()["valid"] is True, r.json().get("errors")


def test_core_without_gpu_and_runner_is_unknown_not_cpu_only(smoke, local_core):
    http, health = _health_of(local_core.core)
    assert smoke.fleet_mode(http, health, PRESET)["mode"] == "UNKNOWN"
