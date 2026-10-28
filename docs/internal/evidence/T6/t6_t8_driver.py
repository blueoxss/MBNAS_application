"""WP-7V T6 (relocation) + T8 (runtime leak capture) driver.

Runs every step from an external working directory (this scratch folder), records each command with its exit code
into transcript.jsonl / transcript.txt, captures gateway responses / logs / process environ into the scratch capture
folder (environ never leaves the scratch folder), and leaves the scanning to t8_scan.sh.
Secrets: tokens are read from the env files into this process only; never printed or written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
T6 = SRC / "runtime/test_root/t6"
OWN = T6 / "mbnas owner test/mbnas_application"
PUB = T6 / "mbnas public test/mbnas_application"
HERE = Path(__file__).resolve().parent
CAP = HERE / "capture"            # raw captures (scanned; environ stays here)
EV6 = SRC / "docs/internal/evidence/T6"
CORE_PORT, PUB_GW = 18700, 18710
GW = f"http://127.0.0.1:{PUB_GW}"
TRANSCRIPT: list[dict] = []
BASE_ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null"}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(EV6 / "transcript.txt", "a") as fh:
        fh.write(line + "\n")


def run(step: str, argv: list[str], cwd: Path, *, timeout: int = 3600, env: dict | None = None,
        out_name: str | None = None) -> int:
    t0 = time.time()
    out = EV6 / (out_name or f"{len(TRANSCRIPT):02d}_{step}.txt")
    with open(out, "w") as fh:
        try:
            p = subprocess.run(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, env=env or BASE_ENV,
                               timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = -999
    rec = {"step": step, "argv": argv, "cwd": str(cwd), "exit": code, "seconds": round(time.time() - t0, 1),
           "output": str(out.relative_to(SRC))}
    TRANSCRIPT.append(rec)
    with open(EV6 / "transcript.jsonl", "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"{step}: exit={code} ({rec['seconds']} s) -> {rec['output']}")
    return code


def envval(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise KeyError(key)


TOKEN = ""


def gw(method: str, path: str, body: object | None = None, *, raw: bytes | None = None, ctype: str | None = None,
       name: str | None = None, auth: bool = True) -> tuple[int, bytes]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(GW + path, data=data, method=method)
    if auth:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", ctype or "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            code, payload, headers = r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, payload, headers = e.code, e.read(), dict(e.headers)
    if name:
        (CAP / "responses" / name).write_bytes(payload)
        (CAP / "responses" / (name + ".headers.json")).write_text(json.dumps({"status": code, "headers": headers},
                                                                             indent=1))
    rec = {"step": f"api {method} {path}", "http": code, "bytes": len(payload), "capture": name}
    TRANSCRIPT.append(rec)
    with open(EV6 / "transcript.jsonl", "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"api {method} {path} -> HTTP {code} ({len(payload)} B)")
    return code, payload


def j(b: bytes) -> dict:
    try:
        return json.loads(b)
    except ValueError:
        return {}


def api_flow() -> dict:
    out: dict = {}
    c, b = gw("GET", "/api/v1/health", name="health.json")
    out["health"] = {"http": c, "status": j(b).get("status"), "core": j(b).get("core")}
    c, b = gw("GET", "/api/v1/health", auth=False, name="health_noauth.json")
    out["health_noauth_http"] = c
    c, b = gw("GET", "/api/v1/protocols/presets", name="presets.json")
    out["presets"] = {"http": c}
    c, b = gw("GET", "/", auth=False, name="web_index.html")
    out["web_index_http"] = c
    yaml_ok = (PUB / "examples/models/yolo11n_user.yaml").read_bytes()
    c, b = gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user.yaml", raw=yaml_ok,
              ctype="application/x-yaml", name="upload.json")
    up = j(b)
    out["upload"] = {"http": c, "upload_id_prefix": str(up.get("upload_id", ""))[:4]}
    c, b = gw("POST", "/api/v1/structure-check", {"upload_id": up.get("upload_id"), "input_size": 64},
              name="structure_check.json")
    sc = j(b)
    out["structure_check"] = {"http": c, "keys": sorted(sc)[:20], "valid": sc.get("valid", sc.get("ok"))}
    yaml_bad = (PUB / "examples/models/yolo11n_user_broken.yaml").read_bytes()
    c, b = gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user_broken.yaml", raw=yaml_bad,
              ctype="application/x-yaml", name="upload_broken.json")
    c, b = gw("POST", "/api/v1/structure-check", {"upload_id": j(b).get("upload_id"), "input_size": 64},
              name="structure_check_broken.json")
    out["structure_check_broken"] = {"http": c, "valid": j(b).get("valid", j(b).get("ok"))}
    # generation-only run (evaluation OFF, CPU) through the public gateway -> owner core + owner worker
    policy = {"schema_version": "nas-policy/1",
              "console": {"paths": {"output_root": str(OWN / "runtime/core/runs")},
                          "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                     "seeds": [7], "candidate_count": 2, "max_attempts": 12,
                                     "allow_duplicates": False, "arms": ["GA"]}}}
    c, b = gw("POST", "/api/v1/protocols/validate", {"protocol": policy}, name="validate.json")
    v = j(b)
    out["validate"] = {"http": c, "valid": v.get("valid"), "errors": (v.get("errors") or [])[:3]}
    if c != 200 or not v.get("valid"):
        return out
    c, b = gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                       "validation_id": v["validation_id"], "idempotency_key": "t6-gen-1"},
              name="run_create.json")
    out["run_create"] = {"http": c}
    run_id = j(b).get("run_id")
    if not run_id:
        return out
    status = None
    for _ in range(300):
        c, b = gw("GET", f"/api/v1/runs/{run_id}") if False else _quiet_get(f"/api/v1/runs/{run_id}")
        status = j(b).get("status")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    out["run_status"] = status
    c, b = gw("GET", f"/api/v1/runs/{run_id}", name="run.json")
    c, b = gw("GET", f"/api/v1/runs/{run_id}/snapshot", name="snapshot.json")
    snap = j(b)
    cands = snap.get("generated_candidates") or snap.get("candidates") or []
    cid = (cands[0].get("candidate_id") or cands[0].get("id")) if cands else None
    out["snapshot"] = {"http": c, "candidates": len(cands)}
    if cid:
        c, b = gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}", name="candidate_detail.json")
        out["candidate_detail"] = {"http": c}
        c, b = gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions", name="config_revisions.json")
        out["config_revisions"] = {"http": c}
    c, b = gw("GET", f"/api/v1/runs/{run_id}/export", name="export.zip")
    out["export"] = {"http": c, "bytes": len(b)}
    c, b = gw("GET", "/api/v1/runs", name="runs_list.json")
    out["runs_list"] = {"http": c}
    out["run_id"] = run_id
    return out


def _quiet_get(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(GW + path, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def capture_processes() -> dict:
    """/proc/<pid>/environ + cmdline of the public processes (scratch only) and their logs."""
    info: dict = {}
    (CAP / "proc").mkdir(parents=True, exist_ok=True)
    (CAP / "logs").mkdir(parents=True, exist_ok=True)
    for role in ("gateway", "runner"):
        pidf = PUB / "runtime/pids" / f"{role}.pid"
        try:
            pid = int(pidf.read_text().strip())
            env = Path(f"/proc/{pid}/environ").read_bytes()
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            maps = Path(f"/proc/{pid}/maps").read_text()
        except (OSError, ValueError) as exc:
            info[role] = f"확인 못 함 ({type(exc).__name__})"
            continue
        for name, data in ((f"{role}.environ", env), (f"{role}.cmdline", cmd)):
            p = CAP / "proc" / name
            p.write_bytes(data.replace(b"\0", b"\n"))
            os.chmod(p, 0o600)
        info[role] = {"pid": pid, "environ_vars": env.count(b"\0"),
                      "environ_keys": sorted({k.split(b"=", 1)[0].decode(errors="replace")
                                              for k in env.split(b"\0") if k}),
                      "maps_mbnas_core": "mbnas_core" in maps}
    for f in (PUB / "runtime/logs").glob("*.log"):
        shutil.copy2(f, CAP / "logs" / f.name)
    for f in (PUB / "runtime/logs").glob("**/*.jsonl"):
        shutil.copy2(f, CAP / "logs" / f.name)
    gl = PUB / "runtime/gateway/logs"
    if gl.is_dir():
        for f in gl.rglob("*"):
            if f.is_file():
                shutil.copy2(f, CAP / "logs" / ("gateway_" + f.name))
    return info


def main() -> int:
    global TOKEN
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    EV6.mkdir(parents=True, exist_ok=True)
    (CAP / "responses").mkdir(parents=True, exist_ok=True)
    cwd = HERE
    ob, pb = OWN / "scripts", PUB / "scripts"
    summary: dict = {"external_cwd": str(cwd), "owner_root": str(OWN), "public_root": str(PUB)}
    if phase in ("all", "up"):
        log(f"== T6 up: external cwd {cwd}")
        summary["owner_start"] = run("owner_start", ["bash", str(ob / "start.sh"), "--profile", "owner"], cwd)
        summary["owner_doctor"] = run("owner_doctor_strict", ["bash", str(ob / "doctor.sh"), "--profile", "owner",
                                                              "--strict", "--json", str(EV6 / "doctor_owner.json")], cwd)
        summary["public_doctor_pre"] = run("public_doctor_strict_prestart",
                                           ["bash", str(pb / "doctor.sh"), "--profile", "public", "--strict"], cwd)
        summary["public_start"] = run("public_start", ["bash", str(pb / "start.sh"), "--profile", "public"], cwd)
        summary["public_doctor"] = run("public_doctor_strict", ["bash", str(pb / "doctor.sh"), "--profile", "public",
                                                                "--strict", "--json",
                                                                str(EV6 / "doctor_public.json")], cwd)
        TOKEN = envval(PUB / "config/public/gateway.env", "MBNAS_GW_API_TOKEN")
        summary["api"] = api_flow()
        summary["processes"] = capture_processes()
        # public venv: the private package must not be importable / loaded
        py = str(PUB / "runtime/venv/bin/python")
        summary["public_find_loader"] = run(
            "public_no_mbnas_core_loader",
            [py, "-c", "import sys, pkgutil; assert not pkgutil.find_loader('mbnas_core'); "
                       "import importlib.util as u; assert u.find_spec('mbnas_core') is None; "
                       "import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts; "
                       "import mbnas_server.api.app, mbnas_runner.daemon; "
                       "bad=[m for m in sys.modules if m.split('.')[0]=='mbnas_core']; assert not bad, bad; "
                       "print('mbnas_core: no loader, no spec, not in sys.modules after importing the public apps')"],
            cwd)
        (EV6 / "api_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    if phase == "loader":
        py = str(PUB / "runtime/venv/bin/python")
        summary["public_find_loader"] = run(
            "public_no_mbnas_core_loader_rerun",
            [py, "-c", "import sys, pkgutil; assert not pkgutil.find_loader('mbnas_core'); "
                       "import importlib.util as u; assert u.find_spec('mbnas_core') is None; "
                       "import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts; "
                       "import mbnas_server.api.app, mbnas_runner.daemon; "
                       "bad=[m for m in sys.modules if m.split('.')[0]=='mbnas_core']; assert not bad, bad; "
                       "print('mbnas_core: no loader, no spec, not in sys.modules after importing the public apps')"],
            cwd)
    if phase in ("all", "verify"):
        summary["public_verify"] = run("public_verify_unit_relocation",
                                       ["bash", str(pb / "verify.sh"), "--profile", "public", "--suite",
                                        "unit,relocation", "--json", str(EV6 / "verify_public.json")], cwd,
                                       timeout=5400)
        env_pkg = {**BASE_ENV, "MBNAS_CANARY_SCAN": str(SRC / "scripts/dev/canary_scan.py")}
        summary["public_verify_package"] = run("public_verify_package",
                                               ["bash", str(pb / "verify.sh"), "--profile", "public", "--suite",
                                                "package", "--json", str(EV6 / "verify_public_package.json")], cwd,
                                               env=env_pkg)
        summary["owner_verify"] = run("owner_verify_unit_integration_relocation",
                                      ["bash", str(ob / "verify.sh"), "--profile", "owner", "--suite",
                                       "unit,integration,relocation", "--json", str(EV6 / "verify_owner.json")], cwd,
                                      timeout=7200)
    if phase in ("all", "down"):
        summary["public_stop"] = run("public_stop", ["bash", str(pb / "stop.sh"), "--profile", "public"], cwd)
        summary["owner_stop"] = run("owner_stop", ["bash", str(ob / "stop.sh"), "--profile", "owner"], cwd)
    if phase in ("all", "reinvoke"):
        cwd2 = HERE / "second cwd"
        cwd2.mkdir(exist_ok=True)
        log(f"== T6 re-invocation from another external cwd {cwd2}")
        summary["re_owner_start"] = run("re_owner_start", ["bash", str(ob / "start.sh"), "--profile", "owner",
                                                           "--role", "core"], cwd2)
        summary["re_public_start"] = run("re_public_start", ["bash", str(pb / "start.sh"), "--profile", "public",
                                                             "--role", "gateway"], cwd2)
        TOKEN = envval(PUB / "config/public/gateway.env", "MBNAS_GW_API_TOKEN")
        c, b = gw("GET", "/api/v1/health", name="health_reinvoke.json")
        summary["re_health"] = {"http": c, "status": j(b).get("status"), "core": j(b).get("core")}
        summary["re_public_doctor"] = run("re_public_doctor", ["bash", "scripts/doctor.sh", "--profile", "public"],
                                          PUB)  # relative invocation from inside ROOT too
        summary["re_public_stop"] = run("re_public_stop", ["bash", str(pb / "stop.sh"), "--profile", "public"], cwd2)
        summary["re_owner_stop"] = run("re_owner_stop", ["bash", str(ob / "stop.sh"), "--profile", "owner"], cwd2)
        left = subprocess.run(["pgrep", "-af", "test_root/t6/.*/runtime/venv/bin/"], capture_output=True,
                              text=True).stdout  # service processes of the t6 trees (venv interpreters)
        summary["leftover_processes"] = [l.split(" ", 1)[0] for l in left.splitlines()
                                         if "pgrep" not in l and "/bin/bash -c" not in l]
        log(f"leftover processes of the t6 trees: {len(summary['leftover_processes'])}")
    prev = EV6 / f"summary_{phase}.json"
    prev.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    log(f"summary -> {prev.relative_to(SRC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
