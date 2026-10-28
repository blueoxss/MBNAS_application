"""Ultralytics 8.4.173 adapter: YOLO11 (``yolo11{n,s,m,l,x}``), YOLO26 (``yolo26{n,s,m,l,x}``) and RT-DETR
(``rtdetr-{l,x}``) plus search candidates expressed as ultralytics model yaml dicts.

``train_until`` implements the audited fixed-horizon ``run_until`` recipe (STAGE_TRAINER_AUDIT D, PLAN §8.2):

* a FRESH ``DetectionTrainer``/``RTDETRTrainer`` per stage with ``epochs = fixed_horizon``, ``nbs = batch``
  (gradient accumulation 1), ``deterministic=True``, ``seed = training_seed``, ``val=True``, ``plots=False``,
  ``save_period=-1``, ``patience=0`` (no early stop), ``final_eval`` disabled (no native strip / best.pt re-val);
* the model module is built right after the trainer is constructed (same RNG position as the native
  ``setup_model``) from the registry yaml or the candidate yaml dict, optionally loading verified official weights
  (``pretrained_native``);
* ``on_pretrain_routine_end`` of a resumed stage overlays the full FP32 state in the audited order (model, EMA +
  updates, optimizer, scaler, scheduler, ``start_epoch``/``last_epoch``, best fitness/stopper, accumulate, YOLO26
  ``E2ELoss.updates``, mosaic ``dataset.buffer``, loader iterator + generator state + pending ``randperm`` tail, and the
  python/numpy/torch/CUDA RNG last);
* ``on_train_epoch_end`` records the epoch's LR (optimizer param groups) and sets ``trainer.stop`` when
  ``epoch + 1 == cumulative_target`` or a pause was requested (validation + save + ``on_model_save`` still run);
* ``on_model_save`` writes the per-epoch full state (``mbnas.full_state.v1``) and, at the rung end, the FP32 EMA
  evaluation snapshot and the FP16 weights-only size artifact. Native ``last.pt``/``best.pt`` (FP16 EMA, FP16
  optimizer) are never a resume source and are deleted after the stage unless ``keep_native_weights=True``.

Classes (S3-F2): :class:`UltralyticsAdapter` is the shared ultralytics base (recipe, fixed-horizon stage, full-state
capture/overlay, predict/latency plumbing). :class:`YoloDetectAdapter` (YOLO11 + YOLO26) and :class:`RtdetrAdapter`
(RT-DETR) are the family adapters; each owns its model class, trainer, preprocessing, decode/postprocess, resume
profile and capability declaration. ``UltralyticsAdapter(family, variant)`` is kept as a constructor that returns
the family class for ``variant`` (``detect`` | ``rtdetr``), so existing callers keep working. ``self.variant`` stays
``detect`` / ``rtdetr`` because it is part of the recipe hash and the full-state ``adapter`` record (resume
compatibility with existing checkpoints).

Exactness (PILOT profile): bitwise for YOLO11/YOLO26 when ``workers == 0``, accumulation 1 and no ``close_mosaic``
boundary falls strictly inside a resumed stage; RT-DETR is never bitwise (nondeterministic ``grid_sample`` backward)
— counters/LR/optimizer-step continuity are exact and weights are compared with a tolerance.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from mbnas_model_runtime import checkpoint as ck
from mbnas_model_runtime.adapters.base import (
    ArchSpec, BuildReport, CapabilityError, RungResult, TrainContext, check_init, resolve_register_hook,
)
from mbnas_model_runtime.adapters.common import count_modules, cuda_index, emit, env_snapshot, recipe_hash, structural_hash, verify_weights

ULTRALYTICS_VERSION = "8.4.173"
INIT_MODES = ("scratch", "pretrained_native")
NATIVE_LOSS = {"YOLO11": "v8DetectionLoss (CIoU + DFL + BCE, TAL topk 10)",
               "YOLO26": "E2ELoss (one2many v8DetectionLoss topk10 + one2one topk7/1, epoch-decayed o2m weight)",
               "RTDETR": "RTDETRDetectionLoss (VFL + L1 + GIoU, Hungarian matching, contrastive denoising)"}
REQUIRED_STATE = ("model_fp32", "ema_fp32", "ema_updates", "optimizer", "scheduler", "scaler",
                  "loader_generator_state", "adapter_meta", "adapter_meta.dataset_buffer", "adapter_meta.stopper",
                  "adapter_meta.accumulate", "adapter_meta.loader_pending_tail", "adapter_meta.optimizer_steps")
EVAL_DEFAULTS = {"conf": 0.001, "iou": 0.7, "max_det": 300}


def prepare_ultralytics_env(cfg_root: str | Path, weights_dir: str) -> Path:
    """Point ultralytics at a run-local settings dir (weights_dir pinned, sync/integrations off, no auto-install).

    Call before ``import ultralytics``; if it is already imported the live ``SETTINGS``/``WEIGHTS_DIR`` are patched.
    """
    import sys

    cfg_root = Path(cfg_root)
    sdir = cfg_root / "Ultralytics"
    sdir.mkdir(parents=True, exist_ok=True)
    settings = {"settings_version": "0.0.8", "datasets_dir": str(cfg_root / "datasets"), "weights_dir": weights_dir,
                "runs_dir": str(cfg_root / "runs"), "uuid": "mbnas", "sync": False, "api_key": "",
                "openai_api_key": "", "clearml": False, "comet": False, "dvc": False, "hub": False, "mlflow": False,
                "neptune": False, "raytune": False, "tensorboard": False, "wandb": False, "vscode_msg": False,
                "openvino_msg": False}
    target = sdir / "settings.json"
    current = json.loads(target.read_text()) if target.is_file() else {}
    if {k: current.get(k) for k in settings if k != "uuid"} != {k: v for k, v in settings.items() if k != "uuid"}:
        target.write_text(json.dumps(settings, indent=2))
    font = Path.home() / ".config" / "Ultralytics" / "Arial.ttf"
    if font.is_file() and not (sdir / "Arial.ttf").exists():
        (sdir / "Arial.ttf").write_bytes(font.read_bytes())
    os.environ["YOLO_CONFIG_DIR"] = str(cfg_root)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ.setdefault("YOLO_VERBOSE", "false")
    if "ultralytics" in sys.modules:
        import ultralytics.utils as uu

        uu.SETTINGS.update({k: v for k, v in settings.items() if k in uu.SETTINGS})
        uu.WEIGHTS_DIR = Path(weights_dir)
    return cfg_root


class UltralyticsAdapter:
    """Shared ultralytics base; ``UltralyticsAdapter(family, variant)`` returns the family adapter for ``variant``.

    Subclasses set :attr:`variant` and implement the family hooks (``_model_class``, ``_trainer_class``,
    ``_scale_fill``, ``_decode``, ``_pin_head``, ``_resume_profile``, ``_loss_key``, ``postprocess_description``).
    """

    backend = "ultralytics"
    variant: str = ""

    def __new__(cls, *args: Any, **kwargs: Any) -> "UltralyticsAdapter":
        if cls is UltralyticsAdapter:
            variant = args[1] if len(args) > 1 else kwargs.get("variant", "detect")
            sub = _VARIANT_CLASSES.get(variant)
            if sub is None:
                raise ValueError(f"unknown ultralytics variant {variant!r}")
            return super().__new__(sub)
        return super().__new__(cls)

    def __init__(self, family: str, variant: str | None = None, *, keep_native_weights: bool = False) -> None:
        if variant is not None and variant != self.variant:
            raise ValueError(f"{type(self).__name__} serves variant {self.variant!r}, not {variant!r}")
        self.family = family
        self.keep_native_weights = keep_native_weights  # native last.pt/best.pt are never a resume source

    # -- family hooks (overridden by YoloDetectAdapter / RtdetrAdapter) -------------------------------------------
    def _model_class(self) -> Any:
        raise NotImplementedError

    def _trainer_class(self) -> Any:
        raise NotImplementedError

    _scale_fill = False
    _resume_mode = ""
    _resume_tail = ""
    _resume_extra = ""
    _determinism = ""

    def _loss_key(self) -> str:
        raise NotImplementedError

    def _pin_head(self, model: Any, meta: Mapping[str, Any]) -> None:
        """Pin the inference head of a rebuilt evaluation model (no-op unless the family has a switchable head)."""

    def _decode(self, model: Any, out: Any, shape: tuple[int, int], orig_shapes: list[tuple[int, int]],
                conf: float, iou: float, max_det: int) -> list[Any]:
        raise NotImplementedError

    # -- declarations ---------------------------------------------------------------------------------------------
    def capability(self) -> dict[str, Any]:
        return {
            "backend": self.backend, "version": ULTRALYTICS_VERSION, "train": True, "val": True,
            "adapter_class": type(self).__qualname__,
            "full_state_resume": self._resume_mode,
            "full_state_resume_detail": (
                "full FP32 state overlay (model, EMA+updates, optimizer, scaler, scheduler, epoch, optimizer steps, "
                "best fitness/stopper, mosaic buffer, loader generator + pending tail, python/numpy/torch/CUDA RNG"
                + self._resume_extra + "); " + self._resume_tail),
            "init_modes": list(INIT_MODES), "native_loss": NATIVE_LOSS[self._loss_key()],
            "precisions": ["FP32", "FP16"], "train_precisions": ["amp_fp16_fp32", "fp32"],
            "gradient_accumulation": "must_be_1",
            "determinism_profile": self._determinism,
            "input_size_rule": {"kind": "stride_ceil", "stride": 32},
            "export_onnx": True, "export_trt": "via benchmark module (format=engine)",
            "inference_head": "e2e_one2one_nms_false" if self.family == "YOLO26" else "native",
        }

    def required_state_keys(self) -> tuple[str, ...]:
        return REQUIRED_STATE

    # -- build ----------------------------------------------------------------------------------------------------
    def _cfg(self, arch: ArchSpec) -> Any:
        hook = resolve_register_hook(arch.register_hook)
        if hook is not None:
            hook()
        return copy.deepcopy(arch.config) if isinstance(arch.config, dict) else str(arch.config)

    def build(self, arch: ArchSpec, num_classes: int, init: str, seed: int) -> BuildReport:
        """CPU build (and optional verified weight transfer) used by registry probes and candidate validation."""
        import torch

        check_init(init, INIT_MODES, arch.model_id)
        torch.manual_seed(seed)
        model = self._model_class()(self._cfg(arch), nc=num_classes, verbose=False)
        notes = []
        if init == "pretrained_native":
            verify_weights(arch.weights_path, arch.weights_sha256, arch.model_id)
            from ultralytics.nn.tasks import load_checkpoint

            model.load(load_checkpoint(arch.weights_path)[0])
            notes.append(f"weights transferred from {arch.weights_path}")
        return BuildReport(arch.model_id, self.backend, int(sum(p.numel() for p in model.parameters())),
                           structural_hash(model), init, count_modules(model), notes)

    # -- recipe ---------------------------------------------------------------------------------------------------
    def overrides(self, ctx: TrainContext, data_yaml: Path, stage_name: str) -> dict[str, Any]:
        """Trainer overrides for one stage (identical for every stage of a candidate except ``name``)."""
        r = ctx.recipe
        if int(r.get("gradient_accumulation", 1)) != 1:
            raise CapabilityError("ACCUMULATION_RESUME_UNSUPPORTED", "ultralytics requires gradient_accumulation=1")
        if abs(float(r.get("beta2", 0.999)) - 0.999) > 1e-12:
            raise CapabilityError("RECIPE_UNSUPPORTED", "ultralytics Adam-family optimizers fix beta2=0.999")
        imgsz = r["input_shape"]
        return {
            "model": str(ctx.candidate_dir / "architecture.yaml"), "data": str(data_yaml), "task": "detect",
            "epochs": int(ctx.fixed_horizon), "batch": int(r["batch"]), "nbs": int(r["batch"]),
            "imgsz": int(max(imgsz)), "workers": int(r.get("workers", 0)), "seed": int(ctx.training_seed),
            "deterministic": True, "optimizer": r["optimizer"], "lr0": float(r["lr0"]), "lrf": float(r["lrf"]),
            "momentum": float(r["momentum"]), "weight_decay": float(r["weight_decay"]),
            "warmup_epochs": float(r["warmup_epochs"]), "cos_lr": bool(r["cos_lr"]),
            "close_mosaic": int(r.get("close_mosaic", 0)), "amp": bool(r["amp"]), "val": bool(r.get("validation_enabled", True)), "plots": bool(r.get("native_image_artifacts", False)),
            "save": True, "save_period": -1, "patience": 0, "exist_ok": True, "project": str(ctx.candidate_dir / "native"),
            "name": stage_name, "device": "cpu" if ctx.device == "cpu" else str(cuda_index(ctx.device)), "pretrained": False, "verbose": False,
            "cache": False, "rect": False, "single_cls": False, "fraction": 1.0, "resume": False, "compile": False,
        }

    def _hashes(self, ctx: TrainContext, model: Any, ov: Mapping[str, Any]) -> dict[str, str]:
        schedule = {k: v for k, v in ov.items() if k not in ("project", "name", "device", "data", "model", "verbose")}
        schedule.update({"init": ctx.init, "weights_sha256": ctx.arch.weights_sha256 if ctx.init != "scratch" else None,
                         "variant": self.variant, "inference_head": ctx.arch.inference_head})
        return {"architecture": structural_hash(model), "recipe": recipe_hash(schedule), "protocol": ctx.protocol_hash,
                **ctx.data.hashes}

    # -- training -------------------------------------------------------------------------------------------------
    def train_until(self, ctx: TrainContext) -> RungResult:
        """One fixed-horizon stage: fresh trainer, overlay (if resuming), stop at the target epoch boundary."""
        if ctx.data.view_root is None:
            raise ValueError("ultralytics training needs DataSpec.view_root (training.dataset_view.build_yolo_view)")
        trainer_cls = self._trainer_class()
        ctx.candidate_dir.mkdir(parents=True, exist_ok=True)
        cfg = self._cfg(ctx.arch)
        self._write_arch_yaml(ctx, cfg)
        start = int(ctx.resume_state["epochs_done"]) if ctx.resume_state else 0
        if start >= ctx.cumulative_target:
            raise ValueError(f"resume state already has {start} epochs >= target {ctx.cumulative_target}")
        ov = self.overrides(ctx, ctx.data.view_root / "data.yaml", f"stage_e{start:04d}_e{ctx.cumulative_target:04d}")
        t0 = time.time()
        trainer = trainer_cls(overrides=ov)
        trainer.final_eval = lambda: None  # StageTrainer owns rung-end evaluation; avoid strip + best.pt re-validation
        if ctx.recipe.get("validation_enabled", True) is False:
            original_loader = trainer.get_dataloader
            class _NoValidationLoader:
                dataset = ()
                def __len__(self):
                    return 0
                def __iter__(self):
                    return iter(())
            trainer.get_dataloader = lambda *args, **kwargs: _NoValidationLoader() if kwargs.get("mode") == "val" else original_loader(*args, **kwargs)
            # Ultralytics calls validate() at final_epoch even when args.val is False. This explicit training mode
            # returns no metrics and no fitness; it never iterates a validation loader or invents best metrics.
            trainer.validate = lambda: ({}, None)
        trainer.model = self._init_module(trainer, cfg, ctx)  # same RNG position as native setup_model()
        hashes = self._hashes(ctx, trainer.model, ov)
        if ctx.resume_state is not None:
            ck.check_state(ctx.resume_state, required=REQUIRED_STATE, expect_hashes=hashes)
        run = _StageRun(self, ctx, trainer, hashes, start)
        run.install()
        try:
            trainer.train()
        finally:
            if not self.keep_native_weights:  # FP16 EMA + FP16 optimizer copies (3x model size) nobody reads
                for native in Path(trainer.wdir).glob("*.pt"):
                    native.unlink()
        run.finish_checks()
        return run.result(ov, time.time() - t0)

    def _write_arch_yaml(self, ctx: TrainContext, cfg: Any) -> None:
        import yaml

        path = ctx.candidate_dir / "architecture.yaml"
        if isinstance(cfg, dict):
            recorded_cfg = copy.deepcopy(cfg)
            prefix = ""
        else:
            from ultralytics.nn.tasks import yaml_model_load

            recorded_cfg = {k: v for k, v in yaml_model_load(cfg).items() if k != "yaml_file"}
            prefix = f"# registry model {cfg} (ultralytics {ULTRALYTICS_VERSION})\n"
        # get_model below applies the dataset's nc to the actual detection head.
        # Record that same executed class count rather than a registry template's nc=80.
        recorded_cfg["nc"] = len(ctx.data.names)
        text = prefix + yaml.safe_dump(recorded_cfg, sort_keys=False)
        # DD-12: an architecture.yaml written by an older serialization (key order, flow/block style, comments) is
        # the same executed configuration; compare the parsed dicts and rewrite only when the configuration differs.
        if path.is_file():
            try:
                if yaml.safe_load(path.read_text()) == recorded_cfg:
                    return
            except (OSError, yaml.YAMLError):
                pass
        path.write_text(text)

    def _init_module(self, trainer: Any, cfg: Any, ctx: TrainContext) -> Any:
        check_init(ctx.init, INIT_MODES, ctx.arch.model_id)
        weights = None
        if ctx.init == "pretrained_native":
            verify_weights(ctx.arch.weights_path, ctx.arch.weights_sha256, ctx.arch.model_id)
            from ultralytics.nn.tasks import load_checkpoint

            weights = load_checkpoint(ctx.arch.weights_path)[0]
        return trainer.get_model(cfg=cfg, weights=weights, verbose=False)

    # -- evaluation -----------------------------------------------------------------------------------------------
    def load_eval_model(self, eval_ckpt: str | Path, device: str) -> Any:
        """Rebuild the module from the snapshot meta, load FP32 EMA weights, pin the inference head, fuse, eval."""
        snap = ck.load_eval_snapshot(eval_ckpt)
        meta = snap["model_meta"]
        model = self._model_class()(copy.deepcopy(meta["yaml"]), nc=int(meta["nc"]), verbose=False)
        model.load_state_dict(snap["state_dict"], strict=True)
        model.names = {int(k): v for k, v in meta["names"].items()}
        self._pin_head(model, meta)
        model = model.fuse(verbose=False) if hasattr(model, "fuse") else model
        model = model.to(device).eval()
        model.mbnas_meta = {**meta, "eval_ckpt": str(eval_ckpt), "variant": self.variant}
        return model

    def _preprocess(self, im0: Any, shape: tuple[int, int]) -> Any:
        from ultralytics.data.augment import LetterBox

        lb = LetterBox(new_shape=shape, auto=False, scale_fill=self._scale_fill, center=True)
        return lb(image=im0)

    def _to_tensor(self, imgs: list[Any], device: str) -> Any:
        import numpy as np
        import torch

        x = np.stack(imgs)[..., ::-1].transpose(0, 3, 1, 2)  # BGR->RGB, BHWC->BCHW
        return torch.from_numpy(np.ascontiguousarray(x)).to(device).float() / 255.0

    def _postprocess(self, model: Any, out: Any, shape: tuple[int, int], orig_shapes: list[tuple[int, int]],
                     cfg: Mapping[str, Any]) -> list[Any]:
        """Native decode/NMS -> per-image tensor [N, 6] (x1, y1, x2, y2, score, class) in source pixels."""
        return self._decode(model, out, shape, orig_shapes, float(cfg["conf"]), float(cfg["iou"]), int(cfg["max_det"]))

    def predict(self, model: Any, images: list[tuple[str, str]], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Common-format predictions for ``[(sample_id, path)]`` at ``cfg`` (conf/iou/max_det/shape/batch/precision)."""
        import cv2
        import torch

        cfg = {**EVAL_DEFAULTS, **cfg}
        shape = tuple(int(v) for v in cfg["shape"])
        device = next(model.parameters()).device
        fp16 = cfg.get("precision", "FP16") == "FP16" and device.type == "cuda"
        out_list: list[dict[str, Any]] = []
        bs = int(cfg.get("batch", 8))
        for i in range(0, len(images), bs):
            chunk = images[i:i + bs]
            ims = [cv2.imread(p) for _, p in chunk]
            if any(im is None for im in ims):
                raise FileNotFoundError(f"unreadable image in {[p for _, p in chunk]}")
            x = self._to_tensor([self._preprocess(im, shape) for im in ims], str(device))
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                out = model(x)
                dets = self._postprocess(model, out, shape, [im.shape[:2] for im in ims], cfg)
            for (sid, _), d in zip(chunk, dets):
                d = d.cpu()
                out_list.append({"sample_id": sid, "boxes_xyxy": d[:, :4].tolist(), "scores": d[:, 4].tolist(),
                                 "classes": [int(c) for c in d[:, 5].tolist()]})
        return out_list

    def latency_fns(self, model: Any, shape: tuple[int, int], precision: str, device: str,
                    sample_image: str, postprocess: Mapping[str, Any] | None = None) -> dict[str, Callable[[], Any]]:
        """Closures per latency scope; each runs one batch-1 inference (caller synchronises/time-stamps).

        ``postprocess`` = latency operating point ``{conf, iou, max_det}`` (resolved console.benchmark.postprocess)
        used by ``forward_and_postprocess`` / ``end_to_end``; without it the AP-evaluation defaults apply.
        """
        import cv2
        import torch

        im0 = cv2.imread(sample_image)
        fp16 = precision == "FP16"
        x = self._to_tensor([self._preprocess(im0, shape)], device)
        cfg = {**EVAL_DEFAULTS, **{k: postprocess[k] for k in ("conf", "iou", "max_det") if k in (postprocess or {})}}
        orig = [im0.shape[:2]]

        def run(fn: Callable[[], Any]) -> Callable[[], Any]:
            def call() -> Any:
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                    return fn()
            return call

        return {
            "forward_only": run(lambda: model(x)),
            "forward_and_postprocess": run(lambda: self._postprocess(model, model(x), shape, orig, cfg)),
            "end_to_end": run(lambda: self._postprocess(
                model, model(self._to_tensor([self._preprocess(im0, shape)], device)), shape, orig, cfg)),
        }

    def postprocess_description(self, model: Any) -> dict[str, Any]:
        raise NotImplementedError

    def weights_only(self, eval_ckpt: str | Path, path: str | Path) -> str:
        snap = ck.load_eval_snapshot(eval_ckpt)
        return ck.write_weights_fp16(snap["state_dict"], path)["sha256"]


