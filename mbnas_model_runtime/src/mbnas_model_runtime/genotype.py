"""Genotype = canonical ultralytics model YAML dict; architecture identity = hash of the BUILT structure.

Contract:

* :class:`Genotype` wraps one canonical model dict (``nc``, ``scale``, ``scales``, ``backbone``, ``head`` and the
  optional ``end2end`` / ``reg_max`` / ``activation`` keys; loader-only keys such as ``yaml_file`` and the ETRI
  ``__mixed_*__`` annotations are dropped). ``yaml_hash`` identifies the text; it is *not* the architecture identity.
* :func:`inspect_genotype` builds the model on CPU with ultralytics ``DetectionModel`` (which also runs its own
  stride forward), then runs one reference forward at the protocol ``imgsz`` with forward hooks, giving per-row
  input/output channels, spatial size and stride. Any exception is captured as ``ok=False`` with the reason.
* Per top-level layer the reference forward also records (mbnas-structure/2): the shapes that actually
  reached the layer per source (pre-hook ``inputs``), every tensor it produced (``outputs``), the observed repeat
  count (``len(m.m | m.blocks | Sequential)``), the ``nn.Sequential`` wrapper ultralytics builds for a non-repeat
  module with n>1, detection-head ``stride``/``nl``/``end2end`` and the per-block ``named_modules()`` listing in which
  an instance reachable from several places (``Conv.default_act``) is flagged ``shared``. A failure inside the
  ultralytics ``_predict_once`` loop keeps the shapes that reached the failing layer (``error_context``).
* ``architecture_hash`` = :func:`mbnas_model_runtime.adapters.common.structural_hash` of the CPU-built model (ordered
  module classes + state names/shapes/dtypes, scheme ``mbnas-structural/1``) — the same function the training
  adapters write into checkpoints and evaluation rows, so a candidate row and its evaluations carry one identical hash.
  Two YAMLs that build the same structure (a no-op mutation, a repair that restores a row, an argument spelled with
  its default) share one hash; trial ids are separate.
* ``signature_hash`` = canonical SHA-256 of the richer :func:`structure_signature` (per top-level layer its routing,
  output channels and stride; per sub-module its class, ``extra_repr()``, parameter/buffer shapes and whitelisted
  structural attributes such as attention heads/window); it is recorded for audits, not used as the identity.
"""
from __future__ import annotations

import copy
import functools
import math
import re
import platform
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

import yaml

from mbnas_contracts.util.canon import canonical_sha256

GENOTYPE_SCHEMA = "mbnas-genotype/1"
SIGNATURE_VERSION = "mbnas-arch-sig/1"
YAML_KEYS: tuple[str, ...] = ("nc", "scale", "scales", "end2end", "reg_max", "activation", "depth_multiple",
                              "width_multiple", "backbone", "head")
STRUCT_ATTRS: tuple[str, ...] = (
    "c", "c_", "c1", "c2", "c3", "n", "nc", "nl", "no", "reg_max", "end2end", "legacy", "add", "shortcut", "window",
    "max_tokens", "requested_heads", "actual_heads", "num_heads", "head_dim", "key_dim", "patch", "area",
    "residual", "batch_first", "embed_dim", "i", "f")


def _plain(value: Any) -> Any:
    """Tuples -> lists recursively (YAML/JSON-safe, canonical)."""
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def canonical_row(row: Any) -> list[Any]:
    """``[from, repeats, module, args]`` with plain lists; raises ValueError for malformed rows."""
    if not isinstance(row, (list, tuple)) or len(row) != 4 or not isinstance(row[3], (list, tuple)):
        raise ValueError(f"model row must be [from, repeats, module, args], got {row!r}")
    frm, rep, module, args = row
    if isinstance(rep, bool) or not isinstance(rep, int) or rep < 1:
        raise ValueError(f"repeats must be a positive integer in row {row!r}")
    return [_plain(frm), rep, str(module), _plain(list(args))]


def canonical_yaml(d: dict[str, Any]) -> dict[str, Any]:
    """Keep only model keys, normalise rows; requires ``nc``, ``backbone`` and ``head``."""
    missing = [k for k in ("nc", "backbone", "head") if k not in d]
    if missing:
        raise ValueError(f"model yaml missing {missing}")
    out = {k: _plain(copy.deepcopy(d[k])) for k in YAML_KEYS if k in d and d[k] is not None}
    out["backbone"] = [canonical_row(r) for r in d["backbone"]]
    out["head"] = [canonical_row(r) for r in d["head"]]
    return out


