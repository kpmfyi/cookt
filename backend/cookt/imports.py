"""Import inbox: every import lands here, is extracted by a queued job, previewed,
optionally edited, then saved (or merged into an existing recipe).

Inbox status flow: queued -> fetching -> extracting -> ready | failed; then saved | dismissed.
After save, enrichment (tags, features, embedding, nutrition) runs as a queued job.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import db, jobs, recipes
from .config import settings
from .document import RecipeDocumentV2

log = logging.getLogger("cookt.imports")

KINDS = (
    "url",
    "paste",
    "photos",
    "handwritten",
    "social",
    "paprika",
    "epub",
    "share",
    "rescrape",
    "proposal",
)


# --- inbox rows ------------------------------------------------------------------------


def create(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    person_id: str | None = None,
    status: str = "queued",
    draft: dict | None = None,
) -> str:
    inbox_id = db.new_id()
    stamp = db.now()
    conn.execute(
        "INSERT INTO inbox(id, kind, status, input, draft, person_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            inbox_id,
            kind,
            status,
            db.dumps(payload),
            db.dumps(draft) if draft else None,
            person_id,
            stamp,
            stamp,
        ),
    )
    if status == "queued":
        jobs.enqueue(conn, "import", {"inbox_id": inbox_id})
    return inbox_id


def set_status(conn: sqlite3.Connection, inbox_id: str, status: str, **fields: Any) -> None:
    sets = {"status": status, "updated_at": db.now(), **fields}
    assignments = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE inbox SET {assignments} WHERE id = ?", (*sets.values(), inbox_id))


def uploads_dir(inbox_id: str) -> Path:
    path = settings.uploads_dir / inbox_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_upload(inbox_id: str, index: int, data: bytes, media_type: str) -> str:
    ext = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/avif": "avif",
        "image/gif": "gif",
    }.get(media_type, "bin")
    name = f"{index:02d}.{ext}"
    (uploads_dir(inbox_id) / name).write_bytes(data)
    return name


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "input": db.loads(row["input"]),
        "draft": db.loads(row["draft"]),
        "error": row["error"],
        "duplicates": db.loads(row["duplicate_of"]) or [],
        "recipe_id": row["recipe_id"],
        "person_id": row["person_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# --- extraction job --------------------------------------------------------------------


def _draft(extraction: Any) -> dict[str, Any]:
    return {
        "document": extraction.document.model_dump(mode="json"),
        "method": extraction.method,
        "source_url": extraction.source_url,
        "canonical_url": extraction.canonical_url,
        "source_site": extraction.source_site,
        "source_author": extraction.source_author,
        "image_urls": extraction.image_urls[:6],
        "nutrition": extraction.nutrition,
        "prep_minutes": extraction.prep_minutes,
        "cook_minutes": extraction.cook_minutes,
        "total_minutes": extraction.total_minutes,
        "yield_text": extraction.yield_text,
        "hints": extraction.hints,
        "coverage": extraction.coverage,
        "uncertain_lines": extraction.uncertain_lines,
        "transcript": extraction.transcript,
        "warnings": extraction.warnings,
    }


def run_import(conn: sqlite3.Connection, inbox_id: str) -> None:
    from .extraction import pipeline

    row = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None or row["status"] in ("saved", "dismissed"):
        return
    payload = db.loads(row["input"])
    kind = row["kind"]
    try:
        if kind in ("url", "rescrape") or (
            kind == "share" and payload.get("url") and not payload.get("images")
        ):
            set_status(conn, inbox_id, "fetching", error=None)
            url = payload["url"]
            try:
                extraction = pipeline.extract_from_url(url)
            except pipeline.ExtractionError:
                if kind == "share" and payload.get("text"):
                    set_status(conn, inbox_id, "extracting")
                    extraction = pipeline.extract_from_text(payload["text"], source_url=url)
                else:
                    raise
        elif kind == "paste" or (
            kind == "share" and payload.get("text") and not payload.get("images")
        ):
            set_status(conn, inbox_id, "extracting", error=None)
            extraction = pipeline.extract_from_text(
                payload.get("text") or "",
                html=payload.get("html"),
                source_url=payload.get("source_url"),
            )
        elif kind in ("photos", "handwritten") or (kind == "share" and payload.get("images")):
            set_status(conn, inbox_id, "extracting", error=None)
            folder = uploads_dir(inbox_id)
            images = []
            for name in payload.get("images", []):
                data = (folder / name).read_bytes()
                from .images import detect_image_media_type

                images.append((data, detect_image_media_type(data) or "image/jpeg"))
            extraction = pipeline.extract_from_images(images, handwritten=kind == "handwritten")
        elif kind == "social":
            set_status(conn, inbox_id, "fetching", error=None)
            text_parts = []
            if payload.get("caption"):
                text_parts.append(payload["caption"])
            meta: dict[str, Any] = {}
            if payload.get("url"):
                try:
                    meta = pipeline.fetch_social_caption(payload["url"])
                    text_parts.append(meta.get("caption") or meta.get("description") or "")
                except pipeline.ExtractionError as exc:
                    log.info("social caption unavailable: %s", exc)
            if payload.get("transcribe") and payload.get("url"):
                set_status(conn, inbox_id, "extracting")
                text_parts.append(pipeline.transcribe_audio_url(payload["url"]))
            set_status(conn, inbox_id, "extracting")
            text = "\n\n".join(t for t in text_parts if t and t.strip())
            if payload.get("images"):
                folder = uploads_dir(inbox_id)
                images = [((folder / n).read_bytes(), "image/jpeg") for n in payload["images"]]
                extraction = pipeline.extract_from_images(images)
            else:
                if not text.strip():
                    raise pipeline.ExtractionError(
                        "No caption or description text to read", "empty"
                    )
                extraction = pipeline.extract_from_text(text, source_url=payload.get("url"))
            if meta.get("title") and not extraction.source_site:
                extraction.source_site = meta.get("uploader") or meta.get("extractor")
        else:
            raise ValueError(f"cannot extract inbox kind {kind}")
    except Exception as exc:  # noqa: BLE001 - shown in the inbox with a retry button
        set_status(conn, inbox_id, "failed", error=str(exc)[:1000])
        return
    draft = _draft(extraction)
    if kind in ("photos", "handwritten", "share", "social") and payload.get("images"):
        draft["upload_images"] = payload["images"]
    duplicates = find_duplicates(conn, draft)
    set_status(
        conn,
        inbox_id,
        "ready",
        draft=db.dumps(draft),
        duplicate_of=db.dumps(duplicates),
        error=None,
    )


# --- dedupe ----------------------------------------------------------------------------

_WORDS = re.compile(r"[a-z]+")


def _norm_title(title: str) -> str:
    return " ".join(_WORDS.findall(title.lower()))


def _ingredient_set(document: dict[str, Any]) -> set[str]:
    from .planning import ingredient_key

    return {
        ingredient_key(i)
        for s in document.get("ingredient_sections", [])
        for i in s.get("ingredients", [])
    } - {""}


def find_duplicates(conn: sqlite3.Connection, draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Canonical URL, then title similarity, then ingredient-set similarity."""
    out: dict[str, dict[str, Any]] = {}
    canonical = draft.get("canonical_url")
    if canonical:
        for row in conn.execute(
            "SELECT id, title FROM recipes WHERE canonical_url = ? AND archived_at IS NULL",
            (canonical,),
        ):
            out[row["id"]] = {
                "recipe_id": row["id"],
                "title": row["title"],
                "reason": "same URL",
                "score": 1.0,
            }
    title = _norm_title(draft["document"]["title"])
    ingredients = _ingredient_set(draft["document"])
    for row in conn.execute("SELECT id, title, document FROM recipes WHERE archived_at IS NULL"):
        if row["id"] in out:
            continue
        ratio = SequenceMatcher(None, title, _norm_title(row["title"])).ratio()
        if ratio < 0.6:
            continue
        other = _ingredient_set(db.loads(row["document"]))
        jaccard = len(ingredients & other) / max(1, len(ingredients | other))
        if ratio >= 0.9 or (ratio >= 0.6 and jaccard >= 0.6):
            reason = (
                "same title"
                if ratio >= 0.9
                else f"similar title, {round(jaccard * 100)}% same ingredients"
            )
            out[row["id"]] = {
                "recipe_id": row["id"],
                "title": row["title"],
                "reason": reason,
                "score": round(max(ratio, jaccard), 3),
            }
    return sorted(out.values(), key=lambda d: -d["score"])[:5]


