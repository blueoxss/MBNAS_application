"""WP-1F gateway skeleton: GatewaySettings, bearer/CORS/request-id/body-limit/log-redaction, minimal health, archive mount, campaign wiring.

New public test (WP-1G adopted the WP-1F draft, WP-1_RESOLUTIONS G1); needs only mbnas_contracts + mbnas_server (no
core/runner/model_runtime, no host data, no GPU). Synthetic archive run: tests/public/server/synth.py.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mbnas_contracts.util.home import mbnas_home
from mbnas_server.api.app import create_app
from mbnas_server.api.contract_check import contract_errors
from mbnas_server.config import GatewaySettings

TOKEN = "gw-test-token-0123456789"
ORIGIN = "http://localhost:5173"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SYNTH_PY = Path(__file__).resolve().parent / "synth.py"


@pytest.fixture
def synth():
    spec = importlib.util.spec_from_file_location("gw_archive_synth", SYNTH_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gw_archive_synth"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "yolo_ga_full_empty"
    r.mkdir()
    return r


NO_WEB_DIST = str(Path(__file__).resolve().parent / "no-such-web-dist")  # never created


@pytest.fixture(autouse=True)
def _gateway_home_in_tmp(clean_env, monkeypatch, tmp_path):
    """Settings built in this module without explicit paths keep every ``$MBNAS_HOME`` default (artifact store, logs,
    audit log, manifest / gateway-input cache) in the test's tmp dir: clean_env removed MBNAS_HOME, and the fallback
    ``<cwd>/runtime`` is the repository's runtime/ when the suite runs from the project root."""
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))


def make_client(**kw) -> TestClient:
    # WP-3C: the default web_dist is <repo>/mbnas_web/dist (DD-A30), which exists once the web bundle is built on the
    # host; these unit rows pin "no bundle" unless a test passes web_dist, so they do not depend on host state.
    kw.setdefault("web_dist", NO_WEB_DIST)
    return TestClient(create_app(GatewaySettings(api_token=TOKEN, **kw)))


# ------------------------------------------------------------------------------------------------ settings
def test_defaults_live_under_mbnas_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    s = GatewaySettings()
    home = mbnas_home()
    assert home == tmp_path / "home"
    assert s.artifact_store == str(home / "gateway" / "artifacts") and s.log_dir == str(home / "gateway" / "logs")
    assert (s.host, s.port, s.core_url, s.core_token) == ("127.0.0.1", 8710, "http://127.0.0.1:8700", None)
    assert (s.api_token, s.artifact_signing_key) == (None, None)
    assert (s.signed_url_ttl_s, s.max_request_body_bytes, s.archive_cache_ttl_s) == (600, 1 << 20, 5.0)
    assert (s.cors_origins, s.archive_roots, s.archive_driver_dirs, s.archive_campaign_roots) == ([], [], [], [])


def test_without_mbnas_home_defaults_are_under_cwd_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("MBNAS_HOME", raising=False)  # this row is about the unset case (the module fixture sets it)
    monkeypatch.chdir(tmp_path)
    assert GatewaySettings().artifact_store == str(tmp_path / "runtime" / "gateway" / "artifacts")


def test_env_prefix_lists_and_secrets(monkeypatch):
    monkeypatch.setenv("MBNAS_GW_PORT", "9123")
    monkeypatch.setenv("MBNAS_GW_API_TOKEN", "tok")
    monkeypatch.setenv("MBNAS_GW_CORS_ORIGINS", "http://a.example:1, https://b.example/")
    monkeypatch.setenv("MBNAS_GW_ARCHIVE_ROOTS", '["/x/a", "/x/b"]')
    monkeypatch.setenv("MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS", "/c/1, /c/2")
    monkeypatch.setenv("MBNAS_GW_CORE_URL", "https://core.example:8700/")
    monkeypatch.setenv("MBNAS_GW_CORE_TOKEN", "core-secret")
    monkeypatch.setenv("MBNAS_PORT", "1111")          # foreign / legacy prefixes are ignored
    monkeypatch.setenv("MBNAS_CORE_PORT", "2222")
    s = GatewaySettings()
    assert s.port == 9123 and s.require_api_token() == "tok"
    assert s.cors_origins == ["http://a.example:1", "https://b.example"]
    assert s.archive_roots == ["/x/a", "/x/b"] and s.archive_campaign_roots == ["/c/1", "/c/2"]
    assert s.core_url == "https://core.example:8700" and "core-secret" not in repr(s)


