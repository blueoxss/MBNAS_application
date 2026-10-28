"""Host-side validation inputs computed by the gateway.

When the core does not share a filesystem with the customer data, ``POST /core/v1/console/protocols/validate`` carries
what the gateway measured on its own host and the validator uses it *instead of* touching the core filesystem:

Wire format (produced by ``mbnas_server.api.validate_prep.prepare``; all three fields optional and independent):

``path_checks``: ``{<console.paths key>: {requested, realpath, ok, code, message}}`` for ``dataset_yaml``,
``train_root``, ``search_val_root``, ``final_eval_root`` and ``output_root`` (the last one also ``free_disk_gb``),
i.e. :class:`mbnas_contracts.util.paths.PathCheck` measured on the gateway host.

``dataset_yaml``: ``{ok: true, names: {"0": "car"}, nc, splits: {train|val|test: realpath}, ignored_keys}`` (loaded
with the default required splits) or ``{ok: false, message}``.

``dataset_manifests``: ``{format: "mbnas.gateway-manifests/1", splits: {train|search_val|final_eval: summary},
cross_split_overlap: [...], cross_split_overlap_total}`` with ``summary = {root, split_hash, class_mapping_hash, names,
counts, sample_count, sample_ids_sha256, issues[<=500], issues_total, duplicates[<=100], duplicates_total,
manifest_json_artifact_id?}`` (no ``format`` key inside a split summary; ``cross_split_overlap`` <= 100 entries;
all models ``extra=forbid``). This core schema is the fixed wire format; the gateway sends exactly this shape.

Without them the core-local behaviour is unchanged (owner all-in-one). A validation that used any of them records
``capabilities.validation_host = "gateway"`` (not ``resolution``: ``protocol_hash`` covers ``resolution``, so a host
marker there would make the same protocol hash differently on the gateway and on a core-local re-validation).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mbnas_model_runtime.datasets.manifest import Issue
from mbnas_model_runtime.datasets.yaml_safe import DatasetYaml, DatasetYamlError

SPLIT_ROLES = ("train", "search_val", "final_eval")
PATH_KEYS = ("dataset_yaml", "train_root", "search_val_root", "final_eval_root", "output_root")
BODY_FIELDS = ("path_checks", "dataset_yaml", "dataset_manifests")
MANIFEST_FORMAT = "mbnas.gateway-manifests/1"


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathCheckIn(_Closed):
    requested: str | None = None
    ok: bool
    realpath: str | None = None
    code: str | None = None
    message: str | None = None
    free_disk_gb: float | None = None


class DatasetYamlIn(_Closed):
    ok: bool
    names: dict[int, str] = Field(default_factory=dict)
    nc: int | None = None
    splits: dict[str, str] = Field(default_factory=dict)
    ignored_keys: list[str] = Field(default_factory=list)
    root: str | None = None
    message: str | None = None


class SplitManifestIn(_Closed):
    split_hash: str
    counts: dict[str, Any]
    class_mapping_hash: str
    names: dict[int, str]
    sample_ids_sha256: str
    sample_count: int = Field(ge=0)
    issues: list[Issue] = Field(default_factory=list)
    issues_total: int | None = None
    duplicates: list[list[str]] = Field(default_factory=list)
    duplicates_total: int | None = None
    manifest_json_artifact_id: str | None = None
    root: str | None = None


class DatasetManifestsIn(_Closed):
    format: Literal["mbnas.gateway-manifests/1"] = MANIFEST_FORMAT
    splits: dict[Literal["train", "search_val", "final_eval"], SplitManifestIn] = Field(default_factory=dict)
    cross_split_overlap: list[dict[str, Any]] = Field(default_factory=list)
    cross_split_overlap_total: int | None = None


class ManifestView:
    """Duck-typed stand-in for :class:`~mbnas_model_runtime.datasets.manifest.Manifest` built from a summary (the
    validator reads ``split_hash``, ``class_mapping_hash``, ``counts``, ``issues``, ``duplicates`` and the sample
    count; the samples themselves stay on the gateway host)."""

    def __init__(self, m: SplitManifestIn) -> None:
        self.split_hash, self.class_mapping_hash, self.counts = m.split_hash, m.class_mapping_hash, dict(m.counts)
        self.issues, self.duplicates, self.names = list(m.issues), [list(d) for d in m.duplicates], dict(m.names)
        self.sample_count, self.sample_ids_sha256 = m.sample_count, m.sample_ids_sha256
        self.manifest_json_artifact_id = m.manifest_json_artifact_id


class GatewayInputs:
    """Parsed host inputs of one validate (or run-create) request (see module docstring)."""

    def __init__(self, path_checks: dict[str, PathCheckIn] | None, dataset_yaml: DatasetYamlIn | None,
                 manifests: DatasetManifestsIn | None) -> None:
        self.path_checks = path_checks
        self.yaml = dataset_yaml
        self.manifests = manifests

    @classmethod
    def parse(cls, path_checks: Any = None, dataset_yaml: Any = None, dataset_manifests: Any = None) -> "GatewayInputs | None":
        """Validate the fields (pydantic ``ValidationError`` / ``ValueError`` on a malformed body); None when absent."""
        if path_checks is None and dataset_yaml is None and dataset_manifests is None:
            return None
        checks = None
        if path_checks is not None:
            if not isinstance(path_checks, dict) or any(k not in PATH_KEYS for k in path_checks):
                raise ValueError(f"path_checks must be an object keyed by {PATH_KEYS}")
            checks = {k: PathCheckIn.model_validate(v) for k, v in path_checks.items()}
        dy = DatasetYamlIn.model_validate(dataset_yaml) if dataset_yaml is not None else None
        manifests = DatasetManifestsIn.model_validate(dataset_manifests) if dataset_manifests is not None else None
        return cls(checks, dy, manifests)

    @property
    def has_dataset(self) -> bool:
        """The dataset YAML summary came from the gateway (the core then reads no dataset file)."""
        return self.yaml is not None

    # -- path checks -----------------------------------------------------------------------------------------------
    def check_path(self, key: str, requested: Any) -> tuple[bool, str | None, str | None, str | None]:
        """``(ok, realpath, code, message)`` of ``console.paths.<key>`` as the gateway checked it."""
        assert self.path_checks is not None
        pc = self.path_checks.get(key)
        if pc is None:
            return False, None, "PATH_CHECK_MISSING", f"the gateway sent no path check for console.paths.{key}"
        if pc.requested is not None and requested not in (pc.requested, pc.realpath):  # resolved protocols carry realpaths
            return False, None, "PATH_CHECK_MISMATCH", f"the gateway path check of console.paths.{key} is for another path"
        if not pc.ok:
            return False, None, pc.code or "PATH_INVALID", pc.message or "path rejected by the gateway host"
        if not pc.realpath:
            return False, None, "PATH_CHECK_MISSING", f"the gateway path check of console.paths.{key} has no realpath"
        return True, pc.realpath, None, None

    def free_gb(self, key: str = "output_root") -> float | None:
        pc = (self.path_checks or {}).get(key)
        return None if pc is None else pc.free_disk_gb

    # -- dataset yaml / manifests --------------------------------------------------------------------------------------
    def dataset_yaml(self, required_splits: tuple[str, ...]) -> DatasetYaml:
        """The gateway's dataset YAML summary as a :class:`DatasetYaml` (``DatasetYamlError`` as the loader would)."""
        dy = self.yaml
        if dy is None:
            raise DatasetYamlError("the gateway sent no dataset_yaml summary")
        if not dy.ok:
            raise DatasetYamlError(dy.message or "dataset yaml rejected on the gateway host")
        for key in required_splits:
            if key not in dy.splits:
                raise DatasetYamlError(f"dataset yaml needs a '{key}' entry")
        if not dy.names:
            raise DatasetYamlError("dataset yaml summary has no class names")
        return DatasetYaml(root=dy.root or "", splits=dict(dy.splits), names=dict(dy.names), nc=len(dy.names),
                           ignored_keys=tuple(dy.ignored_keys))

    def manifest(self, role: str, root: str, names: dict[int, str]) -> "ManifestView":
        m = self.manifests.splits.get(role) if self.manifests is not None else None  # type: ignore[call-overload]
        if m is None:  # reported as a validation error of that split (empty, MANIFEST_MISSING), never a 500
            view = ManifestView(SplitManifestIn(split_hash="", counts={}, class_mapping_hash="", names=dict(names),
                                                sample_ids_sha256="", sample_count=0))
            view.issues.append(Issue(code="MANIFEST_MISSING", message=f"the gateway sent no manifest summary for {role}"))
            return view
        view = ManifestView(m)
        if m.root is not None and m.root != root:
            view.issues.append(Issue(code="MANIFEST_ROOT_MISMATCH", message=f"manifest of {role} is for another root"))
        if dict(m.names) != dict(names):
            view.issues.append(Issue(code="CLASS_MAPPING_MISMATCH",
                                     message=f"manifest of {role} uses another class mapping than the dataset yaml"))
        return view

    def overlap(self, roles: list[str]) -> list[dict[str, Any]]:
        items = list(self.manifests.cross_split_overlap) if self.manifests is not None else []
        return [o for o in items if len(set(o.get("splits") or []) & set(roles)) > 1]
