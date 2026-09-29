"""Recipe read/write paths shared by the HTTP API and the (read-only) MCP server."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from . import db
from .document import RecipeDocumentV2

TAG_KINDS = ("cuisine", "course", "protein", "diet", "equipment", "personal")
COURSES = (
    "main",
    "side",
    "soup",
    "salad",
    "sauce/condiment",
    "bread",
    "dessert",
    "breakfast",
    "snack",
    "drink",
)
PROTEINS = (
    "chicken",
    "pork",
    "beef",
    "lamb",
    "fish",
    "shellfish",
    "tofu/tempeh",
    "beans/legumes",
    "eggs",
    "none",
)
DIETS = ("vegetarian", "vegan", "gluten-free", "dairy-free")
EDITABLE_FIELDS = (
    "title",
    "document",
    "source_url",
    "source_site",
    "source_author",
    "credit",
    "description",
    "prep_minutes",
    "cook_minutes",
    "total_minutes",
    "servings",
)


class NotFound(LookupError):
    pass


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80] or "recipe"


def unique_slug(conn: sqlite3.Connection, title: str, exclude_id: str | None = None) -> str:
    base = slugify(title)
    slug, n = base, 2
    while True:
        row = conn.execute("SELECT id FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None or row["id"] == exclude_id:
            return slug
        slug = f"{base}-{n}"
        n += 1


def resolve_id(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT id FROM recipes WHERE (id = ? OR slug = ?) AND archived_at IS NULL", (key, key)
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT recipe_id FROM recipe_aliases WHERE alias = ?", (key,)).fetchone()
    if row:
        return row["recipe_id"]
    raise NotFound(key)


def _image_urls(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "src": f"/images/{row['path']}",
        "thumb": f"/images/{row['thumb_path']}" if row["thumb_path"] else f"/images/{row['path']}",
        "width": row["width"],
        "height": row["height"],
    }


def _ingredient_lines(document: dict[str, Any]) -> list[str]:
    return [
        item.get("name") or item.get("source_text") or ""
        for section in document.get("ingredient_sections", [])
        for item in section.get("ingredients", [])
    ]


def _tags_by_recipe(conn: sqlite3.Connection) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for row in conn.execute("SELECT recipe_id, kind, value FROM recipe_tags ORDER BY kind, value"):
        out.setdefault(row["recipe_id"], {}).setdefault(row["kind"], []).append(row["value"])
    return out


def catalog(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything the client needs to browse, search, read and cook offline."""
    tags = _tags_by_recipe(conn)
    images = {row["id"]: row for row in conn.execute("SELECT * FROM images WHERE role = 'primary'")}
    favorites: dict[str, list[str]] = {}
    for row in conn.execute("SELECT recipe_id, person_id FROM favorites"):
        favorites.setdefault(row["recipe_id"], []).append(row["person_id"])
    cooks: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT recipe_id, COUNT(*) AS n, MAX(cooked_on) AS last FROM cook_log GROUP BY recipe_id"
    ):
        cooks[row["recipe_id"]] = {"count": row["n"], "last": row["last"]}
    notes: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT id, recipe_id, person_id, text, created_at FROM next_time_notes "
        "WHERE resolved_at IS NULL ORDER BY created_at"
    ):
        notes.setdefault(row["recipe_id"], []).append(
            {
                "id": row["id"],
                "person_id": row["person_id"],
                "text": row["text"],
                "created_at": row["created_at"],
            }
        )
    nutrition = {
        row["recipe_id"]: {
            "source": row["source"],
            "confidence": row["confidence"],
            **db.loads(row["per_serving"]),
        }
        for row in conn.execute("SELECT recipe_id, source, confidence, per_serving FROM nutrition")
    }
    step_images: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute("SELECT * FROM images WHERE role = 'step' ORDER BY position"):
        step_images.setdefault(row["recipe_id"], []).append(
            {**(_image_urls(row) or {}), "step_id": row["step_id"]}
        )
    recipes = []
    for row in conn.execute(
        "SELECT * FROM recipes WHERE archived_at IS NULL ORDER BY created_at DESC, title"
    ):
        document = db.loads(row["document"])
        recipes.append(
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "credit": row["credit"],
                "description": row["description"],
                "source_url": row["source_url"],
                "source_site": row["source_site"],
                "source_author": row["source_author"],
                "prep_minutes": row["prep_minutes"],
                "cook_minutes": row["cook_minutes"],
                "total_minutes": row["total_minutes"],
                "servings": row["servings"],
                "image": _image_urls(images.get(row["image_id"])),
                "step_images": step_images.get(row["id"], []),
                "tags": tags.get(row["id"], {}),
                "favorites": favorites.get(row["id"], []),
                "cooked": cooks.get(row["id"]),
                "next_time": notes.get(row["id"], []),
                "nutrition": nutrition.get(row["id"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "version": row["version"],
                "document": document,
            }
        )
    people = [dict(r) for r in conn.execute("SELECT id, name FROM people ORDER BY name")]
    version = conn.execute(
        "SELECT COALESCE(MAX(updated_at), '') || ':' || COUNT(*) FROM recipes"
    ).fetchone()[0]
    return {"version": version, "recipes": recipes, "people": people}


def detail(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    recipe_id = resolve_id(conn, key)
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    image = conn.execute("SELECT * FROM images WHERE id = ?", (row["image_id"],)).fetchone()
    cook_log = [
        dict(r)
        for r in conn.execute(
            "SELECT c.*, p.name AS person FROM cook_log c LEFT JOIN people p ON p.id = c.person_id "
            "WHERE c.recipe_id = ? ORDER BY c.cooked_on DESC, c.created_at DESC",
            (recipe_id,),
        )
    ]
    next_time = [
        dict(r)
        for r in conn.execute(
            "SELECT n.id, n.text, n.created_at, n.person_id, p.name AS person "
            "FROM next_time_notes n LEFT JOIN people p ON p.id = n.person_id "
            "WHERE n.recipe_id = ? AND n.resolved_at IS NULL ORDER BY n.created_at",
            (recipe_id,),
        )
    ]
    revisions = [
        dict(r)
        for r in conn.execute(
            "SELECT id, version, reason, created_at FROM revisions WHERE recipe_id = ? "
            "ORDER BY version DESC, created_at DESC",
            (recipe_id,),
        )
    ]
    tags = [
        dict(r)
        for r in conn.execute(
            "SELECT kind, value, source, confidence, evidence FROM recipe_tags WHERE recipe_id = ? "
            "ORDER BY kind, value",
            (recipe_id,),
        )
    ]
    nutrition_row = conn.execute(
        "SELECT * FROM nutrition WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()
    nutrition = None
    if nutrition_row:
        nutrition = {
            "source": nutrition_row["source"],
            "confidence": nutrition_row["confidence"],
            "per_serving": db.loads(nutrition_row["per_serving"]),
            "breakdown": db.loads(nutrition_row["breakdown"]),
            "servings": nutrition_row["servings"],
            "computed_at": nutrition_row["computed_at"],
        }
    aliases = [
        dict(r)
        for r in conn.execute(
            "SELECT alias, system FROM recipe_aliases WHERE recipe_id = ?", (recipe_id,)
        )
    ]
    favorites = [
        r["person_id"]
        for r in conn.execute("SELECT person_id FROM favorites WHERE recipe_id = ?", (recipe_id,))
    ]
    return {
        **{k: row[k] for k in row.keys() if k != "document"},
        "document": db.loads(row["document"]),
        "image": _image_urls(image),
        "tags": tags,
        "favorites": favorites,
        "cook_log": cook_log,
        "next_time": next_time,
        "revisions": revisions,
        "nutrition": nutrition,
        "aliases": aliases,
    }


def _snapshot(row: sqlite3.Row, tags: list[sqlite3.Row]) -> dict[str, Any]:
    snap = {field: row[field] for field in EDITABLE_FIELDS}
    snap["document"] = db.loads(row["document"])
    snap["personal_tags"] = [t["value"] for t in tags if t["kind"] == "personal"]
    return snap


def update_recipe(
    conn: sqlite3.Connection,
    recipe_id: str,
    changes: dict[str, Any],
    *,
    reason: str = "edit",
    person_id: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Full edit. Snapshots the previous state into `revisions` first."""
    with db.tx(conn):
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is None:
            raise NotFound(recipe_id)
        if expected_version is not None and row["version"] != expected_version:
            raise ValueError("recipe was changed elsewhere; reload and try again")
        tag_rows = conn.execute(
            "SELECT kind, value FROM recipe_tags WHERE recipe_id = ?", (recipe_id,)
        ).fetchall()
        conn.execute(
            "INSERT INTO revisions(id, recipe_id, version, snapshot, reason, person_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                recipe_id,
                row["version"],
                db.dumps(_snapshot(row, tag_rows)),
                reason,
                person_id,
                db.now(),
            ),
        )
        sets: dict[str, Any] = {}
        for field in EDITABLE_FIELDS:
            if field not in changes:
                continue
            value = changes[field]
            if field == "document":
                document = RecipeDocumentV2.model_validate(value)
                value = db.dumps(document.model_dump(mode="json"))
                sets["title"] = document.title
            sets[field] = value
        if "title" in sets and sets["title"] != row["title"]:
            sets["slug"] = unique_slug(conn, sets["title"], exclude_id=recipe_id)
            conn.execute(
                "INSERT OR IGNORE INTO recipe_aliases(alias, recipe_id, system) VALUES (?, ?, 'slug')",
                (row["slug"], recipe_id),
            )
        sets["version"] = row["version"] + 1
        sets["updated_at"] = db.now()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE recipes SET {assignments} WHERE id = ?", (*sets.values(), recipe_id))
        if "personal_tags" in changes:
            conn.execute(
                "DELETE FROM recipe_tags WHERE recipe_id = ? AND kind = 'personal'", (recipe_id,)
            )
            for value in dict.fromkeys(t.strip() for t in changes["personal_tags"] if t.strip()):
                conn.execute(
                    "INSERT OR IGNORE INTO recipe_tags(recipe_id, kind, value, source, created_at) "
                    "VALUES (?, 'personal', ?, 'manual', ?)",
                    (recipe_id, value, db.now()),
                )
        for kind in ("cuisine", "course", "protein", "diet", "equipment"):
            key = f"{kind}_tags"
            if key not in changes:
                continue
            wanted = set(changes[key])
            current = {
                r["value"]
                for r in conn.execute(
                    "SELECT value FROM recipe_tags WHERE recipe_id = ? AND kind = ?",
                    (recipe_id, kind),
                )
            }
            for value in current - wanted:
                conn.execute(
                    "DELETE FROM recipe_tags WHERE recipe_id = ? AND kind = ? AND value = ?",
                    (recipe_id, kind, value),
                )
                # A manual removal is also a "never re-suggest".
                conn.execute(
                    "INSERT OR IGNORE INTO rejected_tags(recipe_id, kind, value, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (recipe_id, kind, value, db.now()),
                )
            for value in wanted - current:
                conn.execute(
                    "INSERT OR IGNORE INTO recipe_tags(recipe_id, kind, value, source, created_at) "
                    "VALUES (?, ?, ?, 'manual', ?)",
                    (recipe_id, kind, value, db.now()),
                )
                conn.execute(
                    "DELETE FROM rejected_tags WHERE recipe_id = ? AND kind = ? AND value = ?",
                    (recipe_id, kind, value),
                )
    return detail(conn, recipe_id)


def restore_revision(
    conn: sqlite3.Connection, recipe_id: str, revision_id: str, person_id: str | None = None
) -> dict[str, Any]:
    rev = conn.execute(
        "SELECT * FROM revisions WHERE id = ? AND recipe_id = ?", (revision_id, recipe_id)
    ).fetchone()
    if rev is None:
        raise NotFound(revision_id)
    snap = db.loads(rev["snapshot"])
    changes = {k: v for k, v in snap.items() if k in EDITABLE_FIELDS or k == "personal_tags"}
    return update_recipe(
        conn, recipe_id, changes, reason=f"restore v{rev['version']}", person_id=person_id
    )


def set_favorite(conn: sqlite3.Connection, recipe_id: str, person_id: str, on: bool) -> None:
    if on:
        conn.execute(
            "INSERT OR IGNORE INTO favorites(recipe_id, person_id, created_at) VALUES (?, ?, ?)",
            (recipe_id, person_id or "", db.now()),
        )
    else:
        conn.execute(
            "DELETE FROM favorites WHERE recipe_id = ? AND person_id = ?",
            (recipe_id, person_id or ""),
        )


def log_cook(
    conn: sqlite3.Connection,
    recipe_id: str,
    *,
    person_id: str | None,
    cooked_on: str,
    make_again: bool | None,
    note: str | None,
    next_time: str | None = None,
) -> dict[str, Any]:
    entry_id = db.new_id()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO cook_log(id, recipe_id, person_id, cooked_on, make_again, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                recipe_id,
                person_id,
                cooked_on,
                None if make_again is None else int(make_again),
                note or None,
                db.now(),
            ),
        )
        if next_time and next_time.strip():
            add_note(conn, recipe_id, next_time.strip(), person_id)
    return {"id": entry_id}


def add_note(conn: sqlite3.Connection, recipe_id: str, text: str, person_id: str | None) -> str:
    note_id = db.new_id()
    conn.execute(
        "INSERT INTO next_time_notes(id, recipe_id, person_id, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (note_id, recipe_id, person_id, text, db.now()),
    )
    return note_id


def resolve_note(conn: sqlite3.Connection, note_id: str) -> None:
    conn.execute("UPDATE next_time_notes SET resolved_at = ? WHERE id = ?", (db.now(), note_id))


def summary(row: sqlite3.Row | dict[str, Any], tags: dict[str, list[str]]) -> dict[str, Any]:
    """cookt2-compatible recipe summary (MCP search_recipes shape)."""
    all_tags = [v for kind in TAG_KINDS for v in tags.get(kind, [])]
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "total_time": row["total_minutes"],
        "servings": row["servings"],
        "difficulty": None,
        "is_favorite": bool(row.get("favorites")) if isinstance(row, dict) else None,
        "tags": all_tags,
    }


def ingredient_lines(document: dict[str, Any]) -> list[str]:
    return _ingredient_lines(document)