class YoloDetectAdapter(UltralyticsAdapter):
    """YOLO detect family adapter: YOLO11 and YOLO26 (``DetectionModel`` + ``DetectionTrainer``).

    YOLO11 and YOLO26 share this class on purpose: both are the same ultralytics ``DetectionModel`` /
    ``DetectionTrainer`` / ``Detect``-head code path with letterbox preprocessing and ``ultralytics.utils.nms``
    decode. Their only differences are data-driven by ``family``: the native loss (v8DetectionLoss vs E2ELoss, chosen
    by the model yaml inside ultralytics), the YOLO26 one-to-one head pin (``end2end``) and the E2ELoss ``updates``
    counter, which the shared full-state capture/overlay already records generically. RT-DETR, RF-DETR and the ETRI
    hybrid each have their own class.
    """

    variant = "detect"
    _scale_fill = False  # letterbox, centre pad 114
    _resume_mode = "EXACT_UNDER_PILOT_PROFILE"
    _resume_extra = ", E2ELoss updates for YOLO26"
    _resume_tail = ("bitwise when workers=0, gradient_accumulation=1, deterministic=True and no close_mosaic boundary "
                    "falls strictly inside a resumed stage")
    _determinism = "pilot_bitwise_workers0"

    def _loss_key(self) -> str:
        return "YOLO26" if self.family == "YOLO26" else "YOLO11"

    def _model_class(self) -> Any:
        from ultralytics.nn.tasks import DetectionModel

        return DetectionModel

    def _trainer_class(self) -> Any:
        from ultralytics.models.yolo.detect import DetectionTrainer

        return DetectionTrainer

    def _pin_head(self, model: Any, meta: Mapping[str, Any]) -> None:
        head = model.model[-1]
        if getattr(head, "one2one_cv2", None) is not None:
            head.end2end = meta.get("inference_head") == "e2e_one2one_nms_false"

    def _decode(self, model: Any, out: Any, shape: tuple[int, int], orig_shapes: list[tuple[int, int]],
                conf: float, iou: float, max_det: int) -> list[Any]:
        from ultralytics.utils import nms, ops

        head = model.model[-1]
        dets = nms.non_max_suppression(out, conf, iou, None, False, max_det=max_det, nc=0,
                                       end2end=bool(getattr(head, "end2end", False)))
        res = []
        for d, (oh, ow) in zip(dets, orig_shapes):
            d = d.float().clone()
            d[:, :4] = ops.scale_boxes(shape, d[:, :4], (oh, ow))
            res.append(d)
        return res

    def postprocess_description(self, model: Any) -> dict[str, Any]:
        head = model.model[-1]
        e2e = bool(getattr(head, "end2end", False))
        return {"head": "one2one (end2end, nms=False)" if e2e else "one2many + NMS", "nms": not e2e,
                "nms_impl": None if e2e else "ultralytics.utils.nms (torchvision.ops.nms when torchvision is imported, "
                                             "else TorchNMS python loop)",
                **EVAL_DEFAULTS, "preprocess": "letterbox center pad 114"}