@dataclass(frozen=True)
class Genotype:
    """One architecture proposal as a canonical model dict plus its template provenance.

    ``family`` is the crossover-compatibility key (same base YAML + scale, identical routing); ``template_id`` names
    the seed template the rows were derived from (``<family>:<variant>``).
    """

    family: str
    template_id: str
    yaml: dict[str, Any] = field(repr=False)

    @classmethod
    def from_yaml_dict(cls, d: dict[str, Any], *, family: str, template_id: str) -> "Genotype":
        return cls(family=family, template_id=template_id, yaml=canonical_yaml(d))

    @classmethod
    def from_yaml_text(cls, text: str, *, family: str, template_id: str) -> "Genotype":
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("architecture yaml must be a mapping")
        return cls.from_yaml_dict(data, family=family, template_id=template_id)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Genotype":
        """Inverse of :meth:`to_json`; accepts ``yaml`` as a dict or as YAML text."""
        body = data["yaml"]
        if isinstance(body, str):
            return cls.from_yaml_text(body, family=data["family"], template_id=data["template_id"])
        return cls.from_yaml_dict(body, family=data["family"], template_id=data["template_id"])

    def to_json(self) -> dict[str, Any]:
        return {"schema": GENOTYPE_SCHEMA, "family": self.family, "template_id": self.template_id,
                "yaml": copy.deepcopy(self.yaml)}

    def rows(self) -> list[list[Any]]:
        """Backbone rows followed by head rows (deep copies)."""
        return copy.deepcopy(self.yaml["backbone"] + self.yaml["head"])

    @property
    def n_backbone(self) -> int:
        return len(self.yaml["backbone"])

    def with_rows(self, rows: list[list[Any]], *, template_id: str | None = None) -> "Genotype":
        """New genotype with all rows replaced (row count must not change)."""
        if len(rows) != len(self.yaml["backbone"]) + len(self.yaml["head"]):
            raise ValueError("row count must not change")
        d = copy.deepcopy(self.yaml)
        d["backbone"] = [canonical_row(r) for r in rows[: self.n_backbone]]
        d["head"] = [canonical_row(r) for r in rows[self.n_backbone:]]
        return Genotype(family=self.family, template_id=template_id or self.template_id, yaml=d)

    def yaml_text(self) -> str:
        """Deterministic YAML text (flow-style rows) that ultralytics loads back to the same dict."""
        head = {k: v for k, v in self.yaml.items() if k not in ("backbone", "head")}
        # A flow-mapping header followed by block backbone/head keys is invalid YAML
        # (scalar-only custom configs commonly triggered that form).
        lines = [yaml.safe_dump(head, sort_keys=True, default_flow_style=False).rstrip()]
        for section in ("backbone", "head"):
            lines.append(f"{section}:")
            lines.extend("  - " + yaml.safe_dump(r, default_flow_style=True, width=10_000).strip()
                         for r in self.yaml[section])
        return "\n".join(lines) + "\n"

    def yaml_hash(self) -> str:
        """Hash of the canonical YAML dict (textual identity, not the built-structure identity)."""
        return canonical_sha256(self.yaml)


# -- declared-YAML allowlist and argument schema -------------------------------------------------------------------
@dataclass(frozen=True)
class DeclaredLimits:
    """Bounds of a declared model YAML checked by :func:`validate_declared` before anything is built."""

    max_text_bytes: int = 1 << 20
    max_aliases: int = 64
    max_rows: int = 256
    max_repeats: int = 64
    max_int: int = 65536
    max_channels: int = 4096
    max_nc: int = 10000
    max_list_depth: int = 2
    max_str_len: int = 64
    max_scales: int = 16


DEFAULT_DECLARED_LIMITS = DeclaredLimits()
EXTRA_ALLOWED_MODULES: tuple[str, ...] = ("nn.Upsample", "nn.Identity")
#: parse_model branches that are never accepted from a user YAML even though the parser knows them
#: (``TorchVision`` instantiates torchvision models and may download weights).
DENIED_MODULES: tuple[str, ...] = ("TorchVision",)
_SAFE_ACTIVATION = re.compile(
    r"(?:torch\.)?nn\.(?:SiLU|ReLU|ReLU6|LeakyReLU|Hardswish|Identity)\((?:\s*(?:True|False|[0-9.]+)\s*(?:,\s*)?)*\)")
_ARG_STRING = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CHANNEL_FIRST = ("HGStem", "HGBlock", "CBLinear")


def _module_key(spelling: str) -> str:
    """Parser-source spelling -> YAML spelling (``torch.nn.ConvTranspose2d`` -> ``nn.ConvTranspose2d``)."""
    return "nn." + spelling[len("torch.nn."):] if spelling.startswith("torch.nn.") else spelling


