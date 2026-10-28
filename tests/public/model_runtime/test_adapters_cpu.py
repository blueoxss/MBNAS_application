"""Adapter declarations and CPU builds (no GPU): ultralytics, ETRI (verbatim notebook extraction), RF-DETR."""
from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest
torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

from mbnas_model_runtime.adapters.base import ArchSpec, CapabilityError, TrainContext, resolve_register_hook
from mbnas_model_runtime.adapters.etri_hybrid import NOTEBOOK_BLOB_SHA1
from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter, lr_factor
from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

_NOTEBOOK_REL = "notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb"
# WP-1 (A8): the ETRI reference checkout is host data under $MBNAS_TEST_DATA_ROOT/etri_reference (MBNAS_TEST_ETRI_ROOT and the
# old host-path fallback are gone, contract §9); the verbatim check is host_data
NOTEBOOK = Path(os.environ.get("MBNAS_TEST_DATA_ROOT", "runtime/test_root/data")) / "etri_reference" / _NOTEBOOK_REL


def _notebook_present() -> bool:
    try:
        return NOTEBOOK.is_file()
    except OSError:  # e.g. PermissionError on the unreadable /root fallback: treat as absent -> skip, not collection error
        return False

ETRI_DIR = Path(__file__).resolve().parents[3] / "mbnas_model_runtime" / "src" / "mbnas_model_runtime" / "adapters" / "etri_hybrid"
EXTRACTED = {"boxes.py": 8, "model.py": 10, "loss.py": 12, "data.py": 6, "postprocess.py": 14}


def test_ultralytics_cpu_build_matches_audit_param_counts() -> None:
    a = UltralyticsAdapter("YOLO11")
    rep = a.build(ArchSpec("yolo11n", "YOLO11", "ultralytics", "yolo11n.yaml"), 80, "scratch", 0)
    assert rep.params == 2_624_080 and rep.architecture_hash.startswith("sha256:")  # STAGE_TRAINER_AUDIT C
    same = a.build(ArchSpec("yolo11n", "YOLO11", "ultralytics", "yolo11n.yaml"), 80, "scratch", 1)
    assert same.architecture_hash == rep.architecture_hash  # structure, not weights
    other = a.build(ArchSpec("yolo11s", "YOLO11", "ultralytics", "yolo11s.yaml"), 80, "scratch", 0)
    assert other.architecture_hash != rep.architecture_hash
    with pytest.raises(CapabilityError):
        a.build(ArchSpec("yolo11n", "YOLO11", "ultralytics", "yolo11n.yaml"), 1, "imagenet", 0)
    assert UltralyticsAdapter("YOLO26").capability()["inference_head"] == "e2e_one2one_nms_false"
    assert UltralyticsAdapter("Hybrid", "rtdetr").capability()["full_state_resume"] == "EXACT_COUNTERS_TOLERANCE_WEIGHTS"


def test_ultralytics_overrides_enforce_recipe(tmp_path: Path) -> None:
    from mbnas_model_runtime.adapters.base import DataSpec

    class M:
        split_hash = "sha256:x"

    recipe = {"batch": 8, "gradient_accumulation": 1, "beta2": 0.999, "input_shape": [640, 640], "workers": 0,
              "optimizer": "AdamW", "lr0": 1e-3, "lrf": 0.01, "momentum": 0.9, "weight_decay": 5e-4,
              "warmup_epochs": 0, "cos_lr": True, "close_mosaic": 0, "amp": True}
    ctx = TrainContext("c", tmp_path, ArchSpec("y", "YOLO11", "ultralytics", "yolo11n.yaml"),
                       DataSpec({0: "a"}, M(), M()), recipe, "scratch", 101, 2, 6, "sha256:p")
    ov = UltralyticsAdapter("YOLO11").overrides(ctx, tmp_path / "data.yaml", "s")
    assert ov["epochs"] == 6 and ov["nbs"] == ov["batch"] == 8 and ov["deterministic"] is True and ov["seed"] == 101
    assert ov["val"] is True and ov["plots"] is False and ov["save_period"] == -1 and ov["patience"] == 0
    for bad in ({"gradient_accumulation": 2}, {"beta2": 0.99}):
        ctx.recipe = {**recipe, **bad}
        with pytest.raises(CapabilityError):
            UltralyticsAdapter("YOLO11").overrides(ctx, tmp_path / "data.yaml", "s")


