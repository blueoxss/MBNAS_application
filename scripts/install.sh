#!/usr/bin/env bash
# install.sh - install the MBNAS Research Console runtime of one profile into <ROOT>/runtime/venv.
# WP-7A; contract docs/internal/WORK_PACKAGES/WP-7_OPS_PACKAGING.md section 1/1b, DD-A31 (standard pip path), DD-A13
# (MBNAS_HOME = <ROOT>/runtime), DD-A7 (Python 3.10). Run `bash scripts/install.sh --help` for the full description.
set -u -o pipefail
unset CDPATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/install.sh --profile owner|public [options]

Thin wrapper around the standard installation path (DD-A31):
    <python3.10> -m venv runtime/venv
    runtime/venv/bin/python -m pip install -r requirements-<profile>.txt      (run from the ROOT directory)
plus the runtime directories and the per-role configuration files. Every path is relative to ROOT, the directory
that holds this scripts/ folder, so the tree can live anywhere (also in a path with spaces) and the script can be
called from any working directory.

Options:
  --profile owner|public  (required) public = mbnas-contracts, mbnas-model-runtime, mbnas-runner, mbnas-server
                          (gateway + runner hosts; they connect to a remote core). owner = public + mbnas-core
                          (core API + owner worker on the owner host).
  --python <interp>       Python 3.10 interpreter that creates runtime/venv (default: python3.10 on PATH, else
                          python3 when it is 3.10). The venv never sees system site-packages (PYTHONNOUSERSITE=1).
  --offline               install without network: pip install --no-index --find-links <wheelhouse>
                          (plus -c constraints.txt when that file exists and the default requirements file is used).
                          Without --offline pip downloads from PyPI and https://download.pytorch.org/whl/cu124 (the
                          index named inside the requirements file): network access is required.
  --wheelhouse <dir>      wheel directory used by --offline (default: <ROOT>/wheelhouse, else <ROOT>/build/wheelhouse).
                          Only valid together with --offline.
  --requirements <file>   use this requirements file instead of requirements-<profile>.txt, for example
                          requirements-cpu.txt (CPU-only torch; the validated platform is cu124). A relative path is
                          taken from ROOT. pip runs in the directory of the file (wheel paths inside are relative).
                          With --profile owner, wheels/mbnas_core-<version>-py3-none-any.whl is added when the file
                          does not list it (requirements-cpu.txt lists the public wheels only).
  --web                   also run `npm ci` in mbnas_web (cache <ROOT>/build/npm-cache; with --offline npm also gets
                          --offline) and `npm run build` when mbnas_web/dist is missing. Needs Node.js. Not needed to
                          serve the shipped mbnas_web/dist: the gateway serves it (DD-A30).
  -h, --help              show this help and exit.

