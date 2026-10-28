"""Helpers of the packager tests (WP-7D): a small synthetic project tree that uses the real allowlists.

The tree has tiny setuptools packages for the five distribution names (the private one included, so the tests can
prove it never reaches a public bundle), a prebuilt ``mbnas_web/dist``, owner-only material (core env example, owner
tests, internal documents, owner deploy files), and things that must never ship (``runtime/``, ``build/`` junk,
``node_modules``, ``__pycache__``, a dotenv file). ``build/allowlists/*.txt`` are copied from the repository, so the
tests exercise the real allowlists. The packager runs as a subprocess, exactly as ``scripts/package.sh`` runs it.

Public tests never name the private package as a module reference (``tests/public`` is a public host for the import
closure checker): its name is composed (``CORE``).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEV = REPO / "scripts" / "dev"
CORE = "mbnas_" + "core"
PUBLIC_PKGS = ("mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server")
VERSION = "0.1.0"


def load_dev(name: str):
    """Import ``scripts/dev/<name>.py`` by path (no package import, nothing written to __pycache__); an owner-only tool:
    skipped (module level allowed) when this bundle does not ship it (DD-A43(b), ``conftest_common.owner_tool``)."""
    from conftest_common import owner_tool

    spec = importlib.util.spec_from_file_location(f"wp7d_test_{name}", owner_tool(f"scripts/dev/{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _w(root: Path, rel: str, text: str, mode: int | None = None) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(p, mode)
    return p


def _package(root: Path, pkg: str, body: str = "", package_data: str = "") -> None:
    """``package_data``: extra TOML appended to the pyproject (a ``[tool.setuptools.package-data]`` table)."""
    dist = pkg.replace("_", "-")
    _w(root, f"{pkg}/pyproject.toml",
       '[build-system]\nrequires = ["setuptools>=69,<81", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n'
       f'[project]\nname = "{dist}"\nversion = "{VERSION}"\nrequires-python = ">=3.10"\n\n'
       f'[tool.setuptools.packages.find]\nwhere = ["src"]\ninclude = ["{pkg}", "{pkg}.*"]\n' + package_data)
    _w(root, f"{pkg}/src/{pkg}/__init__.py", f'"""Synthetic {pkg} for the packager tests."""\nVALUE = 1\n{body}')


def make_tree(base: Path) -> Path:
    root = base / "proj"
    root.mkdir(parents=True)
    _w(root, "VERSION", VERSION + "\n")
    _w(root, "constraints.txt", "# synthetic constraints (empty)\n")
    _w(root, "THIRD_PARTY_NOTICES.md", "# notices\n")
    _w(root, "THIRD_PARTY_NOTICES_WEB.md", "# npm notices of the web bundle\n")  # required in every profile (DD-A65)
    _w(root, "LICENSE", "All rights reserved (placeholder; licence undetermined)\n")  # required in every profile
    _w(root, "LICENSE_STATUS.md", "# licence status\n")
    _w(root, "README.md", "# synthetic project\n")
    _w(root, ".dockerignore", "*\n!wheels/*.whl\n!mbnas_web/dist/\n")
    _w(root, "pyproject.toml", "[tool.pytest.ini_options]\naddopts = \"-ra\"\n")
    for name in ("requirements-public.txt", "requirements-cpu.txt"):
        _w(root, name, "# synthetic requirements\n./wheels/mbnas_contracts-0.1.0-py3-none-any.whl\n")
    _w(root, "requirements-owner.txt", f"./wheels/{CORE}-0.1.0-py3-none-any.whl\n")
    for pkg in PUBLIC_PKGS:
        _package(root, pkg)
    # the core ships data files declared as package-data, as the real core does (schema.sql); the wheel-contents gate
    # (C09) checks them
    _package(root, CORE, package_data=f'\n[tool.setuptools.package-data]\n"{CORE}.db" = ["schema.sql"]\n')
    for rel in ("search/space.py", "search/mixed_pool/seed_variants.py", "protocol/fitness.py",
                "protocol/promotion.py"):
        _w(root, f"{CORE}/src/{CORE}/{rel}", '"""private policy module (synthetic)."""\nRULE = 3\n')
    for rel in ("search/__init__.py", "search/mixed_pool/__init__.py", "protocol/__init__.py", "db/__init__.py"):
        _w(root, f"{CORE}/src/{CORE}/{rel}", "")
    _w(root, f"{CORE}/src/{CORE}/db/schema.sql", "CREATE TABLE t (id INTEGER);\n")
    _w(root, "mbnas_web/package.json", json.dumps({"name": "mbnas-web", "version": VERSION}) + "\n")
    _w(root, "mbnas_web/dist/index.html", "<!doctype html><title>console</title><script src=assets/app.js></script>\n")
    _w(root, "mbnas_web/dist/assets/app.js", "console.log('app');\n")
    _w(root, "mbnas_web/src/main.ts", "export const source = 1;\n")
    # DD-A43 (a): the test-only fitness oracle and every web test importing it (also transitively) are owner only
    _w(root, "mbnas_web/tests/fitness_oracle.ts", "export const fitnessOracle = (x: number) => x;\n")
    _w(root, "mbnas_web/tests/fixtures.ts",
       "import { fitnessOracle } from './fitness_oracle';\nexport const f = fitnessOracle(1);\n")
    _w(root, "mbnas_web/tests/uses_fixtures.test.ts", "import {\n  f,\n} from './fixtures.js';\nexport const g = f;\n")
    _w(root, "mbnas_web/e2e/oracle.spec.ts", "import type { X } from '../tests/fitness_oracle';\nexport {};\n")
    _w(root, "mbnas_web/tests/standalone.test.ts", "export const h = 1;\n")
    gate = REPO / "mbnas_web" / "scripts" / "check-bundle.mjs"  # the real WP-6A bundle hygiene gate
    if gate.is_file():
        (root / "mbnas_web" / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(gate, root / "mbnas_web" / "scripts" / "check-bundle.mjs")
    _w(root, "mbnas_web/node_modules/left-pad/index.js", "module.exports = 1;\n")
    for name in ("install", "doctor", "start", "stop", "verify"):
        _w(root, f"scripts/{name}.sh", "#!/usr/bin/env bash\necho synthetic\n", 0o644)
    _w(root, "scripts/dev/tool.py", "print('owner tool')\n")
    _w(root, "config/examples/gateway.env.example", "# gateway\nMBNAS_GW_TOKEN=\nMBNAS_GW_PORT=8710\n")
    _w(root, "config/examples/runner.env.example", "# runner\nMBNAS_RUNNER_TOKEN=<issued by the core owner>\n")
    _w(root, "config/examples/core.env.example", "# core (owner only)\nMBNAS_CORE_TICKET_KEY=\n")
    _w(root, "config/examples/owner-all-in-one.env.example", "# owner all-in-one\nMBNAS_HOME=runtime\n")
    _w(root, "deploy/public/Dockerfile", "FROM scratch\n")
    _w(root, "deploy/public/docker-compose.yml", "services: {}\n")
    _w(root, "deploy/owner/Dockerfile", "FROM scratch\n")
    _w(root, "deploy/owner/docker-compose.yml", "services: {}\n")
    _w(root, "docs/public/INSTALL.md", f"# Install\nThe private core package ({CORE}) runs on the owner host only.\n")
    _w(root, "docs/registration/PROGRAM_OVERVIEW.md", "# Program overview\n")
    _w(root, "docs/internal/DECISIONS.md", "# decisions (internal)\n")
    _w(root, "docs/internal/TASK_STATE.md", "# task state (internal)\n")
    _w(root, "docs/internal/provenance/vendored_reference/README.md", "# vendored reference (owner)\n")
    _w(root, "docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl", "{}\n")
    # a valid manifest whose token must never reach any bundle (docs/internal/canary/** is denied everywhere)
    self_token = "MBNAS-" + "CANARY-" + "MANIFEST_SELF-" + os.urandom(8).hex()
    _w(root, "docs/internal/canary/CANARY_MANIFEST.json", json.dumps({
        "schema": "mbnas.canary_manifest/1", "areas": [
            {"area": "MANIFEST_SELF", "value": self_token, "expected_in": [],
             "must_not_in": ["owner", "public", "registration"], "planted_in": []}]}) + "\n")
    _w(root, "tests/owner/conftest.py", '"""owner tests (synthetic)."""\n')
    _w(root, "tests/public/test_synthetic.py", "def test_ok():\n    assert True\n")
    _w(root, "tests/_shared/harness.py", f'"""shared test plumbing (synthetic)."""\nimport {CORE}\n')
    _w(root, "tests/_shared/conftest_common.py", '"""core-free shared test plumbing (synthetic)."""\n')
    _w(root, "tests/public/infra/test_no_host_facts.py", "FORBIDDEN = ()  # the real file holds host facts\n")
    _w(root, "examples/sample.txt", "synthetic example\n")
    _w(root, "runtime/state.txt", "runtime state must not ship\n")
    _w(root, "build/junk.txt", "build junk must not ship\n")
    _w(root, ".env", "MBNAS_GW_TOKEN=" + "s3cr3t" * 4 + "\n")
    _w(root, "mbnas_server/src/mbnas_server/__pycache__/x.cpython-310.pyc", "not bytecode\n")
    (root / "build" / "allowlists").mkdir(parents=True, exist_ok=True)
    for f in sorted((REPO / "build" / "allowlists").glob("*.txt")):
        shutil.copy2(f, root / "build" / "allowlists" / f.name)
    return root


def run_build(root: Path, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "SOURCE_DATE_EPOCH"))}
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="")
    return subprocess.run([sys.executable, "-I", "-B", str(DEV / "package_build.py"), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=timeout, env=env)


def report(root: Path, profile: str) -> dict:
    return json.loads((root / "build" / "package_reports" / f"{profile}.json").read_text(encoding="utf-8"))


def zip_of(root: Path, profile: str) -> Path:
    zips = sorted((root / "dist" / profile).glob("*.zip"))
    assert len(zips) == 1, zips
    return zips[0]


def members(zpath: Path) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(zpath) as zf:
        return {i.filename: i for i in zf.infolist()}
