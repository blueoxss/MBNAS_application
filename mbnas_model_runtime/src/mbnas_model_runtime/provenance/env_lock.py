"""Environment lock: interpreter, package, CUDA/cuDNN/TensorRT/driver versions and the live GPU inventory.

``collect()`` reads installed distribution versions via ``importlib.metadata`` (no heavy imports), imports torch
only to read its CUDA/cuDNN build, and queries NVML (nvidia-ml-py) for the driver and each GPU. Anything that
cannot be read is recorded as ``None`` with an ``errors`` entry instead of guessed. ``write_environment_lock``
stores it atomically as ``$MBNAS_HOME/environment.lock.json`` (:data:`DEFAULT_LOCK_PATH`, DD-A13).

``host_gpu_inventory()`` / ``validation_devices()`` give the device list the validator resolves
``console.benchmark.device`` against: ids ``host-gpu-<nvml index>``, plus the UUID, the product name and (only
when ``CUDA_VISIBLE_DEVICES`` is unset, so indices agree) ``cuda:<index>`` as aliases. INT8 is not offered: there is
no calibrated INT8 pipeline.
"""
from __future__ import annotations

import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.home import mbnas_home
from mbnas_contracts.util.time import utcnow_iso

LOCK_SCHEMA = "mbnas.environment_lock/1"
#: ``$MBNAS_HOME/environment.lock.json``, fixed when this module is imported (no installation-tree path, DD-A13)
DEFAULT_LOCK_PATH: Path = mbnas_home() / "environment.lock.json"
#: third-party distributions + the five MBNAS distributions of the package split (DD-A24)
DISTRIBUTIONS = ("torch", "torchvision", "ultralytics", "rfdetr", "tensorrt_cu12", "onnx", "onnxruntime-gpu",
                 "onnxsim", "fastapi", "uvicorn", "pydantic", "pydantic-settings", "numpy", "pillow",
                 "opencv-python-headless", "nvidia-ml-py", "transformers", "PyYAML", "mbnas-server",
                 "mbnas-contracts", "mbnas-model-runtime", "mbnas-core", "mbnas-runner")
VALIDATION_PRECISIONS = ("FP32", "FP16")


def _dist_versions(errors: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in DISTRIBUTIONS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
            errors.append(f"distribution {name} not installed")
    return out


def _torch_build(errors: list[str]) -> dict[str, Any]:
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
        errors.append(f"torch import failed: {exc}")
        return {"cuda": None, "cudnn": None, "cuda_available": None}
    return {"cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
            "cuda_available": bool(torch.cuda.is_available())}


def _nvml(errors: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import pynvml
    except ImportError as exc:
        errors.append(f"nvidia-ml-py unavailable: {exc}")
        return {"driver": None, "cuda_driver": None}, []
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError as exc:
        errors.append(f"NVML init failed: {exc}")
        return {"driver": None, "cuda_driver": None}, []
    try:
        cuda_drv = pynvml.nvmlSystemGetCudaDriverVersion()
        system = {"driver": pynvml.nvmlSystemGetDriverVersion(),
                  "cuda_driver": f"{cuda_drv // 1000}.{(cuda_drv % 1000) // 10}"}
        gpus = [_gpu(pynvml, i, errors) for i in range(pynvml.nvmlDeviceGetCount())]
        return system, gpus
    finally:
        pynvml.nvmlShutdown()


def _gpu(pynvml: Any, index: int, errors: list[str]) -> dict[str, Any]:
    h = pynvml.nvmlDeviceGetHandleByIndex(index)
    gpu: dict[str, Any] = {"index": index, "id": f"host-gpu-{index}", "uuid": pynvml.nvmlDeviceGetUUID(h),
                           "name": pynvml.nvmlDeviceGetName(h),
                           "memory_total_mb": pynvml.nvmlDeviceGetMemoryInfo(h).total // (1024 * 1024)}
    probes = {
        "compute_capability": lambda: ".".join(map(str, pynvml.nvmlDeviceGetCudaComputeCapability(h))),
        "power_limit_w": lambda: pynvml.nvmlDeviceGetPowerManagementLimit(h) / 1000,
        "max_sm_clock_mhz": lambda: pynvml.nvmlDeviceGetMaxClockInfo(h, pynvml.NVML_CLOCK_SM),
    }
    for key, probe in probes.items():
        try:
            gpu[key] = probe()
        except pynvml.NVMLError as exc:
            gpu[key] = None
            errors.append(f"gpu {index} {key}: {exc}")
    return gpu


def host_gpu_inventory() -> list[dict[str, Any]]:
    """Live NVML GPU list (empty when NVML is unavailable)."""
    return _nvml([])[1]


def validation_devices(inventory: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Device entries for ``ValidationContext.devices`` built from the live (or given) inventory."""
    inventory = host_gpu_inventory() if inventory is None else inventory
    remapped = os.environ.get("CUDA_VISIBLE_DEVICES") is not None
    return [{"id": g["id"], "uuid": g["uuid"], "name": g["name"],
             "aliases": [g["uuid"], g["name"]] + ([] if remapped else [f"cuda:{g['index']}"]),
             "precisions": list(VALIDATION_PRECISIONS), "memory_total_mb": g.get("memory_total_mb")}
            for g in inventory]


def collect() -> dict[str, Any]:
    """Collect the environment lock document (see module docstring)."""
    errors: list[str] = []
    system, gpus = _nvml(errors)
    return {
        "schema": LOCK_SCHEMA, "generated_at": utcnow_iso(), "host": platform.node(),
        "os": platform.platform(), "python": {"version": platform.python_version(), "executable": sys.executable},
        "packages": _dist_versions(errors), "torch_build": _torch_build(errors), "nvidia": system, "gpus": gpus,
        "errors": errors,
    }


def write_environment_lock(path: str | os.PathLike[str] = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    """Collect and atomically write the lock file; returns the document."""
    doc = collect()
    atomic_write_json(path, doc)
    return doc
