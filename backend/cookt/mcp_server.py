"""Read-only MCP server for assistants, mounted at /mcp (streamable HTTP).

An assistant can read the catalog and derived views but never write: this module
exposes NO mutating tools, and every tool body goes through `_read_conn()`,
a SQLite connection opened with `mode=ro` so a write attempt fails at the
driver. `backend/tests/test_mcp.py` asserts that calling every tool leaves the
DB byte-for-byte unchanged.

`search_recipes` and `get_recipe` keep cookt2's names and shapes so an existing client can
switch by URL only.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db, recipes, search
from .config import settings

mcp = FastMCP(
    "cookt",
    instructions=(
        "cookt is the household recipe catalog. Read-only: search and read recipes, "
        "pairings, what can be made from staples, the week's meal plan and the shopping "
        "list. There are no tools that change anything."
    ),
    stateless_http=True,
)

READ_ONLY_TOOLS = (
    "search_recipes",
    "get_recipe",
    "get_household_context",
    "suggest_pairings",
    "what_can_i_make",
    "get_meal_plan",
    "get_shopping_list",
)


def _read_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _tags(conn: sqlite3.Connection, recipe_id: str) -> list[str]:
    return [
        r["value"]
        for r in conn.execute(
            "SELECT value FROM recipe_tags WHERE recipe_id = ? ORDER BY kind, value", (recipe_id,)
        )
    ]


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    fav = conn.execute(
        "SELECT 1 FROM favorites WHERE recipe_id = ? LIMIT 1", (row["id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "total_time": row["total_minutes"],
        "servings": row["servings"],
        "difficulty": None,
        "is_favorite": fav is not None,
        "tags": _tags(conn, row["id"]),
    }


@mcp.tool()
def search_recipes(query: str, limit: int = 10, favorites_only: bool = False) -> list[dict]:
    """Search the household recipe catalog (fuzzy keywords + semantic, e.g. "something
    cozy with squash"). Returns recipe summaries with ids for get_recipe."""
    with _read_conn() as conn:
        if query.strip():
            ids = search.hybrid(conn, query, limit=max(limit * 3, 30))
        else:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM recipes WHERE archived_at IS NULL ORDER BY updated_at DESC"
                )
            ]
        out = []
        for recipe_id in ids:
            row = conn.execute(
                "SELECT * FROM recipes WHERE id = ? AND archived_at IS NULL", (recipe_id,)
            ).fetchone()
            if row is None:
                continue
            item = _summary(conn, row)
            if favorites_only and not item["is_favorite"]:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out


@mcp.tool()
def get_recipe(recipe_id: str) -> dict:
    """Fetch one recipe in full: ingredients, instructions, notes, tags. Accepts the
    recipe id, its slug, or an old cookt2 / recipe-table id."""
    with _read_conn() as conn:
        try:
            rid = recipes.resolve_id(conn, recipe_id)
        except recipes.NotFound as exc:
            raise ValueError(f"no recipe with id {recipe_id}") from exc
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (rid,)).fetchone()
        document = db.loads(row["document"])
        ingredients = []
        for section in document.get("ingredient_sections", []):
            for item in section.get("ingredients", []):
                ingredients.append(
                    {
                        "name": item.get("name") or item.get("source_text"),
                        "quantity": item.get("quantity"),
                        "unit": item.get("unit"),
                        "group": section.get("heading"),
                        "notes": item.get("note"),
                        "text": item.get("source_text"),
                    }
                )
        instructions = []
        n = 0
        for section in document.get("instruction_sections", []):
            for step in section.get("steps", []):
                n += 1
                instructions.append(
                    {
                        "step": n,
                        "text": step["text"],
                        "duration_minutes": None,
                        "section": section.get("heading"),
                    }
                )
        notes = [
            r["text"]
            for r in conn.execute(
                "SELECT text FROM next_time_notes WHERE recipe_id = ? AND resolved_at IS NULL",
                (rid,),
            )
        ]
        nutrition = conn.execute(
            "SELECT source, confidence, per_serving FROM nutrition WHERE recipe_id = ?", (rid,)
        ).fetchone()
        return {
            **_summary(conn, row),
            "prep_time": row["prep_minutes"],
            "cook_time": row["cook_minutes"],
            "notes": "\n".join(document.get("notes", [])) or None,
            "next_time_notes": notes,
            "source_url": row["source_url"],
            "credit": row["credit"],
            "yield_text": document.get("yield_text"),
            "ingredients": ingredients,
            "instructions": instructions,
            "nutrition_per_serving": (
                {
                    **db.loads(nutrition["per_serving"]),
                    "source": nutrition["source"],
                    "confidence": nutrition["confidence"],
                }
                if nutrition
                else None
            ),
        }


