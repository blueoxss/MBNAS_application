"""scripts/resume_equivalence.py helpers (CPU): RF-DETR states keep their loader generator for the comparison (S5-F9)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from conftest_common import owner_tool  # DD-A43(b)

torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "runner" / "resume_equivalence.py"  # contract §11


def _script():
    spec = importlib.util.spec_from_file_location("resume_equivalence", owner_tool("scripts/runner/resume_equivalence.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _rfdetr_state(tmp_path: Path, name: str, seed: int) -> Path:
    native = tmp_path / f"native_{name}.ckpt"
    torch.save({"state_dict": {"w": torch.ones(2)}, "epoch": 1, "global_step": 4,
                "callbacks": {"RFDETREMACallback": {"average_model_state_dict": {"w": torch.ones(2)},
                                                    "latest_update_step": 4}},
                "lr_schedulers": [{"last_epoch": 4, "_step_count": 5}],
                "optimizer_states": [{"state": {0: {"exp_avg": torch.zeros(2)}}, "param_groups": [{}]}]}, native)
    gen = torch.Generator().manual_seed(seed)
    state = {"native_resume_ckpt": {"path": str(native)}, "loader_generator_state": gen.get_state(),
             "epochs_done": 1, "global_step": 4, "samples_seen": 32, "lr_history": [{"epoch": 0, "lr": [0.1]}],
             "rng": {}, "hashes": {}}
    path = tmp_path / f"full_{name}.pt"
    torch.save(state, path)
    return path


def test_rfdetr_load_keeps_the_loader_generator_state(tmp_path: Path) -> None:
    mod = _script()
    a = mod._load(_rfdetr_state(tmp_path, "a", 1))
    b = mod._load(_rfdetr_state(tmp_path, "b", 1))
    c = mod._load(_rfdetr_state(tmp_path, "c", 2))
    assert isinstance(a["loader_generator_state"], torch.Tensor)
    assert mod.compare(a, b)["loader_generator_equal"] is True
    assert mod.compare(a, c)["loader_generator_equal"] is False
