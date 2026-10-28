# Derived from Ultralytics 8.4.173 (https://github.com/ultralytics/ultralytics), licensed AGPL-3.0:
#   ultralytics/nn/tasks.py:parse_model of the installed ultralytics==8.4.173 wheel (lines 2030-2243).
# modified: +3 base, +2 repeat (base_modules += NASPatchMerge, NASWindowTransformer, NASGlobalTransformer;
#   repeat_modules += NASWindowTransformer, NASGlobalTransformer), module-global names read through `_tasks`.
# MBNAS WP-1 contract §6.2 / DD-A12. This is a modified copy of AGPL-3.0 code; its license obligations are part of the
# DD-A9 review ("parse_model 복제·수정").
"""Static, exec-free ``parse_model`` for ultralytics 8.4.173 with the three ETRI mixed-pool operators.

The ETRI mixed pool extended ``ultralytics.nn.tasks.parse_model`` by compiling an AST-patched copy of the installed
source with ``exec`` (``etri_mixed_blocks._extend_parser_source``). MBNAS ships the patched function as source instead
(DD-A12): :func:`parse_model` is the installed 8.4.173 function with exactly these differences, each keeping its
behaviour:

* ``base_modules`` gains ``NASPatchMerge, NASWindowTransformer, NASGlobalTransformer`` and ``repeat_modules`` gains
  ``NASWindowTransformer, NASGlobalTransformer``, appended to the set literals as the ETRI AST patch did;
* every module-global name ``X`` of the original is spelled ``_tasks.X``, so it is looked up in
  ``ultralytics.nn.tasks`` at call time, as the original's globals were;
* ``globals()[m]`` is ``_resolve(m)``: one of the three operator classes, else ``vars(_tasks)[m]`` (the original's
  globals mapping, with the same ``KeyError`` for an unknown module name);
* ``eval(act)`` is ``eval(act, vars(_tasks), locals())`` (the original's implicit globals/locals made explicit).

The local variable names are unchanged, so ``locals()``-based YAML argument resolution and frame inspection
(``mbnas_model_runtime.genotype`` reads ``i``/``m`` of the ``parse_model`` frame) behave as before. The copy is only
valid for the source it was made from: ``register_ultralytics_blocks`` installs it only when the installed source
hashes to :data:`EXPECTED_PARSE_MODEL_SHA256` and otherwise raises ``OperatorsUnavailable``.
:func:`verify_static_copy` is the static proof (nothing executed): it applies the ETRI AST patch to the installed
source and compares the result with this function after undoing the three spellings above.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import inspect
import textwrap
import types
from pathlib import Path
from typing import Any

from ultralytics.nn import tasks as _tasks

from mbnas_model_runtime.operators.etri_mixed_blocks import NASGlobalTransformer, NASPatchMerge, NASWindowTransformer

ULTRALYTICS_VERSION = "8.4.173"
#: ``hashlib.sha256(inspect.getsource(ultralytics.nn.tasks.parse_model).encode()).hexdigest()`` of the installed
#: ultralytics 8.4.173, measured with runtime/venv/bin/python on 2026-10-23 (WP-1C); equal to the value derived
#: statically from the wheel in ETRI_RUNTIME_SCOPE §4.2.
EXPECTED_PARSE_MODEL_SHA256 = "7da6bb14102890302fd9c8708a9f7d0fbb38e94e8f511c0434fc41c0fcff5f6d"
#: how the parser is extended: ``static_copy`` (this module); the contract's fallback would be ``ast_patch_exec``
PARSE_MODEL_EXTENSION_MODE = "static_copy"
ADDED_BASE_MODULES: tuple[str, ...] = ("NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer")
ADDED_REPEAT_MODULES: tuple[str, ...] = ("NASWindowTransformer", "NASGlobalTransformer")
_OWN = {cls.__name__: cls for cls in (NASPatchMerge, NASWindowTransformer, NASGlobalTransformer)}


def _resolve(m):
    """``globals()[m]`` of the original: an operator class of this package, else the ``ultralytics.nn.tasks``
    namespace (``KeyError`` for an unknown name, as before)."""
    own = _OWN.get(m)
    return own if own is not None else vars(_tasks)[m]


def parse_model(d, ch, verbose=True):
    """Parse a YOLO model.yaml dictionary into a PyTorch model.

    Args:
        d (dict): Model dictionary.
        ch (int): Input channels.
        verbose (bool): Whether to print model details.

    Returns:
        model (torch.nn.Sequential): PyTorch model.
        save (list): Sorted list of layer indices whose outputs need to be saved.
    """
    import ast

    # Args
    legacy = True  # backward compatibility for v3/v5/v8/v9 models
    max_channels = float("inf")
    nc, act, scales, end2end = (d.get(x) for x in ("nc", "activation", "scales", "end2end"))
    reg_max = d.get("reg_max", 16)
    depth, width, kpt_shape = (d.get(x, 1.0) for x in ("depth_multiple", "width_multiple", "kpt_shape"))
    scale = d.get("scale")
    if scales:
        if not scale:
            scale = next(iter(scales.keys()))
            _tasks.LOGGER.warning(f"no model scale passed. Assuming scale='{scale}'.")
        depth, width, max_channels = scales[scale]

    restricted = _tasks._SafeLoad.restricted()
    default_act = _tasks.Conv.default_act  # restore before returning: Conv.default_act is process-wide state
    if act:
        # redefine default activation, i.e. Conv.default_act = torch.nn.SiLU(). Under restricted loading, resolve the
        # spec without eval() (see _SafeLoad.activation).
        _tasks.Conv.default_act = _tasks._SafeLoad.activation(act) if restricted else eval(act, vars(_tasks), locals())
        if verbose:
            _tasks.LOGGER.info(f"{_tasks.colorstr('activation:')} {act}")  # print

    if verbose:
        _tasks.LOGGER.info(f"\n{'':>3}{'from':>20}{'n':>3}{'params':>10}  {'module':<45}{'arguments':<30}")
    ch = [ch]
    layers, save, c2 = [], [], ch[-1]  # layers, savelist, ch out
    base_modules = frozenset(
        {
            _tasks.Classify,
            _tasks.Conv,
            _tasks.ConvTranspose,
            _tasks.GhostConv,
            _tasks.Bottleneck,
            _tasks.GhostBottleneck,
            _tasks.SPP,
            _tasks.SPPF,
            _tasks.C2fPSA,
            _tasks.C2PSA,
            _tasks.DWConv,
            _tasks.Focus,
            _tasks.BottleneckCSP,
            _tasks.C1,
            _tasks.C2,
            _tasks.C2f,
            _tasks.C3k2,
            _tasks.RepNCSPELAN4,
            _tasks.ELAN1,
            _tasks.ADown,
            _tasks.AConv,
            _tasks.SPPELAN,
            _tasks.C2fAttn,
            _tasks.C3,
            _tasks.C3TR,
            _tasks.C3Ghost,
            _tasks.torch.nn.ConvTranspose2d,
            _tasks.DWConvTranspose2d,
            _tasks.C3x,
            _tasks.RepC3,
            _tasks.PSA,
            _tasks.SCDown,
            _tasks.C2fCIB,
            _tasks.A2C2f,
            NASPatchMerge,
            NASWindowTransformer,
            NASGlobalTransformer,
        }
    )
    repeat_modules = frozenset(  # modules with 'repeat' arguments
        {
            _tasks.BottleneckCSP,
            _tasks.C1,
            _tasks.C2,
            _tasks.C2f,
            _tasks.C3k2,
            _tasks.C2fAttn,
            _tasks.C3,
            _tasks.C3TR,
            _tasks.C3Ghost,
            _tasks.C3x,
            _tasks.RepC3,
            _tasks.C2fPSA,
            _tasks.C2fCIB,
            _tasks.C2PSA,
            _tasks.A2C2f,
            NASWindowTransformer,
            NASGlobalTransformer,
        }
    )
    for i, (f, n, m, args) in enumerate(d["backbone"] + d["head"]):  # from, number, module, args
        m = (
            getattr(_tasks.torch.nn, m[3:])
            if m.startswith("nn.")
            else getattr(__import__("torchvision").ops, m[16:])
            if m.startswith("torchvision.ops.")
            else _resolve(m)
        )  # get module
        if restricted and not (isinstance(m, type) and issubclass(m, _tasks.torch.nn.Module)):
            # Under restricted loading, only known model layers may be named here.
            raise TypeError(_tasks.emojis(f"ERROR ❌️ module '{m}' is not a permitted model layer under restricted loading."))
        for j, a in enumerate(args):
            if isinstance(a, str):
                with _tasks.contextlib.suppress(ValueError):
                    args[j] = locals()[a] if a in locals() else ast.literal_eval(a)
        n = n_ = max(round(n * depth), 1) if n > 1 else n  # depth gain
        if m in base_modules:
            c1, c2 = ch[f], args[0]
            if m is not _tasks.Classify:  # Classify() output must stay at nc; every other layer scales by width
                c2 = _tasks.make_divisible(min(c2, max_channels) * width, 8)
            if m is _tasks.C2fAttn:  # set 1) embed channels and 2) num heads
                args[2] = int(max(round(min(args[2], max_channels // 2 // 32)) * width, 1) if args[2] > 1 else args[2])
                hidden_channels = int(c2 * (args[6] if len(args) > 6 else 0.5))
                if hidden_channels % args[2]:
                    raise ValueError(
                        f"C2fAttn hidden channels {hidden_channels} (from c2={c2}) must be divisible by nh={args[2]}; "
                        "adjust width_multiple, nh, or C2fAttn expansion"
                    )
                args[1] = hidden_channels

            args = [c1, c2, *args[1:]]
            if m in repeat_modules:
                args.insert(2, n)  # number of repeats
                n = 1
            if m is _tasks.C3k2:  # for M/L/X sizes
                legacy = False
                if scale in {"m", "l", "x"}:
                    args[3:4] = [True]  # slice assignment also supplies c3k when the YAML omits it
            if m is _tasks.A2C2f:
                legacy = False
                if scale in {"l", "x"}:  # for L/X sizes
                    args.extend((True, 1.2))
            if m is _tasks.C2fCIB:
                legacy = False
        elif m is _tasks.AIFI:
            args = [ch[f], *args]
        elif m in frozenset({_tasks.HGStem, _tasks.HGBlock}):
            c1, cm, c2 = ch[f], args[0], args[1]
            args = [c1, cm, c2, *args[2:]]
            if m is _tasks.HGBlock:
                args.insert(4, n)  # number of repeats
                n = 1
        elif m is _tasks.ResNetLayer:
            c2 = args[1] if args[3] else args[1] * 4
        elif m is _tasks.torch.nn.BatchNorm2d:
            args = [ch[f]]
        elif m is _tasks.Concat:
            c2 = sum(ch[x] for x in f)
        elif m in frozenset(
            {
                _tasks.Detect,
                _tasks.WorldDetect,
                _tasks.YOLOEDetect,
                _tasks.Segment,
                _tasks.Segment26,
                _tasks.YOLOESegment,
                _tasks.YOLOESegment26,
                _tasks.Pose,
                _tasks.Pose26,
                _tasks.OBB,
                _tasks.OBB26,
            }
        ):
            args.extend([reg_max, end2end, [ch[x] for x in f]])
            if m is _tasks.Segment or m is _tasks.YOLOESegment or m is _tasks.Segment26 or m is _tasks.YOLOESegment26:
                args[2] = _tasks.make_divisible(min(args[2], max_channels) * width, 8)
            if m in {_tasks.Detect, _tasks.YOLOEDetect, _tasks.Segment, _tasks.Segment26, _tasks.YOLOESegment, _tasks.YOLOESegment26, _tasks.Pose, _tasks.Pose26, _tasks.OBB, _tasks.OBB26}:
                m.legacy = legacy
        elif m is _tasks.Depth:
            args = [*args[:1], [ch[x] for x in f]]  # c_mid, ch tuple; drops the legacy mode arg old checkpoints store
        elif m is _tasks.SemanticSegment:
            args.append([ch[x] for x in f])  # nc, ch tuple
        elif m is _tasks.v10Detect:
            args.append([ch[x] for x in f])
        elif m is _tasks.ImagePoolingAttn:
            args.insert(1, [ch[x] for x in f])  # channels as second arg
        elif m is _tasks.RTDETRDecoder:  # special case, channels arg must be passed in index 1
            args.insert(1, [ch[x] for x in f])
        elif m is _tasks.CBLinear:
            c2 = args[0]
            c1 = ch[f]
            args = [c1, c2, *args[1:]]
        elif m is _tasks.CBFuse:
            c2 = ch[f[-1]]
        elif m in frozenset({_tasks.TorchVision, _tasks.Index}):
            c2 = args[0]
            c1 = ch[f]
            args = [*args[1:]]
        else:
            c2 = ch[f]

        m_ = _tasks.torch.nn.Sequential(*(m(*args) for _ in range(n))) if n > 1 else m(*args)  # module
        if m is _tasks.SPPF and len(args) <= 3:  # Legacy YAML rows predate the unactivated YOLO26 SPPF.
            for block in m_ if n > 1 else [m_]:
                block.cv1.act = _tasks.Conv.default_act
        t = str(m)[8:-2].replace("__main__.", "")  # module type
        m_.np = sum(x.numel() for x in m_.parameters())  # number params
        m_.i, m_.f, m_.type = i, f, t  # attach index, 'from' index, type
        if verbose:
            _tasks.LOGGER.info(f"{i:>3}{f!s:>20}{n_:>3}{m_.np:10.0f}  {t:<45}{args!s:<30}")  # print
        save.extend(x % i for x in ([f] if isinstance(f, int) else f) if x != -1)  # append to savelist
        layers.append(m_)
        if i == 0:
            ch = []
        ch.append(c2)
    _tasks.Conv.default_act = default_act
    return _tasks.torch.nn.Sequential(*layers), sorted(save)


def fresh_parse_model() -> types.FunctionType:
    """A new function object running :func:`parse_model`; the registration sets its attributes (``__module__``,
    ``__doc__``, ``_nas_mixed_*``) on this object, never on this module's function, like the fresh function the ETRI
    ``exec`` produced on every installation."""
    fn = types.FunctionType(parse_model.__code__, parse_model.__globals__, parse_model.__name__,
                            parse_model.__defaults__, parse_model.__closure__)
    fn.__qualname__ = parse_model.__qualname__
    fn.__kwdefaults__ = parse_model.__kwdefaults__
    return fn


def source_sha256() -> str:
    """sha256 of this module's installed source file (``replacement_source_sha256`` in the registration
    capabilities); a module that is not a readable ``.py`` file raises ``OperatorsUnavailable``."""
    from mbnas_model_runtime.operators import OperatorsUnavailable

    file = globals().get("__file__")
    if not file or not str(file).endswith(".py") or not Path(file).is_file():
        raise OperatorsUnavailable(f"{__name__} is not installed as a source file ({file!r})")
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def _parse_model_def(source: str | ast.Module) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source)) if isinstance(source, str) else source
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "parse_model"]
    if len(found) != 1:
        raise ValueError("expected exactly one parse_model function")
    return found[0]


def _is_call(node: ast.AST, func: str, *names: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func
            and not node.keywords and [getattr(a, "id", None) for a in node.args] == list(names))


class _UndoSpellings(ast.NodeTransformer):
    """``_tasks.X`` -> ``X``; ``_resolve(m)`` -> ``globals()[m]``; ``eval(a, vars(_tasks), locals())`` -> ``eval(a)``."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if isinstance(node.value, ast.Name) and node.value.id == "_tasks":
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if isinstance(node.func, ast.Name) and node.func.id == "_resolve" and len(node.args) == 1 and not node.keywords:
            index = self.visit(node.args[0])
            return ast.copy_location(ast.Subscript(
                value=ast.Call(func=ast.Name(id="globals", ctx=ast.Load()), args=[], keywords=[]), slice=index,
                ctx=ast.Load()), node)
        if (isinstance(node.func, ast.Name) and node.func.id == "eval" and len(node.args) == 3 and not node.keywords
                and _is_call(node.args[1], "vars", "_tasks") and _is_call(node.args[2], "locals")):
            return ast.copy_location(ast.Call(func=node.func, args=[self.visit(node.args[0])], keywords=[]), node)
        return self.generic_visit(node)


