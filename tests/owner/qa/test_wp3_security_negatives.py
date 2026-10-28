"""WP-3 negative security tests (SECURITY_BASELINE section 4 items 1-6, 8, 9; WP-3 contract 1.5 / 3).

All requests from the "user" go through the GATEWAY (``client``, combined harness); core-only checks use
``core_client`` with an issued principal. Synthetic YAMLs; CPU only.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

import harness
from harness import auth

ROW_OK = [-1, 1, "Conv", [16, 3, 2]]


def _model(rows, nc=2, head=None):
    return {"nc": nc, "backbone": rows, "head": head or [[-1, 1, "Detect", ["nc"]]]}


def _codes(diags):
    return {d["code"] for d in diags}


# ------------------------------------------------------------------------------------------------ static layer (items 1-4)
@pytest.mark.parametrize("module,args", [("check_requirements", ["evil-pkg==0.0.0"]), ("torch_load", ["/tmp/x.pt"]),
                                         ("check_file", ["http://127.0.0.1:9/x.yaml"]), ("torch.load", ["/tmp/x.pt"]),
                                         ("os.system", ["touch /tmp/pwned"])])
def test_module_outside_allowlist_is_block_unsupported(module, args):
    from mbnas_model_runtime.genotype import validate_declared

    diags = validate_declared(_model([ROW_OK, [-1, 1, module, args]]))
    assert "BLOCK_UNSUPPORTED" in _codes(diags), diags
    hit = [d for d in diags if d["code"] == "BLOCK_UNSUPPORTED"]
    assert any(module in (d.get("cause") or "") or module in str(d.get("path")) for d in hit), hit


def test_nn_linear_huge_dims_refused():
    from mbnas_model_runtime.genotype import validate_declared

    diags = validate_declared(_model([ROW_OK, [-1, 1, "nn.Linear", [10**9, 10**9]]]))
    assert _codes(diags) & {"BLOCK_UNSUPPORTED", "ARG_SCHEMA_INVALID"}, diags


def test_allowlisted_model_passes_static_layer():
    from mbnas_model_runtime.genotype import validate_declared

    assert validate_declared(_model([ROW_OK, [-1, 1, "C3k2", [32, False, 0.25]]])) == []


def _alias_yaml(n: int) -> str:
    lines = ["nc: 2", "x: &a [16, 3, 2]", "backbone:"]
    lines += ["  - [-1, 1, Conv, *a]"] * n
    lines += ["head:", "  - [-1, 1, Detect, [nc]]"]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("n,ok", [(64, True), (65, False)])
def test_alias_limit(n, ok):
    import yaml

    from mbnas_model_runtime.genotype import validate_declared

    text = _alias_yaml(n)
    data = yaml.safe_load(text)
    data.pop("x")
    diags = validate_declared(data, raw_text=text)
    assert (not [d for d in diags if "alias" in (d.get("cause") or "").lower()]) is ok, diags
    if not ok:
        assert "ARG_SCHEMA_INVALID" in _codes(diags)


@pytest.mark.parametrize("rows,ok", [(255, True), (256, False)])
def test_row_limit(rows, ok):
    """``rows`` backbone rows + 1 head row: 256 total passes, 257 is refused."""
    from mbnas_model_runtime.genotype import validate_declared

    diags = validate_declared(_model([ROW_OK] * rows))
    assert (diags == []) is ok, diags[:3]


@pytest.mark.parametrize("channels,ok", [(4096, True), (4097, False)])
def test_channel_limit(channels, ok):
    from mbnas_model_runtime.genotype import validate_declared

    diags = validate_declared(_model([[-1, 1, "Conv", [channels, 3, 2]]]))
    assert (diags == []) is ok, diags


def test_text_over_1mib_refused():
    from mbnas_model_runtime.genotype import validate_declared

    text = "nc: 2\n# " + "x" * (1 << 20) + "\nbackbone: []\nhead: []\n"
    assert "ARG_SCHEMA_INVALID" in _codes(validate_declared({"nc": 2}, raw_text=text))


# ------------------------------------------------------------------------------------------------ API layer: no side effects
@pytest.fixture
def no_network(monkeypatch):
    """Socket-block fixture: any outbound connect attempt in THIS process is recorded and refused."""
    attempts: list[object] = []
    real_connect = socket.socket.connect

    def guarded(self, address):  # noqa: ANN001
        if isinstance(address, tuple):  # every IP connect (the relay is in-process: no socket is legitimate)
            attempts.append(address)
            raise OSError("network blocked by test")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return attempts


@pytest.fixture
def build_spy(monkeypatch, tmp_path):
    """Marker file written if a model build is ever reached in this process - or if the structure sandbox is even
    started (since WP-3 the build happens in the sandbox CHILD, which in-process patches cannot see; a refused YAML must
    be refused before any child starts)."""
    from mbnas_core.structure import sandbox as sbmod
    from mbnas_model_runtime import genotype as gmod

    marker = tmp_path / "BUILD_REACHED"

    def spy(*a, **k):  # noqa: ANN002, ANN003
        marker.write_text("built")
        raise AssertionError("model build reached")

    monkeypatch.setattr(gmod, "inspect_genotype", spy, raising=False)
    monkeypatch.setattr(sbmod.StructureSandbox, "run", spy)
    try:
        from mbnas_core.console import model_services as ms

        monkeypatch.setattr(ms, "check_structure", spy, raising=False)
    except ImportError:
        pass
    return marker


def _upload(client, text: str) -> str:
    r = client.post("/api/v1/uploads/model-config", content=text.encode(),
                    headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": "m.yaml"})
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


def _tree(root) -> list[str]:
    """Every path under the output root (recursive). The upload is taken before the snapshot, so any new path is a side
    effect of the refused check itself. ``db/`` is the core SQLite database (its ``-wal``/``-shm`` sidecars appear on the
    first write of ANY request, e.g. the audit row of the refusal), not check output."""
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.relative_to(root).parts[0] != "db")


BAD_YAML = {"check_requirements": "nc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\n  - [-1, 1, check_requirements, [\"evil==0\"]]\nhead:\n  - [-1, 1, Detect, [nc]]\n",
            "torch_load": "nc: 2\nbackbone:\n  - [-1, 1, torch_load, [\"/tmp/x.pt\"]]\nhead:\n  - [-1, 1, Detect, [nc]]\n",
            "check_file": "nc: 2\nbackbone:\n  - [-1, 1, check_file, [\"http://10.255.255.1/x\"]]\nhead:\n  - [-1, 1, Detect, [nc]]\n",
            "nn.Linear": "nc: 2\nbackbone:\n  - [-1, 1, nn.Linear, [1000000000, 1000000000]]\nhead:\n  - [-1, 1, Detect, [nc]]\n"}


@pytest.mark.parametrize("name", sorted(BAD_YAML))
def test_api_bad_module_fails_before_build_without_side_effects(client, no_network, build_spy, name, tmp_roots):
    upload_id = _upload(client, BAD_YAML[name])  # the upload itself legitimately writes the gateway upload store
    before = _tree(tmp_roots["runs"])
    r = client.post("/api/v1/structure-check", json={"upload_id": upload_id, "input_size": 64},
                    headers=auth())
    text = r.text
    assert r.status_code in (200, 422), text
    if r.status_code == 200:
        assert r.json()["status"] in ("FAIL", "UNVERIFIED"), text
    assert "BLOCK_UNSUPPORTED" in text or "ARG_SCHEMA_INVALID" in text, text
    assert not build_spy.exists(), "model build was reached"
    assert no_network == [], no_network
    after = _tree(tmp_roots["runs"])
    assert after == before  # nothing written into the output root by a refused check


def test_build_spy_trips_on_an_allowed_model(client, build_spy):
    """Negative control for the row above: the same spy DOES fire when an allowlisted YAML reaches the check."""
    good = "nc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [-1, 1, Detect, [nc]]\n"
    r = client.post("/api/v1/structure-check", json={"upload_id": _upload(client, good), "input_size": 64},
                    headers=auth())
    assert build_spy.exists(), (r.status_code, r.text[:200])
    assert r.status_code == 500 and r.json()["location"] == "core"  # the spy's AssertionError, relayed as INTERNAL


def test_api_input_size_over_limit_422(client):
    good = "nc: 2\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [-1, 1, Detect, [nc]]\n"
    r = client.post("/api/v1/structure-check", json={"upload_id": _upload(client, good), "input_size": 1281},
                    headers=auth())
    assert r.status_code == 422, r.text


# ------------------------------------------------------------------------------------------------ item 8: owner scope
def test_gateway_server_token_cannot_reach_owner_routes(combined, core_client):
    server = combined.tokens["server"]
    for path in ("/core/v1/owner/runners", "/core/v1/owner/principals"):
        r = core_client.get(path, headers=auth(server))
        assert r.status_code == 403, (path, r.text)
        assert r.json()["code"] == "ACCESS_DENIED"
        assert core_client.get(path, headers=auth(combined.tokens["admin"])).status_code == 200


def test_user_bearer_is_not_a_core_credential(core_client):
    r = core_client.get("/core/v1/console/runs", headers=auth())
    assert r.status_code in (401, 403)
    assert core_client.get("/api/v1/runs", headers=auth()).status_code == 404  # legacy mount off


# ------------------------------------------------------------------------------------------------ item 9: projection
def test_projection_strips_unknown_keys_for_server_scope():
    """SECURITY_BASELINE 4 item 9. Was xfail(strict) until the WP-4 DTO allowlist landed (DD-A45; WP-3 projection =
    prune(), Run open). Contract 1.5.0: Run is closed, the projection keeps only declared members (``id``, not the
    undeclared ``run_id``), whatever the principal: since WP-4B the admin principal on the console mount gets the same
    projected body; raw owner payloads exist only under /core/v1/owner/* (tests/owner/projection/test_owner_routes.py)."""
    from mbnas_core.console.projection import project_public

    payload = {"id": "r1", "status": "RUNNING", "protocol_hash": "sha256:" + "0" * 64, "run_id": "r1",
               "zz_internal_path": "/srv/secret", "zz_token": "t"}
    out = project_public("Run", dict(payload), "server")
    assert "zz_internal_path" not in out and "zz_token" not in out and "run_id" not in out
    assert out["id"] == "r1" and out["status"] == "RUNNING" and out["protocol_hash"] == payload["protocol_hash"]
    assert project_public("Run", dict(payload), "admin") == out  # the console mount projects every principal
