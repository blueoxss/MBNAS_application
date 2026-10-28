"""TensorRT engine execution with torch CUDA tensors as I/O bindings (``execute_async_v3``).

:class:`TrtRunner` deserializes an engine (verifying its SHA-256 when given), allocates one torch CUDA tensor per
output with the engine's static shape/dtype, and per call sets tensor addresses and enqueues on a dedicated
(non-default) CUDA stream that first waits for torch's current stream (input ready); torch's current stream then
waits for the TensorRT stream, so later torch ops (postprocess) are ordered after inference. Outputs are reused.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mbnas_contracts.util.canon import sha256_hex


class TrtRunner:
    def __init__(self, engine_path: str | Path, device_index: int = 0, expect_sha256: str | None = None) -> None:
        import tensorrt as trt
        import torch

        data = Path(engine_path).read_bytes()
        if expect_sha256 and sha256_hex(data) != expect_sha256:
            raise ValueError(f"engine checksum mismatch for {engine_path}")
        self.device = torch.device("cuda", device_index)
        torch.cuda.set_device(self.device)
        self._logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self._logger)
        self.engine = self.runtime.deserialize_cuda_engine(data)
        if self.engine is None:
            raise RuntimeError(f"deserialize_cuda_engine failed for {engine_path}")
        self.context = self.engine.create_execution_context()
        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        is_input = {n: self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT for n in names}
        self.input_names = [n for n in names if is_input[n]]
        self.output_names = [n for n in names if not is_input[n]]
        if len(self.input_names) != 1:
            raise ValueError(f"expected one input tensor, got {self.input_names}")
        dtypes = {trt.DataType.FLOAT: torch.float32, trt.DataType.HALF: torch.float16,
                  trt.DataType.INT32: torch.int32, trt.DataType.INT64: torch.int64, trt.DataType.BOOL: torch.bool}
        self.input_shape = tuple(self.engine.get_tensor_shape(self.input_names[0]))
        self.input_dtype = dtypes[self.engine.get_tensor_dtype(self.input_names[0])]
        self._out = []
        for n in self.output_names:
            shape = tuple(self.engine.get_tensor_shape(n))
            if any(d < 0 for d in shape):
                raise ValueError(f"dynamic output shape {shape} for {n}; batch-1 static engines only")
            self._out.append(torch.empty(shape, device=self.device, dtype=dtypes[self.engine.get_tensor_dtype(n)]))
        self.sha256 = sha256_hex(data)
        self.version = trt.__version__
        self.stream = torch.cuda.Stream(device=self.device)

    def describe(self) -> dict[str, Any]:
        return {"runtime": "tensorrt", "version": self.version, "engine_sha256": self.sha256,
                "input": {"name": self.input_names[0], "shape": list(self.input_shape), "dtype": str(self.input_dtype)},
                "outputs": [{"name": n, "shape": list(t.shape), "dtype": str(t.dtype)}
                            for n, t in zip(self.output_names, self._out)],
                "execution": "IExecutionContext.execute_async_v3 on a dedicated CUDA stream, ordered with torch's "
                             "current stream via wait_stream"}

    def __call__(self, x: Any) -> list[Any]:
        import torch

        x = x.to(self.input_dtype).contiguous()
        if tuple(x.shape) != self.input_shape:
            raise ValueError(f"input shape {tuple(x.shape)} != engine shape {self.input_shape}")
        self.context.set_tensor_address(self.input_names[0], x.data_ptr())
        for n, buf in zip(self.output_names, self._out):
            self.context.set_tensor_address(n, buf.data_ptr())
        current = torch.cuda.current_stream(self.device)
        self.stream.wait_stream(current)
        x.record_stream(self.stream)
        if not self.context.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("execute_async_v3 returned False")
        current.wait_stream(self.stream)
        return self._out
