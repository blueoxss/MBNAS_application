#!/usr/bin/env python3
"""FIX7 / DD-A75 F1 real-process check: absolute runner paths in the public raw_metrics (evidence script, not a
repository script).

Reuses the unchanged FIX5 smoke classes (``../../FIX5_T3_34/fix5_prune_roundtrip_smoke.py`` ->
``scripts/dev/wp3_three_process_smoke.py``: core + owner worker + REAL runner (ultralytics, CPU) + gateway as real
processes on 127.0.0.1 ephemeral ports, ``CUDA_VISIBLE_DEVICES=''``) and drives the gateway's user API only:

1. ``POST /api/v1/training-runs`` with the uploaded yolo11n YAML (nc 1), 1 epoch, 64 px, ``validation_enabled=true``.
   The core inventory is the registered runners (``--device-source runners``: CPU-only fleet as in T4, no NVML query
   of this host) and ``console.benchmark.device = cpu``; the CPU runner trains, cannot evaluate (DD-A60 (4)) and the core
   commits a NOT_EVALUATED row whose STORED ``raw_metrics.resolved_recipe`` holds the runner's absolute paths
   (positive control, read from the core DB read-only; stored rows are never rewritten);
2. through the gateway with the user bearer: the snapshot, the candidate detail, the SSE replay from seq 0 to
   ``last_seq`` and the run export zip (default profile);
3. every ``mbnas_core.console.projection._ABS_PATH`` match of those bodies (the ``/api/v1`` paths the scrub keeps are
   excluded) is located by JSON pointer and classified ``raw_metrics`` vs elsewhere; the stored absolute recipe values
   are searched verbatim in every body.

``--expect leak`` (pre-fix code) passes when the raw_metrics paths ARE visible; ``--expect clean`` (fixed code) passes
when no body has a match inside ``raw_metrics`` and no stored recipe path appears anywhere. Matches elsewhere are
reported, not judged (P2 T4 classified the client-provided ``resolved_protocol.console.paths`` echo separately).

usage: runtime/venv/bin/python -B <this file> --root DIR --label NAME --expect {leak,clean} [--evidence FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "scripts" / "dev" / "wp3_three_process_smoke.py").is_file())
_spec = importlib.util.spec_from_file_location("fix5_prune_roundtrip_smoke", ROOT / "docs" / "internal" / "evidence" /
                                               "WP7" / "FIX5_T3_34" / "fix5_prune_roundtrip_smoke.py")
fix5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix5)  # type: ignore[union-attr]
PY, now, TERMINAL = fix5.PY, fix5.now, fix5.TERMINAL

if str(ROOT / "mbnas_core" / "src") not in sys.path:
    sys.path.append(str(ROOT / "mbnas_core" / "src"))
from mbnas_core.console import projection  # noqa: E402  (the regex under test; never used to project here)

ABS, KEEP = projection._ABS_PATH, projection._PUBLIC_PATH_PREFIXES
PROJECTION_PY = ROOT / "mbnas_core" / "src" / "mbnas_core" / "console" / "projection.py"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_matches(text: str) -> list[str]:
    return [m.group(0) for m in ABS.finditer(text) if not m.group(0).startswith(KEEP)]


def json_matches(doc: Any, where: str = "$") -> list[tuple[str, str]]:
    """(JSON pointer, match) of every non-``/api/v1`` ``_ABS_PATH`` match in the strings and keys of ``doc``."""
    out: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                out.extend((f"{path}.<key>", m) for m in text_matches(str(k)))
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            out.extend((path, m) for m in text_matches(node))

    walk(doc, where)
    return out


def pattern(pointer: str) -> str:
    return re.sub(r"\[\d+\]", "[]", pointer)


class F1Smoke(fix5.PruneSmoke):
    def policy(self, ds: dict[str, str]) -> dict[str, Any]:
        p = self.training_policy(ds)  # FIX5: PILOT_SMOKE preset, 64 px, batch 2, workers 0, fp32, SGD
        p["console"]["training"]["epoch_target_total"] = 1
        p["console"]["retention"] = {"full_state": "keep_all"}
        p["console"]["benchmark"]["device"] = "cpu"  # the CPU-only inventory's device (no host GPU named)
        return p

    def compute_apps(self) -> str:
        try:
            return subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv"],
                                  capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            return f"nvidia-smi unavailable: {exc}"

    def sample_gpu(self) -> None:
        """Record any GPU compute app started from this project's runtime (core, worker, runner, job children)."""
        text = self.compute_apps()
        self.gpu_samples += 1
        for line in text.splitlines()[1:]:
            if str(ROOT) in line or "mbnas" in line:
                self.own_gpu_lines.add(line.strip())

    def read_sse(self, run_id: str, last_seq: int, timeout: float = 60.0) -> tuple[int, str, int]:
        import httpx

        rid = "f1-sse-" + uuid.uuid4().hex[:12]
        h = {"Authorization": f"Bearer {self.user_token}", "X-Request-ID": rid, "Accept": "text/event-stream"}
        lines, seen, status = [], 0, 0
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c, \
                c.stream("GET", f"{self.gw_base}/api/v1/runs/{run_id}/events?after_seq=0", headers=h) as r:
            status = r.status_code
            for line in r.iter_lines():
                lines.append(line)
                if line.startswith("id: ") and line[4:].strip().isdigit():
                    seen = max(seen, int(line[4:].strip()))
                if (line == "" and seen >= last_seq) or time.monotonic() > deadline:
                    break
        self.note(f"GW GET /api/v1/runs/{run_id}/events?after_seq=0 -> {status} request_id={rid} last id {seen}")
        return status, "\n".join(lines), seen

    def step_train(self, ds: dict[str, str]) -> tuple[str, str, dict[str, str]] | None:
        t0 = time.monotonic()
        up = self.upload("yolo11n.yaml")
        body = {"source": {"kind": "user", "upload_id": up["upload_id"]}, "protocol": self.policy(ds),
                "mode": "scratch", "validation_enabled": True, "idempotency_key": "f1-train-" + uuid.uuid4().hex[:8]}
        code, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        if code != 200 or not isinstance(created, dict):
            self.step("validation-on-training-run", False, t0, http=code, body=created, request_id=rid_c)
            return None
        run_id, cid = created["run_id"], created["candidate_id"]
        def poll() -> dict[str, Any] | None:
            self.sample_gpu()
            r = self.run_status(run_id)
            return r if r and r.get("status") in TERMINAL else None

        run = self.wait(f"run {run_id} terminal", poll, timeout=self.args.timeout, interval=2.0)
        jobs = self.db_rows("SELECT kind, status, lease_owner FROM jobs WHERE run_id = ?", (run_id,))
        stored = [json.loads(r["row_json"]) for r in self.db_rows(
            "SELECT row_json FROM evaluations WHERE run_id = ?", (run_id,))]
        rows, recipe_abs = [], {}
        for row in stored:
            raw = row.get("raw_metrics") or {}
            recipe = raw.get("resolved_recipe") if isinstance(raw.get("resolved_recipe"), dict) else {}
            abs_keys = {k: v for k, v in recipe.items() if isinstance(v, str) and text_matches(v)}
            recipe_abs.update(abs_keys)
            rows.append({"id": row.get("id"), "status": row.get("status"),
                         "performance_status": row.get("performance_status"), "reason_code": row.get("reason_code"),
                         "validation_enabled": raw.get("validation_enabled"), "device_used": raw.get("device_used"),
                         "resolved_recipe_keys": len(recipe), "stored_abs_recipe_keys": sorted(abs_keys)})
        ok = (run["status"] == "COMPLETED" and any(r["performance_status"] == "NOT_EVALUATED" and r["validation_enabled"]
                                                  is True for r in rows)
              and bool(recipe_abs))
        self.step("validation-on-training-run", bool(ok), t0, run_id=run_id, candidate_id=cid, run_status=run["status"],
                  jobs=jobs, rows=rows, request_id=rid_c,
                  positive_control_stored_resolved_recipe_abs=recipe_abs)
        return (run_id, cid, recipe_abs) if ok else None

    def step_fetch_and_scan(self, run_id: str, cid: str, recipe_abs: dict[str, str]) -> bool:
        t0 = time.monotonic()
        bodies: dict[str, tuple[int, str, Any]] = {}
        code, snap, rid_s, _ = self.gw("GET", f"/api/v1/runs/{run_id}/snapshot", raw=True)
        text = snap.decode("utf-8", "replace")
        bodies["snapshot"] = (code, text, json.loads(text) if code == 200 else None)
        code, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}", raw=True)
        text = det.decode("utf-8", "replace")
        bodies["candidate_detail"] = (code, text, json.loads(text) if code == 200 else None)
        last_seq = int((bodies["snapshot"][2] or {}).get("last_seq") or 0)
        sse_code, sse_text, seen = self.read_sse(run_id, last_seq)
        events = [json.loads(ln[len("data: "):]) for ln in sse_text.splitlines() if ln.startswith("data: ")]
        bodies["sse_replay"] = (sse_code, sse_text, events)
        export_code, zbytes, rid_z, _ = self.gw("GET", f"/api/v1/runs/{run_id}/export", raw=True)
        members: list[str] = []
        if export_code == 200:
            with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                for name in zf.namelist():
                    data = zf.read(name).decode("utf-8", "replace")
                    members.append(name)
                    doc: Any = None
                    if name.endswith(".json"):
                        doc = json.loads(data)
                    elif name.endswith(".jsonl"):
                        doc = [json.loads(x) for x in data.splitlines() if x.strip()]
                    bodies[f"export!{name}"] = (export_code, data, doc)
        else:
            bodies["export"] = (export_code, zbytes.decode("utf-8", "replace")[:400], None)
        report: dict[str, Any] = {}
        totals = {"text": 0, "raw_metrics": 0, "elsewhere": 0, "recipe_values_verbatim": 0}
        for name, (code_, text_, doc_) in bodies.items():
            located = json_matches(doc_) if doc_ is not None else []
            in_raw = [(p, m) for p, m in located if ".raw_metrics" in p]
            other = [(p, m) for p, m in located if ".raw_metrics" not in p]
            verbatim = sorted(k for k, v in recipe_abs.items() if v in text_)
            groups: dict[str, list[str]] = {}
            for p, m in other:
                groups.setdefault(pattern(p), []).append(m)
            report[name] = {"http": code_, "bytes": len(text_), "text_matches": len(text_matches(text_)),
                            "raw_metrics_matches": len(in_raw),
                            "raw_metrics_pointers": sorted({pattern(p) for p, _ in in_raw}),
                            "elsewhere_matches": len(other),
                            "elsewhere_by_pointer": {k: {"count": len(v), "sample": v[0]} for k, v in sorted(groups.items())},
                            "stored_recipe_values_found_verbatim": verbatim}
            totals["text"] += report[name]["text_matches"]
            totals["raw_metrics"] += len(in_raw)
            totals["elsewhere"] += len(other)
            totals["recipe_values_verbatim"] += len(verbatim)
        carried = sum(1 for e in events if isinstance((e.get("payload") or {}).get("raw_metrics"), dict))
        fetched = (bodies["snapshot"][0] == 200 and bodies["candidate_detail"][0] == 200 and sse_code == 200
                   and seen >= last_seq > 0 and carried > 0)  # export: scanned when 200, not required
        if self.args.expect == "clean":
            verdict = fetched and totals["raw_metrics"] == 0 and totals["recipe_values_verbatim"] == 0
        else:
            verdict = fetched and totals["raw_metrics"] > 0 and totals["recipe_values_verbatim"] > 0
        self.scan = {"totals": totals, "bodies": report, "sse": {"last_seq": last_seq, "replayed_to": seen,
                                                                   "events": len(events),
                                                                   "events_with_raw_metrics": carried},
                     "export_members": members}
        return self.step(f"fetch-and-scan(expect={self.args.expect})", bool(verdict), t0, totals=totals,
                         http={"snapshot": bodies["snapshot"][0], "candidate_detail": bodies["candidate_detail"][0],
                               "sse_replay": sse_code, "export": export_code},
                         sse_events=len(events), sse_events_with_raw_metrics=carried, last_seq=last_seq,
                         request_ids=[rid_s, rid_d, rid_z])

    def run(self) -> int:
        started, t_all = now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scan: dict[str, Any] = {}
        self.projection_sha = {"start": file_sha(PROJECTION_PY)}
        self.gpu_apps = {"before": self.compute_apps()}
        self.gpu_samples, self.own_gpu_lines = 0, set()
        self.note(f"smoke root {self.root}; interpreter {PY}; core port {self.port}; gateway port {self.gw_port}; "
                  f"projection.py sha256 {self.projection_sha['start']}; label {self.args.label}")
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            gw_ok = self.start_gateway()
            trained = self.step_train(ds)
            ok = bool(trained) and self.step_fetch_and_scan(*trained)
            self.note(f"gateway-start step ok={gw_ok} (recorded; not part of the F1 verdict)")
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            self.gpu_apps["during_or_after"] = self.compute_apps()
            pids = {n: p.pid for n, p in self.procs.items()}
            c = self.stop_all()
        self.projection_sha["end"] = file_sha(PROJECTION_PY)
        own_on_gpu = sorted(self.own_gpu_lines | {
            line for line in self.gpu_apps["during_or_after"].splitlines()
            if line.split(",")[0].strip() in {str(p) for p in pids.values()}})
        self.step("no-own-process-on-gpu", not own_on_gpu, t_all, own_compute_apps=own_on_gpu,
                  samples_during_training=self.gpu_samples)
        ok = ok and c and not own_on_gpu and self.projection_sha["start"] == self.projection_sha["end"]
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        head = [f"# FIX7 DD-A75 F1 real-process raw_metrics path check, label={self.args.label}, "
                f"expect={self.args.expect} ({started} .. {now()}, {dur:.1f} s) result={'PASS' if ok else 'FAIL'}",
                f"# command: {PY} -B {Path(__file__).relative_to(ROOT)} {' '.join(sys.argv[1:])}",
                f"# cwd: {ROOT}  CUDA_VISIBLE_DEVICES=''  device source: {self.args.device_source}  core: {self.base}  "
                f"gateway: {self.gw_base}",
                f"# smoke root: {self.root}",
                f"# projection.py sha256 at start/end: {self.projection_sha.get('start')} / {self.projection_sha.get('end')}",
                f"# _ABS_PATH: {ABS.pattern}   kept prefixes: {list(KEEP)}"]
        doc = {"result": "PASS" if ok else "FAIL", "label": self.args.label, "expect": self.args.expect,
               "projection_py_sha256": self.projection_sha, "steps": self.steps, "scan": self.scan,
               "gpu_compute_apps": self.gpu_apps, "step_log": self.log}
        text = "\n".join(head) + "\n" + json.dumps(doc, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        for secret in (self.user_token, self.api_token, *self.tokens.values()):
            text = text.replace(secret, "<redacted>")
        out.write_text(text)
        print(f"evidence -> {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", required=True, help="smoke root (outside the repository)")
    p.add_argument("--label", required=True)
    p.add_argument("--expect", choices=("leak", "clean"), required=True)
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--device-source", choices=("local", "runners"), default="runners")
    p.add_argument("--evidence", default=None)
    args = p.parse_args(argv)
    if not args.evidence:
        args.evidence = str(HERE / f"30_real_process_{args.label}.txt")
    return F1Smoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
