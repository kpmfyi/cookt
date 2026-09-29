"""Import endpoints: URL, paste, photos/handwritten, social, Paprika, cookbook EPUB, iOS share
target; the inbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import db, imports
from ..config import settings
from ..images import detect_image_media_type

router = APIRouter()


def _conn():
    return db.get_conn()


class UrlImport(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    person_id: str | None = None


@router.post("/api/import/url")
def import_url(body: UrlImport) -> dict[str, Any]:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return {"id": imports.create(_conn(), "url", {"url": url}, person_id=body.person_id)}


class TextImport(BaseModel):
    text: str = Field(min_length=10, max_length=200_000)
    html: str | None = Field(default=None, max_length=2_000_000)
    source_url: str | None = None
    person_id: str | None = None


@router.post("/api/import/text")
def import_text(body: TextImport) -> dict[str, Any]:
    payload = {"text": body.text, "html": body.html, "source_url": body.source_url}
    return {"id": imports.create(_conn(), "paste", payload, person_id=body.person_id)}


async def _read_images(files: list[UploadFile]) -> list[tuple[bytes, str]]:
    out = []
    for upload in files:
        data = await upload.read(settings.image_max_bytes + 1)
        if len(data) > settings.image_max_bytes:
            raise HTTPException(
                413, f"{upload.filename} is larger than {settings.image_max_bytes // 1_000_000} MB"
            )
        media_type = detect_image_media_type(data) or (upload.content_type or "")
        if not media_type.startswith("image/"):
            raise HTTPException(415, f"{upload.filename} is not an image")
        out.append((data, media_type))
    return out


@router.post("/api/import/photos")
async def import_photos(
    images: list[UploadFile] = File(...),
    handwritten: bool = Form(False),
    person_id: str | None = Form(None),
) -> dict[str, Any]:
    files = await _read_images(images)
    if not files:
        raise HTTPException(422, "add at least one photo")
    if len(files) > settings.vision_max_pages:
        raise HTTPException(422, f"at most {settings.vision_max_pages} pages per recipe")
    conn = _conn()
    kind = "handwritten" if handwritten else "photos"
    # Create the row first (not queued), store files, then queue.
    inbox_id = imports.create(conn, kind, {"images": []}, person_id=person_id, status="uploading")
    names = [imports.store_upload(inbox_id, i, data, mt) for i, (data, mt) in enumerate(files)]
    conn.execute("UPDATE inbox SET input = ? WHERE id = ?", (db.dumps({"images": names}), inbox_id))
    imports.set_status(conn, inbox_id, "queued")
    from .. import jobs

    jobs.enqueue(conn, "import", {"inbox_id": inbox_id})
    return {"id": inbox_id}


@router.post("/api/import/social")
async def import_social(
    url: str | None = Form(None),
    caption: str | None = Form(None),
    transcribe: bool = Form(False),
    images: list[UploadFile] | None = File(None),
    person_id: str | None = Form(None),
) -> dict[str, Any]:
    if not (url or caption or images):
        raise HTTPException(422, "give a link, the caption text, or screenshots")
    conn = _conn()
    files = await _read_images(images or [])
    inbox_id = imports.create(conn, "social", {}, person_id=person_id, status="uploading")
    names = [imports.store_upload(inbox_id, i, data, mt) for i, (data, mt) in enumerate(files)]
    payload = {"url": url, "caption": caption, "transcribe": transcribe, "images": names}
    conn.execute("UPDATE inbox SET input = ? WHERE id = ?", (db.dumps(payload), inbox_id))
    imports.set_status(conn, inbox_id, "queued")
    from .. import jobs

    jobs.enqueue(conn, "import", {"inbox_id": inbox_id})
    return {"id": inbox_id}


@router.post("/api/import/paprika")
async def import_paprika(
    file: UploadFile = File(...), person_id: str | None = Form(None)
) -> dict[str, Any]:
    """Paprika HTML/ZIP/.paprikarecipes: deterministic, so drafts are ready immediately."""
    from ..extraction import pipeline

    data = await file.read(settings.bulk_import_max_bytes + 1)
    if len(data) > settings.bulk_import_max_bytes:
        raise HTTPException(413, "export is too large")
    try:
        extractions = pipeline.extract_from_paprika(data, file.filename or "export.zip")
    except pipeline.ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    conn = _conn()
    ids = []
    for extraction in extractions:
        draft = imports._draft(extraction)
        inbox_id = imports.create(
            conn,
            "paprika",
            {"filename": file.filename},
            person_id=person_id,
            status="ready",
            draft=draft,
        )
        if extraction.image_data:
            names = [
                imports.store_upload(inbox_id, i, d, mt)
                for i, (d, mt) in enumerate(extraction.image_data[:1])
            ]
            draft["photo_uploads"] = names
            conn.execute("UPDATE inbox SET draft = ? WHERE id = ?", (db.dumps(draft), inbox_id))
        conn.execute(
            "UPDATE inbox SET duplicate_of = ? WHERE id = ?",
            (db.dumps(imports.find_duplicates(conn, draft)), inbox_id),
        )
        ids.append(inbox_id)
    return {"ids": ids, "count": len(ids)}


@router.post("/api/import/epub")
def import_epub(file: UploadFile = File(...), person_id: str | None = Form(None)) -> dict[str, Any]:
    """A cookbook EPUB the household owns: every recipe in it lands in the inbox, ready.

    Deterministic (no model). Re-uploading the same book skips recipes already imported from it.
    """
    import hashlib
    import tempfile

    from ..extraction import epub

    digest = hashlib.sha256()
    size = 0
    books = settings.data_dir / "books"  # kept so a book can be re-read after parser fixes
    books.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=books, suffix=".part", delete=False) as tmp:
        try:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > settings.epub_max_bytes:
                    raise HTTPException(413, "book is too large")
                digest.update(chunk)
                tmp.write(chunk)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
    path = books / f"{digest.hexdigest()}.epub"
    Path(tmp.name).replace(path)
    try:
        extractions, stats = epub.extract_from_epub(path)
    except epub.EpubError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    if not extractions:
        raise HTTPException(422, "no recipes found in this book")
    extractions = extractions[: settings.epub_max_recipes]
    result = imports.file_epub(
        _conn(), extractions, stats, file.filename, digest.hexdigest(), person_id=person_id
    )
    return {**stats, **result}


@router.post("/share", include_in_schema=False)
async def share_target(request: Request) -> RedirectResponse:
    """PWA share_target (and the iOS Shortcut fallback): URL, text and/or images -> inbox."""
    form = await request.form()
    url = (form.get("url") or "").strip() if isinstance(form.get("url"), str) else ""
    text = form.get("text") if isinstance(form.get("text"), str) else ""
    title = form.get("title") if isinstance(form.get("title"), str) else ""
    if not url and text:
        # iOS often puts the link inside the text field
        import re

        match = re.search(r"https?://\S+", text)
        if match:
            url = match.group(0).rstrip(").,")
    uploads = [v for v in form.getlist("images") if hasattr(v, "read")]
    files = await _read_images(uploads) if uploads else []
    conn = _conn()
    social_hosts = ("instagram.com", "tiktok.com", "youtube.com", "youtu.be", "facebook.com")
    kind = "social" if url and any(h in url for h in social_hosts) else "share"
    inbox_id = imports.create(conn, kind, {}, status="uploading")
    names = [imports.store_upload(inbox_id, i, d, mt) for i, (d, mt) in enumerate(files)]
    payload = {
        "url": url or None,
        "text": "\n".join(t for t in (title, text) if t) or None,
        "caption": text or None,
        "images": names,
    }
    conn.execute("UPDATE inbox SET input = ? WHERE id = ?", (db.dumps(payload), inbox_id))
    imports.set_status(conn, inbox_id, "queued")
    from .. import jobs

    jobs.enqueue(conn, "import", {"inbox_id": inbox_id})
    if request.headers.get("accept", "").startswith("application/json"):
        return RedirectResponse(f"/inbox?item={inbox_id}", status_code=303)
    return RedirectResponse(f"/inbox?item={inbox_id}", status_code=303)


# --- inbox -----------------------------------------------------------------------------


@router.get("/api/inbox")
def list_inbox(include_done: bool = False) -> dict[str, Any]:
    where = "" if include_done else "WHERE status NOT IN ('saved', 'dismissed')"
    rows = (
        _conn()
        .execute(f"SELECT * FROM inbox {where} ORDER BY created_at DESC LIMIT 200")
        .fetchall()
    )
    items = []
    for row in rows:
        item = imports.row_dict(row)
        if item["draft"]:
            doc = item["draft"]["document"]
            item["summary"] = {
                "title": doc["title"],
                "ingredients": sum(len(s["ingredients"]) for s in doc["ingredient_sections"]),
                "steps": sum(len(s["steps"]) for s in doc["instruction_sections"]),
            }
        items.append(item)
    recent = [
        {
            "id": r["id"],
            "recipe_id": r["recipe_id"],
            "title": r["title"],
            "slug": r["slug"],
            "updated_at": r["updated_at"],
        }
        for r in _conn().execute(
            "SELECT i.id, i.recipe_id, i.updated_at, r.title, r.slug FROM inbox i "
            "JOIN recipes r ON r.id = i.recipe_id WHERE i.status = 'saved' "
            "ORDER BY i.updated_at DESC LIMIT 10"
        )
    ]
    return {"items": items, "recently_saved": recent}


@router.get("/api/inbox/{inbox_id}")
def get_inbox(inbox_id: str) -> dict[str, Any]:
    row = _conn().execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return imports.row_dict(row)


class DraftEdit(BaseModel):
    document: dict[str, Any]


@router.put("/api/inbox/{inbox_id}/draft")
def edit_draft(inbox_id: str, body: DraftEdit) -> dict[str, Any]:
    from ..document import RecipeDocumentV2

    conn = _conn()
    row = conn.execute("SELECT draft, status FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None or row["draft"] is None:
        raise HTTPException(404, "no draft")
    draft = db.loads(row["draft"])
    draft["document"] = RecipeDocumentV2.model_validate(body.document).model_dump(mode="json")
    draft["edited"] = True
    conn.execute(
        "UPDATE inbox SET draft = ?, updated_at = ? WHERE id = ?",
        (db.dumps(draft), db.now(), inbox_id),
    )
    return {"ok": True}


class SaveBody(BaseModel):
    merge_into: str | None = None
    person_id: str | None = None


@router.post("/api/inbox/{inbox_id}/save")
def save_inbox(inbox_id: str, body: SaveBody) -> dict[str, Any]:
    return imports.save(_conn(), inbox_id, merge_into=body.merge_into, person_id=body.person_id)


@router.post("/api/inbox/{inbox_id}/retry")
def retry_inbox(inbox_id: str) -> dict[str, Any]:
    conn = _conn()
    imports.set_status(conn, inbox_id, "queued", error=None)
    from .. import jobs

    jobs.enqueue(conn, "import", {"inbox_id": inbox_id})
    return {"ok": True}


@router.post("/api/inbox/{inbox_id}/dismiss")
def dismiss_inbox(inbox_id: str) -> dict[str, Any]:
    imports.set_status(_conn(), inbox_id, "dismissed")
    return {"ok": True}


@router.get("/api/inbox-uploads/{inbox_id}/{name}")
def upload_file(inbox_id: str, name: str):
    from fastapi.responses import FileResponse

    root = settings.uploads_dir.resolve()
    target = (root / inbox_id / name).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)
