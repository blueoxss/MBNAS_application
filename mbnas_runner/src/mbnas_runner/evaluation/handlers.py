"""Evaluation of one rung-end checkpoint and the ``EVALUATE`` job handler.

:func:`evaluate_checkpoint` produces the *evaluation payload* shared by training and evaluation jobs: AP/AP50/recall
and counts from the common evaluator on a split manifest, four gt/tp/fn/fp panels for each fixed sample, measured
latency (all scopes, ``forward_and_postprocess`` is the fitness scope) on the job's device at the protocol's benchmark
precision/batch/warmup/repeats with a device manifest, the FP16 weights-only size, the evaluation/split hashes and the
artifact list (paths relative to the run directory). Fitness and the promotion gate are not computed here: the core
scores the raw metrics with the source rung's weights. The latency of ``forward_and_postprocess`` runs at
the resolved ``console.benchmark.postprocess`` operating point (``latency_scope`` = e.g.
``forward_and_postprocess(conf=0.25,iou=0.7,max_det=300)``, part of ``evaluation_hash``); torchvision is imported
before any NMS and the NMS implementation that ran is recorded as ``postprocess_runtime``. AP keeps the evaluator's
own floor (conf 0.001, max_det 300).

``run_evaluation_job(spec, emit)`` re-evaluates an existing evaluation snapshot (e.g. on another device entry or after
an evaluator fix). The final evaluation split is refused unless ``spec.arm == "Final"`` (search never sees it).

CPU device: latency is measured on a CUDA device only, so on a job device that is not a CUDA
device - a CPU-only runner maps the core's ``cuda:0`` to ``cpu`` - :func:`evaluate_checkpoint` measures nothing and
touches no CUDA API (no ``Torch not compiled with CUDA enabled`` AssertionError): it returns the
:func:`not_evaluated_payload` (``evaluation_status``/``performance_status`` ``NOT_EVALUATED``, ``reason_code``
``EVALUATION_REQUIRES_CUDA``, ``latency_source`` ``pending``, every metric null, no fitness input) that still names the
rung-end checkpoint (path + sha256), so the training job SUCCEEDS and keeps its checkpoint; an EVALUATE job on such a
device SUCCEEDS with the same payload. No number is made up for the missing measurement.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mbnas_model_runtime.adapters.base import DEFAULT_LATENCY_SCOPE, LATENCY_SCOPES, CapabilityError, ResumeBlocked
from mbnas_runner.config import RunnerSettings
from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec, JobStatus, ProgressMessage
from mbnas_contracts.util.atomic import atomic_write_json, sha256_file
from mbnas_contracts.util.time import utcnow_iso
from mbnas_model_runtime.evaluation import evaluator as ev
from mbnas_model_runtime.evaluation.images import render_fixed_samples
from mbnas_model_runtime.evaluation.latency import (
    ensure_torchvision, gpu_manifest, measure, postprocess_runtime, resolved_postprocess, scope_label,
)
from mbnas_model_runtime.evaluation.size import SIZE_DEFINITION, size_mb

NOT_EVALUATED = "NOT_EVALUATED"
CUDA_REQUIRED = "EVALUATION_REQUIRES_CUDA"


class EvalJobParams(BaseModel):
    """``JobSpec.params`` of an EVALUATE job."""

    model_config = ConfigDict(extra="forbid")

    run_dir: str
    adapter_id: str
    eval_ckpt: str
    protocol_path: str | None = None
    resolved_protocol: dict[str, Any] | None = None
    arch: dict[str, Any] | None = None
    device: str = "cuda:0"
    device_entry: str | None = None
    precision: Literal["FP16", "FP32"] | None = None
    eval_split: Literal["search_val", "final_eval"] = "search_val"
    eval_batch: int = Field(default=8, ge=1)
    image_samples: int = Field(default=2, ge=0)
    latency_scopes: list[str] = Field(default_factory=lambda: list(LATENCY_SCOPES))
    input_shape: list[int] | None = None
    checkpoint_sha256: str | None = None


def latency_device_problem(device: str) -> str | None:
    """Why no evaluation can be measured on the job ``device``, or None for a CUDA device (``cuda`` / ``cuda:N``).

    Only the device NAME is inspected - no CUDA call - so a CPU device (a CPU-only runner) is recognised before
    anything touches CUDA (on a CPU-only torch build any CUDA call raises ``AssertionError: Torch not compiled with
    CUDA enabled``). A CUDA device that turns out to be unusable keeps failing as before (that is a broken GPU host,
    not the CPU path)."""
    name = str(device or "").strip().lower()
    if name == "cuda" or name.startswith("cuda:"):
        return None
    return (f"the job device is {device!r}, not a CUDA device: latency is measured on a CUDA device only "
            "(CPU runner, DD-A32), so this snapshot is not evaluated")


def not_evaluated_payload(*, reason: str, device: str, device_entry: str | None, precision: str, eval_batch: int,
                          input_shape: list[int] | tuple[int, int], benchmark: Mapping[str, Any], split: str,
                          epochs: int, eval_ckpt: str | Path | None = None, eval_sha256: str | None = None,
                          checkpoint_path: str | None = None, checkpoint_sha256: str | None = None) -> dict[str, Any]:
    """Evaluation payload of a snapshot that was NOT evaluated: ``NOT_EVALUATED`` with reason
    ``EVALUATION_REQUIRES_CUDA`` and the human-readable ``reason``, ``latency_source`` ``pending``, ``ap`` / ``recall``
    / ``latency_ms`` / ``size_mb`` / hashes null (never a made-up value, never fitness input), the latency scope the
    protocol asked for, and the checkpoint references so the trained snapshot is kept. Same keys the callers read from
    a measured payload (``epochs``, ``timings`` with zero seconds, ``artifacts`` empty)."""
    shape = [int(input_shape[0]), int(input_shape[1])]
    return {
        "evaluation_status": NOT_EVALUATED, "performance_status": NOT_EVALUATED, "reason_code": CUDA_REQUIRED,
        "not_evaluated_reason": reason,
        "ap": None, "ap_50": None, "recall": None, "latency_ms": None, "size_mb": None,
        "evaluation_hash": None, "split_hash": None,
        "latency_source": "pending", "latency_scope": scope_label(resolved_postprocess(benchmark)),
        "latency_scope_base": DEFAULT_LATENCY_SCOPE, "eval_split": split,
        "device": device_entry or device, "device_uuid": None, "device_name": None,
        "precision": precision, "batch": 1, "eval_batch": int(eval_batch), "resolved_input_shape": shape,
        "eval_checkpoint_path": str(eval_ckpt) if eval_ckpt is not None else None,
        "eval_checkpoint_sha256": eval_sha256, "epochs": int(epochs),
        "checkpoint_path": checkpoint_path, "checkpoint_sha256": checkpoint_sha256,
        "raw_metrics": {"evaluation_status": NOT_EVALUATED, "reason_code": CUDA_REQUIRED, "detail": reason,
                        "job_device": str(device)},
        "artifacts": [], "timings": {"val_s": 0.0, "profile_s": 0.0}, "recorded_at": utcnow_iso(),
    }


def device_identity(device: str) -> dict[str, Any]:
    """uuid/name of a CUDA device via NVML (``torch`` name as fallback)."""
    man = gpu_manifest(device)
    return {"torch_device": device, "uuid": man.get("uuid"), "name": man.get("name") or man.get("torch_device_name"),
            "manifest": man}


def latency_manifest(ident: Mapping[str, Any], lat: Mapping[str, Any], *, precision: str,
                     input_shape: tuple[int, int] | list[int], device_entry: str | None) -> dict[str, Any]:
    """``latency.manifest`` of a :func:`measure_latency` record: the device facts of :func:`device_identity` plus the
    precision, input shape, post-processing operating point/runtime, latency scope and device entry it was measured at
    (shared by the training evaluation and the official evaluation payloads)."""
    return {**ident["manifest"], "precision": precision, "batch": 1, "input_shape": list(input_shape),
            "precision_mode": "torch.autocast(float16) over FP32 weights" if precision == "FP16" else "fp32",
            "postprocess": lat["postprocess"], "postprocess_operating_point": lat["operating_point"],
            "postprocess_runtime": lat["postprocess_runtime"], "torchvision": lat["torchvision"],
            "latency_scope": lat["latency_scope"], "device_entry": device_entry,
            "device_sampling": lat.get("device_sampling")}


def measure_latency(adapter: Any, model: Any, input_shape: tuple[int, int], precision: str, device: str,
                    sample_image: str, benchmark: Mapping[str, Any], scopes: list[str]) -> dict[str, Any]:
    """Measured latency of ``model`` at the resolved post-processing operating point (see module docstring).

    Returns :func:`latency.measure`'s record plus ``operating_point``, ``latency_scope`` (label) and
    ``postprocess_runtime``; ``manifest`` is filled by the caller.
    """
    pp = resolved_postprocess(benchmark)
    torchvision_version = ensure_torchvision()  # before any NMS: ultralytics picks torchvision.ops.nms only if imported
    fns = adapter.latency_fns(model, input_shape, precision, device, sample_image, postprocess=pp)
    lat = measure(fns, warmup=int(benchmark.get("warmup", 20)), repeats=int(benchmark.get("repeats", 100)),
                  device=device, scopes=tuple(dict.fromkeys(scopes)))
    desc = adapter.postprocess_description(model)
    lat.update(operating_point=pp, latency_scope=scope_label(pp), postprocess_runtime=postprocess_runtime(desc, device),
               torchvision=torchvision_version, postprocess=desc)
    return lat


def evaluate_checkpoint(adapter: Any, eval_ckpt: str | Path, manifest: Any, *, run_dir: Path, out_dir: Path,
                        device: str, precision: str, eval_batch: int, input_shape: list[int],
                        benchmark: Mapping[str, Any], scopes: list[str], image_samples: int,
                        device_entry: str | None = None, split: str = "search_val") -> dict[str, Any]:
    """Evaluation payload for one snapshot (see module docstring); writes ``evaluation.json`` and artifacts.

    ``split`` names the manifest's split (``search_val`` or ``final_eval``); it labels the panels and the payload.
    On a device that is not a CUDA device (:func:`latency_device_problem`) nothing is measured or written: the
    :func:`not_evaluated_payload` of this snapshot is returned.
    """
    problem = latency_device_problem(device)
    if problem is not None:
        from mbnas_model_runtime import checkpoint as ck

        snap_side = ck.verify_file(eval_ckpt)  # the snapshot is still verified: a broken file stays a failure
        epochs_done = int(snap_side["epochs_done"])
        full = ck.full_state_path(Path(eval_ckpt).parent, epochs_done)
        full_side = ck.read_sidecar(full) if full.is_file() else None
        return not_evaluated_payload(reason=problem, device=device, device_entry=device_entry, precision=precision,
                                     eval_batch=eval_batch, input_shape=input_shape, benchmark=benchmark, split=split,
                                     epochs=epochs_done, eval_ckpt=eval_ckpt, eval_sha256=sha256_file(eval_ckpt),
                                     checkpoint_path=str(full) if full_side else None,
                                     checkpoint_sha256=full_side["sha256"] if full_side else None)
    if int(benchmark.get("batch", 1)) != 1:
        raise CapabilityError("LATENCY_BATCH_UNSUPPORTED", "PyTorch latency is measured at batch 1 only")
    if DEFAULT_LATENCY_SCOPE not in scopes:
        scopes = [DEFAULT_LATENCY_SCOPE, *scopes]
    out_dir.mkdir(parents=True, exist_ok=True)
    from mbnas_model_runtime import checkpoint as ck

    snap_side = ck.verify_file(eval_ckpt)
    epochs_done = int(snap_side["epochs_done"])
    full = ck.full_state_path(Path(eval_ckpt).parent, epochs_done)
    full_side = ck.read_sidecar(full) if full.is_file() else None
    ident = device_identity(device)
    ensure_torchvision()  # before the AP predictions too: their NMS must be the same CUDA torchvision path
    t0 = time.perf_counter()
    model = adapter.load_eval_model(eval_ckpt, device)
    label = scope_label(resolved_postprocess(benchmark))
    cfg = ev.EvalConfig(input_shape=(int(input_shape[0]), int(input_shape[1])), batch=int(eval_batch),
                        precision=precision, device_class=str(ident["name"]), latency_scope=label)
    result = ev.evaluate(adapter, model, manifest, cfg)
    eval_sha = sha256_file(eval_ckpt)
    artifacts = render_fixed_samples(result, manifest, out_dir / "images", run_dir, checkpoint_sha256=eval_sha,
                                     iou_thr=cfg.recall_iou, conf_thr=cfg.recall_conf, n=image_samples, split=split)
    t1 = time.perf_counter()
    sample = sorted(manifest.samples, key=lambda s: s.sample_id)[0].image
    lat = measure_latency(adapter, model, cfg.input_shape, precision, device, sample, benchmark, scopes)
    lat["manifest"] = latency_manifest(ident, lat, precision=precision, input_shape=cfg.input_shape,
                                       device_entry=device_entry)
    t2 = time.perf_counter()
    weights_path = Path(eval_ckpt).with_name(Path(eval_ckpt).name.replace("eval_ema_", "weights_fp16_"))
    if not weights_path.is_file():
        adapter.weights_only(eval_ckpt, weights_path)
    default = lat["scopes"][DEFAULT_LATENCY_SCOPE]
    payload = {
        "ap": result["ap"], "ap_50": result["ap_50"], "recall": result["recall"], "counts": result["counts"],
        "per_class_ap": result["per_class_ap"], "per_class_recall": result["per_class_recall"],
        "per_sample": result["per_sample"], "no_predictions": result["no_predictions"],
        "invalid_reason": result["invalid_reason"], "coco_stats": result["coco_stats"],
        "evaluation_hash": result["evaluation_hash"], "split_hash": result["split_hash"],
        "evaluation_signature": result["signature"], "metric_definitions": result["metric_definitions"],
        "latency_ms": default["p50_ms"],
        "latency_statistics": {k: default[k] for k in ("p50_ms", "p95_ms", "mean_ms")},
        "latency_scope": label, "latency_scope_base": DEFAULT_LATENCY_SCOPE,
        "postprocess_operating_point": lat["operating_point"], "postprocess_runtime": lat["postprocess_runtime"],
        "latency_source": "measured", "latency": lat, "eval_split": split,
        "size_mb": size_mb(weights_path), "size_dtype": "float16", "size_definition": SIZE_DEFINITION,
        "weights_only_path": str(weights_path), "weights_only_sha256": sha256_file(weights_path),
        "device": device_entry or device, "device_uuid": ident["uuid"], "device_name": ident["name"],
        "precision": precision, "batch": 1, "eval_batch": int(eval_batch),
        "resolved_input_shape": list(cfg.input_shape), "eval_checkpoint_path": str(eval_ckpt),
        "eval_checkpoint_sha256": eval_sha, "epochs": epochs_done,
        "checkpoint_path": str(full) if full_side else None,
        "checkpoint_sha256": full_side["sha256"] if full_side else None,
        "raw_metrics": {"ap": result["ap"], "ap_50": result["ap_50"], "recall": result["recall"],
                        "coco_stats": result["coco_stats"], "counts": result["counts"]},
        "postprocess": adapter.postprocess_description(model),
        "timings": {"val_s": round(t1 - t0, 4), "profile_s": round(t2 - t1, 4)},
        "evaluated_at": utcnow_iso(),
    }
    report = out_dir / "evaluation.json"
    atomic_write_json(report, {**payload, "artifacts": artifacts})
    rel = Path(os.path.relpath(report, run_dir)).as_posix()
    payload["artifacts"] = [*artifacts, {"kind": "other", "relpath": rel, "path": str(report),
                                         "sha256": sha256_file(report), "bytes": report.stat().st_size,
                                         "mime_type": "application/json", "state": "READY",
                                         "split_hash": result["split_hash"]}]
    return payload


def run_evaluation_job(spec: JobSpec, emit: Callable[[ProgressMessage], None],
                       settings: RunnerSettings | None = None) -> JobResult:
    """EVALUATE job: re-evaluate ``params.eval_ckpt`` (never trains)."""
    from mbnas_runner.training.context import (
        JobInputError, SplitAccess, check_run_dir, load_protocol, prepare_backend_env, resolve_model, split_manifests,
    )
    from mbnas_runner.training.recipe import input_shape_for

    started = utcnow_iso()
    if spec.kind != JobKind.EVALUATE:
        raise ValueError(f"run_evaluation_job got a {spec.kind} job")
    try:
        params = EvalJobParams.model_validate(spec.params)
        settings = settings or RunnerSettings.load()
        protocol = load_protocol(params, spec)
        run_dir = check_run_dir(params.run_dir, settings)
        if params.eval_split == "final_eval" and spec.arm != "Final":
            raise JobInputError("FINAL_SPLIT_FORBIDDEN", "final_eval is only evaluated for arm=Final")
        prepare_backend_env(settings)
        arch, adapter, entry = resolve_model(params, settings)
        _, manifests = split_manifests(protocol, settings, (params.eval_split,),
                                       access=SplitAccess.for_job(run_dir, spec, purpose="evaluation"))
        bench = protocol["console"]["benchmark"]
        shape = input_shape_for(protocol, params.adapter_id, params.input_shape,
                                adapter.capability().get("input_size_rule"))
        out_dir = Path(params.eval_ckpt).parent.parent / "eval" / f"{spec.rung or 'adhoc'}_{params.eval_split}_{spec.job_id}"
        payload = evaluate_checkpoint(adapter, params.eval_ckpt, manifests[params.eval_split], run_dir=run_dir,
                                      out_dir=out_dir, device=params.device,
                                      precision=params.precision or bench.get("precision", "FP16"),
                                      eval_batch=params.eval_batch, input_shape=shape, benchmark=bench,
                                      scopes=params.latency_scopes, image_samples=params.image_samples,
                                      device_entry=params.device_entry or bench.get("device"),
                                      split=params.eval_split)
        payload["requested_input_shape"] = list(protocol["training"]["requested_input_size"])
        payload["eval_split"] = params.eval_split
        emit(ProgressMessage(type="metric", job_id=spec.job_id, ts=utcnow_iso(),
                             payload=_clean({k: payload[k] for k in ("ap", "recall", "latency_ms", "size_mb",
                                                                     "evaluation_hash")})))
        seconds = payload["timings"]["val_s"] + payload["timings"]["profile_s"]
        return JobResult(job_id=spec.job_id, status=JobStatus.SUCCEEDED, attempt=spec.attempt, exit_code=0,
                         outputs={"epochs_done": payload["epochs"], "evaluation": _clean(payload)},
                         gpu_seconds=round(seconds, 4), started_at=started,
                         finished_at=utcnow_iso())
    except ValidationError as exc:
        return _failed(spec, "JOB_PARAMS_INVALID", str(exc), started)
    except (JobInputError, CapabilityError) as exc:
        return _failed(spec, exc.code, exc.message, started)
    except ResumeBlocked as exc:
        return _failed(spec, "EVAL_FAILED", f"evaluation snapshot unusable: {exc.reason} {exc.detail}", started)


def _clean(obj: Any) -> Any:
    from mbnas_runner.training.context import jsonable

    return jsonable(obj)


def _failed(spec: JobSpec, code: str, message: str, started: str) -> JobResult:
    return JobResult(job_id=spec.job_id, status=JobStatus.FAILED, attempt=spec.attempt, exit_code=1, reason_code=code,
                     message=message[:2000], started_at=started, finished_at=utcnow_iso())
