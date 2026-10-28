"""C02-D1 (C02 EVAL_1; SECURITY_BASELINE section 5.3): the settings of the public services carry no core state.

``RunnerSettings`` (``MBNAS_RUNNER_``) and ``GatewaySettings`` (``MBNAS_GW_``) must not hold a core DB path, a DB
credential, the core's ticket key, an admin / owner / principal secret or the core's signing key, and nothing in the
core's ``MBNAS_CORE_*`` namespace may land in them - not from the environment and not from an env file. The only secrets
they hold are their own: the runner's bearer token (issued by the core for this runner id) and, for the gateway, its
browser token, the service token the core issued to it and its own artifact signing key.

Public bundle: imports only the public packages (no ``tests/_shared`` harness, no ``mbnas_core``). Adding a secret field
to either class is meant to fail here until this file is updated on purpose.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from mbnas_runner.config import RunnerSettings
from mbnas_server.config import GatewaySettings

# word tokens (field name split at "_") that point at core-side state or a credential of someone else
CORE_STATE_TOKENS = frozenset({"db", "sqlite", "sqlite3", "database", "dsn", "ticket", "tickets", "admin", "owner",
                               "principal", "principals", "salt", "hmac", "password", "passwd", "credential",
                               "credentials", "private"})
SECRET_TOKENS = frozenset({"token", "secret", "key", "password", "passwd"})

# the core's own settings (mbnas_core.config.CoreSettings, prefix MBNAS_CORE_; not importable in the public bundle)
CORE_SECRET_ENV = ("MBNAS_CORE_DB_PATH", "MBNAS_CORE_API_TOKEN", "MBNAS_CORE_TICKET_KEY",
                   "MBNAS_CORE_ARTIFACT_SIGNING_KEY")

#: the secret-bearing fields each class may have (own credentials only), and what they are
EXPECTED_SECRET_FIELDS = {
    RunnerSettings: {"token": "the runner's bearer token (runner principal bound to runner_id)",
                     "token_file": "file holding that same runner token",
                     "gateway_upload_token_file": "reserved for artifact upload (not implemented in 0.1.0)"},
    GatewaySettings: {"api_token": "the browser's bearer token for /api/v1",
                      "core_token": "the service token the core issued to this gateway (server principal)",
                      "artifact_signing_key": "the gateway's own signing key for its signed artifact URLs"},
}
EXPECTED_PREFIX = {RunnerSettings: "MBNAS_RUNNER_", GatewaySettings: "MBNAS_GW_"}
CLASSES = (RunnerSettings, GatewaySettings)


def _tokens(name: str) -> set[str]:
    return {t for t in name.lower().split("_") if t}


def _alias_names(field: Any) -> list[str]:
    """Every explicit alias string of a pydantic field (alias, validation_alias as str / AliasChoices / AliasPath)."""
    out: list[str] = []
    for value in (field.alias, field.validation_alias, field.serialization_alias):
        if value is None:
            continue
        if isinstance(value, str):
            out.append(value)
            continue
        for choice in getattr(value, "choices", [value]):  # AliasChoices -> choices; AliasPath -> itself
            if isinstance(choice, str):
                out.append(choice)
            else:
                out.extend(str(p) for p in getattr(choice, "path", []) if isinstance(p, str))
    return out


_SECRET_NAME = re.compile(r"(?:^|_)(?:token|secret|key|password|passwd)(?:_file|_path)?$")


def _is_secret(name: str, field: Any) -> bool:
    """A SecretStr field, or a text field whose name says it holds (or points at) a token / secret / key / password
    (numeric limits such as ``sse_max_per_token`` are not secrets)."""
    annotation = str(field.annotation)
    return "SecretStr" in annotation or ("str" in annotation and bool(_SECRET_NAME.search(name)))


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_no_core_db_or_core_credential_field(cls: type) -> None:
    fields = cls.model_fields
    assert "db_path" not in fields
    assert "ticket_key" not in fields
    assert not {"admin_token", "owner_token", "principal_token", "core_api_token", "core_ticket_key",
                "core_signing_key", "core_artifact_signing_key", "core_db_path"} & set(fields)
    suspicious = sorted(n for n in fields if _tokens(n) & CORE_STATE_TOKENS)
    assert suspicious == [], f"{cls.__name__} has fields that name core state or a foreign credential: {suspicious}"
    # a "core_*" field is only the address / transport of the core, never a core-side secret
    core_secrets = sorted(n for n in fields if n.startswith("core_") and _tokens(n) & SECRET_TOKENS
                          and n not in EXPECTED_SECRET_FIELDS[cls])
    assert core_secrets == [], core_secrets


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_secret_fields_are_exactly_the_services_own(cls: type) -> None:
    secret_fields = {n for n, f in cls.model_fields.items() if _is_secret(n, f)}
    assert secret_fields == set(EXPECTED_SECRET_FIELDS[cls]), (
        f"{cls.__name__} secret-bearing fields changed: {sorted(secret_fields)}; review them against C02 / "
        "SECURITY_BASELINE 5.3 and update EXPECTED_SECRET_FIELDS on purpose")


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_env_names_stay_in_the_services_own_namespace(cls: type) -> None:
    prefix = cls.model_config.get("env_prefix")
    assert prefix == EXPECTED_PREFIX[cls]
    assert not prefix.upper().startswith("MBNAS_CORE")
    for name, field in cls.model_fields.items():
        aliases = _alias_names(field)
        assert not [a for a in aliases if a.upper().startswith("MBNAS_CORE")], (cls.__name__, name, aliases)
        if name in EXPECTED_SECRET_FIELDS[cls]:
            # own secrets are read only from this service's prefix (e.g. MBNAS_GW_ARTIFACT_SIGNING_KEY)
            assert aliases == [], (cls.__name__, name, aliases)


def _dump_values(settings: Any) -> str:
    """Every field value as text, secrets revealed (a planted value must not hide behind SecretStr's mask)."""
    parts = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        parts.append(f"{name}={value!r}")
    return "\n".join(parts)


PLANTED = {key: f"planted-{key.lower().replace('_', '-')}-c02d1" for key in CORE_SECRET_ENV}


def test_core_variables_never_reach_runner_or_gateway_settings(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    """The core's variables in the process environment or in the services' env files are ignored (extra="ignore",
    own prefix): none of the planted values appears in any field. A misnamed ``MBNAS_RUNNER_DB_PATH`` /
    ``MBNAS_GW_DB_PATH`` / ``..._TICKET_KEY`` finds no field either."""
    for key, value in PLANTED.items():
        monkeypatch.setenv(key, value)
    runner_extra = {"MBNAS_RUNNER_DB_PATH": "planted-runner-db-path-c02d1",
                    "MBNAS_RUNNER_TICKET_KEY": "planted-runner-ticket-key-c02d1",
                    "MBNAS_RUNNER_ADMIN_TOKEN": "planted-runner-admin-token-c02d1"}
    gateway_extra = {"MBNAS_GW_DB_PATH": "planted-gw-db-path-c02d1",
                     "MBNAS_GW_TICKET_KEY": "planted-gw-ticket-key-c02d1",
                     "MBNAS_GW_ADMIN_TOKEN": "planted-gw-admin-token-c02d1"}
    runner_env = tmp_path / "runner.env"
    runner_env.write_text("".join(f"{k}={v}\n" for k, v in {**PLANTED, **runner_extra}.items())
                          + "MBNAS_RUNNER_RUNNER_ID=runner-c02d1aa0\n", encoding="utf-8")
    gateway_env = tmp_path / "gateway.env"
    gateway_env.write_text("".join(f"{k}={v}\n" for k, v in {**PLANTED, **gateway_extra}.items()), encoding="utf-8")

    runner = RunnerSettings.load(runner_env)
    gateway = GatewaySettings.load(gateway_env)
    assert runner.runner_id == "runner-c02d1aa0"  # the env file was read
    planted = [*PLANTED.values(), *runner_extra.values(), *gateway_extra.values()]
    for settings in (runner, gateway, RunnerSettings(), GatewaySettings()):
        dumped = _dump_values(settings)
        assert [v for v in planted if v in dumped] == [], (type(settings).__name__, dumped)
        assert not hasattr(settings, "db_path") and not hasattr(settings, "ticket_key")
