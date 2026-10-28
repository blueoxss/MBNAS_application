"""Structure views for models that are not (yet) a candidate: registry baselines and user detector YAMLs.

* ``GET  /registry/{model_id}/structure``       saved check or declaration-only document; never builds a model.
* ``POST /registry/{model_id}/structure-check`` explicit CPU observation, stored under
  ``<output_root>/registry_structure/<model_id>/<architecture_hash>.json``.
* ``POST /structure-check``                     explicit CPU check of a user YAML from an allowed server root, or of the
  YAML text the gateway carries (``model_yaml_text`` + ``source_sha256``); not stored.
* ``GET  /structure/block-catalog``             the static block design-origin table (DD-38); no import, no model.

Contract: openapi 1.5.0. Responses are projected onto ``StructureReport`` / ``BlockCatalogTable`` (WP-4) and validated
with their pydantic mirrors (:func:`mbnas_core.console.deps.contract_json`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from mbnas_contracts.schemas.records import BlockCatalogTable, StructureReport
from mbnas_model_runtime import block_catalog
from mbnas_core.console import model_services
from mbnas_core.console.deps import contract_json, services
from mbnas_contracts.errors import ApiError
from mbnas_core.console.services import AppServices

router = APIRouter()


def _raise(exc: model_services.StructureRequestError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


def _body_keys(body: Any, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ApiError(422, "VALIDATION_FAILED", "request body must be a JSON object")
    errors = [{"field": k, "message": "unknown field", "code": "EXTRA_FORBIDDEN"} for k in body if k not in allowed]
    errors += [{"field": k, "message": "field required", "code": "MISSING"} for k in required if k not in body]
    if errors:
        raise ApiError(422, "VALIDATION_FAILED", "request does not match the contract schema", errors=errors)
    return body


@router.get("/registry/{model_id}/structure")
def registry_structure(model_id: str, svc: AppServices = Depends(services)):
    try:
        doc = model_services.describe_registry_model(svc.settings, model_id, svc.registry_lookup(model_id))
    except model_services.StructureRequestError as exc:
        raise _raise(exc) from exc
    return contract_json("StructureReport", StructureReport, doc)


@router.post("/registry/{model_id}/structure-check")
def registry_structure_check(model_id: str, body: Any = Body(None), svc: AppServices = Depends(services)):
    data = _body_keys(body, {"input_size"})
    try:
        doc = model_services.check_registry_model(svc.settings, model_id, data.get("input_size"))
    except model_services.StructureRequestError as exc:
        raise _raise(exc) from exc
    return contract_json("StructureReport", StructureReport, doc)


@router.post("/structure-check")
def user_structure_check(body: Any = Body(...), svc: AppServices = Depends(services)):
    """``model_path`` (a YAML under ``model_yaml_roots`` + ``output_roots``) or ``model_yaml_text`` + ``source_sha256``
    (+ optional ``model_name``): the uploaded text the gateway carries (WP-3 section 2.5); exactly one of the two."""
    data = _body_keys(body, {"model_path", "model_yaml_text", "source_sha256", "model_name", "input_size", "class_count"})
    has_path, has_text = "model_path" in data, "model_yaml_text" in data
    if has_path == has_text:
        raise ApiError(422, "VALIDATION_FAILED", "request does not match the contract schema",
                       errors=[{"field": "model_path", "message": "give exactly one of model_path or model_yaml_text",
                                "code": "MISSING" if not has_path else "EXCLUSIVE"}])
    if has_path and (not isinstance(data["model_path"], str) or not data["model_path"]):
        raise ApiError(422, "VALIDATION_FAILED", "request does not match the contract schema",
                       errors=[{"field": "model_path", "message": "must be a non-empty string", "code": "STRING_TYPE"}])
    if has_text and "source_sha256" not in data:
        raise ApiError(422, "VALIDATION_FAILED", "request does not match the contract schema",
                       errors=[{"field": "source_sha256", "message": "field required with model_yaml_text", "code": "MISSING"}])
    try:
        doc = model_services.check_user_model(svc.settings, data.get("model_path"), data.get("input_size"),
                                              data.get("class_count"), model_yaml_text=data.get("model_yaml_text"),
                                              source_sha256=data.get("source_sha256"), model_name=data.get("model_name"))
    except model_services.StructureRequestError as exc:
        raise _raise(exc) from exc
    return contract_json("StructureReport", StructureReport, doc)


@router.get("/structure/block-catalog")
def structure_block_catalog():
    """Static module-name -> design origin table (``search/block_catalog.py``).  Returns data only: nothing is
    imported, no model is built, so ``source_path`` is not part of it (it exists only on explicitly checked documents).
    Contract component ``BlockCatalogTable`` (1.4.2)."""
    return contract_json("BlockCatalogTable", BlockCatalogTable, block_catalog.static_table())
