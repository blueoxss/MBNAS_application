"""Full-state checkpoint format: atomic write, sha256 sidecar, corruption fallback, RESUME_BLOCKED cases, RNG."""
from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
import pytest
torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

from mbnas_model_runtime.adapters.base import ResumeBlocked
from mbnas_model_runtime import checkpoint as ck
from mbnas_contracts.util.atomic import atomic_write_stream

HASHES = {"architecture": "sha256:a", "recipe": "sha256:r", "protocol": "sha256:p", "dataset_train": "sha256:t",
          "dataset_val": "sha256:v"}


def make_state(epochs_done: int, **extra) -> dict:
    model = torch.nn.Linear(3, 2)
    opt = torch.optim.AdamW(model.parameters(), lr=0.1)
    model(torch.ones(1, 3)).sum().backward()
    opt.step()
    return {"format": ck.FORMAT, "epoch": epochs_done - 1, "epochs_done": epochs_done, "global_step": 4 * epochs_done,
            "samples_seen": 32 * epochs_done, "model_fp32": ck.to_cpu_clone(model.state_dict()),
            "optimizer": ck.to_cpu_clone(opt.state_dict()), "scheduler": {"last_epoch": epochs_done},
            "rng": ck.capture_rng(include_cuda=False), "hashes": dict(HASHES), "lr_history": [{"epoch": 0, "lr": [0.1]}],
            "adapter_meta": {"k": 1}, **extra}


def test_roundtrip_and_sidecar(tmp_path: Path) -> None:
    path = ck.full_state_path(tmp_path, 2)
    side = ck.save_full_state(path, make_state(2), rung_end=True)
    assert path.name == "full_e0002.pt" and ck.sidecar_path(path).is_file()
    assert side["rung_end"] is True and side["epochs_done"] == 2 and len(side["sha256"]) == 64
    st = ck.load_full_state(path, required=("model_fp32", "optimizer"), expect_hashes=HASHES)
    assert st["global_step"] == 8 and st["model_fp32"]["weight"].shape == (2, 3)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_refuses_non_weights_only_values(tmp_path: Path) -> None:
    class Ver(str):
        pass

    with pytest.raises(TypeError, match="weights_only"):
        ck.save_full_state(tmp_path / "x.pt", make_state(1, env={"torch": Ver("2.6")}))
    assert not (tmp_path / "x.pt").exists()


