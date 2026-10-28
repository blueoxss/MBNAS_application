"""WP-5A download helper and signed URLs: ``serve_artifact`` headers, ARTIFACT_NOT_READY, digest re-check before and
while streaming (held-back final chunk), the trailing-mismatch behaviour over a real loopback HTTP connection, and the
HMAC scheme ported from the original artifact registry (``sign`` / ``verify_signature`` / ``artifact_view``).

Synthetic bytes in ``tmp_path``; the HTTP cases use an in-process ASGI client and one uvicorn server on an ephemeral
127.0.0.1 port (no GPU, no external network)."""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from mbnas_server.api.errors import install
from mbnas_server.artifacts import (
    ArtifactBody, ArtifactStore, SignatureError, StoreError, artifact_path, artifact_view, require_valid_signature,
    serve_artifact, sign, signing_key, verify_signature,
)

KEY = b"unit-test-signing-key-0123456789"
FORBIDDEN_PORTS = {8000, 8001, 8002, 5173, 5174, 5175}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "store")


def stored(store: ArtifactStore, data: bytes, **extra) -> dict:
    h = sha(data)
    store.put_stream(h, io.BytesIO(data), {"run_id": "run-1", "artifact_id": "art-1", "kind": "checkpoint"})
    return {"artifact_id": "art-1", "sha256": h, "bytes": len(data), "mime_type": "image/png", "run_id": "run-1",
            **extra}


def tamper(store: ArtifactStore, h: str, offset: int) -> None:
    """Flip one byte in place (same size): what a disk fault or a hand edit does to a stored object."""
    with open(store.path(h), "r+b") as fh:
        fh.seek(offset)
        byte = fh.read(1)
        fh.seek(offset)
        fh.write(bytes([byte[0] ^ 0xFF]))


# ------------------------------------------------------------------------------------------------ serve_artifact
def test_headers_and_bytes(store: ArtifactStore) -> None:
    data = os.urandom(3 * 1024 * 1024 + 17)
    headers, body = serve_artifact(store, stored(store, data))
    assert headers == {
        "Content-Type": "image/png", "Content-Length": str(len(data)), "X-Artifact-Sha256": sha(data),
        "X-Artifact-Verification": "pre-send", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Accept-Ranges": "none",
    }
    assert isinstance(body, ArtifactBody) and b"".join(body) == data and body.closed


def test_large_objects_stream_with_verification_at_the_end(store: ArtifactStore) -> None:
    data = os.urandom(64 * 1024 + 5)
    headers, body = serve_artifact(store, stored(store, data), verify_first_max_bytes=1024, chunk_bytes=4096)
    assert headers["X-Artifact-Verification"] == "streaming" and headers["Content-Length"] == str(len(data))
    chunks = list(body)
    assert b"".join(chunks) == data and len(chunks) == 17 and body.closed
    _, body = serve_artifact(store, stored(store, data), verify_first=False, chunk_bytes=4096)
    assert b"".join(body) == data  # verify_first=False streams even small objects


def test_record_id_alias_and_missing_size(store: ArtifactStore) -> None:
    data = b"core returns id, not artifact_id"
    rec = stored(store, data)
    rec = {"id": rec["artifact_id"], "sha256": rec["sha256"], "mime_type": None}
    headers, body = serve_artifact(store, rec)
    assert headers["Content-Type"] == "application/octet-stream" and b"".join(body) == data


@pytest.mark.parametrize("record, code, status", [
    ({"artifact_id": "a", "sha256": "f" * 64, "bytes": 1}, "ARTIFACT_NOT_READY", 404),  # not transferred
    ({"artifact_id": "a", "sha256": None}, "ARTIFACT_NOT_READY", 404),  # core has no digest yet
    ({"artifact_id": "a", "sha256": ""}, "ARTIFACT_NOT_READY", 404),
    ({"artifact_id": "a", "sha256": "../../etc/passwd"}, "LOAD_FAILED", 409),  # never used as a path
    ({"artifact_id": "a", "sha256": "F" * 64}, "LOAD_FAILED", 409),
    ({"artifact_id": "a", "sha256": "sha256:" + "f" * 64}, "LOAD_FAILED", 409),
], ids=["absent", "no-digest", "empty-digest", "path", "upper", "prefixed"])
def test_absent_or_unusable_records(store: ArtifactStore, record: dict, code: str, status: int) -> None:
    with pytest.raises(StoreError) as exc:
        serve_artifact(store, record)
    assert (exc.value.code, exc.value.status, exc.value.location) == (code, status, "store")
    assert str(store.root) not in exc.value.message