def _set_names(fn: ast.FunctionDef, variable: str) -> list[str]:
    """Element spellings of the ``frozenset({...})`` literal assigned to ``variable`` in ``fn``."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [variable]:
            elements = node.value.args[0] if isinstance(node.value, ast.Call) else node.value
            return [ast.unparse(element) for element in elements.elts]
    raise ValueError(f"{variable} assignment not found")


def static_module_sets() -> dict[str, list[str]]:
    """``{"base_modules": [...], "repeat_modules": [...]}``: the set elements of this module's :func:`parse_model`
    as written in its source with the ``_tasks.`` spelling removed (37 / 17 names for ultralytics 8.4.173)."""
    undone = _UndoSpellings().visit(_parse_model_def(inspect.getsource(parse_model)))
    return {name: _set_names(undone, name) for name in ("base_modules", "repeat_modules")}


def verify_static_copy(installed_source: str | None = None) -> dict[str, Any]:
    """Static proof that :func:`parse_model` is the ETRI-extended installed ``parse_model``; nothing is executed.

    ``installed_source`` defaults to the source of the installed, unpatched ``ultralytics.nn.tasks.parse_model``
    (``_nas_mixed_original`` once the registration is installed). The ETRI AST patch
    (``etri_mixed_blocks._extend_parser_source``) is applied to it and the result is compared with this function after
    :class:`_UndoSpellings`. ``equivalent`` is True when both ASTs are identical; ``difference`` holds the first lines
    of a diff of their dumps otherwise.
    """
    from mbnas_model_runtime.operators.etri_mixed_blocks import _extend_parser_source

    if installed_source is None:
        original = _tasks.parse_model
        installed_source = inspect.getsource(getattr(original, "_nas_mixed_original", original))
    installed_sha256 = hashlib.sha256(installed_source.encode()).hexdigest()
    extended = _parse_model_def(_extend_parser_source(installed_source))
    undone = _UndoSpellings().visit(_parse_model_def(inspect.getsource(parse_model)))
    left, right = ast.dump(extended, indent=1), ast.dump(undone, indent=1)
    difference = None if left == right else list(difflib.unified_diff(
        left.splitlines(), right.splitlines(), "etri_ast_patch(installed)", "static_copy(undone)", n=1, lineterm=""))[:40]
    sets = {name: _set_names(undone, name) for name in ("base_modules", "repeat_modules")}
    return {"installed_sha256": installed_sha256, "expected_sha256": EXPECTED_PARSE_MODEL_SHA256,
            "sha256_match": installed_sha256 == EXPECTED_PARSE_MODEL_SHA256,
            "base_modules": len(sets["base_modules"]), "repeat_modules": len(sets["repeat_modules"]),
            "added_base_modules": [n for n in sets["base_modules"] if n in ADDED_BASE_MODULES],
            "added_repeat_modules": [n for n in sets["repeat_modules"] if n in ADDED_REPEAT_MODULES],
            "equivalent": difference is None, "difference": difference}
