"""Child program of tests/owner/search/test_operators_parity.py (WP-1 contract §6.11, DD-A29; design reused from the
WP-1C parity check, C-cross-5). CPU only; every interpreter is started with -I -B.

  --mode ref:      synthetic package ``yolo_ga_runtime`` whose __path__ is the snapshot directory
                   build/source_snapshot/yolo_ga_runtime (package __init__ NOT executed; modules are loaded from the files
                   by the import system), the original ``mixed_blocks.register_ultralytics_blocks()`` (AST patch + exec),
                   seed variants from the original ``mixed_search_space.make_mixed_seed_variants``;
  --mode vendored: ``mbnas_model_runtime.operators.ensure_registered()`` only (no yolo_ga_runtime anywhere), building the
                   SAME YAML dicts that the ref child produced.
Each builds every YAML with ultralytics ``DetectionModel`` on CPU (torch.manual_seed(0) first) and records: module
class-name sequence, qualified class sequence, parameter count, structural_hash (raw and with the operator module path
normalised), per-layer type/np/i/f, save list, state digest and a 64 px forward (output shapes + digest).
:func:`compare` is called by the test in the driver process.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

REF_PREFIX = "yolo_ga_runtime.mixed_blocks."
VEN_PREFIX = "mbnas_model_runtime.operators.etri_mixed_blocks."
NORM_PREFIX = "<etri_mixed_blocks>."


def _digest_tensors(obj) -> str:
    import torch

    h = hashlib.sha256()

    def walk(x, path):
        if torch.is_tensor(x):
            t = x.detach().cpu().contiguous()
            h.update(f"{path}|{list(t.shape)}|{t.dtype}|".encode())
            h.update(t.reshape(-1).view(torch.uint8).numpy().tobytes() if t.numel() else b"")
        elif isinstance(x, dict):
            for k in sorted(x, key=str):
                walk(x[k], f"{path}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        else:
            h.update(f"{path}={x!r};".encode())

    walk(obj, "$")
    return h.hexdigest()


def _shapes(x):
    import torch

    if torch.is_tensor(x):
        return list(x.shape)
    if isinstance(x, (list, tuple)):
        return [_shapes(v) for v in x]
    if isinstance(x, dict):
        return {k: _shapes(v) for k, v in sorted(x.items())}
    return repr(type(x))


def _norm(s: str) -> str:
    return s.replace(REF_PREFIX, NORM_PREFIX).replace(VEN_PREFIX, NORM_PREFIX)


def build_record(cfg: dict) -> dict:
    import torch
    from ultralytics.nn.tasks import DetectionModel

    from mbnas_contracts.util.canon import canonical_sha256
    from mbnas_model_runtime.adapters.common import ARCH_HASH_SCHEME, structural_hash

    torch.manual_seed(0)
    model = DetectionModel(copy.deepcopy(cfg), ch=3, nc=cfg.get("nc"), verbose=False)
    model.eval()
    qual = [f"{type(m).__module__}.{type(m).__qualname__}" for m in model.modules()]
    state = [[k, list(v.shape), str(v.dtype)] for k, v in model.state_dict().items()]
    norm_hash = canonical_sha256({"scheme": ARCH_HASH_SCHEME, "modules": [_norm(q) for q in qual], "state": state})
    torch.manual_seed(1)
    x = torch.randn(1, 3, 64, 64)
    with torch.no_grad():
        y = model(x)
    layers = [{"i": m.i, "f": m.f, "type": _norm(m.type), "np": int(m.np)} for m in model.model]
    return {"class_names": [type(m).__name__ for m in model.modules()], "qualified_normalised": [_norm(q) for q in qual],
            "qualified_raw": qual, "params": int(sum(p.numel() for p in model.parameters())),
            "structural_hash_raw": structural_hash(model), "structural_hash_normalised": norm_hash,
            "layers": layers, "save": list(model.save), "stride": [float(s) for s in model.stride],
            "state_digest": _digest_tensors(model.state_dict()), "forward64_shapes": _shapes(y),
            "forward64_digest": _digest_tensors(y)}


def _base_yaml(family: str, nc: int) -> dict:
    """Same as mbnas search space ``load_base_yaml``: installed ultralytics cfg with nc/scale set."""
    import re

    import ultralytics
    import yaml

    m = re.fullmatch(r"(yolo11|yolo26)([nslmx])", family)
    rel = {"yolo11": "cfg/models/11/yolo11.yaml", "yolo26": "cfg/models/26/yolo26.yaml"}[m.group(1)]
    data = yaml.safe_load((Path(ultralytics.__file__).resolve().parent / rel).read_text())
    data["nc"], data["scale"] = int(nc), m.group(2)
    return data


def _strip(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if not (k.startswith("__") and k.endswith("__"))}


def _extra_global(transformer: dict) -> dict:
    """Extra coverage of the +2 repeat path: transformer_backbone with NASGlobalTransformer rows (repeats 4 -> n=2 after the n-scale depth gain 0.5)."""
    cfg = copy.deepcopy(transformer)
    for idx in (8, 10):  # 1/32 resolution rows: 64 px -> 2x2 tokens, stride probe 256 px -> 8x8 = 64 tokens
        row = cfg["backbone"][idx]
        cfg["backbone"][idx] = [row[0], 4, "NASGlobalTransformer", [row[3][0], 4, 1024]]
    cfg["backbone"][6] = [-1, 4, "NASWindowTransformer", [cfg["backbone"][6][3][0], 4, 7, 2.0]]
    return cfg


def run_ref(etri_dir: str, out: str) -> None:
    import importlib
    import types

    sys.dont_write_bytecode = True  # nothing is written next to the reference copy
    pkg = types.ModuleType("yolo_ga_runtime")
    pkg.__path__ = [etri_dir]  # package __init__ (install_runtime_hooks) is deliberately NOT executed
    sys.modules["yolo_ga_runtime"] = pkg
    mb = importlib.import_module("yolo_ga_runtime.mixed_blocks")
    mss = importlib.import_module("yolo_ga_runtime.mixed_search_space")
    assert Path(mb.__file__).resolve().parent == Path(etri_dir).resolve()
    caps = mb.register_ultralytics_blocks()
    yamls = {}
    for family in ("yolo11n", "yolo26n"):
        variants = mss.make_mixed_seed_variants(_base_yaml(family, 1))
        for name in ("native", "cnn_backbone", "transformer_backbone"):
            yamls[f"{family}:{name}"] = _strip(variants[name])
        yamls[f"{family}:transformer_global_extra"] = _extra_global(yamls[f"{family}:transformer_backbone"])
    loaded = sorted(n for n in sys.modules if n == "yolo_ga_runtime" or n.startswith("yolo_ga_runtime."))
    records = {key: build_record(cfg) for key, cfg in yamls.items()}
    json.dump({"mode": "ref", "registration": caps, "yamls": yamls, "records": records, "etri_modules_loaded": loaded,
               "mixed_blocks_sha256": hashlib.sha256(Path(mb.__file__).read_bytes()).hexdigest()},
              open(out, "w"), indent=1, default=str)


def run_vendored(yamls_from: str, out: str) -> None:
    import mbnas_model_runtime.operators as ops

    caps = ops.ensure_registered()
    yamls = json.load(open(yamls_from))["yamls"]
    records = {key: build_record(cfg) for key, cfg in yamls.items()}
    etri = sorted(n for n in sys.modules if n == "yolo_ga_runtime" or n.startswith("yolo_ga_runtime."))
    json.dump({"mode": "vendored", "registration": caps, "records": records, "etri_modules_loaded": etri,
               "operator_files_sha256": ops.operator_files_sha256()}, open(out, "w"), indent=1, default=str)


def compare(ref: dict, ven: dict) -> dict:
    fields = ("class_names", "qualified_normalised", "params", "structural_hash_normalised", "layers", "save", "stride",
              "state_digest", "forward64_shapes", "forward64_digest")
    rows, ok = {}, True
    for key, a in ref["records"].items():
        b = ven["records"][key]
        same = {f: a[f] == b[f] for f in fields}
        raw_same = a["structural_hash_raw"] == b["structural_hash_raw"]
        uses_ops = any(q.startswith(NORM_PREFIX) for q in a["qualified_normalised"])
        row_ok = all(same.values()) and (raw_same or uses_ops)
        ok &= row_ok
        rows[key] = {"ok": row_ok, "fields_equal": same, "structural_hash_raw_equal": raw_same,
                     "uses_vendored_operators": uses_ops, "params": a["params"], "modules": len(a["class_names"]),
                     "forward64_shapes": a["forward64_shapes"],
                     "structural_hash_raw": {"ref": a["structural_hash_raw"], "vendored": b["structural_hash_raw"]}}
    return {"ok": ok and not ven["etri_modules_loaded"], "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("ref", "vendored"), required=True)
    ap.add_argument("--etri-dir")
    ap.add_argument("--yamls-from")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.mode == "ref":
        run_ref(args.etri_dir, args.out)
    else:
        run_vendored(args.yamls_from, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