def test_atomic_write_leaves_no_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "full_e0001.pt"

    def boom(fh) -> None:
        fh.write(b"partial")
        raise RuntimeError("crash mid-write")

    with pytest.raises(RuntimeError):
        atomic_write_stream(target, boom)
    assert not target.exists() and not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_checksum_mismatch_and_fallback(tmp_path: Path) -> None:
    for e in (1, 2):
        ck.save_full_state(ck.full_state_path(tmp_path, e), make_state(e), rung_end=True)
    newest = ck.full_state_path(tmp_path, 2)
    data = bytearray(newest.read_bytes())
    data[len(data) // 2] ^= 0xFF
    newest.write_bytes(bytes(data))
    with pytest.raises(ResumeBlocked) as err:
        ck.load_full_state(newest)
    assert err.value.reason == "CHECKSUM_MISMATCH"
    state, path, skipped = ck.latest_resumable(tmp_path, max_epochs_done=2)
    assert path == ck.full_state_path(tmp_path, 1) and state["epochs_done"] == 1
    assert skipped and skipped[0]["reason"] == "CHECKSUM_MISMATCH"


def test_missing_sidecar_is_skipped(tmp_path: Path) -> None:
    ck.save_full_state(ck.full_state_path(tmp_path, 1), make_state(1))
    ck.save_full_state(ck.full_state_path(tmp_path, 2), make_state(2))
    os.unlink(ck.sidecar_path(ck.full_state_path(tmp_path, 2)))
    state, path, skipped = ck.latest_resumable(tmp_path, max_epochs_done=5)
    assert state["epochs_done"] == 1 and skipped[0]["reason"] == "SIDECAR_MISSING"


def test_payload_deleted_sidecar_kept_is_file_missing(tmp_path: Path) -> None:
    """S5-F1: a sidecar whose payload was deleted is a skipped FILE_MISSING entry, never an empty (scratch) dir."""
    ck.save_full_state(ck.full_state_path(tmp_path, 2), make_state(2), rung_end=True)
    os.unlink(ck.full_state_path(tmp_path, 2))
    state, path, skipped = ck.latest_resumable(tmp_path, max_epochs_done=4)
    assert state is None and path is None
    assert [(Path(s["path"]).name, s["reason"]) for s in skipped] == [("full_e0002.pt", "FILE_MISSING")]


def test_payload_deleted_falls_back_to_older_verified_state(tmp_path: Path) -> None:
    for e in (1, 2):
        ck.save_full_state(ck.full_state_path(tmp_path, e), make_state(e), rung_end=True)
    os.unlink(ck.full_state_path(tmp_path, 2))
    state, path, skipped = ck.latest_resumable(tmp_path, max_epochs_done=4)
    assert state["epochs_done"] == 1 and path == ck.full_state_path(tmp_path, 1)
    assert skipped[0]["reason"] == "FILE_MISSING"


@pytest.mark.parametrize("key", ["optimizer", "rng", "model_fp32", "loader_generator_state"])
def test_stripped_state_blocks(tmp_path: Path, key: str) -> None:
    state = make_state(2, loader_generator_state=torch.Generator().get_state())
    state.pop(key)
    path = ck.full_state_path(tmp_path, 2)
    ck.save_full_state(path, state)
    with pytest.raises(ResumeBlocked) as err:
        ck.latest_resumable(tmp_path, max_epochs_done=2,
                            required=("model_fp32", "optimizer", "loader_generator_state"))
    assert err.value.reason == f"MISSING_STATE:{key}"


def test_hash_mismatch_blocks(tmp_path: Path) -> None:
    ck.save_full_state(ck.full_state_path(tmp_path, 2), make_state(2))
    with pytest.raises(ResumeBlocked) as err:
        ck.latest_resumable(tmp_path, max_epochs_done=2, expect_hashes={**HASHES, "architecture": "sha256:other"})
    assert err.value.reason == "HASH_MISMATCH:architecture"


def test_foreign_file_is_format_unknown(tmp_path: Path) -> None:
    path = tmp_path / "full_e0001.pt"
    torch.save({"epoch": 0, "model": None, "ema": None}, path)
    ck.atomic_write_json(ck.sidecar_path(path), {"sha256": ck.sha256_file(path)})
    with pytest.raises(ResumeBlocked) as err:
        ck.load_full_state(path)
    assert err.value.reason == "FORMAT_UNKNOWN"


def test_prune_keeps_newest_and_rung_ends(tmp_path: Path) -> None:
    for e, rung_end in ((1, False), (2, True), (3, False), (4, False)):
        ck.save_full_state(ck.full_state_path(tmp_path, e), make_state(e), rung_end=rung_end)
    removed = ck.prune(tmp_path)
    assert sorted(Path(p).name for p in removed) == ["full_e0001.pt", "full_e0003.pt"]
    assert [e for e, _ in ck.list_full_states(tmp_path)] == [2, 4]
    ck.mark_rung_end(ck.full_state_path(tmp_path, 4))
    assert ck.read_sidecar(ck.full_state_path(tmp_path, 4))["rung_end"] is True


def test_rng_roundtrip() -> None:
    random.seed(1)
    np.random.seed(2)
    torch.manual_seed(3)
    saved = ck.capture_rng(include_cuda=False)  # CPU streams; the CUDA streams are covered by the gpu test below
    a = (random.random(), float(np.random.rand()), float(torch.rand(1)))
    random.seed(9)
    np.random.seed(9)
    torch.manual_seed(9)
    ck.restore_rng(saved)
    assert (random.random(), float(np.random.rand()), float(torch.rand(1))) == a
    assert all(ck.rng_equal(saved, saved).values())


def test_cpu_only_capture_does_not_touch_cuda() -> None:
    """The CPU-only capture used by these unit tests never initialises CUDA (runnable standalone, no GPU lock)."""
    before = torch.cuda.is_initialized()
    rng = ck.capture_rng(include_cuda=False)
    assert rng["torch_cuda"] == [] and torch.cuda.is_initialized() == before
    assert set(rng) == {"python", "numpy", "torch_cpu", "torch_cuda"}


@pytest.mark.gpu
@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA GPU required")
def test_rng_roundtrip_includes_cuda() -> None:
    torch.cuda.manual_seed_all(5)
    saved = ck.capture_rng()
    assert len(saved["torch_cuda"]) == torch.cuda.device_count()
    a = float(torch.rand(1, device="cuda"))
    torch.cuda.manual_seed_all(9)
    ck.restore_rng(saved)
    assert float(torch.rand(1, device="cuda")) == a
    assert all(ck.rng_equal(saved, saved).values())


def test_clone_preserves_channels_last() -> None:
    t = torch.randn(2, 3, 4, 5).to(memory_format=torch.channels_last)
    c = ck.to_cpu_clone({"t": t})["t"]
    assert c.is_contiguous(memory_format=torch.channels_last) and torch.equal(c, t)


def test_eval_snapshot_and_fp16_weights(tmp_path: Path) -> None:
    sd = torch.nn.Linear(4, 4).state_dict()
    side = ck.save_eval_snapshot(ck.eval_snapshot_path(tmp_path, 3), sd, model_meta={"a": np.float32(1.5)},
                                 epochs_done=3, hashes=HASHES)
    snap = ck.load_eval_snapshot(ck.eval_snapshot_path(tmp_path, 3))
    assert snap["dtype"] == "float32" and snap["model_meta"]["a"] == 1.5 and side["epochs_done"] == 3
    w = ck.write_weights_fp16(sd, ck.weights_only_path(tmp_path, 3))
    loaded = torch.load(w["path"], weights_only=True)
    assert loaded["weight"].dtype == torch.float16 and w["size_mb"] == Path(w["path"]).stat().st_size / 1e6