def file_epub(
    conn: sqlite3.Connection,
    extractions: list[Any],
    stats: dict[str, Any],
    filename: str | None,
    sha256: str,
    *,
    person_id: str | None = None,
) -> dict[str, Any]:
    """Put a cookbook's recipes in the inbox (ready), skipping ones already imported from it."""
    book = stats["book"] or filename
    seen = {
        (db.loads(r["input"]).get("book"), db.loads(r["draft"])["document"]["title"])
        for r in conn.execute(
            "SELECT input, draft FROM inbox WHERE kind = 'epub' AND status != 'dismissed' "
            "AND draft IS NOT NULL"
        )
    }
    seen |= {
        (r["source_site"], r["title"])
        for r in conn.execute(
            "SELECT source_site, title FROM recipes WHERE source_site = ? AND archived_at IS NULL",
            (book,),
        )
    }
    ids = []
    already = 0
    for extraction in extractions:
        if (book, extraction.document.title) in seen:
            already += 1
            continue
        seen.add((book, extraction.document.title))
        draft = _draft(extraction)
        draft["source_site"] = book
        inbox_id = create(
            conn,
            "epub",
            {"filename": filename, "book": book, "sha256": sha256},
            person_id=person_id,
            status="ready",
            draft=draft,
        )
        if extraction.image_data:
            data, media_type = extraction.image_data[0]
            draft["photo_uploads"] = [store_upload(inbox_id, 0, data, media_type)]
        conn.execute(
            "UPDATE inbox SET draft = ?, duplicate_of = ? WHERE id = ?",
            (db.dumps(draft), db.dumps(find_duplicates(conn, draft)), inbox_id),
        )
        ids.append(inbox_id)
    return {"ids": ids, "count": len(ids), "already_imported": already}


