"""WP-7V P-final-2 T8 §2 runtime scan of the T6 rerun2 sandbox captures (public, registration): gateway responses,
service logs, /proc environ + cmdline of the gateway and runner. No normalization is needed: inside the sandbox the
tree lives at /srv/mbnas <label> r2 and the customer output root at /srv/outputs, so every host fact or owner path that
shows up is a real leak. Needles: canary tokens, host facts #1-#6, the secret-like values of every env file of the
rerun2 trees (owner core/gateway/runner, public and registration gateway/runner), and owner/build-host strings (owner
tree root, its DB/config/log/runs/data directories, mbnas.sqlite3, the source tree root, the session scratch prefix)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
RT = SRC / "runtime/test_root/t6/rerun2"
OWN = RT / "mbnas owner r2/mbnas_application"
HERE = Path(__file__).resolve().parent
EV = SRC / "docs/internal/evidence/WP7/PFINAL2/t8"
PY = str(SRC / "runtime/venv/bin/python")
SCAN = str(SRC / "scripts/dev/canary_scan.py")
MAN = str(SRC / "docs/internal/canary/CANARY_MANIFEST.json")


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    envs = [OWN / "config/owner" / f for f in ("core.env", "gateway.env", "runner.env")]
    for label in ("public", "registration"):
        envs += [RT / f"mbnas {label} r2/mbnas_application/config/public" / f for f in ("gateway.env", "runner.env")]
    sec = HERE / "t8r2_secrets.env"
    with open(sec, "w") as fh:
        for p in envs:
            fh.write(p.read_text() + "\n")
    os.chmod(sec, 0o600)
    needles = HERE / "t8r2_needles.txt"
    needles.write_text("\n".join([str(OWN), str(OWN / "runtime/core/db"), str(OWN / "config/owner"),
                                  str(OWN / "runtime/core/logs"), str(OWN / "runtime/core/runs"),
                                  str(OWN / "runtime/data"), "mbnas.sqlite3", str(SRC), "/tmp/claude-1001"]) + "\n")
    os.chmod(needles, 0o600)
    res = []
    for label in ("public", "registration"):
        ev = RT / f"mbnas {label} r2/evidence"
        for name, targets in ((f"{label}_responses_logs", [ev / "responses", ev / "logs"]), (f"{label}_proc", [ev / "proc"])):
            argv = [PY, "-I", SCAN, "--manifest", MAN, "--profile", "public", "--secrets-env", str(sec),
                    "--needles-file", str(needles), "--json", str(EV / f"runtime_{name}.json")] + [str(t) for t in targets]
            p = subprocess.run(argv, capture_output=True, text=True, cwd=str(HERE),
                               env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"})
            rel = [str(t.relative_to(RT)) for t in targets]
            (EV / f"runtime_{name}.txt").write_text(
                "$ python -I scripts/dev/canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile "
                "public --secrets-env <every env file of the rerun2 trees, scratch 0600> --needles-file <owner/build-host "
                f"strings, scratch 0600> {' '.join('<RT>/' + r for r in rel)}\nexit={p.returncode}\n" + p.stdout + p.stderr)
            doc = json.loads((EV / f"runtime_{name}.json").read_text())
            r = {"scan": name, "exit": p.returncode, "files": doc["scanned"]["files"], "needles": doc["needle_count"],
                 "hits_by_label": doc["hits_by_label"], "failing": doc["failing_hits"], "status": doc["status"]}
            res.append(r)
            print(json.dumps(r, ensure_ascii=False))
    (EV / "runtime_scan_summary.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if all(r["status"] == "PASS" for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
