"""SCRATCH fake core for WP-2C development only (not T3 evidence): stdlib http.server implementing E1-E10 in memory."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mbnas_contracts import runner_protocol as rp
from mbnas_contracts.util.canon import canonical_sha256

REQ = {"/register": rp.RegisterRequest, "/claim": rp.ClaimRequest, "/deregister": rp.DeregisterRequest,
       "start": rp.StartRequest, "heartbeat": rp.HeartbeatRequest, "progress": rp.ProgressBatch,
       "checkpoints": rp.CheckpointReport, "artifacts": rp.ArtifactReport, "result": rp.ResultCommit,
       "ack": rp.AckRequest}
RESP = {"/register": rp.RegisterResponse, "/claim": rp.ClaimResponse, "start": rp.StartResponse,
        "heartbeat": rp.HeartbeatResponse, "progress": rp.ProgressResponse, "checkpoints": rp.CheckpointResponse,
        "artifacts": rp.ArtifactResponse, "result": rp.ResultResponse}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FakeCore:
    def __init__(self, jobs: list[dict[str, Any]], *, lease_ttl_s: float = 30, heartbeat_s: float = 0.3) -> None:
        self.queue = list(jobs)
        self.lease_ttl_s, self.heartbeat_s = lease_ttl_s, heartbeat_s
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.leased: dict[str, dict[str, Any]] = {}
        self.control: dict[str, Any] = {"action": None}
        self.results: dict[tuple[str, int], dict[str, Any]] = {}
        self.acked: set[tuple[str, int]] = set()
        self.checkpoints: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.progress: list[dict[str, Any]] = []
        self.lost: set[str] = set()
        self.fail_result_once = False
        self.lock = threading.Lock()
        self.invalid: list[tuple[str, str]] = []
        self.invalid_responses: list[tuple[str, str]] = []
        core = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                assert self.headers.get("Authorization") == "Bearer tok-test", self.headers.get("Authorization")
                status, out = core.handle(self.path, body)
                data = b"" if out is None else json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _claim(self, env: dict[str, Any], now: datetime) -> dict[str, Any]:
        return {"envelope": {k: v for k, v in env.items() if not k.startswith("_")},
                "ticket": f"tkt-{env['job']['job_id']}", "server_time": iso(now)}

    def stop(self) -> None:
        self.server.shutdown()

    def _err(self, status: int, code: str) -> tuple[int, Any]:
        return status, {"code": code, "message": code, "request_id": "x", "errors": []}

    def handle(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        with self.lock:
            self.calls.append((path, body))
            now = datetime.now(timezone.utc)
            assert path.startswith("/core/v1/runner/"), path
            p = path[len("/core/v1/runner"):]
            key = p if not p.startswith("/jobs/") else p.split("/")[3]
            try:
                REQ[key].model_validate(body)  # the WP-2B wire model must accept every runner request
            except Exception as exc:  # noqa: BLE001
                self.invalid.append((p, str(exc)))
                return self._err(422, "VALIDATION_ERROR")
            status, out = self._handle(p, body, now)
            if status == 200 and key in RESP:
                try:
                    RESP[key].model_validate(out)  # and the fake answers in the wire shape
                except Exception as exc:  # noqa: BLE001 - deliberately malformed fixtures (invalid envelope test)
                    self.invalid_responses.append((p, str(exc)[:200]))
            return status, out

    def _handle(self, p: str, body: dict[str, Any], now: datetime) -> tuple[int, Any]:
        if True:
            if p == "/register":
                return 200, {"runner_id": body["runner_id"], "core_contract": "fake", "envelope_version": 1,
                             "lease_ttl_s": self.lease_ttl_s, "heartbeat_s": self.heartbeat_s,
                             "progress_interval_s": 1.0, "max_batch_messages": 50, "max_body_bytes": 32 << 20,
                             "allowed_kinds": ["BASELINE_TRAIN", "CANDIDATE_TRAIN", "FINAL_RETRAIN", "EVALUATE",
                                               "BENCHMARK"], "artifact_store": "runner_local", "server_time": iso(now)}
            if p == "/claim":
                for env in self.leased.values():
                    if env["_state"] == "LEASED":
                        return 200, self._claim(env, now)
                if not self.queue:
                    return 204, None
                env = self.queue.pop(0)
                env["_state"] = "LEASED"
                env["fence"] = {"runner_id": body["runner_id"], "attempt": env["job"]["attempt"], "claim_id": "clm-1",
                                "issued_at": iso(now)}
                env["job"]["leased_at"] = iso(now)
                env["job"]["lease_expires_at"] = iso(now + timedelta(seconds=self.lease_ttl_s))
                self.leased[env["job"]["job_id"]] = env
                return 200, self._claim(env, now)
            if p == "/deregister":
                return 204, None
            parts = p.split("/")  # '', 'jobs', id, action
            job_id, action = parts[2], parts[3]
            env = self.leased.get(job_id)
            if env is None or body.get("attempt") != env["job"]["attempt"] or body.get("ticket") != f"tkt-{job_id}":
                return self._err(409, "LEASE_LOST")
            if job_id in self.lost and action != "result":
                return self._err(409, "LEASE_LOST")
            exp = iso(now + timedelta(seconds=self.lease_ttl_s))
            if action == "start":
                if env["_state"] != "LEASED":
                    return self._err(409, "LEASE_LOST")
                env["_state"], env["_started_at"] = "RUNNING", iso(now)
                return 200, {"started_at": env["_started_at"], "lease_expires_at": exp, "server_time": iso(now)}
            if action == "heartbeat":
                return 200, {"lease_expires_at": exp, "server_time": iso(now),
                             "control": {**self.control, "run_status": "RUNNING", "stop_requested": bool(self.control.get("action"))}}
            if action == "progress":
                self.progress.append(body)
                return 200, {"accepted_seq": body["seq_start"] + len(body["messages"]) - 1}
            if action == "checkpoints":
                for key in ("sha256", "bytes", "kind", "relpath"):
                    assert key in body, key
                self.checkpoints.append(body)
                return 200, {"checkpoint_id": f"ck-{len(self.checkpoints)}", "location_id": "loc", "state": "PRESENT"}
            if action == "artifacts":
                out = []
                for i, a in enumerate(body["artifacts"]):
                    self.artifacts.append(a)
                    out.append({"relpath": a["relpath"], "artifact_id": f"art-{len(self.artifacts)}", "state": "READY"})
                return 200, out
            if action == "result":
                digest = canonical_sha256({k: body.get(k) for k in ("result", "started_at", "finished_at",
                                                                    "cancel_observed", "artifact_ids")})
                if digest != body["payload_digest"]:
                    return self._err(422, "DIGEST_MISMATCH")
                key = (job_id, body["attempt"])
                if self.fail_result_once:
                    self.fail_result_once = False
                    return self._err(500, "COMMIT_FAILED")
                if key in self.results:
                    if self.results[key]["digest"] != digest:
                        return self._err(409, "RESULT_CONFLICT")
                    return 200, self.results[key]["response"]
                if job_id in self.lost:
                    return self._err(409, "LEASE_LOST")
                res = body["result"]
                outcome = "CANCELLED" if body["cancel_observed"] else ("WORKER_SHUTDOWN" if body["runner_shutting_down"]
                                                                       else (res or {}).get("status", "NO_RESULT"))
                resp = {"outcome": outcome, "job_status": outcome, "run_status": "RUNNING", "response_digest": digest}
                self.results[key] = {"digest": digest, "response": resp, "body": body}
                env["_state"] = "DONE"
                return 200, resp
            if action == "ack":
                self.acked.add((job_id, body["attempt"]))
                return 204, None
            return self._err(404, "NOT_FOUND")


PROTOCOL = {"training": {}, "dataset": {"search_validation_manifest_hash": "sha256:sv",
                                         "final_evaluation_manifest_hash": "sha256:fe"}, "console": {"paths": {}}}


def envelope(job_id: str = "job-1", *, attempt: int = 1, target: int = 3, run_id: str = "run-x") -> dict[str, Any]:
    cid = f"{run_id}.ga0.g1.c001"
    spec = {"job_id": job_id, "run_id": run_id, "kind": "CANDIDATE_TRAIN", "protocol_hash": "sha256:p",
            "candidate_id": cid, "rung": "L0", "cumulative_target": target, "fixed_horizon": max(6, target), "arm": "GA",
            "search_seed": 0, "training_seed": 1, "params": {"candidate": {"init": "scratch"},
                                                             "stage": {"id": "L0", "epochs_added": target}}}
    return {"envelope_version": 1,
            "job": {"job_id": job_id, "attempt": attempt, "kind": "CANDIDATE_TRAIN", "spec": spec, "max_attempts": 3,
                    "priority": 100, "lease_expires_at": "2026-10-26T00:00:30Z", "leased_at": "2026-10-26T00:00:00Z"},
            "run": {"run_id": run_id, "kind": "SEARCH", "protocol_hash": "sha256:p", "resolved_protocol": PROTOCOL,
                    "status_at_claim": "RUNNING", "execution_mode": "PILOT"},
            "candidate": {"candidate_id": cid, "arm": "GA", "family": "YOLO11", "architecture_yaml": "nc: 1\n",
                          "genotype": {}},
            "resume": {"recorded_latest": None, "live_latest": None, "eval_ema": None, "location": None},
            "data_refs": {}, "versions": {"core_contract": "fake", "progress_version": 1,
                                         "checkpoint_format": "mbnas.full_state.v1", "required_runtime": {}},
            "fence": {"runner_id": "runner-A", "attempt": attempt, "claim_id": "clm-0", "issued_at": "2026-10-26T00:00:00Z"},
            "control": {"lease_ttl_s": 30, "heartbeat_s": 0.3, "progress_interval_s": 1.0, "kill_grace_s": 2}}
