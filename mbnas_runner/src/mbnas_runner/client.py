"""HTTP client of the core's runner control plane ``/core/v1/runner/*`` (A3 section 3, WP-2 contract §2.1).

* transport security (A3 section 11.2, DD-A18): ``core_url`` must be ``https://`` unless its host is a loopback address
  (``127.0.0.0/8``, ``::1``, ``localhost``); anything else is refused at construction with
  :class:`RunnerConfigError` code ``CORE_URL_INSECURE``. TLS verification uses ``ca_file`` when configured, else the
  system store (``verify=True``); it is never switched off;
* authentication: ``Authorization: Bearer <runner token>`` (``RunnerSettings.bearer_token``), ``X-Request-ID`` per call;
* every write carries ``runner_id``; job-scoped writes also carry ``attempt`` and the job ``ticket`` from the claim
  (:class:`JobFence`);
* retries: connection errors, timeouts, 5xx (``COMMIT_FAILED`` included) and 202 ``PROCESSING`` are retried with
  exponential backoff (``Retry-After`` honoured) up to ``request_retries`` times, then raised as
  :class:`CoreUnavailable`; any other 4xx is raised at once as :class:`CoreError` with the core's error ``code``
  (``LEASE_LOST``, ``RESULT_CONFLICT``, ``KIND_NOT_ALLOWED``, ``RUNNER_MISMATCH``, ``VERSION_MISMATCH``,
  ``TICKET_INVALID``, ...). Retrying is safe because every write is fenced or idempotent (A3 sections 5.6/7).
"""
from __future__ import annotations

import ipaddress
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

RUNNER_PREFIX = "/core/v1/runner"


class RunnerConfigError(RuntimeError):
    """The runner configuration cannot be used (``code`` is the protocol error code, e.g. ``CORE_URL_INSECURE``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class CoreError(RuntimeError):
    """A non-retryable answer of the core (``status`` HTTP status, ``code`` its error code, ``body`` the JSON body)."""

    def __init__(self, status: int, code: str, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body


class CoreUnavailable(RuntimeError):
    """The core did not answer usefully within the retry budget (connection error, 5xx, PROCESSING)."""


@dataclass(frozen=True)
class JobFence:
    """What fences a job-scoped write: the job, this runner, the lease epoch and the claim's ticket."""

    job_id: str
    attempt: int
    ticket: str | None

    @classmethod
    def from_claim(cls, claim: dict[str, Any]) -> "JobFence":
        """Fence of a claim response ``{envelope, ticket, server_time}`` (``ClaimResponse``)."""
        env = claim_envelope(claim)
        return cls(str(env["job"]["job_id"]), int(env["job"]["attempt"]), claim.get("ticket"))


