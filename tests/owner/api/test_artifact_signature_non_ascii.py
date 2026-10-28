"""API regression (verification 2026-10-15): a non-ASCII ``sig`` query value on the unauthenticated signed-URL route must
be answered 403 ACCESS_DENIED like any other invalid signature, never 500 INTERNAL_ERROR.

The module under test (``mbnas_server.artifacts.registry.verify_signature``) was reimplemented on 2026-10-15; the
original is missing, so this pins the contract matrix of tests/api/test_artifacts_export.py for the non-ASCII case.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

import harness
from harness import auth


@pytest.fixture
def signed_artifact_url(client, protocol, inline_worker):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    art = next(a for c in snap["candidates"] for a in c["artifacts"] if a["kind"] == "tp")
    parts = urlsplit(art["url"])
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


@pytest.mark.parametrize("raw_query", ["exp={exp}&sig=%C3%A9", "exp={exp}&sig=%E2%82%AC%E2%82%AC", "exp=%D9%A3&sig=%C3%A9",
                                       "exp={exp}&sig=%FF%FE", "exp={exp}&sig=%ED%A0%80"])
def test_non_ascii_signature_is_403_access_denied(client, signed_artifact_url, raw_query):
    path, query = signed_artifact_url
    r = client.get(f"{path}?{raw_query.format(exp=query['exp'])}")  # no Authorization header
    assert r.status_code == 403 and r.json()["code"] == "ACCESS_DENIED", (r.status_code, r.text[:200])
    assert client.get(path, params=query).status_code == 200  # the genuine URL still works
