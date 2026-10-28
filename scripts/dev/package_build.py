#!/usr/bin/env python3
"""Build an MBNAS Research Console distribution ZIP from an allowlist.

Called by ``scripts/package.sh`` (the documented entry point); usable directly for a temporary tree (``--root``).

Pipeline (one pass)
 1. allowlist ``<root>/build/allowlists/<profile>.txt`` (sections ``[profile] [wheels] [include] [deny] [generated]
    [required] [expected] [private-reference-allow]``; glob per line, ``**`` = any number of path components, a
    trailing ``/`` = everything below; deny wins over include);
 2. select files under ``<root>`` (symbolic links and special files inside the allowlist are violations, never
    followed or copied) and copy them to ``<root>/build/staging/<profile>/mbnas_application/`` (modes normalised to
    0644/0755, ``*.sh`` executable);
 3. wheels: ``pip wheel --no-deps --no-index --no-build-isolation`` of each ``[wheels]`` package, built from a copy of
    its *staged* tree, into ``<root>/build/wheels/`` and the staging ``wheels/``; then the wheel-contents gate:
    every wheel's members (``*.dist-info`` aside) must equal the staged ``<pkg>/src`` files byte for byte, and every
    file declared in ``[tool.setuptools.package-data]`` must exist in the staged sources and in the wheel with the same
    sha256. The member listing (wheel sha256, every member with bytes and sha256, the declared package-data) goes to
    ``<root>/build/package_reports/<profile>_wheels.txt``; a profile whose allowlist sets ``wheel_listing_member``
    also ships it at that path;
 4. web: ``mbnas_web/dist/`` comes through the allowlist (prebuilt; this tool never runs npm). Missing ``index.html``
    = "web dist missing": a violation where the profile says ``web_dist = required`` unless ``--allow-missing-web``.
    A staged dist must pass the bundle hygiene gate ``node mbnas_web/scripts/check-bundle.mjs --dist <staged
    dist> --json`` (any hit, a missing script or no ``node`` on PATH = violation);
 6. checks: entry paths (absolute, ``..``, backslash, control characters), symbolic links / special files, duplicate
    and case- or Unicode-colliding entries, deny patterns, required / expected files, forbidden content (canary tokens
    and host facts via ``canary_scan.py``, private key blocks, secret-like values in env/compose/unit files), version
    consistency, and for ``private_package_check = strict`` (public, registration) the absence of the private package:
    no ``mbnas_core`` path component, no import / string module reference (``ast`` for ``.py``, staged files and wheel
    members), no requirement / entry point / wheel name of it in any text file except files listed in
    ``[private-reference-allow]`` (documented mentions in docs); remaining prose mentions are reported as ``info``;
 7. ``PACKAGE_PROFILE.json``, ``BUILD_INFO.json`` (version from ``VERSION``, build id, source tree sha256 of the
    staged packages with ``source_tree_hash.py``'s algorithm, payload sha256, time, platform, tool versions, the
    checks that ran with their result; no git) and ``FILE_MANIFEST.jsonl`` (one JSON object per file: relpath, bytes,
    sha256, role, origin, licence; sorted; excludes itself). Registration: then ``docs/registration/FILE_LIST.md`` (the
    owner-free file listing) is generated from that manifest by ``gen_registration_docs.py --only file-list``
    and the manifest is written again. Every profile: then ``PACKAGE_CONTENTS.md`` (the human-readable contents
    table: ZIP name, version, profile, build id; path, bytes, sha256 prefix, role, licence; totals per role) is
    rendered from that manifest and the manifest is written a last time. Circularity rule: FILE_MANIFEST.jsonl lists
    every entry but itself; PACKAGE_CONTENTS.md lists every entry but itself and the manifest; FILE_LIST.md lists every
    entry but itself, the manifest and PACKAGE_CONTENTS.md. Both documents are outside ``payload_sha256`` and are
    re-rendered and compared when the ZIP is verified;
 8. ``dist/<profile>/mbnas_application-<version>-<profile>-runtime-<platform>.zip`` (owner:
    ``-owner-private-runtime-``) with the single top-level folder ``mbnas_application/`` and ``<zip>.sha256``; the ZIP
    is re-opened and checked against FILE_MANIFEST before it is published.

``--reproducible``: fixed timestamps (``SOURCE_DATE_EPOCH``, default 1980-01-01T00:00:00Z) for files, wheels and ZIP
entries, sorted entries, normalised modes, content-derived build id; the whole pipeline runs twice (second staging
``build/staging/<profile>.pass2``) and the two ZIP digests are compared; BUILD_INFO ``reproducibility`` records both
digests and ``equal`` (only then the build is published; a mismatch is a violation).

``--canary`` (leak-detection test build): copies the tree to ``<root>/build/canary/<profile>/tree`` and
plants the canary tokens there (``canary_plant.py``; manifest ``<root>/docs/internal/canary/CANARY_MANIFEST.json``),
builds the profile from that tree (outputs stay inside it; ``dist/`` and ``build/wheels`` are not touched), then
scans the ZIP with ``canary_scan.py``: public/registration must hold no token, owner must hold every planted area
(positive control) and no ``MANIFEST_SELF``. Without ``--canary`` any canary token in a bundle is a violation.

Exit status: 0 built, 1 checks failed (nothing published), 2 usage/configuration error, 3 build tool failure.
Report: ``<root>/build/package_reports/<profile>.json`` (every check that ran with pass/warn/fail/not run, all findings,
wall-clock timings) and ``<profile>_wheels.txt`` (wheel member listing); the check table is also printed.
Determinism: findings about source-tree copies that do not ship (``generated_shadow``: e.g. the source placeholder of a
generated FILE_LIST.md) stay in the report but never enter BUILD_INFO.json, so the bundle does not depend on them.
"""
from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

DEV_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DEV_DIR.parents[1]
TOP = "mbnas_application"
PROFILES = ("owner", "public", "registration")
METADATA = ("BUILD_INFO.json", "PACKAGE_PROFILE.json", "FILE_MANIFEST.jsonl")
# registration: owner-free file listing generated from the staged manifest by gen_registration_docs.py
FILE_LIST = "docs/registration/FILE_LIST.md"
FILE_LIST_GENERATOR = "gen_registration_docs.py"
# bundle hygiene gate (forbidden policy embeds / host facts / removed client code in the web bundle); run on the
# staged mbnas_web/dist with node; exit 0 clean, 1 hits, 2 dist missing/empty
WEB_GATE = "mbnas_web/scripts/check-bundle.mjs"
# human-readable contents table of every bundle, rendered from FILE_MANIFEST.jsonl (see the module docstring)
PACKAGE_CONTENTS = "PACKAGE_CONTENTS.md"
LATE = (FILE_LIST, PACKAGE_CONTENTS)  # generated from FILE_MANIFEST; outside payload_sha256 and the build id seed
# findings about source-tree files that never ship (the packager generates that path): kept in the build report, never
# counted in BUILD_INFO.json, so that two trees differing only there build byte-identical bundles
TREE_ONLY_CHECKS = frozenset({"generated_shadow"})
MANIFEST_REL = "docs/internal/canary/CANARY_MANIFEST.json"
NORMALIZED_EPOCH = 315532800  # 1980-01-01T00:00:00Z, earliest ZIP timestamp
EXIT_OK, EXIT_VIOLATION, EXIT_USAGE, EXIT_TOOL = 0, 1, 2, 3
SECTIONS = ("profile", "wheels", "include", "deny", "deny-importers", "generated", "required", "expected",
            "private-reference-allow")
# relative ES-module / CommonJS imports followed by [deny-importers] (static text scan of the staged web files)
JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
RE_JS_IMPORT = re.compile(r"""(?:\bimport|\bexport)\s[^'"`;]*?\bfrom\s*['"]([^'"]+)['"]|\bimport\s*['"]([^'"]+)['"]"""
                          r"""|\bimport\(\s*['"]([^'"]+)['"]\s*\)|\brequire\(\s*['"]([^'"]+)['"]\s*\)""")
PROFILE_KEYS = {"name": None, "web_dist": ("required", "optional"), "private_package_check": ("strict", "off"),
                "host_facts": ("fail", "report")}
# optional [profile] keys. offline_install_evidence = <path relative to ROOT> of a record (JSON) of an install of
# this profile with the network removed (`unshare -rn bash scripts/install.sh --profile <p> --offline ...`); only
# then does PACKAGE_PROFILE.json say offline_install: true.
# wheel_listing_member = <path relative to the bundle root>: the wheel member listing of this build is also shipped at
# that path; it must be declared in [generated].
OPTIONAL_PROFILE_KEYS = {"offline_install_evidence": None, "wheel_listing_member": None}
CORE = "mbnas_core"
RE_PRIVATE_COMPONENT = re.compile(r"^mbnas[-_.]core(?:$|[-.])", re.I)
RE_PRIVATE_MODREF = re.compile(r"^mbnas_core(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*)?\.?$")
RE_PRIVATE_CODE = re.compile(r"\b(?:import|from)\s+mbnas_core\b")
RE_PRIVATE_TEXT_REF = re.compile(
    r"\bimport\s+mbnas_core\b|\bfrom\s+mbnas_core\b|(?:^|\s)-m\s+mbnas_core\b"
    r"|\bmbnas_core(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*"
    r"|(?i:\bmbnas[-_.]core\s*(?:===?|>=|<=|~=|!=|@))"
    r"|(?i:\bmbnas[-_.]core-\d[\w.!+]*-py\d[\w.]*-none-any\.whl)"
    r"|(?im:^Requires-Dist:\s*mbnas[-_.]core\b)"
    r"|(?im:^\s*mbnas[-_.]core\s*(?:$|[;\[<>=!~@]))", re.M)
RE_PRIVATE_MENTION = re.compile(r"mbnas[-_.]core", re.I)
# file types whose '#' lines are comments: a reference there is reported as a warning, not a violation
HASH_COMMENT_SUFFIXES = (".txt", ".sh", ".bash", ".env", ".example", ".yml", ".yaml", ".toml", ".cfg", ".ini",
                         ".conf", ".service", ".in")
RE_PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----")
RE_ENV_SECRET = re.compile(r"^\s*(?:export\s+|-\s+|Environment=\"?)?"
                           r"([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|"
                           r"API_KEY|TICKET_KEY|SIGNING_KEY|PRIVATE_KEY))\s*[=:]\s*(.*?)\s*$", re.I)
RE_PLACEHOLDER = re.compile(r"^(?:<[^>]*>|\$\{[^}]*\}|\$[A-Za-z_]\w*|[*x.]{3,}|"
                            r".*(?:change[-_ ]?me|replace|placeholder|example|generated|your[-_]|dummy|"
                            r"set[-_ ]?at[-_ ]?install|todo|fill[-_ ]?in|redacted|secret_here|token_here).*)$", re.I)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.]+)?$")
