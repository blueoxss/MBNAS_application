"""
runtime.py  --  MBNAS random-validation-phase engine.

This module is the NEW code for the "search-space validity & model-compilation"
phase described in the MBNAS task.  It does NOT re-implement crossover/mutation:
the paper's resolution-matched modular-block crossover is reused verbatim from
`crossover_v22.py` (lifted from YOLO_GA_MUTATION_v2.2.ipynb).  Here we only add
the validation layers that the existing notebooks lacked:

    generate (compat-aware, no GA feedback)
        -> static validation
        -> symbolic shape propagation (ultralytics-faithful channels + spatial scale)
        -> real DetectionModel build
        -> forward with hooks + symbolic-vs-actual shape comparison
        -> native detector loss + backward + optimizer step
        -> acceptance / structured failure

All heavy validation runs against the *real* Ultralytics builder
(`ultralytics.nn.tasks.DetectionModel`) and the *real* detector loss.

Author: MBNAS phase-1 harness.  Reuse-first, wrapper-not-rewrite.
"""

from __future__ import annotations

import os
import io
import json
import math
import copy
import glob
import time
import random
import hashlib
import contextlib
import traceback
from dataclasses import dataclass, field, asdict
from collections import Counter, OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .crossover_v22 import NASUtils, Crossover

# ---------------------------------------------------------------------------
# 0. Module taxonomy  (mirrors ultralytics 8.3.x parse_model classification)
# ---------------------------------------------------------------------------

# Feature modules: parse_model computes c2 = make_divisible(min(args[0],max_ch)*width, 8)
FEATURE_MODULES = {
    "Classify", "Conv", "ConvTranspose", "GhostConv", "Bottleneck",
    "GhostBottleneck", "SPP", "SPPF", "C2fPSA", "C2PSA", "DWConv", "Focus",
    "BottleneckCSP", "C1", "C2", "C2f", "C3k2", "RepNCSPELAN4", "ELAN1",
    "ADown", "AConv", "SPPELAN", "C2fAttn", "C3", "C3TR", "C3Ghost",
    "DWConvTranspose2d", "C3x", "RepC3", "PSA", "SCDown", "C2fCIB",
    # custom v6/v7 blocks that exist in the repo's ultralytics (kept for completeness)
    "RepVGGBlock", "BepC3", "BepC3StageBlock", "ELANBlock",
}
# repeat-carrying feature modules (parse_model inserts n into args)
REPEAT_MODULES = {
    "BottleneckCSP", "C1", "C2", "C2f", "C3k2", "C2fAttn", "C3", "C3TR",
    "C3Ghost", "C3x", "RepC3", "C2fPSA", "C2fCIB", "C2PSA",
}
DETECT_MODULES = {"Detect", "v10Detect", "Segment", "Pose", "OBB", "WorldDetect"}
CONCAT_MODULES = {"Concat"}

# spatial-scale rules
UPSAMPLE_MODULES = {"nn.Upsample", "nn.ConvTranspose2d", "ConvTranspose"}
# modules that always halve resolution regardless of explicit stride arg
FIXED_DOWN2_MODULES = {"ADown", "SCDown", "AConv"}

# which YOLO generation a module is characteristic of (for reporting families_used)
MODULE_FAMILY = {
    "Bottleneck": "v3", "SPP": "v3",
    "C3": "v5", "SPPF": "v5", "C3x": "v5", "C3Ghost": "v5", "RepC3": "v5",
    "RepVGGBlock": "v6", "BepC3": "v6", "BepC3StageBlock": "v6",
    "ELANBlock": "v7", "ELAN1": "v7",
    "C2f": "v8", "C2": "v8",
    "RepNCSPELAN4": "v9", "ADown": "v9", "SPPELAN": "v9",
    "SCDown": "v10", "C2fCIB": "v10", "PSA": "v10", "v10Detect": "v10",
    "C3k2": "v11", "C2PSA": "v11", "C2fPSA": "v11",
}


def make_divisible(x, divisor=8):
    """ultralytics.utils.make_divisible replica."""
    return math.ceil(x / divisor) * divisor


# ---------------------------------------------------------------------------
# 1. Data contracts
# ---------------------------------------------------------------------------

@dataclass
class TensorSignature:
    """Contract carried by every graph tensor (per PHASE research interpretation)."""
    stage_id: int                 # producing layer index
    semantic_role: str            # backbone | neck_head | detect | input
    spatial_scale: int            # downsample divisor relative to input (1,2,4,8,16,32,...)
    channels: Optional[int]       # symbolic output channels (None = terminal/unknown)
    producer: str                 # module name
    legal_consumers: List[int] = field(default_factory=list)


@dataclass
class NodeShape:
    idx: int
    module: str
    from_: Any
    n: int
    sym_channels: Optional[int]
    sym_scale: Optional[int]              # divisor img//H (pyramid level, for readability)
    sym_h: Optional[int] = None          # exact symbolic H
    sym_w: Optional[int] = None          # exact symbolic W
    act_channels: Optional[int] = None
    act_scale: Optional[int] = None
    act_h: Optional[int] = None
    act_w: Optional[int] = None
    match: Optional[bool] = None


@dataclass
class ValidationFailure:
    candidate_id: str
    phase: str            # static | symbolic | build | forward | shape_compare | loss | backward | step | generate
    rule: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_row(self):
        return f"{self.phase}:{self.rule}"


# ---------------------------------------------------------------------------
# 2. Genotype extraction (JSON-serializable)
# ---------------------------------------------------------------------------

def _module_name(line):
    return str(line[2]) if isinstance(line, list) and len(line) >= 3 else "?"


