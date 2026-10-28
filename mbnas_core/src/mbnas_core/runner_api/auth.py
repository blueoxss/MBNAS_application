"""Scoped principals of the core (WP-2 contract section 1.3, design A3 section 11.1, DD-A18/DD-A20).

* Table ``principals`` (schema v4): ``kind`` in server|runner|admin, ``runner_id`` (required for runner), the token
  ``token_prefix`` (8 hex, lookup key) and ``token_hash`` = sha256 hex of the 32-byte random secret (stored, never the
  secret), ``scopes_json``, ``expires_at``, ``revoked_at``, ``rotated_from``, ``last_used_at``.
* Token format ``mbnas_<kind>_<prefix>_<secret>``; :func:`create_principal` / :func:`rotate_principal` return it once.
* :func:`authenticate`: prefix lookup -> constant-time hash compare -> not revoked, not expired. Every failure is the same
  401 ``AUTH_REQUIRED`` (no distinction between unknown, wrong, expired or revoked tokens).
* Scope: a route requiring ``runner`` accepts kind runner or admin; ``admin`` only admin; ``server`` server or admin.
  Otherwise 403 ``ACCESS_DENIED``. A runner token is bound to its ``runner_id``: :func:`check_runner` answers 403
  ``RUNNER_MISMATCH`` when a request names another runner.
* Rotation: a new row (``rotated_from``) is issued and the old row's ``expires_at`` is cut to ``now + grace``
  (default 15 min) so both work during the grace window. Revocation sets ``revoked_at``.
* Default lifetimes: runner 90 days, server 365 days, admin 24 hours.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, Request

from mbnas_contracts.errors import ApiError
from mbnas_contracts.util.time import iso_after, parse_iso, utcnow_iso
from mbnas_core.db.core import connect, tx

KINDS = ("server", "runner", "admin")
DEFAULT_TTL_S = {"runner": 90 * 86400, "server": 365 * 86400, "admin": 86400}
ROTATION_GRACE_S = 15 * 60
LAST_USED_THROTTLE_S = 60
SCOPE_ACCEPTS = {"runner": ("runner", "admin"), "admin": ("admin",), "server": ("server", "admin")}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    out = dict(r)
    out["scopes"] = json.loads(out.pop("scopes_json") or "[]")
    return out


def public_view(p: dict[str, Any]) -> dict[str, Any]:
    """A principal without its hash (for listings / the owner route)."""
    return {k: v for k, v in p.items() if k != "token_hash"}


def ensure_runner_row(conn: sqlite3.Connection, runner_id: str, now: str | None = None) -> None:
    """A ``runners`` row for ``runner_id`` (status REGISTERED) when none exists (principals reference it)."""
    now = now or utcnow_iso()
    conn.execute("INSERT OR IGNORE INTO runners (id, status, registered_at, created_at, updated_at) "
                 "VALUES (?, 'REGISTERED', ?, ?, ?)", (runner_id, now, now, now))


def create_principal(conn: sqlite3.Connection, kind: str, *, runner_id: str | None = None,
                     ttl_s: float | None = None, scopes: list[str] | None = None, rotated_from: str | None = None,
                     now: str | None = None) -> tuple[dict[str, Any], str]:
    """Issue a principal (inside a transaction); returns ``(row without hash, token)``. ``ttl_s=0`` = no expiry."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "runner" and not runner_id:
        raise ValueError("a runner principal needs runner_id")
    if kind != "runner" and runner_id:
        raise ValueError("only runner principals are bound to a runner_id")
    now = now or utcnow_iso()
    ttl = DEFAULT_TTL_S[kind] if ttl_s is None else ttl_s
    prefix, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
    pid = f"pr-{secrets.token_hex(8)}"
    with tx(conn):
        if runner_id:
            ensure_runner_row(conn, runner_id, now)
        conn.execute("INSERT INTO principals (id, kind, runner_id, token_prefix, token_hash, scopes_json, created_at, "
                     "expires_at, rotated_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (pid, kind, runner_id, prefix, _hash(secret), json.dumps(scopes or [kind]), now,
                      iso_after(now, ttl) if ttl else None, rotated_from))
    row = get_principal(conn, pid)
    assert row is not None
    return public_view(row), f"mbnas_{kind}_{prefix}_{secret}"


