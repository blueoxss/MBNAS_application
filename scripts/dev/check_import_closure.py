#!/usr/bin/env python3
"""Import-closure, trust-boundary and forbidden-string checker for the five MBNAS packages (WP-1 contract §13.2, DD-A25).

Static only (``ast``; nothing is imported or executed). Scope:

* package sources ``<pkg>/src/<pkg>/**/*.py`` for mbnas_contracts, mbnas_model_runtime, mbnas_core, mbnas_runner,
  mbnas_server — every import statement, including function-level (lazy) imports, plus string module references:
  string literals that are exactly a dotted module reference to one of the five packages (``importlib.import_module``
  targets, ``-m`` argv, ``module:function`` handler/hook strings, ``module.Class`` import strings, ``module.`` prefixes)
  and the code of ``-c`` argv strings;
* host-class files: ``scripts/runner/**`` (runner host), ``scripts/public/**`` and ``tests/public/**`` (public, includes
  the gateway tools), ``scripts/owner/**`` and ``scripts/dev/**`` (owner; boundary-free, resolution only). The two dev
  tools ``rewrite_imports.py`` and this checker are excluded: their string tables are module-path data, not references.

Rules (rule ids in the output):

* ``i``    a public package (contracts, model_runtime, runner, server) imports mbnas_core;
* ``ii``   mbnas_contracts imports another internal package or a third-party module other than pydantic / PyYAML;
* ``iii``  mbnas_core imports mbnas_runner (no allowance: the WP-1 transitional edges expired with WP-2);
* ``iv``   layer inversion: model_runtime -> runner|server, runner -> server, server -> runner, core -> server;
* ``deps`` an internal import not declared in the importer's pyproject dependencies (contract §2), e.g. server ->
  model_runtime;
* ``host`` a runner-host / public script or public test imports a package its host does not install
  (runner host: contracts, model_runtime, runner; public: no mbnas_core);
* ``abs``  a relative import in a package source (contract §3.2: all imports absolute);
* ``resolve`` an internal module or imported name that does not exist in the tree (static: module file/package
  present, name bound at the module's top level or a submodule) — the list of pending symbol moves;
* ``forbidden`` forbidden strings in package sources (any file under ``*/src/``): ``/root/detection``,
  ``/data/mgjeong``, ``147.46.114.253``, ``pc2300``, ``mgjeong``, ``MBNAS_server``, ``etri_nas_root``, and any import
  of ``yolo_ga_runtime`` (statement or string module reference).

The WP-1 transitional allowance (owner_worker.child/contract/main -> mbnas_runner; DD-A14/DD-A25/DD-A27) expired
with WP-2 and was removed. DD-A26 documented data literals (``DATA_EXCEPTIONS``; the remaining one expires with
1.6 - DD-A26 extended by DD-A58) are listed under ``excepted`` and do not fail the run.

Output: one JSON document on stdout (``--format text`` for a readable listing). Exit 0 = no violation, no unresolved
reference, no forbidden string; 1 = findings; 2 = usage/internal error. ``--no-resolve`` skips the ``resolve`` rule.
Importable: ``run(root, resolve=True, include_web=False) -> dict``.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "mbnas.import_closure/1"
PACKAGES = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_core", "mbnas_runner", "mbnas_server")
PUBLIC = {"mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server"}
DECLARED = {  # contract §2 runtime dependencies between the five packages
    "mbnas_contracts": set(),
    "mbnas_model_runtime": {"mbnas_contracts"},
    "mbnas_core": {"mbnas_contracts", "mbnas_model_runtime"},
    "mbnas_runner": {"mbnas_contracts", "mbnas_model_runtime"},
    "mbnas_server": {"mbnas_contracts"},
}
CONTRACTS_THIRD_PARTY = {"pydantic", "yaml"}  # DD-A10
HOSTS = (  # (directory relative to root, host class, allowed internal packages or None = unrestricted)
    ("scripts/runner", "runner-host", {"mbnas_contracts", "mbnas_model_runtime", "mbnas_runner"}),
    ("scripts/public", "public", PUBLIC),
    ("tests/public", "public", PUBLIC),
    ("scripts/owner", "owner", None),
    ("scripts/dev", "owner", None),
)
# tooling whose string tables describe module paths of both layouts (data, not references); they import no package
TOOLING_EXCLUDED = ("scripts/dev/rewrite_imports.py", "scripts/dev/check_import_closure.py")
# DD-A36: the gateway (mbnas_server) builds dataset manifests on the data host; exactly these model_runtime modules
# (and nothing else of mbnas_model_runtime) are allowed for mbnas_server. mbnas_core / mbnas_runner stay forbidden.
SERVER_RUNTIME_MODULES = ("mbnas_model_runtime.datasets.yaml_safe", "mbnas_model_runtime.datasets.manifest")
# WP-1 transitional allowances (owner_worker.child/contract/main -> mbnas_runner; DD-A14/A25/A27) expired with
# WP-2 (WP-2V round 1): every mbnas_core -> mbnas_runner edge is now a rule ``iii`` violation.
TRANSITIONAL_ALLOW: tuple = ()
# DD-A26 documented data literals (expiry 1.6: DD-A26 extended by DD-A58): exactly these (file, literal) pairs are
# data, not references; a forbidden-string hit or a string module reference of that literal in that file is listed
# under ``excepted``
# (DD-A26 (1) openapi.json MBNAS_server entry dropped in the P stage: the 1.5.0 contract no longer carries it.)
DATA_EXCEPTIONS = (
    {"file": "mbnas_model_runtime/src/mbnas_model_runtime/adapters/base.py",
     "literal": "mbnas_server.search.etri_bridge:ensure_mixed_registered",
     "expires": "1.6", "basis": "DD-A26 (2), extended to 1.6 by DD-A58 (public ZIP compatibility shim, DD-A12): "
                                "legacy REGISTER_HOOKS key mapped to operators.ensure_registered"},
)
FORBIDDEN_SUBSTRINGS = ("/root/detection", "/data/mgjeong", "147.46.114.253", "pc2300", "mgjeong", "MBNAS_server",
                        "etri_nas_root")
RE_REF = re.compile(r"^(mbnas_(?:contracts|model_runtime|core|runner|server))((?:\.[A-Za-z_]\w*)*)(\.?)(:[A-Za-z_]\w*)?$")
RE_ETRI_REF = re.compile(r"^yolo_ga_runtime(?:\.[A-Za-z_]\w*)*\.?$")
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


@dataclass
class Ref:
    file: str
    line: int
    importer: str              # dotted module of the file (package sources) or the relative path (scripts)
    target: str                # dotted module referenced
    names: list[str]
    kind: str                  # import | string | string-c
    lazy: bool
    dash_m: bool = False       # string came right after "-m"


def module_name(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _doc_ids(tree: ast.AST) -> set[int]:
    return {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _lazy(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return True
        cur = parents.get(id(cur))
    return False


def _resolve_relative(module: str, is_pkg: bool, level: int, name: str | None) -> str:
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    if level > 1:
        base = base[: max(0, len(base) - (level - 1))]
    return ".".join(base + ([name] if name else []))


class Tree:
    """The five packages as found on disk: module -> file, and statically bound top-level names."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, Path] = {}
        self.src_roots: dict[str, Path] = {}
        for pkg in PACKAGES:
            src = root / pkg / "src"
            self.src_roots[pkg] = src
            base = src / pkg
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                self.files[module_name(path, src)] = path
        self._bound: dict[str, tuple[set[str], bool]] = {}

    def is_package(self, module: str) -> bool:
        path = self.files.get(module)
        return path is not None and path.name == "__init__.py"

    def bound(self, module: str) -> tuple[set[str], bool]:
        if module not in self._bound:
            try:
                tree = ast.parse(self.files[module].read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                self._bound[module] = (set(), True)
                return self._bound[module]
            names: set[str] = set()
            dynamic = False

            def targets(node: ast.AST) -> None:
                for n in ast.walk(node):
                    if isinstance(n, ast.Name):
                        names.add(n.id)

            def visit(stmts: list[ast.stmt]) -> None:
                nonlocal dynamic
                for st in stmts:
                    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(st.name)
                    elif isinstance(st, ast.Assign):
                        for t in st.targets:
                            targets(t)
                    elif isinstance(st, (ast.AnnAssign, ast.AugAssign)):
                        targets(st.target)
                    elif isinstance(st, ast.Import):
                        for a in st.names:
                            names.add(a.asname or a.name.split(".")[0])
                    elif isinstance(st, ast.ImportFrom):
                        for a in st.names:
                            if a.name == "*":
                                dynamic = True
                            else:
                                names.add(a.asname or a.name)
                    elif isinstance(st, (ast.For, ast.AsyncFor)):
                        targets(st.target)
                        visit(st.body)
                        visit(st.orelse)
                    elif isinstance(st, (ast.With, ast.AsyncWith)):
                        for item in st.items:
                            if item.optional_vars is not None:
                                targets(item.optional_vars)
                        visit(st.body)
                    elif isinstance(st, (ast.If, ast.While)):
                        visit(st.body)
                        visit(st.orelse)
                    elif isinstance(st, ast.Try):
                        visit(st.body)
                        visit(st.orelse)
                        visit(st.finalbody)
                        for h in st.handlers:
                            visit(h.body)

            visit(tree.body)
            self._bound[module] = (names, dynamic or "__getattr__" in names)
        return self._bound[module]

    def check(self, target: str, names: list[str], *, dash_m: bool = False) -> list[tuple[str, str]]:
        """``[(missing, reason)]`` for an internal reference (empty when everything resolves)."""
        missing = []
        if target not in self.files:
            # `module.Attr` string references: longest existing module prefix + bound attribute
            parts = target.split(".")
            for i in range(len(parts) - 1, 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in self.files:
                    attr = parts[i]
                    sub = f"{prefix}.{attr}"
                    bound, dynamic = self.bound(prefix)
                    if sub not in self.files and attr not in bound and not dynamic:
                        missing.append((target, f"module {target} not found and {attr!r} is not bound in {prefix}"))
                    return missing
            return [(target, "module not found")]
        if dash_m and self.is_package(target) and f"{target}.__main__" not in self.files:
            missing.append((target, "`-m` target package has no __main__"))
        bound, dynamic = self.bound(target)
        for name in names:
            if f"{target}.{name}" in self.files or name in bound or dynamic:
                continue
            missing.append((f"{target}:{name}", f"name {name!r} is not bound in {target}"))
        return missing


def collect_refs(path: Path, rel: str, importer: str, is_pkg: bool, *, relative_ok: bool
                 ) -> tuple[list[Ref], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(internal refs, relative imports, third-party imports, etri references)`` of one file."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=rel)
    parents, docs = _parents(tree), _doc_ids(tree)
    refs: list[Ref] = []
    relative: list[dict[str, Any]] = []
    third: list[dict[str, Any]] = []
    etri: list[dict[str, Any]] = []

    def add_import(node: ast.AST, module: str, names: list[str], kind: str, line: int) -> None:
        top = module.split(".")[0]
        if top in PACKAGES:
            refs.append(Ref(rel, line, importer, module, names, kind, _lazy(node, parents)))
        elif top == "yolo_ga_runtime":
            etri.append({"file": rel, "line": line, "module": module, "kind": kind})
        elif top not in STDLIB and top:
            third.append({"file": rel, "line": line, "module": module, "kind": kind})

    def imports_of(node: ast.AST, kind: str, line_of: Any) -> None:
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                for a in n.names:
                    add_import(n, a.name, [], kind, line_of(n))
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    if relative_ok:  # host files (scripts/tests) are not packages of the five distributions
                        continue
                    relative.append({"file": rel, "line": line_of(n), "statement": "." * n.level + (n.module or "")})
                    mod = _resolve_relative(importer, is_pkg, n.level, n.module)
                else:
                    mod = n.module or ""
                add_import(n, mod, [a.name for a in n.names if a.name != "*"], kind, line_of(n))

    imports_of(tree, "import", lambda n: n.lineno)

    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):  # "-m" module / "-c" code in argv displays
            elts = node.elts
            for i, el in enumerate(elts[:-1]):
                nxt = elts[i + 1]
                if not (isinstance(el, ast.Constant) and isinstance(nxt, ast.Constant) and isinstance(nxt.value, str)):
                    continue
                if el.value == "-c":
                    try:
                        code = ast.parse(nxt.value)
                    except SyntaxError:
                        continue
                    imports_of(code, "string-c", lambda _n, ln=nxt.lineno: ln)
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
            continue
        value = node.value
        line = node.lineno
        if isinstance(parents.get(id(node)), ast.JoinedStr):
            line = parents[id(node)].lineno
        m = RE_REF.match(value)
        if m:
            dash_m = False
            par = parents.get(id(node))
            if isinstance(par, (ast.List, ast.Tuple)):
                idx = next((k for k, e in enumerate(par.elts) if e is node), None)
                prev = par.elts[idx - 1] if idx else None
                dash_m = isinstance(prev, ast.Constant) and prev.value == "-m"
            module = m.group(1) + m.group(2)
            names = [m.group(4)[1:]] if m.group(4) else []
            refs.append(Ref(rel, line, importer, module, names, "string", _lazy(node, parents), dash_m))
        elif RE_ETRI_REF.match(value):
            etri.append({"file": rel, "line": line, "module": value, "kind": "string"})
    return refs, relative, third, etri


def _edge_rule(src: str, dst: str) -> str | None:
    if src == dst:
        return None
    if src == "mbnas_contracts":
        return "ii"
    if dst == "mbnas_core":
        return "i"
    if src == "mbnas_core" and dst == "mbnas_runner":
        return "iii"
    if (src == "mbnas_model_runtime" and dst in ("mbnas_runner", "mbnas_server")) or \
            (src, dst) in (("mbnas_runner", "mbnas_server"), ("mbnas_server", "mbnas_runner"),
                           ("mbnas_core", "mbnas_server")):
        return "iv"
    if dst not in DECLARED[src]:
        return "deps"
    return None


def _allowed(importer: str, target_pkg: str, kind: str, names: list[str] | None = None) -> dict[str, Any] | None:
    for entry in TRANSITIONAL_ALLOW:
        if importer == entry["importer"] and target_pkg == entry["target_package"] \
                and kind.split("-")[0] in entry["kinds"] \
                and ("names" not in entry or sorted(names or []) == sorted(entry["names"])):
            return entry
    return None


def _data_exception(rel: str, *, literal_in: str | None = None, ref: "Ref | None" = None) -> dict[str, Any] | None:
    """DD-A26: the documented (file, literal) pair that covers a forbidden-string line or a string reference."""
    for entry in DATA_EXCEPTIONS:
        if rel != entry["file"]:
            continue
        if literal_in is not None and entry["literal"] in literal_in:
            return entry
        if ref is not None and ref.kind.startswith("string") and entry["literal"].split(":")[0] == ref.target:
            return entry
    return None


def scan_forbidden(base: Path, root: Path) -> list[dict[str, Any]]:
    hits = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for pat in FORBIDDEN_SUBSTRINGS:
                if pat in line:
                    hits.append({"file": str(path.relative_to(root)), "line": no, "pattern": pat,
                                 "text": line.strip()[:160]})
    return hits


def run(root: Path, *, resolve: bool = True, include_web: bool = False) -> dict[str, Any]:
    root = root.resolve()
    tree = Tree(root)
    violations: list[dict[str, Any]] = []
    allowlisted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    forbidden: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    excepted: list[dict[str, Any]] = []
    third_by_pkg: dict[str, set[str]] = {p: set() for p in PACKAGES}
    n_pkg = n_host = 0

    def refd(ref: Ref, **extra: Any) -> dict[str, Any]:
        return {"file": ref.file, "line": ref.line, "importer": ref.importer, "target": ref.target,
                "names": ref.names, "kind": ref.kind, "lazy": ref.lazy, **extra}

    def resolve_refs(refs: list[Ref]) -> None:
        if not resolve:
            return
        for ref in refs:
            for missing, reason in tree.check(ref.target, ref.names, dash_m=ref.dash_m):
                unresolved.append({"rule": "resolve", "file": ref.file, "line": ref.line, "importer": ref.importer,
                                   "missing": missing, "reason": reason, "kind": ref.kind})

    for module, path in sorted(tree.files.items()):
        n_pkg += 1
        rel = str(path.relative_to(root))
        pkg = module.split(".")[0]
        try:
            refs, relative, third, etri = collect_refs(path, rel, module, path.name == "__init__.py", relative_ok=False)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for item in relative:
            violations.append({"rule": "abs", **item, "importer": module})
        for item in etri:
            forbidden.append({"rule": "forbidden", "file": item["file"], "line": item["line"],
                              "pattern": "yolo_ga_runtime import", "text": item["module"], "kind": item["kind"]})
        for item in third:
            third_by_pkg[pkg].add(item["module"].split(".")[0])
            if pkg == "mbnas_contracts" and item["module"].split(".")[0] not in CONTRACTS_THIRD_PARTY:
                violations.append({"rule": "ii", "file": item["file"], "line": item["line"], "importer": module,
                                   "target": item["module"], "names": [], "kind": item["kind"], "lazy": None,
                                   "detail": "third-party import outside pydantic/PyYAML"})
        kept = []
        for ref in refs:
            exc_entry = _data_exception(rel, ref=ref)
            if exc_entry is not None:
                excepted.append(refd(ref, rule="data-exception", literal=exc_entry["literal"],
                                     expires=exc_entry["expires"]))
                continue
            kept.append(ref)
        refs = kept
        for ref in refs:
            rule = _edge_rule(pkg, ref.target.split(".")[0])
            if rule == "deps" and pkg == "mbnas_server" and ref.target in SERVER_RUNTIME_MODULES:
                rule = None  # DD-A36
            if rule is None:
                continue
            entry = _allowed(module, ref.target.split(".")[0], ref.kind, ref.names)
            if entry is not None and rule == "iii":
                allowlisted.append(refd(ref, rule=rule, allowance=f"{entry['importer']} -> {entry['target_package']}.*"
                                                                  f" (expires {entry['expires']})"))
            else:
                violations.append(refd(ref, rule=rule))
        resolve_refs(refs)

    for rel_dir, host, allowed in HOSTS:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or str(path.relative_to(root)) in TOOLING_EXCLUDED:
                continue
            n_host += 1
            rel = str(path.relative_to(root))
            try:
                refs, _rel, _third, _etri = collect_refs(path, rel, rel, False, relative_ok=True)
            except (SyntaxError, UnicodeDecodeError) as exc:
                errors.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for ref in refs:
                if allowed is not None and ref.target.split(".")[0] not in allowed:
                    violations.append(refd(ref, rule="host", host=host))
            resolve_refs(refs)

    for pkg in PACKAGES:
        base = root / pkg / "src"
        if base.is_dir():
            for h in scan_forbidden(base, root):
                exc_entry = _data_exception(h["file"], literal_in=h["text"])
                if exc_entry is not None and h["pattern"] in exc_entry["literal"]:
                    excepted.append({"rule": "data-exception", **h, "literal": exc_entry["literal"],
                                     "expires": exc_entry["expires"]})
                else:
                    forbidden.append({"rule": "forbidden", **h})
    web = []
    if include_web and (root / "mbnas_web" / "src").is_dir():
        web = [{"rule": "forbidden", **h} for h in scan_forbidden(root / "mbnas_web" / "src", root)]
        forbidden.extend(web)

    by_rule: dict[str, int] = {}
    for v in violations + unresolved + forbidden:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    ok = not violations and not unresolved and not forbidden and not errors
    return {
        "schema": SCHEMA,
        "root": str(root),
        "ok": ok,
        "scanned": {"package_modules": n_pkg, "host_files": n_host, "web_included": include_web,
                    "resolve_checked": resolve, "excluded_tooling": list(TOOLING_EXCLUDED)},
        "allowlist": list(TRANSITIONAL_ALLOW),
        "data_exceptions": list(DATA_EXCEPTIONS),
        "counts": {"violations": len(violations), "unresolved": len(unresolved), "forbidden_strings": len(forbidden),
                   "allowlisted": len(allowlisted), "excepted": len(excepted), "errors": len(errors), "by_rule": dict(sorted(by_rule.items()))},
        "violations": violations,
        "allowlisted": allowlisted,
        "excepted": excepted,
        "unresolved": unresolved,
        "forbidden_strings": forbidden,
        "errors": errors,
        "third_party_by_package": {p: sorted(v) for p, v in third_by_pkg.items()},
    }


def _text(result: dict[str, Any]) -> str:
    out = [f"import closure: {'OK' if result['ok'] else 'FINDINGS'} ({result['root']})",
           f"counts: {json.dumps(result['counts'])}", "transitional allowlist:"]
    out += [f"  {a['importer']} -> {a['target_package']}.* ({', '.join(a['kinds'])}; expires {a['expires']})"
            for a in result["allowlist"]]
    out.append("documented data exceptions (DD-A26):")
    out += [f"  {e['file']}: {e['literal']!r} (expires {e['expires']})" for e in result["data_exceptions"]]
    for key in ("violations", "allowlisted", "excepted", "unresolved", "forbidden_strings", "errors"):
        out.append(f"{key}: {len(result[key])}")
        for item in result[key]:
            if key == "forbidden_strings" or (key == "excepted" and "pattern" in item):
                out.append(f"  [{item['pattern']}] {item['file']}:{item['line']}  {item['text']}")
            elif key == "unresolved":
                out.append(f"  {item['file']}:{item['line']}  {item['missing']}  ({item['reason']})")
            elif key == "errors":
                out.append(f"  {item['file']}: {item['error']}")
            else:
                names = f" ({', '.join(item['names'])})" if item.get("names") else ""
                out.append(f"  [{item['rule']}] {item['file']}:{item['line']}  {item.get('importer')} -> "
                           f"{item.get('target', item.get('statement'))}{names} [{item.get('kind', 'import')}"
                           f"{', lazy' if item.get('lazy') else ''}]")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]), help="TARGET_ROOT (default: %(default)s)")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    ap.add_argument("--out", default=None, help="also write the JSON result to this file")
    ap.add_argument("--no-resolve", action="store_true", help="skip the static resolution rule")
    ap.add_argument("--include-web", action="store_true", help="also scan mbnas_web/src for forbidden strings")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"no such root: {root}", file=sys.stderr)
        return 2
    try:
        result = run(root, resolve=not args.no_resolve, include_web=args.include_web)
    except Exception as exc:  # noqa: BLE001 - report and exit 2 instead of a partial result
        print(f"checker error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    doc = json.dumps(result, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(doc + "\n", encoding="utf-8")
    print(doc if args.format == "json" else _text(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