def _resolved_scale_tuple(cfg: Dict):
    """(scale_key, depth, width, max_channels) actually used to COMPILE this cfg."""
    scales = cfg.get("scales")
    if scales:
        key = cfg.get("scale") or list(scales.keys())[0]   # ultralytics picks first key if unset
        d, w, mc = scales[key]
        return (key, d, w, mc)
    return (None, cfg.get("depth_multiple", 1.0), cfg.get("width_multiple", 1.0), None)


def head_globals_of(cfg: Dict) -> Dict:
    """Normalize the 8.4-only top-level head-mode keys (`end2end`, `reg_max`) that
    `reconstruct_config_from_genotype` would otherwise drop (spec_v3 §2.2). Explicit
    defaults (end2end falsy, reg_max==16) collapse to the SAME empty dict as key-absence,
    so a normalized/explicit cfg and a bare cfg fingerprint identically."""
    hg: Dict = {}
    if cfg.get("end2end"):                  # truthy only (None/False -> omitted)
        hg["end2end"] = True
    if cfg.get("reg_max", 16) != 16:        # default 16 -> omitted
        hg["reg_max"] = int(cfg["reg_max"])
    return hg


def architecture_fingerprint(cfg: Dict) -> str:
    # include the RESOLVED scale (depth/width/max_channels) — two cfgs with identical
    # backbone/head but a different active scale compile to DIFFERENT graphs, so they must
    # not collide.  (D7 fix)
    payload = {
        "backbone": cfg.get("backbone"),
        "head": cfg.get("head"),
        "nc": cfg.get("nc"),
        "resolved_scale": _resolved_scale_tuple(cfg),
    }
    # spec_v3 §2.2: only insert when non-empty, so legacy (no end2end/reg_max) fingerprints
    # stay byte-identical to the payload produced before this key existed.
    hg = head_globals_of(cfg)
    if hg:
        payload["head_globals"] = hg
    s = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def families_used(cfg: Dict) -> List[str]:
    fams = set()
    for part in ("backbone", "head"):
        for line in cfg.get(part, []) or []:
            nm = _module_name(line)
            base = nm.split(".")[-1]
            if base in MODULE_FAMILY:
                fams.add(MODULE_FAMILY[base])
    return sorted(fams)


def build_genotype(cfg: Dict, parent_ids: List[str], mode: str,
                   candidate_id: str, seed: int, repairs: Optional[List] = None) -> Dict:
    nas = NASUtils()
    backbone = cfg.get("backbone", []) or []
    head = cfg.get("head", []) or []

    # backbone modular-block slices == paper's M(P_i) stage grouping
    try:
        starts, blocks = nas.find_dna_to_list(backbone)
    except Exception:
        starts, blocks = [0], [backbone]

    stages = []
    for si, (st, blk) in enumerate(zip(starts, blocks)):
        mods = [_module_name(l) for l in blk]
        stages.append({
            "stage_index": si,
            "global_start": int(st),
            "modules": mods,
            "downsamples": sum(1 for l in blk if NASUtils._is_down_event(l)),
            # D10: raw [from, n, module, args] lines make the genotype REVERSIBLE
            "raw_lines": copy.deepcopy(blk),
        })

    # detection sources = Detect `from` list
    detection_sources = []
    routes = []
    all_lines = backbone + head
    for gi, line in enumerate(all_lines):
        nm = _module_name(line).split(".")[-1]
        if nm in CONCAT_MODULES or nm in DETECT_MODULES:
            src = line[0]
            routes.append({"idx": gi, "module": nm, "from": src})
            if nm in DETECT_MODULES:
                detection_sources = src if isinstance(src, list) else [src]

    # scale used
    scales = cfg.get("scales")
    if scales:
        scale_key = cfg.get("scale") or list(scales.keys())[0]
        d, w, mc = scales[scale_key]
        scale = {"source": "scales", "scale_key": scale_key,
                 "depth": d, "width": w, "max_channels": mc}
    else:
        scale = {"source": "multiple",
                 "depth": cfg.get("depth_multiple", 1.0),
                 "width": cfg.get("width_multiple", 1.0),
                 "max_channels": None}

    return {
        "candidate_id": candidate_id,
        "seed": seed,
        "genotype_kind": "reversible_chromosome",   # raw lines below -> exact cfg
        "nc": cfg.get("nc"),
        "n_backbone": len(backbone),
        "n_head": len(head),
        "stage_count": len(stages),
        "stages": stages,
        "neck_head": [_module_name(l) for l in head],
        "raw_backbone": copy.deepcopy(backbone),     # D10: reversible
        "raw_head": copy.deepcopy(head),             # D10: reversible
        "routes": routes,
        "detection_sources": detection_sources,
        "detection_source_count": len(detection_sources),
        "families_used": families_used(cfg),
        "scale": scale,
        "head_globals": head_globals_of(cfg),
        "repairs": repairs or [],
        "lineage": {"mode": mode, "parent_ids": parent_ids},
        "architecture_fingerprint": architecture_fingerprint(cfg),
    }


def reconstruct_config_from_genotype(genotype: Dict) -> Dict:
    """D10: rebuild the exact ultralytics runtime config from a reversible genotype."""
    cfg = {"nc": genotype.get("nc"),
           "backbone": copy.deepcopy(genotype["raw_backbone"]),
           "head": copy.deepcopy(genotype["raw_head"])}
    sc = genotype.get("scale", {})
    if sc.get("source") == "scales":
        cfg["scales"] = {sc["scale_key"]: [sc["depth"], sc["width"], sc["max_channels"]]}
        cfg["scale"] = sc["scale_key"]
    elif sc.get("source") == "multiple":
        cfg["depth_multiple"] = sc.get("depth", 1.0)
        cfg["width_multiple"] = sc.get("width", 1.0)
    # spec_v3 §2.2: reapply the normalized head-mode globals lost by the raw
    # backbone/head/scale fields above. Empty/absent -> no-op (parse_model defaults
    # end2end=None, reg_max=16 already match "no head_globals").
    cfg.update(genotype.get("head_globals") or {})
    return cfg


