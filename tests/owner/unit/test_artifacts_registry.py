"""artifacts.registry: signatures, run-relative registration, file resolution and the contract view.

Added 2026-10-15 with the reimplementation of the missing ``mbnas_server.artifacts`` package (the API-level access
matrix lives in tests/api/test_artifacts_export.py; this file pins the module's own rules without an API).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mbnas_core.artifacts.registry import (
    ArtifactError, artifact_path, artifact_view, register_file, resolve_file, run_dir, sign, verify_signature,
)
from mbnas_core.db import repo
from mbnas_core.db.core import tx

KEY = b"unit-test-signing-key-0123456789"


def test_sign_and_verify() -> None:
    sig = sign(KEY, "art-1", 2000)
    assert len(sig) == 64 and verify_signature(KEY, "art-1", "2000", sig, now=1999.0) == "ok"
    assert verify_signature(KEY, "art-1", "2000", sig, now=2001.0) == "expired"
    assert verify_signature(KEY, "art-2", "2000", sig, now=1.0) == "invalid"  # bound to the artifact id
    assert verify_signature(KEY, "art-1", "2001", sig, now=1.0) == "invalid"  # bound to the expiry
    assert verify_signature(b"other", "art-1", "2000", sig, now=1.0) == "invalid"
    assert verify_signature(KEY, "art-1", "2000", "0" * 64, now=5000.0) == "invalid"  # forged is never "expired"
    for exp, s in ((None, sig), ("2000", None), ("x", sig), ("", sig)):
        assert verify_signature(KEY, "art-1", exp, s) == "invalid"


@pytest.mark.parametrize("bad_sig", ["\u00e9", "\u00e9" * 32, "\u0663" * 64, "0" * 63 + "\u00e9", "\ud800", "\x00" * 64, "", " "])
def test_verify_signature_non_ascii_or_odd_sig_is_invalid_not_an_exception(bad_sig: str) -> None:
    """Regression (verification 2026-10-15): hmac.compare_digest(str, str) raises TypeError on non-ASCII text, which
    surfaced as HTTP 500 INTERNAL_ERROR for ``?sig=%C3%A9``; the contract expects 403 ACCESS_DENIED (-> "invalid")."""
    assert verify_signature(KEY, "art-1", "2000", bad_sig, now=1.0) == "invalid"
    assert verify_signature(KEY, "art-1", "\u0663", bad_sig, now=1.0) == "invalid"  # non-ASCII exp digit too


def _run(tmp_path: Path) -> dict:
    return {"id": "run-u1", "resolved_protocol": {"console": {"paths": {"output_root": str(tmp_path / "out")}}}}


def test_run_dir_and_paths(tmp_path: Path) -> None:
    assert run_dir(_run(tmp_path)) == tmp_path / "out" / "run-u1"
    assert artifact_path("r", "a") == "/api/v1/runs/r/artifacts/a"
    for bad in ("", "..", "a/b"):
        with pytest.raises(ArtifactError):
            run_dir({**_run(tmp_path), "id": bad})


@pytest.fixture
def conn_run(db, tmp_path: Path):
    """A DB with one run row whose output root is tmp_path/out."""
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    proto = pilot_smoke_protocol()
    proto["console"]["paths"]["output_root"] = str(tmp_path / "out")
    with tx(db):
        run, _ = repo.create_run(db, id="run-u1", namespace="t", protocol_hash="sha256:" + "0" * 64,
                                 resolved_protocol=proto, requested_protocol=proto, execution_mode="PILOT",
                                 idempotency_key="k", payload_sha256="p")
    return db, run


def test_register_resolve_and_view(conn_run, tmp_path: Path) -> None:
    conn, run = conn_run
    f = run_dir(run) / "images" / "a.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x89PNG-bytes")
    with tx(conn):
        art = register_file(conn, run, kind="tp", path=str(f), evaluation_id="ev1", sample_id="s1",
                            meta={"matching": {"sample_id": "s1"}})
        again = register_file(conn, run, kind="tp", path="images/a.png", evaluation_id="ev1")  # relative, same row
    assert art["id"] == again["id"] and art["relpath"] == "images/a.png" and art["state"] == "READY"
    assert art["sha256"] == hashlib.sha256(b"\x89PNG-bytes").hexdigest() and art["mime_type"] == "image/png"
    assert resolve_file(run, art, [str(tmp_path / "out")]) == f.resolve()
    with pytest.raises(ArtifactError) as exc:
        resolve_file(run, art, [str(tmp_path / "elsewhere")])  # run dir not below any output root
    assert exc.value.code == "ACCESS_DENIED"
    view = artifact_view(art, base_url="http://h:1/", key=KEY, ttl_s=60, now=1000.0)
    assert view["auth_mode"] == "signed_url" and view["url"].startswith("http://h:1/api/v1/runs/run-u1/artifacts/")
    assert "exp=1060" in view["url"] and view["expires_at"].startswith("1970-01-01T00:17:40") and view["matching"] == {"sample_id": "s1"}
    plain = artifact_view(art, base_url="http://h:1", key=None, ttl_s=60)
    assert plain["auth_mode"] == "bearer_fetch" and "?" not in plain["url"] and plain["expires_at"] is None


def test_register_refuses_escape_missing_and_hash_mismatch(conn_run, tmp_path: Path) -> None:
    conn, run = conn_run
    base = run_dir(run)
    base.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    os.symlink(outside, base / "link.txt")
    (base / "ok.txt").write_text("ok")
    with tx(conn):
        for path, code in (("link.txt", "ACCESS_DENIED"), (str(outside), "ACCESS_DENIED"), ("../x", "ACCESS_DENIED"),
                           ("nope.txt", "MISSING")):
            with pytest.raises(ArtifactError) as exc:
                register_file(conn, run, kind="other", path=path)
            assert exc.value.code == code, path
        with pytest.raises(ArtifactError) as exc:
            register_file(conn, run, kind="other", path="ok.txt", sha256="0" * 64)
        assert exc.value.code == "LOAD_FAILED"
        with pytest.raises(ValueError):
            register_file(conn, run, kind="not-a-kind", path="ok.txt")
