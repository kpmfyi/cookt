"""HTTP entry point: JSON API under /api, read-only MCP at /mcp, images, and the PWA."""

from __future__ import annotations

import contextlib
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import db, recipes, search
from .config import settings
from .mcp_server import mcp

log = logging.getLogger("cookt")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = db.connect()
    db.init(conn)
    conn.close()
    worker = None
    if settings.run_worker:
        from . import jobs

        worker = jobs.start_worker()
    from . import push

    sender = push.Sender().start()
    async with mcp.session_manager.run():
        yield
    sender.stop()
    if worker is not None:
        worker.stop()


app = FastAPI(
    title="cookt", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# --- optional shared PIN (only if COOKT_PIN is set, e.g. if ever exposed beyond the tailnet) ---
_PIN_COOKIE = "cookt_pin"
_PIN_PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>cookt</title><body
style="font:17px system-ui;background:#c9cdd0;display:grid;place-items:center;min-height:90vh">
<form method=post action=/pin style="background:#fbfbf8;padding:24px;border-radius:6px">
<p>Kitchen PIN</p><input name=pin type=password inputmode=numeric autofocus
style="font-size:24px;width:8em"> <button style="font-size:20px">Enter</button></form>"""


def _pin_token() -> str:
    import hashlib

    return hashlib.sha256(f"cookt:{settings.pin}".encode()).hexdigest()


@app.middleware("http")
async def pin_gate(request: Request, call_next):
    if not settings.pin:
        return await call_next(request)
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1") or request.url.path == "/pin":
        return await call_next(request)  # loopback (local MCP clients, local checks) is trusted
    if request.cookies.get(_PIN_COOKIE) == _pin_token():
        return await call_next(request)
    if request.url.path.startswith(("/api/", "/mcp", "/images/")):
        return JSONResponse({"detail": "PIN required"}, status_code=401)
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_PIN_PAGE, status_code=401)


@app.post("/pin", include_in_schema=False)
async def pin_submit(request: Request) -> Response:
    import hmac

    from fastapi.responses import HTMLResponse, RedirectResponse

    form = await request.form()
    if settings.pin and hmac.compare_digest(str(form.get("pin", "")), settings.pin):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(_PIN_COOKIE, _pin_token(), max_age=400 * 86400, httponly=True,
                            samesite="lax")
        return response
    return HTMLResponse(_PIN_PAGE, status_code=401)


def conn():
    return db.get_conn()


@app.exception_handler(recipes.NotFound)
async def _not_found(_request: Request, exc: recipes.NotFound) -> JSONResponse:
    return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)


@app.exception_handler(ValueError)
async def _bad_value(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


# --- catalog & recipes -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    count = conn().execute("SELECT COUNT(*) FROM recipes WHERE archived_at IS NULL").fetchone()[0]
    return {"ok": True, "recipes": count}


@app.get("/api/catalog")
def get_catalog(request: Request) -> Response:
    data = recipes.catalog(conn())
    etag = f'W/"{abs(hash(data["version"]))}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(data, headers={"ETag": etag, "Cache-Control": "no-cache"})


@app.get("/api/recipes/{key}")
def get_recipe(key: str) -> dict[str, Any]:
    return recipes.detail(conn(), key)


class RecipeEdit(BaseModel):
    document: dict[str, Any] | None = None
    source_url: str | None = None
    source_site: str | None = None
    source_author: str | None = None
    credit: str | None = None
    description: str | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    servings: float | None = None
    personal_tags: list[str] | None = None
    cuisine_tags: list[str] | None = None
    course_tags: list[str] | None = None
    protein_tags: list[str] | None = None
    diet_tags: list[str] | None = None
    equipment_tags: list[str] | None = None
    person_id: str | None = None
    expected_version: int | None = None


@app.put("/api/recipes/{key}")
def put_recipe(key: str, body: RecipeEdit) -> dict[str, Any]:
    c = conn()
    recipe_id = recipes.resolve_id(c, key)
    changes = body.model_dump(exclude_unset=True, exclude={"person_id", "expected_version"})
    return recipes.update_recipe(
        c, recipe_id, changes, person_id=body.person_id, expected_version=body.expected_version
    )


@app.get("/api/recipes/{key}/revisions/{revision_id}")
def get_revision(key: str, revision_id: str) -> dict[str, Any]:
    c = conn()
    recipe_id = recipes.resolve_id(c, key)
    row = c.execute(
        "SELECT * FROM revisions WHERE id = ? AND recipe_id = ?", (revision_id, recipe_id)
    ).fetchone()
    if row is None:
        raise recipes.NotFound(revision_id)
    return {**dict(row), "snapshot": db.loads(row["snapshot"])}


class PersonRef(BaseModel):
    person_id: str | None = None


@app.post("/api/recipes/{key}/revisions/{revision_id}/restore")
def restore(key: str, revision_id: str, body: PersonRef) -> dict[str, Any]:
    c = conn()
    return recipes.restore_revision(c, recipes.resolve_id(c, key), revision_id, body.person_id)


class FavoriteBody(BaseModel):
    person_id: str = ""
    on: bool


@app.post("/api/recipes/{key}/favorite")
def favorite(key: str, body: FavoriteBody) -> dict[str, Any]:
    c = conn()
    recipe_id = recipes.resolve_id(c, key)
    recipes.set_favorite(c, recipe_id, body.person_id, body.on)
    return {"ok": True}


class CookBody(BaseModel):
    person_id: str | None = None
    cooked_on: str = Field(default_factory=lambda: date.today().isoformat())
    make_again: bool | None = None
    note: str | None = None
    next_time: str | None = None


@app.post("/api/recipes/{key}/cooked")
def cooked(key: str, body: CookBody) -> dict[str, Any]:
    c = conn()
    date.fromisoformat(body.cooked_on)
    return recipes.log_cook(
        c,
        recipes.resolve_id(c, key),
        person_id=body.person_id,
        cooked_on=body.cooked_on,
        make_again=body.make_again,
        note=body.note,
        next_time=body.next_time,
    )


@app.delete("/api/cooklog/{entry_id}")
def delete_cook(entry_id: str) -> dict[str, Any]:
    conn().execute("DELETE FROM cook_log WHERE id = ?", (entry_id,))
    return {"ok": True}


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    person_id: str | None = None


@app.post("/api/recipes/{key}/notes")
def add_note(key: str, body: NoteBody) -> dict[str, Any]:
    c = conn()
    return {
        "id": recipes.add_note(c, recipes.resolve_id(c, key), body.text.strip(), body.person_id)
    }


@app.delete("/api/notes/{note_id}")
def resolve_note(note_id: str) -> dict[str, Any]:
    recipes.resolve_note(conn(), note_id)
    return {"ok": True}


# --- people ------------------------------------------------------------------------------


class PersonBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@app.get("/api/people")
def people() -> list[dict[str, Any]]:
    return [dict(r) for r in conn().execute("SELECT id, name FROM people ORDER BY name")]


@app.post("/api/people")
def add_person(body: PersonBody) -> dict[str, Any]:
    c = conn()
    name = body.name.strip()
    row = c.execute("SELECT id, name FROM people WHERE name = ?", (name,)).fetchone()
    if row:
        return dict(row)
    person_id = db.new_id()
    c.execute(
        "INSERT INTO people(id, name, created_at) VALUES (?, ?, ?)", (person_id, name, db.now())
    )
    return {"id": person_id, "name": name}


# --- search ------------------------------------------------------------------------------


@app.get("/api/search/semantic")
def semantic(q: str, limit: int = 40) -> dict[str, Any]:
    return {"results": search.semantic(conn(), q, limit=limit)}


# --- feature routers ---------------------------------------------------------------------

from .api import intelligence as _intelligence  # noqa: E402

app.include_router(_intelligence.router)
for _optional in ("imports", "planning", "review", "push"):
    try:
        _module = __import__(f"cookt.api.{_optional}", fromlist=["router"])
    except ModuleNotFoundError as _exc:
        if _exc.name != f"cookt.api.{_optional}":
            raise
    else:
        app.include_router(_module.router)


# --- MCP (read-only) ---------------------------------------------------------------------

mcp.settings.streamable_http_path = "/"
app.mount("/mcp", mcp.streamable_http_app())


# --- images & PWA ------------------------------------------------------------------------


@app.get("/images/{path:path}")
def image(path: str) -> FileResponse:
    root = settings.images_dir.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _dist() -> Path:
    return settings.frontend_dist


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Response:
    if path.startswith(("api/", "mcp")):
        raise HTTPException(404)
    dist = _dist().resolve()
    target = (dist / path).resolve()
    if path and dist in target.parents and target.is_file():
        headers = {}
        if path in ("sw.js", "index.html", "manifest.webmanifest"):
            headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return FileResponse(target, headers=headers)
    index = dist / "index.html"
    if not index.is_file():
        return JSONResponse({"detail": "frontend not built"}, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


class _McpSlashFix:
    """Starlette's Mount only matches '/mcp/...'; agents POST to '/mcp'."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.inner(scope, receive, send)


asgi = _McpSlashFix(app)