@mcp.tool()
def get_household_context() -> dict:
    """Who cooks here, pantry staples, favorites count and recent cooking."""
    with _read_conn() as conn:
        people = [r["name"] for r in conn.execute("SELECT name FROM people ORDER BY name")]
        staples = [r["name"] for r in conn.execute("SELECT name FROM pantry_staples ORDER BY name")]
        recent = [
            {
                "recipe_id": r["recipe_id"],
                "title": r["title"],
                "cooked_on": r["cooked_on"],
                "person": r["person"],
            }
            for r in conn.execute(
                "SELECT c.recipe_id, r.title, c.cooked_on, p.name AS person FROM cook_log c "
                "JOIN recipes r ON r.id = c.recipe_id LEFT JOIN people p ON p.id = c.person_id "
                "ORDER BY c.cooked_on DESC LIMIT 10"
            )
        ]
        count = conn.execute("SELECT COUNT(*) FROM recipes WHERE archived_at IS NULL").fetchone()[0]
        favorites = conn.execute("SELECT COUNT(DISTINCT recipe_id) FROM favorites").fetchone()[0]
        return {
            "people": people,
            "pantry_staples": staples,
            "recipe_count": count,
            "favorite_count": favorites,
            "recently_cooked": recent,
            "units": "us",
        }


@mcp.tool()
def suggest_pairings(recipe_id: str, limit: int = 6) -> dict:
    """Build a meal around a main: sides, sauces and breads from the catalog, each
    with a one-line reason. Also returns 'more like this'."""
    from .enrich import pairings

    with _read_conn() as conn:
        rid = recipes.resolve_id(conn, recipe_id)
        return pairings.suggest(conn, rid, limit=limit)


@mcp.tool()
def what_can_i_make(limit: int = 10) -> list[dict]:
    """Recipes ranked by how few non-staple ingredients you'd need to buy."""
    from .planning import what_can_i_make as rank

    with _read_conn() as conn:
        return rank(conn, limit=limit)


@mcp.tool()
def get_meal_plan(start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """The week board: planned recipes per day (YYYY-MM-DD; defaults to this week)."""
    start = date.fromisoformat(start_date) if start_date else date.today()
    end = date.fromisoformat(end_date) if end_date else start + timedelta(days=6)
    with _read_conn() as conn:
        return [
            {
                "date": r["day"],
                "recipe_id": r["recipe_id"],
                "title": r["title"],
                "servings": r["servings"],
                "note": r["note"],
            }
            for r in conn.execute(
                "SELECT p.day, p.recipe_id, r.title, p.servings, p.note FROM plan_entries p "
                "JOIN recipes r ON r.id = p.recipe_id WHERE p.day BETWEEN ? AND ? "
                "ORDER BY p.day, p.position",
                (start.isoformat(), end.isoformat()),
            )
        ]


@mcp.tool()
def get_shopping_list(include_checked: bool = False) -> list[dict]:
    """The current shopping list grouped by store section."""
    with _read_conn() as conn:
        rows = conn.execute(
            "SELECT name, quantity, unit, section, checked FROM shopping_items "
            + ("" if include_checked else "WHERE checked = 0 ")
            + "ORDER BY section, position, name"
        ).fetchall()
        return [
            {
                "name": r["name"],
                "quantity": r["quantity"],
                "unit": r["unit"],
                "section": r["section"] or "other",
                "checked": bool(r["checked"]),
            }
            for r in rows
        ]
