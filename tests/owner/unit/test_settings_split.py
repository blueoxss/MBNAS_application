"""Settings split (WP-1 contract §5, DD-A13): CoreSettings / GatewaySettings / RunnerSettings defaults under MBNAS_HOME,
their env prefixes, their own *_ENV_FILE variables, and the removed monolith fields/variables."""
from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

import pytest

from mbnas_contracts.util.home import mbnas_home
from mbnas_core.config import CoreSettings
from mbnas_runner.config import RunnerSettings
from mbnas_server.config import GatewaySettings


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_mbnas_home_default_is_cwd_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MBNAS_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert mbnas_home() == tmp_path / "runtime"
    assert CoreSettings().db_path == str(tmp_path / "runtime" / "core" / "db" / "mbnas.sqlite3")
    assert RunnerSettings().work_root == str(tmp_path / "runtime" / "runner" / "runs")
    assert GatewaySettings().artifact_store == str(tmp_path / "runtime" / "gateway" / "artifacts")


def test_core_defaults(home: Path) -> None:
    s = CoreSettings()
    assert mbnas_home() == home
    assert s.db_path == str(home / "core" / "db" / "mbnas.sqlite3")
    assert s.output_root == str(home / "core" / "runs") and s.output_roots == [s.output_root]
    assert s.dataset_roots == [str(home / "data")] and s.weights_dir == str(home / "weights")
    assert s.model_yaml_roots == [str(home / "uploads")] and s.log_dir == str(home / "core" / "logs")
    assert (s.host, s.port, s.worker_python) == ("127.0.0.1", 8700, sys.executable)
    assert (s.api_token, s.artifact_signing_key, s.cors_origins) == (None, None, [])
    assert (s.signed_url_ttl_s, s.lease_ttl_s, s.heartbeat_s, s.max_replay_events) == (600, 60, 10, 5000)
    assert (s.max_request_body_bytes, s.min_free_disk_gb, s.full_state_retention) == (1 << 20, 5.0, "keep_all")
    assert (s.preset_data_root, s.preset_output_root) == (None, None)
    for removed in ("etri_nas_root", "archive_roots", "archive_driver_dirs", "archive_campaign_roots", "archive_cache_ttl_s"):
        assert removed not in CoreSettings.model_fields, removed


def test_runner_defaults(home: Path) -> None:
    s = RunnerSettings()
    assert (s.core_url, s.token) == ("http://127.0.0.1:8700", None)
    # C06-D1 (fix round 1): the default runner id is the runtime root's generated id (runner-<8 hex>, persisted in
    # MBNAS_HOME/runner/runner_id and read back by the next settings object), no longer runner-<hostname>
    assert re.fullmatch(r"runner-[0-9a-f]{8}", s.runner_id) and s.runner_id != f"runner-{socket.gethostname()}"
    assert (home / "runner" / "runner_id").read_text(encoding="utf-8").strip() == s.runner_id
    assert RunnerSettings().runner_id == s.runner_id
    assert s.work_root == str(home / "runner" / "runs") and s.output_roots == [s.work_root]
    assert s.output_root == s.work_root  # read by DatasetService.from_settings (duck typing, contract §5)
    assert s.dataset_roots == [str(home / "data")] and s.weights_dir == str(home / "weights")
    assert s.log_dir == str(home / "runner" / "logs") and s.worker_python == sys.executable
    assert (s.heartbeat_s, s.kill_grace_s, s.gpu_index, s.allow_pickle_resume) == (10, 10, 0, False)


def test_gateway_defaults(home: Path) -> None:
    s = GatewaySettings()
    assert (s.host, s.port, s.core_url, s.core_token) == ("127.0.0.1", 8710, "http://127.0.0.1:8700", None)
    assert s.artifact_store == str(home / "gateway" / "artifacts") and s.log_dir == str(home / "gateway" / "logs")
    assert (s.archive_roots, s.archive_driver_dirs, s.archive_campaign_roots, s.archive_cache_ttl_s) == ([], [], [], 5.0)
    assert (s.signed_url_ttl_s, s.max_request_body_bytes) == (600, 1 << 20)


def test_each_class_reads_only_its_prefix(home: Path, monkeypatch, tmp_path) -> None:
    for prefix, port in (("MBNAS_CORE_", "9101"), ("MBNAS_GW_", "9102"), ("MBNAS_", "9100")):
        monkeypatch.setenv(f"{prefix}PORT", port)
    monkeypatch.setenv("MBNAS_RUNNER_GPU_INDEX", "1")
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", str(tmp_path / "core_runs"))
    monkeypatch.setenv("MBNAS_RUNNER_WORK_ROOT", str(tmp_path / "runner_runs"))
    assert CoreSettings().port == 9101 and GatewaySettings().port == 9102
    assert CoreSettings().output_root == str(tmp_path / "core_runs")
    assert RunnerSettings().gpu_index == 1 and RunnerSettings().work_root == str(tmp_path / "runner_runs")
    monkeypatch.setenv("MBNAS_OUTPUT_ROOT", str(tmp_path / "legacy"))  # the monolith's names are not read
    monkeypatch.setenv("MBNAS_DB_PATH", str(tmp_path / "legacy.sqlite3"))
    assert CoreSettings().db_path == str(home / "core" / "db" / "mbnas.sqlite3")
    assert RunnerSettings().work_root == str(tmp_path / "runner_runs")


@pytest.mark.parametrize("cls,var,key,value,attr", [
    (CoreSettings, "MBNAS_CORE_ENV_FILE", "MBNAS_CORE_PORT", "9201", "port"),
    (GatewaySettings, "MBNAS_GW_ENV_FILE", "MBNAS_GW_PORT", "9202", "port"),
    (RunnerSettings, "MBNAS_RUNNER_ENV_FILE", "MBNAS_RUNNER_GPU_INDEX", "3", "gpu_index")])
def test_each_class_has_its_own_env_file_variable(home, monkeypatch, tmp_path, cls, var, key, value, attr) -> None:
    f = tmp_path / f"{cls.__name__}.env"
    f.write_text(f"{key}={value}\n")
    assert str(getattr(cls.load(f), attr)) == value
    monkeypatch.setenv(var, str(f))
    assert str(getattr(cls.load(), attr)) == value
    monkeypatch.delenv(var)
    monkeypatch.setenv("MBNAS_ENV_FILE", str(f))  # the monolith's variable no longer exists
    assert str(getattr(cls.load(), attr)) != value


@pytest.mark.parametrize("cls,kw", [
    (CoreSettings, {"output_root": "relative/runs"}), (CoreSettings, {"cors_origins": ["*"]}),
    (CoreSettings, {"api_token": ""}), (RunnerSettings, {"work_root": "relative"}), (RunnerSettings, {"token": ""}),
    (GatewaySettings, {"archive_roots": ["relative"]}), (GatewaySettings, {"api_token": ""})])
def test_original_validation_rules_are_kept(home, cls, kw) -> None:
    with pytest.raises(ValueError):
        cls(**kw)
