"""In-process runner simulator for the T3 runner-protocol tests (WP-2 contract section 4, design A3 section 14).

SYNTHETIC test plumbing. Two ways to play the runner against the real core app (``mbnas_core.api.app.create_app``,
driven through a ``fastapi.testclient.TestClient``):

* :class:`RunnerSim` - calls E1-E10 one by one over HTTP and plays the daemon's part in-process: the job's JobSpec is
  derived on the *runner* side (``mbnas_runner.envelope.spec_from_envelope``, generic params for the stub handlers),
  the stub handler runs in this process (``mbnas_runner.child.run_job`` with ``MBNAS_JOB_HANDLERS_MODULE=stub_handlers``)
  and its JSONL lines are turned into protocol calls with the runner's own helpers (``mbnas_runner.reports``:
  checkpoint sha recompute, artifact registration). Every step is a separate call so a test can stop, reorder or
  repeat any of them (lost responses, stale attempts, races).
* :func:`make_daemon` - the real ``mbnas_runner.daemon.RunnerDaemon`` whose ``CoreClient`` sends its requests through
  the TestClient's ASGI transport (no socket); the job child is a real ``python -m mbnas_runner.child`` subprocess with
  the stub handlers (``tests/_shared`` on its PYTHONPATH).

Neither is integration evidence of the HTTP/TLS path; ``scripts/dev/wp2_two_process_smoke.py`` is.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PREFIX = "/core/v1/runner"
TICKET_KEY = "t3-ticket-key-0123456789abcdef0123456789"  # fixed: tickets survive an app re-creation (T3-21)
RUNNER_KINDS = ["BASELINE_TRAIN", "CANDIDATE_TRAIN", "FINAL_RETRAIN", "EVALUATE", "BENCHMARK"]


def runtime_manifest() -> dict[str, Any]:
    """The runner's real runtime manifest (R6), as ``mbnas-runner run`` reports it."""
    from mbnas_runner import reports

    return reports.runtime_manifest()


