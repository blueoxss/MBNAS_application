"""Generic fixed-horizon stage orchestration around ``adapter.train_until`` (PLAN §2 ``training/stage_trainer.py``).

``StageTrainer.run_until(candidate_id, cumulative_target, fixed_horizon, protocol_hash, *, prior_epochs,
expected_resume)`` (the stage is explicit: both keywords have no default, so a caller cannot silently run a later
stage with first-stage semantics):

1. looks for the newest verified full-state checkpoint of the candidate with ``epochs_done <= cumulative_target``
   (corrupt/sidecar-less files are skipped and reported, falling back to the next older verified file; a stripped
   file, a protocol/dataset hash mismatch, or checkpoints that exist but none verifies raise :class:`ResumeBlocked`
   — never a weights-only fallback and never a silent restart from scratch);
   An *absent* state is blocked too: a sidecar whose payload was deleted is a skipped ``FILE_MISSING`` entry, and when
   the caller says a state must exist — ``expected_resume`` (the orchestrator's latest non-PRUNED ``training_full``
   row) or ``prior_epochs > 0`` (the job's stage is not the candidate's first stage) — finding none (or one below
   ``prior_epochs``) raises ``ResumeBlocked("MISSING_STATE:checkpoint_file")``: a scratch start is a new trial,
   never an implicit continuation. When the resumed file IS the recorded row (same realpath) and the row carries a
   ``sha256``, it must equal the file's sidecar sha256 (the DB record is the cross-check of §7 "atomic write ·
   checksum · DB commit"): ``recorded_checksum_policy="block"`` (default) raises
   ``ResumeBlocked("CHECKSUM_MISMATCH:recorded")``, ``"note"`` resumes and reports it as a result note plus a
   ``{"type": "log"}`` progress event. A different file than the recorded one is noted as resumed from an
   ``older``/``newer`` verified state (newer: the DB row lags behind a written file, e.g. a checkpoint message lost on
   SIGKILL);
2. if that checkpoint already reached the target, its architecture/recipe hashes match the inputs bound to this
   candidate (``ckpt/input_binding.json``, see :meth:`StageTrainer._check_binding`) and its rung-end evaluation
   snapshot verifies, returns the recorded result without training (idempotent re-delivery of the same job);
3. otherwise calls ``adapter.train_until`` with a fresh trainer whose LR schedule spans ``fixed_horizon`` epochs; the
   adapter verifies architecture/recipe hashes after building the module and overlays the state. The binding gets the
   adapter's architecture/recipe hashes at the FIRST checkpoint event of the stage (the adapter has verified or
   produced them by then) and again when ``train_until`` returns, so a rung end on disk always has bound hashes; a
   binding without hashes on the idempotent path is ``HASH_UNVERIFIED`` (blocked, never returned unverified).

Every checkpoint written is reported through ``progress`` as ``{"type": "checkpoint", kind, epoch, path, sha256,
bytes, hashes, rung}`` (the worker turns these into ``checkpoints`` rows), every finished epoch as ``{"type":
"epoch", ...}``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from mbnas_model_runtime.adapters.base import ArchSpec, DataSpec, MbnasAdapter, ResumeBlocked, RungResult, TrainContext
from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_model_runtime import checkpoint as ck

BINDING_FILE = "input_binding.json"
BINDING_FORMAT = "mbnas.input_binding.v1"


@dataclass
class StageTrainer:
    adapter: MbnasAdapter
    candidate_dir: Path
    arch: ArchSpec
    data: DataSpec
    recipe: dict[str, Any]
    init: str
    training_seed: int
    device: str = "cuda:0"
    progress: Callable[[dict[str, Any]], None] | None = None
    # what a recorded resume row whose sha256 differs from the resumed file's sidecar does: "block" | "note"
    recorded_checksum_policy: str = "block"
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def ckpt_dir(self) -> Path:
        return Path(self.candidate_dir) / "ckpt"

    def find_resume(self, cumulative_target: int, protocol_hash: str) -> tuple[dict[str, Any] | None, Path | None]:
        """Newest verified full state ``<= cumulative_target`` (protocol + dataset hashes enforced here)."""
        expect = {"protocol": protocol_hash, **self.data.hashes}
        state, path, skipped = ck.latest_resumable(self.ckpt_dir, max_epochs_done=cumulative_target,
                                                   required=self.adapter.required_state_keys(), expect_hashes=expect)
        self.skipped = skipped
        if state is None and skipped:
            raise ResumeBlocked("NO_VALID_CHECKPOINT", "; ".join(f"{s['path']}: {s['reason']}" for s in skipped))
        return state, path

    # -- input binding (architecture/recipe hash verification without building the module) --------------------------
    def input_fingerprints(self, fixed_horizon: int) -> dict[str, str]:
        """Fingerprints of what determines the adapter's architecture and recipe hashes."""
        arch = {"arch": self.arch.as_dict(), "names": {str(k): v for k, v in sorted(self.data.names.items())},
                "backend": getattr(self.adapter, "backend", None)}
        recipe = {"recipe": self.recipe, "init": self.init, "training_seed": int(self.training_seed),
                  "fixed_horizon": int(fixed_horizon)}
        return {"architecture": canonical_sha256(json.loads(json.dumps(arch, default=str))),
                "recipe": canonical_sha256(json.loads(json.dumps(recipe, default=str)))}

    @property
    def binding_path(self) -> Path:
        return self.ckpt_dir / BINDING_FILE

    def _read_binding(self) -> dict[str, Any] | None:
        try:
            doc = json.loads(self.binding_path.read_text())
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) and doc.get("format") == BINDING_FORMAT else None

    def _write_binding(self, fps: Mapping[str, str], hashes: Mapping[str, str] | None) -> None:
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.binding_path, {
            "format": BINDING_FORMAT, "inputs": dict(fps),
            "hashes": None if hashes is None else {k: hashes.get(k) for k in ("architecture", "recipe")}})

    def _check_binding(self, state: Mapping[str, Any], fixed_horizon: int) -> list[str]:
        """Raise ``HASH_MISMATCH:architecture|recipe`` unless the stored state was produced from the current inputs.

        The binding records the input fingerprints of the last ``train_until`` of this candidate and, from its first
        checkpoint event on, the architecture/recipe hashes the adapter computed from the built module. A changed
        input or a stored hash that differs from the bound one blocks; no binding at all, or a binding without hashes,
        cannot be verified and blocks too (``HASH_UNVERIFIED``).
        """
        fps = self.input_fingerprints(fixed_horizon)
        binding = self._read_binding()
        if binding is None:
            raise ResumeBlocked("HASH_UNVERIFIED", f"no {BINDING_FILE} in {self.ckpt_dir}: architecture/recipe hashes "
                                "of the recorded rung end cannot be verified, so it is not returned")
        notes = []
        for name in ("architecture", "recipe"):
            if binding.get("inputs", {}).get(name) != fps[name]:
                raise ResumeBlocked(f"HASH_MISMATCH:{name}", f"{name} inputs of this job differ from the ones that "
                                    "produced the recorded checkpoint")
            bound = (binding.get("hashes") or {}).get(name)
            have = (state.get("hashes") or {}).get(name)
            if bound is None:
                raise ResumeBlocked("HASH_UNVERIFIED", f"{BINDING_FILE} in {self.ckpt_dir} has no bound {name} hash: "
                                    "the recorded rung end cannot be verified, so it is not returned")
            if have != bound:
                raise ResumeBlocked(f"HASH_MISMATCH:{name}", f"checkpoint {have} != bound {bound}")
        return notes

    def _check_recorded(self, path: Path | None, state: Mapping[str, Any] | None,
                        expected_resume: Mapping[str, Any] | None) -> list[str]:
        """Cross-check the orchestrator's recorded resume row against the file actually resumed from."""
        if not expected_resume or path is None or state is None:
            return []
        if os.path.realpath(str(expected_resume.get("path"))) != os.path.realpath(path):
            exp_epoch = expected_resume.get("epoch")
            found = int(state["epochs_done"])
            rel = ("newer" if found > int(exp_epoch) else "older" if found < int(exp_epoch) else "different") \
                if isinstance(exp_epoch, int) else "different"
            return [f"expected resume source {expected_resume.get('path')} (epoch {exp_epoch}) not used; resumed from "
                    f"{rel} verified {path} (epoch {found})"]
        recorded = expected_resume.get("sha256")
        if not recorded:
            return []
        actual = ck.read_sidecar(path).get("sha256")
        if recorded == actual:
            return []
        msg = (f"recorded sha256 {recorded} != file sidecar {actual} for {path} (checkpoint row "
               f"{expected_resume.get('id')})")
        if self.recorded_checksum_policy == "block":
            raise ResumeBlocked("CHECKSUM_MISMATCH:recorded", msg)
        if self.progress is not None:
            self.progress({"type": "log", "level": "warning", "message": f"CHECKSUM_MISMATCH:recorded: {msg}",
                           "path": str(path), "recorded_sha256": recorded, "file_sha256": actual})
        return [msg]

    def run_until(self, candidate_id: str, cumulative_target: int, fixed_horizon: int, protocol_hash: str, *,
                  prior_epochs: int, expected_resume: Mapping[str, Any] | None, rung: str | None = None,
                  stop_requested: Callable[[], bool] | None = None) -> RungResult:
        """Run (or return) the stage ``prior_epochs -> cumulative_target``.

        ``prior_epochs`` is the number of epochs completed before this stage (0 only for the candidate's first
        stage) and ``expected_resume`` the orchestrator's recorded resume row (``None`` when nothing is recorded);
        both are required keywords so the stage is always explicit.
        """
        if self.recorded_checksum_policy not in ("block", "note"):
            raise ValueError(f"recorded_checksum_policy must be 'block' or 'note', got {self.recorded_checksum_policy!r}")
        if not 1 <= cumulative_target <= fixed_horizon:
            raise ValueError(f"need 1 <= cumulative_target ({cumulative_target}) <= fixed_horizon ({fixed_horizon})")
        if not 0 <= prior_epochs < cumulative_target:
            raise ValueError(f"need 0 <= prior_epochs ({prior_epochs}) < cumulative_target ({cumulative_target})")
        state, path = self.find_resume(cumulative_target, protocol_hash)
        found = int(state["epochs_done"]) if state is not None else 0
        if state is None and expected_resume:
            raise ResumeBlocked("MISSING_STATE:checkpoint_file", f"recorded resume source {expected_resume.get('path')} "
                                f"(epoch {expected_resume.get('epoch')}) not found in {self.ckpt_dir}; a scratch "
                                "restart would be a new trial")
        if found < prior_epochs:
            raise ResumeBlocked("MISSING_STATE:checkpoint_file", f"stage starts after {prior_epochs} completed epochs "
                                f"but the newest verified full state in {self.ckpt_dir} has {found}; a scratch/earlier "
                                "restart would be a new trial")
        recorded_notes = self._check_recorded(path, state, expected_resume)
        if state is not None and found == cumulative_target:
            notes = self._check_binding(state, fixed_horizon) + recorded_notes
            result = self._already_done(candidate_id, state, path, fixed_horizon)  # type: ignore[arg-type]
            result.notes.extend(notes)
            return result
        fps = self.input_fingerprints(fixed_horizon)
        if self._read_binding() is None:  # bound before training (crash after the rung-end write keeps the inputs);
            self._write_binding(fps, None)  # a differing existing binding is replaced only after the adapter verified
        ctx = TrainContext(candidate_id=candidate_id, candidate_dir=Path(self.candidate_dir), arch=self.arch,
                           data=self.data, recipe=self.recipe, init=self.init, training_seed=self.training_seed,
                           cumulative_target=cumulative_target, fixed_horizon=fixed_horizon,
                           protocol_hash=protocol_hash, device=self.device, rung=rung, resume_state=state,
                           resume_path=str(path) if path else None, stop_requested=stop_requested,
                           progress=self._binding_progress(fps))
        result = self.adapter.train_until(ctx)
        self._write_binding(fps, result.hashes)  # the adapter verified (resume) or produced these hashes
        result.notes.extend(f"skipped checkpoint {s['path']}: {s['reason']}" for s in self.skipped)
        result.notes.extend(recorded_notes)
        return result

    def _binding_progress(self, fps: Mapping[str, str]) -> Callable[[dict[str, Any]], None]:
        """Wrap ``progress``: the first checkpoint event that reports hashes binds them before it is forwarded."""
        bound = False

        def progress(event: dict[str, Any]) -> None:
            nonlocal bound
            if not bound and event.get("type") == "checkpoint" and isinstance(event.get("hashes"), Mapping) and all(
                    event["hashes"].get(k) for k in ("architecture", "recipe")):
                self._write_binding(fps, event["hashes"])
                bound = True
            if self.progress is not None:
                self.progress(event)

        return progress

    def _already_done(self, candidate_id: str, state: dict[str, Any], path: Path, fixed_horizon: int) -> RungResult:
        done = int(state["epochs_done"])
        eval_path = ck.eval_snapshot_path(self.ckpt_dir, done)
        weights = ck.weights_only_path(self.ckpt_dir, done)
        eval_side = ck.verify_file(eval_path)
        if not weights.is_file():
            raise ResumeBlocked("RUNG_END_ARTIFACT_MISSING", str(weights))
        from mbnas_contracts.util.atomic import sha256_file

        side = ck.read_sidecar(path)
        return RungResult(
            candidate_id=candidate_id, epochs_done=done, cumulative_target=done, fixed_horizon=fixed_horizon,
            stopped_reason="TARGET_REACHED", rung_end_training_ckpt=str(path), rung_end_ema_eval_ckpt=str(eval_path),
            weights_only_path=str(weights), size_mb=weights.stat().st_size / 1e6, size_dtype="float16",
            lr_history=list(state["lr_history"]), step_count=int(state["global_step"]),
            samples_seen=int(state["samples_seen"]),
            checkpoint_sha256={"training_full": side["sha256"], "eval_ema": eval_side["sha256"],
                               "weights_only": sha256_file(weights)},
            resumed_from=str(path), resolved_recipe={}, hashes=dict(state["hashes"]),
            notes=["rung already complete: returned recorded checkpoints without training"])