@functools.lru_cache(maxsize=1)
def module_sets() -> dict[str, frozenset[str]]:
    """``base`` / ``repeat`` (``static_module_sets()`` of the vendored 8.4.173 ``parse_model``), ``branch`` (every other
    class the parser compares ``m`` with: heads, Concat, AIFI, ...; read from the same function's AST) and ``allowlist``
    = base | repeat | branch | ``nn.Upsample`` / ``nn.Identity`` - ``TorchVision``."""
    import ast

    from mbnas_model_runtime.operators import parse_model_8_4_173 as pm

    sets = pm.static_module_sets()
    undone = pm._UndoSpellings().visit(pm._parse_model_def(pm.inspect.getsource(pm.parse_model)))
    branch: set[str] = set()
    for node in ast.walk(undone):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "m"):
            continue
        for comp in node.comparators:
            elts = comp.args[0].elts if isinstance(comp, ast.Call) and comp.args and isinstance(comp.args[0], (ast.Set, ast.List, ast.Tuple)) \
                else comp.elts if isinstance(comp, (ast.Set, ast.List, ast.Tuple)) else [comp]
            for elt in elts:
                if isinstance(elt, ast.Name) and elt.id in ("base_modules", "repeat_modules"):
                    continue  # the two set variables themselves (their members come from static_module_sets)
                if isinstance(elt, (ast.Name, ast.Attribute)):
                    branch.add(_module_key(ast.unparse(elt)))
    base = frozenset(_module_key(n) for n in sets["base_modules"])
    repeat = frozenset(_module_key(n) for n in sets["repeat_modules"])
    allow = (base | repeat | frozenset(branch) | frozenset(EXTRA_ALLOWED_MODULES)) - frozenset(DENIED_MODULES)
    return {"base": base, "repeat": repeat, "branch": frozenset(branch), "allowlist": allow}


def module_allowlist() -> frozenset[str]:
    """``MODULE_ALLOWLIST``: module names a declared YAML row may use (see :func:`module_sets`)."""
    return module_sets()["allowlist"]


def count_yaml_aliases(text: str) -> int:
    """Number of alias nodes (``*anchor``) in ``text``; parsing events only, nothing is constructed."""
    return sum(1 for event in yaml.parse(text, Loader=yaml.SafeLoader) if isinstance(event, yaml.AliasEvent))


def _diag(code: str, cause: str, path: str | None = None, position: int | None = None,
          suggestion: str = "Use only supported blocks with scalar arguments inside the documented bounds.") -> dict[str, Any]:
    return {"code": code, "position": position, "path": path, "cause": cause, "suggestion": suggestion,
            "evidence_level": "declared"}


