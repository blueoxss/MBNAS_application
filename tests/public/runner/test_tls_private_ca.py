"""Runner -> core TLS with a private CA (C05 D4; design A3 section 11.2, ``mbnas_runner.client.CoreClient``).

A local HTTPS stub on 127.0.0.1 presents a server certificate signed by a throw-away private test CA (EC P-256, made
per module with the ``openssl`` CLI under the test's tmp dir; SYNTHETIC). The runner's core client refuses it without
``ca_file`` (default trust store) and with an unrelated CA - before any request is delivered, so the runner token never
leaves - and registers with ``ca_file`` = the private CA. The runner has no switch that turns verification off.
Loopback only: the cross-host TLS path (reverse proxy, real certificates) is T5/T6 evidence, not this test's.
"""
from __future__ import annotations

import json
import secrets
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

RUNNER_TOKEN = "runner-token-0123456789abcdef"  # SYNTHETIC

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
        body = json.dumps({"runner_id": "R1", "stub": "SYNTHETIC https core"}).encode()
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


def _register(tmp_path: Path, url: str, ca_file: str | None) -> dict[str, Any]:
    from mbnas_runner.client import CoreClient
    from mbnas_runner.config import RunnerSettings

    settings = RunnerSettings(core_url=url, token=RUNNER_TOKEN, runner_id="R1", work_root=str(tmp_path / "work"),
                              output_roots=[str(tmp_path / "work")], dataset_roots=[str(tmp_path / "data")],
                              log_dir=str(tmp_path / "logs"), weights_dir=str(tmp_path / "weights"), ca_file=ca_file,
                              request_retries=0, request_timeout_s=10.0)
    client = CoreClient(settings)
    try:
        return client.register(kinds=["BASELINE_TRAIN"], runtime={"envelope_version": 1})
    finally:
        client.close()


@pytest.mark.parametrize("ca", [None, "other_ca"], ids=["no ca_file", "unrelated CA"])
def test_untrusted_core_certificate_is_refused_before_any_request(tmp_path, pki, https_core, ca):
    from mbnas_runner.client import CoreUnavailable

    url, seen = https_core
    with pytest.raises(CoreUnavailable, match="CERTIFICATE_VERIFY_FAILED"):
        _register(tmp_path, url, str(pki[ca]) if ca else None)
    assert seen == []  # no request completed: the runner token never reached the impostor


def test_private_ca_file_connects(tmp_path, pki, https_core):
    url, seen = https_core
    assert _register(tmp_path, url, str(pki["ca"])) == {"runner_id": "R1", "stub": "SYNTHETIC https core"}
    assert seen == [{"method": "POST", "path": "/core/v1/runner/register", "authorization": f"Bearer {RUNNER_TOKEN}"}]


def test_runner_settings_have_no_switch_that_disables_verification():
    from mbnas_runner.config import RunnerSettings

    fields = set(RunnerSettings.model_fields)
    assert "ca_file" in fields  # the only TLS knob: an extra CA bundle; verification itself is never off
    assert not {f for f in fields if f == "verify" or any(w in f for w in ("tls", "ssl", "insecure"))}, fields
