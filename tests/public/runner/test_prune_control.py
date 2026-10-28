"""DD-A66 runner side of the retention prune round trip (``control.prune`` -> delete -> E11 report), unit level: the
runner deletes ONLY the named full-state payload (and its RF-DETR native companion) inside its own work root, after the
digest matched; it refuses everything that would leave ``<work_root>/runs/<run_id>`` (``..``, absolute paths, unsafe
run ids, symlinked files or directories, the artifact store) and deletes nothing then; a file that is gone or holds other
bytes is reported ``already_absent`` and kept; a re-delivered order (same ``request_id``) never deletes again and
resends the journalled report. SYNTHETIC files; the core is a recording fake (not integration evidence: T3-34 in
tests/owner/runner_protocol runs the round trip against the real core app)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from mbnas_contracts.runner_protocol import PruneReport
from mbnas_runner import gpu
from mbnas_runner.client import CoreError, CoreUnavailable
from mbnas_runner.config import RunnerSettings
from mbnas_runner.daemon import RunnerDaemon, execute_prune_order
from mbnas_runner.journal import PruneJournal

RUN = "run-0123456789ab"
REL = "candidates/run-0123456789ab.base.yolo11n/checkpoints/full_e0002.pt"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _order(relpath: str = REL, data: bytes = b"F" * 64, **over: Any) -> dict[str, Any]:
    order = {"request_id": "prq-" + "1" * 24, "checkpoint_id": "ck-0001", "run_id": RUN, "relpath": relpath,
             "sha256": _sha(data), "bytes": len(data), "policy": "last_rung_end_only",
             "reason": "RETENTION_LAST_RUNG_END_ONLY"}
    order.update(over)
    return order


def _tree(work: Path) -> dict[str, Path]:
    ck = work / "runs" / RUN / "candidates" / "run-0123456789ab.base.yolo11n" / "checkpoints"
    ck.mkdir(parents=True)
    files = {"payload": ck / "full_e0002.pt", "sidecar": ck / "full_e0002.pt.sha256.json",
             "native": ck / "native_e0002.ckpt", "newer": ck / "full_e0003.pt", "eval": ck / "eval_ema_e0002.pt"}
    files["payload"].write_bytes(b"F" * 64)
    files["sidecar"].write_text('{"sha256": "x"}')
    files["native"].write_bytes(b"N" * 10)
    files["newer"].write_bytes(b"G" * 64)
    files["eval"].write_bytes(b"E" * 8)
    return files


def test_deletes_only_the_named_payload_and_its_native_companion(tmp_path):
    work = tmp_path / "work"
    files = _tree(work)
    report, refusal = execute_prune_order(work, _order(), runner_id="runner-A")
    assert refusal is None
    PruneReport.model_validate(report)  # the wire shape
    assert report["already_absent"] is False and report["runner_id"] == "runner-A"
    assert report["removed"] == [{"relpath": REL, "bytes": 64, "sha256": _sha(b"F" * 64)},
                                 {"relpath": REL.replace("full_e0002.pt", "native_e0002.ckpt"), "bytes": 10}]
    assert report["bytes_freed"] == 74
    assert not files["payload"].exists() and not files["native"].exists()
    for keep in ("sidecar", "newer", "eval"):  # sidecar, newer full state and eval snapshot are never touched
        assert files[keep].exists(), keep


def test_already_absent_and_other_bytes_are_reported_and_nothing_is_deleted(tmp_path):
    work = tmp_path / "work"
    files = _tree(work)
    files["payload"].unlink()
    report, refusal = execute_prune_order(work, _order(), runner_id="runner-A")
    assert refusal is None and report["already_absent"] is True and report["removed"] == []
    assert report["bytes_freed"] == 0 and files["native"].exists()  # nothing deleted for an absent payload
    PruneReport.model_validate(report)
    files["payload"].write_bytes(b"other bytes, same name")
    report, refusal = execute_prune_order(work, _order(), runner_id="runner-A")
    assert refusal is None and report["already_absent"] is True and "other bytes" in report["detail"]
    assert files["payload"].read_bytes() == b"other bytes, same name" and files["native"].exists()
    gone, _ = execute_prune_order(work, _order(run_id="run-ffffffffffff"), runner_id="runner-A")
    assert gone["already_absent"] is True  # the run directory itself is gone


@pytest.mark.parametrize("over,code", [
    ({"relpath": "../outside.pt"}, "PRUNE_ORDER_INVALID"),
    ({"relpath": "candidates/../../outside.pt"}, "PRUNE_ORDER_INVALID"),
    ({"relpath": "/abs/outside.pt"}, "PRUNE_ORDER_INVALID"),
    ({"run_id": ".."}, "PRUNE_ORDER_INVALID"),
    ({"run_id": "a/b"}, "PRUNE_ORDER_INVALID"),
    ({"sha256": "not-a-digest"}, "PRUNE_ORDER_INVALID"),
    ({"extra": 1}, "PRUNE_ORDER_INVALID"),
])
def test_invalid_orders_are_refused_before_any_file_access(tmp_path, over, code):
    work = tmp_path / "work"
    files = _tree(work)
    outside = tmp_path / "work" / "runs" / "outside.pt"
    outside.write_bytes(b"F" * 64)  # same bytes as the ordered digest: only the checks protect it
    report, refusal = execute_prune_order(work, _order(**over), runner_id="runner-A")
    assert report is None and refusal["code"] == code
    assert outside.exists() and all(p.exists() for p in files.values())


def test_symlinks_and_locations_outside_the_work_root_are_refused(tmp_path):
    work = tmp_path / "work"
    files = _tree(work)
    victim_dir = tmp_path / "elsewhere"
    victim_dir.mkdir()
    victim = victim_dir / "victim.pt"
    victim.write_bytes(b"F" * 64)
    run = work / "runs" / RUN
    (run / "candidates" / "linkdir").symlink_to(victim_dir, target_is_directory=True)
    (run / "candidates" / "linkfile.pt").symlink_to(victim)
    (run / "candidates" / "adir").mkdir()
    cases = [("candidates/linkdir/victim.pt", "PRUNE_OUTSIDE_WORK_ROOT"),
             ("candidates/linkfile.pt", "PRUNE_SYMLINK_REFUSED"),
             ("candidates/adir", "PRUNE_NOT_A_FILE")]
    for rel, code in cases:
        report, refusal = execute_prune_order(work, _order(relpath=rel), runner_id="runner-A")
        assert report is None and refusal["code"] == code, (rel, refusal)
    assert victim.read_bytes() == b"F" * 64 and (run / "candidates" / "linkfile.pt").is_symlink()
    # the artifact store is never pruned by a runner, even when it lies inside the run directory
    store = run / "store"
    (store / "ab").mkdir(parents=True)
    (store / "ab" / "obj").write_bytes(b"F" * 64)
    report, refusal = execute_prune_order(work, _order(relpath="store/ab/obj"), runner_id="runner-A",
                                          store_dir=str(store))
    assert report is None and refusal["code"] == "PRUNE_IN_ARTIFACT_STORE" and (store / "ab" / "obj").exists()
    # a work root whose runs/ directory is a symlink to another disk: the run directory resolves outside -> refused
    other = tmp_path / "work2"
    other.mkdir()
    (other / "runs").symlink_to(work / "runs", target_is_directory=True)
    report, refusal = execute_prune_order(other, _order(), runner_id="runner-A")
    assert report is None and refusal["code"] == "PRUNE_OUTSIDE_WORK_ROOT"
    assert all(p.exists() for p in files.values())


class _FakeClient:
    """Records E11 calls; ``fail`` = list of exceptions raised by the next calls (then 200 acks)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: list[Exception] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, **_: Any) -> tuple[int, Any, Any]:
        self.calls.append((method, path, dict(body or {})))
        if self.fail:
            raise self.fail.pop(0)
        return 200, {"checkpoint_id": path.split("/")[2], "request_id": (body or {}).get("request_id"),
                     "state": "PRUNED", "replayed": len(self.calls) > 1}, {}

    def close(self) -> None:
        pass


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "device_info", lambda index: {"id": "cpu", "index": index, "uuid": None, "name": None,
                                                           "memory_total_mb": None})  # never asks NVML
    settings = RunnerSettings(core_url="http://127.0.0.1:8700", token="t", runner_id="runner-A",
                              work_root=str(tmp_path / "work"), output_roots=[str(tmp_path / "work")],
                              log_dir=str(tmp_path / "logs"), weights_dir=str(tmp_path / "w"))
    logs: list[str] = []
    d = RunnerDaemon(settings, client=_FakeClient(), log=logs.append)
    d.logs = logs  # type: ignore[attr-defined]
    return d