class RtdetrAdapter(UltralyticsAdapter):
    """RT-DETR family adapter (``rtdetr-{l,x}``): ``RTDETRDetectionModel`` + ``RTDETRTrainer``, stretch resize
    (``scale_fill``), top-k query decode without NMS, RTDETRDetectionLoss; resume is exact on counters/LR/optimizer
    steps but weights only within tolerance (nondeterministic ``grid_sample`` backward)."""

    variant = "rtdetr"
    _scale_fill = True  # stretch, no letterbox
    _resume_mode = "EXACT_COUNTERS_TOLERANCE_WEIGHTS"
    _resume_extra = ""
    _resume_tail = ("grid_sample backward is nondeterministic, so weights match only within tolerance even between "
                    "two continuous runs")
    _determinism = "torch_default_nondeterministic_backward"

    def _loss_key(self) -> str:
        return "RTDETR"

    def _model_class(self) -> Any:
        from ultralytics.nn.tasks import RTDETRDetectionModel

        return RTDETRDetectionModel

    def _trainer_class(self) -> Any:
        from ultralytics.models.rtdetr.train import RTDETRTrainer

        return RTDETRTrainer

    def _decode(self, model: Any, out: Any, shape: tuple[int, int], orig_shapes: list[tuple[int, int]],
                conf: float, iou: float, max_det: int) -> list[Any]:
        import torch
        from ultralytics.utils import ops

        pred = out[0] if isinstance(out, (list, tuple)) else out
        res = []
        for p, (oh, ow) in zip(pred.float(), orig_shapes):
            boxes = ops.xywh2xyxy(p[:, :4])
            keep = p[:, 4] > conf
            det = torch.cat([boxes, p[:, 4:6]], dim=-1)[keep][:max_det]
            det[:, [0, 2]] *= ow
            det[:, [1, 3]] *= oh
            res.append(det)
        return res

    def postprocess_description(self, model: Any) -> dict[str, Any]:
        return {"head": "RTDETRDecoder top-k queries", "nms": False, **EVAL_DEFAULTS, "preprocess": "stretch"}


