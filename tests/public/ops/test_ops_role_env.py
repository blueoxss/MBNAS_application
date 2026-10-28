"""start.sh gives every role a clean Python environment, and doctor.sh checks it (C09 EVAL_1 D1; C12 EVAL_1 D7).

Port of the C09 evaluator probe ``c09_startsh_env_probe.py``: the caller of start.sh has a PYTHONPATH whose directory
holds a shadowing ``mbnas_server`` and a marker module, plus PYTHONHOME / PYTHONSTARTUP / PYTHONUSERBASE / OLDPWD. The
started roles (stub programs, see opstest_support) must not have any of these in ``/proc/<pid>/environ``, must run with
PYTHONNOUSERSITE=1, must not have the caller directory on ``sys.path`` and must not be able to import the marker.
doctor.sh then reports ``env:<role>`` PASS for them, and FAIL (blocking under --strict, key names only, never values) for a
role that was started by hand with PYTHONPATH / PYTHONHOME set. Every process started here is stopped here (fallback:
``kill_own``); the GPU check of doctor.sh only ever sees the fake nvidia-smi."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import opstest_support as ops
from opstest_support import StubCore, alive, fake_nvidia_smi, free_port, run, script_env, stub_info, wait_dead, write

pytestmark = pytest.mark.slow

SCRUBBED = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "OLDPWD")
MARKER = "mbnas_c09_shadow_marker"
# recorded in addition by the stub roles: their search path, and whether the caller-only marker module is importable
_EXTRA = (', "sys_path": sys.path, "shadow_import": __import__("importlib.util").util.find_spec("' + MARKER
          + '") is not None}')


def _instrument(root: Path) -> None:
    """Rewrite this ROOT's stub gateway / runner so that they also record sys.path and the marker lookup (the shared
    STUB_* texts of opstest_support stay untouched)."""
    vbin = root / "runtime" / "venv" / "bin"
    for name, stub in (("mbnas-gateway", ops.STUB_GATEWAY), ("mbnas-runner", ops.STUB_RUNNER)):
        text = stub.replace('"pid": os.getpid()}', '"pid": os.getpid()' + _EXTRA, 1)
        assert text != stub, f"the {name} stub no longer has the expected info line"
        write(vbin / name, text, 0o755)


def _environ_keys(pid: int) -> dict[str, str]:
    with open(f"/proc/{pid}/environ", "rb") as fh:
        items = [i.split(b"=", 1) for i in fh.read().split(b"\0") if i]
    return {k.decode(): (v[0].decode(errors="replace") if v else "") for k, *v in items}


def _pid(root: Path, name: str) -> int:
    return int((root / "runtime" / "pids" / f"{name}.pid").read_text().split()[0])


def _doctor(root: Path, cwd: Path, tmp: Path) -> tuple[subprocess.CompletedProcess, dict]:
    env = script_env(tmp / "doctor", fake_bin=fake_nvidia_smi(tmp / "fakebin"))
    proc = run(root, "doctor", "--profile", "public", "--json", "-", cwd=cwd, env=env, timeout=300)
    return proc, json.loads(proc.stdout)


def _caller_python_env(tmp: Path) -> tuple[Path, dict[str, str]]:
    shadow = tmp / "caller shadow dir"
    write(shadow / "mbnas_server" / "__init__.py", "SHADOW = True\n")  # would shadow the installed mbnas_server
    write(shadow / f"{MARKER}.py", "MARKER = True\n")
    startup = write(tmp / "startup.py", "open(__file__ + '.ran', 'w').close()\n")
    return shadow, {"PYTHONPATH": str(shadow), "PYTHONHOME": sys.base_prefix,  # a valid home: the role would still run
                    "PYTHONSTARTUP": str(startup), "PYTHONUSERBASE": str(tmp / "caller userbase"),
                    "OLDPWD": str(tmp)}


def test_start_removes_the_callers_python_variables_and_oldpwd(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    _instrument(ops_root)
    shadow, caller = _caller_python_env(tmp_path)
    port = free_port()
    with StubCore() as core:
        env = script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(port), "MBNAS_RUNNER_CORE_URL": core.url, **caller})
        proc = run(ops_root, "start", "--profile", "public", "--wait-s", "5", cwd=ext_cwd, env=env)
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            for name in ("gateway", "runner"):
                pid = _pid(ops_root, name)
                environ = _environ_keys(pid)
                assert sorted(set(SCRUBBED) & set(environ)) == [], (name, sorted(set(SCRUBBED) & set(environ)))
                assert environ.get("PYTHONNOUSERSITE") == "1", name
                assert environ.get("MBNAS_HOME") == str(ops_root / "runtime"), name
                info = stub_info(ops_root, name)
                assert info["pid"] == pid
                assert info["shadow_import"] is False, f"{name} can import a module of the caller's PYTHONPATH"
                assert not [p for p in info["sys_path"] if p and p.startswith(str(shadow))], (name, info["sys_path"])
                assert len(info["sys_path"]) > 1 and info["sys_path"][1] != str(shadow), info["sys_path"]
            doctor, doc = _doctor(ops_root, ext_cwd, tmp_path)
            by_name = {c["name"]: c for c in doc["checks"]}
            for name in ("gateway", "runner"):
                check = by_name[f"env:{name}"]
                assert check["status"] == "PASS", check
                assert "OLDPWD" not in check["detail"], check  # nothing else of the scrub list is left either
            assert "env:gateway" not in doc["blocking"] and "env:runner" not in doc["blocking"]
        finally:
            stop = run(ops_root, "stop", "--profile", "public", cwd=ext_cwd, env=env)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert not list((ops_root / "runtime" / "pids").glob("*.pid"))


def test_doctor_fails_a_running_role_that_has_pythonpath(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    """A gateway started by hand (not through start.sh) inherits PYTHONPATH / PYTHONHOME: doctor reports env:gateway
    FAIL and lists it as blocking, naming only the keys (the shadow directory path never appears in its output)."""
    shadow, caller = _caller_python_env(tmp_path)
    vbin = ops_root / "runtime" / "venv" / "bin"
    pids = ops_root / "runtime" / "pids"
    pids.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update(MBNAS_HOME=str(ops_root / "runtime"), MBNAS_GW_PORT=str(free_port()), PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=caller["PYTHONPATH"], PYTHONHOME=caller["PYTHONHOME"])
    gateway = subprocess.Popen([str(vbin / "python"), str(vbin / "mbnas-gateway"), "--env-file",
                                str(ops_root / "config" / "public" / "gateway.env")], cwd=str(ops_root), env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        info_file = ops_root / "runtime" / "stub-gateway.json"
        end = time.monotonic() + 20
        while not info_file.is_file() and time.monotonic() < end and gateway.poll() is None:
            time.sleep(0.1)
        assert info_file.is_file() and alive(gateway.pid), "the hand-started stub gateway did not come up"
        (pids / "gateway.pid").write_text(f"{gateway.pid}\n")
        assert {"PYTHONPATH", "PYTHONHOME"} <= set(_environ_keys(gateway.pid))
        proc, doc = _doctor(ops_root, ext_cwd, tmp_path)
        by_name = {c["name"]: c for c in doc["checks"]}
        assert by_name["process:gateway"]["status"] == "PASS"  # identity check passed: this tree's gateway
        check = by_name["env:gateway"]
        assert check["status"] == "FAIL", check
        assert "PYTHONPATH" in check["detail"] and "PYTHONHOME" in check["detail"]
        assert "env:gateway" in doc["blocking"] and not doc["ok"]
        assert "[FAIL] env:gateway" in proc.stderr
        assert str(shadow) not in proc.stdout + proc.stderr and "caller shadow dir" not in proc.stdout + proc.stderr
        assert "env:runner" not in by_name  # not running: no environment check
        strict = run(ops_root, "doctor", "--profile", "public", "--strict", cwd=ext_cwd,
                     env=script_env(tmp_path / "doctor", fake_bin=fake_nvidia_smi(tmp_path / "fakebin")), timeout=300)
        assert strict.returncode == 1 and "[FAIL] env:gateway" in strict.stdout
        stop = run(ops_root, "stop", "--profile", "public", "--role", "gateway", cwd=ext_cwd,
                   env=script_env(tmp_path / "t"))
        assert stop.returncode == 0, stop.stdout + stop.stderr
        assert wait_dead(gateway.pid)
    finally:
        if gateway.poll() is None:
            gateway.kill()
        gateway.wait(timeout=10)
