#!/usr/bin/env python3
"""Build the deterministic PILOT_SMOKE dataset subset (real images, disjoint splits) without modifying the source dataset.

Source: a YOLO-format dataset with images/{train,val,test} and labels/{train,val,test}. Output: a self-contained
subset directory with images/labels per role (train, search_val, final_eval), data.yaml and manifest.json (sha256 per
file, class ids, seed, selection rule). Selection: sort file names, random.Random(seed).sample. Copies files (no symlinks).
The logic lives in mbnas_model_runtime.datasets.subset; this CLI is a thin wrapper with unchanged options and output.
--source and --out are required: there are no host-specific default paths (DD-A59 (b)). Example:

    python scripts/public/make_pilot_subset.py --source /ABSOLUTE/PATH/TO/dataset --out /ABSOLUTE/PATH/TO/subset
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

try:
    from mbnas_model_runtime.datasets.subset import SubsetError, SubsetSpec, build_subset
except ImportError:  # running from a checkout without the editable install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from mbnas_model_runtime.datasets.subset import SubsetError, SubsetSpec, build_subset


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="source YOLO dataset root holding images/{train,val,test} and labels/{train,val,test}")
    ap.add_argument("--out", required=True,
                    help="output directory of the subset (created if missing; the source is never modified)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--n-train", type=int, default=32)
    ap.add_argument("--n-search-val", type=int, default=16)
    ap.add_argument("--n-final-eval", type=int, default=16)
    ap.add_argument("--names", default="0", help="comma separated class names (source yaml declares names: [0])")
    a = ap.parse_args()
    spec = SubsetSpec(source=Path(a.source), out=Path(a.out), seed=a.seed, n_train=a.n_train,
                      n_search_val=a.n_search_val, n_final_eval=a.n_final_eval, names=tuple(a.names.split(",")))
    try:
        build_subset(spec)
    except SubsetError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
