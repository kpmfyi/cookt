"""Copy-edit proposals: validation guards, applying edits, and the Review API (model stubbed)."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

DOC = {
    "schema_version": 2,
    "title": "The BEST Garlic Beans III",
    "yield_text": None,
    "notes": ["My kids love these!", "Cook: 10 mins", "Import warning: something"],
    "ingredient_sections": [
        {
            "id": "ingredients_1",
            "heading": None,
            "ingredients": [
                {"id": "i1", "source_text": "1 lb. green beans"},
                {"id": "i2", "source_text": "2 tbsp. Kerrygold butter"},
                {"id": "i3", "source_text": "Sauce"},
                {"id": "i4", "source_text": "½ cup soy sauce"},
                {"id": "i5", "source_text": "½ cup soy sauce"},
            ],
        }
    ],
    "instruction_sections": [
        {
            "id": "instructions_1",
            "heading": None,
            "steps": [
                {"id": "s1", "text": "Get cooking! Melt butter, add beans, cook 3-4 minutes."},
                {"id": "s2", "text": "Add sauce and enjoy!"},
            ],
        }
    ],
}


def model_output(**overrides):
    base = {
        "score": 2,
        "summary": "Filler and brands.",
        "title": "Garlic Beans",
        "title_reason": "hype words",
        "headings": [{"id": "H2", "text": "Method", "reason": "section heading"}],
        "ingredients": [
            {"id": "I1", "action": "rewrite", "text": "1 pound green beans", "reason": "unit"},
            {
                "id": "I2",
                "action": "rewrite",
                "text": "2 tablespoons unsalted butter",
                "reason": "brand",
            },
            {"id": "I3", "action": "remove", "text": "", "reason": "section name in list"},
            {"id": "I4", "action": "rewrite", "text": "1/2 cup soy sauce", "reason": "fraction"},
            {"id": "I5", "action": "remove", "text": "", "reason": "duplicate line"},
            {"id": "I99", "action": "remove", "text": "", "reason": "unknown id"},
        ],
        "steps": [
            {
                "id": "S1",
                "action": "rewrite",
                "text": (
                    "Melt the butter in a skillet, add the beans and cook until crisp-tender, "
                    "about 4 minutes."
                ),
                "reason": "filler",
            },
            {
                "id": "S2",
                "action": "rewrite",
                "text": "Add sauce and enjoy!",
                "reason": "already clean",
            },
        ],
        "notes": [
            {"index": 0, "action": "remove", "text": "", "reason": "anecdote"},
            {"index": 2, "action": "remove", "text": "", "reason": "protected"},
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKT_RUN_WORKER", "0")
    monkeypatch.setenv("COOKT_LLM_LOCK", str(tmp_path / "llm.lock"))
    import cookt.config

    importlib.reload(cookt.config)
    for name in [
        "cookt.db",
        "cookt.search",
        "cookt.recipes",
        "cookt.planning",
        "cookt.jobs",
        "cookt.imports",
        "cookt.enrich.tagging",
        "cookt.copyedit",
        "cookt.api.intelligence",
        "cookt.api.planning",
        "cookt.api.imports",
        "cookt.api.review",
        "cookt.mcp_server",
        "cookt.pipeline_jobs",
        "cookt.app",
    ]:
        importlib.reload(importlib.import_module(name))
    from cookt import copyedit, db, llm

    conn = db.connect()
    db.init(conn)
    now = db.now()
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, cook_minutes, origin, created_at, "
        "updated_at) "
        "VALUES ('r1', 'garlic-beans', ?, ?, 10, 'test', ?, ?)",
        (DOC["title"], json.dumps(DOC), now, now),
    )
    output = {"value": model_output()}
    monkeypatch.setattr(
        llm,
        "complete_json",
        lambda prompt, schema, name, **kw: schema.model_validate(output["value"]),
    )
    from cookt.app import app

    with TestClient(app) as client:
        yield {"client": client, "conn": conn, "copyedit": copyedit, "output": output, "db": db}


def edits_by_key(conn, db):
    row = conn.execute("SELECT edits FROM copyedit_proposals WHERE recipe_id = 'r1'").fetchone()
    return {e["key"]: e for e in db.loads(row["edits"])}


def test_numbers_normalize():
    from cookt.copyedit import numbers

    assert numbers("1 ½ cups") == numbers("1 1/2 cups")
    assert numbers("six hours") == numbers("6 hours")
    assert numbers("3-4 minutes") != numbers("about 4 minutes")


def test_proposal_guards(env):
    conn, db = env["conn"], env["db"]
    assert env["copyedit"].propose(conn, "r1")["status"] == "pending"
    edits = edits_by_key(conn, db)
    assert edits["title"]["after"] == "Garlic Beans"
    assert "ing:I99" not in edits and "ing:i99" not in edits  # unknown id dropped
    assert "ing:i4" not in edits  # ½ -> 1/2 is not an edit
    assert "step:s2" not in edits  # "already clean" is not an edit
    assert edits["ing:i3"]["op"] == "heading" and edits["ing:i3"]["after"] == "Sauce"
    assert edits["ing:i5"]["op"] == "remove"
    assert edits["step:s1"]["numbers_changed"] is True
    assert edits["note:0"]["op"] == "remove"
    assert edits["note:1"]["reason"] == "repeats the recipe's time fields"  # cook_minutes is set
    assert "note:2" not in edits  # import warnings are protected
    assert "heading:instructions_1" not in edits  # sole section needs no heading


def test_approve_subset_then_undo(env):
    client, conn = env["client"], env["conn"]
    env["copyedit"].propose(conn, "r1")
    items = client.get("/api/review").json()["items"]
    assert items[0]["status"] == "pending" and items[0]["score"] == 2

    accepted = ["title", "ing:i2", "ing:i3", "ing:i5"]
    assert client.post("/api/review/r1/approve", json={"accepted": accepted}).json()["applied"] == 4
    recipe = client.get("/api/recipes/r1").json()
    doc = recipe["document"]
    assert recipe["title"] == "Garlic Beans"
    assert [s["heading"] for s in doc["ingredient_sections"]] == [None, "Sauce"]
    assert [i["source_text"] for i in doc["ingredient_sections"][0]["ingredients"]] == [
        "1 lb. green beans",  # not accepted
        "2 tablespoons unsalted butter",
    ]
    assert [i["source_text"] for i in doc["ingredient_sections"][1]["ingredients"]] == [
        "½ cup soy sauce"
    ]
    assert doc["notes"] == DOC["notes"]
    assert recipe["revisions"][0]["reason"] == "copy edit"

    assert client.post("/api/review/r1/undo", json={}).json()["ok"]
    recipe = client.get("/api/recipes/r1").json()
    assert recipe["document"]["title"] == DOC["title"]
    assert client.get("/api/review").json()["items"][0]["status"] == "pending"


def test_stale_proposal_is_refused(env):
    client, conn = env["client"], env["conn"]
    env["copyedit"].propose(conn, "r1")
    doc = json.loads(json.dumps(DOC))
    doc["ingredient_sections"][0]["ingredients"][1]["source_text"] = "2 tablespoons butter"
    client.put("/api/recipes/r1", json={"document": doc})
    assert client.get("/api/review").json()["items"][0]["stale"] is True
    response = client.post("/api/review/r1/approve", json={"accepted": ["ing:i2"]})
    assert response.status_code == 409
    # Edits whose lines are untouched still apply.
    assert client.post("/api/review/r1/approve", json={"accepted": ["ing:i5"]}).status_code == 200


def test_delete_archives_and_undo_restores(env):
    client = env["client"]
    assert client.post("/api/review/garlic-beans/delete").json()["ok"]
    assert client.get("/api/catalog").json()["recipes"] == []
    assert client.get("/api/recipes/r1").status_code == 404
    items = client.get("/api/review").json()["items"]
    assert items[0]["status"] == "deleted"
    client.post("/api/review/r1/undo", json={})
    assert len(client.get("/api/catalog").json()["recipes"]) == 1
    assert client.get("/api/review").json()["items"][0]["status"] == "nochange"


def test_runner_does_not_overwrite_decisions(env):
    conn, copyedit = env["conn"], env["copyedit"]
    copyedit.propose(conn, "r1")
    copyedit.reject(conn, "r1")
    copyedit.propose(conn, "r1")
    status = conn.execute("SELECT status FROM copyedit_proposals").fetchone()[0]
    assert status == "rejected"


def test_empty_recipe_is_flagged_without_model(env, monkeypatch):
    conn, copyedit, db = env["conn"], env["copyedit"], env["db"]
    doc = json.loads(json.dumps(DOC))
    doc["ingredient_sections"][0]["ingredients"] = [
        {"id": "i1", "source_text": "Ingredients were not included in the original export."}
    ]
    conn.execute("UPDATE recipes SET document = ? WHERE id = 'r1'", (json.dumps(doc),))
    from cookt import llm

    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("model called"))
    assert copyedit.propose(conn, "r1")["status"] == "nochange"
    row = conn.execute("SELECT flags FROM copyedit_proposals").fetchone()
    assert db.loads(row["flags"]) == ["empty"]