def _depth(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def _scalar_problem(value: Any, limits: DeclaredLimits) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if abs(value) <= limits.max_int else f"integer {value} exceeds {limits.max_int}"
    if isinstance(value, float):
        return None if math.isfinite(value) and abs(value) <= limits.max_int else f"number {value!r} is not finite or exceeds {limits.max_int}"
    if isinstance(value, str):
        if len(value) > limits.max_str_len or not _ARG_STRING.fullmatch(value):
            return f"string argument {value[:40]!r} is not an identifier of at most {limits.max_str_len} characters"
        return None
    return f"argument of type {type(value).__name__} is not a scalar"


def _value_problem(value: Any, limits: DeclaredLimits) -> str | None:
    if isinstance(value, (list, tuple)):
        if _depth(value) > limits.max_list_depth:
            return f"list nesting deeper than {limits.max_list_depth}"
        for item in value:
            problem = _value_problem(item, limits)
            if problem:
                return problem
        return None
    return _scalar_problem(value, limits)


def text_diagnostics(text: str, limits: DeclaredLimits | None = None) -> list[dict[str, Any]]:
    """Raw-text bounds checked before the YAML is constructed: size and number of aliases (billion-laughs guard)."""
    lim = limits or DEFAULT_DECLARED_LIMITS
    size = len(text.encode("utf-8"))
    if size > lim.max_text_bytes:
        return [_diag("ARG_SCHEMA_INVALID", f"model configuration is {size} bytes (limit {lim.max_text_bytes})")]
    try:
        aliases = count_yaml_aliases(text)
    except yaml.YAMLError:
        return [_diag("MODEL_CONFIG_INVALID", "model configuration is not valid YAML")]
    if aliases > lim.max_aliases:
        return [_diag("ARG_SCHEMA_INVALID", f"model configuration uses {aliases} YAML aliases (limit {lim.max_aliases})")]
    return []


def validate_declared(yaml_dict: Any, limits: DeclaredLimits | None = None, *, raw_text: str | None = None) -> list[dict[str, Any]]:
    """Static allowlist + argument-schema check of a declared model YAML (nothing is imported from the YAML, built or
    evaluated). Returns diagnostics (empty: acceptable): ``BLOCK_UNSUPPORTED`` for a module outside
    :func:`module_allowlist`, ``ACTIVATION_REFUSED`` for an ``activation`` outside the safe constructors, and
    ``ARG_SCHEMA_INVALID`` for everything else: argument types (scalars, lists nested at most ``max_list_depth``),
    integers <= ``max_int``, output channels <= ``max_channels``, repeats <= ``max_repeats``, rows <= ``max_rows``,
    ``nc`` <= ``max_nc``, and - when ``raw_text`` is given - text size <= ``max_text_bytes`` and YAML aliases <=
    ``max_aliases``."""
    lim = limits or DEFAULT_DECLARED_LIMITS
    out: list[dict[str, Any]] = []
    if raw_text is not None:
        pre = text_diagnostics(raw_text, lim)
        if pre:
            return pre
    if not isinstance(yaml_dict, dict):
        return [_diag("ARG_SCHEMA_INVALID", "model configuration must be a mapping")]
    nc = yaml_dict.get("nc")
    if nc is not None and (isinstance(nc, bool) or not isinstance(nc, int) or not 1 <= nc <= lim.max_nc):
        out.append(_diag("ARG_SCHEMA_INVALID", f"nc must be an integer in 1..{lim.max_nc}", "nc"))
    act = yaml_dict.get("activation")
    if act is not None and not (isinstance(act, str) and len(act) <= 200 and _SAFE_ACTIVATION.fullmatch(act)):
        out.append(_diag("ACTIVATION_REFUSED", "activation expression is outside the safe supported constructors",
                         "activation", suggestion="Use a supported torch.nn activation constructor."))
    scale = yaml_dict.get("scale")
    if scale is not None and (not isinstance(scale, str) or len(scale) > lim.max_str_len):
        out.append(_diag("ARG_SCHEMA_INVALID", "scale must be a short string", "scale"))
    scales = yaml_dict.get("scales")
    if scales is not None:
        if not isinstance(scales, dict) or len(scales) > lim.max_scales:
            out.append(_diag("ARG_SCHEMA_INVALID", f"scales must be a mapping of at most {lim.max_scales} entries", "scales"))
        else:
            for key, val in scales.items():
                if not isinstance(key, str) or len(key) > lim.max_str_len or not isinstance(val, (list, tuple)) or len(val) > 3 \
                        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > lim.max_int for v in val) \
                        or (len(val) == 3 and val[2] > lim.max_channels):
                    out.append(_diag("ARG_SCHEMA_INVALID", f"scale {str(key)[:20]!r} must be [depth, width, max_channels] "
                                     f"numbers (max_channels <= {lim.max_channels})", f"scales.{str(key)[:20]}"))
    for key in ("depth_multiple", "width_multiple"):
        v = yaml_dict.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 64):
            out.append(_diag("ARG_SCHEMA_INVALID", f"{key} must be a number in (0, 64]", key))
    for key in ("end2end",):
        if yaml_dict.get(key) is not None and not isinstance(yaml_dict[key], bool):
            out.append(_diag("ARG_SCHEMA_INVALID", f"{key} must be boolean", key))
    rm = yaml_dict.get("reg_max")
    if rm is not None and (isinstance(rm, bool) or not isinstance(rm, int) or not 0 <= rm <= lim.max_int):
        out.append(_diag("ARG_SCHEMA_INVALID", "reg_max must be a non-negative integer", "reg_max"))
    sections = [(name, yaml_dict.get(name)) for name in ("backbone", "head")]
    if any(not isinstance(rows, list) for _, rows in sections):
        out.append(_diag("ARG_SCHEMA_INVALID", "backbone and head must be lists of rows"))
        return out
    total = sum(len(rows) for _, rows in sections)
    if total > lim.max_rows:
        out.append(_diag("ARG_SCHEMA_INVALID", f"{total} rows declared (limit {lim.max_rows})"))
        return out
    sets = module_sets()
    allow, base = sets["allowlist"], sets["base"]
    index = 0
    for name, rows in sections:
        for j, row in enumerate(rows):
            path = f"{name}[{j}]"
            if not isinstance(row, (list, tuple)) or len(row) != 4:
                out.append(_diag("ARG_SCHEMA_INVALID", "row must be [from, repeats, module, args]", path, index))
                index += 1
                continue
            frm, rep, module, args = row
            refs = frm if isinstance(frm, (list, tuple)) else [frm]
            if not refs or len(refs) > lim.max_rows or any(isinstance(r, bool) or not isinstance(r, int) or abs(r) > lim.max_rows for r in refs):
                out.append(_diag("ARG_SCHEMA_INVALID", f"from must be an integer or a list of integers within +-{lim.max_rows}",
                                 path + ".from", index))
            if isinstance(rep, bool) or not isinstance(rep, int) or not 1 <= rep <= lim.max_repeats:
                out.append(_diag("ARG_SCHEMA_INVALID", f"repeats must be an integer in 1..{lim.max_repeats}", path + ".repeats", index))
            if not isinstance(module, str) or module not in allow:
                shown = module[:60] if isinstance(module, str) else type(module).__name__
                out.append(_diag("BLOCK_UNSUPPORTED", f"module {shown!r} is not in the supported block allowlist",
                                 path + ".module", index, "Choose a registered block name."))
            if not isinstance(args, (list, tuple)):
                out.append(_diag("ARG_SCHEMA_INVALID", "args must be a list", path + ".args", index))
            else:
                problem = None if len(args) <= 32 else "more than 32 arguments"
                if problem is None and _depth(list(args)) - 1 > lim.max_list_depth:
                    problem = f"argument lists nested deeper than {lim.max_list_depth}"
                for a in args if problem is None else []:
                    problem = _value_problem(a, lim)
                    if problem:
                        break
                if problem is None and isinstance(module, str) and (module in base or module in _CHANNEL_FIRST):
                    for a in list(args)[:2 if module in ("HGStem", "HGBlock") else 1]:
                        if isinstance(a, int) and not isinstance(a, bool) and a > lim.max_channels:
                            problem = f"channel argument {a} exceeds {lim.max_channels}"
                if problem:
                    out.append(_diag("ARG_SCHEMA_INVALID", problem, path + ".args", index))
            index += 1
    return out


