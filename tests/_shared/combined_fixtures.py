"""Owner-suite fixtures of the WP-3 combined harness (contract WP-3 section 2.8); imported by the api / worker /
orchestration / qa conftests with ``from combined_fixtures import *``.

* ``combined``: :class:`harness.Combined` = core app (``api_settings`` with ``expose_legacy_api=False``, ``services``)
  + gateway app relaying ``/api/v1`` to ``/core/v1/console`` through an in-process streaming ASGI transport, with a
  ``server`` principal for the gateway and an ``admin`` principal (``combined.tokens``).
* ``core_app`` / ``app``: the core app / the GATEWAY app (``app`` is what the browser talks to; tests that serve
  ``app`` with uvicorn therefore exercise the relay).
* ``core_client``: TestClient over the core app (lifespan entered: the core sweeper runs as before) for core-only
  routes (``/core/v1/owner``, ``/core/v1/console`` with the server/admin token).
* ``client``: TestClient over the gateway with the user bearer routes (``harness.auth()``); ``core_client`` is always
  entered first so the core lifespan is active whenever the gateway is.
"""
from __future__ import annotations

from typing import Any

import pytest

import harness

__all__ = ["combined", "core_app", "app", "core_client", "client"]


@pytest.fixture
def combined(tmp_roots, api_settings, services, registry, host_devices) -> Any:
    return harness.combined_app(tmp_roots, registry, host_devices, core_settings=api_settings, services=services)


@pytest.fixture
def core_app(combined):
    return combined.core_app


@pytest.fixture
def app(combined):
    return combined.gateway_app


@pytest.fixture
def core_client(core_app):
    from fastapi.testclient import TestClient

    with TestClient(core_app) as c:
        yield c


@pytest.fixture
def client(app, core_client):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
