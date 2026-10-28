"""Argv-only process spawning in the package sources (C05 D9; DD-A20, SECURITY_BASELINE R10).

Static (``ast``; nothing is imported or executed). Every ``*.py`` under ``<package>/src/`` of the five packages present
in this tree (a public bundle has no private core sources) is parsed and every call is checked:

* ``shell``: no ``shell=`` keyword other than the literal ``False`` (also through ``**{"shell": ...}``), on any call;
* ``string-api``: none of the string-command APIs ``os.system``, ``os.popen``, ``subprocess.getoutput``,
  ``subprocess.getstatusoutput``, ``asyncio.create_subprocess_shell``, ``<loop>.subprocess_shell``;
* ``string-command``: the command of an argv API (``subprocess.run/call/check_call/check_output/Popen``,
  ``os.execv*``, ``os.spawnv*``, ``os.posix_spawn*``) is never a string (literal, f-string, ``%``/``+`` formatting,
  ``.format``, ``join``, ``str()``), also not through a local name bound to one;
* ``shell-argv``: no shell interpreter with ``-c`` as the program of an argv (``["bash", "-c", ...]``), also for
  ``os.execl*``/``os.spawnl*``/``asyncio.create_subprocess_exec``;
* ``unproven``: the command of an argv API is a list/tuple display, a list comprehension, a concatenation of those, or a
  name / ``self.<method>()`` result that is only ever bound to one; a command whose shape cannot be shown statically
  must be listed in ``REVIEWED_ARGV`` with the reason it is an argv list (entries whose site is gone fail as stale).

The checker runs on synthetic snippets first (each rule must fire, the accepted shapes must pass), and the real scan
must find the runner's own child spawn, so a scan that sees nothing cannot pass by accident.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# composed names: the import-closure checker reads a bare package-name literal in a public test as a reference
PACKAGES = tuple(f"mbnas_{name}" for name in ("contracts", "model_runtime", "core", "runner", "server"))
WATCHED = {"subprocess", "os", "asyncio"}
STRING_APIS = {("os", "system"), ("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "getstatusoutput"),
               ("asyncio", "create_subprocess_shell")}
ARGV_APIS = {  # (module, function) -> index of the argv argument (keyword "args" for subprocess)
    **{("subprocess", n): 0 for n in ("run", "call", "check_call", "check_output", "Popen")},
    **{("os", n): 1 for n in ("execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp")},
    **{("os", n): 2 for n in ("spawnv", "spawnve", "spawnvp", "spawnvpe")},
}
VARARG_APIS = {  # program and arguments as separate positional arguments, from this index on
    **{("os", n): 1 for n in ("execl", "execle", "execlp", "execlpe")},
    **{("os", n): 2 for n in ("spawnl", "spawnle", "spawnlp", "spawnlpe")},
    ("asyncio", "create_subprocess_exec"): 0,
}
SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish"}
# (file, enclosing function, command source) -> why the command is an argv list although the checker cannot show it
REVIEWED_ARGV = {
    ("mbnas_runner/src/mbnas_runner/gpu_guard.py", "main", "cmd"):
        "args.cmd is an argparse positional with nargs=argparse.REMAINDER: always a list of strings",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str
    detail: str


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST], kinds: tuple[type, ...]) -> ast.AST | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, kinds):
            return node
    return None


class _Checker:
    def __init__(self, source: str, rel: str) -> None:
        self.rel = rel
        self.tree = ast.parse(source)
        self.parents = _parents(self.tree)
        self.modules: dict[str, str] = {}  # local name -> watched module
        self.names: dict[str, tuple[str, str]] = {}  # local name -> (watched module, function)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in WATCHED:
                        self.modules[a.asname or a.name] = a.name
                    elif a.asname is None and a.name.split(".")[0] in WATCHED:
                        self.modules[a.name.split(".")[0]] = a.name.split(".")[0]  # import os.path binds os
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in WATCHED:
                for a in node.names:
                    self.names[a.asname or a.name] = (node.module, a.name)
        self.findings: list[Finding] = []
        self.sites: list[str] = []
        self.reviewed: set[tuple[str, str, str]] = set()

    # -- what is called --------------------------------------------------------------------------------------------
    def _api(self, call: ast.Call) -> tuple[str, str] | None:
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in self.modules:
            return self.modules[f.value.id], f.attr
        if isinstance(f, ast.Name) and f.id in self.names:
            return self.names[f.id]
        if isinstance(f, ast.Attribute) and f.attr == "subprocess_shell":  # asyncio event loop method
            return "asyncio", "create_subprocess_shell"
        return None

    # -- shapes ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _stringy(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, (str, bytes))
        if isinstance(expr, ast.JoinedStr):
            return True
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Mod, ast.Add)):
            return _Checker._stringy(expr.left) or (isinstance(expr.op, ast.Add) and _Checker._stringy(expr.right))
        if isinstance(expr, ast.Call):
            f = expr.func
            if isinstance(f, ast.Attribute) and f.attr in ("format", "join", "quote"):
                return True
            if isinstance(f, ast.Name) and f.id == "str":
                return True
        return False

    @staticmethod
    def _scope_nodes(scope: ast.AST):
        """Nodes of a function (nested ones included), or the module's top-level statements outside defs/classes."""
        if isinstance(scope, ast.Module):
            for stmt in scope.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    yield from ast.walk(stmt)
        else:
            yield from ast.walk(scope)

    def _bindings(self, name: str, scope: ast.AST) -> list[tuple[ast.AST, int | None, ast.AST] | None]:
        """Values bound to ``name`` in ``scope``: (value, tuple index or None, scope); None = a parameter / loop / with
        target (shape unknown)."""
        out: list[tuple[ast.AST, int | None, ast.AST] | None] = []
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = scope.args
            if name in {p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []),
                                         *([a.kwarg] if a.kwarg else [])]}:
                out.append(None)
        for node in self._scope_nodes(scope):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        out.append((node.value, None, scope))
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for i, elt in enumerate(target.elts):
                            if isinstance(elt, ast.Name) and elt.id == name:
                                out.append((node.value, i, scope))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name) \
                    and node.target.id == name and node.value is not None:
                out.append((node.value, None, scope))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.withitem)):
                target = node.target if not isinstance(node, ast.withitem) else node.optional_vars
                if any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(target or ast.Pass())):
                    out.append(None)
        return out

    def _method_returns(self, call: ast.Call, site: ast.AST) -> list[ast.AST] | None:
        f = call.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self"):
            return None
        cls = _enclosing(site, self.parents, (ast.ClassDef,))
        if cls is None:
            return None
        for node in cls.body:  # type: ignore[attr-defined]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == f.attr:
                returns = [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None
                           and _enclosing(n, self.parents, (ast.FunctionDef, ast.AsyncFunctionDef)) is node]
                return [node, *returns] if returns else None
        return None

    def _shape(self, expr: ast.AST, scope: ast.AST, depth: int = 0) -> str:
        """``argv`` | ``string`` | ``unknown``."""
        if depth > 6:
            return "unknown"
        if isinstance(expr, (ast.List, ast.Tuple, ast.ListComp)):
            return "argv"
        if self._stringy(expr):
            return "string"
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self._combine([self._shape(expr.left, scope, depth + 1), self._shape(expr.right, scope, depth + 1)])
        if isinstance(expr, ast.IfExp):
            return self._combine([self._shape(expr.body, scope, depth + 1), self._shape(expr.orelse, scope, depth + 1)])
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in ("list", "tuple",
                                                                                                "sorted"):
            return "argv"
        if isinstance(expr, ast.Name):
            bound = self._bindings(expr.id, scope)
            if not bound and scope is not self.tree:
                bound = self._bindings(expr.id, self.tree)  # a module-level constant
            shapes = []
            for b in bound:
                if b is None:
                    shapes.append("unknown")
                else:
                    value, index, where = b
                    shapes.append(self._indexed(value, index, where, depth + 1))
            return self._combine(shapes) if shapes else "unknown"
        return "unknown"

    def _indexed(self, value: ast.AST, index: int | None, scope: ast.AST, depth: int) -> str:
        if index is None:
            return self._shape(value, scope, depth)
        if isinstance(value, (ast.Tuple, ast.List)) and index < len(value.elts):
            return self._shape(value.elts[index], scope, depth)
        if isinstance(value, ast.Call):
            found = self._method_returns(value, scope)
            if found:
                method, *returns = found
                return self._combine([self._indexed(r, index, method, depth + 1) for r in returns])
        return "unknown"

    @staticmethod
    def _combine(shapes: list[str]) -> str:
        if "string" in shapes:
            return "string"
        return "argv" if shapes and all(s == "argv" for s in shapes) else "unknown"

    @staticmethod
    def _shell_c(items: list[ast.AST]) -> bool:
        if not items or not (isinstance(items[0], ast.Constant) and isinstance(items[0].value, str)):
            return False
        if items[0].value.rsplit("/", 1)[-1] not in SHELLS:
            return False
        return any(isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value.startswith("-")
                   and not x.value.startswith("--") and "c" in x.value for x in items[1:])

    # -- the scan ----------------------------------------------------------------------------------------------------
    def run(self) -> "_Checker":
        for call in [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)]:
            where = f"{self.rel}:{call.lineno}"
            for kw in call.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    self.findings.append(Finding("shell", where, ast.unparse(kw.value)))
                elif kw.arg is None and isinstance(kw.value, ast.Dict):
                    for k, v in zip(kw.value.keys, kw.value.values):
                        if isinstance(k, ast.Constant) and k.value == "shell" and not (
                                isinstance(v, ast.Constant) and v.value is False):
                            self.findings.append(Finding("shell", where, "**{'shell': ...}"))
            api = self._api(call)
            if api is None:
                continue
            if api in STRING_APIS:
                self.findings.append(Finding("string-api", where, ".".join(api)))
            elif api in VARARG_APIS:
                self.sites.append(where)
                if self._shell_c(call.args[VARARG_APIS[api]:]):
                    self.findings.append(Finding("shell-argv", where, ast.unparse(call)))
            elif api in ARGV_APIS:
                self.sites.append(where)
                self._argv_site(call, api, where)
        return self

    def _argv_site(self, call: ast.Call, api: tuple[str, str], where: str) -> None:
        index = ARGV_APIS[api]
        cmd = call.args[index] if len(call.args) > index else next(
            (k.value for k in call.keywords if k.arg == "args"), None)
        if cmd is None:
            self.findings.append(Finding("unproven", where, "no command argument found"))
            return
        scope = _enclosing(call, self.parents, (ast.FunctionDef, ast.AsyncFunctionDef)) or self.tree
        shape = self._shape(cmd, scope)
        if isinstance(cmd, (ast.List, ast.Tuple)) and self._shell_c(list(cmd.elts)):
            self.findings.append(Finding("shell-argv", where, ast.unparse(cmd)))
        if shape == "string":
            self.findings.append(Finding("string-command", where, ast.unparse(cmd)))
        elif shape == "unknown":
            key = (self.rel, getattr(scope, "name", "<module>"), ast.unparse(cmd))
            if key in REVIEWED_ARGV:
                self.reviewed.add(key)
            else:
                self.findings.append(Finding("unproven", where, f"{key[1]}(): {key[2]}"))


