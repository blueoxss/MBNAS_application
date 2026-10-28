"""Common structure representation for backends without a YAML gene (``mbnas-structure/2``, representation ``native_modules``).

RF-DETR and the ETRI controlled CNN+Transformer hybrid are built by code, not by YAML rows.  This module turns the
*built* ``nn.Module`` into the same document family the Ultralytics path produces (``search/structure.py``), but
never invents a gene: ``gene_index``/``yaml_*``/``from`` are ``None``, ``lineage`` is ``None``/``NOT_APPLICABLE`` and the
connections are only those that forward hooks (``observed``, tensor identity) or the code-fixed forward
(``native_fixed``, corroborated by the hooked shapes) support.  Everything else is listed in ``edges_unobserved``.

Declared (D), derived (R) and observed (O) values stay under separate keys (CONTRACTS_v2 section 1).  The check uses
CPU, ``torch.no_grad`` and one zeros input at the backend's fixed resolution; ``torch.fx`` is not used (the
``fx_traceable: false`` constant is taken from the I5 inventory and labelled as such).  torch is imported lazily so the API process stays import-light.
"""
from __future__ import annotations

import importlib.metadata as md
import math
import platform
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from mbnas_model_runtime import block_catalog
from mbnas_contracts.structure_doc import CHECK_NAMES, SCHEMA

BACKENDS = ("rfdetr", "etri_hybrid")
REPRESENTATION = "native_modules"
PREFIX = {"rfdetr": "rf", "etri_hybrid": "etri"}
FAMILY = {"rfdetr": "Transformer", "etri_hybrid": "Hybrid"}
RUNTIME_REQUIREMENTS = {
    "rfdetr": {"python": ">=3.10", "packages": ["torch>=2.2", "rfdetr==1.11.2", "transformers>=5.1,<6"]},
    "etri_hybrid": {"python": ">=3.9", "packages": ["torch"]},
}
_ENV_ERRORS = (ImportError, OSError)  # ModuleNotFoundError is an ImportError; HF offline cache misses are OSError


# -- small helpers ---------------------------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version(dist: str) -> str | None:
    try:
        return md.version(dist)
    except Exception:  # noqa: BLE001
        return None


def _cause(exc: BaseException) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text[:600]


def _diag(code: str, cause: str, suggestion: str, level: str | None = None, position: int | None = None,
          path: str | None = None) -> dict[str, Any]:
    out = {"code": code, "position": position, "path": path, "cause": cause, "suggestion": suggestion}
    if level is not None:
        out["evidence_level"] = level
    return out


def _checks(status: dict[str, str], level: dict[str, str | None], rows: dict[str, int] | None = None) -> list[dict[str, Any]]:
    out = []
    for name in CHECK_NAMES:
        st = status.get(name, "NOT_CHECKED")
        item: dict[str, Any] = {"name": name, "status": st, "evidence_level": level.get(name) if st != "NOT_CHECKED" else None}
        if rows and name in rows and st != "NOT_CHECKED":
            item["rows_checked"] = rows[name]
        out.append(item)
    return out


def _torch_version() -> str | None:
    try:
        import torch

        return str(torch.__version__)
    except Exception:  # noqa: BLE001
        return None


def _base_document(backend: str, model_id: str, config: dict[str, Any] | None) -> dict[str, Any]:
    return {"schema": SCHEMA, "backend": backend, "representation": REPRESENTATION, "family": FAMILY.get(backend),
            "template_id": model_id, "class_count": (config or {}).get("num_classes"), "scale": None, "yaml_hash": None,
            "architecture_hash": None, "signature_hash": None, "parameters": None, "flops": None,
            "flops_status": "NOT_MEASURED", "flops_source": None, "observation": None, "blocks": [],
            "catalog_summary": {"by_origin_family": {}, "unknown": 0}, "edges": [],
            "edges_unobserved": [], "lineage": None, "lineage_status": "NOT_APPLICABLE",
            "native_config": dict(config) if config else None}


def declared_native_document(backend: str, model_id: str, config: dict[str, Any] | None, *,
                             diagnostics: list[dict[str, Any]], status: str = "UNVERIFIED",
                             input_size: int | None = None) -> dict[str, Any]:
    """Document with no observation: declared configuration plus diagnostics; every check NOT_CHECKED."""
    doc = _base_document(backend, model_id, config)
    doc.update(status=status, diagnostics=diagnostics, input_size=input_size, checks=_checks({}, {}))
    return doc


