#!/usr/bin/env python3
"""DIAGNOSTIC ONLY (not a PASS of the real path): run the runner's BENCHMARK handler on a JobSpec file with one
in-process shim, ``Manifest.root`` -> ``Manifest.roots[0]``, to see how far the benchmark pipeline gets once the defect
found by item g (``mbnas_model_runtime/benchmark/pipeline.py`` reads ``manifest.root``; the Manifest model only has
``roots``) is out of the way. No source file is changed. Prints the JobResult JSON on stdout (last line)."""
from __future__ import annotations

import json
import sys

from mbnas_model_runtime.datasets.manifest import Manifest

Manifest.root = property(lambda self: self.roots[0] if self.roots else None)  # type: ignore[attr-defined]

from mbnas_contracts.schemas.jobs import JobSpec  # noqa: E402
from mbnas_model_runtime.evaluation.latency import ensure_torchvision  # noqa: E402
from mbnas_runner.benchmark.handlers import run_benchmark_job  # noqa: E402

ensure_torchvision()
spec = JobSpec.model_validate(json.loads(open(sys.argv[1]).read()))
logs: list[str] = []
res = run_benchmark_job(spec, emit=lambda m: logs.append(m.message or "") if m.type == "log" else None)
print(json.dumps({"result": res.model_dump(mode="json"), "logs": logs[-50:], "shim": "Manifest.root = roots[0]"},
                 default=str))