_VARIANT_CLASSES: dict[str, type[UltralyticsAdapter]] = {"detect": YoloDetectAdapter, "rtdetr": RtdetrAdapter}


class _StageRun:
    """Callback state for one fresh-trainer stage (kept out of the adapter so the adapter stays stateless)."""

    def __init__(self, adapter: UltralyticsAdapter, ctx: TrainContext, trainer: Any, hashes: dict[str, str],
                 start: int) -> None:
        self.a, self.ctx, self.tr, self.hashes, self.start = adapter, ctx, trainer, hashes, start
        prev = ctx.resume_state or {}
        self.lr_history: list[dict[str, Any]] = list(prev.get("lr_history", []))
        self.steps = int(prev.get("adapter_meta", {}).get("optimizer_steps", 0))
        self.checkpoints: list[dict[str, Any]] = []
        self.paused = False
        self.rung_end: dict[str, Any] = {}
        self.last_full: tuple[Path, dict[str, Any]] | None = None
        self.amp_requested = bool(ctx.recipe["amp"])
        self.ckpt_dir = ctx.candidate_dir / "ckpt"

    def install(self) -> None:
        tr = self.tr
        orig_step = tr.optimizer_step

        def counted_step() -> None:
            orig_step()
            self.steps += 1

        tr.optimizer_step = counted_step
        tr.add_callback("on_pretrain_routine_end", self.on_pretrain_routine_end)
        tr.add_callback("on_train_epoch_end", self.on_train_epoch_end)
        tr.add_callback("on_model_save", self.on_model_save)

    # callbacks -----------------------------------------------------------------------------------------------------
    def on_pretrain_routine_end(self, tr: Any) -> None:
        if tr.amp != self.amp_requested:
            raise CapabilityError("PRECISION_FALLBACK", f"requested amp={self.amp_requested}, trainer resolved "
                                  f"amp={tr.amp} (check_amp); refusing a silent precision change")
        if self.ctx.recipe.get("validation_enabled", True) is False:
            tr.metrics, tr.fitness = {}, None
        if self.ctx.resume_state is not None:
            _overlay(tr, self.ctx.resume_state)

    def on_train_epoch_end(self, tr: Any) -> None:
        if tr.batch_size != int(self.ctx.recipe["batch"]):
            raise CapabilityError("BATCH_CHANGED", f"trainer batch {tr.batch_size} != recipe {self.ctx.recipe['batch']} "
                                  "(OOM auto-halving); refusing a silent recipe change")
        self.lr_history.append({"epoch": int(tr.epoch), "lr": [float(g["lr"]) for g in tr.optimizer.param_groups],
                                "param_groups": [g.get("param_group") for g in tr.optimizer.param_groups],
                                "global_step": self.steps})
        if tr.epoch + 1 == self.ctx.cumulative_target:
            tr.stop = True
        elif self.ctx.stop_requested is not None and self.ctx.stop_requested():
            tr.stop = True
            self.paused = True

    def on_model_save(self, tr: Any) -> None:
        epochs_done = int(tr.epoch) + 1
        if self.lr_history and self.lr_history[-1]["epoch"] == tr.epoch:
            self.lr_history[-1]["native_metrics"] = {k: float(v) for k, v in (tr.metrics or {}).items()}
            self.lr_history[-1]["native_fitness"] = None if tr.fitness is None else float(tr.fitness)
        state = _capture(tr, self.hashes, self.lr_history, self.steps, self.a, self.ctx)
        rung_end = epochs_done == self.ctx.cumulative_target
        path = ck.full_state_path(self.ckpt_dir, epochs_done)
        side = ck.save_full_state(path, state, rung_end=rung_end)
        self.last_full = (path, side)
        self._record("training_full", epochs_done, path, side)
        if rung_end:
            self._write_rung_end(tr, epochs_done, state)
        ck.prune(self.ckpt_dir)
        emit(self.ctx.progress, {"type": "epoch", "epoch": int(tr.epoch), "epochs_done": epochs_done,
                                 "epoch_target_total": self.ctx.cumulative_target, "global_step": self.steps,
                                 "samples_seen": state["samples_seen"], "lr": self.lr_history[-1]["lr"][0],
                                 "native_metrics": self.lr_history[-1].get("native_metrics")})

    def _write_rung_end(self, tr: Any, epochs_done: int, state: Mapping[str, Any]) -> None:
        from ultralytics.utils.torch_utils import unwrap_model

        m = unwrap_model(tr.model)
        meta = {"yaml": {k: v for k, v in m.yaml.items() if k != "yaml_file"}, "nc": int(tr.data["nc"]),
                "names": {str(k): v for k, v in tr.data["names"].items()}, "variant": self.a.variant,
                "family": self.a.family, "inference_head": self.ctx.arch.inference_head,
                "imgsz": int(tr.args.imgsz), "stride": [float(s) for s in m.stride.tolist()]}
        eval_path = ck.eval_snapshot_path(self.ckpt_dir, epochs_done)
        side = ck.save_eval_snapshot(eval_path, state["ema_fp32"], model_meta=meta, epochs_done=epochs_done,
                                     hashes=self.hashes)
        self._record("eval_ema", epochs_done, eval_path, side)
        w = ck.write_weights_fp16(state["ema_fp32"], ck.weights_only_path(self.ckpt_dir, epochs_done))
        self._record("weights_only", epochs_done, Path(w["path"]), w)
        self.rung_end = {"eval": (eval_path, side), "weights": w}

    def _record(self, kind: str, epochs_done: int, path: Path, side: Mapping[str, Any]) -> None:
        row = {"kind": kind, "epoch": epochs_done, "path": str(path), "sha256": side["sha256"],
               "bytes": int(side["bytes"]), "hashes": dict(self.hashes), "rung": self.ctx.rung}
        self.checkpoints.append(row)
        emit(self.ctx.progress, {"type": "checkpoint", **row})

    def finish_checks(self) -> None:
        if self.last_full is None:
            raise RuntimeError("trainer finished without writing a full-state checkpoint")

    def result(self, ov: Mapping[str, Any], seconds: float) -> RungResult:
        path, side = self.last_full  # type: ignore[misc]
        done = int(side["epochs_done"])
        reached = done == self.ctx.cumulative_target
        tr = self.tr
        resolved = {k: v for k, v in vars(tr.args).items() if isinstance(v, (str, int, float, bool, type(None)))}
        resolved.update({"optimizer_class": type(tr.optimizer).__name__, "resolved_optimizer": tr.args.optimizer,
                         "resolved_lr0": float(tr.args.lr0), "warmup_bias_lr": float(tr.args.warmup_bias_lr),
                         "batches_per_epoch": len(tr.train_loader), "accumulate": int(tr.accumulate),
                         "warmup_iterations": int(tr._get_warmup_iterations(len(tr.train_loader))),
                         "amp_resolved": bool(tr.amp), "train_images": len(tr.train_loader.dataset),
                         "fused_optimizer": bool(tr.optimizer.defaults.get("fused", False))})
        rung = self.rung_end
        notes = [] if self.ctx.recipe.get("workers", 0) == 0 else ["workers>0: augmentation RNG not restorable"]
        cm = int(ov.get("close_mosaic", 0))
        if cm and self.start > self.ctx.fixed_horizon - cm:
            notes.append("close_mosaic boundary inside a resumed stage: loader reset differs (not bitwise)")
        return RungResult(
            candidate_id=self.ctx.candidate_id, epochs_done=done, cumulative_target=self.ctx.cumulative_target,
            fixed_horizon=self.ctx.fixed_horizon, stopped_reason="TARGET_REACHED" if reached else "PAUSED_AT_EPOCH",
            rung_end_training_ckpt=str(path) if reached else None,
            rung_end_ema_eval_ckpt=str(rung["eval"][0]) if rung else None,
            weights_only_path=rung["weights"]["path"] if rung else None,
            size_mb=rung["weights"]["size_mb"] if rung else None, size_dtype="float16" if rung else None,
            lr_history=self.lr_history, step_count=self.steps, samples_seen=int(side["samples_seen"]),
            checkpoint_sha256={"training_full": side["sha256"],
                               **({"eval_ema": rung["eval"][1]["sha256"], "weights_only": rung["weights"]["sha256"]}
                                  if rung else {})},
            resumed_from=self.ctx.resume_path, resolved_recipe=resolved, hashes=self.hashes,
            checkpoints=self.checkpoints, timings={"stage_wall_s": round(seconds, 3)}, notes=notes)