def test_daemon_prune_is_idempotent_by_request_id(daemon, tmp_path):
    files = _tree(Path(daemon.settings.work_root))
    order = _order()
    assert daemon.prune_one(order) == "deleted"
    assert not files["payload"].exists()
    (method, path, body), = daemon.client.calls
    assert (method, path) == ("POST", "/checkpoints/ck-0001/pruned") and body["request_id"] == order["request_id"]
    PruneReport.model_validate(body)
    entry = PruneJournal(daemon.settings.work_root).get(order["request_id"])
    assert entry["report"] == body and entry["response"]["ack"]["state"] == "PRUNED"
    files["payload"].write_bytes(b"F" * 64)  # SYNTHETIC: the same bytes appear again under that name
    assert daemon.prune_one(order) == "deleted"  # re-delivered order: the journalled report is resent ...
    assert files["payload"].exists()  # ... and nothing is deleted a second time
    assert [c[2] for c in daemon.client.calls] == [body, body]


def test_unanswered_reports_are_resent_unchanged_and_refusals_never_reported(daemon):
    files = _tree(Path(daemon.settings.work_root))
    daemon.client.fail = [CoreUnavailable("core down")]
    assert daemon.prune_one(_order()) == "unreported"
    assert not files["payload"].exists()  # deleted; the report waits in the journal
    journal = PruneJournal(daemon.settings.work_root)
    assert [e["report"]["request_id"] for e in journal.unanswered()] == [_order()["request_id"]]
    assert daemon.resend_prune_reports() == 1
    assert journal.unanswered() == [] and daemon.client.calls[0][2] == daemon.client.calls[1][2]
    bad = _order(request_id="prq-" + "2" * 24, relpath="../escape.pt")
    assert daemon.prune_one(bad) == "refused"
    assert len(daemon.client.calls) == 2  # a refused order is never reported as pruned
    assert journal.get(bad["request_id"])["refused"]["code"] == "PRUNE_ORDER_INVALID"
    conflict = _order(request_id="prq-" + "3" * 24, relpath=REL.replace("0002", "0003"), data=b"G" * 64)
    daemon.client.fail = [CoreError(409, "PRUNE_REPORT_CONFLICT", "no")]
    assert daemon.prune_one(conflict) == "deleted"
    assert journal.get(conflict["request_id"])["response"] == {"error": "PRUNE_REPORT_CONFLICT", "status": 409}
    assert journal.unanswered() == []  # a refused report is final (logged), not resent forever


