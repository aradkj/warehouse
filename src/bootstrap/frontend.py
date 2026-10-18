"""SPA static files serving and routing integration for FastAPI."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from bootstrap.config import Settings

logger = logging.getLogger(__name__)


def mount_frontend_spa(app: FastAPI, settings: Settings) -> None:
    """Mount the compiled frontend SPA with safe fallback routing.

    Ensures that API routes (/v1/*), OpenAPI schemas (/openapi.json),
    API docs (/docs), and health probes (/healthz, /readyz) maintain absolute
    priority over any frontend asset or SPA client-side fallback.
    Path traversal is strictly prevented via resolved relative path checking.
    """
    dist_dir: Path | None = None

    if settings.frontend_dist_dir:
        candidate = Path(settings.frontend_dist_dir).resolve()
        if candidate.is_dir() and (candidate / "index.html").is_file():
            dist_dir = candidate
    else:
        # Check standard deployment locations:
        # 1. /app/frontend/dist (Docker runtime)
        # 2. repo_root / frontend / dist (Local development after npm run build)
        docker_candidate = Path("/app/frontend/dist").resolve()
        repo_candidate = (Path(__file__).resolve().parents[2] / "frontend" / "dist").resolve()

        if docker_candidate.is_dir() and (docker_candidate / "index.html").is_file():
            dist_dir = docker_candidate
        elif repo_candidate.is_dir() and (repo_candidate / "index.html").is_file():
            dist_dir = repo_candidate

    if dist_dir is None:
        logger.debug("No frontend SPA distribution found; running in API-only mode.")
        return

    logger.info("Serving frontend SPA from %s", dist_dir)
    index_html_path = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend_assets")

    clean_prefix = settings.api_prefix.strip("/")

    index_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/", include_in_schema=False)
    async def serve_index() -> Response:
        return FileResponse(index_html_path, headers=index_headers)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_or_file(full_path: str) -> Response:
        # 1. If path falls under API prefix, let it fail with 404 error
        if full_path == clean_prefix or full_path.startswith(f"{clean_prefix}/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        # 2. Health and documentation reserved routes that weren't matched
        if full_path in ("healthz", "readyz", "docs", "openapi.json"):
            raise HTTPException(status_code=404, detail="Resource not found")

        # 3. Check for static file in dist directory (e.g., favicon.ico, vite.svg)
        safe_path = (dist_dir / full_path).resolve()
        if safe_path.is_relative_to(dist_dir) and safe_path.is_file():
            return FileResponse(safe_path)

        # 4. Fallback to SPA index.html for client-side routing
        return FileResponse(index_html_path, headers=index_headers)

