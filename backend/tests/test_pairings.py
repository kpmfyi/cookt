"""Pairings: cuisine-aware "goes with" and same-course, same-cuisine "similar"."""

from __future__ import annotations

import json

import pytest
from cookt import db
from cookt.enrich import pairings


def _doc(title: str, lines: list[str]) -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "title": title,
            "notes": [],
            "ingredient_sections": [
                {
                    "id": "ingredients_1",
                    "ingredients": [
                        {"id": f"i{n}", "source_text": line} for n, line in enumerate(lines, 1)
                    ],
                }
            ],
            "instruction_sections": [
                {"id": "instructions_1", "steps": [{"id": "s1", "text": "Cook."}]}
            ],
        }
    )


RECIPES = [
    # id, title, course, cuisine, ingredients, features
    (
        "mapo",
        "Mapo Tofu",
        "main",
        "Chinese",
        ["tofu", "chili bean paste", "soy sauce"],
        {"richness": 2, "texture": ["saucy", "silky"], "flavors": ["spicy", "umami"]},
    ),
    (
        "bokchoy",
        "Garlicky Bok Choy",
        "side",
        "Chinese",
        ["bok choy", "garlic", "soy sauce"],
        {"richness": 1, "flavors": ["savory"]},
    ),
    (
        "hummus",
        "Hummus",
        "sauce/condiment",
        "Middle Eastern",
        ["chickpeas", "tahini"],
        {"richness": 2, "flavors": ["savory"]},
    ),
    (
        "greek",
        "Greek Salad",
        "salad",
        "Greek",
        ["feta", "kalamata olives", "cucumber"],
        {"richness": 1, "acidity": 2, "flavors": ["bright"]},
    ),
    (
        "slaw",
        "Crunchy Slaw",
        "salad",
        "American",
        ["cabbage", "kimchi", "sesame oil"],
        {"richness": 1, "flavors": ["tangy", "savory"]},
    ),
    (
        "green",
        "Green Salad",
        "salad",
        "American",
        ["lettuce", "olive oil", "lemon"],
        {"richness": 0, "flavors": ["fresh"]},
    ),
    (
        "banana",
        "Miso Banana Bread",
        "bread",
        "American",
        ["banana", "miso", "sugar"],
        {"richness": 2, "flavors": ["sweet"]},
    ),
    (
        "paste",
        "Green Curry Paste",
        "sauce/condiment",
        "Thai",
        ["lemongrass", "galangal"],
        {"flavors": ["spicy"]},
    ),
    (
        "cookies",
        "Sugar Cookies",
        "dessert",
        "American",
        ["flour", "butter", "sugar"],
        {"richness": 2, "flavors": ["sweet"]},
    ),
    (
        "chili",
        "Beef Chili",
        "main",
        "Tex-Mex",
        ["beef", "chipotle", "ancho chiles"],
        {"richness": 2, "flavors": ["spicy", "smoky"]},
    ),
    (
        "sesametofu",
        "Sticky Sesame Tofu",
        "main",
        "Asian",
        ["tofu", "sesame oil", "soy sauce"],
        {"richness": 1, "flavors": ["sweet", "umami"]},
    ),
    (
        "stroganoff",
        "Beef Stroganoff",
        "main",
        "American",
        ["beef", "sour cream", "soy sauce", "fish sauce"],
        {"richness": 3, "flavors": ["rich"]},
    ),
    (
        "bokchoy2",
        "Bok Choy Stir-Fry",
        "side",
        "Chinese",
        ["bok choy", "soy sauce"],
        {"richness": 1, "flavors": ["savory"]},
    ),
]


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(pairings.search, "recipe_vectors", lambda conn: {})
    c = db.connect(tmp_path / "t.db")
    db.init(c)
    now = db.now()
    for rid, title, course, cuisine, lines, features in RECIPES:
        c.execute(
            "INSERT INTO recipes(id, slug, title, document, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'test', ?, ?)",
            (rid, rid, title, _doc(title, lines), now, now),
        )
        for kind, value in (("course", course), ("cuisine", cuisine)):
            c.execute(
                "INSERT INTO recipe_tags(recipe_id, kind, value, source, created_at) "
                "VALUES (?, ?, ?, 'manual', ?)",
                (rid, kind, value, now),
            )
        c.execute(
            "INSERT INTO recipe_features(recipe_id, features, created_at) VALUES (?, ?, ?)",
            (rid, json.dumps(features), now),
        )
    return c


def ids(items):
    return [item["id"] for item in items]


def test_goes_with_stays_in_cuisine_or_plain(conn):
    meal = ids(pairings.suggest(conn, "mapo")["build_a_meal"])
    assert "bokchoy" in meal
    assert "slaw" in meal  # tagged American, but kimchi + sesame oil read East Asian
    assert "green" in meal or "cookies" in meal  # plain dishes can go with anything
    for clash in ("hummus", "greek", "chili"):
        assert clash not in meal
    assert "banana" not in meal  # sweet bread beside a savory main
    assert "paste" not in meal  # a component, not a dish


def test_one_borrowed_ingredient_does_not_change_cuisine(conn):
    meal = ids(pairings.suggest(conn, "stroganoff")["build_a_meal"])
    assert "bokchoy" not in meal  # soy + fish sauce in stroganoff don't make it Asian
    assert "green" in meal


def test_similar_is_same_course_and_cuisine(conn):
    similar = ids(pairings.suggest(conn, "mapo")["more_like_this"])
    assert "sesametofu" in similar  # "Asian" covers Chinese
    assert "chili" not in similar and "stroganoff" not in similar  # clash
    assert "bokchoy" not in similar  # a side, not a main


def test_similar_side(conn):
    similar = ids(pairings.suggest(conn, "bokchoy")["more_like_this"])
    assert similar == ["bokchoy2"]
