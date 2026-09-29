"""Server-side search: semantic (local embeddings) and a small lexical scorer, fused by RRF.

The app's primary search is client-side (MiniSearch, offline). This module serves
the semantic half to the client and full hybrid search to MCP clients.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
import time
from difflib import SequenceMatcher
from typing import Any

from . import db, llm
from .config import settings

log = logging.getLogger("cookt.search")

_cache_lock = threading.Lock()
_matrix: dict[str, Any] = {"stamp": None, "ids": [], "vectors": []}


def embedding_text(recipe: sqlite3.Row | dict[str, Any], tags: list[str]) -> str:
    document = (
        db.loads(recipe["document"]) if isinstance(recipe["document"], str) else recipe["document"]
    )
    ingredients = [
        item.get("name") or item.get("source_text")
        for section in document.get("ingredient_sections", [])
        for item in section.get("ingredients", [])
    ]
    steps = [
        step["text"]
        for section in document.get("instruction_sections", [])
        for step in section.get("steps", [])
    ]
    parts = [
        recipe["title"],
        recipe["description"] or "",
        "Tags: " + ", ".join(tags) if tags else "",
        "Ingredients: " + "; ".join(i for i in ingredients if i),
        "Method: " + " ".join(steps)[:1500],
    ]
    return "\n".join(p for p in parts if p)[:6000]


def content_hash(text: str) -> str:
    return hashlib.sha256(f"{settings.embedding_model}\n{text}".encode()).hexdigest()


def _load_matrix(conn: sqlite3.Connection) -> tuple[list[str], list[list[float]]]:
    stamp = conn.execute(
        "SELECT COUNT(*) || ':' || COALESCE(MAX(created_at), '') FROM embeddings"
    ).fetchone()[0]
    with _cache_lock:
        if _matrix["stamp"] != stamp:
            ids, vectors = [], []
            for row in conn.execute(
                "SELECT e.recipe_id, e.vector FROM embeddings e JOIN recipes r ON r.id = e.recipe_id "
                "WHERE r.archived_at IS NULL"
            ):
                ids.append(row["recipe_id"])
                vectors.append(llm.normalize(llm.unpack(row["vector"])))
            _matrix.update(stamp=stamp, ids=ids, vectors=vectors)
        return _matrix["ids"], _matrix["vectors"]


def recipe_vectors(conn: sqlite3.Connection) -> dict[str, list[float]]:
    ids, vectors = _load_matrix(conn)
    return dict(zip(ids, vectors, strict=True))


def semantic(conn: sqlite3.Connection, query: str, limit: int = 40) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    ids, vectors = _load_matrix(conn)
    if not ids:
        return []
    started = time.monotonic()
    try:
        q = llm.normalize(llm.embed_query(query))
    except llm.ModelError as exc:
        log.warning("semantic search unavailable: %s", exc)
        return []
    scored = sorted(
        (
            (sum(a * b for a, b in zip(q, v, strict=False)), rid)
            for rid, v in zip(ids, vectors, strict=True)
        ),
        reverse=True,
    )[:limit]
    log.debug("semantic search %.1f ms", (time.monotonic() - started) * 1000)
    return [{"id": rid, "score": round(score, 4)} for score, rid in scored]


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _term_score(term: str, words: set[str]) -> float:
    if term in words:
        return 1.0
    best = 0.0
    for word in words:
        if word.startswith(term) and len(term) >= 3:
            best = max(best, 0.8)
        elif len(term) >= 4 and abs(len(word) - len(term)) <= 2:
            ratio = SequenceMatcher(None, term, word).ratio()
            if ratio >= 0.8:
                best = max(best, 0.6 * ratio)
    return best


def lexical(conn: sqlite3.Connection, query: str, limit: int = 40) -> list[dict[str, Any]]:
    terms = _tokens(query)
    if not terms:
        return []
    tags: dict[str, list[str]] = {}
    for row in conn.execute("SELECT recipe_id, value FROM recipe_tags"):
        tags.setdefault(row["recipe_id"], []).append(row["value"])
    results = []
    for row in conn.execute(
        "SELECT id, title, document, source_site, credit FROM recipes WHERE archived_at IS NULL"
    ):
        document = db.loads(row["document"])
        fields = {
            "title": (set(_tokens(row["title"] + " " + (row["credit"] or ""))), 5.0),
            "tags": (set(_tokens(" ".join(tags.get(row["id"], [])))), 3.0),
            "ingredients": (
                set(
                    _tokens(
                        " ".join(
                            i.get("source_text", "")
                            for s in document.get("ingredient_sections", [])
                            for i in s.get("ingredients", [])
                        )
                    )
                ),
                2.0,
            ),
            "source": (set(_tokens(row["source_site"] or "")), 1.0),
        }
        total = 0.0
        for term in terms:
            best = max((_term_score(term, words) * w for words, w in fields.values()), default=0)
            if best == 0:
                total = 0
                break
            total += best
        if total > 0:
            results.append({"id": row["id"], "score": round(total, 3)})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def rrf(*rankings: list[dict[str, Any]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (k + rank + 1)
    return [rid for rid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


def hybrid(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[str]:
    lex = lexical(conn, query, limit=40)
    sem = semantic(conn, query, limit=40)
    # Semantic-only hits need a reasonable similarity to be worth showing.
    sem = [s for s in sem if s["score"] >= 0.35] if lex else sem
    return rrf(lex, sem)[:limit]