def test_size_mismatch_is_refused_before_any_byte(store: ArtifactStore) -> None:
    data = os.urandom(10_000)
    for wrong in (9_999, 10_001, 0):
        with pytest.raises(StoreError) as exc:
            serve_artifact(store, stored(store, data, bytes=wrong), verify_first=False)
        assert exc.value.code == "LOAD_FAILED" and exc.value.status == 409
    for bad in (-1, "10000", True, 1.5):
        with pytest.raises(StoreError) as exc:
            serve_artifact(store, stored(store, data, bytes=bad))
        assert exc.value.code == "LOAD_FAILED"


def test_tampered_object_verify_first_is_a_clean_409(store: ArtifactStore) -> None:
    data = os.urandom(2 * 1024 * 1024)
    rec = stored(store, data)
    tamper(store, rec["sha256"], 1_000_000)
    with pytest.raises(StoreError) as exc:
        serve_artifact(store, rec)  # verify_first (default): no headers, no iterator
    assert exc.value.code == "LOAD_FAILED" and exc.value.status == 409


def test_single_chunk_objects_are_always_verified_first(store: ArtifactStore) -> None:
    data = b"small and tampered"
    rec = stored(store, data)
    tamper(store, rec["sha256"], 3)
    with pytest.raises(StoreError) as exc:
        serve_artifact(store, rec, verify_first=False)
    assert exc.value.code == "LOAD_FAILED"
    empty = b""
    headers, body = serve_artifact(store, stored(store, empty), verify_first=False)
    assert headers["Content-Length"] == "0" and headers["X-Artifact-Verification"] == "pre-send" and list(body) == []


def test_streamed_tampered_object_withholds_the_final_chunk(store: ArtifactStore) -> None:
    data = os.urandom(40 * 1024 + 123)
    rec = stored(store, data)
    tamper(store, rec["sha256"], 20_000)
    headers, body = serve_artifact(store, rec, verify_first=False, chunk_bytes=4096)
    assert headers["X-Artifact-Sha256"] == rec["sha256"] and headers["X-Artifact-Verification"] == "streaming"
    received = bytearray()
    with pytest.raises(StoreError) as exc:
        for chunk in body:
            received.extend(chunk)
    assert exc.value.code == "LOAD_FAILED" and body.closed
    last = len(data) % 4096 or 4096
    assert len(received) == len(data) - last  # everything but the held-back final chunk
    with open(store.path(rec["sha256"]), "rb") as fh:
        assert bytes(received) == fh.read(len(received))
    assert list(body) == []  # exhausted after the error


@pytest.mark.parametrize("change", ["grow", "shrink"])
def test_object_changing_while_streamed_is_refused(store: ArtifactStore, change: str) -> None:
    data = os.urandom(32 * 1024)
    rec = stored(store, data)
    headers, body = serve_artifact(store, rec, verify_first=False, chunk_bytes=4096)
    first = next(body)
    assert first == data[:4096]
    with open(store.path(rec["sha256"]), "r+b") as fh:
        if change == "grow":
            fh.seek(0, os.SEEK_END)
            fh.write(b"appended")
        else:
            fh.truncate(10_000)
    with pytest.raises(StoreError) as exc:
        list(body)
    assert exc.value.code == "LOAD_FAILED" and body.closed


def test_unsafe_mime_falls_back_to_octet_stream(store: ArtifactStore) -> None:
    data = b"mime"
    for mime, expected in (("text/html\r\nSet-Cookie: x=1", "application/octet-stream"), ("image/png", "image/png"),
                           ("application/json; charset=utf-8", "application/json; charset=utf-8"),
                           ("../../etc", "application/octet-stream"), (None, "application/octet-stream"),
                           ("téxt/plain", "application/octet-stream")):
        headers, body = serve_artifact(store, stored(store, data, mime_type=mime))
        body.close()
        assert headers["Content-Type"] == expected