# Licence column of FILE_MANIFEST.jsonl (also FILE_LIST.md / PACKAGE_CONTENTS.md). Every reference names a file that
# ships in all three profiles: third-party-derived files point at THIRD_PARTY_NOTICES.md (Python side) or
# THIRD_PARTY_NOTICES_WEB.md (npm code in the web bundle); the project's own files are labelled "project" (no bundle
# ships a licence file of the project).
LICENCE_RULES: tuple[tuple[str, str], ...] = (
    ("**/mbnas_model_runtime/operators/parse_model_8_4_173.py",
     "third-party-derived (AGPL-3.0; see THIRD_PARTY_NOTICES.md)"),
    ("**/mbnas_model_runtime/operators/etri_mixed_blocks.py", "third-party-derived (see THIRD_PARTY_NOTICES.md)"),
    ("**/mbnas_model_runtime/adapters/etri_hybrid/**", "third-party-derived (see THIRD_PARTY_NOTICES.md)"),
    ("examples/models/yolo11*.yaml", "third-party-derived (AGPL-3.0; see THIRD_PARTY_NOTICES.md)"),
    ("wheels/mbnas_model_runtime-*.whl",
     "project + third-party-derived modules (incl. AGPL-3.0; see THIRD_PARTY_NOTICES.md)"),
    ("mbnas_web/dist/**", "project + bundled npm third-party code (see THIRD_PARTY_NOTICES_WEB.md)"),
    ("THIRD_PARTY_NOTICES*.md", "notice (third-party licence list)"),
    ("**", "project"),
)
ROLE_RULES: tuple[tuple[str, str], ...] = (
    ("wheels/*.whl", "wheel"), ("BUILD_INFO.json", "build-metadata"), ("PACKAGE_PROFILE.json", "build-metadata"),
    (FILE_LIST, "build-metadata"), (PACKAGE_CONTENTS, "build-metadata"),
    ("docs/internal/evidence/package_build/**", "build-metadata"), ("mbnas_*/pyproject.toml", "package-build-config"),
    ("mbnas_*/src/**", "package-source"), ("mbnas_web/index.html", "package-source"),  # the web entry page (source)
    ("mbnas_web/dist/**", "web-dist"), ("mbnas_web/package.json", "web-provenance"),
    ("mbnas_web/package-lock.json", "web-provenance"),
    ("mbnas_web/tests/**", "test"), ("mbnas_web/e2e/**", "test"),
    ("mbnas_web/playwright*.config.ts", "web-config"), ("mbnas_web/vite.config.ts", "web-config"),
    ("mbnas_web/tsconfig*.json", "web-config"), ("mbnas_web/scripts/**", "script"), ("mbnas_web/README*", "doc"),
    ("scripts/**", "script"),
    ("config/**", "config-example"), ("deploy/**", "deploy"), (".dockerignore", "deploy"), ("tests/**", "test"),
    ("examples/**", "example"),
    ("docs/**", "doc"), ("README*", "doc"), ("requirements*.txt", "requirements"), ("constraints.txt", "requirements"),
    ("LICENSE*", "licence-notice"), ("THIRD_PARTY_NOTICES*.md", "licence-notice"),
    ("VERSION", "version"), ("pyproject.toml", "workspace-config"), ("**", "other"),
)

# Every check of the packager (the report names each check that ran with its result, not only totals):
# (id, what it verifies, the finding ``check`` names it emits). Status per build: fail (a violation), warn (a warning),
# pass (ran, neither), not run (its precondition did not hold, e.g. private_package for private_package_check = off, or
# the ZIP checks of a build that stopped earlier). Findings of an unknown name are reported under "other".
CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("selection", "allowlist selection: no symbolic link / special file inside the allowlist, every source readable, "
                  "source copies of generated paths ignored",
     ("symlink", "special_file", "source_unreadable", "generated_shadow")),
    ("deny_importers", "[deny-importers]: the listed test-only web files and every file importing them are excluded",
     ("deny_importers_target_missing", "excluded_importer")),
    ("deny_patterns", "no staged or generated path matches a [deny] pattern", ("denied_present",)),
    ("required_files", "[required] files present", ("required_missing",)),
    ("expected_files", "[expected] deliverables present", ("expected_missing",)),
    ("web_dist", "prebuilt web dist present (mbnas_web/dist/index.html)", ("web_dist_missing",)),
    ("web_bundle_gate", "web bundle hygiene gate on the staged dist (mbnas_web/scripts/check-bundle.mjs)",
     ("web_bundle_gate",)),
    ("path_safety", "entry paths: no absolute path, '.'/'..' or empty component, backslash or control character; "
                    "length <= 240", ("path_safety", "long_path")),
    ("case_collision", "no entries colliding on case-insensitive / Unicode-normalising file systems",
     ("case_collision",)),
    ("content_scan", "no canary token, host fact, private key block or secret-like env value (sources, wheels, "
                     "generated metadata)",
     ("host_fact", "canary_token", "canary_expected", "private_key", "secret_like_value")),
    ("private_package", "private package absent: no path component, import, string module reference, requirement, "
                        "entry point or wheel name of it (private_package_check = strict)",
     ("private_path", "private_import", "private_reference_allowed", "private_reference_in_comment",
      "private_mention")),
    ("wheel_build", "one wheel per [wheels] package from its staged sources; METADATA Name/Version match the file "
                    "name", ("wheel_source_missing", "wheel_metadata")),
    ("wheel_contents", "C09 package-data gate: wheel members (minus *.dist-info) == staged <pkg>/src files with equal "
                       "sha256; every declared [tool.setuptools.package-data] file staged and in the wheel",
     ("wheel_member_missing", "wheel_member_extra", "wheel_member_mismatch", "package_data_missing",
      "package_data_unparsable")),
    ("versions", "wheel versions and mbnas_web/package.json equal VERSION", ("version_mismatch",)),
    ("generated_declared", "every generated output is declared in [generated]", ("generated_undeclared",)),
    ("zip_entries", "ZIP re-opened: no duplicate entry, CRC ok, entries == staging, all below the top folder",
     ("zip_duplicate_entry", "zip_crc", "zip_entries", "zip_entry_path")),
    ("manifest", "FILE_MANIFEST.jsonl: no self entry; every other entry listed with equal sha256 and bytes",
     ("manifest_self", "manifest_extra_entry", "manifest_missing_entry", "manifest_mismatch")),
    ("package_contents", "PACKAGE_CONTENTS.md equals its rendering from the ZIP's FILE_MANIFEST.jsonl",
     ("package_contents_mismatch",)),
    ("reproducibility", "--reproducible: two complete builds give byte-identical ZIPs", ("reproducibility_mismatch",)),
)
# checks that run only after BUILD_INFO.json is written (their results are in the build report and the printed table)
POST_METADATA_CHECKS = ("zip_entries", "manifest", "package_contents", "reproducibility")


class UsageError(Exception):
    pass


class ToolError(Exception):
    pass


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"mbnas_dev_{name}", DEV_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------- globs
class Glob:
    """Anchored path glob: components separated by '/', ``**`` = zero or more components, fnmatch inside one."""

    def __init__(self, text: str):
        t = text.strip()
        if not t or t.startswith("/") or "\\" in t or any(p in ("..", ".") for p in t.split("/")):
            raise UsageError(f"invalid pattern {text!r} (relative, '/'-separated, no '.'/'..' components)")
        if t.endswith("/"):
            t += "**"
        if "" in t.split("/"):
            raise UsageError(f"invalid pattern {text!r} (empty path component)")
        self.text = t
        self.parts = tuple(t.split("/"))

    def __repr__(self) -> str:
        return f"Glob({self.text!r})"

    @staticmethod
    def _match(pat: tuple[str, ...], parts: tuple[str, ...]) -> bool:
        if not pat:
            return not parts
        if pat[0] == "**":
            return any(Glob._match(pat[1:], parts[i:]) for i in range(len(parts) + 1))
        return bool(parts) and fnmatchcase(parts[0], pat[0]) and Glob._match(pat[1:], parts[1:])

    def match(self, rel: str | tuple[str, ...]) -> bool:
        parts = tuple(rel.split("/")) if isinstance(rel, str) else rel
        return self._match(self.parts, parts)

    def could_match_below(self, dir_parts: tuple[str, ...]) -> bool:
        """Some path strictly below the directory ``dir_parts`` can match."""
        pat, parts = self.parts, dir_parts
        while parts:
            if not pat:
                return False
            if pat[0] == "**":
                return True
            if not fnmatchcase(parts[0], pat[0]):
                return False
            pat, parts = pat[1:], parts[1:]
        return bool(pat)

    def covers_dir(self, dir_parts: tuple[str, ...]) -> bool:
        """Every path below the directory ``dir_parts`` matches (pattern ``<prefix>/**`` with the prefix matching
        the directory or one of its ancestors)."""
        if self.parts[-1] != "**":
            return False
        prefix = self.parts[:-1]
        return any(self._match(prefix, dir_parts[:i]) for i in range(len(dir_parts) + 1))


@dataclass
class Allowlist:
    path: Path
    sha256: str
    settings: dict[str, str]
    wheels: list[str]
    include: list[Glob]
    deny: list[Glob]
    deny_notes: list[tuple[str, str]]  # (pattern, reason) of [deny] lines written as '<glob> :: <reason>'
    deny_importers: list[tuple[str, str]]  # (file, reason): the file and every staged file importing it (transitively)
    generated: list[Glob]
    required: list[str]
    expected: list[str]
    private_reference_allow: list[tuple[Glob, str | None]]  # (file glob, required line substring or None)

    def included(self, rel: str) -> bool:
        return any(g.match(rel) for g in self.include) and not self.denied(rel)

    def denied(self, rel: str) -> bool:
        return any(g.match(rel) for g in self.deny)

    def is_generated(self, rel: str) -> bool:
        return any(g.match(rel) for g in self.generated)

    def prune_dir(self, dir_parts: tuple[str, ...]) -> bool:
        return (any(g.covers_dir(dir_parts) for g in self.deny)
                or not any(g.could_match_below(dir_parts) for g in self.include))


def load_allowlist(path: Path, profile: str) -> Allowlist:
    if not path.is_file():
        raise UsageError(f"allowlist not found: {path}")
    raw = path.read_bytes()
    sections: dict[str, list[str]] = {s: [] for s in SECTIONS}
    current = "include"
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        text = line.split(" #", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not text:
            continue
        m = re.fullmatch(r"\[([a-z-]+)\]", text)
        if m:
            if m.group(1) not in SECTIONS:
                raise UsageError(f"{path}:{n}: unknown section [{m.group(1)}]")
            current = m.group(1)
            continue
        sections[current].append(text)
    settings: dict[str, str] = {}
    for item in sections["profile"]:
        key, sep, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in PROFILE_KEYS and key not in OPTIONAL_PROFILE_KEYS:
            raise UsageError(f"{path}: bad [profile] entry {item!r}")
        allowed = PROFILE_KEYS.get(key, OPTIONAL_PROFILE_KEYS.get(key))
        if allowed and value not in allowed:
            raise UsageError(f"{path}: [profile] {key} must be one of {allowed}, got {value!r}")
        settings[key] = value
    missing = [k for k in PROFILE_KEYS if k not in settings]
    if missing:
        raise UsageError(f"{path}: [profile] lacks {missing}")
    if "wheel_listing_member" in settings:
        try:
            Glob(settings["wheel_listing_member"])  # a relative, '/'-separated path without '.'/'..' components
        except UsageError as exc:
            raise UsageError(f"{path}: [profile] wheel_listing_member: {exc}") from exc
    if settings["name"] != profile:
        raise UsageError(f"{path}: [profile] name = {settings['name']!r} but --profile {profile}")
    for w in sections["wheels"]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", w):
            raise UsageError(f"{path}: bad [wheels] entry {w!r}")
    for r in sections["required"] + sections["expected"]:
        Glob(r)  # validate
    deny, notes = [], []
    for t in sections["deny"]:
        pattern, sep, reason = t.partition(" :: ")
        if sep and not reason.strip():
            raise UsageError(f"{path}: empty reason in [deny] entry {t!r}")
        deny.append(Glob(pattern.strip()))
        if sep:
            notes.append((pattern.strip(), reason.strip()))
    importers = []
    for t in sections["deny-importers"]:
        target, sep, reason = t.partition(" :: ")
        if not sep or not reason.strip() or any(c in target for c in "*?[") or not target.strip().endswith(JS_SUFFIXES):
            raise UsageError(f"{path}: [deny-importers] entries are '<script file> :: <reason>', got {t!r}")
        Glob(target.strip())  # validate the path
        importers.append((target.strip(), reason.strip()))
    return Allowlist(path=path, sha256=sha256_bytes(raw), settings=settings, wheels=sections["wheels"],
                     include=[Glob(t) for t in sections["include"]], deny=deny, deny_notes=notes,
                     deny_importers=importers,
                     generated=[Glob(t) for t in sections["generated"]], required=sections["required"],
                     expected=sections["expected"],
                     private_reference_allow=[_allow_entry(t, path) for t in sections["private-reference-allow"]])


def _allow_entry(text: str, path: Path) -> tuple[Glob, str | None]:
    """``<glob>`` (whole file) or ``<glob> :: <literal>`` (only lines that contain the literal)."""
    glob, sep, literal = text.partition(" :: ")
    if sep and not literal.strip():
        raise UsageError(f"{path}: empty literal in [private-reference-allow] entry {text!r}")
    return Glob(glob.strip()), (literal.strip() if sep else None)


# -------------------------------------------------------------------------------------------------------- findings
@dataclass
class Finding:
    check: str
    severity: str  # violation | warning | info
    path: str
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity, "path": self.path, "detail": self.detail}


