"""``mbnas-gateway-doctor``: pre-flight checks of a gateway installation.

Checks (each ``{name, ok, detail}``; secrets are never printed):

* ``config``         ``GatewaySettings.load()`` succeeds and ``api_token`` is set;
* ``core_token``     the service token for core is set (warning only: an owner core may run without principals);
* ``core_url``       not plain ``http://`` to a non-loopback host unless ``core_allow_insecure_http`` is set
                     (``CORE_URL_INSECURE``; the ``core`` check is then skipped so the token is never sent in clear);
* ``core_tls``       the core TLS certificate is verified (``core_tls_verify``; warning only when it is off:
                     ``CORE_TLS_VERIFY_DISABLED``);
* ``core``           ``GET <core_url><core_prefix>/health`` answers 200 with the service token (TLS per settings);
* ``store``          ``artifact_store`` (and ``uploads/``) is writable; ``log_dir`` writable (audit log);
* ``web_dist``       ``web_dist/index.html`` exists (warning only: the API works without the web console).

Prints one JSON document; exit code 0 when every non-warning check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def store_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".doctor-")
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


def _loopback(url: str) -> bool:
    from mbnas_server.config import is_loopback_host

    return is_loopback_host(urlsplit(url).hostname)


def run_checks(settings: Any) -> list[dict[str, Any]]:
    import httpx

    from mbnas_server.api.core_client import _verify
    from mbnas_server.api.static_web import dist_present

    checks: list[dict[str, Any]] = [
        {"name": "config", "ok": settings.api_token is not None, "warning": False,
         "detail": "api_token set" if settings.api_token is not None else "MBNAS_GW_API_TOKEN is not set"},
        {"name": "core_token", "ok": settings.core_token is not None, "warning": True,
         "detail": "core_token set" if settings.core_token is not None else "MBNAS_GW_CORE_TOKEN is not set"},
    ]
    problem = settings.core_url_problem()
    if problem is not None:
        url_detail = problem
    elif settings.core_url.startswith("https://"):
        url_detail = "https"
    elif _loopback(settings.core_url):
        url_detail = "loopback http"
    else:
        url_detail = "plain http to a non-loopback host, allowed explicitly (core_allow_insecure_http)"
    checks.append({"name": "core_url", "ok": problem is None, "warning": False, "detail": url_detail})
    tls_problem = settings.core_tls_problem()
    if tls_problem is not None:
        tls_detail = tls_problem
    elif not settings.core_url.startswith("https://"):
        tls_detail = "plain http core_url: no TLS on this connection (see core_url)"
    else:
        tls_detail = ("certificate verified against core_ca_file" if settings.core_ca_file
                      else "certificate verified against the default trust store")
    checks.append({"name": "core_tls", "ok": tls_problem is None, "warning": True, "detail": tls_detail})
    url = settings.core_url + settings.core_prefix + "/health"
    parts = urlsplit(url)
    shown = urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))  # never print URL userinfo
    headers = {"Authorization": f"Bearer {settings.core_token.get_secret_value()}"} if settings.core_token else {}
    if problem is not None:  # never send the service token in clear text to a remote host
        ok, detail = False, f"{shown} not contacted: CORE_URL_INSECURE"
    else:
        try:
            r = httpx.get(url, headers=headers, timeout=settings.core_connect_timeout_s + 5, verify=_verify(settings))
            ok = r.status_code == 200
            detail = f"{shown} -> HTTP {r.status_code}"
            if ok:
                detail += f" status={r.json().get('status')}"
            else:
                try:
                    detail += f" code={r.json().get('code')}"
                except ValueError:
                    pass
        except httpx.HTTPError as exc:
            ok, detail = False, f"{shown} unreachable: {type(exc).__name__}"
    checks.append({"name": "core", "ok": ok, "warning": False, "detail": detail})
    store_ok = store_writable(settings.artifact_store) and store_writable(os.path.join(settings.artifact_store, "uploads"))
    checks.append({"name": "store", "ok": store_ok, "warning": False, "detail": settings.artifact_store})
    checks.append({"name": "log_dir", "ok": store_writable(settings.log_dir), "warning": False,
                   "detail": settings.log_dir})
    checks.append({"name": "web_dist", "ok": dist_present(settings.web_dist), "warning": True,
                   "detail": settings.web_dist})
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MBNAS gateway doctor (config, core reachability, store, web dist)")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args(argv)
    from mbnas_server.config import GatewaySettings

    try:
        settings = GatewaySettings.load(args.env_file)
    except Exception as exc:  # noqa: BLE001 - report, do not trace
        print(json.dumps({"ok": False, "checks": [{"name": "config", "ok": False, "detail": f"{type(exc).__name__}: "
                                                    f"{str(exc)[:500]}"}]}, indent=2))
        return 1
    checks = run_checks(settings)
    ok = all(c["ok"] or c["warning"] for c in checks)
    print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