def test_closing_the_body_releases_the_file(store: ArtifactStore, monkeypatch) -> None:
    data = os.urandom(50_000)
    rec = stored(store, data)
    opened: list = []
    real_open = store.open_stream

    def spy(h: str):
        fh = real_open(h)
        opened.append(fh)
        return fh

    monkeypatch.setattr(store, "open_stream", spy)
    headers, body = serve_artifact(store, rec, verify_first=False, chunk_bytes=4096)
    assert next(body) == data[:4096] and len(opened) == 1 and not opened[0].closed
    body.close()  # client disconnect
    assert body.closed and opened[0].closed and list(body) == []
    for bad in ({**rec, "bytes": 1}, {**rec, "bytes": len(data)}):  # refused before headers / fully consumed
        if bad["bytes"] == 1:
            with pytest.raises(StoreError):
                serve_artifact(store, bad)
        else:
            assert b"".join(serve_artifact(store, bad)[1]) == data
    tamper(store, rec["sha256"], 100)
    with pytest.raises(StoreError):  # verify-first mismatch
        serve_artifact(store, rec)
    assert len(opened) == 4 and all(fh.closed for fh in opened)  # no descriptor is left open on any path


# ------------------------------------------------------------------------------------------------ HTTP (ASGI)
def _app(store: ArtifactStore, records: dict, key: bytes | None):
    """Minimal route with the wiring WP-5 asks for: signed-query check, record lookup, serve_artifact (the real
    gateway route also accepts the bearer token; FastAPI resolves the string annotations from module globals)."""
    app = FastAPI()
    install(app)

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}")
    async def get_artifact(request: Request, run_id: str, artifact_id: str):
        q = request.query_params
        if q.get("exp") is not None or q.get("sig") is not None:
            require_valid_signature(key, artifact_id, q.get("exp"), q.get("sig"))
        record = records.get((run_id, artifact_id))
        if record is None:
            raise StoreError("ARTIFACT_NOT_READY", "unknown artifact")
        headers, body = await run_in_threadpool(serve_artifact, store, record)
        return StreamingResponse(body, headers=headers)

    return app


def test_asgi_route_serves_bytes_signed_urls_and_error_bodies(store: ArtifactStore) -> None:
    from fastapi.testclient import TestClient

    data = os.urandom(256 * 1024)
    rec = stored(store, data)
    small = b"small tampered"
    rec_small = stored(store, small, artifact_id="art-2")
    tamper(store, rec_small["sha256"], 0)
    records = {("run-1", "art-1"): rec, ("run-1", "art-2"): rec_small,
               ("run-1", "art-3"): {**rec, "artifact_id": "art-3", "sha256": "e" * 64}}
    with TestClient(_app(store, records, KEY)) as client:
        r = client.get("/api/v1/runs/run-1/artifacts/art-1")
        assert r.status_code == 200 and r.content == data
        assert r.headers["x-artifact-sha256"] == sha(data) and r.headers["content-length"] == str(len(data))
        assert r.headers["content-type"] == "image/png" and r.headers["cache-control"] == "private, no-store"
        bad = client.get("/api/v1/runs/run-1/artifacts/art-2")
        assert bad.status_code == 409 and bad.json()["code"] == "LOAD_FAILED" and bad.json()["location"] == "store"
        absent = client.get("/api/v1/runs/run-1/artifacts/art-3")
        assert absent.status_code == 404 and absent.json()["code"] == "ARTIFACT_NOT_READY"
        # signed URLs from artifact_view work; expired -> 410, forged -> 403 (gateway location)
        view = artifact_view({"id": "art-1", "kind": "checkpoint", "state": "READY", "sha256": rec["sha256"]},
                             base_url="http://testserver/", key=KEY, ttl_s=60, run_id="run-1")
        assert view["auth_mode"] == "signed_url"
        assert client.get(view["url"]).content == data
        exp = int(time.time()) - 5
        stale = client.get(f"/api/v1/runs/run-1/artifacts/art-1?exp={exp}&sig={sign(KEY, 'art-1', exp)}")
        assert stale.status_code == 410 and stale.json()["code"] == "EXPIRED" and stale.json()["location"] == "gateway"
        forged = client.get(f"/api/v1/runs/run-1/artifacts/art-1?exp={exp + 10_000}&sig={'0' * 64}")
        assert forged.status_code == 403 and forged.json()["code"] == "ACCESS_DENIED"
        other = client.get(f"/api/v1/runs/run-1/artifacts/art-2?{view['url'].split('?', 1)[1]}")
        assert other.status_code == 403  # a signature is bound to its artifact id