@dataclass
class Options:
    profile: str
    root: Path
    platform: str
    reproducible: bool
    allow_missing_web: bool
    canary: bool = False
    canary_mode: bool = False          # set internally while building inside the canary tree
    manifest_path: Path | None = None  # canary manifest used for scanning
    display_root: Path | None = None   # paths in messages are shown relative to this (default: root)

    def shown(self, path: Path) -> str:
        return os.path.relpath(path, self.display_root or self.root)


@dataclass
class PassResult:
    findings: list[Finding] = field(default_factory=list)
    staging_top: Path | None = None
    files: dict[str, dict] = field(default_factory=dict)  # rel -> {bytes, sha256, mode, origin}
    wheels: list[dict] = field(default_factory=list)
    zip_tmp: Path | None = None
    zip_sha256: str | None = None
    web_present: bool = False
    meta: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    ran: set = field(default_factory=set)  # ids of CHECKS that executed in this pass

    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "violation"]


def check_table(findings: list[Finding], ran: set, *, exclude: frozenset = frozenset(),
                only: tuple[str, ...] | None = None) -> list[dict]:
    """One row per CHECKS entry: id, status (pass | warn | fail | not run), counts per severity, description.
    ``exclude``: finding checks left out of the counts (TREE_ONLY_CHECKS for BUILD_INFO.json)."""
    owner_of = {name: cid for cid, _, names in CHECKS for name in names}
    counts: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.check in exclude:
            continue
        cid = owner_of.get(f.check, "other")
        c = counts.setdefault(cid, {"violation": 0, "warning": 0, "info": 0})
        c[f.severity] += 1
    rows = []
    for cid, what, _ in CHECKS + (("other", "findings of a check not listed above", ()),):
        if only is not None and cid not in only:
            continue
        c = counts.get(cid, {"violation": 0, "warning": 0, "info": 0})
        if cid == "other" and not any(c.values()):
            continue
        if c["violation"]:
            status = "fail"
        elif c["warning"]:
            status = "warn"
        elif cid in ran or any(c.values()):
            status = "pass"
        else:
            status = "not run"
        rows.append({"id": cid, "status": status, "violations": c["violation"], "warnings": c["warning"],
                     "info": c["info"], "what": what})
    return rows


# ------------------------------------------------------------------------------------------------------- selection
def select_files(root: Path, al: Allowlist, res: PassResult) -> list[str]:
    res.ran.add("selection")
    selected: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        dparts = tuple(rel_dir.parts)
        keep = []
        for d in sorted(dirnames):
            parts = dparts + (d,)
            if al.prune_dir(parts):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                res.findings.append(Finding("symlink", "violation", "/".join(parts) + "/",
                                            "symbolic link to a directory inside the allowlist (not followed)"))
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            rel = "/".join(dparts + (fn,))
            if not al.included(rel):
                continue
            mode = os.lstat(os.path.join(dirpath, fn)).st_mode
            if stat.S_ISLNK(mode):
                res.findings.append(Finding("symlink", "violation", rel, "symbolic link inside the allowlist"))
                continue
            if not stat.S_ISREG(mode):
                res.findings.append(Finding("special_file", "violation", rel, "not a regular file"))
                continue
            if al.is_generated(rel):
                res.findings.append(Finding("generated_shadow", "info", rel,
                                            "source copy not used: this path is generated by the packager"))
                continue
            selected.append(rel)
    return selected


def _resolve_js(rel: str, spec: str, present: set[str]) -> str | None:
    """Resolve a relative import specifier of ``rel`` against the selected files (TypeScript-style extensions)."""
    if not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(rel), spec)).replace(os.sep, "/")
    stem = base[: -len(Path(base).suffix)] if Path(base).suffix in (".js", ".jsx", ".mjs", ".cjs") else base
    for cand in [base, *(base + s for s in JS_SUFFIXES), *(stem + s for s in JS_SUFFIXES),
                 *(f"{base}/index{s}" for s in JS_SUFFIXES)]:
        if cand in present:
            return cand
    return None


def exclude_importers(root: Path, al: Allowlist, selected: list[str], res: PassResult) -> list[str]:
    """Drop every [deny-importers] target and every selected script that imports one, directly or transitively."""
    if not al.deny_importers:
        return selected
    res.ran.add("deny_importers")
    present = set(selected)
    js = [r for r in selected if r.endswith(JS_SUFFIXES)]
    deps: dict[str, set[str]] = {}
    for rel in js:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        deps[rel] = {t for m in RE_JS_IMPORT.finditer(text) for spec in m.groups() if spec
                     for t in [_resolve_js(rel, spec, present)] if t}
    removed: set[str] = set()
    report = []
    for target, reason in al.deny_importers:
        if target not in present:
            res.findings.append(Finding("deny_importers_target_missing", "warning", target,
                                        "[deny-importers] target is not in the selection (rule may be stale)"))
            report.append({"pattern": target, "reason": reason, "files": []})
            continue
        closure, changed = {target}, True
        while changed:
            changed = False
            for rel, ds in deps.items():
                if rel not in closure and ds & closure:
                    closure.add(rel)
                    changed = True
        removed |= closure
        report.append({"pattern": target, "reason": reason, "files": sorted(closure)})
        for rel in sorted(closure):
            res.findings.append(Finding("excluded_importer", "info", rel,
                                        f"{'target' if rel == target else 'imports'} {target} ({reason})"))
    res.meta["importer_exclusions"] = report
    return [r for r in selected if r not in removed]


def _safe_rmtree(path: Path, root: Path) -> None:
    path, build = path.resolve(), (root / "build").resolve()
    if build not in path.parents:
        raise UsageError(f"refusing to delete {path}: not below {build}")
    if path.exists():
        shutil.rmtree(path)


def _mode_for(rel: str, src_mode: int) -> int:
    return 0o755 if (src_mode & 0o111 or rel.endswith(".sh")) else 0o644


def stage_files(root: Path, rels: list[str], top: Path, epoch: int | None, res: PassResult) -> None:
    res.ran.add("selection")  # readability of every selected source
    for rel in rels:
        src, dst = root / rel, top / rel
        try:
            data = src.read_bytes()
            st = os.stat(src)
        except OSError as exc:  # removed or made unreadable while the tree was being staged
            res.findings.append(Finding("source_unreadable", "violation", rel, f"cannot read: {exc.strerror}"))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        mode = _mode_for(rel, st.st_mode)
        os.chmod(dst, mode)
        mtime = epoch if epoch is not None else max(int(st.st_mtime), NORMALIZED_EPOCH)
        os.utime(dst, (mtime, mtime))
        res.files[rel] = {"bytes": len(data), "sha256": sha256_bytes(data), "mode": mode, "origin": "tree"}


def _put_generated(top: Path, rel: str, data: bytes, epoch: int | None, res: PassResult, origin: str) -> None:
    dst = top / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    os.chmod(dst, 0o644)
    mtime = epoch if epoch is not None else int(time.time())
    os.utime(dst, (mtime, mtime))
    res.files[rel] = {"bytes": len(data), "sha256": sha256_bytes(data), "mode": 0o644, "origin": origin}


# ---------------------------------------------------------------------------------------------------------- wheels
def _pip_env(epoch: int | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES="")
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    if epoch is not None:
        env.update(SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0")
    return env


def _wheel_meta(whl: Path) -> dict:
    with zipfile.ZipFile(whl) as zf:
        meta = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1), None)
        text = zf.read(meta).decode("utf-8") if meta else ""
    fields = {}
    for line in text.splitlines():
        if not line.strip():
            break
        k, sep, v = line.partition(":")
        if sep and k in ("Name", "Version") and k not in fields:
            fields[k] = v.strip()
    return fields


def build_wheels(opts: Options, al: Allowlist, top: Path, pass_no: int, epoch: int | None, res: PassResult,
                 log_dir: Path) -> None:
    root = opts.root
    res.ran.add("wheel_build")
    work = root / "build" / "wheel_build" / f"{opts.profile}.pass{pass_no}"
    _safe_rmtree(work, root)
    out = work / "out"
    out.mkdir(parents=True)
    published = root / "build" / "wheels"
    extra: list[str] = []
    if (root / "build" / "wheelhouse").is_dir():
        extra += ["--find-links", str(root / "build" / "wheelhouse")]
    if (root / "constraints.txt").is_file():
        extra += ["-c", str(root / "constraints.txt")]
    for pkg in al.wheels:
        src = top / pkg
        if not (src / "pyproject.toml").is_file():
            res.findings.append(Finding("wheel_source_missing", "violation", f"{pkg}/pyproject.toml",
                                        "a [wheels] package has no staged pyproject.toml"))
            continue
        bdir = work / "src" / pkg
        shutil.copytree(src, bdir)
        before = set(out.glob("*.whl"))
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-index", "--no-build-isolation",
               "--no-cache-dir", "--disable-pip-version-check", "--progress-bar", "off", *extra, "-w", str(out),
               str(bdir)]
        log = log_dir / f"pip_wheel_{pkg}.pass{pass_no}.log"
        t0 = time.monotonic()
        proc = subprocess.run(cmd, env=_pip_env(epoch), cwd=str(work), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=900)
        log.write_bytes(proc.stdout)
        res.timings[f"wheel_{pkg}_s"] = round(time.monotonic() - t0, 2)
        if proc.returncode != 0:
            raise ToolError(f"pip wheel failed for {pkg} (exit {proc.returncode}); log: {log}")
        made = sorted(set(out.glob("*.whl")) - before)
        expect = re.compile(rf"^{re.escape(pkg)}-([0-9][^-]*)-py3-none-any\.whl$")
        if len(made) != 1 or not expect.match(made[0].name):
            raise ToolError(f"pip wheel for {pkg} produced {[m.name for m in made]} (expected one "
                            f"{pkg}-<version>-py3-none-any.whl); log: {log}")
        whl = made[0]
        version = expect.match(whl.name).group(1)
        meta = _wheel_meta(whl)
        if meta.get("Version") != version or (meta.get("Name") or "").replace("-", "_").lower() != pkg.lower():
            res.findings.append(Finding("wheel_metadata", "violation", f"wheels/{whl.name}",
                                        f"METADATA Name/Version {meta} do not match the file name"))
        data = whl.read_bytes()
        _put_generated(top, f"wheels/{whl.name}", data, epoch, res, f"built:pip-wheel:{pkg}")
        if pass_no == 1:
            published.mkdir(parents=True, exist_ok=True)
            tmp = published / (whl.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, published / whl.name)
        res.wheels.append({"import_name": pkg, "name": meta.get("Name"), "version": version,
                           "wheel": f"wheels/{whl.name}", "sha256": sha256_bytes(data), "bytes": len(data)})


