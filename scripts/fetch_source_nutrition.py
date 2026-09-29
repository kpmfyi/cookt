"""Recover publisher JSON-LD `nutrition` blocks for migrated recipes (recipe-table discarded them).

Deterministic: fetches each source page once (robots.txt respected, it's a batch job; SSRF-safe,
2 s between requests), reads only the JSON-LD Recipe `nutrition` object, and stores it verbatim in
`source_nutrition`. Recipe text is never touched. Resumable: recipes already having a row, or
already attempted (logged in data/source-nutrition.jsonl), are skipped.

    uv run python scripts/fetch_source_nutrition.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time

from bs4 import BeautifulSoup
from cookt import db
from cookt.config import settings
from cookt.url_safety import fetch_recipe_page

LOG = settings.data_dir / "source-nutrition.jsonl"


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def nutrition_block(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Recipe" in types and isinstance(node.get("nutrition"), dict):
                block = dict(node["nutrition"])
                if node.get("recipeYield") is not None:
                    block["_recipeYield"] = node["recipeYield"]
                return block
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    conn = db.connect()
    tried = set()
    if LOG.exists():
        tried = {
            json.loads(line)["recipe_id"] for line in LOG.read_text().splitlines() if line.strip()
        }
    rows = conn.execute(
        "SELECT r.id, r.source_url FROM recipes r LEFT JOIN source_nutrition s ON s.recipe_id = r.id "
        "WHERE s.recipe_id IS NULL AND r.source_url LIKE 'http%' AND r.archived_at IS NULL ORDER BY r.id"
    ).fetchall()
    rows = [r for r in rows if r["id"] not in tried]
    if args.limit:
        rows = rows[: args.limit]
    found = 0
    with LOG.open("a") as log:
        for n, row in enumerate(rows, 1):
            outcome: dict = {"recipe_id": row["id"], "url": row["source_url"]}
            try:
                page = fetch_recipe_page(
                    row["source_url"], respect_robots=True, allow_restricted=True
                )
                block = nutrition_block(page.html)
                outcome["found"] = bool(block)
                if block:
                    conn.execute(
                        "INSERT OR REPLACE INTO source_nutrition(recipe_id, data, created_at) VALUES (?, ?, ?)",
                        (row["id"], db.dumps(block), db.now()),
                    )
                    found += 1
            except Exception as exc:  # noqa: BLE001 - logged and skipped
                outcome["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.write(json.dumps(outcome) + "\n")
            log.flush()
            print(f"{n}/{len(rows)} found={found} {outcome.get('error', '')[:80]}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