@pytest.mark.parametrize("kw", [
    {"cors_origins": ["*"]}, {"cors_origins": ["http://x.example/p"]}, {"cors_origins": ["x.example"]},
    {"archive_roots": ["rel"]}, {"archive_campaign_roots": ["rel"]}, {"archive_driver_dirs": ["rel"]},
    {"api_token": ""}, {"core_token": ""}, {"artifact_signing_key": ""}, {"port": 0}, {"port": 70000},
    {"core_url": "ftp://x:1"}, {"artifact_store": "rel"}, {"log_dir": "rel"}, {"max_request_body_bytes": 0}])
def test_invalid_settings_are_rejected(kw):
    with pytest.raises(ValueError):
        GatewaySettings(**kw)


def test_empty_secret_in_env_is_rejected(monkeypatch):
    monkeypatch.setenv("MBNAS_GW_API_TOKEN", "")
    with pytest.raises(ValueError):
        GatewaySettings()


def test_load_dotenv(monkeypatch, tmp_path):
    f = tmp_path / "gw.env"
    f.write_text("MBNAS_GW_API_TOKEN=from-dotenv\nMBNAS_GW_PORT=9555\nMBNAS_CORE_DB_PATH=/ignored\n", encoding="utf-8")
    assert GatewaySettings.load(f).port == 9555
    monkeypatch.setenv("MBNAS_GW_ENV_FILE", str(f))
    assert GatewaySettings.load().require_api_token() == "from-dotenv"
    monkeypatch.setenv("MBNAS_GW_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(FileNotFoundError):
        GatewaySettings.load()
    monkeypatch.delenv("MBNAS_GW_ENV_FILE")
    monkeypatch.setenv("MBNAS_ENV_FILE", str(tmp_path / "missing.env"))  # the monolith's variable no longer exists
    assert GatewaySettings.load().port == 8710


def test_require_secrets():
    with pytest.raises(RuntimeError, match="MBNAS_GW_API_TOKEN"):
        GatewaySettings().require_api_token()
    with pytest.raises(RuntimeError, match="MBNAS_GW_ARTIFACT_SIGNING_KEY"):
        GatewaySettings().require_signing_key()
    assert GatewaySettings(artifact_signing_key="k" * 32).require_signing_key() == b"k" * 32


# ------------------------------------------------------------------------------------------------ app
def test_health_with_and_without_bearer(root):
    # WP-3 contract 2.2: health merges gateway status + core summary; with no core listening it is 200 "degraded"
    # (was the WP-1 minimal {"status":"ok","gateway":true}, DD-A29). A closed port keeps this independent of port 8700.
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    c = make_client(archive_roots=[root], core_url=f"http://127.0.0.1:{closed}")
    r = c.get("/api/v1/health", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded" and body["core"] == "unreachable", body
    # the gateway reports the version of the packaged contract it serves (1.5.0 since the WP-4 promotion, DD-A42)
    from mbnas_contracts.openapi import contract

    assert body["gateway"]["status"] == "ok" and body["gateway"]["version"] == contract()["info"]["version"] == "1.5.0", \
        body["gateway"]
    assert body["request_id"] == r.headers["x-request-id"]
    assert body["allowlists"]["archive_roots"] == [str(root)]
    r = c.get("/api/v1/health")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json()["code"] == "AUTH_REQUIRED" and r.json()["request_id"] == r.headers["x-request-id"]
    assert contract_errors("Error", r.json()) == []
    r = c.get("/api/v1/health", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED"
    for header in ("Basic dXNlcjpwYXNz", "Bearer", "Bearer   ", "token abc"):
        assert c.get("/api/v1/health", headers={"Authorization": header}).status_code == 401


def test_archive_runs_on_an_existing_empty_root(root):
    c = make_client(archive_roots=[root])
    assert c.get("/api/v1/archive/runs").status_code == 401
    r = c.get("/api/v1/archive/runs", headers=AUTH)
    assert r.status_code == 200 and r.json()["runs"] == [] and r.json()["roots"][0]["exists"] is True
    assert contract_errors("ArchiveRunList", r.json()) == []


def test_archive_without_roots_answers_503(root):
    r = make_client().get("/api/v1/archive/runs", headers=AUTH)
    assert r.status_code == 503 and r.json()["code"] == "ARCHIVE_ROOT_UNAVAILABLE" and contract_errors("Error", r.json()) == []


def test_archive_endpoints_pass_the_contract(synth, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)  # no NVML query on this host
    r0 = tmp_path / "yolo_ga_full_synth"
    r0.mkdir()
    synth.make_run(r0, "synthetic-day")
    c = make_client(archive_roots=[r0])
    base = "/api/v1/archive/runs/synthetic-day"
    cases = [("/api/v1/archive/runs", "ArchiveRunList"), (base, "ArchiveRun"), (f"{base}/baselines", "ArchiveBaselineList"),
             (f"{base}/generations", "ArchiveGenerationList"), (f"{base}/generations/1", "ArchiveGenerationDetail"),
             (f"{base}/candidates", "ArchiveCandidateList"), (f"{base}/candidates/g001_c0005", "ArchiveCandidateDetail"),
             (f"{base}/pareto", "ArchivePareto"), (f"{base}/fidelity", "ArchiveFidelity"), (f"{base}/confirm", "ArchiveConfirm"),
             ("/api/v1/archive/compare?runs=synthetic-day", "ArchiveCompare"), ("/api/v1/archive/live", "ArchiveLive")]
    for path, comp in cases:
        r = c.get(path, headers=AUTH)
        assert r.status_code == 200, (path, r.text[:200])
        assert contract_errors(comp, r.json()) == [], (path, contract_errors(comp, r.json())[:3])
    assert c.get("/api/v1/archive/runs/nope", headers=AUTH).json()["code"] == "ARCHIVE_RUN_NOT_FOUND"


def test_campaign_roots_reach_the_store(tmp_path):
    app = create_app(GatewaySettings(api_token=TOKEN, archive_roots=[tmp_path], archive_driver_dirs=[tmp_path / "d"],
                                     archive_campaign_roots=[tmp_path / "c"], archive_cache_ttl_s=1.5))
    st = app.state.archive
    assert (st.root_paths, st.driver_dirs, st.campaign_roots, st.ttl_s) == ([str(tmp_path)], [str(tmp_path / "d")], [str(tmp_path / "c")], 1.5)


def test_docs_disabled_and_unknown_routes_are_contract_errors(tmp_path):
    c = make_client()
    for path in ("/docs", "/redoc", "/openapi.json", "/api/v1/nope"):
        r = c.get(path, headers=AUTH)
        assert r.status_code == 404 and contract_errors("Error", r.json()) == [], path
    # WP-3C: with a web bundle served (DD-A30) the SPA fallback may answer /docs etc. with index.html, but never with
    # the API documentation or the OpenAPI schema; unknown /api paths stay JSON 404 contract errors.
    dist = tmp_path / "dist"
    dist.mkdir()
    index = "<!doctype html><title>MBNAS</title><div id=root></div>"
    (dist / "index.html").write_text(index)
    c = make_client(web_dist=str(dist))
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(path, headers=AUTH)
        assert r.status_code in (200, 404), (path, r.status_code)
        if r.status_code == 200:
            assert r.text == index and r.headers["content-type"].startswith("text/html"), path
        else:
            assert contract_errors("Error", r.json()) == [], path
        assert "swagger" not in r.text.lower() and '"openapi"' not in r.text, path
    r = c.get("/api/v1/nope", headers=AUTH)
    assert r.status_code == 404 and contract_errors("Error", r.json()) == []


def test_request_id_echo_and_replacement():
    c = make_client()
    r = c.get("/api/v1/health", headers={"X-Request-ID": "trace-123.abc", **AUTH})
    assert r.headers["x-request-id"] == "trace-123.abc" and r.json()["request_id"] == "trace-123.abc"
    r = c.get("/api/v1/health", headers={"X-Request-ID": "bad id with spaces", **AUTH})
    assert r.headers["x-request-id"] != "bad id with spaces" and len(r.headers["x-request-id"]) == 32


def test_cors_exact_origin_only():
    c = make_client(cors_origins=[ORIGIN])
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = c.options("/api/v1/health", headers={"Origin": ORIGIN, **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in ok.headers
    bad = c.options("/api/v1/health", headers={"Origin": "http://evil.example", **pre})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    denied = c.get("/api/v1/health", headers={"Origin": ORIGIN})  # a 401 still carries CORS so the browser can read the Error body
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == ORIGIN


def test_body_limit_is_enforced_before_routing():
    c = make_client(max_request_body_bytes=1024)
    r = c.post("/api/v1/health", content=b"x" * 2048, headers=AUTH)
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE" and r.json()["request_id"] == r.headers["x-request-id"]
    assert r.json()["location"] == "gateway"  # C05 D3: the gateway refused it (Error.location)
    assert c.post("/api/v1/health", content=b"x" * 10, headers=AUTH).status_code == 405


def test_create_app_refuses_to_start_without_a_token():
    with pytest.raises(RuntimeError, match="MBNAS_GW_API_TOKEN"):
        create_app(GatewaySettings())


# ------------------------------------------------------------------------------------------------ boundary and entry points
def test_importing_the_gateway_loads_no_core_runner_model_runtime_or_ml_module():
    code = ("import sys; import mbnas_server.api.app, mbnas_server.archive, mbnas_server.api.contract_check; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('mbnas_core', 'mbnas_runner', 'mbnas_model_runtime', 'torch', 'ultralytics')); "
            "print(bad); sys.exit(1 if bad else 0)")
    p = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_main_help_exits_zero(capsys):
    from mbnas_server.api import app as appmod

    with pytest.raises(SystemExit) as e:
        appmod.main(["--help"])
    assert e.value.code == 0 and "--env-file" in capsys.readouterr().out


def test_uvicorn_access_log_never_contains_query_or_header_secret(root):
    import httpx
    import uvicorn

    secret = "querysecret-DO-NOT-LOG-123"
    seen: list[str] = []

    class Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    cap = Cap(logging.DEBUG)
    loggers = [logging.getLogger(n) for n in ("uvicorn.access", "uvicorn.error")]
    for lg in loggers:
        lg.addHandler(cap)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(GatewaySettings(api_token=TOKEN, archive_roots=[root])), host="127.0.0.1", port=port,
                                           log_level="info", access_log=True, log_config=None, lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        deadline = time.time() + 20
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        base = f"http://127.0.0.1:{port}/api/v1"
        with httpx.Client(timeout=10) as hc:
            codes = [hc.get(f"{base}/health?token={secret}").status_code, hc.get(f"{base}/archive/runs?access_token={secret}").status_code,
                     hc.get(f"{base}/archive/runs/x/files/plot_hypervolume?exp=1799999999&sig={secret}").status_code,
                     hc.get(f"{base}/archive/runs", headers={"Authorization": f"Bearer {secret}"}).status_code]
        time.sleep(0.3)
    finally:
        server.should_exit = True
        t.join(timeout=10)
        for lg in loggers:
            lg.removeHandler(cap)
    joined = "\n".join(seen)
    assert codes == [401, 401, 401, 403]
    assert "token=***" in joined and "access_token=***" in joined and "sig=***" in joined and secret not in joined
