"""S3-F5: the native_fallback search space is opt-in (console.search.allow_native_fallback, default false).

Without the opt-in an unloadable ETRI mixed pool is a SEARCH_SPACE_UNAVAILABLE error in the validator and in
``SearchSpace.create`` / the PROPOSE_GENERATION handler; the resolved variant is in the validate ``resolved`` doc,
the validation capabilities and /health capabilities. CPU only.

WP-1 (DD-A12): the pool is vendored, so "unavailable" can no longer be produced with a missing ETRI root
(``etri_root="/nonexistent"`` / ``MBNAS_ETRI_NAS_ROOT``). It is simulated instead (ETRI_RUNTIME_SCOPE §6.4):
``SearchSpace.create`` gets the documented ``EtriUnavailable`` from ``load_etri_mixed``, the validator gets an
unavailable probe, and the subprocess probe check hides ultralytics (``find_spec`` -> None, as without ml extras).
"""
from __future__ import annotations

import copy
import importlib.util
import os
from typing import Any

import pytest

from mbnas_core.protocol.presets import pilot_smoke_protocol
from mbnas_core.protocol.validator import validate
from mbnas_core.search import space as space_module
from mbnas_core.search.mixed_pool import bridge
from mbnas_core.search.mixed_pool.bridge import SearchSpaceUnavailable, probe_mixed_pool
from mbnas_core.search.space import SearchSpace

SIMULATED = "simulated: vendored mixed pool not registrable (WP-1 test)"


def _pool_unavailable(monkeypatch) -> None:
    """``SearchSpace.create`` gets the documented EtriUnavailable from the mixed-pool loader."""
    def unavailable(*_a, **_k):
        raise bridge.EtriUnavailable(SIMULATED)

    monkeypatch.setattr(space_module, "load_etri_mixed", unavailable)


def _probe_unavailable(monkeypatch) -> None:
    """The validator probe reports the pool unavailable with SIMULATED as the reason."""
    def probe(*_a, **_k):
        return {"root": None, "available": False, "variant": "unavailable", "reason": SIMULATED,
                "checked": "simulated by the test"}

    monkeypatch.setattr(bridge, "probe_mixed_pool", probe)
    import mbnas_core.protocol.validator as validator_module

    if hasattr(validator_module, "probe_mixed_pool"):  # a module-level import of the probe is patched as well
        monkeypatch.setattr(validator_module, "probe_mixed_pool", probe)


