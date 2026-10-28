#!/usr/bin/env python
"""Second API process for the explicit-origin browser test: same DB/output root as the dev stack, NO artifact signing
key (so every artifact is served as ``bearer_fetch`` and the web must fetch it with Authorization into a Blob URL),
and CORS limited to one exact web origin.

    api_bearer_only.py --env-file /abs/.env [--port 8001] [--cors-origin http://127.0.0.1:5173]   start (prints pid)

WP-1V (WP-1_RESOLUTIONS V2): gateway settings (``MBNAS_GW_*``), started with this interpreter (``sys.executable``).
Secrets are read from the dotenv file and passed to uvicorn through the environment only (never argv or stdout).
Stop it with ``kill -TERM -<pid>`` (it runs in its own session). Log: ``<log_dir>/api_bearer_only.log``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from dotenv import dotenv_values

from mbnas_server.config import GatewaySettings as Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--cors-origin", default="http://127.0.0.1:5173")
    args = parser.parse_args(argv)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_GW_")}
    env.update({k: v for k, v in dotenv_values(args.env_file).items()
                if k.startswith("MBNAS_GW_") and k != "MBNAS_GW_ARTIFACT_SIGNING_KEY" and v is not None})
    env.update({"MBNAS_GW_PORT": str(args.port), "MBNAS_GW_CORS_ORIGINS": args.cors_origin})
    settings = Settings.load(args.env_file)
    log_path = os.path.join(str(settings.log_dir), "api_bearer_only.log")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "mbnas_server.api.app:app_from_env",
                                 "--host", "127.0.0.1", "--port", str(args.port), "--timeout-graceful-shutdown", "3"],
                                env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    print(proc.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
