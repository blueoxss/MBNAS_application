#!/usr/bin/env bash
# doctor.sh - pre-flight / health diagnostics of an installed MBNAS Research Console profile (no secret is printed).
# WP-7A; contract docs/internal/WORK_PACKAGES/WP-7_OPS_PACKAGING.md section 1. Run with --help for the check list.
set -u -o pipefail
unset CDPATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/doctor.sh --profile owner|public [--strict] [--json <file>|-]

Checks one installed profile of the tree that holds this scripts/ folder (ROOT) and prints one line per check:
PASS, WARN (works, but look at it) or FAIL (blocking). Secret values are never printed (only whether they are set).

Options:
  --profile owner|public  (required) profile to check (public: gateway + runner with a remote core; owner: + core).
  --strict                exit 1 when a check FAILs (default: always exit 0 after printing the report).
  --json <file>|-         also write the report as JSON ({root, profile, ok, counts, blocking, checks[]}) to <file>;
                          `-` writes the JSON to stdout and the text report to stderr.
  -h, --help              show this help and exit.

Checks:
  interpreter        runtime/venv exists, Python 3.10, no system site-packages, sys.prefix inside ROOT
  package:<dist>     each package of the profile is installed and imported from inside ROOT (public: mbnas-core
                     must be absent - WARN otherwise)
  path_strings       the installed packages, entry points, config/<profile>/*.env and scripts/*.sh contain no
                     original-host path strings
  entry_points       the console scripts of the profile exist in runtime/venv/bin and point at the venv inside ROOT
  config:<role>      config/<profile>/<role>.env exists, is private (0600), loads with the role's settings class,
                     and the required secrets are set (gateway: MBNAS_GW_API_TOKEN; runner: MBNAS_RUNNER_TOKEN or
                     _TOKEN_FILE; core: MBNAS_CORE_API_TOKEN)
  port:<role>        the configured port of core (8700, owner) / gateway (8710) / web preview (8720 or
                     $MBNAS_WEB_PORT) is free or held by this tree's own running process
  process:<role>     pid file state under runtime/pids (running / not running / stale)
  env:<role>         only for a running Python role (core, owner worker, gateway, runner): its process environment
                     (/proc/<pid>/environ, key names only, never values) has no PYTHONPATH / PYTHONHOME - FAIL when
                     set (the role could import packages from outside runtime/venv; scripts/start.sh removes them),
                     WARN when the environment cannot be read
  gpu                nvidia-smi inventory (read-only query; absent = CPU-only runner, WARN)
  core:gateway       mbnas-gateway-doctor (core health with the gateway service token, artifact store, log dir)
  core:runner        the runner's core_url answers HTTP (no token is sent)
  core_doctor        owner: mbnas-core-doctor problems (DB, worker interpreter, API token)
  disk               free space under runtime/ (WARN < 10 GB, FAIL < 1 GB)
  web_dist           mbnas_web/dist/index.html present (the gateway serves it; WARN when missing)
  A core that is not running is a WARN for the owner profile (start.sh starts it) and a FAIL for the public profile
  (the remote core must answer).

Environment: settings variables already in the environment (MBNAS_GW_*, MBNAS_RUNNER_*, MBNAS_CORE_*) take
precedence over the env files, exactly as for the started processes; MBNAS_HOME is set to <ROOT>/runtime.

Exit codes: 0 report printed (and, with --strict, no FAIL); 1 --strict and at least one FAIL; 2 usage error.
EOF
}

die_usage() { echo "doctor.sh: $*" >&2; echo "Try: bash scripts/doctor.sh --help" >&2; exit 2; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die_usage "option $1 needs a value"; }

PROFILE=""
STRICT=0
JSON_OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --strict) STRICT=1; shift ;;
    --json) need_value "$@"; JSON_OUT="$2"; shift 2 ;;
    --json=*) JSON_OUT="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option: $1" ;;
  esac
done
case "$PROFILE" in
  owner|public) ;;
  "") die_usage "--profile owner|public is required" ;;
  *) die_usage "--profile must be owner or public, got '$PROFILE'" ;;
esac
if [ -n "$JSON_OUT" ] && [ "$JSON_OUT" != "-" ]; then
  case "$JSON_OUT" in /*) ;; *) JSON_OUT="$PWD/$JSON_OUT" ;; esac
fi

export MBNAS_HOME="$ROOT/runtime"
export PYTHONNOUSERSITE=1
VENV_PY="$ROOT/runtime/venv/bin/python"
if [ -x "$VENV_PY" ]; then
  RUN_PY="$VENV_PY"
else
  RUN_PY="$(command -v python3 2>/dev/null || true)"
  [ -n "$RUN_PY" ] || { echo "[FAIL] interpreter: $VENV_PY missing and no python3 on PATH to report further (run scripts/install.sh)"; [ "$STRICT" -eq 1 ] && exit 1; exit 0; }
fi

"$RUN_PY" -I -B - "$ROOT" "$PROFILE" "$JSON_OUT" "$STRICT" <<'PY'
import json, os, re, shutil, socket, stat, subprocess, sys, time
import urllib.error, urllib.request

root, profile, json_out, strict = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
root_real = os.path.realpath(root)
venv = os.path.join(root, "runtime", "venv")
venv_bin = os.path.join(venv, "bin")
env_dir = os.path.join(root, "config", profile)
pids_dir = os.path.join(root, "runtime", "pids")
in_venv = os.path.realpath(sys.prefix) == os.path.realpath(venv)
out = sys.stderr if json_out == "-" else sys.stdout
checks = []
# original-host path strings, composed so that this file itself does not contain them
NEEDLES = ("/etri" + "_nas/", "/root" + "/detection")
MODS = ["mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server"] + (["mbnas_" + "core"] if profile == "owner" else [])
ROLES = ["core", "gateway", "runner"] if profile == "owner" else ["gateway", "runner"]
ENTRY = {"public": ["mbnas-gateway", "mbnas-gateway-doctor", "mbnas-runner"],
         "owner": ["mbnas-gateway", "mbnas-gateway-doctor", "mbnas-runner", "mbnas-core-api", "mbnas-core-owner-worker",
                   "mbnas-core-doctor", "mbnas-core-principal"]}[profile]
PROCS = {"public": ["gateway", "runner", "web"], "owner": ["core", "owner-worker", "gateway", "runner", "web"]}[profile]
MARKER = {"core": "mbnas-core-api", "owner-worker": "mbnas-core-owner-worker", "gateway": "mbnas-gateway",
          "runner": "mbnas-runner", "web": "vite.js"}


def add(name, status, detail):
    checks.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}", file=out, flush=True)


def under_root(path):
    return path is not None and os.path.realpath(path).startswith(root_real + os.sep)


def errors_of(exc):
    """pydantic ValidationError -> 'loc: msg' (never the input values, which may be secrets)."""
    try:
        return "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors())[:600]
    except Exception:  # noqa: BLE001
        return type(exc).__name__


# ---- process identity: marker in argv and (ROOT in argv or cwd == ROOT) -------------------------------------------
def proc_info(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except (OSError, IndexError):
        return None
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
    return state, argv, cwd


def ours(pid, name):
    info = proc_info(pid)
    if info is None or info[0] in ("Z", "X"):
        return False
    _, argv, cwd = info
    marker = any(os.path.basename(a) == MARKER[name] for a in argv)
    rooted = any(a.startswith(root + "/") or a.startswith(root_real + "/") for a in argv) or cwd == root_real
    return marker and rooted


def pid_state(name):
    path = os.path.join(pids_dir, f"{name}.pid")
    if not os.path.isfile(path):
        return "absent", None
    try:
        with open(path, encoding="ascii") as fh:
            pid = int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return "stale", None
    return ("running", pid) if ours(pid, name) else ("stale", pid)


def script_interpreter(head):
    """Interpreter of a console script: the shebang path, or the quoted path of pip's /bin/sh trampoline (used when
    the venv path contains spaces)."""
    lines = head.splitlines()
    if not lines or not lines[0].startswith("#!"):
        return None
    first = lines[0][2:].strip()
    if first == "/bin/sh" or first.endswith("/sh"):
        m = re.search(r"exec'\s+\"([^\"]+)\"", head) or re.search(r"exec'\s+'([^']+)'", head)
        return m.group(1) if m else None
    return first.split()[0] if first else None


# ---- interpreter -------------------------------------------------------------------------------------------------------
if not os.path.isfile(os.path.join(venv_bin, "python")):
    add("interpreter", "FAIL", f"{venv}/bin/python missing - run: bash scripts/install.sh --profile {profile}")
else:
    cfg = ""
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8") as fh:
            cfg = fh.read()
    except OSError:
        pass
    problems = []
    if sys.version_info[:2] != (3, 10):
        problems.append(f"Python {sys.version.split()[0]} (3.10 required)")
    if not re.search(r"^include-system-site-packages\s*=\s*false", cfg, re.M):
        problems.append("system site-packages visible")
    if not in_venv:
        problems.append("doctor did not run inside runtime/venv")
    elif not under_root(sys.prefix):
        problems.append(f"sys.prefix {sys.prefix} outside ROOT")
    add("interpreter", "FAIL" if problems else "PASS",
        "; ".join(problems) if problems else f"Python {sys.version.split()[0]} venv {sys.prefix}")

# ---- packages / path strings / entry points ----------------------------------------------------------------------------
scan_roots = []
if in_venv:
    import importlib.util
    from importlib import metadata
    for mod in MODS:
        dist = mod.replace("_", "-")
        try:
            version = metadata.version(dist)
        except metadata.PackageNotFoundError:
            add(f"package:{dist}", "FAIL", "not installed")
            continue
        spec = importlib.util.find_spec(mod)
        origin = spec.origin if spec else None
        editable = False
        try:
            d = metadata.distribution(dist)
            editable = bool((json.loads(d.read_text("direct_url.json") or "{}").get("dir_info") or {}).get("editable"))
            scan_roots.append(str(getattr(d, "_path", "") or ""))  # the .dist-info directory
        except Exception:  # noqa: BLE001
            pass
        if spec and spec.submodule_search_locations:
            scan_roots.extend(spec.submodule_search_locations)
        if under_root(origin):
            add(f"package:{dist}", "PASS", f"{version} from {os.path.dirname(origin)}" + (" (editable)" if editable else ""))
        else:
            add(f"package:{dist}", "FAIL", f"{version} imported from outside ROOT: {origin}")
    if profile == "public":
        if importlib.util.find_spec("mbnas_" + "core") is not None:
            add("package:mbnas-core", "WARN", "the owner-only package is importable in this venv (a public host must not have it)")
        else:
            add("package:mbnas-core", "PASS", "absent (public profile)")

entry_problems, entry_ok = [], []
for name in ENTRY:
    path = os.path.join(venv_bin, name)
    if not os.access(path, os.X_OK):
        entry_problems.append(f"{name} missing")
        continue
    try:
        with open(path, "rb") as fh:
            head = fh.read(600).decode("utf-8", "replace")
    except OSError:
        entry_problems.append(f"{name} unreadable")
        continue
    interp = script_interpreter(head)
    if not interp or not under_root(os.path.dirname(interp)):  # bin/python itself links to the base
        entry_problems.append(f"{name} runs {interp} (not the venv inside ROOT: reinstall after moving the tree)")
    else:
        entry_ok.append(name)
add("entry_points", "FAIL" if entry_problems else "PASS",
    "; ".join(entry_problems) if entry_problems else f"{len(entry_ok)} console scripts in runtime/venv/bin")

scan_files = []
for base in [p for p in scan_roots if p]:
    if os.path.isdir(base):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            scan_files += [os.path.join(dirpath, f) for f in filenames]
scan_files += [os.path.join(venv_bin, n) for n in ENTRY if os.path.isfile(os.path.join(venv_bin, n))]
if os.path.isdir(env_dir):
    scan_files += [os.path.join(env_dir, f) for f in sorted(os.listdir(env_dir)) if f.endswith(".env")]
scripts_dir = os.path.join(root, "scripts")
if os.path.isdir(scripts_dir):
    scan_files += [os.path.join(scripts_dir, f) for f in sorted(os.listdir(scripts_dir)) if f.endswith(".sh")]
hits = []
for path in scan_files:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        continue
    for needle in NEEDLES:
        if needle.encode() in data:
            hits.append(os.path.relpath(path, root))
            break
if not in_venv:
    add("path_strings", "WARN", "packages not scanned (runtime/venv missing)")
else:
    add("path_strings", "FAIL" if hits else "PASS",
        f"{len(hits)} file(s) hold original-host paths: {', '.join(hits[:8])}" if hits
        else f"{len(scan_files)} files scanned, no original-host path string")

# ---- config ------------------------------------------------------------------------------------------------------------
settings = {}
SETTINGS = {"core": ("mbnas_" + "core.config", "CoreSettings"), "gateway": ("mbnas_server.config", "GatewaySettings"),
            "runner": ("mbnas_runner.config", "RunnerSettings")}
for role in ROLES:
    path = os.path.join(env_dir, f"{role}.env")
    if not os.path.isfile(path):
        add(f"config:{role}", "FAIL", f"config/{profile}/{role}.env missing - run: bash scripts/install.sh --profile {profile}")
        continue
    notes, status = [], "PASS"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        status = "WARN"
        notes.append(f"mode {oct(mode)} (chmod 600 config/{profile}/{role}.env)")
    if not in_venv:
        add(f"config:{role}", "WARN", "present; not loaded (runtime/venv missing)")
        continue
    modname, cls = SETTINGS[role]
    try:
        module = __import__(modname, fromlist=[cls])
        s = getattr(module, cls).load(path)
    except Exception as exc:  # noqa: BLE001
        detail = errors_of(exc) if hasattr(exc, "errors") else type(exc).__name__
        add(f"config:{role}", "FAIL", f"does not load: {detail}")
        continue
    settings[role] = s
    if role == "gateway":
        need = {"MBNAS_GW_API_TOKEN": s.api_token is not None}
        soft = {"MBNAS_GW_ARTIFACT_SIGNING_KEY": s.artifact_signing_key is not None, "MBNAS_GW_CORE_TOKEN": s.core_token is not None}
        notes.append(f"core_url {s.core_url}, listen {s.host}:{s.port}")
    elif role == "runner":
        has_token = s.token is not None or (s.token_file is not None and os.path.isfile(s.token_file))
        need = {"MBNAS_RUNNER_TOKEN|_TOKEN_FILE": has_token}
        soft = {}
        notes.append(f"core_url {s.core_url}, runner_id {s.runner_id}")
    else:
        need = {"MBNAS_CORE_API_TOKEN": s.api_token is not None}
        soft = {"MBNAS_CORE_ARTIFACT_SIGNING_KEY": s.artifact_signing_key is not None, "MBNAS_CORE_TICKET_KEY": s.ticket_key is not None}
        notes.append(f"listen {s.host}:{s.port}, db {s.db_path}")
    missing = [k for k, ok in need.items() if not ok]
    soft_missing = [k for k, ok in soft.items() if not ok]
    if missing:
        status = "FAIL"
        notes.append("required secret not set: " + ", ".join(missing))
    if soft_missing:
        status = "WARN" if status == "PASS" else status
        notes.append("not set: " + ", ".join(soft_missing))
    notes.append("secrets set: " + (", ".join(k for k, ok in {**need, **soft}.items() if ok) or "none"))
    add(f"config:{role}", status, "; ".join(notes))

# ---- processes and ports -------------------------------------------------------------------------------------------------
states = {name: pid_state(name) for name in PROCS}
for name in PROCS:
    st, pid = states[name]
    if st == "running":
        add(f"process:{name}", "PASS", f"running (pid {pid})")
    elif st == "stale":
        add(f"process:{name}", "WARN", f"stale pid file runtime/pids/{name}.pid" + (f" (pid {pid} is not ours)" if pid else ""))
    elif name != "web":
        add(f"process:{name}", "PASS", "not running")

# ---- environment of the running Python roles (C09 D1): no inherited search-path variables ---------------------------
SHADOW_KEYS = ("PYTHONPATH", "PYTHONHOME")  # FAIL: imports could come from outside runtime/venv
SCRUBBED_KEYS = ("PYTHONSTARTUP", "PYTHONUSERBASE", "OLDPWD")  # start.sh removes these too (noted, not blocking)
for name in PROCS:
    st, pid = states[name]
    if st != "running" or name == "web":  # web = node (vite preview), not a Python role
        continue
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            keys = {item.split(b"=", 1)[0].decode("utf-8", "replace") for item in fh.read().split(b"\0") if item}
    except OSError as exc:
        add(f"env:{name}", "WARN", f"/proc/{pid}/environ not readable ({type(exc).__name__}): not checked")
        continue
    shadow = [k for k in SHADOW_KEYS if k in keys]
    extra = [k for k in SCRUBBED_KEYS if k in keys]
    note = f"; also set (not blocking): {', '.join(extra)}" if extra else ""
    if shadow:
        add(f"env:{name}", "FAIL", f"pid {pid} runs with {', '.join(shadow)} set: it can import packages from outside "
            f"runtime/venv - restart it with scripts/stop.sh and scripts/start.sh (which removes them){note}")
    else:
        add(f"env:{name}", "PASS", f"pid {pid}: no PYTHONPATH / PYTHONHOME in its environment{note}")


def port_free(host, port):
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


ports = []
if "core" in settings:
    ports.append(("core", settings["core"].host, settings["core"].port, "FAIL"))
if "gateway" in settings:
    ports.append(("gateway", settings["gateway"].host, settings["gateway"].port, "FAIL"))
try:
    ports.append(("web", "127.0.0.1", int(os.environ.get("MBNAS_WEB_PORT") or 8720), "WARN"))
except ValueError:
    add("port:web", "WARN", "MBNAS_WEB_PORT is not a number")
for name, host, port, severity in ports:
    if port_free(host, port):
        add(f"port:{name}", "PASS", f"{host}:{port} free")
    elif states.get(name, ("absent", None))[0] == "running":
        add(f"port:{name}", "PASS", f"{host}:{port} held by this tree's {name} (pid {states[name][1]})")
    else:
        add(f"port:{name}", severity, f"{host}:{port} in use by another process")

# ---- gpu ----------------------------------------------------------------------------------------------------------------
smi = shutil.which("nvidia-smi")
if not smi:
    add("gpu", "WARN", "nvidia-smi not found: CPU-only runner (training/checkpoints only; latency needs CUDA, DD-A32)")
else:
    try:
        q = subprocess.run([smi, "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
        rows = [r.split(", ") for r in q.stdout.strip().splitlines() if r.strip()]
        if q.returncode != 0 or not rows:
            add("gpu", "WARN", f"nvidia-smi exit {q.returncode}: no GPU listed")
        else:
            add("gpu", "PASS", "; ".join(f"GPU{r[0]} {r[1]} {r[2]}/{r[3]} MiB util {r[4]}%" for r in rows if len(r) >= 5))
    except (OSError, subprocess.TimeoutExpired) as exc:
        add("gpu", "WARN", f"nvidia-smi failed: {type(exc).__name__}")

# ---- core connection ----------------------------------------------------------------------------------------------------
core_running = states.get("core", ("absent", None))[0] == "running"
miss_severity = "FAIL" if profile == "public" or core_running else "WARN"
miss_hint = "" if profile == "public" or core_running else " (core not running: bash scripts/start.sh --profile owner --role core)"
gw_doc = os.path.join(venv_bin, "mbnas-gateway-doctor")
if "gateway" in settings and os.access(gw_doc, os.X_OK):
    try:
        r = subprocess.run([gw_doc, "--env-file", os.path.join(env_dir, "gateway.env")], capture_output=True,
                           text=True, timeout=120, cwd=root)
        doc = json.loads(r.stdout)
        for c in doc.get("checks", []):
            if c.get("name") == "core":
                add("core:gateway", "PASS" if c.get("ok") else miss_severity, str(c.get("detail")) + ("" if c.get("ok") else miss_hint))
            elif c.get("name") in ("config", "core_token", "web_dist"):
                continue  # covered by config:gateway / web_dist
            else:
                add(f"gateway:{c.get('name')}", "PASS" if c.get("ok") else ("WARN" if c.get("warning") else "FAIL"), str(c.get("detail")))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        add("core:gateway", "FAIL", f"mbnas-gateway-doctor did not report: {type(exc).__name__}")
if "runner" in settings:
    s = settings["runner"]
    url = s.core_url.rstrip("/") + "/core/v1/console/health"
    ctx = None
    if url.startswith("https://"):
        import ssl
        ctx = ssl.create_default_context(cafile=s.ca_file) if s.ca_file else ssl.create_default_context()
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10, context=ctx) as resp:
            add("core:runner", "PASS", f"{s.core_url} answers (HTTP {resp.status})")
    except urllib.error.HTTPError as exc:
        add("core:runner", "PASS", f"{s.core_url} answers (HTTP {exc.code} without a token)")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        add("core:runner", miss_severity, f"{s.core_url} unreachable ({type(getattr(exc, 'reason', exc)).__name__})" + miss_hint)

if profile == "owner" and "core" in settings:
    exe = os.path.join(venv_bin, "mbnas-core-doctor")
    try:
        r = subprocess.run([exe, "--env-file", os.path.join(env_dir, "core.env")], capture_output=True, text=True,
                           timeout=300, cwd=root)
        doc = json.loads(r.stdout)
        problems = doc.get("problems") or []
        db = doc.get("db") or {}
        reg = doc.get("registry") or {}
        summary = (f"db {'present' if db.get('exists') else 'not created yet'}"
                   + (f" (schema {db.get('schema_version')})" if db.get("schema_version") else "")
                   + f"; registry {reg.get('entries')} entries ({reg.get('source')}); free {doc.get('output_root_free_gb')} GB")
        add("core_doctor", "FAIL" if problems else "PASS", ("; ".join(problems) + "; " if problems else "") + summary)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        add("core_doctor", "FAIL", f"mbnas-core-doctor did not report: {type(exc).__name__}")

# ---- disk / web ---------------------------------------------------------------------------------------------------------
runtime_dir = os.path.join(root, "runtime")
try:
    free_gb = shutil.disk_usage(runtime_dir if os.path.isdir(runtime_dir) else root).free / 1e9
    add("disk", "FAIL" if free_gb < 1 else ("WARN" if free_gb < 10 else "PASS"), f"{free_gb:.1f} GB free under runtime/")
except OSError as exc:
    add("disk", "WARN", f"cannot stat runtime/: {type(exc).__name__}")
dist_index = os.path.join(root, "mbnas_web", "dist", "index.html")
add("web_dist", "PASS" if os.path.isfile(dist_index) else "WARN",
    "mbnas_web/dist present (served by the gateway at /)" if os.path.isfile(dist_index)
    else "mbnas_web/dist missing: the gateway serves the API only (bash scripts/install.sh --web builds it)")

counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("PASS", "WARN", "FAIL")}
blocking = [c["name"] for c in checks if c["status"] == "FAIL"]
print(f"doctor: profile={profile} root={root} PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}"
      + (f" blocking: {', '.join(blocking)}" if blocking else ""), file=out)
report = {"schema": "mbnas.doctor/1", "root": root, "profile": profile, "ok": not blocking, "counts": counts,
          "blocking": blocking, "checks": checks, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
if json_out == "-":
    print(json.dumps(report, indent=1))
elif json_out:
    os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
sys.exit(1 if strict and blocking else 0)
PY
exit $?