def claim_envelope(claim: dict[str, Any]) -> dict[str, Any]:
    """The JobEnvelope inside a claim response (``ClaimResponse.envelope``)."""
    env = claim.get("envelope")
    if not isinstance(env, dict):
        raise KeyError("claim response has no envelope")
    return env


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_core_url(url: str) -> str:
    """``url`` without a trailing slash; ``CORE_URL_INSECURE`` for plain http to a non-loopback host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RunnerConfigError("CORE_URL_INVALID", f"core_url must be an http(s) URL with a host, got {url!r}")
    if parts.scheme == "http" and not is_loopback_host(parts.hostname):
        raise RunnerConfigError("CORE_URL_INSECURE", f"core_url {url} is plain http to a non-loopback host; use "
                                "https:// (with ca_file for a private CA)")
    return url.rstrip("/")


def _error_code(resp: httpx.Response) -> tuple[str, str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP_{resp.status_code}", resp.text[:300], None
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        detail = err.get("detail") if isinstance(err.get("detail"), dict) else err
        return (str(detail.get("code") or f"HTTP_{resp.status_code}"), str(detail.get("message") or "")[:500], body)
    return f"HTTP_{resp.status_code}", str(body)[:300], body


class CoreClient:
    """One runner's view of ``/core/v1/runner``; see module docstring."""

    def __init__(self, settings: Any, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self.base_url = check_core_url(settings.core_url)
        self.runner_id = str(settings.runner_id)
        self.retries = int(getattr(settings, "request_retries", 5))
        self.backoff_s = float(getattr(settings, "request_backoff_s", 0.5))
        self._sleep = sleep
        token = settings.bearer_token() if hasattr(settings, "bearer_token") else None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        ca_file = getattr(settings, "ca_file", None)
        # never False; a private CA becomes an SSLContext (httpx deprecated verify=<path>; same check)
        verify: Any = ssl.create_default_context(cafile=str(ca_file)) if ca_file else True
        self.http = httpx.Client(base_url=self.base_url + RUNNER_PREFIX, headers=headers, verify=verify,
                                 timeout=float(getattr(settings, "request_timeout_s", 30.0)), transport=transport,
                                 follow_redirects=False)

    def close(self) -> None:
        self.http.close()

    # -- transport -------------------------------------------------------------------------------------------
    def request(self, method: str, path: str, body: dict[str, Any] | None = None, *,
                retries: int | None = None) -> tuple[int, Any, httpx.Headers]:
        """(status, JSON body or None, headers) of a 2xx answer other than 202; see module docstring for errors."""
        budget = self.retries if retries is None else retries
        delay = self.backoff_s
        last: str = "no attempt"
        for attempt in range(budget + 1):
            try:
                resp = self.http.request(method, path, json=body, headers={"X-Request-ID": uuid.uuid4().hex})
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 202:  # PROCESSING: the same commit is in flight on the core
                    last = "202 PROCESSING"
                    retry_after = resp.headers.get("Retry-After")
                    if attempt < budget:
                        self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                        delay = min(delay * 2, 30.0)
                        continue
                    break
                if 200 <= resp.status_code < 300:
                    data = resp.json() if resp.content else None
                    return resp.status_code, data, resp.headers
                code, message, payload = _error_code(resp)
                if resp.status_code < 500:
                    raise CoreError(resp.status_code, code, message, payload)
                last = f"HTTP {resp.status_code} {code}: {message}"
            if attempt < budget:
                self._sleep(delay)
                delay = min(delay * 2, 30.0)
        raise CoreUnavailable(f"{method} {path}: {last} (after {budget + 1} attempts)")

    def _job_body(self, fence: JobFence, **fields: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"runner_id": self.runner_id, "attempt": fence.attempt, **fields}
        if fence.ticket is not None:
            body["ticket"] = fence.ticket
        return body

    # -- E1..E10 -----------------------------------------------------------------------------------------------
    def register(self, *, kinds: list[str], runtime: dict[str, Any], host: str | None = None,
                 device: str | None = None, device_uuid: str | None = None, device_name: str | None = None,
                 data_mounts: dict[str, bool] | None = None) -> dict[str, Any]:
        """E1 ``POST /register`` (``RegisterRequest``) -> ``RegisterResponse``."""
        body = {"runner_id": self.runner_id, "runtime": runtime, "kinds": list(kinds), "host": host, "device": device,
                "device_uuid": device_uuid, "device_name": device_name, "data_mounts": dict(data_mounts or {})}
        _, data, _ = self.request("POST", "/register", body)
        return data or {}

    def claim(self, kinds: list[str]) -> dict[str, Any] | None:
        """E2 ``POST /claim``: the ``ClaimResponse`` ``{envelope, ticket, server_time}``, or None (204)."""
        status, data, _ = self.request("POST", "/claim", {"runner_id": self.runner_id, "kinds": list(kinds)})
        return None if status == 204 or not data else data

    def start(self, fence: JobFence, *, device_uuid: str | None = None) -> dict[str, Any]:
        """E3 ``POST /jobs/{id}/start`` -> ``{started_at, lease_expires_at, server_time}``."""
        body = self._job_body(fence, device_uuid=device_uuid)
        return self.request("POST", f"/jobs/{fence.job_id}/start", body)[1] or {}

    def heartbeat(self, fence: JobFence, *, device_sample: dict[str, Any] | None = None,
                  progress: dict[str, Any] | None = None) -> dict[str, Any]:
        """E4 ``POST /jobs/{id}/heartbeat`` -> ``{lease_expires_at, control: {action, run_status, ...}}``.

        Not retried here (the next beat is the retry; a beat that cannot be delivered must not delay the watchdog)."""
        body = self._job_body(fence, device_sample=device_sample, progress=progress or {})
        return self.request("POST", f"/jobs/{fence.job_id}/heartbeat", body, retries=0)[1] or {}

    def progress(self, fence: JobFence, *, seq_start: int, messages: list[dict[str, Any]],
                 retries: int | None = None) -> dict[str, Any]:
        """E5 ``POST /jobs/{id}/progress`` (``ProgressBatch``: ProgressMessage dicts numbered from ``seq_start``)
        -> ``{accepted_seq}``."""
        body = self._job_body(fence, seq_start=seq_start, messages=messages)
        return self.request("POST", f"/jobs/{fence.job_id}/progress", body, retries=retries)[1] or {}

    def checkpoint(self, fence: JobFence, report: dict[str, Any]) -> dict[str, Any]:
        """E6 ``POST /jobs/{id}/checkpoints`` (``CheckpointReport``) -> ``{checkpoint_id, location_id, state}``."""
        return self.request("POST", f"/jobs/{fence.job_id}/checkpoints", self._job_body(fence, **report))[1] or {}

    def artifacts(self, fence: JobFence, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """E7 ``POST /jobs/{id}/artifacts`` (batch) -> ``[{relpath, artifact_id, state}]``."""
        data = self.request("POST", f"/jobs/{fence.job_id}/artifacts", self._job_body(fence, artifacts=items))[1]
        if isinstance(data, dict):
            data = data.get("artifacts", [])
        return list(data or [])

    def result(self, fence: JobFence, commit: dict[str, Any]) -> dict[str, Any]:
        """E8 ``POST /jobs/{id}/result`` (idempotent on (job, attempt, payload_digest))."""
        return self.request("POST", f"/jobs/{fence.job_id}/result", self._job_body(fence, **commit))[1] or {}

    def ack(self, fence: JobFence) -> None:
        """E9 ``POST /jobs/{id}/ack``."""
        self.request("POST", f"/jobs/{fence.job_id}/ack", self._job_body(fence))

    def deregister(self) -> None:
        """E10 ``POST /deregister``."""
        self.request("POST", "/deregister", {"runner_id": self.runner_id}, retries=1)
