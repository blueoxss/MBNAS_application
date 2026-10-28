"""S11-F3: secrets carried in query strings / Authorization values never reach a log record."""
from __future__ import annotations

import logging

import pytest

from mbnas_contracts.util.log_redact import REDACTED, RedactSecretsFilter, install_log_redaction, redact

SECRET = "s3cr3t-VALUE_xyz"


@pytest.mark.parametrize("text", [
    f"/api/v1/runs?token={SECRET}",
    f"/api/v1/runs?a=1&access_token={SECRET}&b=2",
    f"/api/v1/artifacts/x?exp=1799999999&sig={SECRET}",
    f"/api/v1/x?TOKEN={SECRET}",
    f"/api/v1/x?sig={SECRET}#frag",
    f"Authorization: Bearer {SECRET}",
    f"authorization={SECRET}",
    f"'authorization': 'Basic {SECRET}'",
])
def test_redact_strips_secret(text):
    out = redact(text)
    assert SECRET not in out
    assert REDACTED in out


def test_redact_keeps_other_params_and_exp_value():
    out = redact("/a?exp=1799999999&sig=abc&limit=10&page=2")
    assert "1799999999" not in out and "abc" not in out
    assert "limit=10" in out and "page=2" in out


def test_redact_leaves_lookalike_names():
    out = redact("/a?mytoken=keep&expected=keep&signal=keep")
    assert out == "/a?mytoken=keep&expected=keep&signal=keep"


def test_filter_redacts_uvicorn_access_style_args():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5", "GET", f"/api/v1/runs?token={SECRET}", "1.1", 401), None)
    assert RedactSecretsFilter().filter(rec) is True
    assert SECRET not in rec.getMessage()
    assert "token=***" in rec.getMessage()
    assert rec.args[4] == 401  # non-strings untouched, format still valid


def test_filter_redacts_msg_and_dict_args():
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, f"Authorization: Bearer {SECRET} %(h)s",
                            ({"h": f"?sig={SECRET}"},), None)
    RedactSecretsFilter().filter(rec)
    assert SECRET not in rec.getMessage()


def test_install_is_idempotent_and_applies_to_named_loggers():
    install_log_redaction()
    install_log_redaction()
    for name in ("uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        assert sum(isinstance(f, RedactSecretsFilter) for f in lg.filters) == 1
        assert any(isinstance(f, RedactSecretsFilter) for h in lg.handlers for f in h.filters) or not lg.handlers


def test_logger_records_are_redacted_end_to_end(caplog):
    install_log_redaction()
    lg = logging.getLogger("uvicorn.access")
    old_propagate = lg.propagate
    lg.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/x?access_token={SECRET}", "1.1", 401)
    finally:
        lg.propagate = old_propagate
    assert caplog.records
    assert all(SECRET not in r.getMessage() for r in caplog.records)
    assert SECRET not in caplog.text
