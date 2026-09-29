"""Step 2: read the source snapshots (read-only) into one intermediate JSON per recipe.

A = recipe-table review DB restored into the scratch Postgres (SELECT only, read-only session).
B = cookt2 SQLite snapshot opened with `mode=ro` + its image snapshot directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..document import load_document
from . import normalize as N

A_DSN_DEFAULT = "host=127.0.0.1 port=15499 user=scratch password=scratch dbname=src"
A_OUT_OF_SCOPE = (
    "cookbook_discovery_entries",
    "cookbook_preference_profiles",
    "discovery_candidates",
    "discovery_sources",
    "recipe_refinements",
    "recipe_image_rerolls",
    "recipe_profiles",
    "suggestion_runs",
    "suggestion_items",
    "suggestion_feedback",
    "scan_attempts",
    "recipe_labels",
    "invite_tokens",
    "password_sessions",
    "household_memberships",
    "users",
    "households",
)
B_OUT_OF_SCOPE = (
    "generation_log",
    "eval_runs",
    "eval_results",
    "llm_models",
    "profiles",
    "profile_preferences",
    "kitchen_equipment",
    "embedding_meta",
    "meal_events",
    "household",
)
MEDIA_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/gif": "gif",
}
AI_CAPTION = "Generated locally"


def a_dsn() -> str:
    return os.getenv("COOKT_MIGRATE_A_DSN", A_DSN_DEFAULT)


def iso(value: Any) -> str | None:
    """Any source timestamp -> ISO-8601 UTC with seconds precision."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True, default=str))


def png_generator_metadata(path: Path) -> str | None:
    """Return a reason string when the file carries Stable Diffusion / ComfyUI metadata."""

    try:
        with Image.open(path) as image:
            info = dict(image.info)
    except Exception:  # noqa: BLE001
        return None
    if "parameters" in info:
        return "PNG 'parameters' text chunk (Stable Diffusion prompt)"
    if "prompt" in info or "workflow" in info:
        return "PNG 'prompt'/'workflow' text chunk (ComfyUI graph)"
    return None


# --------------------------------------------------------------------------------------------
# A
# --------------------------------------------------------------------------------------------


def _pg_connect(dsn: str):
    import psycopg  # dev dependency; imported lazily so the app never needs it

    conn = psycopg.connect(dsn, autocommit=False)
    conn.read_only = True
    return conn


