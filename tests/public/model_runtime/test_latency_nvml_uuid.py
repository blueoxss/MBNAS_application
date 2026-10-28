"""NVML facts of a latency record belong to the CUDA device that ran (DD-A49 follow-up): ``gpu_manifest`` and
``NvmlSampler`` find the NVML device by torch's uuid, because NVML numbers devices in PCI bus order and ignores
``CUDA_VISIBLE_DEVICES`` (a runner child's ``cuda:0`` can be NVML index 1). The torch index is used as the NVML index
only when the uuid lookup is unavailable, with a logged warning and ``nvml_lookup = "index"``.

SYNTHETIC: NVML and torch are stubs in which torch's ``cuda:0`` is NVML index 1. No real NVML query, no CUDA call.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from mbnas_model_runtime.evaluation import latency as L

UUID_A = "GPU-aaaaaaaa-0000-0000-0000-000000000000"  # NVML index 0
UUID_B = "GPU-bbbbbbbb-1111-1111-1111-111111111111"  # NVML index 1 = torch cuda:0 below
LOGGER = "mbnas_model_runtime.evaluation.latency"


class FakeNvmlError(Exception):
    pass


class FakeNvml:
    """pynvml subset over two devices whose NVML order is (A, B); every reading identifies its device."""

    NVMLError = FakeNvmlError
    NVML_CLOCK_SM, NVML_CLOCK_MEM, NVML_TEMPERATURE_GPU = 0, 2, 0
    DEVICES = ({"uuid": UUID_A, "name": "SYNTHETIC card A", "power_mw": 50000, "sm": 1000, "limit_mw": 300000},
               {"uuid": UUID_B, "name": "SYNTHETIC card B", "power_mw": 90000, "sm": 1800, "limit_mw": 450000})

    def __init__(self) -> None:
        self.calls: list[str] = []

    def nvmlInit(self) -> None:
        self.calls.append("init")

    def nvmlShutdown(self) -> None:
        self.calls.append("shutdown")

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        self.calls.append(f"by_index:{index}")
        return index

    def nvmlDeviceGetHandleByUUID(self, uuid: str) -> int:
        self.calls.append(f"by_uuid:{uuid}")
        for i, dev in enumerate(self.DEVICES):
            if dev["uuid"] == uuid:
                return i
        raise FakeNvmlError("Not Found")

    def nvmlDeviceGetIndex(self, handle: int) -> int:
        return handle

    def nvmlDeviceGetUUID(self, handle: int) -> str:
        return self.DEVICES[handle]["uuid"]

    def nvmlDeviceGetName(self, handle: int) -> str:
        return self.DEVICES[handle]["name"]

    def nvmlSystemGetDriverVersion(self) -> str:
        return "SYNTHETIC driver"

    def nvmlDeviceGetPowerManagementLimit(self, handle: int) -> int:
        return self.DEVICES[handle]["limit_mw"]

    def nvmlDeviceGetPowerUsage(self, handle: int) -> int:
        return self.DEVICES[handle]["power_mw"]

    def nvmlDeviceGetClockInfo(self, handle: int, kind: int) -> int:
        return self.DEVICES[handle]["sm"]

    def nvmlDeviceGetMaxClockInfo(self, handle: int, kind: int) -> int:
        return self.DEVICES[handle]["sm"] + 100

    def nvmlDeviceGetTemperature(self, handle: int, kind: int) -> int:
        return 40 + handle

    def nvmlDeviceGetUtilizationRates(self, handle: int):
        return SimpleNamespace(gpu=10 * handle)

    def nvmlDeviceGetPersistenceMode(self, handle: int) -> int:
        return 1


class NvmlWithoutUuidLookup(FakeNvml):
    nvmlDeviceGetHandleByUUID = None  # an nvidia-ml-py without the call


class _TorchUuid:
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:  # torch's _CUuuid prints the bare 8-4-4-4-12 form
        return self.text


def fake_torch(uuid_text: str | None = UUID_B[len("GPU-"):]):
    """torch whose cuda:0 is card B (NVML index 1); ``uuid_text=None``: a torch without device uuids."""
    def props(index: int):
        return SimpleNamespace(name="SYNTHETIC torch name") if uuid_text is None else SimpleNamespace(
            name="SYNTHETIC torch name", uuid=_TorchUuid(uuid_text))

    def device(spec: str):
        kind, _, idx = spec.partition(":")
        return SimpleNamespace(type=kind, index=int(idx) if idx else None)

    cuda = SimpleNamespace(get_device_name=lambda index: "SYNTHETIC torch name", get_device_properties=props)
    return SimpleNamespace(__version__="2.6.0+synthetic", version=SimpleNamespace(cuda="12.4"),
                           backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: 90100)), cuda=cuda,
                           device=device)


def test_torch_device_uuid_is_nvml_shaped():
    assert L.torch_device_uuid(0, torch_module=fake_torch()) == UUID_B
    assert L.torch_device_uuid(0, torch_module=fake_torch(UUID_B)) == UUID_B  # already prefixed: unchanged
    assert L.torch_device_uuid(0, torch_module=fake_torch(None)) is None


def test_gpu_manifest_reads_the_device_with_torchs_uuid_not_the_same_index(caplog):
    nv = FakeNvml()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        man = L.gpu_manifest("cuda:0", nvml=nv, torch_module=fake_torch())
    assert man["cuda_index"] == 0 and man["nvml_lookup"] == "uuid" and "nvml_lookup_warning" not in man
    assert man["uuid"] == UUID_B and man["name"] == "SYNTHETIC card B"  # NVML index 1, not index 0 (card A)
    assert man["power_limit_w"] == 450.0 and man["sm_clock_mhz"] == 1800 and man["temperature_c"] == 41
    assert f"by_uuid:{UUID_B}" in nv.calls and not any(c.startswith("by_index") for c in nv.calls)
    assert nv.calls[0] == "init" and nv.calls[-1] == "shutdown" and not caplog.records


@pytest.mark.parametrize("nvml, torch_uuid, why", [
    (NvmlWithoutUuidLookup(), UUID_B[len("GPU-"):], "has no nvmlDeviceGetHandleByUUID"),
    (FakeNvml(), None, "torch reported no uuid"),
    (FakeNvml(), "cccccccc-2222-2222-2222-222222222222", "nvmlDeviceGetHandleByUUID(GPU-cccccccc"),
])
def test_gpu_manifest_falls_back_to_the_index_only_with_a_logged_warning(caplog, nvml, torch_uuid, why):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        man = L.gpu_manifest("cuda:0", nvml=nvml, torch_module=fake_torch(torch_uuid))
    assert man["nvml_lookup"] == "index" and why in man["nvml_lookup_warning"]
    assert man["uuid"] == UUID_A and "by_index:0" in nvml.calls  # the index fallback, visibly labelled
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and why in warnings[0].getMessage() and "NVML index" in warnings[0].getMessage()


def test_sampler_samples_the_device_with_the_given_uuid(caplog):
    nv = FakeNvml()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = L.NvmlSampler(0, nvml=nv, uuid=UUID_B)
        s.sample("after_warmup")
        s.sample("after_repeats")
        out = s.summary()
        s.close()
    assert out["power_draw_w"] == {"min": 90.0, "max": 90.0, "mean": 90.0, "n": 2}  # card B, not card A
    assert out["sm_clock_mhz"]["max"] == 1800 and out["nvml_lookup"] == "uuid"
    assert out["nvml_index"] == 1 and out["cuda_index"] == 0 and out["error"] is None
    assert nv.calls[-1] == "shutdown" and not caplog.records


def test_sampler_without_a_uuid_uses_the_index_and_says_so(caplog):
    nv = FakeNvml()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = L.NvmlSampler(0, nvml=nv)
        s.sample("after_warmup")
        out = s.summary()
        s.close()
    assert out["power_draw_w"]["max"] == 50.0 and out["nvml_lookup"] == "index" and out["nvml_index"] == 0
    assert "torch reported no uuid" in out["nvml_lookup_warning"]
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_measure_hands_torchs_uuid_to_the_sampler(monkeypatch):
    """measure() wiring on the CPU: CUDA synchronisation and the torch uuid read are stubbed (no CUDA call)."""
    torch = pytest.importorskip("torch")
    nv = FakeNvml()
    seen: list[tuple[int, str | None]] = []
    real_sampler = L.NvmlSampler

    def sampler(index, nvml=None, *, uuid=None):
        seen.append((index, uuid))
        return real_sampler(index, nvml=nv, uuid=uuid)

    monkeypatch.setattr(torch.cuda, "synchronize", lambda *a, **k: None)
    monkeypatch.setattr(L, "torch_device_uuid", lambda index, torch_module=None: UUID_B)
    monkeypatch.setattr(L, "NvmlSampler", sampler)
    rec = L.measure({"forward_only": lambda: None}, warmup=1, repeats=2, device="cuda:0", scopes=("forward_only",))
    assert seen == [(0, UUID_B)]
    ds = rec["device_sampling"]
    assert ds["nvml_lookup"] == "uuid" and ds["nvml_index"] == 1 and ds["power_draw_w"]["max"] == 90.0
    assert not torch.cuda.is_initialized()
