"""Official-weights evaluation (``EVALUATE`` job of an explicit ``evaluate_only`` training run).

``run_official_evaluation(spec, emit_progress)`` evaluates verified official YOLO/RT-DETR weights directly on the
``search_val`` split: no training, no export campaign, no fitness or gate (the core scores the raw metrics, DD-A15).
It writes ``evaluations/<candidate id>/evaluation.json`` below the run directory and returns the ``JobResult`` the
runner child reports. Moved from the core's ``training.selection`` (WP-1 contract §4): a refusal is a
:class:`~mbnas_contracts.jobs_protocol.JobFailure` whose ``reason_code`` becomes the result's ``reason_code``.

CPU device (DD-A32 / DD-A60 (4)): when the job's ``params.device`` is not a CUDA device (a CPU-only runner maps the
core's ``cuda:0`` to ``cpu``), the weights are not loaded and nothing is measured: the job SUCCEEDS with the
``NOT_EVALUATED`` payload of :func:`mbnas_runner.evaluation.handlers.not_evaluated_payload` (``latency_source``
``pending``, metrics null) instead of failing on the first CUDA call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.schemas.jobs import JobResult, ProgressMessage
from mbnas_contracts.util.atomic import atomic_write_json, sha256_file
from mbnas_contracts.util.time import utcnow_iso


def run_official_evaluation(spec: Any, emit_progress: Any, settings: Any = None) -> JobResult:
    """Evaluate verified official YOLO/RT-DETR weights directly; no training or export campaign is run."""
    from mbnas_runner.config import RunnerSettings
    from mbnas_model_runtime.adapters.common import structural_hash
    from mbnas_model_runtime.benchmark.subjects import ultralytics_from_weights
    from mbnas_model_runtime.evaluation import evaluator
    from mbnas_runner.evaluation.handlers import device_identity, latency_manifest, measure_latency
    from mbnas_model_runtime.evaluation.images import render_fixed_samples
    from mbnas_runner.training.context import split_manifests, SplitAccess, check_run_dir, jsonable
    settings = settings or RunnerSettings.load()
    started = utcnow_iso()
    try:
        proto = spec.params["resolved_protocol"]
        cfg = proto["console"]["extra"]["training"]
        arch, bench = cfg["arch"], proto["console"]["benchmark"]
        base = check_run_dir(spec.params["run_dir"], settings)
        from mbnas_runner.evaluation.handlers import latency_device_problem, not_evaluated_payload

        device = str(spec.params.get("device") or "cuda:0")
        problem = latency_device_problem(device)
        if problem is not None:  # DD-A60 (4): a CPU runner evaluates nothing (no weights load, no CUDA call)
            size = max(proto["training"]["requested_input_size"])
            metric = not_evaluated_payload(reason=problem, device=device, device_entry=bench.get("device"),
                                           precision=bench["precision"],
                                           eval_batch=int(proto["training"]["batch_per_gpu"]),
                                           input_shape=(size, size), benchmark=bench, split="search_val", epochs=0)
            metric.update(source="official_weights", training_mode="evaluate_only",
                          requested_input_shape=[size, size], architecture_hash=cfg.get("architecture_hash"))
            return JobResult(job_id=spec.job_id, attempt=spec.attempt, status="SUCCEEDED",
                             outputs={"epochs_done": 0, "evaluation": jsonable(metric)}, started_at=started,
                             finished_at=utcnow_iso())
        _, manifests = split_manifests(proto, settings, ("search_val",),
            access=SplitAccess.for_job(base, spec, purpose="official pretrained evaluation only; no training"))
        manifest = manifests["search_val"]
        size = max(proto["training"]["requested_input_size"])
        subject = ultralytics_from_weights(arch["weights_path"], family=arch["family"], imgsz=size,
                                           device="cuda:0", weights_dir=settings.weights_dir)
        actual = getattr(subject.model, "names", {})
        if isinstance(actual, list): actual = dict(enumerate(actual))
        if {int(k): str(v) for k, v in actual.items()} != {int(k): str(v) for k, v in manifest.names.items()}:
            raise JobFailure("CLASS_MAPPING_MISMATCH", "official checkpoint label mapping differs from the dataset; use pretrained transfer training for another label set")
        ident = device_identity("cuda:0")
        eval_cfg = evaluator.EvalConfig(input_shape=(size, size), batch=int(proto["training"]["batch_per_gpu"]),
                                       precision=bench["precision"], device_class=ident.get("name") or "host-gpu")
        result = evaluator.evaluate(subject.adapter, subject.model, manifest, eval_cfg)
        out_dir = base / "evaluations" / spec.candidate_id
        artifacts = render_fixed_samples(result, manifest, out_dir / "images", base,
            checkpoint_sha256=sha256_file(arch["weights_path"]), iou_thr=eval_cfg.recall_iou,
            conf_thr=eval_cfg.recall_conf, n=2)
        lat = measure_latency(subject.adapter, subject.model, (size,size), bench["precision"], "cuda:0",
                              manifest.samples[0].image, bench, ["forward_and_postprocess"])
        # DD-A49 (a): the same device evidence as a training evaluation payload (latency record with its device
        # manifest, device uuid/name, statistics and post-processing provenance), in the slots the contract declares
        lat["manifest"] = latency_manifest(ident, lat, precision=bench["precision"], input_shape=(size, size),
                                           device_entry=bench.get("device"))
        fpp = lat["scopes"]["forward_and_postprocess"]
        metric = {**evaluator.public(result), "latency_ms":fpp["p50_ms"],
                  "latency_statistics": {k: fpp[k] for k in ("p50_ms", "p95_ms", "mean_ms")}, "latency": lat,
                  "postprocess_operating_point": lat["operating_point"],
                  "postprocess_runtime": lat["postprocess_runtime"],
                  "device_uuid": ident["uuid"], "device_name": ident["name"],
                  "size_mb":Path(arch["weights_path"]).stat().st_size/1e6, "latency_source":"measured",
                  "latency_scope":lat["latency_scope"], "checkpoint_path":arch["weights_path"],
                  "checkpoint_sha256":sha256_file(arch["weights_path"]), "architecture_hash":cfg.get("architecture_hash") or structural_hash(subject.model),
                  "device":bench["device"], "precision":bench["precision"], "batch":1,
                  "requested_input_shape":[size,size], "resolved_input_shape":[size,size],
                  "epochs":0, "source":"official_weights", "training_mode":"evaluate_only", "artifacts":artifacts,
                  "size_definition":"actual official checkpoint bytes / 1e6 (not FP16 weights-only)", "eval_split":"search_val"}
        atomic_write_json(out_dir / "evaluation.json", jsonable(metric))
        emit_progress(ProgressMessage(type="metric", job_id=spec.job_id, ts=utcnow_iso(), payload={"ap":metric.get("ap"),"recall":metric.get("recall")}))
        return JobResult(job_id=spec.job_id,attempt=spec.attempt,status="SUCCEEDED",outputs={"epochs_done":0,"evaluation":jsonable(metric)},started_at=started,finished_at=utcnow_iso())
    except JobFailure as fail:
        return JobResult(job_id=spec.job_id,attempt=spec.attempt,status="FAILED",reason_code=fail.reason_code,message=str(fail)[:1000],started_at=started,finished_at=utcnow_iso())
    except Exception as exc:
        code = getattr(exc,"code","EVAL_FAILED")
        return JobResult(job_id=spec.job_id,attempt=spec.attempt,status="FAILED",reason_code=code,message=str(exc)[:1000],started_at=started,finished_at=utcnow_iso())
