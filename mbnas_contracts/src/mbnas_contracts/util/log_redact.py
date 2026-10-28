"""Log redaction for secrets carried in URLs or headers (S11-F3).

uvicorn's access logger writes the full request path including the query string. A signed artifact URL
(``exp=``/``sig=``) is a bearer capability, and a client that wrongly puts its API token in the URL
(``token=``/``access_token=``) would otherwise leak it to ``api.log``. ``RedactSecretsFilter`` rewrites
``msg`` and every string argument of a record so those values (and any ``Authorization`` header value) become
``***``. ``install_log_redaction()`` attaches it, idempotently, to the ``uvicorn.access`` and ``uvicorn.error``
loggers and their handlers; the API app factory calls it, so every entry point (``mbnas-api``, ``uvicorn
--factory``, ``scripts/dev_stack.py``) is covered.
"""
from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "***"
SECRET_QUERY_KEYS = ("token", "access_token", "sig", "exp")
DEFAULT_LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn")

# key=value in a query string (also matches a bare ``key=value`` after ?, &, ;, whitespace, quote or start)
_QUERY_RE = re.compile(r"(?i)(?P<pre>(?:^|[?&;\s\"'(,]))(?P<key>" + "|".join(SECRET_QUERY_KEYS)
                       + r")=(?P<val>[^&\s\"'#;,)]*)")
# Authorization: <scheme> <value> / authorization=<value> / 'authorization': 'value'
_AUTH_RE = re.compile(r"(?i)(?P<key>\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?)"
                      r"(?P<val>(?:(?:bearer|basic|token|digest)\s+)?[^\s\"',;]+)")


def redact(text: str) -> str:
    """Return ``text`` with secret query values and Authorization header values replaced by ``***``."""
    if not text:
        return text
    out = _QUERY_RE.sub(lambda m: f"{m.group('pre')}{m.group('key')}={REDACTED}", text)
    return _AUTH_RE.sub(lambda m: f"{m.group('key')}{REDACTED}", out)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bytes):
        try:
            return redact(value.decode("latin-1")).encode("latin-1")
        except Exception:  # pragma: no cover - defensive
            return value
    return value


class RedactSecretsFilter(logging.Filter):
    """Rewrite a record in place so no secret query value / Authorization value survives; never drops records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            args = record.args
            if isinstance(args, dict):
                record.args = {k: _redact_value(v) for k, v in args.items()}
            elif isinstance(args, tuple):
                record.args = tuple(
                    {k: _redact_value(v) for k, v in a.items()} if isinstance(a, dict) else _redact_value(a)
                    for a in args)
        except Exception:  # pragma: no cover - a logging filter must never raise
            pass
        return True


def install_log_redaction(logger_names: tuple[str, ...] = DEFAULT_LOGGERS) -> None:
    """Attach one ``RedactSecretsFilter`` to each named logger and each of its handlers (idempotent)."""
    for name in logger_names:
        lg = logging.getLogger(name)
        targets: list[Any] = [lg, *lg.handlers]
        for target in targets:
            if not any(isinstance(f, RedactSecretsFilter) for f in target.filters):
                target.addFilter(RedactSecretsFilter())
