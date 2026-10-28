"""S8-F4 / S8-F6 (CPU, no CUDA): derived preprocess time, explicit throughput null and the NVML power/clock
sampling summary of ``evaluation.latency`` (the timing itself is covered by tests/evaluation/test_latency_gpu.py)."""
from __future__ import annotations

import pytest

from mbnas_model_runtime.evaluation import latency as L


def _scopes(e2e: float, fpp: float, fwd: float = 1.0) -> dict:
    return {"forward_only": {"p50_ms": fwd}, "forward_and_postprocess": {"p50_ms": fpp}, "end_to_end": {"p50_ms": e2e}}


def test_latency_version_bumped_for_the_new_fields() -> None:
    assert L.LATENCY_VERSION == "mbnas-latency/4"


def test_preprocess_is_derived_and_labelled() -> None:
    out = L.preprocess_and_throughput(_scopes(3.329, 1.810))
    assert out["preprocess_ms"] == pytest.approx(1.519, abs=1e-9)
    assert out["preprocess_ms_method"] == "derived"
    assert "p50(end_to_end) - p50(forward_and_postprocess)" in out["preprocess_ms_definition"]
    assert "round-robin" in out["preprocess_ms_definition"]
    assert out["throughput_fps"] is None and out["throughput_fps_note"] == "not measured (batch 1 only)"


def test_preprocess_is_null_when_a_scope_is_missing() -> None:
    out = L.preprocess_and_throughput({"forward_only": {"p50_ms": 1.0}, "forward_and_postprocess": {"p50_ms": 2.0}})
    assert out["preprocess_ms"] is None and out["preprocess_ms_method"] == "not available"
    assert out["throughput_fps"] is None and out["throughput_fps_note"] == "not measured (batch 1 only)"


def test_negative_derived_preprocess_is_recorded_verbatim_not_clamped() -> None:
    out = L.preprocess_and_throughput(_scopes(1.80, 1.81))
    assert out["preprocess_ms"] == pytest.approx(-0.01)  # timing noise is shown, never hidden


class _FakeNvml:
    """Stand-in for the pynvml module: power in mW and SM clock in MHz from fixed sequences."""

    NVML_CLOCK_SM = 0

    class NVMLError(Exception):
        pass

    def __init__(self, power_mw, clocks, fail=False):
        self.power, self.clocks, self.fail, self.shutdown = list(power_mw), list(clocks), fail, False

    def nvmlInit(self):
        if self.fail:
            raise self.NVMLError("NVML Shared Library Not Found")

    def nvmlShutdown(self):
        self.shutdown = True

    def nvmlDeviceGetHandleByIndex(self, idx):
        return ("h", idx)

    def nvmlDeviceGetPowerUsage(self, h):
        return self.power.pop(0)

    def nvmlDeviceGetClockInfo(self, h, kind):
        return self.clocks.pop(0)


def test_sampler_summarises_power_and_sm_clock(monkeypatch) -> None:
    fake = _FakeNvml([60000, 80000, 100000, 90000], [1410, 1410, 1395, 1410])
    s = L.NvmlSampler(0, nvml=fake)
    s.sample("after_warmup")
    s.sample("repeat")
    s.sample("repeat")
    s.sample("after_repeats")
    out = s.summary()
    s.close()
    assert fake.shutdown
    assert out["power_draw_w"] == {"min": 60.0, "max": 100.0, "mean": 82.5, "n": 4}
    assert out["sm_clock_mhz"] == {"min": 1395, "max": 1410, "mean": 1406.25, "n": 4}
    assert out["after_warmup"] == {"power_draw_w": 60.0, "sm_clock_mhz": 1410}
    assert out["after_repeats"] == {"power_draw_w": 90.0, "sm_clock_mhz": 1410}
    assert "outside the timed window" in out["method"] and out["nvml_index"] == 0


def test_sampler_records_nvml_failure_instead_of_raising() -> None:
    s = L.NvmlSampler(0, nvml=_FakeNvml([], [], fail=True))
    s.sample("after_warmup")
    out = s.summary()
    s.close()
    assert out["power_draw_w"] is None and out["sm_clock_mhz"] is None
    assert out["error"].startswith("NVMLError")
