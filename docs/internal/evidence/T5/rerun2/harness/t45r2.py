#!/usr/bin/env python3
"""T4/T5 client harness (WP-8), RERUN2 version (adds response dumps for T8, SSE, 3-epoch prune round trip, generated-config
training + config revisions). Based on the FINAL re-run version (adds waitdev, trainval, refusal-by-design decision, gen
architecture download, health allowlist check). Drives a RUNNING gateway only through its user ``/api/v1`` (user bearer), stdlib only
(urllib, sqlite3 read-only for verification), so the same file serves T4 (inside the owner sandbox) and T5 (host
client of the public containers). Never prints or stores a secret: the bearer is read from an env file and every
evidence text is redacted.

Items (``--items``, comma list, executed in order):
  health     GET /api/v1/health without token (401) and with token (200, core reachable)
  web        GET / (index.html) + one /assets/* file named by the index (+ byte compare with --web-dist)
  structure  upload valid + broken example YAML -> POST /structure-check (PASS / FAIL with codes)
  gen        OFF generation-only run (examples/protocol.cpu_generation_only.json shape, CPU) -> COMPLETED,
             candidates training_status UNTRAINED, candidate detail
  train      explicit CPU training of the uploaded user YAML on --dataset-yaml (64 px, 2 epochs);
             --pause: pause while epoch 1 runs -> PAUSED -> resume -> COMPLETED (full-state resume checked in --db)
  results    result rows of the training run through the gateway (run, candidates, candidate detail, snapshot)
  download   artifact + checkpoint download of the training run through the gateway with sha256 match; export zip
  decision   small CPU search run (evaluation ON) -> visible per-candidate decisions; final-retrain request
  coredown   (core must already be stopped) health degraded + validate 503 CORE_UNAVAILABLE (no fallback)
  reqids     request ids of this harness vs the gateway audit log / gateway log / core log (--audit-log/--gw-log/--core-log)
"""
from __future__ import annotations

import argparse
import shlex
import socket
import subprocess
import threading
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_env_value(path: str, key: str) -> str:
    for line in Path(path).read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{key} not found in {path}")