# --- save ------------------------------------------------------------------------------


def _save_image(
    conn: sqlite3.Connection,
    recipe_id: str,
    data: bytes,
    source_url: str | None,
    role: str = "primary",
) -> str | None:
    from .images import detect_image_media_type
    from .migrate.images import derive

    media_type = detect_image_media_type(data)
    if media_type is None:
        return None
    ext = media_type.split("/")[1].replace("jpeg", "jpg")
    digest = hashlib.sha256(data).hexdigest()
    image_id = db.new_id()
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    try:
        derived = derive(temp, settings.images_dir, f"{recipe_id}/{image_id[:8]}", "", ext)
    finally:
        temp.unlink(missing_ok=True)
    conn.execute(
        "INSERT INTO images(id, recipe_id, role, position, path, thumb_path, media_type, width, "
        "height, sha256, source_url, created_at) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            image_id,
            recipe_id,
            role,
            derived.path,
            derived.thumb_path,
            media_type,
            derived.width,
            derived.height,
            digest,
            source_url,
            db.now(),
        ),
    )
    return image_id


def _fetch_primary_image(draft: dict[str, Any]) -> tuple[bytes, str] | None:
    from .images import ImageCandidate, download_image_candidates

    urls = draft.get("image_urls") or []
    if not urls:
        return None
    try:
        payloads, _failures = download_image_candidates(
            [ImageCandidate(url=u, role="primary") for u in urls[:3]], max_images=3
        )
    except Exception as exc:  # noqa: BLE001
        log.info("image download failed: %s", exc)
        return None
    if not payloads:
        return None
    from .images import image_quality_key

    best = max(payloads, key=lambda p: image_quality_key(p.data))
    return best.data, best.source_url


