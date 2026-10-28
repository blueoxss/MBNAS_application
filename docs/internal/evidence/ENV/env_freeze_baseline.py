#!/usr/bin/env python3
"""Freeze baselines of the research environments + the project venv pin check (C01 env_compat, EVAL_1 D3). Read-only.

No freeze of the research environments was recorded before this project started (2026-10-23), so the baseline is
recorded now and its "unchanged since the project start" claim rests on file times: every site directory, its
top-level entries, its ``*.dist-info`` / ``*.egg-info`` directories and the environment's ``bin/`` are listed with
their mtimes and the entries newer than ``--project-start`` (default 2026-10-23T20:00:00+09:00) are counted. Later
stages run this script again and compare (``freeze sha256`` and ``newer_than_project_start`` per environment).

Environments (absent ones are reported as absent):

* ``sg26`` (``~/venvs/sg26``), ``sg`` and ``mbnas310`` (``<conda>/envs/...``): ``<python> -B -m pip freeze --all``,
  run inside ``bwrap`` with the whole file system read-only, no network and no device nodes but the basic ones (so the
  run cannot write anything and cannot reach a GPU); environment ``PIP_CONFIG_FILE=/dev/null``,
  ``PIP_DISABLE_PIP_VERSION_CHECK=1``, ``PIP_NO_CACHE_DIR=1``, ``PYTHONDONTWRITEBYTECODE=1``, ``HOME`` kept (the user
  site of that Python version belongs to the environment as the research code sees it);
* ``original_venv`` (``<source>/application/MBNAS_server/.venv`` of the read-only original checkout): never executed;
  its freeze is derived from the metadata files only (``Name``/``Version`` of every ``*.dist-info/METADATA`` and
  ``*.egg-info``). The same metadata method is applied to the three executable environments and cross-checked against
  their pip freeze, which validates it;
* ``project`` (``runtime/venv``): ``pip freeze --all`` against ``constraints.txt`` (equal / different / unpinned /
  missing / editable) and ``pip check``.

Outputs in ``--out-dir``: ``freeze_<env>.txt`` (raw pip output; userinfo of URLs redacted, count reported),
``freeze_original_venv.metadata.txt``, ``env_baseline_<tag>.json``; a text summary on stdout.
Exit: 0 = every present research environment frozen and the project venv equals constraints.txt; 1 = otherwise;
2 = usage error.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOME = Path.home()
KST = datetime.timezone(datetime.timedelta(hours=9))
RE_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/@\s]+)@", re.I)
RE_NORM = re.compile(r"[-_.]+")


def norm(name: str) -> str:
    return RE_NORM.sub("-", name).lower()


def canon_version(v: str) -> str:
    """PEP 440 normal form, as pip prints it (METADATA may hold e.g. ``0.1.1-2209072238`` for ``0.1.1.post2209072238``)."""
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover - packaging is in the project venv
        return v
    try:
        return str(Version(v))
    except InvalidVersion:
        return v


def iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, KST).isoformat(timespec="seconds")


def sandboxed(cmd: list[str]) -> list[str]:
    """Read-only, offline, no-GPU sandbox (bwrap); the plain command when bwrap is missing (reported)."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return cmd
    return [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--unshare-net",
            "--unshare-pid", "--die-with-parent", *cmd]


