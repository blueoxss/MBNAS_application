#!/usr/bin/env python3
"""Container environment leak check for T5 (values are never printed, only labels and counts).

For each container: the configured env (``docker inspect .Config.Env``) and the live process env (``docker exec env``)
are searched for needles. Needle groups (label -> values) come from dotenv files (``--group label=file:KEY[,KEY..]``,
or ``label=file:*secret*`` for every secret-like key) and from the canary manifest (``--canary``).
``--expect container:label`` marks a group that is EXPECTED in that container (e.g. the gateway's own server token);
every other hit is a leak. Exit 0 = no unexpected hit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SECRET_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|TICKET_KEY|SIGNING_KEY|PRIVATE_KEY)", re.I)


def dotenv(path: str) -> dict[str, str]:
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", action="append", required=True)
    ap.add_argument("--group", action="append", default=[], help="label=file:KEY1,KEY2 or label=file:*secret*")
    ap.add_argument("--canary", help="CANARY_MANIFEST.json (values + the MBNAS-CANARY- prefix)")
    ap.add_argument("--expect", action="append", default=[], help="container:label expected to be present")
    a = ap.parse_args()
    groups: dict[str, list[str]] = {}
    for g in a.group:
        label, spec = g.split("=", 1)
        path, keys = spec.rsplit(":", 1)
        env = dotenv(path)
        if keys == "*secret*":
            vals = [v for k, v in env.items() if SECRET_RE.search(k) and len(v) >= 12]
        else:
            vals = [env[k] for k in keys.split(",") if env.get(k)]
        groups.setdefault(label, []).extend(vals)
    if a.canary:
        man = json.load(open(a.canary, encoding="utf-8"))
        groups["canary_values"] = [x["value"] for x in man["areas"] if x.get("value")]
        groups["canary_prefix"] = ["MBNAS-CANARY-"]
    expected = {tuple(e.split(":", 1)) for e in a.expect}
    bad = 0
    report = {"groups": {k: len(v) for k, v in groups.items()}, "containers": {}}
    for c in a.container:
        cfg = subprocess.run(["docker", "inspect", c, "--format", "{{json .Config.Env}}"], capture_output=True, text=True)
        live = subprocess.run(["docker", "exec", c, "env"], capture_output=True, text=True)
        texts = {"config_env": cfg.stdout, "live_env": live.stdout}
        res = {"inspect_exit": cfg.returncode, "exec_exit": live.returncode,
               "env_keys": sorted({l.split("=", 1)[0] for l in live.stdout.splitlines() if "=" in l and l.startswith("MBNAS_")})}
        for label, vals in groups.items():
            for where, text in texts.items():
                n = sum(1 for v in vals if v and v in text)
                exp = (c, label) in expected
                res[f"{label}@{where}"] = {"hits": n, "of": len(vals), "expected": exp}
                if n and not exp:
                    bad += 1
        report["containers"][c] = res
    report["unexpected_hits"] = bad
    report["verdict"] = "PASS" if bad == 0 else "FAIL"
    print(json.dumps(report, indent=1))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
