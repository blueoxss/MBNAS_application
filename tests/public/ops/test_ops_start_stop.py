"""start.sh / stop.sh mechanics with stub role programs (WP-7 contract section 1): setsid'd processes with pid files under
runtime/pids, readiness, refusal of a running role, of the public core and of an unreachable core, CPU-role
environment, own-pid-only stopping (pid reuse), SIGTERM -> SIGKILL escalation with descendants, a path with spaces and
an external working directory. Every process started here is stopped here (fallback: ``kill_own``)."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from opstest_support import StubCore, alive, free_port, make_root, run, script_env, stub_info, wait_dead

pytestmark = pytest.mark.slow


def _pid(root: Path, name: str) -> int:
    return int((root / "runtime" / "pids" / f"{name}.pid").read_text().split()[0])


def test_public_profile_refuses_core(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    proc = run(ops_root, "start", "--profile", "public", "--role", "core", cwd=ext_cwd, env=script_env(tmp_path / "t"))
    assert proc.returncode == 1
    assert "public profile does not run the core" in proc.stderr
    assert not (ops_root / "runtime" / "pids").exists() or not list((ops_root / "runtime" / "pids").iterdir())
    stop = run(ops_root, "stop", "--profile", "public", "--role", "core", cwd=ext_cwd, env=script_env(tmp_path / "t"))
    assert stop.returncode == 0 and "runs no core" in stop.stdout


def test_start_stop_cycle_from_external_cwd(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    port = free_port()
    with StubCore() as core:
        env = script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(port), "MBNAS_RUNNER_CORE_URL": core.url,
                                          "CUDA_VISIBLE_DEVICES": "7"})
        proc = run(ops_root, "start", "--profile", "public", "--wait-s", "3", cwd=ext_cwd, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "gateway: ready" in proc.stdout and "core=reachable" in proc.stdout
        assert "runner: ready" in proc.stdout
        gw, rn = _pid(ops_root, "gateway"), _pid(ops_root, "runner")
        assert alive(gw) and alive(rn)
        for pid in (gw, rn):  # own session: pid == process group == session
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            assert int(fields[2]) == pid and int(fields[3]) == pid
        root_real = os.path.realpath(ops_root)
        g, r = stub_info(ops_root, "gateway"), stub_info(ops_root, "runner")
        assert g["cuda"] == "" and r["cuda"] == "7"  # CPU role vs runner inherits the caller's devices
        assert g["home"] == str(ops_root / "runtime") and r["home"] == str(ops_root / "runtime")
        assert os.path.realpath(g["cwd"]) == root_real and os.path.realpath(r["cwd"]) == root_real
        assert g["argv"][1:] == ["--env-file", str(ops_root / "config" / "public" / "gateway.env")]
        assert r["argv"][1:] == ["run", "--env-file", str(ops_root / "config" / "public" / "runner.env")]
        try:  # the stub gateway listens on the requested port (401 without a bearer token)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)  # noqa: S310 - local stub
            raise AssertionError("the stub gateway accepted a request without a token")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    again = run(ops_root, "start", "--profile", "public", "--role", "gateway", cwd=ext_cwd, env=env)
    assert again.returncode == 1 and "already running" in again.stderr
    stop = run(ops_root, "stop", "--profile", "public", cwd=ext_cwd, env=env)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert wait_dead(gw) and wait_dead(rn)
    assert not list((ops_root / "runtime" / "pids").glob("*.pid"))
    assert "stopped (pid" in stop.stdout
    idle = run(ops_root, "stop", "--profile", "public", cwd=ext_cwd, env=env)
    assert idle.returncode == 0 and "not running" in idle.stdout


def test_runner_needs_a_reachable_core(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    env = script_env(tmp_path / "t", {"MBNAS_RUNNER_CORE_URL": f"http://127.0.0.1:{free_port()}"})
    proc = run(ops_root, "start", "--profile", "public", "--role", "runner", cwd=ext_cwd, env=env)
    assert proc.returncode == 1
    assert "unreachable" in proc.stderr
    assert not (ops_root / "runtime" / "pids" / "runner.pid").exists()


def test_runner_needs_a_token(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    (ops_root / "config" / "public" / "runner.env").write_text("# no token\n")
    with StubCore() as core:
        env = script_env(tmp_path / "t", {"MBNAS_RUNNER_CORE_URL": core.url})
        proc = run(ops_root, "start", "--profile", "public", "--role", "runner", cwd=ext_cwd, env=env)
    assert proc.returncode == 1 and "no runner token" in proc.stderr


def test_gateway_port_in_use_is_refused(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        env = script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(busy.getsockname()[1])})
        proc = run(ops_root, "start", "--profile", "public", "--role", "gateway", cwd=ext_cwd, env=env)
    assert proc.returncode == 1 and "in use" in proc.stderr
    assert not (ops_root / "runtime" / "pids" / "gateway.pid").exists()


def test_missing_install_is_reported(tmp_path: Path, ext_cwd: Path) -> None:
    root = make_root(tmp_path / "bare", venv=False, env_files=False)
    proc = run(root, "start", "--profile", "public", "--role", "gateway", cwd=ext_cwd, env=script_env(tmp_path / "t"))
    assert proc.returncode == 1 and "scripts/install.sh" in proc.stderr


def test_stop_never_signals_a_foreign_pid(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    foreign = subprocess.Popen(["sleep", "60"], cwd=str(ext_cwd))  # not this tree's process (argv, cwd)
    try:
        pids = ops_root / "runtime" / "pids"
        pids.mkdir(parents=True)
        (pids / "runner.pid").write_text(f"{foreign.pid}\n")
        (pids / "gateway.pid").write_text("not-a-pid\n")
        proc = run(ops_root, "stop", "--profile", "public", cwd=ext_cwd, env=script_env(tmp_path / "t"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "not this tree's runner" in proc.stdout and "not signalled" in proc.stdout
        assert foreign.poll() is None and alive(foreign.pid)
        assert not (pids / "runner.pid").exists() and not (pids / "gateway.pid").exists()
    finally:
        foreign.kill()
        foreign.wait()


def test_pid_of_a_process_without_cmdline_is_not_ours(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    """A live pid with an empty /proc/<pid>/cmdline (pid 2, kthreadd, on Linux hosts) is never this tree's process.
    Related fix: the argv loop variable of is_ours starts set, because a read error on a process that exits mid-check
    left it unassigned once and `set -u` aborted stop.sh (`arg: unbound variable`; that race is not reproducible on
    demand, this test covers the empty-cmdline branch)."""
    try:
        empty = Path("/proc/2/cmdline").read_bytes() == b""
    except OSError:
        empty = False
    if not empty:
        pytest.skip("no process with an empty cmdline at pid 2 on this host")
    pids = ops_root / "runtime" / "pids"
    pids.mkdir(parents=True)
    (pids / "runner.pid").write_text("2\n")
    proc = run(ops_root, "stop", "--profile", "public", "--role", "runner", cwd=ext_cwd, env=script_env(tmp_path / "t"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "not this tree's runner" in proc.stdout and "unbound" not in proc.stderr
    assert not (pids / "runner.pid").exists()
    (pids / "gateway.pid").write_text("2\n")
    start = run(ops_root, "start", "--profile", "public", "--role", "gateway", "--wait-s", "1", cwd=ext_cwd,
                env=script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(free_port())}))
    try:
        assert "removed stale pid file" in start.stdout and "unbound" not in start.stderr
    finally:
        run(ops_root, "stop", "--profile", "public", "--role", "gateway", cwd=ext_cwd, env=script_env(tmp_path / "t"))


def test_start_drops_a_stale_pid_file(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    pids = ops_root / "runtime" / "pids"
    pids.mkdir(parents=True)
    (pids / "gateway.pid").write_text("999999999\n")
    env = script_env(tmp_path / "t", {"MBNAS_GW_PORT": str(free_port())})
    proc = run(ops_root, "start", "--profile", "public", "--role", "gateway", "--wait-s", "20", cwd=ext_cwd, env=env)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "removed stale pid file" in proc.stdout
    finally:
        assert run(ops_root, "stop", "--profile", "public", "--role", "gateway", cwd=ext_cwd, env=env).returncode == 0


def test_stop_escalates_to_sigkill_and_reaps_descendants(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    with StubCore() as core:
        env = script_env(tmp_path / "t", {"MBNAS_RUNNER_CORE_URL": core.url, "STUB_IGNORE_TERM": "1"})
        proc = run(ops_root, "start", "--profile", "public", "--role", "runner", "--wait-s", "2", cwd=ext_cwd, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    info = stub_info(ops_root, "runner")
    pid, child = info["pid"], info["child"]
    assert alive(pid) and alive(child)
    stop = run(ops_root, "stop", "--profile", "public", "--role", "runner", "--timeout-s", "1", cwd=ext_cwd, env=env)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert "SIGKILL" in stop.stdout
    assert wait_dead(pid) and wait_dead(child)
    assert not (ops_root / "runtime" / "pids" / "runner.pid").exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH (web preview needs Node.js)")
def test_web_role_preview(tmp_path: Path, ext_cwd: Path) -> None:
    root = make_root(tmp_path, web=True)
    port = free_port()
    env = script_env(tmp_path / "t", {"MBNAS_WEB_PORT": str(port)})
    try:
        proc = run(root, "start", "--profile", "public", "--role", "web", "--wait-s", "20", cwd=ext_cwd, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert f"http://127.0.0.1:{port}/" in proc.stdout
        pid = _pid(root, "web")
        stop = run(root, "stop", "--profile", "public", "--role", "web", cwd=ext_cwd, env=env)
        assert stop.returncode == 0 and wait_dead(pid)
    finally:
        from opstest_support import kill_own

        kill_own(root)
