#!/usr/bin/env python3
"""Generate ``THIRD_PARTY_NOTICES_WEB.md``: the npm packages of the web console (DD-A65, C11 D8).

Sources, read only:

* ``mbnas_web/package-lock.json`` (lockfileVersion 2 or 3, the ``packages`` map: name, version, license and the
  dev / optional / peer flags), and
* for every package that is installed, ``mbnas_web/node_modules/<path>/package.json`` (``license``, or the legacy
  ``licenses`` list / ``{type}`` object).

A licence that neither source states is written as ``UNKNOWN``. When both sources state one and they differ, both are
shown. Nothing is fetched from the network and no clock is read, so the output is a pure function of the inputs. The
values are package metadata, not a legal review (see ``LICENSE`` and the rights review of the registration package).

Groups in the output:

* ``runtime``: the production dependency closure of ``mbnas_web`` (lockfile entries without a dev flag). Vite bundles
  into ``mbnas_web/dist`` only the modules of this group that the console imports.
* ``development``: build and test tools (lockfile ``dev``). They build and test the console and are not part of
  ``mbnas_web/dist``. Optional platform binaries for other systems are listed as "not installed".

Usage::

  runtime/venv/bin/python -I -B scripts/dev/gen_npm_notices.py            # write THIRD_PARTY_NOTICES_WEB.md
  runtime/venv/bin/python -I -B scripts/dev/gen_npm_notices.py --check    # write nothing; exit 1 on drift

Options: ``--root <tree>`` (default: this repository), ``--out <file>`` (default: ``<root>/THIRD_PARTY_NOTICES_WEB.md``).
Exit codes: 0 ok; 1 ``--check`` found a missing or different file; 2 input error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
OUT_NAME = "THIRD_PARTY_NOTICES_WEB.md"
WEB_DIR = "mbnas_web"
UNKNOWN = "UNKNOWN"


def _licence_value(meta: object) -> str | None:
    """Normalise an npm licence field: a string, a legacy {type} object or a legacy list of either."""
    if isinstance(meta, str):
        return meta.strip() or None
    if isinstance(meta, dict):
        return _licence_value(meta.get("type"))
    if isinstance(meta, list):
        parts = [p for p in (_licence_value(x) for x in meta) if p]
        return " OR ".join(parts) if parts else None
    return None


def _package_json_licence(pkg: dict) -> str | None:
    return _licence_value(pkg.get("license")) or _licence_value(pkg.get("licenses"))


def _name_from_path(path: str) -> str:
    return path.rsplit("node_modules/", 1)[-1]


def _md(text: str) -> str:
    return text.replace("|", "\\|")


def collect(root: Path) -> dict:
    web = root / WEB_DIR
    lock_path = web / "package-lock.json"
    try:
        raw = lock_path.read_bytes()
        lock = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot read {lock_path.relative_to(root)}: {exc}") from None
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise SystemExit("error: package-lock.json has no 'packages' map (lockfileVersion 2 or 3 required)")
    rows = []
    for path in sorted(p for p in packages if p):
        entry = packages[path]
        if entry.get("link"):
            continue
        name = entry.get("name") or _name_from_path(path)
        version = entry.get("version") or UNKNOWN
        lock_lic = _licence_value(entry.get("license"))
        pj_path = web / path / "package.json"
        installed = pj_path.is_file()
        pj_lic = pj_version = None
        if installed:
            try:
                pj = json.loads(pj_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pj = {}
            pj_lic = _package_json_licence(pj)
            pj_version = pj.get("version")
        if lock_lic and pj_lic and lock_lic != pj_lic:
            licence, source = f"{lock_lic} (package.json: {pj_lic})", "lockfile ≠ package.json"
        elif lock_lic and pj_lic:
            licence, source = lock_lic, "lockfile = package.json"
        elif lock_lic:
            licence, source = lock_lic, "lockfile"
        elif pj_lic:
            licence, source = pj_lic, "package.json"
        else:
            licence, source = UNKNOWN, "none"
        flags = [f for f in ("dev", "optional", "devOptional", "peer") if entry.get(f)]
        group = "development" if ("dev" in flags or "devOptional" in flags) else "runtime"
        rows.append({"path": path, "name": name, "version": version, "licence": licence, "licence_key": lock_lic or pj_lic or UNKNOWN,
                     "source": source, "flags": flags, "group": group, "installed": installed,
                     "version_note": (f"installed {pj_version}" if installed and pj_version and pj_version != version else "")})
    root_entry = packages.get("", {})
    return {"lock_sha256": hashlib.sha256(raw).hexdigest(), "lock_version": lock.get("lockfileVersion"),
            "project": f"{root_entry.get('name', lock.get('name', WEB_DIR))} {root_entry.get('version', lock.get('version', ''))}".strip(),
            "direct_runtime": sorted((root_entry.get("dependencies") or {}).items()),
            "direct_dev": sorted((root_entry.get("devDependencies") or {}).items()), "rows": rows}


def render(data: dict) -> str:
    rows = data["rows"]
    groups = {"runtime": [r for r in rows if r["group"] == "runtime"],
              "development": [r for r in rows if r["group"] == "development"]}
    lic_all = Counter(r["licence_key"] for r in rows)
    lic_by = {g: Counter(r["licence_key"] for r in rs) for g, rs in groups.items()}
    unknown = [r for r in rows if r["licence_key"] == UNKNOWN]
    mismatch = [r for r in rows if r["source"] == "lockfile ≠ package.json"]

    def counts(c: Counter) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or "—"

    out = [
        "# THIRD_PARTY_NOTICES_WEB — npm packages of the web console",
        "",
        "Generated by `scripts/dev/gen_npm_notices.py` (do not edit by hand) from `mbnas_web/package-lock.json` "
        f"(lockfileVersion {data['lock_version']}, sha256 `{data['lock_sha256']}`) and the installed "
        "`mbnas_web/node_modules/*/package.json`. The licence values are copied from package metadata. They are not a legal "
        f"review. A licence that no source states is written as `{UNKNOWN}`.",
        "",
        f"- Web console: `{data['project']}`. Its own licence is not decided yet (see `LICENSE`).",
        "- Python packages, base images and other components: `THIRD_PARTY_NOTICES.md`.",
        "- **runtime**: the production dependency closure of the web console. Vite bundles into `mbnas_web/dist` only the "
        "modules of this group that the console imports. The minified bundle carries no licence comments, so this file is "
        "its notice. Whether the licence texts must also ship inside the bundle is an open rights-review item.",
        "- **development**: build and test tools. They build and test the console and are not part of `mbnas_web/dist`. "
        "Optional platform binaries for other systems are listed as not installed; their licence comes from the lockfile.",
        "",
        "## Summary",
        "",
        "| group | packages | installed here | licences |",
        "|---|---:|---:|---|",
    ]
    for g in ("runtime", "development"):
        rs = groups[g]
        out.append(f"| {g} | {len(rs)} | {sum(r['installed'] for r in rs)} | {_md(counts(lic_by[g]))} |")
    out.append(f"| **total** | **{len(rows)}** | **{sum(r['installed'] for r in rows)}** | {_md(counts(lic_all))} |")
    out += ["", f"- `{UNKNOWN}` licences: {len(unknown)}"
            + (" (" + ", ".join(f"`{r['name']}@{r['version']}`" for r in unknown) + ")" if unknown else "") + ".",
            f"- Lockfile and installed package.json disagree: {len(mismatch)}"
            + (" (" + ", ".join(f"`{r['name']}@{r['version']}`" for r in mismatch) + ")" if mismatch else "") + ".",
            "- Direct runtime dependencies: " + (", ".join(f"`{n}` {v}" for n, v in data["direct_runtime"]) or "—") + ".",
            "- Direct development dependencies: " + (", ".join(f"`{n}` {v}" for n, v in data["direct_dev"]) or "—") + ".",
            "", "## Licences", "", "| licence | runtime | development | total |", "|---|---:|---:|---:|"]
    for lic, n in sorted(lic_all.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| {_md(lic)} | {lic_by['runtime'].get(lic, 0)} | {lic_by['development'].get(lic, 0)} | {n} |")
    for title, g in (("1. Runtime packages", "runtime"), ("2. Development packages", "development")):
        out += ["", f"## {title}", "", "| package | version | licence | licence source | flags | installed here |",
                "|---|---|---|---|---|---|"]
        for r in groups[g]:
            inst = "yes" if r["installed"] else "no"
            if r["version_note"]:
                inst += f" ({r['version_note']})"
            out.append(f"| `{_md(r['name'])}` | {_md(r['version'])} | {_md(r['licence'])} | {r['source']} | "
                       f"{', '.join(r['flags']) or '—'} | {inst} |")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write THIRD_PARTY_NOTICES_WEB.md from mbnas_web/package-lock.json and "
                                             "node_modules/*/package.json (licence metadata only, no network).")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="tree holding mbnas_web/ (default: this repository)")
    ap.add_argument("--out", default=None, help=f"output file (default: <root>/{OUT_NAME})")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if the output file is missing or differs")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    out = Path(args.out) if args.out else root / OUT_NAME
    text = render(collect(root))
    if args.check:
        try:
            current = out.read_text(encoding="utf-8")
        except OSError:
            print(f"check: {out.name} missing", file=sys.stderr)
            return 1
        if current != text:
            print(f"check: {out.name} differs from the regenerated text", file=sys.stderr)
            return 1
        print(f"check: OK ({out.name})")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
