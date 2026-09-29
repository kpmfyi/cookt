"""Job handlers (import, enrichment). Registered on import by `jobs.start_worker`."""

from __future__ import annotations

import sqlite3
from typing import Any

from .jobs import handler


@handler("enrich")
def enrich(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    from .enrich import STEPS, enrich_recipe

    steps = tuple(payload.get("steps") or STEPS)
    enrich_recipe(conn, payload["recipe_id"], steps)


@handler("classify_sections")
def classify_sections(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Store-section classification for new shopping item names (LLM, cached per name)."""
    from typing import Literal

    from pydantic import BaseModel, ConfigDict

    from . import db, llm
    from .config import settings
    from .planning import SECTIONS

    names = [
        r["name"]
        for r in conn.execute(
            "SELECT DISTINCT s.name FROM shopping_items s LEFT JOIN store_section_cache c "
            "ON c.name = s.name WHERE c.name IS NULL LIMIT 80"
        )
    ]
    if not names:
        return
    Section = Literal[SECTIONS]  # type: ignore[valid-type]

    class Item(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str
        section: Section  # type: ignore[valid-type]

    class Result(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[Item]

    result = llm.complete_json(
        "Assign each grocery item to the US supermarket section where you'd find it. "
        f"Sections: {', '.join(SECTIONS)}.\nItems:\n" + "\n".join(f"- {n}" for n in names),
        Result,
        "store_sections",
        max_tokens=4000,
    )
    stamp = db.now()
    for item in result.items:
        if item.name in names:
            conn.execute(
                "INSERT OR REPLACE INTO store_section_cache(name, section, model, created_at) "
                "VALUES (?, ?, ?, ?)",
                (item.name, item.section, settings.text_model, stamp),
            )
            conn.execute(
                "UPDATE shopping_items SET section = ? WHERE name = ?", (item.section, item.name)
            )


@handler("import")
def import_job(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    from .imports import run_import

    run_import(conn, payload["inbox_id"])
