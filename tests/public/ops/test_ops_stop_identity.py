"""stop.sh re-verifies every SIGKILL target right before the signal (C12 EVAL_1 D8): the descendants collected after
the SIGTERM timeout are recorded with their start time (/proc/<pid>/stat field 22) and ``kill_verified`` signals a pid
only while it still is that process incarnation, so a pid reused meanwhile by another process is never signalled.

- unit: ``proc_starttime`` / ``kill_verified`` taken verbatim from scripts/stop.sh and run by bash against a live
  ``sleep``: a different (or missing) recorded start time -> "replaced", not signalled; the right one -> "killed";
  afterwards -> "gone". (A real pid reuse cannot be produced on demand, so the decision itself is tested here.)
- behaviour: a runner that ignores SIGTERM and has a child in its own session (outside the runner's process group,
  so the group SIGKILL does not reach it): the verified descendant SIGKILL still stops that child.
Every process started here is stopped here (fallback: ``kill_own`` and the ``finally`` blocks)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from opstest_support import REPO, StubCore, alive, run, script_env, stub_info, wait_dead, write

pytestmark = pytest.mark.slow

STUB_RUNNER_DETACHED_CHILD = r'''
import json, os, signal, subprocess, sys, time
home = os.environ.get("MBNAS_HOME", "")
signal.signal(signal.SIGTERM, signal.SIG_IGN)  # a runner that does not stop on SIGTERM ...
child = subprocess.Popen(["sleep", "600"], start_new_session=True).pid  # ... whose child is outside its process group
with open(os.path.join(home, "stub-runner.json"), "w") as fh:
    json.dump({"argv": sys.argv, "cwd": os.getcwd(), "home": home, "pid": os.getpid(), "child": child}, fh)
while True:
    time.sleep(0.2)
'''


def _stop_text() -> str:
    return (REPO / "scripts" / "stop.sh").read_text(encoding="utf-8")


def _functions(*names: str) -> str:
    """The bash source of the named top-level functions of scripts/stop.sh (from `name() {` to the closing `}`)."""
    text, parts = _stop_text(), []
    for name in names:
        m = re.search(rf"^{name}\(\) {{.*?^}}\n", text, re.S | re.M)
        assert m, f"{name}() not found in scripts/stop.sh"
        parts.append(m.group(0))
    return "".join(parts)


def _stat_fields(pid: int) -> list[str]:
    return Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace").rsplit(")", 1)[1].split()


def test_kill_verified_signals_only_the_recorded_process(tmp_path: Path) -> None:
    lib = _functions("proc_starttime", "kill_verified")

    def bash(*args: str) -> str:
        return subprocess.run(["bash", "-c", lib + '"$@"', "stoptest", *args], capture_output=True, text=True,
                              timeout=30, cwd=str(tmp_path)).stdout.strip()

    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        start = _stat_fields(sleeper.pid)[19]  # field 22: start time in clock ticks after boot
        assert bash("proc_starttime", str(sleeper.pid)) == start
        assert bash("kill_verified", str(sleeper.pid), str(int(start) + 1)) == "replaced"  # another incarnation
        assert sleeper.poll() is None and alive(sleeper.pid)
        assert bash("kill_verified", str(sleeper.pid), "") == "replaced"  # nothing recorded: never signalled blindly
        assert sleeper.poll() is None and alive(sleeper.pid)
        assert bash("kill_verified", str(sleeper.pid), start) == "killed"
        assert sleeper.wait(timeout=10) == -9  # SIGKILL
        assert bash("kill_verified", str(sleeper.pid), start) == "gone"
        assert bash("proc_starttime", str(sleeper.pid)) == ""
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_every_sigkill_target_of_stop_one_is_reverified() -> None:
    body = re.search(r"^stop_one\(\) \{.*?^}\n", _stop_text(), re.S | re.M).group(0)
    assert 'kill_verified "$p" "$t"' in body
    assert "group_still_ours" in body
    assert not re.search(r"for p in \$kids; do kill", body), "descendants are signalled without re-verification"
    kills = [line.strip() for line in body.splitlines() if "kill -KILL" in line]
    assert kills == ['kill -KILL -- "-$pid" 2>/dev/null', 'kill -KILL "$pid" 2>/dev/null'], kills


def test_sigkill_reaches_a_descendant_outside_the_process_group(ops_root: Path, ext_cwd: Path, tmp_path: Path) -> None:
    write(ops_root / "runtime" / "venv" / "bin" / "mbnas-runner", STUB_RUNNER_DETACHED_CHILD, 0o755)
    with StubCore() as core:
        env = script_env(tmp_path / "t", {"MBNAS_RUNNER_CORE_URL": core.url})
        proc = run(ops_root, "start", "--profile", "public", "--role", "runner", "--wait-s", "2", cwd=ext_cwd, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    info = stub_info(ops_root, "runner")
    pid, child = info["pid"], info["child"]
    try:
        assert alive(pid) and alive(child)
        child_stat = _stat_fields(child)
        assert int(child_stat[1]) == pid  # ppid: a descendant of the runner ...
        assert int(child_stat[2]) != pid  # ... but not in the runner's process group: the group SIGKILL misses it
        stop = run(ops_root, "stop", "--profile", "public", "--role", "runner", "--timeout-s", "1", cwd=ext_cwd, env=env)
        assert stop.returncode == 0, stop.stdout + stop.stderr
        assert "SIGKILL" in stop.stdout and f"descendants: {child} " in stop.stdout, stop.stdout
        assert "not signalled" not in stop.stdout
        assert wait_dead(pid) and wait_dead(child)
        assert not (ops_root / "runtime" / "pids" / "runner.pid").exists()
    finally:
        for p in (pid, child):
            if alive(p):
                subprocess.run(["kill", "-KILL", str(p)], check=False)