def test_submit_prunes_queues_each_request_once_and_the_worker_drains(daemon):
    files = _tree(Path(daemon.settings.work_root))
    order = _order()
    assert daemon.submit_prunes([order, dict(order), "junk", {"no": "id"}]) == 1  # the duplicate id is skipped
    assert daemon.wait_prunes(30)
    assert not files["payload"].exists()
    assert len(daemon.client.calls) == 1
    assert daemon.submit_prunes(None) == 0 and daemon.submit_prunes([]) == 0
    assert os.listdir(Path(daemon.settings.work_root) / "prunes") == [f"{order['request_id']}.json"]


def test_prune_is_deferred_while_the_store_transfer_of_the_file_is_owed(tmp_path, monkeypatch):
    """A file the runner still owes the artifact store (transfer item pending / failed and retryable) is never deleted:
    the order is deferred (journalled as PRUNE_TRANSFER_PENDING, nothing reported) and carried out once the transfer is
    done - pruning never loses bytes the gateway store has not received yet."""
    from mbnas_runner.journal import AttemptJournal
    from mbnas_runner.transfer import FAILED, OK, transfer_item

    monkeypatch.setattr(gpu, "device_info", lambda index: {"id": "cpu", "index": index, "uuid": None, "name": None,
                                                           "memory_total_mb": None})
    work = tmp_path / "work"
    settings = RunnerSettings(core_url="http://127.0.0.1:8700", token="t", runner_id="runner-A", work_root=str(work),
                              output_roots=[str(work)], log_dir=str(tmp_path / "logs"), weights_dir=str(tmp_path / "w"),
                              artifact_transfer="local", artifact_store_dir=str(tmp_path / "store"))
    d = RunnerDaemon(settings, client=_FakeClient(), log=lambda m: None)
    files = _tree(work)
    j = AttemptJournal.create(work, "job-1", 1)
    item = transfer_item(obj="checkpoint", artifact_id="ck-0001", run_id=RUN, job_id="job-1", attempt=1, relpath=REL,
                         path=files["payload"], sha256=_sha(b"F" * 64), size=64, kind="checkpoint", mime=None,
                         link=True, registered_by="runner-A")
    item.update(state=FAILED, tries=1, code="STORE_UNAVAILABLE")
    entry_path = j.record_transfers("local", [item])
    assert d.prune_one(_order()) == "deferred"
    assert files["payload"].exists() and d.client.calls == []
    assert PruneJournal(work).get(_order()["request_id"])["refused"]["code"] == "PRUNE_TRANSFER_PENDING"
    entry = j.read_transfers(entry_path)
    entry["items"][0]["state"] = OK  # the retry pass delivered it
    j.update_transfers(entry_path, entry)
    assert d.prune_one(_order()) == "deleted"  # the re-delivered order is carried out now
    assert not files["payload"].exists() and len(d.client.calls) == 1
