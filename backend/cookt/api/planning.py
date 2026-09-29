"""Week board, shopping list (with offline sync), pantry staples, "what can I make"."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, jobs, planning, recipes

router = APIRouter(prefix="/api")


def _conn():
    return db.get_conn()


# --- week board ------------------------------------------------------------------------


@router.get("/plan")
def get_plan(start: str | None = None, days: int = 7) -> dict[str, Any]:
    first = date.fromisoformat(start) if start else date.today()
    last = first + timedelta(days=max(1, min(days, 42)) - 1)
    rows = (
        _conn()
        .execute(
            "SELECT p.*, r.title, r.slug, r.servings AS recipe_servings FROM plan_entries p "
            "JOIN recipes r ON r.id = p.recipe_id WHERE p.day BETWEEN ? AND ? ORDER BY p.day, p.position",
            (first.isoformat(), last.isoformat()),
        )
        .fetchall()
    )
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "entries": [
            {
                "id": r["id"],
                "day": r["day"],
                "recipe_id": r["recipe_id"],
                "title": r["title"],
                "slug": r["slug"],
                "servings": r["servings"],
                "recipe_servings": r["recipe_servings"],
                "position": r["position"],
                "note": r["note"],
            }
            for r in rows
        ],
    }


class PlanEntry(BaseModel):
    day: str
    recipe_id: str
    servings: float | None = None
    note: str | None = None


@router.post("/plan")
def add_plan(body: PlanEntry) -> dict[str, Any]:
    conn = _conn()
    date.fromisoformat(body.day)
    recipe_id = recipes.resolve_id(conn, body.recipe_id)
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM plan_entries WHERE day = ?", (body.day,)
    ).fetchone()[0]
    entry_id = db.new_id()
    conn.execute(
        "INSERT INTO plan_entries(id, day, recipe_id, servings, position, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (entry_id, body.day, recipe_id, body.servings, position, body.note, db.now()),
    )
    return {"id": entry_id}


class PlanPatch(BaseModel):
    day: str | None = None
    servings: float | None = None
    position: int | None = None
    note: str | None = None


@router.patch("/plan/{entry_id}")
def patch_plan(entry_id: str, body: PlanPatch) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if "day" in changes:
        date.fromisoformat(changes["day"])
    if not changes:
        return {"ok": True}
    sets = ", ".join(f"{k} = ?" for k in changes)
    _conn().execute(f"UPDATE plan_entries SET {sets} WHERE id = ?", (*changes.values(), entry_id))
    return {"ok": True}


@router.delete("/plan/{entry_id}")
def delete_plan(entry_id: str) -> dict[str, Any]:
    _conn().execute("DELETE FROM plan_entries WHERE id = ?", (entry_id,))
    return {"ok": True}


# --- shopping --------------------------------------------------------------------------


@router.get("/shopping")
def get_shopping() -> dict[str, Any]:
    conn = _conn()
    return {"items": planning.shopping_list(conn), "sections": list(planning.SECTIONS)}


class FromPlan(BaseModel):
    start: str
    days: int = 7


def _classify_later(conn) -> None:
    unknown = conn.execute(
        "SELECT COUNT(*) FROM shopping_items s LEFT JOIN store_section_cache c ON c.name = s.name "
        "WHERE c.name IS NULL"
    ).fetchone()[0]
    if unknown:
        jobs.enqueue(conn, "classify_sections", {})


@router.post("/shopping/from-plan")
def shopping_from_plan(body: FromPlan) -> dict[str, Any]:
    conn = _conn()
    first = date.fromisoformat(body.start)
    last = first + timedelta(days=max(1, min(body.days, 42)) - 1)
    entries = []
    for row in conn.execute(
        "SELECT p.recipe_id, p.servings, r.servings AS base FROM plan_entries p "
        "JOIN recipes r ON r.id = p.recipe_id WHERE p.day BETWEEN ? AND ?",
        (first.isoformat(), last.isoformat()),
    ):
        factor = (row["servings"] / row["base"]) if row["servings"] and row["base"] else 1.0
        entries.append((row["recipe_id"], factor))
    result = planning.add_to_shopping(conn, entries)
    _classify_later(conn)
    return {**result, "recipes": len(entries)}


class FromRecipes(BaseModel):
    recipe_ids: list[str] = Field(min_length=1, max_length=50)
    factors: list[float] | None = None


@router.post("/shopping/from-recipes")
def shopping_from_recipes(body: FromRecipes) -> dict[str, Any]:
    conn = _conn()
    factors = body.factors or [1.0] * len(body.recipe_ids)
    entries = [
        (recipes.resolve_id(conn, rid), f) for rid, f in zip(body.recipe_ids, factors, strict=False)
    ]
    result = planning.add_to_shopping(conn, entries)
    _classify_later(conn)
    return result


class ManualItem(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    quantity: str | None = None
    unit: str | None = None


@router.post("/shopping/items")
def add_item(body: ManualItem) -> dict[str, Any]:
    conn = _conn()
    stamp = db.now()
    name = body.name.strip().lower()
    item_id = body.id or db.new_id()
    position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM shopping_items").fetchone()[
        0
    ]
    conn.execute(
        "INSERT OR IGNORE INTO shopping_items(id, name, quantity, unit, section, sources, checked, "
        "position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '[]', 0, ?, ?, ?)",
        (
            item_id,
            name,
            body.quantity,
            body.unit,
            planning.section_for(conn, name),
            position,
            stamp,
            stamp,
        ),
    )
    _classify_later(conn)
    return {"id": item_id}


class SyncOp(BaseModel):
    id: str
    op: str  # check | uncheck | delete | add
    at: str
    name: str | None = None


class SyncBody(BaseModel):
    ops: list[SyncOp] = Field(max_length=2000)


@router.post("/shopping/sync")
def sync(body: SyncBody) -> dict[str, Any]:
    """Replay check-offs made offline. Last writer wins per item, by client timestamp."""
    conn = _conn()
    applied = 0
    for op in sorted(body.ops, key=lambda o: o.at):
        if op.op == "add" and op.name:
            add_item(ManualItem(id=op.id, name=op.name))
            applied += 1
            continue
        row = conn.execute(
            "SELECT updated_at FROM shopping_items WHERE id = ?", (op.id,)
        ).fetchone()
        if row is None:
            continue
        if op.op in ("check", "uncheck"):
            if row["updated_at"] > op.at:
                continue  # a newer server-side change wins
            conn.execute(
                "UPDATE shopping_items SET checked = ?, updated_at = ? WHERE id = ?",
                (1 if op.op == "check" else 0, op.at, op.id),
            )
            applied += 1
        elif op.op == "delete":
            conn.execute("DELETE FROM shopping_items WHERE id = ?", (op.id,))
            applied += 1
    return {"applied": applied, "items": planning.shopping_list(conn)}


class Check(BaseModel):
    checked: bool


@router.patch("/shopping/items/{item_id}")
def check_item(item_id: str, body: Check) -> dict[str, Any]:
    _conn().execute(
        "UPDATE shopping_items SET checked = ?, updated_at = ? WHERE id = ?",
        (int(body.checked), db.now(), item_id),
    )
    return {"ok": True}


@router.delete("/shopping/items/{item_id}")
def delete_item(item_id: str) -> dict[str, Any]:
    _conn().execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
    return {"ok": True}


@router.delete("/shopping/checked")
def clear_checked() -> dict[str, Any]:
    cur = _conn().execute("DELETE FROM shopping_items WHERE checked = 1")
    return {"removed": cur.rowcount}


# --- pantry staples --------------------------------------------------------------------


@router.get("/pantry")
def get_pantry() -> dict[str, Any]:
    return {"staples": planning.staples(_conn())}


class Staple(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/pantry")
def add_staple(body: Staple) -> dict[str, Any]:
    _conn().execute(
        "INSERT OR IGNORE INTO pantry_staples(name, created_at) VALUES (?, ?)",
        (body.name.strip().lower(), db.now()),
    )
    return {"ok": True}


@router.delete("/pantry/{name}")
def delete_staple(name: str) -> dict[str, Any]:
    cur = _conn().execute("DELETE FROM pantry_staples WHERE name = ?", (name.lower(),))
    if not cur.rowcount:
        raise HTTPException(404, "not a staple")
    return {"ok": True}


@router.get("/what-can-i-make")
def what_can_i_make(limit: int = 30) -> dict[str, Any]:
    return {"results": planning.what_can_i_make(_conn(), limit=limit)}
