"""Enrichment steps: tags (+ derived metadata), features, embedding, nutrition.

`run_step` runs one step for one recipe and records the checkpoint (or the error)
in `enrichment_state`. Used by the backfill CLI and by the import job queue.
"""

from __future__ import annotations

import logging
import sqlite3

from .. import db

log = logging.getLogger("cookt.enrich")


def _versions() -> dict[str, str]:
    from . import embeddings, features, tagging

    versions = {
        "tags": tagging.PROMPT_VERSION,
        "features": features.PROMPT_VERSION,
        "embedding": embeddings.VERSION,
    }
    try:
        from . import nutrition

        versions["nutrition"] = getattr(nutrition, "PROMPT_VERSION", "nutrition-v1")
    except ImportError:
        pass
    return versions


STEP_VERSIONS = _versions()
STEPS = tuple(STEP_VERSIONS)


def _mark(conn: sqlite3.Connection, recipe_id: str, step: str, error: str | None) -> None:
    conn.execute(
        "INSERT INTO enrichment_state(recipe_id, step, version, error, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_id, step) DO UPDATE SET "
        "version = excluded.version, error = excluded.error, updated_at = excluded.updated_at",
        (recipe_id, step, STEP_VERSIONS[step], error, db.now()),
    )


def run_step(conn: sqlite3.Connection, step: str, recipe_id: str) -> bool:
    try:
        if step == "tags":
            from . import tagging

            tagging.tag_recipe(conn, recipe_id)
        elif step == "features":
            from . import features

            features.save(conn, recipe_id, features.profile(conn, recipe_id))
        elif step == "embedding":
            from . import embeddings

            embeddings.embed(conn, [recipe_id])
        elif step == "nutrition":
            from . import nutrition

            result = nutrition.compute_nutrition(conn, recipe_id)
            nutrition.save_nutrition(conn, recipe_id, result)
        else:
            raise ValueError(f"unknown step {step}")
    except Exception as exc:  # noqa: BLE001 - recorded, backfill continues
        log.warning("%s failed for %s: %s", step, recipe_id, exc)
        _mark(conn, recipe_id, step, str(exc)[:1000])
        return False
    _mark(conn, recipe_id, step, None)
    if step in ("tags", "nutrition"):
        # visible in the catalog: bump updated_at so clients refresh
        conn.execute("UPDATE recipes SET updated_at = ? WHERE id = ?", (db.now(), recipe_id))
    return True


def enrich_recipe(
    conn: sqlite3.Connection, recipe_id: str, steps: tuple[str, ...] = STEPS
) -> dict[str, bool]:
    return {step: run_step(conn, step, recipe_id) for step in steps}
