"""Recipe embeddings (Qwen3-Embedding, documents embedded plain; queries get the instruct prefix)."""

from __future__ import annotations

import sqlite3

from .. import db, llm, search
from ..config import settings

VERSION = f"emb-{settings.embedding_model}-v1"


def text_for(conn: sqlite3.Connection, recipe_id: str) -> str:
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    tags = [
        r["value"]
        for r in conn.execute(
            "SELECT value FROM recipe_tags WHERE recipe_id = ? AND kind != 'personal'", (recipe_id,)
        )
    ]
    return search.embedding_text(row, tags)


def embed(conn: sqlite3.Connection, recipe_ids: list[str], force: bool = False) -> int:
    todo = []
    for rid in recipe_ids:
        text = text_for(conn, rid)
        digest = search.content_hash(text)
        row = conn.execute(
            "SELECT content_hash FROM embeddings WHERE recipe_id = ?", (rid,)
        ).fetchone()
        if force or row is None or row["content_hash"] != digest:
            todo.append((rid, text, digest))
    for start in range(0, len(todo), 16):
        batch = todo[start : start + 16]
        vectors = llm.embed_texts([t for _, t, _ in batch])
        for (rid, _text, digest), vector in zip(batch, vectors, strict=True):
            conn.execute(
                "INSERT INTO embeddings(recipe_id, model, content_hash, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_id) DO UPDATE SET model = excluded.model,"
                " content_hash = excluded.content_hash, vector = excluded.vector, "
                "created_at = excluded.created_at",
                (rid, settings.embedding_model, digest, llm.pack(vector), db.now()),
            )
    return len(todo)
