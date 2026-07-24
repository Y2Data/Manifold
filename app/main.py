from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.omnigent_compat import routes_core, routes_resources, routes_sessions, routes_stream, routes_stubs
from app.routers import chat, connections, health, imports, projects
from app.store import get_or_create_project, seed_default_connections

app = FastAPI(title="manifold-deck")
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(projects.router)
app.include_router(connections.router)
app.include_router(imports.router)
app.include_router(routes_core.router)
app.include_router(routes_sessions.router)
app.include_router(routes_stream.router)
app.include_router(routes_resources.router)
app.include_router(routes_stubs.router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# Vendored Omnigent web UI (see app/omnigent_compat/VENDORED.md) — mounted at
# /app, alongside (not replacing) the dashboard at /. The Vite build was
# compiled with an absolute "/" base, so index.html and its JS chunks
# reference /assets/..., /favicon.svg, /manifest.webmanifest etc. at the
# domain root (not /app/...) regardless of where the page itself is served
# from — so those exact root paths need to exist too. None collide with
# manifold-deck's own routes (/, /health, /summary, /decisions, /api/*,
# /v1/*, /static/*).
_OMNIGENT_STATIC = Path(__file__).parent / "omnigent_compat" / "static"
app.mount("/assets", StaticFiles(directory=str(_OMNIGENT_STATIC / "assets")), name="omnigent-assets")

for _root_asset in (
    "favicon.svg", "manifest.webmanifest", "apple-touch-icon.png",
    "pwa-192.png", "pwa-512.png", "pwa-maskable-512.png", "sw.js", "version.json",
):
    def _make_handler(filename: str):
        async def _serve():
            return FileResponse(_OMNIGENT_STATIC / filename)
        return _serve

    app.add_api_route(f"/{_root_asset}", _make_handler(_root_asset), methods=["GET"], include_in_schema=False)


@app.get("/app")
async def omnigent_spa_root():
    return FileResponse(_OMNIGENT_STATIC / "index.html")


@app.get("/app/{full_path:path}")
async def omnigent_spa(full_path: str):
    candidate = _OMNIGENT_STATIC / full_path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(_OMNIGENT_STATIC / "index.html")


# The compiled bundle's client-side router hardcodes its own internal links
# as root-relative (/c/{id}, /inbox, /settings, /login, /register, /members,
# plus nested sub-routes like /settings/policies) — it has no concept of the
# /app mount prefix, since it was built assuming root deployment. These
# specific patterns don't collide with any existing manifold-deck route, so
# serve index.html at the exact paths (and sub-paths) the bundle itself
# generates, rather than only under /app. Each needs both an exact-path route
# (for the bare "/settings" etc.) and a {path:path} route (for
# "/settings/policies" etc.) — FastAPI doesn't treat a path-converter route
# as matching zero extra segments. (Bare "/" is NOT included here — that's
# the existing dashboard and stays that way; the compiled app's own
# literal-"/" home screen is consequently only reachable via /app, not by
# clicking its in-app "New session" link, which points at "/". Flagged to
# the user as a known rough edge rather than patched silently.)
async def omnigent_spa_conversation(full_path: str):
    return FileResponse(_OMNIGENT_STATIC / "index.html")


for _spa_root_route in ("/inbox", "/settings", "/login", "/register", "/members", "/c"):
    app.add_api_route(
        _spa_root_route, omnigent_spa_root, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        _spa_root_route + "/{full_path:path}",
        omnigent_spa_conversation,
        methods=["GET"],
        include_in_schema=False,
    )

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