def pg_rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        names = [col.name for col in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def extract_a(dsn: str, work: Path) -> dict[str, Any]:
    """Write intermediate/A/<id>.json + images; return {records, meta}."""

    out_dir = work / "intermediate" / "A"
    img_dir = work / "intermediate" / "images" / "A"
    conn = _pg_connect(dsn)
    try:
        households = {
            str(r["id"]): r["name"] for r in pg_rows(conn, "SELECT id, name FROM households")
        }
        recipes = pg_rows(
            conn,
            """SELECT id::text AS id, household_id::text AS household_id, canonical_url,
                      source_site, source_author, scraped_at, state, document, search_text,
                      edit_version, archived_at, created_at, updated_at, source_metadata
               FROM recipe_artifacts ORDER BY id""",
        )
        images = pg_rows(
            conn,
            """SELECT id::text AS id, recipe_id::text AS recipe_id, scan_id::text AS scan_id,
                      role, position, instruction_step_id, caption, source_url, media_type,
                      content_hash, byte_size, data, created_at
               FROM recipe_images ORDER BY recipe_id NULLS LAST, role, position, id""",
        )
        favorites = pg_rows(
            conn,
            "SELECT id::text AS id, user_id::text AS user_id, recipe_id::text AS recipe_id, "
            "created_at FROM favorites ORDER BY created_at, id",
        )
        classifications = pg_rows(
            conn,
            "SELECT recipe_id::text AS recipe_id, user_id::text AS user_id, tags, facets, "
            "desire, note, updated_at FROM recipe_classifications ORDER BY recipe_id, user_id",
        )
        cook_events = pg_rows(
            conn,
            "SELECT id::text AS id, recipe_id::text AS recipe_id, cooked_at, note, make_again, "
            "created_at FROM recipe_cook_events ORDER BY cooked_at, id",
        )
        revisions = pg_rows(
            conn,
            "SELECT id::text AS id, recipe_id::text AS recipe_id, recipe_version, reason, "
            "document, source_site, source_author, scraped_at, created_at "
            "FROM recipe_revisions ORDER BY recipe_id, recipe_version, created_at, id",
        )
        out_of_scope = {
            table: pg_rows(conn, f"SELECT count(*) AS n FROM {table}")[0]["n"]  # noqa: S608
            for table in A_OUT_OF_SCOPE
        }
    finally:
        conn.rollback()
        conn.close()

    by_recipe: dict[str, dict[str, list]] = {
        r["id"]: {"images": [], "favorites": [], "classifications": [], "cook": [], "rev": []}
        for r in recipes
    }
    orphans: list[dict[str, Any]] = []
    for image in images:
        data: bytes = bytes(image.pop("data"))
        digest = sha256_bytes(data)
        ext = MEDIA_EXT.get(image["media_type"], "bin")
        owner = image["recipe_id"] or "_orphan"
        path = img_dir / owner / f"{image['id']}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ai = (image["caption"] or "").strip() == AI_CAPTION
        entry = {
            "id": image["id"],
            "role": image["role"],
            "position": image["position"],
            "step_id": image["instruction_step_id"],
            "caption": image["caption"],
            "source_url": image["source_url"],
            "media_type": image["media_type"],
            "sha256": digest,
            "content_hash": image["content_hash"],
            "byte_size": len(data),
            "file": str(path.relative_to(work)),
            "created_at": iso(image["created_at"]),
            "ai_generated": ai,
            "ai_reason": f"caption '{AI_CAPTION}' (recipe-table local painting)" if ai else None,
        }
        if image["recipe_id"] in by_recipe:
            by_recipe[image["recipe_id"]]["images"].append(entry)
        else:
            entry["scan_id"] = image["scan_id"]
            orphans.append(entry)
    for fav in favorites:
        if fav["recipe_id"] in by_recipe:
            by_recipe[fav["recipe_id"]]["favorites"].append(
                {"id": fav["id"], "user_id": fav["user_id"], "created_at": iso(fav["created_at"])}
            )
    for cls in classifications:
        if cls["recipe_id"] in by_recipe:
            by_recipe[cls["recipe_id"]]["classifications"].append(
                {
                    "user_id": cls["user_id"],
                    "tags": cls["tags"],
                    "facets": cls["facets"],
                    "desire": cls["desire"],
                    "note": cls["note"],
                    "updated_at": iso(cls["updated_at"]),
                }
            )
    for event in cook_events:
        if event["recipe_id"] in by_recipe:
            by_recipe[event["recipe_id"]]["cook"].append(
                {
                    "id": event["id"],
                    "cooked_at": iso(event["cooked_at"]),
                    "note": event["note"],
                    "make_again": event["make_again"],
                    "created_at": iso(event["created_at"]),
                }
            )
    for rev in revisions:
        if rev["recipe_id"] in by_recipe:
            by_recipe[rev["recipe_id"]]["rev"].append(
                {
                    "id": rev["id"],
                    "recipe_version": rev["recipe_version"],
                    "reason": rev["reason"],
                    "document": rev["document"],
                    "source_site": rev["source_site"],
                    "source_author": rev["source_author"],
                    "scraped_at": iso(rev["scraped_at"]),
                    "created_at": iso(rev["created_at"]),
                }
            )

    records: list[dict[str, Any]] = []
    for row in recipes:
        raw_doc = row["document"]
        document, legacy = load_document(raw_doc)
        stored_version = raw_doc.get("schema_version", 1) if isinstance(raw_doc, dict) else 1
        extras = by_recipe[row["id"]]
        raw_url = row["canonical_url"]
        record = {
            "source": "A",
            "source_id": row["id"],
            "household": households.get(row["household_id"]),
            "title": document.title,
            "document_raw": raw_doc,
            "document": document.model_dump(mode="json"),
            "stored_schema_version": stored_version,
            "legacy_v1": stored_version != 2,
            "legacy_source": legacy,
            "source_url_raw": raw_url,
            "source_url": raw_url if N.canonical_url(raw_url) else None,
            "canonical_url": N.canonical_url(raw_url),
            "source_site": row["source_site"],
            "source_author": row["source_author"],
            "source_metadata": row["source_metadata"],
            "state": row["state"],
            "edit_version": row["edit_version"],
            "scraped_at": iso(row["scraped_at"]),
            "created_at": iso(row["created_at"]),
            "updated_at": iso(row["updated_at"]),
            "archived_at": iso(row["archived_at"]),
            "personal_notes": document.personal_notes,
            "classifications": extras["classifications"],
            "favorites": extras["favorites"],
            "cook_events": extras["cook"],
            "revisions": extras["rev"],
            "images": extras["images"],
        }
        _write_json(out_dir / f"{row['id']}.json", record)
        records.append(record)

    meta = {
        "rows": len(recipes),
        "households": {
            name: sum(1 for r in records if r["household"] == name)
            for name in sorted(set(households.values()))
        },
        "images_total": len(images),
        "orphan_images": [
            {k: o[k] for k in ("id", "role", "media_type", "byte_size", "scan_id", "file")}
            for o in orphans
        ],
        "favorites": len(favorites),
        "classifications": len(classifications),
        "cook_events": len(cook_events),
        "revisions": len(revisions),
        "out_of_scope_tables": out_of_scope,
    }
    _write_json(work / "intermediate" / "A" / "_meta.json", meta)
    return {"records": records, "meta": meta}


# --------------------------------------------------------------------------------------------
# B
# --------------------------------------------------------------------------------------------


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def extract_b(db_path: Path, images_src: Path, work: Path) -> dict[str, Any]:
    out_dir = work / "intermediate" / "B"
    img_dir = work / "intermediate" / "images" / "B"
    conn = _sqlite_ro(db_path)
    try:
        recipes = _rows(conn, "SELECT * FROM recipes ORDER BY id")
        ingredients = _rows(conn, "SELECT * FROM ingredients ORDER BY recipe_id, order_index, id")
        instructions = _rows(conn, "SELECT * FROM instructions ORDER BY recipe_id, step_number, id")
        tags = _rows(
            conn,
            "SELECT rt.recipe_id, t.name, t.category FROM recipe_tags rt "
            "JOIN tags t ON t.id = rt.tag_id ORDER BY rt.recipe_id, t.category, t.name",
        )
        tag_count = conn.execute("SELECT count(*) FROM tags").fetchone()[0]
        collections = _rows(
            conn,
            "SELECT c.name, cr.recipe_id FROM collection_recipes cr "
            "JOIN collections c ON c.id = cr.collection_id ORDER BY c.name, cr.recipe_id",
        )
        feedback = _rows(conn, "SELECT * FROM feedback ORDER BY created_at, id")
        edits = _rows(conn, "SELECT * FROM recipe_edits ORDER BY edited_at, id")
        planning = {
            "shopping_lists": _rows(conn, "SELECT * FROM shopping_lists ORDER BY created_at, id"),
            "shopping_items": _rows(
                conn,
                "SELECT s.*, l.name AS list_name, l.created_at AS list_created_at "
                "FROM shopping_items s JOIN shopping_lists l ON l.id = s.list_id "
                "ORDER BY l.created_at, l.id, s.order_index, s.id",
            ),
            "meal_plans": _rows(conn, "SELECT * FROM meal_plans ORDER BY date, meal_type, id"),
            "pantry_items": _rows(conn, "SELECT * FROM pantry_items ORDER BY name, id"),
            "collections": _rows(conn, "SELECT * FROM collections ORDER BY name"),
        }
        out_of_scope = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in B_OUT_OF_SCOPE
        }
    finally:
        conn.close()

    def group(rows: list[dict[str, Any]], key: str = "recipe_id") -> dict[str, list]:
        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(row[key], []).append(row)
        return grouped

    ing_by, ins_by, tag_by = group(ingredients), group(instructions), group(tags)
    col_by, fb_by, edit_by = group(collections), group(feedback), group(edits)

    records: list[dict[str, Any]] = []
    for row in recipes:
        rid = row["id"]
        ings = [
            {
                k: i[k]
                for k in ("id", "name", "quantity", "unit", "group_name", "notes", "order_index")
            }
            for i in ing_by.get(rid, [])
        ]
        steps = [
            {k: s[k] for k in ("id", "step_number", "text", "duration_minutes")}
            for s in ins_by.get(rid, [])
        ]
        document = None
        doc_error = None
        doc_flags: list[str] = []
        try:
            doc, doc_flags = N.b_to_document(row["title"], ings, steps, row["notes"])
            document = doc.model_dump(mode="json")
        except ValueError as exc:
            doc_error = str(exc).splitlines()[0]
        images = []
        if row["image_path"]:
            name = Path(row["image_path"]).name
            src = images_src / name
            entry: dict[str, Any] = {
                "id": None,
                "role": "primary",
                "position": 0,
                "step_id": None,
                "source_path": row["image_path"],
                "snapshot_file": str(src),
                "exists": src.is_file(),
            }
            if src.is_file():
                dest = img_dir / rid / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                meta_reason = png_generator_metadata(src)
                status_reason = (
                    f"image_status '{row['image_status']}' (set only by cookt2's "
                    "ComfyUI/sd-cli generate_image pipeline)"
                    if row["image_status"]
                    else None
                )
                entry.update(
                    {
                        "media_type": "image/png" if name.endswith(".png") else "image/jpeg",
                        "sha256": sha256_file(src),
                        "byte_size": src.stat().st_size,
                        "file": str(dest.relative_to(work)),
                        "ai_generated": bool(meta_reason or status_reason),
                        "ai_reason": "; ".join(r for r in (meta_reason, status_reason) if r),
                    }
                )
                if row["thumbnail_path"]:
                    thumb = images_src / Path(row["thumbnail_path"]).name
                    if thumb.is_file():
                        tdest = img_dir / rid / thumb.name
                        shutil.copyfile(thumb, tdest)
                        entry["thumb_file"] = str(tdest.relative_to(work))
            images.append(entry)
        url = (row["source_url"] or "").strip() or None
        record = {
            "source": "B",
            "source_id": rid,
            "title": row["title"],
            "slug": row["slug"],
            "origin": row["origin"],
            "description": row["description"],
            "notes": row["notes"],
            "document": document,
            "document_error": doc_error,
            "document_flags": doc_flags,
            "ingredients": ings,
            "instructions": steps,
            "source_url": url if N.canonical_url(url) else None,
            "source_url_raw": row["source_url"],
            "canonical_url": N.canonical_url(url),
            "source_name": row["source_name"],
            "source_site": N.site_from_url(url) or _domain_like(row["source_name"]),
            "prep_time": row["prep_time"],
            "cook_time": row["cook_time"],
            "total_time": row["total_time"],
            "servings": row["servings"],
            "difficulty": row["difficulty"],
            "is_favorite": bool(row["is_favorite"]),
            "is_archived": bool(row["is_archived"]),
            "source_recipe_id": row["source_recipe_id"],
            "generation_id": row["generation_id"],
            "image_status": row["image_status"],
            "created_at": iso(row["created_at"]),
            "updated_at": iso(row["updated_at"]),
            "tags": [{"category": t["category"], "name": t["name"]} for t in tag_by.get(rid, [])],
            "collections": [c["name"] for c in col_by.get(rid, [])],
            "feedback": fb_by.get(rid, []),
            "recipe_edits": edit_by.get(rid, []),
            "images": images,
            "nutrition": {
                k: row[k]
                for k in (
                    "calories_per_serving",
                    "protein_g_per_serving",
                    "carbs_g_per_serving",
                    "fat_g_per_serving",
                    "fiber_g_per_serving",
                    "nutrition_source",
                    "nutrition_confidence",
                )
            },
        }
        _write_json(out_dir / f"{rid}.json", record)
        records.append(record)

    _write_json(out_dir / "_planning.json", planning)
    meta = {
        "rows": len(recipes),
        "origins": {
            o: sum(1 for r in records if r["origin"] == o)
            for o in sorted({r["origin"] for r in records})
        },
        "tags": tag_count,
        "recipe_tags": len(tags),
        "favorites": sum(1 for r in records if r["is_favorite"]),
        "feedback": len(feedback),
        "recipe_edits": len(edits),
        "collections": len(planning["collections"]),
        "collection_recipes": len(collections),
        "shopping_items": len(planning["shopping_items"]),
        "meal_plans": len(planning["meal_plans"]),
        "pantry_items": len(planning["pantry_items"]),
        "images": sum(len(r["images"]) for r in records),
        "out_of_scope_tables": out_of_scope,
    }
    _write_json(out_dir / "_meta.json", meta)
    return {"records": records, "meta": meta, "planning": planning}


def _domain_like(value: str | None) -> str | None:
    text = (value or "").strip().lower().removeprefix("www.")
    return text if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", text) else None


def reset_intermediate(work: Path) -> None:
    target = work / "intermediate"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
