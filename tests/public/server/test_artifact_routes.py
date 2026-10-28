"""WP-5B gateway artifact wiring against a STUB core (DD-A38, DD-A39, DD-A46, DD-A47 (d); W4 wiring notes).

The gateway app is real; the upstream is ``stub_core.StubCore`` (programmed records / manifests, every call recorded)
reached in-process. The artifact store is a real :class:`mbnas_server.artifacts.store.ArtifactStore` in ``tmp_path``
filled the way the runner's ``local`` transfer fills it (DD-A39 layout). These rows pin what the gateway does with a
given core answer; the real core is exercised by ``tests/owner/artifacts``. SYNTHETIC bytes only.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest_common import owner_harness  # DD-A43(b): owner-only harness; module skipped without it

from stub_core import CORE_TOKEN, PREFIX, StubCore

harness = owner_harness()
auth = harness.auth

from mbnas_server.api.contract_check import contract_errors  # noqa: E402
from mbnas_server.artifacts.signing import sign  # noqa: E402
from mbnas_server.artifacts.store import ArtifactStore  # noqa: E402

GW_KEY = harness.SIGNING_KEY
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
YAML = b"# executed architecture\nnc: 1\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\n"
CKPT = b"checkpoint-bytes-" * 512


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def stub() -> StubCore:
    return StubCore()


@pytest.fixture
def gw(tmp_roots, tmp_path, stub):
    """``gw(**overrides)`` -> (TestClient, settings) over a gateway relaying to ``stub``; inputs cache in tmp."""
    opened: list[TestClient] = []

    def build(**overrides: Any):
        overrides.setdefault("manifest_cache_root", str(tmp_path / "gw-cache"))
        settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN, **overrides)
        app = harness.gateway_app_over(settings, stub.app, harness.StreamingASGITransport(stub.app))
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c

    yield build
    for c in opened:
        c.__exit__(None, None, None)


def store_of(c: TestClient) -> ArtifactStore:
    return c.app.state.artifact_store


def put(c: TestClient, data: bytes, **meta: Any) -> str:
    h = sha(data)
    store_of(c).put_stream(h, io.BytesIO(data), {"run_id": "r1", "artifact_id": meta.pop("artifact_id", "art-1"),
                                                  "kind": meta.pop("kind", "tp"), "source": "local", **meta})
    return h


def record(id_: str = "art-1", data: bytes = PNG, **kw: Any) -> dict[str, Any]:
    rec = {"object": "artifact", "id": id_, "run_id": "r1", "kind": "tp", "candidate_id": "c1",
           "evaluation_id": "e1", "sha256": sha(data), "bytes": len(data), "mime_type": "image/png",
           "sample_id": "s1", "split_hash": None, "metadata": {"filename": "x.png"}, "matching": None,
           "origin": "runner", "registry_state": "READY", "server_store": None, "state": "NOT_READY"}
    rec.update(kw)
    return rec


def on_record(stub: StubCore, rec: dict[str, Any]) -> None:
    stub.on("GET", f"{PREFIX}/runs/{rec['run_id']}/artifacts/{rec['id']}/record", body=rec)


def location_calls(stub: StubCore) -> list[dict[str, Any]]:
    return [c for c in stub.calls if c["method"] == "POST" and c["path"].endswith("/locations")]


def byte_calls(stub: StubCore) -> list[dict[str, Any]]:
    return [c for c in stub.calls if c["method"] == "GET" and c["path"].startswith(f"{PREFIX}/runs/")
            and "/artifacts/" in c["path"] and not c["path"].endswith("/record")]


# ------------------------------------------------------------------------------------------------ settings / startup
def test_settings_defaults_and_store_on_app_state(gw, tmp_roots, monkeypatch):
    from mbnas_server.config import GatewaySettings

    s = GatewaySettings()
    assert s.artifact_max_bytes == 4 << 30 and s.artifact_verify_first_max_bytes == 64 << 20
    monkeypatch.setenv("MBNAS_GW_ARTIFACT_MAX_BYTES", "1048576")
    monkeypatch.setenv("MBNAS_GW_ARTIFACT_VERIFY_FIRST_MAX_BYTES", "4096")
    s = GatewaySettings()
    assert s.artifact_max_bytes == 1 << 20 and s.artifact_verify_first_max_bytes == 4096
    c = gw(artifact_max_bytes=12345)
    store = store_of(c)
    assert isinstance(store, ArtifactStore) and store.max_bytes == 12345
    assert str(store.root) == str(tmp_roots["runs"] / "gateway" / "artifacts")


def test_startup_sweeps_stale_temp_files_only(tmp_roots, tmp_path, stub):
    from mbnas_server.artifacts.store import TMP_MAX_AGE_S, TMP_PREFIX

    settings = harness.gateway_settings_for_core(tmp_roots, CORE_TOKEN, manifest_cache_root=str(tmp_path / "c"))
    shard = Path(settings.artifact_store) / "ab"
    shard.mkdir(parents=True)
    old, fresh = shard / f"{TMP_PREFIX}{'ab' * 32}.1.x.part", shard / f"{TMP_PREFIX}{'ab' * 32}.2.y.part"
    old.write_bytes(b"crashed writer")
    fresh.write_bytes(b"live writer")
    then = time.time() - 2 * TMP_MAX_AGE_S
    import os

    os.utime(old, (then, then))
    app = harness.gateway_app_over(settings, stub.app, harness.StreamingASGITransport(stub.app))
    with TestClient(app):
        assert not old.exists() and fresh.exists()


def test_artifact_verification_header_is_exposed_to_the_browser(gw):
    r = gw().get("/api/v1/runs/r1/artifacts/a1", headers={"Origin": harness.ORIGIN})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == harness.ORIGIN
    exposed = {h.strip().lower() for h in r.headers["access-control-expose-headers"].split(",")}
    assert {"x-artifact-sha256", "x-artifact-verification", "x-bundle-sha256", "content-disposition"} <= exposed


# ------------------------------------------------------------------------------------------------ download
def test_download_from_store_with_digest_headers_and_one_location_report(gw, stub):
    c = gw()
    put(c, PNG)
    on_record(stub, record())
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth())
    assert r.status_code == 200, r.text
    assert r.content == PNG and r.headers["x-artifact-sha256"] == sha(PNG) == sha(r.content)
    assert r.headers["content-type"] == "image/png" and r.headers["content-length"] == str(len(PNG))
    assert r.headers["x-artifact-verification"] == "pre-send"
    assert r.headers["cache-control"] == "private, no-store" and "sandbox" in r.headers["content-security-policy"]
    assert byte_calls(stub) == []  # bytes never come from core for a runner object
    (rep,) = location_calls(stub)
    assert rep["path"] == f"{PREFIX}/artifacts/art-1/locations"
    assert rep["body"] == {"run_id": "r1", "sha256": sha(PNG), "bytes": len(PNG), "state": "PRESENT",
                           "store": "server_store", "verified_by": "store"}
    assert rep["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"
    # once the core records PRESENT@server_store the gateway does not report again
    on_record(stub, record(server_store="PRESENT", state="READY"))
    assert c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth()).status_code == 200
    assert len(location_calls(stub)) == 1


def test_prefixed_digest_in_the_record_is_served(gw, stub):
    c = gw()
    put(c, PNG)
    on_record(stub, record(sha256="sha256:" + sha(PNG)))
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth())
    assert r.status_code == 200 and r.content == PNG and r.headers["x-artifact-sha256"] == sha(PNG)


def test_tampered_store_bytes_are_409_load_failed_and_reported(gw, stub):
    c = gw()
    h = put(c, PNG)
    path = store_of(c).path(h)
    data = bytearray(path.read_bytes())
    data[10] ^= 0xFF  # same size, different bytes
    path.write_bytes(bytes(data))
    on_record(stub, record())
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED" and r.json()["location"] == "store"
    assert str(path) not in r.text
    (rep,) = location_calls(stub)
    assert rep["body"]["state"] == "LOAD_FAILED" and rep["body"]["sha256"] == h


def test_size_mismatch_between_record_and_store_is_409(gw, stub):
    c = gw()
    put(c, PNG)
    on_record(stub, record(bytes=len(PNG) + 1))
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED"


def test_untransferred_runner_object_is_404_artifact_not_ready(gw, stub):
    c = gw()
    on_record(stub, record())
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY" and r.json()["location"] == "store"
    assert byte_calls(stub) == [] and location_calls(stub) == []


def test_core_object_is_relayed_from_core_with_its_digest(gw, stub):
    c = gw()
    on_record(stub, record("art-c", YAML, kind="architecture", mime_type="application/x-yaml", origin="core",
                           state="READY"))
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-c", content=YAML, media_type="application/x-yaml",
            headers={"X-Artifact-Sha256": sha(YAML)})
    r = c.get("/api/v1/runs/r1/artifacts/art-c", headers=auth())
    assert r.status_code == 200 and r.content == YAML
    assert r.headers["x-artifact-sha256"] == sha(YAML) and r.headers["x-artifact-verification"] == "core"
    (call,) = byte_calls(stub)
    assert call["query"] == {} and call["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"
    # core errors keep their code (owner all-in-one semantics: MISSING / NOT_READY / LOAD_FAILED)
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-c", status=404, body={"code": "MISSING", "message": "gone"})
    r = c.get("/api/v1/runs/r1/artifacts/art-c", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "MISSING" and r.json()["location"] == "core"
    # bytes that do not match the record are never passed on
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-c", content=YAML + b"#", media_type="application/x-yaml")
    r = c.get("/api/v1/runs/r1/artifacts/art-c", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED"


def test_checkpoint_is_served_from_the_store_and_never_relayed_from_core(gw, stub):
    c = gw()
    ck = record("ck-1", CKPT, object="checkpoint", kind="checkpoint", mime_type="application/octet-stream",
                origin="core", evaluation_id=None, sample_id=None, metadata={"checkpoint_id": "ck-1", "epoch": 2})
    on_record(stub, ck)
    r = c.get("/api/v1/runs/r1/artifacts/ck-1", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY" and byte_calls(stub) == []
    put(c, CKPT, artifact_id="ck-1", kind="checkpoint")
    r = c.get("/api/v1/runs/r1/artifacts/ck-1", headers=auth())
    assert r.status_code == 200 and sha(r.content) == r.headers["x-artifact-sha256"] == sha(CKPT)
    assert r.headers["content-type"] == "application/octet-stream"


def test_signed_query_with_the_gateway_key_needs_no_bearer(gw, stub):
    c = gw()
    put(c, PNG)
    on_record(stub, record())
    exp = int(time.time()) + 60
    r = c.get("/api/v1/runs/r1/artifacts/art-1", params={"exp": exp, "sig": sign(GW_KEY.encode(), "art-1", exp)})
    assert r.status_code == 200 and r.content == PNG
    rec_call = [x for x in stub.calls if x["path"].endswith("/record")][-1]
    assert rec_call["query"] == {}  # verified here: nothing for core to check


def test_expired_gateway_signature_is_410_without_core(gw, stub):
    exp = int(time.time()) - 5
    r = gw().get("/api/v1/runs/r1/artifacts/art-1", params={"exp": exp, "sig": sign(GW_KEY.encode(), "art-1", exp)})
    assert r.status_code == 410 and r.json()["code"] == "EXPIRED" and stub.calls == []


def test_foreign_signature_is_delegated_to_core_before_any_byte(gw, stub):
    """A URL the core signed in one of its views (core key != gateway key) is verified by the core."""
    c = gw()
    put(c, PNG)
    exp = int(time.time()) + 60
    core_sig = sign(b"the-core-key-is-different-0123456789", "art-1", exp)
    on_record(stub, record())
    r = c.get("/api/v1/runs/r1/artifacts/art-1", params={"exp": exp, "sig": core_sig})
    assert r.status_code == 200 and r.content == PNG
    rec_call = [x for x in stub.calls if x["path"].endswith("/record")][-1]
    assert rec_call["query"] == {"exp": str(exp), "sig": core_sig}
    assert rec_call["headers"]["authorization"] == f"Bearer {CORE_TOKEN}"
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-1/record", status=403,
            body={"code": "ACCESS_DENIED", "message": "invalid artifact signature"})
    r = c.get("/api/v1/runs/r1/artifacts/art-1", params={"exp": exp, "sig": "0" * 64})
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED" and r.json()["location"] == "core"


def test_no_credentials_never_reach_core(gw, stub):
    c = gw()
    r = c.get("/api/v1/runs/r1/artifacts/art-1")
    assert r.status_code == 401 and r.json()["code"] == "AUTH_REQUIRED"
    r = c.get("/api/v1/runs/r1/artifacts/art-1", headers=auth("wrong-token"))
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED"
    assert stub.calls == []


def test_unknown_id_is_core_404_and_a_foreign_record_is_502(gw, stub):
    c = gw()
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-x/record", status=404,
            body={"code": "MISSING", "message": "artifact not found in this run's registry"})
    r = c.get("/api/v1/runs/r1/artifacts/art-x", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "MISSING"
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-y/record", body=record("art-other"))
    r = c.get("/api/v1/runs/r1/artifacts/art-y", headers=auth())
    assert r.status_code == 502 and r.json()["code"] == "CORE_BAD_RESPONSE"


# ------------------------------------------------------------------------------------------------ refresh
def _q(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_refresh_signs_with_the_gateway_key_when_the_bytes_are_in_the_store(gw, stub):
    c = gw(signed_url_ttl_s=120)
    on_record(stub, record())
    view = c.post("/api/v1/runs/r1/artifacts/art-1/refresh", headers=auth()).json()
    assert view["state"] == "NOT_READY" and view["auth_mode"] == "bearer_fetch" and "sig=" not in view["url"]
    put(c, PNG)
    r = c.post("/api/v1/runs/r1/artifacts/art-1/refresh", headers=auth())
    assert r.status_code == 200 and contract_errors("Artifact", r.json()) == []
    view = r.json()
    assert view["state"] == "READY" and view["auth_mode"] == "signed_url" and view["sha256"] == sha(PNG)
    assert urlsplit(view["url"]).path == "/api/v1/runs/r1/artifacts/art-1"
    q = _q(view["url"])
    assert q["sig"] == sign(GW_KEY.encode(), "art-1", int(q["exp"])) and int(q["exp"]) <= time.time() + 121
    assert view["refresh_url"].endswith("/api/v1/runs/r1/artifacts/art-1/refresh")
    assert c.get(urlsplit(view["url"]).path, params=q).content == PNG  # the refreshed URL works without bearer


def test_refresh_of_a_checkpoint_is_an_artifact_view(gw, stub):
    c = gw()
    put(c, CKPT, artifact_id="ck-1", kind="checkpoint")
    on_record(stub, record("ck-1", CKPT, object="checkpoint", kind="checkpoint", evaluation_id=None,
                           mime_type="application/octet-stream", metadata={"checkpoint_id": "ck-1", "epoch": 2}))
    view = c.post("/api/v1/runs/r1/artifacts/ck-1/refresh", headers=auth()).json()
    assert contract_errors("Artifact", view) == []
    assert view["kind"] == "checkpoint" and view["state"] == "READY" and view["metadata"]["checkpoint_id"] == "ck-1"


# ------------------------------------------------------------------------------------------------ export
def _manifest(objects: list[dict[str, Any]], members: dict[str, str] | None = None) -> dict[str, Any]:
    members = members or {"README.txt": "readme\n", "protocol/protocol_hash.txt": "sha256:" + "a" * 64 + "\n",
                          "manifests/hashes.json": json.dumps({"resolved": {"train_manifest_hash": "sha256:" + "b" * 64}}),
                          "SHA256SUMS": "stale\n"}
    return {"format": "mbnas.export-manifest/1", "run_id": "r1", "filename": "r1-export.zip", "members": members,
            "objects": objects}


def test_export_zip_adds_yaml_bytes_from_the_store_and_core_and_is_deterministic(gw, stub, tmp_path):
    c = gw()
    executed = YAML + b"# executed\n"
    put(c, executed, artifact_id="art-exec", kind="architecture")
    put(c, PNG, artifact_id="art-img", kind="tp")
    objects = [
        {"name": "architectures/art-exec.yaml", "id": "art-exec", "kind": "architecture", "candidate_id": "c1",
         "evaluation_id": "e1", "sha256": sha(executed), "bytes": len(executed), "origin": "runner"},
        {"name": "architectures/art-core.yaml", "id": "art-core", "kind": "architecture", "candidate_id": "c2",
         "evaluation_id": None, "sha256": sha(YAML), "bytes": len(YAML), "origin": "core"},
        {"name": "architectures/art-gone.yaml", "id": "art-gone", "kind": "architecture", "candidate_id": "c3",
         "evaluation_id": None, "sha256": "c" * 64, "bytes": 3, "origin": "runner"},
        {"name": "images/art-img.png", "id": "art-img", "kind": "tp", "candidate_id": "c1", "evaluation_id": "e1",
         "sha256": sha(PNG), "bytes": len(PNG), "origin": "runner"},
        {"name": "../escape.yaml", "id": "x", "kind": "architecture", "sha256": sha(YAML), "origin": "core"},
    ]
    stub.on("GET", f"{PREFIX}/runs/r1/export-manifest", body=_manifest(objects))
    stub.on("GET", f"{PREFIX}/runs/r1/artifacts/art-core", content=YAML, media_type="application/x-yaml")
    cache = tmp_path / "gw-cache" / "manifests"
    cache.mkdir(parents=True)
    (cache / f"{'b' * 64}.json").write_text(json.dumps({
        "split_hash": "sha256:" + "b" * 64, "roots": [str(tmp_path / "data")],
        "samples": [{"sample_id": "s1", "image": str(tmp_path / "data" / "a.png"), "label": "/x/a.txt", "sha256": "f" * 64}],
        "issues": [{"code": "LABEL_EMPTY", "message": f"empty label {tmp_path}/data/a.txt"}]}))
    (cache / f"{'d' * 64}.json").write_text(json.dumps({"split_hash": "sha256:" + "d" * 64}))  # not this run's
    r = c.get("/api/v1/runs/r1/export", headers=auth())
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["x-bundle-sha256"] == sha(r.content)
    assert r.headers["content-disposition"] == 'attachment; filename="r1-export.zip"'
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert {"README.txt", "architectures/art-exec.yaml", "architectures/art-core.yaml", "export_objects.json",
            "SHA256SUMS", f"manifests/{'b' * 64}.json"} <= names
    assert zf.read("architectures/art-exec.yaml") == executed and zf.read("architectures/art-core.yaml") == YAML
    assert "images/art-img.png" not in names and "architectures/art-gone.yaml" not in names
    assert f"manifests/{'d' * 64}.json" not in names and not any(".." in n for n in names)
    split = json.loads(zf.read(f"manifests/{'b' * 64}.json"))  # the core's public manifest form: no host paths
    assert "roots" not in split and split["samples"] == [{"sample_id": "s1", "sha256": "f" * 64}]
    assert split["issues"][0]["message"] == "empty label <path>" and str(tmp_path) not in r.content.decode("latin-1")
    sums = dict(reversed(line.split("  ", 1)) for line in zf.read("SHA256SUMS").decode().splitlines())
    assert set(sums) == names - {"SHA256SUMS"}
    assert all(sha(zf.read(n)) == d for n, d in sums.items())
    index = {e["id"]: e for e in json.loads(zf.read("export_objects.json"))}
    assert index["art-exec"]["included"] and index["art-core"]["included"]
    assert index["art-gone"] == {**index["art-gone"], "included": False, "reason": "ARTIFACT_NOT_READY"}
    assert "art-img" not in index
    again = c.get("/api/v1/runs/r1/export", headers=auth())
    assert again.content == r.content  # deterministic archive
    with_images = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/runs/r1/export?include=images", headers=auth()).content))
    assert with_images.read("images/art-img.png") == PNG
    assert c.get("/api/v1/runs/r1/export").status_code == 401


def test_export_never_includes_a_tampered_object(gw, stub):
    c = gw()
    h = put(c, YAML, artifact_id="art-exec", kind="architecture")
    path = store_of(c).path(h)
    path.write_bytes(YAML.replace(b"16", b"32"))  # same size
    objects = [{"name": "architectures/art-exec.yaml", "id": "art-exec", "kind": "architecture", "sha256": h,
                "bytes": len(YAML), "origin": "runner"}]
    stub.on("GET", f"{PREFIX}/runs/r1/export-manifest", body=_manifest(objects))
    zf = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/runs/r1/export", headers=auth()).content))
    assert "architectures/art-exec.yaml" not in zf.namelist()
    (entry,) = json.loads(zf.read("export_objects.json"))
    assert entry["included"] is False and entry["reason"] == "LOAD_FAILED"


def test_export_with_a_malformed_manifest_is_502(gw, stub):
    stub.on("GET", f"{PREFIX}/runs/r1/export-manifest", body={"members": {"/etc/passwd": "x"}, "objects": []})
    r = gw().get("/api/v1/runs/r1/export", headers=auth())
    assert r.status_code == 502 and r.json()["code"] == "CORE_BAD_RESPONSE"


# ------------------------------------------------------------------------------------------------ config revisions
def test_config_revision_routes_are_relayed(gw, stub):
    c = gw()
    listing = {"revisions": [{"revision_id": "rev-1", "kind": "original", "source_sha256": "sha256:" + sha(YAML)}]}
    stub.on("GET", f"{PREFIX}/runs/r1/candidates/c1/config-revisions", body=listing)
    stub.on("GET", f"{PREFIX}/runs/r1/candidates/c1/config-revisions/rev-1/yaml", content=YAML,
            media_type="application/x-yaml", headers={"X-Artifact-Sha256": sha(YAML)})
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions", headers=auth())
    assert r.status_code == 200 and r.json() == listing
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions/rev-1/yaml", headers=auth())
    assert r.status_code == 200 and r.content == YAML and r.headers["x-artifact-sha256"] == sha(YAML)
    assert r.headers["content-type"].startswith("application/x-yaml")
    assert c.get("/api/v1/runs/r1/candidates/c1/config-revisions").status_code == 401
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions/%2E%2E/yaml", headers=auth())
    assert r.status_code == 404 and r.json()["location"] == "gateway"


def test_revision_bytes_core_does_not_hold_come_from_the_gateway_stores(gw, stub):
    """WP-4B: the core answers 404 ARTIFACT_NOT_READY for a revision whose bytes it does not keep; the gateway serves
    them by ``source_sha256`` from the upload store or the artifact store, re-hashed."""
    c = gw()
    up = c.post("/api/v1/uploads/model-config", content=YAML,
                headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": "m.yaml"}).json()
    executed = YAML + b"# executed\n"
    put(c, executed, artifact_id="art-exec", kind="architecture")
    revs = {"run_id": "r1", "candidate_id": "c1", "revisions": [
        {"revision_id": "rev-up", "kind": "original", "source_sha256": up["source_sha256"], "artifact_id": up["upload_id"]},
        {"revision_id": "rev-ex", "kind": "executed", "source_sha256": "sha256:" + sha(executed), "artifact_id": "art-exec"},
        {"revision_id": "rev-gone", "kind": "selected", "source_sha256": "sha256:" + "9" * 64, "artifact_id": None}]}
    stub.on("GET", f"{PREFIX}/runs/r1/candidates/c1/config-revisions", body=revs)
    for rid in ("rev-up", "rev-ex", "rev-gone"):
        stub.on("GET", f"{PREFIX}/runs/r1/candidates/c1/config-revisions/{rid}/yaml", status=404,
                body={"code": "ARTIFACT_NOT_READY", "message": "core holds no bytes of this revision"})
    for rid, data in (("rev-up", YAML), ("rev-ex", executed)):
        r = c.get(f"/api/v1/runs/r1/candidates/c1/config-revisions/{rid}/yaml", headers=auth())
        assert r.status_code == 200 and r.content == data and r.headers["x-artifact-sha256"] == sha(data)
        assert r.headers["content-type"].startswith("application/x-yaml")
        assert r.headers["content-disposition"] == f'attachment; filename="c1_{rid}.yaml"'
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions/rev-gone/yaml", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY"
    upload = Path(c.app.state.settings.artifact_store) / "uploads" / f"{sha(YAML)}.yaml"
    upload.write_bytes(YAML.replace(b"16", b"32"))
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions/rev-up/yaml", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED"
    # any other core answer is relayed as it is (no store lookup)
    stub.on("GET", f"{PREFIX}/runs/r1/candidates/c1/config-revisions/rev-ex/yaml", status=404,
            body={"code": "NOT_FOUND", "message": "no such revision"})
    r = c.get("/api/v1/runs/r1/candidates/c1/config-revisions/rev-ex/yaml", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


# ------------------------------------------------------------------------------------------------ DD-A46 host inputs
def _protocol(ds: dict[str, str], out: Path) -> dict[str, Any]:
    return {"schema_version": "x", "console": {"paths": {"dataset_yaml": ds["dataset_yaml"],
                                                         "train_root": ds["train_root"],
                                                         "search_val_root": ds["search_val_root"],
                                                         "final_eval_root": ds["final_eval_root"],
                                                         "output_root": str(out)}}}


PH = "sha256:" + "e" * 64


def test_validate_caches_inputs_and_runs_attach_a_fresh_measurement(gw, stub, make_dataset, tmp_roots, tmp_path):
    import shutil

    ds = make_dataset("ds")
    proto = _protocol(ds, tmp_roots["runs"] / "out")
    c = gw()
    stub.on("POST", f"{PREFIX}/protocols/validate", body={"valid": True, "protocol_hash": PH, "resolved": proto,
                                                         "errors": [], "capabilities": {}})
    forged = {"output_root": {"ok": True, "requested": "/", "realpath": "/"}}
    r = c.post("/api/v1/protocols/validate", json={"protocol": proto, "path_checks": forged}, headers=auth())
    assert r.status_code == 200
    sent = stub.last("/protocols/validate")["body"]
    assert sent["path_checks"]["output_root"]["realpath"] != "/"  # the browser's host inputs never reach core
    assert sent["path_checks"]["train_root"]["ok"] and "dataset_manifests" in sent and "dataset_yaml" in sent
    cached = tmp_path / "gw-cache" / "gateway_inputs" / f"{'e' * 64}.json"
    assert json.loads(cached.read_text())["inputs"] == {k: sent[k] for k in
                                                        ("path_checks", "dataset_yaml", "dataset_manifests")}
    body = {"protocol": proto, "protocol_hash": PH, "idempotency_key": "k1", "validation_id": "val-1",
            "dataset_manifests": {"forged": True}}
    r = c.post("/api/v1/runs", json=body, headers={**auth(), "Idempotency-Key": "k1"})
    assert r.status_code == 200
    call = stub.last("/runs")
    assert call["path"] == f"{PREFIX}/runs" and call["headers"]["idempotency-key"] == "k1"
    assert call["body"]["dataset_manifests"] == sent["dataset_manifests"]  # same host, same data: same inputs
    assert call["body"]["path_checks"]["train_root"] == sent["path_checks"]["train_root"]
    assert {k: call["body"][k] for k in ("protocol", "protocol_hash", "idempotency_key", "validation_id")} == {
        k: body[k] for k in ("protocol", "protocol_hash", "idempotency_key", "validation_id")}
    # the data changed after validation: the inputs describe the host NOW (core check_fresh -> STALE_VALIDATION),
    # never the validate-time cache entry
    shutil.rmtree(ds["root"])
    assert c.post("/api/v1/runs", json=body, headers=auth()).status_code == 200
    now = stub.last("/runs")["body"]
    assert now["path_checks"]["train_root"]["ok"] is False and "dataset_manifests" not in now


def test_cache_entry_is_attached_when_there_is_nothing_to_measure(gw, stub, make_dataset, tmp_roots):
    """A final retrain whose parent run view carries no protocol paths: the inputs cached for its protocol hash."""
    ds = make_dataset("ds4")
    proto = _protocol(ds, tmp_roots["runs"] / "out")
    c = gw()
    stub.on("POST", f"{PREFIX}/protocols/validate", body={"valid": True, "protocol_hash": PH, "resolved": proto,
                                                         "errors": [], "capabilities": {}})
    assert c.post("/api/v1/protocols/validate", json={"protocol": proto}, headers=auth()).status_code == 200
    sent = stub.last("/protocols/validate")["body"]
    stub.on("GET", f"{PREFIX}/runs/run-p", body={"id": "run-p", "protocol_hash": PH})
    r = c.post("/api/v1/runs/run-p/final-retrain", json={"idempotency_key": "f1", "budget_gpu_hours": 1.0},
               headers=auth())
    assert r.status_code == 200
    fr = stub.last("/final-retrain")["body"]
    assert {k: fr[k] for k in ("path_checks", "dataset_yaml", "dataset_manifests")} == {
        k: sent[k] for k in ("path_checks", "dataset_yaml", "dataset_manifests")}
    stub.on("GET", f"{PREFIX}/runs/run-q", body={"id": "run-q", "protocol_hash": "sha256:" + "f" * 64})
    assert c.post("/api/v1/runs/run-q/final-retrain", json={"idempotency_key": "f2", "budget_gpu_hours": 1.0},
                  headers=auth()).status_code == 200
    assert not {"path_checks", "dataset_yaml", "dataset_manifests"} & set(stub.last("/final-retrain")["body"])


def test_runs_training_and_final_retrain_measure_their_protocol(gw, stub, make_dataset, tmp_roots):
    ds = make_dataset("ds2")
    proto = _protocol(ds, tmp_roots["runs"] / "out")
    c = gw()
    r = c.post("/api/v1/runs", json={"protocol": proto, "protocol_hash": PH, "idempotency_key": "k2"}, headers=auth())
    assert r.status_code == 200
    sent = stub.last("/runs")["body"]
    assert sent["path_checks"]["dataset_yaml"]["ok"] and sent["dataset_manifests"]["format"] == "mbnas.gateway-manifests/1"
    # training run: the request's own protocol is measured (it may never have been validated through this gateway)
    r = c.post("/api/v1/training-runs", json={"source": {"kind": "baseline", "baseline_id": "yolo11n"},
                                              "protocol": proto, "idempotency_key": "t1"}, headers=auth())
    assert r.status_code == 200
    measured = stub.last("/training-runs")["body"]
    assert measured["path_checks"]["train_root"] == sent["path_checks"]["train_root"]
    assert measured["dataset_manifests"] == sent["dataset_manifests"]
    # final retrain: the parent run's protocol (core run view) is measured
    stub.on("GET", f"{PREFIX}/runs/run-p", body={"id": "run-p", "protocol_hash": PH, "resolved_protocol": proto})
    r = c.post("/api/v1/runs/run-p/final-retrain", json={"idempotency_key": "f1", "budget_gpu_hours": 1.0},
               headers=auth())
    assert r.status_code == 200
    fr = stub.last("/final-retrain")["body"]
    assert fr["idempotency_key"] == "f1" and fr["budget_gpu_hours"] == 1.0
    assert fr["path_checks"]["dataset_yaml"] == sent["path_checks"]["dataset_yaml"]
    assert fr["dataset_manifests"] == sent["dataset_manifests"]


def test_resume_attaches_the_runs_host_inputs(gw, stub, make_dataset, tmp_roots):
    ds = make_dataset("ds5")
    proto = _protocol(ds, tmp_roots["runs"] / "out")
    c = gw()
    stub.on("GET", f"{PREFIX}/runs/run-p", body={"id": "run-p", "protocol_hash": PH, "resolved_protocol": proto})
    r = c.post("/api/v1/runs/run-p/resume", headers=auth())  # the web sends no body
    assert r.status_code == 200
    sent = stub.last("/resume")["body"]
    assert sent["path_checks"]["dataset_yaml"]["ok"] and sent["dataset_manifests"]["format"] == "mbnas.gateway-manifests/1"
    stub.on("GET", f"{PREFIX}/runs/run-x", status=404, body={"code": "RUN_NOT_FOUND", "message": "no run"})
    assert c.post("/api/v1/runs/run-x/resume", json={}, headers=auth()).status_code == 200  # core answers itself
    assert stub.last("/resume")["body"] == {}
    aio = gw(dataset_roots=[])  # all-in-one: nothing measured; an empty body stays empty, forged inputs are dropped
    assert aio.post("/api/v1/runs/run-p/resume", headers=auth()).status_code == 200
    assert stub.last("/resume")["body"] is None
    assert aio.post("/api/v1/runs/run-p/resume", json={"path_checks": {"output_root": {"ok": True}}},
                    headers=auth()).status_code == 200
    assert stub.last("/resume")["body"] == {}


def test_owner_all_in_one_attaches_nothing_and_drops_browser_inputs(gw, stub, make_dataset, tmp_roots):
    ds = make_dataset("ds3")
    proto = _protocol(ds, tmp_roots["runs"] / "out")
    c = gw(dataset_roots=[], output_roots=[])
    forged = {"path_checks": {"output_root": {"ok": True, "realpath": "/"}}, "dataset_yaml": {"ok": True}}
    for route, body in (("/api/v1/protocols/validate", {"protocol": proto, **forged}),
                        ("/api/v1/runs", {"protocol": proto, "protocol_hash": PH, "idempotency_key": "k", **forged}),
                        ("/api/v1/training-runs", {"protocol": proto, "idempotency_key": "t", **forged}),
                        ("/api/v1/runs/run-p/final-retrain", {"idempotency_key": "f", **forged})):
        assert c.post(route, json=body, headers=auth()).status_code == 200
        sent = stub.calls[-1]["body"]
        assert not {"path_checks", "dataset_yaml", "dataset_manifests"} & set(sent), (route, sent)
    assert not [x for x in stub.calls if x["method"] == "GET"]  # no parent lookup without dataset roots


def test_empty_body_and_non_object_bodies_still_reach_core(gw, stub):
    c = gw()
    r = c.post("/api/v1/runs", content=b"", headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 200 and stub.last("/runs")["body"] is None
    r = c.post("/api/v1/runs/run-p/final-retrain", json=[1, 2], headers=auth())
    assert r.status_code == 200 and stub.last("/final-retrain")["body"] == [1, 2]


# ------------------------------------------------------------------------------------------------ DD-A47 (d)
def test_model_path_goes_to_core_unchanged_without_gateway_roots(gw, stub, tmp_path):
    c = gw(model_yaml_roots=[], output_roots=[])
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "m.yaml").write_bytes(YAML)
    path = str(tmp_path / "elsewhere" / "m.yaml")  # outside every root: core checks it against its own roots
    for route, body in (("/api/v1/structure-check", {"model_path": path, "input_size": 64}),
                        ("/api/v1/training-runs", {"source": {"kind": "user", "model_path": path}})):
        r = c.post(route, json=body, headers=auth())
        assert r.status_code == 200, (route, r.text)
        assert stub.calls[-1]["body"] == body
    r = c.post("/api/v1/structure-check", json={"model_path": path, "upload_id": None}, headers=auth())
    assert r.status_code == 200 and stub.calls[-1]["body"] == {"model_path": path}  # null upload_id: core lacks the key
    # with roots configured the gateway still checks (DD-A44 (d) codes unchanged)
    r = gw(model_yaml_roots=[str(tmp_path / "roots")]).post("/api/v1/structure-check", json={"model_path": path},
                                                             headers=auth())
    assert r.status_code == 422 and r.json()["code"] == "PATH_OUTSIDE_ALLOWLIST"
