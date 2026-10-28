#!/usr/bin/env python3
"""G2-4 sandbox RLIMIT_AS measurement on the GPU host (frozen copy, GPUs idle and owned by the G2 track).

Repeats the WP-3A measurement (registry_yaml structure checks in the real ``mbnas_core.structure.sandbox`` worker:
``unshare -rn`` + setsid + RLIMIT_CPU/AS/NOFILE/FSIZE) at RLIMIT_AS 5/6/8 GiB, now with the NVIDIA driver/GPUs
available on the host:
* ``production``: the sandbox's own SAFE_ENV (``CUDA_VISIBLE_DEVICES=""``: the worker never sees a GPU);
* ``cuda_visible``: DIAGNOSTIC variant - SAFE_ENV patched in THIS measuring process only so the worker sees GPU 1
  (``CUDA_VISIBLE_DEVICES=1``), to learn whether a GPU-visible worker would still fit the 8 GiB default.
Writes JSON to argv[1]. No source file is changed."""
from __future__ import annotations

import json
import os
import platform
import sys
import time

from mbnas_core.structure import sandbox as sb

CASES = [("yolo11n", 640), ("yolo11x", 1280), ("rt-detr-x", 1280)]


def one(model: str, size: int, gib: int, cuda: str) -> dict:
    sb.SAFE_ENV["CUDA_VISIBLE_DEVICES"] = cuda
    box = sb.StructureSandbox(sb.Limits(cpu_s=900, as_bytes=gib << 30, nofile=1024, fsize_bytes=64 << 20, wall_s=300.0,
                                        queue_s=60.0, max_output_bytes=1 << 20, unshare=True))
    t0 = time.monotonic()
    rec = {"model": model, "input_size": size, "as_gib": gib, "worker_CUDA_VISIBLE_DEVICES": cuda}
    try:
        doc = box.run("registry_yaml", {"model_id": model, "input_size": size})
        rec.update(status=doc.get("status"), codes=sorted({d.get("code") for d in doc.get("diagnostics") or []}),
                   sandbox=doc.get("sandbox"))
    except sb.SandboxFailure as exc:
        rec.update(status="SANDBOX_FAILURE", code=exc.code, cause=str(exc.cause)[:300], sandbox=exc.sandbox)
    rec["seconds"] = round(time.monotonic() - t0, 2)
    return rec


def main() -> int:
    out = {"host": platform.node(), "nproc": os.cpu_count(), "python": sys.executable,
           "unshare_available": sb.unshare_available(), "safe_env_default": dict(sb.SAFE_ENV), "runs": []}
    for model, size in CASES:
        for gib in (5, 6, 8):
            out["runs"].append({"variant": "production", **one(model, size, gib, "")})
            print(json.dumps({k: out["runs"][-1].get(k) for k in ("variant", "model", "input_size", "as_gib", "status",
                                                                   "codes", "code", "seconds")}), flush=True)
    for model, size in CASES[:2]:
        for gib in (6, 8):
            out["runs"].append({"variant": "cuda_visible", **one(model, size, gib, "1")})
            print(json.dumps({k: out["runs"][-1].get(k) for k in ("variant", "model", "input_size", "as_gib", "status",
                                                                   "codes", "code", "seconds")}), flush=True)
    sb.SAFE_ENV["CUDA_VISIBLE_DEVICES"] = ""
    with open(sys.argv[1], "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
