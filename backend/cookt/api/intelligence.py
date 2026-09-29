"""Pairings, the Changes feed (auto changes, one-tap revert), nutrition overrides, re-enrichment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, jobs, recipes
from ..enrich import STEPS, pairings, tagging

router = APIRouter(prefix="/api")


def _conn():
    return db.get_conn()


@router.get("/recipes/{key}/pairings")
def get_pairings(key: str, limit: int = 6) -> dict[str, Any]:
    conn = _conn()
    return pairings.suggest(conn, recipes.resolve_id(conn, key), limit=limit)


@router.get("/changes")
def list_changes(
    limit: int = 100,
    offset: int = 0,
    recipe: str | None = None,
    include_reverted: bool = True,
) -> dict[str, Any]:
    conn = _conn()
    where, params = ["1=1"], []
    if recipe:
        where.append("c.recipe_id = ?")
        params.append(recipes.resolve_id(conn, recipe))
    if not include_reverted:
        where.append("c.reverted_at IS NULL")
    rows = conn.execute(
        "SELECT c.*, r.title, r.slug FROM changes c JOIN recipes r ON r.id = c.recipe_id "
        f"WHERE {' AND '.join(where)} ORDER BY c.created_at DESC, c.rowid DESC LIMIT ? OFFSET ?",
        (*params, min(limit, 500), max(0, offset)),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
    reverted = conn.execute(
        "SELECT COUNT(*) FROM changes WHERE reverted_at IS NOT NULL"
    ).fetchone()[0]
    return {
        "total": total,
        "reverted": reverted,
        "items": [
            {
                "id": r["id"],
                "recipe_id": r["recipe_id"],
                "title": r["title"],
                "slug": r["slug"],
                "field": r["field"],
                "before": db.loads(r["before"]),
                "after": db.loads(r["after"]),
                "evidence": r["evidence"],
                "model": r["model"],
                "prompt_version": r["prompt_version"],
                "created_at": r["created_at"],
                "reverted_at": r["reverted_at"],
            }
            for r in rows
        ],
    }


@router.post("/changes/{change_id}/revert")
def revert_change(change_id: str) -> dict[str, Any]:
    try:
        return tagging.revert(_conn(), change_id)
    except LookupError as exc:
        raise HTTPException(404, "change not found") from exc


class EnrichBody(BaseModel):
    steps: list[str] | None = None


@router.post("/recipes/{key}/enrich")
def enrich(key: str, body: EnrichBody) -> dict[str, Any]:
    conn = _conn()
    recipe_id = recipes.resolve_id(conn, key)
    steps = [s for s in (body.steps or list(STEPS)) if s in STEPS]
    job_id = jobs.enqueue(conn, "enrich", {"recipe_id": recipe_id, "steps": steps})
    return {"job_id": job_id}


class OverrideBody(BaseModel):
    ingredient_id: str
    fdc_id: int | None = None
    grams: float | None = None


@router.post("/recipes/{key}/nutrition/override")
def nutrition_override(key: str, body: OverrideBody) -> dict[str, Any]:
    conn = _conn()
    recipe_id = recipes.resolve_id(conn, key)
    from ..enrich import nutrition

    nutrition.set_override(conn, recipe_id, body.ingredient_id, body.fdc_id, grams=body.grams)
    nutrition.refresh(conn, recipe_id)
    conn.execute("UPDATE recipes SET updated_at = ? WHERE id = ?", (db.now(), recipe_id))
    return {"ok": True}


@router.get("/fdc/search")
def fdc_search(q: str, limit: int = 8) -> dict[str, Any]:
    from ..enrich import fdc

    return {
        "results": [
            dict(r) if not isinstance(r, dict) else r
            for r in fdc.search_foods(_conn(), q, limit=limit)
        ]
    }


@router.get("/enrichment/status")
def enrichment_status() -> dict[str, Any]:
    conn = _conn()
    from ..enrich import STEP_VERSIONS

    total = conn.execute("SELECT COUNT(*) FROM recipes WHERE archived_at IS NULL").fetchone()[0]
    out = {}
    for step, version in STEP_VERSIONS.items():
        out[step] = conn.execute(
            "SELECT COUNT(*) FROM enrichment_state e JOIN recipes r ON r.id = e.recipe_id "
            "WHERE e.step = ? AND e.version = ? AND e.error IS NULL AND r.archived_at IS NULL",
            (step, version),
        ).fetchone()[0]
    return {"total": total, "done": out}