def run_py(python: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(HOME), "LANG": "C.UTF-8", "PIP_CONFIG_FILE": "/dev/null",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1", "PIP_NO_INPUT": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "CUDA_VISIBLE_DEVICES": ""}
    return subprocess.run(sandboxed([str(python), "-B", *args]), capture_output=True, text=True, env=env, timeout=600)


def redact(text: str) -> tuple[str, int]:
    out, n = RE_URL_USERINFO.subn(r"\1<redacted>@", text)
    return out, n


def pyvenv_cfg(prefix: Path) -> dict[str, str]:
    cfg = prefix / "pyvenv.cfg"
    if not cfg.is_file():
        return {}
    out = {}
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def dist_metadata(site: Path) -> list[tuple[str, str, str, float]]:
    """(name, version, entry, mtime) of every dist-info / egg-info directly in ``site`` (files only read)."""
    out = []
    try:
        entries = sorted(os.scandir(site), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if e.name.endswith(".dist-info") and e.is_dir(follow_symlinks=False):
            meta = Path(e.path) / "METADATA"
        elif e.name.endswith(".egg-info"):
            meta = Path(e.path) / "PKG-INFO" if e.is_dir(follow_symlinks=False) else Path(e.path)
        else:
            continue
        name = version = None
        try:
            with open(meta, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        break
                    if line.startswith("Name:") and name is None:
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("Version:") and version is None:
                        version = line.split(":", 1)[1].strip()
        except OSError:
            pass
        out.append((name or e.name, version or "?", e.name, e.stat(follow_symlinks=False).st_mtime))
    return out


def dir_times(path: Path, start: float) -> dict:
    """mtime of ``path``, its top-level entries (lstat), the newest one and the entries newer than ``start``."""
    if not path.is_dir():
        return {"path": str(path), "exists": False}
    entries = []
    with os.scandir(path) as it:
        for e in it:
            entries.append((e.name, e.stat(follow_symlinks=False).st_mtime))
    newest = max(entries, key=lambda x: x[1]) if entries else (None, 0.0)
    newer = sorted([(n, iso(t)) for n, t in entries if t > start], key=lambda x: x[1])
    return {"path": str(path), "exists": True, "mtime": iso(path.stat().st_mtime), "dir_newer_than_project_start":
            path.stat().st_mtime > start, "entries": len(entries), "newest_entry": newest[0],
            "newest_entry_mtime": iso(newest[1]) if newest[0] else None, "newer_than_project_start": newer}


def parse_freeze(text: str) -> dict:
    pins, urls, editable, other = {}, {}, [], []
    pending_editable = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"# Editable install with no version control \((.+?)==(.+)\)", s)
        if m:
            pending_editable = (m.group(1), m.group(2))
            continue
        if s.startswith("#"):
            continue
        if s.startswith("-e "):
            editable.append({"name": pending_editable[0] if pending_editable else None,
                             "version": pending_editable[1] if pending_editable else None, "location": s[3:].strip()})
            pending_editable = None
            continue
        if "==" in s:
            n, v = s.split("==", 1)
            pins[norm(n)] = v.strip()
        elif " @ " in s:
            n, u = s.split(" @ ", 1)
            urls[norm(n)] = u.strip()
        else:
            other.append(s)
    return {"pins": pins, "urls": urls, "editable": editable, "other": other}


def env_record(name: str, prefix: Path, start: float, out_dir: Path, executable: bool) -> dict:
    rec: dict = {"env": name, "prefix": str(prefix), "exists": prefix.is_dir()}
    if not rec["exists"]:
        return rec
    python = prefix / "bin" / "python"
    rec["pyvenv_cfg"] = pyvenv_cfg(prefix)
    sites: list[Path] = []
    if executable:
        probe = run_py(python, ["-c", "import json,site,sys;print(json.dumps({'version':sys.version.split()[0],'prefix':sys.prefix,"
                                      "'base_prefix':sys.base_prefix,'path':sys.path,'user_site':site.getusersitepackages(),"
                                      "'user_site_enabled':bool(site.ENABLE_USER_SITE)}))"])
        rec["probe_exit"] = probe.returncode
        if probe.returncode == 0:
            info = json.loads(probe.stdout.strip().splitlines()[-1])
            rec.update({"python_version": info["version"], "base_prefix": info["base_prefix"],
                        "user_site": info["user_site"], "user_site_enabled": info["user_site_enabled"]})
            sites = [Path(p) for p in info["path"] if p.endswith("site-packages") and Path(p).is_dir()]
        freeze = run_py(python, ["-m", "pip", "freeze", "--all"])
        rec["freeze_exit"] = freeze.returncode
        text, n_red = redact(freeze.stdout)
        rec["freeze_redactions"] = n_red
        if freeze.returncode != 0:
            rec["freeze_stderr_tail"] = freeze.stderr[-500:]
        fpath = out_dir / f"freeze_{name}.txt"
        fpath.write_text(text, encoding="utf-8")
        rec["freeze_file"] = fpath.name
        rec["freeze_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec["freeze_lines"] = len([l for l in text.splitlines() if l.strip()])
        parsed = parse_freeze(text)
        rec["freeze_counts"] = {"pinned": len(parsed["pins"]), "url": len(parsed["urls"]), "editable": len(parsed["editable"]),
                                "other": len(parsed["other"])}
    else:
        cfg = rec["pyvenv_cfg"]
        rec["python_version"] = cfg.get("version") or cfg.get("version_info")
        sites = sorted((prefix / "lib").glob("python*/site-packages"))
        if cfg.get("include-system-site-packages", "false").lower() == "true":
            rec["note"] = "include-system-site-packages = true: base site-packages not derived (not executed)"
        parsed = None
    # metadata-derived freeze over the site dirs in sys.path order (first occurrence of a name wins)
    meta: dict[str, tuple[str, str, str]] = {}
    rec["site_dirs"] = []
    for site in sites:
        dists = dist_metadata(site)
        for n, v, entry, _t in dists:
            meta.setdefault(norm(n), (n, canon_version(v), str(site)))
        newest = max(dists, key=lambda d: d[3]) if dists else None
        rec["site_dirs"].append({**dir_times(site, start), "dists": len(dists),
                                 "newest_dist": newest[2] if newest else None,
                                 "newest_dist_mtime": iso(newest[3]) if newest else None,
                                 "dists_newer_than_project_start": sorted(d[2] for d in dists if d[3] > start),
                                 "dist_mtimes": [[d[2], iso(d[3])] for d in dists]})
    rec["bin"] = dir_times(prefix / "bin", start)
    meta_lines = sorted(f"{n}=={v}" for n, v, _s in meta.values())
    if executable and parsed is not None:
        pip_names = set(parsed["pins"]) | set(parsed["urls"]) | {norm(e["name"]) for e in parsed["editable"] if e["name"]}
        mismatch = sorted(n for n in pip_names ^ set(meta) )
        vdiff = sorted(n for n, v in parsed["pins"].items() if n in meta and meta[n][1] != canon_version(v))
        rec["metadata_crosscheck"] = {"pip_names": len(pip_names), "metadata_names": len(meta), "name_mismatch": mismatch,
                                      "version_mismatch": vdiff, "agrees": not mismatch and not vdiff}
    else:
        mpath = out_dir / f"freeze_{name}.metadata.txt"
        body = "\n".join(meta_lines) + "\n"
        mpath.write_text(f"# metadata-derived freeze of {prefix} (not executed; files read only; versions in PEP 440 normal form); "
                         f"site dirs: {[str(s) for s in sites]}\n" + body, encoding="utf-8")
        rec["freeze_file"] = mpath.name
        rec["freeze_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        rec["freeze_lines"] = len(meta_lines)
    newer = [s["dists_newer_than_project_start"] for s in rec["site_dirs"]]
    rec["unchanged_since_project_start_by_mtime"] = (not any(newer) and not rec["bin"].get("newer_than_project_start")
                                                     and not any(s.get("newer_than_project_start") for s in rec["site_dirs"]))
    return rec


def project_check(root: Path, out_dir: Path) -> dict:
    python = root / "runtime" / "venv" / "bin" / "python"
    freeze = run_py(python, ["-m", "pip", "freeze", "--all"])
    text, n_red = redact(freeze.stdout)
    (out_dir / "freeze_project_runtime_venv.txt").write_text(text, encoding="utf-8")
    parsed = parse_freeze(text)
    cons = {}
    for line in (root / "constraints.txt").read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if "==" in s:
            n, v = s.split("==", 1)
            cons[norm(n)] = v.strip()
    equal = sorted(n for n in cons if parsed["pins"].get(n) == cons[n])
    different = sorted(n for n in cons if n in parsed["pins"] and parsed["pins"][n] != cons[n])
    missing = sorted(n for n in cons if n not in parsed["pins"])
    unpinned = sorted(n for n in parsed["pins"] if n not in cons)
    check = run_py(python, ["-m", "pip", "check"])
    cfg = pyvenv_cfg(root / "runtime" / "venv")
    return {"python": str(python.relative_to(root)), "pyvenv_cfg": cfg, "freeze_exit": freeze.returncode,
            "freeze_file": "freeze_project_runtime_venv.txt", "freeze_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "freeze_redactions": n_red, "constraints_pins": len(cons), "equal": len(equal), "different": different,
            "missing": missing, "unpinned": unpinned, "url_installs": sorted(parsed["urls"]),
            "editable": [{"name": e["name"], "version": e["version"],
                          "location": os.path.relpath(e["location"], root) if e["location"].startswith("/") else e["location"]}
                         for e in parsed["editable"]],
            "pip_check_exit": check.returncode, "pip_check": (check.stdout + check.stderr).strip()[-500:],
            "system_site_false": cfg.get("include-system-site-packages") == "false",
            "ok": freeze.returncode == 0 and len(equal) == len(cons) and not different and not missing and not unpinned
            and check.returncode == 0 and cfg.get("include-system-site-packages") == "false"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    conda = HOME / "miniconda3" / "envs"
    ap.add_argument("--sg26", default=str(HOME / "venvs" / "sg26"))
    ap.add_argument("--sg", default=str(conda / "sg"))
    ap.add_argument("--mbnas310", default=str(conda / "mbnas310"))
    ap.add_argument("--source-root", help="the read-only original checkout (its application/MBNAS_server/.venv)")
    ap.add_argument("--project-start", default="2026-10-23T20:00:00+09:00")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = datetime.datetime.fromisoformat(args.project_start).timestamp()
    tag = datetime.datetime.now(KST).strftime("%Y%m%dT%H%M%S")
    envs = [env_record("sg26", Path(args.sg26), start, out_dir, True), env_record("sg", Path(args.sg), start, out_dir, True),
            env_record("mbnas310", Path(args.mbnas310), start, out_dir, True)]
    if args.source_root:
        envs.append(env_record("original_venv", Path(args.source_root) / "application" / "MBNAS_server" / ".venv", start, out_dir,
                               False))
    proj = project_check(ROOT, out_dir)
    report = {"schema": "mbnas.env_freeze_baseline/1", "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"),
              "command": ["python", "-I", "-B", "docs/internal/evidence/ENV/env_freeze_baseline.py",
                          *(argv if argv is not None else sys.argv[1:])],
              "sandbox": "bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --unshare-net --unshare-pid" if shutil.which("bwrap")
              else "none (bwrap missing)", "project_start": args.project_start,
              "note": "no freeze of the research environments exists from before the project start; this is the first "
                      "baseline; 'unchanged since the project start' is judged from mtimes (site dirs, their top-level "
                      "entries, dist-info/egg-info, bin/)",
              "environments": envs, "project_runtime_venv": proj}
    research_ok = all((not e["exists"]) or e.get("freeze_exit", 0) == 0 for e in envs)
    report["ok"] = research_ok and proj["ok"]
    jpath = out_dir / f"env_baseline_{tag}.json"
    jpath.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"report {jpath}")
    for e in envs:
        if not e["exists"]:
            print(f"{e['env']}: absent ({e['prefix']})")
            continue
        sd = "; ".join(f"{s['path']}: {s.get('dists')} dists, newest {s.get('newest_dist')} {s.get('newest_dist_mtime')}, "
                       f"newer than start {len(s.get('dists_newer_than_project_start') or [])}/{len(s.get('newer_than_project_start') or [])}"
                       for s in e["site_dirs"])
        print(f"{e['env']}: py {e.get('python_version')} freeze {e.get('freeze_file')} lines {e.get('freeze_lines')} sha256 "
              f"{e.get('freeze_sha256', '')[:16]}... redactions {e.get('freeze_redactions', 0)}; crosscheck "
              f"{e.get('metadata_crosscheck', {}).get('agrees', 'n/a')}; bin newest {e['bin'].get('newest_entry_mtime')} "
              f"(newer than start {len(e['bin'].get('newer_than_project_start') or [])}); unchanged by mtime "
              f"{e['unchanged_since_project_start_by_mtime']}\n    {sd}")
    print(f"project runtime/venv: constraints {proj['constraints_pins']}, equal {proj['equal']}, different {len(proj['different'])}, "
          f"missing {len(proj['missing'])}, unpinned {len(proj['unpinned'])}, editable {len(proj['editable'])}, "
          f"system-site false {proj['system_site_false']}, pip check exit {proj['pip_check_exit']} ({proj['pip_check'][:60]})")
    print(f"ok {report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
