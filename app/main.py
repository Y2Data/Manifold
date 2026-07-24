from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import chat, connections, health, imports, projects
from app.store import get_or_create_project, seed_default_connections

app = FastAPI(title="manifold-deck")
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(projects.router)
app.include_router(connections.router)
app.include_router(imports.router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
async def _seed_defaults():
    # So the workspace isn't empty on first launch — this project's own
    # directory is a reasonable default; add others via the sidebar.
    get_or_create_project(str(Path(__file__).resolve().parent.parent))
    seed_default_connections()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {})