Steps:
  1. checks the package metadata (BUILD_INFO.json / PACKAGE_PROFILE.json when present: a public or registration
     package cannot be installed with --profile owner) and the requirements file;
  2. creates runtime/venv with the interpreter (an existing venv is reused when it is Python 3.10 without system
     site-packages; a venv holding editable development installs of the mbnas packages is refused, never replaced);
  3. builds a project wheel referenced by the requirements file but missing from ROOT (source tree only: pip wheel
     --no-deps from <ROOT>/<package>, build isolation; offline builds take setuptools/wheel from the wheelhouse);
  4. pip install -r <requirements> (log: runtime/logs/install-<UTC stamp>.log) and checks that every package of the
     profile imports from inside ROOT;
  5. creates runtime/{pids,logs,data,weights,uploads,locks};
  6. writes config/<profile>/<role>.env (roles: public = gateway, runner; owner = core, gateway, runner) by copying
     config/examples/<role>.env.example. An existing file is never overwritten: only secrets that are missing,
     empty or a placeholder are filled. Generated secrets (python secrets.token_urlsafe(48)):
       core.env     MBNAS_CORE_API_TOKEN, MBNAS_CORE_ARTIFACT_SIGNING_KEY, MBNAS_CORE_TICKET_KEY
       gateway.env  MBNAS_GW_API_TOKEN (the console bearer token), MBNAS_GW_ARTIFACT_SIGNING_KEY
     Core-issued tokens: owner profile - a server principal (gateway.env MBNAS_GW_CORE_TOKEN) and a runner principal
     (runner.env MBNAS_RUNNER_TOKEN, bound to the runner id of runner.env) are created in the local core DB with
     mbnas-core-principal; public profile - these two come from the core operator
     (mbnas-core-principal create --kind server | --kind runner --runner-id <id>; the runner id to give is printed),
     and a placeholder line is commented out until then. Any other line with an empty value is commented out (unset =
     default). The runner id is MBNAS_RUNNER_RUNNER_ID when runner.env sets it, else the runner's default: a random
     runner-<8 hex> generated at install time and kept in runtime/runner/runner_id (never derived from the host name).
     Files and the directory are private (0600 / 0700). Secret values are never printed: read the console token
     with  grep MBNAS_GW_API_TOKEN config/<profile>/gateway.env
  7. optional web step (--web), then an environment summary.

Environment:
  pip honours its usual variables (PIP_CONFIG_FILE, PIP_INDEX_URL, PIP_CACHE_DIR, ...); set PIP_CONFIG_FILE=/dev/null
  to ignore a host pip.conf. MBNAS_HOME is set to <ROOT>/runtime for every step (DD-A13).

Exit codes: 0 installed; 1 a step failed (see the message and the install log); 2 usage error.

Next: bash scripts/doctor.sh --profile <profile>, then bash scripts/start.sh --profile <profile>.
EOF
}

die_usage() { echo "install.sh: $*" >&2; echo "Try: bash scripts/install.sh --help" >&2; exit 2; }
fail() { echo "install.sh: ERROR: $*" >&2; [ -n "${LOG:-}" ] && [ -f "$LOG" ] && echo "install.sh: log: $LOG" >&2; exit 1; }
say() { echo "[install] $*"; [ -n "${LOG:-}" ] && [ -f "$LOG" ] && echo "[install] $*" >>"$LOG"; return 0; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die_usage "option $1 needs a value"; }

PROFILE=""
PYTHON_ARG=""
OFFLINE=0
WHEELHOUSE=""
REQ_ARG=""
WITH_WEB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --python) need_value "$@"; PYTHON_ARG="$2"; shift 2 ;;
    --python=*) PYTHON_ARG="${1#*=}"; shift ;;
    --offline) OFFLINE=1; shift ;;
    --wheelhouse) need_value "$@"; WHEELHOUSE="$2"; shift 2 ;;
    --wheelhouse=*) WHEELHOUSE="${1#*=}"; shift ;;
    --requirements) need_value "$@"; REQ_ARG="$2"; shift 2 ;;
    --requirements=*) REQ_ARG="${1#*=}"; shift ;;
    --web) WITH_WEB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option: $1" ;;
  esac
done

case "$PROFILE" in
  owner|public) ;;
  "") die_usage "--profile owner|public is required" ;;
  *) die_usage "--profile must be owner or public, got '$PROFILE'" ;;
esac
if [ -n "$WHEELHOUSE" ] && [ "$OFFLINE" -ne 1 ]; then
  die_usage "--wheelhouse is only used with --offline (online installs use the indexes of the requirements file)"
fi

VENV="$ROOT/runtime/venv"
VENV_PY="$VENV/bin/python"
export MBNAS_HOME="$ROOT/runtime"
export PYTHONNOUSERSITE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

mkdir -p "$ROOT/runtime/logs" || fail "cannot create $ROOT/runtime/logs"
LOG="$ROOT/runtime/logs/install-$(date -u +%Y%m%dT%H%M%SZ).log"
: >"$LOG" || fail "cannot write $LOG"
say "profile=$PROFILE root=$ROOT offline=$OFFLINE log=$LOG"

