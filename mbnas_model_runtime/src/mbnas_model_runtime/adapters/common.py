"""Helpers shared by the backend adapters: structural architecture hash, recipe hash, environment snapshot,
weights verification and progress/epoch bookkeeping. torch is imported lazily."""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Callable, Mapping

from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_model_runtime.adapters.base import CapabilityError

ARCH_HASH_SCHEME = "mbnas-structural/1"


def structural_hash(module: Any) -> str:
    """Canonical SHA-256 of the *built* module: ordered (module type) list + (state key, shape, dtype) list.

    Two modules hash equal iff they have the same module class sequence and the same parameter/buffer names, shapes
    and dtypes, so a no-op mutation or a repair that rebuilds the same structure keeps the same hash. Weights do not
    enter the hash.
    """
    types = [f"{type(m).__module__}.{type(m).__qualname__}" for m in module.modules()]
    state = [[k, list(v.shape), str(v.dtype)] for k, v in module.state_dict().items()]
    return canonical_sha256({"scheme": ARCH_HASH_SCHEME, "modules": types, "state": state})


def count_modules(module: Any) -> dict[str, int]:
    """Convolution / attention / total parameter counts of a built module (operator audit)."""
    import torch.nn as nn

    conv = sum(isinstance(m, (nn.Conv1d, nn.Conv2d, nn.Conv3d)) for m in module.modules())
    attn = sum(isinstance(m, nn.MultiheadAttention) or "Attention" in type(m).__name__ for m in module.modules())
    return {"conv_modules": int(conv), "attention_modules": int(attn),
            "params": int(sum(p.numel() for p in module.parameters()))}


def recipe_hash(recipe: Mapping[str, Any]) -> str:
    """Canonical hash of the training recipe (excludes nothing: callers pass only schedule-relevant fields)."""
    return canonical_sha256({"recipe": dict(recipe)})


def verify_weights(path: str | None, expected_sha256: str | None, model_id: str) -> str:
    """Return the file sha256; raise ``CapabilityError(WEIGHTS_UNVERIFIED)`` on a missing file or digest mismatch."""
    if not path or not Path(path).is_file():
        raise CapabilityError("WEIGHTS_MISSING", f"{model_id}: pretrained weights file {path!r} not found")
    digest = sha256_file(path)
    if expected_sha256 and digest != expected_sha256:
        raise CapabilityError("WEIGHTS_UNVERIFIED", f"{model_id}: {path} sha256 {digest} != registry {expected_sha256}")
    return digest


def env_snapshot(device: str | None = None) -> dict[str, Any]:
    """Versions/device facts stored in every full state (provenance, not a resume key)."""
    import torch

    out: dict[str, Any] = {"python": platform.python_version(), "torch": str(torch.__version__),
                           "cuda": None if torch.version.cuda is None else str(torch.version.cuda),
                           "cudnn": torch.backends.cudnn.version()}
    if torch.cuda.is_available():
        idx = torch.device(device).index if device and device.startswith("cuda") else 0
        out["device_name"] = torch.cuda.get_device_name(idx or 0)
    return out


def cuda_index(device: str) -> int:
    """``cuda:N`` / ``N`` / ``cuda`` -> N (0 by default)."""
    text = str(device)
    if text.startswith("cuda"):
        return int(text.split(":", 1)[1]) if ":" in text else 0
    return int(text) if text.isdigit() else 0


def emit(progress: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    """Call the progress callback when present (exceptions propagate: a broken channel must fail the job)."""
    if progress is not None:
        progress(payload)


def tensor_state_digest(obj: Any) -> str:
    """SHA-256 over every tensor (bytes + shape + dtype) in a nested container, in key order (evidence helper)."""
    import hashlib

    import torch

    h = hashlib.sha256()

    def walk(x: Any, path: str) -> None:
        if torch.is_tensor(x):
            t = x.detach().cpu().contiguous()
            h.update(f"{path}|{list(t.shape)}|{t.dtype}|".encode())
            h.update(t.reshape(-1).view(torch.uint8).numpy().tobytes() if t.numel() else b"")
        elif isinstance(x, Mapping):
            for k in sorted(x, key=str):
                walk(x[k], f"{path}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        else:
            h.update(f"{path}={x!r};".encode())

    walk(obj, "$")
    return "sha256:" + h.hexdigest()
