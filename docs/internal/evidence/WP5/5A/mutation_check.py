"""Mutation check for the WP-5A tests: each mutant must make at least one test fail."""
import shutil, subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # <TARGET_ROOT>/docs/internal/evidence/WP5/5A/mutation_check.py
SCR = Path(sys.argv[1])
PY = str(ROOT / "runtime/venv/bin/python")
TESTS = ["tests/public/server/test_artifact_store.py", "tests/public/server/test_artifact_download.py"]
MUTANTS = {
    "M1 link->replace (no first-writer-wins)": ("artifacts/store.py",
        "            os.link(tmp, final)\n            return True", "            os.replace(tmp, final)\n            return True"),
    "M2 no digest check on put": ("artifacts/store.py", "        if digest != self.sha256:", "        if False:"),
    "M3 re.match with $ (accepts trailing newline)": ("artifacts/store.py",
        "return isinstance(value, str) and _SHA256.fullmatch(value) is not None",
        "return isinstance(value, str) and re.match(r'^[0-9a-f]{64}$', value) is not None"),
    "M4 no held-back final chunk": ("artifacts/download.py",
        "                held, self._held = self._held, block\n                if held is not None:\n                    return held",
        "                return block"),
    "M5 no verify-first hashing": ("artifacts/download.py", "            if _hash_to_end(fh, chunk) != sha:", "            if False:"),
    "M6 abort keeps temp file": ("artifacts/store.py", "                pass\n        _unlink_quiet(self._tmp)", "                pass\n        pass"),
    "M8 HMAC message format changed": ("artifacts/signing.py", 'f"{artifact_id}.{int(exp)}"', 'f"{artifact_id}:{int(exp)}"'),
    "M9 no directory fsync after publish": ("artifacts/store.py",
        "            _unlink_quiet(tmp)\n        _fsync_dir(final.parent)", "            _unlink_quiet(tmp)\n        pass"),
    "M10 symlinks followed on open": ("artifacts/store.py",
        '_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)',
        '_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)'),
    "M11 size cap not enforced while streaming": ("artifacts/store.py",
        "        if total > self._store.max_bytes:", "        if False:"),
}
env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null"}
bad = 0
for name, (rel, old, new) in MUTANTS.items():
    d = SCR / name.split()[0]
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(ROOT / "mbnas_server/src/mbnas_server", d / "mbnas_server")
    f = d / "mbnas_server" / rel
    text = f.read_text()
    assert text.count(old) == 1, (name, text.count(old))
    f.write_text(text.replace(old, new))
    r = subprocess.run([PY, "-B", "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "-x", "--no-header",
                        "-W", "ignore::DeprecationWarning"], cwd=ROOT, env={**env, "PYTHONPATH": str(d)},
                       capture_output=True, text=True, timeout=600)
    import_check = subprocess.run([PY, "-B", "-c", "import mbnas_server.artifacts as a; print(a.__file__)"], cwd=ROOT,
                                  env={**env, "PYTHONPATH": str(d)}, capture_output=True, text=True)
    failed = [l for l in r.stdout.splitlines() if l.startswith("FAILED")]
    killed = r.returncode != 0
    bad += not killed
    print(f"{name}: {'KILLED' if killed else 'SURVIVED'} rc={r.returncode} module={import_check.stdout.strip()[-60:]}")
    for l in failed[:2]:
        print("   ", l[:160])
    shutil.rmtree(d, ignore_errors=True)
print("survivors:", bad)
