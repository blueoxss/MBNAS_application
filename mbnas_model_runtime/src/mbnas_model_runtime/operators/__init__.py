"""Custom NAS operators for ultralytics YAML models: the vendored ETRI mixed operator pool (DD-A2, DD-A12; WP-1
contract §6.1-§6.3).

* :mod:`.etri_mixed_blocks` — ``NASPatchMerge`` / ``NASWindowTransformer`` / ``NASGlobalTransformer``, ``audit_model``
  and ``register_ultralytics_blocks``, vendored from ETRI_NAS ``yolo_ga_runtime/mixed_blocks.py``;
* :mod:`.parse_model_8_4_173` — the static, exec-free ``parse_model`` of ultralytics 8.4.173 with the three operators
  added (``base_modules`` +3, ``repeat_modules`` +2), installed only when the installed parser source matches the
  pinned sha256 (otherwise :class:`OperatorsUnavailable`; there is no silent fallback).

Importing this package is torch-free: torch, ultralytics and the operator code are imported only when
:func:`register_ultralytics_blocks`, :func:`ensure_registered` or :func:`audit_model` is called, so the validator and
``/health`` can use :func:`probe`, :func:`yaml_uses_mixed_modules` and :func:`operator_files_sha256` without loading
torch. :func:`ensure_registered` is the target of the ``register_hook`` string
``"mbnas_model_runtime.operators:ensure_registered"`` (:data:`mbnas_model_runtime.adapters.base.REGISTER_HOOKS`).
The classes now live in this package, so ultralytics checkpoints that pickled them under the pre-vendoring module
path ``yolo_ga_runtime.mixed_blocks`` cannot be unpickled and are not supported (DD-A12).
"""
from __future__ import annotations

from typing import Any

MIXED_MODULES: tuple[str, ...] = ("NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer")
#: operator modules of this package whose installed bytes identify the parser extension (:func:`operator_files_sha256`)
OPERATOR_MODULES: tuple[str, ...] = ("etri_mixed_blocks", "parse_model_8_4_173")
PROBE_CHECKED = "vendored operators importable + ultralytics installed"

__all__ = ["MIXED_MODULES", "OPERATOR_MODULES", "OperatorsUnavailable", "audit_model", "ensure_registered",
           "operator_files_sha256", "probe", "register_ultralytics_blocks", "yaml_uses_mixed_modules"]


class OperatorsUnavailable(RuntimeError):
    """The vendored operators could not be registered with the installed ultralytics (message = exact reason), e.g.
    ``ultralytics parse_model source mismatch`` when the installed parser is not the one the static copy was made
    from."""


def register_ultralytics_blocks() -> dict[str, Any]:
    """Re-export of :func:`.etri_mixed_blocks.register_ultralytics_blocks` (imported on call, keeping this package
    torch-free): register the three classes and install the static ``parse_model`` copy in this process only;
    idempotent; returns the registration capabilities."""
    from mbnas_model_runtime.operators.etri_mixed_blocks import register_ultralytics_blocks as register

    return register()


def audit_model(model: Any) -> dict[str, Any]:
    """Re-export of :func:`.etri_mixed_blocks.audit_model` (observed structural taxonomy of a built model)."""
    from mbnas_model_runtime.operators.etri_mixed_blocks import audit_model as audit

    return audit(model)


def ensure_registered() -> dict[str, Any]:
    """Register the vendored operators for building candidate YAMLs; returns the registration capabilities.

    The ``register_hook`` target (``"mbnas_model_runtime.operators:ensure_registered"``) that training, evaluation
    and benchmark children call before building a YAML that names the custom modules. Idempotent. Every import or
    registration failure is raised as :class:`OperatorsUnavailable` with the exact reason; on success the three classes
    are resolvable from ``ultralytics.nn.tasks`` and the installed ``parse_model`` carries ``_nas_mixed_installed``.
    """
    try:
        capabilities = register_ultralytics_blocks()
        from ultralytics.nn import tasks
    except OperatorsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - every import/registration failure must surface as one reason
        raise OperatorsUnavailable(f"{type(exc).__name__}: {exc}") from exc
    unresolved = [name for name in MIXED_MODULES if not isinstance(getattr(tasks, name, None), type)]
    if unresolved or not getattr(tasks.parse_model, "_nas_mixed_installed", False):
        raise OperatorsUnavailable(f"registration did not expose {unresolved or 'patched parse_model'} on "
                                   "ultralytics.nn.tasks")
    return capabilities


def yaml_uses_mixed_modules(model_yaml: dict[str, Any]) -> bool:
    """True when any backbone/head row names one of the ETRI custom modules."""
    rows = list(model_yaml.get("backbone", [])) + list(model_yaml.get("head", []))
    return any(str(row[2]).split(".")[-1] in MIXED_MODULES for row in rows if isinstance(row, (list, tuple)) and len(row) > 2)


def _spec_origin(name: str) -> str | None:
    """``importlib.util.find_spec(name).origin`` without importing ``name`` (``None`` when it cannot be found)."""
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    return None if spec is None else (spec.origin or "")


def probe() -> dict[str, Any]:
    """Torch-free availability check of the vendored operators (validator, ``/health``); imports neither torch,
    ultralytics nor the operator code.

    ``available`` is True when the registration is already installed in this process, or when ultralytics and torch
    are installed and both operator modules are present; else False with the exact ``reason``. A registration that
    fails later (e.g. ``ultralytics parse_model source mismatch``) is still raised by :func:`ensure_registered`.
    Keys: ``available``, ``reason``, ``checked``, ``installed_in_process``.
    """
    import sys

    tasks = sys.modules.get("ultralytics.nn.tasks")
    if tasks is not None and getattr(getattr(tasks, "parse_model", None), "_nas_mixed_installed", False):
        return {"available": True, "reason": None, "checked": "registered in this process",
                "installed_in_process": True}
    out: dict[str, Any] = {"available": False, "checked": PROBE_CHECKED, "installed_in_process": False}
    for name in ("ultralytics", "torch"):
        if _spec_origin(name) is None:
            return {**out, "reason": f"{name} is not installed"}
    missing = [module for module in OPERATOR_MODULES if not _spec_origin(f"{__name__}.{module}")]
    if missing:
        return {**out, "reason": f"vendored operator modules missing from {__name__}: {missing}"}
    return {**out, "available": True, "reason": None}


def operator_files_sha256() -> dict[str, str]:
    """sha256 of the installed bytes of the operator modules (:data:`OPERATOR_MODULES`), keyed
    ``mbnas_model_runtime/operators/<module>.py``; read from the files, nothing is imported. A module that is not
    installed as a readable ``.py`` file raises :class:`OperatorsUnavailable` (never silently omitted)."""
    import hashlib
    from pathlib import Path

    out: dict[str, str] = {}
    for module in OPERATOR_MODULES:
        origin = _spec_origin(f"{__name__}.{module}")
        if not origin or not origin.endswith(".py") or not Path(origin).is_file():
            raise OperatorsUnavailable(f"operator module {__name__}.{module} is not installed as a source file "
                                       f"(origin {origin!r})")
        out[f"{__name__.replace('.', '/')}/{module}.py"] = hashlib.sha256(Path(origin).read_bytes()).hexdigest()
    return out
