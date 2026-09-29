"""LLM feature profiles for deterministic pairing (computed at enrichment time only)."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import db, llm
from ..config import settings

PROMPT_VERSION = "features-v2"  # v2: saucy/brothy/stewy textures, dish-family nouns
Texture = Literal[
    "creamy",
    "crisp",
    "crunchy",
    "tender",
    "chewy",
    "saucy",
    "soft",
    "flaky",
    "silky",
    "hearty",
    "brothy",
    "juicy",
    "stewy",
]
Flavor = Literal[
    "rich",
    "bright",
    "acidic",
    "spicy",
    "smoky",
    "sweet",
    "savory",
    "herbal",
    "earthy",
    "fresh",
    "salty",
    "bitter",
    "umami",
    "tangy",
]


class Features(BaseModel):
    model_config = ConfigDict(extra="forbid")
    richness: int = Field(ge=0, le=3, description="0 lean .. 3 very rich (fat, cream, cheese)")
    acidity: int = Field(ge=0, le=3, description="0 none .. 3 sharp (citrus, vinegar, pickles)")
    weight: Literal["light", "medium", "heavy"]
    texture: list[Texture] = Field(max_length=4)
    flavors: list[Flavor] = Field(max_length=5)
    dish_family: str = Field(
        max_length=40,
        description="short family noun, e.g. braise, stir-fry, "
        "taco, pasta, curry, soup, salad, roast, cookie, cake, flatbread",
    )


def profile(conn: sqlite3.Connection, recipe_id: str) -> Features:
    row = conn.execute("SELECT title, document FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    document = db.loads(row["document"])
    ingredients = "\n".join(
        i["source_text"] for s in document["ingredient_sections"] for i in s["ingredients"]
    )
    steps = " ".join(st["text"] for s in document["instruction_sections"] for st in s["steps"])
    text = f"TITLE: {row['title']}\nINGREDIENTS:\n{ingredients}\nMETHOD: {steps[:3000]}"
    return llm.complete_json(
        "Describe how this finished dish eats, for pairing it with other dishes: richness, "
        "acidity, overall weight, dominant textures and flavours, and its dish family.\n"
        "Textures: include 'saucy' whenever the dish is served in or coated with a sauce "
        "(mapo tofu, curries, braises in their liquid, saucy stir-fries, pasta in sauce), "
        "'brothy' for soups and anything in broth, 'stewy' for stews and chili. List the textures "
        "you'd notice first, up to 4.\n"
        "Dish family: a plain noun for the kind of dish (stir-fry, braise, curry, noodle soup, "
        "fried rice, taco, pasta, roast, salad, dumpling, cookie), never a cuisine name.\n\n"
        + llm.untrusted(text),
        Features,
        "dish_features",
        max_tokens=800,
    )


def save(conn: sqlite3.Connection, recipe_id: str, features: Features) -> None:
    stamp = db.now()
    conn.execute(
        "INSERT INTO recipe_features(recipe_id, features, model, prompt_version, created_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_id) DO UPDATE SET features = excluded.features,"
        " model = excluded.model, prompt_version = excluded.prompt_version, "
        "created_at = excluded.created_at",
        (recipe_id, db.dumps(features.model_dump()), settings.text_model, PROMPT_VERSION, stamp),
    )
