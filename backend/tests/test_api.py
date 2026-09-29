"""HTTP API: catalog, edit + revisions, favorites, cook log, notes, planning, shopping, inbox, changes."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

DOC = {
    "schema_version": 2,
    "title": "Weeknight Chili",
    "yield_text": "Serves 4",
    "notes": [],
    "ingredient_sections": [
        {
            "id": "ingredients_1",
            "heading": None,
            "ingredients": [
                {"id": "i1", "source_text": "1 lb ground beef"},
                {"id": "i2", "source_text": "1 cup chopped onion"},
                {"id": "i3", "source_text": "2 tbsp chili powder"},
                {"id": "i4", "source_text": "1 tsp salt"},
            ],
        }
    ],
    "instruction_sections": [
        {
            "id": "instructions_1",
            "heading": None,
            "steps": [
                {"id": "s1", "text": "Brown the beef with the onion."},
                {"id": "s2", "text": "Add chili powder and simmer 20 minutes."},
            ],
        }
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKT_RUN_WORKER", "0")
    monkeypatch.setenv("COOKT_LLM_LOCK", str(tmp_path / "llm.lock"))
    import cookt.config

    importlib.reload(cookt.config)
    modules = [
        "cookt.db",
        "cookt.search",
        "cookt.recipes",
        "cookt.planning",
        "cookt.jobs",
        "cookt.imports",
        "cookt.enrich.tagging",
        "cookt.api.intelligence",
        "cookt.api.planning",
        "cookt.api.imports",
        "cookt.mcp_server",
        "cookt.pipeline_jobs",
        "cookt.app",
    ]
    for name in modules:
        importlib.reload(importlib.import_module(name))
    import cookt.search as search

    monkeypatch.setattr(search, "semantic", lambda conn, q, limit=40: [])
    from cookt import db

    conn = db.connect()
    db.init(conn)
    now = db.now()
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, servings, origin, created_at, updated_at) "
        "VALUES ('r1', 'weeknight-chili', 'Weeknight Chili', ?, 4, 'test', ?, ?)",
        (json.dumps(DOC), now, now),
    )
    conn.execute(
        "INSERT INTO recipe_aliases(alias, recipe_id, system) VALUES ('old-id', 'r1', 'cookt2')"
    )
    conn.close()
    from cookt.app import app

    with TestClient(app) as c:
        yield c
    importlib.reload(cookt.config)


def test_catalog_and_alias_resolution(client):
    catalog = client.get("/api/catalog").json()
    assert [r["title"] for r in catalog["recipes"]] == ["Weeknight Chili"]
    assert client.get("/api/recipes/old-id").json()["id"] == "r1"
    assert client.get("/api/recipes/weeknight-chili").json()["id"] == "r1"
    assert client.get("/api/recipes/nope").status_code == 404


def test_edit_creates_revision_and_restore(client):
    doc = json.loads(json.dumps(DOC))
    doc["title"] = "Better Chili"
    doc["ingredient_sections"][0]["ingredients"].append({"id": "i5", "source_text": "1 can beans"})
    r = client.put(
        "/api/recipes/r1",
        json={"document": doc, "personal_tags": ["weeknight"], "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["title"] == "Better Chili" and detail["slug"] == "better-chili"
    assert len(detail["revisions"]) == 1
    # the old slug still resolves
    assert client.get("/api/recipes/weeknight-chili").json()["id"] == "r1"
    # stale write rejected
    assert (
        client.put("/api/recipes/r1", json={"description": "x", "expected_version": 1}).status_code
        == 422
    )
    rev = detail["revisions"][0]["id"]
    restored = client.post(f"/api/recipes/r1/revisions/{rev}/restore", json={}).json()
    assert restored["title"] == "Weeknight Chili"
    assert len(restored["document"]["ingredient_sections"][0]["ingredients"]) == 4
    assert len(restored["revisions"]) == 2


def test_manual_tag_removal_is_remembered(client):
    client.put("/api/recipes/r1", json={"cuisine_tags": ["Tex-Mex"]})
    client.put("/api/recipes/r1", json={"cuisine_tags": []})
    from cookt import db

    conn = db.get_conn()
    assert (
        conn.execute("SELECT COUNT(*) FROM rejected_tags WHERE value='Tex-Mex'").fetchone()[0] == 1
    )


def test_people_favorites_cooklog_notes(client):
    person = client.post("/api/people", json={"name": "Sam"}).json()
    assert client.post("/api/people", json={"name": "Sam"}).json()["id"] == person["id"]
    client.post("/api/recipes/r1/favorite", json={"person_id": person["id"], "on": True})
    client.post(
        "/api/recipes/r1/cooked",
        json={
            "person_id": person["id"],
            "cooked_on": "2026-09-20",
            "make_again": True,
            "note": "great",
            "next_time": "more cumin",
        },
    )
    recipe = client.get("/api/catalog").json()["recipes"][0]
    assert recipe["favorites"] == [person["id"]]
    assert recipe["cooked"] == {"count": 1, "last": "2026-09-20"}
    assert recipe["next_time"][0]["text"] == "more cumin"
    detail = client.get("/api/recipes/r1").json()
    assert detail["cook_log"][0]["person"] == "Sam"
    client.delete(f"/api/notes/{recipe['next_time'][0]['id']}")
    assert client.get("/api/catalog").json()["recipes"][0]["next_time"] == []


def test_plan_and_shopping_merge_and_staples(client):
    client.post("/api/pantry", json={"name": "salt"})
    client.post("/api/plan", json={"day": "2026-09-28", "recipe_id": "r1", "servings": 8})
    client.post("/api/plan", json={"day": "2026-09-29", "recipe_id": "old-id"})
    plan = client.get("/api/plan?start=2026-09-28").json()
    assert len(plan["entries"]) == 2
    result = client.post("/api/shopping/from-plan", json={"start": "2026-09-28", "days": 7}).json()
    assert result["skipped_staples"] == ["salt"]
    items = {i["name"]: i for i in client.get("/api/shopping").json()["items"]}
    # 2x (8 servings) + 1x = 3 lb beef, merged into one line
    assert items["ground beef"]["quantity"] == "3" and items["ground beef"]["unit"] == "lb"
    assert items["onion"]["quantity"] == "3" and items["onion"]["unit"] == "cups"
    assert len(items["ground beef"]["sources"]) == 2
    assert "salt" not in items


def test_shopping_offline_sync_last_writer_wins(client):
    client.post("/api/shopping/items", json={"id": "a", "name": "limes"})
    r = client.post(
        "/api/shopping/sync",
        json={
            "ops": [
                {"id": "a", "op": "check", "at": "2099-01-01T00:00:00+00:00"},
                {"id": "b", "op": "add", "at": "2099-01-01T00:00:01+00:00", "name": "cilantro"},
            ]
        },
    ).json()
    by_name = {i["name"]: i for i in r["items"]}
    assert by_name["limes"]["checked"] is True
    assert "cilantro" in by_name
    # an older offline op does not override a newer server change
    r = client.post(
        "/api/shopping/sync",
        json={"ops": [{"id": "a", "op": "uncheck", "at": "2000-01-01T00:00:00+00:00"}]},
    ).json()
    assert {i["name"]: i for i in r["items"]}["limes"]["checked"] is True


def test_what_can_i_make(client):
    client.post("/api/pantry", json={"name": "chili powder"})
    results = client.get("/api/what-can-i-make").json()["results"]
    assert results[0]["title"] == "Weeknight Chili"
    assert "chili powder" not in results[0]["to_buy"]


def test_changes_feed_revert_remembers(client):
    from cookt import db
    from cookt.enrich import tagging

    conn = db.get_conn()
    result = tagging.TagResult(
        cuisine=[tagging.TagEvidence(value="Tex-Mex", confidence=0.9, evidence="chili powder")],
        course=[tagging.TagEvidence(value="main", confidence=0.95, evidence="title")],
        protein=[tagging.TagEvidence(value="beef", confidence=0.95, evidence="1 lb ground beef")],
        diet=[tagging.TagEvidence(value="vegetarian", confidence=0.9, evidence="wrong")],
        prep_minutes=10,
        cook_minutes=None,
        total_minutes=None,
        servings=None,
        ingredient_names=[tagging.CleanName(id="i1", name="ground beef")],
    )
    summaries = tagging.apply(conn, "r1", result, model="fake")
    assert "+protein:beef" in summaries
    assert not any("vegetarian" in s for s in summaries)  # diet guard: beef present
    feed = client.get("/api/changes").json()
    change = next(c for c in feed["items"] if c["field"] == "tag:cuisine")
    assert client.post(f"/api/changes/{change['id']}/revert").json()["ok"]
    tags = {t["value"] for t in client.get("/api/recipes/r1").json()["tags"]}
    assert "Tex-Mex" not in tags
    # re-applying never re-suggests the reverted tag
    tagging.apply(conn, "r1", result, model="fake")
    tags = {t["value"] for t in client.get("/api/recipes/r1").json()["tags"]}
    assert "Tex-Mex" not in tags and "beef" in tags
    # prep_minutes was filled (it was empty) and is revertible
    prep = next(c for c in feed["items"] if c["field"] == "prep_minutes")
    client.post(f"/api/changes/{prep['id']}/revert")
    assert client.get("/api/recipes/r1").json()["prep_minutes"] is None


def test_inbox_paste_extract_dedupe_save_merge(client, monkeypatch):
    from cookt import imports, jobs
    from cookt.extraction import pipeline

    body = "\n".join(
        [
            "Weeknight Chili",
            "",
            "Ingredients",
            "1 lb ground beef",
            "1 cup chopped onion",
            "2 tbsp chili powder",
            "1 tsp salt",
            "",
            "Directions",
            "Brown the beef with the onion.",
            "Add chili powder and simmer 25 minutes.",
        ]
    )
    inbox_id = client.post("/api/import/text", json={"text": body}).json()["id"]
    from cookt import db

    conn = db.get_conn()
    assert jobs.run_one(conn)  # the queued import job
    item = client.get(f"/api/inbox/{inbox_id}").json()
    assert item["status"] == "ready", item["error"]
    assert item["draft"]["document"]["title"] == "Weeknight Chili"
    assert item["duplicates"] and item["duplicates"][0]["recipe_id"] == "r1"
    saved = client.post(f"/api/inbox/{inbox_id}/save", json={"merge_into": "r1"}).json()
    assert saved["recipe_id"] == "r1"
    detail = client.get("/api/recipes/r1").json()
    assert "25 minutes" in json.dumps(detail["document"])
    assert detail["revisions"][0]["reason"].startswith("merged import")
    assert client.get("/api/inbox").json()["items"] == []
    _ = (imports, pipeline, monkeypatch)


def test_classifier_supersedes_only_cookt2_machine_tags(client):
    from cookt import db
    from cookt.enrich import tagging

    conn = db.get_conn()
    now = db.now()
    for kind, value, evidence in (
        ("course", "dessert", "cookt2 tag meal_type:Dessert"),  # machine guess: may be replaced
        ("diet", "vegetarian", "cookt2 tag diet:Vegetarian"),  # unconfirmed machine diet: removed
        ("cuisine", "Tex-Mex", "recipe-table facet"),  # human (recipe-table): never touched
    ):
        conn.execute(
            "INSERT INTO recipe_tags(recipe_id, kind, value, source, evidence, created_at) "
            "VALUES ('r1', ?, ?, 'migrated', ?, ?)",
            (kind, value, evidence, now),
        )
    result = tagging.TagResult(
        cuisine=[tagging.TagEvidence(value="American", confidence=0.8, evidence="chili")],
        course=[tagging.TagEvidence(value="main", confidence=0.95, evidence="title")],
        protein=[],
        diet=[],
        prep_minutes=None,
        cook_minutes=None,
        total_minutes=None,
        servings=None,
        ingredient_names=[],
    )
    tagging.apply(conn, "r1", result, model="fake")
    tags = {(t["kind"], t["value"]) for t in client.get("/api/recipes/r1").json()["tags"]}
    assert ("course", "main") in tags and ("course", "dessert") not in tags
    assert ("diet", "vegetarian") not in tags
    assert ("cuisine", "Tex-Mex") in tags and ("cuisine", "American") in tags
    removed = [c for c in client.get("/api/changes").json()["items"] if c["after"] is None]
    assert {c["before"] for c in removed} == {"dessert", "vegetarian"}


def test_optional_pin_gate(client, monkeypatch):
    import cookt.app as app_module

    monkeypatch.setattr(app_module.settings.__class__, "pin", property(lambda self: "4321"))
    # TestClient's client host is "testclient", i.e. not loopback
    assert client.get("/api/catalog").status_code == 401
    assert client.post("/pin", data={"pin": "nope"}).status_code == 401
    r = client.post("/pin", data={"pin": "4321"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/catalog").status_code == 200  # cookie now set
