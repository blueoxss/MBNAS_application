"""WP-5A artifact store (``mbnas_server.artifacts.store``): DD-A39 layout and meta round trip, SHA-256 re-check on put,
size cap, key-only addressing (path injection), idempotent and concurrent puts, crash leftovers, foreign entries.

Synthetic bytes in ``tmp_path`` only; the crash and multi-process cases run the project interpreter as subprocesses
(no sockets, no GPU)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mbnas_contracts.errors import ApiError
from mbnas_server.artifacts import ArtifactStore, PendingPut, StoredArtifact, StoreError
from mbnas_server.artifacts.store import DEFAULT_MAX_BYTES, META_KEYS, TMP_PREFIX

META = {"run_id": "run-1", "artifact_id": "art-1", "relpath": "candidates/c1/weights/last.pt", "kind": "checkpoint",
        "mime": "application/octet-stream", "registered_by": "runner-a", "job_id": "job-1", "attempt": 2}
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
SUBPROCESS_ENV = {"CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def files(root: Path) -> list[str]:
    """Every regular file or symlink below ``root`` (relative, sorted)."""
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_symlink())


def chunked(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]


class Untouchable:
    """A stream that fails the test if it is read (a no-op put must not consume the request body)."""

    def read(self, n: int = -1) -> bytes:
        raise AssertionError("the stream must not be read")


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "store")


def _env() -> dict[str, str]:
    return {**os.environ, **SUBPROCESS_ENV}


# ------------------------------------------------------------------------------------------------ layout + meta
def test_put_layout_and_meta_round_trip(store: ArtifactStore) -> None:
    data = os.urandom(300_000)
    h = sha(data)
    res = store.put_stream(h, io.BytesIO(data), META)
    assert isinstance(res, StoredArtifact) and res.created and res.sha256 == h and res.bytes == len(data)
    obj = store.root / h[:2] / h
    assert res.path == obj == store.path(h) and obj.read_bytes() == data
    meta_file = store.root / h[:2] / f"{h}.json"
    on_disk = json.loads(meta_file.read_text(encoding="utf-8"))
    assert sorted(on_disk) == sorted(META_KEYS)  # exactly the DD-A39 keys
    assert on_disk == store.meta(h) == res.meta == store.get(h).meta
    assert {k: on_disk[k] for k in META} == META
    assert on_disk["bytes"] == len(data) and on_disk["source"] == "upload" and ISO.fullmatch(on_disk["received_at"])
    assert store.exists(h) and store.verify(h)
    with store.open_stream(h) as fh:
        assert fh.read() == data
    assert [a.sha256 for a in store.iter_all()] == [h]
    assert files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]  # no temp file left
    assert stat.S_IMODE(obj.stat().st_mode) == 0o644 and obj.stat().st_nlink == 1


def test_meta_without_caller_fields_and_local_source(store: ArtifactStore) -> None:
    a, b = b"no meta", b"local copy"
    assert store.put_stream(sha(a), a).meta == {**{k: None for k in META_KEYS}, "bytes": len(a), "source": "upload",
                                                 "received_at": store.meta(sha(a))["received_at"]}
    assert store.put_stream(sha(b), b, {"source": "local", "registered_by": "runner-b"}).meta["source"] == "local"


@pytest.mark.parametrize("kind", ["filelike", "bytes", "bytearray", "memoryview", "list", "generator"])
def test_put_accepts_file_like_bytes_and_chunk_iterables(store: ArtifactStore, kind: str) -> None:
    data = os.urandom(70_000)
    stream = {"filelike": lambda: io.BytesIO(data), "bytes": lambda: data, "bytearray": lambda: bytearray(data),
              "memoryview": lambda: memoryview(data), "list": lambda: list(chunked(data, 7000)),
              "generator": lambda: chunked(data, 999)}[kind]()
    res = store.put_stream(sha(data), stream)
    assert res.created and store.verify(sha(data)) and res.path.read_bytes() == data


def test_unsupported_streams_and_chunks_store_nothing(store: ArtifactStore) -> None:
    async def agen():
        yield b"x"

    with pytest.raises(TypeError, match="begin_put"):
        store.put_stream(sha(b"x"), agen())
    with pytest.raises(TypeError, match="bytes-like"):
        store.put_stream(sha(b"x"), io.StringIO("x"))  # text-mode stream
    with pytest.raises(TypeError):
        store.put_stream(sha(b"x"), 42)
    assert files(store.root) == [] and not store.exists(sha(b"x"))


# ------------------------------------------------------------------------------------------------ digest / size
def test_sha_mismatch_is_load_failed_and_stores_nothing(store: ArtifactStore) -> None:
    data = b"payload" * 1000
    wrong = sha(b"something else")
    with pytest.raises(StoreError) as exc:
        store.put_stream(wrong, chunked(data, 1000), META)
    err = exc.value
    assert isinstance(err, ApiError) and err.code == "LOAD_FAILED" and err.status == 409 and err.location == "store"
    assert str(store.root) not in err.message and wrong in err.message
    assert not store.exists(wrong) and not store.exists(sha(data)) and list(store.iter_all()) == []
    assert files(store.root) == []  # no object, no meta, no temp file


@pytest.mark.parametrize("declared", [10, 6999, 7001, 50_000])
def test_declared_size_must_match_the_received_bytes(store: ArtifactStore, declared: int) -> None:
    data = os.urandom(7000)
    with pytest.raises(StoreError) as exc:
        store.put_stream(sha(data), chunked(data, 500), declared_bytes=declared)
    assert exc.value.code == "LOAD_FAILED" and files(store.root) == []
    with pytest.raises(StoreError) as exc:  # same rule through meta.bytes
        store.put_stream(sha(data), data, {"bytes": declared})
    assert exc.value.code == "LOAD_FAILED" and files(store.root) == []
    with pytest.raises(StoreError) as exc:  # contradicting declarations are a request error
        store.put_stream(sha(data), data, {"bytes": 7000}, declared_bytes=declared)
    assert exc.value.code == "META_INVALID"
    assert store.put_stream(sha(data), data, {"bytes": 7000}, declared_bytes=7000).created


def test_size_cap(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "capped", max_bytes=1000)
    exact = b"a" * 1000
    assert store.put_stream(sha(exact), chunked(exact, 300)).created  # == cap is accepted
    big = b"b" * 1001
    with pytest.raises(StoreError) as exc:
        store.put_stream(sha(big), chunked(big, 300))  # refused while streaming
    assert exc.value.code == "PAYLOAD_TOO_LARGE" and exc.value.status == 413
    with pytest.raises(StoreError) as exc:
        store.put_stream(sha(big), Untouchable(), declared_bytes=1001)  # refused before reading
    assert exc.value.code == "PAYLOAD_TOO_LARGE"
    assert not store.exists(sha(big)) and files(store.root) == [f"{sha(exact)[:2]}/{sha(exact)}",
                                                               f"{sha(exact)[:2]}/{sha(exact)}.json"]
    assert ArtifactStore(tmp_path / "default").max_bytes == DEFAULT_MAX_BYTES == 4 * 1024 ** 3


def test_declared_size_above_free_space_is_store_unavailable(store: ArtifactStore, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=100, used=90, free=10))
    with pytest.raises(StoreError) as exc:
        store.put_stream(sha(b"x" * 11), Untouchable(), declared_bytes=11)
    assert exc.value.code == "STORE_UNAVAILABLE" and exc.value.status == 507 and files(store.root) == []


# ------------------------------------------------------------------------------------------------ keys and paths
BAD_KEYS = [
    "../" + "a" * 61, "/etc/passwd", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "sha256:" + "a" * 64, "a" * 64 + "\n",
    "a" * 63 + "\x00", "aa/" + "a" * 61, "..", "", " " + "a" * 63, "\u0663" * 64, "\uff41" * 64, None, 123, b"a" * 64,
]


@pytest.mark.parametrize("key", BAD_KEYS, ids=range(len(BAD_KEYS)))
def test_only_lowercase_hex_sha256_keys_are_accepted(store: ArtifactStore, tmp_path: Path, key: object) -> None:
    before = files(tmp_path)
    calls = (lambda: store.path(key), lambda: store.exists(key), lambda: store.get(key), lambda: store.meta(key),
             lambda: store.open_stream(key), lambda: store.verify(key), lambda: store.begin_put(key),
             lambda: store.put_stream(key, io.BytesIO(b"x"), META))
    for call in calls:
        with pytest.raises(StoreError) as exc:
            call()
        assert exc.value.code == "SHA256_INVALID" and exc.value.status == 422 and exc.value.location == "store"
        assert (not isinstance(key, str) or not key) or key not in exc.value.message  # never echoed
    assert files(tmp_path) == before and not store.root.exists()  # nothing created anywhere


@pytest.mark.parametrize("meta", [
    {"relpath": "../../etc/passwd"}, {"relpath": "/abs/file.pt"}, {"relpath": "a/../b"}, {"relpath": "C:/x"},
    {"relpath": "a\x00b"}, {"relpath": "..\\x"}, {"relpath": ""}, {"path": "/tmp/x"}, {"received_at": "2020-01-01"},
    {"source": "ftp"}, {"attempt": -1}, {"attempt": True}, {"bytes": "12"}, {"mime": "text/html\r\nX-Evil: 1"},
    {"run_id": "x" * 300}, {"artifact_id": "a\nb"}, {"kind": 5}, "not-a-mapping", ["run_id"],
], ids=range(19))
def test_meta_is_validated(store: ArtifactStore, meta: object) -> None:
    with pytest.raises(StoreError) as exc:
        store.put_stream(sha(b"m"), b"m", meta)  # type: ignore[arg-type]
    assert exc.value.code == "META_INVALID" and exc.value.status == 422
    assert files(store.root) == []


def test_relpath_is_data_never_a_location(store: ArtifactStore, tmp_path: Path) -> None:
    data = b"weights"
    res = store.put_stream(sha(data), data, {"relpath": "deep/dir/with spaces/last.pt"})
    assert res.path == store.root / sha(data)[:2] / sha(data) and res.meta["relpath"] == "deep/dir/with spaces/last.pt"
    assert not (store.root / "deep").exists() and files(tmp_path) == [f"store/{sha(data)[:2]}/{sha(data)}",
                                                                      f"store/{sha(data)[:2]}/{sha(data)}.json"]


def test_store_root_must_be_absolute(tmp_path: Path) -> None:
    for bad in ("relative/store", "store", b"/abs/bytes"):
        with pytest.raises(ValueError):
            ArtifactStore(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path, max_bytes=0)


# ------------------------------------------------------------------------------------------------ idempotency
def test_reput_of_a_stored_digest_is_a_no_op(store: ArtifactStore) -> None:
    data = b"same bytes" * 500
    h = sha(data)
    first = store.put_stream(h, io.BytesIO(data), META)
    st1, meta_text = first.path.stat(), (store.root / h[:2] / f"{h}.json").read_text()
    again = store.put_stream(h, Untouchable(), {**META, "run_id": "run-2", "artifact_id": "art-2"})
    assert again.created is False and again.bytes == len(data) and again.meta == first.meta  # the first meta wins
    st2 = again.path.stat()
    assert (st2.st_ino, st2.st_mtime_ns) == (st1.st_ino, st1.st_mtime_ns)
    assert (store.root / h[:2] / f"{h}.json").read_text() == meta_text
    assert store.put_stream(h, Untouchable(), declared_bytes=len(data)).created is False  # same declared size
    assert files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]


def test_reput_writes_a_missing_meta(store: ArtifactStore) -> None:
    data = b"meta lost in a crash"
    h = sha(data)
    store.put_stream(h, data, META)
    (store.root / h[:2] / f"{h}.json").unlink()
    assert store.meta(h) is None and store.get(h).meta is None
    res = store.put_stream(h, Untouchable(), {**META, "artifact_id": "art-repair"})
    assert res.created is False and res.meta["artifact_id"] == "art-repair" == store.meta(h)["artifact_id"]


def test_unreadable_meta_is_load_failed_and_repaired_by_a_reput(store: ArtifactStore) -> None:
    data = b"meta damaged"
    h = sha(data)
    store.put_stream(h, data, META)
    (store.root / h[:2] / f"{h}.json").write_text("{not json")
    with pytest.raises(StoreError) as exc:
        store.meta(h)
    assert exc.value.code == "LOAD_FAILED"
    assert store.get(h).meta is None  # listings stay usable
    assert store.put_stream(h, Untouchable(), META).meta["artifact_id"] == "art-1" == store.meta(h)["artifact_id"]


def test_truncated_object_is_replaced_by_a_verified_put(store: ArtifactStore) -> None:
    data = os.urandom(20_000)
    h = sha(data)
    store.put_stream(h, data, META)
    with open(store.path(h), "r+b") as fh:  # damage outside the store API (e.g. a non-atomic copier)
        fh.truncate(5000)
    assert store.exists(h) and not store.verify(h)
    with pytest.raises(StoreError) as exc:  # a wrong declared size is a client error; the object is left alone
        store.put_stream(h, data, declared_bytes=len(data) + 1)
    assert exc.value.code == "LOAD_FAILED" and store.path(h).stat().st_size == 5000
    res = store.put_stream(h, chunked(data, 4096), declared_bytes=len(data))
    assert res.created is True and store.verify(h) and res.path.stat().st_size == len(data)
    assert res.meta == store.meta(h) and res.meta["artifact_id"] == "art-1"  # meta kept


def test_begin_put_incremental_writer(store: ArtifactStore) -> None:
    data = os.urandom(10_000)
    h = sha(data)
    pending = store.begin_put(h, META, declared_bytes=len(data))
    assert isinstance(pending, PendingPut) and pending.reused is None
    for part in chunked(data, 999):
        pending.write(part)
    assert pending.received_bytes == len(data)
    res = pending.commit()
    assert res.created and store.verify(h)
    with pytest.raises(RuntimeError):
        pending.write(b"late")
    again = store.begin_put(h, META)
    assert again.reused is not None and again.reused.created is False and again.commit() is again.reused
    with pytest.raises(RuntimeError):
        again.write(b"x")
    other = b"never committed"
    with store.begin_put(sha(other)) as p:  # leaving without commit aborts
        p.write(other)
    assert not store.exists(sha(other))
    with pytest.raises(KeyError):
        with store.begin_put(sha(other)) as p:
            p.write(other[:4])
            raise KeyError("route failed")
    assert files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]


def test_failing_stream_aborts_and_removes_the_temp_file(store: ArtifactStore) -> None:
    def stream():
        yield b"a" * 1000
        raise ConnectionResetError("client went away")

    with pytest.raises(ConnectionResetError):
        store.put_stream(sha(b"a" * 2000), stream())
    assert files(store.root) == []


# ------------------------------------------------------------------------------------------------ concurrency
def test_concurrent_puts_of_one_digest_in_threads(store: ArtifactStore) -> None:
    data = os.urandom(512 * 1024)
    h = sha(data)
    n = 8
    barrier = threading.Barrier(n)
    results: list[StoredArtifact | None] = [None] * n
    errors: list[BaseException] = []

    def stream():
        barrier.wait(timeout=30)  # every writer has its temp file open before anyone streams
        yield from chunked(data, 16 * 1024)

    def worker(i: int) -> None:
        try:
            results[i] = store.put_stream(h, stream(), {**META, "artifact_id": f"art-{i}"})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors and all(r is not None for r in results)
    assert sum(r.created for r in results) == 1  # type: ignore[union-attr]
    stored = store.meta(h)
    assert stored["artifact_id"] in {f"art-{i}" for i in range(n)}
    assert all(r.meta == stored for r in results)  # type: ignore[union-attr]
    assert store.verify(h) and files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]


PUT_SCRIPT = textwrap.dedent("""
    import json, os, sys, time
    from mbnas_server.artifacts.store import ArtifactStore
    root, src, go, digest, ident = sys.argv[1:6]
    store = ArtifactStore(root)
    deadline = time.time() + 30
    while not os.path.exists(go) and time.time() < deadline:
        time.sleep(0.002)
    with open(src, "rb") as fh:
        res = store.put_stream(digest, fh, {"artifact_id": ident, "source": "local"})
    print(json.dumps({"created": res.created, "meta": res.meta}))