# ---------------------------------------------------------------------------
# 3. Symbolic shape propagation (ultralytics-faithful)
# ---------------------------------------------------------------------------

def _resolve_scale_and_depth_width(cfg: Dict):
    scales = cfg.get("scales")
    if scales:
        scale_key = cfg.get("scale") or list(scales.keys())[0]
        depth, width, max_channels = scales[scale_key]
        return depth, width, float(max_channels)
    return cfg.get("depth_multiple", 1.0), cfg.get("width_multiple", 1.0), float("inf")


def _conv_stride_from_args(args) -> int:
    # Conv args: [c_out, k, s, ...]  ->  stride at index 2
    if isinstance(args, (list, tuple)) and len(args) >= 3 and isinstance(args[2], int):
        return args[2]
    return 1


def _autopad(k, p=None):
    if p is None:
        p = k // 2 if isinstance(k, int) else [x // 2 for x in k]
    return p


def _conv_hw(h, w, k, s, p=None):
    p = _autopad(k, p)
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    return max(1, ho), max(1, wo)


def _module_out_hw(nm_base: str, args, in_hw: Tuple[int, int]) -> Tuple[int, int]:
    """Return exact output (H, W) given the module and its raw config args.
    Only the resolution-changing ops are modelled explicitly; every feature block
    (C2f/C3/C3k2/SPPF/RepNCSPELAN4/...) preserves spatial size."""
    h, w = in_hw
    if nm_base in ("nn.Upsample",) or nm_base in UPSAMPLE_MODULES:
        if nm_base in ("nn.ConvTranspose2d", "ConvTranspose"):
            s = _conv_stride_from_args(args) or 2
            return h * s, w * s
        scale = 2
        if isinstance(args, (list, tuple)) and len(args) >= 2 and isinstance(args[1], (int, float)):
            scale = int(args[1])
        return h * scale, w * scale
    if nm_base in FIXED_DOWN2_MODULES:
        # ADown/AConv: 3x3 stride-2 downsample ; SCDown: conv k(arg1) stride s(arg2)
        if nm_base == "SCDown":
            k = args[1] if isinstance(args, (list, tuple)) and len(args) >= 2 else 3
            s = args[2] if isinstance(args, (list, tuple)) and len(args) >= 3 else 2
            return _conv_hw(h, w, k, s)
        return _conv_hw(h, w, 3, 2)
    if nm_base in ("nn.MaxPool2d",):
        k = args[0] if isinstance(args, (list, tuple)) and args else 2
        s = args[1] if isinstance(args, (list, tuple)) and len(args) >= 2 else k
        p = args[2] if isinstance(args, (list, tuple)) and len(args) >= 3 else 0
        ho = (h + 2 * p - k) // s + 1
        wo = (w + 2 * p - k) // s + 1
        return max(1, ho), max(1, wo)
    if nm_base in ("nn.ZeroPad2d",):
        # args = [[l, r, t, b]] (ultralytics) or [l, r, t, b]
        pad = args[0] if isinstance(args, (list, tuple)) and args and isinstance(args[0], (list, tuple)) else args
        if isinstance(pad, (list, tuple)) and len(pad) == 4:
            l, r, t, b = pad
            return h + t + b, w + l + r
        return h, w
    if nm_base in ("Conv", "GhostConv", "DWConv", "Focus"):
        if nm_base == "Focus":
            return _conv_hw(h, w, 3, 2)
        k = args[1] if isinstance(args, (list, tuple)) and len(args) >= 2 and isinstance(args[1], int) else 1
        s = _conv_stride_from_args(args)
        # explicit padding (Conv args = [c_out, k, s, p, ...]); else autopad = k//2
        p = args[3] if isinstance(args, (list, tuple)) and len(args) >= 4 and isinstance(args[3], int) else None
        return _conv_hw(h, w, k, s, p)
    # passthrough (feature blocks, Concat handled by caller): unchanged
    return h, w