def load_declared_text(text: str, limits: DeclaredLimits | None = None) -> tuple[Any, list[dict[str, Any]]]:
    """Size/alias-check ``text`` *before* constructing it, ``yaml.safe_load`` it, then :func:`validate_declared`.
    Returns ``(data or None, diagnostics)``."""
    lim = limits or DEFAULT_DECLARED_LIMITS
    pre = text_diagnostics(text, lim)
    if pre:
        return None, pre
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, [_diag("MODEL_CONFIG_INVALID", "model configuration is not valid YAML")]
    return data, validate_declared(data, lim)


@dataclass
class LayerInfo:
    """Observed shapes of one top-level layer at the reference forward.

    The first eleven fields are the v1 record. The rest (mbnas-structure/2) are observations of the same forward:
    ``inputs`` = what each source actually delivered to this layer, ``outputs`` = every tensor it returned (without
    the batch axis), the repeat count read from the built module, the ``nn.Sequential`` wrapper ultralytics builds for
    a non-repeat module with n>1, detection-head attributes, and the block's own ``named_modules()`` listing.
    """

    index: int
    module: str
    frm: Any
    c_in: int | None
    c_out: int | None
    h_in: int | None
    w_in: int | None
    h_out: int | None
    w_out: int | None
    stride: float | None
    params: int
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[list[int]] | None = None
    wrapper: str | None = None
    wrapped_module: str | None = None
    observed_repeats: int | None = None
    repeats_source: str | None = None
    head: dict[str, Any] | None = None
    internal: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {"index": self.index, "module": self.module, "from": self.frm, "c_in": self.c_in, "c_out": self.c_out,
               "h_in": self.h_in, "w_in": self.w_in, "h_out": self.h_out, "w_out": self.w_out,
               "stride": self.stride, "params": self.params,
               "inputs": copy.deepcopy(self.inputs), "outputs": copy.deepcopy(self.outputs)}
        if self.head:
            out.update(copy.deepcopy(self.head))
        return out


@dataclass
class BuildReport:
    """Result of :func:`inspect_genotype`; ``model`` is kept only when requested.

    ``stage`` (``build``/``forward``) and ``failure_kind`` (``parse``/``forward``, read from the traceback) say where a
    failure happened; ``error_context`` holds the row, module class and per-source input shapes that reached the
    failing layer when the failure was inside the ultralytics forward loop; ``observation`` is the environment the
    check ran in (:func:`observation_environment`).
    """

    ok: bool
    error: str | None
    imgsz: int
    error_row: int | None = None
    layers: list[LayerInfo] = field(default_factory=list)
    signature: dict[str, Any] | None = None
    signature_hash: str | None = None
    architecture_hash: str | None = None
    params: int | None = None
    flops: int | None = None
    built_audit: dict[str, Any] | None = None
    model: Any = field(default=None, repr=False)
    yaml_hash: str | None = None
    stage: str | None = None
    failure_kind: str | None = None
    error_context: dict[str, Any] | None = None
    observation: dict[str, Any] | None = None