def H(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Lease:
    """One claim response ``{envelope, ticket, server_time}`` plus what the simulator learned about the attempt."""

    claim: dict[str, Any]
    runner_id: str
    started: dict[str, Any] | None = None
    seq: int = 0
    checkpoint_ids: list[str] = field(default_factory=list)

    @property
    def envelope(self) -> dict[str, Any]:
        return self.claim["envelope"]

    @property
    def job_id(self) -> str:
        return str(self.envelope["job"]["job_id"])

    @property
    def attempt(self) -> int:
        return int(self.envelope["job"]["attempt"])

    @property
    def kind(self) -> str:
        return str(self.envelope["job"]["kind"])

    @property
    def run_id(self) -> str:
        return str(self.envelope["run"]["run_id"])

    @property
    def ticket(self) -> str:
        return str(self.claim["ticket"])

    def fence(self, **extra: Any) -> dict[str, Any]:
        return {"runner_id": self.runner_id, "attempt": self.attempt, "ticket": self.ticket, **extra}


class RunnerSim:
    """See module docstring. ``client`` is the core TestClient, ``token`` this runner's bearer token."""

    def __init__(self, client: Any, token: str, runner_id: str, work_root: Path) -> None:
        self.client = client
        self.token = token
        self.runner_id = runner_id
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)

    # -- raw calls -------------------------------------------------------------------------------------------
    def post(self, path: str, body: dict[str, Any], token: str | None = None) -> Any:
        return self.client.post(PREFIX + path, json=body, headers=H(token or self.token))

    def register(self, **extra: Any) -> dict[str, Any]:
        body = {"runner_id": self.runner_id, "runtime": runtime_manifest(), "kinds": list(RUNNER_KINDS),
                "host": "sim-host", "device": "sim-dev0", "device_uuid": f"GPU-sim-{self.runner_id}",
                "device_name": "SYNTHETIC simulated device", "data_mounts": {}, **extra}
        r = self.post("/register", body)
        assert r.status_code == 200, r.text
        return r.json()

    def claim_raw(self, kinds: list[str] | None = None) -> Any:
        body: dict[str, Any] = {"runner_id": self.runner_id}
        if kinds is not None:
            body["kinds"] = kinds
        return self.post("/claim", body)

    def claim(self, kinds: list[str] | None = None) -> Lease | None:
        r = self.claim_raw(kinds)
        assert r.status_code in (200, 204), r.text
        return Lease(r.json(), self.runner_id) if r.status_code == 200 else None

    def start_raw(self, lease: Lease) -> Any:
        return self.post(f"/jobs/{lease.job_id}/start", lease.fence(device_uuid=f"GPU-sim-{self.runner_id}"))

    def start(self, lease: Lease) -> dict[str, Any]:
        r = self.start_raw(lease)
        assert r.status_code == 200, r.text
        lease.started = r.json()
        return lease.started

    def heartbeat(self, lease: Lease, progress: dict[str, Any] | None = None) -> Any:
        return self.post(f"/jobs/{lease.job_id}/heartbeat", lease.fence(progress=progress or {}, device_sample={}))

    def progress(self, lease: Lease, messages: list[dict[str, Any]]) -> Any:
        r = self.post(f"/jobs/{lease.job_id}/progress", lease.fence(seq_start=lease.seq + 1, messages=messages))
        if r.status_code == 200:
            lease.seq = int(r.json()["accepted_seq"])
        return r

    def checkpoint(self, lease: Lease, report: dict[str, Any]) -> Any:
        return self.post(f"/jobs/{lease.job_id}/checkpoints", lease.fence(**report))

    def artifacts(self, lease: Lease, items: list[dict[str, Any]]) -> Any:
        return self.post(f"/jobs/{lease.job_id}/artifacts", lease.fence(artifacts=items))

    def commit_body(self, lease: Lease, result: Any, *, started_at: str | None = None, finished_at: str | None = None,
                    cancel_observed: bool = False, runner_shutting_down: bool = False,
                    artifact_ids: list[str] | None = None) -> dict[str, Any]:
        from mbnas_contracts.runner_protocol import compute_payload_digest
        from mbnas_contracts.util.time import utcnow_iso

        started_at = started_at or (lease.started or {}).get("started_at") or utcnow_iso()
        finished_at = finished_at or utcnow_iso()
        ids = list(artifact_ids or [])
        digest = compute_payload_digest(result, started_at, finished_at, cancel_observed, ids)
        return lease.fence(result=result.model_dump(mode="json"), started_at=started_at, finished_at=finished_at,
                           cancel_observed=cancel_observed, runner_shutting_down=runner_shutting_down,
                           payload_digest=digest, artifact_ids=ids, device_uuid=f"GPU-sim-{self.runner_id}")

    def result(self, lease: Lease, body: dict[str, Any]) -> Any:
        return self.post(f"/jobs/{lease.job_id}/result", body)

    def ack(self, lease: Lease) -> Any:
        return self.post(f"/jobs/{lease.job_id}/ack", lease.fence())

    def deregister(self) -> Any:
        return self.post("/deregister", {"runner_id": self.runner_id})

    # -- the daemon's part, in-process -----------------------------------------------------------------------
    def run_dir(self, lease: Lease) -> Path:
        from mbnas_runner.envelope import run_dir_for

        return run_dir_for(self.work_root, lease.run_id)

    def stop_path(self, lease: Lease) -> Path:
        from mbnas_runner.envelope import stop_path_for

        return stop_path_for(self.run_dir(lease), lease.job_id)

    def spec(self, lease: Lease, *, native: bool = False) -> Any:
        from mbnas_runner.envelope import spec_from_envelope

        return spec_from_envelope(lease.envelope, self.work_root, native=native)

    def execute_stub(self, lease: Lease) -> tuple[Any, list[dict[str, Any]]]:
        """Run the stub handler of ``lease`` in-process (runner-side spec); returns (JobResult, protocol lines)."""
        from mbnas_contracts.jobs_protocol import Emitter
        from mbnas_contracts.schemas.jobs import ProgressMessage
        from mbnas_runner.child import run_job

        spec = self.spec(lease)
        stop = self.stop_path(lease)
        stop.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        result = run_job(spec, Emitter(lease.job_id, buf, str(stop)))
        lines = [ProgressMessage.parse_line(x).model_dump(mode="json") for x in buf.getvalue().splitlines() if x.strip()]
        return result, lines

    def report_lines(self, lease: Lease, lines: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """Daemon role for child lines: progress -> one E5 batch, checkpoints -> runner sha recompute -> E6 (a rejected
        report becomes a CHECKPOINT_REPORT_REJECTED log message of the batch, design 6.3)."""
        from mbnas_runner import reports

        spec = lease.envelope["job"]["spec"]
        base = self.run_dir(lease)
        out: dict[str, list[Any]] = {"checkpoints": [], "rejected": [], "progress": []}
        batch: list[dict[str, Any]] = []
        for msg in lines:
            if msg["type"] == "checkpoint":
                report, detail = reports.verify_checkpoint(msg["payload"], base, spec.get("candidate_id"),
                                                           spec.get("rung"))
                if report is None:
                    out["rejected"].append(detail)
                    batch.append({"type": "log", "job_id": lease.job_id, "ts": msg["ts"],
                                  "message": f"checkpoint report rejected ({detail['problem']})",
                                  "payload": {"level": "error", "reason_code": "CHECKPOINT_REPORT_REJECTED",
                                              **{k: v for k, v in detail.items() if k != "problem" and v is not None}}})
                    continue
                ck_id = f"ck-sim-{lease.job_id[-8:]}-{lease.attempt}-{len(lease.checkpoint_ids)}"
                r = self.checkpoint(lease, {"checkpoint_id": ck_id, **report})
                assert r.status_code == 200, r.text
                lease.checkpoint_ids.append(ck_id)
                out["checkpoints"].append(r.json())
            elif msg["type"] == "progress" and msg["payload"].get("generation_summary") is None:
                batch.append({k: msg.get(k) for k in ("type", "job_id", "ts", "epoch", "epoch_target_total",
                                                      "global_step", "samples_seen", "learning_rate")}
                             | {"payload": {}})
        if batch:
            r = self.progress(lease, batch)
            assert r.status_code == 200, r.text
            out["progress"].append(r.json())
        return out

    def preregister(self, lease: Lease, result: Any) -> tuple[Any, list[str]]:
        """E7 for the result's evaluation artifacts (runner-side hashing); returns (result with artifact_id, ids)."""
        from mbnas_runner import reports

        outputs = json.loads(json.dumps(result.outputs))
        ev = outputs.get("evaluation") if isinstance(outputs.get("evaluation"), dict) else None
        entries = list((ev or {}).get("artifacts") or [])
        regs = []
        for entry in entries:
            reg, problem = reports.artifact_registration(entry, self.run_dir(lease))
            assert reg is not None, problem
            regs.append((entry, reg))
        ids: list[str] = []
        if regs:
            r = self.artifacts(lease, [reg for _, reg in regs])
            assert r.status_code == 200, r.text
            by_rel = {row["relpath"]: row for row in r.json()}
            for entry, reg in regs:
                entry["artifact_id"] = by_rel[reg["relpath"]]["artifact_id"]
                ids.append(entry["artifact_id"])
        return result.model_copy(update={"outputs": outputs}), ids

    def run_attempt(self, lease: Lease, *, commit: bool = True, **commit_kw: Any) -> dict[str, Any]:
        """start (unless started) -> stub run -> E5/E6 -> E7 -> E8 (-> E9 when 200); returns what happened."""
        if lease.started is None:
            self.start(lease)
        result, lines = self.execute_stub(lease)
        reported = self.report_lines(lease, lines)
        result, ids = self.preregister(lease, result)
        out: dict[str, Any] = {"result": result, "lines": lines, "artifact_ids": ids, **reported}
        if commit:
            body = self.commit_body(lease, result, artifact_ids=ids, **commit_kw)
            r = self.result(lease, body)
            out.update(body=body, response=r)
            if r.status_code == 200:
                assert self.ack(lease).status_code == 204
        return out


def daemon_settings(work_root: Path, token: str, runner_id: str, **overrides: Any):
    """RunnerSettings of a runner whose core is reached through an injected transport (``core_url`` is never dialled)."""
    from mbnas_runner.config import RunnerSettings

    kw: dict[str, Any] = dict(core_url="http://127.0.0.1:8700", token=token, runner_id=runner_id,
                              work_root=str(work_root), output_roots=[str(work_root)],
                              dataset_roots=[str(work_root / "data")], log_dir=str(work_root / "logs"),
                              weights_dir=str(work_root / "weights"), poll_s=0.1, request_backoff_s=0.05,
                              request_retries=3, kill_grace_s=2.0, gpu_clear_timeout_s=0.0)
    kw.update(overrides)
    return RunnerSettings(**kw)


def make_daemon(test_client: Any, settings: Any, **kw: Any):
    """The real RunnerDaemon talking to the core app through ``test_client``'s ASGI transport."""
    from mbnas_runner.client import CoreClient
    from mbnas_runner.daemon import RunnerDaemon

    client = CoreClient(settings, transport=test_client._transport)
    return RunnerDaemon(settings, client=client, log=kw.pop("log", None), **kw)


def write_synthetic_probe(output_root: str | Path, ids: tuple[str, ...] = ("yolo11n",)) -> Path:
    """SYNTHETIC registry probe evidence (``<output_root>/registry/probe_<ts>.json``) marking ``ids`` buildable with
    verified weights, so that the core's claim-time baseline resolution (``runner_api.envelope.baseline_arch``, which
    reads ``registry.load_registry(settings.output_root)``) sees the same AVAILABLE status as the synthetic all-AVAILABLE
    registry the console validated the protocol against (``harness.make_services``). Not probe evidence of this host."""
    path = Path(output_root) / "registry" / "probe_20260101T000000Z.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"created_at": "2026-01-01T00:00:00Z", "synthetic": True, "versions": {},
                                "entries": {i: {"build": {"ok": True}, "weights": {"status": "VERIFIED"}} for i in ids}}))
    return path
