"""Runner settings (pydantic-settings, environment prefix ``MBNAS_RUNNER_``).

One settings class per host (WP-1 contract §5, DD-A13): the runner host reads only this one; the core service and the
gateway have settings classes of their own. Every default path lives below
:func:`mbnas_contracts.util.home.mbnas_home` (``$MBNAS_HOME`` or ``<cwd>/runtime``); nothing points into a source tree
or at a fixed host directory. The core's owner worker hands its job children the roots they may use through
``MBNAS_RUNNER_WORK_ROOT`` / ``_OUTPUT_ROOTS`` / ``_DATASET_ROOTS`` / ``_WEIGHTS_DIR`` / ``_LOG_DIR`` and, for the
owner profile, ``MBNAS_RUNNER_ALLOW_PICKLE_RESUME=1``.

``work_root`` is the directory this runner writes to; handlers and :class:`DatasetService` read it under the older name
``output_root`` (read-only :attr:`RunnerSettings.output_root`; ``RunnerSettings(output_root=...)`` is accepted as an
alias of ``work_root`` because ``extra="ignore"`` would otherwise drop it silently and fall back to the default
root). ``token`` is optional at load time so tools and tests can share one object; an explicitly empty secret is
always rejected. List settings accept JSON (``["a","b"]``) or comma-separated values.

Remote queue settings (WP-2 contract §2.4): ``token_file`` (bearer token read from a file; wins over nothing, an
explicit ``token`` wins over the file), ``ca_file`` (CA bundle that verifies the core's TLS certificate; unset = the
system store), ``dataset_mounts`` (JSON object ``{protocol_root: local_root}``: the core host's dataset paths frozen in
the resolved protocol and where the same bytes live on this host; every local root is also a dataset allow root),
``kinds`` (job kinds this runner claims, default the five runner kinds of DD-A4), ``kill_on_lease_expiry`` (stop the
child when the lease ran out locally without a renewal, F15), ``progress_interval_s`` / ``max_batch_messages`` (progress
batching) and ``request_retries`` / ``request_backoff_s`` (HTTP retry policy of :mod:`mbnas_runner.client`).

Artifact transfer settings (WP-5C, DD-A38/DD-A39; :mod:`mbnas_runner.transfer`): ``artifact_transfer`` = ``none``
(default: nothing is transferred, behaviour unchanged) | ``local`` (every registered file is hard-linked, or copied, into
``artifact_store_dir`` = the gateway's artifact store directory on this host, ``MBNAS_GW_ARTIFACT_STORE``; required in
that mode), ``artifact_store_mode`` (``same-uid`` | ``link-0644`` | ``copy-0644``: who may read the stored files, see
:mod:`mbnas_runner.transfer`), ``artifact_transfer_max_tries`` (tries per file before it is abandoned). ``upload``
(runner -> gateway HTTP upload) is reserved: **upload mode is not implemented in 0.1.0** and is refused; ``gateway_url``
and ``gateway_upload_token_file`` are reserved names for it and are never read in 0.1.0.

Runner id (C06-D1, fix round 1): ``runner_id`` (``MBNAS_RUNNER_RUNNER_ID``) names this runner to the core, and its runner
token is bound to it (``mbnas-core-principal create --kind runner --runner-id <id>``). Unset, it is the runtime root's
generated id (:func:`persistent_runner_id`): ``runner-`` + 8 lower-case hex digits from :func:`secrets.token_hex`,
written to ``mbnas_home()/runner/runner_id`` the first time a settings object needs it (``scripts/install.sh`` does that
when it issues or announces the runner principal) and read back on every later start. It is random and stable, and it
carries no host name, account or address (the public API shows a runner row's id as ``Worker.id`` / ``runner_id``; the
default used to be ``runner-<hostname>``).
"""
from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mbnas_contracts.util.home import mbnas_home

StrList = Annotated[list[str], NoDecode]
StrMap = Annotated[dict[str, str], NoDecode]

