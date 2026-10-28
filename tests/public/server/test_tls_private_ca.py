"""Gateway -> core TLS with a private CA (C05 D4/D5; DD-A41 (d), ``mbnas_server.api.core_client._verify``).

A local HTTPS stub on 127.0.0.1 presents a server certificate signed by a throw-away private test CA (EC P-256, made
per module with the ``openssl`` CLI under the test's tmp dir; SYNTHETIC). The gateway's core client and the doctor's
``core`` check refuse it without ``core_ca_file`` (default trust store) and with an unrelated CA, and connect with
``core_ca_file`` = the private CA; a refused handshake delivers no request, so the service token never leaves.
``core_tls_verify=false`` still connects (documented switch, unchanged) but is never silent: a ``WARNING`` line when the
app opens its core client and a ``core_tls`` warning in ``mbnas-gateway-doctor`` (``CORE_TLS_VERIFY_DISABLED``).
Loopback only: the cross-host TLS path (reverse proxy, real certificates) is T5/T6 evidence, not this test's.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SERVICE_TOKEN = "gw-core-service-token-0123456789"  # SYNTHETIC
API_TOKEN = "gw-api-token-0123456789"  # SYNTHETIC

CA_CNF = """[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = {cn}
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
"""
SERVER_CNF = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = 127.0.0.1
[v3_srv]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"""


def _openssl(cwd: Path, *args: str) -> None:
    exe = shutil.which("openssl")
    if exe is None:
        pytest.skip("openssl CLI not available: cannot make the private test CA")
    subprocess.run([exe, *args], cwd=cwd, check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> dict[str, Path]:
    """``ca`` (signs the server certificate), ``other_ca`` (unrelated), ``srv`` certificate + key for 127.0.0.1."""
    d = tmp_path_factory.mktemp("private_ca")
    for name in ("ca", "other_ca"):
        (d / f"{name}.cnf").write_text(CA_CNF.format(cn=f"MBNAS SYNTHETIC test CA {name}"))
        _openssl(d, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", f"{name}.key")
        _openssl(d, "req", "-x509", "-new", "-config", f"{name}.cnf", "-key", f"{name}.key", "-sha256", "-days", "2",
                 "-out", f"{name}.pem")
    (d / "srv.cnf").write_text(SERVER_CNF)
    _openssl(d, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "srv.key")
    _openssl(d, "req", "-new", "-config", "srv.cnf", "-key", "srv.key", "-out", "srv.csr")
    _openssl(d, "x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-set_serial",
             "0x" + secrets.token_hex(8), "-days", "2", "-sha256", "-extfile", "srv.cnf", "-extensions", "v3_srv",
             "-out", "srv.pem")
    return {"ca": d / "ca.pem", "other_ca": d / "other_ca.pem", "srv_cert": d / "srv.pem", "srv_key": d / "srv.key"}


class _Handler(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        self.server.seen.append({"method": self.command, "path": self.path,  # type: ignore[attr-defined]
                                 "authorization": self.headers.get("authorization")})
        body = json.dumps({"status": "ok", "stub": "SYNTHETIC https core"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def https_core(pki, monkeypatch):
    """(url, seen requests) of the HTTPS stub; the trust store is the default one (no SSL_CERT_* / proxy variables)."""
    for var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy",
                "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    srv.seen = []  # type: ignore[attr-defined]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(pki["srv_cert"], pki["srv_key"])
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)  # a refused handshake fails in accept(): no request
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{srv.server_address[1]}", srv.seen  # type: ignore[attr-defined]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


def _settings(tmp_path: Path, url: str, **kw: Any):
    from mbnas_server.config import GatewaySettings

    return GatewaySettings(core_url=url, core_token=SERVICE_TOKEN, api_token=API_TOKEN, artifact_signing_key="k" * 48,
                           artifact_store=str(tmp_path / "store"), log_dir=str(tmp_path / "logs"),
                           audit_log=str(tmp_path / "logs" / "audit.jsonl"),
                           manifest_cache_root=str(tmp_path / "cache"), web_dist=str(tmp_path / "web_dist"), **kw)


def _relay_health(settings) -> Any:
    """``GET <core_prefix>/health`` through the gateway's core client (the relay's own network client)."""
    from mbnas_server.api.core_client import CoreClient

    async def call() -> Any:
        client = CoreClient(settings)
        try:
            return await client.request("GET", "/health", request_id="c05-tls")
        finally:
            await client.aclose()

    return asyncio.run(call())


def _doctor(settings) -> dict[str, dict[str, Any]]:
    from mbnas_server import doctor

    checks = doctor.run_checks(settings)
    assert SERVICE_TOKEN not in json.dumps(checks) and API_TOKEN not in json.dumps(checks)
    return {c["name"]: c for c in checks}


@pytest.mark.parametrize("ca", [None, "other_ca"], ids=["no core_ca_file", "unrelated CA"])
def test_untrusted_core_certificate_is_refused_before_any_request(tmp_path, pki, https_core, ca):
    from mbnas_server.api.errors import GatewayError

    url, seen = https_core
    settings = _settings(tmp_path, url, core_ca_file=str(pki[ca]) if ca else None)
    with pytest.raises(GatewayError) as exc:
        _relay_health(settings)
    assert (exc.value.status, exc.value.code, exc.value.location) == (503, "CORE_UNAVAILABLE", "core")
    assert "CERTIFICATE_VERIFY_FAILED" in str(exc.value.__cause__)
    checks = _doctor(settings)
    assert checks["core"]["ok"] is False and "unreachable: ConnectError" in checks["core"]["detail"]
    assert checks["core_tls"]["ok"] is True and checks["core_url"]["detail"] == "https"
    assert seen == []  # no request completed: the service token never reached the impostor


def test_private_ca_file_connects(tmp_path, pki, https_core, caplog):
    from mbnas_server.api.app import create_app

    url, seen = https_core
    settings = _settings(tmp_path, url, core_ca_file=str(pki["ca"]))
    r = _relay_health(settings)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert seen == [{"method": "GET", "path": "/core/v1/console/health", "authorization": f"Bearer {SERVICE_TOKEN}"}]
    checks = _doctor(settings)
    assert checks["core"]["ok"] is True and checks["core"]["detail"].endswith("-> HTTP 200 status=ok")
    assert checks["core_tls"] == {"name": "core_tls", "ok": True, "warning": True,
                                  "detail": "certificate verified against core_ca_file"}
    caplog.set_level(logging.WARNING, logger="mbnas_server.config")
    create_app(settings)  # opens the network core client: verification on, so no TLS warning
    assert "CORE_TLS_VERIFY_DISABLED" not in caplog.text


def test_tls_verify_off_still_connects_but_warns_at_start_and_in_doctor(tmp_path, pki, https_core, caplog):
    from mbnas_server import doctor
    from mbnas_server.api.app import create_app

    url, seen = https_core
    settings = _settings(tmp_path, url, core_tls_verify=False)
    assert settings.core_tls_problem().startswith("CORE_TLS_VERIFY_DISABLED")
    assert _settings(tmp_path, url).core_tls_problem() is None
    # unchanged behaviour: the switch accepts the untrusted certificate
    assert _relay_health(settings).status_code == 200 and len(seen) == 1
    # never silent: one WARNING line when the app opens its network client to core (app start) ...
    caplog.set_level(logging.WARNING, logger="mbnas_server.config")
    caplog.clear()  # the relay client above logged its own line: count only the app start's
    create_app(settings)
    lines = [r for r in caplog.records
             if r.name == "mbnas_server.config" and "CORE_TLS_VERIFY_DISABLED" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    assert SERVICE_TOKEN not in caplog.text and API_TOKEN not in caplog.text
    # ... and a core_tls warning in the doctor (warning only: the doctor still passes, as before)
    checks = _doctor(settings)
    assert checks["core_tls"]["ok"] is False and checks["core_tls"]["warning"] is True
    assert checks["core_tls"]["detail"].startswith("CORE_TLS_VERIFY_DISABLED")
    assert checks["core"]["ok"] is True
    assert all(c["ok"] or c["warning"] for c in doctor.run_checks(settings))


def test_doctor_tls_detail_for_plain_http_names_no_certificate(tmp_path):
    checks = _doctor(_settings(tmp_path, "http://127.0.0.1:9"))  # loopback http: accepted, nothing listens there
    assert checks["core_tls"] == {"name": "core_tls", "ok": True, "warning": True,
                                  "detail": "plain http core_url: no TLS on this connection (see core_url)"}
    assert checks["core_url"]["detail"] == "loopback http"


def test_tls_warning_names_no_userinfo(tmp_path):
    settings = _settings(tmp_path, "https://user:pw-secret@core.example:8443", core_tls_verify=False)
    problem = settings.core_tls_problem()
    assert "https://core.example:8443" in problem and "pw-secret" not in problem and "user:" not in problem
