#!/usr/bin/env python3
"""T8 leak scanner: MBNAS canary tokens, host facts and extra secret needles (SECURITY_BASELINE §5, WP-7 contract §3).

Targets: ZIP / wheel files, directories, single files, or ``-`` (text on stdin, e.g. a saved API response body).
Containers are opened recursively (zip family by magic bytes, gzip, tar; at most 4 levels, bounded sizes); every
other file is scanned as raw bytes (sqlite files included). Symbolic links are listed, never followed.

Needles

* ``pattern``: the generic token shape ``MBNAS-CANARY-<AREA>-<16 hex>`` (regular expression on raw bytes) plus the
  hex / base64 (standard and urlsafe, three byte alignments) / UTF-16LE forms of the token prefix: found without a
  manifest;
* ``canary``: every token of ``--manifest`` (default layout ``docs/internal/canary/CANARY_MANIFEST.json``, written by
  ``canary_plant.py``) in the variants raw, hex, HEX, base64 / base64url at the three alignments, UTF-16LE and
  URL-encoded;
* ``host_fact``: the fixed host facts of SECURITY_BASELINE §5.2 plus the original host's GPU UUID (C05 D8), stored
  base64-encoded below so that this file does not contain them; ``--no-host-facts`` disables them;
* ``extra``: lines of ``--needles-file`` and the values of secret-like keys of ``--secrets-env`` (a dotenv file).
  Their values are never printed: a hit names the needle by label only.

Verdict

* without ``--profile`` every hit fails: "any token anywhere" (exit 1 with the locations);
* with ``--profile owner|public|registration`` (needs ``--manifest``): a manifest token fails when the profile is in
  the area's ``must_not_in`` or not in its ``expected_in``; a pattern hit that is no manifest token fails; host facts
  fail for public/registration and are informational for owner; extra needles always fail. ``--expect-present``
  also fails when an area whose ``expected_in`` holds the profile has no hit (positive control: proves the scanner
  sees the planted markers inside the bundle, nested wheels included);
* ``--min-files N`` fails when fewer than N files (container members included) were scanned.

Exit status: 0 PASS, 1 FAIL, 2 usage or I/O error. Output: a summary on stdout (one line per hit: verdict, needle
label, variant, location ``target!member:line``) and with ``--json OUT`` the report (schema ``mbnas.canary_scan/1``).
Importable: ``load_manifest``, ``build_needles``, ``scan_targets``, ``token_variants``, ``HOST_FACT_LABELS``.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

SCHEMA = "mbnas.canary_scan/1"
PROFILES = ("owner", "public", "registration")
PREFIX = "MBNAS-" + "CANARY-"  # composed: this file never holds the token prefix as one literal
TOKEN_RE = re.compile(re.escape(PREFIX).encode() + rb"[A-Z][A-Z0-9_]{0,63}-[0-9a-f]{16}")
# SECURITY_BASELINE §5.2 HOST_FACT (fixed needles, not markers), base64 so the scanner itself is clean; #6 (C05 D8) is
# the original host's GPU UUID without the "GPU-" prefix (a hardware identifier: matches with and without the prefix)
_HOST_FACTS_B64 = ("L2RhdGEvbWdqZW9uZw==", "L3Jvb3QvZGV0ZWN0aW9u", "MTQ3LjQ2LjExNC4yNTM=", "cGMyMzAw", "bWdqZW9uZw==",
                   "ZGVkM2EwODYtNWMyZi1mMzA1LWU0ZDUtNGExOGYyNzJiOTA1")
HOST_FACT_LABELS = ("HOST_FACT#1 owner data root", "HOST_FACT#2 original host root",
                    "HOST_FACT#3 original host address", "HOST_FACT#4 original host name",
                    "HOST_FACT#5 owner account name", "HOST_FACT#6 original host GPU UUID")
SECRET_KEY_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|TICKET_KEY|SIGNING_KEY|PRIVATE_KEY)", re.I)
MAX_DEPTH = 4
MAX_MEMBER_BYTES = 1 << 30      # one container member
MAX_TOTAL_BYTES = 8 << 30       # everything unpacked below one top-level target
MAX_HITS_PER_NEEDLE_PER_BLOB = 20


def host_facts() -> tuple[bytes, ...]:
    return tuple(base64.b64decode(x) for x in _HOST_FACTS_B64)


def _b64_fragments(raw: bytes) -> list[tuple[str, bytes]]:
    """Base64 text that encodes ``raw`` whatever precedes it: one fragment per byte alignment (0, 1, 2)."""
    out = []
    for k in range(3):
        enc = base64.b64encode(b"\0" * k + raw)
        start, end = -(-8 * k // 6), (8 * (k + len(raw))) // 6
        frag = enc[start:end]
        out.append((f"base64@{k}", frag))
        out.append((f"base64url@{k}", frag.replace(b"+", b"-").replace(b"/", b"_")))
    return out


def token_variants(token: str, include_raw: bool = True) -> list[tuple[str, bytes]]:
    """(variant, bytes) forms of ``token`` that the scanner looks for; a form equal to an earlier one (e.g. the
    URL-encoding of a URL-safe token, which equals the raw token) is dropped, also when the raw form is not returned."""
    raw = token.encode()
    items = [("raw", raw), ("hex", raw.hex().encode()), ("HEX", raw.hex().upper().encode()),
             ("utf16le", token.encode("utf-16-le")), ("urlencoded", quote(token, safe="").encode())]
    items += _b64_fragments(raw)
    seen: set[bytes] = set()
    out = []
    for name, data in items:
        if data and data not in seen:
            seen.add(data)
            if name != "raw" or include_raw:
                out.append((name, data))
    return out


@dataclass(frozen=True)
class Needle:
    label: str          # area name, HOST_FACT#n ..., NEEDLE#n, SECRET:<KEY>, PATTERN
    kind: str           # canary | pattern | host_fact | extra
    variant: str
    data: bytes
    area: str | None = None


@dataclass
class NeedleSet:
    literals: list[Needle]
    tokens: dict[bytes, str]            # raw manifest token -> area (raw form is matched through TOKEN_RE)
    areas: dict[str, dict]              # manifest areas by name
    host_facts_enabled: bool
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    _regex: Any = field(default=None, repr=False)
    _by_bytes: dict[bytes, list[Needle]] = field(default_factory=dict, repr=False)

    def compile(self) -> "NeedleSet":
        self._by_bytes = {}
        for n in self.literals:
            self._by_bytes.setdefault(n.data, []).append(n)
        alts = sorted(self._by_bytes, key=len, reverse=True)
        self._regex = re.compile(b"|".join(re.escape(a) for a in alts)) if alts else None
        return self


def load_manifest(path: str | os.PathLike) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != "mbnas.canary_manifest/1" or not isinstance(doc.get("areas"), list):
        raise ValueError(f"{path}: not a canary manifest (schema mbnas.canary_manifest/1)")
    for a in doc["areas"]:
        if not TOKEN_RE.fullmatch(str(a.get("value", "")).encode()):
            raise ValueError(f"{path}: area {a.get('area')!r} has no valid token value")
    return doc


def _dotenv_secret_values(path: str | os.PathLike) -> list[tuple[str, bytes]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if SECRET_KEY_RE.search(key) and len(value) >= 12:
            out.append((f"SECRET:{key}", value.encode()))
    return out


def build_needles(manifest: dict | None = None, *, host_facts_enabled: bool = True,
                  extra: Iterable[tuple[str, bytes]] = (), manifest_path: str | None = None) -> NeedleSet:
    literals: list[Needle] = []
    tokens: dict[bytes, str] = {}
    areas: dict[str, dict] = {}
    for variant, data in token_variants(PREFIX, include_raw=False):
        literals.append(Needle("PATTERN", "pattern", f"prefix-{variant}", data))
    for a in (manifest or {}).get("areas", []):
        areas[a["area"]] = a
        tokens[a["value"].encode()] = a["area"]
        for variant, data in token_variants(a["value"], include_raw=False):
            literals.append(Needle(a["area"], "canary", variant, data, a["area"]))
    if host_facts_enabled:
        for label, data in zip(HOST_FACT_LABELS, host_facts()):
            literals.append(Needle(label, "host_fact", "raw", data))
    for label, data in extra:
        if data:
            literals.append(Needle(label, "extra", "raw", data))
            encoded = quote(data.decode("utf-8", "replace"), safe="").encode()
            literals.append(Needle(label, "extra", "urlencoded", encoded))
    sha = None
    if manifest_path:
        sha = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
    return NeedleSet(literals, tokens, areas, host_facts_enabled, manifest_path, sha).compile()


@dataclass
class Hit:
    label: str
    kind: str
    variant: str
    location: str
    area: str | None = None
    failing: bool = True

    def as_dict(self) -> dict:
        return {"label": self.label, "kind": self.kind, "variant": self.variant, "location": self.location,
                "area": self.area, "failing": self.failing}


@dataclass
class ScanState:
    needles: NeedleSet
    files: int = 0
    bytes: int = 0
    containers: int = 0
    skipped: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    budget: int = MAX_TOTAL_BYTES


def _line_of(data: bytes, pos: int) -> str:
    if b"\0" in data[:8192]:
        return f"@{pos}"
    line = data.count(b"\n", 0, pos) + 1
    return f":{line}"


def _match_blob(name: str, data: bytes, st: ScanState) -> None:
    ns = st.needles
    spans: list[tuple[int, int]] = []
    found: list[tuple[int, int, Hit]] = []
    counts: dict[tuple[str, str], int] = {}

    def add(start: int, end: int, hit: Hit) -> None:
        key = (hit.label, hit.variant)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= MAX_HITS_PER_NEEDLE_PER_BLOB:
            found.append((start, end, hit))

    for m in TOKEN_RE.finditer(data):
        area = ns.tokens.get(m.group(0))
        loc = name + _line_of(data, m.start())
        if area is not None:
            add(m.start(), m.end(), Hit(area, "canary", "raw", loc, area))
            spans.append((m.start(), m.end()))
        else:
            add(m.start(), m.end(), Hit("PATTERN", "pattern", "token-shape", loc))
    if ns._regex is not None:
        for m in ns._regex.finditer(data):
            for n in ns._by_bytes.get(m.group(0), []):
                add(m.start(), m.end(), Hit(n.label, n.kind, n.variant, name + _line_of(data, m.start()), n.area))
                if n.kind == "canary":
                    spans.append((m.start(), m.end()))
    for start, end, hit in found:
        # a prefix variant inside a known manifest token variant is that token, not an unknown one
        if hit.kind == "pattern" and hit.variant.startswith("prefix-") and any(s < end and start < e for s, e in spans):
            continue
        st.hits.append(hit)


def _scan_blob(name: str, data: bytes, depth: int, st: ScanState) -> None:
    st.files += 1
    st.bytes += len(data)
    _match_blob(name, data, st)
    if depth >= MAX_DEPTH:
        return
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError):
            return
        st.containers += 1
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > MAX_MEMBER_BYTES or info.file_size > st.budget:
                    st.skipped.append(f"{name}!{info.filename}: too large ({info.file_size} bytes)")
                    continue
                try:
                    member = zf.read(info)
                except (zipfile.BadZipFile, RuntimeError, ValueError, OSError) as exc:  # encrypted / corrupt member
                    st.skipped.append(f"{name}!{info.filename}: unreadable ({type(exc).__name__})")
                    continue
                st.budget -= len(member)
                _scan_blob(f"{name}!{info.filename}", member, depth + 1, st)
    elif data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                inner = gz.read(min(MAX_MEMBER_BYTES, st.budget) + 1)
        except (OSError, EOFError):
            return
        if len(inner) > min(MAX_MEMBER_BYTES, st.budget):
            st.skipped.append(f"{name}: gzip payload too large")
            return
        st.containers += 1
        st.budget -= len(inner)
        _scan_blob(f"{name}!gunzip", inner, depth + 1, st)
    elif len(data) > 512 and data[257:262] == b"ustar":
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
        except tarfile.TarError:
            return
        st.containers += 1
        with tf:
            for ti in tf.getmembers():
                if not ti.isfile():
                    continue
                if ti.size > MAX_MEMBER_BYTES or ti.size > st.budget:
                    st.skipped.append(f"{name}!{ti.name}: too large ({ti.size} bytes)")
                    continue
                fh = tf.extractfile(ti)
                member = fh.read() if fh else b""
                st.budget -= len(member)
                _scan_blob(f"{name}!{ti.name}", member, depth + 1, st)


def _scan_path(target: str, st: ScanState) -> None:
    if target == "-":
        _scan_blob("<stdin>", sys.stdin.buffer.read(), 0, st)
        return
    p = Path(target)
    if p.is_symlink():
        st.skipped.append(f"{target}: symbolic link not followed")
        return
    if p.is_file():
        st.budget = MAX_TOTAL_BYTES
        _scan_blob(target, p.read_bytes(), 0, st)
        return
    if not p.is_dir():
        raise FileNotFoundError(target)
    for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
        dirnames.sort()
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                st.skipped.append(f"{os.path.join(dirpath, d)}: symbolic link not followed")
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            mode = os.lstat(fp).st_mode
            if stat.S_ISLNK(mode):
                st.skipped.append(f"{fp}: symbolic link not followed")
                continue
            if not stat.S_ISREG(mode):
                st.skipped.append(f"{fp}: not a regular file")
                continue
            st.budget = MAX_TOTAL_BYTES
            with open(fp, "rb") as fh:
                _scan_blob(fp, fh.read(), 0, st)


def _judge(st: ScanState, profile: str | None, expect_present: bool) -> list[dict]:
    areas = st.needles.areas
    for h in st.hits:
        if h.kind == "extra" or h.kind == "pattern":
            h.failing = True
        elif h.kind == "host_fact":
            h.failing = profile != "owner"
        elif h.kind == "canary":
            if profile is None:
                h.failing = True
            else:
                a = areas.get(h.area or "", {})
                h.failing = profile in a.get("must_not_in", []) or profile not in a.get("expected_in", [])
    expectations = []
    if expect_present and profile is not None:
        for name, a in sorted(areas.items()):
            if profile in a.get("expected_in", []):
                n = sum(1 for h in st.hits if h.kind == "canary" and h.area == name)
                expectations.append({"area": name, "profile": profile, "hits": n, "ok": n > 0})
    return expectations


def scan_targets(targets: list[str], needles: NeedleSet, *, profile: str | None = None,
                 expect_present: bool = False, min_files: int = 1) -> dict:
    """Scan ``targets`` and return the report document (``status`` PASS/FAIL)."""
    if profile is not None and profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    st = ScanState(needles)
    for t in targets:
        _scan_path(t, st)
    expectations = _judge(st, profile, expect_present)
    failing = [h for h in st.hits if h.failing]
    unmet = [e for e in expectations if not e["ok"]]
    too_few = st.files < min_files
    by_label: dict[str, int] = {}
    for h in st.hits:
        by_label[h.label] = by_label.get(h.label, 0) + 1
    return {
        "schema": SCHEMA, "targets": list(targets), "profile": profile, "expect_present": expect_present,
        "manifest": ({"path": needles.manifest_path, "sha256": needles.manifest_sha256,
                      "areas": sorted(needles.areas)} if needles.manifest_path else None),
        "host_facts": needles.host_facts_enabled,
        "needle_count": len(needles.literals) + len(needles.tokens) + 1,
        "scanned": {"files": st.files, "bytes": st.bytes, "containers": st.containers, "min_files": min_files,
                    "skipped": st.skipped},
        "hits": [h.as_dict() for h in st.hits], "hits_by_label": dict(sorted(by_label.items())),
        "expectations": expectations,
        "failing_hits": len(failing), "unmet_expectations": len(unmet), "too_few_files": too_few,
        "status": "FAIL" if (failing or unmet or too_few) else "PASS",
    }


def _short(location: str, targets: list[str]) -> str:
    for t in targets:
        if t not in ("-",) and location.startswith(t):
            return os.path.basename(t.rstrip(os.sep)) + location[len(t):]
    return location


def format_summary(report: dict, limit: int = 200) -> str:
    """Failing hits one per line (up to ``limit``); non-failing hits as counts per needle label."""
    lines = []
    failing = [h for h in report["hits"] if h["failing"]]
    for h in failing[:limit]:
        lines.append(f"FAIL {h['kind']:<9} {h['label']} [{h['variant']}] {_short(h['location'], report['targets'])}")
    if len(failing) > limit:
        lines.append(f"... {len(failing) - limit} more failing hits (see --json)")
    ok: dict[tuple[str, str], int] = {}
    for h in report["hits"]:
        if not h["failing"]:
            ok[(h["kind"], h["label"])] = ok.get((h["kind"], h["label"]), 0) + 1
    for (kind, label), n in sorted(ok.items()):
        lines.append(f"ok   {kind:<9} {label}: {n} hit(s) (not failing for profile {report['profile']})")
    for e in report["expectations"]:
        verdict = "ok  " if e["ok"] else "FAIL"
        lines.append(f"{verdict} expect   {e['area']} present in {e['profile']}: {e['hits']} hit(s)")
    sc = report["scanned"]
    lines.append(f"scanned files={sc['files']} bytes={sc['bytes']} containers={sc['containers']} "
                 f"skipped={len(sc['skipped'])} hits={len(report['hits'])} failing={report['failing_hits']} "
                 f"unmet={report['unmet_expectations']}{' too_few_files' if report['too_few_files'] else ''}")
    lines.append(f"canary_scan: {report['status']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="+", help="zip/whl file, directory, file, or - for stdin")
    ap.add_argument("--manifest", help="canary manifest (CANARY_MANIFEST.json)")
    ap.add_argument("--profile", choices=PROFILES, help="judge hits for this bundle profile (needs --manifest)")
    ap.add_argument("--expect-present", action="store_true",
                    help="with --profile: every area expected in the profile must be found (positive control)")
    ap.add_argument("--no-host-facts", action="store_true", help="do not look for the fixed host facts")
    ap.add_argument("--needles-file", help="extra needles, one per line (values are never printed)")
    ap.add_argument("--secrets-env", help="dotenv file whose secret-like values are needles (never printed)")
    ap.add_argument("--min-files", type=int, default=1, help="fail when fewer files were scanned (default 1)")
    ap.add_argument("--json", dest="json_out", help="write the report document here")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict line")
    a = ap.parse_args(argv)
    if (a.profile or a.expect_present) and not a.manifest:
        ap.error("--profile/--expect-present need --manifest")
    if a.expect_present and not a.profile:
        ap.error("--expect-present needs --profile")
    try:
        manifest = load_manifest(a.manifest) if a.manifest else None
        extra: list[tuple[str, bytes]] = []
        if a.needles_file:
            lines = Path(a.needles_file).read_text(encoding="utf-8").splitlines()
            extra += [(f"NEEDLE#{i}", ln.strip().encode()) for i, ln in enumerate(lines, 1)
                      if ln.strip() and not ln.lstrip().startswith("#")]
        if a.secrets_env:
            extra += _dotenv_secret_values(a.secrets_env)
        needles = build_needles(manifest, host_facts_enabled=not a.no_host_facts, extra=extra,
                                manifest_path=a.manifest)
        report = scan_targets(a.targets, needles, profile=a.profile, expect_present=a.expect_present,
                              min_files=a.min_files)
    except (OSError, ValueError) as exc:
        print(f"canary_scan: error: {exc}", file=sys.stderr)
        return 2
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    text = format_summary(report)
    print(text.splitlines()[-1] if a.quiet else text)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