def check_source(source: str, rel: str = "<snippet>") -> _Checker:
    return _Checker(source, rel).run()


def _files() -> list[Path]:
    out = []
    for pkg in PACKAGES:
        src = ROOT / pkg / "src"
        if src.is_dir():
            out += [p for p in sorted(src.rglob("*.py"))
                    if not any(part == "__pycache__" or part.endswith(".egg-info") for part in p.parts)]
    return out


# -- the checker on synthetic snippets ------------------------------------------------------------------------------
REFUSED = {
    "shell=True": ("import subprocess\nsubprocess.run(['ls'], shell=True)\n", "shell"),
    "shell through **kwargs": ("import subprocess\nsubprocess.run(['ls'], **{'shell': True})\n", "shell"),
    "shell=variable": ("import subprocess\ndef f(s):\n    subprocess.run(['ls'], shell=s)\n", "shell"),
    "os.system": ("import os\nos.system('ls')\n", "string-api"),
    "os.popen imported under an alias": ("from os import popen as p\np('ls')\n", "string-api"),
    "subprocess.getoutput": ("import subprocess as sp\nsp.getoutput('ls')\n", "string-api"),
    "asyncio.create_subprocess_shell": (
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_shell('ls')\n", "string-api"),
    "loop.subprocess_shell": ("async def f(loop):\n    await loop.subprocess_shell(lambda: None, 'ls')\n",
                              "string-api"),
    "string literal": ("import subprocess\nsubprocess.Popen('ls -l')\n", "string-command"),
    "f-string": ("import subprocess\ndef f(x):\n    subprocess.run(f'ls {x}')\n", "string-command"),
    "name bound to a string": ("import subprocess\ndef f(x):\n    cmd = 'ls ' + x\n    subprocess.check_call(cmd)\n",
                               "string-command"),
    "join": ("import subprocess\ndef f(a):\n    subprocess.check_output(' '.join(a))\n", "string-command"),
    "format, imported name": ("from subprocess import call\ncall('ls {}'.format(1))\n", "string-command"),
    "args= keyword": ("import subprocess\nsubprocess.run(args='ls -l')\n", "string-command"),
    "os.execv string": ("import os\nos.execv('/bin/ls', '/bin/ls -l')\n", "string-command"),
    "bash -c argv": ("import subprocess\nsubprocess.run(['bash', '-c', 'ls'])\n", "shell-argv"),
    "/bin/sh -ec argv": ("import subprocess\nsubprocess.run(('/bin/sh', '-ec', 'ls'))\n", "shell-argv"),
    "sh -c through exec": (
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('sh', '-c', 'ls')\n", "shell-argv"),
    "command from a parameter": ("import subprocess\ndef f(cmd):\n    subprocess.run(cmd)\n", "unproven"),
}
ACCEPTED = {
    "list display": "import subprocess\nsubprocess.run(['ls', '-l'], check=True, shell=False)\n",
    "name bound to lists": ("import subprocess\ndef f(x):\n    argv = ['ls', x]\n    argv += ['-l']\n"
                            "    subprocess.Popen(argv)\n"),
    "self.method() tuple": ("import subprocess\nclass S:\n    def argv(self):\n        base = ['py', '-m', 'w']\n"
                            "        if self.u:\n            return ['unshare', '-rn', *base], 'u'\n"
                            "        return base, 'plain'\n    def run(self):\n        argv, iso = self.argv()\n"
                            "        return subprocess.Popen(argv)\n"),
    "concatenation": "import subprocess\nsubprocess.run(['a'] + ['b'], shell=False)\n",
    "exec with separate arguments": (
        "import asyncio\nasync def f(p):\n    await asyncio.create_subprocess_exec('ls', p)\n"),
    "unrelated shell keyword holder": "def f(g):\n    g(shell=False)\n",
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_checker_flags_each_rule(name):
    source, rule = REFUSED[name]
    findings = check_source(source).findings
    assert rule in {f.rule for f in findings}, (name, findings)


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_checker_accepts_argv_shapes(name):
    checker = check_source(ACCEPTED[name])
    assert checker.findings == [], (name, checker.findings)


# -- the package sources --------------------------------------------------------------------------------------------
def test_package_sources_spawn_processes_with_argv_lists_only():
    files = _files()
    present = {p.relative_to(ROOT).parts[0] for p in files}
    assert {"mbnas_contracts", "mbnas_runner"} <= present and len(files) > 50, (sorted(present), len(files))
    findings: list[Finding] = []
    sites: list[str] = []
    reviewed: set[tuple[str, str, str]] = set()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        checker = check_source(path.read_text(encoding="utf-8"), rel)
        findings += checker.findings
        sites += checker.sites
        reviewed |= checker.reviewed
    assert findings == [], "\n".join(f"{f.rule}: {f.where}: {f.detail}" for f in findings)
    # the scan sees real call sites: the runner daemon spawns its job child with an argv list
    assert any(s.startswith("mbnas_runner/src/mbnas_runner/daemon.py:") for s in sites), sites
    stale = {k for k in REVIEWED_ARGV if (ROOT / k[0]).is_file() and k not in reviewed}
    assert not stale, f"REVIEWED_ARGV entries without a matching call site: {sorted(stale)}"
