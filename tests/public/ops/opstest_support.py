"""Helpers of the ops-script tests (WP-7A: scripts/install.sh, doctor.sh, start.sh, stop.sh, verify.sh).

``make_root(base)`` builds a throw-away ROOT below ``base``, always in a path with spaces, holding copies of the five
scripts and, unless ``venv=False``, a fake ``runtime/venv``: ``bin/python`` is a ``/bin/sh`` wrapper that execs the
test interpreter (so the settings classes of the public packages are importable), ``bin/mbnas-gateway`` and
``bin/mbnas-runner`` are stub programs (a tiny HTTP server / a sleeper) that record their argv, cwd,
``CUDA_VISIBLE_DEVICES`` and ``MBNAS_HOME`` in ``runtime/stub-<name>.json``. ``config/public/{gateway,runner}.env`` hold
test-only tokens. ``fake_nvidia_smi(dir)`` writes an ``nvidia-smi`` stand-in (mode from ``$STUB_GPU_MODE``), so no test
ever queries the real GPUs. ``StubCore`` answers every request with 401 on an ephemeral 127.0.0.1 port (a core that
is up but sees no credentials). ``make_wheel`` writes a minimal pure-Python wheel by hand (no build backend needed).

Nothing here touches a GPU, the network beyond 127.0.0.1, or a port outside the ephemeral range; every process a test
starts is stopped by the test (scripts/stop.sh) or, as a fallback, by :func:`kill_own` on the pids recorded below its
own throw-away ROOT.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = ("install", "doctor", "start", "stop", "verify")
TEST_API_TOKEN = "ops-test-api-token-" + "a" * 24
TEST_RUNNER_TOKEN = "ops-test-runner-token-" + "b" * 24

STUB_GATEWAY = r'''
import http.server, json, os, signal, sys
home = os.environ.get("MBNAS_HOME", "")
port = int(os.environ["MBNAS_GW_PORT"])
info = {"argv": sys.argv, "cwd": os.getcwd(), "cuda": os.environ.get("CUDA_VISIBLE_DEVICES"), "home": home,
        "pid": os.getpid()}
with open(os.path.join(home, "stub-gateway.json"), "w") as fh:
    json.dump(info, fh)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization", "").startswith("Bearer ")
        body = json.dumps({"status": "ok", "core": {"status": "ok", "reachable": True}}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def _term(*_):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, _term)
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''

STUB_RUNNER = r'''
import json, os, signal, subprocess, sys, time
home = os.environ.get("MBNAS_HOME", "")
info = {"argv": sys.argv, "cwd": os.getcwd(), "cuda": os.environ.get("CUDA_VISIBLE_DEVICES"), "home": home,
        "pid": os.getpid()}
if os.environ.get("STUB_IGNORE_TERM") == "1":  # a runner that does not stop on SIGTERM and has a child process
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    info["child"] = subprocess.Popen(["sleep", "600"]).pid
else:
    def _term(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
with open(os.path.join(home, "stub-runner.json"), "w") as fh:
    json.dump(info, fh)
while True:
    time.sleep(0.2)
'''

STUB_VITE = r'''
const http = require('http');
const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
const srv = http.createServer((req, res) => { res.writeHead(200, {'Content-Type': 'text/html'}); res.end('<p>stub</p>'); });
srv.listen(port, '127.0.0.1');
process.on('SIGTERM', () => { srv.close(); process.exit(0); });
'''

FAKE_NVIDIA_SMI = r'''#!/bin/sh
# test stand-in for nvidia-smi; never touches a GPU. STUB_GPU_MODE = busy (default) | idle | fail
mode="${STUB_GPU_MODE:-busy}"
[ "$mode" = fail ] && exit 9
case "$*" in
  *--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total*)
    if [ "$mode" = idle ]; then echo "0, GPU-stub-0, Stub GPU, 0, 10, 49140"; else echo "0, GPU-stub-0, Stub GPU, 95, 30000, 49140"; fi ;;
  *--query-gpu=*)
    echo "0, Stub GPU, 10, 49140, 0" ;;
  *--query-compute-apps=*)
    [ "$mode" = idle ] || echo "GPU-stub-0, 4242" ;;
esac
exit 0
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def make_root(base: Path, *, venv: bool = True, env_files: bool = True, web: bool = False) -> Path:
    """A throw-away ROOT (path with spaces) with copies of scripts/*.sh and, optionally, the fake venv / env files."""
    root = base / "ops root with spaces" / "mbnas app"
    (root / "scripts").mkdir(parents=True)
    for name in SCRIPTS:
        src = REPO / "scripts" / f"{name}.sh"
        (root / "scripts" / f"{name}.sh").write_bytes(src.read_bytes())
    if venv:
        vbin = root / "runtime" / "venv" / "bin"
        write(vbin / "python", f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n", 0o755)
        write(root / "runtime" / "venv" / "pyvenv.cfg", "include-system-site-packages = false\n")
        write(vbin / "mbnas-gateway", STUB_GATEWAY, 0o755)
        write(vbin / "mbnas-runner", STUB_RUNNER, 0o755)
    if env_files:
        write(root / "config" / "public" / "gateway.env",
              f"MBNAS_GW_API_TOKEN={TEST_API_TOKEN}\nMBNAS_GW_HOST=127.0.0.1\n", 0o600)
        write(root / "config" / "public" / "runner.env", f"MBNAS_RUNNER_TOKEN={TEST_RUNNER_TOKEN}\n", 0o600)
    if web:
        write(root / "mbnas_web" / "node_modules" / "vite" / "bin" / "vite.js", STUB_VITE)
        write(root / "mbnas_web" / "dist" / "index.html", "<!doctype html><title>stub</title>\n")
    return root


def fake_nvidia_smi(directory: Path) -> Path:
    write(directory / "nvidia-smi", FAKE_NVIDIA_SMI, 0o755)
    return directory


def script_env(tmp: Path, extra: dict[str, str] | None = None, fake_bin: Path | None = None) -> dict[str, str]:
    """Environment of a script run: the test process environment without MBNAS_* (the operator's settings), pip
    pointed away from the host pip.conf, a private TMPDIR and an optional fake-tool directory first on PATH."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update(PIP_CONFIG_FILE="/dev/null", PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp))
    tmp.mkdir(parents=True, exist_ok=True)
    if fake_bin is not None:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env.update(extra or {})
    return env


def run(root: Path, script: str, *args: str, cwd: Path, env: dict[str, str], timeout: float = 120,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(root / "scripts" / f"{script}.sh"), *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=timeout, input=stdin)


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


def wait_dead(pid: int, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


def stub_info(root: Path, name: str) -> dict:
    return json.loads((root / "runtime" / f"stub-{name}.json").read_text(encoding="utf-8"))


def kill_own(root: Path) -> None:
    """Fallback cleanup: SIGKILL the processes recorded below this test's own ROOT (pid files and stub children) when
    their argv still names this ROOT (never anything else)."""
    pids: set[int] = set()
    for pidfile in (root / "runtime" / "pids").glob("*.pid"):
        try:
            pids.add(int(pidfile.read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            pass
    children: set[int] = set()
    for info in (root / "runtime").glob("stub-*.json"):
        try:
            doc = json.loads(info.read_text())
        except (OSError, ValueError):
            continue
        pids.add(int(doc.get("pid", 0)))
        if doc.get("child"):
            children.add(int(doc["child"]))
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if str(root).encode() not in fh.read():
                    continue
            os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass
    for pid in children:  # recorded by our own stub; "sleep 600"
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if fh.read().split(b"\0")[:2] == [b"sleep", b"600"]:
                    os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class StubCore:
    """An HTTP server on 127.0.0.1:<ephemeral> answering 401 to everything (a core seen without credentials)."""

    def __init__(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubCore":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def make_wheel(directory: Path, dist: str, version: str = "0.1.0") -> Path:
    """A minimal pure-Python wheel ``<module>-<version>-py3-none-any.whl`` with an empty package (correct RECORD)."""
    mod = dist.replace("-", "_")
    info = f"{mod}-{version}.dist-info"
    files = {f"{mod}/__init__.py": f'"""stub of {dist} for the install.sh tests"""\n',
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n",
             f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: wp7a-ops-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = []
    for name, text in files.items():
        data = text.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = "\n".join(record) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{mod}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path