def symbolic_shape_propagate(cfg: Dict, candidate_id: str = "?",
                             img_size: int = 640) -> Tuple[List[NodeShape], List[ValidationFailure], List[TensorSignature]]:
    """
    Replicate ultralytics parse_model channel bookkeeping + track EXACT spatial
    (H, W), without building the model.  Returns (node shapes, failures, sigs).
    Exact H/W (not a lossy divisor) makes the symbolic trace match the real forward
    even for odd-size ops like nn.ZeroPad2d (YOLOv3-tiny pattern).
    """
    depth, width, max_channels = _resolve_scale_and_depth_width(cfg)
    nc = cfg.get("nc", 80)
    backbone = cfg.get("backbone", []) or []
    head = cfg.get("head", []) or []
    layers = backbone + head
    n_bb = len(backbone)

    ch: List[int] = [3]                       # running channel list (ultralytics semantics)
    hw: List[Tuple[int, int]] = [(img_size, img_size)]   # running exact (H, W) list
    outs: List[NodeShape] = []
    sigs: List[TensorSignature] = []
    failures: List[ValidationFailure] = []

    def _src_indices(f, i) -> List[int]:
        if isinstance(f, int):
            return [i + f if f < 0 else f]
        if isinstance(f, list):
            return [i + x if isinstance(x, int) and x < 0 else x for x in f]
        return [i - 1]

    for i, line in enumerate(layers):
        if not (isinstance(line, list) and len(line) >= 3):
            failures.append(ValidationFailure(candidate_id, "symbolic", "malformed_line",
                                              {"idx": i, "line": str(line)}))
            return outs, failures, sigs
        f, n, m = line[0], line[1], line[2]
        args = line[3] if len(line) >= 4 else []
        nm = str(m)
        nm_base = nm.split(".")[-1] if not nm.startswith("nn.") else nm
        role = "backbone" if i < n_bb else ("detect" if nm_base in DETECT_MODULES else "neck_head")

        # ---- channels (exact parse_model replica) ----
        try:
            if nm_base in CONCAT_MODULES:
                srcs = _src_indices(f, i)
                c2 = sum(ch[x] for x in srcs)
            elif nm_base in DETECT_MODULES:
                c2 = ch[-1] if len(ch) else nc          # terminal; value unused downstream
            elif nm_base in FEATURE_MODULES:
                a0 = args[0] if isinstance(args, (list, tuple)) and args else ch[f if isinstance(f, int) else -1]
                c2 = a0
                if c2 != nc and isinstance(c2, int):
                    c2 = make_divisible(min(c2, max_channels) * width, 8)
            else:
                # passthrough (Upsample, MaxPool, ZeroPad, BatchNorm, unknown)
                src0 = f if isinstance(f, int) else (f[0] if isinstance(f, list) and f else -1)
                c2 = ch[src0]
        except Exception as e:
            failures.append(ValidationFailure(candidate_id, "symbolic", "channel_resolve_error",
                                              {"idx": i, "module": nm, "err": str(e)[:120]}))
            return outs, failures, sigs

        # ---- spatial (exact H, W) ----
        srcs = _src_indices(f, i)
        try:
            src_hw = [hw[x] for x in srcs]
        except Exception as e:
            failures.append(ValidationFailure(candidate_id, "symbolic", "scale_resolve_error",
                                              {"idx": i, "from": f, "err": str(e)[:120]}))
            return outs, failures, sigs

        if nm_base in CONCAT_MODULES:
            # HARD CONTRACT: all concat inputs must share spatial resolution
            if len(set(src_hw)) != 1:
                failures.append(ValidationFailure(candidate_id, "symbolic", "concat_scale_mismatch",
                                                  {"idx": i, "from": f, "src_hw": src_hw}))
            out_hw = src_hw[0] if src_hw else (hw[-1] if hw else (img_size, img_size))
        elif nm_base in DETECT_MODULES:
            if len(srcs) < 1:
                failures.append(ValidationFailure(candidate_id, "symbolic", "detect_no_source",
                                                  {"idx": i, "from": f}))
            out_hw = src_hw[-1] if src_hw else (hw[-1] if hw else (img_size, img_size))
        else:
            in_hw = src_hw[0] if src_hw else (hw[-1] if hw else (img_size, img_size))
            out_hw = _module_out_hw(nm_base, args, in_hw)

        out_scale = img_size // out_hw[0] if out_hw[0] else None
        node = NodeShape(idx=i, module=nm_base, from_=f, n=n,
                         sym_channels=(None if nm_base in DETECT_MODULES else c2),
                         sym_scale=out_scale, sym_h=out_hw[0], sym_w=out_hw[1])
        outs.append(node)
        sigs.append(TensorSignature(stage_id=i, semantic_role=role,
                                    spatial_scale=out_scale,
                                    channels=(None if nm_base in DETECT_MODULES else c2),
                                    producer=nm_base,
                                    legal_consumers=[]))

        if i == 0:
            ch = []
            hw = []
        ch.append(c2)
        hw.append(out_hw)

    return outs, failures, sigs


# ---------------------------------------------------------------------------
# 4. Static validator
# ---------------------------------------------------------------------------

