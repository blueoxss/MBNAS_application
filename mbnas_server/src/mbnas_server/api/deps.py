"""Shared route helpers of the gateway: contract-pruned JSON responses.

Only :func:`contract_json` is needed here (the archive routes use it). The per-request database connection, the service
container and the run lookup of the original ``api.deps`` belong to the core console (``mbnas_core.console.deps``); the
gateway holds no database. ``prune`` comes from :mod:`mbnas_contracts.openapi` (WP-1 contract section 4).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from mbnas_contracts.errors import ApiError, pydantic_errors
from mbnas_contracts.openapi import prune

log = logging.getLogger("mbnas_server.api")


def contract_json(component: str, model: type[BaseModel], payload: dict[str, Any], status: int = 200,
                  headers: dict[str, str] | None = None) -> JSONResponse:
    """Validate ``payload`` with the pydantic mirror of ``component`` and return it pruned to the contract.

    A payload that does not match is the SERVER's fault (a stored row or a view out of step with the contract): it is
    logged and answered 500 ``RESPONSE_CONTRACT_VIOLATION`` with the field errors, never 422 "request does not match"
    (which would blame the client's request).
    """
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        errors = pydantic_errors(exc)
        log.error("response for %s violates the contract: %s", component, errors[:5])
        raise ApiError(500, "RESPONSE_CONTRACT_VIOLATION",
                       f"server response for {component} does not match the contract (see server log by request_id)",
                       errors=errors) from exc
    return JSONResponse(prune(component, payload), status_code=status, headers=headers)
