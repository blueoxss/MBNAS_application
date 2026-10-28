"""MBNAS adapter for the ETRI controlled CNN+Transformer hybrid with its own deterministic training loop.

Loop (one ``train_until`` stage): ``SetCriterion-mbnas`` (:mod:`.criterion`), math-kernel attention during training,
AdamW (lr, weight decay, betas from the recipe), per-epoch ``LambdaLR`` whose factor
is a linear warmup followed by cosine decay to ``lrf`` over ``fixed_horizon`` epochs (with warmup 0 this equals the
notebook's ``CosineAnnealingLR(T_max=epochs, eta_min=lr*lrf)``), gradient clipping 1.0 (notebook), optional AMP
(GradScaler), optional EMA (recorded in the state), ``num_workers=0`` and a ``torch.Generator`` seeded with the
training seed for shuffling. A full state (model, EMA + updates, optimizer, scheduler, scaler, epoch, steps, samples,
python/numpy/torch/CUDA RNG, loader generator) is written after every epoch; resuming restores all of it, so a
2→4→6 staged run is expected to equal a continuous 0→6 run bitwise on the same device (verified by
``scripts/resume_equivalence.py``). Deterministic algorithms are enabled (``warn_only=True``) with
``CUBLAS_WORKSPACE_CONFIG=:4096:8``.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from mbnas_model_runtime import checkpoint as ck
from mbnas_model_runtime.adapters.base import ArchSpec, BuildReport, CapabilityError, RungResult, TrainContext, check_init
from mbnas_model_runtime.adapters.common import count_modules, emit, env_snapshot, recipe_hash, structural_hash
from mbnas_model_runtime.adapters.etri_hybrid import FAMILY_LABEL, NOTEBOOK_BLOB_SHA1

INIT_MODES = ("scratch",)
MODEL_DEFAULTS = {"family": "cnn_transformer", "imgsz": 384, "patch": 16, "dim": 128, "depth": 3, "heads": 4}
REQUIRED_STATE = ("model_fp32", "optimizer", "scheduler", "scaler", "loader_generator_state", "adapter_meta",
                  "adapter_meta.ema_enabled")
EVAL_DEFAULTS = {"conf": 0.001, "nms_iou": 0.5, "max_det": 300}
GRAD_CLIP = 1.0


class _Ema:
    """Exponential moving average of all floating state (ultralytics-style ramp ``decay*(1-exp(-u/tau))``)."""

    def __init__(self, model: Any, decay: float = 0.9999, tau: float = 2000.0) -> None:
        import copy

        self.ema = copy.deepcopy(model).eval()
        for p in self.ema.parameters():
            p.requires_grad_(False)
        self.decay, self.tau, self.updates = decay, tau, 0

    def update(self, model: Any) -> None:
        import torch

        self.updates += 1
        d = self.decay * (1 - math.exp(-self.updates / self.tau))
        with torch.no_grad():
            msd = model.state_dict()
            for k, v in self.ema.state_dict().items():
                if v.dtype.is_floating_point:
                    v.mul_(d).add_(msd[k].detach(), alpha=1 - d)


def _collate(batch: list[Any]) -> Any:
    import torch

    images, targets = zip(*batch)
    return torch.stack(images), list(targets)


def lr_factor(epoch: int, horizon: int, warmup: int, lrf: float) -> float:
    """Per-epoch LR multiplier: linear warmup ``(e+1)/(w+1)`` then cosine from 1 to ``lrf`` over the rest."""
    if epoch < warmup:
        return (epoch + 1) / (warmup + 1)
    span = max(horizon - warmup, 1)
    return lrf + (1 - lrf) * 0.5 * (1 + math.cos(math.pi * (epoch - warmup) / span))


class EtriHybridAdapter:
    backend = "etri_hybrid"
    family = "Hybrid"

    def capability(self) -> dict[str, Any]:
        return {
            "backend": self.backend, "family_label": FAMILY_LABEL, "train": True, "val": True,
            "full_state_resume": "EXACT_BITWISE_TARGET",
            "full_state_resume_detail": "own loop restores model, EMA+updates, optimizer, LambdaLR, GradScaler, epoch, "
                                        "steps, samples, python/numpy/torch/CUDA RNG and the shuffle generator",
            "init_modes": list(INIT_MODES), "native_loss": "SetCriterion (Hungarian; CE + 5*L1 + 2*GIoU, eos 0.05)",
            "precisions": ["FP32", "FP16"], "train_precisions": ["amp_fp16_fp32", "fp32"],
            "determinism_profile": "deterministic_algorithms_warn_only_workers0",
            "input_size_rule": {"kind": "fixed", "shape": [MODEL_DEFAULTS["imgsz"]] * 2},
            "export_onnx": True, "export_trt": "via benchmark module", "inference_head": "native",
            "source": f"ETRI_NAS notebook blob {NOTEBOOK_BLOB_SHA1} cells 6/8/10/12/14",
        }

    def required_state_keys(self) -> tuple[str, ...]:
        return REQUIRED_STATE

    def model_kwargs(self, arch: ArchSpec, num_classes: int) -> dict[str, Any]:
        cfg = dict(MODEL_DEFAULTS)
        if isinstance(arch.config, Mapping):
            cfg.update({k: v for k, v in arch.config.items() if k in MODEL_DEFAULTS})
        cfg["num_classes"] = int(num_classes)
        return cfg

    def _make(self, kwargs: Mapping[str, Any]) -> Any:
        from mbnas_model_runtime.adapters.etri_hybrid.model import audit_architecture, make_detector

        model = make_detector(**kwargs)
        audit_architecture(model, kwargs["family"])
        return model

    def build(self, arch: ArchSpec, num_classes: int, init: str, seed: int) -> BuildReport:
        import torch

        check_init(init, INIT_MODES, arch.model_id)
        torch.manual_seed(seed)
        kw = self.model_kwargs(arch, num_classes)
        model = self._make(kw)
        from mbnas_model_runtime.adapters.etri_hybrid.model import audit_architecture

        audit = audit_architecture(model, kw["family"])
        return BuildReport(arch.model_id, self.backend, int(audit["params"]), structural_hash(model), init,
                           count_modules(model), [f"audit {audit}", FAMILY_LABEL])

    # -- structure (mbnas-structure/2, native_modules) --------------------------------------------------------------
    def declared_config(self, arch: ArchSpec | None = None, num_classes: int = 1) -> dict[str, Any]:
        """Declared kwargs (``MODEL_DEFAULTS`` + ``arch.config`` + ``num_classes``): plain data, no torch import and no
        build (``GET /registry/{id}/structure`` calls this, so it must stay import-light; ``describe_structure`` is the
        observing path)."""
        return self.model_kwargs(arch or ArchSpec("etri", "Hybrid", self.backend, MODEL_DEFAULTS), num_classes)

    def describe_structure(self, arch: ArchSpec, num_classes: int, input_size: int | None = None) -> dict[str, Any]:
        """Explicit CPU structure observation (scratch init): build once, hook one zeros forward at ``imgsz``."""
        import torch

        from mbnas_model_runtime.adapters.structure_native import observe_native_structure, unavailable_document

        config = self.declared_config(arch, num_classes)
        try:
            torch.manual_seed(0)
            model = self._make(config)
        except Exception as exc:  # noqa: BLE001 - environment problems are UNVERIFIED data, never a crash
            return unavailable_document(self.backend, arch.model_id, config, exc, input_size=input_size)
        return observe_native_structure(self.backend, model, input_size=input_size, model_id=arch.model_id, config=config)

    # -- training -------------------------------------------------------------------------------------------------
    def _schedule(self, ctx: TrainContext, kw: Mapping[str, Any]) -> dict[str, Any]:
        r = ctx.recipe
        if int(r.get("gradient_accumulation", 1)) != 1:
            raise CapabilityError("ACCUMULATION_RESUME_UNSUPPORTED", "ETRI loop implements gradient_accumulation=1")
        if [int(v) for v in r["input_shape"]] != [kw["imgsz"], kw["imgsz"]]:
            raise CapabilityError("INPUT_SHAPE_UNSUPPORTED", f"ETRI hybrid is fixed at {kw['imgsz']}x{kw['imgsz']}")
        return {"epochs": int(ctx.fixed_horizon), "batch": int(r["batch"]), "lr0": float(r["lr0"]),
                "lrf": float(r["lrf"]), "weight_decay": float(r["weight_decay"]),
                "betas": [float(r["momentum"]), float(r.get("beta2", 0.999))],
                "warmup_epochs": int(round(float(r["warmup_epochs"]))), "amp": bool(r["amp"]),
                "ema": bool(r.get("ema", True)), "workers": 0, "grad_clip": GRAD_CLIP, "seed": int(ctx.training_seed),
                "model": dict(kw), "init": ctx.init, "augment": "hflip_p0.5 (notebook dataset)"}

    def train_until(self, ctx: TrainContext) -> RungResult:
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        import torch

        check_init(ctx.init, INIT_MODES, ctx.arch.model_id)
        if ctx.data.view_root is None:
            raise ValueError("ETRI training needs DataSpec.view_root")
        kw = self.model_kwargs(ctx.arch, len(ctx.data.names))
        sched = self._schedule(ctx, kw)
        t0 = time.time()
        torch.use_deterministic_algorithms(True, warn_only=True)
        try:
            stage = _Stage(self, ctx, kw, sched)
            stage.run()
        finally:
            torch.use_deterministic_algorithms(False)
        return stage.result(time.time() - t0)

    def _row(self, ctx: TrainContext, kind: str, epoch: int, path: Path, side: Mapping[str, Any],
             hashes: Mapping[str, str]) -> dict[str, Any]:
        row = {"kind": kind, "epoch": epoch, "path": str(path), "sha256": side["sha256"], "bytes": int(side["bytes"]),
               "hashes": dict(hashes), "rung": ctx.rung}
        emit(ctx.progress, {"type": "checkpoint", **row})
        return row

    def _rung_end(self, ctx: TrainContext, ckpt_dir: Path, epochs_done: int, state: Mapping[str, Any],
                  kw: Mapping[str, Any], hashes: Mapping[str, str], rows: list[dict[str, Any]]) -> dict[str, Any]:
        sd = state["ema_fp32"] if state["ema_fp32"] is not None else state["model_fp32"]
        meta = {"model_kwargs": dict(kw), "names": {str(k): v for k, v in ctx.data.names.items()},
                "weights": "ema" if state["ema_fp32"] is not None else "model", "family_label": FAMILY_LABEL}
        eval_path = ck.eval_snapshot_path(ckpt_dir, epochs_done)
        eside = ck.save_eval_snapshot(eval_path, sd, model_meta=meta, epochs_done=epochs_done, hashes=hashes)
        rows.append(self._row(ctx, "eval_ema", epochs_done, eval_path, eside, hashes))
        w = ck.write_weights_fp16(sd, ck.weights_only_path(ckpt_dir, epochs_done))
        rows.append(self._row(ctx, "weights_only", epochs_done, Path(w["path"]), w, hashes))
        return {"eval_path": str(eval_path), "weights_path": w["path"], "size_mb": w["size_mb"],
                "sha": {"eval_ema": eside["sha256"], "weights_only": w["sha256"]}}

    # -- evaluation -----------------------------------------------------------------------------------------------
    def load_eval_model(self, eval_ckpt: str | Path, device: str) -> Any:
        snap = ck.load_eval_snapshot(eval_ckpt)
        kw = snap["model_meta"]["model_kwargs"]
        model = self._make(kw)
        model.load_state_dict(snap["state_dict"], strict=True)
        model = model.to(device).eval()
        model.mbnas_meta = {**snap["model_meta"], "eval_ckpt": str(eval_ckpt)}
        return model

    def _dataset(self, model: Any, paths: list[str]) -> Any:
        from mbnas_model_runtime.adapters.etri_hybrid.data import YoloDetectionDataset

        kw = model.mbnas_meta["model_kwargs"]
        return YoloDetectionDataset(paths, kw["imgsz"], kw["num_classes"], augment=False)

    def _decode(self, outputs: Any, targets: list[Any], cfg: Mapping[str, Any]) -> list[Any]:
        from mbnas_model_runtime.adapters.etri_hybrid.postprocess import _original_boxes, postprocess

        res = postprocess(outputs, threshold=float(cfg["conf"]), nms_iou=float(cfg["nms_iou"]),
                          max_det=int(cfg["max_det"]))
        return [(_original_boxes(r["boxes"], t), r["scores"], r["labels"]) for r, t in zip(res, targets)]

    def predict(self, model: Any, images: list[tuple[str, str]], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
        import torch

        cfg = {**EVAL_DEFAULTS, **cfg}
        device = next(model.parameters()).device
        fp16 = cfg.get("precision", "FP16") == "FP16" and device.type == "cuda"
        ds = self._dataset(model, [p for _, p in images])
        out: list[dict[str, Any]] = []
        bs = int(cfg.get("batch", 8))
        for i in range(0, len(images), bs):
            items = [ds[j] for j in range(i, min(i + bs, len(images)))]
            x = torch.stack([t for t, _ in items]).to(device)
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                dec = self._decode(model(x), [tg for _, tg in items], cfg)
            for k, (boxes, scores, labels) in enumerate(dec):
                out.append({"sample_id": images[i + k][0], "boxes_xyxy": boxes.tolist(),
                            "scores": scores.tolist(), "classes": [int(c) for c in labels.tolist()]})
        return out

    def latency_fns(self, model: Any, shape: tuple[int, int], precision: str, device: str,
                    sample_image: str, postprocess: Mapping[str, Any] | None = None) -> dict[str, Callable[[], Any]]:
        """``postprocess`` = latency operating point ``{conf, iou, max_det}`` (``iou`` is the NMS IoU)."""
        import torch

        ds = self._dataset(model, [sample_image])
        tensor, target = ds[0]
        x = tensor[None].to(device)
        fp16 = precision == "FP16"
        pp = dict(postprocess or {})
        cfg = {**EVAL_DEFAULTS, **{k: pp[k] for k in ("conf", "max_det") if k in pp},
               **({"nms_iou": pp["iou"]} if "iou" in pp else {})}

        def run(fn: Callable[[], Any]) -> Callable[[], Any]:
            def call() -> Any:
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                    return fn()
            return call

        from PIL import Image

        with Image.open(sample_image) as loaded:
            decoded = loaded.convert("RGB")
        imgsz = model.mbnas_meta["model_kwargs"]["imgsz"]
        return {"forward_only": run(lambda: model(x)),
                "forward_and_postprocess": run(lambda: self._decode(model(x), [target], cfg)),
                "end_to_end": run(lambda: self._decode(model(letterbox_pil(decoded, imgsz)[None].to(device)),
                                                       [target], cfg))}

    def postprocess_description(self, model: Any) -> dict[str, Any]:
        return {"head": "slot classifier (softmax, background last)", "nms": True,
                "nms_impl": "python_loop (class-wise pure-Torch NMS on CPU, ETRI notebook)", **EVAL_DEFAULTS,
                "preprocess": "letterbox center pad 114 (notebook dataset)"}

    def weights_only(self, eval_ckpt: str | Path, path: str | Path) -> str:
        return ck.write_weights_fp16(ck.load_eval_snapshot(eval_ckpt)["state_dict"], path)["sha256"]


def letterbox_pil(image: Any, imgsz: int) -> Any:
    """Same resize/pad/normalise as the notebook dataset ``__getitem__`` for an already-decoded RGB PIL image."""
    import numpy as np
    import torch
    from PIL import Image

    width, height = image.size
    scale = min(imgsz / width, imgsz / height)
    resized = (max(1, round(width * scale)), max(1, round(height * scale)))
    left, top = (imgsz - resized[0]) // 2, (imgsz - resized[1]) // 2
    canvas = Image.new("RGB", (imgsz, imgsz), (114, 114, 114))
    canvas.paste(image.resize(resized, resample=Image.Resampling.BILINEAR), (left, top))
    return torch.from_numpy(np.asarray(canvas, dtype=np.float32).copy()).permute(2, 0, 1) / 255


class _Stage:
    """One ``train_until`` stage of the ETRI loop (state captured/restored as a whole)."""

    def __init__(self, adapter: EtriHybridAdapter, ctx: TrainContext, kw: dict[str, Any], sched: dict[str, Any]) -> None:
        import random

        import numpy as np
        import torch

        from mbnas_model_runtime.adapters.etri_hybrid.criterion import MbnasSetCriterion
        from mbnas_model_runtime.adapters.etri_hybrid.data import YoloDetectionDataset

        self.a, self.ctx, self.kw, self.sched = adapter, ctx, kw, sched
        self.device = torch.device(ctx.device)
        seed = int(ctx.training_seed)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.model = adapter._make(kw).to(self.device)
        self.hashes = {"architecture": structural_hash(self.model), "recipe": recipe_hash(sched),
                       "protocol": ctx.protocol_hash, **ctx.data.hashes}
        self.criterion = MbnasSetCriterion(kw["num_classes"]).to(self.device)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=sched["lr0"], weight_decay=sched["weight_decay"],
                                           betas=tuple(sched["betas"]))
        horizon, warm, lrf = sched["epochs"], sched["warmup_epochs"], sched["lrf"]
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(self.optimizer, lambda e: lr_factor(e, horizon, warm, lrf))
        self.use_amp = sched["amp"] and self.device.type == "cuda"
        self.scaler = torch.amp.GradScaler("cuda", enabled=self.use_amp)
        self.ema = _Ema(self.model) if sched["ema"] else None
        files = sorted(str(p) for p in (ctx.data.view_root / "images" / "train").iterdir())  # type: ignore[operator]
        self.dataset = YoloDetectionDataset(files, kw["imgsz"], kw["num_classes"], augment=True)
        self.generator = torch.Generator().manual_seed(seed)
        self.loader = torch.utils.data.DataLoader(self.dataset, batch_size=sched["batch"], shuffle=True, num_workers=0,
                                                  collate_fn=_collate, generator=self.generator)
        self.start = self.steps = self.samples = 0
        self.lr_history: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.rung: dict[str, Any] = {}
        self.last: tuple[Path, dict[str, Any]] | None = None
        self.paused = False
        self.ckpt_dir = ctx.candidate_dir / "ckpt"
        if ctx.resume_state is not None:
            self._restore(ctx.resume_state)

    def _restore(self, st: Mapping[str, Any]) -> None:
        from mbnas_model_runtime.adapters.base import ResumeBlocked

        ck.check_state(st, required=REQUIRED_STATE, expect_hashes=self.hashes)
        if bool(st["adapter_meta"]["ema_enabled"]) != (self.ema is not None):
            raise ResumeBlocked("HASH_MISMATCH:recipe", "EMA setting differs from the checkpoint")
        self.model.load_state_dict(st["model_fp32"])
        if self.ema is not None:
            if st.get("ema_fp32") is None or st.get("ema_updates") is None:
                raise ResumeBlocked("MISSING_STATE:ema_fp32", "EMA enabled but not in the checkpoint")
            self.ema.ema.load_state_dict(st["ema_fp32"])
            self.ema.updates = int(st["ema_updates"])
        self.optimizer.load_state_dict(st["optimizer"])
        self.scheduler.load_state_dict(st["scheduler"])
        self.scaler.load_state_dict(st["scaler"])
        self.start, self.steps, self.samples = int(st["epochs_done"]), int(st["global_step"]), int(st["samples_seen"])
        self.lr_history = [dict(h) for h in st["lr_history"]]
        self.generator.set_state(st["loader_generator_state"])
        ck.restore_rng(st["rng"])  # last: nothing below consumes RNG before the first batch

    def _epoch(self, epoch: int) -> None:
        from torch.nn.attention import SDPBackend, sdpa_kernel

        self.model.train()
        with sdpa_kernel(SDPBackend.MATH):  # flash/mem-efficient attention backward is nondeterministic
            self._batches(epoch)
        self.lr_history.append({"epoch": epoch, "lr": [float(g["lr"]) for g in self.optimizer.param_groups],
                                "global_step": self.steps, "train_loss": self._loss_sum / max(self._batches_n, 1)})
        self.scheduler.step()

    def _batches(self, epoch: int) -> None:
        import torch

        self._loss_sum, self._batches_n = 0.0, 0
        for images, targets in self.loader:
            images = images.to(self.device)
            targets = [{"boxes": t["boxes"].to(self.device), "labels": t["labels"].to(self.device)} for t in targets]
            self.optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=self.device.type, dtype=torch.float16, enabled=self.use_amp):
                losses = self.criterion(self.model(images), targets)
            if not torch.isfinite(losses["loss"]).item():
                raise FloatingPointError(f"non-finite training loss at epoch {epoch}")
            self.scaler.scale(losses["loss"]).backward()
            self.scaler.unscale_(self.optimizer)
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=GRAD_CLIP)
            self.scaler.step(self.optimizer)
            self.scaler.update()
            if self.ema is not None:
                self.ema.update(self.model)
            self.steps += 1
            self.samples += int(images.shape[0])
            self._loss_sum += float(losses["loss"].detach())
            self._batches_n += 1

    def _state(self, epoch: int) -> dict[str, Any]:
        return {
            "format": ck.FORMAT, "epoch": epoch, "epochs_done": epoch + 1, "global_step": self.steps,
            "samples_seen": self.samples, "model_fp32": ck.to_cpu_clone(self.model.state_dict()),
            "ema_fp32": ck.to_cpu_clone(self.ema.ema.state_dict()) if self.ema is not None else None,
            "ema_updates": self.ema.updates if self.ema is not None else None,
            "optimizer": ck.to_cpu_clone(self.optimizer.state_dict()), "scheduler": ck.plain(self.scheduler.state_dict()),
            "scaler": ck.plain(self.scaler.state_dict()), "rng": ck.capture_rng(),
            "loader_generator_state": self.generator.get_state().clone(), "hashes": dict(self.hashes),
            "lr_history": [dict(h) for h in self.lr_history],
            "adapter": {"backend": self.a.backend, "model_id": self.ctx.arch.model_id, "family_label": FAMILY_LABEL},
            "adapter_meta": {"ema_enabled": self.ema is not None, "model_kwargs": dict(self.kw),
                             "batches_per_epoch": len(self.loader)},
            "env": env_snapshot(self.ctx.device),
        }

    def run(self) -> None:
        ctx = self.ctx
        if self.start >= ctx.cumulative_target:
            raise ValueError(f"resume state already has {self.start} epochs >= target {ctx.cumulative_target}")
        for epoch in range(self.start, ctx.cumulative_target):
            self._epoch(epoch)
            state = self._state(epoch)
            done = epoch + 1
            is_end = done == ctx.cumulative_target
            path = ck.full_state_path(self.ckpt_dir, done)
            side = ck.save_full_state(path, state, rung_end=is_end)
            self.last = (path, side)
            self.checkpoints.append(self.a._row(ctx, "training_full", done, path, side, self.hashes))
            if is_end:
                self.rung = self.a._rung_end(ctx, self.ckpt_dir, done, state, self.kw, self.hashes, self.checkpoints)
            ck.prune(self.ckpt_dir)
            emit(ctx.progress, {"type": "epoch", "epoch": epoch, "epochs_done": done,
                                "epoch_target_total": ctx.cumulative_target, "global_step": self.steps,
                                "samples_seen": self.samples, "lr": self.lr_history[-1]["lr"][0],
                                "train_loss": self.lr_history[-1]["train_loss"]})
            if not is_end and ctx.stop_requested is not None and ctx.stop_requested():
                self.paused = True
                break

    def result(self, seconds: float) -> RungResult:
        if self.last is None:
            raise RuntimeError("no epoch ran")
        path, side = self.last
        ctx, rung = self.ctx, self.rung
        return RungResult(
            candidate_id=ctx.candidate_id, epochs_done=int(side["epochs_done"]), cumulative_target=ctx.cumulative_target,
            fixed_horizon=ctx.fixed_horizon, stopped_reason="PAUSED_AT_EPOCH" if self.paused else "TARGET_REACHED",
            rung_end_training_ckpt=None if self.paused else str(path),
            rung_end_ema_eval_ckpt=rung.get("eval_path"), weights_only_path=rung.get("weights_path"),
            size_mb=rung.get("size_mb"), size_dtype="float16" if rung else None, lr_history=self.lr_history,
            step_count=self.steps, samples_seen=self.samples,
            checkpoint_sha256={"training_full": side["sha256"], **rung.get("sha", {})},
            resumed_from=ctx.resume_path,
            resolved_recipe={**self.sched, "optimizer_class": "AdamW", "scheduler": "LambdaLR(warmup+cosine, per epoch)",
                             "batches_per_epoch": len(self.loader), "train_images": len(self.dataset),
                             "amp_resolved": self.use_amp},
            hashes=self.hashes, checkpoints=self.checkpoints, timings={"stage_wall_s": round(seconds, 3)},
            notes=[FAMILY_LABEL])
