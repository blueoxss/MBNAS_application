"""C08 EVAL_1 D1/D2 (DD-A66): the three hashes of a StructureReport and the source digest checks, pinned by tests.

* ``source_sha256`` - sha256 hex of the checked YAML BYTES (contract: "sha256 hex of the checked YAML bytes"): a CRLF
  file keeps its CR bytes on BOTH paths, the gateway upload path and the core ``model_path`` path (D1: the latter used to
  hash newline-translated text), and equals what the training ``original`` revision records (``sha256_file``);
* ``yaml_hash`` - the canonical YAML dict: blind to line endings and comments, sensitive to the structure;
* ``architecture_hash`` - the built structure: set after a build, same for LF / CRLF+comment, different when the
  structure changes;
* a carried text whose ``source_sha256`` does not match its bytes is 422 ``SOURCE_SHA256_MISMATCH`` (malformed digest:
  422 ``SOURCE_SHA256_INVALID``).

CPU only (``CUDA_VISIBLE_DEVICES=""``); the structure check builds the tiny YAML in the structure sandbox child."""
from __future__ import annotations

import hashlib
import os

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
pytest.importorskip("torch")
pytest.importorskip("ultralytics")

from mbnas_contracts.util.atomic import sha256_file  # noqa: E402
from mbnas_server.api.contract_check import contract_errors  # noqa: E402

LF = "nc: 1\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\nhead:\n  - [[0], 1, Detect, [nc]]\n"
CRLF_COMMENTED = "# user model - 사용자 모델 (UTF-8 comment)\r\n" + "".join(
    line + ("  # same layer" if i < 2 else "") + "\r\n" for i, line in enumerate(LF.splitlines()))
OTHER_STRUCTURE = LF.replace("[16, 3, 2]", "[32, 3, 2]")


def auth():
    import harness
    return harness.auth()


def _hex(value: str | None) -> str:
    return str(value or "").removeprefix("sha256:")


def _upload_and_check(client, data: bytes, name: str = "user_model.yaml") -> tuple[dict, dict]:
    up = client.post("/api/v1/uploads/model-config", content=data,
                     headers={**auth(), "Content-Type": "application/x-yaml", "X-Filename": name})
    assert up.status_code == 200, up.text
    r = client.post("/api/v1/structure-check", json={"upload_id": up.json()["upload_id"], "input_size": 64},
                    headers=auth())
    assert r.status_code == 200, r.text
    return up.json(), r.json()


def test_three_hashes_follow_bytes_canonical_yaml_and_built_structure(client):
    assert b"\r\n" in CRLF_COMMENTED.encode() and b"\r\n" not in LF.encode()
    docs = {}
    for key, text in (("lf", LF), ("crlf", CRLF_COMMENTED), ("other", OTHER_STRUCTURE)):
        data = text.encode("utf-8")
        upload, doc = _upload_and_check(client, data)
        assert doc["status"] == "PASS", doc.get("diagnostics")
        assert contract_errors("StructureReport", doc) == []
        assert doc["source_sha256"] == hashlib.sha256(data).hexdigest()  # the bytes, unprefixed hex (contract)
        assert _hex(upload["source_sha256"]) == doc["source_sha256"]  # the upload names the same bytes
        assert doc["yaml_hash"] and doc["architecture_hash"]
        assert len({doc["source_sha256"], _hex(doc["yaml_hash"]), _hex(doc["architecture_hash"])}) == 3
        docs[key] = doc
    lf, crlf, other = docs["lf"], docs["crlf"], docs["other"]
    assert lf["source_sha256"] != crlf["source_sha256"]  # different bytes ...
    assert lf["yaml_hash"] == crlf["yaml_hash"]  # ... same canonical YAML (line endings, comments ignored)
    assert lf["architecture_hash"] == crlf["architecture_hash"]  # ... same built structure
    assert other["yaml_hash"] != lf["yaml_hash"] and other["architecture_hash"] != lf["architecture_hash"]


def test_source_sha256_mismatch_and_malformed_digest_are_422(client, core_client, combined):
    data = CRLF_COMMENTED.encode("utf-8")
    good = hashlib.sha256(data).hexdigest()
    lf_sha = hashlib.sha256(CRLF_COMMENTED.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    server = {"Authorization": f"Bearer {combined.tokens['server']}"}
    for url, headers in (("/api/v1/structure-check", auth()), ("/core/v1/console/structure-check", server)):
        api = client if url.startswith("/api/") else core_client
        for digest, code in ((lf_sha, "SOURCE_SHA256_MISMATCH"), ("0" * 64, "SOURCE_SHA256_MISMATCH"),
                             ("sha256:" + lf_sha, "SOURCE_SHA256_MISMATCH"), ("not-a-digest", "SOURCE_SHA256_INVALID")):
            r = api.post(url, json={"model_yaml_text": CRLF_COMMENTED, "source_sha256": digest, "input_size": 64},
                         headers=headers)
            assert r.status_code == 422 and r.json()["code"] == code, (url, digest, r.text)
        ok = api.post(url, json={"model_yaml_text": CRLF_COMMENTED, "source_sha256": "sha256:" + good, "input_size": 64},
                      headers=headers)
        assert ok.status_code == 200 and ok.json()["source_sha256"] == good, (url, ok.text)


def test_model_path_crlf_source_sha256_is_the_file_bytes_sha(client, core_client, combined, tmp_roots):
    """D1: a ``model_path`` read by the core (owner all-in-one path) reports the sha256 of the FILE BYTES, not of the
    newline-translated text - the same digest the gateway path reports for those bytes and the training ``original``
    revision records (``sha256_file``)."""
    path = tmp_roots["runs"] / "crlf_user_model.yaml"
    data = CRLF_COMMENTED.encode("utf-8")
    path.write_bytes(data)
    translated = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()  # universal newlines
    assert translated != hashlib.sha256(data).hexdigest()  # the old (D1) value differs from the file bytes' digest
    server = {"Authorization": f"Bearer {combined.tokens['server']}"}
    r = core_client.post("/core/v1/console/structure-check", json={"model_path": str(path), "input_size": 64},
                         headers=server)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS", doc.get("diagnostics")
    assert contract_errors("StructureReport", doc) == []
    assert doc["source_sha256"] == hashlib.sha256(data).hexdigest() == sha256_file(path)
    assert doc["source_sha256"] != translated
    _, via_gateway = _upload_and_check(client, data)
    assert via_gateway["source_sha256"] == doc["source_sha256"]
    assert via_gateway["yaml_hash"] == doc["yaml_hash"] and via_gateway["architecture_hash"] == doc["architecture_hash"]
