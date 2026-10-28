"""jsonschema validation of live responses against the canonical contract (tests and smoke scripts).

The contract document itself (``contract``, ``component``, ``prune``) is read through :mod:`mbnas_contracts.openapi`, which
stays dependency-free (pydantic/PyYAML only; trust rule ii). The jsonschema Draft 2020-12 validator therefore lives here,
in the package that already depends on jsonschema (``mbnas-server``; installed in the owner venv as well).

* :func:`validator` returns a jsonschema Draft 2020-12 validator for one component with all ``$ref``s resolved
  against the full document (used by tests and the smoke script to check live responses).
* :func:`contract_errors` lists the violations of a value against one component.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from mbnas_contracts.openapi import contract

_REF_PREFIX = "#/components/schemas/"


@lru_cache(maxsize=None)
def validator(name: str) -> Any:
    """jsonschema validator for component ``name`` (Draft 2020-12, local ``$ref`` resolution)."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resource = Resource(contents=contract(), specification=DRAFT202012)
    registry = Registry().with_resource("urn:mbnas-contract", resource)
    schema = {"$ref": f"urn:mbnas-contract{_REF_PREFIX}{name}"}
    return Draft202012Validator(schema, registry=registry)


def contract_errors(name: str, value: Any) -> list[str]:
    """Human-readable contract violations of ``value`` against component ``name`` (empty when valid)."""
    return [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
            for e in validator(name).iter_errors(value)]