def observation_environment(imgsz: int) -> dict[str, Any]:
    """Environment of a CPU structure observation (G5). Best effort: a missing package is recorded as ``None``."""
    env: dict[str, Any] = {"method": "ultralytics_parse_model+forward_hook", "device": "cpu", "imgsz": imgsz,
                           "python": platform.python_version(), "torch": None, "ultralytics": None}
    try:
        import torch

        env["torch"] = str(torch.__version__)
    except Exception:  # noqa: BLE001
        pass
    try:
        import ultralytics

        env["ultralytics"] = str(ultralytics.__version__)
        from ultralytics.nn import tasks

        parser = tasks.parse_model
        if getattr(parser, "_nas_mixed_installed", False):  # ETRI mixed pool registered in this process
            sha = (getattr(parser, "_nas_mixed_capabilities", None) or {}).get("parser_source_sha256")
            if sha:
                env["parser_source_sha256"] = str(sha)
    except Exception:  # noqa: BLE001
        pass
    env["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return env


def _failing_row(exc: BaseException) -> tuple[int | None, str | None]:
    """Row index/module of a parse or forward failure, read from the innermost ultralytics frame of the traceback."""
    row, module, tb = None, None, exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if frame.f_code.co_name == "parse_model" and isinstance(frame.f_locals.get("i"), int):
            row, m = frame.f_locals["i"], frame.f_locals.get("m")
            module = getattr(m, "__name__", None) or str(m)
        elif frame.f_code.co_name == "_predict_once" and hasattr(frame.f_locals.get("m"), "i"):
            row, module = frame.f_locals["m"].i, type(frame.f_locals["m"]).__name__
        tb = tb.tb_next
    return row, module


def _describe_failure(stage: str, exc: BaseException) -> tuple[str, int | None]:
    row, module = _failing_row(exc)
    where = f" at row {row} ({module})" if row is not None else ""
    return f"{stage}{where}: {type(exc).__name__}: {str(exc) or 'assertion failed'}", row


def _shape_of(out: Any) -> tuple[int, int, int] | None:
    import torch

    if isinstance(out, torch.Tensor) and out.ndim == 4:
        return int(out.shape[1]), int(out.shape[2]), int(out.shape[3])
    return None


def _input_shapes(x: Any) -> list[tuple[int, int, int] | None]:
    """(c, h, w) of each tensor handed to a layer: one entry for a tensor, one per element for a list."""
    import torch

    if isinstance(x, torch.Tensor):
        return [_shape_of(x)]
    if isinstance(x, (list, tuple)):
        return [_shape_of(v) for v in x]
    return []


def _output_shapes(out: Any) -> list[list[int]]:
    """Shape without the batch axis of every tensor a layer returned (Detect returns a tuple/list/dict of tensors)."""
    import torch

    found: list[list[int]] = []

    def walk(value: Any, depth: int) -> None:
        if len(found) >= 32 or depth > 3:
            return
        if isinstance(value, torch.Tensor):
            found.append([int(v) for v in value.shape[1:]])
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, depth + 1)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v, depth + 1)

    walk(out, 0)
    return found


def _resolve_sources(index: int, frm: Any) -> list[int]:
    """Source row of every ``from`` reference; ``-1`` stands for the input image of row 0."""
    srcs = frm if isinstance(frm, (list, tuple)) else [frm]
    return [-1 if (index == 0 and s == -1) else (index + s if s < 0 else s) for s in srcs]


def _inputs_record(index: int, frm: Any, shapes: list[tuple[int, int, int] | None]) -> list[dict[str, Any]]:
    """Per-source ``{source, c, h, w}``; ``source`` is None when the observed arity differs from ``from``."""
    sources = _resolve_sources(index, frm)
    aligned = len(sources) == len(shapes)
    return [{"source": sources[k] if aligned else None, "c": s[0] if s else None, "h": s[1] if s else None,
             "w": s[2] if s else None} for k, s in enumerate(shapes)]


def _failure_details(exc: BaseException) -> tuple[str | None, dict[str, Any] | None]:
    """``(kind, context)`` of a failure: kind ``parse``/``forward`` from the traceback frames; context = the layer that
    failed inside ultralytics' ``_predict_once`` with the shapes of the tensors that reached it."""
    kind, ctx, tb = None, None, exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if frame.f_code.co_name == "parse_model":
            kind = "parse"
        elif frame.f_code.co_name == "_predict_once":
            kind = "forward"
            m = frame.f_locals.get("m")
            if isinstance(getattr(m, "i", None), int):
                try:
                    ctx = {"row": m.i, "module_class": type(m).__name__, "from": getattr(m, "f", None),
                           "inputs": _inputs_record(m.i, getattr(m, "f", -1), _input_shapes(frame.f_locals.get("x")))}
                except Exception:  # noqa: BLE001 - context is best-effort evidence, never a new failure
                    ctx = None
        tb = tb.tb_next
    return kind, ctx


