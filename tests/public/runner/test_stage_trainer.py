"""StageTrainer orchestration (fake adapter) and a real CPU resume-equivalence check on the ETRI adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
torch = __import__("pytest").importorskip("torch")  # 2026-10-15: skip the module where torch (ml extras) is absent

from mbnas_model_runtime.adapters.base import ArchSpec, DataSpec, ResumeBlocked, RungResult, TrainContext
from mbnas_model_runtime.datasets.manifest import build_manifest
from mbnas_model_runtime import checkpoint as ck
from mbnas_model_runtime.datasets.yolo_view import build_yolo_view
from mbnas_runner.training.stage_trainer import StageTrainer


class _M:
    def __init__(self, h: str) -> None:
        self.split_hash = h


class FakeAdapter:
    """Counts epochs; writes complete full states so StageTrainer's resume logic is exercised without a backend."""

    backend = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def required_state_keys(self) -> tuple[str, ...]:
        return ("model_fp32",)

    def train_until(self, ctx: TrainContext) -> RungResult:
        start = int(ctx.resume_state["epochs_done"]) if ctx.resume_state else 0
        self.calls.append((ctx.cumulative_target, start))
        hashes = {"architecture": "a", "recipe": "r", "protocol": ctx.protocol_hash, **ctx.data.hashes}
        d = ctx.candidate_dir / "ckpt"
        for e in range(start, ctx.cumulative_target):
            state = {"format": ck.FORMAT, "epoch": e, "epochs_done": e + 1, "global_step": e + 1,
                     "samples_seen": 8 * (e + 1), "rng": ck.capture_rng(include_cuda=False), "hashes": hashes,
                     "lr_history": [{"epoch": i, "lr": [0.1]} for i in range(e + 1)],
                     "model_fp32": {"w": torch.full((2,), float(e + 1))}}
            ck.save_full_state(ck.full_state_path(d, e + 1), state, rung_end=e + 1 == ctx.cumulative_target)
        done = ctx.cumulative_target
        ck.save_eval_snapshot(ck.eval_snapshot_path(d, done), {"w": torch.ones(2)}, model_meta={}, epochs_done=done,
                              hashes=hashes)
        ck.write_weights_fp16({"w": torch.ones(2)}, ck.weights_only_path(d, done))
        return RungResult(ctx.candidate_id, done, done, ctx.fixed_horizon, "TARGET_REACHED", None, None, None, None,
                          None, [], done, 8 * done, {}, ctx.resume_path, {}, hashes)


def _trainer(tmp_path: Path, adapter: Any) -> StageTrainer:
    data = DataSpec({0: "x"}, _M("sha256:train"), _M("sha256:val"))
    return StageTrainer(adapter=adapter, candidate_dir=tmp_path / "cand", arch=ArchSpec("m", "F", "fake", None),
                        data=data, recipe={}, init="scratch", training_seed=1, device="cpu")


def test_resumes_from_latest_and_is_idempotent(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=None)
    res = st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=None)  # re-delivered job: no training
    assert fake.calls == [(2, 0), (4, 2)]
    assert res.notes and "already complete" in res.notes[0] and res.epochs_done == 4


def test_protocol_change_blocks(tmp_path: Path) -> None:
    st = _trainer(tmp_path, FakeAdapter())
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 4, 6, "sha256:OTHER", prior_epochs=2, expected_resume=None)
    assert err.value.reason == "HASH_MISMATCH:protocol"


def test_all_corrupt_never_restarts_silently(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    for _, p in ck.list_full_states(tmp_path / "cand" / "ckpt"):
        p.write_bytes(b"garbage")
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=None)
    assert err.value.reason == "NO_VALID_CHECKPOINT" and fake.calls == [(2, 0)]


def test_target_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _trainer(tmp_path, FakeAdapter()).run_until("c1", 7, 6, "sha256:p", prior_epochs=0, expected_resume=None)


def _etri_data(make_dataset, tmp_path: Path) -> DataSpec:
    paths = make_dataset("etri", counts={"train": 6, "val": 2}, label="0 0.5 0.5 0.3 0.3\n0 0.2 0.2 0.1 0.1\n")
    train = build_manifest(paths["train_root"], names={0: "drone"})
    val = build_manifest(paths["search_val_root"], names={0: "drone"})
    view = build_yolo_view(tmp_path / "view", {"train": train, "val": val}, {0: "drone"})
    return DataSpec({0: "drone"}, train, val, view_root=view.parent)