def _capture(tr: Any, hashes: dict[str, str], lr_history: list[dict[str, Any]], steps: int,
             adapter: UltralyticsAdapter, ctx: TrainContext) -> dict[str, Any]:
    """Full FP32 state at an epoch boundary (A.10 / D.3), tensors cloned to CPU."""
    from ultralytics.utils.torch_utils import unwrap_model

    m = unwrap_model(tr.model)
    crit = getattr(m, "criterion", None)
    dl = tr.train_loader
    ds = dl.dataset
    epochs_done = int(tr.epoch) + 1
    return {
        "format": ck.FORMAT, "epoch": int(tr.epoch), "epochs_done": epochs_done, "global_step": int(steps),
        "samples_seen": epochs_done * len(ds),
        "model_fp32": ck.to_cpu_clone(m.state_dict()), "ema_fp32": ck.to_cpu_clone(tr.ema.ema.state_dict()),
        "ema_updates": int(tr.ema.updates), "optimizer": ck.to_cpu_clone(tr.optimizer.state_dict()),
        "scheduler": ck.plain(tr.scheduler.state_dict()), "scaler": ck.plain(tr.scaler.state_dict()),
        "rng": ck.capture_rng(), "loader_generator_state": dl.generator.get_state().clone(),
        "best_fitness": None if tr.best_fitness is None else float(tr.best_fitness),
        "hashes": dict(hashes), "lr_history": copy.deepcopy(lr_history),
        "adapter": {"backend": adapter.backend, "variant": adapter.variant, "family": adapter.family,
                    "model_id": ctx.arch.model_id, "version": ULTRALYTICS_VERSION},
        "adapter_meta": {
            "dataset_buffer": [int(i) for i in getattr(ds, "buffer", [])],
            "stopper": {"best_fitness": None if tr.stopper.best_fitness is None else float(tr.stopper.best_fitness),
                        "best_epoch": int(tr.stopper.best_epoch)},
            "accumulate": int(tr.accumulate), "optimizer_steps": int(steps),
            "loader_pending_tail": bool(len(ds) % dl.batch_size == 0 and not dl.drop_last),
            "criterion_updates": None if getattr(crit, "updates", None) is None else int(crit.updates),
            "batch_size": int(tr.batch_size), "batches_per_epoch": len(dl),
            "fitness": None if tr.fitness is None else float(tr.fitness),
        },
        "env": env_snapshot(str(tr.device)),
    }


