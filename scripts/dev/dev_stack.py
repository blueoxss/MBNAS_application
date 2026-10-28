#!/usr/bin/env python
"""Development stack: API (uvicorn) + one GPU worker (+ optional Vite web dev server) as separate processes.

    dev_stack.py [--env-file /abs/.env] [--web] [--gpu-index 0] [--gpu-lock /abs/gpu.lock] [start]
                                                                     start (the default action; the word
                                                                     ``start`` is optional; refuses if already
                                                                     running)
    dev_stack.py --status                                            pids, liveness, API health
    dev_stack.py --restart api|worker|web                            restart ONE process (API restart must not
                                                                     touch the worker or its training child;
                                                                     a worker restart waits until the old
                                                                     worker's processes have left NVML)
    dev_stack.py --stop                                              SIGTERM each process group, SIGKILL after 60 s

``--gpu-lock`` runs the worker under ``python -m mbnas_server.worker.gpu_guard --lock <lock> --wait-s 3600`` so it
never shares the GPU with other measurement jobs on the host that use the same lock file. Unlike ``flock -w 3600
<lock>`` the guardian keeps the lock until the worker *and* its training child have exited and their CUDA contexts
have left NVML (S7-F1: a SIGKILLed child stays listed ~150 ms after it died); the worker and its child also inherit
the lock descriptor. ``--restart worker`` waits (bounded, logged) for the old worker's pids to leave NVML before it
starts the new worker.

Every child is started with an argv list (no shell), in its own session, stdout+stderr to
``<log_dir>/dev_stack/<name>.log``; pids/argv/log paths are kept in ``<log_dir>/dev_stack/state.json``. Settings
come from the environment or ``--env-file`` (``MBNAS_ENV_FILE`` is passed on), never from command-line secrets.
The API token is read from the settings only to probe ``/api/v1/health`` and is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mbnas_core.config import CoreSettings as Settings
from mbnas_runner import gpu
from mbnas_contracts.util.atomic import atomic_write_json
from mbnas_contracts.util.time import utcnow_iso

WEB_ROOT = Path(__file__).resolve().parents[2] / "MBNAS_web"  # sibling of MBNAS_server (ETRI_NAS/application)


def _state_path(settings: Settings) -> Path:
    return Path(settings.log_dir or f"{settings.output_root}/logs") / "dev_stack" / "state.json"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def _health(settings: Settings) -> dict[str, Any]:
    req = urllib.request.Request(f"http://{settings.host}:{settings.port}/api/v1/health")
    if settings.api_token is not None:
        req.add_header("Authorization", f"Bearer {settings.api_token.get_secret_value()}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            return {"http": resp.status, "status": body.get("status"), "worker_count": body.get("worker_count"),
                    "commit_sha": body.get("commit_sha"), "warnings": body.get("warnings")}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code}
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


def _spawn(name: str, argv: list[str], log_dir: Path, env: dict[str, str], cwd: str | None = None) -> dict[str, Any]:
    log = log_dir / f"{name}.log"
    fh = open(log, "ab")  # noqa: SIM115 - the child owns the descriptor after spawn
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                            start_new_session=True, close_fds=True)
    fh.close()
    return {"name": name, "pid": proc.pid, "argv": argv, "log": str(log), "cwd": cwd, "started_at": utcnow_iso()}


def _argv(name: str, settings: Settings, args: argparse.Namespace) -> list[str]:
    """argv list of one stack process (never a shell string)."""
    py = settings.worker_python
    if name == "api":
        return [py, "-m", "uvicorn", "--factory", "mbnas_core.api.app:app_from_env", "--host", settings.host,
                "--port", str(settings.port), "--log-level", "info", "--timeout-graceful-shutdown", "3"]
    if name == "worker":
        worker = [py, "-m", "mbnas_core.owner_worker", "--gpu-index", str(args.gpu_index)]
        if not args.gpu_lock:
            return worker
        return [py, "-m", "mbnas_runner.gpu_guard", "--lock", os.path.abspath(args.gpu_lock), "--wait-s", "3600",
                "--gpu-index", str(args.gpu_index), "--", *worker]
    return ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--strictPort"]


def _env(settings: Settings, args: argparse.Namespace, name: str) -> dict[str, str]:
    env = dict(os.environ)
    if args.env_file:
        env["MBNAS_ENV_FILE"] = os.path.abspath(args.env_file)
    if name == "web":
        env["MBNAS_DEV_API"] = f"http://{settings.host}:{settings.port}"
    return env


def _wait_health(settings: Settings, timeout_s: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    health: dict[str, Any] = {}
    while time.monotonic() < deadline:
        health = _health(settings)
        if health.get("http") == 200:
            break
        time.sleep(1)
    return health


def _group_pids(pgid: int) -> set[int]:
    """Live pids of process group ``pgid`` (a stack process, its worker and the worker's training child)."""
    out: set[int] = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            fields = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[-1].split()
            if int(fields[2]) == pgid:
                out.add(int(name))
        except (OSError, ValueError, IndexError):
            continue
    return out


def wait_pids_leave_nvml(pids: set[int], gpu_index: int, timeout_s: float = 60.0,
                         pids_fn: Any = None) -> dict[str, Any]:
    """Wait (bounded, logged) until NVML lists none of ``pids`` on GPU ``gpu_index``."""
    listed_fn = pids_fn or gpu.compute_pids

    def ours(index: int) -> list[int] | None:
        listed = listed_fn(index)
        return None if listed is None else [p for p in listed if p in pids]

    return gpu.wait_clear(gpu_index, timeout_s=timeout_s, pids_fn=ours,
                          log=lambda m: print(f"{utcnow_iso()} dev_stack: {m}", file=sys.stderr, flush=True))


def _terminate(proc: dict[str, Any], grace_s: float = 20) -> None:
    """SIGTERM the process group, SIGKILL after ``grace_s``."""
    if _alive(proc["pid"]):
        os.killpg(proc["pid"], signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and _alive(proc["pid"]):
        time.sleep(0.5)
    if _alive(proc["pid"]):
        os.killpg(proc["pid"], signal.SIGKILL)


def start(settings: Settings, args: argparse.Namespace) -> int:
    state_path = _state_path(settings)
    if state_path.is_file():
        old = json.loads(state_path.read_text())
        if any(_alive(p["pid"]) for p in old.get("processes", [])):
            print(f"dev stack already running (state {state_path}); use --status or --stop")
            return 1
    settings.require_api_token()
    log_dir = state_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    names = ["api", "worker", *(["web"] if args.web else [])]
    procs = [_spawn(n, _argv(n, settings, args), log_dir, _env(settings, args, n),
                    cwd=str(WEB_ROOT) if n == "web" else None) for n in names]
    atomic_write_json(state_path, {"processes": procs, "started_at": utcnow_iso(), "state_file": str(state_path),
                                   "options": {"gpu_index": args.gpu_index, "gpu_lock": args.gpu_lock,
                                               "env_file": args.env_file}})
    health = _wait_health(settings)
    print(json.dumps({"state": str(state_path), "processes": procs, "health": health}, indent=1))
    return 0 if health.get("http") == 200 else 2


def status(settings: Settings) -> int:
    state_path = _state_path(settings)
    if not state_path.is_file():
        print(json.dumps({"running": False, "state": str(state_path)}))
        return 1
    state = json.loads(state_path.read_text())
    procs = [{**p, "alive": _alive(p["pid"])} for p in state["processes"]]
    print(json.dumps({"state": str(state_path), "processes": procs, "health": _health(settings)}, indent=1))
    return 0 if all(p["alive"] for p in procs) else 1


def restart(settings: Settings, args: argparse.Namespace) -> int:
    """Restart one process of a running stack; the others (e.g. the worker during training) are left alone."""
    state_path = _state_path(settings)
    if not state_path.is_file():
        print("dev stack not running")
        return 1
    state = json.loads(state_path.read_text())
    options = state.get("options") or {}
    for key in ("gpu_index", "gpu_lock", "env_file"):  # keep the options the stack was started with
        if getattr(args, key) in (None, 0) and options.get(key) is not None:
            setattr(args, key, options[key])
    name = args.restart
    idx = next((i for i, p in enumerate(state["processes"]) if p["name"] == name), None)
    if idx is None:
        print(f"no {name} process in {state_path}")
        return 1
    old = state["processes"][idx]
    old_pids = _group_pids(old["pid"]) | {old["pid"]} if name == "worker" else set()
    _terminate(old, grace_s=60 if name == "worker" else 20)
    gpu_wait = None
    if name == "worker":  # S7-F1: the old child's CUDA context must be gone before the new worker can lease
        gpu_wait = wait_pids_leave_nvml(old_pids, int(args.gpu_index or 0))
    new = _spawn(name, _argv(name, settings, args), state_path.parent, _env(settings, args, name),
                 cwd=str(WEB_ROOT) if name == "web" else None)
    state["processes"][idx] = new
    state.setdefault("restarts", []).append({"name": name, "old_pid": old["pid"], "new_pid": new["pid"],
                                             "at": utcnow_iso(), "old_pids": sorted(old_pids),
                                             "gpu_wait": gpu_wait})
    atomic_write_json(state_path, state)
    health = _wait_health(settings) if name == "api" else _health(settings)
    print(json.dumps({"restarted": name, "old_pid": old["pid"], "new_pid": new["pid"], "old_pids": sorted(old_pids),
                      "gpu_wait": gpu_wait, "health": health}, indent=1))
    return 0 if health.get("http") == 200 else 2


def stop(settings: Settings) -> int:
    state_path = _state_path(settings)
    if not state_path.is_file():
        print("dev stack not running")
        return 0
    procs = json.loads(state_path.read_text())["processes"]
    for p in reversed(procs):  # web, worker (finishes/requeues its job), then API
        if _alive(p["pid"]):
            os.killpg(p["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 60  # the worker cancels its child (kill grace) and its guardian waits for NVML
    while time.monotonic() < deadline and any(_alive(p["pid"]) for p in procs):
        time.sleep(0.5)
    for p in procs:
        if _alive(p["pid"]):
            os.killpg(p["pid"], signal.SIGKILL)
    state_path.rename(state_path.with_suffix(f".stopped-{int(time.time())}.json"))
    print(json.dumps({"stopped": [p["name"] for p in procs]}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--web", action="store_true", help="also run `npm run dev` in MBNAS_web")
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--gpu-lock", default=None,
                        help="run the worker under the GPU-lock guardian (mbnas_server.worker.gpu_guard, wait 3600 s)")
    parser.add_argument("action", nargs="?", choices=("start",), default=None,
                        help="optional; starting the stack is the default action")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stop", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--restart", choices=("api", "worker", "web"), default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "start" and (args.stop or args.status or args.restart):
        parser.error("`start` cannot be combined with --stop, --status or --restart")
    settings = Settings.load(args.env_file)
    if args.stop:
        return stop(settings)
    if args.status:
        return status(settings)
    if args.restart:
        return restart(settings, args)
    return start(settings, args)


if __name__ == "__main__":
    sys.exit(main())
