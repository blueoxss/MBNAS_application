"""Weights-only size artifact: FP16 contiguous ``state_dict`` saved with ``torch.save``; size in decimal MB (1e6 B).

The dtype is part of the definition (an FP32 file is about twice as large), so it is always recorded next to the
number. Optimizer, EMA bookkeeping and training metadata are never included.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from mbnas_model_runtime.checkpoint import write_weights_fp16

SIZE_DEFINITION = "weights_only_state_dict_fp16_contiguous_torch_save_decimal_MB"


def size_mb(path: str | os.PathLike[str]) -> float:
    """Decimal megabytes of a file."""
    return Path(path).stat().st_size / 1e6


def weights_only_artifact(state_dict: Mapping[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
    """Write the FP16 weights-only file and describe it (sha256, bytes, size_mb, dtype, definition)."""
    info = write_weights_fp16(state_dict, path)
    return {**info, "definition": SIZE_DEFINITION}