# ------------------------------------------------------------------------- wheel contents / package-data gate
RE_TOML_HEADER = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_\-]+(?:\s*\.\s*[A-Za-z0-9_\-]+)*)\s*\]\]?\s*(?:#.*)?$")


class _TomlSubsetError(ValueError):
    pass


def _toml_string(text: str, i: int) -> tuple[str, int]:
    """A TOML basic string ``"..."`` starting at ``text[i]``; returns (value, index after the closing quote)."""
    if text[i] != '"':
        raise _TomlSubsetError(f"expected '\"' at offset {i}")
    out, i = [], i + 1
    while i < len(text):
        c = text[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\":
            nxt = text[i + 1: i + 2]
            if nxt not in ('"', "\\"):
                raise _TomlSubsetError(f"unsupported escape \\{nxt}")
            out.append(nxt)
            i += 2
            continue
        if c == "\n":
            raise _TomlSubsetError("newline inside a string")
        out.append(c)
        i += 1
    raise _TomlSubsetError("unterminated string")


def _toml_skip(text: str, i: int) -> int:
    while i < len(text):
        if text[i] in " \t\r\n":
            i += 1
        elif text[i] == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            break
    return i


def declared_package_data(pyproject_text: str) -> dict[str, list[str]]:
    """``[tool.setuptools.package-data]`` of a pyproject.toml as {package: [glob, ...]} ({} when absent).

    Python 3.10 has no ``tomllib``, so this reads the one table this gate needs: keys that are TOML basic strings or
    bare keys, values that are arrays of basic strings (one line or several, comments allowed). Anything else raises
    ``ValueError``: the gate then reports the table as unparsable instead of guessing."""
    lines = pyproject_text.splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        m = RE_TOML_HEADER.match(line)
        if m and not line.lstrip().startswith(("[\"", "['")):
            name = re.sub(r"\s+", "", m.group(1))
            inside = name == "tool.setuptools.package-data" and not line.lstrip().startswith("[[")
            continue
        if inside:
            body.append(line)
    text = "\n".join(body)
    out: dict[str, list[str]] = {}
    i = _toml_skip(text, 0)
    while i < len(text):
        if text[i] == '"':
            key, i = _toml_string(text, i)
        else:
            m = re.compile(r"[A-Za-z0-9_\-]+").match(text, i)
            if not m:
                raise _TomlSubsetError(f"unsupported key at {text[i:i + 20]!r}")
            key, i = m.group(0), m.end()
        i = _toml_skip(text, i)
        if i >= len(text) or text[i] != "=":
            raise _TomlSubsetError(f"expected '=' after key {key!r}")
        i = _toml_skip(text, i + 1)
        if i >= len(text) or text[i] != "[":
            raise _TomlSubsetError(f"value of {key!r} is not an array")
        i = _toml_skip(text, i + 1)
        values: list[str] = []
        while True:
            if i >= len(text):
                raise _TomlSubsetError(f"unterminated array of {key!r}")
            if text[i] == "]":
                i += 1
                break
            value, i = _toml_string(text, i)
            values.append(value)
            i = _toml_skip(text, i)
            if i < len(text) and text[i] == ",":
                i = _toml_skip(text, i + 1)
            elif i < len(text) and text[i] != "]":
                raise _TomlSubsetError(f"expected ',' or ']' in the array of {key!r}")
        if key in out:
            raise _TomlSubsetError(f"duplicate key {key!r}")
        out[key] = values
        i = _toml_skip(text, i)
    return out


def check_wheel_contents(top: Path, res: PassResult) -> list[dict]:
    """Package-data gate on every built wheel: the members (``*.dist-info/`` aside) are exactly the staged
    ``<pkg>/src`` files with equal sha256 (a data file that is not declared as package-data is therefore missing from
    the wheel and fails here), and every declared ``[tool.setuptools.package-data]`` glob matches at least one staged
    file. Returns the per-wheel listing (``res.meta["wheel_listing"]``)."""
    res.ran.add("wheel_contents")
    listing: list[dict] = []
    for w in res.wheels:
        pkg, wrel = w["import_name"], w["wheel"]
        src = top / pkg / "src"
        staged = {q.relative_to(src).as_posix(): q for q in sorted(src.rglob("*")) if q.is_file()}
        with zipfile.ZipFile(top / wrel) as zf:
            members = {n: zf.read(n) for n in sorted(zf.namelist()) if not n.endswith("/")}
        payload = {n: d for n, d in members.items() if not n.split("/", 1)[0].endswith(".dist-info")}
        rows = []
        for name, data in payload.items():
            rows.append({"path": name, "bytes": len(data), "sha256": sha256_bytes(data)})
        missing = sorted(set(staged) - set(payload))
        extra = sorted(set(payload) - set(staged))
        mismatch = sorted(r for r in set(staged) & set(payload) if sha256_file(staged[r]) != sha256_bytes(payload[r]))
        for r in missing:
            res.findings.append(Finding("wheel_member_missing", "violation", f"{wrel}!{r}",
                                        "staged package source is not in the wheel (a directory without __init__.py, "
                                        "or a data file not declared in [tool.setuptools.package-data])"))
        for r in extra:
            res.findings.append(Finding("wheel_member_extra", "violation", f"{wrel}!{r}",
                                        "wheel member without a staged source file"))
        for r in mismatch:
            res.findings.append(Finding("wheel_member_mismatch", "violation", f"{wrel}!{r}",
                                        "wheel member bytes differ from the staged source"))
        declared: list[dict] = []
        try:
            pdata = declared_package_data((top / pkg / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            pdata = {}
            res.findings.append(Finding("package_data_unparsable", "violation", f"{pkg}/pyproject.toml",
                                        f"[tool.setuptools.package-data] not readable by the gate: {exc}"))
        for package, patterns in sorted(pdata.items()):
            pdir = package.replace(".", "/")
            for pat in patterns:
                try:
                    g = Glob(pat)
                except UsageError as exc:
                    res.findings.append(Finding("package_data_unparsable", "violation", f"{pkg}/pyproject.toml",
                                                f"package-data {package}: {exc}"))
                    continue
                hits = sorted(r for r in staged if r.startswith(pdir + "/") and g.match(r[len(pdir) + 1:]))
                if not hits:
                    res.findings.append(Finding("package_data_missing", "violation", f"{pkg}/pyproject.toml",
                                                f"declared package-data {package}: {pat!r} matches no staged file"))
                for r in hits:
                    sha = sha256_file(staged[r])
                    declared.append({"package": package, "pattern": pat, "path": r, "sha256": sha,
                                     "in_wheel": r in payload and sha256_bytes(payload[r]) == sha})
        ok = not (missing or extra or mismatch) and all(d["in_wheel"] for d in declared) and not any(
            f.check in ("package_data_missing", "package_data_unparsable") and f.path == f"{pkg}/pyproject.toml"
            for f in res.findings)
        w["members"] = len(payload)
        w["package_data"] = [{"path": d["path"], "sha256": d["sha256"]} for d in declared]
        listing.append({"wheel": wrel, "sha256": w["sha256"], "bytes": w["bytes"], "dist_info_members":
                        len(members) - len(payload), "members": rows, "package_data": declared,
                        "package_data_declared": {k: list(v) for k, v in sorted(pdata.items())},
                        "missing": missing, "extra": extra, "mismatch": mismatch, "status": "pass" if ok else "fail"})
    res.meta["wheel_listing"] = listing
    return listing


def render_wheel_listing(profile: str, version: str, listing: list[dict]) -> bytes:
    """Text listing of the wheels of one build (deterministic: no clock, no build id, no host path)."""
    out = [f"# MBNAS Research Console {version} - wheel member listing, profile {profile}",
           "# Written by scripts/dev/package_build.py (package-data gate). One block per wheel: the wheel sha256,",
           "# the gate verdict, the declared [tool.setuptools.package-data] files, then every member (dist-info aside)",
           "# as '<sha256>  <bytes>  <path>' ([data] = declared package-data). Gate: members == staged <pkg>/src files",
           "# with equal sha256, every declared package-data file present.", ""]
    for w in listing:
        out.append(f"wheel {w['wheel']}  sha256 {w['sha256']}  bytes {w['bytes']}  members {len(w['members'])} "
                   f"(+{w['dist_info_members']} dist-info)")
        decl = "; ".join(f"{k} = {', '.join(v)}" for k, v in w["package_data_declared"].items()) or "none declared"
        out.append(f"  package-data declared: {decl}")
        present = sum(1 for d in w["package_data"] if d["in_wheel"])
        out.append(f"  gate: {w['status'].upper()} (missing {len(w['missing'])}, extra {len(w['extra'])}, "
                   f"sha256 mismatch {len(w['mismatch'])}, declared package-data in wheel {present}/"
                   f"{len(w['package_data'])})")
        data_paths = {d["path"] for d in w["package_data"]}
        for m in w["members"]:
            tag = "[data] " if m["path"] in data_paths else ""
            out.append(f"  {m['sha256']}  {m['bytes']:>9}  {tag}{m['path']}")
        out.append("")
    return ("\n".join(out)).encode("utf-8")


# --------------------------------------------------------------------------------------------------------- listing
def _is_text(data: bytes) -> bool:
    if b"\0" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


@functools.lru_cache(maxsize=None)
def _glob(pattern: str) -> Glob:
    return Glob(pattern)


def first_match(rules: tuple[tuple[str, str], ...], rel: str) -> str:
    for pattern, value in rules:
        if _glob(pattern).match(rel):
            return value
    return rules[-1][1]


# ---------------------------------------------------------------------------------------------------------- checks
def check_paths(res: PassResult, only: set[str] | None = None) -> None:
    res.ran.update(("path_safety", "case_collision"))
    seen: dict[str, str] = {}
    for rel in sorted(res.files):
        if only is not None and rel not in only:
            seen.setdefault(unicodedata.normalize("NFC", rel).casefold(), rel)
            continue
        parts = rel.split("/")
        bad = None
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            bad = "absolute path"
        elif any(p in ("", ".", "..") for p in parts):
            bad = "empty, '.' or '..' component"
        elif "\\" in rel:
            bad = "backslash in path"
        elif any(ord(c) < 32 or ord(c) == 127 for c in rel):
            bad = "control character in path"
        if bad:
            res.findings.append(Finding("path_safety", "violation", rel, bad))
        key = unicodedata.normalize("NFC", rel).casefold()
        if key in seen:
            res.findings.append(Finding("case_collision", "violation", rel,
                                        f"collides with {seen[key]!r} on case-insensitive / Unicode-normalising file "
                                        "systems"))
        else:
            seen[key] = rel
        if len(f"{TOP}/{rel}") > 240:
            res.findings.append(Finding("long_path", "warning", rel, f"{len(TOP) + 1 + len(rel)} characters (>240)"))


def check_deny_and_presence(al: Allowlist, res: PassResult, selected: list[str]) -> None:
    res.ran.update(("deny_patterns", "required_files", "expected_files"))
    for rel in res.files:
        if al.denied(rel):
            res.findings.append(Finding("denied_present", "violation", rel, "staged path matches a [deny] pattern"))
    for pattern in al.required:
        g = Glob(pattern)
        if not any(g.match(r) for r in selected):
            res.findings.append(Finding("required_missing", "violation", pattern, "required by the allowlist"))
    for pattern in al.expected:
        g = Glob(pattern)
        if not any(g.match(r) for r in selected):
            res.findings.append(Finding("expected_missing", "warning", pattern,
                                        "expected deliverable not present in the source tree"))


def _py_private_refs(text: str) -> list[tuple[int, str]] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    docs = {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, f"import {a.name}") for a in node.names if a.name.split(".")[0] == CORE]
        elif isinstance(node, ast.ImportFrom):
            if not node.level and (node.module or "").split(".")[0] == CORE:
                out.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            v = node.value.strip()
            if RE_PRIVATE_MODREF.match(v):
                out.append((node.lineno, f"string module reference {v!r}"))
            elif RE_PRIVATE_CODE.search(v):
                out.append((node.lineno, "code string importing the private package"))
    return out


def _private_scan_blob(rel: str, data: bytes, al: Allowlist, res: PassResult, mentions: dict[str, int]) -> None:
    outer = rel.split("!", 1)[0]
    allow = [lit for g, lit in al.private_reference_allow if g.match(outer)] if "!" not in rel else []
    for part in rel.replace("!", "/").split("/"):
        if RE_PRIVATE_COMPONENT.match(part):
            res.findings.append(Finding("private_path", "violation", rel, "path component names the private package"))
            break
    if not _is_text(data):
        return
    text = data.decode("utf-8")
    refs: list[tuple[int, str]] | None = None
    if rel.endswith(".py"):
        refs = _py_private_refs(text)
    if refs is None:  # not Python (or unparsable): textual reference patterns
        name = rel.rsplit("/", 1)[-1]
        hash_comments = name.endswith(HASH_COMMENT_SUFFIXES) or name.startswith(("Dockerfile", "Makefile"))
        lines = text.splitlines()
        refs = []
        for m in RE_PRIVATE_TEXT_REF.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            src = lines[line - 1].lstrip() if line <= len(lines) else ""
            if any(lit is None or lit in src for lit in allow):
                res.findings.append(Finding("private_reference_allowed", "info", f"{rel}:{line}",
                                            f"{m.group(0).strip()!r} excused by [private-reference-allow]"))
            elif hash_comments and src.startswith("#"):
                res.findings.append(Finding("private_reference_in_comment", "warning", f"{rel}:{line}",
                                            f"comment names a private artifact {m.group(0).strip()!r}"))
            else:
                refs.append((line, f"reference {m.group(0).strip()!r}"))
    for line, detail in refs:
        res.findings.append(Finding("private_import", "violation", f"{rel}:{line}", detail))
    n = len(RE_PRIVATE_MENTION.findall(text))
    if n:
        mentions[rel] = n


def check_private(al: Allowlist, top: Path, res: PassResult, only: list[str] | None = None) -> None:
    if al.settings["private_package_check"] != "strict":
        return
    res.ran.add("private_package")
    mentions: dict[str, int] = {}
    for rel in sorted(res.files if only is None else only):
        data = (top / rel).read_bytes()
        _private_scan_blob(rel, data, al, res, mentions)
        if rel.endswith(".whl"):
            with zipfile.ZipFile(top / rel) as zf:
                for name in sorted(zf.namelist()):
                    if not name.endswith("/"):
                        _private_scan_blob(f"{rel}!{name}", zf.read(name), al, res, mentions)
    for rel, n in sorted(mentions.items()):
        res.findings.append(Finding("private_mention", "info", rel,
                                    f"{n} prose mention(s) of the private package name (no import, no path)"))


def check_content(opts: Options, al: Allowlist, top: Path, res: PassResult, scan_mod,
                  rels: list[str] | None = None) -> dict:
    res.ran.add("content_scan")
    manifest = None
    mpath = opts.manifest_path if opts.manifest_path and opts.manifest_path.is_file() else None
    if mpath:
        try:
            manifest = scan_mod.load_manifest(mpath)
        except ValueError as exc:  # also json.JSONDecodeError
            raise UsageError(f"canary manifest unusable ({exc}); fix or remove it") from exc
    needles = scan_mod.build_needles(manifest, host_facts_enabled=True, manifest_path=str(mpath) if mpath else None)
    targets = [str(top / r) for r in sorted(rels)] if rels is not None else [str(top)]
    if not targets:
        return {"files": 0, "bytes": 0, "containers": 0, "hits": 0}
    report = scan_mod.scan_targets(targets, needles, profile=opts.profile if opts.canary_mode else None, min_files=0)
    prefix = str(top) + os.sep
    for h in report["hits"]:
        loc = h["location"][len(prefix):] if h["location"].startswith(prefix) else h["location"]
        if h["kind"] == "host_fact":
            sev = "violation" if al.settings["host_facts"] == "fail" else "info"
            res.findings.append(Finding("host_fact", sev, loc, h["label"]))
        elif h["kind"] == "canary":
            if opts.canary_mode:
                sev = "violation" if h["failing"] else "info"
                res.findings.append(Finding("canary_token" if sev == "violation" else "canary_expected", sev, loc,
                                            f"{h['label']} [{h['variant']}]"))
            else:
                res.findings.append(Finding("canary_token", "violation", loc,
                                            f"{h['label']} [{h['variant']}] (release bundles hold no canary token)"))
        else:
            res.findings.append(Finding("canary_token", "violation", loc, f"{h['label']} [{h['variant']}]"))
    for rel in sorted(rels if rels is not None else res.files):
        data = (top / rel).read_bytes()
        for m in RE_PRIVATE_KEY.finditer(data):
            res.findings.append(Finding("private_key", "violation", rel, "private key block"))
        name = rel.rsplit("/", 1)[-1]
        envlike = (".env" in name or name.endswith((".service", ".service.example")) or
                   re.match(r"^(docker-)?compose.*\.ya?ml$", name))
        if envlike and _is_text(data):
            for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
                m = RE_ENV_SECRET.match(line)
                if not m:
                    continue
                value = m.group(2).split(" #", 1)[0].strip().strip("'\"")
                if not value or len(value) < 12 or RE_PLACEHOLDER.match(value):
                    continue
                if scan_mod.TOKEN_RE.fullmatch(value.encode()):
                    continue  # a canary token (judged above)
                res.findings.append(Finding("secret_like_value", "violation", f"{rel}:{n}",
                                            f"{m.group(1)} has a non-placeholder value"))
    return {"files": report["scanned"]["files"], "bytes": report["scanned"]["bytes"],
            "containers": report["scanned"]["containers"], "hits": len(report["hits"])}


def web_bundle_gate(root: Path, top: Path, res: PassResult) -> None:
    """Run ``check-bundle.mjs`` on the staged ``mbnas_web/dist`` (the bytes that ship)."""
    script, node = root / WEB_GATE, shutil.which("node")
    res.ran.add("web_bundle_gate")
    gate = {"tool": WEB_GATE, "status": "not run"}
    res.meta["web_bundle_gate"] = gate
    if not script.is_file() or node is None:
        why = f"{WEB_GATE} not found" if not script.is_file() else "node not found on PATH"
        res.findings.append(Finding("web_bundle_gate", "violation", "mbnas_web/dist",
                                    f"bundle hygiene gate cannot run: {why}"))
        gate["status"] = f"cannot run ({why})"
        return
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    gate["node"] = ver.stdout.decode("utf-8", "replace").strip()
    proc = subprocess.run([node, str(script), "--dist", str(top / "mbnas_web" / "dist"), "--json"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, cwd=str(root / "mbnas_web"))
    try:
        rep = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        rep = {}
    needles = rep.get("needles") or {}
    gate.update(exit=proc.returncode, files=len(rep.get("files") or []), bytes=rep.get("bytes"),
                needles=len(needles.get("required", [])) + len(needles.get("extra", [])),
                hits=len(rep.get("hits") or []))
    if proc.returncode == 0 and rep.get("ok") is True:
        gate["status"] = "pass"
        return
    gate["status"] = "fail"
    for h in rep.get("hits") or []:
        res.findings.append(Finding("web_bundle_gate", "violation", f"mbnas_web/dist/{Path(h.get('file', '?')).name}",
                                    f"forbidden string {h.get('needle')!r} x{h.get('count')} "
                                    f"({'required' if h.get('required') else 'extra'} needle of {WEB_GATE})"))
    if not rep.get("hits"):
        err = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        res.findings.append(Finding("web_bundle_gate", "violation", "mbnas_web/dist",
                                    f"{WEB_GATE} exit {proc.returncode}: {err or 'no report'}"))


def check_versions(version: str, top: Path, res: PassResult) -> None:
    res.ran.add("versions")
    for w in res.wheels:
        if w["version"] != version:
            res.findings.append(Finding("version_mismatch", "warning", w["wheel"],
                                        f"wheel version {w['version']} != VERSION {version} (DD-A24 wants them equal)"))
    pj = top / "mbnas_web" / "package.json"
    if pj.is_file():
        try:
            v = json.loads(pj.read_text(encoding="utf-8")).get("version")
        except ValueError:
            v = None
        if v and v != version:
            res.findings.append(Finding("version_mismatch", "warning", "mbnas_web/package.json",
                                        f"package.json version {v} != VERSION {version}"))


# -------------------------------------------------------------------------------------------------------- metadata
def platform_tag(arg: str) -> str:
    t = sysconfig.get_platform() if arg in ("", "auto") else arg
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", t):
        raise UsageError(f"bad --platform {arg!r}")
    return t


def zip_name(version: str, profile: str, ptag: str) -> str:
    label = "owner-private-runtime" if profile == "owner" else f"{profile}-runtime"
    return f"{TOP}-{version}-{label}-{ptag}.zip"


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def payload_digest(res: PassResult) -> str:
    h = hashlib.sha256()
    for rel in sorted(res.files):
        if rel not in METADATA and rel not in LATE:
            h.update(f"{rel}\0{res.files[rel]['sha256']}\n".encode())
    return "sha256:" + h.hexdigest()


def offline_install_doc(opts: Options, al: Allowlist) -> dict:
    """``offline_install`` of PACKAGE_PROFILE.json: true only with a passing offline-install record (see
    OPTIONAL_PROFILE_KEYS)."""
    rel = al.settings.get("offline_install_evidence")
    if not rel:
        return {"offline_install": False,
                "offline_install_note": "not verified for this package (true only after an install "
                                        "with the network removed succeeds)"}
    path = opts.root / rel
    if not path.is_file():  # e.g. a copied tree without the owner-side evidence: never claim, never fail the build
        return {"offline_install": False,
                "offline_install_note": f"not claimed: the allowlist names the offline-install record {rel}, which "
                                        "is not present in this source tree"}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"[profile] offline_install_evidence {rel}: unreadable ({type(exc).__name__})") from exc
    need = {"profile": opts.profile, "exit": 0, "network": "none (unshare -rn)"}
    bad = [k for k, v in need.items() if rec.get(k) != v]
    for k in ("verified_at", "command", "wheelhouse", "tested_zip_sha256"):
        if not isinstance(rec.get(k), str) or not rec.get(k):
            bad.append(k)
    if bad:
        raise UsageError(f"[profile] offline_install_evidence {rel}: not a passing record for profile "
                         f"{opts.profile} (fields {bad})")
    return {"offline_install": True,
            "offline_install_note": (f"verified {rec['verified_at']}: `{rec['command']}` exited 0 with the "
                                     "network removed (unshare -rn) into a fresh path with spaces. The third-party "
                                     "wheels are NOT bundled in this ZIP: an offline install needs a wheelhouse of the "
                                     "pinned wheels (constraints.txt), passed with --wheelhouse <dir>; the project "
                                     "wheels come from wheels/."),
            "offline_install_wheelhouse": rec["wheelhouse"],
            "offline_install_verified_zip_sha256": rec["tested_zip_sha256"]}


def package_profile_doc(opts: Options, al: Allowlist, version: str, ptag: str, build_id: str, res: PassResult) -> dict:
    p = opts.profile
    req = "requirements-owner.txt" if p == "owner" else "requirements-public.txt"
    deploy = "deploy/owner" if p == "owner" else "deploy/public"
    all_pkgs = ["mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server", CORE]
    examples = sorted(r for r in res.files if r.startswith("config/examples/"))
    areas = sorted({r.split("/", 1)[0] + ("/" if "/" in r else "") for r in res.files} | set(METADATA)
                   | {PACKAGE_CONTENTS})
    prefixes = {"MBNAS_HOME": "runtime root (scripts set <package>/runtime)", "MBNAS_GW_": "gateway settings",
                "MBNAS_RUNNER_": "runner settings"}
    if p == "owner":
        prefixes["MBNAS_CORE_"] = "core settings (owner host)"
    return {
        "schema": "mbnas.package_profile/1", "product": "MBNAS Research Console", "profile": p, "version": version,
        "build_id": build_id, "zip": zip_name(version, p, ptag), "top_level_folder": f"{TOP}/",
        "exclusions_with_reason": [{"pattern": pat, "reason": why} for pat, why in al.deny_notes]
                                  + [{"pattern": f"{e['pattern']} and every file importing it", "reason": e["reason"],
                                      "files": e["files"]} for e in res.meta.get("importer_exclusions", [])],
        "packages": {"included": list(al.wheels),
                     "excluded": ([x for x in all_pkgs if x not in al.wheels] if p == "owner" else
                                  ["core (private; runs on the owner host and is reached through its URL)"])},
        "contents": areas,
        "requirements": {
            "python": f"CPython 3.10 (requires-python >=3.10,<3.11; build interpreter {platform.python_version()})",
            "os": f"{platform.system()} {platform.machine()} (build platform {ptag}; other platforms unverified)",
            "cuda": "GPU runner: NVIDIA driver supporting CUDA 12.4 (torch 2.6.0+cu124 wheels); CPU-only runners "
                    "cover training, checkpoints, structure checks and generation, not latency",
            "node": ("not needed to run: the gateway serves the prebuilt mbnas_web/dist; Node.js is "
                     "needed only to rebuild the web UI from the bundled mbnas_web sources (npm ci, npm run build)"
                     if any(r.startswith("mbnas_web/src/") for r in res.files) else
                     "not needed to run: the gateway serves the prebuilt mbnas_web/dist; this package has no "
                     "web sources, so Node.js matters only for rebuilding the web UI from the source repository")},
        "core_connection_required": p != "owner", "core_included": p == "owner",
        "config": {"env_prefixes": prefixes, "example_files": examples},
        "data_and_weights": "not bundled: datasets, pretrained weights and run outputs are provided by the operator "
                            "(paths configured per role; examples/ holds only small synthetic samples if present)",
        "install": {"methods": [f"docker compose ({deploy}/)", f"pip install -r {req} (project wheels in wheels/)",
                                "bash scripts/install.sh (wrapper)"],
                    "network_dependency": "online install downloads pinned third-party packages from PyPI and the "
                                          "PyTorch cu124 index (download.pytorch.org); the project wheels are bundled",
                    **offline_install_doc(opts, al)},
        "web_dist": {"present": res.web_present,
                     "note": "prebuilt mbnas_web/dist copied as-is" if res.web_present else
                     "missing at build time: the gateway has no web UI to serve"},
        "tested_platform": {"tag": ptag, "build_id": build_id,
                            "note": "build platform; install/run verification is recorded separately"},
    }


def build_info_doc(opts: Options, al: Allowlist, version: str, ptag: str, build_id: str, built_at: str,
                   at_source: str, res: PassResult, tree_hash: dict, reproducibility: dict) -> dict:
    sev: dict[str, int] = {"violation": 0, "warning": 0, "info": 0}
    by_check: dict[str, int] = {}
    shipped = [f for f in res.findings if f.check not in TREE_ONLY_CHECKS]  # see TREE_ONLY_CHECKS
    for f in shipped:
        sev[f.severity] += 1
        by_check[f"{f.severity}:{f.check}"] = by_check.get(f"{f.severity}:{f.check}", 0) + 1
    warnings = sorted({f"{f.check}: {f.path}" for f in shipped if f.severity == "warning"})
    pre_zip = tuple(cid for cid, _, _ in CHECKS if cid not in POST_METADATA_CHECKS) + ("other",)
    run = [{k: v for k, v in row.items() if k != "what"}
           for row in check_table(res.findings, res.ran, exclude=TREE_ONLY_CHECKS, only=pre_zip)]
    return {
        "schema": "mbnas.build_info/1", "product": "MBNAS Research Console", "version": version,
        "profile": opts.profile, "build_id": build_id, "built_at": built_at, "built_at_source": at_source,
        "test_build": "canary (T8 markers planted; never release this bundle)" if opts.canary_mode else None,
        "platform": {"tag": ptag, "system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "libc": " ".join(platform.libc_ver()).strip() or None},
        "source_tree": {"algorithm": tree_hash["algorithm"], "sha256": tree_hash["sha256"],
                        "roots": [r["label"] for r in tree_hash["roots"]], "files": tree_hash["files"],
                        "computed_over": "the staged package directories <pkg>/src/<pkg> of this bundle",
                        "tool": "scripts/dev/source_tree_hash.py (mbnas_contracts.util.source_hash)", "git": None},
        "payload_sha256": payload_digest(res),
        "payload_sha256_covers": "every file of the bundle except BUILD_INFO.json, PACKAGE_PROFILE.json, "
                                 "FILE_MANIFEST.jsonl and the documents rendered from the manifest "
                                 "(PACKAGE_CONTENTS.md; registration: docs/registration/FILE_LIST.md)",
        "packages": res.wheels,
        "web_dist": {"present": res.web_present, "bundle_gate": res.meta.get("web_bundle_gate"),
                     "files": sum(1 for r in res.files if r.startswith("mbnas_web/dist/")),
                     "status": "present (prebuilt, copied as-is)" if res.web_present else (
                         "missing (allowed by --allow-missing-web)" if opts.allow_missing_web else "missing")},
        "allowlist": {"file": f"build/allowlists/{opts.profile}.txt", "sha256": al.sha256},
        "tools": {"python": platform.python_version(), "pip": _dist_version("pip"),
                  "setuptools": _dist_version("setuptools"), "wheel": _dist_version("wheel"),
                  "package_build_sha256": sha256_file(DEV_DIR / "package_build.py"),
                  "canary_scan_sha256": sha256_file(DEV_DIR / "canary_scan.py"),
                  "source_tree_hash_sha256": sha256_file(DEV_DIR / "source_tree_hash.py")},
        "checks": {"status": "pass" if not sev["violation"] and not warnings else (
                   "pass_with_warnings" if not sev["violation"] else "fail"),
                   "counts": sev, "by_check": dict(sorted(by_check.items())), "warnings": warnings[:200],
                   "run": run,
                   "after_this_file": {"checks": list(POST_METADATA_CHECKS),
                                       "note": "run on the finished ZIP after BUILD_INFO.json is written (the ZIP "
                                               "contains this file); results in build/package_reports/<profile>.json "
                                               "and the package.sh check table"},
                   "not_counted": "findings about source-tree copies that do not ship (generated_shadow) are in the "
                                  "build report only"},
        "reproducibility": reproducibility,
    }


def manifest_lines(res: PassResult) -> bytes:
    out = []
    for rel in sorted(res.files):
        if rel == "FILE_MANIFEST.jsonl":
            continue
        f = res.files[rel]
        out.append(json.dumps({"relpath": rel, "bytes": f["bytes"], "sha256": f["sha256"],
                               "role": first_match(ROLE_RULES, rel), "origin": f["origin"],
                               "licence": first_match(LICENCE_RULES, rel)}, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")))
    return ("\n".join(out) + "\n").encode("utf-8")


def _md(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_package_contents(rows: list[dict], header: dict) -> bytes:
    """PACKAGE_CONTENTS.md: the human-readable table of FILE_MANIFEST.jsonl ``rows`` (every entry except the
    manifest and this file). ``header``: zip, version, profile, build_id, platform. Deterministic (no clock)."""
    rows = sorted((r for r in rows if r["relpath"] not in (PACKAGE_CONTENTS, "FILE_MANIFEST.jsonl")),
                  key=lambda r: r["relpath"])
    total = sum(int(r["bytes"]) for r in rows)
    by_role: dict[str, list[int]] = {}
    by_licence: dict[str, list[int]] = {}
    for r in rows:
        by_role.setdefault(r["role"], [0, 0])
        by_role[r["role"]][0] += 1
        by_role[r["role"]][1] += int(r["bytes"])
        by_licence.setdefault(r["licence"], [0, 0])
        by_licence[r["licence"]][0] += 1
        by_licence[r["licence"]][1] += int(r["bytes"])
    out = [f"# PACKAGE_CONTENTS - {header['zip']}", "",
           "| 항목 (item) | 값 (value) |", "|---|---|",
           f"| ZIP | `{header['zip']}` (top-level folder `{TOP}/`) |",
           f"| 제품 (product) | MBNAS Research Console {header['version']} |",
           f"| 버전 (version) | {header['version']} |",
           f"| 프로필 (profile) | {header['profile']} |",
           f"| build id | `{header['build_id']}` |",
           f"| 플랫폼 (platform) | {header['platform']} |",
           f"| 파일 (files) | 아래 표 {len(rows):,}개 ({total:,} bytes) + `{PACKAGE_CONTENTS}`(이 파일) + "
           f"`FILE_MANIFEST.jsonl` = ZIP 항목 {len(rows) + 2:,}개 |", "",
           f"이 파일은 `scripts/dev/package_build.py`가 이 ZIP의 `FILE_MANIFEST.jsonl`에서 생성한다(손으로 고치지 않는다). "
           "정본은 `FILE_MANIFEST.jsonl`(파일마다 relpath, bytes, sha256 전체, role, origin, licence)이고 이 표는 사람이 "
           "읽는 사본이다. 순환을 피하려고 이 표는 자기 자신과 `FILE_MANIFEST.jsonl`을 넣지 않는다. "
           "`FILE_MANIFEST.jsonl`은 자기 자신만 빼고 이 파일까지 모두 나열한다. ZIP 파일 전체의 sha256은 ZIP 옆의 "
           "`<zip>.sha256` 파일에 있다. 빌드 정보와 검사 결과는 `BUILD_INFO.json`, 포함·제외 범위는 `PACKAGE_PROFILE.json`에 "
           "있다. 라이선스 열은 자동 분류이며 법적 검토 결과가 아니다(제3자 라이선스 목록: `THIRD_PARTY_NOTICES.md`, "
           "`THIRD_PARTY_NOTICES_WEB.md`).", "",
           "This file is generated from this ZIP's FILE_MANIFEST.jsonl (the authoritative, machine-readable manifest). "
           "It lists every entry except itself and FILE_MANIFEST.jsonl; the manifest lists every entry except itself. "
           "The licence column is an automatic classification, not a legal review.", "",
           "## 역할별 합계 (totals per role)", "", "| 역할 (role) | 파일 (files) | 바이트 (bytes) |", "|---|---:|---:|"]
    for role in sorted(by_role):
        n, b = by_role[role]
        out.append(f"| {_md(role)} | {n:,} | {b:,} |")
    out += [f"| **합계 (total)** | **{len(rows):,}** | **{total:,}** |", "",
            "## 라이선스 표기별 합계 (totals per licence label)", "", "| 라이선스 (licence) | 파일 (files) | 바이트 (bytes) |",
            "|---|---:|---:|"]
    for lic in sorted(by_licence):
        n, b = by_licence[lic]
        out.append(f"| {_md(lic)} | {n:,} | {b:,} |")
    out += ["", "## 전체 목록 (all files)", "",
            "| # | 경로 (path) | 바이트 (bytes) | sha256 (앞 16자) | 역할 (role) | 라이선스 (licence) |",
            "|---:|---|---:|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | `{_md(r['relpath'])}` | {int(r['bytes']):,} | `{r['sha256'][:16]}` | {_md(r['role'])} | "
                   f"{_md(r['licence'])} |")
    out.append("")
    return "\n".join(out).encode("utf-8")


def _manifest_rows(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def write_metadata(opts: Options, al: Allowlist, version: str, ptag: str, top: Path, epoch: int | None,
                   res: PassResult, tree_hash: dict, reproducibility: dict, built_at: str, at_source: str) -> None:
    for name in METADATA + LATE:
        res.files.pop(name, None)
    pd = payload_digest(res)
    seed = f"{version}|{opts.profile}|{ptag}|{pd}|{al.sha256}"
    if opts.reproducible:
        build_id = "r-" + sha256_bytes(seed.encode())[:16]
    else:
        build_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + sha256_bytes(seed.encode())[:8]
    res.meta["build_id"] = build_id
    prof = package_profile_doc(opts, al, version, ptag, build_id, res)
    _put_generated(top, "PACKAGE_PROFILE.json", (json.dumps(prof, indent=1, ensure_ascii=False) + "\n").encode(),
                   epoch, res, "generated:package_build")
    info = build_info_doc(opts, al, version, ptag, build_id, built_at, at_source, res, tree_hash, reproducibility)
    _put_generated(top, "BUILD_INFO.json", (json.dumps(info, indent=1, ensure_ascii=False) + "\n").encode(),
                   epoch, res, "generated:package_build")
    res.meta["build_info"] = info
    _put_generated(top, "FILE_MANIFEST.jsonl", manifest_lines(res), epoch, res, "generated:package_build")
    if opts.profile == "registration":
        generate_file_list(top, epoch, res)
        _put_generated(top, "FILE_MANIFEST.jsonl", manifest_lines(res), epoch, res, "generated:package_build")
    header = {"zip": zip_name(version, opts.profile, ptag), "version": version, "profile": opts.profile,
              "build_id": build_id, "platform": ptag}
    res.meta["contents_header"] = header
    _put_generated(top, PACKAGE_CONTENTS, render_package_contents(_manifest_rows(manifest_lines(res)), header), epoch,
                   res, "generated:package_build")
    _put_generated(top, "FILE_MANIFEST.jsonl", manifest_lines(res), epoch, res, "generated:package_build")


def generate_file_list(top: Path, epoch: int | None, res: PassResult) -> None:
    """docs/registration/FILE_LIST.md of the staged tree, by gen_registration_docs.py, from FILE_MANIFEST.jsonl v1."""
    gen = DEV_DIR / FILE_LIST_GENERATOR
    if not gen.is_file():
        raise ToolError(f"{gen} not found: the registration file list cannot be generated")
    (top / "docs" / "registration").mkdir(parents=True, exist_ok=True)
    target = top / FILE_LIST
    if target.exists():
        target.unlink()
    proc = subprocess.run([sys.executable, "-I", "-B", str(gen), "--only", "file-list", "--root", str(top),
                           "--manifest", str(top / "FILE_MANIFEST.jsonl")], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=300, env=_pip_env(epoch))
    res.meta["file_list_generator"] = {"exit": proc.returncode,
                                       "output": proc.stdout.decode("utf-8", "replace")[-2000:]}
    if proc.returncode != 0 or not target.is_file():
        raise ToolError(f"{FILE_LIST_GENERATOR} --only file-list failed (exit {proc.returncode}): "
                        f"{proc.stdout.decode('utf-8', 'replace')[-800:]}")
    data = target.read_bytes()
    _put_generated(top, FILE_LIST, data, epoch, res, "generated:gen_registration_docs")


# ------------------------------------------------------------------------------------------------------------- zip
def write_zip(top: Path, res: PassResult, dest: Path, epoch: int | None) -> str:
    """Write the staged files (sorted, top folder ``mbnas_application/``, normalised modes) to ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in sorted(res.files):
            path = top / rel
            st = os.stat(path)
            ts = epoch if epoch is not None else max(int(st.st_mtime), NORMALIZED_EPOCH)
            zi = zipfile.ZipInfo(f"{TOP}/{rel}", date_time=time.gmtime(ts)[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = (stat.S_IFREG | res.files[rel]["mode"]) << 16
            zf.writestr(zi, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return sha256_file(dest)


def verify_zip(zpath: Path, res: PassResult) -> list[Finding]:
    res.ran.update(("zip_entries", "manifest"))
    bad: list[Finding] = []
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        if len(names) != len(set(names)):
            bad.append(Finding("zip_duplicate_entry", "violation", zpath.name, "duplicate entry names"))
        corrupt = zf.testzip()
        if corrupt:
            bad.append(Finding("zip_crc", "violation", corrupt, "CRC check failed"))
        expected = {f"{TOP}/{r}" for r in res.files}
        if set(names) != expected:
            bad.append(Finding("zip_entries", "violation", zpath.name,
                               f"entries differ from staging: extra {sorted(set(names) - expected)[:5]}, "
                               f"missing {sorted(expected - set(names))[:5]}"))
        for n in names:
            if not n.startswith(f"{TOP}/") or n.startswith("/") or ".." in n.split("/") or "\\" in n:
                bad.append(Finding("zip_entry_path", "violation", n, "outside the top-level folder or unsafe"))
        listed = {}
        if f"{TOP}/FILE_MANIFEST.jsonl" in names:
            for line in zf.read(f"{TOP}/FILE_MANIFEST.jsonl").decode("utf-8").splitlines():
                e = json.loads(line)
                listed[e["relpath"]] = e
        if "FILE_MANIFEST.jsonl" in listed:
            bad.append(Finding("manifest_self", "violation", "FILE_MANIFEST.jsonl", "manifest lists itself"))
        in_zip = {n[len(TOP) + 1:] for n in names}
        for rel in sorted(set(listed) - in_zip):
            bad.append(Finding("manifest_extra_entry", "violation", rel, "listed in FILE_MANIFEST but not in the ZIP"))
        for n in names:
            rel = n[len(TOP) + 1:]
            if rel == "FILE_MANIFEST.jsonl":
                continue
            e = listed.get(rel)
            data = zf.read(n)
            if e is None:
                bad.append(Finding("manifest_missing_entry", "violation", rel, "not listed in FILE_MANIFEST"))
            elif e["sha256"] != sha256_bytes(data) or e["bytes"] != len(data):
                bad.append(Finding("manifest_mismatch", "violation", rel, "sha256/bytes differ from FILE_MANIFEST"))
        header = res.meta.get("contents_header")
        if header is not None:
            res.ran.add("package_contents")
            member = f"{TOP}/{PACKAGE_CONTENTS}"
            if member not in names:
                bad.append(Finding("package_contents_mismatch", "violation", PACKAGE_CONTENTS, "missing from the ZIP"))
            elif zf.read(member) != render_package_contents(list(listed.values()), header):
                bad.append(Finding("package_contents_mismatch", "violation", PACKAGE_CONTENTS,
                                   "differs from its rendering of the ZIP's FILE_MANIFEST.jsonl"))
    return bad


# ------------------------------------------------------------------------------------------------------------ pass
def build_pass(opts: Options, al: Allowlist, version: str, ptag: str, pass_no: int, epoch: int | None,
               reproducibility: dict, scan_mod, hash_mod,
               snapshot: tuple[Path, list[str], list[Finding], list] | None = None) -> PassResult:
    """One complete build. ``snapshot`` = (directory, relative paths, selection findings): stage those files instead
    of selecting from the tree (the second --reproducible pass re-stages pass 1's source snapshot and inherits its
    selection findings, so a concurrent edit of the tree cannot make the comparison fail or pass by accident)."""
    root = opts.root
    res = PassResult()
    t0 = time.monotonic()
    staging = root / "build" / "staging" / (opts.profile if pass_no == 1 else f"{opts.profile}.pass{pass_no}")
    _safe_rmtree(staging, root)
    top = staging / TOP
    top.mkdir(parents=True)
    res.staging_top = top
    log_dir = root / "build" / "package_logs" / opts.profile
    log_dir.mkdir(parents=True, exist_ok=True)
    if snapshot is None:
        selected = exclude_importers(root, al, select_files(root, al, res), res)
        res.meta["selection_findings"] = list(res.findings)
        stage_files(root, selected, top, epoch, res)
    else:
        selected = list(snapshot[1])
        res.findings.extend(snapshot[2])
        if al.deny_importers:  # the closure was computed by pass 1; same check table in both passes
            res.ran.add("deny_importers")
        res.meta["selection_findings"] = list(snapshot[2])
        res.meta["importer_exclusions"] = snapshot[3]
        stage_files(snapshot[0], selected, top, epoch, res)
    res.meta["selected"] = selected
    res.timings["stage_s"] = round(time.monotonic() - t0, 2)
    res.web_present = "mbnas_web/dist/index.html" in res.files
    res.ran.add("web_dist")
    if not res.web_present:
        has_files = any(r.startswith("mbnas_web/dist/") for r in res.files)
        what = "web dist incomplete (no index.html)" if has_files else "web dist missing (mbnas_web/dist)"
        if al.settings["web_dist"] == "required" and not opts.allow_missing_web:
            res.findings.append(Finding("web_dist_missing", "violation", "mbnas_web/dist/index.html",
                                        what + "; build the web first or pass --allow-missing-web"))
        else:
            res.findings.append(Finding("web_dist_missing", "warning", "mbnas_web/dist/index.html",
                                        what + (" (allowed by --allow-missing-web)" if opts.allow_missing_web
                                                else " (optional for this profile)")))
    check_deny_and_presence(al, res, selected)
    if res.web_present:
        web_bundle_gate(opts.root, top, res)
    # checks of the copied sources first: a violation stops the pass before any wheel is built
    t1 = time.monotonic()
    src_rels = sorted(res.files)
    check_paths(res)
    scans = [check_content(opts, al, top, res, scan_mod, rels=src_rels)]
    check_private(al, top, res, only=src_rels)
    res.timings["checks_sources_s"] = round(time.monotonic() - t1, 2)
    if res.violations():
        return res
    t2 = time.monotonic()
    build_wheels(opts, al, top, pass_no, epoch, res, log_dir)
    res.timings["wheels_s"] = round(time.monotonic() - t2, 2)
    listing = check_wheel_contents(top, res)
    member = al.settings.get("wheel_listing_member")
    if member:
        _put_generated(top, member, render_wheel_listing(opts.profile, version, listing), epoch, res,
                       "generated:package_build")
    t3 = time.monotonic()
    whl_rels = sorted(r for r in res.files if r not in src_rels)
    check_paths(res, only=set(whl_rels))
    for rel in whl_rels:  # generated outputs must not fall under a deny pattern either
        if al.denied(rel):
            res.findings.append(Finding("denied_present", "violation", rel, "generated path matches a [deny] pattern"))
    scans.append(check_content(opts, al, top, res, scan_mod, rels=whl_rels))
    check_private(al, top, res, only=whl_rels)
    check_versions(version, top, res)
    res.meta["content_scan"] = {k: sum(sc[k] for sc in scans) for k in ("files", "bytes", "containers", "hits")}
    res.timings["checks_wheels_s"] = round(time.monotonic() - t3, 2)
    if res.violations():
        return res
    roots = [(pkg, str(top / pkg / "src" / pkg)) for pkg in al.wheels]
    tree_hash = hash_mod.compute_source_tree_hash(roots)
    res.meta["source_tree"] = {k: tree_hash[k] for k in ("algorithm", "sha256", "files")}
    if epoch is not None:
        built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
        at_source = ("SOURCE_DATE_EPOCH" if os.environ.get("SOURCE_DATE_EPOCH")
                     else "normalized (1980-01-01, --reproducible)")
    else:
        built_at, at_source = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "wall clock"
    write_metadata(opts, al, version, ptag, top, epoch, res, tree_hash, reproducibility, built_at, at_source)
    # the generated metadata is checked like the payload; every generated output must be declared
    late = [r for r in METADATA + LATE if r in res.files]
    check_paths(res, only=set(late))
    check_content(opts, al, top, res, scan_mod, rels=late)
    check_private(al, top, res, only=[r for r in LATE if r in res.files])
    res.ran.add("generated_declared")
    for rel, f in sorted(res.files.items()):
        if f["origin"] != "tree" and not al.is_generated(rel):
            res.findings.append(Finding("generated_undeclared", "violation", rel,
                                        "generated output not declared in the allowlist [generated] section"))
    if res.violations():
        return res
    res.zip_tmp = root / "build" / "package_tmp" / f"{opts.profile}.pass{pass_no}.zip"
    res.zip_sha256 = write_zip(top, res, res.zip_tmp, epoch)
    res.findings += verify_zip(res.zip_tmp, res)
    res.timings["total_s"] = round(time.monotonic() - t0, 2)
    return res


def _finalize_reproducible(opts, al, version, ptag, epoch, p1: PassResult, verdict: dict, scan_mod, hash_mod):
    top = p1.staging_top
    roots = [(pkg, str(top / pkg / "src" / pkg)) for pkg in al.wheels]
    tree_hash = hash_mod.compute_source_tree_hash(roots)
    info = p1.meta["build_info"]
    write_metadata(opts, al, version, ptag, top, epoch, p1, tree_hash, verdict, info["built_at"],
                   info["built_at_source"])
    check_content(opts, al, top, p1, scan_mod, rels=[r for r in METADATA + LATE if r in p1.files])
    p1.zip_tmp = opts.root / "build" / "package_tmp" / f"{opts.profile}.final.zip"
    p1.zip_sha256 = write_zip(top, p1, p1.zip_tmp, epoch)
    p1.findings += verify_zip(p1.zip_tmp, p1)


def _publish(opts: Options, version: str, ptag: str, res: PassResult) -> Path:
    """Move the verified ZIP into dist/<profile>/ with its .sha256; earlier outputs of this packager for the same
    profile (another version or platform) are removed so that dist/<profile>/ holds exactly one bundle."""
    dist = opts.root / "dist" / opts.profile
    dist.mkdir(parents=True, exist_ok=True)
    final = dist / zip_name(version, opts.profile, ptag)
    label = "owner-private-runtime" if opts.profile == "owner" else f"{opts.profile}-runtime"
    for old in sorted(dist.glob(f"{TOP}-*-{label}-*.zip*")):
        if old.is_file() and old.name not in (final.name, final.name + ".sha256"):
            old.unlink()
            print(f"[package] removed earlier output {opts.shown(old)}")
    os.replace(res.zip_tmp, final)
    sha_tmp = final.with_name(final.name + ".sha256.partial")
    sha_tmp.write_text(f"{res.zip_sha256}  {final.name}\n", encoding="utf-8")
    os.replace(sha_tmp, final.with_name(final.name + ".sha256"))
    return final


def _write_report(opts: Options, report: dict) -> Path:
    out = opts.root / "build" / "package_reports" / f"{opts.profile}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _write_wheel_listing(opts: Options, version: str, res: PassResult) -> Path | None:
    """``build/package_reports/<profile>_wheels.txt``: same text as a listing shipped through wheel_listing_member."""
    listing = res.meta.get("wheel_listing")
    if not listing:
        return None
    out = opts.root / "build" / "package_reports" / f"{opts.profile}_wheels.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render_wheel_listing(opts.profile, version, listing))
    return out


def _print_checks(rows: list[dict]) -> None:
    for r in rows:
        print(f"[package] check {r['id']:<19} {r['status'].upper():<7} violations {r['violations']}, warnings "
              f"{r['warnings']}, info {r['info']} - {r['what']}")


def _print_findings(findings: list[Finding], limit: int = 60) -> None:
    order = {"violation": 0, "warning": 1, "info": 2}
    shown = 0
    for f in sorted(findings, key=lambda f: (order[f.severity], f.check, f.path)):
        if f.severity == "info" and shown >= limit:
            continue
        print(f"[package] {f.severity.upper():<9} {f.check}: {f.path} — {f.detail}")
        shown += 1


def build(opts: Options) -> int:
    scan_mod, hash_mod = _load("canary_scan"), _load("source_tree_hash")
    root = opts.root
    al = load_allowlist(root / "build" / "allowlists" / f"{opts.profile}.txt", opts.profile)
    vfile = root / "VERSION"
    if not vfile.is_file():
        raise UsageError(f"{vfile} not found")
    version = vfile.read_text(encoding="utf-8").strip()
    if not VERSION_RE.match(version):
        raise UsageError(f"VERSION {version!r} is not a version string")
    ptag = platform_tag(opts.platform)
    if opts.manifest_path is None:
        opts.manifest_path = root / MANIFEST_REL
    epoch = None
    if opts.reproducible:
        env_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        epoch = max(int(env_epoch), NORMALIZED_EPOCH) if env_epoch and env_epoch.isdigit() else NORMALIZED_EPOCH
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.monotonic()
    print(f"[package] profile={opts.profile} version={version} platform={ptag} reproducible={opts.reproducible} "
          f"canary_test_build={opts.canary_mode} root={opts.shown(root)}")
    pending = {"requested": True, "status": "pending (verdict written after the second build)"} if opts.reproducible \
        else {"requested": False}
    p1 = build_pass(opts, al, version, ptag, 1, epoch, pending, scan_mod, hash_mod)
    passes = [p1]
    verdict = pending
    if opts.reproducible and not p1.violations():
        p2 = build_pass(opts, al, version, ptag, 2, epoch, pending, scan_mod, hash_mod,
                        snapshot=(p1.staging_top, p1.meta["selected"], p1.meta["selection_findings"],
                                  p1.meta.get("importer_exclusions", [])))
        passes.append(p2)
        if not p2.violations():
            equal = p1.zip_sha256 == p2.zip_sha256
            verdict = {"requested": True, "builds": 2, "equal": equal,
                       "zip_sha256": [p1.zip_sha256, p2.zip_sha256],
                       "payload_sha256": [payload_digest(p1), payload_digest(p2)],
                       "method": "two complete builds (staging, wheels, metadata, zip) with fixed timestamps; the "
                                 "second re-stages the first build's source snapshot; ZIP digests compared before "
                                 "this verdict was written into BUILD_INFO",
                       "timestamp_epoch": epoch}
            if not equal:
                diff = sorted(r for r in set(p1.files) | set(p2.files)
                              if p1.files.get(r, {}).get("sha256") != p2.files.get(r, {}).get("sha256"))
                p1.findings.append(Finding("reproducibility_mismatch", "violation", "zip",
                                           f"two builds differ; differing entries: {diff[:20]}"))
            else:
                _finalize_reproducible(opts, al, version, ptag, epoch, p1, verdict, scan_mod, hash_mod)
                _safe_rmtree(p2.staging_top.parent, root)
            if p2.zip_tmp and p2.zip_tmp.exists():
                p2.zip_tmp.unlink()
        else:
            p1.findings += [Finding(f.check, f.severity, f"(pass 2) {f.path}", f.detail) for f in p2.violations()]
    findings = p1.findings
    violations = [f for f in findings if f.severity == "violation"]
    published = None
    if not violations and p1.zip_tmp is not None:
        published = _publish(opts, version, ptag, p1)
    elif p1.zip_tmp is not None and p1.zip_tmp.exists():
        p1.zip_tmp.unlink()
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ("violation", "warning", "info")}
    ran = set().union(*(p.ran for p in passes))
    if opts.reproducible and isinstance(verdict.get("equal"), bool):
        ran.add("reproducibility")
    checks = check_table(findings, ran)
    wpath = _write_wheel_listing(opts, version, p1)
    listing = p1.meta.get("wheel_listing") or []
    report = {"schema": "mbnas.package_report/1", "profile": opts.profile, "version": version, "platform": ptag,
              "started_at": started, "duration_s": round(time.monotonic() - t0, 2),
              "reproducible": opts.reproducible, "canary_test_build": opts.canary_mode,
              "status": "built" if published else "failed", "counts": counts, "checks": checks,
              "zip": str(published.relative_to(root)) if published else None,
              "zip_sha256": p1.zip_sha256 if published else None,
              "build_id": p1.meta.get("build_id"), "source_tree": p1.meta.get("source_tree"),
              "content_scan": p1.meta.get("content_scan"), "reproducibility": verdict,
              "wheel_contents": [{k: w[k] for k in ("wheel", "sha256", "bytes", "status", "missing", "extra", "mismatch",
                                                    "package_data_declared", "package_data")}
                                 | {"members": len(w["members"])} for w in listing],
              "wheel_listing": str(wpath.relative_to(root)) if wpath else None,
              "timings": [p.timings for p in passes], "staged_files": len(p1.files),
              "findings": [f.as_dict() for f in findings]}
    rpath = _write_report(opts, report)
    _print_findings(findings)
    _print_checks(checks)
    skipped = [c["id"] for c in checks if c["status"] == "not run"]
    print(f"[package] checks: {counts['violation']} violation(s), {counts['warning']} warning(s), "
          f"{counts['info']} info; {len(checks) - len(skipped)} of {len(checks)} checks ran "
          f"({', '.join(skipped) + ' not run' if skipped else 'all ran'}); report {opts.shown(rpath)}")
    if wpath:
        print(f"[package] wheel listing {opts.shown(wpath)}: " + ", ".join(
            f"{w['wheel'].split('/')[-1]} {w['status']} ({len(w['members'])} members, "
            f"{sum(1 for d in w['package_data'] if d['in_wheel'])}/{len(w['package_data'])} package-data)"
            for w in listing))
    if opts.reproducible and isinstance(verdict.get("equal"), bool):
        print(f"[package] reproducible: two builds {'IDENTICAL' if verdict['equal'] else 'DIFFER'} "
              f"({verdict['zip_sha256'][0][:16]} / {verdict['zip_sha256'][1][:16]})")
    if not published:
        print(f"[package] FAILED: nothing published for profile {opts.profile}")
        return EXIT_VIOLATION
    print(f"[package] staged {len(p1.files)} files in {opts.shown(p1.staging_top)}; wheels: "
          f"{', '.join(w['wheel'].split('/')[-1] for w in p1.wheels)}")
    print(f"[package] zip {opts.shown(published)} ({published.stat().st_size} bytes) sha256={p1.zip_sha256}")
    return EXIT_OK


def build_canary(opts: Options) -> int:
    """Canary test build: copy + plant (canary_plant.py) -> build the profile in the copy -> scan the ZIP."""
    plant_mod, scan_mod = _load("canary_plant"), _load("canary_scan")
    root = opts.root
    tree = root / "build" / "canary" / opts.profile / "tree"  # per profile: parallel profiles do not collide
    manifest = root / MANIFEST_REL
    try:
        stats = plant_mod.make_tree(tree, source=root)
        doc = plant_mod.plant(tree, manifest, rotate=False, source=root)
    except plant_mod.Refused as exc:
        raise UsageError(f"canary test build refused: {exc}") from exc
    planted = {a["area"]: [p["path"] for p in a["planted_in"] if p.get("status") == "planted"] for a in doc["areas"]}
    print(f"[package] canary tree {tree.relative_to(root)}: {stats['files']} files; planted "
          + ", ".join(f"{k}={len(v)}" for k, v in planted.items()))
    sub = Options(profile=opts.profile, root=tree, platform=opts.platform, reproducible=opts.reproducible,
                  allow_missing_web=opts.allow_missing_web, canary=False, canary_mode=True, manifest_path=manifest,
                  display_root=root)
    rc = build(sub)
    if rc != EXIT_OK:
        return rc
    zpath = tree / "dist" / opts.profile / zip_name((tree / "VERSION").read_text().strip(), opts.profile,
                                                     platform_tag(opts.platform))
    needles = scan_mod.build_needles(scan_mod.load_manifest(manifest), manifest_path=str(manifest))
    report = scan_mod.scan_targets([str(zpath)], needles, profile=opts.profile,
                                   expect_present=opts.profile == "owner", min_files=10)
    out = zpath.with_name(zpath.name + ".canary.json")
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    text = scan_mod.format_summary(report, limit=40)
    for line in text.splitlines():
        print(f"[canary] {line}")
    print(f"[canary] report {opts.shown(out)}")
    return EXIT_OK if report["status"] == "PASS" else EXIT_VIOLATION


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True, choices=PROFILES)
    ap.add_argument("--platform", default="auto", help="platform tag of the ZIP name (default auto: this host)")
    ap.add_argument("--reproducible", action="store_true", help="fixed timestamps, build twice, compare digests")
    ap.add_argument("--allow-missing-web", action="store_true",
                    help="build even when mbnas_web/dist is missing (recorded in BUILD_INFO)")
    ap.add_argument("--canary", action="store_true",
                    help="T8 test build in build/canary/<profile>/tree with planted tokens, then scan the ZIP")
    ap.add_argument("--root", default=str(PROJECT_ROOT), help="source tree root (default: this project)")
    a = ap.parse_args(argv)
    opts = Options(profile=a.profile, root=Path(a.root).resolve(), platform=a.platform, reproducible=a.reproducible,
                   allow_missing_web=a.allow_missing_web, canary=a.canary)
    try:
        return build_canary(opts) if opts.canary else build(opts)
    except UsageError as exc:
        print(f"[package] error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ToolError as exc:
        print(f"[package] build tool failure: {exc}", file=sys.stderr)
        return EXIT_TOOL


if __name__ == "__main__":
    sys.exit(main())