#: the job kinds a runner may run (DD-A4); the core rejects anything else with 400 KIND_NOT_ALLOWED
RUNNER_KINDS: tuple[str, ...] = ("BASELINE_TRAIN", "CANDIDATE_TRAIN", "FINAL_RETRAIN", "EVALUATE", "BENCHMARK")
#: ``artifact_transfer`` values (mirrors mbnas_runner.transfer.MODES / RESERVED_MODES; not imported to keep this light)
ARTIFACT_TRANSFER_MODES: tuple[str, ...] = ("none", "local")
ARTIFACT_TRANSFER_RESERVED: tuple[str, ...] = ("upload",)
ARTIFACT_STORE_MODES: tuple[str, ...] = ("same-uid", "link-0644", "copy-0644")  # = mbnas_runner.transfer.STORE_MODES
#: where the generated default runner id lives, below ``mbnas_home()`` (one id per runtime root; C06-D1)
RUNNER_ID_FILE: tuple[str, ...] = ("runner", "runner_id")
#: the form of a generated runner id (8 hex digits are generated; longer ones are accepted)
GENERATED_RUNNER_ID = re.compile(r"runner-[0-9a-f]{8,}")


def runner_id_path() -> Path:
    """``mbnas_home()/runner/runner_id``: the file holding this runtime root's generated runner id."""
    return mbnas_home().joinpath(*RUNNER_ID_FILE)


def new_runner_id() -> str:
    """A fresh random runner id ``runner-<8 hex>`` (:func:`secrets.token_hex`); nothing about the host goes into it."""
    return f"runner-{secrets.token_hex(4)}"


