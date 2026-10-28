"""DD-26 diagnostic ``registry_roots_skipped`` (contract 1.4.1) after contract 1.5.0.

1.4.1 put it on the public GET /registry next to ``source`` / ``warning``. It names owner-host paths, so contract 1.5.0
(DTO allowlist OWNER_ONLY, DD-A19) removed it from the public ``RegistryList`` (closed); the admin-only owner route
``GET /core/v1/owner/registry`` serves it next to the raw entries, ``source`` and ``warning``. The 1.4.1 behaviour is
kept there unchanged: the documented ``[{path, reason}]`` shape, ``[]`` when every root is readable, a failing
diagnostic is reported (never raised), a really unreadable output root is named. Synthetic settings, no GPU.
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from harness import auth
from pydantic import ValidationError

import mbnas_core.registry as core_registry
from mbnas_server.api.contract_check import contract_errors
from mbnas_contracts.openapi import prune
from mbnas_contracts import schemas

SKIPPED = [{"path": "/ABSOLUTE/PATH/TO/MBNAS_runs/evidence", "reason": "PermissionError: [Errno 13] denied"}]


@pytest.fixture
def api_settings(tmp_roots, tmp_path):
    """WP-3C: the core DB lives OUTSIDE the output root in this module. Behind the gateway every core request
    authenticates the gateway's server principal against the DB (WP-3 contract 1.7), so a mode-000 output root that also
    held ``db/`` turned the registry answer into a 500 before the route ran. Same settings as tests/owner/api/conftest.py
    otherwise."""
    import harness

    db = tmp_path / "core_db"
    db.mkdir()
    return harness.make_settings(tmp_roots, expose_legacy_api=False, db_path=str(db / "mbnas.sqlite3"))


def _public(client) -> dict:
    """The gateway's GET /api/v1/registry: a closed 1.5.0 RegistryList without the diagnostic."""
    resp = client.get("/api/v1/registry", headers=auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert contract_errors("RegistryList", body) == [], contract_errors("RegistryList", body)[:3]
    assert prune("RegistryList", body) == body
    assert "registry_roots_skipped" not in body and body["registry"]
    return body


def _owner(core_client, combined) -> dict:
    resp = core_client.get("/core/v1/owner/registry", headers={"Authorization": f"Bearer {combined.tokens['admin']}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"registry", "source", "warning", "registry_roots_skipped"} <= set(body) and body["registry"]
    return body


def test_registry_lists_the_skipped_roots_in_the_documented_shape(client, core_client, combined, monkeypatch):
    seen = []

    def fake(output_root):
        seen.append(output_root)
        return SKIPPED

    monkeypatch.setattr(core_registry, "roots_skipped", fake)
    body = _owner(core_client, combined)
    assert body["registry_roots_skipped"] == SKIPPED
    assert seen and isinstance(seen[0], str)
    public = _public(client)  # the same moment: the public answer never carries the owner-host paths
    assert "source" in public and "warning" in public and SKIPPED[0]["path"] not in json.dumps(public)
    server = core_client.get("/core/v1/owner/registry", headers={"Authorization": f"Bearer {combined.tokens['server']}"})
    assert server.status_code == 403 and server.json()["code"] == "ACCESS_DENIED"  # the gateway's token cannot read it


def test_registry_roots_skipped_is_an_empty_list_when_every_root_is_readable(client, core_client, combined, monkeypatch):
    monkeypatch.setattr(core_registry, "roots_skipped", lambda output_root: [])
    assert _owner(core_client, combined)["registry_roots_skipped"] == []
    _public(client)


def test_a_failing_diagnostic_is_reported_not_raised(client, core_client, combined, monkeypatch):
    def boom(output_root):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(core_registry, "roots_skipped", boom)
    body = _owner(core_client, combined)
    assert body["registry_roots_skipped"][0]["reason"] == "RuntimeError: scan failed" and body["registry"]
    _public(client)  # the public registry is unaffected


def test_a_really_unreadable_output_root_is_named(client, core_client, combined, api_settings):
    root = api_settings.output_root
    os.makedirs(root, exist_ok=True)
    mode = stat.S_IMODE(os.stat(root).st_mode)
    os.chmod(root, 0)
    try:
        try:
            os.scandir(root).close()
        except OSError:
            pass
        else:
            pytest.skip("this process can read a mode-000 directory (root user?)")
        skipped = _owner(core_client, combined)["registry_roots_skipped"]
        public = _public(client)
    finally:
        os.chmod(root, mode)
    assert [s for s in skipped if s["path"] == str(root)] and any("PermissionError" in s["reason"] for s in skipped)
    assert str(root) not in json.dumps(public.get("warning"))


def test_registry_list_mirror_has_the_documented_members() -> None:
    """1.5.0: the public mirror has exactly registry / source / warning and rejects the diagnostic."""
    js = schemas.RegistryList.model_json_schema()
    assert set(js["properties"]) == {"registry", "source", "warning"} and js["required"] == ["registry"]
    with pytest.raises(ValidationError):
        schemas.RegistryList.model_validate({"registry": [], "registry_roots_skipped": []})