def static_validate(cfg: Dict, candidate_id: str = "?",
                    known_modules: Optional[set] = None) -> List[ValidationFailure]:
    fails: List[ValidationFailure] = []
    backbone = cfg.get("backbone")
    head = cfg.get("head", [])
    if not isinstance(backbone, list) or len(backbone) == 0:
        fails.append(ValidationFailure(candidate_id, "static", "empty_backbone", {}))
        return fails
    if not isinstance(head, list):
        fails.append(ValidationFailure(candidate_id, "static", "bad_head_type", {}))
        return fails

    all_lines = backbone + head
    total = len(all_lines)

    # line well-formedness + parameter domains
    for i, line in enumerate(all_lines):
        if not (isinstance(line, list) and len(line) >= 3):
            fails.append(ValidationFailure(candidate_id, "static", "malformed_line", {"idx": i, "line": str(line)}))
            continue
        f, n, m = line[0], line[1], line[2]
        nm = str(m).split(".")[-1] if not str(m).startswith("nn.") else str(m)
        if known_modules is not None and nm not in known_modules and str(m) not in known_modules:
            fails.append(ValidationFailure(candidate_id, "static", "unknown_module", {"idx": i, "module": str(m)}))
        if not isinstance(n, int) or n <= 0:
            fails.append(ValidationFailure(candidate_id, "static", "bad_repeat", {"idx": i, "n": n}))
        # from indices must reference already-produced layers (ultralytics semantics):
        #   layer 0 -> may only read the network input (from in {-1, 0})
        #   layer i>=1 -> negative -i..-1 (relative) or absolute 0..i-1 ; no forward/self refs
        srcs = f if isinstance(f, list) else [f]
        for s in srcs:
            if not isinstance(s, int):
                fails.append(ValidationFailure(candidate_id, "static", "bad_from_type", {"idx": i, "from": f}))
                continue
            if i == 0:
                if s not in (-1, 0):
                    fails.append(ValidationFailure(candidate_id, "static", "from_out_of_range",
                                                   {"idx": i, "from": f, "resolved": s}))
                continue
            absidx = i + s if s < 0 else s
            if absidx < 0 or absidx >= i:
                # forward reference (absidx >= i) or underflow (absidx < 0) is illegal
                fails.append(ValidationFailure(candidate_id, "static", "from_out_of_range",
                                               {"idx": i, "from": f, "resolved": absidx}))
        # channel/args positivity for feature modules
        if nm in FEATURE_MODULES and len(line) >= 4 and isinstance(line[3], list) and line[3]:
            a0 = line[3][0]
            if isinstance(a0, int) and a0 <= 0:
                fails.append(ValidationFailure(candidate_id, "static", "nonpositive_channels", {"idx": i, "args": line[3]}))

    # detection contract
    detect_lines = [(i, l) for i, l in enumerate(all_lines)
                    if _module_name(l).split(".")[-1] in DETECT_MODULES]
    if len(detect_lines) == 0:
        fails.append(ValidationFailure(candidate_id, "static", "no_detect_layer", {}))
    else:
        # at least one detection source
        di, dl = detect_lines[-1]
        dsrc = dl[0] if isinstance(dl[0], list) else [dl[0]]
        if len(dsrc) < 1:
            fails.append(ValidationFailure(candidate_id, "static", "detect_no_source", {"idx": di}))
        # final detect must be the last layer (ultralytics convention)
        if di != total - 1:
            fails.append(ValidationFailure(candidate_id, "static", "detect_not_last", {"idx": di, "total": total}))
        # PD constraint (paper): 1 <= |D| <= 4  (ultralytics Detect supports variable nl; cap at 4 for feasibility)
        if not (1 <= len(dsrc) <= 4):
            fails.append(ValidationFailure(candidate_id, "static", "detect_pathway_count",
                                           {"idx": di, "count": len(dsrc)}))
    return fails


# ---------------------------------------------------------------------------
# 5. Compatibility-aware random sampler  (NO GA feedback)
# ---------------------------------------------------------------------------

class CompatibilityAwareSampler:
    """
    NO-FITNESS random sampler over the mixed-YOLO modular search space. Two modes
    (D6 fix — the previous "pure random" naming was inaccurate):

      mode="random_crossover" (default): pick two parents UNIFORMLY at random from the
          config pool and apply the verbatim resolution-matched modular-block Crossover.
          -> parent_ids are NON-empty; lineage_mode = "random_crossover_no_fitness".
          This is a compatibility-aware generator, NOT parent-free pure random.

      mode="pure_random": stage-first parent-free sampling. Draw a random stage skeleton
          from ONE random parent's backbone, then independently resample each stage's
          feature block from a family-agnostic operator catalog (O(P_i)) at the matching
          resolution, plus a random head from another parent. parent_ids = [] (truly
          independent of any single parent architecture identity).

    Reproducible from `seed`. Global-random isolation: `Crossover` uses the `random`
    module, so we keep a private contiguous global-random stream (save/restore around
    each sample) => identical seed -> identical stream regardless of any score fn.
    """

    def __init__(self, configs: List[Dict], seed: int = 42, enable_modular_mutation: bool = False,
                 mode: str = "random_crossover"):
        assert mode in ("random_crossover", "pure_random")
        self.configs = configs
        self.seed = seed
        self.mode = mode
        self.enable_modular_mutation = enable_modular_mutation
        self.parent_rng = random.Random(seed)
        # family-agnostic feature-operator catalog O(P_i) observed in the pool (for pure_random)
        self._op_catalog = sorted({str(l[2]) for c in configs for l in c.get("backbone", [])
                                   if _module_name(l).split(".")[-1] in REPEAT_MODULES})
        # initialise private global-random stream
        _saved = random.getstate()
        random.seed(seed ^ 0x9E3779B9)
        self._gstate = random.getstate()
        random.setstate(_saved)
        self._counter = 0

    @property
    def lineage_mode(self):
        return "pure_random" if self.mode == "pure_random" else "random_crossover_no_fitness"

    def _clean(self, cfg):
        return copy.deepcopy({k: v for k, v in cfg.items() if not str(k).startswith("__")})

    def sample(self) -> Tuple[Dict, List[str], List[Dict]]:
        _outer = random.getstate()
        random.setstate(self._gstate)
        try:
            if self.mode == "pure_random":
                cfg, parents, repairs = self._sample_pure_random()
            else:
                cfg, parents, repairs = self._sample_crossover()
            self._gstate = random.getstate()
        finally:
            random.setstate(_outer)
        self._counter += 1
        return cfg, parents, repairs

    def _sample_crossover(self):
        c1, c2 = self.parent_rng.sample(self.configs, 2)
        p1, p2 = self._clean(c1), self._clean(c2)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cx = Crossover(p1, p2)
        cfg = cx.cfg_cross
        repairs = []
        if self.enable_modular_mutation:
            cfg, repairs = self._modular_mutation(cfg)
        return cfg, [c1.get("__name__", "p1"), c2.get("__name__", "p2")], repairs

    def _sample_pure_random(self):
        """Parent-free: take a backbone skeleton, independently resample each feature
        block's operator from O(P_i), and attach a random head. parent_ids = []."""
        base = self._clean(self.parent_rng.choice(self.configs))
        repairs = []
        for i, line in enumerate(base.get("backbone", [])):
            nm = _module_name(line).split(".")[-1]
            if nm in REPEAT_MODULES and self._op_catalog:
                new_mod = self.parent_rng.choice(self._op_catalog)
                if str(line[2]) != new_mod:
                    before = copy.deepcopy(line)
                    line[2] = new_mod
                    # keep args shape-safe: repeat modules take [c_out, ...]; drop trailing
                    # family-specific extras so parse_model uses defaults
                    if len(line) > 3 and isinstance(line[3], list) and line[3]:
                        line[3] = [line[3][0]]
                    repairs.append({"rule": "pure_random_operator_resample", "stage": i,
                                    "before": before, "after": copy.deepcopy(line)})
        return base, [], repairs

    def _modular_mutation(self, cfg):
        """Optional modular-level mutation (C_repeats / C_filters) on one backbone line.
        Deterministic-domain, records repairs.  Disabled by default in phase-1."""
        repairs = []
        bb = cfg.get("backbone", [])
        cand_idx = [i for i, l in enumerate(bb)
                    if _module_name(l).split(".")[-1] in REPEAT_MODULES]
        if cand_idx:
            i = random.choice(cand_idx)
            line = bb[i]
            before = copy.deepcopy(line)
            # C_repeats in {1,2,3}
            line[1] = random.choice([1, 2, 3])
            repairs.append({"rule": "modular_repeat_mutation", "stage": i,
                            "before": before, "after": copy.deepcopy(line)})
        return cfg, repairs


