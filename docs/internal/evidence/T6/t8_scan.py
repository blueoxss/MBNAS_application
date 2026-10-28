"""T8 runtime leak scan over the T6 captures (responses, public logs, public /proc environ+cmdline).

1. raw scan  : canary tokens + host facts + secret values of the owner env files (+ the public env files for responses
               and logs) + extra needles (owner DB path, owner config dir, owner core log dir). Values never printed.
2. normalized: the same scan over a copy in which only the paths the test itself legitimately exposes are replaced:
               the public tree root (the customer's own install path, in its own logs/environ) and the output_root the
               test passed in the protocol (echoed back by validate/run). Remaining host-fact hits = leaks.
Secrets are judged on the raw scan only (normalization never touches them).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
T6 = SRC / "runtime/test_root/t6"
OWN = T6 / "mbnas owner test/mbnas_application"
PUB = T6 / "mbnas public test/mbnas_application"
HERE = Path(__file__).resolve().parent
CAP = HERE / "capture"
EV8 = SRC / "docs/internal/evidence/T8"
PY = str(SRC / "runtime/venv/bin/python")
SCAN = str(SRC / "scripts/dev/canary_scan.py")
MAN = str(SRC / "docs/internal/canary/CANARY_MANIFEST.json")


def combined_env(paths: list[Path], out: Path) -> Path:
    with open(out, "w") as fh:
        for p in paths:
            fh.write(p.read_text() + "\n")
    os.chmod(out, 0o600)
    return out


def scan(name: str, targets: list[Path], secrets: Path, needles: Path, *, host_facts: bool = True) -> dict:
    argv = [PY, "-I", SCAN, "--manifest", MAN, "--profile", "public", "--secrets-env", str(secrets),
            "--needles-file", str(needles), "--json", str(EV8 / f"{name}.json")]
    if not host_facts:
        argv.append("--no-host-facts")
    argv += [str(t) for t in targets]
    p = subprocess.run(argv, capture_output=True, text=True, cwd=str(HERE),
                       env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"})
    (EV8 / f"{name}.txt").write_text(f"$ canary_scan.py --manifest <CANARY_MANIFEST> --profile public --secrets-env "
                                     f"<combined env, scratch 0600> --needles-file <scratch> "
                                     f"{'--no-host-facts ' if not host_facts else ''}<targets>\n"
                                     f"targets: {[str(t) for t in targets]}\nexit={p.returncode}\n" + p.stdout + p.stderr)
    print(f"{name}: exit={p.returncode} :: {p.stdout.strip().splitlines()[-2:] if p.stdout.strip() else p.stderr[-300:]}")
    return {"name": name, "exit": p.returncode}


def normalize(src: Path, dst: Path) -> int:
    reps = [(str(PUB), "<PUBLIC_ROOT>"), (str(OWN / "runtime/core/runs"), "<TEST_SUPPLIED_OUTPUT_ROOT>")]
    n = 0
    if dst.exists():
        shutil.rmtree(dst)
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        out = dst / f.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = f.read_bytes()
        if f.suffix != ".zip":
            for a, b in reps:
                n += data.count(a.encode())
                data = data.replace(a.encode(), b.encode())
        out.write_bytes(data)
    return n


def main() -> int:
    EV8.mkdir(parents=True, exist_ok=True)
    own_env = [OWN / "config/owner" / f for f in ("core.env", "gateway.env", "runner.env")]
    pub_env = [PUB / "config/public" / f for f in ("gateway.env", "runner.env")]
    sec_all = combined_env(own_env + pub_env, HERE / "secrets_all.env")
    sec_own = combined_env(own_env, HERE / "secrets_owner.env")
    needles = HERE / "needles_owner.txt"
    needles.write_text("\n".join([str(OWN / "runtime/core/db"), str(OWN / "config/owner"),
                                  str(OWN / "runtime/core/logs"), "mbnas.sqlite3"]) + "\n")
    os.chmod(needles, 0o600)
    # unzip the export bundle so its members are scanned as files too (the scanner also opens zips)
    res = []
    res.append(scan("t8_runtime_raw_responses_logs", [CAP / "responses", CAP / "logs"], sec_all, needles))
    res.append(scan("t8_runtime_raw_proc", [CAP / "proc"], sec_own, needles))
    norm = HERE / "capture_normalized"
    replaced = normalize(CAP, norm)
    print(f"normalized: {replaced} replacement(s)")
    res.append(scan("t8_runtime_normalized_responses_logs", [norm / "responses", norm / "logs"], sec_all, needles))
    res.append(scan("t8_runtime_normalized_proc", [norm / "proc"], sec_own, needles))
    (EV8 / "t8_runtime_scan_summary.json").write_text(json.dumps({"scans": res, "normalized_replacements": replaced},
                                                                 indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