@pytest.mark.slow
def test_etri_cpu_resume_is_bitwise(tmp_path: Path, make_dataset) -> None:
    """Real ETRI adapter on CPU: 1 -> 2 -> 3 staged equals continuous 3 bitwise (model, EMA, optimizer, RNG)."""
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter

    data = _etri_data(make_dataset, tmp_path)
    arch = ArchSpec("etri-controlled-cnn-transformer", "Hybrid", "etri_hybrid",
                    {"imgsz": 64, "patch": 16, "dim": 32, "depth": 1, "heads": 4})
    recipe = {"optimizer": "AdamW", "lr0": 1e-3, "lrf": 0.01, "weight_decay": 1e-4, "momentum": 0.9, "beta2": 0.999,
              "warmup_epochs": 1, "batch": 4, "gradient_accumulation": 1, "workers": 0, "amp": False,
              "input_shape": [64, 64], "ema": True}

    def trainer(name: str) -> StageTrainer:
        return StageTrainer(adapter=EtriHybridAdapter(), candidate_dir=tmp_path / name, arch=arch, data=data,
                            recipe=recipe, init="scratch", training_seed=7, device="cpu")

    a = trainer("A").run_until("A", 3, 3, "sha256:p", prior_epochs=0, expected_resume=None)
    b = trainer("B")
    b.run_until("B", 1, 3, "sha256:p", prior_epochs=0, expected_resume=None)
    b.run_until("B", 2, 3, "sha256:p", prior_epochs=1, expected_resume=None)
    rb = b.run_until("B", 3, 3, "sha256:p", prior_epochs=2, expected_resume=None)
    sa = ck.load_full_state(ck.full_state_path(tmp_path / "A" / "ckpt", 3))
    sb = ck.load_full_state(ck.full_state_path(tmp_path / "B" / "ckpt", 3))
    assert [h["lr"] for h in sa["lr_history"]] == [h["lr"] for h in sb["lr_history"]]
    assert (sa["global_step"], sa["samples_seen"], sa["ema_updates"]) == (sb["global_step"], sb["samples_seen"],
                                                                            sb["ema_updates"])
    for key in ("model_fp32", "ema_fp32"):
        assert all(torch.equal(sa[key][k], sb[key][k]) for k in sa[key])
    for pid, st in sa["optimizer"]["state"].items():
        assert all(torch.equal(v, sb["optimizer"]["state"][pid][k]) for k, v in st.items())
    assert all(ck.rng_equal(sa["rng"], sb["rng"]).values())
    assert rb.resumed_from.endswith("full_e0002.pt") and a.size_mb and a.size_mb > 0


# -- S5-F1: an absent checkpoint set for a later stage is RESUME_BLOCKED, never a silent scratch restart ----------
def test_deleted_ckpt_dir_blocks_a_later_stage(tmp_path: Path) -> None:
    import shutil

    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    shutil.rmtree(tmp_path / "cand" / "ckpt")
    with pytest.raises(ResumeBlocked) as err:  # the stage says 2 epochs already exist (L1 = 2 -> 4)
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=None)
    assert err.value.reason == "MISSING_STATE:checkpoint_file" and fake.calls == [(2, 0)]
    expected = {"path": str(tmp_path / "cand" / "ckpt" / "full_e0002.pt"), "epoch": 2}
    with pytest.raises(ResumeBlocked) as err:  # the orchestrator recorded a resume source
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=0, expected_resume=expected)
    assert err.value.reason == "MISSING_STATE:checkpoint_file" and "full_e0002.pt" in err.value.detail
    assert fake.calls == [(2, 0)] and not (tmp_path / "cand" / "ckpt").exists()


def test_payload_deleted_sidecar_kept_blocks_a_later_stage(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    for _, p in ck.list_full_states(tmp_path / "cand" / "ckpt"):
        p.unlink()  # payload gone, sidecar kept
    with pytest.raises(ResumeBlocked) as err:  # even without any orchestrator hint
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == "NO_VALID_CHECKPOINT" and "FILE_MISSING" in err.value.detail
    assert fake.calls == [(2, 0)]


def test_first_stage_scratch_start_is_allowed_and_fallback_is_noted(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)  # first stage: scratch is the trial
    st.run_until("c1", 3, 6, "sha256:p", prior_epochs=2, expected_resume=None)
    ck.full_state_path(tmp_path / "cand" / "ckpt", 3).unlink()  # newest recorded payload deleted, e2 still verifies
    res = st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2,
                       expected_resume={"path": str(ck.full_state_path(tmp_path / "cand" / "ckpt", 3)), "epoch": 3})
    assert fake.calls == [(2, 0), (3, 2), (4, 2)] and res.resumed_from.endswith("full_e0002.pt")
    assert any("FILE_MISSING" in n for n in res.notes) and any("expected resume source" in n for n in res.notes)


