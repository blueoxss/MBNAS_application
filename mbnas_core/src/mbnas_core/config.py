"""Core (owner host) settings (pydantic-settings, environment prefix ``MBNAS_CORE_``).

WP-1 contract section 5 / DD-A13: the core owns the DB, the run output roots, the dataset/weights allowlists, the
transitional owner worker and the WP-1 console app (``/api/v1``). Gateway and runner have their own classes
(``mbnas_server.config.GatewaySettings`` / ``mbnas_runner.config.RunnerSettings``); the only thing shared at runtime
is the common root ``MBNAS_HOME`` (:func:`mbnas_contracts.util.home.mbnas_home`), under which every default path lives.
The former ETRI checkout root setting (the ETRI mixed pool is vendored, DD-A12), the ``archive_*`` settings (gateway)
and the source-tree-relative ``SERVER_ROOT`` are gone.

Secrets (``MBNAS_CORE_API_TOKEN``, ``MBNAS_CORE_ARTIFACT_SIGNING_KEY``) are optional at load time so the worker, tests
and offline tools can share one Settings object; the API calls :meth:`CoreSettings.require_api_token` and the
artifact service :meth:`CoreSettings.require_signing_key` at startup. An explicitly empty secret is always rejected.
List settings accept JSON (``["a","b"]``) or comma-separated values (items may also be ``os.PathLike`` when passed as
keyword arguments). CORS origins must be exact origins (``scheme://host[:port]``), never ``*`` or a path.
``output_roots`` defaults to ``[output_root]``; user model YAMLs are accepted from ``model_yaml_roots`` together with
``output_roots`` only (WP-3 contract section 1.6; ``weights_dir`` is a checkpoint root, not a YAML root).
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mbnas_contracts.util.home import mbnas_home

ENV_PREFIX = "MBNAS_CORE_"
ENV_FILE_VAR = "MBNAS_CORE_ENV_FILE"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8700
SIGNING_KEY_GUIDANCE = (
    "generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
    "and set MBNAS_CORE_ARTIFACT_SIGNING_KEY in the service environment or .env"
)

StrList = Annotated[list[str], NoDecode]
RETENTION_POLICIES: tuple[str, ...] = ("keep_all", "rung_end_only", "last_rung_end_only")
RetentionPolicy = Literal["keep_all", "rung_end_only", "last_rung_end_only"]


def _split_list(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            import json

            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [os.fspath(item) if isinstance(item, os.PathLike) else item for item in value]
    return value


def _home_path(*parts: str) -> str:
    return str(mbnas_home().joinpath(*parts))


def _home_default(*parts: str) -> Callable[[], str]:
    """Default factory for a path under ``MBNAS_HOME`` (evaluated when the Settings object is built)."""

    def make() -> str:
        return _home_path(*parts)

    return make


class CoreSettings(BaseSettings):
    """Core runtime configuration; every path default lives under ``MBNAS_HOME`` (never inside the source tree)."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", case_sensitive=False)

    api_token: SecretStr | None = None
    """Bearer token of the WP-1 core console app (``/api/v1``); WP-2 replaces it with scoped principals."""
    artifact_signing_key: SecretStr | None = None
    cors_origins: StrList = Field(default_factory=list)
    db_path: str = Field(default_factory=_home_default("core", "db", "mbnas.sqlite3"))
    output_root: str = Field(default_factory=_home_default("core", "runs"))
    output_roots: StrList = Field(default_factory=lambda: [_home_path("core", "runs")])
    """Write allowlist of run outputs; when not set explicitly it is ``[output_root]``."""
    dataset_roots: StrList = Field(default_factory=lambda: [_home_path("data")])
    weights_dir: str = Field(default_factory=_home_default("weights"))
    model_yaml_roots: StrList = Field(default_factory=lambda: [_home_path("uploads")])
    """Read roots of user model YAMLs (structure check / explicit training); used together with ``output_roots``."""
    worker_python: str = Field(default_factory=lambda: sys.executable)
    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    signed_url_ttl_s: int = Field(default=600, gt=0)
    lease_ttl_s: int = Field(default=60, gt=0)
    heartbeat_s: int = Field(default=10, gt=0)
    max_replay_events: int = Field(default=5000, gt=0)
    max_request_body_bytes: int = Field(default=1 << 20, gt=0)
    log_dir: str | None = Field(default_factory=_home_default("core", "logs"))
    min_free_disk_gb: float = Field(default=5.0, ge=0)
    full_state_retention: RetentionPolicy = "keep_all"
    """Server default for ``console.retention.full_state`` when a protocol does not set it (frozen at validation):
    which full-state training payloads survive once a candidate is terminal (see orchestration.retention)."""
    preset_data_root: str | None = None
    """Directory of the PILOT subset used as the preset example ``console.paths`` (unset: ``/ABSOLUTE/PATH/TO/...``
    placeholders, see :func:`mbnas_core.protocol.presets.preset_paths`)."""
    preset_output_root: str | None = None
    """Directory under which the preset example ``console.paths.output_root`` values are placed (unset: placeholder)."""
    # -- WP-2 remote runner protocol (contract section 1.8) --------------------------------------------------------
    ticket_key: SecretStr | None = None
    """HMAC-SHA256 key of the per-claim job tickets (``mbnas_core.runner_api.tickets``). Unset: the app derives a random
    per-process key at startup (tickets then do not survive a core restart; set it in production)."""
    runner_body_limit_bytes: int = Field(default=32 << 20, gt=0)
    """Request body limit of the ``/core/v1/runner`` routes (DD-A18 / design D6: 32 MiB); other routes keep
    ``max_request_body_bytes``."""
    device_source: Literal["local", "runners"] = "local"
    """Validation device inventory: ``local`` = NVML of this host (WP-1 behaviour), ``runners`` = devices of the
    registered runners (``protocol.validator.devices_from_runners``). Env ``MBNAS_CORE_DEVICE_SOURCE``."""
    remote_retention_policy: RetentionPolicy = "keep_all"
    """DEPRECATED, ignored since runner protocol 1.1.0 (DD-A66): remote runs apply their own policy through the prune
    round trip (``orchestration.retention``). Kept only so existing env files (``MBNAS_CORE_REMOTE_RETENTION_POLICY``)
    still load; formerly the policy forced on runs executed by remote runners (WP-5, design D7)."""
    sweeper_enabled: bool = True
    """Run the core sweeper thread (lease reclaim, runner liveness, idempotency orphans) inside the API process."""
    ticket_ttl_s: int = Field(default=7 * 24 * 3600, gt=0)
    """Lifetime of a claim ticket (it must outlive the longest attempt; the fence still bounds what it can do)."""
    # -- WP-3A console split, structure-check sandbox, YAML limits (WP-3 contract section 1) -------------------------
    expose_legacy_api: bool = False
    """Also mount the console routes at ``/api/v1`` behind the static ``api_token`` (owner-mode local debugging only;
    env ``MBNAS_CORE_EXPOSE_LEGACY_API``). The canonical console mount is ``/core/v1/console`` (scope server|admin)."""
    structure_check_max_input: int = Field(default=1280, ge=32)
    """Largest ``input_size`` an explicit structure check accepts (larger: 422 ``INPUT_SIZE_INVALID``)."""
    structure_sandbox: bool = True
    """Run explicit console structure checks in the ``mbnas_core.structure.check_worker`` subprocess sandbox
    (REMOTE_PROTOCOL_DESIGN 10.2). False keeps the in-process check (library/test use only)."""
    sandbox_in_proposer: bool = False
    """Also sandbox ``SearchSpace.inspect`` inside the PROPOSE_GENERATION child (already an isolated child process)."""
    structure_check_timeout_s: float = Field(default=300.0, gt=0)
    """Wall-clock limit of one sandboxed check (the process group is killed: ``STRUCTURE_CHECK_TIMEOUT``)."""
    structure_check_queue_timeout_s: float = Field(default=60.0, ge=0)
    """How long a check waits for the global sandbox semaphore (1 concurrent check) before 429 ``STRUCTURE_CHECK_BUSY``."""
    structure_check_cpu_s: int = Field(default=900, gt=0)
    """RLIMIT_CPU (CPU seconds summed over all threads) of the sandbox."""
    structure_check_as_bytes: int = Field(default=8 << 30, gt=0)
    """RLIMIT_AS of the sandbox (8 GiB). Measured on the owner host (WP-3A evidence ``3A/sandbox_rlimit_as.txt``,
    sandboxed worker, OMP_NUM_THREADS=4): torch + ultralytics import alone maps ~5.3 GiB of address space; the
    heaviest registry checks at the largest accepted input (yolo11x / rt-detr-x at 1280) fail at 5 GiB and pass at
    6 GiB, rf-detr-large passes at 5 GiB, the ETRI hybrid at 4 GiB. 8 GiB = measured need + ~33 % headroom."""
    structure_check_nofile: int = Field(default=1024, gt=16)
    structure_check_fsize_bytes: int = Field(default=64 << 20, ge=0)
    structure_check_max_output_bytes: int = Field(default=1 << 20, gt=0)
    """Largest JSON document the sandbox may print on stdout (larger: ``STRUCTURE_CHECK_RESOURCE_LIMIT``)."""
    structure_check_unshare: bool = True
    """Wrap the sandbox in ``unshare -rn`` (no network namespace access) when the host allows unprivileged user
    namespaces; the isolation level actually used is recorded in every result (``sandbox.isolation``)."""

    @field_validator("cors_origins", "dataset_roots", "output_roots", "model_yaml_roots", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("db_path", "output_root", "weights_dir", "worker_python", "log_dir", "preset_data_root",
                     "preset_output_root", mode="before")
    @classmethod
    def _path_like(cls, value: Any) -> Any:
        return os.fspath(value) if isinstance(value, os.PathLike) else value

    @field_validator("api_token", "artifact_signing_key", "ticket_key", mode="before")
    @classmethod
    def _no_empty_secret(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is not None and str(raw) == "":
            raise ValueError("secret must not be empty; unset the variable instead")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parts = urlsplit(origin)
            if parts.scheme not in ("http", "https") or not parts.netloc or "*" in origin:
                raise ValueError(f"CORS origin must be an exact scheme://host[:port], got {origin!r}")
            if parts.path not in ("", "/") or parts.query or parts.fragment:
                raise ValueError(f"CORS origin must not contain a path/query: {origin!r}")
        return [o.rstrip("/") for o in origins]

    @field_validator("db_path", "output_root", "weights_dir", "worker_python")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not os.path.isabs(value):
            raise ValueError(f"must be an absolute path: {value!r}")
        return value

    @field_validator("preset_data_root", "preset_output_root")
    @classmethod
    def _absolute_optional(cls, value: str | None) -> str | None:
        if value is not None and not os.path.isabs(value):
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

    @field_validator("model_yaml_roots")
    @classmethod
    def _absolute_yaml_roots(cls, roots: list[str]) -> list[str]:
        for root in roots:
            if not os.path.isabs(root):
                raise ValueError(f"model YAML root must be absolute: {root!r}")
        return roots

    @model_validator(mode="after")
    def _derive(self) -> "CoreSettings":
        if "output_roots" not in self.model_fields_set:
            self.output_roots = [self.output_root]
        if self.log_dir is None:
            self.log_dir = _home_path("core", "logs")
        return self

    @classmethod
    def load(cls, dotenv_path: str | os.PathLike[str] | None = None) -> "CoreSettings":
        """Load from the environment, plus ``dotenv_path`` (or ``$MBNAS_CORE_ENV_FILE``) when given."""
        path = dotenv_path or os.environ.get(ENV_FILE_VAR)
        if path is not None and not Path(path).is_file():
            raise FileNotFoundError(f"dotenv file not found: {path}")
        return cls(_env_file=path) if path else cls()

    def require_api_token(self) -> str:
        """Return the bearer token or raise; the API must not start without one."""
        if self.api_token is None:
            raise RuntimeError("MBNAS_CORE_API_TOKEN is required to start the core API")
        return self.api_token.get_secret_value()

    def require_signing_key(self) -> bytes:
        """Return the artifact URL signing key or raise with generation guidance."""
        if self.artifact_signing_key is None:
            raise RuntimeError(f"MBNAS_CORE_ARTIFACT_SIGNING_KEY is required for signed artifact URLs; {SIGNING_KEY_GUIDANCE}")
        return self.artifact_signing_key.get_secret_value().encode("utf-8")


Settings = CoreSettings
"""Transitional alias: core-internal modules and owner tools import ``Settings`` from here (it IS ``CoreSettings``).
Public packages never import this module (import-closure rule i)."""
