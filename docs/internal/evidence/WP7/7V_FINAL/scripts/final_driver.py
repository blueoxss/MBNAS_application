"""WP-7V P1-final: T6 (relocation) + T8 §2 (runtime capture) driver for the FINAL ZIPs.

Every step runs from an external working directory (this scratch folder). Each command and exit code goes to
<EV>/transcript.jsonl and transcript.txt. The service processes are started with a service-like minimal environment
(as a systemd unit would: PATH, LANG, HOME inside the tree, CUDA_VISIBLE_DEVICES=''), so the scanned /proc environ
holds only what the product sets. Raw captures (responses, logs, /proc environ) stay in <HERE>/capture; environ never
leaves the scratch folder. Tokens are read from the env files into this process only, never printed or written.

Phases: configure | up | verify | down | reinvoke   (python -I final_driver.py <phase>)
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
FIN = SRC / "runtime/test_root/t6/final"
OWN = FIN / "mbnas owner final/mbnas_application"
PUB = FIN / "mbnas public final/mbnas_application"
REG = FIN / "mbnas registration final/mbnas_application"
HERE = Path(__file__).resolve().parent
CAP = HERE / "capture"
EV = SRC / "docs/internal/evidence/WP7/7V_FINAL/T6"
CORE_PORT, OWN_GW, PUB_GW, REG_GW = 18800, 18811, 18810, 18812
CORE = f"http://127.0.0.1:{CORE_PORT}"
SUPPLIED_OUTPUT_ROOT = OWN / "runtime/core/runs/t8_supplied"   # what the client puts in its protocol (T8 normalizes it)
BASE_ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null"}
TRANSCRIPT: list[dict] = []
TOKEN = ""
GW = f"http://127.0.0.1:{PUB_GW}"


def service_env(tree: Path) -> dict:
    home = tree / "runtime/home"
    home.mkdir(parents=True, exist_ok=True)
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
            "HOME": str(home), "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(EV / "transcript.txt", "a") as fh:
        fh.write(line + "\n")


def record(rec: dict) -> None:
    TRANSCRIPT.append(rec)
    with open(EV / "transcript.jsonl", "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(step: str, argv: list[str], cwd: Path, *, timeout: int = 3600, env: dict | None = None,
        env_label: str = "caller env") -> int:
    t0 = time.time()
    out = EV / f"{step}.txt"
    with open(out, "w") as fh:
        fh.write(f"$ (cwd={cwd}) ({env_label}) {' '.join(repr(a) if ' ' in a else a for a in argv)}\n")
        fh.flush()
        try:
            p = subprocess.run(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, env=env or BASE_ENV,
                               timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = -999
        fh.write(f"exit={code}\n")
    rec = {"step": step, "argv": argv, "cwd": str(cwd), "env": env_label, "exit": code,
           "seconds": round(time.time() - t0, 1), "output": str(out.relative_to(SRC))}
    record(rec)
    log(f"{step}: exit={code} ({rec['seconds']} s)")
    return code


def envval(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise KeyError(key)


def gw(method: str, path: str, body: object | None = None, *, raw: bytes | None = None, ctype: str | None = None,
       name: str | None = None, auth: bool = True, sub: str = "responses", quiet: bool = False) -> tuple[int, bytes]:
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
        d = CAP / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(payload)
        (d / (name + ".headers.json")).write_text(json.dumps({"status": code, "headers": headers}, indent=1))
    if not quiet:
        record({"step": f"api {method} {GW}{path.split('?')[0]}", "http": code, "bytes": len(payload), "capture": name})
        log(f"api {method} {GW}{path} -> HTTP {code} ({len(payload)} B)")
    return code, payload


def j(b: bytes) -> dict:
    try:
        v = json.loads(b)
        return v if isinstance(v, dict) else {"_list": v}
    except ValueError:
        return {}


def api_full() -> dict:
    out: dict = {}
    c, b = gw("GET", "/api/v1/health", name="health.json")
    h = j(b)
    out["health"] = {"http": c, "status": h.get("status"), "core": h.get("core"), "allowlists": h.get("allowlists")}
    out["health_allowlists_own_roots_only"] = h.get("allowlists") == {"dataset_roots": [], "output_roots": [],
                                                                      "archive_roots": []}
    c, b = gw("GET", "/api/v1/health", auth=False, name="health_noauth.json")
    out["health_noauth_http"] = c
    c, b = gw("GET", "/api/v1/protocols/presets", name="presets.json")
    out["presets_http"] = c
    c, b = gw("GET", "/api/v1/registry", name="registry.json")
    out["registry_http"] = c
    c, b = gw("GET", "/", auth=False, name="web_index.html")
    out["web_index_http"] = c
    c, b = gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user.yaml",
              raw=(PUB / "examples/models/yolo11n_user.yaml").read_bytes(), ctype="application/x-yaml",
              name="upload.json")
    up = j(b).get("upload_id")
    out["upload_http"] = c
    c, b = gw("POST", "/api/v1/structure-check", {"upload_id": up, "input_size": 64}, name="structure_check.json")
    out["structure_check"] = {"http": c, "status": j(b).get("status")}
    c, b = gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user_broken.yaml",
              raw=(PUB / "examples/models/yolo11n_user_broken.yaml").read_bytes(), ctype="application/x-yaml",
              name="upload_broken.json")
    c, b = gw("POST", "/api/v1/structure-check", {"upload_id": j(b).get("upload_id"), "input_size": 64},
              name="structure_check_broken.json")
    diag = (j(b).get("diagnostics") or [{}])[0] if isinstance(j(b).get("diagnostics"), list) else {}
    out["structure_check_broken"] = {"http": c, "status": j(b).get("status"), "first_code": diag.get("code")}
    SUPPLIED_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    policy = {"schema_version": "nas-policy/1",
              "console": {"paths": {"output_root": str(SUPPLIED_OUTPUT_ROOT)},
                          "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"],
                                     "seeds": [7], "candidate_count": 2, "max_attempts": 12,
                                     "allow_duplicates": False, "arms": ["GA"]}}}
    c, b = gw("POST", "/api/v1/protocols/validate", {"protocol": policy}, name="validate.json")
    v = j(b)
    out["validate"] = {"http": c, "valid": v.get("valid"), "errors": (v.get("errors") or [])[:3]}
    if c != 200 or not v.get("valid"):
        return out
    c, b = gw("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                       "validation_id": v["validation_id"], "idempotency_key": "t6-final-gen-1"},
              name="run_create.json")
    out["run_create_http"] = c
    run_id = j(b).get("run_id")
    if not run_id:
        return out
    status = None
    for _ in range(300):
        c, b = gw("GET", f"/api/v1/runs/{run_id}", quiet=True)
        status = j(b).get("status")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    out["run_status"] = status
    gw("GET", f"/api/v1/runs/{run_id}", name="run.json")
    c, b = gw("GET", f"/api/v1/runs/{run_id}/snapshot", name="snapshot.json")
    snap = j(b)
    cands = snap.get("generated_candidates") or snap.get("candidates") or []
    cid = (cands[0].get("candidate_id") or cands[0].get("id")) if cands else None
    out["snapshot"] = {"http": c, "candidates": len(cands)}
    if cid:
        c, b = gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}", name="candidate_detail.json")
        out["candidate_detail_http"] = c
        c, b = gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions", name="config_revisions.json")
        out["config_revisions_http"] = c
    c, b = gw("GET", f"/api/v1/runs/{run_id}/export", name="export.zip")
    out["export"] = {"http": c, "bytes": len(b)}
    c, b = gw("GET", "/api/v1/runs", name="runs_list.json")
    out["runs_list_http"] = c
    out["run_id"] = run_id
    return out


def api_light(sub: str) -> dict:
    out: dict = {}
    c, b = gw("GET", "/api/v1/health", name="health.json", sub=sub)
    h = j(b)
    out["health"] = {"http": c, "status": h.get("status"), "core": h.get("core"), "allowlists": h.get("allowlists")}
    c, b = gw("GET", "/api/v1/protocols/presets", sub=sub, name="presets.json")
    out["presets_http"] = c
    c, b = gw("GET", "/", auth=False)
    out["web_index_http"] = c
    c, b = gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user.yaml",
              raw=(REG / "examples/models/yolo11n_user.yaml").read_bytes(), ctype="application/x-yaml")
    out["upload_http"] = c
    c, b = gw("POST", "/api/v1/structure-check", {"upload_id": j(b).get("upload_id"), "input_size": 64})
    out["structure_check"] = {"http": c, "status": j(b).get("status")}
    return out


def capture_processes(tree: Path, sub: str) -> dict:
    info: dict = {}
    (CAP / sub / "proc").mkdir(parents=True, exist_ok=True)
    (CAP / sub / "logs").mkdir(parents=True, exist_ok=True)
    (CAP / sub / "install_logs").mkdir(parents=True, exist_ok=True)
    for role in ("gateway", "runner"):
        try:
            pid = int((tree / "runtime/pids" / f"{role}.pid").read_text().strip())
            env = Path(f"/proc/{pid}/environ").read_bytes()
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            maps = Path(f"/proc/{pid}/maps").read_text()
        except (OSError, ValueError) as exc:
            info[role] = f"확인 못 함 ({type(exc).__name__})"
            continue
        for name, data in ((f"{role}.environ", env), (f"{role}.cmdline", cmd)):
            p = CAP / sub / "proc" / name
            p.write_bytes(data.replace(b"\0", b"\n"))
            os.chmod(p, 0o600)
        info[role] = {"pid": pid, "environ_keys": sorted({k.split(b"=", 1)[0].decode(errors="replace")
                                                          for k in env.split(b"\0") if k}),
                      "maps_mbnas_core": "mbnas_core" in maps}
    logs = tree / "runtime/logs"
    for f in sorted(logs.rglob("*")):
        if not f.is_file() or "verify" in f.relative_to(logs).parts:
            continue
        dest = "install_logs" if f.name.startswith("install-") else "logs"
        shutil.copy2(f, CAP / sub / dest / f.relative_to(logs).as_posix().replace("/", "__"))
    for extra in (tree / "runtime/gateway/logs", tree / "runtime/runner/logs"):
        if extra.is_dir():
            for f in extra.rglob("*"):
                if f.is_file():
                    shutil.copy2(f, CAP / sub / "logs" / (extra.parent.name + "__" + f.name))
    return info


def loader_check(tree: Path, step: str, cwd: Path) -> int:
    py = str(tree / "runtime/venv/bin/python")
    return run(step, [py, "-c", "import sys, pkgutil; assert not pkgutil.find_loader('mbnas_core'); "
                                "import importlib.util as u; assert u.find_spec('mbnas_core') is None; "
                                "import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts; "
                                "import mbnas_server.api.app, mbnas_runner.daemon; "
                                "bad=[m for m in sys.modules if m.split('.')[0]=='mbnas_core']; assert not bad, bad; "
                                "print('mbnas_core: no loader, no spec, not in sys.modules after importing the public "
                                "apps')"], cwd)


def setkeys(path: Path, kv: dict) -> None:
    lines = path.read_text().splitlines()
    keep = [l for l in lines if not any(l.startswith(f"{k}=") or l.startswith(f"# {k}=") for k in kv)]
    keep += [f"{k}={v}" for k, v in kv.items()]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, ("\n".join(keep) + "\n").encode())
    os.close(fd)
    log(f"configured {path.relative_to(FIN)}: {sorted(kv)} (values not printed)")


def issue(*args: str) -> str:
    r = subprocess.run([str(OWN / "runtime/venv/bin/mbnas-core-principal"), "--env-file",
                        str(OWN / "config/owner/core.env"), "create", *args], capture_output=True, text=True,
                       cwd=str(HERE), env={**BASE_ENV, "MBNAS_HOME": str(OWN / "runtime")})
    if r.returncode != 0:
        log(f"principal create failed exit={r.returncode}")
        sys.exit(1)
    doc = json.loads(r.stdout)
    log(f"issued {args[1]} principal {doc['principal']['id']}")
    return doc["token"]


def main() -> int:
    global TOKEN, GW
    phase = sys.argv[1]
    EV.mkdir(parents=True, exist_ok=True)
    CAP.mkdir(parents=True, exist_ok=True)
    cwd = HERE
    ob, pb, rb = OWN / "scripts", PUB / "scripts", REG / "scripts"
    s: dict = {"phase": phase, "external_cwd": str(cwd)}
    if phase == "configure":
        setkeys(OWN / "config/owner/core.env", {"MBNAS_CORE_PORT": CORE_PORT})
        setkeys(OWN / "config/owner/gateway.env", {"MBNAS_GW_PORT": OWN_GW, "MBNAS_GW_CORE_URL": CORE})
        setkeys(OWN / "config/owner/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE})
        for tree, port, rid in ((PUB, PUB_GW, "runner-t6final-public"), (REG, REG_GW, "runner-t6final-registration")):
            srv = issue("--kind", "server")
            rtk = issue("--kind", "runner", "--runner-id", rid)
            setkeys(tree / "config/public/gateway.env", {"MBNAS_GW_PORT": port, "MBNAS_GW_CORE_URL": CORE,
                                                         "MBNAS_GW_CORE_TOKEN": srv})
            setkeys(tree / "config/public/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE, "MBNAS_RUNNER_TOKEN": rtk,
                                                        "MBNAS_RUNNER_RUNNER_ID": rid})
    if phase == "up":
        log(f"== T6 final up: external cwd {cwd}")
        s["owner_start"] = run("owner_start", ["bash", str(ob / "start.sh"), "--profile", "owner"], cwd,
                               env=service_env(OWN), env_label="service env")
        s["owner_doctor"] = run("owner_doctor_strict", ["bash", str(ob / "doctor.sh"), "--profile", "owner",
                                                        "--strict", "--json", str(EV / "doctor_owner.json")], cwd)
        s["public_doctor_pre"] = run("public_doctor_strict_prestart",
                                     ["bash", str(pb / "doctor.sh"), "--profile", "public", "--strict"], cwd)
        s["public_start"] = run("public_start", ["bash", str(pb / "start.sh"), "--profile", "public"], cwd,
                                env=service_env(PUB), env_label="service env")
        s["public_doctor"] = run("public_doctor_strict", ["bash", str(pb / "doctor.sh"), "--profile", "public",
                                                          "--strict", "--json", str(EV / "doctor_public.json")], cwd)
        s["reg_doctor_pre"] = run("reg_doctor_strict_prestart",
                                  ["bash", str(rb / "doctor.sh"), "--profile", "public", "--strict"], cwd)
        s["reg_start"] = run("reg_start", ["bash", str(rb / "start.sh"), "--profile", "public"], cwd,
                             env=service_env(REG), env_label="service env")
        s["reg_doctor"] = run("reg_doctor_strict", ["bash", str(rb / "doctor.sh"), "--profile", "public",
                                                    "--strict", "--json", str(EV / "doctor_registration.json")], cwd)
        TOKEN, GW = envval(PUB / "config/public/gateway.env", "MBNAS_GW_API_TOKEN"), f"http://127.0.0.1:{PUB_GW}"
        s["api_public"] = api_full()
        s["processes_public"] = capture_processes(PUB, "public")
        TOKEN, GW = envval(REG / "config/public/gateway.env", "MBNAS_GW_API_TOKEN"), f"http://127.0.0.1:{REG_GW}"
        s["api_registration"] = api_light("responses_registration")
        s["processes_registration"] = capture_processes(REG, "registration")
        s["public_loader"] = loader_check(PUB, "public_no_mbnas_core_loader", cwd)
        s["reg_loader"] = loader_check(REG, "reg_no_mbnas_core_loader", cwd)
    if phase == "verify":
        env_pkg = {**BASE_ENV, "MBNAS_CANARY_SCAN": str(SRC / "scripts/dev/canary_scan.py")}
        s["public_verify"] = run("public_verify_unit_relocation",
                                 ["bash", str(pb / "verify.sh"), "--profile", "public", "--suite", "unit,relocation",
                                  "--json", str(EV / "verify_public.json")], cwd, timeout=5400)
        s["public_verify_package"] = run("public_verify_package",
                                         ["bash", str(pb / "verify.sh"), "--profile", "public", "--suite", "package",
                                          "--json", str(EV / "verify_public_package.json")], cwd, env=env_pkg)
        s["reg_verify"] = run("reg_verify_unit_relocation",
                              ["bash", str(rb / "verify.sh"), "--profile", "public", "--suite", "unit,relocation",
                               "--json", str(EV / "verify_registration.json")], cwd, timeout=5400)
        s["reg_verify_package"] = run("reg_verify_package",
                                      ["bash", str(rb / "verify.sh"), "--profile", "public", "--suite", "package",
                                       "--json", str(EV / "verify_registration_package.json")], cwd, env=env_pkg)
        s["owner_verify"] = run("owner_verify_unit_integration_relocation",
                                ["bash", str(ob / "verify.sh"), "--profile", "owner", "--suite",
                                 "unit,integration,relocation", "--json", str(EV / "verify_owner.json")], cwd,
                                timeout=7200)
    if phase == "down":
        s["public_stop"] = run("public_stop", ["bash", str(pb / "stop.sh"), "--profile", "public"], cwd)
        s["reg_stop"] = run("reg_stop", ["bash", str(rb / "stop.sh"), "--profile", "public"], cwd)
        s["owner_stop"] = run("owner_stop", ["bash", str(ob / "stop.sh"), "--profile", "owner"], cwd)
    if phase == "reinvoke":
        cwd2 = Path("/")   # a second external cwd that names no host fact (T8: OLDPWD of the invoking shell)
        log(f"== T6 final re-invocation from another external cwd {cwd2}")
        s["re_owner_start"] = run("re_owner_start", ["bash", str(ob / "start.sh"), "--profile", "owner", "--role",
                                                     "core"], cwd2, env=service_env(OWN), env_label="service env")
        s["re_public_start"] = run("re_public_start", ["bash", str(pb / "start.sh"), "--profile", "public", "--role",
                                                       "gateway"], cwd2, env=service_env(PUB), env_label="service env")
        TOKEN, GW = envval(PUB / "config/public/gateway.env", "MBNAS_GW_API_TOKEN"), f"http://127.0.0.1:{PUB_GW}"
        s["re_processes"] = capture_processes(PUB, "reinvoke")
        c, b = gw("GET", "/api/v1/health", name="health_reinvoke.json")
        s["re_health"] = {"http": c, "status": j(b).get("status"), "core": j(b).get("core"),
                          "allowlists": j(b).get("allowlists")}
        s["re_public_doctor"] = run("re_public_doctor_relative", ["bash", "scripts/doctor.sh", "--profile", "public"],
                                    PUB)
        s["re_public_stop"] = run("re_public_stop", ["bash", str(pb / "stop.sh"), "--profile", "public"], cwd2)
        s["re_owner_stop"] = run("re_owner_stop", ["bash", str(ob / "stop.sh"), "--profile", "owner"], cwd2)
        left = subprocess.run(["pgrep", "-af", "test_root/t6/final/.*/runtime/venv/bin/"], capture_output=True,
                              text=True).stdout
        s["leftover_processes"] = [l.split(" ", 1)[0] for l in left.splitlines()
                                   if "pgrep" not in l and "/bin/bash -c" not in l]
        log(f"leftover service processes of the final trees: {len(s['leftover_processes'])}")
    (EV / f"summary_{phase}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False))
    log(f"summary -> {(EV / f'summary_{phase}.json').relative_to(SRC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
