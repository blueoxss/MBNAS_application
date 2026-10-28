"""Static web serving (DD-A30, WP-3 contract section 2.7).

When ``GatewaySettings.web_dist`` (``MBNAS_GW_WEB_DIST``, default ``<ROOT>/mbnas_web/dist``) holds an ``index.html``, every
``GET``/``HEAD`` outside ``/api`` is served from it: an existing file is returned as is, any other path gets
``index.html`` (SPA fallback). Hashed build assets (``assets/<name>-<hash>.<ext>``) are sent with
``Cache-Control: public, max-age=31536000, immutable``; ``index.html`` and other files with ``no-cache`` (revalidate, not
``no-store``). Paths are resolved with ``realpath`` and must stay inside the dist directory. Without a dist, those paths
answer 404 ``WEB_DIST_MISSING`` (JSON Error with guidance). Unknown ``/api/...`` paths stay 404 ``NOT_FOUND``. The web
bundle calls the API same-origin (``/api/v1``). No bearer is required for the static files (they hold no data).
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from mbnas_server.api.errors import error_response

router = APIRouter()
HASHED = re.compile(r"^assets/.+[-.][A-Za-z0-9_-]{6,}\.[A-Za-z0-9]+$")  # vite: assets/<name>-<hash>.<ext>
IMMUTABLE = "public, max-age=31536000, immutable"


def dist_present(dist: str) -> bool:
    return os.path.isfile(os.path.join(dist, "index.html"))


def _file(path: str, rel: str) -> FileResponse:
    cache = IMMUTABLE if HASHED.match(rel) else "no-cache"
    return FileResponse(path, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})


@router.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def web(request: Request, full_path: str) -> Response:
    if full_path == "api" or full_path.startswith("api/"):
        return error_response(request, 404, "NOT_FOUND", "Not Found")
    dist = os.path.realpath(request.app.state.settings.web_dist)
    if not dist_present(dist):
        return error_response(request, 404, "WEB_DIST_MISSING",
                              "web console is not installed on this gateway: build mbnas_web (npm run build) or set "
                              "MBNAS_GW_WEB_DIST to a directory holding index.html; the API is under /api/v1")
    rel = full_path.lstrip("/")
    if rel and "\x00" not in rel:
        candidate = os.path.realpath(os.path.join(dist, rel))
        if os.path.commonpath([candidate, dist]) == dist and os.path.isfile(candidate):
            return _file(candidate, os.path.relpath(candidate, dist).replace(os.sep, "/"))
        if rel.startswith("assets/"):  # a missing hashed asset must not turn into index.html
            return error_response(request, 404, "NOT_FOUND", "Not Found")
    return _file(os.path.join(dist, "index.html"), "index.html")
