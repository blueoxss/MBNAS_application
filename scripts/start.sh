#!/usr/bin/env bash
# start.sh - start the processes of one MBNAS Research Console profile in the background (setsid nohup).
# WP-7A; contract docs/internal/WORK_PACKAGES/WP-7_OPS_PACKAGING.md section 1, DD-A30 (gateway serves mbnas_web/dist).
set -u -o pipefail
unset CDPATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/start.sh --profile owner|public [--role core|gateway|runner|web|all] [--env-dir <dir>]
                             [--wait-s <seconds>]

Starts the role processes of the tree that holds this scripts/ folder (ROOT), each in its own session
(setsid nohup), with the working directory ROOT and MBNAS_HOME=<ROOT>/runtime. Pid files: runtime/pids/<name>.pid,
logs (appended): runtime/logs/<name>.log. A role whose process is already running is refused (stop it first).

Options:
  --profile owner|public  (required) public = gateway + runner, connected to a remote core (the public profile never
                          runs a core: --role core is refused). owner = core API + owner worker + gateway + runner.
  --role <role>           core     core API (mbnas-core-api, port 8700) and owner worker
                                   (mbnas-core-owner-worker --owner); owner profile only
                          gateway  mbnas-gateway (port 8710): /api/v1 relay to the core + the web console from
                                   mbnas_web/dist at / (DD-A30)
                          runner   mbnas-runner run: claims training/evaluation/benchmark jobs from the core
                          web      vite preview of mbnas_web/dist on 127.0.0.1:${MBNAS_WEB_PORT:-8720} (development
                                   / e2e only; needs Node.js and mbnas_web/node_modules)
                          all      (default) owner: core, owner worker, gateway, runner; public: gateway, runner
  --env-dir <dir>         directory of the env files (default: <ROOT>/config/<profile>): core.env (core API and owner
                          worker), gateway.env, runner.env - written by scripts/install.sh
  --wait-s <seconds>      how long to wait for each process to become ready (default 60)
  -h, --help              show this help and exit.

Readiness: core = its HTTP port answers; gateway = GET /api/v1/health with the console token answers (the line shows
whether the core is reachable); owner worker and runner = still alive after a few seconds. Before the runner starts,
its core_url must answer HTTP (a runner without a reachable core exits).

Environment: variables already set win over the env files (pydantic-settings), e.g. for other ports:
  MBNAS_CORE_PORT=18700 MBNAS_GW_PORT=18710 MBNAS_GW_CORE_URL=http://127.0.0.1:18700 \
  MBNAS_RUNNER_CORE_URL=http://127.0.0.1:18700 bash scripts/start.sh --profile owner
MBNAS_WEB_PORT sets the web preview port (default 8720). The core, owner worker and gateway run with
CUDA_VISIBLE_DEVICES='' (CPU roles); the runner inherits the caller's CUDA_VISIBLE_DEVICES. The caller's
PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, PYTHONUSERBASE and OLDPWD are removed before any process starts and
PYTHONNOUSERSITE=1 is set, so only the packages of runtime/venv are imported (doctor.sh checks env:<role>).

Exit codes: 0 every requested process is ready; 1 refused (already running, missing install/config, port in use,
core unreachable) or a process failed to become ready (it is left as is: see its log, stop with scripts/stop.sh);
2 usage error.
EOF
}

die_usage() { echo "start.sh: $*" >&2; echo "Try: bash scripts/start.sh --help" >&2; exit 2; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die_usage "option $1 needs a value"; }

PROFILE=""
ROLE="all"
ENV_DIR=""
WAIT_S=60
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --role) need_value "$@"; ROLE="$2"; shift 2 ;;
    --role=*) ROLE="${1#*=}"; shift ;;
    --env-dir) need_value "$@"; ENV_DIR="$2"; shift 2 ;;
    --env-dir=*) ENV_DIR="${1#*=}"; shift ;;
    --wait-s) need_value "$@"; WAIT_S="$2"; shift 2 ;;
    --wait-s=*) WAIT_S="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option: $1" ;;
  esac
