"""DD-26 successor (WP-1 contract §9): a failing vendored mixed pool is a *diagnostic*, never an exception or HTTP 500.

Replaces ``tests/search/test_etri_bridge_permissions.py`` (deleted: the ETRI root, ``MBNAS_ETRI_NAS_ROOT`` and the
unreadable-root case no longer exist after vendoring, DD-A12). What it protected stays: ``probe_mixed_pool`` (backing
``/health`` and the protocol validator) reports ``available=False`` with the exact cause and the full key set when the
operator package fails, ``load_etri_mixed`` raises the documented ``EtriUnavailable`` (not the raw error), an
unreadable vendored file is ``SearchSpaceUnavailable``, and ``GET /api/v1/health`` answers 200 with the cause. CPU only.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from mbnas_core.search.mixed_pool import bridge
from mbnas_core.search.mixed_pool.bridge import EtriUnavailable, SearchSpaceUnavailable, load_etri_mixed, probe_mixed_pool

KEYS = {"available", "variant", "reason", "root", "checked"}


@pytest.fixture
def fresh_bridge(monkeypatch):
    monkeypatch.setattr(bridge, "_LOADED", None)  # as in a fresh process: nothing registered yet
    return bridge


@pytest.fixture
def failing_probe(fresh_bridge, monkeypatch):
    from mbnas_model_runtime import operators

    def boom():
        raise PermissionError(13, "Permission denied", "/opt/site-packages/mbnas_model_runtime/operators")

    monkeypatch.setattr(operators, "probe", boom)
    return boom


def test_probe_reports_unavailable_with_the_cause_instead_of_raising(failing_probe):
    out = probe_mixed_pool()  # must not raise
    assert KEYS <= set(out)
    assert out["available"] is False and out["variant"] == "unavailable" and out["root"] is None
    assert "PermissionError" in out["reason"] and "Permission denied" in out["reason"]


def test_probe_passes_the_operator_reason_through(fresh_bridge, monkeypatch):
    from mbnas_model_runtime import operators

    monkeypatch.setattr(operators, "probe", lambda: {"available": False, "reason": "ultralytics is not installed"})
    out = probe_mixed_pool()
    assert out["available"] is False and out["reason"] == "ultralytics is not installed" and KEYS <= set(out)


def test_probe_root_argument_is_accepted_and_ignored(fresh_bridge):
    out = probe_mixed_pool("/nonexistent/ETRI_NAS")  # old callers still pass a root
    assert out["root"] is None and KEYS <= set(out)


def test_load_raises_the_documented_unavailable_error_not_the_raw_error(fresh_bridge, monkeypatch):
    from mbnas_model_runtime import operators

    def boom():
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(operators, "ensure_registered", boom)
    with pytest.raises(EtriUnavailable) as err:
        load_etri_mixed()
    assert "PermissionError" in str(err.value) and not isinstance(err.value, PermissionError)
    assert bridge._LOADED is None  # a failed load is not cached


def test_unreadable_vendored_file_is_search_space_unavailable(tmp_path: Path):
    locked = tmp_path / "seed_variants.py"
    locked.write_text("X = 1\n")
    module = types.ModuleType("mbnas_core.search.mixed_pool.seed_variants")
    module.__file__ = str(locked)
    locked.chmod(0)
    try:
        try:
            locked.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("this process can read a mode-000 file (root user?)")
        with pytest.raises(SearchSpaceUnavailable) as err:
            bridge._vendored_file(module)
        assert "not readable" in str(err.value) and "PermissionError" in str(err.value)
    finally:
        locked.chmod(0o600)
    no_source = types.ModuleType("compiled_only")
    no_source.__file__ = None
    with pytest.raises(SearchSpaceUnavailable):
        bridge._vendored_file(no_source)


def test_health_answers_200_with_the_cause(failing_probe, tmp_roots, registry, host_devices):
    import harness
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app

    settings = harness.make_settings(tmp_roots)
    with TestClient(create_app(settings, harness.make_services(settings, registry, host_devices))) as c:
        r = c.get("/api/v1/health", headers=harness.auth())
    assert r.status_code == 200, r.text
    space = r.json()["capabilities"]["search_space"]
    # contract 1.5.0: capabilities.search_space is closed without root (owner-only; the pool is vendored, DD-A12)
    assert space["variant"] == "unavailable" and "root" not in space and "PermissionError" in space["reason"]
