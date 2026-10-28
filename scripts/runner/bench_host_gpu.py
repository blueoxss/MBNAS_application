#!/usr/bin/env python
"""Real host-GPU benchmark evidence: official weights -> ONNX -> ONNX Runtime (CUDA EP) -> TensorRT FP16/FP32,
latency per scope and accuracy delta on a PILOT-layout search_val split, through the BENCHMARK job handler.

This owner script is how BENCHMARK evidence is produced in 0.1.0: the console queue does not create BENCHMARK jobs (the
core has no BENCHMARK job API). The handler runs in this process (``run_benchmark_job``). TensorRT is not in
the offline wheelhouse: without an importable ``tensorrt`` the engines are BLOCKED, the evidence says
``tensorrt.status = BLOCKED_BY_ENV`` and no TensorRT number is produced or substituted.

Paths are parameters (never fixed host paths; the same rule as ``scripts/runner/resume_equivalence.py``):

* ``--data-root`` (env ``MBNAS_BENCH_DATA_ROOT``, required for a benchmark run): a PILOT-layout dataset directory with
  ``data.yaml`` (``names``) and the benchmark split image directory ``--val-dir`` (relative to the data root; default
  ``images/search_val``, the PILOT_SMOKE layout); it is the only dataset allow root of the run;
* ``--weights-dir`` (env ``MBNAS_RUNNER_WEIGHTS_DIR``, default ``$MBNAS_HOME/weights``): the ultralytics checkpoints
  named by ``--weights`` and the RF-DETR checkpoints under ``rfdetr/`` (``--rfdetr``; ``RF_HOME`` below it);
* ``--runs-root`` (env ``MBNAS_BENCH_RUNS_ROOT``, default ``$MBNAS_HOME/runner/bench_host_gpu``): the output allow root:
  per-run outputs ``benchmark/host_gpu_<ts>/<subject>/`` (``benchmark.json`` + ONNX + engines; temporaries in
  ``benchmark/host_gpu_<ts>/tmp`` via ``TMPDIR``, never /tmp), the TensorRT timing cache, the ultralytics settings
  directory and the evidence directory;
* ``--evidence-dir`` (default ``<runs-root>/evidence``) and ``--doc`` (default
  ``<evidence-dir>/BENCHMARK_HOST_GPU.md``).

Usage (one GPU, chosen with ``CUDA_VISIBLE_DEVICES``; wrap in the host's GPU lock, e.g. ``$MBNAS_HOME/locks/gpu.lock``,
when other jobs share the device)::

  CUDA_VISIBLE_DEVICES=0 flock -w 3600 "$MBNAS_HOME/locks/gpu.lock" runtime/venv/bin/python \\
      scripts/runner/bench_host_gpu.py --data-root /abs/pilot_subset --weights yolo11n.pt rtdetr-l.pt
  # RF-DETR (official checkpoints under <weights-dir>/rfdetr, verified against rfdetr's published MD5):
  ... scripts/runner/bench_host_gpu.py --data-root /abs/pilot_subset --weights --rfdetr rf-detr-nano.pth

Writes ``<evidence-dir>/bench_host_gpu_<ts>.json`` (``bench_host_gpu_rfdetr_<ts>.json`` when only RF-DETR subjects run;
all records) and, with ``--write-doc``, the summary re-rendered from the newest record of every subject in the evidence
directory (the new run replaces only its own subjects; the other subjects keep their records). ``--render-latest``
re-renders the document the same way without GPU work; ``--render-from A.json B.json ...`` renders exactly the given
files. Records that are not rendered (an older full-matrix record of the same subject, a reduced run without TensorRT, a
failed job) are listed in the document as superseded.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

DATA_ROOT_ENV = "MBNAS_BENCH_DATA_ROOT"
RUNS_ROOT_ENV = "MBNAS_BENCH_RUNS_ROOT"
WEIGHTS_DIR_ENV = "MBNAS_RUNNER_WEIGHTS_DIR"
DOC_NAME = "BENCHMARK_HOST_GPU.md"
FAMILY_BY_PREFIX = {"yolo11": "YOLO11", "yolo26": "YOLO26", "rtdetr": "RTDETR"}


def _home(*parts: str) -> Path:
    from mbnas_contracts.util.home import mbnas_home

    return mbnas_home().joinpath(*parts)


def _abs(value: str | os.PathLike[str], what: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{what} must be an absolute path, got {str(value)!r}")
    return path


def _family(weights: str) -> str:
    return next(f for p, f in FAMILY_BY_PREFIX.items() if weights.startswith(p))


def tensorrt_status(cap: dict[str, Any]) -> dict[str, Any]:
    """``AVAILABLE`` (with its version) when ``tensorrt`` imports here, else ``BLOCKED_BY_ENV`` with the error."""
    trt = ((cap.get("host") or {}).get("tensorrt")) or {}
    if trt.get("available"):
        return {"status": "AVAILABLE", "version": trt.get("version")}
    return {"status": "BLOCKED_BY_ENV", "error": trt.get("error"),
            "reason": "tensorrt is not importable in this environment (not in the offline wheelhouse): the TensorRT "
                      "engines are BLOCKED and no TensorRT number is produced or substituted"}


def device_label(cap: dict[str, Any]) -> str:
    """The CUDA device names torch sees in this process (``CUDA_VISIBLE_DEVICES`` applied), for ``evidence_type``."""
    devices = (((cap.get("host") or {}).get("torch")) or {}).get("devices") or []
    return ", ".join(str(d) for d in devices) or "no CUDA device visible"


def _spec(job_id: str, params: dict[str, Any]) -> Any:
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec

    return JobSpec(job_id=job_id, run_id="standalone-host-benchmark", kind=JobKind.BENCHMARK,
                   protocol_hash="standalone:bench_host_gpu", params=params)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weights", nargs="*", default=["yolo11n.pt"],
                    help="ultralytics checkpoint file names in --weights-dir (default %(default)s)")
    ap.add_argument("--rfdetr", nargs="*", default=[], help="RF-DETR checkpoint file names in <weights-dir>/rfdetr")
    ap.add_argument("--data-root", default=os.environ.get(DATA_ROOT_ENV),
                    help=f"PILOT-layout dataset directory (data.yaml + --val-dir); env {DATA_ROOT_ENV}; required for a "
                         "benchmark run (not for --render-latest / --render-from)")
    ap.add_argument("--val-dir", default="images/search_val",
                    help="benchmark split image directory relative to --data-root (default %(default)s)")
    ap.add_argument("--weights-dir", default=os.environ.get(WEIGHTS_DIR_ENV),
                    help=f"weights directory (env {WEIGHTS_DIR_ENV}; default $MBNAS_HOME/weights)")
    ap.add_argument("--runs-root", default=os.environ.get(RUNS_ROOT_ENV),
                    help=f"benchmark output root (env {RUNS_ROOT_ENV}; default $MBNAS_HOME/runner/bench_host_gpu)")
    ap.add_argument("--postprocess", nargs=3, metavar=("CONF", "IOU", "MAX_DET"), default=None,
                    help="latency operating point (default 0.25 0.7 300)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--repeats", type=int, default=100)
    ap.add_argument("--runtimes", nargs="+", default=None)
    ap.add_argument("--write-doc", action="store_true",
                    help="after the run, re-render the document from the newest record of every subject")
    ap.add_argument("--render-latest", action="store_true",
                    help="only re-render the document from the newest record of every subject (no GPU work)")
    ap.add_argument("--render-from", nargs="+", default=None,
                    help="only render the document from exactly these evidence files (in order)")
    ap.add_argument("--evidence-dir", default=None, help="evidence directory (default <runs-root>/evidence)")
    ap.add_argument("--doc", default=None, help=f"document to write (default <evidence-dir>/{DOC_NAME})")
    args = ap.parse_args(argv)
    runs = _abs(args.runs_root, "--runs-root") if args.runs_root else _home("runner", "bench_host_gpu")
    evidence_dir = Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else runs / "evidence"
    doc = Path(args.doc).expanduser().resolve() if args.doc else evidence_dir / DOC_NAME
    if args.render_from:
        srcs = [Path(a) for a in args.render_from]
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(render_docs([(json.loads(src.read_text()), src) for src in srcs]))
        print(f"doc: {doc}")
        return 0
    if args.render_latest:
        doc.parent.mkdir(parents=True, exist_ok=True)
        write_latest_doc(evidence_dir, doc)
        print(f"doc: {doc}")
        return 0
    if not args.data_root:
        ap.error(f"--data-root (or {DATA_ROOT_ENV}) is required for a benchmark run: the PILOT-layout dataset "
                 "directory (data.yaml + the --val-dir images)")
    data_root = _abs(args.data_root, "--data-root")
    weights_dir = _abs(args.weights_dir, "--weights-dir") if args.weights_dir else _home("weights")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_root = runs / "benchmark" / f"host_gpu_{ts}"
    tmp = out_root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(tmp)  # rfdetr/TensorRT/onnx temporaries stay on the run dir, never /tmp
    tempfile.tempdir = str(tmp)
    from mbnas_model_runtime.adapters.rfdetr_adapter import default_rf_home

    os.environ.setdefault("RF_HOME", default_rf_home(str(weights_dir)))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from mbnas_model_runtime.adapters.ultralytics_adapter import prepare_ultralytics_env

    prepare_ultralytics_env(runs / "ultralytics_cfg" / "bench_host_gpu", str(weights_dir))
    os.environ.setdefault("YOLO_OFFLINE", "1")
    from mbnas_model_runtime.benchmark.capability import capability
    from mbnas_model_runtime.datasets.yaml_safe import load_dataset_yaml
    from mbnas_runner.benchmark.handlers import run_benchmark_job
    from mbnas_runner.config import RunnerSettings as Settings
    from mbnas_contracts.util.source_hash import compute_source_tree_hash, package_roots

    # allow roots of this run only: outputs below --runs-root, the split below --data-root, weights in --weights-dir
    settings = Settings(work_root=str(runs), output_roots=[str(runs)], dataset_roots=[str(data_root)],
                        weights_dir=str(weights_dir))
    names = load_dataset_yaml(str(data_root / "data.yaml"), required_splits=()).names
    split = {"root": str(data_root / args.val_dir), "names": {str(k): v for k, v in sorted(names.items())}}
    postprocess = None
    if args.postprocess:
        postprocess = {"conf": float(args.postprocess[0]), "iou": float(args.postprocess[1]),
                       "max_det": int(args.postprocess[2])}
    cap = capability()
    if not (((cap.get("host") or {}).get("torch")) or {}).get("cuda_available"):
        shutil.rmtree(out_root, ignore_errors=True)  # only this run's empty tmp dir exists so far
        print(f"no CUDA device is visible to this process (CUDA_VISIBLE_DEVICES="
              f"{os.environ.get('CUDA_VISIBLE_DEVICES')!r}): the benchmark measures on a CUDA device only and writes "
              "no evidence without one", file=sys.stderr)
        return 2
    evidence: dict[str, Any] = {
        "kind": "bench_host_gpu", "created_at": ts, "command": [sys.executable, *sys.argv], "cwd": os.getcwd(),
        "evidence_type": f"real_gpu ({device_label(cap)}), measured", "source_tree": {
            k: v for k, v in compute_source_tree_hash(package_roots(("mbnas_contracts", "mbnas_model_runtime",
                                                                     "mbnas_runner"))).items()
            if k in ("algorithm", "sha256", "files")},
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "tensorrt": tensorrt_status(cap),
        "paths": {"data_root": str(data_root), "weights_dir": str(weights_dir), "runs_root": str(runs),
                  "out_root": str(out_root)},
        "capability": cap, "split": split, "subjects": [], "blocked_targets": []}
    jobs = [(w, {"imgsz": args.imgsz, "subject": {"backend": "ultralytics", "source": "official_weights",
                                                  "family": _family(w), "path": str(weights_dir / w)}})
            for w in args.weights]
    jobs += [(w, {"subject": {"backend": "rfdetr", "source": "official_weights",
                              "path": str(weights_dir / "rfdetr" / w)}}) for w in args.rfdetr]
    for w, extra in jobs:
        params = {"device": "host-gpu-0", "split": split, "warmup": args.warmup, "repeats": args.repeats,
                  "output_dir": str(out_root / Path(w).stem), **extra}
        if postprocess is not None:
            params["postprocess"] = postprocess
        if args.runtimes:
            params["runtimes"] = args.runtimes
        t0 = time.perf_counter()
        res = run_benchmark_job(_spec(f"bench-{Path(w).stem}", params), emit=lambda m: print(m.to_line(), flush=True),
                                settings=settings)
        bench = Path(res.outputs.get("benchmark_path", "")) if res.outputs.get("benchmark_path") else None
        evidence["subjects"].append({"weights": w, "job_result": res.model_dump(mode="json"),
                                     "seconds": round(time.perf_counter() - t0, 1),
                                     "record": json.loads(bench.read_text()) if bench and bench.is_file() else None})
    blocked = run_benchmark_job(_spec("bench-jetson", {"device": "jetson-orin-nx"}), settings=settings)
    evidence["blocked_targets"].append(blocked.model_dump(mode="json"))
    shutil.rmtree(tmp, ignore_errors=True)
    tag = "rfdetr_" if args.rfdetr and not args.weights else ""
    path = evidence_dir / f"bench_host_gpu_{tag}{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=1, default=str))
    print(f"evidence: {path}")
    if evidence["tensorrt"]["status"] != "AVAILABLE":
        print(f"tensorrt: {evidence['tensorrt']['status']} ({evidence['tensorrt']['reason']})", file=sys.stderr)
    ok = all(s["job_result"]["status"] == "SUCCEEDED" for s in evidence["subjects"])
    if args.write_doc:
        try:
            write_latest_doc(evidence_dir, doc, must_include=path)
            print(f"doc: {doc}")
        except ValueError as exc:  # e.g. a reduced run (no TensorRT) never replaces a full-matrix record
            print(f"doc not written: {exc}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


def _created(ev: dict[str, Any], path: Path) -> str:
    return str(ev.get("created_at") or "_".join(path.stem.rsplit("_", 2)[-2:]))  # ..._<YYYYmmdd>_<HHMMSS>.json


def _rank(s: dict[str, Any]) -> tuple[bool, bool]:
    """(successful with a record, full matrix with a MEASURED TensorRT runtime); newer wins among equal ranks. A run
    whose TensorRT runtimes were not measured (BLOCKED_BY_ENV: no ``tensorrt`` here) counts as a reduced run."""
    rec = s.get("record")
    ok = s["job_result"]["status"] == "SUCCEEDED" and bool(rec)
    return ok, ok and any(k.startswith("tensorrt:") and (v or {}).get("status") == "MEASURED"
                          for k, v in rec.get("runtimes", {}).items())


def _applied(rec: dict[str, Any] | None) -> str:
    lat = ((rec or {}).get("postprocess_operating_point") or {}).get("latency")
    return "not recorded" if not lat else str(lat.get("applied_by_adapter")).lower()


def select_latest(evidence_dir: Path) -> tuple[list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    """Per subject (``weights``) keep the newest successful full-matrix record (a reduced run without TensorRT or a
    failed job only when nothing better exists). Returns ``(selected, superseded)``: ``selected`` = [(evidence with
    only its selected subjects, path)] ordered by creation time; ``superseded`` = one entry per record not rendered."""
    docs = sorted(((json.loads(p.read_text()), p) for p in Path(evidence_dir).glob("bench_host_gpu_*.json")),
                  key=lambda d: (_created(*d), d[1].name))
    best: dict[str, tuple[tuple[bool, bool, str], Path]] = {}
    for ev, path in docs:
        for s in ev.get("subjects", []):
            key = (*_rank(s), _created(ev, path))
            if s["weights"] not in best or key > best[s["weights"]][0]:
                best[s["weights"]] = (key, path)
    selected, superseded = [], []
    records = {(p, s["weights"]): s.get("record") for ev, p in docs for s in ev.get("subjects", [])}
    for ev, path in docs:
        keep = [s for s in ev.get("subjects", []) if best[s["weights"]][1] == path]
        if keep:
            selected.append(({**ev, "subjects": keep}, path))
        for s in ev.get("subjects", []):
            winner = best[s["weights"]][1]
            if winner == path:
                continue
            ok, full = _rank(s)
            reason = (f"job status {s['job_result']['status']}" if not ok else
                      "reduced run without TensorRT runtimes" if not full else "older full-matrix record")
            old, new = _applied(s.get("record")), _applied(records[(winner, s["weights"])])
            if ok and old != new:
                reason += f"; latency operating point applied_by_adapter {old} -> {new}"
            superseded.append({"path": path, "weights": s["weights"], "superseded_by": winner, "reason": reason})
    return selected, superseded


def write_latest_doc(evidence_dir: Path, doc: Path, must_include: Path | None = None) -> None:
    """Render ``doc`` from :func:`select_latest`; ``must_include`` (a run's new evidence file) must be selected."""
    selected, superseded = select_latest(evidence_dir)
    if must_include is not None and Path(must_include) not in {p for _, p in selected}:
        why = [s["reason"] for s in superseded if s["path"] == Path(must_include)]
        raise ValueError(f"{must_include} is not selected ({'; '.join(why) or 'no subjects'}); document unchanged")
    doc.write_text(render_docs(selected, superseded=superseded))


def _fmt(v: Any, nd: int = 3, signed: bool = False) -> str:
    """Fixed-point number (``signed`` adds the sign), '—' for None, str() otherwise."""
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"
    return str(v)


def _split_roots(split: dict[str, Any]) -> str:
    """The split's image root(s): ``roots`` (newer records) or the single ``root`` of older records."""
    roots = split.get("roots") or ([split["root"]] if split.get("root") else [])
    return ", ".join(str(r) for r in roots) or "not recorded"


def _subject_lines(s: dict[str, Any]) -> list[str]:
    rec, res = s["record"], s["job_result"]
    lines = [f"## {s['weights']} — job status {res['status']}" + (f" ({res.get('reason_code')})"
                                                                 if res.get("reason_code") else ""), ""]
    if not rec:
        return lines + [f"No record: {res.get('message')}", ""]
    dev, sub = rec["device"], rec["subject"]
    src = sub["source"]
    hr = dev["host_runtime"]
    head = f"inference head `{src['inference_head']}`; " if src.get("inference_head") else ""
    extra = (f", class {src['class_name']}, resolution {src.get('resolution')}, num_select {src.get('num_select')}, "
             f"published md5 `{src.get('published_md5')}` (observed `{src.get('md5')}`), source {src.get('source_url')}"
             if sub["backend"] == "rfdetr" else "")
    lines += [f"- Source: `{src['path']}` sha256 `{src['sha256']}` ({src['kind']} {src['family']}{extra}; {head}"
              f"postprocess {sub['postprocess']}).",
              f"- Device: {dev.get('name')} uuid `{dev.get('uuid')}`, driver {dev.get('driver')}, CUDA runtime "
              f"{dev.get('cuda_runtime')}, cuDNN {dev.get('cudnn')}, power limit {dev.get('power_limit_w')} W, "
              f"SM clock {dev.get('sm_clock_mhz')}/{dev.get('sm_clock_max_mhz')} MHz; TF32 {dev.get('tf32')}.",
              "- Versions: " + ", ".join(f"{k} {(hr.get(k) or {}).get('version')}"
                                         for k in ("torch", "torchvision", "onnx", "onnxruntime", "tensorrt",
                                                   "ultralytics", "rfdetr") if k in hr) + ".",
              f"- Scope: batch {rec['batch']}, input {rec['input_shape']}, warmup {rec['warmup']}, repeats "
              f"{rec['repeats']}, timer = perf_counter with torch.cuda.synchronize around every repeat; sample image "
              f"`{Path(rec['sample_image']).name}`; split `{_split_roots(rec['split'])}` "
              f"({rec['split']['images']} images, "
              f"split_hash `{rec['split']['split_hash']}`); postprocess runtime "
              f"{rec.get('postprocess_runtime', 'not recorded')}.",
              f"- Postprocess operating point: {rec.get('postprocess_operating_point', 'not recorded (record predates it)')}.",
              f"- ONNX: `{rec['export'].get('path')}` sha256 `{rec['export'].get('sha256')}`, opset "
              f"{rec['export'].get('opset')}, flags {rec['export'].get('flags')}, outputs {rec['export'].get('outputs')}"
              + (f", error `{rec['export']['error']}`" if rec["export"].get("error") else "") + "."]
    for prec, eng in rec["engines"].items():
        lines.append(f"- TensorRT {prec}: status {eng['status']}, `{eng.get('engine_path')}` sha256 `{eng.get('sha256')}`, "
                     f"{eng.get('bytes')} bytes, TensorRT {eng.get('tensorrt_version')}, build {eng.get('seconds')} s, "
                     f"config {eng.get('builder_config')}" + (f", error `{eng['error']}`" if eng.get("error") else ""))
    lines += ["", "| runtime | precision | scope | p50 ms | p95 ms | mean ms | 1000/p50 (batch-1) |",
              "|---|---|---|---:|---:|---:|---:|"]
    for r in rec["latency_table"]:
        lines.append(f"| {r['runtime']} | {r['precision']} | {r['scope']} | {r['p50_ms']:.3f} | {r['p95_ms']:.3f} | "
                     f"{r['mean_ms']:.3f} | {r['fps_batch1']} |")
    lines += ["", "| runtime | status | AP@[.5:.95] | AP50 | recall | ΔAP | ΔAP50 | Δrecall | max abs raw diff |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, r in rec["runtimes"].items():
        a, d = r.get("accuracy") or {}, r.get("accuracy_delta_vs_reference") or {}
        lines.append(f"| {key} | {r['status']}{(' — ' + r['error']) if r.get('error') else ''} | "
                     f"{_fmt(a.get('ap_for_delta'), 4)} | {_fmt(a.get('ap_50'), 4)} | {_fmt(a.get('recall'), 4)} | "
                     f"{_fmt(d.get('ap_delta'), 4, True)} | {_fmt(d.get('ap_50_delta'), 4, True)} | "
                     f"{_fmt(d.get('recall_delta'), 4, True)} | "
                     f"{_fmt(r.get('raw_output_max_abs_diff_vs_reference'), 4)} |")
    if any((r.get("accuracy") or {}).get("class_agnostic") for r in rec["runtimes"].values()):
        lines += ["", "Class-agnostic scoring of the same predictions (GT and predictions mapped to one class):", "",
                  "| runtime | AP@[.5:.95] | AP50 | recall | ΔAP | ΔAP50 | Δrecall |", "|---|---:|---:|---:|---:|---:|---:|"]
        for key, r in rec["runtimes"].items():
            a = (r.get("accuracy") or {}).get("class_agnostic") or {}
            d = r.get("accuracy_class_agnostic_delta_vs_reference") or {}
            lines.append(f"| {key} | {_fmt(a.get('ap_for_delta'), 4)} | {_fmt(a.get('ap_50'), 4)} | "
                         f"{_fmt(a.get('recall'), 4)} | {_fmt(d.get('ap_delta'), 4, True)} | "
                         f"{_fmt(d.get('ap_50_delta'), 4, True)} | {_fmt(d.get('recall_delta'), 4, True)} |")
    sources = sorted({(r.get("accuracy") or {}).get("ap_for_delta_source") for r in rec["runtimes"].values()} - {None})
    return lines + ["", f"AP@[.5:.95] source: {'; '.join(sources)}.", "",
                    "Limitations: " + " ".join(rec["limitations"]), ""]


def _fp16_note(evidence: list[tuple[dict[str, Any], Path]]) -> str:
    """Notes bullet: pytorch:FP16 is autocast, with the rendered records' forward_only p50 FP16 vs FP32 numbers."""
    pairs = []
    for ev, _ in evidence:
        for s in ev.get("subjects", []):
            rts = (s.get("record") or {}).get("runtimes") or {}
            p50 = {k: (((rts.get(k) or {}).get("latency") or {}).get("scopes") or {}).get("forward_only", {}).get("p50_ms")
                   for k in ("pytorch:FP16", "pytorch:FP32")}
            if all(isinstance(v, (int, float)) for v in p50.values()):
                pairs.append((s["weights"], p50["pytorch:FP16"], p50["pytorch:FP32"]))
    slower = [w for w, h, f in pairs if h > f]
    measured = "; ".join(f"{w} {h:.3f} vs {f:.3f} ms" for w, h, f in pairs) or "no PyTorch FP16/FP32 pair rendered"
    verdict = (f"at batch 1 on this GPU it measured slower than pytorch:FP32 for {', '.join(slower)}"
               if slower else "it was not slower than pytorch:FP32 in the rendered records")
    return ("- pytorch:FP16 is `torch.autocast(float16)` over FP32 weights, not a half-precision engine; "
            f"{verdict} (forward_only p50, FP16 vs FP32: {measured}). The search-time fitness latency with `precision = FP16` is this autocast number "
            "(`precision_mode` in evaluation.json); tensorrt:FP16 is the half-precision engine number.")


def render_docs(evidence: list[tuple[dict[str, Any], Path]], superseded: list[dict[str, Any]] | None = None) -> str:
    """Markdown summary rendered only from the evidence files (no number is typed by hand). ``superseded`` (from
    :func:`select_latest`) is listed so a reader can see which records were left out and why."""
    how = "--render-latest" if superseded is not None else "--render-from"
    lines = ["# Host GPU benchmark (ONNX / ONNX Runtime / TensorRT)", "",
             "Evidence type: **real GPU, measured on the host GPU named in each record** (device line per subject); "
             "not a Jetson/target-device result. Every number below is rendered by "
             f"`scripts/runner/bench_host_gpu.py {how}` from these evidence files:", ""]
    lines += [f"- `{path}` (generated {ev['created_at']}; {ev.get('evidence_type', 'evidence type not recorded')}; "
              f"TensorRT {(ev.get('tensorrt') or {}).get('status', 'status not recorded')}; subjects "
              f"{', '.join(s['weights'] for s in ev['subjects'])})" for ev, path in evidence]
    lines.append("")
    if superseded:
        lines += ["## Superseded evidence (not rendered below)", "",
                  "Each subject is rendered from its newest successful full-matrix record. These records are kept on "
                  "disk but are not rendered:", ""]
        lines += [f"- `{s['path']}` `{s['weights']}` -> `{s['superseded_by'].name}`: {s['reason']}" for s in superseded]
        lines.append("")
    for ev, _ in evidence:
        for s in ev["subjects"]:
            lines += _subject_lines(s)
    lines += ["## Notes", "",
              "- `max abs raw diff` is element-wise over the raw head outputs for the sample image against "
              "pytorch:FP32 (RF-DETR: `pred_boxes`/`dets` and `pred_logits`/`labels`) — a diagnostic only. For "
              "top-k heads (RT-DETR, YOLO26 end2end) the query order and the class-index column can differ between "
              "runtimes, so large values there do not indicate an accuracy change; the accuracy criterion is the "
              "AP/AP50/recall delta from the common evaluator.",
              "- ultralytics `non_max_suppression` uses `torchvision.ops.nms` only when `torchvision` is already "
              "imported, otherwise a Python-loop NMS; the pipeline imports torchvision first and records the "
              "implementation under `postprocess_runtime`.",
              "- RF-DETR: the ONNX Runtime / TensorRT forward replaces LWDETR inside `RFDETR.predict` "
              "(`adapters/rfdetr_adapter.py`), so resize/normalise preprocessing, `PostProcess` (top-k num_select, no "
              "NMS) and the score threshold are the same code for every runtime. The official checkpoints are "
              "COCO-trained (91 logit slots, COCO category ids), while the PILOT split is VisDrone: absolute AP "
              "describes a label-space mismatch and is not a model-quality claim (class-aware AP is 0 on the single-class "
              "PILOT split); the class-agnostic table scores the same predictions with one class and is the "
              "informative runtime comparison. Importing rfdetr calls `torch.set_float32_matmul_precision('high')`, "
              "so the RF-DETR pytorch:FP32 reference runs its matmuls as TF32 (see the TF32 field of its device line); "
              "the version probe does not import rfdetr and the BENCHMARK handler restores the process-wide precision after "
              "each job, so other subjects run at `highest` (recorded as `float32_matmul_precision` in newer records).",
              "- `postprocess_operating_point.latency` is the latency operating point (job `postprocess` = protocol "
              "`benchmark.postprocess`, default conf 0.25 / iou 0.7 / max_det 300); `applied_by_adapter` says whether "
              "the adapter's `latency_fns` took it. Accuracy always uses the evaluator floor (conf 0.001, max_det 300).",
              "- AP@[.5:.95] in the tables is the evaluator's `ap` when it is a number; in the older records it was "
              "None for max_det != 100 (pycocotools `summarize` uses maxDets=100 for stats[0]) and the mean of the "
              "evaluator's per-class AP at maxDets=max_det was used instead; the source line under each table says "
              "which.",
              _fp16_note(evidence),
              "- ETRI controlled CNN-Transformer hybrid: its post-processing is the notebook's NMS kept verbatim, a "
              "Python loop on the CPU (`postprocess_runtime = python_loop`), so its forward_and_postprocess / end_to_end "
              "latency is dominated by that NMS and is not comparable to rows with `torchvision_cuda_nms` (YOLO) or "
              "`no_nms` (RT-DETR, RF-DETR, YOLO26 end2end). Every evaluation row carries "
              "`postprocess_runtime`; compare latency only within one implementation.",
              "- Latency records `mbnas-latency/4` and newer also carry `preprocess_ms` (`preprocess_ms_method = "
              "derived`: p50(end_to_end) - p50(forward_and_postprocess) from the same round-robin repeats, not timed on "
              "its own), `throughput_fps = null` ('not measured (batch 1 only)') and `device_sampling`: NVML power "
              "draw (W) and SM clock (MHz) min/max/mean sampled after the warm-up, after every repeat round (outside "
              "the timed window) and after the repeats. Older records show only the power limit and before/after "
              "clock snapshots.", ""]
    blocked = [b for ev, _ in evidence for b in ev.get("blocked_targets", [])][-1:]
    for b in blocked:
        lines += ["## Jetson / other targets — BLOCKED", "",
                  f"BENCHMARK job for device `{b['outputs'].get('target', {}).get('device')}` returned status "
                  f"{b['status']} / {b['reason_code']}: {b['message']}"
                  + (f" ({b['outputs']['job_status_fallback']})" if b["outputs"].get("job_status_fallback") else ""),
                  ""]
    return "\n".join(lines) + "\n"


def render_doc(ev: dict[str, Any], path: Path) -> str:
    """Single-evidence rendering (kept for callers of the previous API)."""
    return render_docs([(ev, path)])

if __name__ == "__main__":
    raise SystemExit(main())