def _overlay(tr: Any, st: Mapping[str, Any]) -> None:
    """Apply a verified full state to a FRESH trainer at ``on_pretrain_routine_end`` (audit D.4 order)."""
    import torch
    from ultralytics.utils.torch_utils import unwrap_model

    m = unwrap_model(tr.model)
    meta = st["adapter_meta"]
    m.load_state_dict(st["model_fp32"])  # in-place copy keeps optimizer param refs valid
    tr.ema.ema.load_state_dict(st["ema_fp32"])
    tr.ema.updates = int(st["ema_updates"])
    tr.optimizer.load_state_dict(st["optimizer"])
    tr.scaler.load_state_dict(st["scaler"])
    tr.scheduler.load_state_dict(st["scheduler"])
    tr.start_epoch = int(st["epoch"]) + 1
    tr.scheduler.last_epoch = tr.start_epoch - 1  # _setup_train already ran this line with start_epoch=0
    tr.best_fitness = st.get("best_fitness")
    tr.stopper.best_fitness = meta["stopper"]["best_fitness"]
    tr.stopper.best_epoch = meta["stopper"]["best_epoch"]
    tr.accumulate = int(meta["accumulate"])
    if meta.get("criterion_updates") is not None and getattr(m.model[-1], "one2one_cv2", None) is not None:
        m.criterion = m.init_criterion()  # E2ELoss o2m/o2o weights depend on `updates`
        m.criterion.updates = int(meta["criterion_updates"]) - 1
        m.criterion.update()
    dl = tr.train_loader
    ds = dl.dataset
    if hasattr(ds, "buffer"):
        ds.buffer = []
        for i in meta["dataset_buffer"]:
            ds.load_image(int(i))  # re-caches ims[i] and re-appends i in the original order
    dl.iterator = dl._get_iterator()  # draws _base_seed from the generator exactly like the continuous run did
    dl.generator.set_state(st["loader_generator_state"])
    if meta["loader_pending_tail"]:
        torch.randperm(len(ds), generator=dl.generator)  # pending RandomSampler tail draw
    ck.restore_rng(st["rng"])  # last: nothing between this hook and the first batch consumes RNG