# ---------------------------------------------------------------------------
# 6. Real build + forward + native loss (executed in the yolov8/sg env)
# ---------------------------------------------------------------------------

class TorchExecutor:
    """Holds torch/ultralytics handles; runs build/forward/loss/backward/step."""

    def __init__(self, device: str = "auto", img_size: int = 640, batch_size: int = 1):
        import torch
        from ultralytics.nn.tasks import DetectionModel
        from ultralytics.cfg import get_cfg
        from ultralytics.utils import DEFAULT_CFG
        self.torch = torch
        self.DetectionModel = DetectionModel
        self._loss_args = get_cfg(DEFAULT_CFG)
        self.device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        self.img_size = img_size
        self.batch_size = batch_size
        # module registry from the live ultralytics (ground truth for static check)
        from ultralytics.nn import modules as _mods
        self.known_modules = set(dir(_mods)) | {f"nn.{x}" for x in dir(torch.nn)}

    # -- build --
    def build(self, cfg: Dict):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            model = self.DetectionModel(copy.deepcopy(cfg))
            model = model.to(self.device)
            model.args = self._loss_args
        return model

    # -- forward with hooks + actual shape trace --
    def forward_trace(self, model):
        torch = self.torch
        actual = OrderedDict()
        handles = []
        seq = model.model  # nn.Sequential of layers with .i attribute

        def mk_hook(idx):
            def hook(mod, inp, out):
                t = out
                if isinstance(t, (list, tuple)) and t:
                    t = t[0]
                if isinstance(t, dict):
                    t = list(t.values())[0]
                    if isinstance(t, (list, tuple)):
                        t = t[0]
                if torch.is_tensor(t) and t.dim() == 4:
                    actual[idx] = (int(t.shape[1]), int(t.shape[2]), int(t.shape[3]))
            return hook

        for idx, layer in enumerate(seq):
            handles.append(layer.register_forward_hook(mk_hook(idx)))
        try:
            model.eval()
            x = torch.randn(self.batch_size, 3, self.img_size, self.img_size, device=self.device)
            with torch.no_grad():
                out = model(x)
        finally:
            for h in handles:
                h.remove()
        return actual, out

    # -- native detector loss + backward + optimizer step --
    def train_smoke(self, model, n_boxes: int = 8):
        torch = self.torch
        model.train()
        imgs = torch.randn(self.batch_size, 3, self.img_size, self.img_size, device=self.device)
        bi = torch.randint(0, self.batch_size, (n_boxes,)).float().view(-1, 1)
        cls = torch.randint(0, int(model.model[-1].nc), (n_boxes,)).float().view(-1, 1)
        cx = torch.rand(n_boxes, 1) * 0.6 + 0.2
        cy = torch.rand(n_boxes, 1) * 0.6 + 0.2
        w = torch.rand(n_boxes, 1) * 0.2 + 0.05
        h = torch.rand(n_boxes, 1) * 0.2 + 0.05
        boxes = torch.cat([cx, cy, w, h], 1)
        batch = {"img": imgs, "batch_idx": bi.to(self.device),
                 "cls": cls.to(self.device), "bboxes": boxes.to(self.device)}
        loss, loss_items = model.loss(batch)
        finite = bool(torch.isfinite(loss).all())
        opt = torch.optim.SGD(model.parameters(), lr=1e-4, momentum=0.9)
        opt.zero_grad(set_to_none=True)
        loss.sum().backward()
        # gradient sanity: count trainable params, those that received a grad, finite vs non-finite
        n_trainable = sum(1 for p in model.parameters() if p.requires_grad)
        n_grad = sum(1 for p in model.parameters() if p.requires_grad and p.grad is not None)
        n_finite_grad = sum(1 for p in model.parameters()
                            if p.grad is not None and torch.isfinite(p.grad).all())
        n_nonfinite_grad = sum(1 for p in model.parameters()
                               if p.grad is not None and not torch.isfinite(p.grad).all())
        opt.step()
        return {
            "loss_finite": finite,
            "loss_value": float(loss.sum().item()),
            "n_trainable_params": n_trainable,
            "n_grad_params": n_grad,
            "n_finite_grad": n_finite_grad,
            "n_nonfinite_grad": n_nonfinite_grad,
            # grad_coverage = fraction of trainable params that received a gradient
            "grad_coverage": (n_grad / n_trainable) if n_trainable else 0.0,
            # gradient contract: at least one grad, and NONE non-finite
            "grad_finite_ok": (n_grad > 0 and n_nonfinite_grad == 0),
            "backward_ok": True,
            "step_ok": True,
        }

    def free(self, model):
        try:
            model.cpu()
        except Exception:
            pass
        del model
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


