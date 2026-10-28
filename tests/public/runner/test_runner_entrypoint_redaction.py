"""C05-O2 (C05 EVAL_2; EVAL_1 D10): the runner entry point installs the shared log redaction before anything talks to the
core, and in a fresh runner interpreter no token / sig / exp / access_token query value and no Authorization value
reaches a log line.

* ``mbnas-runner run`` and ``mbnas-runner register`` install :class:`RedactSecretsFilter` on every logger of
  ``mbnas_runner.__main__.REDACTED_LOGGERS`` and on :data:`logging.lastResort` before the settings are read (ported from
  the C05 fix-1d scratch test ``d10``); ``--spec-file`` (the job child) does not.
* E12 form (C05 EVAL_2): a new ``-I -B`` interpreter calls ``install_redaction()`` and logs synthetic secrets through the
  HTTP client loggers, the runner package logger, a runner child logger, a third-party logger and the root logger. With no
  handler configured (the runner configures none) every record goes through ``logging.lastResort``; after an operator
  configures a root handler, records of the redacted loggers are still rewritten at their origin. A control run of the
  same script without ``install_redaction()`` shows the secrets, so the scan cannot pass vacuously.

Public bundle: public packages only (no ``tests/_shared`` harness). The in-process tests put every logger / handler /
lastResort filter list back the way they found it.
"""
from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mbnas_contracts.util.log_redact import REDACTED, RedactSecretsFilter


def _targets(entry: Any) -> list[Any]:
    """The loggers of REDACTED_LOGGERS, their current handlers and logging.lastResort (what install_redaction touches)."""
    out: list[Any] = []
    for name in entry.REDACTED_LOGGERS:
        lg = logging.getLogger(name)
        out.append(lg)
        out.extend(lg.handlers)
    if logging.lastResort is not None:
        out.append(logging.lastResort)
    return out


def _has(target: Any) -> bool:
    return any(isinstance(f, RedactSecretsFilter) for f in target.filters)


@contextlib.contextmanager
def _clean_redaction_state(entry: Any) -> Iterator[None]:
    """Strip RedactSecretsFilter from every target for the block (so "installed" is not inherited from an earlier test),
    then restore every filter list exactly; targets that appeared during the block lose the filters they gained."""
    saved = [(t, list(t.filters)) for t in _targets(entry)]
    for t, _ in saved:
        t.filters = [f for f in t.filters if not isinstance(f, RedactSecretsFilter)]
    try:
        yield
    finally:
        known = {id(t) for t, _ in saved}
        for t in _targets(entry):
            if id(t) not in known:
                t.filters = [f for f in t.filters if not isinstance(f, RedactSecretsFilter)]
        for t, filters in saved:
            t.filters = filters


@pytest.mark.parametrize("command", ["run", "register"])
def test_runner_commands_install_redaction_before_settings(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from mbnas_runner import __main__ as entry

    seen: dict[str, Any] = {}

    def fake_settings(env_file: str | None) -> Any:
        seen["loggers"] = {name: _has(logging.getLogger(name)) for name in entry.REDACTED_LOGGERS}
        seen["last_resort"] = logging.lastResort is not None and _has(logging.lastResort)
        raise SystemExit(0)

    monkeypatch.setattr(entry, "_settings", fake_settings)
    with _clean_redaction_state(entry):
        assert not any(_has(logging.getLogger(n)) for n in entry.REDACTED_LOGGERS)  # really starts clean
        with pytest.raises(SystemExit):
            entry.main([command])
        assert seen["loggers"] == {name: True for name in entry.REDACTED_LOGGERS}
        assert seen["last_resort"] is True
        # the installed filter rewrites an HTTP client request line (args) and an Authorization value (msg)
        flt = next(f for f in logging.getLogger("httpx").filters if isinstance(f, RedactSecretsFilter))
        rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, 'HTTP Request: GET %s "HTTP/1.1 200 OK"',
                                ("https://core.example/x?token=abc123secret&sig=deadbeef&exp=1767225600",), None)
        assert flt.filter(rec) is True  # never drops a record
        text = rec.getMessage()
        assert "abc123secret" not in text and "deadbeef" not in text and "1767225600" not in text
        assert text.count(REDACTED) == 3
        auth = logging.LogRecord("mbnas_runner", logging.WARNING, __file__, 1, "Authorization: Bearer tok-0a1b2c", None,
                                 None)
        flt.filter(auth)
        assert "tok-0a1b2c" not in auth.getMessage() and REDACTED in auth.getMessage()
    # restored: nothing this test installed is left on the shared loggers
    assert seen  # the fake settings ran


