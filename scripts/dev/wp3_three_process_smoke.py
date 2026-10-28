#!/usr/bin/env python3
"""WP-3 real multi-process smoke (WP-3 contract section 3, last row; WP-3C): core + owner worker + runner daemon +
gateway as real processes, driven ONLY through the user-facing ``/api/v1`` of the gateway with the user bearer.

Processes (all started here, all stopped here; only their own pids are signalled):

* core: ``uvicorn --factory mbnas_core.api.app:app_from_env`` on 127.0.0.1:<ephemeral>, ``MBNAS_CORE_EXPOSE_LEGACY_API
  =false`` (no ``/api/v1`` on core);
* owner worker: ``python -m mbnas_core.owner_worker --owner``;
* runner daemon: ``python -m mbnas_runner run`` (CPU, its own work root and dataset mount; local transfer:
  ``MBNAS_RUNNER_ARTIFACT_TRANSFER=local`` with ``MBNAS_RUNNER_ARTIFACT_STORE_DIR`` = the gateway's artifact store);
* gateway: ``uvicorn --factory mbnas_server.api.app:app_from_env`` on 127.0.0.1:<ephemeral> with the server-scope
  principal as ``MBNAS_GW_CORE_TOKEN`` and ``MBNAS_GW_WEB_DIST`` = ``mbnas_web/dist`` (when built).

Steps (each recorded with UTC time, request id, HTTP status, duration):

 1. inputs: synthetic 4-image dataset (gateway data root, runner copy), yolo11 YAML with ``nc: 1`` and a bad YAML
    (module ``check_requirements`` outside the allowlist);
 2. core start, principals (server/runner/admin via ``mbnas-core-principal``), owner worker + runner start
    (runner ONLINE checked with the admin token on ``/core/v1/owner/runners`` - the only direct core call);
 3. gateway start; ``GET /api/v1/health`` -> core reachable; ``GET /`` serves the static web when ``dist`` exists;
    core ``/api/v1`` is NOT exposed (404 on core);
 4. upload the user YAML -> ``POST /api/v1/structure-check {upload_id}`` -> PASS;
 5. upload the bad YAML -> structure-check -> FAIL / BLOCK_UNSUPPORTED with a precise diagnostic (row + module);
 6. ``POST /api/v1/protocols/validate`` with a dataset-bearing PILOT_SMOKE protocol (3-split synthetic dataset under the
    gateway's dataset root) -> the gateway computes path checks + per-split manifests and core answers
    ``capabilities.validation_host = "gateway"`` with no dataset/path errors; then the generation-only OFF policy -> valid;
 7. OFF generation run -> COMPLETED (``/api/v1/runs``, polling ``/api/v1/runs/{id}``);
 8. candidate detail ``GET /api/v1/runs/{id}/candidates/{cid}`` and the run's export zip through the gateway
    (``X-Bundle-Sha256`` = sha256 of the body, a readable zip with ``SHA256SUMS``);
 9. a runner-produced artifact from the gateway store: the uploaded user model trained by the REAL runner
    (``POST /api/v1/training-runs {upload_id}``, 1 epoch, 64 px, validation off, CPU) -> the runner registered its files
    and put them into the gateway store (local transfer); one artifact id the runner registered, listed by the candidate
    detail, is downloaded with ``GET /api/v1/runs/{id}/artifacts/{artifact_id}`` (user bearer): sha256(body) ==
    ``X-Artifact-Sha256`` == the registry sha256, the store holds those bytes, the gateway then records
    PRESENT@server_store and the detail shows the artifact READY;
10. stop core -> ``GET /api/v1/health`` = 200 ``degraded``/``core: unreachable``; ``POST /api/v1/protocols/validate``
    = 503 ``CORE_UNAVAILABLE`` (no local fallback);
11. stop everything started here; verify exit.

Fleet mode. Two steps depend on the core's validation device inventory -
``validate-dataset-manifests-via-gateway`` (step 6, an evaluation-ON PILOT_SMOKE protocol) and
``runner-training-via-gateway`` (step 9, explicit training) - so right before step 6 the smoke asks the running stack
which devices exist (``GET /api/v1/health`` through the gateway with the user bearer: ``devices``, the inventory
``console.benchmark.device`` is resolved against) and records the mode and why in the evidence header and the step log
(:func:`fleet_mode`):

* GPU - the inventory holds a CUDA device (default ``MBNAS_CORE_DEVICE_SOURCE=local``: NVML of the core host, which
  ``CUDA_VISIBLE_DEVICES`` does not hide): every step runs exactly as before with the preset device ``host-gpu-0``.
  Nothing executes on a GPU: step 6 only validates and the runner is CPU-only (``CUDA_VISIBLE_DEVICES=""``);
* CPU-only - every entry is the CPU entry (a core host without an NVML GPU plus CPU runners; or
  ``--device-source runners`` with this CPU runner): the SAME steps run with ``console.benchmark.device`` = that CPU
  entry and assert the CPU-only fleet semantics as PASS-by-design. Step 6: the evaluation-ON protocol is refused
  at validate with exactly EVALUATION_REQUIRES_CUDA (no resolution claim, no validation stored) while the dataset part
  still holds - the gateway checked the paths and computed the three per-split manifests (hashes read back from its
  manifest cache) and core used them (``validation_host = gateway``, per-split counts, no path/dataset error), see
  :func:`cpu_refusal_checks`. Step 9: explicit training validates on the CPU entry and the CPU runner trains it; its
  assertions (and those of the artifact download after it) are the GPU-mode ones, unchanged;
* UNKNOWN - no readable or an empty inventory (an empty inventory is not a CPU-only fleet either): the GPU-mode steps
  run unchanged and fail on their own.

The step list, its order and every GPU-mode assertion are the same in every mode; no step is skipped and every step
ends PASS or FAIL.

Exit 0 only when every step passed. Evidence: the step report written to ``--evidence`` (``scripts/verify.sh``
passes its own path).
No GPU, no network beyond 127.0.0.1, never ports 8000-8002 / 5173-5175.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
RESERVED = (8000, 8001, 8002, 5173, 5174, 5175)

_spec = importlib.util.spec_from_file_location("wp2_two_process_smoke", ROOT / "scripts" / "dev" / "wp2_two_process_smoke.py")
wp2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp2)  # type: ignore[union-attr]
now, TERMINAL = wp2.now, wp2.TERMINAL

BAD_YAML = """# deliberately unsafe: module name outside the structure allowlist (SECURITY_BASELINE section 4 item 1)
nc: 1
backbone:
  - [-1, 1, Conv, [16, 3, 2]]
  - [-1, 1, check_requirements, ["definitely-not-a-package==0.0.0"]]