@pytest.fixture
def live(store: ArtifactStore):
    """uvicorn on an ephemeral loopback port serving the minimal route (records filled by the test)."""
    import uvicorn

    records: dict = {}
    for _ in range(20):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        if port not in FORBIDDEN_PORTS:
            break
    server = uvicorn.Server(uvicorn.Config(_app(store, records, KEY), host="127.0.0.1", port=port,
                                           log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}", records
    server.should_exit = True
    thread.join(timeout=15)


def test_trailing_mismatch_over_http_is_an_incomplete_response(store: ArtifactStore, live) -> None:
    base, records = live
    data = os.urandom(96 * 1024 * 1024 + 7)  # above the 64 MiB verify-first threshold: streamed
    rec = stored(store, data)
    records[("run-1", "art-1")] = rec
    with httpx.Client(timeout=60) as client:
        ok = client.get(f"{base}/api/v1/runs/run-1/artifacts/art-1")
        assert ok.status_code == 200 and ok.headers["x-artifact-verification"] == "streaming"
        assert len(ok.content) == len(data) and sha(ok.content) == ok.headers["x-artifact-sha256"] == rec["sha256"]
        tamper(store, rec["sha256"], 50 * 1024 * 1024)
        received = bytearray()
        with pytest.raises(httpx.RemoteProtocolError):
            with client.stream("GET", f"{base}/api/v1/runs/run-1/artifacts/art-1") as r:
                assert r.status_code == 200 and int(r.headers["content-length"]) == len(data)
                for part in r.iter_raw():
                    received.extend(part)
    assert 0 < len(received) < len(data)  # the client never gets the complete tampered body


# ------------------------------------------------------------------------------------------------ signing (ported)
def test_sign_and_verify() -> None:
    """Ported unchanged from the original registry unit tests."""
    sig = sign(KEY, "art-1", 2000)
    assert len(sig) == 64 and verify_signature(KEY, "art-1", "2000", sig, now=1999.0) == "ok"
    assert verify_signature(KEY, "art-1", "2000", sig, now=2001.0) == "expired"
    assert verify_signature(KEY, "art-2", "2000", sig, now=1.0) == "invalid"  # bound to the artifact id
    assert verify_signature(KEY, "art-1", "2001", sig, now=1.0) == "invalid"  # bound to the expiry
    assert verify_signature(b"other", "art-1", "2000", sig, now=1.0) == "invalid"
    assert verify_signature(KEY, "art-1", "2000", "0" * 64, now=5000.0) == "invalid"  # forged is never "expired"
    for exp, s in ((None, sig), ("2000", None), ("x", sig), ("", sig)):
        assert verify_signature(KEY, "art-1", exp, s) == "invalid"


@pytest.mark.parametrize("bad_sig", ["é", "é" * 32, "٣" * 64, "0" * 63 + "é", "\ud800", "\x00" * 64, "", " "])
def test_verify_signature_non_ascii_or_odd_sig_is_invalid_not_an_exception(bad_sig: str) -> None:
    """Ported unchanged: non-ASCII input must be "invalid" (403), never a TypeError (HTTP 500)."""
    assert verify_signature(KEY, "art-1", "2000", bad_sig, now=1.0) == "invalid"
    assert verify_signature(KEY, "art-1", "٣", bad_sig, now=1.0) == "invalid"  # non-ASCII exp digit too


def test_hmac_scheme_is_unchanged() -> None:
    """Known answer: hex HMAC-SHA256(key, "<artifact_id>.<exp>") - URLs signed by the previous code still verify."""
    expected = hmac.new(KEY, b"art-abc.1700000000", hashlib.sha256).hexdigest()
    assert sign(KEY, "art-abc", 1700000000) == expected
    assert verify_signature(KEY, "art-abc", "1700000000", expected, now=1699999999.0) == "ok"


def test_require_valid_signature_maps_to_route_errors() -> None:
    sig = sign(KEY, "art-1", 2000)
    assert require_valid_signature(KEY, "art-1", "2000", sig, now=10.0) == "signed_url"
    for key, exp, s, now, status, code in ((KEY, "2000", sig, 3000.0, 410, "EXPIRED"),
                                           (KEY, "2000", "f" * 64, 10.0, 403, "ACCESS_DENIED"),
                                           (None, "2000", sig, 10.0, 403, "ACCESS_DENIED"),  # gateway without key
                                           (KEY, None, None, 10.0, 403, "ACCESS_DENIED")):
        with pytest.raises(SignatureError) as exc:
            require_valid_signature(key, "art-1", exp, s, now=now)
        assert (exc.value.status, exc.value.code, exc.value.location) == (status, code, "gateway")


def test_signing_key_from_gateway_settings() -> None:
    from mbnas_server.config import GatewaySettings

    assert signing_key(GatewaySettings()) is None
    assert signing_key(GatewaySettings(artifact_signing_key="k" * 40)) == b"k" * 40
    assert signing_key(SimpleNamespace(artifact_signing_key=None)) is None


def test_artifact_view_registry_row_shape() -> None:
    """Same expectations as the original ``test_register_resolve_and_view`` for the view part."""
    row = {"id": "art-x", "run_id": "run-u1", "kind": "tp", "state": "READY", "sha256": "a" * 64,
           "mime_type": "image/png", "sample_id": "s1", "split_hash": None, "evaluation_id": "ev1",
           "meta": {"matching": {"sample_id": "s1"}, "origin": "evaluation", "secret_path": "/x", "epoch": 3,
                    "nested": {"a": 1}}}
    view = artifact_view(row, base_url="http://h:1/", key=KEY, ttl_s=60, now=1000.0)
    assert view["auth_mode"] == "signed_url" and view["url"].startswith("http://h:1/api/v1/runs/run-u1/artifacts/")
    assert "exp=1060" in view["url"] and view["expires_at"].startswith("1970-01-01T00:17:40")
    assert view["matching"] == {"sample_id": "s1"} and view["refresh_url"] == "http://h:1" + artifact_path("run-u1", "art-x") + "/refresh"
    assert view["metadata"] == {"origin": "evaluation", "epoch": 3}  # allowlist + scalars only
    sig = view["url"].split("sig=", 1)[1]
    assert verify_signature(KEY, "art-x", "1060", sig, now=1000.0) == "ok"
    plain = artifact_view(row, base_url="http://h:1", key=None, ttl_s=60)
    assert plain["auth_mode"] == "bearer_fetch" and "?" not in plain["url"] and plain["expires_at"] is None
    not_ready = artifact_view({**row, "state": "NOT_READY"}, base_url="http://h:1", key=KEY, ttl_s=60)
    assert not_ready["auth_mode"] == "bearer_fetch" and not_ready["refresh_url"] is None


def test_artifact_view_core_record_shape() -> None:
    """Core's id-based record (WP-5 section 2): no URL, ``metadata`` instead of ``meta``, run id from the caller."""
    rec = {"id": "art-y", "kind": "checkpoint", "sha256": "b" * 64, "bytes": 10, "mime_type": "application/octet-stream",
           "state": "READY", "sample_id": None, "split_hash": None, "metadata": {"epoch": 5, "checkpoint_id": "ck"},
           "matching": None}
    view = artifact_view(rec, base_url="http://gw:8710", key=KEY, ttl_s=600, now=0.0, run_id="run-9")
    assert view["url"].startswith("http://gw:8710/api/v1/runs/run-9/artifacts/art-y?exp=600&sig=")
    assert view["metadata"] == {"epoch": 5, "checkpoint_id": "ck"} and view["matching"] is None
    with pytest.raises(ValueError):
        artifact_view(rec, base_url="http://gw", key=KEY, ttl_s=1)  # no run id at all
    with pytest.raises(ValueError):
        artifact_view({**rec, "run_id": "run-1"}, base_url="http://gw", key=KEY, ttl_s=1, run_id="run-2")
