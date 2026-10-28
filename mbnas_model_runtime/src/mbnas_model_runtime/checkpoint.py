"""Full-state training checkpoints (format ``mbnas.full_state.v1``) with SHA-256 sidecars.

File layout per candidate (``<candidate_dir>/ckpt``):

* ``full_e{E:04d}.pt`` + ``full_e{E:04d}.pt.sha256.json`` — unstripped training state after ``E`` completed epochs
  (``epochs_done == E``). Written atomically (temp + fsync + rename) *before* its sidecar; a file without a matching
  sidecar is never resumed from. Retention keeps the newest file and every rung-end file.
* ``eval_ema_e{E:04d}.pt`` — FP32 EMA weights-only snapshot taken at the exact epoch boundary (evaluation input).
* ``weights_fp16_e{E:04d}.pt`` — FP16 contiguous state_dict (size artifact, decimal MB).

A full state is a dict of tensors and plain containers only, so it loads with ``torch.load(weights_only=True)``.
Generic keys: ``format, epoch (0-based last completed), epochs_done, global_step, samples_seen, rng{python, numpy,
torch_cpu, torch_cuda}, hashes{architecture, recipe, protocol, dataset_train, dataset_val}, lr_history, adapter,
adapter_meta, env``; each adapter adds its own required keys (model_fp32, ema_fp32, optimizer, scheduler, scaler,
loader_generator_state, …) via ``required_state_keys()``. :func:`load_full_state` raises
:class:`~mbnas_model_runtime.adapters.base.ResumeBlocked` for a missing sidecar, checksum mismatch, unreadable or
foreign file, missing/None state key or hash mismatch — there is no weights-only fallback.
"""
from __future__ import annotations

import json
import os
import random
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping

from mbnas_model_runtime.adapters.base import ResumeBlocked
from mbnas_contracts.util.atomic import atomic_write_json, atomic_write_stream, sha256_file
from mbnas_contracts.util.time import utcnow_iso

FORMAT = "mbnas.full_state.v1"
EVAL_FORMAT = "mbnas.eval_ema.v1"
WEIGHTS_FORMAT = "mbnas.weights_fp16.v1"
SIDECAR_SUFFIX = ".sha256.json"
GENERIC_REQUIRED = ("format", "epoch", "epochs_done", "global_step", "samples_seen", "rng", "rng.python",
                    "rng.numpy", "rng.torch_cpu", "hashes", "lr_history")
HASH_NAMES = ("architecture", "recipe", "protocol", "dataset_train", "dataset_val")
_FULL_RE = re.compile(r"^full_e(\d{4,})\.pt$")
_FULL_SIDECAR_RE = re.compile(r"^full_e(\d{4,})\.pt\.sha256\.json$")


# -- RNG ---------------------------------------------------------------------------------------------------------
def capture_rng(include_cuda: bool = True) -> dict[str, Any]:
    """Python/numpy/torch CPU/all-CUDA RNG states encoded as tensors and primitives (weights_only-safe).

    ``include_cuda=False`` records ``torch_cuda: []`` without touching (initialising) CUDA — for CPU-only callers
    such as unit tests; training adapters always capture every CUDA device.
    """
    import numpy as np
    import torch

    version, state, gauss = random.getstate()
    name, keys, pos, has_gauss, cached = np.random.get_state()
    return {
        "python": {"version": int(version), "state": torch.tensor(state, dtype=torch.int64), "gauss": gauss},
        "numpy": {"name": str(name), "keys": torch.from_numpy(np.asarray(keys, dtype=np.int64).copy()),
                  "pos": int(pos), "has_gauss": int(has_gauss), "cached_gaussian": float(cached)},
        "torch_cpu": torch.get_rng_state().clone(),
        "torch_cuda": ([s.clone() for s in torch.cuda.get_rng_state_all()]
                       if include_cuda and torch.cuda.is_available() else []),
    }


def restore_rng(rng: Mapping[str, Any]) -> None:
    """Inverse of :func:`capture_rng`; raises ``ResumeBlocked`` when the CUDA device count differs."""
    import numpy as np
    import torch

    py = rng["python"]
    random.setstate((py["version"], tuple(int(v) for v in py["state"].tolist()), py["gauss"]))
    npy = rng["numpy"]
    np.random.set_state((npy["name"], npy["keys"].numpy().astype(np.uint32), npy["pos"], npy["has_gauss"],
                         npy["cached_gaussian"]))
    torch.set_rng_state(rng["torch_cpu"])
    cuda = rng.get("torch_cuda") or []
    if cuda:
        if not torch.cuda.is_available() or torch.cuda.device_count() != len(cuda):
            raise ResumeBlocked("RNG_DEVICE_MISMATCH", f"saved {len(cuda)} CUDA RNG states, "
                                f"{torch.cuda.device_count() if torch.cuda.is_available() else 0} visible")
        torch.cuda.set_rng_state_all(cuda)