""")


def test_concurrent_puts_of_one_digest_in_processes(store: ArtifactStore, tmp_path: Path) -> None:
    data = os.urandom(2 * 1024 * 1024)
    h = sha(data)
    src, go = tmp_path / "src.bin", tmp_path / "go"
    src.write_bytes(data)
    procs = [subprocess.Popen([sys.executable, "-B", "-c", PUT_SCRIPT, str(store.root), str(src), str(go), h, f"p{i}"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
             for i in range(4)]
    time.sleep(0.3)
    go.touch()
    outs = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0, 0, 0], [o[1][-2000:] for o in outs]
    results = [json.loads(o[0].strip().splitlines()[-1]) for o in outs]
    assert sum(r["created"] for r in results) == 1
    assert len({json.dumps(r["meta"], sort_keys=True) for r in results}) == 1
    assert store.verify(h) and files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]


# ------------------------------------------------------------------------------------------------ crash + fsync
KILL_SCRIPT = textwrap.dedent("""
    import sys, time
    from mbnas_server.artifacts.store import ArtifactStore
    root, digest = sys.argv[1:3]
    def stream():
        yield b"z" * (1024 * 1024)
        print("WRITING", flush=True)
        time.sleep(120)
        yield b"z" * (2 * 1024 * 1024)
    ArtifactStore(root).put_stream(digest, stream())
