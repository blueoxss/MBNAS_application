"""Opt-in mixed-operator integration. Importing this module does not import Torch."""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import pickle
import sys
from pathlib import Path

CUSTOM_FEATURES = {"NASWindowTransformer", "NASGlobalTransformer", "NASPatchMerge"}
CUSTOM_REPEATS = {"NASWindowTransformer", "NASGlobalTransformer"}
SAFE_NATIVE_REPEATS = {"C1", "C2", "C2f", "C3", "C3k2", "BottleneckCSP", "C3Ghost", "C3x", "RepC3"}


def extension_revision():
    """Content identity, independent of device and Python bytecode version."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in ("mixed_blocks.py", "mixed_search_space.py", "mixed_hooks.py", "mixed_cli.py"):
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"Incomplete mixed-operator installation: {path}")
        digest.update(name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _enabled():
    from .mixed_search_space import mixed_enabled
    return mixed_enabled()


def _operator_rate():
    try:
        rate = float(os.environ.get("ETRI_NAS_OPERATOR_RATE", "0.35"))
    except ValueError as exc:
        raise ValueError("ETRI_NAS_OPERATOR_RATE must be a finite number in [0, 1]") from exc
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("ETRI_NAS_OPERATOR_RATE must be a finite number in [0, 1]")
    return rate


def bootstrap_training_process():
    """Also required before external YOLO(checkpoint) loading of custom models."""
    if _enabled():
        from .mixed_blocks import register_ultralytics_blocks
        register_ultralytics_blocks()


def _revision_identity(original):
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        base = original(*args, **kwargs)
        if not _enabled():
            return base
        return hashlib.sha256(f"{base}:{extension_revision()}".encode()).hexdigest()[:len(base)]
    wrapped._etri_mixed_hook = True
    return wrapped


def install_runtime_hooks():
    """Install once after yolo_ga_runtime has finished importing its public API."""
    from . import runtime as runtime
    from .crossover_v22 import Crossover, NASUtils
    if getattr(runtime, "_etri_mixed_hooks_installed", False):
        return
    runtime.FEATURE_MODULES.update(CUSTOM_FEATURES | {"A2C2f"})
    runtime.REPEAT_MODULES.update(CUSTOM_REPEATS | {"A2C2f"})
    runtime.MODULE_FAMILY.update({
        "NASWindowTransformer": "custom_window_transformer",
        "NASGlobalTransformer": "custom_global_transformer",
        "NASPatchMerge": "custom_linear_patch_merge", "A2C2f": "v12",
    })

    original_hw = runtime._module_out_hw
    @functools.wraps(original_hw)
    def output_hw(name, args, input_hw):
        if str(name).split(".")[-1] == "NASPatchMerge":
            stride = args[1] if len(args) > 1 else 2
            if isinstance(stride, bool) or not isinstance(stride, int) or stride < 1:
                raise ValueError("NASPatchMerge patch size must be a positive integer")
            return tuple((value + stride - 1) // stride for value in input_hw)
        return original_hw(name, args, input_hw)
    runtime._module_out_hw = output_hw

    original_stride = NASUtils._conv_stride
    @functools.wraps(original_stride)
    def conv_stride(line):
        if isinstance(line, list) and len(line) >= 3 and str(line[2]).split(".")[-1] in CUSTOM_REPEATS:
            return 1  # heads/window/MLP arguments never represent a spatial stride.
        if isinstance(line, list) and len(line) >= 3 and str(line[2]).split(".")[-1] == "NASPatchMerge":
            args = NASUtils._args(line) or []
            stride = args[1] if len(args) > 1 else 2
            if isinstance(stride, bool) or not isinstance(stride, int) or stride < 1:
                raise ValueError("NASPatchMerge patch size must be a positive integer")
            return stride
        return original_stride(line)
    NASUtils._conv_stride = staticmethod(conv_stride)

    original_down = NASUtils._is_down_event
    @functools.wraps(original_down)
    def down_event(line):
        if isinstance(line, list) and len(line) >= 3 and str(line[2]).split(".")[-1] == "NASPatchMerge":
            return conv_stride(line) > 1
        return original_down(line)
    NASUtils._is_down_event = staticmethod(down_event)

    original_feature = Crossover._is_feature_module
    @functools.wraps(original_feature)
    def feature_module(line):
        return (isinstance(line, list) and len(line) >= 3 and
                str(line[2]).split(".")[-1] in CUSTOM_FEATURES) or original_feature(line)
    Crossover._is_feature_module = staticmethod(feature_module)

    original_channels = Crossover._guess_out_channels
    @functools.wraps(original_channels)
    def guess_channels(self, line, last_channels):
        if isinstance(line, list) and len(line) >= 4 and str(line[2]).split(".")[-1] in CUSTOM_FEATURES:
            args = line[3]
            if isinstance(args, list) and args and isinstance(args[0], int) and not isinstance(args[0], bool) and args[0] > 0:
                return args[0]
        return original_channels(self, line, last_channels)
    Crossover._guess_out_channels = guess_channels

    original_executor_init = runtime.TorchExecutor.__init__
    @functools.wraps(original_executor_init)
    def executor_init(self, *args, **kwargs):
        bootstrap_training_process()
        original_executor_init(self, *args, **kwargs)
    runtime.TorchExecutor.__init__ = executor_init

    original_build = runtime.TorchExecutor.build
    @functools.wraps(original_build)
    def build(self, *args, **kwargs):
        model = original_build(self, *args, **kwargs)
        if _enabled():
            from .mixed_blocks import audit_model
            model._etri_mixed_operator_audit = audit_model(model)
            model._etri_mixed_extension_revision = extension_revision()
        return model
    runtime.TorchExecutor.build = build

    original_sampler_init = runtime.CompatibilityAwareSampler.__init__
    @functools.wraps(original_sampler_init)
    def sampler_init(self, *args, **kwargs):
        original_sampler_init(self, *args, **kwargs)
        if _enabled() and self.mode == "pure_random":
            self._op_catalog = [name for name in self._op_catalog
                                if name.split(".")[-1] in SAFE_NATIVE_REPEATS | {"NASWindowTransformer"}]
    runtime.CompatibilityAwareSampler.__init__ = sampler_init

    original_sample = runtime.CompatibilityAwareSampler.sample
    @functools.wraps(original_sample)
    def sample(self, *args, **kwargs):
        config, parents, repairs = original_sample(self, *args, **kwargs)
        if _enabled():
            from .mixed_search_space import mutate_operators
            config, changes = mutate_operators(config, self.parent_rng, rate=_operator_rate(), all_slots=True)
            repairs = list(repairs) + list(changes)
        return config, parents, repairs
    runtime.CompatibilityAwareSampler.sample = sample
    fingerprint = _revision_identity(runtime.architecture_fingerprint)
    runtime.architecture_fingerprint = fingerprint
    package = sys.modules.get(__package__)
    if package is not None:
        package.architecture_fingerprint = fingerprint
    pipeline = sys.modules.get(f"{__package__}.pipeline")
    if pipeline is not None:
        pipeline.architecture_fingerprint = fingerprint
    runtime._etri_mixed_hooks_installed = True


def _tuples(value):
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def install_ga_hooks():
    """Install after yolo_ga_full_runtime imports; Torch stays lazy until execution."""
    from yolo_ga_full_runtime import search, train_eval
    cls = search.MultiObjectiveGASearch
    if getattr(cls, "_etri_mixed_hooks_installed", False):
        return
    hashed = _revision_identity(train_eval.config_hash)
    train_eval.config_hash = hashed
    search.config_hash = hashed
    package = sys.modules.get("yolo_ga_full_runtime")
    if package is not None and hasattr(package, "config_hash"):
        package.config_hash = hashed
    original_mutate = cls._mutate
    @functools.wraps(original_mutate)
    def mutate(self, config):
        config = original_mutate(self, config)
        if _enabled():
            from .mixed_search_space import mutate_operators
            config, repairs = mutate_operators(config, self.rng, rate=1.0, all_slots=False)
            self._etri_last_operator_mutation = repairs
        return config
    cls._mutate = mutate

    original_checkpoint = cls._checkpoint
    @functools.wraps(original_checkpoint)
    def checkpoint(self, generation):
        original_checkpoint(self, generation)
        if _enabled():
            directory = Path(self.cfg.run_dir) / "generations"
            with (directory / "search_state.pkl").open("rb") as handle:
                state = pickle.load(handle)
            _atomic_json(directory / "mixed_sampler_state.json", {
                "extension_revision": extension_revision(),
                "generation": state["gen_index"], "counter": state["counter"],
                "parent_rng": self.sampler.parent_rng.getstate(),
                "global_stream": self.sampler._gstate, "sampler_counter": self.sampler._counter,
            })
    cls._checkpoint = checkpoint

    original_resume = cls.resume
    @functools.wraps(original_resume)
    def resume(self):
        directory = Path(self.cfg.run_dir) / "generations"
        state_path = directory / "search_state.pkl"
        extra = None
        if _enabled() and state_path.is_file():
            sidecar = directory / "mixed_sampler_state.json"
            if not sidecar.is_file():
                raise RuntimeError("Existing checkpoint lacks mixed sampler provenance; use a fresh run_dir")
            extra = json.loads(sidecar.read_text(encoding="utf-8"))
            if extra.get("extension_revision") != extension_revision():
                raise RuntimeError("Mixed extension source changed; use a fresh run_dir")
            with state_path.open("rb") as handle:
                state = pickle.load(handle)
            if (extra.get("generation"), extra.get("counter")) != (state["gen_index"], state["counter"]):
                raise RuntimeError("Mixed sampler/checkpoint commit mismatch; refusing an inconsistent resume")
        resumed = original_resume(self)
        if resumed and extra is not None:
            self.sampler.parent_rng.setstate(_tuples(extra["parent_rng"]))
            self.sampler._gstate = _tuples(extra["global_stream"])
            self.sampler._counter = extra["sampler_counter"]
        return resumed
    cls.resume = resume
    cls._etri_mixed_hooks_installed = True
