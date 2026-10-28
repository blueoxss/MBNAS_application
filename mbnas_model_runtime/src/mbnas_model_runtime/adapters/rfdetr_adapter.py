"""RF-DETR 1.11.2 adapter (``RFDETRNano/Small/Medium/Large``) on the PyTorch-Lightning training stack.

Data: the train/search_val manifests are written as an RF-DETR COCO layout (``train/`` + ``valid/`` with
``_annotations.coco.json``, images symlinked, categories in class-index order) inside the run directory.

Stage semantics (STAGE_TRAINER_AUDIT B.3/B.4, PLAN §8.10): every stage calls ``RFDETR.train(epochs=fixed_horizon,
lr_scheduler="cosine", ...)`` so the per-step ``LambdaLR`` always spans the fixed horizon. A PTL ``Checkpoint``
subclass appended to the trainer (``rfdetr.training.build_trainer`` is wrapped for the duration of the call) runs
after the native ``ModelCheckpoint(last)`` in ``on_train_epoch_end`` and

* copies the native full checkpoint ``last.ckpt`` (FP32 model, EMA callback state + update counter, optimizer,
  LR scheduler, precision plugin/scaler, loop counters, best-model callback) to ``ckpt/native_e{E}.ckpt``;
* writes the MBNAS full state (``mbnas.full_state.v1``) that references that file by sha256 and adds the
  python/numpy/torch/CUDA RNG states, epoch/step/sample counters, LR history and hashes;
* at the rung end writes the FP32 EMA evaluation snapshot and the FP16 weights-only size artifact, and sets
  ``trainer.should_stop`` (also on a pause request).

Housekeeping (after every stage, also a paused one): native files RF-DETR/PTL leave in ``native/`` (``last.ckpt`` —
a duplicate of the copied ``ckpt/native_e{E}.ckpt`` —, ``last.pth``, ``last_ema.pth``, ``checkpoint_best_*.pth``,
``checkpoint_*.ckpt``) are deleted once the sha256-referenced copy of the latest full state verifies; logs
(``metrics.csv``, ``training_config.json``) stay. What was deleted is recorded in ``native/housekeeping.jsonl``, in
``RungResult.resolved_recipe["native_housekeeping"]`` and as a progress ``log`` event.

Resume = ``train(resume=<native ckpt>)`` (restores everything PTL saves) + MBNAS RNG overlay at the first resumed
``on_train_epoch_start`` (after PTL's ``seed_everything`` in ``on_fit_start``) + the train sampler order: the train
DataLoader built by ``RFDETRDataModule.train_dataloader`` gets an explicit ``torch.Generator`` (seeded from the training
seed on a fresh start, restored from ``loader_generator_state`` on resume; :func:`_seeded_train_loader`), saved in every
full state. ``full_state_resume`` stays declared ``PARTIAL``: PTL itself restores no RNG/sampler state (MBNAS does) and
runs with ``deterministic=False``, so weights match within a tolerance only; whether a staged run equals a continuous
one is an empirical question answered by the evidence file, never assumed. The native checkpoint is a pickle
(``torch.load(weights_only=False)``): resuming from it needs ``MBNAS_RUNNER_ALLOW_PICKLE_RESUME=1`` on the executing
host (DD-A20; the owner worker sets it), otherwise the resume is ``ResumeBlocked("PICKLE_RESUME_DISABLED")`` and an
RF-DETR full-state resume is not available there.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from mbnas_model_runtime import checkpoint as ck
from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.time import utcnow_iso
from mbnas_model_runtime.adapters.base import ArchSpec, BuildReport, CapabilityError, ResumeBlocked, RungResult, TrainContext, check_init
from mbnas_model_runtime.adapters.base import default_weights_dir
from mbnas_model_runtime.adapters.common import count_modules, emit, env_snapshot, recipe_hash, structural_hash, verify_weights

RFDETR_VERSION = "1.11.2"
INIT_MODES = ("scratch", "pretrained_native")
REQUIRED_STATE = ("native_resume_ckpt", "native_resume_ckpt.path", "native_resume_ckpt.sha256", "adapter_meta",
                  "adapter_meta.samples_seen", "loader_generator_state")
NATIVE_CKPT_KEYS = ("state_dict", "optimizer_states", "lr_schedulers", "loops", "callbacks", "epoch", "global_step")
EVAL_DEFAULTS = {"conf": 0.001, "max_det": 300}
#: environment switch for unpickling the native PTL checkpoint on resume (WP-1 contract §5 ``allow_pickle_resume``,
#: DD-A20); read here because model_runtime does not import the runner's settings. Default: disabled.
PICKLE_RESUME_ENV = "MBNAS_RUNNER_ALLOW_PICKLE_RESUME"
_ENABLED_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
NATIVE_PAYLOAD_GLOBS = ("*.ckpt", "*.pth")  # PTL/RF-DETR checkpoints in native/ (logs are kept)
HOUSEKEEPING_LOG = "housekeeping.jsonl"

# Static preset table for ``declared_config`` (DD-41): the defaults of ``rfdetr.config.RFDETR{Nano,Small,Medium,Large}Config`` in
# rfdetr 1.11.2 for the fields in ``RfdetrAdapter._CONFIG_KEYS`` (values as observed in docs/integration_20261021/inv_I5_adapters.md
# section 2.3 for Nano and in the WP-S8 extraction from the installed package for all four presets; ``num_classes`` is the library
# default 90, a placeholder until a dataset fixes it). ``tests/api/test_registry_structure.py::test_rfdetr_preset_table_matches_installed_library``
# compares this table with the installed package (in a CUDA-hidden subprocess) so a library upgrade cannot silently drift from it.
_RFDETR_COMMON = {"encoder": "dinov2_windowed_small", "out_feature_indexes": (3, 6, 9, 12), "projector_scale": ("P4",),
                  "hidden_dim": 256, "patch_size": 16, "num_windows": 2, "sa_nheads": 8, "ca_nheads": 16, "dec_n_points": 2,
                  "num_queries": 300, "two_stage": True, "bbox_reparam": True, "layer_norm": True, "group_detr": 13,
                  "num_classes": 90}
RFDETR_PRESETS: dict[str, dict[str, Any]] = {
    name: {**_RFDETR_COMMON, "dec_layers": dec, "resolution": res, "positional_encoding_size": pe}
    for name, dec, res, pe in (("RFDETRNano", 2, 384, 24), ("RFDETRSmall", 3, 512, 32), ("RFDETRMedium", 4, 576, 36),
                               ("RFDETRLarge", 4, 704, 44))
}


def default_rf_home(weights_dir: str | os.PathLike[str] | None = None) -> str:
    """Default ``RF_HOME`` (rfdetr's weights directory): ``<weights_dir>/rfdetr``, with ``weights_dir`` defaulting to
    ``$MBNAS_HOME/weights`` (DD-A13; replaces the former host-fixed constant). Used with ``os.environ.setdefault``, so an
    ``RF_HOME`` set by the caller (the runner sets it from its configured ``weights_dir``) always wins."""
    return str(Path(default_weights_dir() if weights_dir is None else weights_dir) / "rfdetr")


def pickle_resume_allowed() -> bool:
    """True when :data:`PICKLE_RESUME_ENV` is ``1``/``true``/``t``/``yes``/``y``/``on`` (any case: the values pydantic
    settings read as true); unset, empty or any other value keeps the native (pickle) resume disabled."""
    return os.environ.get(PICKLE_RESUME_ENV, "").strip().lower() in _ENABLED_VALUES


def native_housekeeping(out_dir: Path, referenced: Mapping[str, Any] | None) -> dict[str, Any]:
    """Delete native checkpoint files in ``out_dir`` that are not the referenced resume source.

    ``referenced`` is the ``native_resume_ckpt`` of the latest MBNAS full state (``{path, sha256}``, a copy under
    ``ckpt/``). Nothing is deleted unless that file exists and its sha256 matches (the only resume source must never
    be lost); a file whose resolved path *is* the referenced one is always kept. Returns the record that is also
    appended to ``out_dir/housekeeping.jsonl``.
    """
    record: dict[str, Any] = {"at": utcnow_iso(), "out_dir": str(out_dir), "deleted": [], "kept_reference": None,
                              "skipped_reason": None}
    if not out_dir.is_dir():
        record["skipped_reason"] = "native out_dir missing"
        return record
    ref = Path(str((referenced or {}).get("path") or ""))
    if not referenced or not ref.is_file() or sha256_file(ref) != referenced.get("sha256"):
        record["skipped_reason"] = "referenced native checkpoint missing or sha256 mismatch: nothing deleted"
    else:
        record["kept_reference"] = {"path": str(ref), "sha256": referenced["sha256"]}
        keep = os.path.realpath(ref)
        for pattern in NATIVE_PAYLOAD_GLOBS:
            for f in sorted(out_dir.glob(pattern)):
                if not f.is_file() or os.path.realpath(f) == keep:
                    continue
                size = f.stat().st_size
                f.unlink()
                record["deleted"].append({"path": str(f), "bytes": size})
    record["bytes_freed"] = sum(d["bytes"] for d in record["deleted"])
    with open(out_dir / HOUSEKEEPING_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


@contextmanager
def _seeded_train_loader(generator: Any, record: dict[str, Any]) -> Iterator[None]:
    """Temporarily wrap ``RFDETRDataModule.train_dataloader`` so the train loader draws from ``generator``.

    RF-DETR builds its train DataLoader without a generator, so its ``RandomSampler`` (shuffle or the small-dataset
    replacement sampler) and the per-iterator base seed come from the global torch RNG. Attaching one explicit
    ``torch.Generator`` (seeded from the training seed, or restored from the full state's
    ``loader_generator_state``) makes the sampler order a saved/restored stream of its own. ``record`` receives what
    was attached (sampler class, whether the generator is used) for the full state's ``adapter_meta``.
    """
    import torch
    from rfdetr.training import module_data

    cls = module_data.RFDETRDataModule
    original = cls.train_dataloader

    def patched(self: Any) -> Any:
        loader = original(self)
        sampler = getattr(loader, "sampler", None)
        attached = isinstance(sampler, torch.utils.data.RandomSampler)
        if not attached:  # e.g. the WebDataset streaming loader: refuse rather than train with an unsaved order
            raise CapabilityError("SAMPLER_GENERATOR_UNSUPPORTED", f"RF-DETR train sampler {type(sampler).__name__} "
                                  "cannot take the MBNAS generator: sampler order would not be continued")
        sampler.generator = generator  # RandomSampler.__iter__ draws its permutation/replacement indices from it
        loader.generator = generator  # _BaseDataLoaderIter base seed (worker seeds) from the same stream
        record.update({"sampler": type(sampler).__name__ if sampler is not None else None,
                       "sampler_generator_attached": attached, "loader_generator_attached": True})
        return loader

    cls.train_dataloader = patched
    try:
        yield
    finally:
        cls.train_dataloader = original


@contextmanager
def _extra_callbacks(callbacks: list[Any]) -> Iterator[None]:
    """Temporarily wrap ``rfdetr.training.build_trainer`` so the built Trainer also runs ``callbacks``."""
    import rfdetr.training as rtraining

    original = rtraining.build_trainer

    def patched(config: Any, model_config: Any, **kwargs: Any) -> Any:
        trainer = original(config, model_config, **kwargs)
        trainer.callbacks.extend(callbacks)  # PTL re-orders Checkpoint subclasses last when fit() starts
        return trainer

    rtraining.build_trainer = patched
    try:
        yield
    finally:
        rtraining.build_trainer = original


class RfdetrAdapter:
    backend = "rfdetr"
    family = "Transformer"

    def __init__(self, class_name: str) -> None:
        if class_name not in ("RFDETRNano", "RFDETRSmall", "RFDETRMedium", "RFDETRLarge"):
            raise ValueError(f"unsupported RF-DETR class {class_name!r}")
        self.class_name = class_name

    def capability(self) -> dict[str, Any]:
        return {
            "backend": self.backend, "version": RFDETR_VERSION, "train": True, "val": True,
            "full_state_resume": "PARTIAL",
            "full_state_resume_detail": (
                "restored natively by PTL from last.ckpt: FP32 model, EMA weights + update counter, optimizer, LR "
                "scheduler (per-step LambdaLR), precision plugin, loop counters (epoch/global_step), callback states. "
                "NOT restored by PTL: RNG and sampler/data order (seed_everything re-seeds at on_fit_start); MBNAS "
                "restores both: python/numpy/torch/CUDA RNG overlay at the first resumed epoch, and the train sampler "
                "draws from an explicit MBNAS torch.Generator whose state is saved per epoch "
                "(loader_generator_state), so the sampler order is continued. PTL deterministic=False (CUDA kernels), "
                "so weights are compared with a tolerance and bitwise equality is not claimed (see evidence)"),
            "init_modes": list(INIT_MODES), "native_recipe_init": "pretrained_native",
            "native_loss": "rfdetr SetCriterion (IA-BCE + L1 + GIoU, HungarianMatcher, group_detr 13)",
            "precisions": ["FP32", "FP16"], "train_precisions": ["amp_fp16_fp32"],
            "determinism_profile": "ptl_nondeterministic_rng_overlay", "gradient_accumulation": "must_be_1",
            "input_size_rule": {"kind": "stride_ceil", "stride": 32, "square": True},
            "export_onnx": True, "export_trt": "via benchmark module", "inference_head": "native_no_nms",
        }

    def required_state_keys(self) -> tuple[str, ...]:
        return REQUIRED_STATE

    def _cls(self) -> Any:
        import rfdetr

        return getattr(rfdetr, self.class_name)

    def _instance(self, *, num_classes: int, resolution: int | None, weights: str | None, device: str) -> Any:
        kwargs: dict[str, Any] = {"pretrain_weights": weights, "num_classes": int(num_classes), "device": device}
        if resolution is not None:
            kwargs["resolution"] = int(resolution)
        return self._cls()(**kwargs)

    def build(self, arch: ArchSpec, num_classes: int, init: str, seed: int) -> BuildReport:
        import torch

        check_init(init, INIT_MODES, arch.model_id)
        torch.manual_seed(seed)
        weights = None
        if init == "pretrained_native":
            verify_weights(arch.weights_path, arch.weights_sha256, arch.model_id)
            weights = arch.weights_path
        m = self._instance(num_classes=num_classes, resolution=None, weights=weights, device="cpu")
        net = m.model.model
        return BuildReport(arch.model_id, self.backend, int(sum(p.numel() for p in net.parameters())),
                           structural_hash(net), init, count_modules(net), [f"resolution {m.model_config.resolution}"])

    # -- structure (mbnas-structure/2, native_modules) --------------------------------------------------------------
    _CONFIG_KEYS = ("encoder", "out_feature_indexes", "projector_scale", "dec_layers", "hidden_dim", "patch_size",
                    "num_windows", "sa_nheads", "ca_nheads", "dec_n_points", "num_queries", "resolution",
                    "positional_encoding_size", "two_stage", "bbox_reparam", "layer_norm", "group_detr", "num_classes")

    def declared_config(self) -> dict[str, Any]:
        """Declared model configuration (preset defaults) from the static :data:`RFDETR_PRESETS` table.

        Never imports ``rfdetr``: importing ``rfdetr.config`` probes the accelerator (``torch.accelerator.is_available`` ->
        CUDA lazy init) and so would initialise CUDA on a ``GET /registry/{id}/structure`` (DD-41; the GET is declaration only).
        ``rfdetr`` is imported only by :meth:`describe_structure` / :meth:`build` (explicit checks, training).
        """
        out: dict[str, Any] = {"class_name": self.class_name}
        preset = RFDETR_PRESETS[self.class_name]
        for key in self._CONFIG_KEYS:
            val = preset[key]
            out[key] = list(val) if isinstance(val, (list, tuple)) else val
        return out

    def describe_structure(self, arch: ArchSpec, num_classes: int, input_size: int | None = None) -> dict[str, Any]:
        """Explicit CPU structure observation (scratch init, no weights): build once, hook one zeros forward."""
        import torch

        from mbnas_model_runtime.adapters.structure_native import observe_native_structure, unavailable_document

        config = self.declared_config()
        try:
            torch.manual_seed(0)
            m = self._instance(num_classes=num_classes, resolution=None, weights=None, device="cpu")
            net = m.model.model
            mc = m.model_config
            config = {"class_name": self.class_name,
                      **{k: (list(v) if isinstance(v, (list, tuple)) else v)
                         for k, v in ((k, getattr(mc, k, None)) for k in self._CONFIG_KEYS)}}
        except Exception as exc:  # noqa: BLE001 - environment problems are UNVERIFIED data, never a crash
            return unavailable_document(self.backend, arch.model_id, config, exc, input_size=input_size)
        return observe_native_structure(self.backend, net, input_size=input_size, model_id=arch.model_id, config=config)

    # -- training -------------------------------------------------------------------------------------------------
    def _train_kwargs(self, ctx: TrainContext, dataset_dir: Path, out_dir: Path) -> dict[str, Any]:
        r = ctx.recipe
        if int(r.get("gradient_accumulation", 1)) != 1:
            raise CapabilityError("ACCUMULATION_RESUME_UNSUPPORTED", "MBNAS RF-DETR stages use grad_accum_steps=1")
        if not r["amp"]:
            raise CapabilityError("PRECISION_UNSUPPORTED", "RF-DETR adapter trains with fp16 mixed precision only")
        shape = [int(v) for v in r["input_shape"]]
        if shape[0] != shape[1] or shape[0] % 32:
            raise CapabilityError("INPUT_SHAPE_UNSUPPORTED", f"RF-DETR needs a square multiple of 32, got {shape}")
        return {"dataset_dir": str(dataset_dir), "output_dir": str(out_dir), "epochs": int(ctx.fixed_horizon),
                "batch_size": int(r["batch"]), "grad_accum_steps": 1, "lr": float(r["lr0"]),
                "lr_encoder": float(r["lr0"]) * 1.5, "weight_decay": float(r["weight_decay"]),
                "warmup_epochs": float(r["warmup_epochs"]), "lr_scheduler": "cosine",
                "lr_scheduler_kwargs": {"min_factor": float(r["lrf"])}, "use_ema": True,
                "num_workers": int(r.get("workers", 0)), "seed": int(ctx.training_seed), "device": ctx.device,
                "tensorboard": False, "progress_bar": None, "checkpoint_interval": int(ctx.fixed_horizon) + 1,
                "resolution": shape[0], "amp_dtype": "fp16", "eval_interval": 1, "run_test": False,
                "early_stopping": False}

    def _layout(self, ctx: TrainContext) -> Path:
        from mbnas_model_runtime.datasets.coco_convert import write_coco_layout

        parent = (ctx.data.view_root.parent if ctx.data.view_root is not None else ctx.candidate_dir / "data")
        return write_coco_layout({"train": ctx.data.train_manifest, "valid": ctx.data.val_manifest}, parent)

    def train_until(self, ctx: TrainContext) -> RungResult:
        import numpy as np
        import torch

        if "RF_HOME" not in os.environ:  # setdefault, without computing the default when RF_HOME is set
            os.environ["RF_HOME"] = default_rf_home()
        check_init(ctx.init, INIT_MODES, ctx.arch.model_id)
        weights = None
        if ctx.init == "pretrained_native":
            verify_weights(ctx.arch.weights_path, ctx.arch.weights_sha256, ctx.arch.model_id)
            weights = ctx.arch.weights_path
        layout = self._layout(ctx)
        out_dir = ctx.candidate_dir / "native"
        kwargs = self._train_kwargs(ctx, layout, out_dir)
        nc = len(ctx.data.names)
        probe_net = self._instance(num_classes=nc, resolution=kwargs["resolution"], weights=None, device="cpu").model.model
        schedule = {k: v for k, v in kwargs.items() if k not in ("dataset_dir", "output_dir", "device")}
        schedule.update({"init": ctx.init, "class": self.class_name, "weights_sha256": ctx.arch.weights_sha256
                         if weights else None})
        hashes = {"architecture": structural_hash(probe_net), "recipe": recipe_hash(schedule),
                  "protocol": ctx.protocol_hash, **ctx.data.hashes}
        del probe_net
        start, native_resume = 0, None
        if ctx.resume_state is not None:
            ck.check_state(ctx.resume_state, required=REQUIRED_STATE, expect_hashes=hashes)
            native_resume = self._verify_native(ctx.resume_state["native_resume_ckpt"])
            start = int(ctx.resume_state["epochs_done"])
        if start >= ctx.cumulative_target:
            raise ValueError(f"resume state already has {start} epochs >= target {ctx.cumulative_target}")
        seed = int(ctx.training_seed)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        model = self._instance(num_classes=nc, resolution=kwargs["resolution"], weights=weights, device=ctx.device)
        generator = torch.Generator()
        if ctx.resume_state is not None:
            generator.set_state(ctx.resume_state["loader_generator_state"])  # sampler order continues
        else:
            generator.manual_seed(seed)
        loader_record: dict[str, Any] = {}
        cb = _make_callback(self, ctx, hashes, out_dir, start, generator, loader_record)
        t0 = time.time()
        with _extra_callbacks([cb]), _seeded_train_loader(generator, loader_record):
            model.train(**kwargs, resume=native_resume)
        result = cb.result(kwargs, time.time() - t0)
        record = native_housekeeping(out_dir, cb.native_reference())
        result.resolved_recipe["native_housekeeping"] = record
        result.notes.append(f"native housekeeping: deleted {len(record['deleted'])} file(s), "
                            f"{record['bytes_freed']} bytes; kept {record['kept_reference']}"
                            + (f" ({record['skipped_reason']})" if record["skipped_reason"] else ""))
        emit(ctx.progress, {"type": "log", "message": "rfdetr native housekeeping", "housekeeping": record})
        return result

    def _verify_native(self, ref: Mapping[str, Any]) -> str:
        path = Path(ref["path"])
        if not path.is_file():
            raise ResumeBlocked("MISSING_STATE:native_resume_ckpt", f"{path} is missing")
        if sha256_file(path) != ref["sha256"]:
            raise ResumeBlocked("CHECKSUM_MISMATCH", f"native checkpoint {path.name} changed")
        if not pickle_resume_allowed():
            raise ResumeBlocked("PICKLE_RESUME_DISABLED",
                                f"resuming unpickles the native checkpoint {path.name} (torch.load weights_only=False); "
                                f"set {PICKLE_RESUME_ENV}=1 on this host to allow it")
        import torch

        native = torch.load(path, map_location="cpu", weights_only=False)
        for key in NATIVE_CKPT_KEYS:
            if native.get(key) is None:
                raise ResumeBlocked(f"MISSING_STATE:native.{key}", "PTL checkpoint is not a full training state")
        if "RFDETREMACallback" not in str(list(native["callbacks"].keys())):
            raise ResumeBlocked("MISSING_STATE:native.callbacks.RFDETREMACallback", "EMA state missing")
        return str(path)

    # -- evaluation -----------------------------------------------------------------------------------------------
    def load_eval_model(self, eval_ckpt: str | Path, device: str) -> Any:
        snap = ck.load_eval_snapshot(eval_ckpt)
        meta = snap["model_meta"]
        m = self._instance(num_classes=int(meta["num_classes"]), resolution=int(meta["resolution"]), weights=None,
                           device=device)
        m.model.model.load_state_dict(snap["state_dict"], strict=True)
        m.model.model.to(device).eval()
        m.model.class_names = [meta["names"][str(i)] for i in range(len(meta["names"]))]
        m.mbnas_meta = {**meta, "eval_ckpt": str(eval_ckpt)}
        return m

    def predict(self, model: Any, images: list[tuple[str, str]], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
        import torch

        cfg = {**EVAL_DEFAULTS, **cfg}
        fp16 = cfg.get("precision", "FP16") == "FP16" and str(model.model.device).startswith("cuda")
        bs = int(cfg.get("batch", 8))
        out: list[dict[str, Any]] = []
        for i in range(0, len(images), bs):
            chunk = images[i:i + bs]
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                dets = model.predict([p for _, p in chunk], threshold=float(cfg["conf"]), include_source_image=False)
            for (sid, _), d in zip(chunk, dets):
                order = d.confidence.argsort()[::-1][: int(cfg["max_det"])]
                out.append({"sample_id": sid, "boxes_xyxy": d.xyxy[order].astype(float).tolist(),
                            "scores": d.confidence[order].astype(float).tolist(),
                            "classes": [int(c) for c in d.class_id[order].tolist()]})
        return out

    def latency_fns(self, model: Any, shape: tuple[int, int], precision: str, device: str,
                    sample_image: str, postprocess: Mapping[str, Any] | None = None) -> dict[str, Callable[[], Any]]:
        """Scopes per batch-1 call; ``postprocess`` = operating point ``{conf, iou, max_det}``: the native top-k
        decode keeps detections with score > conf (at most max_det); ``iou`` does not apply (no NMS)."""
        import numpy as np
        import torch
        import torchvision.transforms.functional as F
        from PIL import Image

        pp = {**EVAL_DEFAULTS, **{k: v for k, v in (postprocess or {}).items() if k in ("conf", "max_det")}}
        conf, max_det = float(pp["conf"]), int(pp["max_det"])

        def select(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for r in results:  # top-k output is already sorted by score (descending)
                idx = torch.nonzero(r["scores"] > conf).flatten()[:max_det]
                out.append({k: v[idx] for k, v in r.items()})
            return out

        with Image.open(sample_image) as loaded:
            rgb = np.array(loaded.convert("RGB"))
        res = int(model.model.resolution)
        net, post = model.model.model, model.model.postprocess
        fp16 = precision == "FP16"
        sizes = torch.tensor([[rgb.shape[0], rgb.shape[1]]], device=device)

        def prep() -> Any:
            t = torch.from_numpy(rgb).to(device).permute(2, 0, 1).float() / 255.0
            t = F.resize(t, [res, res], antialias=False)
            return F.normalize(t, model.means, model.stds)[None]

        x = prep()

        def as_dict(o: Any) -> Any:
            return {"pred_logits": o[1], "pred_boxes": o[0]} if isinstance(o, tuple) else o

        def run(fn: Callable[[], Any]) -> Callable[[], Any]:
            def call() -> Any:
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
                    return fn()
            return call

        return {"forward_only": run(lambda: net(x)),
                "forward_and_postprocess": run(lambda: select(post(as_dict(net(x)), target_sizes=sizes))),
                "end_to_end": run(lambda: select(post(as_dict(net(prep())), target_sizes=sizes)))}

    def postprocess_description(self, model: Any) -> dict[str, Any]:
        return {"head": "top-k (num_select=300) sigmoid(query x class), no NMS", "nms": False, "nms_impl": None,
                **EVAL_DEFAULTS,
                "preprocess": "resize to square resolution (no letterbox), ImageNet normalisation"}

    def weights_only(self, eval_ckpt: str | Path, path: str | Path) -> str:
        return ck.write_weights_fp16(ck.load_eval_snapshot(eval_ckpt)["state_dict"], path)["sha256"]


def _make_callback(adapter: RfdetrAdapter, ctx: TrainContext, hashes: dict[str, str], out_dir: Path,
                   start: int, generator: Any, loader_record: Mapping[str, Any]) -> Any:
    """Build the PTL ``Checkpoint`` subclass instance (defined lazily: pytorch_lightning imports are heavy)."""
    from pytorch_lightning.callbacks import Checkpoint

    class MbnasStageCallback(Checkpoint):
        """Runs after the native ModelCheckpoint callbacks (PTL orders Checkpoint subclasses last)."""

        def __init__(self) -> None:
            super().__init__()
            prev = ctx.resume_state or {}
            self.lr_history: list[dict[str, Any]] = [dict(h) for h in prev.get("lr_history", [])]
            self.samples = int(prev.get("adapter_meta", {}).get("samples_seen", 0))
            self.pending_rng = prev.get("rng")
            self.checkpoints: list[dict[str, Any]] = []
            self.last: tuple[Path, dict[str, Any]] | None = None
            self.rung: dict[str, Any] = {}
            self.paused = False
            self.ckpt_dir = ctx.candidate_dir / "ckpt"
            self.rng_restored_at: int | None = None
            self.native_sha: dict[str, str] = {}

        def on_train_epoch_start(self, trainer: Any, pl_module: Any) -> None:
            if self.pending_rng is not None:
                ck.restore_rng(self.pending_rng)  # after PTL's on_fit_start seed_everything, before the iterator
                self.rng_restored_at = int(trainer.current_epoch)
                self.pending_rng = None

        def on_train_batch_end(self, trainer: Any, pl_module: Any, outputs: Any, batch: Any, batch_idx: int) -> None:
            targets = batch[1] if isinstance(batch, (list, tuple)) and len(batch) > 1 else None
            self.samples += len(targets) if hasattr(targets, "__len__") else int(trainer.train_dataloader.batch_size)

        def on_train_epoch_end(self, trainer: Any, pl_module: Any) -> None:
            epoch = int(trainer.current_epoch)
            done = epoch + 1
            self.lr_history.append({"epoch": epoch, "lr": [float(g["lr"]) for g in trainer.optimizers[0].param_groups],
                                    "global_step": int(trainer.global_step)})
            native_src = out_dir / "last.ckpt"
            if not native_src.is_file():
                raise RuntimeError(f"native last.ckpt missing after epoch {epoch}")
            native_dst = self.ckpt_dir / f"native_e{done:04d}.ckpt"
            self.ckpt_dir.mkdir(parents=True, exist_ok=True)
            tmp = native_dst.with_suffix(".ckpt.tmp")
            shutil.copyfile(native_src, tmp)
            os.replace(tmp, native_dst)
            native_ref = {"path": str(native_dst), "sha256": sha256_file(native_dst),
                          "bytes": native_dst.stat().st_size}
            self.native_sha[str(native_dst)] = native_ref["sha256"]
            state = {"format": ck.FORMAT, "epoch": epoch, "epochs_done": done, "global_step": int(trainer.global_step),
                     "samples_seen": self.samples, "rng": ck.capture_rng(), "hashes": dict(hashes),
                     "loader_generator_state": generator.get_state().clone(),
                     "lr_history": [dict(h) for h in self.lr_history], "native_resume_ckpt": native_ref,
                     "adapter": {"backend": adapter.backend, "class": adapter.class_name,
                                 "model_id": ctx.arch.model_id, "version": RFDETR_VERSION},
                     "adapter_meta": {"samples_seen": self.samples, "rng_restored_at_epoch": self.rng_restored_at,
                                      "train_loader": dict(loader_record)},
                     "env": env_snapshot(ctx.device)}
            is_end = done == ctx.cumulative_target
            path = ck.full_state_path(self.ckpt_dir, done)
            side = ck.save_full_state(path, state, rung_end=is_end)
            self.last = (path, side)
            self._row("training_full", done, path, side)
            if is_end:
                self._rung_end(trainer, pl_module, done)
            ck.prune(self.ckpt_dir)
            self._prune_native()
            emit(ctx.progress, {"type": "epoch", "epoch": epoch, "epochs_done": done,
                                "epoch_target_total": ctx.cumulative_target, "global_step": int(trainer.global_step),
                                "samples_seen": self.samples, "lr": self.lr_history[-1]["lr"][0]})
            if is_end:
                trainer.should_stop = True
            elif ctx.stop_requested is not None and ctx.stop_requested():
                trainer.should_stop = True
                self.paused = True

        def native_reference(self) -> dict[str, Any] | None:
            """``native_resume_ckpt`` reference ({path, sha256}) of the latest full state written by this stage."""
            if self.last is None:
                return None
            path = ck.full_state_path(self.ckpt_dir, int(self.last[1]["epochs_done"]))
            native = self.ckpt_dir / f"native_e{int(self.last[1]['epochs_done']):04d}.ckpt"
            return {"path": str(native), "sha256": self.native_sha.get(str(native)), "full_state": str(path)}

        def _prune_native(self) -> None:
            """Delete native PTL copies whose MBNAS full state was pruned (``native_e{E}`` <-> ``full_e{E}``)."""
            live = {f"native_e{e:04d}.ckpt" for e, _ in ck.list_full_states(self.ckpt_dir)}
            for p in self.ckpt_dir.glob("native_e*.ckpt"):
                if p.name not in live:
                    p.unlink()

        def _rung_end(self, trainer: Any, pl_module: Any, done: int) -> None:
            ema_cb = next((c for c in trainer.callbacks if type(c).__name__ == "RFDETREMACallback"), None)
            avg = getattr(ema_cb, "_average_model", None)
            net = avg.module.model if avg is not None else pl_module.model
            meta = {"class_name": adapter.class_name, "num_classes": len(ctx.data.names),
                    "resolution": int(ctx.recipe["input_shape"][0]),
                    "names": {str(k): v for k, v in ctx.data.names.items()},
                    "weights": "ema" if avg is not None else "model"}
            eval_path = ck.eval_snapshot_path(self.ckpt_dir, done)
            eside = ck.save_eval_snapshot(eval_path, net.state_dict(), model_meta=meta, epochs_done=done, hashes=hashes)
            self._row("eval_ema", done, eval_path, eside)
            w = ck.write_weights_fp16(net.state_dict(), ck.weights_only_path(self.ckpt_dir, done))
            self._row("weights_only", done, Path(w["path"]), w)
            self.rung = {"eval": (str(eval_path), eside), "weights": w}

        def _row(self, kind: str, epoch: int, path: Path, side: Mapping[str, Any]) -> None:
            row = {"kind": kind, "epoch": epoch, "path": str(path), "sha256": side["sha256"],
                   "bytes": int(side["bytes"]), "hashes": dict(hashes), "rung": ctx.rung}
            self.checkpoints.append(row)
            emit(ctx.progress, {"type": "checkpoint", **row})

        def result(self, kwargs: Mapping[str, Any], seconds: float) -> RungResult:
            if self.last is None:
                raise RuntimeError("RF-DETR stage finished without an epoch checkpoint")
            path, side = self.last
            done = int(side["epochs_done"])
            reached = done == ctx.cumulative_target
            rung = self.rung
            return RungResult(
                candidate_id=ctx.candidate_id, epochs_done=done, cumulative_target=ctx.cumulative_target,
                fixed_horizon=ctx.fixed_horizon, stopped_reason="TARGET_REACHED" if reached else "PAUSED_AT_EPOCH",
                rung_end_training_ckpt=str(path) if reached else None,
                rung_end_ema_eval_ckpt=rung["eval"][0] if rung else None,
                weights_only_path=rung["weights"]["path"] if rung else None,
                size_mb=rung["weights"]["size_mb"] if rung else None, size_dtype="float16" if rung else None,
                lr_history=self.lr_history, step_count=int(side["global_step"]), samples_seen=self.samples,
                checkpoint_sha256={"training_full": side["sha256"],
                                   **({"eval_ema": rung["eval"][1]["sha256"], "weights_only": rung["weights"]["sha256"]}
                                      if rung else {})},
                resumed_from=ctx.resume_path,
                resolved_recipe={**{k: v for k, v in kwargs.items() if k not in ("dataset_dir", "output_dir")},
                                 "class": adapter.class_name, "rng_restored_at_epoch": self.rng_restored_at},
                hashes=dict(hashes), checkpoints=self.checkpoints, timings={"stage_wall_s": round(seconds, 3)},
                notes=["full_state_resume PARTIAL: PTL restores no RNG/sampler; MBNAS RNG overlay + sampler generator "
                       "state applied (PTL deterministic=False: weights within tolerance, not bitwise)"])

    return MbnasStageCallback()
