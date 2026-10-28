"""ONNX Runtime inference on the CUDA execution provider with torch CUDA tensors bound through IOBinding.

:class:`OrtRunner` refuses to run when the session's first provider is not ``CUDAExecutionProvider`` (no silent CPU
fallback for a GPU measurement). ``runner(x)`` binds the contiguous float32 CUDA input tensor and pre-allocated CUDA
output tensors (static shapes from the graph), runs, and returns the output tensors (reused across calls; clone to
keep them). ORT runs on its own CUDA stream, so timing callers synchronise the device (``torch.cuda.synchronize``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

_NP_TO_TORCH = {"tensor(float)": "float32", "tensor(float16)": "float16", "tensor(int64)": "int64",
                "tensor(int32)": "int32", "tensor(bool)": "bool"}


class OrtUnavailable(RuntimeError):
    """CUDA execution provider not active for this session (exact reason in the message)."""


class OrtRunner:
    def __init__(self, onnx_path: str | Path, device_index: int = 0) -> None:
        import onnxruntime as ort
        import torch

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=[
            ("CUDAExecutionProvider", {"device_id": device_index}), "CPUExecutionProvider"])
        self.providers = self.session.get_providers()
        if not self.providers or self.providers[0] != "CUDAExecutionProvider":
            raise OrtUnavailable(f"CUDAExecutionProvider inactive; session providers {self.providers}")
        self.device = torch.device("cuda", device_index)
        self.inputs = [(i.name, i.shape, i.type) for i in self.session.get_inputs()]
        self.outputs = [(o.name, o.shape, o.type) for o in self.session.get_outputs()]
        if len(self.inputs) != 1:
            raise ValueError(f"expected one graph input, got {[i[0] for i in self.inputs]}")
        dynamic = [o[0] for o in self.outputs if not all(isinstance(d, int) for d in o[1])]
        if dynamic or not all(isinstance(d, int) for d in self.inputs[0][1]):
            raise ValueError(f"static shapes required (batch-1 export); dynamic outputs {dynamic}")
        self._out = [torch.empty(tuple(shape), device=self.device, dtype=getattr(torch, _NP_TO_TORCH[t]))
                     for _, shape, t in self.outputs]
        self._binding = self.session.io_binding()
        self.version = ort.__version__

    def describe(self) -> dict[str, Any]:
        return {"runtime": "onnxruntime", "version": self.version, "providers": self.providers,
                "inputs": [{"name": n, "shape": s, "type": t} for n, s, t in self.inputs],
                "outputs": [{"name": n, "shape": s, "type": t} for n, s, t in self.outputs]}

    def __call__(self, x: Any) -> list[Any]:
        name, shape, _ = self.inputs[0]
        x = x.contiguous().float()
        if tuple(x.shape) != tuple(shape):
            raise ValueError(f"input shape {tuple(x.shape)} != static graph shape {tuple(shape)}")
        b = self._binding
        b.clear_binding_inputs()
        b.clear_binding_outputs()
        b.bind_input(name, "cuda", self.device.index, np.float32, tuple(x.shape), x.data_ptr())
        for (oname, _, _), buf in zip(self.outputs, self._out):
            b.bind_output(oname, "cuda", self.device.index, np.dtype(str(buf.dtype).replace("torch.", "")),
                          tuple(buf.shape), buf.data_ptr())
        self.session.run_with_iobinding(b)
        return self._out
