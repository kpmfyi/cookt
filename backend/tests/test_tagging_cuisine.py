"""Cuisine tagging: vague answers ("Asian") get a second, enum-constrained pick."""

from __future__ import annotations

import json

from cookt import db
from cookt.enrich import tagging

DOC = {
    "schema_version": 2,
    "title": "Sticky Sesame Tofu",
    "notes": [],
    "ingredient_sections": [
        {"id": "ingredients_1", "ingredients": [{"id": "i1", "source_text": "1 block tofu"}]}
    ],
    "instruction_sections": [{"id": "instructions_1", "steps": [{"id": "s1", "text": "Fry."}]}],
}


def _result(cuisine):
    return tagging.TagResult(
        cuisine=cuisine,
        course=[],
        protein=[],
        diet=[],
        prep_minutes=None,
        cook_minutes=None,
        total_minutes=None,
        servings=None,
        ingredient_names=[],
    )


def test_vague_cuisine_gets_a_specific_pick(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    now = db.now()
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, origin, created_at, updated_at) "
        "VALUES ('r1', 'tofu', 'Sticky Sesame Tofu', ?, 'test', ?, ?)",
        (json.dumps(DOC), now, now),
    )
    calls = []

    def fake(prompt, schema, name, **kw):
        calls.append(name)
        if name == "recipe_tags":
            vague = tagging.TagEvidence(value="Asian", confidence=0.9, evidence="soy sauce")
            return _result([vague])
        choices = schema.model_json_schema()["properties"]["cuisine"]["enum"]
        assert "Asian" not in choices and "Chinese" in choices and "none" in choices
        return schema(cuisine="Chinese", second="none", confidence=0.9, evidence="sesame, soy")

    monkeypatch.setattr(tagging.llm, "complete_json", fake)
    result = tagging.classify(conn, "r1")
    assert calls == ["recipe_tags", "cuisine_pick"]
    assert [c.value for c in result.cuisine] == ["Chinese"]


def test_generic_cuisines_are_never_assigned():
    assert tagging._normalize("cuisine", "Asian", ["Asian", "Chinese"]) is None
    assert tagging._normalize("cuisine", "European", []) is None
    assert tagging._normalize("cuisine", "korean", ["Korean"]) == "Korean"