# ---- 1. package metadata + requirements -------------------------------------------------------------------------
if [ -n "$REQ_ARG" ]; then
  case "$REQ_ARG" in /*) REQ="$REQ_ARG" ;; *) REQ="$ROOT/$REQ_ARG" ;; esac
else
  REQ="$ROOT/requirements-$PROFILE.txt"
fi
[ -f "$REQ" ] || fail "requirements file not found: $REQ"
REQ_DIR="$(cd "$(dirname "$REQ")" && pwd)"
REQ="$REQ_DIR/$(basename "$REQ")"
say "requirements: $REQ"

if [ "$OFFLINE" -eq 1 ]; then
  if [ -z "$WHEELHOUSE" ]; then
    for cand in "$ROOT/wheelhouse" "$ROOT/build/wheelhouse"; do
      if [ -d "$cand" ]; then WHEELHOUSE="$cand"; break; fi
    done
    [ -n "$WHEELHOUSE" ] || fail "--offline needs a wheelhouse: none at $ROOT/wheelhouse or $ROOT/build/wheelhouse (use --wheelhouse <dir>)"
  fi
  [ -d "$WHEELHOUSE" ] || fail "wheelhouse is not a directory: $WHEELHOUSE"
  WHEELHOUSE="$(cd "$WHEELHOUSE" && pwd)"
  compgen -G "$WHEELHOUSE/*.whl" >/dev/null || fail "no *.whl files in the wheelhouse $WHEELHOUSE"
  say "wheelhouse: $WHEELHOUSE"
fi

# ---- 2. interpreter + venv ----------------------------------------------------------------------------------------
py_version() { "$1" -I -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null; }

if [ -n "$PYTHON_ARG" ]; then
  BASE_PY="$PYTHON_ARG"
  if [ "${BASE_PY#*/}" = "$BASE_PY" ]; then
    BASE_PY="$(command -v -- "$PYTHON_ARG" 2>/dev/null)" || fail "--python $PYTHON_ARG is not on PATH"
  fi
else
  BASE_PY=""
  for cand in python3.10 python3; do
    p="$(command -v -- "$cand" 2>/dev/null)" || continue
    case "$(py_version "$p")" in 3.10.*) BASE_PY="$p"; break ;; esac
  done
  [ -n "$BASE_PY" ] || fail "no Python 3.10 found (python3.10 / python3 on PATH); pass --python <path to python3.10>"
fi
[ -x "$BASE_PY" ] || fail "interpreter is not executable: $BASE_PY"
BASE_VER="$(py_version "$BASE_PY")"
case "$BASE_VER" in
  3.10.*) ;;
  *) fail "Python 3.10 is required (DD-A7; the pinned wheels are cp310), $BASE_PY is '${BASE_VER:-unknown}'" ;;
esac
say "interpreter: $BASE_PY ($BASE_VER)"

# BUILD_INFO.json / PACKAGE_PROFILE.json (stdlib only, run with the base interpreter)
"$BASE_PY" -I -B - "$ROOT" "$PROFILE" <<'PY' 2>&1 | tee -a "$LOG"
import json, os, sys
root, profile = sys.argv[1], sys.argv[2]
def load(name):
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[install] ERROR: {name} is not readable JSON: {type(exc).__name__}")
        sys.exit(1)
    if not isinstance(doc, dict):
        print(f"[install] ERROR: {name} is not a JSON object")
        sys.exit(1)
    return doc
info, prof = load("BUILD_INFO.json"), load("PACKAGE_PROFILE.json")
if info is None:
    print("[install] BUILD_INFO.json: absent (source tree, not a packaged ZIP)")
else:
    print(f"[install] BUILD_INFO.json: version={info.get('version', '?')} build_id={info.get('build_id', '?')}")
