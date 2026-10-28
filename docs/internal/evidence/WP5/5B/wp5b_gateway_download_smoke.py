#!/usr/bin/env python3
"""WP-5B real-process check of the gateway artifact wiring (scratch wrapper kept with the evidence, not a repository
script; it changes nothing under scripts/).

Uses the WP-3 three-process smoke plumbing (``scripts/dev/wp3_three_process_smoke.py`` -> ``Smoke3``: uvicorn core with
``MBNAS_CORE_EXPOSE_LEGACY_API=false``, owner worker, ``python -m mbnas_runner run``, uvicorn gateway; principals via
``mbnas-core-principal``; ephemeral 127.0.0.1 ports, never 8000-8002 / 5173-5175) with one change: the runner process
gets ``MBNAS_RUNNER_ARTIFACT_TRANSFER=local`` and ``MBNAS_RUNNER_ARTIFACT_STORE_DIR`` = the gateway's
``MBNAS_GW_ARTIFACT_STORE``. Core and gateway use DIFFERENT artifact signing keys (smoke defaults).

Steps (everything user-facing goes through the gateway with the user bearer; the core DB is read read-only):

1. inputs, core, principals, owner worker + runner (local transfer), gateway;
2. ``POST /api/v1/training-runs`` (user YAML by legacy ``model_path`` under the gateway's model roots, PILOT_SMOKE recipe,
   REAL ultralytics yolo11n CPU training, 1 epoch, 64 px, native plots) -> COMPLETED; the gateway attaches its host
   inputs (DD-A46);
3. every artifact and checkpoint the runner registered: core record NOT_READY -> ``GET /api/v1/runs/{run}/artifacts/{id}``
   200 with ``X-Artifact-Sha256`` == sha256(body) == registered digest -> ``artifact_locations`` server_store PRESENT
   (verified_by store) and record READY;
4. a URL signed in a core view (candidate detail, core key) used WITHOUT bearer through the gateway -> 200; a forged
   signature -> 403; ``POST .../refresh`` -> gateway-signed URL -> 200;
5. ``GET /api/v1/runs/{run}/export`` -> zip with ``architectures/<id>.yaml`` bytes from the store (sha match) and a
   consistent ``SHA256SUMS``;
6. stop every process started here.

usage: runtime/venv/bin/python -B <this file> [--root DIR] [--evidence FILE] [--timeout S]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
HERE = Path(__file__).resolve().parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wp3 = _load("wp3_three_process_smoke", "scripts/dev/wp3_three_process_smoke.py")


class Smoke5B(wp3.Smoke3):
    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        env = super().runner_env(name, runner_id)
        env.update(MBNAS_RUNNER_ARTIFACT_TRANSFER="local",
                   MBNAS_RUNNER_ARTIFACT_STORE_DIR=str(self.root / "gateway" / "artifacts"))
        return env

    def no_bearer(self, path: str, params: dict[str, str]):
        import httpx

        return httpx.get(self.gw_base + path, params=params, timeout=120)

    def record(self, run_id: str, oid: str) -> dict:
        code, body, _ = self.http("GET", f"/core/v1/console/runs/{run_id}/artifacts/{oid}/record",
                                  token=self.tokens["server"])
        return body if code == 200 and isinstance(body, dict) else {"http": code, "body": body}

    def step_train(self) -> str | None:
        t0 = time.monotonic()
        sys.path.append(str(ROOT / "mbnas_core" / "src"))
        from mbnas_core.protocol.presets import pilot_smoke_protocol

        ds_yaml = self.root / "core_data" / "smoke4" / "data.yaml"
        policy = pilot_smoke_protocol({"dataset_yaml": str(ds_yaml), "output_root": str(self.root / "core" / "runs")})
        policy["training"].update(requested_input_size=[64, 64], batch_per_gpu=2, workers=0, precision="fp32",
                                  warmup_epochs=0, optimizer="SGD", lr_initial=0.001, close_mosaic_epochs=0)
        policy["console"]["training"] = {"epoch_target_total": 1, "seed": 41, "native_image_artifacts": True}
        body = {"source": {"kind": "user", "model_path": str(self.root / "uploads" / "yolo11n.yaml")},
                "protocol": policy, "mode": "scratch", "validation_enabled": False,
                "idempotency_key": "wp5b-smoke-train-" + uuid.uuid4().hex[:8]}
        code, created, rid, _ = self.gw("POST", "/api/v1/training-runs", body)
        if code != 200:
            self.step("train-via-gateway", False, t0, http=code, body=created, request_id=rid)
            return None
        run_id = created["run_id"]

        def terminal():
            c, run, _, _ = self.gw("GET", f"/api/v1/runs/{run_id}")
            return run if c == 200 and run.get("status") in wp3.wp2.TERMINAL else None

        run = self.wait(f"run {run_id} terminal", terminal, timeout=self.args.timeout, interval=2.0)
        jobs = self.db_rows("SELECT kind, status, attempt, lease_owner, result_json FROM jobs WHERE run_id = ?",
                            (run_id,))
        transfer = [(json.loads(j["result_json"] or "null") or {}).get("outputs", {}).get("artifact_transfer")
                    for j in jobs]
        ok = run["status"] == "COMPLETED" and all(isinstance(t, dict) and t.get("ok") for t in transfer)
        self.step("train-via-gateway", bool(ok), t0, run_id=run_id, run_status=run["status"], request_id=rid,
                  jobs=[(j["kind"], j["status"], j["attempt"], j["lease_owner"]) for j in jobs],
                  artifact_transfer=transfer)
        return run_id if ok else None

    def step_downloads(self, run_id: str) -> bool:
        t0 = time.monotonic()
        runner_objs = {(r["object_kind"], r["object_id"]) for r in self.db_rows(
            "SELECT object_kind, object_id FROM artifact_locations WHERE run_id = ? AND store = 'runner_local'",
            (run_id,))}
        rows = [("artifact", r["id"], r["sha256"], r["kind"]) for r in
                self.db_rows("SELECT id, sha256, kind FROM artifacts WHERE run_id = ?", (run_id,))]
        rows += [("checkpoint", r["id"], r["sha256"], "checkpoint") for r in
                 self.db_rows("SELECT id, sha256 FROM checkpoints WHERE run_id = ?", (run_id,))]
        results, ok = [], True
        for obj, oid, sha, kind in rows:
            sha = str(sha).removeprefix("sha256:")
            before = self.record(run_id, oid)
            code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{run_id}/artifacts/{oid}", raw=True)
            got = hashlib.sha256(data).hexdigest() if code == 200 else None
            after = self.record(run_id, oid)
            locs = self.db_rows("SELECT store, state, verified_by FROM artifact_locations WHERE object_id = ? "
                                "AND store = 'server_store'", (oid,))
            runner = (obj, oid) in runner_objs
            good = (code == 200 and got == sha == resp.headers.get("X-Artifact-Sha256")
                    and (not runner or (before.get("state") == "NOT_READY" and after.get("state") == "READY"
                                        and locs == [{"store": "server_store", "state": "PRESENT",
                                                      "verified_by": "store"}])))
            ok = ok and good
            results.append({"object": obj, "id": oid, "kind": kind, "origin": before.get("origin"), "http": code,
                            "bytes": len(data), "sha_ok": got == sha, "verification": resp.headers.get(
                                "X-Artifact-Verification"), "state_before": before.get("state"),
                            "state_after": after.get("state"), "server_store_rows": locs, "ok": good,
                            "request_id": rid})
        ok = ok and len(runner_objs) > 0
        return self.step("download-every-object-via-gateway", ok, t0, objects=len(rows),
                         runner_objects=len(runner_objs), results=results)

    def step_signed_urls(self, run_id: str) -> bool:
        t0 = time.monotonic()
        snap = self.snapshot_gw(run_id)
        cands = snap.get("candidates") or []
        cid = cands[0].get("candidate_id") if cands else None
        code, detail, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        arts = [a for e in (detail.get("evaluations") or []) for a in (e.get("artifacts") or [])] \
            if code == 200 else []
        arts += detail.get("artifacts") or [] if code == 200 else []
        signed = [a for a in arts if a.get("auth_mode") == "signed_url"]
        checks = []
        for a in signed[:3]:
            parts = urlsplit(a["url"])
            q = {k: v[0] for k, v in parse_qs(parts.query).items()}
            r = self.no_bearer(parts.path, q)
            forged = self.no_bearer(parts.path, {**q, "sig": "0" * 64})
            checks.append({"id": a["id"], "core_signed_http": r.status_code,
                           "sha_ok": hashlib.sha256(r.content).hexdigest() == r.headers.get("X-Artifact-Sha256"),
                           "forged_http": forged.status_code, "forged_code": forged.json().get("code")})
        any_id = (signed or arts or [{}])[0].get("id")
        refreshed = {}
        if any_id:
            c, view, _, _ = self.gw("POST", f"/api/v1/runs/{run_id}/artifacts/{any_id}/refresh")
            parts = urlsplit(view.get("url", "")) if c == 200 else None
            r = self.no_bearer(parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}) if parts else None
            refreshed = {"http": c, "auth_mode": view.get("auth_mode") if c == 200 else None,
                         "download_http": r.status_code if r is not None else None}
        ok = (bool(signed) and all(c["core_signed_http"] == 200 and c["sha_ok"] and c["forged_http"] == 403
                                   for c in checks) and refreshed.get("download_http") == 200)
        return self.step("signed-urls-core-and-gateway-keys", bool(ok), t0, candidate=cid, request_id=rid,
                         artifacts=len(arts), signed=len(signed), checks=checks, refresh=refreshed)

    def snapshot_gw(self, run_id: str) -> dict:
        code, body, _, _ = self.gw("GET", f"/api/v1/runs/{run_id}/snapshot")
        return body if code == 200 else {}

    def step_export(self, run_id: str) -> bool:
        t0 = time.monotonic()
        code, data, rid, resp = self.gw("GET", f"/api/v1/runs/{run_id}/export", raw=True)
        if code != 200:
            return self.step("export-via-gateway", False, t0, http=code, request_id=rid)
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
        sums = dict(reversed(line.split("  ", 1)) for line in zf.read("SHA256SUMS").decode().splitlines())
        sums_ok = set(sums) == names - {"SHA256SUMS"} and all(
            hashlib.sha256(zf.read(n)).hexdigest() == d for n, d in sums.items())
        arch = self.db_rows("SELECT id, sha256 FROM artifacts WHERE run_id = ? AND kind = 'architecture'", (run_id,))
        yaml_ok = [(a["id"], f"architectures/{a['id']}.yaml" in names and hashlib.sha256(
            zf.read(f"architectures/{a['id']}.yaml")).hexdigest() == str(a["sha256"]).removeprefix("sha256:"))
            for a in arch]
        ok = (resp.headers.get("X-Bundle-Sha256") == hashlib.sha256(data).hexdigest() and sums_ok and arch
              and all(v for _, v in yaml_ok))
        return self.step("export-via-gateway", bool(ok), t0, http=code, bytes=len(data), members=len(names),
                         sha256sums_ok=sums_ok, architecture_yaml=yaml_ok, request_id=rid,
                         index=json.loads(zf.read("export_objects.json")) if "export_objects.json" in names else None)

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        super().write_evidence(ok, started, dur)
        out = Path(self.args.evidence)
        lines = out.read_text().splitlines()
        lines[0] = lines[0].replace("# WP-3 three-process smoke", "# WP-5B gateway download smoke (Smoke3 plumbing)")
        lines[1] = (f"# command: {wp3.PY} -B {Path(__file__).relative_to(ROOT)} {' '.join(sys.argv[1:])}  "
                    "(runner: MBNAS_RUNNER_ARTIFACT_TRANSFER=local into MBNAS_GW_ARTIFACT_STORE)")
        out.write_text("\n".join(lines) + "\n")

    def run(self) -> int:
        started, t_all = wp3.wp2.now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        self.note(f"smoke root {self.root}; core port {self.port}; gateway port {self.gw_port}")
        ok = False
        try:
            self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            run_id = self.step_train()
            ok = ok and run_id is not None
            if run_id:
                ok = self.step_downloads(run_id) and ok
                ok = self.step_signed_urls(run_id) and ok
                ok = self.step_export(run_id) and ok
        except Exception as exc:  # noqa: BLE001
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=None)
    p.add_argument("--evidence", default=str(HERE / "gateway_download_smoke.txt"))
    p.add_argument("--timeout", type=float, default=900)
    args = p.parse_args()
    ns = argparse.Namespace(port=0, gw_port=0, root=args.root, timeout=args.timeout, web_dist=None,
                            device_source="local", evidence=args.evidence)
    return Smoke5B(ns).run()


if __name__ == "__main__":
    raise SystemExit(main())
