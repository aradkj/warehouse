"""Tests for FastAPI frontend static serving and SPA fallback."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from bootstrap.app import create_app
from bootstrap.config import Settings

pytestmark = pytest.mark.api


@pytest.fixture
def mock_frontend_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    index_html = dist / "index.html"
    index_html.write_text(
        "<!doctype html><html><head><title>Arad Platform</title></head><body><div id='root'></div></body></html>"
    )

    assets = dist / "assets"
    assets.mkdir()
    css_file = assets / "test.css"
    css_file.write_text("body { background: #000; }")

    vite_svg = dist / "vite.svg"
    vite_svg.write_text("<svg>icon</svg>")

    return dist


@pytest.mark.asyncio
async def test_frontend_spa_serving_and_route_precedence(mock_frontend_dist: Path):
    settings = Settings(
        frontend_dist_dir=str(mock_frontend_dist),
        environment="test",
        api_prefix="/v1",
    )
    app = create_app(settings=settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Root / returns index.html
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "<title>Arad Platform</title>" in resp.text
        assert resp.headers["content-type"].startswith("text/html")

        # 2. Direct client-side route navigation falls back to index.html
        resp_spa = await client.get("/orders/new")
        assert resp_spa.status_code == 200
        assert "<title>Arad Platform</title>" in resp_spa.text

        # 3. Static asset in assets/ directory
        resp_css = await client.get("/assets/test.css")
        assert resp_css.status_code == 200
        assert "background: #000" in resp_css.text

        # 4. Root-level static file
        resp_svg = await client.get("/vite.svg")
        assert resp_svg.status_code == 200
        assert "<svg>icon</svg>" in resp_svg.text

        # 5. Non-existent API route does NOT return index.html (returns 404 error)
        resp_api = await client.get("/v1/non_existent_route")
        assert resp_api.status_code == 404
        assert "text/html" not in resp_api.headers.get("content-type", "")

        # 6. Health probe endpoint is not intercepted by SPA
        resp_health = await client.get("/healthz")
        assert resp_health.status_code == 200
        assert resp_health.headers["content-type"].startswith("application/json")

        # 7. Path traversal attempt fails safely
        resp_traversal = await client.get("/../../etc/passwd")
        # Starlette normalizes or responds with 404
        assert resp_traversal.status_code in (404, 200)
        if resp_traversal.status_code == 200:
            # Must be index.html, never an arbitrary file
            assert "<title>Arad Platform</title>" in resp_traversal.text
