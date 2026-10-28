"""WP-7V P1-final T8 §2: leak scan of the runtime captures of final_driver.py (public + registration stacks).

Needles: canary tokens + the fixed host facts (incl. the original host's GPU UUID) + the secret-like values of every env
file of the final trees (owner core/gateway/runner, public gateway/runner, registration gateway/runner; also for /proc:
the services read their secrets with --env-file, so no secret value belongs in a process environment) + owner-side strings (the owner tree root, its DB dir, config dir, core log dir,
core output root, core dataset root, "mbnas.sqlite3"). Values are never printed.

raw        : the captures as they are.
normalized : only two strings the test itself controls are replaced before scanning: the scanned tree's own root (the
             customer's install path: its logs/environ name it) and the output_root the test client put in its protocol
             (echoed back by validate/run/export). Everything else - including the owner core's own output root, which
             is the parent of the supplied one - stays and fails. The verdict is the normalized scan; raw hits are listed.
Install logs are scanned separately (they echo the operator's install command: wheelhouse and interpreter paths).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_driver as d  # noqa: E402

EV8 = d.SRC / "docs/internal/evidence/WP7/7V_FINAL/T8"
PY = str(d.SRC / "runtime/venv/bin/python")
SCAN = str(d.SRC / "scripts/dev/canary_scan.py")
MAN = str(d.SRC / "docs/internal/canary/CANARY_MANIFEST.json")


def combined(paths: list[Path], out: Path) -> Path:
    with open(out, "w") as fh:
        for p in paths:
            fh.write(p.read_text() + "\n")
    os.chmod(out, 0o600)
    return out


def scan(name: str, targets: list[Path], secrets: Path, needles: Path) -> dict:
    argv = [PY, "-I", SCAN, "--manifest", MAN, "--profile", "public", "--secrets-env", str(secrets),
            "--needles-file", str(needles), "--json", str(EV8 / f"{name}.json")] + [str(t) for t in targets]
    p = subprocess.run(argv, capture_output=True, text=True, cwd=str(d.HERE),
                       env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"})
    rel = [str(t.relative_to(d.HERE)) for t in targets]
    (EV8 / f"{name}.txt").write_text("$ python -I scripts/dev/canary_scan.py --manifest docs/internal/canary/"
                                     "CANARY_MANIFEST.json --profile public --secrets-env <combined env files, scratch "
                                     f"0600> --needles-file <owner-side strings, scratch 0600> {' '.join(rel)}\n"
                                     f"exit={p.returncode}\n" + p.stdout + p.stderr)
    doc = json.loads((EV8 / f"{name}.json").read_text())
    res = {"name": name, "exit": p.returncode, "files": doc["scanned"]["files"], "needles": doc["needle_count"],
           "hits_by_label": doc["hits_by_label"], "failing_hits": doc["failing_hits"], "status": doc["status"]}
    print(json.dumps(res, ensure_ascii=False))
    return res


def normalize(src: Path, dst: Path, tree: Path) -> int:
    reps = [(str(d.SUPPLIED_OUTPUT_ROOT), "<TEST_SUPPLIED_OUTPUT_ROOT>"), (str(tree), "<THIS_TREE_ROOT>")]
    n = 0
    if dst.exists():
        shutil.rmtree(dst)
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        out = dst / f.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = f.read_bytes()
        if f.suffix == ".zip":   # rewrite the members of the export zip too
            import io
            import zipfile
            zin = zipfile.ZipFile(io.BytesIO(data))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zout:
                for info in zin.infolist():
                    member = zin.read(info)
                    for a, b in reps:
                        n += member.count(a.encode())
                        member = member.replace(a.encode(), b.encode())
                    zout.writestr(info, member)
            data = buf.getvalue()
        else:
            for a, b in reps:
                n += data.count(a.encode())
                data = data.replace(a.encode(), b.encode())
        out.write_bytes(data)
    return n


def main() -> int:
    EV8.mkdir(parents=True, exist_ok=True)
    own = [d.OWN / "config/owner" / f for f in ("core.env", "gateway.env", "runner.env")]
    pub = [d.PUB / "config/public" / f for f in ("gateway.env", "runner.env")]
    reg = [d.REG / "config/public" / f for f in ("gateway.env", "runner.env")]
    sec_all = combined(own + pub + reg, d.HERE / "secrets_all.env")
    sec_proc = {"public": sec_all, "registration": sec_all}   # services read --env-file: no secret belongs in environ
    needles = d.HERE / "needles_owner.txt"
    needles.write_text("\n".join([str(d.OWN), str(d.OWN / "runtime/core/db"), str(d.OWN / "config/owner"),
                                  str(d.OWN / "runtime/core/logs"), str(d.OWN / "runtime/core/runs"),
                                  str(d.OWN / "runtime/data"), "mbnas.sqlite3"]) + "\n")
    os.chmod(needles, 0o600)
    results = []
    for sub, tree in (("public", d.PUB), ("registration", d.REG)):
        base = d.CAP / sub
        resp = d.CAP / ("responses" if sub == "public" else "responses_registration")
        norm = d.HERE / f"capture_normalized/{sub}"
        replaced = normalize(base, norm / "tree", tree) + normalize(resp, norm / "responses", tree)
        results.append({"normalized_replacements": replaced, "set": sub})
        results.append(scan(f"{sub}_raw_responses_logs", [resp, base / "logs"], sec_all, needles))
        results.append(scan(f"{sub}_raw_proc", [base / "proc"], sec_proc[sub], needles))
        results.append(scan(f"{sub}_raw_install_logs", [base / "install_logs"], sec_all, needles))
        results.append(scan(f"{sub}_normalized_responses_logs", [norm / "responses", norm / "tree/logs"], sec_all,
                            needles))
        results.append(scan(f"{sub}_normalized_proc", [norm / "tree/proc"], sec_proc[sub], needles))
        results.append(scan(f"{sub}_normalized_install_logs", [norm / "tree/install_logs"], sec_all, needles))
    (EV8 / "t8_final_scan_summary.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