def get_principal(conn: sqlite3.Connection, principal_id: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM principals WHERE id = ?", (principal_id,)).fetchone())


def list_principals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [public_view(_row(r)) for r in conn.execute("SELECT * FROM principals ORDER BY created_at, id")]  # type: ignore[arg-type]


def rotate_principal(conn: sqlite3.Connection, principal_id: str, *, grace_s: float = ROTATION_GRACE_S,
                     ttl_s: float | None = None, now: str | None = None) -> tuple[dict[str, Any], str]:
    """New token for the same identity; the old one stays valid for ``grace_s`` more seconds at most."""
    now = now or utcnow_iso()
    old = get_principal(conn, principal_id)
    if old is None or old.get("revoked_at"):
        raise LookupError(f"principal {principal_id} not found or revoked")
    with tx(conn):
        new, token = create_principal(conn, old["kind"], runner_id=old.get("runner_id"), ttl_s=ttl_s,
                                      scopes=old["scopes"], rotated_from=principal_id, now=now)
        cut = iso_after(now, grace_s)
        if old.get("expires_at") is None or parse_iso(old["expires_at"]) > parse_iso(cut):
            conn.execute("UPDATE principals SET expires_at = ? WHERE id = ?", (cut, principal_id))
    return new, token


def revoke_principal(conn: sqlite3.Connection, principal_id: str, now: str | None = None) -> bool:
    with tx(conn):
        return conn.execute("UPDATE principals SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                            (now or utcnow_iso(), principal_id)).rowcount == 1


def _unauthorized() -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", "valid bearer token required", headers={"WWW-Authenticate": "Bearer"})


def authenticate(conn: sqlite3.Connection, token: str, now: str | None = None) -> dict[str, Any]:
    """The principal of ``token`` or 401 ``AUTH_REQUIRED`` (see module docstring)."""
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[0] != "mbnas" or parts[1] not in KINDS or not parts[3]:
        raise _unauthorized()
    _, kind, prefix, secret = parts
    digest = _hash(secret)
    now = now or utcnow_iso()
    for r in conn.execute("SELECT * FROM principals WHERE token_prefix = ? AND kind = ?", (prefix, kind)).fetchall():
        p = _row(r)
        assert p is not None
        if not hmac.compare_digest(p["token_hash"].encode("ascii"), digest.encode("ascii")):
            continue
        if p.get("revoked_at") or (p.get("expires_at") and parse_iso(p["expires_at"]) <= parse_iso(now)):
            raise _unauthorized()
        return p
    raise _unauthorized()


def _touch(conn: sqlite3.Connection, p: dict[str, Any], now: str) -> None:
    last = p.get("last_used_at")
    if last and (parse_iso(now) - parse_iso(last)).total_seconds() < LAST_USED_THROTTLE_S:
        return
    try:
        with tx(conn):
            conn.execute("UPDATE principals SET last_used_at = ? WHERE id = ?", (now, p["id"]))
    except sqlite3.OperationalError:  # bookkeeping only: never fail a request because the DB is busy
        pass


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if not header.strip() or scheme.lower() != "bearer" or not value.strip():
        raise _unauthorized()
    return value.strip()


def principal_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Per-request connection (same as ``console.deps.db``; kept here so runner routes do not depend on console)."""
    conn = connect(request.app.state.services.settings, init=False, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def require_principal(scope: str) -> Callable[..., dict[str, Any]]:
    """FastAPI dependency: authenticate the bearer token and require ``scope`` (see module docstring)."""
    if scope not in SCOPE_ACCEPTS:
        raise ValueError(f"unknown scope {scope!r}")

    def dependency(request: Request, conn: sqlite3.Connection = Depends(principal_db)) -> dict[str, Any]:
        p = authenticate(conn, bearer_token(request))
        if p["kind"] not in SCOPE_ACCEPTS[scope]:
            raise ApiError(403, "ACCESS_DENIED", f"token scope {p['kind']} cannot use this route")
        _touch(conn, p, utcnow_iso())
        request.state.principal = public_view(p)
        return public_view(p)

    return dependency


def check_runner(principal: dict[str, Any], runner_id: str) -> None:
    """403 ``RUNNER_MISMATCH`` when a runner token names another runner (admin tokens may act for any runner)."""
    if principal["kind"] == "runner" and principal.get("runner_id") != runner_id:
        raise ApiError(403, "RUNNER_MISMATCH", "runner_id does not match the token's runner")
