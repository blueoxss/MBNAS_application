"""Fix round 1 install smoke check (evidence only; run with the scratch install's venv python, MBNAS_HOME=<copy>/runtime).

Prints NO secret: only key names of runner.env, the runner id (public by design), the principal's id/kind/runner_id.
Usage: python -I -B verify_install_runner_id.py <copy root> <install transcript>
"""
import json, os, re, socket, sqlite3, sys
from pathlib import Path

root, transcript = Path(sys.argv[1]), Path(sys.argv[2])
host = socket.gethostname()
out = {"hostname_checked": "<this host's name, not printed>", "checks": {}}
env = root / "config" / "owner" / "runner.env"
active = {}
for line in env.read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
    if m:
        active[m.group(1)] = m.group(2)
out["runner_env_active_keys"] = sorted(active)
out["runner_env_sets_MBNAS_RUNNER_RUNNER_ID"] = "MBNAS_RUNNER_RUNNER_ID" in active
id_file = root / "runtime" / "runner" / "runner_id"
file_id = id_file.read_text(encoding="utf-8").strip()
out["runtime/runner/runner_id"] = file_id
out["runtime/runner/runner_id mode"] = oct(id_file.stat().st_mode & 0o777)
from mbnas_runner.config import RunnerSettings  # the runner's own rule, as `mbnas-runner run --env-file` loads it
settings_id = RunnerSettings.load(str(env)).runner_id
out["RunnerSettings.load(runner.env).runner_id"] = settings_id
db = next((root / "runtime" / "core" / "db").glob("*.sqlite3"))
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = conn.execute("SELECT id, kind, runner_id, revoked_at FROM principals ORDER BY created_at").fetchall()
runners = conn.execute("SELECT id, status FROM runners ORDER BY id").fetchall()
conn.close()
out["principals"] = [{"id": r[0], "kind": r[1], "runner_id": r[2], "revoked": r[3] is not None} for r in rows]
out["runners_rows"] = [{"id": r[0], "status": r[1]} for r in runners]
issued = [l for l in transcript.read_text(encoding="utf-8").splitlines() if "issued runner principal" in l]
out["transcript_issue_line"] = issued
runner_principals = [r for r in rows if r[1] == "runner"]
c = out["checks"]
c["one runner principal"] = len(runner_principals) == 1
c["principal bound to the generated id"] = bool(runner_principals) and runner_principals[0][2] == file_id
c["runner's own settings use the same id"] = settings_id == file_id
c["generated form runner-<8 hex>"] = bool(re.fullmatch(r"runner-[0-9a-f]{8}", file_id))
c["host name not in the id"] = host not in file_id and host not in (runner_principals[0][2] if runner_principals else "")
c["not runner-<hostname>"] = file_id != f"runner-{host}"
c["transcript names the same id"] = any(file_id in l for l in issued)
c["host name not in runner.env or the id file"] = host not in env.read_text(encoding="utf-8") and host not in id_file.read_text()
out["ALL_PASS"] = all(c.values())
print(json.dumps(out, indent=1))
sys.exit(0 if out["ALL_PASS"] else 1)