""")


def test_killed_writer_leaves_only_an_ignored_temp_file(store: ArtifactStore) -> None:
    data = b"z" * (3 * 1024 * 1024)
    h = sha(data)
    proc = subprocess.Popen([sys.executable, "-B", "-c", KILL_SCRIPT, str(store.root), h], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=_env())
    try:
        line = proc.stdout.readline()  # type: ignore[union-attr]
        assert line.strip() == "WRITING", proc.stderr.read()  # type: ignore[union-attr]
        os.kill(proc.pid, signal.SIGKILL)  # simulated crash: no cleanup code runs
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()  # type: ignore[union-attr]
        proc.stderr.close()  # type: ignore[union-attr]
    assert proc.returncode == -signal.SIGKILL
    leftovers = [p for p in store.root.rglob("*") if p.is_file()]
    assert len(leftovers) == 1 and leftovers[0].name.startswith(TMP_PREFIX) and leftovers[0].parent.name == h[:2]
    assert leftovers[0].stat().st_size <= 1024 * 1024
    # readers ignore the leftover
    assert not store.exists(h) and store.get(h) is None and list(store.iter_all()) == []
    with pytest.raises(StoreError) as exc:
        store.open_stream(h)
    assert exc.value.code == "ARTIFACT_NOT_READY" and exc.value.status == 404
    with pytest.raises(StoreError) as exc:
        store.meta(h)
    assert exc.value.code == "ARTIFACT_NOT_READY"
    # the put can be repeated while the leftover is still there
    assert store.put_stream(h, chunked(data, 1 << 20)).created and store.verify(h)
    # a fresh temp file may belong to a live writer: kept; an old one is swept
    assert store.sweep_tmp() == []
    old = time.time() - 7200
    os.utime(leftovers[0], (old, old))
    assert store.sweep_tmp() == [f"{h[:2]}/{leftovers[0].name}"]
    assert files(store.root) == [f"{h[:2]}/{h}", f"{h[:2]}/{h}.json"]


def test_put_fsyncs_file_then_directory(store: ArtifactStore, monkeypatch) -> None:
    kinds: list[str] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        kinds.append("d" if stat.S_ISDIR(os.fstat(fd).st_mode) else "f")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    store.put_stream(sha(b"durable"), b"durable", META)
    # new store root + fan-out dir (d d), bytes file then its directory (f d), meta file then its directory (f d)
    assert "".join(kinds) == "ddfdfd"
    kinds.clear()
    store.put_stream(sha(b"second"), b"second", META)  # existing directories: file/dir pairs only
    assert "".join(kinds).endswith("fdfd")


# ------------------------------------------------------------------------------------------------ foreign entries
def test_iter_all_lists_only_layout_objects(store: ArtifactStore, tmp_path: Path) -> None:
    a, b = store.put_stream(sha(b"a"), b"a"), store.put_stream(sha(b"b"), b"b")
    root = store.root
    (root / "uploads").mkdir()
    (root / "uploads" / f"{sha(b'y')}.yaml").write_bytes(b"y")  # model-config uploads share the store root
    (root / ".doctor-probe").write_text("")
    pa = a.sha256[:2]
    (root / pa / "README").write_text("x")
    (root / pa / a.sha256.upper()).write_text("x")
    (root / pa / f"{TMP_PREFIX}{a.sha256}.junk.part").write_text("x")
    (root / pa / f"{sha(b'orphan')}.json").write_text("{}")  # meta without bytes (and in the wrong directory)
    misplaced = sha(b"m")
    wrong_dir = root / ("00" if misplaced[:2] != "00" else "01")
    wrong_dir.mkdir(exist_ok=True)
    (wrong_dir / misplaced).write_bytes(b"m")
    (root / "abc").mkdir()
    (root / "zz").mkdir()
    (tmp_path / "outside.bin").write_bytes(b"s")
    sdir = root / sha(b"s")[:2]
    sdir.mkdir(exist_ok=True)
    os.symlink(tmp_path / "outside.bin", sdir / sha(b"s"))
    assert [x.sha256 for x in store.iter_all()] == sorted([a.sha256, b.sha256])
    assert not store.exists(misplaced) and not store.exists(sha(b"s")) and not store.exists(sha(b"orphan"))


def test_symlinks_and_fifos_are_never_served(store: ArtifactStore, tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_bytes(b"secret")
    h = sha(b"secret")
    (store.root / h[:2]).mkdir(parents=True)
    os.symlink(secret, store.root / h[:2] / h)
    assert not store.exists(h) and store.get(h) is None
    with pytest.raises(StoreError) as exc:
        store.open_stream(h)
    assert exc.value.code == "LOAD_FAILED"
    res = store.put_stream(h, b"secret")  # a verified put replaces the anomaly with a regular file
    assert res.created and not res.path.is_symlink() and store.exists(h) and secret.read_bytes() == b"secret"

    fifo_data = b"fifo"
    hf = sha(fifo_data)
    (store.root / hf[:2]).mkdir(exist_ok=True)
    os.mkfifo(store.root / hf[:2] / hf)
    assert not store.exists(hf)
    with pytest.raises(StoreError) as exc:
        store.open_stream(hf)  # O_NONBLOCK: refused, not blocked
    assert exc.value.code == "LOAD_FAILED"

    other = next(d for d in (b"two", b"three", b"four", b"five") if sha(d)[:2] not in (h[:2], hf[:2]))
    h2 = sha(other)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / h2).write_bytes(other)
    os.symlink(elsewhere, store.root / h2[:2])  # symlinked fan-out directory
    assert not store.exists(h2) and list(x.sha256 for x in store.iter_all()) == [h]
    with pytest.raises(StoreError) as exc:
        store.open_stream(h2)
    assert exc.value.code == "LOAD_FAILED"
    with pytest.raises(StoreError) as exc:
        store.put_stream(h2, other)
    assert exc.value.code == "STORE_UNAVAILABLE" and sorted(os.listdir(elsewhere)) == [h2]


# ------------------------------------------------------------------------------------------------ settings + errors
def test_from_settings(tmp_path: Path, monkeypatch) -> None:
    from mbnas_server.config import GatewaySettings

    s = ArtifactStore.from_settings(GatewaySettings(artifact_store=str(tmp_path / "gw" / "artifacts")))
    assert s.root == tmp_path / "gw" / "artifacts" and s.max_bytes == DEFAULT_MAX_BYTES
    assert ArtifactStore.from_settings(SimpleNamespace(artifact_store=str(tmp_path / "x"), artifact_max_bytes=123)).max_bytes == 123
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    assert ArtifactStore.from_settings(GatewaySettings()).root == tmp_path / "home" / "gateway" / "artifacts"


def test_store_error_is_an_api_error_with_store_location() -> None:
    for code, status in (("ARTIFACT_NOT_READY", 404), ("LOAD_FAILED", 409), ("PAYLOAD_TOO_LARGE", 413),
                         ("SHA256_INVALID", 422), ("META_INVALID", 422), ("STORE_UNAVAILABLE", 507)):
        err = StoreError(code, "m")
        assert isinstance(err, ApiError) and (err.status, err.code, err.location) == (status, code, "store")
