"""DD-A62 (c) follow-up: the owner worker entry point with ``--env-file`` exports it as ``MBNAS_CORE_ENV_FILE`` (like
``mbnas-core-api``), so every ``CoreSettings.load()`` of the worker process reads the same file - in particular the
engine's core-local journal lookup (``engine._core_run_dir_from_settings``) - while its job children still never inherit
the dotenv path (``owner_worker.main._inherited``). Before: ``CoreSettings.load(args.env_file)`` loaded the file for the
worker's own settings only, and the engine fell back to the defaults (``scripts/start.sh`` starts the owner worker with
``--env-file``). The Worker loop, logging setup and signal handlers are replaced by test doubles (nothing global is
changed); no job runs.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from mbnas_core.config import ENV_FILE_VAR
from mbnas_core.orchestration import engine
from mbnas_core.owner_worker import main as owner_main


@pytest.fixture
def entry_point(monkeypatch):
    """``owner_main.main`` with a recording Worker double; MBNAS_CORE_ENV_FILE / MBNAS_CORE_OUTPUT_ROOT absent before and
    restored (absent) after the test."""
    for key in (ENV_FILE_VAR, "MBNAS_CORE_OUTPUT_ROOT", "MBNAS_CORE_OUTPUT_ROOTS", "MBNAS_CORE_DB_PATH"):
        monkeypatch.setenv(key, "placeholder")  # recorded first, so the teardown removes what main() sets
        monkeypatch.delenv(key)
    seen: dict = {}

    class RecordingWorker:
        def __init__(self, settings, cfg, worker_id=None):
            seen["settings"], self.id, self.jobs_done, self.stopping = settings, "dd-a62-env:1", 0, False

        def run(self) -> int:  # what the engine sees inside this process while the worker runs
            seen["engine_core_run_dir"] = engine._core_run_dir_from_settings("run-dd-a62")
            seen["env_file"] = os.environ.get(ENV_FILE_VAR)
            return 0

    monkeypatch.setattr(owner_main, "Worker", RecordingWorker)
    monkeypatch.setattr(owner_main, "configure_logging", lambda: logging.getLogger("dd-a62-env-file-test"))
    monkeypatch.setattr(owner_main.signal, "signal", lambda *_a, **_k: None)
    return seen


def test_env_file_is_exported_to_this_process_and_never_to_job_children(entry_point, tmp_path):
    core_runs = tmp_path / "core-host" / "runs"
    env = tmp_path / "core.env"
    env.write_text(f"MBNAS_CORE_OUTPUT_ROOT={core_runs}\nMBNAS_CORE_DB_PATH={tmp_path / 'db' / 'mbnas.sqlite3'}\n")
    assert owner_main.main(["--owner", "--env-file", str(env)]) == 0
    assert entry_point["env_file"] == str(env) and os.environ.get(ENV_FILE_VAR) == str(env)
    assert entry_point["settings"].output_root == str(core_runs)  # the worker's settings, as before
    assert entry_point["engine_core_run_dir"] == Path(core_runs) / "run-dd-a62"  # the engine reads the same file
    assert owner_main._inherited(ENV_FILE_VAR) is False  # the job children never see the dotenv path
    child_env = {k: v for k, v in os.environ.items() if owner_main._inherited(k)}
    assert ENV_FILE_VAR not in child_env


def test_without_env_file_nothing_is_exported(entry_point):
    assert owner_main.main(["--owner"]) == 0
    assert entry_point["env_file"] is None and ENV_FILE_VAR not in os.environ


def test_a_missing_env_file_is_an_error_as_before(entry_point, tmp_path):
    with pytest.raises(FileNotFoundError):
        owner_main.main(["--owner", "--env-file", str(tmp_path / "absent.env")])
    assert "settings" not in entry_point  # no worker was created