package_profile = None
for doc in (prof, info):
    if isinstance(doc, dict) and isinstance(doc.get("profile"), str):
        package_profile = doc["profile"]
        break
if package_profile is not None:
    print(f"[install] package profile: {package_profile}")
    if profile == "owner" and package_profile in ("public", "registration"):
        print(f"[install] ERROR: this is a {package_profile} package (no mbnas-core); --profile owner needs the "
              "owner-private package")
        sys.exit(1)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "package metadata check failed"

if [ -e "$VENV" ]; then
  [ -x "$VENV_PY" ] || fail "$VENV exists but has no bin/python; remove it (rm -rf runtime/venv) and re-run"
  VENV_VER="$(py_version "$VENV_PY")"
  case "$VENV_VER" in 3.10.*) ;; *) fail "existing $VENV is Python '${VENV_VER:-broken}', 3.10 is required; remove it and re-run" ;; esac
  grep -Eq '^include-system-site-packages *= *false' "$VENV/pyvenv.cfg" 2>/dev/null \
    || fail "existing $VENV includes system site-packages (or has no pyvenv.cfg); remove it and re-run"
  # never replace editable development installs (the source-tree dev venv) with wheels
  EDITABLE="$("$VENV_PY" -I -B - <<'PY'
import json
from importlib import metadata
names = []
for dist in metadata.distributions():
    name = (dist.metadata.get("Name") or "").lower().replace("_", "-")
    if not name.startswith("mbnas-"):
        continue
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        info = {}
    if (info.get("dir_info") or {}).get("editable"):
        names.append(name)
print(" ".join(sorted(set(names))))
PY
)" || fail "cannot inspect the existing venv $VENV"
  if [ -n "$EDITABLE" ]; then
    fail "$VENV holds editable development installs ($EDITABLE); install.sh would replace them with wheels. Use a fresh copy of the tree or remove runtime/venv first."
  fi
  say "reusing venv $VENV (Python $VENV_VER)"
else
  say "creating venv $VENV"
  "$BASE_PY" -I -m venv "$VENV" >>"$LOG" 2>&1 || fail "python -m venv failed"
  grep -Eq '^include-system-site-packages *= *false' "$VENV/pyvenv.cfg" || fail "venv was created with system site-packages"
fi

# ---- 3. project wheels referenced by the requirements file --------------------------------------------------------
# A requirement line whose first token is a local *.whl path; pip resolves it from the directory it runs in (REQ_DIR).
mapfile -t WHEEL_REFS < <(sed -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' "$REQ" | awk 'NF && $1 !~ /^[-#]/ && $1 ~ /\.whl$/ && $1 !~ /:\/\// {print $1}')
EXTRA_REQS=()
if [ "$PROFILE" = "owner" ]; then
  # an owner install with a requirements file that lists the public wheels only (e.g. requirements-cpu.txt) also
  # installs the core wheel of the same version from the same directory
  core_ref="" ver="" cdir=""
  for ref in ${WHEEL_REFS[@]+"${WHEEL_REFS[@]}"}; do
    case "$(basename "$ref")" in
      mbnas_core-*) core_ref="$ref" ;;
      mbnas_contracts-*) ver="$(basename "$ref" | cut -d- -f2)"; cdir="$(dirname "$ref")" ;;
    esac
  done
  if [ -z "$core_ref" ]; then
    [ -n "$ver" ] || fail "--profile owner: $(basename "$REQ") lists neither the mbnas_core wheel nor the mbnas_contracts wheel (to derive its version)"
    core_ref="$cdir/mbnas_core-$ver-py3-none-any.whl"
    WHEEL_REFS+=("$core_ref")
    EXTRA_REQS+=("$core_ref")
    say "owner profile: adding $core_ref (not listed in $(basename "$REQ"))"
  fi