head:
  - [-1, 1, Detect, [nc]]
"""

GPU_MODE, CPU_MODE, UNKNOWN_MODE = "GPU", "CPU-only", "UNKNOWN"
#: the 3-split synthetic dataset of step validate-dataset-manifests-via-gateway (dataset YAML splits train/val/test) and
#: the same image counts per validator role, as core reports them in ``capabilities.dataset.counts``
SPLIT3 = {"train": 3, "val": 2, "test": 2}
SPLIT3_IMAGES = {"train": 3, "search_val": 2, "final_eval": 2}
#: the only validate error of an evaluation-ON protocol whose ``console.benchmark.device`` is the CPU entry of
#: a CPU-only inventory (``mbnas_core.protocol.validator._Run.evaluation_device``)
CPU_EVALUATION_REFUSAL = (("EVALUATION_REQUIRES_CUDA", "console.search.evaluation_enabled"),)
CPU_BY_DESIGN = ("PASS-by-design (DD-A60 (1) / DD-A62, CPU-only fleet): an evaluation-ON protocol is refused at validate "
                 "with EVALUATION_REQUIRES_CUDA (latency is measured on CUDA only), so core makes no resolution claim and "
                 "stores no validation; the gateway's path checks and per-split manifests are still computed and used")


def is_cpu_device(entry: Any) -> bool:
    """True for the CPU entry of the core's validation inventory as ``GET /api/v1/health`` lists it (``devices``): kind
    ``cpu``, or the id ``cpu`` without a public ``device_id``. The rule of ``mbnas_core.protocol.validator.is_cpu_device``
    on the projected entry (the projection drops ``uuid`` and adds ``device_id`` only for a uuid-bearing, i.e. NVML /
    CUDA, device)."""
    return isinstance(entry, dict) and (entry.get("kind") == "cpu" or (
        not entry.get("device_id") and str(entry.get("id") or "").lower() == "cpu"))


def fleet_mode(http: int | None, health: Any, preset_device: str) -> dict[str, Any]:
    """Mode of the device-dependent steps (module docstring, "Fleet mode") from a ``GET /api/v1/health`` answer:
    ``{mode, why, benchmark_device, <inventory summary>}``. GPU when the inventory holds at least one non-CPU device (the
    steps use ``preset_device``, exactly as before); CPU-only when it is non-empty and every entry is the CPU entry (the
    validator's CPU-only fleet; the steps use that entry); UNKNOWN otherwise (the GPU-mode steps run unchanged)."""
    body = health if isinstance(health, dict) else {}
    devices = body.get("devices") if http == 200 else None
    inventory = [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []
    cuda = [d for d in inventory if not is_cpu_device(d)]
    cpu = [d for d in inventory if is_cpu_device(d)]
    caps = body.get("capabilities") if isinstance(body.get("capabilities"), dict) else {}
    preset = next((t for t in caps.get("device_targets") or [] if isinstance(t, dict) and t.get("id") == preset_device),
                  None)
    preset_target = {k: preset.get(k) for k in ("status", "name", "reason")} if preset else None
    in_inventory = any(preset_device in {d.get("id"), d.get("name"), *(d.get("aliases") or [])} for d in inventory)
    info = {"http": http, "inventory": [{k: d[k] for k in ("id", "kind", "name", "device_id", "runners", "worker_id")
                                         if k in d} for d in inventory],
            "cuda_device_ids": [d.get("id") for d in cuda], "cpu_device_ids": [d.get("id") for d in cpu],
            "preset_device": preset_device, "preset_device_in_inventory": in_inventory, "preset_target": preset_target}
    if cuda:
        return {"mode": GPU_MODE, "benchmark_device": preset_device, **info, "why": (
            f"the core's validation device inventory holds CUDA device(s) {info['cuda_device_ids']}: the steps run "
            f"exactly as before with the preset console.benchmark.device {preset_device!r} (in the inventory: "
            f"{in_inventory}); nothing executes on a GPU (step 6 only validates, the runner is CPU-only)")}
    if cpu:
        device = cpu[0].get("id")
        status, reason = (preset_target or {}).get("status"), (preset_target or {}).get("reason")
        return {"mode": CPU_MODE, "benchmark_device": device, **info, "why": (
            f"the core's validation device inventory holds only the CPU entry {info['cpu_device_ids']}, no CUDA device "
            f"(preset target {preset_device}: {status} - {reason}): the same steps run with console.benchmark.device "
            f"{device!r} and assert the DD-A60 (1) / DD-A62 CPU semantics - explicit training validates and trains on "
            f"the CPU runner, the evaluation-ON protocol is refused at validate with EVALUATION_REQUIRES_CUDA "
            f"(PASS-by-design)")}
    return {"mode": UNKNOWN_MODE, "benchmark_device": preset_device, **info, "why": (
        f"no usable device inventory (HTTP {http}, devices {devices!r}): neither a CUDA device nor a CPU-only fleet is "
        f"established, so the GPU-mode steps run unchanged with the preset device {preset_device!r}")}


def cpu_refusal_checks(http: int | None, answer: Any, *, device: str, rows_before: Any, rows_after: Any,
                       gateway_manifests: Any, expected_images: dict[str, int] | None = None) -> dict[str, bool]:
    """CPU-only mode of ``validate-dataset-manifests-via-gateway`` (PASS-by-design): named checks that must
    ALL hold. ``answer``: the gateway's validate answer for the evaluation-ON PILOT_SMOKE protocol whose
    ``console.benchmark.device`` is the CPU entry ``device``; ``rows_before`` / ``rows_after``: the core's validation rows
    around that request; ``gateway_manifests``: what the gateway's manifest cache holds for the three split roots."""
    expected = SPLIT3_IMAGES if expected_images is None else expected_images
    body = answer if isinstance(answer, dict) else {}
    errors = [e for e in body.get("errors") or [] if isinstance(e, dict)]
    caps = body.get("capabilities") if isinstance(body.get("capabilities"), dict) else {}
    dataset = caps.get("dataset") if isinstance(caps.get("dataset"), dict) else {}
    counts = dataset.get("counts") if isinstance(dataset.get("counts"), dict) else {}
    images = {role: c.get("images") if isinstance(c, dict) else None for role, c in counts.items()}
    resolved_device = caps.get("device") if isinstance(caps.get("device"), dict) else {}
    gm = gateway_manifests if isinstance(gateway_manifests, dict) else {}
    return {
        "http_200": http == 200,
        "refused_at_validate": body.get("valid") is False,
        "only_error_is_EVALUATION_REQUIRES_CUDA":
            tuple((e.get("code"), e.get("field")) for e in errors) == CPU_EVALUATION_REFUSAL,
        "cpu_entry_resolved": bool(device) and resolved_device.get("id") == device,
        "no_resolution_claim": (body.get("resolved") is None and not body.get("protocol_hash")
                                and not body.get("validation_id")),
        "no_validation_stored": isinstance(rows_before, int) and rows_before == rows_after,
        "validation_host_gateway": caps.get("validation_host") == "gateway",
        "core_used_the_3_split_manifests": images == expected,
        "gateway_split_hashes_set": gm.get("all_split_hashes_set") is True and gm.get("images") == expected,
    }


def _script_display() -> str:
    """sys.argv[0] relative to ROOT when inside it (else only its name): the header names the program really run."""
    try:
        return str(Path(sys.argv[0]).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return Path(sys.argv[0]).name or "wp3_three_process_smoke.py"

class Smoke3(wp2.Smoke):
    def __init__(self, args: argparse.Namespace) -> None:
        if not args.root:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            test_root = Path(os.environ.get("MBNAS_TEST_ROOT") or ROOT / "runtime" / "test_root")
            args.root = str(test_root / "wp3_smoke" / stamp)
        super().__init__(args)
        self.user_token = "smoke-user-token-" + uuid.uuid4().hex
        self.fleet: dict[str, Any] | None = None  # discover_fleet(): fleet mode
        self.gw_port = args.gw_port or self._free_port()
        while self.gw_port == self.port:
            self.gw_port = self._free_port()
        self.gw_base = f"http://127.0.0.1:{self.gw_port}"
        self.web_dist = Path(args.web_dist) if args.web_dist else ROOT / "mbnas_web" / "dist"
        self.web_dist_kind = "built"
        if not (self.web_dist / "index.html").is_file():
            # mbnas_web/dist is not built on this host: serve a SYNTHETIC bundle (index.html + one hashed asset) so the
            # real gateway process still proves static serving, SPA fallback and the asset cache header.
            # The evidence records web_dist_kind = "synthetic"; the real bundle is "확인 못 함" until it is built.
            self.web_dist = Path(args.root) / "web_dist"
            (self.web_dist / "assets").mkdir(parents=True, exist_ok=True)
            (self.web_dist / "index.html").write_text("<!doctype html><title>MBNAS smoke</title><div id=root></div>\n")
            (self.web_dist / "assets" / "app-0smoke0.js").write_text("console.log('mbnas smoke');\n")
            self.web_dist_kind = "synthetic"

    # -- env -----------------------------------------------------------------------------------------------------
    def core_env(self) -> dict[str, str]:
        env = super().core_env()
        env["MBNAS_CORE_EXPOSE_LEGACY_API"] = "false"
        return env

    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        env = super().runner_env(name, runner_id)
        # local transfer (same host): every file the runner registers goes into the gateway's artifact store
        env.update({"MBNAS_RUNNER_ARTIFACT_TRANSFER": "local",
                    "MBNAS_RUNNER_ARTIFACT_STORE_DIR": str(self.root / "gateway" / "artifacts")})
        return env

    def gateway_env(self) -> dict[str, str]:
        g = self.root / "gateway"
        env = self.base_env()
        env.update({"MBNAS_GW_API_TOKEN": self.user_token, "MBNAS_GW_CORE_URL": self.base,
                    "MBNAS_GW_CORE_TOKEN": self.tokens["server"],
                    "MBNAS_GW_ARTIFACT_SIGNING_KEY": "smoke-gw-signing-key-" + "g" * 24,
                    "MBNAS_GW_ARTIFACT_STORE": str(g / "artifacts"), "MBNAS_GW_LOG_DIR": str(g / "logs"),
                    "MBNAS_GW_AUDIT_LOG": str(g / "logs" / "audit.jsonl"),
                    "MBNAS_GW_MANIFEST_CACHE_ROOT": str(g / "cache"),
                    "MBNAS_GW_DATASET_ROOTS": json.dumps([str(self.root / "core_data")]),
                    "MBNAS_GW_OUTPUT_ROOTS": json.dumps([str(self.root / "core" / "runs")]),
                    "MBNAS_GW_MODEL_YAML_ROOTS": json.dumps([str(self.root / "uploads")]),
                    "MBNAS_GW_WEB_DIST": str(self.web_dist),
                    "MBNAS_GW_HOST": "127.0.0.1", "MBNAS_GW_PORT": str(self.gw_port),
                    "MBNAS_GW_CORE_CONNECT_TIMEOUT_S": "2", "MBNAS_GW_CORE_READ_TIMEOUT_S": "120"})
        return env

    # -- http ----------------------------------------------------------------------------------------------------
    def gw(self, method: str, path: str, body: Any = None, *, content: bytes | None = None,
           headers: dict[str, str] | None = None, raw: bool = False, token: str | None = None) -> tuple[int, Any, str, Any]:
        import httpx

        rid = "smoke3-" + uuid.uuid4().hex[:16]
        h = {"Authorization": f"Bearer {token or self.user_token}", "X-Request-ID": rid, **(headers or {})}
        r = httpx.request(method, self.gw_base + path, json=body if content is None else None, content=content,
                          headers=h, timeout=120)
        data: Any
        if raw:
            data = r.content
        else:
            try:
                data = r.json()
            except ValueError:
                data = r.text
        self.note(f"GW {method} {path} -> {r.status_code} request_id={rid} echoed={r.headers.get('X-Request-ID') == rid}")
        return r.status_code, data, rid, r

    def run_status(self, run_id: str) -> dict[str, Any] | None:  # through the gateway
        code, body, _, _ = self.gw("GET", f"/api/v1/runs/{run_id}")
        return body if code == 200 and isinstance(body, dict) else None

    def snapshot(self, run_id: str) -> dict[str, Any]:
        code, body, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/snapshot")
        return body if code == 200 else {"error": body, "request_id": rid}

    # -- steps ---------------------------------------------------------------------------------------------------
    def build_inputs(self) -> dict[str, str]:
        ds = super().build_inputs()
        (self.root / "uploads" / "bad_module.yaml").write_text(BAD_YAML)
        return ds

    def start_core(self) -> None:
        t0 = time.monotonic()
        self.spawn("core", [PY, "-B", "-m", "uvicorn", "--factory", "mbnas_core.api.app:app_from_env", "--host",
                            "127.0.0.1", "--port", str(self.port), "--log-level", "warning"], self.core_env())

        def up() -> Any:
            import httpx

            try:
                r = httpx.get(self.base + "/core/v1/owner/runners", timeout=5)
            except httpx.HTTPError:
                return None
            return r.status_code  # 401 without a token = core serving

        self.wait("core listening", up, timeout=120)
        self.step("core-start", True, t0, pid=self.procs["core"].pid, port=self.port)

    def start_gateway(self) -> bool:
        t0 = time.monotonic()
        self.spawn("gateway", [PY, "-B", "-m", "uvicorn", "--factory", "mbnas_server.api.app:app_from_env", "--host",
                               "127.0.0.1", "--port", str(self.gw_port), "--log-level", "warning"], self.gateway_env())
        health = self.wait("gateway health", lambda: (lambda r: r[1] if r[0] == 200 else None)(
            self.gw("GET", "/api/v1/health")), timeout=120)
        import httpx

        legacy = httpx.get(self.base + "/api/v1/health", headers={"Authorization": f"Bearer {self.tokens['server']}"},
                           timeout=10).status_code
        index = (self.web_dist / "index.html").read_bytes()
        web_code, web_body, _, web_resp = self.gw("GET", "/", raw=True)
        deep_code, deep_body, _, _ = self.gw("GET", "/monitor/smoke-run", raw=True)  # SPA fallback
        assets = sorted((self.web_dist / "assets").glob("*")) if (self.web_dist / "assets").is_dir() else []
        asset = {"path": None}
        if assets:
            a_code, a_body, _, a_resp = self.gw("GET", f"/assets/{assets[0].name}", raw=True)
            asset = {"path": f"/assets/{assets[0].name}", "status": a_code, "same_bytes": a_body == assets[0].read_bytes(),
                     "cache_control": a_resp.headers.get("cache-control")}
        web_ok = (web_code == 200 and web_body == index and "text/html" in web_resp.headers.get("content-type", "")
                  and deep_code == 200 and deep_body == index
                  and (not assets or (asset["status"] == 200 and asset["same_bytes"]
                                      and "immutable" in (asset["cache_control"] or ""))))
        ok = (health.get("status") == "ok" and health.get("core") not in ("unreachable", None) and legacy == 404
              and health.get("gateway", {}).get("web_dist") is True and web_ok)
        return self.step("gateway-start", bool(ok), t0, pid=self.procs["gateway"].pid, port=self.gw_port,
                         health=health, core_legacy_api_status=legacy, web_dist=str(self.web_dist),
                         web_dist_kind=self.web_dist_kind, web_status=web_code, spa_fallback_status=deep_code,
                         web_content_type=web_resp.headers.get("content-type"), asset=asset)

    def upload(self, name: str) -> dict[str, Any]:
        data = (self.root / "uploads" / name).read_bytes()
        code, body, rid, _ = self.gw("POST", "/api/v1/uploads/model-config", content=data,
                                     headers={"Content-Type": "application/x-yaml", "X-Filename": name})
        if code != 200:
            raise RuntimeError(f"upload {name} -> {code} {body} (request_id {rid})")
        assert body["source_sha256"].removeprefix("sha256:") == hashlib.sha256(data).hexdigest(), body
        return body

    def step_structure(self) -> bool:
        t0 = time.monotonic()
        good = self.upload("yolo11n.yaml")
        code, doc, rid, _ = self.gw("POST", "/api/v1/structure-check", {"upload_id": good["upload_id"], "input_size": 64})
        ok_good = code == 200 and doc.get("status") == "PASS"
        a = self.step("structure-check-pass", ok_good, t0, http=code, request_id=rid, upload=good,
                      status=(doc or {}).get("status"), code=(doc or {}).get("code"),
                      params=(doc or {}).get("params"), sandbox=(doc or {}).get("sandbox"))
        t1 = time.monotonic()
        bad = self.upload("bad_module.yaml")
        code, doc, rid, _ = self.gw("POST", "/api/v1/structure-check", {"upload_id": bad["upload_id"], "input_size": 64})
        text = json.dumps(doc)
        precise = "check_requirements" in text and "BLOCK_UNSUPPORTED" in text
        failed = (code == 200 and isinstance(doc, dict) and doc.get("status") in ("FAIL", "UNVERIFIED")) or code == 422
        b = self.step("structure-check-bad-yaml", bool(failed and precise), t1, http=code, request_id=rid,
                      body=doc if len(text) < 3000 else text[:3000])
        return a and b

    def step_validate_and_generate(self, ds: dict[str, str]) -> tuple[bool, str | None]:
        t0 = time.monotonic()
        policy = {"schema_version": "nas-policy/1",
                  "console": {"paths": {"output_root": str(self.root / "core" / "runs")},
                              "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                         "seeds": [7], "candidate_count": 2, "max_attempts": 12,
                                         "allow_duplicates": False, "arms": ["GA"]}}}
        code, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": policy})
        valid = code == 200 and isinstance(v, dict) and v.get("valid")
        manifests = self._validation_manifests(v.get("validation_id") if isinstance(v, dict) else None)
        self.step("validate-via-gateway", bool(valid), t0, http=code, request_id=rid_v,
                  errors=(v or {}).get("errors") if isinstance(v, dict) else v, gateway_manifests=manifests)
        if not valid:
            return False, None
        t1 = time.monotonic()
        code, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"],
                                                                   "protocol_hash": v["protocol_hash"],
                                                                   "validation_id": v["validation_id"],
                                                                   "idempotency_key": "wp3-gen-" + uuid.uuid4().hex[:8]})
        if code != 200:
            self.step("off-generation-run", False, t1, http=code, body=created, request_id=rid_c)
            return False, None
        run_id = created["run_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        jobs = [(j["kind"], j["status"]) for j in self.db_rows("SELECT kind, status FROM jobs WHERE run_id = ?", (run_id,))]
        ok = run["status"] == "COMPLETED" and jobs and all(s == "SUCCEEDED" for _, s in jobs)
        self.step("off-generation-run", bool(ok), t1, run_id=run_id, run_status=run["status"], jobs=jobs,
                  request_id=rid_c)
        return bool(ok), run_id

    def step_validate_dataset_manifests(self) -> bool:
        """Validate a dataset-bearing protocol through the gateway: the GATEWAY computes path checks + per-split
        manifests on its host (gateway-inputs wire format) and core records ``capabilities.validation_host=gateway``.
        A 3-split synthetic dataset (3 train / 2 val / 2 test PNG) under the gateway's dataset root.

        CPU-only fleet (module docstring "Fleet mode"): the protocol names the CPU entry and the step asserts
        :func:`cpu_refusal_checks` (the EVALUATION_REQUIRES_CUDA refusal plus the dataset part) instead of a valid
        result; the GPU-mode assertion is unchanged."""
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        t0 = time.monotonic()
        spec = importlib.util.spec_from_file_location("wp3_smoke_datasets", ROOT / "tests" / "_shared" / "datasets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        ds = mod.build_dataset(self.root / "core_data", "smoke3split", counts=dict(SPLIT3))
        paths = {k: ds[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
        paths["output_root"] = str(self.root / "core" / "runs")
        proto = pilot_smoke_protocol(paths)
        for b in proto["console"].get("baselines") or []:  # registry entries are UNVERIFIED on a fresh smoke home
            b["enabled"] = False
        proto["console"]["baseline_override_reason"] = "wp3 smoke: dataset-manifest validation only (no baseline run)"
        cpu = self.cpu_fleet()
        if cpu:  # the CPU entry of the CPU-only inventory instead of the preset host-gpu-0
            proto["console"]["benchmark"]["device"] = self.fleet["benchmark_device"]  # type: ignore[index]
            rows_before = self._validation_count()
        code, v, rid, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": proto})
        caps = (v or {}).get("capabilities") if isinstance(v, dict) else None
        errors = (v or {}).get("errors") if isinstance(v, dict) else v
        data_errors = [e for e in (errors or []) if isinstance(e, dict)
                       and str(e.get("field", "")).startswith(("console.paths", "dataset"))]
        host = (caps or {}).get("validation_host")
        db_row = self._validation_manifests((v or {}).get("validation_id") if isinstance(v, dict) else None)
        if cpu:
            rows_after = self._validation_count()
            gateway = self._gateway_split_manifests({"train": ds["train_root"], "search_val": ds["search_val_root"],
                                                     "final_eval": ds["final_eval_root"]})
            checks = cpu_refusal_checks(code, v, device=proto["console"]["benchmark"]["device"],
                                        rows_before=rows_before, rows_after=rows_after, gateway_manifests=gateway)
            return self.step("validate-dataset-manifests-via-gateway", bool(checks) and all(checks.values()), t0,
                             http=code, request_id=rid, fleet_mode=CPU_MODE,
                             benchmark_device=proto["console"]["benchmark"]["device"], expected=CPU_BY_DESIGN,
                             cpu_checks=checks, valid=(v or {}).get("valid") if isinstance(v, dict) else None,
                             validation_host=host, data_errors=data_errors,
                             other_errors=[e for e in (errors or []) if e not in data_errors][:10],
                             capabilities_device=(caps or {}).get("device"),
                             dataset_counts=((caps or {}).get("dataset") or {}).get("counts"),
                             gateway_split_manifests=gateway,
                             validation_rows={"before": rows_before, "after": rows_after}, db_row=db_row)
        ok = (code == 200 and host == "gateway" and not data_errors and isinstance(v, dict) and v.get("valid") is True
              and isinstance(db_row, dict) and db_row.get("validation_host") == "gateway"
              and db_row.get("all_split_hashes_set") is True)
        return self.step("validate-dataset-manifests-via-gateway", bool(ok), t0, http=code, request_id=rid,
                         valid=(v or {}).get("valid") if isinstance(v, dict) else None, validation_host=host,
                         data_errors=data_errors, other_errors=[e for e in (errors or []) if e not in data_errors][:10],
                         db_row=db_row, fleet_mode=self.fleet_label(),
                         benchmark_device=proto["console"]["benchmark"]["device"])

    # -- fleet mode ------------------------------------------------------------------------------------------------
    def discover_fleet(self) -> dict[str, Any]:
        """Ask the running stack which devices exist (module docstring, "Fleet mode"): ``GET /api/v1/health`` through
        the gateway (user bearer). Not a step (the step list stays as before); an unreadable answer is the UNKNOWN mode,
        recorded like the others in the step log and the evidence header."""
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        preset = pilot_smoke_protocol()["console"]["benchmark"]["device"]
        try:
            code, health, rid, _ = self.gw("GET", "/api/v1/health")
        except Exception as exc:  # noqa: BLE001 - recorded as the UNKNOWN mode
            code, health, rid = None, {"error": f"{type(exc).__name__}: {exc}"[:300]}, None
        self.fleet = {**fleet_mode(code, health, preset), "request_id": rid,
                      "source": "GET /api/v1/health through the gateway (user bearer): devices",
                      "core_device_source": self.args.device_source}
        self.note(f"FLEET MODE {self.fleet['mode']}: {self.fleet['why']}")
        self.note("device discovery " + json.dumps({k: v for k, v in self.fleet.items() if k != "why"}, sort_keys=True,
                                                   default=str, ensure_ascii=False))
        return self.fleet

    def cpu_fleet(self) -> bool:
        return (self.fleet or {}).get("mode") == CPU_MODE

    def fleet_label(self) -> str:
        return str((self.fleet or {}).get("mode") or "not determined")

    def _validation_count(self) -> Any:
        """Rows of the core's validation table (read-only, verification only) or a "확인 못 함" string."""
        try:
            tables = {r["name"] for r in self.db_rows("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in ("validations", "protocol_validations"):
                if t in tables:
                    return int(self.db_rows(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"])
            return "확인 못 함 (no validation table)"
        except Exception as exc:  # noqa: BLE001
            return f"확인 못 함 ({type(exc).__name__})"

    def _gateway_split_manifests(self, split_roots: dict[str, str]) -> Any:
        """Read-only look at the gateway's manifest cache (``MBNAS_GW_MANIFEST_CACHE_ROOT``/manifests, written by
        validate_prep through DatasetService: ``_index/<signature>.json`` = ``{split_hash, root}``, ``<hex>.json`` = the
        manifest) for the per-split manifests the gateway computed for ``split_roots`` (verification only)."""
        cache = self.root / "gateway" / "cache" / "manifests"
        try:
            by_root: dict[str, set[str]] = {}
            for f in sorted((cache / "_index").glob("*.json")):
                doc = json.loads(f.read_text())
                by_root.setdefault(os.path.realpath(doc["root"]), set()).add(doc["split_hash"])
            splits: dict[str, Any] = {}
            for role, root in split_roots.items():
                hashes = sorted(by_root.get(os.path.realpath(root), ()))
                if len(hashes) != 1:
                    splits[role] = {"split_hashes": hashes}
                    continue
                m = json.loads((cache / f"{hashes[0].split(':', 1)[1]}.json").read_text())
                splits[role] = {"split_hash": m.get("split_hash"), "class_mapping_hash": m.get("class_mapping_hash"),
                                "images": (m.get("counts") or {}).get("images")}
            mappings = {s.get("class_mapping_hash") for s in splits.values()}
            all_set = (set(splits) == set(split_roots) and len(mappings) == 1 and all(
                s.get("split_hash") and s.get("class_mapping_hash") for s in splits.values()))
            return {"cache": str(cache), "splits": splits, "images": {r: s.get("images") for r, s in splits.items()},
                    "all_split_hashes_set": all_set}
        except Exception as exc:  # noqa: BLE001
            return f"확인 못 함 ({type(exc).__name__}: {exc})"[:300]

    def _validation_manifests(self, validation_id: str | None) -> Any:
        """Read-only look at the core DB for what the gateway attached (verification only)."""
        if not validation_id:
            return "확인 못 함 (no validation_id)"
        try:
            tables = {r["name"] for r in self.db_rows("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in ("validations", "protocol_validations"):
                if t in tables:
                    rows = self.db_rows(f"SELECT * FROM {t} WHERE id = ?", (validation_id,))
                    if rows:
                        row = rows[0]

                        def col(name: str) -> Any:
                            try:
                                return json.loads(row.get(name) or "null")
                            except (TypeError, ValueError):
                                return None

                        caps, hashes = col("capabilities_json"), col("dataset_manifest_hashes_json")
                        return {"table": t, "columns": sorted(row.keys()),
                                "validation_host": (caps or {}).get("validation_host") if isinstance(caps, dict) else None,
                                "dataset_manifest_hashes": hashes,
                                "all_split_hashes_set": isinstance(hashes, dict) and all(
                                    hashes.get(k) for k in ("train_manifest_hash", "search_validation_manifest_hash",
                                                            "final_evaluation_manifest_hash", "class_mapping_hash"))}
            return "확인 못 함 (no validation table row)"
        except Exception as exc:  # noqa: BLE001
            return f"확인 못 함 ({type(exc).__name__})"

    def step_candidate_and_download(self, run_id: str) -> bool:
        t0 = time.monotonic()
        snap = self.snapshot(run_id)
        cands = snap.get("generated_candidates") or snap.get("candidates") or []
        cid = (cands[0].get("candidate_id") or cands[0].get("id")) if cands else None
        if not cid:
            return self.step("candidate-detail", False, t0, snapshot_keys=sorted(snap)[:40])
        code, detail, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        a = self.step("candidate-detail", code == 200 and isinstance(detail, dict), t0, http=code, candidate_id=cid,
                      request_id=rid, keys=sorted(detail)[:40] if isinstance(detail, dict) else detail)
        t1 = time.monotonic()
        path = f"/api/v1/runs/{run_id}/export"
        code, data, rid, resp = self.gw("GET", path, raw=True)
        sha_hdr = (resp.headers.get("X-Bundle-Sha256") or "").removeprefix("sha256:")
        digest = hashlib.sha256(data).hexdigest()
        members: list[str] = []
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = zf.namelist()
        except Exception as exc:  # noqa: BLE001 - recorded as a failure below
            self.note(f"export is not a readable zip: {type(exc).__name__}: {exc}")
        ok = code == 200 and sha_hdr == digest and "SHA256SUMS" in members
        b = self.step("export-download-via-gateway", bool(ok), t1, http=code, path=path, bytes=len(data),
                      sha256=digest, header_sha=sha_hdr or None, members=len(members), request_id=rid,
                      content_type=resp.headers.get("content-type"))
        return a and b

    def step_runner_artifact_download(self, ds: dict[str, str]) -> bool:
        """Step 9 (see the module docstring): a runner-produced artifact downloaded from the gateway store."""
        t0 = time.monotonic()
        if str(ROOT / "mbnas_core" / "src") not in sys.path:
            sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        policy = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs")})
        policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                  warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        policy["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
        if self.cpu_fleet():  # explicit training validates on the CPU entry of the inventory
            policy["console"]["benchmark"]["device"] = self.fleet["benchmark_device"]  # type: ignore[index]
        mode = {"fleet_mode": self.fleet_label(), "benchmark_device": policy["console"]["benchmark"]["device"]}
        up = self.upload("yolo11n.yaml")  # the same bytes as step 4: the same content-addressed upload id
        body = {"source": {"kind": "user", "upload_id": up["upload_id"]}, "protocol": policy, "mode": "scratch",
                "validation_enabled": False, "idempotency_key": "wp3-train-" + uuid.uuid4().hex[:8]}
        code, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        if code != 200 or not isinstance(created, dict):
            return self.step("runner-training-via-gateway", False, t0, http=code, body=created, request_id=rid_c, **mode)
        run_id, cid = created["run_id"], created["candidate_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        locs = self.db_rows("SELECT object_kind, object_id, store, runner_id, state, sha256, relpath FROM "
                            "artifact_locations WHERE run_id = ?", (run_id,))
        runner_arts = [loc for loc in locs if loc["object_kind"] == "artifact" and loc["runner_id"] == "smoke-runner-1"]
        a = self.step("runner-training-via-gateway", run["status"] == "COMPLETED" and bool(runner_arts), t0,
                      run_id=run_id, candidate_id=cid, run_status=run["status"], upload_id=up["upload_id"],
                      runner_artifacts=[{k: loc[k] for k in ("object_id", "store", "relpath")} for loc in runner_arts],
                      request_id=rid_c, **mode)
        t1 = time.monotonic()
        code, detail, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        listed = {x.get("id"): x for x in ((detail or {}).get("artifacts") or [])} if isinstance(detail, dict) else {}
        pick = next((loc for loc in runner_arts if loc["object_id"] in listed), None)
        if code != 200 or pick is None:
            return a and self.step("artifact-download-from-gateway-store", False, t1, http=code, request_id=rid_d,
                                   detail_artifact_ids=sorted(x for x in listed if x),
                                   runner_artifact_ids=[loc["object_id"] for loc in runner_arts])
        art_id = pick["object_id"]
        before = listed[art_id].get("state")
        code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{art_id}", raw=True)
        digest = hashlib.sha256(data).hexdigest()
        header = (resp.headers.get("X-Artifact-Sha256") or "").removeprefix("sha256:")
        registry = (listed[art_id].get("sha256") or pick["sha256"] or "").removeprefix("sha256:")
        stored = self.root / "gateway" / "artifacts" / digest[:2] / digest
        in_store = stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == digest
        located = self.wait("PRESENT@server_store", lambda: [r for r in self.db_rows(
            "SELECT state, verified_by FROM artifact_locations WHERE object_id = ? AND store = 'server_store'", (art_id,))
            if r["state"] == "PRESENT"] or None, timeout=30, interval=0.5) if code == 200 else []
        code2, detail2, _, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        after = next((x.get("state") for x in ((detail2 or {}).get("artifacts") or []) if x.get("id") == art_id), None)
        ok = (code == 200 and len(data) > 0 and digest == header == registry and in_store and bool(located)
              and after == "READY")
        b = self.step("artifact-download-from-gateway-store", bool(ok), t1, http=code, artifact_id=art_id,
                      kind=listed[art_id].get("kind"), bytes=len(data), sha256=digest, header_sha=header or None,
                      registry_sha=registry or None, store_file=str(stored), in_store=in_store, state_before=before,
                      state_after=after, server_store_location=located, verification=resp.headers.get(
                          "X-Artifact-Verification"), request_ids=[rid_d, rid])
        return a and b

    def step_core_down(self) -> bool:
        t0 = time.monotonic()
        p = self.procs["core"]
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=60)
        self.note(f"core stopped (exit {p.returncode}) for the degraded check")
        code_h, health, rid_h, _ = self.gw("GET", "/api/v1/health")
        code_v, body_v, rid_v, resp_v = self.gw("POST", "/api/v1/protocols/validate", {"protocol": {}})
        code_r, body_r, rid_r, _ = self.gw("GET", "/api/v1/runs")
        ok = (code_h == 200 and health.get("status") == "degraded" and health.get("core") == "unreachable"
              and code_v == 503 and body_v.get("code") == "CORE_UNAVAILABLE" and "valid" not in body_v
              and code_r == 503 and body_r.get("code") == "CORE_UNAVAILABLE")
        return self.step("core-down-degraded-503", bool(ok), t0, health=health, validate={"http": code_v, "body": body_v,
                         "retry_after": resp_v.headers.get("Retry-After"), "request_id": rid_v},
                         runs={"http": code_r, "code": (body_r or {}).get("code")}, request_ids=[rid_h, rid_v, rid_r])

    def stop_all(self) -> bool:
        t0 = time.monotonic()
        codes = {}
        for name in ("gateway", "runner", "owner", "core"):
            p = self.procs.get(name)
            if p is None:
                continue
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self.note(f"{name} pid={p.pid} did not stop on SIGTERM: SIGKILL")
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=30)
            codes[name] = p.returncode
        alive = [n for n, p in self.procs.items() if p.poll() is None]
        return self.step("stop-own-processes", not alive, t0, exit_codes=codes, still_alive=alive,
                         pids={n: p.pid for n, p in self.procs.items()})

    def wait(self, what: str, fn: Any, timeout: float, interval: float = 1.0) -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for name in ("core", "owner", "runner", "gateway"):
                p = self.procs.get(name)
                if p is not None and p.poll() is not None and not getattr(p, "_expected_exit", False):
                    raise RuntimeError(f"{name} (pid {p.pid}) exited with {p.returncode} while waiting for {what}")
            try:
                last = fn()
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            if last and not isinstance(last, str):
                return last
            time.sleep(interval)
        raise TimeoutError(f"{what} not reached within {timeout} s (last: {str(last)[:300]})")

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        script = _script_display()  # the program actually run (e.g. web_real_stack.py drives this stack)
        title = ("WP-3 three-process smoke" if script.endswith("wp3_three_process_smoke.py")
                 else f"{script} on the WP-3 three-process stack")
        lines = [f"# {title} ({started} .. {now()}, {dur:.1f} s) result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B {script} {' '.join(sys.argv[1:])}",
                 f"# cwd: {ROOT}  interpreter: {PY}  CUDA_VISIBLE_DEVICES=''  core: {self.base}  gateway: {self.gw_base}",
                 f"# smoke root: {self.root}  process logs: {self.root / 'logs'}"]
        fleet = self.fleet or {"mode": "not determined", "why": "no device discovery (no device-dependent step ran)"}
        lines += [f"# fleet mode: {fleet['mode']} - {fleet['why']}",
                  "# device discovery: " + json.dumps({k: v for k, v in fleet.items() if k != "why"}, sort_keys=True,
                                                      default=str, ensure_ascii=False), "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        for name in ("core", "owner", "runner", "gateway"):
            logf = self.root / "logs" / f"{name}.log"
            if logf.is_file():
                tail = logf.read_text(errors="replace").splitlines()[-40:]
                lines += [f"# tail of {name}.log ({len(tail)} lines):"] + ["  " + t for t in tail]
        text = "\n".join(lines) + "\n"
        for secret in (self.user_token, *self.tokens.values()):  # evidence never carries a live token
            text = text.replace(secret, "<redacted>")
        out.write_text(text)
        print(f"evidence -> {out}")

    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            ok = self.step_structure() and ok
            self.discover_fleet()  # before the first device-dependent step (not a step of its own)
            ok = self.step_validate_dataset_manifests() and ok
            gen_ok, run_id = self.step_validate_and_generate(ds)
            ok = ok and gen_ok
            if run_id:
                ok = self.step_candidate_and_download(run_id) and ok
            ok = self.step_runner_artifact_download(ds) and ok
            self.procs["core"]._expected_exit = True  # type: ignore[attr-defined]
            ok = self.step_core_down() and ok
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0, help="core port (default ephemeral)")
    p.add_argument("--gw-port", type=int, default=0, help="gateway port (default ephemeral)")
    p.add_argument("--root", default=None, help="smoke root (default $MBNAS_TEST_ROOT/wp3_smoke/<stamp>)")
    p.add_argument("--timeout", type=float, default=600, help="seconds for the run to reach a terminal state")
    p.add_argument("--web-dist", default=None, help="built web bundle (default mbnas_web/dist)")
    p.add_argument("--device-source", choices=("local", "runners"), default="local",
                   help="core MBNAS_CORE_DEVICE_SOURCE (local = NVML of this host, runners = registered runners); the "
                        "device-dependent steps follow the fleet mode the running stack reports (module docstring)")
    p.add_argument("--evidence", default=str(ROOT / "docs" / "internal" / "evidence" / "WP3" / "three_process_smoke.txt"))
    args = p.parse_args(argv)
    if args.port in RESERVED or args.gw_port in RESERVED:
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    return Smoke3(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
