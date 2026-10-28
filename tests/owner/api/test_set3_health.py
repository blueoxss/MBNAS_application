"""S3-F1 / S3-F5 on /health: declared-but-absent device targets (available=false + reason) and the search-space
variant are listed in ``capabilities`` (additive fields; Health allows additionalProperties)."""
from __future__ import annotations

from harness import auth

from mbnas_server.api.contract_check import contract_errors

DECLARED_ABSENT = ("jetson-orin-nx", "jetson-orin-nano", "jetson-agx-orin")


def test_health_lists_declared_but_absent_targets(client) -> None:
    resp = client.get("/api/v1/health", headers=auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert contract_errors("Health", body) == []
    targets = {t["id"]: t for t in body["capabilities"]["device_targets"]}
    assert targets["host-gpu-0"]["available"] is True and targets["host-gpu-0"]["name"]
    for t in DECLARED_ABSENT:
        assert targets[t]["available"] is False and targets[t]["reason"], t
    assert [d["id"] for d in body["devices"]] == ["host-gpu-0"]  # live inventory unchanged


def test_health_surfaces_search_space_variant(client) -> None:
    body = client.get("/api/v1/health", headers=auth()).json()
    ss = body["capabilities"]["search_space"]
    assert ss["variant"] == "etri_mixed" and ss["fallback_policy"].startswith("opt-in")


def test_health_marks_host_gpu_unavailable_when_not_in_inventory(api_settings, registry, tmp_roots) -> None:
    import harness
    from fastapi.testclient import TestClient

    # WP-3C: the core no longer serves /api/v1 (expose_legacy_api=False); the same empty-inventory core is reached
    # through the gateway of a combined app (WP-3 contract 2.8)
    combined = harness.combined_app(tmp_roots, registry, [], core_settings=api_settings)
    with TestClient(combined.core_app), TestClient(combined.gateway_app) as c:
        targets = {t["id"]: t for t in c.get("/api/v1/health", headers=auth()).json()["capabilities"]["device_targets"]}
    assert targets["host-gpu-0"]["available"] is False and "inventory" in targets["host-gpu-0"]["reason"]
