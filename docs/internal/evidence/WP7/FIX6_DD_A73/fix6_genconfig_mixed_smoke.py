#!/usr/bin/env python3
"""FIX6 / DD-A73 real-process check of the T5 rerun2 ``genconfig`` scenario (evidence script, not a repository script).

Reuses the FIX5 smoke classes (``../FIX5_T3_34/fix5_prune_roundtrip_smoke.py`` -> ``scripts/dev/wp3_three_process_smoke.py``:
core + owner worker + REAL runner + gateway as real processes on 127.0.0.1 ephemeral ports, CPU only) and drives the
gateway's user API only:

1. an evaluation-OFF generation run with ``examples/protocol.cpu_generation_only.json`` (the protocol T5 used: GA, seed 7,
   2 candidates, yolo11n, 64 px) -> COMPLETED (PROPOSE_GENERATION on the owner worker);
2. a generated candidate whose YAML contains an ETRI mixed block (NASGlobalTransformer or NASWindowTransformer) is picked
   from the public candidate detail (``architecture_yaml``);
3. WITHOUT any explicit structure-check call before (counted in the core and gateway access logs: 0), a training run is
   created from it (``source.kind = generated``, 1 epoch, 64 px, validation off) -> 200 (T5: 422 STRUCTURE_INVALID
   KeyError 'NASGlobalTransformer' before DD-A73); the run COMPLETES on the REAL runner (ultralytics, CPU), the bound
   structure reads PASS and the ``executed`` configuration revision is recorded and downloads with a matching sha256
   (and still contains the mixed block).

Exit 0 only when every step passed. usage: runtime/venv/bin/python -B <this file> [--root DIR] [--evidence FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]  # docs/internal/evidence/WP7/FIX6_DD_A73 -> TARGET_ROOT
assert (ROOT / "examples" / "protocol.cpu_generation_only.json").is_file(), ROOT
_spec = importlib.util.spec_from_file_location("fix5_prune_roundtrip_smoke", HERE.parent / "FIX5_T3_34" /
                                               "fix5_prune_roundtrip_smoke.py")
fix5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix5)  # type: ignore[union-attr]
PY, now, TERMINAL = fix5.PY, fix5.now, fix5.TERMINAL
MIXED = ("NASGlobalTransformer", "NASWindowTransformer", "NASPatchMerge")
WANTED = ("NASGlobalTransformer", "NASWindowTransformer")


class GenConfigSmoke(fix5.PruneSmoke):
    def __init__(self, args: argparse.Namespace) -> None:
        if not args.root:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            test_root = Path(os.environ.get("MBNAS_TEST_ROOT") or ROOT / "runtime" / "test_root")
            args.root = str(test_root / "fix6_genconfig_smoke" / stamp)
        super().__init__(args)

    def access_count(self, fragment: str) -> dict[str, int]:
        out = {}
        for name in ("core", "gateway"):
            logf = self.root / "logs" / f"{name}.log"
            text = logf.read_text(errors="replace") if logf.is_file() else ""
            out[name] = sum(1 for line in text.splitlines() if "access" in line and fragment in line)
        return out

    def step_generation(self) -> tuple[str | None, list[dict[str, Any]]]:
        t0 = time.monotonic()
        policy = json.loads((ROOT / "examples" / "protocol.cpu_generation_only.json").read_text())
        policy["console"]["paths"]["output_root"] = str(self.root / "core" / "runs")
        code, v, rid_v, _ = self.gw("POST", "/api/v1/protocols/validate", {"protocol": policy})
        if code != 200 or not (isinstance(v, dict) and v.get("valid")):
            self.step("gen-run", False, t0, http=code, body=v, request_id=rid_v)
            return None, []
        code, created, rid_c, _ = self.gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                                   "validation_id": v["validation_id"],
                                                                   "idempotency_key": "fix6-gen-" + uuid.uuid4().hex[:8]})
        if code != 200:
            self.step("gen-run", False, t0, http=code, body=created, request_id=rid_c)
            return None, []
        run_id = created["run_id"]
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        snap = self.snapshot(run_id)
        cands = snap.get("generated_candidates") or []
        jobs = self.db_rows("SELECT kind, status, lease_owner FROM jobs WHERE run_id = ?", (run_id,))
        ok = run["status"] == "COMPLETED" and bool(cands) and jobs and all(j["status"] == "SUCCEEDED" for j in jobs)
        self.step("gen-run", bool(ok), t0, run_id=run_id, run_status=run["status"], candidates=len(cands),
                  jobs=jobs, request_ids=[rid_v, rid_c], policy="examples/protocol.cpu_generation_only.json")
        return (run_id if ok else None), cands

    def step_pick(self, run_id: str, cands: list[dict[str, Any]]) -> dict[str, Any] | None:
        import yaml

        t0 = time.monotonic()
        seen, pick = [], None
        for c in cands:
            cid = c.get("candidate_id") or c.get("id")
            code, det, rid, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
            text = (det or {}).get("architecture_yaml") if isinstance(det, dict) else None
            names = []
            if isinstance(text, str):
                cfg = yaml.safe_load(text) or {}
                names = [str(r[2]) for part in ("backbone", "head") for r in (cfg.get(part) or []) if len(r) > 2]
            mixed = sorted({n for n in names if n in MIXED})
            seen.append({"candidate_id": cid, "family": c.get("family"), "mixed_blocks": mixed, "http": code,
                         "structure_status": (det or {}).get("structure_status") if isinstance(det, dict) else None,
                         "request_id": rid})
            if pick is None and any(n in WANTED for n in mixed):
                pick = {"candidate_id": cid, "yaml": text, "mixed_blocks": mixed,
                        "yaml_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        self.step("pick-mixed-candidate", pick is not None, t0, candidates=seen,
                  picked=None if pick is None else {k: pick[k] for k in ("candidate_id", "mixed_blocks", "yaml_sha256")})
        return pick

    def step_genconfig(self, ds: dict[str, str], gen_run: str, pick: dict[str, Any]) -> bool:
        t0 = time.monotonic()
        checks_before = self.access_count("structure-check")
        policy = self.training_policy(ds)
        policy["console"]["training"]["epoch_target_total"] = 1
        policy["console"]["retention"] = {"full_state": "keep_all"}
        body = {"source": {"kind": "generated", "run_id": gen_run, "candidate_id": pick["candidate_id"]},
                "protocol": policy, "mode": "scratch", "validation_enabled": False,
                "idempotency_key": "fix6-genconfig-" + uuid.uuid4().hex[:8]}
        core_pid = self.procs["core"].pid
        code, created, rid_c, _ = self.gw("POST", "/api/v1/training-runs", body)
        ok = code == 200 and isinstance(created, dict) and bool(created.get("run_id"))
        a = self.step("genconfig-create-without-prior-structure-check", bool(ok and checks_before == {"core": 0, "gateway": 0}),
                      t0, http=code, request_id=rid_c, source=body["source"], mixed_blocks=pick["mixed_blocks"],
                      structure_check_calls_before=checks_before, core_pid=core_pid,
                      body=created if not ok else {k: created.get(k) for k in ("run_id", "status", "candidate_id")})
        if not ok:
            return False
        run_id, cid = created["run_id"], created["candidate_id"]
        t1 = time.monotonic()
        run = self.wait(f"run {run_id} terminal", lambda: (lambda r: r if r and r.get("status") in TERMINAL else None)(
            self.run_status(run_id)), timeout=self.args.timeout, interval=2.0)
        jobs = self.db_rows("SELECT kind, status, attempt, lease_owner FROM jobs WHERE run_id = ?", (run_id,))
        cd, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}")
        structure = (det or {}).get("structure") if isinstance(det, dict) else None
        cr, revs, rid_r, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions")
        rows = ((revs or {}).get("revisions") or []) if isinstance(revs, dict) else []
        out = []
        for rv in rows:
            cy, ybody, rid_y, hy = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions/"
                                                  f"{rv.get('revision_id')}/yaml", raw=True)
            dg = hashlib.sha256(ybody).hexdigest() if isinstance(ybody, bytes) else None
            text = ybody.decode("utf-8", "replace") if isinstance(ybody, bytes) else ""
            out.append({"revision_id": rv.get("revision_id"), "kind": rv.get("kind"), "yaml_http": cy,
                        "yaml_sha256": dg, "listed_source_sha256": rv.get("source_sha256"),
                        "sha_match": bool(dg) and str(rv.get("source_sha256") or "").removeprefix("sha256:") == dg,
                        "contains_mixed_block": any(n in text for n in pick["mixed_blocks"]), "request_id": rid_y})
        executed = [o for o in out if o["kind"] == "executed"]
        ok2 = (run["status"] == "COMPLETED" and bool(jobs) and all(j["status"] == "SUCCEEDED" for j in jobs)
               and all(j["lease_owner"] == "smoke-runner-1" for j in jobs) and cr == 200 and bool(executed)
               and all(o["yaml_http"] == 200 and o["sha_match"] and o["contains_mixed_block"] for o in executed)
               and isinstance(structure, dict) and structure.get("status") == "PASS")
        b = self.step("genconfig-completed-executed-revision", bool(ok2), t1, run_id=run_id, candidate_id=cid,
                      run_status=run["status"], jobs=jobs, bound_structure_status=(structure or {}).get("status")
                      if isinstance(structure, dict) else structure,
                      bound_structure_modules=sorted({b_.get("module") for b_ in (structure or {}).get("blocks") or []
                                                      if b_.get("module") in MIXED}) if isinstance(structure, dict) else None,
                      revisions_http=cr, revisions=out, request_ids=[rid_c, rid_d, rid_r],
                      structure_check_calls_after=self.access_count("structure-check"))
        return a and b

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
            gen_run, cands = self.step_generation()
            pick = self.step_pick(gen_run, cands) if gen_run else None
            ok = bool(gen_run and pick and self.step_genconfig(ds, gen_run, pick)) and ok
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            c = self.stop_all()
        ok = ok and c and all(s["ok"] for s in self.steps)
        self.write_evidence(ok, started, time.monotonic() - t_all)
        return 0 if ok else 1

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        out = Path(self.args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# FIX6 DD-A73 genconfig smoke ({started} .. {now()}, {dur:.1f} s) result={'PASS' if ok else 'FAIL'}",
                 f"# command: {PY} -B {Path(__file__).relative_to(ROOT)} {' '.join(sys.argv[1:])}",
                 f"# cwd: {ROOT}  CUDA_VISIBLE_DEVICES=''  core: {self.base}  gateway: {self.gw_base}",
                 f"# smoke root: {self.root}", "# steps:"]
        lines += ["  " + json.dumps(s, sort_keys=True, default=str, ensure_ascii=False) for s in self.steps]
        lines += ["# step log:"] + ["  " + line for line in self.log]
        for name in ("core", "gateway"):
            logf = self.root / "logs" / f"{name}.log"
            if logf.is_file():
                keep = [t for t in logf.read_text(errors="replace").splitlines()
                        if "training-runs" in t or "structure-check" in t or "/runs" in t and "POST" in t][-40:]
                lines += [f"# selected access lines of {name}.log ({len(keep)}):"] + ["  " + t for t in keep]
        text = "\n".join(lines) + "\n"
        for secret in (self.user_token, *self.tokens.values()):
            text = text.replace(secret, "<redacted>")
        out.write_text(text)
        print(f"evidence -> {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", default=None)
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--device-source", choices=("local", "runners"), default="local")
    p.add_argument("--evidence", default=str(HERE / "genconfig_mixed_smoke.txt"))
    args = p.parse_args(argv)
    return GenConfigSmoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
