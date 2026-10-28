"""Gateway-side pre-processing of ``POST /api/v1/protocols/validate`` (WP-3 contract sections 1.3 / 2.4, DD-A34).

When ``GatewaySettings.dataset_roots`` is configured, the gateway checks the protocol's ``console.paths`` on *its* host
(the customer host holding the data) with :func:`mbnas_contracts.util.paths.check_server_path` against
``dataset_roots`` / ``output_roots``, reads the dataset YAML with ``mbnas_model_runtime.datasets.yaml_safe`` and builds
the per-split manifests with ``mbnas_model_runtime.datasets.manifest.DatasetService`` (cache
``<manifest_cache_root>/manifests``, default ``$MBNAS_HOME/gateway/manifests``). The results are added to the core request
body next to ``protocol``:

``path_checks``      ``{key: {requested, realpath, ok, code, message}}`` for dataset_yaml/train_root/search_val_root/
                     final_eval_root/output_root, plus ``output_root.free_disk_gb``;
``dataset_yaml``     ``{ok, names, nc, splits{train,val,test: realpath}, ignored_keys}`` or ``{ok: false, message}``;
``dataset_manifests`` ``{format: "mbnas.gateway-manifests/1", splits: {role: summary}, cross_split_overlap: [<=100],
                     cross_split_overlap_total}`` (DD-A37) for role in train/search_val/final_eval, ``summary = {root,
                     split_hash, class_mapping_hash, names, counts, sample_count, sample_ids_sha256, issues[<=500],
                     issues_total, duplicates[<=100], duplicates_total}``;
                     overlap = identical images across splits (``cross_split_overlap`` of model_runtime); present only when every path check passed, the YAML parsed, its splits match the console
                     roots and the roots are distinct (the same order the core validator uses).

With ``dataset_roots`` empty the body is forwarded unchanged (core checks locally: owner all-in-one). The model-runtime
modules are imported lazily, so importing :mod:`mbnas_server` never loads ``mbnas_model_runtime``. Host-input fields a
caller put in the body (:data:`HOST_INPUT_FIELDS`) are never forwarded: core trusts them as this host's measurements
(WP-5B finding: without this a browser could hand core forged ``path_checks`` and pass core's path allowlist).

:func:`scrub_realpaths` replaces ``resolution.paths.*.realpath`` (top level or under ``resolved``) by ``requested`` in the core answer (temporary
scrub until the WP-4 projection removes the field).
"""
from __future__ import annotations

import os
from typing import Any

from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.paths import check_server_path, free_disk_gb
from mbnas_server.config import GatewaySettings

DATASET_PATH_FIELDS = (("dataset_yaml", "file"), ("train_root", "dir"), ("search_val_root", "dir"),
                       ("final_eval_root", "dir"))
# role -> (console.paths key, dataset-yaml split key); mirrors the core validator's SPLIT_FIELDS
SPLIT_FIELDS = {"train": ("train_root", "train"), "search_val": ("search_val_root", "val"),
                "final_eval": ("final_eval_root", "test")}
FORMAT = "mbnas.gateway-manifests/1"
MAX_ISSUES, MAX_DUPLICATES, MAX_OVERLAP = 500, 100, 100
HOST_INPUT_FIELDS = ("path_checks", "dataset_yaml", "dataset_manifests")


def _paths_of(protocol: Any) -> dict[str, Any] | None:
    console = protocol.get("console") if isinstance(protocol, dict) else None
    paths = console.get("paths") if isinstance(console, dict) else None
    return paths if isinstance(paths, dict) else None


def _summary(m: Any) -> dict[str, Any]:
    issues = [i.model_dump(mode="json") for i in m.issues]
    return {"root": (m.roots or [None])[0], "split_hash": m.split_hash, "class_mapping_hash": m.class_mapping_hash,
            "names": {str(k): v for k, v in m.names.items()}, "counts": m.counts, "sample_count": len(m.samples),
            "sample_ids_sha256": canonical_sha256(sorted(s.sample_id for s in m.samples)),
            "issues": issues[:MAX_ISSUES], "issues_total": len(issues),
            "duplicates": m.duplicates[:MAX_DUPLICATES], "duplicates_total": len(m.duplicates)}


def prepare(settings: GatewaySettings, body: dict[str, Any]) -> dict[str, Any]:
    """Core request body for validate (see module docstring); never raises for bad paths (core reports them)."""
    body = {k: v for k, v in body.items() if k not in HOST_INPUT_FIELDS}  # only this host's measurements reach core
    paths = _paths_of(body.get("protocol"))
    if not settings.dataset_roots or paths is None:
        return body
    checks: dict[str, dict[str, Any]] = {}
    for key, kind in DATASET_PATH_FIELDS:
        checks[key] = check_server_path(paths.get(key), kind=kind, allow_roots=list(settings.dataset_roots),  # type: ignore[arg-type]
                                        must_exist=True, writable=False).as_dict()
    out = check_server_path(paths.get("output_root"), kind="dir", allow_roots=list(settings.output_roots),  # type: ignore[arg-type]
                            must_exist=False, writable=True).as_dict()
    if out["ok"]:
        out["free_disk_gb"] = free_disk_gb(str(out["realpath"]))
    checks["output_root"] = out
    result = dict(body, path_checks=checks)
    if not all(checks[k]["ok"] for k, _ in DATASET_PATH_FIELDS):
        return result

    from mbnas_model_runtime.datasets.yaml_safe import DatasetYamlError, load_dataset_yaml  # lazy: see docstring

    try:
        dy = load_dataset_yaml(str(checks["dataset_yaml"]["realpath"]))
    except DatasetYamlError as exc:
        result["dataset_yaml"] = {"ok": False, "message": str(exc)}
        return result
    result["dataset_yaml"] = {"ok": True, "names": {str(k): v for k, v in dy.names.items()}, "nc": dy.nc,
                              "splits": {k: os.path.realpath(v) for k, v in dy.splits.items()},
                              "ignored_keys": list(dy.ignored_keys)}
    reals = {role: str(checks[key]["realpath"]) for role, (key, _) in SPLIT_FIELDS.items()}
    consistent = all(yk not in dy.splits or os.path.realpath(dy.splits[yk]) == reals[role]
                     for role, (_, yk) in SPLIT_FIELDS.items())
    if not consistent or len(set(reals.values())) != len(reals):
        return result

    from mbnas_model_runtime.datasets.manifest import DatasetService, cross_split_overlap

    service = DatasetService(settings.manifest_cache_root, list(settings.dataset_roots))
    manifests = {role: service.manifest_for(root, dy.names) for role, root in reals.items()}
    overlap = cross_split_overlap(manifests)
    result["dataset_manifests"] = {"format": FORMAT, "splits": {r: _summary(m) for r, m in manifests.items()},
                                   "cross_split_overlap": overlap[:MAX_OVERLAP],
                                   "cross_split_overlap_total": len(overlap)}  # DD-A37: core gateway_inputs schema
    return result


def scrub_realpaths(answer: Any) -> Any:
    """Replace ``resolution.paths.*.realpath`` with ``requested`` at the top level and under ``resolved`` (in place)."""
    if not isinstance(answer, dict):
        return answer
    resolved = answer.get("resolved")
    for holder in (answer, resolved if isinstance(resolved, dict) else {}):
        resolution = holder.get("resolution")
        paths = resolution.get("paths") if isinstance(resolution, dict) else None
        if isinstance(paths, dict):
            for entry in paths.values():
                if isinstance(entry, dict) and "realpath" in entry:
                    entry["realpath"] = entry.get("requested")
    return answer
