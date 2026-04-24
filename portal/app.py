"""
Certification Portal — FastAPI + React (Click UI) SPA.

Run with:
    python -m portal.app
    uvicorn portal.app:app --reload --port 8050

Development:
    # API
    uvicorn portal.app:app --reload --port 8050
    # UI with live reload (proxies /api → :8050)
    cd portal/frontend && npm run dev

Production:
    cd portal/frontend && npm run build
    uvicorn portal.app:app --port 8050
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

from portal.langfuse_client import PortalClient

# --------------- App Setup ---------------

PORTAL_DIR = Path(__file__).parent
FRONTEND_DIST = PORTAL_DIR / "frontend" / "dist"

app = FastAPI(title="LLM Certification Portal", docs_url="/docs")
client = PortalClient()


# --------------- JSON API Routes ---------------

@app.get("/api/config")
async def api_config():
    return JSONResponse({"langfuse_url": client.host})


@app.get("/api/dashboard")
async def api_dashboard():
    return JSONResponse(client.get_dashboard_data())


@app.get("/api/breakdown/{dataset:path}/{run_name}")
async def api_breakdown(dataset: str, run_name: str):
    return JSONResponse(client.get_run_breakdown(dataset, run_name))


@app.get("/api/history/{dataset:path}")
async def api_history(dataset: str):
    return JSONResponse(client.get_history(dataset))


@app.get("/api/run/{dataset:path}/{run_name}")
async def api_run(dataset: str, run_name: str):
    return JSONResponse(client.get_run_detail(dataset, run_name))


# --------------- SPA Static Serving ---------------

if FRONTEND_DIST.exists():
    # Serve hashed asset files from /assets (Vite's default output dir)
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_file = FRONTEND_DIST / "index.html"

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve index.html for any unmatched path so React Router handles it."""
        # Serve bundled files at the root (favicon.svg, robots.txt, etc.)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)
else:
    @app.get("/")
    async def not_built():
        return JSONResponse(
            {
                "error": "Frontend not built.",
                "fix": "cd portal/frontend && npm install && npm run build",
            },
            status_code=503,
        )


# --------------- Entry Point ---------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORTAL_PORT", "8050"))
    print(f"Starting Certification Portal on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
