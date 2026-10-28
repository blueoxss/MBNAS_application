"""Path traversal, symlink escape, file-kind enum, size cap, auth, read-only guarantees and secret hygiene."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
import archive_synth as synth

from mbnas_server.archive import views

RUN = "synthetic-day"
BASE = f"/api/v1/archive/runs/{RUN}"
SRC = Path(__file__).resolve().parents[3] / "mbnas_server" / "src" / "mbnas_server" / "archive"

# literal "." / ".." are collapsed by the HTTP client before sending; test_literal_dot_segments_* sends them raw
TRAVERSAL_RUN_IDS = ["%2e%2e", "%2E%2E", "%2e", "..%2f..%2fetc", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "....//",
                     "a%2fb", "a%5cb", "..\\..", "%00", "run%00", ".hidden", "x" * 200, "%E2%80%AE", "a b"]


@pytest.mark.parametrize("rid", TRAVERSAL_RUN_IDS)
@pytest.mark.parametrize("suffix", ["", "/baselines", "/generations", "/candidates", "/pareto", "/files/plot_hypervolume"])
def test_malicious_run_ids_are_rejected(client, rid, suffix):
    r = client.get(f"/api/v1/archive/runs/{rid}{suffix}")
    assert r.status_code in (404, 422), (rid, suffix, r.status_code, r.text[:200])
    assert "root:" not in r.text and "passwd" not in r.text.split('"message"')[0]


@pytest.mark.parametrize("cid", ["%2e%2e", "../../x", "%2e%2e%2f%2e%2e%2fx", ".hidden", "a%2fb", "g000_c0001%00", "x" * 129])
def test_malicious_candidate_ids_are_rejected(client, cid):
    r = client.get(f"{BASE}/candidates/{cid}")
    assert r.status_code in (404, 422), (cid, r.status_code)


def raw_get(app, path: str) -> tuple[int, dict]:
    """Drive the ASGI app with an unnormalised path (HTTP clients collapse ``/./`` and ``/../`` before sending)."""
    import asyncio

    from conftest_common import owner_harness

    TOKEN = owner_harness().TOKEN

    async def run():
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "headers": [(b"authorization", f"Bearer {TOKEN}".encode()), (b"host", b"testserver")],
                 "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http", "root_path": ""}
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, json.loads(body or b"{}")
    return asyncio.run(run())


@pytest.mark.parametrize("path", ["/api/v1/archive/runs/..", "/api/v1/archive/runs/.", "/api/v1/archive/runs/synthetic-day/candidates/..",
                                  "/api/v1/archive/runs/synthetic-day/candidates/.", "/api/v1/archive/runs/../../../etc/passwd"])
def test_literal_dot_segments_reach_the_server_as_bad_ids(make_client, synth_root, path):
    _c, app = make_client([synth_root])
    status, body = raw_get(app, path)
    assert status in (404, 422), (path, status, body)
    if status == 422:
        assert body["code"] == "ARCHIVE_BAD_ID"


@pytest.mark.parametrize("kind", ["../../etc/passwd", "%2e%2e%2fsecret", "final_summary", "final_summary.json", "run.log", "plots%2Fhypervolume.png",
                                  "plot_hypervolume.png", "PLOT_HYPERVOLUME", "", "viz_*", "viz_train/../../x"])
def test_unregistered_file_kinds_are_rejected(client, kind):
    r = client.get(f"{BASE}/files/{kind}")
    assert r.status_code in (404, 422), (kind, r.status_code)
    if r.status_code == 422:
        assert r.json()["code"] in ("ARCHIVE_BAD_KIND", "VALIDATION_FAILED")


def test_registered_kinds_only(client):
    kinds = set(views.FILE_KINDS)
    assert kinds == {"plot_pareto_map50_latency", "plot_final_pareto_val", "plot_hypervolume", "plot_params_vs_map50",
                     "plot_rank_correlation", "viz_train", "viz_val", "final_report_md", "run_log_tail"}
    for k in kinds - {"viz_val"}:
        assert client.get(f"{BASE}/files/{k}").status_code == 200, k


def test_query_values_cannot_reach_the_filesystem(client):
    for q in ("generation=../1", "tier=../../etc", "status=%2e%2e", "limit=1;rm"):
        r = client.get(f"{BASE}/candidates?{q}")
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert r.json()["total"] == 0  # a tier/status string is only compared, never opened


def test_run_directory_symlink_escape_is_not_listed(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_s"
    root.mkdir()
    synth.make_run(root, "inside")
    secret = tmp_path / "elsewhere"
    synth.make_run(secret, "stolen")
    (root / "stolen").symlink_to(secret / "stolen", target_is_directory=True)
    c, _ = make_client([root])
    body = c.get("/api/v1/archive/runs").json()
    assert [r["id"] for r in body["runs"]] == ["inside"] and any("escapes its root" in w for w in body["warnings"])
    assert c.get("/api/v1/archive/runs/stolen").status_code == 404


def test_file_symlinks_leaving_the_run_are_ignored(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_s"
    root.mkdir()
    run = synth.make_run(root, "linky")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"SECRET-PNG")
    (outside / "report.md").write_text("TOP SECRET REPORT")
    (outside / "cfg.json").write_text(json.dumps(synth.make_cfg(["Conv"], ["Detect"])))
    (outside / "fs.json").write_text(json.dumps({"verdict": "LEAKED", "scale": "leak"}))
    for rel, target in (("plots/hypervolume.png", "secret.png"), ("three_seed/final_report.md", "report.md"),
                        ("search/candidates/train_runs/g000_c0001.cfg.json", "cfg.json"), ("final_summary.json", "fs.json")):
        (run / rel).unlink()
        (run / rel).symlink_to(outside / target)
    c, _ = make_client([root])
    r = c.get("/api/v1/archive/runs/linky/files/plot_hypervolume")
    assert r.status_code == 404 and b"SECRET" not in r.content
    r = c.get("/api/v1/archive/runs/linky/files/final_report_md")
    assert r.status_code == 404 and "SECRET" not in r.text
    detail = c.get("/api/v1/archive/runs/linky").json()
    assert detail["verdicts"]["notebook"] is None and detail["scale"] != "leak"
    assert any("symlink leaves the run directory" in w for w in detail["warnings"])
    cand = c.get("/api/v1/archive/runs/linky/candidates/g000_c0001").json()
    assert cand["row"]["has_cfg"] is False  # a cfg link that leaves the run does not count ...
    assert cand["architecture"] is None or cand["architecture"]["source"]["candidate_id"] != "g000_c0001"  # ... and is never read
    assert "LEAKED" not in json.dumps(detail)


def test_symlinked_viz_directory_cannot_leak(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_s"
    root.mkdir()
    run = synth.make_run(root, "vizlink")
    outside = tmp_path / "outside_viz"
    outside.mkdir()
    (outside / "samples_X_val.png").write_bytes(b"SECRET-VIZ")
    (run / "viz_link").mkdir(exist_ok=True)
    os.symlink(outside, run / "viz_val_dir")
    import shutil
    shutil.rmtree(run / "viz")
    os.symlink(outside, run / "viz")
    c, _ = make_client([root])
    assert c.get("/api/v1/archive/runs/vizlink/files/viz_val").status_code == 404
    assert c.get("/api/v1/archive/runs/vizlink/files/viz_train").status_code == 404


def test_file_size_cap_is_413(make_client, tmp_path, monkeypatch):
    root = tmp_path / "yolo_ga_full_s"
    root.mkdir()
    run = synth.make_run(root, "big")
    (run / "plots/hypervolume.png").write_bytes(b"\0" * ((8 << 20) + 1))
    (run / "plots/params_vs_map50.png").write_bytes(b"\0" * (8 << 20))
    c, _ = make_client([root])
    r = c.get("/api/v1/archive/runs/big/files/plot_hypervolume")
    assert r.status_code == 413 and r.json()["code"] == "ARCHIVE_FILE_TOO_LARGE"
    ok = c.get("/api/v1/archive/runs/big/files/plot_params_vs_map50")
    assert ok.status_code == 200 and len(ok.content) == 8 << 20


def test_run_log_tail_is_bounded_and_strips_escapes(make_client, tmp_path):
    root = tmp_path / "yolo_ga_full_s"
    root.mkdir()
    run = synth.make_run(root, "logs")
    (run / "run.log").write_text("".join(f"\x1b[31mline {i}\x1b[0m\n" for i in range(1000)) + "NBCONVERT_EXIT=0\n")
    c, _ = make_client([root])
    r = c.get("/api/v1/archive/runs/logs/files/run_log_tail")
    lines = r.text.splitlines()
    assert len(lines) == 200 and lines[-1] == "NBCONVERT_EXIT=0" and "\x1b" not in r.text and lines[0] == "line 801"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------------------------------------ auth

ENDPOINTS = ["/api/v1/archive/runs", BASE, f"{BASE}/baselines", f"{BASE}/generations", f"{BASE}/generations/0", f"{BASE}/candidates",
             f"{BASE}/candidates/g000_c0001", f"{BASE}/pareto", f"{BASE}/fidelity", f"{BASE}/confirm", f"{BASE}/files/plot_hypervolume",
             f"/api/v1/archive/compare?runs={RUN}", "/api/v1/archive/live"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_archive_route_requires_the_bearer_token(client, path):
    from conftest_common import owner_harness

    auth = owner_harness().auth
    anonymous = client.get(path, headers={"Authorization": ""})
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "AUTH_REQUIRED"
    wrong = client.get(path, headers=auth("wrong-token"))
    assert wrong.status_code == 403 and wrong.json()["code"] == "ACCESS_DENIED"
    assert client.get(path, params={"access_token": "test-token-0123456789"}, headers={"Authorization": ""}).status_code == 401  # no query-string token


@pytest.mark.parametrize("path", ENDPOINTS)
def test_archive_routes_are_get_only(client, path):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, path).status_code == 405, (method, path)


# ------------------------------------------------------------------------------------------------ read-only + secrets

def tree_digest(root: Path) -> dict[str, tuple[int, int, str]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def test_requests_never_modify_the_archive(make_client, synth_root):
    c, _ = make_client([synth_root])
    before = tree_digest(synth_root)
    for path in ENDPOINTS:
        c.get(path)
    c.get(f"{BASE}/generations/2")
    c.get(f"{BASE}/candidates/g001_c0006")
    c.get(f"{BASE}/files/run_log_tail")
    assert tree_digest(synth_root) == before
    assert sorted(p.name for p in synth_root.iterdir()) == [RUN]  # no cache / lock / temp files created next to the runs


WRITE_PATTERNS = [r"open\([^)]*['\"][wax+][b+]?['\"]", r"\.write_(text|bytes)\(", r"os\.(remove|unlink|rename|replace|rmdir|mkdir|makedirs|chmod|chown|symlink|link|truncate)\(",
                  r"shutil\.", r"subprocess", r"os\.system", r"os\.popen", r"\beval\(", r"\bexec\(", r"pickle", r"\.touch\(", r"tempfile", r"sqlite3",
                  r"os\.kill", r"signal\.", r"\.unlink\(", r"\.mkdir\("]


@pytest.mark.parametrize("pattern", WRITE_PATTERNS)
def test_archive_package_has_no_write_or_exec_code(pattern):
    hits = []
    for f in sorted(SRC.glob("*.py")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("#") or '"""' in line:
                continue
            if re.search(pattern, line):
                hits.append(f"{f.name}:{n}: {line.strip()}")
    assert not hits, hits


def test_every_open_in_the_package_is_read_only():
    for f in sorted(SRC.glob("*.py")):
        for m in re.finditer(r"\bopen\(([^)]*)\)", f.read_text()):
            args = m.group(1)
            mode = re.search(r"['\"]([rwabx+t]+)['\"]", args)
            assert mode is None or set(mode.group(1)) <= set("rbt"), (f.name, args)


def test_responses_do_not_contain_the_token_or_env_values(client, make_client, synth_root):
    secrets = ["test-token-0123456789", "test-signing-key-abcdefghijklmnop"]
    for path in ENDPOINTS + [f"{BASE}/candidates/g001_c0005", f"{BASE}/files/final_report_md"]:
        r = client.get(path)
        for s in secrets:
            assert s not in r.text and s not in json.dumps(dict(r.headers)), path
    err = client.get(f"{BASE}/candidates/zzz", headers={"X-Request-ID": "abc"})
    assert all(s not in err.text for s in secrets)


def test_access_log_redaction_applies_to_archive_urls():
    from mbnas_contracts.util.log_redact import redact
    assert "SECRET" not in redact("GET /api/v1/archive/runs/x?access_token=SECRET HTTP/1.1")
