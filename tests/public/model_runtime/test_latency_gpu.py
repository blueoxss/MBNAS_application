"""REAL GPU: latency measurement mechanics (synchronised repeats, scopes, device manifest)."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu, pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA GPU required")]


def test_measure_scopes_and_manifest() -> None:
    from mbnas_model_runtime.evaluation.latency import gpu_manifest, measure

    net = torch.nn.Sequential(torch.nn.Conv2d(3, 16, 3, padding=1), torch.nn.ReLU()).cuda().eval()
    x = torch.randn(1, 3, 64, 64, device="cuda")

    def fwd():
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            return net(x)

    out = measure({"forward_only": fwd, "forward_and_postprocess": lambda: fwd().amax(),
                   "end_to_end": lambda: net(torch.randn(1, 3, 64, 64).cuda())}, warmup=3, repeats=10, device="cuda:0")
    assert out["latency_source"] == "measured" and out["batch"] == 1
    for scope in ("forward_only", "forward_and_postprocess", "end_to_end"):
        s = out["scopes"][scope]
        assert s["n"] == 10 and 0 < s["min_ms"] <= s["p50_ms"] <= s["p95_ms"] <= s["max_ms"]
    # S8-F4: preprocess derived from the same round-robin repeats; batch-1 only, so no throughput number
    assert out["version"] == "mbnas-latency/4"
    assert out["preprocess_ms"] == pytest.approx(out["scopes"]["end_to_end"]["p50_ms"]
                                                 - out["scopes"]["forward_and_postprocess"]["p50_ms"])
    assert out["preprocess_ms_method"] == "derived"
    assert out["throughput_fps"] is None and out["throughput_fps_note"] == "not measured (batch 1 only)"
    # S8-F6: NVML power draw and SM clock sampled after warm-up, after every repeat round and after the repeats
    ds = out["device_sampling"]
    assert ds.get("error") is None, ds
    assert ds["power_draw_w"]["n"] == ds["sm_clock_mhz"]["n"] == 10 + 2
    for key in ("power_draw_w", "sm_clock_mhz"):
        assert 0 < ds[key]["min"] <= ds[key]["mean"] <= ds[key]["max"]
    assert ds["after_warmup"]["sm_clock_mhz"] > 0 and ds["after_repeats"]["power_draw_w"] > 0
    man = gpu_manifest("cuda:0")
    assert man["uuid"].startswith("GPU-") and man["driver"] and man["cuda_runtime"] and man["power_limit_w"] > 0
    with pytest.raises(ValueError):
        measure({"forward_only": fwd}, warmup=0, repeats=1, device="cuda:0")  # missing default scopes