class H:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.gw_base = a.gw.rstrip("/")
        self.token = read_env_value(a.token_env, a.token_key)
        self.secrets = [self.token] + [read_env_value(f, k) for f, k in (s.split(":", 1) for s in a.redact_env)]
        self.ev = Path(a.evidence_dir)
        self.ev.mkdir(parents=True, exist_ok=True)
        self.steps_f = open(self.ev / f"{a.label}_steps.jsonl", "a", encoding="utf-8")
        self.http_f = open(self.ev / f"{a.label}_http.log", "a", encoding="utf-8")
        self.request_ids: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}
        sp = self.ev / f"{a.label}_state.json"
        if sp.is_file():
            self.state = json.loads(sp.read_text())
        self.results: dict[str, str] = {}

    # ------------------------------------------------------------------ recording
    def red(self, text: str) -> str:
        for s in self.secrets:
            if s:
                text = text.replace(s, "<redacted>")
        return text

    def step(self, name: str, ok: bool | None, t0: float, **kw: Any) -> bool:
        rec = {"utc": utc(), "label": self.a.label, "step": name,
               "verdict": "PASS" if ok else ("FAIL" if ok is False else "INFO"),
               "seconds": round(time.monotonic() - t0, 2), **kw}
        line = self.red(json.dumps(rec, ensure_ascii=False, default=str))
        self.steps_f.write(line + "\n")
        self.steps_f.flush()
        print(line[:1500], flush=True)
        return bool(ok)

    def save(self, name: str, obj: Any) -> None:
        (self.ev / name).write_text(self.red(json.dumps(obj, indent=1, ensure_ascii=False, default=str)))

    def gw(self, method: str, path: str, body: Any = None, *, content: bytes | None = None,
           headers: dict[str, str] | None = None, raw: bool = False, token: bool = True,
           timeout: float = 180) -> tuple[int, Any, str, dict[str, str]]:
        rid = f"{self.a.label}-" + uuid.uuid4().hex[:20]
        h = {"X-Request-ID": rid, **(headers or {})}
        if token:
            h["Authorization"] = f"Bearer {self.token}"
        data = content
        if content is None and body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.gw_base + path, data=data, method=method, headers=h)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code, payload, hdrs = r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            code, payload, hdrs = e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
        except (urllib.error.URLError, OSError) as e:
            code, payload, hdrs = 0, f"{type(e).__name__}: {e}".encode(), {}
        parsed: Any = payload
        if not raw:
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = payload.decode(errors="replace")
        echoed = hdrs.get("x-request-id")
        self.dump(method, path, code, rid, payload, hdrs)
        self.request_ids.append({"rid": rid, "method": method, "path": path, "status": code, "echoed": echoed == rid})
        self.http_f.write(self.red(f"{utc()} {method} {path} -> {code} rid={rid} echoed={echoed == rid} "
                                   f"{round(time.monotonic() - t0, 3)}s\n"))
        self.http_f.flush()
        return code, parsed, rid, hdrs

    def dump(self, method: str, path: str, code: int, rid: str, payload: bytes, hdrs: dict[str, str]) -> None:
        """T8 runtime capture: every public response body as returned (raw bytes) + an index line."""
        if not self.a.dump_dir:
            return
        d = Path(self.a.dump_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._dump_n = getattr(self, "_dump_n", 0) + 1
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.split("?")[0]).strip("_")[:120] or "root"
        name = f"{self.a.label}_{self._dump_n:04d}_{method}_{safe}_{code}.bin"
        (d / name).write_bytes(payload if isinstance(payload, bytes) else str(payload).encode())
        with open(d / f"{self.a.label}_index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"file": name, "method": method, "path": path, "status": code, "request_id": rid,
                                "bytes": len(payload) if isinstance(payload, bytes) else None,
                                "content_type": hdrs.get("content-type")}) + "\n")

    def sse_read(self, run_id: str, *, last_event_id: str | None = None, after_seq: int | None = None,
                 max_events: int = 1000, max_seconds: float = 10.0, stop: threading.Event | None = None,
                 sock_timeout: float = 4.0) -> dict[str, Any]:
        """Read the gateway SSE stream of a run (``GET /api/v1/runs/{id}/events``) for at most ``max_seconds`` (or until
        ``stop`` is set / ``max_events``); returns the parsed events (id, event, data size) and the raw text."""
        rid = f"{self.a.label}-" + uuid.uuid4().hex[:20]
        h = {"X-Request-ID": rid, "Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"}
        if last_event_id is not None:
            h["Last-Event-ID"] = str(last_event_id)
        path = f"/api/v1/runs/{run_id}/events" + (f"?after_seq={after_seq}" if after_seq is not None else "")
        req = urllib.request.Request(self.gw_base + path, method="GET", headers=h)
        events: list[dict[str, Any]] = []
        raw: list[str] = []
        code, hdrs, err = 0, {}, None
        t0 = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=sock_timeout)
            code, hdrs = resp.status, {k.lower(): v for k, v in resp.headers.items()}
            cur: dict[str, Any] = {}
            while time.monotonic() - t0 < max_seconds and len(events) < max_events and not (stop and stop.is_set()):
                try:
                    line = resp.readline()
                except (socket.timeout, TimeoutError):
                    raw.append(f": <client: no data for {sock_timeout}s, stop reading>")
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                raw.append(text)
                if text == "":
                    if cur:
                        events.append(cur)
                    cur = {}
                elif text.startswith(":"):
                    cur.setdefault("comments", 0)
                    cur["comments"] += 1
                elif ":" in text:
                    k, _, v = text.partition(":")
                    v = v[1:] if v.startswith(" ") else v
                    if k == "data":
                        cur["data_bytes"] = cur.get("data_bytes", 0) + len(v)
                        try:
                            dj = json.loads(v)
                            if isinstance(dj, dict):
                                cur["data_type"] = dj.get("type") or dj.get("event")
                        except ValueError:
                            pass
                    else:
                        cur[k] = v
            resp.close()
        except urllib.error.HTTPError as e:
            code, hdrs, err = e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()[:500].decode(errors="replace")
        except (urllib.error.URLError, OSError) as e:
            err = f"{type(e).__name__}: {e}"
        self.dump("GET", path, code, rid, ("\n".join(raw)).encode(), hdrs)
        self.request_ids.append({"rid": rid, "method": "GET", "path": path, "status": code,
                                 "echoed": hdrs.get("x-request-id") == rid})
        self.http_f.write(self.red(f"{utc()} GET {path} (SSE) -> {code} rid={rid} events={len(events)} "
                                   f"{round(time.monotonic() - t0, 3)}s\n"))
        self.http_f.flush()
        real = [e for e in events if "id" in e or "event" in e]
        return {"http": code, "request_id": rid, "content_type": hdrs.get("content-type"), "events": len(real),
                "heartbeat_or_comment_blocks": len(events) - len(real), "error": err,
                "ids": [e.get("id") for e in real], "types": sorted({str(e.get("event") or e.get("data_type")) for e in real})}

    def db(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        if not self.a.db:
            return []
        con = sqlite3.connect(f"file:{self.a.db}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def wait(self, what: str, fn: Any, timeout: float, interval: float = 2.0) -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = fn()
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            if last and not isinstance(last, str):
                return last
            time.sleep(interval)
        raise TimeoutError(f"{what} not reached in {timeout}s (last {str(last)[:300]})")

    def run_status(self, run_id: str) -> dict[str, Any] | None:
        code, body, _, _ = self.gw("GET", f"/api/v1/runs/{run_id}")
        return body if code == 200 and isinstance(body, dict) else None

    def run_wait(self, run_id: str, timeout: float, statuses: set[str] = TERMINAL) -> dict[str, Any]:
        return self.wait(f"run {run_id} in {sorted(statuses)}",
                         lambda: (lambda r: r if r and r.get("status") in statuses else None)(self.run_status(run_id)),
                         timeout, 2.0)

    def snap_lists(self, run_id: str) -> tuple[int, dict[str, Any], str]:
        c, snap, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/snapshot")
        return c, (snap if isinstance(snap, dict) else {"_raw": snap}), rid

    def jobs(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db("SELECT id, kind, status, attempt, rung, lease_owner, error_json FROM jobs WHERE run_id = ? "
                       "ORDER BY created_at", (run_id,)) if self.a.db else []
        for r in rows:
            try:
                r["result_reason"] = (json.loads(r.pop("error_json") or "null") or {}).get("reason_code")
            except (TypeError, ValueError, AttributeError):
                r["result_reason"] = None
        return rows

    # ------------------------------------------------------------------ items
    def item_health(self) -> bool:
        t0 = time.monotonic()
        c0, b0, r0, _ = self.gw("GET", "/api/v1/health", token=False)
        c1, b1, r1, _ = self.gw("GET", "/api/v1/health")
        core = b1.get("core") if isinstance(b1, dict) else None
        reach = (core or {}).get("reachable") if isinstance(core, dict) else core
        ok = c0 == 401 and c1 == 200 and reach not in (False, "unreachable", None)
        text = json.dumps(b1)
        forbidden_hits = [f for f in self.a.forbid_health if f and f in text]
        allow = (b1 or {}).get("allowlists") if isinstance(b1, dict) else None
        expect_ok = None
        if self.a.expect_health_allowlists:
            expect_ok = allow == json.loads(self.a.expect_health_allowlists)
            ok = ok and expect_ok
        ok = ok and not forbidden_hits
        return self.step("health", ok, t0, no_token_http=c0, http=c1, request_ids=[r0, r1],
                         status=(b1 or {}).get("status") if isinstance(b1, dict) else b1, core=core,
                         contract_version=(b1 or {}).get("contract_version") if isinstance(b1, dict) else None,
                         gateway=(b1 or {}).get("gateway") if isinstance(b1, dict) else None,
                         allowlists=allow, allowlists_equal_expected=expect_ok,
                         forbidden_strings_checked=len(self.a.forbid_health), forbidden_hits=len(forbidden_hits),
                         devices=[{k: d.get(k) for k in ("id", "name")} for d in ((b1 or {}).get("devices") or [])
                                  if isinstance(d, dict)] if isinstance(b1, dict) else None)

    def item_waitdev(self) -> bool:
        """The core caches its validation device list for up to 30 s (OPERATIONS.md 15): wait until the health shows a
        device (the CPU runner's ``cpu`` entry on a GPU-less core) before training validation."""
        t0 = time.monotonic()
        seen: list[Any] = []
        rid = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            c, b, rid, _ = self.gw("GET", "/api/v1/health")
            devs = (b or {}).get("devices") if isinstance(b, dict) else None
            seen = [{k: d.get(k) for k in ("id", "name")} for d in (devs or []) if isinstance(d, dict)]
            if seen:
                break
            time.sleep(3)
        return self.step("wait-device-inventory", bool(seen), t0, devices=seen, last_request_id=rid,
                         waited_s=round(time.monotonic() - t0, 1))

    def item_web(self) -> bool:
        t0 = time.monotonic()
        c, body, rid, h = self.gw("GET", "/", raw=True, token=False)
        html = body.decode(errors="replace") if isinstance(body, bytes) else str(body)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
        res: dict[str, Any] = {"http": c, "content_type": h.get("content-type"), "bytes": len(body),
                               "index_sha256": hashlib.sha256(body).hexdigest(), "assets_in_index": assets,
                               "request_id": rid}
        ok = c == 200 and "text/html" in (h.get("content-type") or "") and bool(assets)
        if self.a.web_dist:
            res["index_same_as_dist"] = body == (Path(self.a.web_dist) / "index.html").read_bytes()
            ok = ok and res["index_same_as_dist"]
        for ap in assets[:2]:
            ca, ab, ra, ha = self.gw("GET", ap, raw=True, token=False)
            a = {"path": ap, "http": ca, "bytes": len(ab), "sha256": hashlib.sha256(ab).hexdigest(),
                 "content_type": ha.get("content-type"), "cache_control": ha.get("cache-control"), "request_id": ra}
            if self.a.web_dist:
                local = Path(self.a.web_dist) / ap.lstrip("/")
                a["same_as_dist"] = local.is_file() and local.read_bytes() == ab
                ok = ok and a["same_as_dist"]
            ok = ok and ca == 200
            res.setdefault("assets", []).append(a)
        cd, bd, rd, _ = self.gw("GET", "/monitor/t45-deep-link", raw=True, token=False)
        res["spa_fallback"] = {"http": cd, "same_as_index": bd == body, "request_id": rd}
        return self.step("web-console", ok, t0, **res)

    def upload(self, path: Path) -> tuple[int, Any, str]:
        data = path.read_bytes()
        c, b, rid, _ = self.gw("POST", f"/api/v1/uploads/model-config?filename={path.name}", content=data,
                               headers={"Content-Type": "application/x-yaml"})
        return c, b, rid

    def item_structure(self) -> bool:
        ok_all = True
        models = Path(self.a.models)
        for name, want in (("yolo11n_user.yaml", "PASS"), ("yolo11n_user_broken.yaml", "FAIL")):
            t0 = time.monotonic()
            p = models / name
            c, up, rid_u = self.upload(p)
            if c != 200:
                ok_all = self.step(f"structure-{want.lower()}", False, t0, upload_http=c, body=up, request_id=rid_u) and ok_all
                continue
            sha_ok = str(up.get("source_sha256", "")).removeprefix("sha256:") == hashlib.sha256(p.read_bytes()).hexdigest()
            if want == "PASS":
                self.state["upload_id"] = up["upload_id"]
            c2, doc, rid_c, _ = self.gw("POST", "/api/v1/structure-check", {"upload_id": up["upload_id"], "input_size": 64})
            status = doc.get("status") if isinstance(doc, dict) else None
            text = json.dumps(doc)
            codes = sorted(set(re.findall(r'"code":\s*"([A-Z_]+)"', text)))
            ok = c2 == 200 and status == want and sha_ok and (want == "PASS" or bool(codes))
            ok_all = self.step(f"structure-{want.lower()}", ok, t0, file=f"examples/models/{name}",
                               file_sha256=hashlib.sha256(p.read_bytes()).hexdigest(), upload_http=c,
                               upload_id=up.get("upload_id"), upload_sha_match=sha_ok, check_http=c2, status=status,
                               codes=codes, params=(doc or {}).get("params") if isinstance(doc, dict) else None,
                               diagnostics=(doc.get("diagnostics") or doc.get("errors") or doc.get("issues"))
                               if isinstance(doc, dict) else doc, request_ids=[rid_u, rid_c]) and ok_all
        return ok_all

    def item_gen(self) -> bool:
        t0 = time.monotonic()
        policy = json.loads(Path(self.a.gen_protocol).read_text())
        policy["console"]["paths"]["output_root"] = self.a.output_root
        c, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": policy})
        valid = c == 200 and isinstance(v, dict) and v.get("valid") is True
        self.step("gen-validate", valid, t0, http=c, request_id=rid_v,
                  errors=(v or {}).get("errors") if isinstance(v, dict) else v,
                  capabilities=(v or {}).get("capabilities") if isinstance(v, dict) else None)
        if not valid:
            return False
        t1 = time.monotonic()
        c, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                                "validation_id": v["validation_id"],
                                                                "idempotency_key": f"{self.a.label}-gen-" + uuid.uuid4().hex[:8]})
        if c != 200:
            return self.step("gen-run", False, t1, http=c, body=created, request_id=rid_c)
        run_id = created["run_id"]
        self.state["gen_run"] = run_id
        run = self.run_wait(run_id, self.a.timeout)
        cc, snap, rid_l = self.snap_lists(run_id)
        cands = snap.get("generated_candidates") or []
        cl = {"generated_candidates": cands, "candidates(evaluation rows)": snap.get("candidates")}
        ts = [c_.get("training_status") for c_ in cands]
        es = [c_.get("evaluation_status") for c_ in cands]
        detail_ok = None
        rid_d = None
        if cands:
            cid = cands[0].get("candidate_id") or cands[0].get("id")
            cd, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
            detail_ok = cd == 200 and isinstance(det, dict)
            self.save(f"{self.a.label}_gen_candidate_detail.json", det)
            arch = [a for a in ((det or {}).get("artifacts") or []) if isinstance(a, dict) and a.get("kind") == "architecture"]
            self.state["gen_candidate"] = cid
            if arch:
                self.state["gen_arch_ok"] = self._download(run_id, arch[0], "gen-architecture", need_store=False)
                self.state["gen_arch_sha"] = self.state.get("last_download_sha")
            else:
                self.state["gen_arch_ok"] = self.step("download-gen-architecture", False, t0,
                                                      reason="no architecture artifact listed for the OFF candidate",
                                                      artifacts=(det or {}).get("artifacts") if isinstance(det, dict) else det)
        self.save(f"{self.a.label}_gen_candidates.json", cl)
        jobs = self.jobs(run_id)
        ok = (run["status"] == "COMPLETED" and cc == 200 and len(cands) >= 1 and all(t == "UNTRAINED" for t in ts)
              and bool(detail_ok) and bool(self.state.get("gen_arch_ok")))
        return self.step("gen-run", ok, t1, run_id=run_id, run_status=run["status"], request_ids=[rid_c, rid_l, rid_d],
                         candidates=len(cands), training_status=ts, evaluation_status=es,
                         jobs=[(j["kind"], j["status"], j["lease_owner"]) for j in jobs], candidate_detail_ok=detail_ok)

    def presets(self) -> dict[str, Any]:
        c, body, rid, _ = self.gw("GET", "/api/v1/protocols/presets")
        if c != 200:
            raise RuntimeError(f"presets -> {c}")
        for p in body.get("presets") or []:
            pid = json.dumps(p.get("id") or p.get("preset") or "")
            if "PILOT" in pid.upper() or "SMOKE" in pid.upper():
                return p
        raise RuntimeError("no PILOT_SMOKE preset in " + json.dumps([p.get("id") for p in body.get("presets") or []]))

    def training_protocol(self, epochs: int) -> dict[str, Any]:
        preset = self.presets()
        proto = json.loads(json.dumps(preset.get("protocol") or preset))
        ds = Path(self.a.dataset_yaml)
        paths = proto["console"].setdefault("paths", {})
        paths.update({"dataset_yaml": str(ds), "output_root": self.a.output_root})
        if self.a.split_paths:  # DD-A53 (g): split deployments name the split roots explicitly
            paths.update({"train_root": str(ds.parent / "images" / "train"),
                          "search_val_root": str(ds.parent / "images" / "val"),
                          "final_eval_root": str(ds.parent / "images" / "test")})
        proto["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                 warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        proto["console"]["training"] = {"epoch_target_total": epochs, "seed": 41, "native_image_artifacts": True}
        if self.a.bench_device:
            proto["console"].setdefault("benchmark", {})["device"] = self.a.bench_device
        return proto

    def item_train(self) -> bool:
        t0 = time.monotonic()
        if "upload_id" not in self.state:
            c, up, _ = self.upload(Path(self.a.models) / "yolo11n_user.yaml")
            self.state["upload_id"] = up["upload_id"]
        body = {"source": {"kind": "user", "upload_id": self.state["upload_id"], "model_name": "yolo11n_user"},
                "protocol": self.training_protocol(2), "mode": "scratch", "validation_enabled": False,
                "idempotency_key": f"{self.a.label}-train-" + uuid.uuid4().hex[:8]}
        c, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        ok = c == 200 and isinstance(created, dict) and created.get("run_id")
        self.step("train-create", bool(ok), t0, http=c, request_id=rid_c,
                  body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id", "created",
                                                                           "mode", "source")})
        if not ok:
            return False
        run_id, cid = created["run_id"], created["candidate_id"]
        self.state.update(train_run=run_id, train_candidate=cid)
        if self.a.pause:
            t1 = time.monotonic()
            running = self.wait("training running", lambda: (lambda r: r if r and r.get("status") == "RUNNING" else None)(
                self.run_status(run_id)), 300, 0.3)
            jobs_before = self.jobs(run_id)
            cp, bp, rid_p, _ = self.gw("POST", f"/api/v1/runs/{run_id}/pause", {"reason": "T4 pause during CPU epoch 1"})
            self.step("train-pause-request", cp == 200, t1, http=cp, request_id=rid_p, run_status_before=running.get("status"),
                      jobs_before=jobs_before, body=bp if cp != 200 else {"status": (bp or {}).get("status")})
            t2 = time.monotonic()
            run = self.run_wait(run_id, 900, {"PAUSED", "COMPLETED", "FAILED", "CANCELLED"})
            jobs_p = self.db("SELECT id, kind, status, attempt, error_json FROM jobs WHERE run_id = ?", (run_id,))
            ck = self.db("SELECT kind, epoch, path FROM checkpoints WHERE run_id = ? ORDER BY epoch", (run_id,))
            reasons = []
            for j in jobs_p:
                try:
                    reasons.append((json.loads(j.get("error_json") or "null") or {}).get("reason_code"))
                except (TypeError, ValueError):
                    reasons.append(None)
            full = [(x["kind"], x["epoch"]) for x in ck if x["kind"] == "training_full"]
            paused_ok = run["status"] == "PAUSED" and any(r in ("PAUSED_AT_EPOCH", "PAUSED_AT_SAFE_BOUNDARY") for r in reasons)
            self.step("train-paused", paused_ok, t2, run_status=run["status"], run_reason=run.get("reason_code"),
                      job_status=[(j["kind"], j["status"], j["attempt"]) for j in jobs_p], pause_reasons=reasons,
                      full_state_checkpoints=full)
            self.state["paused_epochs"] = [e for _, e in full]
            if run["status"] != "PAUSED":
                return False
            t3 = time.monotonic()
            cr, br, rid_r, _ = self.gw("POST", f"/api/v1/runs/{run_id}/resume", {})
            self.step("train-resume-request", cr == 200, t3, http=cr, request_id=rid_r,
                      body=br if cr != 200 else {"status": (br or {}).get("status")})
        t4 = time.monotonic()
        run = self.run_wait(run_id, self.a.timeout)
        jobs = self.db("SELECT id, kind, status, attempt, lease_owner, result_json FROM jobs WHERE run_id = ?", (run_id,))
        rr: dict[str, Any] = {}
        outputs: dict[str, Any] = {}
        for j in jobs:
            try:
                res = json.loads(j.get("result_json") or "null") or {}
            except (TypeError, ValueError):
                res = {}
            outputs = res.get("outputs") or outputs
            rr = outputs.get("rung_result") or res.get("rung_result") or rr
        ok = run["status"] == "COMPLETED"
        extra: dict[str, Any] = {}
        if self.a.pause:
            pe = max(self.state.get("paused_epochs") or [0])
            resumed_from = str(rr.get("resumed_from") or "")
            full_resume = bool(pe) and f"full_e{int(pe):04d}" in resumed_from
            att = max((j["attempt"] for j in jobs), default=0)
            ok = ok and full_resume and att >= 2 and int(rr.get("epochs_done") or 0) == 2
            extra = {"paused_epoch": pe, "resumed_from": resumed_from.split("/")[-1] if resumed_from else None,
                     "full_state_resume": full_resume, "max_attempt": att}
        return self.step("train-finish", ok, t4, run_id=run_id, candidate_id=cid, run_status=run["status"],
                         jobs=[(j["kind"], j["status"], j["attempt"], j["lease_owner"]) for j in jobs],
                         epochs_done=rr.get("epochs_done"), device_used=outputs.get("device_used"),
                         rung_result_keys=sorted(rr)[:30], **extra)

    def item_trainval(self) -> bool:
        t0 = time.monotonic()
        if "upload_id" not in self.state:
            c, up, _ = self.upload(Path(self.a.models) / "yolo11n_user.yaml")
            self.state["upload_id"] = up["upload_id"]
        body = {"source": {"kind": "user", "upload_id": self.state["upload_id"], "model_name": "yolo11n_user_val"},
                "protocol": self.training_protocol(2), "mode": "scratch", "validation_enabled": True,
                "idempotency_key": f"{self.a.label}-trainval-" + uuid.uuid4().hex[:8]}
        c, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        ok = c == 200 and isinstance(created, dict) and created.get("run_id")
        self.step("trainval-create", bool(ok), t0, http=c, request_id=rid_c,
                  body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id", "mode")})
        if not ok:
            return False
        run_id, cid = created["run_id"], created["candidate_id"]
        self.state.update(trainval_run=run_id, trainval_candidate=cid)
        t1 = time.monotonic()
        run = self.run_wait(run_id, self.a.timeout)
        cs, snap, rid_s = self.snap_lists(run_id)
        rows = [x for x in (snap.get("candidates") or []) if (x.get("candidate_id") or x.get("id")) == cid]
        cd, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        self.save(f"{self.a.label}_trainval_candidate_detail.json", det)
        self.save(f"{self.a.label}_trainval_snapshot_rows.json", rows)
        pub = rows[-1] if rows else {}
        db_rows = []
        for e in self.db("SELECT status, row_json FROM evaluations WHERE run_id = ?", (run_id,)):
            try:
                r = json.loads(e.get("row_json") or "null") or {}
            except (TypeError, ValueError):
                r = {}
            db_rows.append({"status": e["status"], "performance_status": r.get("performance_status"),
                            "reason_code": r.get("reason_code"), "checkpoint_path": r.get("checkpoint_path"),
                            "latency_source": r.get("latency_source"), "ap": r.get("ap"), "fitness": r.get("fitness"),
                            "device_used": (r.get("raw_metrics") or {}).get("device_used")})
        jobs = self.jobs(run_id)
        text = json.dumps(pub) + json.dumps(db_rows)
        ckpts = (det or {}).get("checkpoints") or [] if isinstance(det, dict) else []
        db_ok = (not self.a.db) or any(r["status"] == "SEARCH_COMPLETE" and r["performance_status"] == "NOT_EVALUATED"
                                       and r["reason_code"] == "EVALUATION_REQUIRES_CUDA" and r["checkpoint_path"]
                                       and r["ap"] is None and r["fitness"] is None for r in db_rows)
        ok = (run["status"] == "COMPLETED" and all(j["status"] == "SUCCEEDED" for j in jobs) and bool(jobs)
              and "EVALUATION_REQUIRES_CUDA" in text and "NOT_EVALUATED" in text and bool(ckpts) and db_ok)
        self.step("trainval-not-evaluated", ok, t1, run_id=run_id, candidate_id=cid, run_status=run["status"],
                  jobs=[(j["kind"], j["status"], j["attempt"], j["result_reason"]) for j in jobs],
                  public_row={k: pub.get(k) for k in ("status", "evaluation_status", "performance_status", "training_status",
                                                     "reason_code", "epochs", "rung", "ap", "fitness", "latency_source",
                                                     "checkpoint_path")},
                  db_rows=db_rows, detail_training_status=(det or {}).get("training_status") if isinstance(det, dict) else None,
                  detail_checkpoints=[{k: a.get(k) for k in ("id", "kind", "epoch", "state")} for a in ckpts],
                  request_ids=[rid_c, rid_s, rid_d])
        full = [a for a in ckpts if a.get("id") and a.get("kind") == "training_full"]
        if full:
            ok = self._download(run_id, max(full, key=lambda a: a.get("epoch") or 0), "trainval-checkpoint",
                                need_store=True) and ok
        else:
            ok = self.step("download-trainval-checkpoint", False, t1, reason="no training_full checkpoint listed") and ok
        return ok

    def item_sse(self) -> bool:
        """SSE through the gateway: full replay of a finished run (``Last-Event-ID: 0``) and a resumed read after the
        2nd event id (only later ids may come back)."""
        t0 = time.monotonic()
        run_id = self.state.get("train_run") or self.state["gen_run"]
        first = self.sse_read(run_id, last_event_id="0", max_seconds=8)
        ids = [int(i) for i in first["ids"] if str(i).isdigit()]
        increasing = all(b > a for a, b in zip(ids, ids[1:]))
        resumed = None
        if len(ids) >= 3:
            resumed = self.sse_read(run_id, last_event_id=str(ids[1]), max_seconds=6)
            rids = [int(i) for i in resumed["ids"] if str(i).isdigit()]
            resumed["all_after_cursor"] = bool(rids) and all(i > ids[1] for i in rids)
            resumed["first_ids"] = rids[:5]
        ok = (first["http"] == 200 and "text/event-stream" in (first["content_type"] or "") and len(ids) >= 3 and increasing
              and bool(resumed and resumed.get("all_after_cursor")))
        return self.step("sse-replay-and-resume", ok, t0, run_id=run_id,
                         replay={k: first[k] for k in ("http", "request_id", "content_type", "events", "types", "error")},
                         replay_first_ids=ids[:8], replay_last_id=ids[-1] if ids else None, ids_strictly_increasing=increasing,
                         resumed={k: resumed.get(k) for k in ("http", "request_id", "events", "all_after_cursor", "first_ids")}
                         if resumed else None)

    def prune_view(self, run_id: str) -> dict[str, Any]:
        reqs = []
        for r in self.db("SELECT id, checkpoint_id, runner_id, relpath, policy, reason, state, deliveries, delivered_job_id, "
                         "cancel_reason, report_json, reported_at FROM prune_requests WHERE run_id = ? ORDER BY created_at", (run_id,)):
            try:
                rep = json.loads(r.pop("report_json") or "null") or {}
            except (TypeError, ValueError):
                rep = {}
            r["report"] = {k: rep.get(k) for k in ("state", "already_absent", "removed", "replayed") if k in rep} or rep or None
            r["relpath"] = (r.get("relpath") or "").split("/")[-1]
            reqs.append(r)
        cks = []
        for r in self.db("SELECT id, kind, epoch, state, prune_json FROM checkpoints WHERE run_id = ? ORDER BY kind, epoch", (run_id,)):
            try:
                pj = json.loads(r.pop("prune_json") or "null") or {}
            except (TypeError, ValueError):
                pj = {}
            r["prune"] = {k: pj.get(k) for k in ("policy", "reason", "remote", "already_absent", "runner_id") if k in pj} or None
            cks.append(r)
        locs = self.db("SELECT object_id, store, state FROM artifact_locations WHERE run_id = ? AND object_kind = 'checkpoint' "
                       "ORDER BY object_id, store", (run_id,))
        return {"prune_requests": reqs, "checkpoints": cks, "checkpoint_locations": locs}

    def item_train3(self) -> bool:
        """3-epoch CPU training under last_rung_end_only (prune round trip, DD-A66/A68); a live SSE read during the run
        and (optionally) an external capture of the runner processes' environment while the job runs."""
        t0 = time.monotonic()
        if "upload_id" not in self.state:
            c, up, _ = self.upload(Path(self.a.models) / "yolo11n_user.yaml")
            self.state["upload_id"] = up["upload_id"]
        proto = self.training_protocol(3)
        proto["console"]["retention"] = {"full_state": "last_rung_end_only"}
        body = {"source": {"kind": "user", "upload_id": self.state["upload_id"], "model_name": "yolo11n_user_e3"},
                "protocol": proto, "mode": "scratch", "validation_enabled": False,
                "idempotency_key": f"{self.a.label}-train3-" + uuid.uuid4().hex[:8]}
        c, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        ok = c == 200 and isinstance(created, dict) and created.get("run_id")
        self.step("train3-create", bool(ok), t0, http=c, request_id=rid_c,
                  body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id")})
        if not ok:
            return False
        run_id, cid = created["run_id"], created["candidate_id"]
        self.state.update(train3_run=run_id, train3_candidate=cid)
        stop = threading.Event()
        live: dict[str, Any] = {}
        th = threading.Thread(target=lambda: live.update(self.sse_read(run_id, max_seconds=600, stop=stop, sock_timeout=40)),
                              daemon=True)
        th.start()
        captured = None
        if self.a.during_train_cmd:
            self.wait("train3 running", lambda: (lambda r: r if r and r.get("status") == "RUNNING" else None)(
                self.run_status(run_id)), 300, 0.5)
            time.sleep(3)
            p = subprocess.run(shlex.split(self.a.during_train_cmd), capture_output=True, text=True, timeout=120)
            captured = {"exit": p.returncode, "stdout_tail": self.red(p.stdout[-400:])}
        run = self.run_wait(run_id, self.a.timeout)
        time.sleep(6)  # heartbeats after the last checkpoint registration / job commit
        stop.set()
        th.join(timeout=45)
        cd, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        self.save(f"{self.a.label}_train3_candidate_detail.json", det)
        ckpts = (det or {}).get("checkpoints") or [] if isinstance(det, dict) else []
        view = self.prune_view(run_id)
        self.save(f"{self.a.label}_train3_prune_view_after_run.json", view)
        full = sorted([a for a in ckpts if a.get("kind") == "training_full"], key=lambda a: a.get("epoch") or 0)
        ok = run["status"] == "COMPLETED" and len(full) >= 3
        res = self.step("train3-finish", ok, t0, run_id=run_id, candidate_id=cid, run_status=run["status"],
                        public_full_states=[{k: a.get(k) for k in ("id", "epoch", "state")} for a in full],
                        prune_requests=[{k: r.get(k) for k in ("checkpoint_id", "state", "deliveries", "reason", "report")}
                                        for r in view["prune_requests"]],
                        live_sse={k: live.get(k) for k in ("http", "request_id", "events", "types", "error")},
                        runner_env_capture=captured, request_ids=[rid_c, rid_d])
        if full:
            last = full[-1]
            res = self._download(run_id, last, "train3-latest-checkpoint-by-id", need_store=True) and res
        return res

    def item_genconfig(self) -> bool:
        """Training whose source is a GENERATED candidate (source.kind=generated): the run COMPLETES and its config
        revisions record the executed configuration, downloadable as YAML with a sha256 match."""
        t0 = time.monotonic()
        gen_run, gcid = self.state["gen_run"], (self.a.gen_candidate or self.state["gen_candidate"])
        body = {"source": {"kind": "generated", "run_id": gen_run, "candidate_id": gcid},
                "protocol": self.training_protocol(2), "mode": "scratch", "validation_enabled": False,
                "idempotency_key": f"{self.a.label}-genconfig-" + uuid.uuid4().hex[:8]}
        c, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        ok = c == 200 and isinstance(created, dict) and created.get("run_id")
        self.step("genconfig-create", bool(ok), t0, http=c, request_id=rid_c, source=body["source"],
                  body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id", "source")})
        if not ok:
            return False
        run_id, cid = created["run_id"], created["candidate_id"]
        self.state.update(genconfig_run=run_id, genconfig_candidate=cid)
        t1 = time.monotonic()
        run = self.run_wait(run_id, self.a.timeout)
        jobs = self.jobs(run_id)
        cr, revs, rid_r, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions")
        self.save(f"{self.a.label}_genconfig_config_revisions.json", revs)
        rows = (revs or {}).get("revisions") or [] if isinstance(revs, dict) else []
        out = []
        for rv in rows:
            cy, ybody, rid_y, hy = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions/"
                                                  f"{rv.get('revision_id')}/yaml", raw=True)
            dg = hashlib.sha256(ybody).hexdigest() if isinstance(ybody, bytes) else None
            out.append({"revision_id": rv.get("revision_id"), "kind": rv.get("kind"), "parent": rv.get("parent_revision_id"),
                        "artifact_id": rv.get("artifact_id"), "listed_source_sha256": rv.get("source_sha256"),
                        "yaml_http": cy, "yaml_bytes": len(ybody) if isinstance(ybody, bytes) else None, "yaml_sha256": dg,
                        "yaml_content_type": hy.get("content-type"),
                        "sha_match": bool(dg) and str(rv.get("source_sha256") or "").removeprefix("sha256:") == dg,
                        "request_id": rid_y})
        kinds = [o["kind"] for o in out]
        executed = [o for o in out if o["kind"] == "executed"]
        generated = [o for o in out if o["kind"] == "generated"]
        gen_same_as_offline_artifact = (bool(generated) and self.state.get("gen_arch_sha") is not None
                                        and generated[0]["yaml_sha256"] == self.state.get("gen_arch_sha"))
        ok = (run["status"] == "COMPLETED" and bool(jobs) and all(j["status"] == "SUCCEEDED" for j in jobs)
              and cr == 200 and bool(executed) and all(o["yaml_http"] == 200 and o["sha_match"] for o in executed))
        return self.step("genconfig-executed-revision", ok, t1, run_id=run_id, candidate_id=cid, run_status=run["status"],
                         source_binding=created.get("source"), jobs=[(j["kind"], j["status"], j["attempt"], j["lease_owner"])
                                                                     for j in jobs],
                         revisions_http=cr, revision_kinds=kinds, revisions=out,
                         generated_revision_equals_off_architecture_artifact=gen_same_as_offline_artifact,
                         request_ids=[rid_c, rid_r])

    def item_gencheck(self) -> bool:
        """Diagnostic: the public candidate structure-check route on every generated candidate of the OFF run."""
        t0 = time.monotonic()
        ok_all = True
        c, snap, rid = self.snap_lists(self.state["gen_run"])
        for cand in snap.get("generated_candidates") or []:
            cid = cand.get("candidate_id") or cand.get("id")
            mods = sorted({b.get("module") for b in (cand.get("blocks") or []) if isinstance(b, dict) and b.get("module")})
            c2, doc, rid2, _ = self.gw("POST", f"/api/v1/runs/{self.state['gen_run']}/candidates/{cid}/structure-check",
                                      {"input_size": 64})
            st = doc.get("status") if isinstance(doc, dict) else None
            ok_all = self.step("gen-candidate-structure-check", c2 == 200 and st in ("PASS", "FAIL", "UNVERIFIED"), t0,
                               candidate_id=cid, generation_structure_status=cand.get("structure_status"), modules=mods,
                               http=c2, status=st, codes=sorted({d.get("code") for d in ((doc or {}).get("diagnostics") or [])
                                                                 if isinstance(d, dict)}) if isinstance(doc, dict) else doc,
                               request_id=rid2) and ok_all
        return ok_all

    def item_prunecheck(self) -> bool:
        """Prune round trip state after further runner jobs (orders ride on heartbeats of a lease): requests DONE and
        checkpoint rows PRUNED (with the runner's already_absent finding recorded as reported), latest full state kept."""
        t0 = time.monotonic()
        run_id, cid = self.state["train3_run"], self.state["train3_candidate"]
        view = self.prune_view(run_id)
        self.save(f"{self.a.label}_train3_prune_view_final.json", view)
        cd, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        ckpts = (det or {}).get("checkpoints") or [] if isinstance(det, dict) else []
        full = sorted([a for a in ckpts if a.get("kind") == "training_full"], key=lambda a: a.get("epoch") or 0)
        done = [r for r in view["prune_requests"] if r["state"] == "DONE"]
        pruned_rows = {r["id"] for r in view["checkpoints"] if r["state"] == "PRUNED"}
        latest = full[-1] if full else {}
        latest_ok = latest.get("state") in ("PRESENT", "READY", None) and latest.get("id") not in pruned_rows
        absent = [bool((r.get("report") or {}).get("already_absent")) for r in done]
        tries = []
        for a in full[:-1]:
            if a.get("id") in pruned_rows:
                c2, data2, rid2, h2 = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{a['id']}", raw=True, timeout=300)
                tries.append({"checkpoint_id": a["id"], "epoch": a.get("epoch"), "http": c2,
                              "bytes": len(data2) if isinstance(data2, bytes) else None,
                              "sha256_matches_header": isinstance(data2, bytes) and c2 == 200 and
                              hashlib.sha256(data2).hexdigest() == (h2.get("x-artifact-sha256") or "").removeprefix("sha256:"),
                              "request_id": rid2})
        ok = (bool(done) and all(r["checkpoint_id"] in pruned_rows for r in done) and latest_ok
              and len(view["prune_requests"]) >= 1)
        return self.step("prune-round-trip", ok, t0, run_id=run_id,
                         prune_requests=[{k: r.get(k) for k in ("checkpoint_id", "relpath", "state", "deliveries", "reason",
                                                                "cancel_reason", "report")} for r in view["prune_requests"]],
                         checkpoints=[{k: r.get(k) for k in ("id", "kind", "epoch", "state", "prune")} for r in view["checkpoints"]],
                         public_full_states=[{k: a.get(k) for k in ("id", "epoch", "state")} for a in full],
                         latest_full_state_kept=latest_ok, already_absent_reported=absent,
                         pruned_checkpoint_downloads_via_gateway_store=tries, request_id=rid_d)

    def item_results(self) -> bool:
        t0 = time.monotonic()
        run_id, cid = self.state["train_run"], self.state["train_candidate"]
        c1, run, r1, _ = self.gw("GET", f"/api/v1/runs/{run_id}")
        c2, snap, r2 = self.snap_lists(run_id)
        cl = {"candidates(evaluation rows)": snap.get("candidates"), "generated_candidates": snap.get("generated_candidates")}
        c3, det, r3, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        c4, r4 = c2, r2
        self.save(f"{self.a.label}_train_candidate_detail.json", det)
        self.save(f"{self.a.label}_train_candidates.json", cl)
        if isinstance(snap, dict):
            self.save(f"{self.a.label}_train_snapshot_keys.json", {k: (type(v).__name__, len(v) if hasattr(v, "__len__") else v)
                                                                  for k, v in snap.items()})
        cands = snap.get("candidates") or []
        row = next((x for x in cands if (x.get("candidate_id") or x.get("id")) == cid), {})
        det_ts = det.get("training_status") if isinstance(det, dict) else None
        ok = c1 == c2 == c3 == 200 and det_ts == "TRAINED"
        return self.step("result-rows", ok, t0, http=[c1, c2, c3, c4], request_ids=[r1, r2, r3, r4],
                         run_status=(run or {}).get("status") if isinstance(run, dict) else run,
                         evaluation_rows=len(cands), detail_training_status=det_ts,
                         candidate_row={k: row.get(k) for k in ("candidate_id", "name", "arm", "status", "training_status",
                                                               "evaluation_status", "epochs", "rung", "ap",
                                                               "latency_source", "checkpoint_path")},
                         detail_keys=sorted(det)[:50] if isinstance(det, dict) else det,
                         detail_artifacts=len((det or {}).get("artifacts") or []) if isinstance(det, dict) else None,
                         detail_checkpoints=len((det or {}).get("checkpoints") or []) if isinstance(det, dict) else None)

    def _download(self, run_id: str, art: dict[str, Any], label: str, need_store: bool = False) -> bool:
        t0 = time.monotonic()
        aid = art.get("id") or art.get("artifact_id")
        c, data, rid, h = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{aid}", raw=True, timeout=600)
        digest = hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else None
        header = (h.get("x-artifact-sha256") or "").removeprefix("sha256:")
        listed = str(art.get("sha256") or "").removeprefix("sha256:")
        store_ok = None
        if self.a.store_dir and digest:
            sp = Path(self.a.store_dir) / digest[:2] / digest
            store_ok = sp.is_file() and hashlib.sha256(sp.read_bytes()).hexdigest() == digest
        ok = c == 200 and isinstance(data, bytes) and len(data) > 0 and digest == header == listed
        self.state["last_download_sha"] = digest
        if need_store:
            ok = ok and bool(store_ok)
        return self.step(f"download-{label}", ok, t0, http=c, artifact_id=aid, kind=art.get("kind"), epoch=art.get("epoch"),
                         state_listed=art.get("state"), store_required=need_store,
                         relpath=art.get("relpath") or art.get("path") or art.get("name"), bytes=len(data) if isinstance(data, bytes) else None,
                         sha256=digest, header_sha256=header or None, listed_sha256=listed or None, in_gateway_store=store_ok,
                         verification=h.get("x-artifact-verification"), request_id=rid)

    def item_download(self) -> bool:
        run_id, cid = self.state["train_run"], self.state["train_candidate"]
        t0 = time.monotonic()
        c, det, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        arts = (det or {}).get("artifacts") or [] if isinstance(det, dict) else []
        ckpts = (det or {}).get("checkpoints") or [] if isinstance(det, dict) else []
        self.step("download-listing", c == 200 and bool(arts), t0, http=c, request_id=rid,
                  artifacts=[{k: a.get(k) for k in ("id", "kind", "state", "relpath", "bytes")} for a in arts][:40],
                  checkpoints=[{k: a.get(k) for k in ("id", "kind", "state", "epoch", "relpath", "bytes")} for a in ckpts][:20])
        ok = True
        # a runner-produced artifact (registered by the runner, bytes in the gateway store via local transfer) and a
        # core-local one (architecture YAML written by the core)
        runner_arts = [a for a in arts if a.get("id") and a.get("kind") != "architecture"]
        core_arts = [a for a in arts if a.get("id") and a.get("kind") == "architecture"]
        if runner_arts:
            ok = self._download(run_id, runner_arts[0], "runner-artifact", need_store=True) and ok
        else:
            ok = self.step("download-runner-artifact", False, t0, reason="no runner-registered artifact listed") and ok
        if core_arts:
            ok = self._download(run_id, core_arts[0], "core-artifact", need_store=False) and ok
        full = [a for a in ckpts if a.get("id") and a.get("kind") == "training_full"]
        wo = [a for a in ckpts if a.get("id") and a.get("kind") == "weights_only"]
        if full:
            ok = self._download(run_id, max(full, key=lambda a: a.get("epoch") or 0), "checkpoint-training_full",
                                need_store=True) and ok
        else:
            ok = self.step("download-checkpoint-training_full", False, t0, reason="no training_full checkpoint listed") and ok
        if wo:
            ok = self._download(run_id, wo[-1], "checkpoint-weights_only", need_store=True) and ok
        if runner_arts:  # the detail now shows the downloaded runner artifact READY (PRESENT@server_store)
            c2, det2, rid2, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
            st = next((a.get("state") for a in ((det2 or {}).get("artifacts") or []) if a.get("id") == runner_arts[0]["id"]), None)
            self.step("runner-artifact-state-after-download", st == "READY", t0, artifact_id=runner_arts[0]["id"],
                      state_before=runner_arts[0].get("state"), state_after=st, request_id=rid2)
        t1 = time.monotonic()
        c, data, rid, h = self.gw("GET", f"/api/v1/runs/{run_id}/export", raw=True, timeout=600)
        digest = hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else None
        hdr = (h.get("x-bundle-sha256") or "").removeprefix("sha256:")
        members: list[str] = []
        sums_ok = None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = zf.namelist()
                sums = next((m for m in members if m.endswith("SHA256SUMS")), None)
                if sums:
                    bad = 0
                    for line in zf.read(sums).decode().splitlines():
                        if not line.strip():
                            continue
                        dg, name = line.split(None, 1)
                        name = name.lstrip("*")
                        base = sums.rsplit("/", 1)[0] + "/" if "/" in sums else ""
                        cand = [m for m in members if m == base + name or m == name]
                        if not cand or hashlib.sha256(zf.read(cand[0])).hexdigest() != dg:
                            bad += 1
                    sums_ok = bad == 0
        except Exception as exc:  # noqa: BLE001
            members = [f"unreadable: {type(exc).__name__}"]
        if isinstance(data, bytes):
            (self.ev / f"{self.a.label}_export_{run_id}.zip.sha256").write_text(f"{digest}  export_{run_id}.zip\n")
        ok_e = c == 200 and digest == hdr and any(m.endswith("SHA256SUMS") for m in members) and sums_ok is not False
        return self.step("export-zip", ok_e, t1, http=c, bytes=len(data) if isinstance(data, bytes) else None,
                         sha256=digest, header_sha256=hdr or None, members=len(members), sha256sums_verified=sums_ok,
                         member_sample=members[:15], request_id=rid) and ok

    def item_decision(self) -> bool:
        t0 = time.monotonic()
        preset = self.presets()
        proto = json.loads(json.dumps(preset.get("protocol") or preset))
        ds = Path(self.a.dataset_yaml)
        proto["console"]["paths"].update({"dataset_yaml": str(ds), "output_root": self.a.output_root,
                                          "train_root": str(ds.parent / "images" / "train"),
                                          "search_val_root": str(ds.parent / "images" / "val"),
                                          "final_eval_root": str(ds.parent / "images" / "test")})
        proto["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                 warmup_epochs=0, scheduler_horizon_epochs=1)
        w = [r["fitness_weights"] for r in proto["rungs"]]
        proto["rungs"] = [{"id": "L0", "epoch_target_total": 1, "fitness_weights": w[0], "promotion": None}]
        proto["final_retraining"].update(epoch_target_total=1, scheduler_horizon_epochs=1)
        c_ = proto["console"]
        for b in c_.get("baselines") or []:
            b["enabled"] = False
        c_["baseline_override_reason"] = "T4/T5 CPU decision probe: search arms only (registry baselines unverified)"
        c_["search"].update(population=2, generations=1, seeds=[101], arms=["GA"])
        proto["comparison"]["independent_search_seed_pairs"] = [101]
        c_["label"] = "T4/T5 CPU decision probe (smoke values, not research results)"
        if self.a.bench_device:
            c_.setdefault("benchmark", {})["device"] = self.a.bench_device
        c, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": proto})
        errors = (v or {}).get("errors") if isinstance(v, dict) else v
        codes = sorted({e.get("code") for e in (errors or []) if isinstance(e, dict)})
        refused = c == 200 and isinstance(v, dict) and v.get("valid") is False and "EVALUATION_REQUIRES_CUDA" in codes
        self.step("decision-evaluation-on-search-refused", refused, t0, http=c, request_id=rid_v, valid=(v or {}).get("valid")
                  if isinstance(v, dict) else None, error_codes=codes, errors=errors,
                  note="PASS-by-design: a CPU-only fleet refuses an evaluation-ON search at validate (DD-A60/A62); "
                       "no promotion / final retrain can be produced on CPU")
        if not refused and isinstance(v, dict) and v.get("valid") is True:
            c2, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                                     "validation_id": v["validation_id"],
                                                                     "idempotency_key": f"{self.a.label}-dec-" + uuid.uuid4().hex[:8]})
            self.step("decision-unexpectedly-accepted", False, t0, http=c2, request_id=rid_c,
                      body=created if not isinstance(created, dict) else {k: created.get(k) for k in ("run_id", "status")})
        return refused

    def item_coredown(self) -> bool:
        t0 = time.monotonic()
        ch, health, rid_h, _ = self.gw("GET", "/api/v1/health")
        cv, bv, rid_v, hv = self.gw("POST", "/api/v1/protocols/validate", {"protocol": {"schema_version": "nas-policy/1"}})
        cr, br, rid_r, _ = self.gw("GET", "/api/v1/runs")
        core = (health or {}).get("core") if isinstance(health, dict) else None
        ok = (ch == 200 and isinstance(health, dict) and health.get("status") == "degraded"
              and (core == "unreachable" or (isinstance(core, dict) and core.get("reachable") is False))
              and cv == 503 and isinstance(bv, dict) and bv.get("code") == "CORE_UNAVAILABLE" and "valid" not in bv
              and cr == 503 and isinstance(br, dict) and br.get("code") == "CORE_UNAVAILABLE")
        return self.step("core-down-degraded-503", ok, t0, health_http=ch, health=health, validate_http=cv, validate_body=bv,
                         retry_after=hv.get("retry-after"), runs_http=cr, runs_code=(br or {}).get("code") if isinstance(br, dict) else br,
                         request_ids=[rid_h, rid_v, rid_r])

    def item_reqids(self) -> bool:
        t0 = time.monotonic()
        mine = {r["rid"] for r in self.request_ids}
        prev = self.ev / f"{self.a.label}_request_ids.jsonl"
        if prev.is_file():
            mine |= {json.loads(l)["rid"] for l in prev.read_text().splitlines() if l.strip()}
        echoed = sum(1 for r in self.request_ids if r["echoed"])
        found: dict[str, Any] = {}
        for name, path in (("audit", self.a.audit_log), ("gateway_log", self.a.gw_log), ("core_log", self.a.core_log)):
            if not path or not Path(path).is_file():
                found[name] = "확인 못 함 (no file)"
                continue
            text = Path(path).read_text(errors="replace")
            hits = sorted(r for r in mine if r in text)
            found[name] = {"file": path, "ids_found": len(hits), "sample": hits[:5]}
        aud = found.get("audit")
        ok = isinstance(aud, dict) and aud["ids_found"] > 0 and echoed == len(self.request_ids)
        return self.step("request-ids", ok, t0, harness_ids=len(mine), echoed_this_session=f"{echoed}/{len(self.request_ids)}",
                         found=found)

    def run(self) -> int:
        ok_all = True
        for it in [x.strip() for x in self.a.items.split(",") if x.strip()]:
            t0 = time.monotonic()
            try:
                ok = getattr(self, f"item_{it}")()
            except Exception as exc:  # noqa: BLE001
                ok = self.step(f"{it}-error", False, t0, error=f"{type(exc).__name__}: {exc}"[:1500])
            self.results[it] = "PASS" if ok else "FAIL"
            ok_all = ok_all and ok
        with open(self.ev / f"{self.a.label}_request_ids.jsonl", "a", encoding="utf-8") as f:
            for r in self.request_ids:
                f.write(json.dumps(r) + "\n")
        (self.ev / f"{self.a.label}_state.json").write_text(json.dumps(self.state, indent=1))
        self.step("summary", ok_all, time.monotonic(), items=self.results)
        return 0 if ok_all else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gw", required=True)
    p.add_argument("--token-env", required=True)
    p.add_argument("--token-key", default="MBNAS_GW_API_TOKEN")
    p.add_argument("--redact-env", action="append", default=[], help="file:KEY of further secrets to redact")
    p.add_argument("--label", required=True)
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--items", required=True)
    p.add_argument("--models", required=True)
    p.add_argument("--gen-protocol", required=False)
    p.add_argument("--dataset-yaml", required=False)
    p.add_argument("--output-root", required=False)
    p.add_argument("--split-paths", action="store_true")
    p.add_argument("--pause", action="store_true")
    p.add_argument("--db")
    p.add_argument("--store-dir")
    p.add_argument("--web-dist")
    p.add_argument("--audit-log")
    p.add_argument("--gw-log")
    p.add_argument("--core-log")
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--bench-device", help="console.benchmark.device (e.g. cpu on a CPU-only fleet)")
    p.add_argument("--forbid-health", action="append", default=[], help="string that must not appear in GET /health")
    p.add_argument("--dump-dir", help="write every response body here (T8 runtime capture)")
    p.add_argument("--gen-candidate", help="generated candidate id for item genconfig (default: the first one)")
    p.add_argument("--during-train-cmd", help="command run once while the train3 job is RUNNING (env capture)")
    p.add_argument("--expect-health-allowlists", help="JSON the health 'allowlists' must equal")
    return H(p.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
