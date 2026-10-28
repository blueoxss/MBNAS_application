"""WP-4C: the value rules of contract 1.5.0 ``x-public-projection.value_rules`` on the seeded world's public responses
(gateway relay and core console; JSON bodies, SSE replays and the public export members).

* scrub: free text (log ``message`` / ``message_detail``) loses this host's name, ``pid=<n>`` and absolute host paths
  but keeps its words (DTO allowlist D13; DD-A48 (b) for the worker's ``<hostname>-gpu-N`` device label);
* ids: worker ids are opaque (never ``hostname:pid``), device UUIDs become core-issued ``device_id`` values and
  ``device_uuid`` is not sent; a runner row keeps its runner id (projection), so that id must have the generated form
  ``runner-<hex>`` - the world registers a runner with the DEFAULT id of ``mbnas_runner.config`` (C06-D1, fix round 1);
* paths: ``checkpoint_path`` / ``architecture_path`` are run-relative or a file name, never absolute host paths;
* urls: artifact ``url`` / ``refresh_url`` are same-origin ``/api/v1/...`` paths;
* labels: the registry source is ``live`` or ``example``, never a file path.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

import projection_world as pw

SIDES = ("gateway", "core-server")
HOST_RE = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(pw.HOST) + r"(?![A-Za-z0-9_])")


def _bodies(world: pw.World, side: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in world.by_side(side):
        if c.status != 200:
            continue
        if "event-stream" in c.content_type:
            out[c.label] = pw.sse_events(c.body)
        elif c.members is not None:
            for name, data in c.members.items():
                if name.endswith(".json"):
                    out[f"{c.label}!{name}"] = json.loads(data)
                elif name.endswith(".jsonl"):
                    out[f"{c.label}!{name}"] = [json.loads(x) for x in data.decode().splitlines() if x.strip()]
        elif "json" in c.content_type:
            out[c.label] = c.json()
    return out


def _walk(node: Any, path: str = "$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
        yield path, node
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("side", SIDES)
def test_free_text_is_scrubbed_but_kept(world: pw.World, side: str) -> None:
    probes = []
    for label, doc in _bodies(world, side).items():
        for where, obj in _walk(doc):
            if obj.get("reason_code") == "SCRUB_PROBE":
                probes.append((f"{label} {where}", obj))
    assert probes, "the scrub probe log event is in the snapshot, the SSE replay and the export"
    abs_dir = str(world.settings.output_root)
    for where, payload in probes:
        for key in ("message", "message_detail"):
            text = payload.get(key) or ""
            assert "checkpoint rejected" in text or "RESUME_BLOCKED" in text, (where, key, text)
            assert not HOST_RE.search(text) and f"pid={pw.SCRUB_PID}" not in text and abs_dir not in text, (where, key, text)


@pytest.mark.parametrize("side", SIDES)
def test_host_name_never_reaches_a_public_body(world: pw.World, side: str) -> None:
    hits = []
    for c in world.by_side(side):
        for loc, blob in pw.capture_blobs(c):
            m = HOST_RE.search(blob.decode("utf-8", "replace"))
            if m:
                hits.append(f"{loc}: ...{blob.decode('utf-8', 'replace')[max(0, m.start() - 60):m.end() + 30]}...")
    assert hits == [], hits[:10]


@pytest.mark.parametrize("side", SIDES)
def test_worker_and_device_identifiers_are_opaque(world: pw.World, side: str) -> None:
    workers = []
    for label, doc in _bodies(world, side).items():
        for where, obj in _walk(doc):
            if {"heartbeat_at", "device", "status"} <= set(obj) and "id" in obj:
                workers.append((f"{label} {where}", obj))
    assert len(workers) >= 4, "snapshot workers + worker.updated events, both sides"
    for where, w in workers:
        assert ":" not in w["id"] and not HOST_RE.search(w["id"]), (where, w["id"])
        assert "device_uuid" not in w, (where, w)
        if w.get("device_id") is not None:
            assert re.fullmatch(r"dev-[0-9a-f]{8,64}", w["device_id"]), (where, w["device_id"])
    ids = {w["id"] for _, w in workers}
    # C06-D1 (fix round 1): the core's opaque owner-worker id (wrk-<hex>) or a runner id of the generated form
    # (^runner-[0-9a-f]{8,}$); before the fix the default runner id was runner-<hostname> and passed the old
    # [A-Za-z0-9._-]+ alternative (HOST_RE's lookbehind does not match a host name after "runner-")
    assert world.runner_id in ids, (world.runner_id, ids)
    assert all(re.fullmatch(r"wrk-[0-9a-f]{8,64}|runner-[0-9a-f]{8,}", i) for i in ids), ids
    runner_ids = {w["runner_id"] for _, w in workers if w.get("runner_id") is not None}
    assert world.runner_id in runner_ids, (world.runner_id, runner_ids)
    assert all(re.fullmatch(r"runner-[0-9a-f]{8,}", i) for i in runner_ids), runner_ids


@pytest.mark.parametrize("side", SIDES)
def test_evaluation_paths_are_never_absolute(world: pw.World, side: str) -> None:
    bad = []
    for label, doc in _bodies(world, side).items():
        for where, obj in _walk(doc):
            for key in ("checkpoint_path", "architecture_path"):
                value = obj.get(key)
                if isinstance(value, str) and value.startswith("/"):
                    bad.append(f"{label} {where}.{key} = {value}")
    assert bad == [], bad[:10]


@pytest.mark.parametrize("side", SIDES)
def test_artifact_urls_are_same_origin_gateway_paths(world: pw.World, side: str) -> None:
    urls = []
    for label, doc in _bodies(world, side).items():
        for where, obj in _walk(doc):
            if "url" in obj and "kind" in obj and ("auth_mode" in obj or "refresh_url" in obj or "state" in obj):
                urls += [(f"{label} {where}.{k}", obj[k]) for k in ("url", "refresh_url") if isinstance(obj.get(k), str)]
    assert urls, "the stub evaluations registered image artifacts"
    bad = [(w, u) for w, u in urls if not u.startswith("/api/v1/")]
    assert bad == [], bad[:10]


@pytest.mark.parametrize("side", SIDES)
def test_registry_source_is_a_label(world: pw.World, side: str) -> None:
    reg = next(c for c in world.by_side(side) if c.label.endswith("GET /registry")).json()
    health = next(c for c in world.by_side(side) if c.label.endswith("GET /health")).json()
    assert reg.get("source") in ("live", "example"), reg.get("source")
    assert (health.get("capabilities") or {}).get("registry_source") in ("live", "example", None)
