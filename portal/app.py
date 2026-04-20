"""
Certification Portal — FastAPI Application

Run with:
    python -m portal.app
    uvicorn portal.app:app --reload --port 8000
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

app = FastAPI(title="LLM Certification Portal", docs_url="/docs")
app.mount("/static", StaticFiles(directory=PORTAL_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PORTAL_DIR / "templates")

client = PortalClient()


# --------------- HTML Routes ---------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = client.get_dashboard_data()
    return templates.TemplateResponse(request, "dashboard.html", {
        "rows": data,
    })


@app.get("/breakdown/{dataset:path}/{run_name}", response_class=HTMLResponse)
async def breakdown(request: Request, dataset: str, run_name: str):
    data = client.get_run_breakdown(dataset, run_name)
    return templates.TemplateResponse(request, "breakdown.html", {
        "data": data,
    })


@app.get("/history/{dataset:path}", response_class=HTMLResponse)
async def history(request: Request, dataset: str):
    runs = client.get_history(dataset)
    return templates.TemplateResponse(request, "history.html", {
        "dataset": dataset,
        "dataset_short": dataset.split("/")[-1],
        "runs": runs,
    })


@app.get("/run/{dataset:path}/{run_name}", response_class=HTMLResponse)
async def run_detail(request: Request, dataset: str, run_name: str):
    data = client.get_run_detail(dataset, run_name)
    return templates.TemplateResponse(request, "run_detail.html", {
        "data": data,
    })


# --------------- JSON API Routes ---------------

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


# --------------- Entry Point ---------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORTAL_PORT", "8050"))
    print(f"Starting Certification Portal on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
