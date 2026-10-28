"""UNIT tests of the runner's ``local`` artifact transfer (WP-5C; DD-A38/DD-A39; ``mbnas_runner.transfer``, its journal
entries and the daemon's use of it against the stdlib fake core ``tests/_shared/fake_core.py``). SYNTHETIC bytes.

Covered: settings (``none`` default, ``local`` needs ``artifact_store_dir``, ``upload`` reserved / not implemented in
0.1.0), hard link vs byte copy (EXDEV fallback, in-place writers always copied, mode bits kept), DD-A39 meta content,
idempotence (an existing identical digest is a no-op, the first meta stays), refusal of mismatching content under an
existing digest, a source changed after registration, journal outbox + bounded retries with backoff, the result
summary, and the daemon: every registered file stored, ``none`` unchanged, a transfer failure never fails the job,
heartbeat-driven retry. Not integration evidence (tests/owner/runner_protocol/test_artifact_transfer.py drives the real
core app)."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from fake_core import FakeCore, envelope

from mbnas_runner.config import RunnerSettings
from mbnas_runner.daemon import SECRET_ENV, RunnerDaemon, _inherited, child_settings_env
from mbnas_runner.journal import AttemptJournal, Stage, prune_closed
from mbnas_runner.transfer import (ABANDONED, DEFAULT_MIME, FAILED, IN_PLACE_ROLES, META_KEYS, OK, REFUSED, TMP_PREFIX,
                                   ArtifactTransfer, LocalArtifactStore, TransferError, transfer_item)

SHARED = Path(__file__).resolve().parents[2] / "_shared"
FAR = 10.0 ** 11  # an injected "now" (year ~5138) after every backoff


# -- helpers ---------------------------------------------------------------------------------------------------------
def _write(path: Path, data: bytes, mode: int | None = None) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mode is not None:
        os.chmod(path, mode)
    return hashlib.sha256(data).hexdigest(), len(data)


def _settings(tmp_path: Path, **kw) -> RunnerSettings:
    base = dict(token="tok-test", runner_id="R1", work_root=str(tmp_path / "work"), artifact_transfer="local",
                artifact_store_dir=str(tmp_path / "store"), heartbeat_s=1)
    base.update(kw)
    return RunnerSettings(**base)


def _item(src: Path, sha: str, size: int, *, aid: str = "art-1", link: bool = True, kind: str = "gt",
          obj: str = "artifact", mime: str | None = "image/png", relpath: str | None = None) -> dict:
    return transfer_item(obj=obj, artifact_id=aid, run_id="run-x", job_id="job-1", attempt=1,
                         relpath=relpath or f"images/{src.name}", path=src, sha256=sha, size=size, kind=kind, mime=mime,
                         link=link, registered_by="R1")


def _journal(s: RunnerSettings, job_id: str = "job-1") -> AttemptJournal:
    j = AttemptJournal.create(s.work_root, job_id, 1)
    j.record_claim({"envelope": envelope(job_id), "ticket": f"tkt-{job_id}", "server_time": "2026-10-27T00:00:00Z"})
    return j


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stray(store: Path) -> list[str]:
    """Temp names left in the store (none may survive a put, failed or not)."""
    return [str(p) for p in store.rglob(".*")] if store.is_dir() else []


# -- settings --------------------------------------------------------------------------------------------------------
def test_settings_default_none_local_requires_store_upload_reserved(tmp_path, monkeypatch):
    s = RunnerSettings(token="t")
    assert (s.artifact_transfer, s.artifact_store_dir, s.artifact_transfer_max_tries) == ("none", None, 6)
    assert s.artifact_store_mode == "same-uid"
    with pytest.raises(ValidationError):
        RunnerSettings(token="t", artifact_store_mode="world-writable")
    assert s.gateway_url is None and s.gateway_upload_token_file is None
    assert ArtifactTransfer(s).enabled is False
    with pytest.raises(ValidationError) as exc:
        RunnerSettings(token="secret-value-123", artifact_transfer="local")
    assert "requires artifact_store_dir" in str(exc.value) and "secret-value-123" not in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        RunnerSettings(token="t", artifact_transfer="upload", artifact_store_dir="/abs/store",
                       gateway_url="https://gw.example:8710")
    assert "not implemented in 0.1.0" in str(exc.value)
    for bad in ({"artifact_transfer": "rsync", "artifact_store_dir": "/abs"},
                {"artifact_transfer": "local", "artifact_store_dir": "relative/store"},
                {"gateway_upload_token_file": "relative/token"}):
        with pytest.raises(ValidationError):
            RunnerSettings(token="t", **bad)
    ok = RunnerSettings(token="t", artifact_transfer=" Local ", artifact_store_dir="/abs/store",
                        gateway_url="https://gw.example:8710", gateway_upload_token_file="/abs/upload.token")
    assert ok.artifact_transfer == "local" and ok.gateway_url == "https://gw.example:8710"  # reserved, accepted, unused
    monkeypatch.setenv("MBNAS_RUNNER_ARTIFACT_TRANSFER", "local")
    monkeypatch.setenv("MBNAS_RUNNER_ARTIFACT_STORE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("MBNAS_RUNNER_ARTIFACT_TRANSFER_MAX_TRIES", "3")
    env = RunnerSettings.load()
    assert (env.artifact_transfer, env.artifact_store_dir, env.artifact_transfer_max_tries) == \
        ("local", str(tmp_path / "s"), 3)


def test_mode_none_writes_nothing(tmp_path):
    s = RunnerSettings(token="t", work_root=str(tmp_path / "work"), artifact_store_dir=str(tmp_path / "store"))
    t = ArtifactTransfer(s)
    src = tmp_path / "work" / "runs" / "run-x" / "a.bin"
    sha, size = _write(src, b"x" * 10)
    j = _journal(s)
    assert t.transfer(j, [_item(src, sha, size)]) == [] and t.summary(j) is None
    assert t.retry_pending(s.work_root) == {"tried": 0, "ok": 0, "failed": 0, "refused": 0, "abandoned": 0, "left": 0}
    assert not (j.root / "transfers").exists() and not (tmp_path / "store").exists()


def test_child_env_forces_transfer_none_and_drops_upload_token(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    child = child_settings_env(s)
    assert child["MBNAS_RUNNER_ARTIFACT_TRANSFER"] == "none"
    # an operator env with transfer=local but the store dir only in the daemon's dotenv: the child still loads settings
    monkeypatch.setenv("MBNAS_RUNNER_ARTIFACT_TRANSFER", "local")
    for k, v in child.items():
        monkeypatch.setenv(k, v)
    assert RunnerSettings.load().artifact_transfer == "none"
    assert "MBNAS_RUNNER_GATEWAY_UPLOAD_TOKEN_FILE" in SECRET_ENV
    assert not _inherited("MBNAS_RUNNER_GATEWAY_UPLOAD_TOKEN_FILE") and _inherited("MBNAS_RUNNER_ARTIFACT_STORE_DIR")


# -- the store ---------------------------------------------------------------------------------------------------------
def test_hard_link_object_meta_and_journal(tmp_path):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    src = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_gt.png"
    sha, size = _write(src, b"\x89PNG synthetic panel" * 50)
    j = _journal(s)
    (out,) = t.transfer(j, [_item(src, sha, size, mime=None, relpath="images/L0_gt.png")])
    assert out["state"] == OK and out["method"] == "link" and out["tries"] == 1
    obj = tmp_path / "store" / sha[:2] / sha
    assert obj.is_file() and os.stat(obj).st_ino == os.stat(src).st_ino and _sha_file(obj) == sha
    meta = json.loads((tmp_path / "store" / sha[:2] / f"{sha}.json").read_text())
    assert tuple(sorted(meta)) == tuple(sorted(META_KEYS))
    assert {k: meta[k] for k in META_KEYS if k != "received_at"} == {
        "run_id": "run-x", "artifact_id": "art-1", "relpath": "images/L0_gt.png", "kind": "gt", "bytes": size,
        "mime": "application/octet-stream", "registered_by": "R1", "job_id": "job-1", "attempt": 1, "source": "local"}
    assert meta["received_at"].endswith("Z")
    assert sorted(p.name for p in obj.parent.iterdir()) == [sha, f"{sha}.json"] and _stray(tmp_path / "store") == []
    ((path, entry),) = list(j.transfers())
    assert path.name == "1.json" and entry["mode"] == "local" and entry["items"][0]["state"] == OK
    assert entry["items"][0]["path"] == str(src)  # the local path stays in the local journal only
    assert t.summary(j) == {"mode": "local", "ok": True, "transferred": 1, "failed": []}
    assert j.stage == Stage.CLAIMED  # transfer entries never move the attempt's stage


def test_copy_fallback_on_exdev_keeps_mode_and_in_place_roles_are_copied(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    calls: list[tuple[str, str]] = []
    real = LocalArtifactStore._link

    def exdev(self, src, dst):
        calls.append((str(src), str(dst)))
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(LocalArtifactStore, "_link", exdev)
    ck = tmp_path / "work" / "runs" / "run-x" / "candidates" / "c" / "full_e001.pt"
    sha, size = _write(ck, os.urandom(3 * (1 << 20) + 17), mode=0o600)  # > one copy chunk
    j = _journal(s)
    (out,) = t.transfer(j, [_item(ck, sha, size, aid="ck-1", obj="checkpoint", kind="checkpoint", mime=None)])
    assert out["state"] == OK and out["method"] == "copy" and out["link_fallback"] == "EXDEV" and len(calls) == 1
    obj = tmp_path / "store" / sha[:2] / sha
    assert os.stat(obj).st_ino != os.stat(ck).st_ino and _sha_file(obj) == sha
    assert stat.S_IMODE(os.stat(obj).st_mode) == 0o600  # the bits a hard link would have shared
    linked: list[str] = []

    def spy(self, src, dst):
        linked.append(str(src))
        return real(self, src, dst)

    monkeypatch.setattr(LocalArtifactStore, "_link", spy)
    yaml = tmp_path / "work" / "runs" / "run-x" / "candidates" / "c" / "architecture.yaml"
    ysha, ysize = _write(yaml, b"nc: 1\nbackbone: []\nhead: []\n")
    assert "execution_configuration" in IN_PLACE_ROLES and "native_image" in IN_PLACE_ROLES
    (out,) = t.transfer(j, [_item(yaml, ysha, ysize, aid="art-yaml", kind="architecture", link=False)])
    assert out["state"] == OK and out["method"] == "copy" and linked == []  # never hard-linked
    yobj = tmp_path / "store" / ysha[:2] / ysha
    assert os.stat(yobj).st_ino != os.stat(yaml).st_ino
    yaml.write_bytes(b"nc: 2\n")  # the writer rewrites it in place: the stored object is not affected
    assert _sha_file(yobj) == ysha and _stray(tmp_path / "store") == []


def test_existing_identical_digest_is_a_noop_and_the_first_meta_stays(tmp_path):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    a = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_gt.png"
    b = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_tp.png"
    sha, size = _write(a, b"same panel bytes")
    _write(b, b"same panel bytes")
    j = _journal(s)
    first, second = t.transfer(j, [_item(a, sha, size, aid="art-a"), _item(b, sha, size, aid="art-b", kind="tp")])
    assert (first["method"], second["method"]) == ("link", "exists") and second["state"] == OK
    obj = tmp_path / "store" / sha[:2] / sha
    meta_path = obj.parent / f"{sha}.json"
    assert os.stat(obj).st_ino == os.stat(a).st_ino  # not replaced by the second registration
    meta_before = meta_path.read_bytes()
    assert json.loads(meta_before)["artifact_id"] == "art-a"
    (again,) = t.transfer(j, [_item(b, sha, size, aid="art-b2")])  # a later re-registration: still a no-op
    assert again["method"] == "exists" and meta_path.read_bytes() == meta_before
    assert sorted(p.name for p in obj.parent.iterdir()) == [sha, f"{sha}.json"]
    meta_path.unlink()  # bytes stored, meta lost (crash between the two): the next registration writes the meta only
    (again,) = t.transfer(j, [_item(b, sha, size, aid="art-b3")])
    assert again["method"] == "exists" and json.loads(meta_path.read_text())["artifact_id"] == "art-b3"
    assert t.summary(j)["transferred"] == 4 and t.summary(j)["ok"] is True


def test_mismatching_content_under_existing_digest_is_refused_and_never_overwritten(tmp_path):
    logs: list[str] = []
    s = _settings(tmp_path)
    t = ArtifactTransfer(s, log=logs.append)
    src = tmp_path / "work" / "runs" / "run-x" / "evaluation.json"
    sha, size = _write(src, b'{"ap": 0.5}')
    obj = tmp_path / "store" / sha[:2] / sha
    _write(obj, b'{"ap": 0.9}')  # corrupted / tampered object under the digest name
    j = _journal(s)
    (out,) = t.transfer(j, [_item(src, sha, size, kind="other", mime="application/json")])
    assert out["state"] == REFUSED and out["code"] == "STORE_CONFLICT" and out["tries"] == 1
    assert obj.read_bytes() == b'{"ap": 0.9}' and not (obj.parent / f"{sha}.json").exists()
    assert os.stat(obj).st_ino != os.stat(src).st_ino
    assert any("refused" in m and "STORE_CONFLICT" in m for m in logs)
    summary = t.summary(j)
    assert summary["ok"] is False and summary["transferred"] == 0
    assert summary["failed"] == [{"artifact_id": "art-1", "object": "artifact", "kind": "other",
                                  "relpath": "images/evaluation.json", "sha256": sha, "code": "STORE_CONFLICT",
                                  "errno": None, "retry": False}]
    assert t.retry_pending(s.work_root, now=FAR)["tried"] == 0  # refused for good: never retried
    assert obj.read_bytes() == b'{"ap": 0.9}'
    # a directory squatting on the object name is a conflict too; an invalid digest is refused before any write
    other = tmp_path / "work" / "runs" / "run-x" / "x.bin"
    osha, osize = _write(other, b"other")
    (tmp_path / "store" / osha[:2] / osha).mkdir(parents=True)
    (out2,) = t.transfer(j, [_item(other, osha, osize, aid="art-2")])
    assert out2["state"] == REFUSED and out2["code"] == "STORE_CONFLICT"
    (out3,) = t.transfer(j, [_item(other, "../../etc/passwd", osize, aid="art-3")])
    assert out3["state"] == REFUSED and out3["code"] == "DIGEST_INVALID"
    elsewhere = tmp_path / "elsewhere-shard"
    elsewhere.mkdir()
    link_sha = hashlib.sha256(b"linked shard").hexdigest()
    (tmp_path / "store" / link_sha[:2]).symlink_to(elsewhere, target_is_directory=True)
    linked_src = tmp_path / "work" / "runs" / "run-x" / "y.bin"
    _write(linked_src, b"linked shard")
    (out5,) = t.transfer(j, [_item(linked_src, link_sha, 12, aid="art-5")])
    assert out5["state"] == FAILED and out5["code"] == "STORE_UNAVAILABLE" and list(elsewhere.iterdir()) == []
    assert _stray(tmp_path / "store") == []


def test_source_changed_or_missing_after_registration_stores_nothing(tmp_path):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    src = tmp_path / "work" / "runs" / "run-x" / "candidates" / "c" / "full_e002.pt"
    sha, size = _write(src, b"A" * 4096)
    j = _journal(s)
    src.write_bytes(b"B" * 4096)  # same size, other bytes: only the re-read of the stored bytes can tell
    (out,) = t.transfer(j, [_item(src, sha, size, aid="ck-2", obj="checkpoint", kind="checkpoint")])
    assert out["state"] == FAILED and out["code"] == "SOURCE_CHANGED" and out["next_try_at"] is not None
    assert not (tmp_path / "store" / sha[:2] / sha).exists() and _stray(tmp_path / "store") == []
    src.write_bytes(b"A" * 10)  # other size: refused before staging
    (out,) = t.transfer(j, [_item(src, sha, size, aid="ck-3", obj="checkpoint", kind="checkpoint")])
    assert out["code"] == "SOURCE_CHANGED"
    src.unlink()
    (out,) = t.transfer(j, [_item(src, sha, size, aid="ck-4", obj="checkpoint", kind="checkpoint")])
    assert out["state"] == FAILED and out["code"] == "SOURCE_MISSING"
    link_target = tmp_path / "elsewhere.bin"
    _write(link_target, b"A" * 4096)
    src.symlink_to(link_target)  # right bytes behind a symlink: never stored (lstat sees no regular file)
    (out,) = t.transfer(j, [_item(src, sha, size, aid="ck-5", obj="checkpoint", kind="checkpoint")])
    assert out["state"] == FAILED and out["code"] == "SOURCE_MISSING"
    assert not (tmp_path / "store" / sha[:2] / sha).exists()
    src.unlink()
    src.write_bytes(b"A" * 4096)  # the registered bytes are back: the retry pass stores every item still retryable
    counts = t.retry_pending(s.work_root, now=FAR)
    assert counts["tried"] == 4 and counts["ok"] == 4 and counts["left"] == 0
    assert _sha_file(tmp_path / "store" / sha[:2] / sha) == sha
    assert t.summary(j) == {"mode": "local", "ok": True, "transferred": 4, "failed": []}


def test_failures_are_retried_with_backoff_and_bounded(tmp_path):
    logs: list[str] = []
    s = _settings(tmp_path, artifact_transfer_max_tries=3)
    t = ArtifactTransfer(s, log=logs.append)
    t.retry_base_s = 10.0
    store = tmp_path / "store"
    store.write_text("a file where the store directory should be")  # shard mkdir fails: STORE_UNAVAILABLE
    src = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_fp.png"
    sha, size = _write(src, b"fp panel")
    j = _journal(s)
    (out,) = t.transfer(j, [_item(src, sha, size, kind="fp")], now=1000.0)
    assert out["state"] == FAILED and out["code"] == "STORE_UNAVAILABLE" and out["errno"] in ("ENOTDIR", "EEXIST")
    assert out["next_try_at"] == 1010.0 and t.pending_hint is True
    assert t.summary(j)["failed"][0]["retry"] is True and t.summary(j)["ok"] is False
    assert t.retry_pending(s.work_root, now=1005.0)["tried"] == 0  # not due yet
    counts = t.retry_pending(s.work_root, now=1010.0)
    assert counts["tried"] == 1 and counts["failed"] == 1 and counts["left"] == 1
    ((_, entry),) = list(j.transfers())
    assert entry["items"][0]["tries"] == 2 and entry["items"][0]["next_try_at"] == 1030.0  # backoff doubled
    counts = t.retry_pending(s.work_root, now=1030.0)
    assert counts["abandoned"] == 1 and counts["left"] == 0 and t.pending_hint is False
    ((_, entry),) = list(j.transfers())
    assert entry["items"][0]["state"] == ABANDONED and entry["items"][0]["tries"] == 3
    assert any("abandoned after 3 tries" in m for m in logs)
    store.unlink()
    store.mkdir()
    assert t.retry_pending(s.work_root, now=FAR)["tried"] == 0  # bounded: an abandoned item is not retried
    assert t.summary(j)["failed"] == [{"artifact_id": "art-1", "object": "artifact", "kind": "fp",
                                       "relpath": "images/L0_fp.png", "sha256": sha, "code": "STORE_UNAVAILABLE",
                                       "errno": out["errno"], "retry": False}]


def test_retry_succeeds_once_the_store_is_back_and_pending_items_left_by_a_crash_are_retried(tmp_path):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    store = tmp_path / "store"
    store.write_text("x")
    src = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_tp.png"
    sha, size = _write(src, b"tp panel")
    j = _journal(s)
    t.transfer(j, [_item(src, sha, size, kind="tp")])
    store.unlink()
    store.mkdir()
    # an entry journalled before a crash that happened before the store write (state pending, 0 tries)
    src2 = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_fn.png"
    sha2, size2 = _write(src2, b"fn panel")
    j2 = _journal(s, "job-2")
    j2.record_transfers("local", [_item(src2, sha2, size2, aid="art-9", kind="fn")])
    t2 = ArtifactTransfer(s)  # a restarted daemon: unknown journal state, the first pass scans everything
    assert t2.pending_hint is True
    counts = t2.retry_pending(s.work_root, now=FAR)
    assert counts["tried"] == 2 and counts["ok"] == 2 and counts["left"] == 0 and t2.pending_hint is False
    for digest in (sha, sha2):
        assert _sha_file(store / digest[:2] / digest) == digest
    assert t2.summary(j)["ok"] is True
    assert t2.summary(j2) == {"mode": "local", "ok": True, "transferred": 1, "failed": []}


def test_prune_closed_keeps_attempts_with_retryable_transfers(tmp_path):
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    (tmp_path / "store").write_text("x")
    src = tmp_path / "work" / "runs" / "run-x" / "a.bin"
    sha, size = _write(src, b"a")
    j = _journal(s)
    t.transfer(j, [_item(src, sha, size)])
    j.record_acked()
    assert j.stage == Stage.CLOSED and j.transfers_open
    assert prune_closed(s.work_root, keep_s=0, now=time.time() + 60) == 0 and j.root.is_dir()
    (tmp_path / "store").unlink()
    t.retry_pending(s.work_root, now=FAR)
    assert not j.transfers_open
    assert prune_closed(s.work_root, keep_s=0, now=time.time() + 60) == 1 and not j.root.exists()


def test_put_rejects_digest_with_wrong_case_and_reports_codes(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    src = tmp_path / "a.bin"
    sha, size = _write(src, b"abc")
    for bad in (sha.upper(), sha + "\n", sha[:63], "../" + sha[3:]):
        with pytest.raises(TransferError) as exc:
            store.put(src, bad, size, {})
        assert exc.value.code == "DIGEST_INVALID" and exc.value.retryable is False
    assert not (tmp_path / "store").exists()  # nothing was created for a refused key
    assert store.put(src, sha, size, {"x": 1}) == "link" and store.put(src, sha, size, {"x": 2}) == "exists"
    assert store.read_meta(sha) == {"x": 1}


def test_store_modes_set_permission_bits(tmp_path, monkeypatch):
    """same-uid keeps the 0600 of a checkpoint on its hard link; link-0644 makes the linked object (and so the run-dir
    file sharing the inode) 0644; copy-0644 copies a file others cannot read as 0644 and leaves the source 0600, but
    still links a world-readable one; a failed link in link-0644 mode copies as 0644."""
    def put(mode: str, data: bytes, src_mode: int, *, exdev: bool = False):
        src = tmp_path / mode / "run" / f"ck{src_mode:o}{int(exdev)}.pt"
        sha, size = _write(src, data, mode=src_mode)
        store = LocalArtifactStore(tmp_path / mode / "store", mode=mode)
        if exdev:
            monkeypatch.setattr(store, "_link", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
        method = store.put(src, sha, size, {"k": 1})
        obj = store.object_path(sha)
        return method, stat.S_IMODE(os.stat(obj).st_mode), stat.S_IMODE(os.stat(src).st_mode), \
            os.stat(obj).st_ino == os.stat(src).st_ino

    assert put("same-uid", b"a" * 64, 0o600) == ("link", 0o600, 0o600, True)
    assert put("link-0644", b"b" * 64, 0o600) == ("link", 0o644, 0o644, True)
    assert put("copy-0644", b"c" * 64, 0o600) == ("copy", 0o644, 0o600, False)
    assert put("copy-0644", b"d" * 64, 0o644) == ("link", 0o644, 0o644, True)
    assert put("link-0644", b"e" * 64, 0o600, exdev=True) == ("copy", 0o644, 0o600, False)
    assert put("same-uid", b"f" * 64, 0o600, exdev=True) == ("copy", 0o600, 0o600, False)
    assert put("same-uid", b"g" * 64, 0o666, exdev=True) == ("copy", 0o644, 0o666, False)  # never group/other-writable
    with pytest.raises(ValueError):
        LocalArtifactStore(tmp_path / "x", mode="chmod-777")
    s = _settings(tmp_path / "cfg", artifact_store_mode="copy-0644")
    assert ArtifactTransfer(s).store.mode == "copy-0644"


def test_temp_names_use_the_store_prefix_and_the_meta_is_exact(tmp_path, monkeypatch):
    """Bytes and meta are staged as ``.tmp-*`` names in the fan-out directory (the gateway's readers skip them and its
    sweeper removes leftovers), published by link and never left behind; the meta holds exactly the DD-A39 keys with
    integer bytes/attempt, a fixed-width UTC received_at and a valid media type (a malformed one becomes the default)."""
    published: list[tuple[str, str]] = []
    real = LocalArtifactStore._publish

    def spy(self, tmp, final):
        published.append((tmp.name, final.name))
        assert tmp.parent == final.parent
        return real(self, tmp, final)

    monkeypatch.setattr(LocalArtifactStore, "_publish", spy)
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    src = tmp_path / "work" / "runs" / "run-x" / "evaluation.json"
    sha, size = _write(src, b'{"ap": 0.25}')
    j = _journal(s)
    (out,) = t.transfer(j, [_item(src, sha, size, kind="other", mime="not a media type")])
    assert out["state"] == OK
    assert [final for _, final in published] == [sha, f"{sha}.json"]
    assert all(tmp.startswith(TMP_PREFIX + sha) and tmp.endswith(".part") for tmp, _ in published)
    assert _stray(tmp_path / "store") == []
    meta = json.loads((tmp_path / "store" / sha[:2] / f"{sha}.json").read_text())
    assert set(meta) == set(META_KEYS) and meta["mime"] == DEFAULT_MIME and meta["source"] == "local"
    assert type(meta["bytes"]) is int and type(meta["attempt"]) is int and meta["bytes"] == size
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", meta["received_at"])
    assert stat.S_IMODE(os.stat(tmp_path / "store" / sha[:2] / f"{sha}.json").st_mode) == 0o644


def test_gateway_store_reads_what_the_runner_wrote(tmp_path):
    """Compatibility with the gateway's store (WP-5A ``mbnas_server.artifacts.store``, read here, never imported by the
    runner): objects the runner linked or copied are found, re-verified and listed by the gateway with the runner's
    meta, which passes the gateway's meta validation; a gateway re-put of the same digest is a no-op that keeps the
    runner's meta, and an object the gateway stored first is a no-op for the runner (the gateway's meta stays)."""
    store_mod = pytest.importorskip("mbnas_server.artifacts.store")
    s = _settings(tmp_path)
    t = ArtifactTransfer(s)
    a = tmp_path / "work" / "runs" / "run-x" / "candidates" / "c" / "full_e001.pt"
    b = tmp_path / "work" / "runs" / "run-x" / "candidates" / "c" / "architecture.yaml"
    sha_a, size_a = _write(a, os.urandom(4096), mode=0o600)
    sha_b, size_b = _write(b, b"nc: 1\n")
    j = _journal(s)
    out = t.transfer(j, [_item(a, sha_a, size_a, aid="ck-1", obj="checkpoint", kind="checkpoint", mime=None),
                         _item(b, sha_b, size_b, aid="art-y", kind="architecture", mime="application/x-yaml",
                               link=False)])
    assert [o["method"] for o in out] == ["link", "copy"]
    gw = store_mod.ArtifactStore(str(tmp_path / "store"))
    assert sorted(o.sha256 for o in gw.iter_all()) == sorted([sha_a, sha_b])
    for sha, aid in ((sha_a, "ck-1"), (sha_b, "art-y")):
        assert gw.exists(sha) and gw.verify(sha)
        meta = gw.meta(sha)
        assert meta["artifact_id"] == aid and meta["source"] == "local"
        store_mod.clean_meta({k: v for k, v in meta.items() if k != "received_at"}, default_source="local")
    assert gw.sweep_tmp(max_age_s=0) == []
    again = gw.put_stream(sha_b, b"nc: 1\n", {"source": "upload", "artifact_id": "art-other"})
    assert again.created is False and gw.meta(sha_b)["artifact_id"] == "art-y"
    c = tmp_path / "work" / "runs" / "run-x" / "images" / "L0_gt.png"
    sha_c, size_c = _write(c, b"panel stored by the gateway first")
    gw.put_stream(sha_c, c.read_bytes(), {"source": "upload", "artifact_id": "art-gw"})
    (out_c,) = t.transfer(j, [_item(c, sha_c, size_c, aid="art-c")])
    assert out_c["state"] == OK and out_c["method"] == "exists" and gw.meta(sha_c)["artifact_id"] == "art-gw"


# -- the daemon (fake core) ------------------------------------------------------------------------------------------
@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "stub_handlers")
    monkeypatch.setenv("PYTHONPATH", str(SHARED))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.05")
    return tmp_path


def rsettings(tmp_path: Path, url: str, **kw) -> RunnerSettings:
    tok = tmp_path / "token"
    tok.write_text("tok-test\n")
    return RunnerSettings(core_url=url, token_file=str(tok), runner_id="runner-A", work_root=str(tmp_path / "work"),
                          request_backoff_s=0.05, poll_s=0.1, kill_grace_s=2, **kw)


def _registered(core: FakeCore) -> list[tuple[str, str]]:
    """(id the fake core answered, sha256) of every E6/E7 registration it accepted."""
    out = [(f"ck-{i + 1}", c["sha256"]) for i, c in enumerate(core.checkpoints)]
    return out + [(f"art-{i + 1}", a["sha256"]) for i, a in enumerate(core.artifacts)]


def test_daemon_local_mode_stores_every_registered_file(env):
    store = env / "store"
    core = FakeCore([envelope(target=3)], heartbeat_s=0.3)
    try:
        assert RunnerDaemon(rsettings(env, core.url, artifact_transfer="local",
                                      artifact_store_dir=str(store))).run(once=True) == 0
    finally:
        core.stop()
    assert not core.invalid and not core.invalid_responses
    regs = _registered(core)
    assert len(core.checkpoints) == 3 and len(core.artifacts) == 4  # 3 epoch checkpoints, 4 evaluation panels
    for aid, sha in regs:
        obj = store / sha[:2] / sha
        assert _sha_file(obj) == sha, aid
        meta = json.loads((obj.parent / f"{sha}.json").read_text())
        assert meta["source"] == "local" and meta["registered_by"] == "runner-A" and meta["run_id"] == "run-x"
        assert meta["job_id"] == "job-1" and meta["attempt"] == 1
        assert meta["artifact_id"] in {i for i, d in regs if d == sha}
    for i, c in enumerate(core.checkpoints):  # atomically written checkpoints are hard-linked
        src = env / "work" / "runs" / "run-x" / c["relpath"]
        assert os.stat(store / c["sha256"][:2] / c["sha256"]).st_ino == os.stat(src).st_ino
        meta = json.loads((store / c["sha256"][:2] / f"{c['sha256']}.json").read_text())
        assert meta["kind"] == "checkpoint" and meta["artifact_id"] == f"ck-{i + 1}" and meta["relpath"] == c["relpath"]
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] == "SUCCEEDED"
    assert body["result"]["outputs"]["artifact_transfer"] == {"mode": "local", "ok": True, "transferred": len(regs),
                                                              "failed": []}
    j = AttemptJournal.create(env / "work", "job-1", 1)
    assert j.stage == Stage.CLOSED and j.result["result"]["outputs"]["artifact_transfer"]["ok"] is True
    states = [it["state"] for _, e in j.transfers() for it in e["items"]]
    assert states == [OK] * len(regs)
    assert _stray(store) == []


def test_daemon_default_none_is_unchanged(env):
    core = FakeCore([envelope(target=2)], heartbeat_s=0.3)
    try:
        assert RunnerDaemon(rsettings(env, core.url)).run(once=True) == 0
    finally:
        core.stop()
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] == "SUCCEEDED" and "artifact_transfer" not in body["result"]["outputs"]
    assert not (AttemptJournal.create(env / "work", "job-1", 1).root / "transfers").exists()


def test_daemon_transfer_failure_never_fails_the_job_and_retry_completes_later(env):
    store = env / "store"
    store.write_text("the gateway store is not there yet")
    core = FakeCore([envelope(target=2)], heartbeat_s=0.3)
    logs: list[str] = []
    try:
        d = RunnerDaemon(rsettings(env, core.url, artifact_transfer="local", artifact_store_dir=str(store),
                                   artifact_transfer_max_tries=50),  # heartbeat-kicked retries never exhaust it here
                         log=logs.append)
        assert d.run(once=True) == 0
    finally:
        core.stop()
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["status"] == "SUCCEEDED"
    at = body["result"]["outputs"]["artifact_transfer"]
    regs = _registered(core)
    assert at["mode"] == "local" and at["ok"] is False and at["transferred"] == 0
    assert sorted(f["artifact_id"] for f in at["failed"]) == sorted(a for a, _ in regs)
    assert {(f["code"], f["retry"]) for f in at["failed"]} == {("STORE_UNAVAILABLE", True)}
    assert str(env) not in json.dumps(at)  # codes, ids, run-relative paths and digests only: no host path is sent
    j = AttemptJournal.create(env / "work", "job-1", 1)
    frozen = j.result
    assert j.stage == Stage.CLOSED and ("job-1", 1) in core.acked
    if d._retry_thread is not None:  # a heartbeat-kicked pass may still be running
        d._retry_thread.join(30)
    store.unlink()
    store.mkdir()
    counts = d.retry_transfers(now=time.time() + 10 ** 6)
    assert counts["ok"] == len(regs) and counts["left"] == 0
    for _, sha in regs:
        assert _sha_file(store / sha[:2] / sha) == sha
    assert j.result == frozen  # result.json stays frozen; the store now holds the bytes
    assert any("artifact transfer failed" in m for m in logs)


def test_heartbeat_kicks_a_background_retry(env, monkeypatch):
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.4")
    store = env / "store"
    store.write_text("not yet")
    core = FakeCore([envelope(target=6)], heartbeat_s=0.2)
    d = RunnerDaemon(rsettings(env, core.url, artifact_transfer="local", artifact_store_dir=str(store)))
    kicks: list[float] = []
    real_kick = d._kick_transfer_retry

    def kick():
        kicks.append(time.monotonic())
        real_kick()

    d._kick_transfer_retry = kick

    first_entry = env / "work" / "journal" / "job-1" / "a1" / "transfers" / "1.json"

    def repair_store():  # once the first checkpoint's transfer is journalled as failed
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if json.loads(first_entry.read_text())["items"][0]["state"] == FAILED:
                    break
            except (OSError, ValueError, KeyError, IndexError):
                pass
            time.sleep(0.02)
        store.unlink()
        store.mkdir()

    threading.Thread(target=repair_store, daemon=True).start()
    try:
        assert d.run(once=True) == 0
    finally:
        core.stop()
    assert len(kicks) >= 2
    if d._retry_thread is not None:
        d._retry_thread.join(30)
    j = AttemptJournal.create(env / "work", "job-1", 1)
    first = [it for _, e in j.transfers() for it in e["items"]][0]
    assert first["object"] == "checkpoint" and first["state"] == OK and first["tries"] >= 2  # retried, then stored
    body = next(iter(core.results.values()))["body"]
    assert body["result"]["outputs"]["artifact_transfer"]["ok"] is True
    for _, sha in _registered(core):
        assert _sha_file(store / sha[:2] / sha) == sha