done
case "$PROFILE" in
  owner|public) ;;
  "") die_usage "--profile owner|public is required" ;;
  *) die_usage "--profile must be owner or public, got '$PROFILE'" ;;
esac
case "$ROLE" in core|gateway|runner|web|all) ;; *) die_usage "--role must be core, gateway, runner, web or all" ;; esac
case "$WAIT_S" in ''|*[!0-9]*) die_usage "--wait-s needs a whole number of seconds" ;; esac
WEB_PORT="${MBNAS_WEB_PORT:-8720}"
case "$WEB_PORT" in ''|*[!0-9]*) die_usage "MBNAS_WEB_PORT must be a port number" ;; esac

ROOT_REAL="$(cd "$ROOT" && pwd -P)"
VENV="$ROOT/runtime/venv"
VENV_PY="$VENV/bin/python"
PIDS="$ROOT/runtime/pids"
LOGS="$ROOT/runtime/logs"
if [ -z "$ENV_DIR" ]; then
  ENV_DIR="$ROOT/config/$PROFILE"
else
  case "$ENV_DIR" in /*) ;; *) ENV_DIR="$PWD/$ENV_DIR" ;; esac
fi

if [ "$PROFILE" = "public" ] && [ "$ROLE" = "core" ]; then
  echo "start.sh: the public profile does not run the core: the gateway and runner connect to the remote core of the" >&2
  echo "start.sh: owner (MBNAS_GW_CORE_URL in gateway.env, MBNAS_RUNNER_CORE_URL in runner.env)." >&2
  exit 1
fi
case "$ROLE" in
  core) PROCS=(core owner-worker) ;;
  all) if [ "$PROFILE" = "owner" ]; then PROCS=(core owner-worker gateway runner); else PROCS=(gateway runner); fi ;;
  *) PROCS=("$ROLE") ;;
esac

# ---- process identity (same block in start.sh and stop.sh) -----------------------------------------------------------
proc_marker() {
  case "$1" in
    core) echo "mbnas-core-api" ;;
    owner-worker) echo "mbnas-core-owner-worker" ;;
    gateway) echo "mbnas-gateway" ;;
    runner) echo "mbnas-runner" ;;
    web) echo "vite.js" ;;
    *) echo "" ;;
  esac
}
proc_state() {  # pid -> one-letter process state, empty when the process is gone
  local stat="" rest=""
  stat="$(cat "/proc/$1/stat" 2>/dev/null)" || { echo ""; return 0; }
  rest="${stat##*) }"
  echo "${rest%% *}"
}
is_ours() {  # name pid -> 0 when pid is a live process of this tree's <name>: the marker is one of its argv basenames
  local name="$1" pid="$2" marker="" arg="" st="" cwd="" found=1 rooted=1  # and an argv entry lies under ROOT or
  # its cwd is ROOT. Locals start set: a process that exits mid-check has an empty cmdline (read assigns nothing)
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  st="$(proc_state "$pid")"
  case "$st" in ''|Z|X) return 1 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 1
  marker="$(proc_marker "$name")"
  while IFS= read -r -d '' arg || [ -n "$arg" ]; do
    [ "${arg##*/}" = "$marker" ] && found=0
    case "$arg" in "$ROOT"/*|"$ROOT_REAL"/*) rooted=0 ;; esac
  done <"/proc/$pid/cmdline"
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  [ "$cwd" = "$ROOT_REAL" ] && rooted=0
  [ "$found" -eq 0 ] && [ "$rooted" -eq 0 ]
}
read_pid() {  # pid file -> pid, empty when missing or not a number
  local pid=""
  [ -f "$1" ] && read -r pid _ <"$1" 2>/dev/null
  case "$pid" in ''|*[!0-9]*) echo "" ;; *) echo "$pid" ;; esac
}
# ---- end of the shared block -------------------------------------------------------------------------------------------

env_file_of() {
  case "$1" in core|owner-worker) echo "$ENV_DIR/core.env" ;; gateway) echo "$ENV_DIR/gateway.env" ;; runner) echo "$ENV_DIR/runner.env" ;; *) echo "" ;; esac
}

# python helper: settings/precheck and readiness; secrets stay inside this process (never printed, never in argv)
read -r -d '' PYHELPER <<'PY'
import json, os, socket, sys, time, urllib.error, urllib.request
mode, name, env_file, pid, wait_s, web_port = sys.argv[1:7]
pid, wait_s, web_port = int(pid or 0), float(wait_s or 0), int(web_port or 0)

def errors_of(exc):
    try:
        return "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors())[:500]
    except Exception:  # noqa: BLE001 - never print the values (they may be secrets)
        return type(exc).__name__

def settings():
    if name in ("core", "owner-worker"):
        from importlib import import_module
        cls = import_module("mbnas_" + "core.config").CoreSettings
    elif name == "gateway":
        from mbnas_server.config import GatewaySettings as cls
    else:
        from mbnas_runner.config import RunnerSettings as cls
    return cls.load(env_file)

def port_free(host, port):
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()

def probe_host(host):
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host

def http(url, token=None, timeout=10, cafile=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    ctx = None
    if url.startswith("https://"):
        import ssl
        ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""

def alive(p):
    try:
        with open(f"/proc/{p}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False

def done(ok, msg):
    print(("OK " if ok else "ERR ") + msg.replace("\n", " "))
    sys.exit(0 if ok else 1)

if mode == "precheck":
    if name == "web":
        if not port_free("127.0.0.1", web_port):
            done(False, f"port 127.0.0.1:{web_port} is in use (MBNAS_WEB_PORT)")
        done(True, f"http://127.0.0.1:{web_port}")
    try:
        s = settings()
    except Exception as exc:  # noqa: BLE001
        done(False, f"{env_file} does not load: " + (errors_of(exc) if hasattr(exc, "errors") else type(exc).__name__))
    if name in ("core", "gateway"):
        if s.api_token is None:
            done(False, f"{'MBNAS_CORE_API_TOKEN' if name == 'core' else 'MBNAS_GW_API_TOKEN'} is not set in {env_file} "
                        "(scripts/install.sh generates it)")
        if not port_free(s.host, s.port):
            done(False, f"port {s.host}:{s.port} is in use by another process")
        done(True, f"http://{probe_host(s.host)}:{s.port}")
    if name == "runner":
        if s.token is None and s.token_file is None:
            done(False, f"no runner token in {env_file}: MBNAS_RUNNER_TOKEN or MBNAS_RUNNER_TOKEN_FILE (issued by the "
                        "core operator: mbnas-core-principal create --kind runner --runner-id " + str(s.runner_id) + ")")
        try:
            code, _ = http(s.core_url.rstrip("/") + "/core/v1/console/health", timeout=10, cafile=s.ca_file)
        except Exception as exc:  # noqa: BLE001
            done(False, f"core {s.core_url} is unreachable ({type(getattr(exc, 'reason', exc)).__name__}); the runner "
                        "would exit - start the core first or fix MBNAS_RUNNER_CORE_URL")
        done(True, f"core {s.core_url} answers (HTTP {code}); runner id {s.runner_id}")
    done(True, "-")

if mode == "wait":
    deadline = time.monotonic() + wait_s
    if name in ("owner-worker", "runner"):
        settle = min(wait_s, 5.0)
        end = time.monotonic() + settle
        while time.monotonic() < end:
            if not alive(pid):
                done(False, "exited during start-up")
            time.sleep(0.25)
        done(True, f"alive after {settle:.0f} s")
    if name == "web":
        url, token, s = f"http://127.0.0.1:{web_port}/", None, None
    else:
        s = settings()
        base = f"http://{probe_host(s.host)}:{s.port}"
        if name == "core":
            url, token = base + "/core/v1/console/health", None
        else:
            url, token = base + "/api/v1/health", s.api_token.get_secret_value()
    while time.monotonic() < deadline:
        if not alive(pid):
            done(False, "exited during start-up")
        try:
            code, body = http(url, token=token, timeout=15)
        except Exception:  # noqa: BLE001 - not listening yet
            time.sleep(0.5)
            continue
        detail = f"HTTP {code} from {url}"
        if name == "gateway" and code == 200:
            try:
                doc = json.loads(body)
                core = doc.get("core")
                reach = core.get("reachable") if isinstance(core, dict) else core
                detail += f" status={doc.get('status')} core={'reachable' if reach is True else reach}"
            except ValueError:
                pass
        elif name == "gateway" and code == 401:
            detail += " (token rejected: the running gateway uses another MBNAS_GW_API_TOKEN?)"
        done(True, detail)
    done(False, f"no HTTP answer from {url} within {wait_s:.0f} s")
done(False, f"unknown mode {mode}")
PY

helper() {  # mode name -> runs the python helper, prints its one-line result
  local py="$VENV_PY"
  [ -x "$py" ] || py="$(command -v python3 2>/dev/null || echo python3)"
  MBNAS_HOME="$ROOT/runtime" PYTHONNOUSERSITE=1 "$py" -I -B -c "$PYHELPER" "$1" "$2" "$(env_file_of "$2")" \
    "${3:-0}" "$WAIT_S" "$WEB_PORT" 2>&1 | tail -n 1
  return "${PIPESTATUS[0]}"
}

build_argv() {  # name -> ARGV (argv array of the role process; never a shell string)
  local envf=""
  envf="$(env_file_of "$1")"
  case "$1" in
    core) ARGV=("$VENV_PY" "$VENV/bin/mbnas-core-api" --env-file "$envf") ;;
    owner-worker) ARGV=("$VENV_PY" "$VENV/bin/mbnas-core-owner-worker" --owner --env-file "$envf") ;;
    gateway) ARGV=("$VENV_PY" "$VENV/bin/mbnas-gateway" --env-file "$envf") ;;
    runner) ARGV=("$VENV_PY" "$VENV/bin/mbnas-runner" run --env-file "$envf") ;;
    web) ARGV=("$(command -v node)" "$ROOT/mbnas_web/node_modules/vite/bin/vite.js" preview --host 127.0.0.1
               --port "$WEB_PORT" --strictPort) ;;
  esac
}

entry_of() {
  case "$1" in core) echo mbnas-core-api ;; owner-worker) echo mbnas-core-owner-worker ;; gateway) echo mbnas-gateway ;; runner) echo mbnas-runner ;; esac
}

mkdir -p "$PIDS" "$LOGS" || { echo "start.sh: cannot create $PIDS / $LOGS" >&2; exit 1; }

# ---- refuse when a requested process already runs; drop stale pid files ----------------------------------------------
running=()
for name in "${PROCS[@]}"; do
  pidf="$PIDS/$name.pid"
  [ -e "$pidf" ] || continue
  pid="$(read_pid "$pidf")"
  if is_ours "$name" "$pid"; then
    running+=("$name (pid $pid)")
  else
    rm -f "$pidf"
    echo "[start] $name: removed stale pid file (pid ${pid:-?} is not running this tree's $name)"
  fi
done
if [ ${#running[@]} -gt 0 ]; then
  echo "start.sh: already running: ${running[*]} - stop first: bash scripts/stop.sh --profile $PROFILE --role $ROLE" >&2
  exit 1
fi

# ---- install / config presence ---------------------------------------------------------------------------------------
for name in "${PROCS[@]}"; do
  if [ "$name" = "web" ]; then
    command -v node >/dev/null 2>&1 || { echo "start.sh: --role web needs Node.js (node on PATH)" >&2; exit 1; }
    [ -f "$ROOT/mbnas_web/node_modules/vite/bin/vite.js" ] || { echo "start.sh: mbnas_web/node_modules missing (bash scripts/install.sh --profile $PROFILE --web)" >&2; exit 1; }
    [ -f "$ROOT/mbnas_web/dist/index.html" ] || { echo "start.sh: mbnas_web/dist missing (bash scripts/install.sh --profile $PROFILE --web)" >&2; exit 1; }
    continue
  fi
  [ -x "$VENV_PY" ] || { echo "start.sh: $VENV_PY missing - run: bash scripts/install.sh --profile $PROFILE" >&2; exit 1; }
  [ -f "$VENV/bin/$(entry_of "$name")" ] || { echo "start.sh: $(entry_of "$name") is not installed in runtime/venv (profile $PROFILE)" >&2; exit 1; }
  envf="$(env_file_of "$name")"
  [ -f "$envf" ] || { echo "start.sh: $envf missing - run: bash scripts/install.sh --profile $PROFILE" >&2; exit 1; }
done

# ---- clean environment of the role processes (C09 D1; T8: no caller cwd in a service environment) ---------------------
# The roles run "<venv>/bin/python <entry>" without -I, so a PYTHONPATH / PYTHONHOME inherited from the caller would put
# other directories before runtime/venv (a shadowing mbnas_* package would be imported); PYTHONSTARTUP and PYTHONUSERBASE
# go too. OLDPWD would carry the caller's working directory: bash exports it after any cd, even when the caller had
# none, so it is removed here, before the cd of launch(), and unset again right after that cd (belt and braces).
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE OLDPWD
export MBNAS_HOME="$ROOT/runtime"
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

launch() {  # name -> starts the process in its own session; writes runtime/pids/<name>.pid
  local name="$1" pidf="$PIDS/$1.pid" log="$LOGS/$1.log" tmp="$PIDS/.$1.pid.starting" workdir="$ROOT" i=0
  [ "$name" = "web" ] && workdir="$ROOT/mbnas_web"
  rm -f "$tmp"
  build_argv "$name"
  echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) start.sh --profile $PROFILE: $name: ${ARGV[*]}" >>"$log"
  case "$name" in
    core|owner-worker|gateway)
      ( cd "$workdir" && unset OLDPWD PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE \
          && CUDA_VISIBLE_DEVICES="" exec setsid nohup bash -c 'echo "$$" >"$0" && exec "$@"' "$tmp" "${ARGV[@]}" ) \
        >>"$log" 2>&1 </dev/null & ;;
    *)
      ( cd "$workdir" && unset OLDPWD PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE \
          && exec setsid nohup bash -c 'echo "$$" >"$0" && exec "$@"' "$tmp" "${ARGV[@]}" ) \
        >>"$log" 2>&1 </dev/null & ;;
  esac
  for i in $(seq 1 100); do
    [ -s "$tmp" ] && break
    sleep 0.1
  done
  LAUNCHED_PID="$(read_pid "$tmp")"
  if [ -z "$LAUNCHED_PID" ]; then
    rm -f "$tmp"
    return 1
  fi
  mv -f "$tmp" "$pidf"
  return 0
}

failed=0
for name in "${PROCS[@]}"; do
  out="$(helper precheck "$name")"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "start.sh: $name not started: ${out#ERR }" >&2
    failed=1
    break
  fi
  if ! launch "$name"; then
    echo "start.sh: $name did not start (no pid file); see $LOGS/$name.log" >&2
    failed=1
    break
  fi
  out="$(helper wait "$name" "$LAUNCHED_PID")"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[start] $name: ready pid=$LAUNCHED_PID ${out#OK } log=runtime/logs/$name.log"
  else
    echo "start.sh: $name (pid $LAUNCHED_PID) is not ready: ${out#ERR }; last log lines:" >&2
    tail -n 8 "$LOGS/$name.log" 2>/dev/null | sed 's/^/    /' >&2
    is_ours "$name" "$LAUNCHED_PID" || rm -f "$PIDS/$name.pid"
    failed=1
    break
  fi
done
if [ "$failed" -ne 0 ]; then
  echo "start.sh: start incomplete; running processes are left as they are (bash scripts/stop.sh --profile $PROFILE)" >&2
  exit 1
fi
if [ "$PROFILE" = "public" ] && [ "$ROLE" = "all" ]; then
  echo "[start] public profile: no core here - the gateway and runner use the remote core of the owner"
fi
exit 0