def test_prior_epochs_not_reached_by_found_state_blocks(tmp_path: Path) -> None:
    """A later stage never starts below its first epoch: the found state must cover the previous stages."""
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 1, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=None)
    assert err.value.reason == "MISSING_STATE:checkpoint_file" and fake.calls == [(1, 0)]


# -- S5-F3: the idempotent re-delivery path verifies architecture and recipe hashes --------------------------------
def _tamper_hash(tmp_path: Path, epochs_done: int, name: str) -> None:
    path = ck.full_state_path(tmp_path / "cand" / "ckpt", epochs_done)
    state = torch.load(path, weights_only=True)
    state["hashes"][name] = "sha256:tampered"
    ck.save_full_state(path, state, rung_end=True)  # sidecar re-signed: integrity checks alone cannot see it


@pytest.mark.parametrize("name", ["architecture", "recipe"])
def test_already_done_refuses_a_tampered_stored_hash(tmp_path: Path, name: str) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    _tamper_hash(tmp_path, 2, name)
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == f"HASH_MISMATCH:{name}" and fake.calls == [(2, 0)]


def test_already_done_refuses_changed_architecture_or_recipe(tmp_path: Path) -> None:
    from dataclasses import replace

    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    other_arch = replace(st, arch=ArchSpec("m", "F", "fake", {"depth": 2}))
    with pytest.raises(ResumeBlocked) as err:
        other_arch.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == "HASH_MISMATCH:architecture"
    other_recipe = replace(st, recipe={"lr0": 0.5})
    with pytest.raises(ResumeBlocked) as err:
        other_recipe.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == "HASH_MISMATCH:recipe"
    with pytest.raises(ResumeBlocked) as err:  # a different fixed horizon is a different recipe
        st.run_until("c1", 2, 8, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == "HASH_MISMATCH:recipe"
    res = st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0,
                       expected_resume=None)  # unchanged inputs: still idempotent
    assert "already complete" in res.notes[0] and fake.calls == [(2, 0)]


# -- S5-F6: the recorded resume row's sha256 is cross-checked against the file actually resumed from -----------------
def _row(tmp_path: Path, epochs_done: int) -> dict[str, Any]:
    p = ck.full_state_path(tmp_path / "cand" / "ckpt", epochs_done)
    return {"id": f"ck-e{epochs_done}", "path": str(p), "sha256": ck.read_sidecar(p)["sha256"], "epoch": epochs_done}


def _resign_swapped_weights(tmp_path: Path, epochs_done: int) -> None:
    path = ck.full_state_path(tmp_path / "cand" / "ckpt", epochs_done)
    state = torch.load(path, weights_only=True)
    state["model_fp32"]["w"] = torch.full((2,), -99.0)  # swapped weights, same hashes
    ck.save_full_state(path, state, rung_end=True)  # sidecar re-signed: only the DB row still knows the old sha


def test_recorded_sha256_mismatch_blocks_by_default(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    row = _row(tmp_path, 2)
    _resign_swapped_weights(tmp_path, 2)
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=row)
    assert err.value.reason == "CHECKSUM_MISMATCH:recorded" and row["sha256"] in err.value.detail
    with pytest.raises(ResumeBlocked) as err:  # the idempotent re-delivery path is cross-checked too
        st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=row)
    assert err.value.reason == "CHECKSUM_MISMATCH:recorded" and fake.calls == [(2, 0)]


def test_recorded_sha256_mismatch_note_mode_reports_and_logs(tmp_path: Path) -> None:
    from dataclasses import replace

    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    row = _row(tmp_path, 2)
    _resign_swapped_weights(tmp_path, 2)
    events: list[dict[str, Any]] = []
    noting = replace(st, recorded_checksum_policy="note", progress=events.append)
    res = noting.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=row)
    side = ck.read_sidecar(ck.full_state_path(tmp_path / "cand" / "ckpt", 2))["sha256"]
    assert fake.calls == [(2, 0), (4, 2)]
    assert any(f"recorded sha256 {row['sha256']} != file sidecar {side}" in n for n in res.notes)
    assert any(e.get("type") == "log" and "CHECKSUM_MISMATCH:recorded" in e.get("message", "") for e in events)
    with pytest.raises(ValueError):
        replace(st, recorded_checksum_policy="ignore").run_until("c1", 4, 6, "sha256:p", prior_epochs=2,
                                                                 expected_resume=row)