def test_spec_file_path_does_not_install(monkeypatch: pytest.MonkeyPatch) -> None:
    from mbnas_runner import __main__ as entry
    import mbnas_runner.child as child

    monkeypatch.setattr(child, "main", lambda argv: 0)
    with _clean_redaction_state(entry):
        assert entry.main(["--spec-file", "/nonexistent.json"]) == 0
        assert not _has(logging.getLogger("httpx"))
        assert logging.lastResort is None or not _has(logging.lastResort)


# E12 form: a fresh interpreter, synthetic secrets, every logger route of the runner process
SECRETS = {
    "token": "c05o2-token-7f3a91",
    "sig": "c05o2-sig-4be2d0",
    "exp": "c05o2-exp-1767225600",
    "access_token": "c05o2-access-9c1e55",
    "bearer": "c05o2-bearer-2d8f6a",
    "authz": "c05o2-authz-6e0b13",
    "late_sig": "c05o2-latesig-3a7c42",
    "late_token": "c05o2-latetoken-81f0de",
    "late_bearer": "c05o2-latebearer-5b2e97",
}
SCRIPT = r'''
import logging, sys
mode, s = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
if mode == "install":
    from mbnas_runner.__main__ import install_redaction
    install_redaction()
else:
    import mbnas_runner.__main__  # control: same import, no redaction installed
# phase 1: no handler configured anywhere (the runner configures none) -> logging.lastResort (stderr, WARNING)
logging.getLogger("httpx").warning('HTTP Request: GET %s "HTTP/1.1 200 OK"',
    "https://core.example/core/v1/runner/claim?token=%s&sig=%s&exp=%s" % (s["token"], s["sig"], s["exp"]))
logging.getLogger("httpcore").warning("send_request_headers Authorization: Bearer %s" % s["bearer"])
logging.getLogger("mbnas_runner").warning("GET /core/v1/runner/artifacts/a1?access_token=%s" % s["access_token"])
logging.getLogger("mbnas_runner.daemon").warning("retry GET /x?sig=%s&exp=%s" % (s["sig"], s["exp"]))
logging.getLogger("some.thirdparty.lib").warning("headers {'authorization': '%s'}" % s["authz"])
logging.getLogger().warning("root line token=%s" % s["token"])
# phase 2: an operator configures a root handler afterwards; records of the redacted loggers are rewritten at the origin
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(name)s|%(levelname)s|%(message)s")
logging.getLogger("httpx").info("HTTP Request: POST %s", "https://core.example/core/v1/runner/result?sig=%s" % s["late_sig"])
logging.getLogger("mbnas_runner").info("claim url https://core.example/c?token=%s" % s["late_token"])
logging.getLogger().info("Authorization: Bearer %s" % s["late_bearer"])
'''
PHASE1_LINES, PHASE2_LINES = 6, 3


def _run_script(mode: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-I", "-B", "-c", SCRIPT, mode, *(f"{k}={v}" for k, v in SECRETS.items())]
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)


def test_fresh_runner_interpreter_logs_no_synthetic_secret(tmp_path: Path) -> None:
    out = _run_script("install", tmp_path)
    assert out.returncode == 0, out.stderr[-2000:]
    stderr = [ln for ln in out.stderr.splitlines() if ln.strip()]
    stdout = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(stderr) == PHASE1_LINES, out.stderr  # every phase-1 record reached lastResort (none dropped)
    assert len(stdout) == PHASE2_LINES, out.stdout
    text = out.stdout + out.stderr
    leaked = sorted(k for k, v in SECRETS.items() if v in text)
    assert leaked == [], f"synthetic secrets in the runner log output: {leaked}\n{text}"
    assert all(REDACTED in ln for ln in stderr + stdout), text
    # the redaction keeps the rest of the line (route, key names, status)
    assert "core.example/core/v1/runner/claim?token=***&sig=***&exp=***" in text
    assert "?access_token=***" in text and 'HTTP/1.1 200 OK' in text
    assert stdout[0].startswith("httpx|INFO|") and stdout[1].startswith("mbnas_runner|INFO|")


def test_control_without_redaction_shows_the_secrets(tmp_path: Path) -> None:
    """Negative control of the scan above: the same records without install_redaction() carry every secret."""
    out = _run_script("control", tmp_path)
    assert out.returncode == 0, out.stderr[-2000:]
    text = out.stdout + out.stderr
    assert sorted(k for k, v in SECRETS.items() if v not in text) == []
    assert REDACTED not in text
