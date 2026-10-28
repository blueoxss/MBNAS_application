"""Helpers bound to the canonical contract ``openapi.json`` (commander-owned, read-only here).

The contract ships as package data of this package next to ``HISTORY.json`` (the version index of the contract
history; the history snapshots and migration scripts themselves are repository files, not package data).
:func:`contract` reads it through :mod:`importlib.resources`; ``CONTRACT_PATH``/``HISTORY_PATH`` are the file-system
locations of the two data files for tools and tests that need a path (the distribution is installed unzipped).

* :func:`prune` removes ``None`` values from properties whose contract schema does not admit ``null`` (e.g.
  ``CandidateEvaluation.search_seed`` or ``Worker.epoch``), recursing through ``$ref``/``items`` and the
  ``allOf``/``if``/``then`` branches whose condition matches (e.g. ``Event.payload`` typed by ``Event.type``). Repository rows
  are dumped with ``exclude_none=False``, so every response passes through here before leaving the API.
* The jsonschema Draft 2020-12 validator (``validator``/``contract_errors``, used by tests and the smoke script to check
  live responses) is not here: this package imports only pydantic/PyYAML, so it lives in the gateway package
  (``mbnas_server.api.contract_check``, WP-1 contract §4) and validates against :func:`contract`.

Source: ``mbnas_server.api.contract`` (monolithic package, snapshot HEAD 18c4815), split per WP-1 contract §4 / DD-A10.
``component``, ``prune`` and their helpers are unchanged; :func:`contract` reads the package data instead of
``SERVER_ROOT/contracts/openapi.json``; ``HISTORY_PATH`` is new.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

_DATA = resources.files(__name__)  # this package's data directory (openapi.json, HISTORY.json)
CONTRACT_PATH = Path(str(_DATA / "openapi.json"))
HISTORY_PATH = Path(str(_DATA / "HISTORY.json"))
_REF_PREFIX = "#/components/schemas/"


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    """Parsed contract document (cached)."""
    return json.loads(_DATA.joinpath("openapi.json").read_text(encoding="utf-8"))


def component(name: str) -> dict[str, Any]:
    """Schema of one ``components.schemas`` entry."""
    return contract()["components"]["schemas"][name]


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        return component(ref[len(_REF_PREFIX):])
    return schema


def _allows_null(schema: dict[str, Any]) -> bool:
    schema = _resolve(schema)
    if not schema:
        return True
    typ = schema.get("type")
    if typ == "null" or (isinstance(typ, list) and "null" in typ):
        return True
    return any(_allows_null(s) for key in ("anyOf", "oneOf") for s in schema.get(key, []))


def _matches(cond: dict[str, Any], value: dict[str, Any]) -> bool:
    """Minimal ``if`` evaluation used by the contract: required keys + ``const``/``enum`` on properties."""
    if any(k not in value for k in cond.get("required", [])):
        return False
    for key, rule in cond.get("properties", {}).items():
        if key not in value:
            continue
        if "const" in rule and value[key] != rule["const"]:
            return False
        if "enum" in rule and value[key] not in rule["enum"]:
            return False
    return True


def _object_schemas(schema: dict[str, Any], value: dict[str, Any]) -> list[dict[str, Any]]:
    schema = _resolve(schema)
    out = [schema]
    for key in ("anyOf", "oneOf"):
        out.extend(_resolve(s) for s in schema.get(key, []) if _resolve(s).get("type") in ("object", None))
    for part in schema.get("allOf", []):
        part = _resolve(part)
        if "if" in part and "then" in part and _matches(part["if"], value):
            out.append(_resolve(part["then"]))
        elif "if" not in part:
            out.append(part)
    return out


def _prune(value: Any, schema: dict[str, Any]) -> Any:
    schema = _resolve(schema)
    if isinstance(value, list):
        items = schema.get("items")
        if items is None:
            for s in schema.get("anyOf", []):
                if _resolve(s).get("type") == "array":
                    items = _resolve(s).get("items")
        return [_prune(v, items) for v in value] if isinstance(items, dict) else value
    if not isinstance(value, dict):
        return value
    props: dict[str, Any] = {}
    for s in _object_schemas(schema, value):
        props.update(s.get("properties", {}))
    out: dict[str, Any] = {}
    for key, val in value.items():
        prop = props.get(key)
        if prop is None:
            out[key] = val
        elif val is None and not _allows_null(prop):
            continue
        else:
            out[key] = _prune(val, prop)
    return out


def prune(name: str, value: Any) -> Any:
    """Copy of ``value`` with nulls dropped wherever component ``name`` forbids them (see module docstring)."""
    return _prune(value, component(name))
