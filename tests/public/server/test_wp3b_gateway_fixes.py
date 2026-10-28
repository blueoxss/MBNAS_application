"""WP-3B fix round (DD-A41 (d), DD-A44 (d)(e), DD-A45): gateway rows that complement the WP-3C relay tests.

The gateway app is real (``mbnas_server.api.app.create_app``); the upstream is ``stub_core.StubCore`` through
``harness.StreamingASGITransport`` (no sockets, no core import). Rows: dot path segments outside ``/api`` never reach
the SPA fallback; control-character ids answer 401 without and 404 with credentials (no existence signal, no relay);
path parameters reach core as ONE percent-encoded segment; a non-ASCII ``Idempotency-Key`` is forwarded byte-faithfully;
non-finite JSON numbers are refused on every JSON route the gateway re-encodes; the legacy ``model_path`` translation
(read on the gateway host, sent as text + sha256 + name; body problems relayed unchanged; file rules answer
``MODEL_CONFIG_INVALID``); ``upload_id`` exclusivity; the upload extension rule (422); ``CORE_URL_INSECURE`` in the app
factory, the CLI and the doctor; ``ArtifactStore.sweep_tmp`` ageing of hard-linked temp files.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest_common import owner_harness  # DD-A43(b): owner-only harness; module skipped without it

from stub_core import CORE_TOKEN, PREFIX, StubCore

harness = owner_harness()
auth = harness.auth

TINY = b"# user model\nnc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [-1, 1, Detect, [nc]]\n"


@pytest.fixture
def stub() -> StubCore:
    return StubCore()


@pytest.fixture
def gw(tmp_roots, stub):
    """``gw(**settings_overrides)`` -> TestClient over a gateway relaying to ``stub``."""
    opened: list[TestClient] = []

    def build(**overrides: Any) -> TestClient:
        settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN, **overrides)
        app = harness.gateway_app_over(settings, stub.app, harness.StreamingASGITransport(stub.app))
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c

    yield build
    for c in opened:
        c.__exit__(None, None, None)


def _is_gateway_404(r: httpx.Response) -> bool:
    if r.status_code != 404 or not r.headers["content-type"].startswith("application/json"):
        return False
    body = r.json()
    return body["code"] == "NOT_FOUND" and body["location"] == "gateway" and body["request_id"] == r.headers["x-request-id"]


# ------------------------------------------------------------------------------------------------ DD-A44 (e)
@pytest.mark.parametrize("path", ["/assets/%2E%2E/index.html", "/monitor/%2e%2e", "/monitor/%2E/x", "/x/..%2Fy",
                                  "/api/v1/runs/%2E%2E/snapshot", "/api/v1/archive/runs/%2E%2E"])
def test_dot_segments_are_refused_before_routing_even_without_credentials(gw, stub, tmp_path, path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>MBNAS</title>")
    c = gw(web_dist=str(dist))
    for headers in ({}, auth()):
        r = c.get(path, headers=headers)
        assert _is_gateway_404(r), (path, r.status_code, r.text[:200])
        assert "MBNAS" not in r.text  # never the SPA fallback
    assert stub.calls == []


@pytest.mark.parametrize("path", ["/api/v1/runs/r1/artifacts/%00", "/api/v1/runs/%01/snapshot",
                                  "/api/v1/runs/r1/candidates/c%7F1", "/api/v1/registry/m%0A/structure"])
def test_control_character_ids_answer_401_without_and_404_with_credentials(gw, stub, path):
    c = gw()
    r = c.get(path)
    assert r.status_code == 401 and r.json()["code"] == "AUTH_REQUIRED"  # same as any other id: no existence signal
    r = c.get(path, headers=auth())
    assert _is_gateway_404(r), (r.status_code, r.text[:200])
    assert stub.calls == []


def _direct(tmp_roots, stub, method: str, path: str, **kw: Any) -> tuple[httpx.Response, Any]:
    """One request through ``httpx.ASGITransport`` (scope ``path`` decoded ONCE from the raw path, header bytes passed
    through, as uvicorn does; Starlette's TestClient decodes the path twice and re-encodes header values as UTF-8).
    Returns the answer and the gateway->core transport, whose ``requests`` hold the (method, decoded path) core saw."""
    settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN)
    transport = harness.StreamingASGITransport(stub.app)
    app = harness.gateway_app_over(settings, stub.app, transport)

    async def go() -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test") as c:
                return await c.request(method, path, **kw)
        finally:
            await app.state.core.aclose()

    return asyncio.run(go()), transport


@pytest.mark.parametrize("raw,decoded", [("a%25b", "a%b"), ("a%3Fb", "a?b"), ("caf%C3%A9", "café"), ("a%23b", "a#b"),
                                         ("%252E%252E", "%2E%2E"), ("a%2Fb", None)])
def test_path_parameters_reach_core_as_one_encoded_segment(tmp_roots, stub, raw, decoded):
    r, transport = _direct(tmp_roots, stub, "GET", f"/api/v1/runs/{raw}/snapshot", headers=auth())
    if decoded is None:  # an encoded slash splits the segment on the gateway already: no route, no relay
        assert r.status_code == 404 and transport.requests == []
        return
    assert r.status_code == 200, r.text
    assert transport.requests == [("GET", f"{PREFIX}/runs/{decoded}/snapshot")]


def test_non_ascii_idempotency_key_is_forwarded_byte_faithfully(tmp_roots, stub):
    r, _ = _direct(tmp_roots, stub, "POST", "/api/v1/runs", content=b"{}",
                   headers={**auth(), "Content-Type": "application/json", "Idempotency-Key": b"key-\xe9-1"})
    assert r.status_code == 200, r.text
    assert stub.last("/runs")["headers"]["idempotency-key"] == b"key-\xe9-1".decode("latin-1")


@pytest.mark.parametrize("route,raw", [
    ("/api/v1/structure-check", '{"model_path": "/x.yaml", "input_size": NaN}'),
    ("/api/v1/training-runs", '{"source": {"kind": "user", "model_path": "/x.yaml"}, "epochs": Infinity}'),
    ("/api/v1/protocols/validate", '{"protocol": {"console": {"search": {"budget_gpu_hours": 1e999}}}}'),
])
def test_non_finite_numbers_are_refused_without_echo(gw, stub, route, raw):
    r = gw().post(route, content=raw, headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED", r.text
    assert "NaN" not in r.text and "Infinity" not in r.text and "1e999" not in r.text
    assert stub.calls == []


# ------------------------------------------------------------------------------------------------ DD-A44 (d), DD-A45
def test_legacy_model_path_is_read_here_and_sent_as_text_sha_and_name(gw, stub, tmp_roots):
    path = tmp_roots["runs"] / "yolo11n.yaml"
    path.write_bytes(TINY)
    c = gw()
    r = c.post("/api/v1/structure-check", json={"model_path": str(path), "input_size": 64}, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/structure-check")["body"]
    assert sent == {"model_yaml_text": TINY.decode(), "source_sha256": "sha256:" + hashlib.sha256(TINY).hexdigest(),
                    "model_name": "yolo11n.yaml", "input_size": 64}
    # a caller's display name is kept unchanged (structure check and training source alike)
    c.post("/api/v1/structure-check", json={"model_path": str(path), "model_name": "shown.yml"}, headers=auth())
    assert stub.last("/structure-check")["body"]["model_name"] == "shown.yml"
    body = {"source": {"kind": "user", "model_path": str(path), "model_name": "shown.yaml"}, "mode": "train"}
    r = c.post("/api/v1/training-runs", json=body, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/training-runs")["body"]
    assert sent["mode"] == "train" and "model_path" not in sent["source"] and str(path) not in json.dumps(sent)
    assert sent["source"] == {"kind": "user", "model_name": "shown.yaml", "model_yaml_text": TINY.decode(),
                              "source_sha256": "sha256:" + hashlib.sha256(TINY).hexdigest()}


@pytest.mark.parametrize("body", [{}, {"model_path": ""}, {"model_path": "m.yaml"}, {"model_path": "~/m.yaml"},
                                  {"model_path": "/x.yaml", "bogus": 1}, {"model_path": 5},
                                  {"model_path": "/x.yaml", "model_yaml_text": "nc: 1\n", "source_sha256": "0" * 64}])
def test_legacy_body_problems_are_relayed_unchanged(gw, stub, body):
    r = gw().post("/api/v1/structure-check", json=body, headers=auth())
    assert r.status_code == 200, r.text  # the stub's answer: core decides (VALIDATION_FAILED / PATH_NOT_ABSOLUTE)
    assert stub.last("/structure-check")["body"] == body


def test_training_body_problems_are_relayed_unchanged(gw, stub):
    body = {"source": {"kind": "user", "model_path": "relative.yaml", "bogus": True}}
    r = gw().post("/api/v1/training-runs", json=body, headers=auth())
    assert r.status_code == 200 and stub.last("/training-runs")["body"] == body


@pytest.mark.parametrize("make", ["not_yaml", "too_big", "not_utf8", "symlink_to_txt"])
def test_legacy_model_path_file_rules_are_model_config_invalid(gw, stub, tmp_roots, make):
    runs = tmp_roots["runs"]
    path = {"not_yaml": runs / "m.txt", "too_big": runs / "big.yaml", "not_utf8": runs / "latin.yaml",
            "symlink_to_txt": runs / "link.yaml"}[make]
    if make == "not_yaml":
        path.write_bytes(TINY)
    elif make == "too_big":
        path.write_bytes(TINY + b"# " + b"x" * 2048)
    elif make == "not_utf8":
        path.write_bytes(b"nc: 1 # caf\xe9\n")
    else:
        (runs / "target.txt").write_bytes(TINY)
        path.symlink_to(runs / "target.txt")
    c = gw(upload_max_bytes=1024)
    for route, body in (("/api/v1/structure-check", {"model_path": str(path)}),
                        ("/api/v1/training-runs", {"source": {"kind": "user", "model_path": str(path)}})):
        r = c.post(route, json=body, headers=auth())
        assert r.status_code == 422 and r.json()["code"] == "MODEL_CONFIG_INVALID", (route, r.text)
        assert str(runs) not in r.text
    assert stub.calls == []


@pytest.mark.parametrize("where,code", [("outside", "PATH_OUTSIDE_ALLOWLIST"), ("missing", "PATH_NOT_FOUND"),
                                        ("traversal", "PATH_TRAVERSAL"), ("placeholder", "PATH_PLACEHOLDER")])
def test_legacy_model_path_check_codes_name_no_host_path(gw, stub, tmp_roots, tmp_path, where, code):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "m.yaml").write_bytes(TINY)
    path = {"outside": str(outside / "m.yaml"), "missing": str(tmp_roots["runs"] / "nope.yaml"),
            "traversal": str(tmp_roots["runs"]) + "/../m.yaml", "placeholder": "/ABSOLUTE/path/m.yaml"}[where]
    r = gw().post("/api/v1/structure-check", json={"model_path": path}, headers=auth())
    assert r.status_code == 422 and r.json()["code"] == code and r.json()["location"] == "gateway", r.text
    assert str(tmp_path) not in r.text and stub.calls == []


def test_upload_id_null_sources_and_exclusivity(gw, stub, tmp_roots):
    c = gw()
    up = c.post("/api/v1/uploads/model-config", content=TINY,
                headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": "m.yaml"}).json()
    r = c.post("/api/v1/structure-check", json={"upload_id": up["upload_id"], "model_path": None,
                                                "model_name": "mine.yaml"}, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/structure-check")["body"]
    assert set(sent) == {"model_yaml_text", "source_sha256", "model_name"} and sent["model_name"] == "mine.yaml"
    # DD-A45: TrainingSource.model_name passes through unchanged next to the carried text
    r = c.post("/api/v1/training-runs", json={"source": {"kind": "user", "upload_id": up["upload_id"],
                                                          "model_name": "My Model.yaml"}}, headers=auth())
    assert r.status_code == 200, r.text
    assert stub.last("/training-runs")["body"]["source"] == {
        "kind": "user", "model_name": "My Model.yaml", "model_yaml_text": TINY.decode(),
        "source_sha256": "sha256:" + hashlib.sha256(TINY).hexdigest()}
    path = tmp_roots["runs"] / "n.yaml"
    path.write_bytes(TINY)
    r = c.post("/api/v1/structure-check", json={"upload_id": None, "model_path": str(path)}, headers=auth())
    assert r.status_code == 200, r.text
    sent = stub.last("/structure-check")["body"]
    assert "upload_id" not in sent and "model_path" not in sent and sent["model_name"] == "n.yaml"
    before = len(stub.calls)
    for extra in ({"model_path": str(path)}, {"model_yaml_text": "nc: 1\n"}):
        r = c.post("/api/v1/structure-check", json={"upload_id": up["upload_id"], **extra}, headers=auth())
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED" and r.json()["location"] == "gateway"
        r = c.post("/api/v1/training-runs", json={"source": {"kind": "user", "upload_id": up["upload_id"], **extra}},
                   headers=auth())
        assert r.status_code == 422 and r.json()["errors"][0]["field"] == "source.upload_id", r.text
    assert len(stub.calls) == before


def test_upload_with_a_wrong_extension_is_422(gw):
    r = gw().post("/api/v1/uploads/model-config", content=TINY,
                  headers={**auth(), "Content-Type": "text/plain", "X-Filename": "m.txt"})
    assert r.status_code == 422 and r.json()["code"] == "UPLOAD_TYPE_UNSUPPORTED" and r.json()["location"] == "gateway"


# ------------------------------------------------------------------------------------------------ DD-A41 (d)
@pytest.mark.parametrize("url,insecure", [("http://core.example:8700", True), ("http://10.0.0.5:8700", True),
                                          ("http://127.0.0.1:8700", False), ("http://localhost:8700", False),
                                          ("http://[::1]:8700", False), ("https://core.example:8700", False)])
def test_core_url_insecure_rule(tmp_roots, url, insecure):
    from mbnas_server.api.app import create_app
    from mbnas_server.config import CoreUrlInsecure

    s = harness.make_gateway_settings(tmp_roots, core_url=url)
    assert (s.core_url_problem() is not None) is insecure
    if insecure:
        assert s.core_url_problem().startswith("CORE_URL_INSECURE")
        with pytest.raises(CoreUrlInsecure, match="CORE_URL_INSECURE"):
            create_app(s)
        # an explicit opt-in is accepted; an injected in-process transport has no wire
        create_app(harness.make_gateway_settings(tmp_roots, core_url=url, core_allow_insecure_http=True))
        create_app(s, core_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    else:
        create_app(s)


def test_core_url_insecure_cli_and_doctor(tmp_roots, monkeypatch, capsys):
    from mbnas_server import doctor
    from mbnas_server.api.app import main

    monkeypatch.setenv("MBNAS_HOME", str(tmp_roots["runs"] / "home"))
    monkeypatch.setenv("MBNAS_GW_API_TOKEN", "gw-token-0123456789")
    monkeypatch.setenv("MBNAS_GW_CORE_TOKEN", "core-secret-0123456789")
    monkeypatch.setenv("MBNAS_GW_CORE_URL", "http://user:pw@core.example:8700")
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "CORE_URL_INSECURE" in err and "pw" not in err
    s = harness.make_gateway_settings(tmp_roots, core_url="http://user:pw@core.example:8700",
                                      core_token="core-secret-0123456789")
    checks = {c["name"]: c for c in doctor.run_checks(s)}
    assert checks["core_url"]["ok"] is False and "CORE_URL_INSECURE" in checks["core_url"]["detail"]
    assert checks["core"]["ok"] is False and "not contacted" in checks["core"]["detail"]  # token never sent in clear
    assert "core-secret" not in json.dumps(checks) and "user:pw" not in json.dumps(checks)


# ------------------------------------------------------------------------------------------------ store sweep
def test_sweep_keeps_a_fresh_hard_link_of_an_old_file(tmp_path):
    from mbnas_server.artifacts.store import TMP_MAX_AGE_S, TMP_PREFIX, ArtifactStore

    store = ArtifactStore(str(tmp_path / "store"))
    source = tmp_path / "run" / "full_e001.pt"
    source.parent.mkdir()
    source.write_bytes(b"checkpoint bytes")
    old = time.time() - 4 * TMP_MAX_AGE_S
    os.utime(source, (old, old))  # a run-directory file written hours ago
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    shard = store.root / sha[:2]
    shard.mkdir(parents=True)
    tmp = shard / f"{TMP_PREFIX}{sha}.123.abc.part"
    os.link(source, tmp)  # what the runner's local transfer stages: same inode, the source's old mtime
    assert tmp.stat().st_mtime < time.time() - TMP_MAX_AGE_S and tmp.stat().st_nlink == 2
    assert store.sweep_tmp() == [] and tmp.exists()  # just linked: not a crash leftover
    assert store.sweep_tmp(now=time.time() + TMP_MAX_AGE_S + 60) == [f"{sha[:2]}/{tmp.name}"]
    assert source.read_bytes() == b"checkpoint bytes"  # only the temp link was removed