def compare_symbolic_actual(sym_nodes: List[NodeShape], actual: Dict[int, Tuple[int, int, int]],
                            img_size: int) -> Tuple[List[NodeShape], Dict[str, int]]:
    """Fill actual fields on NodeShape and compute match stats.
    Compares EXACT (channels, H, W): symbolic must equal the real forward tensor."""
    stats = {"compared": 0, "channel_match": 0, "hw_match": 0, "both_match": 0, "mismatch": 0}
    for node in sym_nodes:
        if node.idx in actual:
            c, hh, ww = actual[node.idx]
            node.act_channels = c
            node.act_h, node.act_w = hh, ww
            node.act_scale = img_size // hh if hh else None
            cm = (node.sym_channels == c) if node.sym_channels is not None else True
            hwm = (node.sym_h == hh and node.sym_w == ww)
            node.match = bool(cm and hwm)
            stats["compared"] += 1
            stats["channel_match"] += int(cm)
            stats["hw_match"] += int(hwm)
            stats["both_match"] += int(cm and hwm)
            if not (cm and hwm):
                stats["mismatch"] += 1
    return sym_nodes, stats


# ---------------------------------------------------------------------------
# 7. Experiment config + pipeline
# ---------------------------------------------------------------------------

@dataclass
class ExperimentConfig:
    population_size: int = 6
    num_generations: int = 3
    seed: int = 42
    image_size: int = 640
    batch_size: int = 1
    device: str = "auto"
    enable_native_train_smoke: bool = True
    use_random_placeholder_score: bool = True
    enable_ga_feedback: bool = False          # MUST stay False in phase-1
    enable_modular_mutation: bool = False
    sampler_mode: str = "random_crossover"    # "random_crossover" | "pure_random"
    max_attempts_per_slot: int = 40
    artifacts_dir: str = "artifacts/yolo_ga"


def load_config_pool(pool_dirs: List[str], executor: Optional["TorchExecutor"] = None,
                     verify_build: bool = True, exclusions: Optional[List[Dict]] = None) -> List[Dict]:
    """Load family base configs; optionally keep only those that build cleanly.
    D8: every skipped YAML is recorded (with reason + traceback) into `exclusions`."""
    configs = []
    seen_fp = set()
    fp_owner = {}
    for d in pool_dirs:
        for y in sorted(glob.glob(os.path.join(d, "*.yaml"))):
            name = os.path.basename(y)
            def _excl(reason, tb=""):
                if exclusions is not None:
                    exclusions.append({"config": name, "dir": d, "reason": reason, "detail": tb})
            try:
                with open(y) as f:
                    c = yaml.safe_load(f)
            except Exception as e:
                _excl("yaml_parse_failure", traceback.format_exc()); continue
            if not isinstance(c, dict):
                _excl("not_a_dict"); continue
            if not c.get("backbone"):
                _excl("missing_backbone"); continue
            if not c.get("head"):
                _excl("missing_head"); continue
            fp = architecture_fingerprint(c)
            if fp in seen_fp:
                _excl("duplicate_fingerprint", f"same as {fp_owner.get(fp)}"); continue
            c["__name__"] = name
            if verify_build and executor is not None:
                try:
                    m = executor.build(c)
                    executor.free(m)
                except Exception as e:
                    _excl("model_build_failure", f"{type(e).__name__}: {str(e)[:160]}"); continue
            seen_fp.add(fp); fp_owner[fp] = name
            configs.append(c)
    return configs


@dataclass
class CandidateRecord:
    candidate_id: str            # UNIQUE per attempt (slot_id + _aNNN)
    slot_id: str                 # human slot label gGGG_pPPP (repeats across retries)
    attempt: int
    generation: int
    population_slot: int
    fingerprint: str
    families_used: List[str]
    stage_count: int
    detection_source_count: int
    parameter_count: Optional[int]
    static_pass: bool
    symbolic_shape_pass: bool
    model_build_pass: bool
    forward_pass: bool
    shape_match_pass: bool
    native_loss_pass: bool
    autograd_pass: bool
    grad_finite_pass: bool
    grad_coverage: float
    optimizer_step_pass: bool
    shape_compared: int
    shape_mismatch: int
    repair_count: int
    placeholder_score: float
    failure_reason: str
    accepted: bool

    def csv_row(self):
        return [
            self.candidate_id, self.slot_id, self.attempt, self.generation, self.population_slot,
            self.fingerprint, "|".join(self.families_used), self.stage_count,
            self.detection_source_count,
            self.parameter_count if self.parameter_count is not None else "",
            int(self.static_pass), int(self.symbolic_shape_pass), int(self.model_build_pass),
            int(self.forward_pass), int(self.shape_match_pass), int(self.native_loss_pass),
            int(self.autograd_pass), int(self.grad_finite_pass), f"{self.grad_coverage:.3f}",
            int(self.optimizer_step_pass), self.shape_compared, self.shape_mismatch,
            self.repair_count, f"{self.placeholder_score:.4f}", self.failure_reason,
        ]


CSV_HEADER = [
    "candidate_id", "slot_id", "attempt", "generation", "population_slot",
    "architecture_fingerprint",
    "families_used", "stage_count", "detection_source_count", "parameter_count",
    "static_pass", "symbolic_shape_pass", "model_build_pass", "forward_pass",
    "shape_match_pass", "native_loss_pass", "autograd_pass", "grad_finite_pass",
    "grad_coverage", "optimizer_step_pass", "shape_compared",
    "shape_mismatch", "repair_count", "placeholder_score", "failure_reason",
]


