"""Validation for an explicit single-model run, reusing existing recipe/fitness validators.

Only splits that this operation will read are inspected. No final/test split is required by a training run;
validation-disabled training inspects train only. This extension is also used on resume revalidation.
"""
from __future__ import annotations

import os
from typing import Any

from mbnas_model_runtime.datasets.manifest import cross_split_overlap
from mbnas_model_runtime.datasets.yaml_safe import DatasetYamlError, load_dataset_yaml
from mbnas_core.protocol.validator import ValidationContext, ValidationResult, _Run, sample_count


class _TrainingValidation(_Run):
    @property
    def operation(self) -> dict[str, Any]:
        return self.req["console"]["extra"]["training"]

    @property
    def roles(self) -> tuple[str, ...]:
        if self.operation.get("mode") == "evaluate_only":
            return ("search_val",)
        return ("train", "search_val") if self.operation.get("validation_enabled", True) else ("train",)

    def paths(self) -> bool:
        keys = {"train":"train_root", "search_val":"search_val_root"}
        paths = self.req["console"]["paths"]
        valid = self.one_path("dataset_yaml", paths.get("dataset_yaml"), "file", self.ctx.settings.dataset_roots,
                              must_exist=True, writable=False)
        for role in self.roles:
            valid &= self.one_path(keys[role], paths.get(keys[role]), "dir", self.ctx.settings.dataset_roots,
                                   must_exist=True, writable=False)
        valid &= self.one_path("output_root", paths.get("output_root"), "dir", self.ctx.settings.output_roots,
                                must_exist=False, writable=True)
        if "output_root" in self.path_real and self.ctx.free_gb("output_root", self.path_real["output_root"]) < self.ctx.settings.min_free_disk_gb:
            self.err("console.paths.output_root", "DISK_LOW", "insufficient output disk space")
        return valid

    def dataset(self) -> None:
        keys = {"train":("train_root","train","train_manifest_hash"),
                "search_val":("search_val_root","val","search_validation_manifest_hash")}
        try:
            dy = self.ctx.read_dataset_yaml(self.path_real["dataset_yaml"], required_splits=tuple(keys[r][1] for r in self.roles))
        except DatasetYamlError as exc:
            self.err("console.paths.dataset_yaml", "DATASET_YAML_INVALID", str(exc))
            return
        manifests = {}
        for role in self.roles:
            path_key, yaml_key, hash_key = keys[role]
            root = self.path_real[path_key]
            if self.ctx.split_realpath(dy.splits[yaml_key]) != root:
                self.err("console.paths."+path_key,"DATASET_YAML_PATH_MISMATCH","selected root differs from the dataset YAML")
                continue
            manifest = self.ctx.split_manifest(role,root,dy.names)
            manifests[role] = manifest
            if not sample_count(manifest):
                self.err("console.paths."+path_key,"DATASET_EMPTY","selected split has no samples")
            for issue in manifest.issues:
                self.err("console.paths."+path_key,issue.code,f"invalid label/image in {role}: {issue.sample_id}")
        if self.ctx.overlap(manifests):
            self.err("console.paths","SPLIT_OVERLAP","training and validation splits contain identical images")
        ds = self.out.setdefault("dataset", {})
        for role, (_, _, hash_key) in keys.items():
            if role in manifests:
                self.dataset_hash(ds, hash_key, manifests[role].split_hash)
            else:
                ds.pop(hash_key,None)
        ds.pop("final_evaluation_manifest_hash",None)
        if manifests:
            first = next(iter(manifests.values()))
            self.dataset_hash(ds,"class_mapping_hash",first.class_mapping_hash)
        ds["counts"] = {role:m.counts for role,m in manifests.items()}
        self.caps["dataset"] = {"names":{str(k):v for k,v in dy.names.items()},"counts":ds["counts"],
                                "read_splits":list(self.roles)}
        if "train" in manifests:
            self.ultralytics_recipe(sample_count(manifests["train"]),len(dy.names))


def validate_training_protocol(requested: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    run = _TrainingValidation(requested,ctx)
    if not run.structure():
        return run.finish()
    cfg = run.operation
    if not isinstance(cfg.get("validation_enabled",True),bool):
        run.err("console.extra.training.validation_enabled","FIELD_INVALID","validation_enabled must be boolean")
    if cfg.get("mode") not in ("scratch","pretrained","evaluate_only"):
        run.err("console.extra.training.mode","FIELD_INVALID","training mode must be scratch, pretrained or evaluate_only")
    if run.req["console"]["search"].get("arms") != []:
        run.err("console.search.arms","TRAINING_SEARCH_FORBIDDEN","explicit training cannot schedule a search arm")
    run.out["console"]["search"]["evaluation_enabled"] = True
    run.console_mode()
    targets=run.rungs()
    run.training(targets)
    run.fitness_and_constraints()
    run.baselines([])
    run.device()
    run.postprocess()
    run.retention()
    if run.paths():
        run.dataset()
    return run.finish()
