"""BENCHMARK job handler (worker child, GPU): ``run_benchmark_job(spec, emit)``.

``spec.params``:

* ``device`` — benchmark target id; only ``host-gpu-0`` is measurable. Any other target returns a JobResult with
  ``reason_code="TARGET_BLOCKED"`` and ``outputs.status="BLOCKED"`` (no proxy latency is ever produced). Its job
  status is ``BLOCKED`` when :class:`JobStatus` / :class:`JobResult` accept that value, otherwise ``FAILED``
  (``outputs.job_status_fallback`` then says so).
* ``subject`` — ``{"backend": "ultralytics", "source": "official_weights"|"eval_snapshot", "path", "family":
  "YOLO11"|"YOLO26"|"RTDETR"}``, ``{"backend": "rfdetr", "source": "official_weights", "path", "class_name"?}``
  (published-MD5 verified ``rf-detr-{nano,small,medium}.pth`` / ``rf-detr-large-2026.pth`` at the class resolution
  384/512/576/704), ``{"backend": "rfdetr", "source": "eval_snapshot", "path"}`` or ``{"backend": "etri_hybrid",
  "source": "eval_snapshot", "path"}``.
* ``imgsz`` (ultralytics), ``split`` ``{"root": <images dir>, "names": {id: name}}`` (allowlisted dataset root),
  ``output_dir`` (allowlisted output root), optional ``warmup`` (20), ``repeats`` (100), ``runtimes``,
  ``postprocess`` ``{conf, iou, max_det}`` (latency operating point = protocol ``benchmark.postprocess``; also read
  from ``params.benchmark.postprocess``; default 0.25 / 0.7 / 300).

The record is written to ``<output_dir>/benchmark.json``; ``outputs`` carries its path, the latency table, the
accuracy deltas, the engine checksums, ``postprocess_runtime`` and ``postprocess_operating_point``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec, JobStatus, ProgressMessage
from mbnas_contracts.util.time import utcnow_iso

Emit = Callable[[ProgressMessage], None]
DEVICE_TO_TORCH = {"host-gpu-0": "cuda:0"}


class BenchJobError(RuntimeError):
    def __init__(self, code: str, message: str, outputs: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.outputs = code, outputs or {}


def _checked(path: Any, *, kind: str, roots: list[str], must_exist: bool = True, writable: bool = False) -> str:
    from mbnas_contracts.util.paths import check_server_path

    chk = check_server_path(str(path), kind=kind, allow_roots=roots, must_exist=must_exist, writable=writable)
    if not chk.ok:
        raise BenchJobError(chk.code or "PATH_INVALID", chk.message or f"path rejected: {path}")
    return chk.realpath


def _subject(sub: dict[str, Any], params: dict[str, Any], settings: Any, device: str) -> Any:
    from mbnas_model_runtime.benchmark import subjects as S

    backend, source = sub.get("backend"), sub.get("source")
    roots = list(settings.output_roots) + [settings.weights_dir]
    path = _checked(sub.get("path"), kind="file", roots=roots)
    if backend == "ultralytics" and source == "official_weights":
        return S.ultralytics_from_weights(path, family=sub.get("family"), imgsz=int(params.get("imgsz", 640)),
                                          device=device, weights_dir=settings.weights_dir)
    if backend == "ultralytics" and source == "eval_snapshot":
        return S.ultralytics_from_snapshot(path, family=sub.get("family"), imgsz=int(params.get("imgsz", 640)),
                                           device=device)
    if backend == "rfdetr" and source == "official_weights":
        return S.rfdetr_from_weights(path, device=device, class_name=sub.get("class_name"))
    if backend == "rfdetr" and source == "eval_snapshot":
        return S.rfdetr_from_snapshot(path, device=device)
    if backend == "etri_hybrid" and source == "eval_snapshot":
        return S.etri_from_snapshot(path, device=device)
    raise BenchJobError("INVALID_SUBJECT", f"unsupported subject backend/source {backend}/{source}")


def _run(spec: JobSpec, emit: Emit, settings: Any) -> dict[str, Any]:
    from mbnas_model_runtime.benchmark.capability import target_status
    from mbnas_model_runtime.benchmark.pipeline import DEFAULT_RUNTIMES, PostprocessError, resolve_postprocess, run_benchmark
    from mbnas_model_runtime.benchmark.subjects import SubjectError
    from mbnas_model_runtime.datasets.manifest import build_manifest

    if spec.kind != JobKind.BENCHMARK:
        raise BenchJobError("INVALID_JOB", f"handler got job kind {spec.kind}")
    p = spec.params
    target = str(p.get("device", "host-gpu-0"))
    status = target_status(target)
    if status["status"] != "AVAILABLE":
        raise BenchJobError("TARGET_BLOCKED", status["reason"], {"status": "BLOCKED", "target": status})
    device = DEVICE_TO_TORCH[target]
    requested_pp = p.get("postprocess")
    if requested_pp is None and isinstance(p.get("benchmark"), dict):
        requested_pp = p["benchmark"].get("postprocess")
    try:
        resolve_postprocess(requested_pp)
    except PostprocessError as exc:
        raise BenchJobError("INVALID_JOB", str(exc)) from exc
    split = p.get("split") or {}
    root = _checked(split.get("root"), kind="dir", roots=list(settings.dataset_roots))
    names = {int(k): str(v) for k, v in (split.get("names") or {}).items()}
    if not names:
        raise BenchJobError("INVALID_JOB", "split.names is required")
    out_dir = _checked(p.get("output_dir"), kind="dir", roots=list(settings.output_roots), must_exist=False,
                       writable=True)
    manifest = build_manifest(root, names=names)
    if not manifest.samples:
        raise BenchJobError("INVALID_JOB", f"split {root} has no samples")
    import torch

    def log(msg: str) -> None:
        emit(ProgressMessage(type="log", job_id=spec.job_id, ts=utcnow_iso(), message=msg))

    # rfdetr switches float32 matmuls to TF32 when imported (process-wide); a subject keeps its framework's setting
    # for its own run, and the next job in this process starts from the precision it had before.
    matmul_precision = torch.get_float32_matmul_precision()
    try:
        try:
            subject = _subject(p.get("subject") or {}, p, settings, device)
        except SubjectError as exc:
            raise BenchJobError(exc.code, str(exc)) from exc
        record = run_benchmark(subject, out_dir=out_dir, manifest=manifest, sample_image=manifest.samples[0].image,
                               device=device, runtimes=tuple(p.get("runtimes") or DEFAULT_RUNTIMES),
                               warmup=int(p.get("warmup", 20)), repeats=int(p.get("repeats", 100)),
                               timing_cache=Path(settings.output_root) / "benchmark" / "trt_timing.cache",
                               postprocess=requested_pp, log=log)
    finally:
        torch.set_float32_matmul_precision(matmul_precision)
    return {"status": "MEASURED", "target": status, "benchmark_path": str(Path(out_dir) / "benchmark.json"),
            "latency_table": record["latency_table"],
            "accuracy_delta": {k: v.get("accuracy_delta_vs_reference") for k, v in record["runtimes"].items()},
            "accuracy_delta_class_agnostic": {k: v.get("accuracy_class_agnostic_delta_vs_reference")
                                              for k, v in record["runtimes"].items()},
            "engines": {k: {"status": v["status"], "sha256": v["sha256"], "error": v["error"]}
                        for k, v in record["engines"].items()},
            "runtime_status": {k: v.get("status") for k, v in record["runtimes"].items()},
            "postprocess_runtime": record["postprocess_runtime"],
            "postprocess_operating_point": record["postprocess_operating_point"]}


def blocked_job_status() -> tuple[Any, bool]:
    """``(status, is_blocked)``: ``JobStatus.BLOCKED`` when the job schema accepts it, else ``JobStatus.FAILED``."""
    blocked = getattr(JobStatus, "BLOCKED", None)
    if blocked is None:
        return JobStatus.FAILED, False
    try:
        JobResult(job_id="probe", status=blocked, reason_code="TARGET_BLOCKED")
    except Exception:  # noqa: BLE001 - schema enum has the value but JobResult does not accept it yet
        return JobStatus.FAILED, False
    return blocked, True


def run_benchmark_job(spec: JobSpec, emit: Emit | None = None, *, settings: Any = None) -> JobResult:
    """Run one BENCHMARK job; typed failures become FAILED results (TARGET_BLOCKED -> BLOCKED when the schema has
    it), never exceptions."""
    from mbnas_runner.config import RunnerSettings

    emit = emit or (lambda _m: None)
    settings = settings or RunnerSettings.load()
    started = utcnow_iso()
    try:
        result = JobResult(job_id=spec.job_id, status=JobStatus.SUCCEEDED, attempt=spec.attempt, exit_code=0,
                           outputs=_run(spec, emit, settings), started_at=started, finished_at=utcnow_iso())
    except BenchJobError as exc:
        status, outputs = JobStatus.FAILED, exc.outputs
        if exc.code == "TARGET_BLOCKED":
            status, is_blocked = blocked_job_status()
            if not is_blocked:
                outputs = {**outputs, "job_status_fallback": "FAILED (JobStatus has no BLOCKED value in this schema)"}
        result = JobResult(job_id=spec.job_id, status=status, attempt=spec.attempt, exit_code=1,
                           reason_code=exc.code, message=str(exc), outputs=outputs, started_at=started,
                           finished_at=utcnow_iso())
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        result = JobResult(job_id=spec.job_id, status=JobStatus.FAILED, attempt=spec.attempt, exit_code=1,
                           reason_code="INTERNAL_ERROR", message=f"{type(exc).__name__}: {exc}", started_at=started,
                           finished_at=utcnow_iso())
    emit(ProgressMessage(type="result", job_id=spec.job_id, ts=utcnow_iso(), payload=result.model_dump(mode="json")))
    return result
