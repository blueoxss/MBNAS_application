"""QA: security negatives against the real FastAPI app (synthetic data, no GPU).

Covers path traversal / symlink escape / placeholder / relative / NUL in ``/protocols/validate``, YAML python tags,
oversized request bodies, absent / wrong tokens (incl. token-in-URL), CORS from a foreign origin, artifact-id guessing
and the absence of secrets from error bodies, the export bundle, server logs and every artifact on disk.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import zipfile
from pathlib import Path

import harness
import pytest
from conftest import codes_of
from harness import ORIGIN, SIGNING_KEY, TOKEN, auth

PATH_KEYS = ("dataset_yaml", "train_root", "search_val_root", "final_eval_root", "output_root")


def _set(proto: dict, key: str, value: str) -> dict:
    out = copy.deepcopy(proto)
    out["console"]["paths"][key] = value
    return out


def _assert_blocked(client, validate, proto: dict, key: str, code: str) -> None:
    r = validate(proto)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is False and body.get("resolved") is None and not body.get("validation_id"), body
    assert any(e["code"] == code and e["field"] == f"console.paths.{key}" for e in body["errors"]), body["errors"]
    # an invalid protocol can never become a run: nothing validated, so POST /runs refuses and no run exists
    run = client.post("/api/v1/runs", headers=auth(), json={"protocol": proto, "protocol_hash": "sha256:" + "0" * 64,
                                                            "idempotency_key": "neg-" + key})
    assert run.status_code in (409, 422), run.text
    assert client.get("/api/v1/runs", headers=auth()).json()["runs"] == []


@pytest.mark.parametrize("key", PATH_KEYS)
@pytest.mark.parametrize("label,make_value,code", [
    ("traversal", lambda root, tmp: f"{root}/ds/../../../etc/passwd", "PATH_TRAVERSAL"),
    ("traversal-dotdot-only", lambda root, tmp: f"{root}/..", "PATH_TRAVERSAL"),
    ("placeholder-ABSOLUTE", lambda root, tmp: "/ABSOLUTE/path/to/data", "PATH_PLACEHOLDER"),
    ("placeholder-angle", lambda root, tmp: f"{root}/<your-dataset>", "PATH_PLACEHOLDER"),
    ("placeholder-YOUR", lambda root, tmp: f"{root}/YOUR_DATASET", "PATH_PLACEHOLDER"),
    ("relative", lambda root, tmp: "datasets/ds/data.yaml", "PATH_NOT_ABSOLUTE"),
    ("relative-dot", lambda root, tmp: "./data.yaml", "PATH_NOT_ABSOLUTE"),
    ("home", lambda root, tmp: "~/data.yaml", "PATH_NOT_ABSOLUTE"),
    ("url", lambda root, tmp: "file:///etc/passwd", "PATH_NOT_ABSOLUTE"),
    ("nul-byte", lambda root, tmp: f"{root}/ds/data.yaml\x00.png", "PATH_PLACEHOLDER"),
    ("outside-allowlist", lambda root, tmp: "/etc", "PATH_OUTSIDE_ALLOWLIST"),
    ("sibling-prefix", lambda root, tmp: f"{root}_evil/ds", "PATH_OUTSIDE_ALLOWLIST"),
])
def test_validate_blocks_unsafe_paths(client, validate, protocol, tmp_roots, key, label, make_value, code):
    value = make_value(str(tmp_roots["data"]), tmp_roots)
    if key == "output_root" and code == "PATH_OUTSIDE_ALLOWLIST" and label == "sibling-prefix":
        value = f"{tmp_roots['runs']}_evil/out"
    if key == "output_root" and label in ("outside-allowlist",):
        value = "/etc"
    _assert_blocked(client, validate, _set(protocol, key, value), key, code)


@pytest.mark.parametrize("key", PATH_KEYS)
def test_empty_path_is_a_contract_422_naming_the_field(validate, protocol, key):
    r = validate(_set(protocol, key, ""))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert any(e["field"] == f"protocol.console.paths.{key}" for e in r.json()["errors"])


@pytest.mark.parametrize("key", ["dataset_yaml", "train_root", "search_val_root", "final_eval_root"])
def test_symlink_escaping_the_allowlist_is_rejected(client, validate, protocol, tmp_roots, tmp_path, key):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("secret")
    real = Path(protocol["console"]["paths"][key])
    target = outside / real.name
    if real.is_dir():
        target.mkdir()
    else:
        target.write_text(real.read_text())
    link = tmp_roots["data"] / f"link_{key}"
    os.symlink(target, link)
    _assert_blocked(client, validate, _set(protocol, key, str(link)), key, "PATH_OUTSIDE_ALLOWLIST")


def test_symlink_chain_and_loop_never_escape_or_hang(client, validate, protocol, tmp_roots, tmp_path):
    hop2 = tmp_path / "hop2"
    os.symlink("/etc", hop2)
    hop1 = tmp_roots["data"] / "hop1"
    os.symlink(hop2, hop1)
    _assert_blocked(client, validate, _set(protocol, "train_root", str(hop1)), "train_root", "PATH_OUTSIDE_ALLOWLIST")
    loop = tmp_roots["data"] / "loop"
    os.symlink(loop, loop)
    body = validate(_set(protocol, "train_root", str(loop))).json()
    assert body["valid"] is False and codes_of(body) & {"PATH_NOT_FOUND", "PATH_OUTSIDE_ALLOWLIST"}


def test_symlinked_image_inside_a_split_cannot_pull_in_outside_files(validate, protocol, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not an image but private")
    link = Path(protocol["console"]["paths"]["train_root"]) / "leak.png"
    os.symlink(secret, link)
    body = validate(protocol).json()
    assert body["valid"] is False and "PATH_OUTSIDE_ALLOWLIST" in codes_of(body), body["errors"]
    assert "private" not in json.dumps(body)


@pytest.mark.parametrize("payload", [
    "!!python/object/apply:os.system ['touch {marker}']\n",
    "train: !!python/object/apply:subprocess.check_output [['touch', '{marker}']]\nval: v\nnames: [x]\n",
    "names: !!python/name:os.getcwd ''\ntrain: t\nval: v\n",
    "!!python/object/new:os.system ['touch {marker}']\n",
])
def test_yaml_python_tags_are_rejected_and_never_executed(validate, protocol, tmp_path, payload):
    marker = tmp_path / "pwned"
    Path(protocol["console"]["paths"]["dataset_yaml"]).write_text(payload.format(marker=marker))
    body = validate(protocol).json()
    assert body["valid"] is False and "DATASET_YAML_INVALID" in codes_of(body), body["errors"]
    assert not marker.exists()


def test_oversized_yaml_is_rejected(validate, protocol):
    Path(protocol["console"]["paths"]["dataset_yaml"]).write_text("names: [x]\ntrain: t\nval: v\n# " + "A" * (1 << 21))
    body = validate(protocol).json()
    assert body["valid"] is False and "DATASET_YAML_INVALID" in codes_of(body)


def test_yaml_download_key_is_ignored_not_executed(validate, protocol, tmp_path):
    marker = tmp_path / "downloaded"
    ypath = Path(protocol["console"]["paths"]["dataset_yaml"])
    ypath.write_text(ypath.read_text() + f"download: |\n  import os; open({str(marker)!r}, 'w').close()\n")
    validate(protocol)
    assert not marker.exists()


# -- request size ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/v1/protocols/validate", "/api/v1/runs"])
def test_oversized_json_body_is_413_with_contract_error(client, api_settings, path):
    limit = api_settings.max_request_body_bytes
    blob = json.dumps({"protocol": {"pad": "x" * (limit + 1024)}, "protocol_hash": "h", "idempotency_key": "k"})
    r = client.post(path, content=blob, headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 413, r.text[:200]
    body = r.json()
    assert body["code"] == "PAYLOAD_TOO_LARGE" and body["request_id"] == r.headers["x-request-id"]
    from mbnas_server.api.contract_check import contract_errors

    assert contract_errors("Error", body) == []


def test_oversized_body_without_content_length_is_413(client, api_settings):
    limit = api_settings.max_request_body_bytes

    def chunks():
        for _ in range(limit // 65536 + 3):
            yield b'{"a": "' + b"y" * 65000 + b'"}'

    r = client.post("/api/v1/protocols/validate", content=chunks(), headers={**auth(), "Content-Type": "application/json"})
    assert r.status_code == 413


def test_oversized_body_is_refused_before_authentication_cost(client, api_settings):
    blob = b"{" + b" " * (api_settings.max_request_body_bytes + 10) + b"}"
    r = client.post("/api/v1/runs", content=blob, headers={"Content-Type": "application/json"})  # no token at all
    assert r.status_code in (401, 413)


def test_body_at_the_limit_boundary_is_still_processed(client, api_settings, protocol):
    ok = client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth())
    assert ok.status_code == 200 and len(json.dumps(protocol)) < api_settings.max_request_body_bytes // 4


# -- tokens ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/v1/health", "/api/v1/registry", "/api/v1/runs", "/api/v1/runs/run-x/snapshot", "/api/v1/runs/run-x/events",
    "/api/v1/runs/run-x/export",
])
def test_token_in_url_or_cookie_is_never_accepted(client, path):
    for extra in (f"?token={TOKEN}", f"?access_token={TOKEN}", f"?api_key={TOKEN}", f"?Authorization=Bearer%20{TOKEN}"):
        assert client.get(path + extra).status_code == 401, path + extra
    assert client.get(path, cookies={"token": TOKEN, "Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert client.get(path, headers={"X-API-Key": TOKEN, "X-Auth-Token": TOKEN}).status_code == 401


@pytest.mark.parametrize("bad", ["", " ", "bearer", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"Bearer {TOKEN.upper()}",
                                 "Bearer " + "A" * 5000, f"Bearer {TOKEN} extra"])
def test_wrong_or_malformed_token_never_succeeds(client, bad):
    r = client.get("/api/v1/health", headers={"Authorization": bad})
    assert r.status_code in (401, 403)
    assert TOKEN not in r.text


def test_wrong_token_never_reaches_artifact_or_export_or_sse(client, protocol, inline_worker):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    art = snap["candidates"][-1]["artifacts"][0]
    for path in (f"/api/v1/runs/{run_id}/artifacts/{art['id']}", f"/api/v1/runs/{run_id}/export",
                 f"/api/v1/runs/{run_id}/events", f"/api/v1/runs/{run_id}/snapshot"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=auth("not-the-token")).status_code == 403, path


# -- CORS -----------------------------------------------------------------------------------------------------
FOREIGN = ["http://evil.example", "https://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "null",
           ORIGIN + ".evil.example", "http://localhost:5173@evil.example"]


@pytest.mark.parametrize("origin", FOREIGN)
def test_foreign_origin_gets_no_cors_grant(client, origin):
    pre = client.options("/api/v1/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "authorization,content-type"})
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    for path in ("/api/v1/health", "/api/v1/runs/run-x/events", "/api/v1/runs/run-x/artifacts/a1"):
        r = client.get(path, headers={"Origin": origin, **auth()})
        assert "access-control-allow-origin" not in r.headers, path
        assert "access-control-allow-credentials" not in r.headers


def test_allowed_origin_is_not_a_wildcard_and_has_no_credentials(client):
    r = client.get("/api/v1/health", headers={"Origin": ORIGIN, **auth()})
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in r.headers


# -- artifact guessing ----------------------------------------------------------------------------------------
def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def test_artifact_id_guessing_yields_404_never_a_file(client, protocol, inline_worker, api_settings, tmp_roots):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    real_ids = {a["id"] for c in snap["candidates"] for a in c["artifacts"]}
    assert real_ids
    guesses = ["art-1", "art-0001", "a1", "1", "0", "art-" + "0" * 12, "ART-1", "gt", "tp", "..%2fsnapshot", "..%2f..%2fdb",
               "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd", "%00", "art-%00", "a" * 400, "*", "%", "..;/..;/etc/passwd",
               "run-other%2Fart-1"]
    guesses += [i[:-1] + ("0" if i[-1] != "0" else "1") for i in sorted(real_ids)[:6]]  # one-character neighbours
    guesses = [g for g in guesses if g not in real_ids]
    leaks = (str(tmp_roots["runs"]), str(tmp_roots["data"]), "/etc/passwd", "root:x:")
    for g in guesses:
        for headers in (auth(), {}):
            r = client.get(f"/api/v1/runs/{run_id}/artifacts/{g}", headers=headers)
            if headers:
                assert r.status_code == 404, (g, r.status_code, r.text[:100])
                assert r.headers["content-type"].startswith("application/json")
                assert not any(s in r.text for s in leaks), (g, r.text)
            else:  # no credentials: never a distinguishable 'exists' answer
                # ids holding a slash never match the route at all (404 before auth, no existence signal)
                assert r.status_code == (404 if ("%2f" in g.lower() or "/" in g) else 401), (g, r.status_code)
    # a forged signature for a guessed id is ACCESS_DENIED, identical to a forged signature for a real one
    fake = client.get(f"/api/v1/runs/{run_id}/artifacts/art-1", params={"exp": 4102444800, "sig": "0" * 64})
    real = client.get(f"/api/v1/runs/{run_id}/artifacts/{sorted(real_ids)[0]}", params={"exp": 4102444800, "sig": "0" * 64})
    assert fake.status_code == real.status_code == 403 and fake.json()["code"] == real.json()["code"] == "ACCESS_DENIED"


def test_error_bodies_never_contain_server_paths_or_secrets(client, protocol, tmp_roots):
    bodies = [client.get("/api/v1/runs/run-nope", headers=auth()).text,
              client.get("/api/v1/runs/run-nope/artifacts/a", headers=auth()).text,
              client.post("/api/v1/runs", json={}, headers=auth()).text,
              client.post("/api/v1/runs", content=b"{not json", headers={**auth(), "Content-Type": "application/json"}).text,
              client.post("/api/v1/protocols/validate", json={"protocol": {"x": 1}}, headers=auth()).text,
              client.get("/api/v1/health", headers=auth("wrong")).text]
    for text in bodies:
        assert TOKEN not in text and SIGNING_KEY not in text
        assert str(tmp_roots["runs"]) not in text and "Traceback" not in text


# -- secrets --------------------------------------------------------------------------------------------------
def test_export_bundle_and_logs_contain_no_secret(client, protocol, inline_worker, api_settings, caplog):
    caplog.set_level(logging.DEBUG)
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    client.get("/api/v1/health", headers=auth("wrong-" + TOKEN))
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    for c in snap["candidates"]:
        for a in c["artifacts"]:
            assert SIGNING_KEY not in json.dumps(a) and TOKEN not in json.dumps(a)
    exp = client.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert exp.status_code == 200
    import io

    with zipfile.ZipFile(io.BytesIO(exp.content)) as z:
        names = z.namelist()
        assert names
        for name in names:
            data = z.read(name)
            assert TOKEN.encode() not in data and SIGNING_KEY.encode() not in data, name
            for env_name in (b"MBNAS_API_TOKEN=", b"MBNAS_ARTIFACT_SIGNING_KEY=",  # + DD-A13 core names
                             b"MBNAS_CORE_API_TOKEN=", b"MBNAS_CORE_ARTIFACT_SIGNING_KEY="):
                assert env_name not in data, name
    assert TOKEN not in caplog.text and SIGNING_KEY not in caplog.text
    out = Path(api_settings.output_root)
    for p in out.rglob("*"):
        if p.is_file() and p.stat().st_size < 5_000_000 and p.suffix not in (".sqlite3", ".png"):
            raw = p.read_bytes()
            assert TOKEN.encode() not in raw and SIGNING_KEY.encode() not in raw, p
    assert os.environ.get("MBNAS_API_TOKEN") is None  # conftest clean_env: no ambient secret leaked in
    assert os.environ.get("MBNAS_CORE_API_TOKEN") is None


SECRET_NAMES = ("MBNAS_API_TOKEN", "MBNAS_ARTIFACT_SIGNING_KEY", "MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY",
                "MBNAS_GW_API_TOKEN", "MBNAS_GW_ARTIFACT_SIGNING_KEY", "MBNAS_GW_CORE_TOKEN", "MBNAS_RUNNER_TOKEN")


def _dotenv_secrets(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip().upper() in SECRET_NAMES and value.strip():
            out[name.strip()] = value.strip().strip("'\"")
    return out


@pytest.mark.host_data
def test_real_env_secrets_are_absent_from_logs_evidence_sources_and_bundles():
    """Greps the output-root logs/evidence, the web evidence + build, the package source trees, scripts, deploy, config
    and docs of this checkout for the REAL values of the deployment env file ($MBNAS_TEST_ENV_FILE)."""
    # WP-1: locations from the MBNAS_TEST_* variables of this run (contract §9); env file $MBNAS_TEST_ENV_FILE or <checkout>/.env
    runs = Path(os.environ["MBNAS_TEST_RUNS_ROOT"])
    repo = Path(__file__).resolve().parents[3]
    env = Path(os.environ.get("MBNAS_TEST_ENV_FILE") or repo / ".env")
    try:
        present = env.is_file()
    except OSError:  # PermissionError when the original host's /root is unreadable
        present = False
    if not present:
        pytest.skip(f"no {env} on this host")
    secrets = _dotenv_secrets(env)
    if not secrets:
        pytest.skip(".env has no secret values")
    needles = {k: v.encode() for k, v in secrets.items() if len(v) >= 16}
    assert needles, "real secrets must be long random strings"
    roots = [runs / "logs", runs / "evidence", runs / "evidence_work", repo / "mbnas_web/evidence",
             repo / "mbnas_web/dist", repo / "mbnas_web/src", repo / "mbnas_web/test-results",
             *(repo / pkg / "src" for pkg in ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner",
                                              "mbnas_server")),
             repo / "docs", repo / "deploy", repo / "scripts", repo / "config"]
    hits: list[str] = []
    scanned = 0
    for root in roots:
        for p in root.rglob("*") if root.is_dir() else []:
            if not p.is_file() or p.is_symlink() or p.stat().st_size > 64_000_000:
                continue
            scanned += 1
            raw = p.read_bytes()
            if p.suffix == ".zip":
                try:
                    with zipfile.ZipFile(p) as z:
                        raw += b"".join(z.read(n) for n in z.namelist())
                except zipfile.BadZipFile:
                    pass
            hits += [f"{name}:{p}" for name, needle in needles.items() if needle in raw]
    assert scanned > 50, f"scan looked at only {scanned} files"
    assert not hits, f"secret values found (names only shown): {hits[:5]}"
