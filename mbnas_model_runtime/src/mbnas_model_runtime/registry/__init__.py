"""Model registry declarations shared by every host (WP-1 contract §4, DD-A11).

* :mod:`.specs` — the static ``ModelSpec`` declarations (baseline14 + PILOT_SMOKE representatives); ``SPEC_BY_ID`` is
  re-exported here;
* :mod:`.probe` — the capability probes that write registry evidence (run by the owner's probe script);
* :func:`adapter_for` / :func:`arch_spec` — the adapter factory and the ``ArchSpec`` of a registry baseline (moved here
  from the registry package of the pre-split server so a runner can build baselines without the owner's registry).

Which entries are AVAILABLE (probe/GPU evidence, status rules) is owner-host state and is not decided here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from mbnas_model_runtime.registry.specs import SPEC_BY_ID, SPECS, ModelSpec, representative_for

__all__ = ["SPEC_BY_ID", "SPECS", "ModelSpec", "adapter_for", "arch_spec", "representative_for"]


def adapter_for(spec: ModelSpec | Mapping[str, Any]) -> Any:
    """Adapter instance for a spec (or a registry entry dict carrying ``capabilities.backend``)."""
    if isinstance(spec, Mapping):
        spec = SPEC_BY_ID[spec["id"]]
    if spec.backend == "ultralytics":
        from mbnas_model_runtime.adapters.ultralytics_adapter import RtdetrAdapter, YoloDetectAdapter

        if spec.variant == "rtdetr":
            return RtdetrAdapter(spec.family)
        if spec.variant == "detect":
            return YoloDetectAdapter(spec.family)
        raise ValueError(f"unknown ultralytics variant {spec.variant!r} for {spec.id}")
    if spec.backend == "rfdetr":
        from mbnas_model_runtime.adapters.rfdetr_adapter import RfdetrAdapter

        return RfdetrAdapter(spec.config)
    from mbnas_model_runtime.adapters.etri_hybrid.adapter import EtriHybridAdapter

    return EtriHybridAdapter()


def arch_spec(model_id: str, weights_dir: str | None = None,
              entry: Mapping[str, Any] | None = None) -> Any:
    """ArchSpec for a registry baseline (weights path/sha from the entry evidence when given).

    ``weights_dir`` (the host's configured weights directory) defaults to ``$MBNAS_HOME/weights``
    (:func:`~mbnas_model_runtime.adapters.base.default_weights_dir`, DD-A13); it is read only when the path is not
    taken from the entry evidence and the model has an official weights file.
    """
    from mbnas_model_runtime.adapters.base import ArchSpec, default_weights_dir

    spec = SPEC_BY_ID[model_id]
    weights = ((entry or {}).get("evidence") or {}).get("weights") or {}
    path = weights.get("path")
    if path is None and spec.weights_file:
        base = default_weights_dir() if weights_dir is None else weights_dir
        path = str(Path(base) / ("rfdetr" if spec.backend == "rfdetr" else "") / spec.weights_file)
    return ArchSpec(model_id=spec.id, family=spec.family, backend=spec.backend, config=spec.config,
                    variant=spec.variant, weights_path=path, weights_sha256=weights.get("sha256"),
                    inference_head=spec.inference_head)