def test_matching_recorded_sha256_resumes_without_note(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    res = st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=_row(tmp_path, 2))
    assert fake.calls == [(2, 0), (4, 2)] and not any("sha256" in n or "expected resume" in n for n in res.notes)


def test_fallback_note_distinguishes_newer_from_older(tmp_path: Path) -> None:
    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    row2 = _row(tmp_path, 2)
    st.run_until("c1", 3, 6, "sha256:p", prior_epochs=2, expected_resume=row2)  # e3 written; DB row still e2
    res = st.run_until("c1", 4, 6, "sha256:p", prior_epochs=2, expected_resume=row2)
    assert res.resumed_from.endswith("full_e0003.pt")
    assert any("resumed from newer verified" in n and "full_e0003.pt" in n for n in res.notes)
    assert not any("older" in n for n in res.notes)


# -- S5-F7: adapter hashes are bound at the first checkpoint event; an unbound binding is HASH_UNVERIFIED -------------
class _CrashAfterFirstCheckpoint(FakeAdapter):
    """Emits the checkpoint event of its first full state, then dies (as a SIGKILL after the write would)."""

    def train_until(self, ctx: TrainContext) -> RungResult:
        start = int(ctx.resume_state["epochs_done"]) if ctx.resume_state else 0
        self.calls.append((ctx.cumulative_target, start))
        hashes = {"architecture": "a", "recipe": "r", "protocol": ctx.protocol_hash, **ctx.data.hashes}
        p = ck.full_state_path(ctx.candidate_dir / "ckpt", start + 1)
        state = {"format": ck.FORMAT, "epoch": start, "epochs_done": start + 1, "global_step": start + 1,
                 "samples_seen": 8, "rng": ck.capture_rng(include_cuda=False), "hashes": hashes,
                 "lr_history": [{"epoch": 0, "lr": [0.1]}], "model_fp32": {"w": torch.ones(2)}}
        side = ck.save_full_state(p, state, rung_end=True)
        ctx.progress({"type": "checkpoint", "kind": "training_full", "epoch": start + 1, "path": str(p),
                      "sha256": side["sha256"], "bytes": side["bytes"], "hashes": hashes, "rung": ctx.rung})
        raise RuntimeError("killed after the checkpoint write")


def test_hashes_are_bound_at_the_first_checkpoint_event(tmp_path: Path) -> None:
    import json

    seen: list[dict[str, Any]] = []
    st = _trainer(tmp_path, _CrashAfterFirstCheckpoint())
    st.progress = seen.append
    with pytest.raises(RuntimeError):
        st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    binding = json.loads((tmp_path / "cand" / "ckpt" / "input_binding.json").read_text())
    assert binding["hashes"] == {"architecture": "a", "recipe": "r"}
    assert [e["type"] for e in seen] == ["checkpoint"]  # the caller's progress still receives every event


def test_unbound_binding_on_idempotent_path_is_hash_unverified(tmp_path: Path) -> None:
    import json

    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    bpath = tmp_path / "cand" / "ckpt" / "input_binding.json"
    b = json.loads(bpath.read_text())
    b["hashes"] = None
    bpath.write_text(json.dumps(b))
    _tamper_hash(tmp_path, 2, "architecture")
    with pytest.raises(ResumeBlocked) as err:
        st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    assert err.value.reason == "HASH_UNVERIFIED" and fake.calls == [(2, 0)]


# -- S5-F8: the stage is explicit -------------------------------------------------------------------------------
def test_run_until_requires_explicit_stage_hints(tmp_path: Path) -> None:
    import shutil

    fake = FakeAdapter()
    st = _trainer(tmp_path, fake)
    with pytest.raises(TypeError):
        st.run_until("c1", 2, 6, "sha256:p")  # no hints at all: refused, never an implicit first stage
    with pytest.raises(TypeError):
        st.run_until("c1", 2, 6, "sha256:p", expected_resume=None)
    with pytest.raises(TypeError):
        st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0)
    st.run_until("c1", 2, 6, "sha256:p", prior_epochs=0, expected_resume=None)
    shutil.rmtree(tmp_path / "cand" / "ckpt")
    with pytest.raises(TypeError):  # the S5-F1 repro (later target, ckpt dir deleted, no hints) cannot be expressed
        st.run_until("c1", 4, 6, "sha256:p")
    assert fake.calls == [(2, 0)]