@pytest.fixture
def tiny_proto(make_dataset, tmp_roots) -> dict[str, Any]:
    paths = make_dataset()
    p = pilot_smoke_protocol()
    p["console"]["paths"] = {k: paths[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    p["console"]["paths"]["output_root"] = str(tmp_roots["runs"] / "out")
    return p


def test_create_without_opt_in_refuses_the_fallback(monkeypatch) -> None:
    _pool_unavailable(monkeypatch)
    with pytest.raises(SearchSpaceUnavailable) as exc:
        SearchSpace.create(nc=1, imgsz=320, families=("yolo11n",))
    assert "allow_native_fallback" in str(exc.value) and SIMULATED in str(exc.value)


# 2026-10-21 (D4): tests that genuinely build real ultralytics models / need the ETRI pool skip (not fail/error) on a
# host without the ml extras. Torch-free tests in this module keep running.
needs_ml = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("ultralytics") is None,
    reason="needs torch + ultralytics (ml extras)")
# the validator only checks that ultralytics is *findable* (never imports it); MBNAS_TEST_ASSUME_ULTRALYTICS is gone
# (DD-A22: ultralytics is installed in the venv)
needs_ultralytics_spec = pytest.mark.skipif(
    importlib.util.find_spec("ultralytics") is None, reason="needs ultralytics findable (installed)")


@needs_ml
def test_create_with_opt_in_falls_back_and_says_so(monkeypatch) -> None:
    _pool_unavailable(monkeypatch)
    s = SearchSpace.create(nc=1, imgsz=320, families=("yolo11n",), allow_native_fallback=True)
    d = s.describe()
    assert d["variant"] == "native_fallback" and SIMULATED in d["fallback_reason"]
    assert d["allow_native_fallback"] is True


@needs_ml
def test_probe_is_torch_free_and_reports_the_variant() -> None:
    import subprocess
    import sys

    # WP-1: no root to point elsewhere; "unavailable" = ultralytics not findable (a None entry in sys.modules makes
    # importlib.util.find_spec return None, as on a host without the ml extras)
    code = ("import sys; from mbnas_core.search.mixed_pool.bridge import probe_mixed_pool as p; "
            "a=p(); sys.modules['ultralytics'] = None; b=p(); print(a['variant'], b['variant']); "
            "sys.exit(0 if a['variant']=='etri_mixed' and b['variant']=='unavailable' and b['reason'] "
            "and 'torch' not in sys.modules else 1)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_ultralytics_spec
def test_validator_default_resolves_etri_mixed(tiny_proto, ctx_factory) -> None:
    r = validate(tiny_proto, ctx_factory())
    assert r.valid, r.errors
    s = r.resolved["console"]["search"]
    assert s["allow_native_fallback"] is False
    assert s["search_space_resolved"]["variant"] == "etri_mixed"
    assert r.capabilities["search_space"]["variant"] == "etri_mixed"
    again = validate(r.resolved, ctx_factory())
    assert again.valid and again.protocol_hash == r.protocol_hash  # fixed point


def test_validator_blocks_missing_pool_without_opt_in(tiny_proto, ctx_factory, monkeypatch) -> None:
    _probe_unavailable(monkeypatch)
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid
    err = next(e for e in r.errors if e["code"] == "SEARCH_SPACE_UNAVAILABLE")
    assert err["field"] == "console.search.allow_native_fallback" and SIMULATED in err["message"]


def test_validator_opt_in_resolves_native_fallback(tiny_proto, ctx_factory, monkeypatch) -> None:
    _probe_unavailable(monkeypatch)
    tiny_proto["console"]["search"]["allow_native_fallback"] = True
    r = validate(tiny_proto, ctx_factory())
    assert r.valid, r.errors
    assert r.resolved["console"]["search"]["search_space_resolved"]["variant"] == "native_fallback"
    assert any(w["code"] == "SEARCH_SPACE_FALLBACK" for w in r.warnings)


def test_validator_baseline_only_run_needs_no_space(tiny_proto, ctx_factory, monkeypatch) -> None:
    _probe_unavailable(monkeypatch)
    tiny_proto["console"]["search"]["arms"] = []
    r = validate(tiny_proto, ctx_factory())
    assert not any(e["code"] == "SEARCH_SPACE_UNAVAILABLE" for e in r.errors)
    if r.valid:
        assert r.resolved["console"]["search"]["search_space_resolved"]["variant"] == "not_required"


def test_validator_rejects_non_bool_flag(tiny_proto, ctx_factory) -> None:
    tiny_proto["console"]["search"]["allow_native_fallback"] = "yes"
    r = validate(tiny_proto, ctx_factory())
    assert not r.valid and any(e["field"] == "console.search.allow_native_fallback" for e in r.errors)


@needs_ultralytics_spec
def test_flag_is_part_of_the_protocol_hash(tiny_proto, ctx_factory) -> None:
    a = validate(tiny_proto, ctx_factory())
    other = copy.deepcopy(tiny_proto)
    other["console"]["search"]["allow_native_fallback"] = True
    b = validate(other, ctx_factory())
    assert a.valid and b.valid and a.protocol_hash != b.protocol_hash


@needs_ml
def test_handler_refuses_fallback_without_opt_in(monkeypatch) -> None:
    from mbnas_core.search import handlers

    monkeypatch.setattr(handlers, "_SPACES", {})
    _pool_unavailable(monkeypatch)
    with pytest.raises(SearchSpaceUnavailable):
        handlers.shared_space(nc=1, imgsz=320, families=("yolo11n",), etri_root=None)
    s = handlers.shared_space(nc=1, imgsz=320, families=("yolo11n",), etri_root=None,
                              allow_native_fallback=True)
    assert s.variant == "native_fallback"