def _store_new_runner_id(target: Path) -> None:
    """Create ``target`` holding a new id unless it exists: temp file + hard link, so concurrent first starts agree on
    one id (the first link wins) and a reader never sees a partially written file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_runner_id() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def persistent_runner_id(path: str | os.PathLike[str] | None = None) -> str:
    """The default runner id (module docstring): the generated id stored in ``path`` (default :func:`runner_id_path`),
    created there first when the file does not exist. A file that does not hold a generated id, or one that cannot be
    read or created, is a ``ValueError`` - never silently replaced, because the runner principal is bound to the stored
    id."""
    target = Path(path) if path is not None else runner_id_path()
    try:
        if not target.exists():
            _store_new_runner_id(target)
        text = target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot create or read the runner id file {target} ({type(exc).__name__}: {exc.strerror}); "
                         "set MBNAS_RUNNER_RUNNER_ID or make the runtime root writable") from exc
    if not GENERATED_RUNNER_ID.fullmatch(text):
        raise ValueError(f"the runner id file {target} does not hold a generated runner id (runner-<hex>); delete it to "
                         "generate a new id (and issue a runner token for that id) or set MBNAS_RUNNER_RUNNER_ID")
    return text


def _split_list(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            import json

            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]
    return value


def _home(*parts: str) -> str:
    """``mbnas_home()/<parts>`` as a string (evaluated when a settings object is created, not at import)."""
    return str(mbnas_home().joinpath(*parts))


class RunnerSettings(BaseSettings):
    """Runtime configuration of a runner host; every path default points below ``mbnas_home()``."""

    model_config = SettingsConfigDict(env_prefix="MBNAS_RUNNER_", extra="ignore", case_sensitive=False)

    core_url: str = "http://127.0.0.1:8700"
    token: SecretStr | None = None
    runner_id: str | None = Field(default=None, min_length=1)
    """``None`` resolves after validation to the runtime root's generated id (:func:`persistent_runner_id`:
    ``runner-<8 hex>`` stored in ``mbnas_home()/runner/runner_id``), so ``settings.runner_id`` is always a string; it is
    never derived from the host name (C06-D1)."""
    work_root: str = Field(default_factory=lambda: _home("runner", "runs"))
    output_roots: StrList = Field(default_factory=lambda data: [data["work_root"]])
    """Roots a run directory may live below (default: ``[work_root]``)."""
    dataset_roots: StrList = Field(default_factory=lambda: [_home("data")])
    weights_dir: str = Field(default_factory=lambda: _home("weights"))
    worker_python: str = sys.executable
    heartbeat_s: int = Field(default=10, gt=0)
    kill_grace_s: float = Field(default=10.0, gt=0)
    gpu_index: int = Field(default=0, ge=0)
    log_dir: str = Field(default_factory=lambda: _home("runner", "logs"))
    allow_pickle_resume: bool = False
    """RF-DETR native resume loads a pickled checkpoint (``torch.load(weights_only=False)``); off unless the operator
    opts in (DD-A20). The owner profile turns it on through ``MBNAS_RUNNER_ALLOW_PICKLE_RESUME=1``."""
    token_file: str | None = None
    ca_file: str | None = None
    dataset_mounts: StrMap = Field(default_factory=dict)
    kinds: StrList = Field(default_factory=lambda: list(RUNNER_KINDS))
    kill_on_lease_expiry: bool = True
    progress_interval_s: float = Field(default=1.0, gt=0)
    max_batch_messages: int = Field(default=100, ge=1)
    artifact_transfer: str = "none"
    """``none`` (default; behaviour unchanged) | ``local`` (registered files go into ``artifact_store_dir`` by hard link,
    else byte copy; :mod:`mbnas_runner.transfer`). ``upload`` is reserved: upload mode is not implemented in 0.1.0."""
    artifact_store_dir: str | None = Field(default=None, validate_default=True)
    """Absolute path of the gateway's artifact store directory on this host (``GatewaySettings.artifact_store``, default
    ``$MBNAS_HOME/gateway/artifacts``); required when ``artifact_transfer=local``, unused otherwise."""
    artifact_store_mode: str = "same-uid"
    """Permission bits of what ``local`` mode stores (checkpoints are written 0600 and a hard link shares the inode):
    ``same-uid`` (default) - the gateway runs as this runner's user, links and copies keep the source's bits (a copy
    never group/other-writable);
    ``link-0644`` - the gateway runs as another user: a linked file not readable by others is set to 0644 (the
    run-directory file with it), copies are 0644; ``copy-0644`` - the gateway runs as another user and run-directory
    files keep their bits: such files are copied with 0644 instead of linked."""
    artifact_transfer_max_tries: int = Field(default=6, ge=1)
    """Tries per registered file (the first one right after its registration, then retries on the heartbeat with
    exponential backoff from the heartbeat interval) before it is recorded as abandoned."""
    gateway_url: str | None = None
    """Reserved for ``artifact_transfer=upload`` (the gateway's base URL). Upload mode is not implemented in 0.1.0; this
    value is never read."""
    gateway_upload_token_file: str | None = None
    """Reserved for ``artifact_transfer=upload`` (file holding the runner's gateway upload token). Upload mode is not
    implemented in 0.1.0; this file is never read."""
    poll_s: float = Field(default=2.0, gt=0)
    request_retries: int = Field(default=5, ge=0)
    request_backoff_s: float = Field(default=0.5, gt=0)
    request_timeout_s: float = Field(default=30.0, gt=0)
    gpu_clear_timeout_s: float = Field(default=15.0, ge=0)

    def __init__(self, **values: Any) -> None:
        if "output_root" in values:  # alias of work_root (see module docstring); extra="ignore" would drop it silently
            alias = values.pop("output_root")
            if "work_root" in values:
                raise ValueError("pass either work_root or its alias output_root, not both")
            values["work_root"] = alias
        super().__init__(**values)

    @property
    def output_root(self) -> str:
        """``work_root`` under the name the shared plumbing reads (``DatasetService.from_settings``, backend env)."""
        return self.work_root

    @field_validator("output_roots", "dataset_roots", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("kinds", mode="before")
    @classmethod
    def _kind_list(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("kinds")
    @classmethod
    def _known_kinds(cls, kinds: list[str]) -> list[str]:
        if not kinds:
            raise ValueError("at least one job kind is required")
        unknown = [k for k in kinds if k not in RUNNER_KINDS]
        if unknown:
            raise ValueError(f"not a runner job kind (DD-A4): {unknown}; allowed {list(RUNNER_KINDS)}")
        return kinds

    @field_validator("dataset_mounts", mode="before")
    @classmethod
    def _mounts(cls, value: Any) -> Any:
        if isinstance(value, str):
            import json

            value = json.loads(value) if value.strip() else {}
        return value

    @field_validator("dataset_mounts")
    @classmethod
    def _absolute_mounts(cls, mounts: dict[str, str]) -> dict[str, str]:
        for proto_root, local_root in mounts.items():
            if not os.path.isabs(proto_root) or not os.path.isabs(local_root):
                raise ValueError(f"dataset mount roots must be absolute: {proto_root!r} -> {local_root!r}")
        return {os.path.normpath(k): os.path.normpath(v) for k, v in mounts.items()}

    @field_validator("token_file", "ca_file", "artifact_store_dir", "gateway_upload_token_file")
    @classmethod
    def _absolute_optional(cls, value: str | None) -> str | None:
        if value is not None and not os.path.isabs(value):
            raise ValueError(f"must be an absolute path: {value!r}")
        return value

    @field_validator("artifact_store_dir")
    @classmethod
    def _store_dir_for_local(cls, value: str | None, info: ValidationInfo) -> str | None:
        # a field validator (not the model validator): its error shows only this field's input, never the token
        if value is None and info.data.get("artifact_transfer") == "local":
            raise ValueError("artifact_transfer=local requires artifact_store_dir (MBNAS_RUNNER_ARTIFACT_STORE_DIR): "
                             "the gateway's artifact store directory on this host")
        return value

    @field_validator("artifact_store_mode", mode="before")
    @classmethod
    def _store_mode(cls, value: Any) -> Any:
        mode = "same-uid" if value is None else str(value).strip().lower()
        if mode not in ARTIFACT_STORE_MODES:
            raise ValueError(f"artifact_store_mode must be one of {list(ARTIFACT_STORE_MODES)}, got {value!r}")
        return mode

    @field_validator("artifact_transfer", mode="before")
    @classmethod
    def _transfer_mode(cls, value: Any) -> Any:
        mode = "none" if value is None else str(value).strip().lower()
        if mode in ARTIFACT_TRANSFER_RESERVED:
            raise ValueError(f"artifact_transfer={mode} is reserved: upload mode is not implemented in 0.1.0 "
                             f"(use one of {list(ARTIFACT_TRANSFER_MODES)})")
        if mode not in ARTIFACT_TRANSFER_MODES:
            raise ValueError(f"artifact_transfer must be one of {list(ARTIFACT_TRANSFER_MODES)}, got {value!r}")
        return mode

    @field_validator("token", mode="before")
    @classmethod
    def _no_empty_secret(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is not None and str(raw) == "":
            raise ValueError("secret must not be empty; unset the variable instead")
        return value

    @field_validator("work_root", "weights_dir", "worker_python", "log_dir")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not os.path.isabs(value):
            raise ValueError(f"must be an absolute path: {value!r}")
        return value

    @field_validator("dataset_roots", "output_roots")
    @classmethod
    def _absolute_roots(cls, roots: list[str]) -> list[str]:
        if not roots:
            raise ValueError("at least one allow root is required")
        for root in roots:
            if not os.path.isabs(root):
                raise ValueError(f"allow root must be absolute: {root!r}")
        return roots

    @model_validator(mode="after")
    def _derive(self) -> "RunnerSettings":
        if self.runner_id is None:
            self.runner_id = persistent_runner_id()
        for local_root in self.dataset_mounts.values():  # an operator-declared mount is a dataset allow root
            if local_root not in self.dataset_roots:
                self.dataset_roots = [*self.dataset_roots, local_root]
        return self

    def bearer_token(self) -> str | None:
        """The runner token: ``token`` when set, else the first line of ``token_file`` (None when neither is set)."""
        if self.token is not None:
            return self.token.get_secret_value()
        if self.token_file is None:
            return None
        text = Path(self.token_file).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"token file {self.token_file} is empty")
        return text.splitlines()[0].strip()

    @classmethod
    def load(cls, dotenv_path: str | os.PathLike[str] | None = None) -> "RunnerSettings":
        """Load from the environment, plus ``dotenv_path`` (or ``$MBNAS_RUNNER_ENV_FILE``) when given."""
        path = dotenv_path or os.environ.get("MBNAS_RUNNER_ENV_FILE")
        if path is not None and not Path(path).is_file():
            raise FileNotFoundError(f"dotenv file not found: {path}")
        return cls(_env_file=path) if path else cls()
