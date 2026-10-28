#!/usr/bin/env bash
# verify.sh - run the verification suites of an installed MBNAS Research Console profile and report
# {suite, status, counts, evidence}. Run with --help for the suite list.
set -u -o pipefail
unset CDPATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/verify.sh --profile owner|public --suite <suite>[,<suite>...] [--json <file>|-]
                              [--pytest-args "<args>"] [--allow-gpu] [-- <pytest args>...]

Runs verification suites against the tree that holds this scripts/ folder (ROOT) and its runtime/venv. Every suite
ends with one status: PASS, FAIL, NOT_APPLICABLE (the suite has nothing to check here; counted neither as pass nor as
fail and listed separately in the summary) or BLOCKED_BY_ENV (this host lacks what the suite needs). Logs and
evidence go to runtime/logs/verify/<UTC stamp>-<profile>/.

Options:
  --profile owner|public  (required) profile under test.
  --suite <list>          (required) comma-separated suites, or all (= unit,integration,relocation,package,gpu):
     unit         pytest -m "not gpu and not live and not legacy_records and not host_data" over the test suites
                  of a source tree (public: the public tests; owner: the owner and the public tests), with
                  CUDA_VISIBLE_DEVICES=''. NOT_APPLICABLE when ROOT has no tests/ folder (test suites are not
                  shipped in this distribution); BLOCKED_BY_ENV when pytest is not installed in runtime/venv. The
                  result's scope lists the owner tests as not applicable for the public profile. Without
                  ROOT/pyproject.toml (its pytest settings) the suite adds --import-mode=importlib -o timeout=600.
     integration  owner: scripts/dev/wp3_three_process_smoke.py (real core + owner worker + runner + gateway
                  processes on ephemeral ports driven through the gateway /api/v1, CPU only, ~40 s), or
                  scripts/dev/wp2_two_process_smoke.py when the three-process smoke is absent; the driver writes
                  its own smoke root and evidence below the verify evidence directory.
                  public: needs a live core - MBNAS_CORE_URL (else BLOCKED_BY_ENV); checks the core through
                  mbnas-gateway-doctor, the runner registration
                  (mbnas-runner register; skipped when this tree's runner daemon is running) and, when this tree's
                  gateway is running, GET /api/v1/health through it. Tokens come from config/public/*.env.
     relocation   the origin of every module of the installed mbnas packages, submodules included (located from a
                  working directory outside ROOT without importing them), under ROOT - relocation.json records the
                  per-module origin table (also relocation_origins.tsv), sys.path and the .pth files of the venv -
                  console scripts bound to the venv inside ROOT, no original-host path strings in the installed
                  packages / entry points / config/<profile>/*.env / scripts/*.sh, and scripts/doctor.sh re-invoked
                  from an external working directory and through a symlinked path containing spaces (its own ROOT
                  detection and quoting). No suite starts or stops a process of this tree.
     package      for an extracted package (FILE_MANIFEST.jsonl present; else NOT_APPLICABLE: a source tree):
                  every manifest entry exists with its size and sha256 (no absolute or .. path, no self entry),
                  then the leak scan scripts/dev/canary_scan.py (or the scanner named by $MBNAS_CANARY_SCAN)
                  over the tree except runtime/ (--min-files 50; with a canary manifest in the tree: --manifest +
                  --profile; owner without a manifest: --no-host-facts). Without a scanner only the manifest is
                  checked and the reason says so.
     gpu          NOT_APPLICABLE when ROOT has no tests/ folder (test suites are not shipped in this distribution);
                  else a read-only nvidia-smi query: BLOCKED_BY_ENV when there is no nvidia-smi or no idle GPU (no
                  compute process, utilization < 10 %). On an idle GPU the GPU-marked tests (pytest -m gpu) run only
                  with --allow-gpu (GPU work needs explicit approval), else BLOCKED_BY_ENV.
  --json <file>|-         write the result as JSON (`-` = stdout; the text report then goes to stderr). One suite:
                          {suite, status, counts, evidence, reason, ...}; several: {suite, status (worst), counts
                          (statuses), evidence, not_applicable (suites), results: [...]}.
  --pytest-args "<args>"  extra pytest arguments for unit/gpu, split like a shell command line (no evaluation);
                          may be repeated. When an argument names an existing path (relative to ROOT, optionally
                          with ::node), the default test directories are not added. A later -m replaces the default
                          marker expression.
  --allow-gpu             allow the gpu suite to run the GPU-marked tests on an idle GPU.
  -- <args>               everything after -- is passed to pytest verbatim (same rules as --pytest-args).
  -h, --help              show this help and exit.

Environment: MBNAS_CORE_URL (public integration: URL of the live core), MBNAS_CANARY_SCAN (package: path of an
external canary_scan.py); MBNAS_HOME is set to <ROOT>/runtime; pytest runs from ROOT (its pyproject.toml settings).

Exit codes: 0 every requested suite is PASS or NOT_APPLICABLE; 1 at least one FAIL; 2 usage error;
3 no FAIL but at least one BLOCKED_BY_ENV.
EOF
}

die_usage() { echo "verify.sh: $*" >&2; echo "Try: bash scripts/verify.sh --help" >&2; exit 2; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die_usage "option $1 needs a value"; }

PROFILE=""
SUITES=""
JSON_OUT=""
ALLOW_GPU=0
PYARGS=()
PASSTHROUGH=()
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --suite) need_value "$@"; SUITES="$2"; shift 2 ;;
    --suite=*) SUITES="${1#*=}"; shift ;;
    --json) need_value "$@"; JSON_OUT="$2"; shift 2 ;;
    --json=*) JSON_OUT="${1#*=}"; shift ;;
    --pytest-args) [ $# -ge 2 ] || die_usage "option --pytest-args needs a value"; PYARGS+=("$2"); shift 2 ;;
    --pytest-args=*) PYARGS+=("${1#*=}"); shift ;;
    --allow-gpu) ALLOW_GPU=1; shift ;;
    --) shift; PASSTHROUGH=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option: $1" ;;
  esac
done
case "$PROFILE" in
  owner|public) ;;
  "") die_usage "--profile owner|public is required" ;;
  *) die_usage "--profile must be owner or public, got '$PROFILE'" ;;
esac
[ -n "$SUITES" ] || die_usage "--suite is required (unit, integration, relocation, package, gpu or all)"
IFS=',' read -r -a SUITE_LIST <<<"$SUITES"
for s in "${SUITE_LIST[@]}"; do
  case "$s" in unit|integration|relocation|package|gpu|all) ;; *) die_usage "unknown suite '$s'" ;; esac
done
if [ -n "$JSON_OUT" ] && [ "$JSON_OUT" != "-" ]; then
  case "$JSON_OUT" in /*) ;; *) JSON_OUT="$PWD/$JSON_OUT" ;; esac
fi

VENV_PY="$ROOT/runtime/venv/bin/python"
if [ -x "$VENV_PY" ]; then
  RUN_PY="$VENV_PY"
else
  RUN_PY="$(command -v python3 2>/dev/null || true)"
  [ -n "$RUN_PY" ] || { echo "verify.sh: no runtime/venv and no python3 on PATH" >&2; exit 1; }
fi
export MBNAS_HOME="$ROOT/runtime"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1

"$RUN_PY" -I -B - "$ROOT" "$PROFILE" "$SUITES" "$JSON_OUT" "$ALLOW_GPU" "${#PYARGS[@]}" \
  ${PYARGS[@]+"${PYARGS[@]}"} ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} <<'PY'
import hashlib, json, os, re, shlex, shutil, subprocess, sys, tempfile, time
import xml.etree.ElementTree as ET

root, profile, suites_arg, json_out, allow_gpu = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] == "1"
n_strings = int(sys.argv[6])
pytest_extra = [a for s in sys.argv[7:7 + n_strings] for a in shlex.split(s)] + sys.argv[7 + n_strings:]
root_real = os.path.realpath(root)
venv_py = os.path.join(root, "runtime", "venv", "bin", "python")
venv_bin = os.path.dirname(venv_py)
have_venv = os.access(venv_py, os.X_OK)
out = sys.stderr if json_out == "-" else sys.stdout
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
EVID = os.path.join(root, "runtime", "logs", "verify", f"{STAMP}-{profile}")
os.makedirs(EVID, exist_ok=True)
MARKERS = "not gpu and not live and not legacy_records and not host_data"
ORDER = ["unit", "integration", "relocation", "package", "gpu"]
NEEDLES = ("/etri" + "_nas/", "/root" + "/detection")  # composed: this file must not contain them
MODS = ["mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server"] + (["mbnas_" + "core"] if profile == "owner" else [])
ENTRY = ["mbnas-gateway", "mbnas-gateway-doctor", "mbnas-runner"] + (
    ["mbnas-core-api", "mbnas-core-owner-worker", "mbnas-core-doctor", "mbnas-core-principal"] if profile == "owner" else [])
OWNER_TESTS, PUBLIC_TESTS = os.path.join("tests", "owner"), os.path.join("tests", "public")
TEST_DIRS = ([OWNER_TESTS] if profile == "owner" else []) + [PUBLIC_TESTS]
HAVE_TESTS = os.path.isdir(os.path.join(root, "tests"))  # a distribution ships no test suites
NOT_SHIPPED = "test suites are not shipped in this distribution"
requested = []
for s in suites_arg.split(","):
    for name in (ORDER if s == "all" else [s]):
        if name not in requested:
            requested.append(name)


def say(msg):
    print(f"[verify] {msg}", file=out, flush=True)


def rel(path):
    return os.path.relpath(path, root)


def result(suite, status, counts=None, evidence=None, reason="", **extra):
    return {"suite": suite, "status": status, "counts": counts or {}, "evidence": [rel(e) for e in (evidence or [])],
            "reason": reason, **extra}


def base_env(**over):
    env = dict(os.environ)
    env.update(MBNAS_HOME=os.path.join(root, "runtime"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    env.update(over)
    return env


def run_logged(cmd, log, cwd, env, timeout):
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"# cwd: {cwd}\n# argv: {json.dumps(cmd)}\n")
        fh.flush()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\n# TIMEOUT after {timeout} s\n")
            return 124


def tail(path, n=15):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except OSError:
        return []


def under_root(path):
    return bool(path) and os.path.realpath(path).startswith(root_real + os.sep)


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


# ---- pytest-based suites --------------------------------------------------------------------------------------------
def names_a_path(arg):
    if arg.startswith("-"):
        return False
    return os.path.exists(os.path.join(root, arg.split("::", 1)[0]))


def pytest_counts(junit, log):
    counts = {}
    try:
        tree = ET.parse(junit)
        suites = [tree.getroot()] if tree.getroot().tag == "testsuite" else list(tree.getroot().iter("testsuite"))
        tot = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
        counts = {"tests": tot["tests"], "passed": tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"],
                  "failed": tot["failures"], "errors": tot["errors"], "skipped": tot["skipped"]}
    except (OSError, ET.ParseError, ValueError):
        pass
    for line in reversed(tail(log, 40)):
        if re.search(r"\b(passed|failed|error|errors|no tests ran|deselected)\b.* in [0-9.]+s", line):
            counts["summary"] = line.strip("= ").strip()
            m = re.search(r"(\d+) deselected", line)
            if m:
                counts["deselected"] = int(m.group(1))
            break
    return counts


def run_pytest(suite, markers, extra_env):
    log, junit = os.path.join(EVID, f"{suite}.log"), os.path.join(EVID, f"{suite}.junit.xml")
    if not have_venv:
        return None, result(suite, "BLOCKED_BY_ENV", reason="runtime/venv missing (bash scripts/install.sh)")
    if subprocess.run([venv_py, "-I", "-c", "import pytest"], capture_output=True).returncode != 0:
        return None, result(suite, "BLOCKED_BY_ENV", reason="pytest is not installed in runtime/venv")
    missing = [d for d in TEST_DIRS if not os.path.isdir(os.path.join(root, d))]
    explicit = any(names_a_path(a) for a in pytest_extra)
    if missing and not explicit:
        return None, result(suite, "FAIL", reason=f"test directories missing from this tree: {', '.join(missing)}")
    cmd = [venv_py, "-B", "-m", "pytest", "-m", markers, "-p", "no:cacheprovider", f"--junitxml={junit}"]
    if not os.path.isfile(os.path.join(root, "pyproject.toml")):  # a package without the workspace pytest settings
        cmd += ["--import-mode=importlib", "-o", "timeout=600"]
    cmd += ([] if explicit else TEST_DIRS) + pytest_extra
    t0 = time.monotonic()
    rc = run_logged(cmd, log, root, base_env(**extra_env), timeout=4 * 3600)
    counts = pytest_counts(junit, log)
    info = {"command": cmd, "exit_code": rc, "duration_s": round(time.monotonic() - t0, 1),
            "scope": {"test_dirs": [] if explicit else TEST_DIRS, "markers": markers,
                      "not_applicable": [] if profile == "owner" else [f"{OWNER_TESTS}: owner-only tests (public profile)"]}}
    if rc == 0:
        return rc, result(suite, "PASS", counts, [log, junit], "", **info)
    reason = "no tests collected (exit 5)" if rc == 5 else f"pytest exit {rc}"
    for line in tail(log, 12):
        say(f"  | {line}")
    return rc, result(suite, "FAIL", counts, [log, junit], reason, **info)


def suite_unit():
    if not HAVE_TESTS:
        return result("unit", "NOT_APPLICABLE", reason=NOT_SHIPPED)
    return run_pytest("unit", MARKERS, {"CUDA_VISIBLE_DEVICES": ""})[1]


# ---- integration ------------------------------------------------------------------------------------------------------
def proc_ours(name, marker):
    try:
        with open(os.path.join(root, "runtime", "pids", f"{name}.pid"), encoding="ascii") as fh:
            pid = int(fh.read().split()[0])
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            if fh.read().rsplit(")", 1)[1].split()[0] in ("Z", "X"):
                return None
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except (OSError, ValueError, IndexError):
        return None
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
    rooted = any(a.startswith(root + "/") or a.startswith(root_real + "/") for a in argv) or cwd == root_real
    return pid if rooted and any(os.path.basename(a) == marker for a in argv) else None


def suite_integration():
    if not have_venv:
        return result("integration", "BLOCKED_BY_ENV", reason="runtime/venv missing (bash scripts/install.sh)")
    if profile == "owner":
        # the three-process smoke (core + owner worker + runner + gateway, driven through the gateway /api/v1) is
        # preferred; the two-process smoke polls core /api/v1/health, which the current core no longer exposes by default
        drivers = [os.path.join(root, "scripts", "dev", n) for n in ("wp3_three_process_smoke.py", "wp2_two_process_smoke.py")]
        driver = next((d for d in drivers if os.path.isfile(d)), None)
        if driver is None:
            return result("integration", "FAIL", reason="owner integration driver missing: scripts/dev/"
                          "wp3_three_process_smoke.py or scripts/dev/wp2_two_process_smoke.py")
        log, evidence = os.path.join(EVID, "integration.log"), os.path.join(EVID, "integration_smoke.txt")
        cmd = [venv_py, "-B", driver, "--root", os.path.join(EVID, "integration_root"), "--evidence", evidence]
        t0 = time.monotonic()
        rc = run_logged(cmd, log, root, base_env(CUDA_VISIBLE_DEVICES="",
                                                 MBNAS_TEST_ROOT=os.path.join(root, "runtime", "test_root")), 1800)
        steps = []
        for line in tail(evidence, 100000):
            line = line.strip()
            if line.startswith("{") and '"step"' in line:
                try:
                    steps.append(json.loads(line))
                except ValueError:
                    pass
        counts = {"steps": len(steps), "passed": sum(1 for s in steps if s.get("ok")),
                  "failed": sum(1 for s in steps if not s.get("ok"))}
        info = {"command": cmd, "exit_code": rc, "duration_s": round(time.monotonic() - t0, 1), "driver": rel(driver)}
        if rc == 0 and counts["failed"] == 0 and counts["steps"] > 0:
            return result("integration", "PASS", counts, [log, evidence], "", **info)
        return result("integration", "FAIL", counts, [log, evidence], f"smoke exit {rc}", **info)
    core_url = os.environ.get("MBNAS_CORE_URL", "").strip()
    if not core_url:
        return result("integration", "BLOCKED_BY_ENV", reason="the public integration suite needs a live core: set "
                      "MBNAS_CORE_URL (tokens: MBNAS_GW_CORE_TOKEN in gateway.env, MBNAS_RUNNER_TOKEN in runner.env)")
    env_dir = os.path.join(root, "config", "public")
    checks, log = [], os.path.join(EVID, "integration_public.json")
    gw_doc = os.path.join(venv_bin, "mbnas-gateway-doctor")
    try:
        r = subprocess.run([gw_doc, "--env-file", os.path.join(env_dir, "gateway.env")], capture_output=True, text=True,
                           timeout=120, cwd=root, env=base_env(MBNAS_GW_CORE_URL=core_url))
        core = next((c for c in json.loads(r.stdout).get("checks", []) if c.get("name") == "core"), {})
        checks.append({"check": "core via gateway service token", "ok": bool(core.get("ok")), "detail": core.get("detail")})
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        checks.append({"check": "core via gateway service token", "ok": False, "detail": type(exc).__name__})
    if proc_ours("runner", "mbnas-runner"):
        checks.append({"check": "runner registration", "ok": True, "detail": "this tree's runner daemon is running "
                       "(registered at start); register step skipped"})
    else:
        try:
            r = subprocess.run([os.path.join(venv_bin, "mbnas-runner"), "register", "--env-file",
                                os.path.join(env_dir, "runner.env")], capture_output=True, text=True, timeout=180,
                               cwd=root, env=base_env(MBNAS_RUNNER_CORE_URL=core_url))
            checks.append({"check": "runner registration", "ok": r.returncode == 0,
                           "detail": f"mbnas-runner register exit {r.returncode}" + (f": {r.stderr.strip()[-300:]}" if r.returncode else "")})
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks.append({"check": "runner registration", "ok": False, "detail": type(exc).__name__})
    if proc_ours("gateway", "mbnas-gateway"):
        try:
            import urllib.error, urllib.request
            from mbnas_server.config import GatewaySettings
            s = GatewaySettings.load(os.path.join(env_dir, "gateway.env"))
            host = "127.0.0.1" if s.host in ("0.0.0.0", "", "::") else s.host
            req = urllib.request.Request(f"http://{host}:{s.port}/api/v1/health")
            req.add_header("Authorization", "Bearer " + s.api_token.get_secret_value())
            with urllib.request.urlopen(req, timeout=30) as resp:
                doc = json.loads(resp.read())
            core = doc.get("core")
            reach = core.get("reachable") if isinstance(core, dict) else False
            checks.append({"check": "gateway /api/v1/health", "ok": reach is True,
                           "detail": f"status={doc.get('status')} core={'reachable' if reach is True else core}"})
        except Exception as exc:  # noqa: BLE001 - never print token values
            checks.append({"check": "gateway /api/v1/health", "ok": False, "detail": type(exc).__name__})
    else:
        checks.append({"check": "gateway /api/v1/health", "ok": None, "detail": "skipped: this tree's gateway is not running"})
    with open(log, "w", encoding="utf-8") as fh:
        json.dump({"core_url": core_url, "checks": checks}, fh, indent=1)
    counts = {"checks": len(checks), "passed": sum(1 for c in checks if c["ok"] is True),
              "failed": sum(1 for c in checks if c["ok"] is False), "skipped": sum(1 for c in checks if c["ok"] is None)}
    for c in checks:
        say(f"  integration: {c['check']}: {'skipped' if c['ok'] is None else ('ok' if c['ok'] else 'FAILED')} - {c['detail']}")
    status = "PASS" if counts["failed"] == 0 and counts["passed"] >= 2 else "FAIL"
    return result("integration", status, counts, [log], "" if status == "PASS" else "a live check failed",
                  core_url=core_url)


# ---- relocation ---------------------------------------------------------------------------------------------------------
ORIGIN_CODE = r"""
import importlib.util, json, os, pkgutil, site, sys, sysconfig
modules = {}
for top in sys.argv[1:]:
    spec = importlib.util.find_spec(top)
    modules[top] = os.path.realpath(spec.origin) if spec and spec.origin else None
    stack = [(top, list(spec.submodule_search_locations or []))] if spec else []
    while stack:  # every submodule, located on disk by its path-entry finder: nothing is imported, no package code runs
        name, paths = stack.pop()
        for info in pkgutil.iter_modules(paths, prefix=name + "."):
            finder = info.module_finder
            sub = finder.find_spec(info.name) if hasattr(finder, "find_spec") else None
            modules[info.name] = os.path.realpath(sub.origin) if sub is not None and sub.origin else None
            if info.ispkg and sub is not None and sub.submodule_search_locations:
                stack.append((info.name, list(sub.submodule_search_locations)))
site_dirs = []
for d in list(getattr(site, "getsitepackages", lambda: [])()) + [sysconfig.get_paths().get(k) for k in ("purelib", "platlib")]:
    if d and os.path.isdir(d) and os.path.realpath(d) not in site_dirs:
        site_dirs.append(os.path.realpath(d))
pth = []
for d in site_dirs:
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".pth"):
            continue
        lines = []
        try:
            with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as fh:
                raw = fh.read().splitlines()
        except OSError as exc:
            pth.append({"file": os.path.join(d, fn), "error": type(exc).__name__, "lines": []})
            continue
        for line in raw:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith(("import ", "import\t")):  # executed by site.py at start-up; recorded, not a search path
                lines.append({"kind": "import", "text": s[:200]})
            else:  # a search-path line, relative to the site directory
                p = os.path.realpath(os.path.join(d, s))
                lines.append({"kind": "path", "path": p, "exists": os.path.exists(p)})
        pth.append({"file": os.path.join(d, fn), "lines": lines})
print(json.dumps({"modules": modules, "prefix": os.path.realpath(sys.prefix),
                  "base_prefix": os.path.realpath(sys.base_prefix), "site_dirs": site_dirs,
                  "sys_path": [os.path.realpath(p) if p else p for p in sys.path], "pth": pth}))
"""


def scan_files_for_needles():
    files = []
    code = ("import importlib.util, json, sys\nfrom importlib import metadata\nout = []\n"
            "for mod in sys.argv[1:]:\n"
            "    spec = importlib.util.find_spec(mod)\n"
            "    out += list(spec.submodule_search_locations or []) if spec else []\n"
            "    try:\n"
            "        out.append(str(getattr(metadata.distribution(mod.replace('_', '-')), '_path', '')))\n"
            "    except Exception:\n"
            "        pass\n"
            "print(json.dumps(out))\n")
    r = subprocess.run([venv_py, "-I", "-B", "-c", code, *MODS], capture_output=True, text=True, cwd="/")
    bases = json.loads(r.stdout) if r.returncode == 0 else []
    for base in [b for b in bases if b and os.path.isdir(b)]:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            files += [os.path.join(dirpath, f) for f in filenames]
    files += [os.path.join(venv_bin, n) for n in ENTRY if os.path.isfile(os.path.join(venv_bin, n))]
    env_dir = os.path.join(root, "config", profile)
    if os.path.isdir(env_dir):
        files += [os.path.join(env_dir, f) for f in os.listdir(env_dir) if f.endswith(".env")]
    sdir = os.path.join(root, "scripts")
    if os.path.isdir(sdir):
        files += [os.path.join(sdir, f) for f in os.listdir(sdir) if f.endswith(".sh")]
    hits = []
    for path in files:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if any(n.encode() in data for n in NEEDLES):
            hits.append(rel(path))
    return len(files), hits


def doctor_json(script_root, cwd, tag):
    out_json = os.path.join(EVID, f"relocation_doctor_{tag}.json")
    log = os.path.join(EVID, f"relocation_doctor_{tag}.log")
    rc = run_logged(["bash", os.path.join(script_root, "scripts", "doctor.sh"), "--profile", profile, "--json", out_json],
                    log, cwd, base_env(), 600)
    try:
        with open(out_json, encoding="utf-8") as fh:
            return rc, json.load(fh), [log, out_json]
    except (OSError, ValueError):
        return rc, None, [log]


RELOC_CHECKS = ("interpreter", "entry_points", "path_strings")
PY_ENV_KEYS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE")


def where(path, base_prefix=None):
    """ROOT (inside this tree), BASE (the base interpreter's own files, e.g. its stdlib), OUTSIDE, or MISSING."""
    if not path:
        return "MISSING"
    if under_root(path):
        return "ROOT"
    if base_prefix and os.path.realpath(path).startswith(os.path.realpath(base_prefix) + os.sep):
        return "BASE"
    return "OUTSIDE"


def shown(path):
    """A path relative to ROOT when it lies inside it, else the absolute path (flagged by where())."""
    if path and under_root(path):
        return os.path.relpath(os.path.realpath(path), root_real)
    return path


def doctor_ok(doc, expect_root):
    if not doc:
        return False, "doctor wrote no JSON report"
    skip = "package:mbnas-core" if profile == "public" else None  # public: the absence check may WARN in a shared venv
    bad = [c["name"] for c in doc.get("checks", [])
           if (c["name"] in RELOC_CHECKS or (c["name"].startswith("package:") and c["name"] != skip))
           and c["status"] != "PASS"]
    if doc.get("root") != expect_root:
        return False, f"doctor located ROOT as {doc.get('root')!r}, expected {expect_root!r}"
    return (not bad), ("origin checks PASS" if not bad else "not PASS: " + ", ".join(bad))


def suite_relocation():
    if not have_venv:
        return result("relocation", "BLOCKED_BY_ENV", reason="runtime/venv missing (bash scripts/install.sh)")
    checks, evidence = [], []
    probe, table, per_pkg = {}, [], {}
    ext = tempfile.mkdtemp(prefix="mbnas-verify-")
    try:
        r = subprocess.run([venv_py, "-I", "-B", "-c", ORIGIN_CODE, *MODS], capture_output=True, text=True, cwd=ext,
                           env=base_env())
        try:
            probe = json.loads(r.stdout)
        except ValueError:
            probe = {}
        modules = probe.get("modules") or {}
        # one row per module of every package (submodules included), origin relative to ROOT or flagged OUTSIDE
        table = [{"module": m, "location": where(o), "origin": shown(o)} for m, o in sorted(modules.items())]
        per_pkg = {m: sum(1 for k in modules if k == m or k.startswith(m + ".")) for m in MODS}
        bad = [row["module"] for row in table if row["location"] != "ROOT"] + [m for m in MODS if m not in modules]
        checks.append({"check": "module __file__ under ROOT (cwd outside ROOT)", "ok": bool(modules) and not bad,
                       "detail": (f"{len(modules)} modules ({', '.join(f'{m} {n}' for m, n in per_pkg.items())}), "
                                  f"all under {root_real}") if modules and not bad
                       else f"outside/missing ({len(bad)}): {bad[:10]}"})
        checks.append({"check": "sys.prefix under ROOT", "ok": under_root(probe.get("prefix")),
                       "detail": str(probe.get("prefix"))})
        bound = []
        for name in ENTRY:
            path = os.path.join(venv_bin, name)
            try:
                with open(path, "rb") as fh:
                    head = fh.read(600).decode("utf-8", "replace")
            except OSError:
                bound.append(f"{name}: missing")
                continue
            interp = script_interpreter(head)
            if not interp or not under_root(os.path.dirname(interp)):  # bin/python links to the base
                bound.append(f"{name}: {interp}")
        checks.append({"check": "console scripts bound to runtime/venv inside ROOT", "ok": not bound,
                       "detail": "ok" if not bound else "; ".join(bound)})
        scanned, hits = scan_files_for_needles()
        checks.append({"check": "no original-host path strings", "ok": not hits and scanned > 0,
                       "detail": f"{scanned} files scanned" + (f"; hits: {hits[:10]}" if hits else "")})
        rc, doc, ev = doctor_json(root, ext, "external_cwd")
        evidence += ev
        ok, detail = doctor_ok(doc, root)
        checks.append({"check": "doctor.sh from an external working directory", "ok": ok and rc == 0,
                       "detail": f"exit {rc}; {detail}"})
        spaced_parent = os.path.join(ext, "path with spaces")
        os.mkdir(spaced_parent)
        spaced = os.path.join(spaced_parent, "mbnas app root")
        os.symlink(root_real, spaced)
        try:
            rc, doc, ev = doctor_json(spaced, ext, "spaced_path")
            evidence += ev
            ok, detail = doctor_ok(doc, spaced)
            checks.append({"check": "doctor.sh through a path with spaces", "ok": ok and rc == 0,
                           "detail": f"exit {rc}; {detail}"})
            # side-effect free only: verify never starts or stops a process of the tree
            log = os.path.join(EVID, "relocation_help_spaced.log")
            rcs = [run_logged(["bash", os.path.join(spaced, "scripts", f"{name}.sh"), "--help"], log, ext, base_env(), 60)
                   for name in ("start", "stop", "install")]
            evidence.append(log)
            checks.append({"check": "start/stop/install.sh --help through a path with spaces", "ok": rcs == [0, 0, 0],
                           "detail": f"exit codes {rcs}"})
        finally:
            os.unlink(spaced)
    finally:
        try:
            if os.path.isdir(os.path.join(ext, "path with spaces")):
                os.rmdir(os.path.join(ext, "path with spaces"))
            os.rmdir(ext)
        except OSError:
            pass
    if " " in root:
        checks.append({"check": "ROOT itself contains spaces", "ok": True, "detail": root})
    base_prefix = probe.get("base_prefix") or sys.base_prefix
    pth_files = [{"file": shown(p.get("file")), "location": where(p.get("file")),
                  **({"error": p["error"]} if p.get("error") else {}),
                  "lines": [{"kind": "path", "path": shown(x.get("path")), "location": where(x.get("path"), base_prefix),
                             "exists": x.get("exists")} if x.get("kind") == "path" else x for x in p.get("lines", [])]}
                 for p in probe.get("pth", [])]
    report = os.path.join(EVID, "relocation.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"root": root, "root_real": root_real, "checks": checks,
                   "module_counts": per_pkg, "module_origins": table,
                   "sys_path": [{"path": shown(p), "location": where(p, base_prefix)} for p in probe.get("sys_path", []) if p],
                   "site_dirs": [shown(d) for d in probe.get("site_dirs", [])], "pth_files": pth_files,
                   # informational, key names only: the probe above runs with -I, and start.sh removes them for the roles
                   "caller_env_python_vars": [k for k in PY_ENV_KEYS if os.environ.get(k)]}, fh, indent=1)
    tsv = os.path.join(EVID, "relocation_origins.tsv")
    with open(tsv, "w", encoding="utf-8") as fh:
        fh.write("module\tlocation\torigin\n")
        fh.writelines(f"{row['module']}\t{row['location']}\t{row['origin']}\n" for row in table)
    evidence.append(tsv)
    for c in checks:
        say(f"  relocation: {c['check']}: {'ok' if c['ok'] else 'FAILED'} - {c['detail']}")
    say(f"  relocation: per-module origins in {rel(report)} and {rel(tsv)}; .pth files: "
        + (", ".join(f"{os.path.basename(p['file'] or '?')} ({len(p['lines'])} line(s))" for p in pth_files) or "none"))
    counts = {"checks": len(checks), "passed": sum(1 for c in checks if c["ok"]),
              "failed": sum(1 for c in checks if not c["ok"])}
    return result("relocation", "PASS" if counts["failed"] == 0 else "FAIL", counts, [report] + evidence,
                  "" if counts["failed"] == 0 else "a relocation check failed")


# ---- package ------------------------------------------------------------------------------------------------------------
def check_manifest(path):
    counts = {"entries": 0, "ok": 0, "missing": 0, "mismatch": 0, "invalid": 0}
    problems = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            counts["entries"] += 1
            try:
                entry = json.loads(line)
                relp = entry.get("path") or entry.get("relpath") or entry.get("file")
                size = entry.get("size", entry.get("bytes"))
                sha = entry.get("sha256")
            except (ValueError, AttributeError):
                counts["invalid"] += 1
                problems.append(f"line {n}: not a JSON object")
                continue
            if not isinstance(relp, str) or not relp or relp.startswith("/") or "\\" in relp or ".." in relp.split("/"):
                counts["invalid"] += 1
                problems.append(f"line {n}: bad path {relp!r}")
                continue
            if relp in ("FILE_MANIFEST.jsonl",):
                counts["invalid"] += 1
                problems.append(f"line {n}: the manifest lists itself")
                continue
            full = os.path.join(root, relp)
            if os.path.islink(full) or not os.path.isfile(full):
                counts["missing"] += 1
                problems.append(f"missing or not a regular file: {relp}")
                continue
            digest = hashlib.sha256()
            with open(full, "rb") as fb:
                for chunk in iter(lambda: fb.read(1 << 20), b""):
                    digest.update(chunk)
            if (size is not None and int(size) != os.path.getsize(full)) or (sha and sha != digest.hexdigest()):
                counts["mismatch"] += 1
                problems.append(f"size/sha256 mismatch: {relp}")
                continue
            counts["ok"] += 1
    return counts, problems


def suite_package():
    manifest = os.path.join(root, "FILE_MANIFEST.jsonl")
    scanner = os.environ.get("MBNAS_CANARY_SCAN") or os.path.join(root, "scripts", "dev", "canary_scan.py")
    if not os.path.isfile(manifest):
        return result("package", "NOT_APPLICABLE", reason="no FILE_MANIFEST.jsonl: a source tree, not an extracted "
                      "package (run this suite in the unpacked ZIP)")
    counts, evidence, failures, notes = {}, [], [], []
    mcounts, problems = check_manifest(manifest)
    counts["manifest"] = mcounts
    report = os.path.join(EVID, "package_manifest.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"counts": mcounts, "problems": problems}, fh, indent=1)
    evidence.append(report)
    if mcounts["entries"] == 0 or problems:
        failures.append(f"manifest: {len(problems)} problem(s), first: {problems[:3]}")
    if os.path.isfile(scanner):
        log, scan_json = os.path.join(EVID, "package_canary.log"), os.path.join(EVID, "package_canary.json")
        targets = [os.path.join(root, e) for e in sorted(os.listdir(root)) if e != "runtime"]  # runtime/ = this host's install
        canary_manifest = os.path.join(root, "docs", "internal", "canary", "CANARY_MANIFEST.json")
        cmd = [venv_py if have_venv else sys.executable, "-B", scanner, *targets, "--json", scan_json, "--min-files", "50"]
        if os.path.isfile(canary_manifest):
            cmd += ["--manifest", canary_manifest, "--profile", profile]
        elif profile == "owner":
            cmd += ["--no-host-facts"]  # host facts are informational inside the private owner bundle
        rc = run_logged(cmd, log, root, base_env(), 3600)
        evidence += [log, scan_json]
        counts["canary_scan"] = {"exit_code": rc, "scanner": scanner}
        try:
            with open(scan_json, encoding="utf-8") as fh:
                doc = json.load(fh)
            for key, value in doc.items():
                if isinstance(value, (int, str, bool)) and key not in ("schema",):
                    counts["canary_scan"][key] = value
                elif isinstance(value, list) and key in ("hits", "failures"):
                    counts["canary_scan"][key] = len(value)
        except (OSError, ValueError):
            pass
        if rc != 0:
            failures.append(f"canary_scan exit {rc} (see {rel(log)})")
    else:
        notes.append("canary scan not run: no scripts/dev/canary_scan.py in this tree (set MBNAS_CANARY_SCAN=<path "
                     "to canary_scan.py> to scan with an external scanner)")
    status = "FAIL" if failures else "PASS"
    return result("package", status, counts, evidence, "; ".join(failures + notes))


# ---- gpu ------------------------------------------------------------------------------------------------------------------
def suite_gpu():
    if not HAVE_TESTS:
        return result("gpu", "NOT_APPLICABLE", reason=NOT_SHIPPED)
    smi = shutil.which("nvidia-smi")
    if not smi:
        return result("gpu", "BLOCKED_BY_ENV", reason="nvidia-smi not found (no NVIDIA driver on this host)")
    try:
        g = subprocess.run([smi, "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=60)
        a = subprocess.run([smi, "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result("gpu", "BLOCKED_BY_ENV", reason=f"nvidia-smi failed: {type(exc).__name__}")
    gpus = [[f.strip() for f in row.split(",")] for row in g.stdout.strip().splitlines() if row.strip()]
    busy_uuids = {row.split(",")[0].strip() for row in a.stdout.strip().splitlines() if row.strip()}
    if g.returncode != 0 or not gpus:
        return result("gpu", "BLOCKED_BY_ENV", reason=f"nvidia-smi exit {g.returncode}: no GPU listed")
    idle, desc = [], []
    for row in gpus:
        if len(row) < 6:
            continue
        index, uuid, name, util, used, total = row[:6]
        procs = uuid in busy_uuids
        try:
            util_ok = float(util) < 10
        except ValueError:
            util_ok = False
        if util_ok and not procs:
            idle.append(index)
        desc.append(f"GPU{index} {name} util {util}% mem {used}/{total} MiB{' compute processes' if procs else ''}")
    counts = {"gpus": len(gpus), "idle": len(idle)}
    report = os.path.join(EVID, "gpu_inventory.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"gpus": desc, "idle": idle}, fh, indent=1)
    if not idle:
        return result("gpu", "BLOCKED_BY_ENV", counts, [report], "no idle GPU: " + "; ".join(desc))
    if not allow_gpu:
        return result("gpu", "BLOCKED_BY_ENV", counts, [report], f"idle GPU {','.join(idle)} found but GPU work needs "
                      "explicit approval: --allow-gpu")
    rc, res = run_pytest("gpu", "gpu", {"CUDA_VISIBLE_DEVICES": idle[0]})
    res["counts"].update(counts)
    res["evidence"].insert(0, rel(report))
    if rc == 5:
        res.update(status="NOT_APPLICABLE", reason="no GPU-marked tests in this profile")
    return res


SUITES = {"unit": suite_unit, "integration": suite_integration, "relocation": suite_relocation,
          "package": suite_package, "gpu": suite_gpu}
started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
say(f"profile={profile} root={root} suites={','.join(requested)} evidence={rel(EVID)}")
results = []
for name in requested:
    t0 = time.monotonic()
    say(f"{name}: running")
    try:
        res = SUITES[name]()
    except Exception as exc:  # noqa: BLE001 - a crashed suite is a FAIL, not a silent pass
        res = result(name, "FAIL", reason=f"suite crashed: {type(exc).__name__}: {str(exc)[:300]}")
    res.setdefault("duration_s", round(time.monotonic() - t0, 1))
    res["profile"] = profile
    results.append(res)
    count_txt = " ".join(f"{k}={v}" for k, v in res["counts"].items() if not isinstance(v, (dict, list)) and k != "summary")
    say(f"{name}: {res['status']}" + (f" ({count_txt})" if count_txt else "") + (f" - {res['reason']}" if res["reason"] else ""))

tally = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "NOT_APPLICABLE", "BLOCKED_BY_ENV")}
worst = "FAIL" if tally["FAIL"] else "BLOCKED_BY_ENV" if tally["BLOCKED_BY_ENV"] else "PASS" if tally["PASS"] else "NOT_APPLICABLE"
not_applicable = [r for r in results if r["status"] == "NOT_APPLICABLE"]  # counted neither as pass nor as fail
meta = {"schema": "mbnas.verify/1", "root": root, "profile": profile, "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
if len(results) == 1:
    doc = {**results[0], **meta}
else:
    doc = {"suite": ",".join(requested), "status": worst, "counts": tally, "evidence": [rel(EVID)],
           "not_applicable": [r["suite"] for r in not_applicable], "results": results, **meta}
with open(os.path.join(EVID, "verify.json"), "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=1)
if json_out == "-":
    print(json.dumps(doc, indent=1))
elif json_out:
    os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
if not_applicable:
    say("not applicable (counted neither as pass nor as fail): "
        + "; ".join(r["suite"] + (f" - {r['reason']}" if r["reason"] else "") for r in not_applicable))
say(f"result: {worst} " + " ".join(f"{k}={v}" for k, v in tally.items() if v) + f" (report {rel(os.path.join(EVID, 'verify.json'))})")
sys.exit(1 if tally["FAIL"] else 3 if tally["BLOCKED_BY_ENV"] else 0)
PY
exit $?
