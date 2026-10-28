"""T6 rerun2 configuration (host side): owner ports; principals for the public and registration sandboxes issued by the
owner core (mbnas-core-principal against the owner DB), bound to the runner id each sandbox install generated
(runtime/runner/runner_id, DD-A61 b; no MBNAS_RUNNER_RUNNER_ID line is written). Customer-side output root of each
sandbox gateway: /srv/outputs (DD-A46/A53 split deployment). Token values move file to file and are never printed."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root/t6/rerun2")
OWN = RT / "mbnas owner r2/mbnas_application"
CORE_PORT, OWN_GW = 18900, 18911
PORTS = {"public": 18910, "registration": 18912}
CORE = f"http://127.0.0.1:{CORE_PORT}"


def setkeys(path: Path, kv: dict) -> None:
    lines = path.read_text().splitlines()
    keep = [l for l in lines if not any(l.startswith(f"{k}=") or l.startswith(f"# {k}=") for k in kv)]
    keep += [f"{k}={v}" for k, v in kv.items()]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, ("\n".join(keep) + "\n").encode())
    os.close(fd)
    print(f"configured {path.relative_to(RT)}: {sorted(kv)} (values not printed)")


def issue(*args: str) -> str:
    r = subprocess.run([str(OWN / "runtime/venv/bin/mbnas-core-principal"), "--env-file", str(OWN / "config/owner/core.env"),
                        "create", *args], capture_output=True, text=True, cwd="/",
                       env={**os.environ, "MBNAS_HOME": str(OWN / "runtime"), "CUDA_VISIBLE_DEVICES": ""})
    if r.returncode != 0:
        print(f"principal create failed: exit {r.returncode}")
        sys.exit(1)
    doc = json.loads(r.stdout)
    print(f"issued {args[1]} principal {doc['principal']['id']}" + (f" for {args[3]}" if len(args) > 3 else ""))
    return doc["token"]


def main() -> int:
    setkeys(OWN / "config/owner/core.env", {"MBNAS_CORE_PORT": CORE_PORT})
    setkeys(OWN / "config/owner/gateway.env", {"MBNAS_GW_PORT": OWN_GW, "MBNAS_GW_CORE_URL": CORE})
    setkeys(OWN / "config/owner/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE})
    for label, port in PORTS.items():
        tree = RT / f"mbnas {label} r2/mbnas_application"
        rid = (tree / "runtime/runner/runner_id").read_text().strip()
        print(f"{label}: runner id generated at install = {rid}")
        srv = issue("--kind", "server")
        rtk = issue("--kind", "runner", "--runner-id", rid)
        setkeys(tree / "config/public/gateway.env", {"MBNAS_GW_PORT": port, "MBNAS_GW_CORE_URL": CORE,
                                                     "MBNAS_GW_CORE_TOKEN": srv,
                                                     "MBNAS_GW_OUTPUT_ROOTS": '["/srv/outputs"]'})
        setkeys(tree / "config/public/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE, "MBNAS_RUNNER_TOKEN": rtk})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
