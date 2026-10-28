"""Gateway settings (pydantic-settings, environment prefix ``MBNAS_GW_``).

The gateway owns the browser-facing settings (user bearer token, CORS, listen address,
the upstream core URL + service token, artifact signing, archive roots, request body limit). Core and runner have their own
classes (``mbnas_core.config.CoreSettings`` / ``mbnas_runner.config.RunnerSettings``); the only thing shared at runtime is the
common root ``MBNAS_HOME`` (:func:`mbnas_contracts.util.home.mbnas_home`), under which every default path lives.

Secrets (``MBNAS_GW_API_TOKEN``, ``MBNAS_GW_CORE_TOKEN``, ``MBNAS_GW_ARTIFACT_SIGNING_KEY``) are optional at load time so
tools and tests can share one Settings object; the app factory calls :meth:`GatewaySettings.require_api_token` at startup and
the artifact service :meth:`GatewaySettings.require_signing_key`. An explicitly empty secret is always rejected.
List settings accept JSON (``["a","b"]``) or comma-separated values (items may also be ``os.PathLike`` when passed as keyword
arguments). CORS origins must be exact origins (``scheme://host[:port]``), never ``*`` or a path. Archive paths must be
absolute and are only ever read.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mbnas_contracts.util.home import mbnas_home

log = logging.getLogger("mbnas_server.config")

ENV_PREFIX = "MBNAS_GW_"
ENV_FILE_VAR = "MBNAS_GW_ENV_FILE"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8710
DEFAULT_CORE_URL = "http://127.0.0.1:8700"
DEFAULT_CORE_PREFIX = "/core/v1/console"
SIGNING_KEY_GUIDANCE = (
    "generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
    "and set MBNAS_GW_ARTIFACT_SIGNING_KEY in the service environment or .env"
)

StrList = Annotated[list[str], NoDecode]


class CoreUrlInsecure(RuntimeError):
    """``core_url`` is plain ``http://`` to a non-loopback host and ``core_allow_insecure_http`` is not set
    (the runner's ``CORE_URL_INSECURE`` rule). ``str(exc)`` starts with the code."""

    code = "CORE_URL_INSECURE"


def is_loopback_host(host: str | None) -> bool:
    """``localhost`` or a loopback IP literal (same rule as ``mbnas_runner.client.is_loopback_host``)."""
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


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


def _default_web_dist() -> str:
    """``<ROOT>/mbnas_web/dist``: ROOT is the source/ZIP tree holding ``mbnas_server/src/mbnas_server`` when the
    package runs from there (editable install / unpacked ZIP), else the current working directory."""
    here = Path(__file__).resolve()
    root = here.parents[3] if len(here.parents) > 3 and (here.parents[3] / "mbnas_server").is_dir() else Path.cwd()
    return str(root / "mbnas_web" / "dist")


def _home_default(*parts: str) -> Callable[[], str]:
    """Default factory for a path under ``MBNAS_HOME`` (evaluated when the Settings object is built, so the env is honoured)."""

    def make() -> str:
        return str(mbnas_home().joinpath(*parts))

    return make


class GatewaySettings(BaseSettings):
    """Gateway runtime configuration; every path default lives under ``MBNAS_HOME`` (never inside the source tree)."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", case_sensitive=False)

    api_token: SecretStr | None = None
    """Bearer token every ``/api/v1`` route requires (the browser's credential); the app refuses to start without it."""
    cors_origins: StrList = Field(default_factory=list)
    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    core_url: str = DEFAULT_CORE_URL
    """Base URL of the core control plane the gateway relays to (``/core/v1/console``)."""
    core_token: SecretStr | None = None
    """Service token the gateway presents to core (never sent to browsers)."""
    artifact_signing_key: SecretStr | None = None
    signed_url_ttl_s: int = Field(default=600, gt=0)
    artifact_store: str = Field(default_factory=_home_default("gateway", "artifacts"))
    """Content-addressed artifact store ``<root>/<sha256[:2]>/<sha256>`` (+ ``.json`` meta); the runner's
    ``local`` transfer writes into the same directory (``MBNAS_RUNNER_ARTIFACT_STORE_DIR``)."""
    artifact_max_bytes: int = Field(default=4 << 30, gt=0)
    """Size cap of one stored artifact object (default 4 GiB)."""
    artifact_verify_first_max_bytes: int = Field(default=64 << 20, ge=0)
    """Downloads of objects up to this size are hashed completely before the response starts, so a digest mismatch is
    a clean 409 ``LOAD_FAILED``; larger objects are verified while they stream (``mbnas_server.artifacts.download``;
    a mismatch then aborts the body, compare ``X-Artifact-Sha256``). Also the per-object limit of the export zip."""
    max_request_body_bytes: int = Field(default=1 << 20, gt=0)
    archive_roots: StrList = Field(default_factory=list)
    """Read-only roots whose sub-directories are ``yolo_ga_full`` run directories (served by ``/api/v1/archive``).
    Never written. Default empty = nothing to serve: the archive routes answer 503 ``ARCHIVE_ROOT_UNAVAILABLE`` until a root
    (or a campaign root) is configured."""
    archive_driver_dirs: StrList = Field(default_factory=list)
    """Pipeline driver directories holding ``STATUS.json`` / ``pipeline.log`` (read-only, shown by ``/archive/live``)."""
    archive_campaign_roots: StrList = Field(default_factory=list)
    """Optional read-only 12-search campaign roots (``MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS``, comma-separated or JSON list like
    ``archive_roots``): each entry is a campaign root (``campaign.json`` + ``arms/<space>/<method>/<seed>/``) or a directory whose
    sub-directories are campaign roots. Every arm is served as an archive run with family ``visdrone-campaign``
    (``archive/campaign.py``). Default empty = feature off."""
    archive_cache_ttl_s: float = Field(default=5.0, ge=0, le=3600)
    """Seconds before cached archive scans are re-validated against file mtime/size (0 = always re-check)."""
    log_dir: str = Field(default_factory=_home_default("gateway", "logs"))

    # -- relay to core ------------------------------------------------------------------------------------------------
    core_prefix: str = DEFAULT_CORE_PREFIX
    """Path prefix of the core console API (``/core/v1/console``; ``/api/v1`` only for a legacy-exposing core)."""
    core_allow_insecure_http: bool = False
    """A plain ``http://`` ``core_url`` to a non-loopback host is refused (``CORE_URL_INSECURE``: the service
    token would cross the network in clear text) unless this is set explicitly (``MBNAS_GW_CORE_ALLOW_INSECURE_HTTP=
    true``). ``https://`` and loopback ``http://`` are always accepted. Enforced when the gateway opens its network client
    to core (app start) and reported by ``mbnas-gateway-doctor``."""
    core_tls_verify: bool = True
    """Verify the core TLS certificate (https core_url). ``core_ca_file`` adds a private CA bundle. ``false`` accepts
    any certificate (the service token can then reach an impostor): never silent - the gateway logs a warning when it
    opens its network client to core and ``mbnas-gateway-doctor`` reports a ``core_tls`` warning
    (:meth:`core_tls_problem`)."""
    core_ca_file: str | None = None
    core_connect_timeout_s: float = Field(default=5.0, gt=0, le=300)
    core_read_timeout_s: float = Field(default=120.0, gt=0, le=3600)
    """Read timeout of ordinary relayed calls (validate/structure checks are synchronous on core)."""
    core_sse_read_timeout_s: float = Field(default=60.0, gt=0, le=3600)
    """Silence on the core SSE stream longer than this ends the browser stream (core heartbeats every 15 s)."""
    sse_heartbeat_s: float = Field(default=15.0, gt=0, le=300)
    """Gateway-side SSE heartbeat when core sent nothing for this long."""
    dataset_roots: StrList = Field(default_factory=list)
    """Customer-host dataset allow-roots for validate pre-processing (path checks + manifests). Empty = the gateway
    forwards validate unchanged and core checks the paths locally (owner all-in-one)."""
    output_roots: StrList = Field(default_factory=list)
    model_yaml_roots: StrList = Field(default_factory=list)
    """Allow-roots for a user model YAML given by ``model_path`` (with ``output_roots``); read by the gateway and sent to
    core as text, never as a path."""
    manifest_cache_root: str = Field(default_factory=_home_default("gateway"))
    """DatasetService output root: manifests are cached under ``<manifest_cache_root>/manifests``."""
    min_free_disk_gb: float = Field(default=0.0, ge=0)
    upload_max_bytes: int = Field(default=1 << 20, gt=0, le=16 << 20)
    # -- rate limits --------------------------------------------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_rps: float = Field(default=10.0, gt=0)
    rate_limit_burst: int = Field(default=50, ge=1)
    expensive_concurrency: int = Field(default=1, ge=1)
    expensive_per_minute: int = Field(default=30, ge=1)
    sse_max_per_token: int = Field(default=4, ge=1)
    audit_log: str = Field(default_factory=_home_default("gateway", "logs", "audit.jsonl"))
    # -- static web ---------------------------------------------------------------------------------------------------
    web_dist: str = Field(default_factory=_default_web_dist)

    @field_validator("cors_origins", "archive_roots", "archive_campaign_roots", "archive_driver_dirs", "dataset_roots",
                     "output_roots", "model_yaml_roots", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("artifact_store", "log_dir", "manifest_cache_root", "audit_log", "web_dist", "core_ca_file",
                     mode="before")
    @classmethod
    def _path_like(cls, value: Any) -> Any:
        return os.fspath(value) if isinstance(value, os.PathLike) else value

    @field_validator("api_token", "core_token", "artifact_signing_key", mode="before")
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

    @field_validator("core_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise ValueError(f"core_url must be http(s)://host[:port][/prefix] without query/fragment, got {value!r}")
        return value.rstrip("/")

    @field_validator("core_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value or ".." in value.split("/"):
            raise ValueError(f"core_prefix must be an absolute URL path such as /core/v1/console, got {value!r}")
        return value.rstrip("/")

    @field_validator("artifact_store", "log_dir", "manifest_cache_root", "audit_log", "web_dist")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not os.path.isabs(value):
            raise ValueError(f"must be an absolute path: {value!r}")
        return value

    @field_validator("archive_roots", "archive_campaign_roots", "archive_driver_dirs", "dataset_roots", "output_roots",
                     "model_yaml_roots")
    @classmethod
    def _absolute_archive(cls, roots: list[str]) -> list[str]:
        for root in roots:
            if not os.path.isabs(root):
                raise ValueError(f"root path must be absolute: {root!r}")
        return roots

    @classmethod
    def load(cls, dotenv_path: str | os.PathLike[str] | None = None) -> "GatewaySettings":
        """Load from the environment, plus ``dotenv_path`` (or ``$MBNAS_GW_ENV_FILE``) when given."""
        path = dotenv_path or os.environ.get(ENV_FILE_VAR)
        if path is not None and not Path(path).is_file():
            raise FileNotFoundError(f"dotenv file not found: {path}")
        return cls(_env_file=path) if path else cls()

    def core_url_problem(self) -> str | None:
        """``CORE_URL_INSECURE`` message for this ``core_url`` (see ``core_allow_insecure_http``), else ``None``."""
        parts = urlsplit(self.core_url)
        if parts.scheme != "http" or is_loopback_host(parts.hostname) or self.core_allow_insecure_http:
            return None
        where = f"http://{parts.hostname}" + (f":{parts.port}" if parts.port else "")  # no userinfo in messages
        return (f"CORE_URL_INSECURE: core_url {where} is plain http to a non-loopback host; use https:// (with "
                "MBNAS_GW_CORE_CA_FILE for a private CA) or set MBNAS_GW_CORE_ALLOW_INSECURE_HTTP=true explicitly")

    def core_tls_problem(self) -> str | None:
        """``CORE_TLS_VERIFY_DISABLED`` warning text when ``core_tls_verify`` is false, else ``None``."""
        if self.core_tls_verify:
            return None
        parts = urlsplit(self.core_url)
        where = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")  # never the userinfo
        return (f"CORE_TLS_VERIFY_DISABLED: core_tls_verify=false: a TLS certificate presented for core_url {where} is "
                "not verified, so whoever answers in its place receives the service token; use MBNAS_GW_CORE_CA_FILE "
                "for a private CA instead")

    def require_secure_core_url(self) -> None:
        """Raise :class:`CoreUrlInsecure` when :meth:`core_url_problem` reports one; log :meth:`core_tls_problem` as a
        warning otherwise. Called when the gateway opens its network client to core (app start)."""
        problem = self.core_url_problem()
        if problem is not None:
            raise CoreUrlInsecure(problem)
        warning = self.core_tls_problem()
        if warning is not None:
            log.warning("%s", warning)

    def require_api_token(self) -> str:
        """Return the bearer token or raise; the gateway must not start without one."""
        if self.api_token is None:
            raise RuntimeError("MBNAS_GW_API_TOKEN is required to start the gateway")
        return self.api_token.get_secret_value()

    def require_signing_key(self) -> bytes:
        """Return the artifact URL signing key or raise with generation guidance."""
        if self.artifact_signing_key is None:
            raise RuntimeError(f"MBNAS_GW_ARTIFACT_SIGNING_KEY is required for signed artifact URLs; {SIGNING_KEY_GUIDANCE}")
        return self.artifact_signing_key.get_secret_value().encode("utf-8")
