#!/usr/bin/env python3
"""Write the tiny SYNTHETIC YOLO dataset of the MBNAS examples (4 images per split, 1 class "drone").

The generator writes solid-colour 32x24 RGB PNG images whose colour comes from a running seed,
one fixed box per image (label line ``0 0.5 0.5 0.25 0.25``) and a ``data.yaml``. The images carry no information:
the dataset exercises validation, dataset manifests and a 1-epoch CPU training at 64 px, never accuracy.

``data.yaml`` has no ``path`` key (the dataset root is the directory holding
``data.yaml`` - both MBNAS and ultralytics resolve it that way - so the folder can be moved or unpacked anywhere), and
the default splits are train/val/test with 4 images each, so every split that ``data.yaml`` names exists.

Usage (needs Pillow, which every MBNAS install has):
  python examples/make_synthetic_dataset.py --out /ABSOLUTE/PATH/TO/datasets/synthetic4
  python examples/make_synthetic_dataset.py --out DIR --count 4 --splits train,val,test
  python examples/make_synthetic_dataset.py --out DIR --protocol-out FILE --output-root /ABSOLUTE/PATH/TO/runs
The committed copy ``examples/synthetic4/`` is the output of ``--out examples/synthetic4``. ``--protocol-out`` also
writes a copy of ``examples/protocol.cpu_generation_only.json`` whose ``console.paths.output_root`` is
``--output-root`` (it must lie under the core's output allow-roots and, behind a gateway with dataset roots, under
MBNAS_GW_OUTPUT_ROOTS). Existing files with the same names are overwritten; nothing else is touched or deleted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABEL = "0 0.5 0.5 0.25 0.25\n"  # class 0, centred box, 1/4 of width and height
NAMES = ["drone"]
SIZE = (32, 24)  # width, height of every image (pixels)
PROTOCOL_TEMPLATE = HERE / "protocol.cpu_generation_only.json"


def png(path: Path, seed: int, size: tuple[int, int] = SIZE) -> None:
    """One solid-colour RGB PNG; the colour is a fixed function of ``seed`` (same as the smoke helper)."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)).save(path)


def build(root: Path, count: int, splits: list[str], seed0: int = 1) -> dict[str, str]:
    """Write ``images/<split>/<split>_NNN.png`` + ``labels/<split>/<split>_NNN.txt`` + ``data.yaml`` under ``root``."""
    seed = seed0
    for split in splits:
        for i in range(count):
            png(root / "images" / split / f"{split}_{i:03d}.png", seed)
            (root / "labels" / split).mkdir(parents=True, exist_ok=True)
            (root / "labels" / split / f"{split}_{i:03d}.txt").write_text(LABEL)
            seed += 1
    lines = ["# SYNTHETIC example dataset (examples/make_synthetic_dataset.py): solid-colour images, one box each.",
             "# No 'path' key: the dataset root is the directory of this file."]
    lines += [f"{split}: images/{split}" for split in splits]
    lines.append(f"names: {json.dumps(NAMES)}")
    (root / "data.yaml").write_text("\n".join(lines) + "\n")
    role = {"train": "train_root", "val": "search_val_root", "test": "final_eval_root"}
    out = {"dataset_yaml": str(root / "data.yaml")}
    out.update({role[s]: str(root / "images" / s) for s in splits if s in role})
    return out


def write_protocol(dest: Path, output_root: str) -> None:
    """Copy of the CPU generation-only example protocol with ``console.paths.output_root`` filled in."""
    if not os.path.isabs(output_root):
        raise SystemExit(f"--output-root must be an absolute path, got {output_root!r}")
    doc = json.loads(PROTOCOL_TEMPLATE.read_text())
    doc["console"]["paths"]["output_root"] = output_root
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, help="dataset directory to write (created if missing)")
    p.add_argument("--count", type=int, default=4, help="images per split (default 4)")
    p.add_argument("--splits", default="train,val,test",
                   help="comma-separated splits among train,val,test (default train,val,test; train and val required)")
    p.add_argument("--protocol-out", default=None, help="also write a filled copy of protocol.cpu_generation_only.json")
    p.add_argument("--output-root", default=None, help="absolute console.paths.output_root for --protocol-out")
    args = p.parse_args(argv)
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    if not {"train", "val"} <= set(splits) or not set(splits) <= {"train", "val", "test"} or len(set(splits)) != len(splits):
        p.error("--splits must name train and val (and optionally test), each once")
    if args.count < 1:
        p.error("--count must be at least 1")
    if (args.protocol_out is None) != (args.output_root is None):
        p.error("--protocol-out and --output-root go together")
    root = Path(args.out).expanduser().absolute()
    paths = build(root, args.count, splits)
    print(json.dumps({"dataset": paths, "images_per_split": args.count, "splits": splits}, indent=1))
    if args.protocol_out:
        write_protocol(Path(args.protocol_out).expanduser().absolute(), args.output_root)
        print(f"protocol: {Path(args.protocol_out).expanduser().absolute()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
