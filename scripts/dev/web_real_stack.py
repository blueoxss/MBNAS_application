#!/usr/bin/env python3
"""Real stack for the web console's REAL-SERVER browser E2E (WP-6 section 2 acceptance, DD-A50 "V4").

Starts, as real processes (pattern and plumbing of ``scripts/dev/wp3_three_process_smoke.py``, reused by import):

* core: ``uvicorn --factory mbnas_core.api.app:app_from_env`` (127.0.0.1, ephemeral port, no legacy ``/api/v1``);
* owner worker: ``python -m mbnas_core.owner_worker --owner``;
* runner daemon: ``python -m mbnas_runner run`` on the CPU (``CUDA_VISIBLE_DEVICES=""``) with WP-5C local transfer
  (``MBNAS_RUNNER_ARTIFACT_TRANSFER=local``, ``MBNAS_RUNNER_ARTIFACT_STORE_DIR`` = the gateway's artifact store);
* gateway: ``uvicorn --factory mbnas_server.api.app:app_from_env`` (127.0.0.1, ephemeral port) serving the BUILT
  ``mbnas_web/dist`` same-origin (DD-A30; the script refuses to start without ``dist/index.html``).

It issues the core principals (server / runner / admin) with ``mbnas-core-principal``, builds a synthetic 4-image
1-class dataset below the stack root (the server paths a user types into the console), then prints ONE line::

    MBNAS_REAL_STACK {"url": ..., "bearer": ..., "pid": ..., "dataset_yaml": ..., "output_root": ..., ...}

and waits. The bearer is a fresh per-start random token (never written to a file by this script). Signals:

* ``SIGUSR1``: stop ONLY the core process (the E2E's "core down -> degraded / explicit failure, no fallback" step);
* ``SIGTERM`` / ``SIGINT`` (or ``--max-seconds`` elapsed): stop every process started here, verify exit, exit 0.

Only pids started here are signalled. No GPU, no network beyond 127.0.0.1, never ports 8000-8002 / 5173-5175.
Process logs: ``<root>/logs/*.log``; step log: ``--evidence`` (tokens redacted) when given.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESERVED = (8000, 8001, 8002, 5173, 5174, 5175)

_spec = importlib.util.spec_from_file_location("wp3_three_process_smoke", ROOT / "scripts" / "dev" / "wp3_three_process_smoke.py")
wp3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp3)  # type: ignore[union-attr]


class Stack(wp3.Smoke3):
    def run_stack(self) -> int:
        started, t_all = wp3.now(), time.monotonic()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.web_dist_kind != "built":
            print("mbnas_web/dist/index.html missing: run `npm run build` in mbnas_web first", file=sys.stderr)
            return 2
        stop = {"all": False, "core": False}
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("all", True))
        signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("all", True))
        signal.signal(signal.SIGUSR1, lambda *_: stop.__setitem__("core", True))
        ok = False
        try:
            ds = self.build_inputs()
            self.start_core()
            self.principals()
            self.start_workers()
            ok = self.start_gateway()
            info = {"url": self.gw_base, "bearer": self.user_token, "pid": __import__("os").getpid(),
                    "core_url": self.base, "core_pid": self.procs["core"].pid, "root": str(self.root),
                    "dataset_yaml": ds["dataset_yaml"], "output_root": str(self.root / "core" / "runs"),
                    "artifact_store": str(self.root / "gateway" / "artifacts"), "gateway_ok": ok}
            print("MBNAS_REAL_STACK " + json.dumps(info), flush=True)
            deadline = time.monotonic() + self.args.max_seconds
            while not stop["all"] and time.monotonic() < deadline:
                if stop["core"] and self.procs["core"].poll() is None:
                    self.procs["core"]._expected_exit = True  # type: ignore[attr-defined]
                    self.procs["core"].send_signal(signal.SIGTERM)
                    self.procs["core"].wait(timeout=60)
                    self.note(f"core stopped on SIGUSR1 (exit {self.procs['core'].returncode})")
                    print("MBNAS_REAL_STACK_CORE_STOPPED", flush=True)
                for name, p in self.procs.items():
                    if p.poll() is not None and not getattr(p, "_expected_exit", False) and not getattr(p, "_noted", False):
                        p._noted = True  # type: ignore[attr-defined]
                        self.note(f"{name} pid={p.pid} exited unexpectedly with {p.returncode}")
                time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001 - recorded, then everything is stopped
            self.step("error", False, t_all, error=f"{type(exc).__name__}: {exc}"[:1000])
            ok = False
        finally:
            for p in self.procs.values():
                p._expected_exit = True  # type: ignore[attr-defined]
            c = self.stop_all()
        if self.args.evidence:
            self.write_evidence(ok and c, started, time.monotonic() - t_all)
        return 0 if c else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0, help="core port (default ephemeral)")
    p.add_argument("--gw-port", type=int, default=0, help="gateway port (default ephemeral)")
    p.add_argument("--root", default=None, help="stack root (default $MBNAS_TEST_ROOT/web_real_stack/<stamp>)")
    p.add_argument("--web-dist", default=None, help="built web bundle (default mbnas_web/dist)")
    p.add_argument("--device-source", choices=("local", "runners"), default="local")
    p.add_argument("--max-seconds", type=float, default=3 * 3600, help="stop everything after this long")
    p.add_argument("--evidence", default=None, help="write the step log here on exit (tokens redacted)")
    args = p.parse_args(argv)
    if args.port in RESERVED or args.gw_port in RESERVED:
        p.error("ports 8000-8002 / 5173-5175 are reserved")
    if not args.root:
        import os
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        args.root = str(Path(os.environ.get("MBNAS_TEST_ROOT") or ROOT / "runtime" / "test_root") / "web_real_stack" / stamp)
    args.timeout = 600
    return Stack(args).run_stack()


if __name__ == "__main__":
    raise SystemExit(main())
