"""Job tickets (DD-A18 / WP-2 contract section 1.3): ``HMAC-SHA256(job_id|attempt|stage|runner_id|exp)``.

``claim`` issues one ticket per lease (``stage`` = the job's rung, ``-`` without one; ``exp`` = unix seconds); every
job-scoped write must present it. Format: ``v1.<base64url(payload)>.<hex hmac>``; the payload is the ``|``-joined
fields (runner ids and job ids never contain ``|``: :data:`ID_PATTERN`). Verification recomputes the MAC with
``CoreSettings.ticket_key`` (constant-time compare), then checks job, attempt, runner and expiry; any mismatch is
the same 403 ``TICKET_INVALID`` (no oracle on which field differed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from mbnas_contracts.errors import ApiError

ID_PATTERN = r"^[A-Za-z0-9._:@-]{1,128}$"
VERSION = "v1"


def _mac(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue(key: bytes, job_id: str, attempt: int, stage: str, runner_id: str, exp: int) -> str:
    """Ticket string for ``(job_id, attempt, stage, runner_id)`` valid until unix time ``exp``."""
    for part in (job_id, stage, runner_id):
        if "|" in part:
            raise ValueError(f"ticket fields must not contain '|': {part!r}")
    payload = f"{job_id}|{int(attempt)}|{stage}|{runner_id}|{int(exp)}"
    body = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{VERSION}.{body}.{_mac(key, payload)}"


def decode(key: bytes, ticket: str) -> dict[str, object] | None:
    """Fields of an authentic ticket (MAC checked, expiry NOT checked), or None."""
    try:
        version, body, mac = str(ticket).split(".")
        if version != VERSION:
            return None
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
        if not hmac.compare_digest(_mac(key, payload).encode("ascii"), mac.encode("ascii")):
            return None
        job_id, attempt, stage, runner_id, exp = payload.split("|")
        return {"job_id": job_id, "attempt": int(attempt), "stage": stage, "runner_id": runner_id, "exp": int(exp)}
    except (ValueError, UnicodeError):
        return None


def verify(key: bytes, ticket: str | None, *, job_id: str, attempt: int, runner_id: str,
           now: float | None = None) -> dict[str, object]:
    """Decoded ticket or 403 ``TICKET_INVALID`` (forged, other job/attempt/runner, or expired)."""
    fields = decode(key, ticket or "")
    now = time.time() if now is None else now
    if (fields is None or fields["job_id"] != job_id or fields["attempt"] != int(attempt)
            or fields["runner_id"] != runner_id or int(fields["exp"]) <= now):  # type: ignore[arg-type]
        raise ApiError(403, "TICKET_INVALID", "job ticket missing, invalid, expired or issued for another lease")
    return fields
