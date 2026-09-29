"""MCP is read-only: no mutating tools exist, and calling every tool leaves the DB unchanged."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sqlite3
from pathlib import Path

import pytest


def _seed(db_path: Path) -> str:
    from cookt import db

    conn = db.connect(db_path)
    db.init(conn)
    rid = "11111111-1111-1111-1111-111111111111"
    document = {
        "schema_version": 2,
        "title": "Pork carnitas",
        "yield_text": "Serves 6",
        "notes": [],
        "ingredient_sections": [
            {
                "id": "ingredients_1",
                "heading": None,
                "ingredients": [
                    {
                        "id": "i_1",
                        "source_text": "3 lb pork shoulder",
                        "quantity": "3",
                        "unit": "lb",
                        "name": "pork shoulder",
                    },
                    {"id": "i_2", "source_text": "1 orange, juiced"},
                ],
            }
        ],
        "instruction_sections": [
            {
                "id": "instructions_1",
                "heading": None,
                "steps": [
                    {"id": "s1", "text": "Braise the pork for 3 hours."},
                    {"id": "s2", "text": "Crisp in a hot pan."},
                ],
            }
        ],
    }
    now = db.now()
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, servings, total_minutes, origin, created_at,"
        " updated_at) VALUES (?, 'pork-carnitas', 'Pork carnitas', ?, 6, 200, 'test', ?, ?)",
        (rid, json.dumps(document), now, now),
    )
    conn.execute(
        "INSERT INTO recipe_aliases(alias, recipe_id, system) VALUES ('old-cookt2-id', ?, 'cookt2')",
        (rid,),
    )
    conn.execute(
        "INSERT INTO recipe_tags(recipe_id, kind, value, source, created_at) "
        "VALUES (?, 'cuisine', 'Mexican', 'manual', ?)",
        (rid, now),
    )
    conn.execute("INSERT INTO pantry_staples(name, created_at) VALUES ('salt', ?)", (now,))
    conn.execute(
        "INSERT INTO shopping_items(id, name, created_at, updated_at) VALUES ('x', 'limes', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO plan_entries(id, day, recipe_id, created_at) VALUES ('p', '2026-09-28', ?, ?)",
        (rid, now),
    )
    conn.close()
    return rid


def _db_fingerprint(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    digest = hashlib.sha256()
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY 1'):
            digest.update(repr(row).encode())
    conn.close()
    return digest.hexdigest()


@pytest.fixture()
def mcp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKT_DATA_DIR", str(tmp_path))
    import cookt.config

    importlib.reload(cookt.config)
    import cookt.mcp_server as server

    monkeypatch.setattr(server, "settings", cookt.config.settings)
    # Semantic search needs the embedding server; keep this test hermetic.
    import cookt.search as search

    monkeypatch.setattr(search, "semantic", lambda conn, q, limit=40: [])
    rid = _seed(tmp_path / "cookt.db")
    yield server, rid, tmp_path / "cookt.db"
    importlib.reload(cookt.config)


def test_only_read_only_tools_are_exposed(mcp_env):
    server, _rid, _db = mcp_env
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == set(server.READ_ONLY_TOOLS)
    for forbidden in (
        "generate_recipe",
        "transform_recipe",
        "save_recipe",
        "record_feedback",
        "list_profiles",
        "plan_meal",
        "add_recipe_to_shopping_list",
        "log_recipe_eaten",
    ):
        assert forbidden not in names


def test_no_tool_mutates_state(mcp_env):
    server, rid, db_path = mcp_env
    before = _db_fingerprint(db_path)
    calls = {
        "search_recipes": {"query": "carnitsa"},
        "get_recipe": {"recipe_id": "old-cookt2-id"},
        "get_household_context": {},
        "suggest_pairings": {"recipe_id": rid},
        "what_can_i_make": {},
        "get_meal_plan": {"start_date": "2026-09-27", "end_date": "2026-10-03"},
        "get_shopping_list": {},
    }
    assert set(calls) == set(server.READ_ONLY_TOOLS)
    results = {}
    for name, args in calls.items():
        results[name] = asyncio.run(server.mcp.call_tool(name, args))
    assert _db_fingerprint(db_path) == before
    # the tools actually returned data
    text = json.dumps([str(r) for r in results.values()])
    assert "Pork carnitas" in text
    assert "limes" in text


def test_read_connection_refuses_writes(mcp_env):
    server, _rid, _db = mcp_env
    conn = server._read_conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM recipes")
    conn.close()


def test_search_recipes_shape_matches_cookt2(mcp_env):
    server, rid, _db = mcp_env
    items = server.search_recipes("carnitsa")  # typo tolerant
    assert items and items[0]["id"] == rid
    assert set(items[0]) >= {
        "id",
        "title",
        "description",
        "total_time",
        "servings",
        "difficulty",
        "is_favorite",
        "tags",
    }
    detail = server.get_recipe(rid)
    assert set(detail) >= {
        "prep_time",
        "cook_time",
        "notes",
        "source_url",
        "ingredients",
        "instructions",
    }
    assert detail["ingredients"][0]["name"] == "pork shoulder"
    assert detail["instructions"][1]["step"] == 2
