"""QA against the LIVE dev stack (real uvicorn API + real DB under the stack's MBNAS_HOME). Opt-in (marker ``live``).

    python scripts/dev/dev_stack.py --env-file .env --gpu-lock .../gpu.lock      # start the stack
    MBNAS_QA_LIVE=1 runtime/venv/bin/python -m pytest tests/owner/live/test_live_stack.py -q

Read-only except for ``/protocols/validate`` (which stores a validation row) and rejected requests: no run is created and no
GPU job is queued. The token is read from the git-ignored ``.env`` and is never printed or asserted into a message.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

# WP-1 (DD-A22): the live console API of WP-1 is the core app on 8700 (the gateway relay comes with WP-3); the env file
# of the running stack is $MBNAS_QA_ENV_FILE (default <checkout>/.env) and holds the DD-A13 MBNAS_CORE_* names
ENV = Path(os.environ.get("MBNAS_QA_ENV_FILE") or Path(__file__).resolve().parents[3] / ".env")
BASE = os.environ.get("MBNAS_QA_BASE", "http://127.0.0.1:8700")
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("MBNAS_QA_LIVE") != "1", reason="set MBNAS_QA_LIVE=1 with the dev stack running")]


def _env() -> dict[str, str]:
    out = {}
    for line in ENV.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and value.strip():
            out[name.strip()] = value.strip().strip("'\"")
    return out


@pytest.fixture(scope="module")
def http():
    env = _env()
    with httpx.Client(base_url=BASE, timeout=20, headers={"Authorization": f"Bearer {env['MBNAS_CORE_API_TOKEN']}"}) as c:
        c.secrets = [v for k, v in env.items() if k in ("MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY")]
        c.origin = (env.get("MBNAS_CORE_CORS_ORIGINS") or "").split(",")[0].strip()
        yield c


def test_health_has_workers_registry_and_source_hash(http):
    r = http.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded") and body["worker_count"] >= 1
    assert len(body["registry"]) >= 14 and {e["status"] for e in body["registry"]} <= {"AVAILABLE", "BLOCKED_SOURCE", "UNSUPPORTED", "UNVERIFIED"}
    assert not any(s in r.text for s in http.secrets)


def test_auth_matrix(http):
    bare = httpx.Client(base_url=BASE, timeout=10)
    assert bare.get("/api/v1/health").status_code == 401
    assert bare.get("/api/v1/health", headers={"Authorization": "Bearer wrong-token"}).status_code == 403
    for suffix in ("?token=x", "?access_token=x"):
        assert bare.get("/api/v1/runs" + suffix).status_code == 401


def test_oversized_bodies_are_413_on_the_real_server(http):
    big = b'{"protocol": {"pad": "' + b"x" * (2 << 20) + b'"}}'
    r = http.post("/api/v1/protocols/validate", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"
    chunks = (b"y" * 65536 for _ in range(40))  # chunked, no Content-Length
    r = http.post("/api/v1/runs", content=chunks, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert http.get("/api/v1/health").status_code == 200  # the server is still fine


def test_foreign_origin_gets_no_cors_headers(http):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    bad = http.options("/api/v1/runs", headers={"Origin": "http://evil.example", **pre})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-origin" not in http.get("/api/v1/health", headers={"Origin": "http://evil.example"}).headers
    if http.origin:
        ok = http.options("/api/v1/runs", headers={"Origin": http.origin, **pre})
        assert ok.headers.get("access-control-allow-origin") == http.origin


def test_absurd_last_event_id_on_the_real_sse_route(http):
    runs = http.get("/api/v1/runs?limit=1").json()["runs"]
    if not runs:
        pytest.skip("no runs in the live DB")
    rid = runs[0]["id"]
    for value in ("²", "9" * 5000, "abc"):
        with http.stream("GET", f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": value.encode("latin-1") if len(value) < 3 else value}) as resp:
            assert resp.status_code == 200
            for i, line in enumerate(resp.iter_lines()):
                if line.startswith("event:") or i > 40:
                    break


def test_artifact_guessing_against_real_runs_leaks_nothing(http):
    runs = http.get("/api/v1/runs?limit=3").json()["runs"]
    if not runs:
        pytest.skip("no runs in the live DB")
    for run in runs:
        for guess in ("art-1", "art-0001", "1", "../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "a" * 300):
            r = http.get(f"/api/v1/runs/{run['id']}/artifacts/{guess}")
            assert r.status_code == 404, (guess, r.status_code)
            assert "root:" not in r.text and "/root/detection" not in r.text
    snap = http.get(f"/api/v1/runs/{runs[0]['id']}/snapshot").json()
    arts = [a for c in snap["candidates"] for a in c.get("artifacts", [])]
    if arts:
        a = arts[0]
        bare = httpx.Client(base_url=BASE, timeout=10)
        assert bare.get(f"/api/v1/runs/{runs[0]['id']}/artifacts/{a['id']}").status_code == 401
        assert bare.get(f"/api/v1/runs/{runs[0]['id']}/artifacts/{a['id']}", params={"exp": 4102444800, "sig": "0" * 64}).status_code == 403
        assert http.get(f"/api/v1/runs/{runs[0]['id']}/artifacts/{a['id']}").status_code == 200


def test_real_pilot_preset_validates_and_unsafe_variants_do_not(http):
    presets = http.get("/api/v1/protocols/presets").json()["presets"]
    pilot = next(p for p in presets if p["id"] == "PILOT_SMOKE")["protocol"]
    ok = http.post("/api/v1/protocols/validate", json={"protocol": pilot}).json()
    assert ok["valid"] is True, ok["errors"]
    assert ok["resolved"]["dataset"]["counts"]["train"]["images"] == 32
    for key, value, code in (("train_root", "/root/detection/MBNAS_data/../../etc", "PATH_TRAVERSAL"), ("train_root", "/etc", "PATH_OUTSIDE_ALLOWLIST"),
                             ("dataset_yaml", "/ABSOLUTE/path/data.yaml", "PATH_PLACEHOLDER"), ("search_val_root", "relative/val", "PATH_NOT_ABSOLUTE"),
                             ("final_eval_root", pilot["console"]["paths"]["search_val_root"], "SPLIT_SAME_ROOT")):
        bad = json.loads(json.dumps(pilot))
        bad["console"]["paths"][key] = value
        r = http.post("/api/v1/protocols/validate", json={"protocol": bad}).json()
        assert r["valid"] is False and r.get("resolved") is None, (key, value)
        assert code in {e["code"] for e in r["errors"]} or key == "final_eval_root", (key, [e["code"] for e in r["errors"]])