def save(
    conn: sqlite3.Connection,
    inbox_id: str,
    *,
    merge_into: str | None = None,
    person_id: str | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        raise recipes.NotFound(inbox_id)
    if row["status"] == "saved" and row["recipe_id"]:
        return {"recipe_id": row["recipe_id"], "already": True}
    if row["status"] != "ready":
        raise ValueError(f"inbox item is {row['status']}, not ready")
    draft = db.loads(row["draft"])
    document = RecipeDocumentV2.model_validate(draft["document"])
    image = None
    if draft.get("photo_uploads"):  # e.g. a Paprika export's embedded photo
        image = ((uploads_dir(inbox_id) / draft["photo_uploads"][0]).read_bytes(), None)
    elif not draft.get("upload_images"):  # cookbook-page photos are text, not a dish photo
        image = _fetch_primary_image(draft)
    if merge_into:
        recipe_id = recipes.resolve_id(conn, merge_into)
        recipes.update_recipe(
            conn,
            recipe_id,
            {
                "document": document.model_dump(mode="json"),
                "source_url": draft.get("source_url"),
                "source_site": draft.get("source_site"),
                "source_author": draft.get("source_author"),
            },
            reason=f"merged import ({row['kind']})",
            person_id=person_id,
        )
    else:
        recipe_id = db.new_id()
        stamp = db.now()
        with db.tx(conn):
            conn.execute(
                "INSERT INTO recipes(id, slug, title, document, source_url, canonical_url, "
                "source_site, source_author, description, prep_minutes, cook_minutes, "
                "total_minutes, origin, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recipe_id,
                    recipes.unique_slug(conn, document.title),
                    document.title,
                    db.dumps(document.model_dump(mode="json")),
                    draft.get("source_url"),
                    draft.get("canonical_url"),
                    draft.get("source_site"),
                    draft.get("source_author"),
                    (draft.get("hints") or {}).get("description"),
                    draft.get("prep_minutes"),
                    draft.get("cook_minutes"),
                    draft.get("total_minutes"),
                    row["kind"],
                    stamp,
                    stamp,
                ),
            )
    if draft.get("nutrition"):
        conn.execute(
            "INSERT OR REPLACE INTO source_nutrition(recipe_id, data, created_at) VALUES (?, ?, ?)",
            (recipe_id, db.dumps(draft["nutrition"]), db.now()),
        )
    image_id = None
    if image:
        image_id = _save_image(conn, recipe_id, image[0], image[1])
    if image_id and (
        merge_into is None
        or conn.execute("SELECT image_id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[
            "image_id"
        ]
        is None
    ):
        conn.execute("UPDATE recipes SET image_id = ? WHERE id = ?", (image_id, recipe_id))
    if row["kind"] == "rescrape":
        # Keep the old ids so old links and assistant references resolve to the re-scraped recipe.
        for system, alias in (db.loads(row["input"]).get("old_ids") or {}).items():
            if alias:
                conn.execute(
                    "INSERT OR IGNORE INTO recipe_aliases(alias, recipe_id, system) "
                    "VALUES (?, ?, ?)",
                    (alias, recipe_id, system),
                )
    set_status(conn, inbox_id, "saved", recipe_id=recipe_id)
    jobs.enqueue(conn, "enrich", {"recipe_id": recipe_id})
    if draft.get("upload_images"):
        keep = settings.data_dir / "imports" / inbox_id
        keep.parent.mkdir(parents=True, exist_ok=True)
        if not keep.exists():
            shutil.copytree(uploads_dir(inbox_id), keep)
    return {
        "recipe_id": recipe_id,
        "slug": conn.execute("SELECT slug FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[
            "slug"
        ],
    }


def recover_queued(conn: sqlite3.Connection) -> int:
    """Queue extraction for inbox rows left 'queued' without a job (e.g. created by migration)."""
    pending = {
        db.loads(r["payload"]).get("inbox_id")
        for r in conn.execute(
            "SELECT payload FROM jobs WHERE type = 'import' AND status IN ('queued', 'running')"
        )
    }
    count = 0
    for row in conn.execute(
        "SELECT id FROM inbox WHERE status IN ('queued', 'fetching', 'extracting')"
    ):
        if row["id"] not in pending:
            jobs.enqueue(conn, "import", {"inbox_id": row["id"]})
            count += 1
    return count