def test_register_hook_allowlist() -> None:
    assert resolve_register_hook(None) is None
    with pytest.raises(CapabilityError):
        resolve_register_hook("os:system")
    # WP-1 contract §6.7: exact allowlist; the legacy etri_bridge name maps to the same vendored hook (C-cross-2)
    from mbnas_model_runtime import operators
    from mbnas_model_runtime.adapters.base import REGISTER_HOOKS

    current = "mbnas_model_runtime.operators:ensure_registered"
    (legacy,) = set(REGISTER_HOOKS) - {current}  # DD-A26 data literal of old runs (not repeated here as a module ref)
    assert legacy.endswith(".search.etri_bridge:ensure_mixed_registered")
    assert resolve_register_hook(current) is operators.ensure_registered
    assert resolve_register_hook(legacy) is operators.ensure_registered
    for refused in ("mbnas_contracts.util.time:utcnow_iso", "mbnas_model_runtime.operators:probe"):
        with pytest.raises(CapabilityError, match="REGISTER_HOOK_REFUSED"):
            resolve_register_hook(refused)


def test_etri_modules_carry_provenance() -> None:
    for name, cell in EXTRACTED.items():
        text = (ETRI_DIR / name).read_text()
        assert NOTEBOOK_BLOB_SHA1 in text and f"cell indices [{cell}]" in text


@pytest.mark.host_data
@pytest.mark.skipif(not _notebook_present(), reason="ETRI notebook not present")
def test_etri_extraction_is_verbatim() -> None:
    """Every top-level definition in the extracted modules is AST-identical to the notebook's (read-only)."""
    blob = subprocess.run(["git", "hash-object", str(NOTEBOOK)], capture_output=True, text=True).stdout.strip()
    assert blob == NOTEBOOK_BLOB_SHA1
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    for name, idx in EXTRACTED.items():
        nb = {getattr(n, "name", None) or getattr(getattr(n, "targets", [None])[0], "id", None): ast.dump(n)
              for n in ast.parse("".join(cells[idx]["source"])).body}
        mod = ast.parse((ETRI_DIR / name).read_text())
        defs = [n for n in mod.body if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Assign))]
        assert defs
        for node in defs:
            key = getattr(node, "name", None) or node.targets[0].id
            assert ast.dump(node) == nb[key], f"{name}:{key} differs from notebook cell {idx}"


def test_etri_build_audit_and_deterministic_criterion() -> None:
    a = EtriHybridAdapter()
    rep = a.build(ArchSpec("etri", "Hybrid", "etri_hybrid", {}), 1, "scratch", 0)
    assert rep.module_counts["conv_modules"] > 0 and rep.module_counts["attention_modules"] > 0  # CNN + Transformer
    with pytest.raises(CapabilityError):
        a.build(ArchSpec("etri", "Hybrid", "etri_hybrid", {}), 1, "pretrained_native", 0)
    from mbnas_model_runtime.adapters.etri_hybrid.criterion import MbnasSetCriterion
    from mbnas_model_runtime.adapters.etri_hybrid.loss import SetCriterion

    torch.manual_seed(0)
    out = {"pred_logits": torch.randn(2, 16, 2), "pred_boxes": torch.rand(2, 16, 4) * 0.5 + 0.25}
    tg = [{"labels": torch.tensor([0, 0]), "boxes": torch.tensor([[0.5, 0.5, 0.2, 0.2], [0.3, 0.3, 0.1, 0.1]])},
          {"labels": torch.zeros(0, dtype=torch.long), "boxes": torch.zeros(0, 4)}]
    ref, new = SetCriterion(1)(out, tg), MbnasSetCriterion(1)(out, tg)
    assert all(torch.allclose(ref[k], new[k], atol=1e-6) for k in ref)


def test_etri_lr_schedule_matches_notebook_cosine() -> None:
    horizon, lrf = 6, 0.01
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(torch.optim.SGD([torch.zeros(1, requires_grad=True)], lr=1.0),
                                                       T_max=horizon, eta_min=lrf)
    ref = []
    for _ in range(horizon):
        ref.append(sched.get_last_lr()[0])
        sched.optimizer.step()
        sched.step()
    assert [pytest.approx(lr_factor(e, horizon, 0, lrf)) for e in range(horizon)] == ref
    assert lr_factor(0, 6, 2, lrf) == pytest.approx(1 / 3) and lr_factor(2, 6, 2, lrf) == 1.0


def test_rfdetr_declarations() -> None:
    from mbnas_model_runtime.adapters.rfdetr_adapter import RfdetrAdapter

    cap = RfdetrAdapter("RFDETRNano").capability()
    assert cap["full_state_resume"] == "PARTIAL" and "NOT restored by PTL" in cap["full_state_resume_detail"]
    assert "loader_generator_state" in cap["full_state_resume_detail"]  # S5-F4: sampler order continued by MBNAS
    assert cap["init_modes"] == ["scratch", "pretrained_native"]
    with pytest.raises(ValueError):
        RfdetrAdapter("RFDETRHuge")