fi
for ref in ${WHEEL_REFS[@]+"${WHEEL_REFS[@]}"}; do
  case "$ref" in /*) wpath="$ref" ;; *) wpath="$REQ_DIR/$ref" ;; esac
  [ -f "$wpath" ] && continue
  wname="$(basename "$wpath")"
  pkgdir="${wname%%-*}"
  [ -f "$ROOT/$pkgdir/pyproject.toml" ] || fail "wheel $ref is missing and there is no source $ROOT/$pkgdir to build it from"
  say "building missing wheel $wname from $pkgdir/"
  mkdir -p "$ROOT/runtime" && BUILD_TMP="$(mktemp -d "$ROOT/runtime/.wheelbuild.XXXXXX")" || fail "mktemp failed"
  # copy the package source so the build leaves no build/ or *.egg-info in the tree
  (cd "$ROOT" && tar --exclude='./build' --exclude='*.egg-info' --exclude='__pycache__' -cf - -C "$pkgdir" .) \
    | (mkdir -p "$BUILD_TMP/$pkgdir" && tar -xf - -C "$BUILD_TMP/$pkgdir") || { rm -rf "$BUILD_TMP"; fail "cannot copy $pkgdir"; }
  WHEEL_OPTS=(--no-deps --no-cache-dir -w "$BUILD_TMP/out")
  if [ "$OFFLINE" -eq 1 ]; then
    WHEEL_OPTS+=(--no-index --find-links "$WHEELHOUSE")
    [ -f "$ROOT/constraints.txt" ] && WHEEL_OPTS+=(-c "$ROOT/constraints.txt")
  fi
  "$VENV_PY" -I -m pip wheel "${WHEEL_OPTS[@]}" "$BUILD_TMP/$pkgdir" >>"$LOG" 2>&1 || { rm -rf "$BUILD_TMP"; fail "pip wheel $pkgdir failed"; }
  [ -f "$BUILD_TMP/out/$wname" ] || { ls "$BUILD_TMP/out" >>"$LOG" 2>&1; rm -rf "$BUILD_TMP"; fail "building $pkgdir did not produce $wname (version mismatch with the requirements file?)"; }
  mkdir -p "$(dirname "$wpath")" && mv "$BUILD_TMP/out/$wname" "$wpath" || { rm -rf "$BUILD_TMP"; fail "cannot place $wpath"; }
  rm -rf "$BUILD_TMP"
done

# ---- 4. pip install ------------------------------------------------------------------------------------------------
PIP_OPTS=(install --disable-pip-version-check)
if [ "$OFFLINE" -eq 1 ]; then
  PIP_OPTS+=(--no-index --find-links "$WHEELHOUSE" --no-cache-dir)
  if [ -z "$REQ_ARG" ] && [ -f "$ROOT/constraints.txt" ]; then
    PIP_OPTS+=(-c "$ROOT/constraints.txt")
  fi
fi
PIP_OPTS+=(-r "$REQ" ${EXTRA_REQS[@]+"${EXTRA_REQS[@]}"})
say "pip ${PIP_OPTS[*]}  (cwd $REQ_DIR)"
(cd "$REQ_DIR" && "$VENV_PY" -I -m pip "${PIP_OPTS[@]}") 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "pip install failed"

say "checking the installed packages"
"$VENV_PY" -I -B - "$ROOT" "$PROFILE" <<'PY' 2>&1 | tee -a "$LOG"
import importlib.util, os, sys
from importlib import metadata
root, profile = os.path.realpath(sys.argv[1]), sys.argv[2]
mods = ["mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server"]
if profile == "owner":
    mods.append("mbnas_" + "core")
bad = 0
for mod in mods:
    dist = mod.replace("_", "-")
    try:
        version = metadata.version(dist)
    except metadata.PackageNotFoundError:
        print(f"[install]   {dist}: NOT INSTALLED")
        bad += 1
        continue
    spec = importlib.util.find_spec(mod)
    origin = os.path.realpath(spec.origin) if spec and spec.origin else None
    inside = origin is not None and origin.startswith(root + os.sep)
    print(f"[install]   {dist} {version}: {'inside ROOT' if inside else 'OUTSIDE ROOT'} ({origin})")
    bad += 0 if inside else 1
if profile == "public" and importlib.util.find_spec("mbnas_" + "core") is not None:
    print("[install]   note: the owner-only package mbnas-core is importable in this venv (installed by an owner install?)")
sys.exit(1 if bad else 0)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "an installed package is missing or outside ROOT"

# ---- 5. runtime directories ----------------------------------------------------------------------------------------
for d in pids logs data weights uploads locks; do
  mkdir -p "$ROOT/runtime/$d" || fail "cannot create runtime/$d"
done
say "runtime directories ready under $ROOT/runtime"

# ---- 6. configuration (secrets never leave the python process) -----------------------------------------------------
"$VENV_PY" -I -B - "$ROOT" "$PROFILE" <<'PY' 2>&1 | tee -a "$LOG"
import datetime, json, os, re, secrets, subprocess, sys
root, profile = sys.argv[1], sys.argv[2]
cfg_dir = os.path.join(root, "config", profile)
ex_dir = os.path.join(root, "config", "examples")
bin_dir = os.path.join(root, "runtime", "venv", "bin")
ROLES = {"public": ["gateway", "runner"], "owner": ["core", "gateway", "runner"]}[profile]
GENERATED = {"core": ["MBNAS_CORE_API_TOKEN", "MBNAS_CORE_ARTIFACT_SIGNING_KEY", "MBNAS_CORE_TICKET_KEY"],
             "gateway": ["MBNAS_GW_API_TOKEN", "MBNAS_GW_ARTIFACT_SIGNING_KEY"], "runner": []}
ISSUED = {"gateway": ("MBNAS_GW_CORE_TOKEN", "server"), "runner": ("MBNAS_RUNNER_TOKEN", "runner")}
LINE = re.compile(r"^(\s*)(export\s+)?(#\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
PLACEHOLDER = re.compile(r"(?i)^(<.*>|\{\{.*\}\}|\$\{.*\}|__.*__|x{3,}|\*{3,}|todo|tbd|none|null|"
                         r".*change[-_ ]?me.*|.*replace[-_ ]?me.*|.*placeholder.*|.*generated?[-_ ](at|by|on|during)[-_ ]install.*)$")
STAMP = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
failed = []

def say(msg):
    print(f"[install] {msg}", flush=True)

def value_of(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()

def parse(lines):
    out = []  # (index, key, value, commented)
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m:
            out.append((i, m.group(4), value_of(m.group(5)), bool(m.group(3))))
    return out

def is_set(lines, key):
    return any(k == key and not c and v and not PLACEHOLDER.match(v) for _, k, v, c in parse(lines))

def put(lines, key, value, note):
    """Set key=value: replace the first active line, else the first commented line, else append."""
    entries = [e for e in parse(lines) if e[1] == key]
    active = [e for e in entries if not e[3]]
    target = (active or entries or [None])[0]
    if target is None:
        lines.append(f"# {note} ({STAMP})")
        lines.append(f"{key}={value}")
    else:
        lines[target[0]] = f"{key}={value}"
        for e in active[1:]:  # a later active duplicate would win in dotenv: comment it out
            lines[e[0]] = "# " + lines[e[0]]

def comment_out(lines, index, why):
    lines[index] = f"# {lines[index].strip()}    # {why}"

def write_private(path, lines):
    tmp = path + ".tmp-install"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def issue(kind, core_env, runner_id=None):
    """Create a principal in the local core DB; returns (principal_id, token) - the token stays in this process."""
    exe = os.path.join(bin_dir, "mbnas-core-principal")
    if not os.path.isfile(exe):
        return None, "mbnas-core-principal is not installed"
    argv = [exe, "--env-file", core_env, "create", "--kind", kind]
    if runner_id:
        argv += ["--runner-id", runner_id]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, type(exc).__name__
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {proc.stderr.strip()[-300:]}"
    try:
        doc = json.loads(proc.stdout)
        return doc["principal"]["id"], doc["token"]
    except (ValueError, KeyError, TypeError):
        return None, "unexpected mbnas-core-principal output"

def runner_id_of(lines):
    """(runner id, problem) the runner will use with these runner.env lines: MBNAS_RUNNER_RUNNER_ID when set, else the
    runner's default (mbnas_runner.config.persistent_runner_id: a random runner-<8 hex> kept in runtime/runner/runner_id,
    created now when absent; never derived from the host name). runner.env is written first and loaded with the
    runner's own RunnerSettings, so the principal is bound by the same rule the runner applies when it starts."""
    path = os.path.join(cfg_dir, "runner.env")
    write_private(path, lines)
    try:
        from mbnas_runner.config import RunnerSettings
        return RunnerSettings.load(path).runner_id, None
    except Exception as exc:  # noqa: BLE001 - report the type only (values may be secrets)
        return None, type(exc).__name__

os.makedirs(cfg_dir, mode=0o700, exist_ok=True)
os.chmod(cfg_dir, 0o700)
contents, created = {}, set()
for role in ROLES:
    path = os.path.join(cfg_dir, f"{role}.env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            contents[role] = fh.read().splitlines()
        say(f"config/{profile}/{role}.env exists: kept (only missing secrets are filled)")
        continue
    example = os.path.join(ex_dir, f"{role}.env.example")
    if not os.path.isfile(example):
        failed.append(f"missing config/examples/{role}.env.example")
        continue
    with open(example, encoding="utf-8") as fh:
        contents[role] = [f"# copied from config/examples/{role}.env.example by scripts/install.sh ({STAMP})"] + fh.read().splitlines()
    created.add(role)
    say(f"config/{profile}/{role}.env created from config/examples/{role}.env.example")

for role, lines in contents.items():
    for key in GENERATED.get(role, []):
        if is_set(lines, key):
            say(f"  {role}.env {key}: kept")
        else:
            put(lines, key, secrets.token_urlsafe(48), "generated by scripts/install.sh")
            say(f"  {role}.env {key}: generated")
if profile == "owner" and "runner" in created and not is_set(contents["runner"], "MBNAS_RUNNER_ALLOW_PICKLE_RESUME"):
    put(contents["runner"], "MBNAS_RUNNER_ALLOW_PICKLE_RESUME", "true",
        "owner profile: RF-DETR native resume of the owner's own checkpoints (DD-A20)")
    say("  runner.env MBNAS_RUNNER_ALLOW_PICKLE_RESUME: true (owner profile, DD-A20)")

if profile == "owner" and "core" in contents and not failed:
    write_private(os.path.join(cfg_dir, "core.env"), contents["core"])  # the principal CLI reads it
    core_env = os.path.join(cfg_dir, "core.env")
    for role in ("gateway", "runner"):
        if role not in contents:
            continue
        key, kind = ISSUED[role]
        lines = contents[role]
        if is_set(lines, key) or (role == "runner" and is_set(lines, "MBNAS_RUNNER_TOKEN_FILE")):
            say(f"  {role}.env {key}: kept")
            continue
        runner_id = None
        if role == "runner":
            runner_id, problem = runner_id_of(lines)
            if runner_id is None:
                failed.append(f"runner.env does not load ({problem}); fix it and re-run")
                continue
        pid, token = issue(kind, core_env, runner_id)
        if pid is None:
            failed.append(f"could not issue the {kind} principal for {role}.env: {token}")
            continue
        put(lines, key, token, f"{kind} principal {pid} issued by scripts/install.sh")
        say(f"  {role}.env {key}: issued {kind} principal {pid}" + (f" for runner id {runner_id}" if runner_id else ""))

for role, lines in contents.items():
    issued_key = ISSUED.get(role, (None,))[0]
    for i, key, val, commented in parse(lines):
        if commented:
            continue
        if key == issued_key and (not val or PLACEHOLDER.match(val)):
            comment_out(lines, i, "unset until the core operator issues it (mbnas-core-principal create)")
        elif not val:
            comment_out(lines, i, "empty value commented out by scripts/install.sh (unset = default)")
    if issued_key and not is_set(lines, issued_key) and not (role == "runner" and is_set(lines, "MBNAS_RUNNER_TOKEN_FILE")):
        rid = f" --runner-id {runner_id_of(lines)[0] or '<runner id>'}" if role == "runner" else ""
        say(f"  {role}.env {issued_key}: NOT SET - the core operator issues it: mbnas-core-principal create "
            f"--kind {ISSUED[role][1]}{rid}; put it in config/{profile}/{role}.env")
    write_private(os.path.join(cfg_dir, f"{role}.env"), lines)
    say(f"  config/{profile}/{role}.env written (mode 0600)")
if profile == "public":
    say("  public profile: also set MBNAS_GW_CORE_URL (gateway.env) and MBNAS_RUNNER_CORE_URL (runner.env) to the "
        "core's URL (https:// for a non-loopback core)")

for problem in failed:
    say(f"ERROR: {problem}")
sys.exit(1 if failed else 0)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "writing config/$PROFILE failed"

# ---- 7. web (optional) + summary -----------------------------------------------------------------------------------
if [ "$WITH_WEB" -eq 1 ]; then
  command -v npm >/dev/null 2>&1 || fail "--web needs npm (Node.js) on PATH"
  [ -f "$ROOT/mbnas_web/package-lock.json" ] || fail "--web: $ROOT/mbnas_web/package-lock.json missing"
  NPM_OPTS=(ci --cache "$ROOT/build/npm-cache" --no-audit --no-fund)
  [ "$OFFLINE" -eq 1 ] && NPM_OPTS+=(--offline)
  say "npm ${NPM_OPTS[*]} (in mbnas_web)"
  (cd "$ROOT/mbnas_web" && npm "${NPM_OPTS[@]}") 2>&1 | tee -a "$LOG"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "npm ci failed"
  if [ ! -f "$ROOT/mbnas_web/dist/index.html" ]; then
    say "npm run build (mbnas_web/dist missing)"
    (cd "$ROOT/mbnas_web" && npm run build) 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" -eq 0 ] || fail "npm run build failed"
  fi
fi
if [ -f "$ROOT/mbnas_web/dist/index.html" ]; then
  say "web console: mbnas_web/dist present (served by the gateway at /)"
else
  say "web console: mbnas_web/dist missing - the gateway serves the API only (install.sh --web builds it; needs Node.js)"
fi

"$VENV_PY" -I -B - "$ROOT" "$PROFILE" <<'PY' 2>&1 | tee -a "$LOG"
import platform, sys
from importlib import metadata
root, profile = sys.argv[1], sys.argv[2]
def ver(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "-"
dists = ["mbnas-contracts", "mbnas-model-runtime", "mbnas-runner", "mbnas-server"] + (["mbnas-core"] if profile == "owner" else [])
print("[install] summary")
print(f"[install]   root      {root}")
print(f"[install]   python    {sys.version.split()[0]} ({sys.executable})  platform {platform.platform()}")
print("[install]   packages  " + ", ".join(f"{d} {ver(d)}" for d in dists))
print(f"[install]   torch     {ver('torch')}  torchvision {ver('torchvision')}  ultralytics {ver('ultralytics')}  "
      f"pytest {ver('pytest')}")
roles = "gateway, runner (core is remote)" if profile == "public" else "core + owner worker, gateway, runner"
print(f"[install]   config    config/{profile}/*.env (0600)  roles: {roles}")
PY
say "done. Next: bash scripts/doctor.sh --profile $PROFILE ; bash scripts/start.sh --profile $PROFILE"
exit 0
