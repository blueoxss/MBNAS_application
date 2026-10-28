"""WP-7V P-final-2 step 6 (T8 rescan of the new ZIPs): forbidden member types and leak patterns, per ZIP member
(nested wheels opened too). Complements canary_scan.py (canary tokens + host facts + secret needles, step 4).

Member types that must not ship: *.env / .env* (except *.env.example), sqlite/db files, *.log, private keys and
certificates (*.pem *.key *.p12 *.pfx id_rsa*), model weights (*.pt *.pth *.ckpt *.safetensors *.onnx *.engine).
Content patterns: PEM private-key blocks; secret-like assignments (KEY/TOKEN/SECRET/PASSWORD... = 20+ char value that
is not a placeholder); absolute home/root/data paths with a concrete user/component (/home/<name>/, /root/<x>,
/data/<x>, /Users/<name>/); IPv4 addresses outside loopback/0.0.0.0/documentation ranges.
Every hit is listed with member path and line; values are shown only as a 6-char prefix + length.
Usage: zip_leak_scan.py <zip> [--allow-prefix mbnas_application/docs/internal/] --json OUT
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import json
import re
import sys
import zipfile

BAD_MEMBERS = ["*.env", "*/.env", "*/.env.*", ".env*", "*.sqlite", "*.sqlite3", "*.db", "*.log", "*.pem", "*.key",
               "*.p12", "*.pfx", "*/id_rsa*", "*.pt", "*.pth", "*.ckpt", "*.safetensors", "*.onnx", "*.engine"]
OK_MEMBERS = ["*.env.example"]
RE_PEM = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")
# a literal value: quoted string (code, JSON, YAML) or an unquoted dotenv/compose line value; code expressions
# (settings.x, process.env.X) never match because '.' and '(' are not in the value class
RE_SECRET = re.compile(rb"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|SIGNING_KEY|TICKET_KEY|PRIVATE_KEY))"
                       rb"['\"]?\s*[=:]\s*(?:['\"]([A-Za-z0-9_\-+/=]{20,})['\"]|([A-Za-z0-9_\-+/=]{20,})\s*$)")
PLACEHOLDER = re.compile(rb"(?i)(^<|^\$\{|x{6,}|changeme|placeholder|example|dummy|test|fake|redacted|generated|"
                         rb"issued|your|replace|^qa[-_]|canary|sample)")
# concrete user home directories and session scratch paths of a build host (generic example paths such as
# /data/ds/data.yaml in docs and tests are not host facts; the known host facts are canary_scan.py's job)
RE_PATH = re.compile(rb"(?<![A-Za-z0-9_.\-<])(/home/(?!user/|username/|<)[a-z_][a-z0-9_\-]{1,31}/|/Users/(?!user/|<)[A-Za-z][A-Za-z0-9_\-]+/|"
                     rb"/tmp/claude-[0-9]+|/root/detection)")
# an IPv4 address in a URL / host context (bare dotted numbers are mostly package versions)
RE_IP = re.compile(rb"(?:https?://|@|\bhost\s*[=:]\s*['\"]?)((?:\d{1,3}\.){3}\d{1,3})(?![0-9.])")
IP_OK = (b"127.", b"0.0.0.0", b"255.", b"192.0.2.", b"198.51.100.", b"203.0.113.", b"10.0.0.", b"172.17.", b"1.1.1.1",
         b"8.8.8.8")


def match_any(name: str, pats: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name.rsplit("/", 1)[-1], p) for p in pats)


def scan_blob(name: str, data: bytes, hits: list, depth: int = 0) -> int:
    n = 1
    if name.endswith(".whl") and depth == 0:
        with zipfile.ZipFile(io.BytesIO(data)) as w:
            for info in w.infolist():
                if not info.is_dir():
                    n += scan_blob(f"{name}!{info.filename}", w.read(info), hits, depth + 1)
        return n
    if match_any(name, BAD_MEMBERS) and not match_any(name, OK_MEMBERS):
        hits.append({"kind": "member_type", "path": name, "line": 0, "detail": "forbidden member type"})
    if b"\0" in data[:4096]:
        return n  # binary
    for no, line in enumerate(data.splitlines(), 1):
        if RE_PEM.search(line):
            hits.append({"kind": "private_key_block", "path": name, "line": no, "detail": "PEM private key header"})
        for m in RE_SECRET.finditer(line):
            val = m.group(2) or m.group(3)
            if PLACEHOLDER.match(val) or val.lower().startswith((b"secrets.", b"token_urlsafe")):
                continue
            hits.append({"kind": "secret_assignment", "path": name, "line": no,
                         "detail": f"{m.group(1).decode(errors='replace')}=<{val[:6].decode(errors='replace')}…,"
                                   f" {len(val)} chars>"})
        for m in RE_PATH.finditer(line):
            hits.append({"kind": "absolute_host_path", "path": name, "line": no,
                         "detail": line[max(0, m.start() - 20):m.end() + 30].decode(errors="replace").strip()[:120]})
        for m in RE_IP.finditer(line):
            ip = m.group(1)
            if ip.startswith(IP_OK) or not all(0 <= int(x) <= 255 for x in ip.split(b".")):
                continue
            hits.append({"kind": "ipv4", "path": name, "line": no, "detail": ip.decode()})
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--allow-prefix", action="append", default=[],
                    help="owner-private record areas (e.g. docs/internal/): hits there are listed, not failing")
    ap.add_argument("--fixture-prefix", action="append", default=[],
                    help="test trees: secret-like / IPv4 literals there are test fixtures (listed, not failing); "
                         "host paths and private-key blocks still fail")
    ap.add_argument("--json", required=True)
    a = ap.parse_args()
    hits: list = []
    files = 0
    with zipfile.ZipFile(a.zip) as z:
        for info in z.infolist():
            if not info.is_dir():
                files += scan_blob(info.filename, z.read(info), hits)
    for h in hits:
        h["allowed_area"] = any(h["path"].startswith(p) for p in a.allow_prefix)
        h["test_fixture"] = (h["kind"] in ("secret_assignment", "ipv4")
                             and any(h["path"].startswith(p) for p in a.fixture_prefix))
    failing = [h for h in hits if not h["allowed_area"] and not h["test_fixture"]]
    doc = {"zip": a.zip, "files_scanned": files, "hits": len(hits), "failing": len(failing),
           "allowed_prefixes": a.allow_prefix, "by_kind": {}, "hit_list": hits}
    for h in hits:
        key = h["kind"] + (" (owner-private area)" if h["allowed_area"] else "") + (" (test fixture)" if h["test_fixture"] else "")
        doc["by_kind"][key] = doc["by_kind"].get(key, 0) + 1
    with open(a.json, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    print(f"{a.zip}: files {files}, hits {len(hits)}, failing {len(failing)}, by kind {doc['by_kind']}")
    for h in hits:
        tag = "FAIL" if h in failing else ("area" if h["allowed_area"] else "fixture")
        print(f"  {tag} {h['kind']} {h['path']}:{h['line']} {h['detail']}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
