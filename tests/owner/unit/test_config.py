"""config.CoreSettings (WP-1 contract §5, DD-A13): defaults, env parsing, secrets, CORS exact origins, dotenv loading.

Same checks as the old single Settings, on the core host settings (env prefix MBNAS_CORE_, defaults under
$MBNAS_HOME); the gateway / runner settings and the MBNAS_HOME rule are covered by test_settings_split.py.
"""
from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from mbnas_core.config import CoreSettings as Settings


def test_defaults_point_outside_source(monkeypatch, tmp_path) -> None:
    for key in ("MBNAS_CORE_WEIGHTS_DIR", "MBNAS_RUNNER_WEIGHTS_DIR"):  # conftest may inject host paths; defaults here
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("MBNAS_HOME", str(home))  # DD-A13: every default path lives under $MBNAS_HOME
    s = Settings()
    assert str(s.db_path) == str(home / "core/db/mbnas.sqlite3")
    assert str(s.output_root) == str(home / "core/runs") and str(s.log_dir) == str(home / "core/logs")
    assert [str(p) for p in s.dataset_roots] == [str(home / "data")]
    assert [str(p) for p in s.output_roots] == [str(home / "core/runs")]
    assert str(s.weights_dir) == str(home / "weights")
    assert str(s.worker_python) == sys.executable
    assert (s.host, s.port, s.signed_url_ttl_s, s.lease_ttl_s, s.heartbeat_s) == ("127.0.0.1", 8700, 600, 60, 10)
    assert s.max_replay_events == 5000 and s.min_free_disk_gb == 5 and not hasattr(s, "etri_nas_root")
    assert s.api_token is None and s.artifact_signing_key is None


def test_env_prefix_and_list_parsing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBNAS_CORE_DATASET_ROOTS", "/a,/b")
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOTS", '["/o"]')
    monkeypatch.setenv("MBNAS_CORE_CORS_ORIGINS", "https://console.example.org, http://localhost:5173/")
    monkeypatch.setenv("MBNAS_CORE_PORT", "8100")
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", "/tmp/out")
    s = Settings()
    assert [str(p) for p in s.dataset_roots] == ["/a", "/b"] and [str(p) for p in s.output_roots] == ["/o"]
    assert s.port == 8100 and s.cors_origins == ["https://console.example.org", "http://localhost:5173"]
    assert str(s.log_dir) == str(tmp_path / "home" / "core/logs")  # DD-A13: default under $MBNAS_HOME


@pytest.mark.parametrize("origin", ["*", "https://x.org/path", "ftp://x.org", "x.org", "https://*.x.org"])
def test_cors_must_be_exact_origin(monkeypatch, origin: str) -> None:
    monkeypatch.setenv("MBNAS_CORE_CORS_ORIGINS", origin)
    with pytest.raises(ValidationError):
        Settings()


def test_secrets_empty_forbidden_and_required(monkeypatch) -> None:
    with pytest.raises(RuntimeError, match="MBNAS_CORE_API_TOKEN"):
        Settings().require_api_token()
    with pytest.raises(RuntimeError, match="token_urlsafe"):
        Settings().require_signing_key()
    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "t0ken-value")
    monkeypatch.setenv("MBNAS_CORE_ARTIFACT_SIGNING_KEY", "k3y")
    s = Settings()
    assert s.require_api_token() == "t0ken-value" and s.require_signing_key() == b"k3y"
    assert "t0ken-value" not in repr(s) and "k3y" not in repr(s)


def test_relative_paths_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(output_root="runs")
    with pytest.raises(ValidationError):
        Settings(dataset_roots=["data"])
    with pytest.raises(ValidationError):
        Settings(output_roots=[])


def test_load_dotenv(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("MBNAS_CORE_API_TOKEN=from-dotenv\nMBNAS_CORE_PORT=8123\n")
    s = Settings.load(env)
    assert s.require_api_token() == "from-dotenv" and s.port == 8123
    monkeypatch.setenv("MBNAS_CORE_ENV_FILE", str(env))  # contract §5: MBNAS_CORE_ENV_FILE replaces MBNAS_ENV_FILE
    assert Settings.load().port == 8123
    with pytest.raises(FileNotFoundError):
        Settings.load(tmp_path / "missing.env")
    monkeypatch.delenv("MBNAS_CORE_ENV_FILE")
    assert Settings.load().port == 8700  # DD-A7/DD-A13 core port