def rng_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, bool]:
    """Per-stream equality of two captured RNG states (used by the resume equivalence evidence)."""
    import torch

    def same(x: Any, y: Any) -> bool:
        if torch.is_tensor(x) and torch.is_tensor(y):
            return bool(torch.equal(x, y))
        if isinstance(x, dict) and isinstance(y, dict):
            return x.keys() == y.keys() and all(same(x[k], y[k]) for k in x)
        if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
            return len(x) == len(y) and all(same(p, q) for p, q in zip(x, y))
        return x == y

    return {k: same(a.get(k), b.get(k)) for k in ("python", "numpy", "torch_cpu", "torch_cuda")}


# -- tensors -----------------------------------------------------------------------------------------------------
def to_cpu_clone(obj: Any) -> Any:
    """Deep copy with every tensor detached, cloned and on CPU, **memory format preserved**.

    Strides matter: ultralytics trains channels_last on CUDA and the fused AdamW kernel requires optimizer moments in
    the same layout as their parameters, so a contiguous copy of a channels_last moment cannot be resumed.
    """
    import torch

    if torch.is_tensor(obj):
        return obj.detach().to("cpu", copy=True)
    if isinstance(obj, dict):
        return {k: to_cpu_clone(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_cpu_clone(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(to_cpu_clone(v) for v in obj)
    return obj


def plain(obj: Any) -> Any:
    """Convert numpy scalars/arrays inside containers to Python numbers/lists (weights_only-safe)."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is a hard dependency
        np = None  # type: ignore[assignment]
    if np is not None and isinstance(obj, np.generic):
        return obj.item()
    if np is not None and isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(plain(v) for v in obj)
    return obj


# -- paths -------------------------------------------------------------------------------------------------------
def full_state_path(ckpt_dir: str | Path, epochs_done: int) -> Path:
    return Path(ckpt_dir) / f"full_e{epochs_done:04d}.pt"


def eval_snapshot_path(ckpt_dir: str | Path, epochs_done: int) -> Path:
    return Path(ckpt_dir) / f"eval_ema_e{epochs_done:04d}.pt"


def weights_only_path(ckpt_dir: str | Path, epochs_done: int) -> Path:
    return Path(ckpt_dir) / f"weights_fp16_e{epochs_done:04d}.pt"


def sidecar_path(path: str | Path) -> Path:
    return Path(str(path) + SIDECAR_SUFFIX)


# -- save / load -------------------------------------------------------------------------------------------------
_SAFE_SCALARS = (str, int, float, bool, type(None))


def assert_weights_only_safe(obj: Any, path: str = "$") -> None:
    """Raise ``TypeError`` naming the first value ``torch.load(weights_only=True)`` would refuse.

    Only tensors, containers (dict/OrderedDict/list/tuple) and plain scalars are allowed; ``str``
    subclasses such as ``torch.torch_version.TorchVersion`` are refused (they pickle as their class).
    """
    import torch

    if torch.is_tensor(obj):
        return
    if type(obj) in _SAFE_SCALARS:
        return
    if type(obj) in (dict, OrderedDict):
        for k, v in obj.items():
            if type(k) not in (str, int):
                raise TypeError(f"non-str/int key {k!r} at {path}")
            assert_weights_only_safe(v, f"{path}.{k}")
        return
    if type(obj) in (list, tuple):
        for i, v in enumerate(obj):
            assert_weights_only_safe(v, f"{path}[{i}]")
        return
    raise TypeError(f"{type(obj).__module__}.{type(obj).__qualname__} at {path} is not weights_only-safe")


def _torch_save_atomic(path: Path, obj: Any) -> None:
    import torch

    assert_weights_only_safe(obj)
    atomic_write_stream(path, lambda fh: torch.save(obj, fh), mode=0o600)


def save_full_state(path: str | Path, state: Mapping[str, Any], *, rung_end: bool = False) -> dict[str, Any]:
    """Atomically write ``state`` and then its sidecar; returns the sidecar dict (``sha256``, ``bytes``, …)."""
    if state.get("format") != FORMAT:
        raise ValueError(f"state format must be {FORMAT!r}")
    target = Path(path)
    _torch_save_atomic(target, dict(state))
    side = {"format": FORMAT, "file": target.name, "sha256": sha256_file(target), "bytes": target.stat().st_size,
            "epoch": int(state["epoch"]), "epochs_done": int(state["epochs_done"]),
            "global_step": int(state["global_step"]), "samples_seen": int(state["samples_seen"]),
            "hashes": dict(state["hashes"]), "rung_end": bool(rung_end), "created_at": utcnow_iso()}
    atomic_write_json(sidecar_path(target), side)
    return side


def read_sidecar(path: str | Path) -> dict[str, Any]:
    """Sidecar of ``path`` or ``ResumeBlocked(SIDECAR_MISSING)``."""
    try:
        return json.loads(sidecar_path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ResumeBlocked("SIDECAR_MISSING", f"{path}: {exc}") from exc


def verify_file(path: str | Path) -> dict[str, Any]:
    """Check the file exists and matches its sidecar sha256; returns the sidecar."""
    p = Path(path)
    if not p.is_file():
        raise ResumeBlocked("FILE_MISSING", str(p))
    side = read_sidecar(p)
    digest = sha256_file(p)
    if digest != side.get("sha256"):
        raise ResumeBlocked("CHECKSUM_MISMATCH", f"{p.name}: sidecar {side.get('sha256')} != file {digest}")
    return side


def _get_dotted(state: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = state
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check_state(state: Mapping[str, Any], *, required: Iterable[str] = (),
                expect_hashes: Mapping[str, str] | None = None) -> None:
    """Raise ``ResumeBlocked`` unless ``state`` is a complete full state with the expected hashes."""
    if not isinstance(state, Mapping) or state.get("format") != FORMAT:
        raise ResumeBlocked("FORMAT_UNKNOWN", f"not a {FORMAT} checkpoint")
    for key in (*GENERIC_REQUIRED, *required):
        if _get_dotted(state, key) is None:
            raise ResumeBlocked(f"MISSING_STATE:{key}", "full-state resume needs it; no weights-only fallback")
    for name, want in (expect_hashes or {}).items():
        have = state["hashes"].get(name)
        if have != want:
            raise ResumeBlocked(f"HASH_MISMATCH:{name}", f"checkpoint {have} != expected {want}")


def load_full_state(path: str | Path, *, required: Iterable[str] = (),
                    expect_hashes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Verify sidecar + checksum, load with ``weights_only=True`` and :func:`check_state`."""
    import torch

    verify_file(path)
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001 - any unpickling failure means the file is not resumable
        raise ResumeBlocked("UNREADABLE", f"{path}: {type(exc).__name__}: {exc}") from exc
    check_state(state, required=required, expect_hashes=expect_hashes)
    return state


def list_full_states(ckpt_dir: str | Path) -> list[tuple[int, Path]]:
    """``[(epochs_done, path)]`` of full-state files (verified or not), ascending."""
    d = Path(ckpt_dir)
    out = []
    if d.is_dir():
        for p in d.iterdir():
            m = _FULL_RE.match(p.name)
            if m:
                out.append((int(m.group(1)), p))
    return sorted(out)


def orphan_sidecars(ckpt_dir: str | Path) -> list[tuple[int, Path]]:
    """``[(epochs_done, payload_path)]`` of full-state sidecars whose payload file is missing, ascending.

    A sidecar is written only after its payload, so an orphan sidecar means the payload was deleted afterwards
    (operator/disk cleanup or a retention policy, which keeps sidecars): the state it describes is gone.
    """
    d = Path(ckpt_dir)
    out = []
    if d.is_dir():
        for p in d.iterdir():
            m = _FULL_SIDECAR_RE.match(p.name)
            if m:
                payload = p.with_name(p.name[: -len(SIDECAR_SUFFIX)])
                if not payload.exists():
                    out.append((int(m.group(1)), payload))
    return sorted(out)


def latest_resumable(ckpt_dir: str | Path, *, max_epochs_done: int, required: Iterable[str] = (),
                     expect_hashes: Mapping[str, str] | None = None) -> tuple[dict[str, Any] | None, Path | None,
                                                                                list[dict[str, str]]]:
    """Newest checkpoint with ``epochs_done <= max_epochs_done`` that verifies.

    Corrupt or sidecar-less files are skipped (recorded in the returned ``skipped`` list) and the next older one is
    tried — the corruption fallback. A *hash mismatch* or a *missing state key* is not skipped: it means the
    protocol/recipe/architecture changed or the file was stripped, so it raises ``ResumeBlocked`` immediately rather
    than silently resuming an older file. A sidecar whose payload is missing is a skipped ``FILE_MISSING`` entry
    (the state existed and was deleted). When files exist but none verifies, ``(None, None, skipped)`` is returned
    and the caller must block (never restart from scratch silently).
    """
    skipped: list[dict[str, str]] = []
    required = tuple(required)
    entries = [(e, p, False) for e, p in list_full_states(ckpt_dir)] + [(e, p, True) for e, p in orphan_sidecars(ckpt_dir)]
    for epochs_done, path, orphan in sorted(entries, key=lambda t: t[0], reverse=True):
        if epochs_done > max_epochs_done:
            continue
        if orphan:
            skipped.append({"path": str(path), "reason": "FILE_MISSING",
                            "detail": f"sidecar {sidecar_path(path).name} present, payload deleted"})
            continue
        try:
            return load_full_state(path, required=required, expect_hashes=expect_hashes), path, skipped
        except ResumeBlocked as exc:
            if exc.reason.startswith("HASH_MISMATCH") or exc.reason.startswith("MISSING_STATE"):
                raise
            skipped.append({"path": str(path), "reason": exc.reason, "detail": exc.detail})
    return None, None, skipped


def prune(ckpt_dir: str | Path) -> list[str]:
    """Delete full-state files that are neither the newest nor marked ``rung_end``; returns removed paths."""
    states = list_full_states(ckpt_dir)
    removed = []
    for epochs_done, path in states[:-1]:
        try:
            keep = bool(read_sidecar(path).get("rung_end"))
        except ResumeBlocked:
            keep = False
        if not keep:
            for p in (path, sidecar_path(path)):
                if p.exists():
                    os.unlink(p)
            removed.append(str(path))
    return removed


def mark_rung_end(path: str | Path) -> dict[str, Any]:
    """Set ``rung_end: true`` in an existing sidecar (atomic rewrite); the checkpoint file itself is unchanged."""
    side = verify_file(path)
    side["rung_end"] = True
    atomic_write_json(sidecar_path(path), side)
    return side


# -- evaluation snapshot / size artifact -------------------------------------------------------------------------
def save_eval_snapshot(path: str | Path, state_dict: Mapping[str, Any], *, model_meta: Mapping[str, Any],
                       epochs_done: int, hashes: Mapping[str, str]) -> dict[str, Any]:
    """FP32 EMA weights-only snapshot (+ what is needed to rebuild the module); returns ``{sha256, bytes}``."""
    import torch

    sd = {k: (v.detach().float().cpu().contiguous() if torch.is_tensor(v) and v.is_floating_point()
              else (v.detach().cpu().contiguous() if torch.is_tensor(v) else v)) for k, v in state_dict.items()}
    target = Path(path)
    _torch_save_atomic(target, {"format": EVAL_FORMAT, "state_dict": sd, "model_meta": plain(dict(model_meta)),
                                "epochs_done": int(epochs_done), "hashes": dict(hashes), "dtype": "float32"})
    side = {"format": EVAL_FORMAT, "file": target.name, "sha256": sha256_file(target), "bytes": target.stat().st_size,
            "epochs_done": int(epochs_done), "created_at": utcnow_iso()}
    atomic_write_json(sidecar_path(target), side)
    return side


def load_eval_snapshot(path: str | Path) -> dict[str, Any]:
    """Verify and load an evaluation snapshot (``ResumeBlocked`` codes reused for integrity failures)."""
    import torch

    verify_file(path)
    snap = torch.load(path, map_location="cpu", weights_only=True)
    if snap.get("format") != EVAL_FORMAT:
        raise ResumeBlocked("FORMAT_UNKNOWN", f"{path} is not {EVAL_FORMAT}")
    return snap


def write_weights_fp16(state_dict: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    """FP16 contiguous state_dict via ``torch.save`` (A.14); returns ``{sha256, bytes, size_mb, dtype}``."""
    import torch

    sd = {k: (v.detach().half().cpu().contiguous() if torch.is_tensor(v) and v.is_floating_point()
              else (v.detach().cpu().contiguous() if torch.is_tensor(v) else v)) for k, v in state_dict.items()}
    target = Path(path)
    _torch_save_atomic(target, sd)
    size = target.stat().st_size
    return {"format": WEIGHTS_FORMAT, "sha256": sha256_file(target), "bytes": size, "size_mb": size / 1e6,
            "dtype": "float16", "path": str(target)}