def _scalar_attrs(module: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in STRUCT_ATTRS:
        if not hasattr(module, name):
            continue
        val = getattr(module, name)
        if isinstance(val, bool) or isinstance(val, (int, str)) or val is None:
            out[name] = val
        elif isinstance(val, float) and math.isfinite(val):
            out[name] = val
        elif isinstance(val, (list, tuple)) and all(isinstance(v, (int, str)) for v in val):
            out[name] = list(val)
    return out


def structure_signature(model: Any, layers: list[LayerInfo]) -> dict[str, Any]:
    """Ordered, YAML-independent description of the built model (see module docstring)."""
    modules = []
    for path, mod in model.model.named_modules():
        modules.append({
            "path": path, "cls": type(mod).__qualname__, "repr": mod.extra_repr(),
            "params": [[n, list(p.shape)] for n, p in mod.named_parameters(recurse=False)],
            "buffers": [[n, list(b.shape)] for n, b in mod.named_buffers(recurse=False)],
            "attrs": _scalar_attrs(mod)})
    stride = getattr(model, "stride", None)
    return {"version": SIGNATURE_VERSION, "input_channels": 3,
            "stride": [float(s) for s in stride.tolist()] if stride is not None else None,
            "layers": [{"i": li.index, "f": li.frm, "cls": li.module, "c_out": li.c_out, "stride": li.stride}
                       for li in layers],
            "modules": modules}


def internal_layer_records(model: Any) -> list[list[dict[str, Any]]]:
    """Per top-level layer ``named_modules()`` listing (U1).

    The traversal keeps every path of an instance reachable more than once (``remove_duplicate=False``): the shared
    ``Conv.default_act`` activation therefore appears in every Conv block, flagged ``shared=True`` when the same
    instance is reachable from more than one place in the whole model. ``path`` keeps the v1 form ``<row>.<relative>``.
    """
    per_layer = [[(path, mod) for path, mod in layer.named_modules(remove_duplicate=False) if path]
                 for layer in model.model]
    count: dict[int, int] = {}
    for items in per_layer:
        for _, mod in items:
            count[id(mod)] = count.get(id(mod), 0) + 1
    records = []
    for index, items in enumerate(per_layer):
        records.append([{"path": f"{index}.{path}", "module": type(mod).__qualname__,
                         "parameters": [[n, list(p.shape)] for n, p in mod.named_parameters(recurse=False)],
                         "attributes": _scalar_attrs(mod), "shared": count[id(mod)] > 1} for path, mod in items])
    return records


def _is_sequential_wrapper(layer: Any) -> bool:
    """ultralytics wraps a non-repeat module with n>1 in ``nn.Sequential`` and sets ``.type`` to the wrapped class."""
    import torch

    if not isinstance(layer, torch.nn.Sequential) or len(layer) < 1 or hasattr(layer, "cv1"):
        return False
    tag = getattr(layer, "type", None)
    return not isinstance(tag, str) or str(tag).rsplit(".", 1)[-1] == type(layer[0]).__name__


def _observe_layer(layer: Any, declared_repeats: Any) -> dict[str, Any]:
    """Wrapper, observed repeat count and detection-head attributes read from the built top-level module."""
    import torch

    out: dict[str, Any] = {"wrapper": None, "wrapped_module": None, "observed_repeats": None,
                           "repeats_source": "unobserved", "head": None}
    inner = layer
    if _is_sequential_wrapper(layer):
        out.update(wrapper="nn.Sequential", wrapped_module=type(layer[0]).__name__, observed_repeats=len(layer),
                   repeats_source="sequential_wrapper")
        inner = layer[0]
    else:
        for attr in ("m", "blocks"):  # C2f/C3k2/C2PSA/A2C2f: .m ; ETRI NAS*: .blocks
            container = getattr(layer, attr, None)
            if isinstance(container, (torch.nn.ModuleList, torch.nn.Sequential)):
                out.update(observed_repeats=len(container), repeats_source=f".{attr}")
                break
        else:
            if isinstance(layer, torch.nn.Sequential):
                out.update(observed_repeats=len(layer), repeats_source="sequential")
            elif declared_repeats == 1:  # no wrapper, no container: the single built instance is what was observed
                out.update(observed_repeats=1, repeats_source="single_module")
    nl = getattr(inner, "nl", None)
    if isinstance(nl, int) and not isinstance(nl, bool) and hasattr(inner, "nc"):
        stride = getattr(inner, "stride", None)
        try:
            strides = [float(s) for s in stride.tolist()] if stride is not None and stride.numel() else None
        except Exception:  # noqa: BLE001
            strides = None
        end2end = getattr(inner, "end2end", None)
        nc = getattr(inner, "nc", None)
        out["head"] = {"head_strides": strides, "nl": nl, "end2end": end2end if isinstance(end2end, bool) else None,
                       "nc": nc if isinstance(nc, int) and not isinstance(nc, bool) else None}
    return out


def build_detection_model(model_yaml: dict[str, Any], nc: int) -> Any:
    """CPU ``DetectionModel`` from a deep copy of ``model_yaml`` (the ultralytics parser mutates its input)."""
    from ultralytics.nn.tasks import DetectionModel

    return DetectionModel(copy.deepcopy(model_yaml), ch=3, nc=nc, verbose=False)


def inspect_genotype(genotype: Genotype, imgsz: int, *, keep_model: bool = False, count_flops: bool = True,
                     audit_hook: Any = None) -> BuildReport:
    """Build on CPU, run the hooked reference forward at ``imgsz`` and compute signature/hash/params/FLOPs.

    ``audit_hook(model, report) -> dict`` (optional) adds the built-model audit while the model is alive.
    """
    import torch

    report = BuildReport(ok=False, error=None, imgsz=imgsz, yaml_hash=genotype.yaml_hash())
    try:
        model = build_detection_model(genotype.yaml, int(genotype.yaml["nc"]))
    except Exception as exc:  # noqa: BLE001 - parser/constructor failures are repair inputs, not crashes
        report.error, report.error_row = _describe_failure("build", exc)
        report.stage = "build"
        report.failure_kind, report.error_context = _failure_details(exc)
        report.observation = observation_environment(imgsz)
        return report
    report.observation = observation_environment(imgsz)
    shapes: dict[int, tuple[int, int, int] | None] = {}
    in_shapes: dict[int, list[tuple[int, int, int] | None]] = {}
    out_shapes: dict[int, list[list[int]]] = {}

    def on_output(idx: int, out: Any) -> None:
        shapes[idx] = _shape_of(out)
        out_shapes[idx] = _output_shapes(out)

    hooks = []
    for idx, layer in enumerate(model.model):
        hooks.append(layer.register_forward_pre_hook(
            lambda m, args, idx=idx: in_shapes.__setitem__(idx, _input_shapes(args[0]) if args else [])))
        hooks.append(layer.register_forward_hook(lambda m, i, o, idx=idx: on_output(idx, o)))
    flops = None
    fast = torch.backends.mha.get_fastpath_enabled()
    try:
        model.eval()
        torch.backends.mha.set_fastpath_enabled(False)  # count MHA as linear/bmm ops
        with torch.no_grad():
            x = torch.zeros(1, 3, imgsz, imgsz)
            if count_flops:
                from torch.utils.flop_counter import FlopCounterMode

                with FlopCounterMode(display=False) as counter:
                    model(x)
                flops = int(counter.get_total_flops())
            else:
                model(x)
    except Exception as exc:  # noqa: BLE001 - e.g. token budget exceeded at this resolution
        report.error, report.error_row = _describe_failure(f"forward@{imgsz}", exc)
        report.stage = "forward"
        report.failure_kind, report.error_context = _failure_details(exc)
        return report
    finally:
        torch.backends.mha.set_fastpath_enabled(fast)
        for h in hooks:
            h.remove()
    declared = [row[1] for row in genotype.rows()]
    internal = internal_layer_records(model)
    for idx, layer in enumerate(model.model):
        frm = layer.f
        inputs = _inputs_record(idx, frm, in_shapes.get(idx, []))
        known = [(i["c"], i["h"], i["w"]) for i in inputs if i["c"] is not None]
        c_in = sum(s[0] for s in known) if len(known) == len(inputs) and known else None
        h_in, w_in = (known[0][1], known[0][2]) if known else (None, None)
        out = shapes.get(idx)
        seen = _observe_layer(layer, declared[idx] if idx < len(declared) else None)
        report.layers.append(LayerInfo(
            index=idx, module=type(layer).__name__, frm=frm, c_in=c_in, c_out=out[0] if out else None,
            h_in=h_in, w_in=w_in, h_out=out[1] if out else None, w_out=out[2] if out else None,
            stride=(imgsz / out[1]) if out else None, params=sum(p.numel() for p in layer.parameters()),
            inputs=inputs, outputs=out_shapes.get(idx), wrapper=seen["wrapper"], wrapped_module=seen["wrapped_module"],
            observed_repeats=seen["observed_repeats"], repeats_source=seen["repeats_source"], head=seen["head"],
            internal=internal[idx]))
    from mbnas_model_runtime.adapters.common import structural_hash

    report.signature = structure_signature(model, report.layers)
    report.signature_hash = canonical_sha256(report.signature)
    report.architecture_hash = structural_hash(model)
    report.params = sum(p.numel() for p in model.parameters())
    report.flops = flops
    report.ok = True
    if audit_hook is not None:
        report.built_audit = audit_hook(model, report)
    if keep_model:
        report.model = model
    return report


def architecture_hash(genotype: Genotype, imgsz: int = 640) -> str:
    """Convenience: hash of the built structure; raises ValueError when the genotype does not build."""
    rep = inspect_genotype(genotype, imgsz, count_flops=False)
    if not rep.ok or rep.architecture_hash is None:
        raise ValueError(f"genotype does not build: {rep.error}")
    return rep.architecture_hash