def unavailable_document(backend: str, model_id: str, config: dict[str, Any] | None, exc: BaseException, *,
                         input_size: int | None = None, stage: str = "build") -> dict[str, Any]:
    """UNVERIFIED document for a runtime that could not build/run (never a fake PASS, never a crash)."""
    env = isinstance(exc, _ENV_ERRORS)
    req = RUNTIME_REQUIREMENTS.get(backend)
    if env:
        diag = _diag("STRUCTURE_RUNTIME_UNAVAILABLE", f"CPU structure runtime unavailable at {stage}: {_cause(exc)}",
                     "Install the listed runtime (or make the required weights available offline), then rerun the check.",
                     "built")
    else:
        diag = _diag("STRUCTURE_CHECK_FAILED", f"structure check did not complete at {stage}: {_cause(exc)}",
                     "Review the model configuration and the configured runtime.", "built")
    doc = declared_native_document(backend, model_id, config, diagnostics=[diag], input_size=input_size)
    doc["runtime_requirements"] = dict(req) if req else None
    return doc


# -- tensor bookkeeping ----------------------------------------------------------------------------------------------
def _flat(x: Any) -> list[Any]:
    """Tensors inside a (nested) container; NestedTensor-like objects contribute their ``.tensors``."""
    import torch

    if isinstance(x, torch.Tensor):
        return [x]
    if hasattr(x, "tensors") and hasattr(x, "mask"):
        return _flat(x.tensors)
    if isinstance(x, dict):
        return [t for v in x.values() for t in _flat(v)]
    if isinstance(x, (list, tuple)):
        return [t for v in x for t in _flat(v)]
    return []


def _chw(shape: list[int], spatial: bool) -> tuple[int | None, int | None, int | None]:
    """``(c, h, w)``: NCHW only for blocks that work on feature maps; otherwise c = last axis (tokens, queries, heads)."""
    if spatial and len(shape) == 4:
        return shape[1], shape[2], shape[3]
    if len(shape) >= 2:
        return shape[-1], None, None
    return None, None, None


_ATTRS = ("in_features", "out_features", "in_channels", "out_channels", "kernel_size", "stride", "padding", "groups",
          "num_groups", "num_channels", "eps", "normalized_shape", "num_embeddings", "embedding_dim", "num_heads",
          "embed_dim", "batch_first", "norm_first", "n_heads", "n_levels", "n_points", "d_model", "hidden_dim",
          "p", "negative_slope", "approximate")


