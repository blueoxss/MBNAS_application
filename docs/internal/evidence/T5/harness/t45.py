#!/usr/bin/env python3
"""T4/T5 client harness (WP-8). Drives a RUNNING gateway only through its user ``/api/v1`` (user bearer), stdlib only
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
        self.request_ids.append({"rid": rid, "method": method, "path": path, "status": code, "echoed": echoed == rid})
        self.http_f.write(self.red(f"{utc()} {method} {path} -> {code} rid={rid} echoed={echoed == rid} "
                                   f"{round(time.monotonic() - t0, 3)}s\n"))
        self.http_f.flush()
        return code, parsed, rid, hdrs

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
        return self.step("health", ok, t0, no_token_http=c0, http=c1, request_ids=[r0, r1],
                         status=(b1 or {}).get("status") if isinstance(b1, dict) else b1, core=core,
                         contract_version=(b1 or {}).get("contract_version") if isinstance(b1, dict) else None,
                         gateway=(b1 or {}).get("gateway") if isinstance(b1, dict) else None)

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
        self.save(f"{self.a.label}_gen_candidates.json", cl)
        jobs = self.jobs(run_id)
        ok = (run["status"] == "COMPLETED" and cc == 200 and len(cands) >= 1 and all(t == "UNTRAINED" for t in ts)
              and bool(detail_ok))
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
        valid = c == 200 and isinstance(v, dict) and v.get("valid") is True
        self.step("decision-validate", valid, t0, http=c, request_id=rid_v,
                  errors=(v or {}).get("errors") if isinstance(v, dict) else v)
        if not valid:
            return False
        t1 = time.monotonic()
        c, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                                "validation_id": v["validation_id"],
                                                                "idempotency_key": f"{self.a.label}-dec-" + uuid.uuid4().hex[:8]})
        if c != 200:
            return self.step("decision-search-run", False, t1, http=c, body=created, request_id=rid_c)
        run_id = created["run_id"]
        run = self.run_wait(run_id, self.a.timeout * 2)
        cc, snap, rid_l = self.snap_lists(run_id)
        rid_s = rid_l
        cands = snap.get("candidates") or []
        slim = {k: snap.get(k) for k in ("run", "candidates", "generations", "events") if k in snap}
        self.save(f"{self.a.label}_decision_snapshot.json", slim)
        dec = [{k: x.get(k) for k in ("candidate_id", "arm", "status", "evaluation_status", "training_status", "rung",
                                      "reason_code", "gate", "latency_source", "fitness", "ap")} for x in cands]
        evals = self.db("SELECT id, candidate_id, status, row_json FROM evaluations WHERE run_id = ?", (run_id,)) \
            if self.a.db else []
        ev_view = []
        for e in evals:
            try:
                r = json.loads(e.get("row_json") or "null") or {}
            except (TypeError, ValueError):
                r = {}
            ev_view.append({"candidate_id": e["candidate_id"], "status": e["status"], "reason_code": r.get("reason_code"),
                            "gate": (r.get("gate") or {}).get("reason_code") if isinstance(r.get("gate"), dict) else r.get("gate"),
                            "latency_source": r.get("latency_source"), "ap": r.get("ap"),
                            "device_used": (r.get("raw_metrics") or {}).get("device_used")})
        self.step("decision-search-run", run["status"] in TERMINAL, t1, run_id=run_id, run_status=run["status"],
                  run_reason=run.get("reason_code"), request_ids=[rid_c, rid_l, rid_s], candidates=dec,
                  evaluations_db=ev_view, jobs=[(j["kind"], j["status"], j["result_reason"]) for j in self.jobs(run_id)])
        t2 = time.monotonic()
        c, fr, rid_f, _ = self.gw("POST", f"/api/v1/runs/{run_id}/final-retrain",
                                  {"idempotency_key": f"{self.a.label}-final-" + uuid.uuid4().hex[:8],
                                   "budget_gpu_hours": 0.25, "top_k_per_arm_per_seed": 1,
                                   "reason": "T4/T5 decision step on CPU"})
        code = fr.get("code") if isinstance(fr, dict) else None
        child = fr.get("run_id") if isinstance(fr, dict) else None
        res: dict[str, Any] = {"http": c, "request_id": rid_f, "code": code,
                               "message": fr.get("message") if isinstance(fr, dict) else fr,
                               "core_request_id": fr.get("core_request_id") if isinstance(fr, dict) else None,
                               "error_request_id": fr.get("request_id") if isinstance(fr, dict) else None}
        if child:
            crun = self.run_wait(child, self.a.timeout * 2)
            res.update(child_run=child, child_status=crun["status"])
        # a decision is VISIBLE when the core answered with an explicit decision: a child FINAL_RETRAIN run or the
        # documented 409 NO_FINALIST refusal (CPU evaluations keep latency_source=pending, DD-A32)
        visible = (c == 200 and bool(child)) or (c == 409 and code == "NO_FINALIST")
        self.state["decision_run"] = run_id
        return self.step("decision-final-retrain", visible, t2, **res)

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
    p.add_argument("--bench-device", help="console.benchmark.device (CPU runner id with MBNAS_CORE_DEVICE_SOURCE=runners)")
    return H(p.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