def evaluate_candidate(cfg, genotype, executor, econf, candidate_id, gen, slot,
                       score_rng, slot_id=None, attempt=1) -> Tuple[CandidateRecord, Dict, List[ValidationFailure]]:
    """Run a single candidate through the full validation pipeline."""
    torch = executor.torch
    failures: List[ValidationFailure] = []
    flags = dict(static=False, symbolic=False, build=False, forward=False,
                 loss=False, backward=False, step=False)
    param_count = None
    shape_stats = {"compared": 0, "mismatch": 0}
    node_shapes: List[NodeShape] = []
    trainable = {}
    failure_reason = ""

    # placeholder score: strictly detached from generation (own rng)
    placeholder_score = score_rng.random() if econf.use_random_placeholder_score else 0.0

    # 1. static
    sfail = static_validate(cfg, candidate_id, known_modules=executor.known_modules)
    failures += sfail
    flags["static"] = len(sfail) == 0

    # 2. symbolic shape
    if flags["static"]:
        node_shapes, symfail, _sigs = symbolic_shape_propagate(cfg, candidate_id, econf.image_size)
        failures += symfail
        flags["symbolic"] = len(symfail) == 0
        if not flags["symbolic"]:
            failure_reason = symfail[0].as_row()
    else:
        failure_reason = sfail[0].as_row()

    # 3. build 4. forward 5. shape-compare 6. loss/backward/step
    model = None
    flags["shape_match"] = False
    flags["grad_finite"] = False
    if flags["static"] and flags["symbolic"]:
        try:
            model = executor.build(cfg)
            param_count = sum(p.numel() for p in model.parameters())
            flags["build"] = True
        except Exception as e:
            failures.append(ValidationFailure(candidate_id, "build", type(e).__name__,
                                              {"err": str(e)[:200], "traceback": traceback.format_exc()}))
            failure_reason = f"build:{type(e).__name__}"
        if flags["build"]:
            try:
                actual, out = executor.forward_trace(model)
                flags["forward"] = True
                node_shapes, shape_stats = compare_symbolic_actual(node_shapes, actual, econf.image_size)
                # D1 HARD GATE: symbolic must equal actual for every comparable node
                flags["shape_match"] = (shape_stats.get("compared", 0) > 0
                                        and shape_stats.get("mismatch", 0) == 0)
                if not flags["shape_match"]:
                    failure_reason = "shape:symbolic_actual_mismatch"
            except Exception as e:
                failures.append(ValidationFailure(candidate_id, "forward", type(e).__name__,
                                                  {"err": str(e)[:200], "traceback": traceback.format_exc()}))
                failure_reason = f"forward:{type(e).__name__}"
        if flags["forward"] and econf.enable_native_train_smoke:
            try:
                trainable = executor.train_smoke(model)
                flags["loss"] = trainable.get("loss_finite", False)
                flags["backward"] = trainable.get("backward_ok", False)
                flags["step"] = trainable.get("step_ok", False)
                # D2 HARD GATE: >=1 grad and NO non-finite gradients
                flags["grad_finite"] = trainable.get("grad_finite_ok", False)
                if not flags["loss"]:
                    failure_reason = "loss:non_finite"
                elif not flags["grad_finite"]:
                    failure_reason = "grad:non_finite_or_absent"
            except Exception as e:
                failures.append(ValidationFailure(candidate_id, "loss", type(e).__name__,
                                                  {"err": str(e)[:200], "traceback": traceback.format_exc()}))
                failure_reason = f"loss:{type(e).__name__}"
        if model is not None:
            executor.free(model)

    autograd_pass = flags["backward"] and flags["step"] and flags["grad_finite"]
    if econf.enable_native_train_smoke:
        accepted = all([flags["static"], flags["symbolic"], flags["build"], flags["forward"],
                        flags["shape_match"], flags["loss"], flags["backward"],
                        flags["grad_finite"], flags["step"]])
    else:
        accepted = all([flags["static"], flags["symbolic"], flags["build"],
                        flags["forward"], flags["shape_match"]])

    if accepted:
        failure_reason = ""
    elif not failure_reason:
        failure_reason = "unknown"

    rec = CandidateRecord(
        candidate_id=candidate_id, slot_id=(slot_id or candidate_id), attempt=attempt,
        generation=gen, population_slot=slot,
        fingerprint=genotype["architecture_fingerprint"],
        families_used=genotype["families_used"],
        stage_count=genotype["stage_count"],
        detection_source_count=genotype["detection_source_count"],
        parameter_count=param_count,
        static_pass=flags["static"], symbolic_shape_pass=flags["symbolic"],
        model_build_pass=flags["build"], forward_pass=flags["forward"],
        shape_match_pass=flags["shape_match"], native_loss_pass=flags["loss"],
        autograd_pass=autograd_pass, grad_finite_pass=flags["grad_finite"],
        grad_coverage=float(trainable.get("grad_coverage", 0.0)) if trainable else 0.0,
        optimizer_step_pass=flags["step"],
        shape_compared=shape_stats.get("compared", 0),
        shape_mismatch=shape_stats.get("mismatch", 0),
        repair_count=len(genotype.get("repairs", [])),
        placeholder_score=placeholder_score,
        failure_reason=failure_reason, accepted=accepted,
    )
    detail = {"genotype": genotype, "flags": flags, "trainable": trainable,
              "shape_stats": shape_stats,
              "node_shapes": [asdict(n) for n in node_shapes]}
    return rec, detail, failures