def _attrs(module: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _ATTRS:
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


def _params(module: Any) -> int:
    return int(sum(p.numel() for p in module.parameters()))


class _Block:
    def __init__(self, path: str, module: Any, section: str, *, kind: str = "module") -> None:
        self.path, self.module, self.section, self.kind = path, module, section, kind
        self.index = -1
        self.declared_repeats: int | None = 1
        self.declared_basis = "single module"
        self.observed_fn: Any = None  # callable(block) -> (int|None, basis)
        self.channels: int | None = None
        self.native_config: dict[str, Any] = {}
        self.group: str | None = None
        self.unit_index: int | None = None
        self.calls: list[dict[str, Any]] = []
        self.children_fired: list[dict[str, Any]] = []
        self.note: str | None = None
        self.module_parent: Any = None  # container whose len() is the observed repeat count
        self.module_owner: Any = None  # model owning a buffer block
        self._in_ids: set[int] = set()


def _get(model: Any, path: str) -> Any | None:
    cur = model
    for part in path.split("."):
        cur = cur._modules.get(part) if hasattr(cur, "_modules") and part in cur._modules else getattr(cur, part, None)
        if cur is None:
            return None
    return cur


# -- backend plans ---------------------------------------------------------------------------------------------------
def _rfdetr_plan(model: Any, cfg: dict[str, Any]) -> tuple[list[_Block], dict[str, Any]]:
    import torch.nn as nn

    blocks: list[_Block] = []

    def add(path: str, section: str, **kw: Any) -> _Block | None:
        mod = _get(model, path)
        if mod is None or not isinstance(mod, nn.Module):
            return None
        b = _Block(path, mod, section)
        for k, v in kw.items():
            setattr(b, k, v)
        blocks.append(b)
        return b

    width = None
    try:
        from rfdetr.models.backbone.dinov2 import size_to_width

        width = size_to_width.get(str(cfg.get("encoder", "")).rsplit("_", 1)[-1])
    except Exception:  # noqa: BLE001
        pass
    idx = cfg.get("out_feature_indexes") or []
    add("backbone.0.encoder", "backbone", declared_repeats=len(idx), declared_basis="len(out_feature_indexes)",
        observed_fn=lambda b: (len(b.calls[0]["outputs"]) if b.calls else None, "len(forward output feature maps)"),
        channels=width, native_config={"encoder": cfg.get("encoder"), "out_feature_indexes": idx,
                                       "patch_size": cfg.get("patch_size"), "num_windows": cfg.get("num_windows")})
    proj = _get(model, "backbone.0.projector")
    add("backbone.0.projector", "backbone", declared_repeats=len(cfg.get("projector_scale") or []),
        declared_basis="len(projector_scale)",
        observed_fn=lambda b: (len(b.module.stages) if hasattr(b.module, "stages") else None, "len(projector.stages)"),
        channels=cfg.get("hidden_dim"), native_config={"projector_scale": cfg.get("projector_scale")})
    add("backbone.1", "backbone", note="positional encoding (PositionEmbeddingSine)")
    nq = (cfg.get("num_queries") or 0) * (cfg.get("group_detr") or 0) or None
    for name in ("refpoint_embed", "query_feat"):
        add(name, "transformer", declared_repeats=nq, declared_basis="num_queries*group_detr",
            observed_fn=lambda b: (getattr(b.module, "num_embeddings", None), "Embedding.num_embeddings"),
            channels=getattr(_get(model, name), "embedding_dim", None))
    for name in ("enc_output", "enc_output_norm", "enc_out_bbox_embed", "enc_out_class_embed"):
        add(f"transformer.{name}", "transformer", declared_repeats=cfg.get("group_detr"), declared_basis="group_detr",
            observed_fn=lambda b: (len(b.module) if hasattr(b.module, "__len__") else None, "len(ModuleList)"))
    add("transformer.decoder.ref_point_head", "transformer", channels=cfg.get("hidden_dim"))
    layers = _get(model, "transformer.decoder.layers")
    n = len(layers) if layers is not None else 0
    for i in range(n):
        add(f"transformer.decoder.layers.{i}", "transformer", declared_repeats=cfg.get("dec_layers"),
            declared_basis="dec_layers", observed_fn=lambda b: (len(b.module_parent), "len(transformer.decoder.layers)"),
            channels=cfg.get("hidden_dim"), group="transformer.decoder.layers", unit_index=i,
            native_config={k: cfg.get(k) for k in ("sa_nheads", "ca_nheads", "dec_n_points", "hidden_dim")})
        blocks[-1].module_parent = layers
    add("transformer.decoder.norm", "transformer", channels=cfg.get("hidden_dim"))
    add("class_embed", "head", note="classification head (outputs num_classes + 1 logits)")
    add("bbox_embed", "head", note="box regression head")
    return blocks, {"fixed_resolution": cfg.get("resolution"), "roots": ("backbone", "transformer")}


def _etri_plan(model: Any, cfg: dict[str, Any]) -> tuple[list[_Block], dict[str, Any]]:
    import torch.nn as nn

    blocks: list[_Block] = []
    dim = cfg.get("dim")
    emb = model.embedding
    patch = cfg.get("patch")
    if isinstance(getattr(emb, "layers", None), nn.Sequential):
        total = len(emb.layers)
        declared = 2 * int(math.log2(patch)) if isinstance(patch, int) and patch > 1 else None
        for i in range(total):
            b = _Block(f"embedding.layers.{i}", emb.layers[i], "stem")
            b.declared_repeats, b.declared_basis = declared, "2*log2(patch) conv blocks"
            b.observed_fn = lambda blk: (len(blk.module_parent), "len(embedding.layers)")
            b.module_parent, b.group, b.unit_index = emb.layers, "embedding.layers", i
            b.channels = dim if i == total - 1 else None
            blocks.append(b)
    else:
        b = _Block("embedding", emb, "stem")
        b.channels = dim
        blocks.append(b)
    if "position" in model._buffers or hasattr(model, "position"):
        b = _Block("position", None, "encoder", kind="buffer")
        b.note = "registered buffer (sin/cos position table), added to the stem tokens in ControlledDetector.forward"
        b.channels = dim
        blocks.append(b)
    enc = model.spatial_encoder
    seq = enc.layers if hasattr(enc, "layers") else enc
    for i, (name, child) in enumerate(seq.named_children()):
        path = f"spatial_encoder.layers.{name}" if hasattr(enc, "layers") else f"spatial_encoder.{name}"
        b = _Block(path, child, "encoder")
        b.declared_repeats, b.declared_basis = cfg.get("depth"), "depth"
        b.observed_fn = lambda blk: (len(blk.module_parent), "len(spatial_encoder layers)")
        b.module_parent, b.group, b.unit_index = seq, "spatial_encoder.layers", i
        b.channels = dim
        b.native_config = {"dim": dim, "heads": cfg.get("heads"), "ffn": (dim * 4) if isinstance(dim, int) else None}
        blocks.append(b)
    for name, section in (("norm", "encoder"), ("class_head", "head"), ("box_head", "head")):
        mod = getattr(model, name, None)
        if mod is not None:
            b = _Block(name, mod, section)
            b.channels = dim
            blocks.append(b)
    return blocks, {"fixed_resolution": cfg.get("imgsz"), "roots": ("embedding", "spatial_encoder")}


def _sweep(model: Any, blocks: list[_Block], roots: tuple[str, ...]) -> list[str]:
    """Add blocks for parameter-carrying children no plan entry covers; return parameter-free ones that were skipped."""
    covered = {b.path for b in blocks}
    ids = set()
    for b in blocks:
        if b.module is not None:
            ids.update(id(m) for m in b.module.modules())
    skipped: list[str] = []

    def walk(mod: Any, prefix: str) -> None:
        for name, child in mod.named_children():
            path = f"{prefix}.{name}" if prefix else name
            if path in covered:
                continue
            if any(c.startswith(path + ".") for c in covered):  # ancestor of a covered block: look inside
                walk(child, path)
                continue
            if id(child) in ids:
                continue
            if _params(child) == 0 and not list(child.buffers()):
                skipped.append(path)
                continue
            top = path.split(".", 1)[0]
            blocks.append(_Block(path, child, top if top in ("backbone", "transformer") else "head"))
            ids.update(id(m) for m in child.modules())

    walk(model, "")
    return skipped


# -- the observation -------------------------------------------------------------------------------------------------
def _run_hooks(model: Any, blocks: list[_Block], zeros: Any) -> dict[str, Any]:
    """One forward under hooks.  Returns observed producer edges and scope observations."""
    import torch
    import torch.nn as nn

    produced: dict[int, tuple[int, str]] = {}
    keep: list[Any] = []  # keeps tensors alive so ids stay unique for the whole run
    edges: dict[tuple[int, int], dict[str, Any]] = {}
    handles = []
    scopes: dict[str, dict[str, Any]] = {}
    produced[id(zeros)] = (-1, "model_input")
    keep.append(zeros)

    def shapes(ts: list[Any]) -> list[list[int]]:
        return [list(t.shape) for t in ts]

    def make(block: _Block) -> tuple[Any, Any]:
        def pre(mod: Any, args: tuple, kwargs: dict) -> None:
            items = [(f"arg{i}", t) for i, a in enumerate(args) for t in _flat(a)]
            items += [(f"kw:{k}", t) for k, v in kwargs.items() for t in _flat(v)]
            ins = []
            for name, t in items:
                src = produced.get(id(t))
                ins.append({"name": name, "shape": list(t.shape), "source_index": None if src is None else src[0],
                            "source": None if src is None else ("model_input" if src[0] < 0 else blocks[src[0]].path)})
                if src is not None and src[0] >= 0 and src[0] != block.index:
                    e = edges.setdefault((src[0], block.index), {"tensors": [], "shape": list(t.shape)})
                    if name not in e["tensors"]:
                        e["tensors"].append(name)
            block.calls.append({"inputs": ins, "outputs": [], "output_shapes": []})
            block._in_ids = {id(t) for _, t in items}

        def post(mod: Any, args: tuple, output: Any) -> None:
            outs = _flat(output)
            call = block.calls[-1]
            call["output_shapes"] = shapes(outs)
            call["outputs"] = [s[1:] for s in call["output_shapes"]]
            for t in outs:
                if id(t) not in block._in_ids:
                    produced[id(t)] = (block.index, block.path)
                    keep.append(t)
        return pre, post

    for b in blocks:
        if b.module is None:
            continue
        if isinstance(b.module, (nn.ModuleList, nn.ModuleDict)):
            kids = list(b.module.named_children())[:2]  # a container has no forward: look at its first elements
            for name, kid in kids:
                rec = {"path": f"{b.path}.{name}", "module": type(kid).__name__, "calls": 0, "inputs": None, "outputs": None}
                b.children_fired.append(rec)

                def kpre(mod: Any, args: tuple, kwargs: dict, rec: dict = rec) -> None:
                    rec["calls"] += 1
                    if rec["inputs"] is None:
                        rec["inputs"] = [list(t.shape) for a in args for t in _flat(a)]

                def kpost(mod: Any, args: tuple, output: Any, rec: dict = rec) -> None:
                    if rec["outputs"] is None:
                        rec["outputs"] = [list(t.shape) for t in _flat(output)]
                handles.append(kid.register_forward_pre_hook(kpre, with_kwargs=True))
                handles.append(kid.register_forward_hook(kpost))
            continue
        pre, post = make(b)
        handles.append(b.module.register_forward_pre_hook(pre, with_kwargs=True))
        handles.append(b.module.register_forward_hook(post))
    for path in ("backbone", "transformer"):  # container scopes (not blocks): context for the flows that cross blocks
        mod = _get(model, path)
        if isinstance(mod, nn.Module):
            rec = scopes.setdefault(path, {"module_path": path, "module_class": type(mod).__name__, "calls": 0})

            def spre(m: Any, args: tuple, kwargs: dict, rec: dict = rec) -> None:
                rec["calls"] += 1
                if "inputs" not in rec:
                    rec["inputs"] = [list(t.shape) for a in args for t in _flat(a)]

            def spost(m: Any, args: tuple, output: Any, rec: dict = rec) -> None:
                if "outputs" not in rec:
                    rec["outputs"] = [list(t.shape) for t in _flat(output)]
            handles.append(mod.register_forward_pre_hook(spre, with_kwargs=True))
            handles.append(mod.register_forward_hook(spost))
    try:
        with torch.no_grad():
            model(zeros)
    finally:
        for h in handles:
            h.remove()
    return {"edges": edges, "scopes": list(scopes.values())}


def _fx_record(model: Any) -> dict[str, Any]:
    """``torch.fx`` is not used (WP text): the constant comes from the I5 inventory, not from a trace run here."""
    return {"fx_traceable": False,
            "fx_basis": "torch.fx is not used; value taken from docs/integration_20261021/inv_I5_adapters.md (not traceable), not re-measured"}


def _dinov2_state(cfg: dict[str, Any]) -> str | None:
    """RF-DETR scratch build: DINOv2 hub weights are loaded only for patch 14 and the stock positional grid."""
    try:
        patch, pos = int(cfg["patch_size"]), int(cfg["positional_encoding_size"])
    except (KeyError, TypeError, ValueError):
        return None
    return "loaded" if patch == 14 and pos * patch == 518 else "skipped"


# fixed code flows, corroborated by hooked shapes: (source path, target path, citation, corroboration)
def _rf_fixed(blocks: list[_Block]) -> list[tuple[str, str, str, str]]:
    cite = ("LWDETR.forward: hs=transformer(...)[0]; TransformerDecoder.forward: intermediate.append(self.norm(output)); "
            "torch.stack(intermediate) -> bbox_embed(hs) / class_embed(hs)")
    return [("transformer.decoder.norm", "class_embed", cite, "stack_leading"),
            ("transformer.decoder.norm", "bbox_embed", cite, "stack_leading")]


def _etri_fixed(blocks: list[_Block]) -> list[tuple[str, str, str, str]]:
    stems = [b.path for b in blocks if b.path.startswith("embedding")]
    encs = [b.path for b in blocks if b.path.startswith("spatial_encoder")]
    out = []
    if stems and encs:
        out.append((stems[-1], encs[0], "ControlledDetector.forward: tokens = self.embedding(images) + self.position; "
                    "CNNStem.forward: layers(images).flatten(2).transpose(1, 2)", "flatten_tokens"))
        if any(b.path == "position" for b in blocks):
            out.append(("position", encs[0], "ControlledDetector.forward: tokens = self.embedding(images) + self.position",
                        "buffer_add"))
    return out


def _corroborate(kind: str, src: _Block, dst: _Block) -> tuple[bool, str]:
    di = dst.calls[0]["inputs"] if dst.calls else []
    dshape = di[0]["shape"] if di else None
    if kind == "buffer_add":
        buf = getattr(src.module_owner, "position", None)
        shape = list(buf.shape) if buf is not None else None
        ok = dshape is not None and shape is not None and shape[1:] == dshape[1:]
        return ok, f"buffer {shape} broadcasts onto layer input {dshape}"
    so = src.calls[0]["output_shapes"][0] if src.calls and src.calls[0]["output_shapes"] else None
    if so is None or dshape is None:
        return False, "source or target shape not observed"
    if kind == "flatten_tokens":
        ok = len(so) == 4 and len(dshape) == 3 and dshape[1] == so[2] * so[3] and dshape[2] == so[1]
        return ok, f"{so} flatten(2).transpose(1,2) -> {[so[0], so[2] * so[3], so[1]] if len(so) == 4 else '?'}; layer input {dshape}"
    if kind == "stack_leading":
        ok = len(dshape) == len(so) + 1 and dshape[1:] == so
        return ok, f"stack of per-layer outputs {so} -> {dshape}"
    return False, "unknown corroboration"


def observe_native_structure(backend: str, model: Any, *, input_size: int | None, model_id: str,
                             config: dict[str, Any]) -> dict[str, Any]:
    """``mbnas-structure/2`` document of a built RF-DETR / ETRI hybrid module (see module docstring).

    ``model`` is the plain ``nn.Module`` (LWDETR / ControlledDetector) already built on CPU; ``config`` is the declared
    native configuration (RF-DETR ``model_config`` fields, ETRI ``make_detector`` keywords).  Never raises for model
    problems: a wrong input size is ``FAIL INPUT_SIZE_INVALID``, an environment error is ``UNVERIFIED``.
    """
    if backend not in BACKENDS:
        raise ValueError(f"unsupported native backend {backend!r}")
    import torch

    from mbnas_model_runtime.adapters.common import structural_hash

    was_training = model.training
    try:
        doc = _observe(backend, model, input_size, model_id, config, torch, structural_hash)
    finally:
        model.train(was_training)
    # DD-38: design-origin catalog of every block (this is an explicit check, so source_path may be resolved)
    doc["catalog_summary"] = block_catalog.attach_native_catalog(doc.get("blocks") or [], backend, resolve_source=True)
    return doc


def _observe(backend: str, model: Any, input_size: int | None, model_id: str, config: dict[str, Any], torch: Any,
             structural_hash: Any) -> dict[str, Any]:
    plan, meta = (_rfdetr_plan if backend == "rfdetr" else _etri_plan)(model, config)
    fixed = meta["fixed_resolution"]
    skipped = _sweep(model, plan, meta["roots"])
    for i, b in enumerate(plan):
        b.index = i
        if b.kind == "buffer":
            b.module_owner = model
    size = fixed if input_size is None else input_size
    arch_hash = structural_hash(model)
    tag = arch_hash[7:19]
    total = _params(model)
    counts = Counter(id(m) for _, m in model.named_modules(remove_duplicate=False))

    def block_doc(b: _Block) -> dict[str, Any]:
        mod = b.module
        internal = []
        if mod is not None:
            for rel, sub in mod.named_modules(remove_duplicate=False):
                if rel:
                    internal.append({"path": f"{b.path}.{rel}", "module": type(sub).__qualname__,
                                     "parameters": [[n, list(p.shape)] for n, p in sub.named_parameters(recurse=False)],
                                     "attributes": _attrs(sub), "shared": counts[id(sub)] > 1})
        return {
            "id": f"block-{b.index:04d}", "block_id": f"block-{b.index:04d}",
            "stable_id": f"{PREFIX[backend]}:{b.path}@{tag}", "index": b.index, "position": b.index,
            "gene_index": None, "name": b.path, "module": None,
            "module_class": type(mod).__name__ if mod is not None else "Tensor", "wrapper": None, "wrapped_module": None,
            "native_path": b.path, "native_kind": b.kind, "section": b.section,
            "section_basis": "native:" + (b.path.rsplit(".", 1)[0] if "." in b.path else b.path),
            "connection_source": "native_fixed", "yaml_section": None, "yaml_index": None, "yaml_path": None,
            "from": None, "from_resolved": None, "repeats": None, "args": None,
            "declared": {"repeats": b.declared_repeats, "channels": b.channels},
            "repeats_detail": {"declared": b.declared_repeats, "scaled": None, "observed": None,
                               "declared_basis": b.declared_basis, "observed_basis": None, "group": b.group,
                               "unit_index": b.unit_index},
            "native_config": b.native_config or None, "note": b.note,
            "effective": {"params": _params(mod) if mod is not None else 0},
            "internal_layers": internal, "checks_local": [],
            "internal_mapping_status": "RECORDED" if mod is not None else "UNVERIFIED",
        }

    docs = [block_doc(b) for b in plan]
    doc = _base_document(backend, model_id, config)
    doc.update(input_size=size, architecture_hash=arch_hash, parameters=total, fixed_resolution=fixed,
               blocks=docs, parameters_unattributed=total - sum(d["effective"]["params"] for d in docs),
               parameter_free_modules_not_listed=skipped)
    status: dict[str, str] = {}
    level: dict[str, str | None] = {}
    rows: dict[str, int] = {}
    diagnostics: list[dict[str, Any]] = []
    missing = [b.path for b in plan if b.kind == "module" and b.module is None]
    status["module_support"], level["module_support"], rows["module_support"] = "PASS", "built", len(plan)
    status["cpu_build"], level["cpu_build"] = "PASS", "built"
    if size != fixed:
        diagnostics.append(_diag("INPUT_SIZE_INVALID", f"{model_id} is built for a fixed {fixed}x{fixed} input; "
                                 f"{size} was requested", f"Check at {fixed}.", "declared", path="input_size"))
        doc.update(status="FAIL", diagnostics=diagnostics, checks=_checks(status, level, rows))
        return doc
    zeros = torch.zeros(1, 3, size, size)
    model.eval()
    try:
        run = _run_hooks(model, plan, zeros)
    except _ENV_ERRORS as exc:
        d = unavailable_document(backend, model_id, config, exc, input_size=size, stage="forward")
        return d
    except Exception as exc:  # noqa: BLE001 - failures are data
        status["reference_forward"], level["reference_forward"] = "FAIL", "forward"
        diagnostics.append(_diag("STRUCTURE_BUILD_FAILED", f"reference forward failed: {_cause(exc)}",
                                 "Correct the model configuration and rerun.", "forward"))
        doc.update(status="FAIL", diagnostics=diagnostics, checks=_checks(status, level, rows))
        return doc
    status["reference_forward"], level["reference_forward"] = "PASS", "forward"
    # --- fill per-block observations
    for b, d in zip(plan, docs):
        eff = d["effective"]
        rd = d["repeats_detail"]
        if b.observed_fn is not None:
            try:
                obs, basis = b.observed_fn(b)
            except Exception:  # noqa: BLE001
                obs, basis = None, "unavailable"
        elif b.kind == "module" and b.module is not None:
            obs, basis = 1, "single_module"
        else:
            obs, basis = None, "not a module"
        rd["observed"], rd["observed_basis"] = obs, basis
        eff["repeats"] = obs
        eff["repeats_scaled"] = None
        # contract enum (.m|.blocks|sequential_wrapper|sequential|single_module|unobserved): native counts that fit none of
        # them stay null here and are named in repeats_detail.observed_basis (contract request R-S2-1)
        eff["repeats_source"] = ("single_module" if basis == "single_module" else
                                 "sequential" if isinstance(getattr(b, "module_parent", None), torch.nn.Sequential) else None)
        eff["calls"] = len(b.calls)
        spatial = b.section in ("backbone", "stem")
        if b.calls:
            c0 = b.calls[0]
            ins = []
            for it in c0["inputs"]:
                c, h, w = _chw(it["shape"], spatial)
                ins.append({"source": it["source_index"], "source_path": it["source"], "name": it["name"],
                            "shape": it["shape"], "c": c, "h": h, "w": w})
            eff.update(inputs=ins, outputs=c0["outputs"], output_shapes=c0["output_shapes"])
            if ins:
                eff["c_in"], eff["h_in"], eff["w_in"] = ins[0]["c"], ins[0]["h"], ins[0]["w"]
            if c0["output_shapes"]:
                c, h, w = _chw(c0["output_shapes"][0], spatial)
                eff["c_out"], eff["h_out"], eff["w_out"] = c, h, w
                eff["stride"] = (size / h) if h else None
                if h is None and len(c0["output_shapes"][0]) == 3 and backend == "etri_hybrid":
                    eff["tokens_out"] = c0["output_shapes"][0][1]
                    eff["stride"] = size / model.grid if hasattr(model, "grid") and model.grid else None
            d["connection_source"] = "observed"
        else:
            eff.update(inputs=None, outputs=None, output_shapes=None)
            if b.children_fired:
                eff["observed_children"] = b.children_fired
            if b.kind == "buffer":
                buf = getattr(model, b.path, None)
                eff["buffers"] = [{"name": b.path, "shape": list(buf.shape)}] if buf is not None else None
    by_path = {b.path: b for b in plan}
    # --- edges: hook-confirmed (tensor identity) first, then corroborated code-fixed flows
    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for (s, t), info in sorted(run["edges"].items(), key=lambda kv: (kv[0][1], kv[0][0])):
        edges.append({"source": s, "target": t, "from_ref": None, "connection_source": "observed",
                      "evidence": {"kind": "tensor_identity", "tensors": info["tensors"], "shape": info["shape"]}})
        seen.add((s, t))
    fixed_flows = (_rf_fixed if backend == "rfdetr" else _etri_fixed)(plan)
    problems: list[tuple[int, str]] = []
    for sp, tp, cite, kind in fixed_flows:
        s, t = by_path.get(sp), by_path.get(tp)
        if s is None or t is None or (s.index, t.index) in seen:
            continue
        if not t.calls or (s.kind == "module" and not s.calls):  # both ends must have been observed
            continue
        ok, detail = _corroborate(kind, s, t)
        edges.append({"source": s.index, "target": t.index, "from_ref": None, "connection_source": "native_fixed",
                      "evidence": {"kind": "code_fixed+shape", "citation": cite, "corroboration": detail,
                                   "corroborated": ok}})
        seen.add((s.index, t.index))
        if not ok:
            problems.append((t.index, f"{sp} -> {tp}: {detail}"))
    doc["edges"] = edges
    # --- checks
    judged = len(edges)
    if judged:
        status["channels_spatial_stride"], level["channels_spatial_stride"], rows["channels_spatial_stride"] = (
            "FAIL" if problems else "PASS", "forward", judged)
    for pos, cause in problems:
        diagnostics.append(_diag("SHAPE_MISMATCH", cause, "The code-fixed connection does not match the hooked shapes.",
                                 "forward", pos, plan[pos].path))
    ncls = config.get("num_classes")
    cls_block = by_path.get("class_embed") or by_path.get("class_head")
    box_block = by_path.get("bbox_embed") or by_path.get("box_head")
    cls_out = cls_block.calls[0]["output_shapes"][0] if cls_block and cls_block.calls and cls_block.calls[0]["output_shapes"] else None
    box_out = box_block.calls[0]["output_shapes"][0] if box_block and box_block.calls and box_block.calls[0]["output_shapes"] else None
    if cls_out is not None and isinstance(ncls, int):
        bad = cls_out[-1] != ncls + 1
        status["class_count"], level["class_count"], rows["class_count"] = ("FAIL" if bad else "PASS"), "forward", 1
        if bad:
            diagnostics.append(_diag("CLASS_COUNT_INVALID", f"class head emits {cls_out[-1]} logits, expected "
                                     f"num_classes+1={ncls + 1}", "Build the model with the requested class count.",
                                     "forward", cls_block.index, cls_block.path))
    if cls_out is not None and box_out is not None:
        bad = box_out[-1] != 4
        status["head_connection"], level["head_connection"], rows["head_connection"] = (
            "FAIL" if bad else "PASS"), "forward", 2
        if bad:
            diagnostics.append(_diag("HEAD_INPUT_INVALID", f"box head emits width {box_out[-1]}, expected 4",
                                     "Check the box head definition.", "forward", box_block.index, box_block.path))
        hooked_in = [bl for bl in (cls_block, box_block) if any(e["target"] == bl.index for e in edges)]
        if len(hooked_in) < 2:  # a head without an established producer cannot be judged as connected
            status["head_connection"], level["head_connection"] = "NOT_CHECKED", None
    # --- unobserved flows
    has_in = {e["target"] for e in edges}
    has_out = {e["source"] for e in edges}
    unobs: list[dict[str, str]] = []
    for b in plan:
        if b.kind == "buffer":
            if b.index in has_out:
                continue
            unobs.append({"module_path": b.path, "reason": "registered buffer, not a module call: no hook can fire; "
                          "its use is part of the parent forward"})
        elif not b.calls:
            fired = [c for c in b.children_fired if c["calls"]]
            if b.children_fired:
                reason = (f"container ({type(b.module).__name__}) without its own forward; elements are indexed inside "
                          f"the parent forward; {len(fired)} of the first {len(b.children_fired)} elements fired under hooks")
            elif isinstance(b.module, torch.nn.Embedding):
                reason = "learned query table whose weight is read directly in the parent forward (never called as a module)"
            else:
                reason = "module was not called in the reference forward"
            unobs.append({"module_path": b.path, "reason": reason})
        else:
            parts = []
            if b.index not in has_in and b.index != 0:
                parts.append("inputs come from tensor ops or unhooked modules inside the parent forward")
            if b.index not in has_out and b.section != "head":
                parts.append("outputs are consumed by tensor ops or unhooked modules inside the parent forward")
            if parts:
                unobs.append({"module_path": b.path, "reason": "hooked (shapes recorded) but " + "; ".join(parts)})
    if backend == "rfdetr":
        unobs.append({"module_path": "backbone.0.projector -> transformer.*",
                      "reason": "code-fixed flow (LWDETR.forward: backbone -> transformer) whose internals (feature "
                                "flatten/concat, two-stage proposal selection, deformable attention sampling) are tensor "
                                "ops inside transformer.forward; only the transformer container in/out was hooked"})
    doc["edges_unobserved"] = unobs
    doc["observed_scopes"] = run["scopes"]
    # repeat groups (declared vs observed side by side)
    groups: dict[str, dict[str, Any]] = {}
    for b, d in zip(plan, docs):
        if b.group:
            g = groups.setdefault(b.group, {"group": b.group, "declared": b.declared_repeats,
                                            "declared_basis": b.declared_basis, "observed": d["repeats_detail"]["observed"],
                                            "blocks": []})
            g["blocks"].append(d["id"])
    doc["repeat_groups"] = list(groups.values())
    # --- observation record
    obs = {"method": "forward_hook", "device": "cpu", "imgsz": size, "python": platform.python_version(),
           "torch": _torch_version(), "dinov2_weights": None, "checked_at": _now(), "report_origin": "executed"}
    obs.update(_fx_record(model))
    if backend == "rfdetr":
        obs.update(rfdetr=_version("rfdetr"), transformers=_version("transformers"), dinov2_weights=_dinov2_state(config))
    else:
        try:
            from mbnas_model_runtime.adapters.etri_hybrid import NOTEBOOK_BLOB_SHA1

            obs["source_blob_sha1"] = NOTEBOOK_BLOB_SHA1
        except Exception:  # noqa: BLE001
            pass
    doc["observation"] = obs
    doc["status"] = "FAIL" if diagnostics else "PASS"
    doc["diagnostics"] = diagnostics
    doc["checks"] = _checks(status, level, rows)
    return doc
