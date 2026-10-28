#!/usr/bin/env python3
"""WP-7B self-check of config/examples/*.env.example against the live settings classes.

1. every field of CoreSettings / GatewaySettings / RunnerSettings appears as `[# ]KEY=` in its role file and in the
   owner all-in-one file; 2. no unknown MBNAS_* key; 3. every documented example value (commented lines uncommented,
   placeholders <...> replaced by dummies) loads through the real class as a dotenv file; 4. no host facts / no
   secret-looking values.
"""
import os, re, sys, tempfile
from pathlib import Path

ROOT = Path(sys.argv[1])
EX = ROOT / "config" / "examples"
from mbnas_core.config import CoreSettings
from mbnas_server.config import GatewaySettings
from mbnas_runner.config import RunnerSettings

CLASSES = {"core.env.example": (CoreSettings, "MBNAS_CORE_"), "gateway.env.example": (GatewaySettings, "MBNAS_GW_"),
           "runner.env.example": (RunnerSettings, "MBNAS_RUNNER_")}
EXTRA_OK = {"MBNAS_HOME", "MBNAS_WORKER_GPU_INDEX", "MBNAS_WORKER_GPU_CLEAR_TIMEOUT_S"}
KEYLINE = re.compile(r"^#?\s?(MBNAS_[A-Z0-9_]+)=(.*)$")
HOST_FACTS = ["mgjeong", "/data/", "/root/detection", "147.46.114.253", "pc2300", "/home/", "etri_nas", "Desktop"]
fail = 0

def keys_of(path):
    out = {}
    for line in path.read_text().splitlines():
        m = KEYLINE.match(line)
        if m:
            out.setdefault(m.group(1), []).append(m.group(2))
    return out

allin = keys_of(EX / "owner-all-in-one.env.example")
for fname, (cls, prefix) in CLASSES.items():
    path = EX / fname
    found = keys_of(path)
    fields = {prefix + n.upper() for n in cls.model_fields}
    missing = sorted(fields - set(found))
    missing_aio = sorted(fields - set(allin))
    unknown = sorted(k for k in found if k not in fields and k not in EXTRA_OK)
    print(f"[{fname}] fields={len(fields)} documented={len(fields & set(found))} missing={missing} "
          f"unknown={unknown} missing_in_all_in_one={missing_aio}")
    fail |= bool(missing or unknown or missing_aio)
    # 3. parse every example value through the class
    lines = []
    for key, vals in found.items():
        if key not in fields:
            continue
        val = vals[-1].split("   (")[0].strip()
        val = re.sub(r"<[A-Z-]+>", "dummy-placeholder-value", val)
        lines.append(f"{key}={val}")
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
        tmp = fh.name
    try:
        obj = cls(_env_file=tmp)
        print(f"   all {len(lines)} example values load through {cls.__name__}: OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   example values FAIL to load through {cls.__name__}: {exc}")
        fail = 1
    finally:
        os.unlink(tmp)

unknown_aio = sorted(k for k in allin if not any(k.startswith(p) for _, p in CLASSES.values()) and k not in EXTRA_OK)
print(f"[owner-all-in-one.env.example] keys={len(allin)} unknown={unknown_aio}")
fail |= bool(unknown_aio)
for path in sorted(EX.glob("*.env.example")):
    text = path.read_text()
    facts = [f for f in HOST_FACTS if f in text]
    active = [l for l in text.splitlines() if l and not l.startswith("#")]
    longtok = [t for t in re.findall(r"[A-Za-z0-9_\-]{24,}", text)
                if re.search(r"[a-z]", t) and re.search(r"[0-9]", t) and re.search(r"[A-Z]", t)]
    print(f"[{path.name}] host-facts={facts} active(uncommented) lines={active} long-token-like={longtok}")
    fail |= bool(facts or longtok)
print("RESULT", "FAIL" if fail else "PASS")
sys.exit(1 if fail else 0)
