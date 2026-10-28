"""T6 helper: set ports/core URLs in the two scratch trees and issue the public principals from the owner core DB.
Token values are only moved between files; never printed."""
import json, os, subprocess, sys
A = "/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root/t6"
O = f"{A}/mbnas owner test/mbnas_application"
P = f"{A}/mbnas public test/mbnas_application"
CORE_PORT, OWN_GW_PORT, PUB_GW_PORT = 18700, 18711, 18710
CORE = f"http://127.0.0.1:{CORE_PORT}"

def setkeys(path, kv):
    lines = open(path).read().splitlines()
    keep = [l for l in lines if not any(l.startswith(f"{k}=") or l.startswith(f"# {k}=") for k in kv)]
    keep += [f"{k}={v}" for k, v in kv.items()]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, ("\n".join(keep) + "\n").encode()); os.close(fd)
    print("set", os.path.relpath(path, A), sorted(k for k in kv))

setkeys(f"{O}/config/owner/core.env", {"MBNAS_CORE_PORT": CORE_PORT})
setkeys(f"{O}/config/owner/gateway.env", {"MBNAS_GW_PORT": OWN_GW_PORT, "MBNAS_GW_CORE_URL": CORE})
setkeys(f"{O}/config/owner/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE})

def issue(*args):
    r = subprocess.run([f"{O}/runtime/venv/bin/mbnas-core-principal", "--env-file", f"{O}/config/owner/core.env",
                        "create", *args], capture_output=True, text=True, cwd="/tmp",
                       env={**os.environ, "MBNAS_HOME": f"{O}/runtime"})
    if r.returncode != 0:
        print("principal create failed", r.returncode, r.stderr[-500:].replace("\n", " | ")); sys.exit(1)
    doc = json.loads(r.stdout)
    print("issued", args[1], doc["principal"]["id"])
    return doc["token"]

R = f"{A}/mbnas registration test/mbnas_application"
if "--registration" in sys.argv:
    srv = issue("--kind", "server")
    run = issue("--kind", "runner", "--runner-id", "runner-t6-registration")
    setkeys(f"{R}/config/public/gateway.env", {"MBNAS_GW_PORT": 18712, "MBNAS_GW_CORE_URL": CORE,
                                               "MBNAS_GW_CORE_TOKEN": srv})
    setkeys(f"{R}/config/public/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE, "MBNAS_RUNNER_TOKEN": run,
                                              "MBNAS_RUNNER_RUNNER_ID": "runner-t6-registration"})
    sys.exit(0)
if "--issue" in sys.argv:
    srv = issue("--kind", "server")
    run = issue("--kind", "runner", "--runner-id", "runner-t6-public")
    setkeys(f"{P}/config/public/gateway.env", {"MBNAS_GW_PORT": PUB_GW_PORT, "MBNAS_GW_CORE_URL": CORE,
                                               "MBNAS_GW_CORE_TOKEN": srv})
    setkeys(f"{P}/config/public/runner.env", {"MBNAS_RUNNER_CORE_URL": CORE, "MBNAS_RUNNER_TOKEN": run,
                                              "MBNAS_RUNNER_RUNNER_ID": "runner-t6-public"})
