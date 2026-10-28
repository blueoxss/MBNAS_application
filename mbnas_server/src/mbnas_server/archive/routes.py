"""``/api/v1/archive/...`` read-only routes (mounted on the bearer-protected router by ``api.app.create_app``).

Contract-validated bodies are cached as bytes inside the run's :class:`RunData` (``vol:`` entries are dropped every
``archive_cache_ttl_s`` because they depend on process liveness), so a repeated request costs a dictionary lookup.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from mbnas_server.api.deps import contract_json
from mbnas_contracts.errors import ApiError
from mbnas_contracts.schemas import archive as S
from mbnas_server.archive import views
from mbnas_server.archive.scanner import ArchiveError, ArchiveStore, RunData

router = APIRouter(prefix="/archive", tags=["archive"])
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def get_store(request: Request) -> ArchiveStore:
    return request.app.state.archive


def _guard(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except ArchiveError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc


def _json(component: str, model: type, build: Callable[[], dict[str, Any]], cache: tuple[RunData, str] | None = None) -> Response:
    """Validate ``build()`` against the contract and answer it; with ``cache=(run_data, key)`` the validated body is reused."""
    if cache is not None:
        rd, key = cache
        # keys starting with "vol:" depend on process liveness and are dropped by RunData.refresh_volatile every TTL
        body = rd.cached_view(key if key.startswith("vol:") else "resp:" + key, lambda: contract_json(component, model, build()).body)
    else:
        body = contract_json(component, model, build()).body
    return Response(content=body, media_type="application/json")


def _run(store: ArchiveStore, run_id: str) -> RunData:
    return _guard(lambda: store.run(run_id))


@router.get("/runs")
def list_runs(store: ArchiveStore = Depends(get_store)):
    return _json("ArchiveRunList", S.ArchiveRunList, lambda: _guard(lambda: views.run_list(store)))


@router.get("/runs/{run_id}")
def get_run(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    live = views.run_summary(rd)["has"]["live"]  # a live body embeds server time / GPU usage: never cached
    return _json("ArchiveRun", S.ArchiveRun, lambda: views.run_detail(rd, lambda: views.live_view(store)),
                 None if live else (rd, "vol:resp:detail"))


@router.get("/runs/{run_id}/baselines")
def get_baselines(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveBaselineList", S.ArchiveBaselineList, lambda: views.baselines_view(rd), (rd, "baselines"))


@router.get("/runs/{run_id}/generations")
def get_generations(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveGenerationList", S.ArchiveGenerationList, lambda: views.generations_view(rd), (rd, "generations"))


@router.get("/runs/{run_id}/generations/{g}")
def get_generation(run_id: str, g: int = Path(..., ge=0, le=100000), store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveGenerationDetail", S.ArchiveGenerationDetail,
                 lambda: _guard(lambda: views.generation_detail(rd, g)), (rd, f"generation:{g}"))


@router.get("/runs/{run_id}/candidates")
def get_candidates(run_id: str, generation: int | None = Query(None, ge=0), tier: str | None = Query(None, max_length=64),
                   status: str | None = Query(None, max_length=32), limit: int = Query(500, ge=1, le=5000),
                   offset: int = Query(0, ge=0), store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    key = f"candidates:{generation}:{tier}:{status}:{limit}:{offset}"
    return _json("ArchiveCandidateList", S.ArchiveCandidateList,
                 lambda: views.candidate_list(rd, generation, tier, status, limit, offset), (rd, key))


@router.get("/runs/{run_id}/candidates/{cid}")
def get_candidate(run_id: str, cid: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveCandidateDetail", S.ArchiveCandidateDetail,
                 lambda: _guard(lambda: views.candidate_detail(rd, cid)), (rd, f"candidate:{cid}"))


@router.get("/runs/{run_id}/pareto")
def get_pareto(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchivePareto", S.ArchivePareto, lambda: views.pareto_view(rd), (rd, "pareto"))


@router.get("/runs/{run_id}/fidelity")
def get_fidelity(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveFidelity", S.ArchiveFidelity, lambda: views.fidelity_view(rd), (rd, "fidelity"))


@router.get("/runs/{run_id}/confirm")
def get_confirm(run_id: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    return _json("ArchiveConfirm", S.ArchiveConfirm, lambda: views.confirm_view(rd), (rd, "confirm"))


@router.get("/runs/{run_id}/files/{kind}")
def get_file(run_id: str, kind: str, store: ArchiveStore = Depends(get_store)):
    rd = _run(store, run_id)
    data, media = _guard(lambda: views.read_file(rd, kind))
    return Response(content=data, media_type=media, headers=NO_STORE)


@router.get("/compare")
def compare(runs: str = Query(..., min_length=1, max_length=1024), store: ArchiveStore = Depends(get_store)):
    ids = [r for r in runs.split(",") if r != ""]
    if not 1 <= len(ids) <= 6 or len(set(ids)) != len(ids):
        raise ApiError(422, "ARCHIVE_BAD_ID", "runs must list 1 to 6 distinct run ids separated by commas")
    for rid in ids:
        _run(store, rid)  # validates every id (422 / 404 / 503) before any work
    return _json("ArchiveCompare", S.ArchiveCompare, lambda: views.compare_view(store, ids))


@router.get("/live")
def live(store: ArchiveStore = Depends(get_store)):
    if not store.has_available_root():
        raise ApiError(503, "ARCHIVE_ROOT_UNAVAILABLE", "no configured archive root is available")
    return _json("ArchiveLive", S.ArchiveLive, lambda: views.live_view(store))
