"""DD-A49 (c): the host GPU target ``host-gpu-0`` is named after the GPU of the host that runs the code (NVML, else an
already initialised torch CUDA context, else ``"unknown"``), never after a fixed product (the inherited table named
the original host's GPU). SYNTHETIC: NVML and torch are fakes; no real NVML query and no CUDA initialisation happen
here, and the module's resolved table is restored after every test."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from mbnas_model_runtime.benchmark import capability

OLD_PRODUCT = "A" + "100"  # the inherited fixed product name (composed: no literal for the host-fact scanners)


class FakeNvml:
    """The pynvml calls ``host_gpu_name`` makes; ``names[i]`` is device i (bytes or str)."""

    def __init__(self, names=(), *, fail_init: bool = False) -> None:
        self.names, self.fail_init, self.calls = list(names), fail_init, []

    def nvmlInit(self) -> None:
        self.calls.append("init")
        if self.fail_init:
            raise RuntimeError("NVML Shared Library Not Found")

    def nvmlShutdown(self) -> None:
        self.calls.append("shutdown")

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        if index >= len(self.names):
            raise RuntimeError("Invalid Argument")
        return index

    def nvmlDeviceGetName(self, handle: int):
        return self.names[handle]


def _fake_torch(*, initialised: bool, name: str = "SYNTHETIC torch GPU"):
    cuda = SimpleNamespace(is_initialized=lambda: initialised, get_device_name=lambda index: f"{name} {index}")
    return SimpleNamespace(cuda=cuda)


@pytest.fixture(autouse=True)
def restore_targets():
    saved, was = {k: dict(v) for k, v in capability._RESOLVED.items()}, capability._resolved_once
    yield
    capability._RESOLVED.clear()
    capability._RESOLVED.update(saved)
    capability._resolved_once = was


def test_host_gpu_name_reads_nvml_and_pairs_init_with_shutdown() -> None:
    nv = FakeNvml(["NVIDIA RTX A6000", b"NVIDIA L4"])
    assert capability.host_gpu_name(0, nvml=nv) == "NVIDIA RTX A6000"
    assert capability.host_gpu_name(1, nvml=nv) == "NVIDIA L4"  # bytes (older nvidia-ml-py) decoded
    assert nv.calls == ["init", "shutdown", "init", "shutdown"]


def test_host_gpu_name_falls_back_to_an_initialised_torch_context_then_unknown(monkeypatch) -> None:
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(initialised=True))
    no_nvml = FakeNvml(fail_init=True)
    assert capability.host_gpu_name(0, nvml=no_nvml) == "SYNTHETIC torch GPU 0"
    assert no_nvml.calls == ["init"]  # no shutdown after a failed init
    assert capability.host_gpu_name(3, nvml=FakeNvml(["only one"])) == "SYNTHETIC torch GPU 3"  # NVML index missing
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(initialised=False))  # never initialises CUDA itself
    assert capability.host_gpu_name(0, nvml=no_nvml) == capability.UNKNOWN_DEVICE_NAME == "unknown"
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(initialised=True))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")  # torch index 0 would be another device than NVML index 0
    assert capability.host_gpu_name(0, nvml=no_nvml) == "unknown"


def test_declared_targets_name_the_host_gpu_from_nvml_and_drop_the_fixed_product(monkeypatch) -> None:
    targets = capability.declared_targets(refresh=True, nvml=FakeNvml(["NVIDIA RTX A6000"]))
    host = targets["host-gpu-0"]
    assert host == {"name": "NVIDIA RTX A6000", "kind": "host_gpu", "aliases": ["cuda:0"]}
    assert capability.DECLARED_TARGETS is targets  # the module attribute the core registry imports
    from mbnas_model_runtime.benchmark.capability import DECLARED_TARGETS

    assert DECLARED_TARGETS["host-gpu-0"]["name"] == "NVIDIA RTX A6000"
    text = repr(targets)
    assert OLD_PRODUCT not in text and set(targets) == {"host-gpu-0", "jetson-orin-nx", "jetson-orin-nano",
                                                        "jetson-agx-orin"}
    assert targets["jetson-orin-nx"]["name"] == "NVIDIA Jetson Orin NX"  # declared non-host targets unchanged
    again = capability.declared_targets(nvml=FakeNvml(["NOT PROBED AGAIN"]))  # cached until refresh=True
    assert again is targets and again["host-gpu-0"]["name"] == "NVIDIA RTX A6000"
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(initialised=False))
    gone = capability.declared_targets(refresh=True, nvml=FakeNvml(fail_init=True))
    assert gone is targets and gone["host-gpu-0"]["name"] == "unknown"  # refreshed in place


def test_device_targets_and_declared_target_for_use_the_live_names(monkeypatch) -> None:
    capability.declared_targets(refresh=True, nvml=FakeNvml(["NVIDIA RTX A6000"]))
    present = capability.device_targets([{"id": "host-gpu-0", "name": "Inventory GPU name"}])
    by_id = {t["id"]: t for t in present}
    assert by_id["host-gpu-0"]["available"] is True and by_id["host-gpu-0"]["name"] == "Inventory GPU name"
    absent = {t["id"]: t for t in capability.device_targets([])}
    assert absent["host-gpu-0"]["available"] is False and absent["host-gpu-0"]["name"] == "NVIDIA RTX A6000"
    assert "inventory" in absent["host-gpu-0"]["reason"]
    assert capability.declared_target_for("nvidia rtx a6000") == "host-gpu-0"
    assert capability.declared_target_for("cuda:0") == "host-gpu-0"
    assert capability.declared_target_for(OLD_PRODUCT) is None
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(initialised=False))
    capability.declared_targets(refresh=True, nvml=FakeNvml(fail_init=True))
    assert capability.declared_target_for("unknown") is None  # the placeholder never matches a requested device
    assert capability.target_status("host-gpu-0")["status"] == "AVAILABLE"
