"""Nutrition pipeline tests. The local model is faked via dependency injection."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cookt import db
from cookt.enrich import fdc, nutrition
from cookt.enrich.nutrition import (
    MatchResponse,
    ParseResponse,
    badge,
    compute_nutrition,
    parse_publisher_nutrition,
    save_nutrition,
    set_override,
)

ROOT = Path(__file__).resolve().parents[2]

# Real SR Legacy ids (so the curated alias table resolves) with their per-100 g values.
FOODS = [
    # fdc_id, data_type, description, category, kcal, protein, carbs, fat, fiber, sodium
    (
        168894,
        "sr_legacy_food",
        "Wheat flour, white, all-purpose, enriched, bleached",
        "Cereal Grains and Pasta",
        364,
        10.33,
        76.31,
        0.98,
        2.7,
        2,
    ),
    (
        173430,
        "sr_legacy_food",
        "Butter, without salt",
        "Dairy and Egg Products",
        717,
        0.85,
        0.06,
        81.11,
        0,
        11,
    ),
    (
        171287,
        "sr_legacy_food",
        "Egg, whole, raw, fresh",
        "Dairy and Egg Products",
        143,
        12.56,
        0.72,
        9.51,
        0,
        142,
    ),
    (173468, "sr_legacy_food", "Salt, table", "Spices and Herbs", 0, 0, 0, 0, 0, 38758),
    (
        169230,
        "sr_legacy_food",
        "Garlic, raw",
        "Vegetables and Vegetable Products",
        149,
        6.36,
        33.06,
        0.5,
        2.1,
        17,
    ),
    (
        170000,
        "sr_legacy_food",
        "Onions, raw",
        "Vegetables and Vegetable Products",
        40,
        1.1,
        9.34,
        0.1,
        1.7,
        4,
    ),
    (
        999001,
        "sr_legacy_food",
        "Sauce, gochujang (fermented chili paste)",
        "Soups, Sauces, and Gravies",
        200,
        5,
        40,
        2,
        3,
        3000,
    ),
]
PORTIONS = [
    # fdc_id, amount, unit, modifier, description, grams
    (168894, 1, "undetermined", "cup", None, 125),
    (173430, 1, "undetermined", "cup", None, 227),
    (173430, 1, "undetermined", "stick", None, 113),
    (171287, 1, "undetermined", "large", None, 50),
    (171287, 1, "undetermined", "medium", None, 44),
    (169230, 1, "undetermined", "clove", None, 3),
    (169230, 1, "undetermined", "cup", None, 136),
    (170000, 1, "undetermined", 'medium (2-1/2" dia)', None, 110),
    (170000, 1, "undetermined", "cup, chopped", None, 160),
    (170000, 1, "undetermined", "cup, sliced", None, 115),
]


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "t.db")
    db.init(connection)
    connection.executemany("INSERT INTO fdc_food VALUES (?,?,?,?,?,?,?,?,?,?)", FOODS)
    connection.executemany(
        "INSERT INTO fdc_portion(fdc_id, amount, unit, modifier, description, gram_weight)"
        " VALUES (?,?,?,?,?,?)",
        PORTIONS,
    )
    connection.execute(
        "INSERT INTO fdc_food_fts(rowid, description, category)"
        " SELECT fdc_id, description, category FROM fdc_food"
    )
    yield connection
    connection.close()


# Canned model output per ingredient line.
PARSES = {
    "2 cups all-purpose flour": (2, "cup", "all-purpose flour", 250, False, None),
    "1 cup unsalted butter, softened": (1, "cup", "unsalted butter", 227, False, None),
    "2 large eggs": (2, "large", "egg", 100, False, None),
    "Salt to taste": (None, None, "salt", None, True, "to taste"),
    "1 tbsp gochujang": (1, "tbsp", "gochujang", 17, False, None),
    "3 cloves garlic, minced": (3, "clove", "garlic", 9, False, None),
    "1 onion, diced": (1, None, "onion", 110, False, None),
    "1 tsp kosher salt": (1, "tsp", "kosher salt", 6, False, None),
    "Oil, for frying": (None, None, "vegetable oil", None, True, "no amount"),
}


class FakeLLM:
    def __init__(self, pick: int = 1, quality: str = "exact"):
        self.calls: list[str] = []
        self.pick = pick
        self.quality = quality

    def __call__(self, prompt, schema, name):
        self.calls.append(name)
        if schema is ParseResponse:
            lines = []
            for number, text in re.findall(r"^(\d+)\. (.+)$", prompt, flags=re.M):
                q, u, food, approx, exclude, reason = PARSES[text.strip()]
                lines.append(
                    {
                        "i": int(number),
                        "quantity": q,
                        "unit": u,
                        "food": food,
                        "approx_grams": approx,
                        "exclude": exclude,
                        "exclude_reason": reason,
                    }
                )
            return ParseResponse.model_validate({"lines": lines})
        if schema is MatchResponse:
            numbers = [int(n) for n in re.findall(r"^Line (\d+):", prompt, flags=re.M)]
            return MatchResponse.model_validate(
                {
                    "choices": [
                        {"i": n, "choice": self.pick, "quality": self.quality} for n in numbers
                    ]
                }
            )
        raise AssertionError(f"unexpected schema {schema}")


def add_recipe(conn, lines, servings=4.0, recipe_id="r1", yield_text=None):
    document = {
        "schema_version": 2,
        "title": "Test",
        "yield_text": yield_text,
        "ingredient_sections": [
            {
                "id": "ing",
                "ingredients": [
                    {"id": f"i{n}", "source_text": text} for n, text in enumerate(lines, 1)
                ],
            }
        ],
        "instruction_sections": [{"id": "ins", "steps": [{"id": "s1", "text": "Cook."}]}],
    }
    now = db.now()
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, servings, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (recipe_id, recipe_id, "Test", json.dumps(document), servings, now, now),
    )
    return recipe_id


# --- publisher ---------------------------------------------------------------------------


def test_parse_publisher_nutrition_strings():
    block = {
        "@type": "NutritionInformation",
        "calories": "250 kcal",
        "proteinContent": "12 g",
        "carbohydrateContent": "30.5g",
        "fatContent": "10 grams",
        "fiberContent": "2 g",
        "sodiumContent": "480 mg",
        "servingSize": "1 slice",
    }
    result = parse_publisher_nutrition(block, 8)
    assert result["kcal"] == 250
    assert result["protein_g"] == 12
    assert result["carbs_g"] == 30.5
    assert result["fat_g"] == 10
    assert result["fiber_g"] == 2
    assert result["sodium_mg"] == 480
    assert result["servings"] == 8


def test_parse_publisher_nutrition_variants():
    assert parse_publisher_nutrition({"calories": "1,250 calories"}, 4)["kcal"] == 1250
    assert parse_publisher_nutrition({"calories": 312.4}, 4)["kcal"] == 312
    assert parse_publisher_nutrition({"calories": "1046 kJ"}, 4)["kcal"] == 250
    grams_sodium = parse_publisher_nutrition({"calories": "100", "sodiumContent": "0.5 g"}, 1)
    assert grams_sodium["sodium_mg"] == 500
    assert parse_publisher_nutrition({"calories": "100", "fatContent": "500 mg"}, 1)["fat_g"] == 0.5
    assert parse_publisher_nutrition({"proteinContent": "5 g"}, 4) is None
    assert parse_publisher_nutrition({"calories": ""}, 4) is None
    assert parse_publisher_nutrition("not a dict", 4) is None  # type: ignore[arg-type]


# --- grams conversion --------------------------------------------------------------------


def test_grams_standard_mass(conn):
    grams, method = fdc.grams_for(conn, 168894, 8, "ounces", "8 oz flour")
    assert grams == pytest.approx(226.8, abs=0.1)
    assert "standard mass" in method
    assert fdc.grams_for(conn, 168894, 1, "kg", "1 kg flour")[0] == 1000


def test_grams_volume_via_portion_and_scaling(conn):
    assert fdc.grams_for(conn, 168894, 2, "cups", "2 cups flour")[0] == pytest.approx(250)
    tbsp, method = fdc.grams_for(conn, 173430, 2, "tbsp", "2 tbsp butter")
    assert tbsp == pytest.approx(227 / 8, rel=0.01)  # 16 tbsp per cup
    assert "scaled portion" in method


def test_grams_volume_prefers_matching_form(conn):
    chopped = fdc.grams_for(conn, 170000, 1, "cup", "1 cup chopped onion")[0]
    sliced = fdc.grams_for(conn, 170000, 1, "cup", "1 cup thinly sliced onion")[0]
    assert chopped == 160
    assert sliced == 115


def test_grams_count_units(conn):
    assert fdc.grams_for(conn, 169230, 3, "cloves", "3 cloves garlic")[0] == 9
    assert fdc.grams_for(conn, 173430, 1, "stick", "1 stick butter")[0] == 113
    assert fdc.grams_for(conn, 171287, 2, "large", "2 large eggs")[0] == 100
    # a bare egg count means large eggs; a bare onion count means medium
    assert fdc.grams_for(conn, 171287, 3, None, "3 eggs")[0] == 150
    assert fdc.grams_for(conn, 170000, 1, None, "1 onion")[0] == 110


def test_grams_returns_none_rather_than_guessing(conn):
    assert fdc.grams_for(conn, 170000, 1, "can", "1 can onions") is None
    assert fdc.grams_for(conn, 168894, 1, "handful", "a handful of flour") is None
    assert fdc.grams_for(conn, 168894, None, "cup", "flour") is None
    assert fdc.grams_for(conn, 999001, 1, "tbsp", "1 tbsp gochujang") is None  # no portions


def test_search_foods_fts(conn):
    results = fdc.search_foods(conn, "diced onions", limit=5)
    assert results and results[0]["fdc_id"] == 170000


def test_alias_lookup_normalizes():
    assert fdc.lookup_alias("Large Eggs") == fdc.lookup_alias("egg")
    assert fdc.lookup_alias("garlic cloves") == fdc.lookup_alias("garlic")
    assert fdc.lookup_alias("chicken thighs, boneless skinless") == fdc.lookup_alias(
        "boneless skinless chicken thigh"
    )
    assert fdc.lookup_alias("fresh basil") == fdc.lookup_alias("basil")
    assert fdc.lookup_alias("gochujang") is None


# --- pipeline ----------------------------------------------------------------------------

LINES = [
    "2 cups all-purpose flour",
    "1 cup unsalted butter, softened",
    "2 large eggs",
    "Salt to taste",
    "1 tbsp gochujang",
]


def test_compute_sums_and_excludes(conn):
    add_recipe(conn, LINES, servings=4)
    llm = FakeLLM(pick=0, quality="none")  # gochujang: model finds no match
    result = compute_nutrition(conn, "r1", llm=llm)
    assert result["source"] == "computed"
    lines = {item["source_text"]: item for item in result["breakdown"]}

    salt = lines["Salt to taste"]
    assert salt["excluded"] and salt["reason"] == "to taste" and salt["kcal"] is None

    goch = lines["1 tbsp gochujang"]
    assert goch["excluded"] and goch["reason"] == "no FDC match"

    flour = lines["2 cups all-purpose flour"]
    assert flour["fdc_id"] == 168894 and flour["grams"] == 250 and flour["kcal"] == 910
    assert flour["confidence"] == "high" and flour["match"] == "alias"

    expected_kcal = (250 * 364 + 227 * 717 + 100 * 143) / 100 / 4
    assert result["per_serving"]["kcal"] == round(expected_kcal)
    assert result["servings"] == 4 and result["servings_assumed"] is False
    # matched mass 577 g of 577 + 17 (gochujang estimate) = 97.1 %
    assert result["mass_matched_pct"] == pytest.approx(97.1, abs=0.1)
    assert result["confidence"] == "high"
    # every number is traceable to a line
    included = [i for i in result["breakdown"] if not i["excluded"]]
    assert sum(i["kcal"] for i in included) == pytest.approx(result["totals"]["kcal"], abs=2)


def test_one_parse_call_per_recipe_and_cache(conn):
    add_recipe(conn, LINES)
    llm = FakeLLM()
    compute_nutrition(conn, "r1", llm=llm)
    assert llm.calls == ["parse", "match"]  # only gochujang needs a model pick
    again = FakeLLM()
    compute_nutrition(conn, "r1", llm=again)
    assert again.calls == []  # parse + pick cached per source_text


def test_model_pick_confidence(conn):
    add_recipe(conn, ["1 onion, diced", "1 tbsp gochujang"])
    result = compute_nutrition(conn, "r1", llm=FakeLLM(pick=1, quality="close"))
    lines = {item["source_text"]: item for item in result["breakdown"]}
    onion = lines["1 onion, diced"]
    # alias match, but size not stated -> FDC default "medium" portion -> medium confidence
    assert onion["grams"] == 110 and onion["confidence"] == "medium"
    goch = lines["1 tbsp gochujang"]
    # model picked the chili paste but FDC has no tbsp portion: model grams estimate, low
    assert goch["fdc_id"] == 999001 and goch["confidence"] == "low"
    assert goch["grams_method"].startswith("model estimate")
    # low-confidence grams do not count as matched mass: 110 / (110 + 17)
    assert result["mass_matched_pct"] == pytest.approx(86.6, abs=0.1)
    assert result["confidence"] == "medium"


def test_overrides_persist_and_apply(conn):
    add_recipe(conn, LINES)
    llm = FakeLLM(pick=0, quality="none")
    base = compute_nutrition(conn, "r1", llm=llm)
    set_override(conn, "r1", "i2", None)  # exclude the butter
    set_override(conn, "r1", "i5", 999001, grams=20)  # map gochujang, 20 g
    result = compute_nutrition(conn, "r1", llm=llm)
    lines = {item["ingredient_id"]: item for item in result["breakdown"]}
    assert lines["i2"]["excluded"] and lines["i2"]["reason"] == "excluded by user"
    assert lines["i5"]["fdc_id"] == 999001 and lines["i5"]["grams"] == 20
    assert lines["i5"]["overridden"] and lines["i5"]["kcal"] == 40
    assert lines["i2"]["kcal"] is None  # excluded lines carry no numbers
    assert base["totals"]["kcal"] > result["totals"]["kcal"]
    assert result["per_serving"]["kcal"] == round((250 * 364 + 100 * 143 + 20 * 200) / 100 / 4)
    rows = conn.execute("SELECT COUNT(*) FROM nutrition_overrides").fetchone()[0]
    assert rows == 2


def test_override_to_new_food_uses_portions(conn):
    add_recipe(conn, ["3 cloves garlic, minced"])
    llm = FakeLLM()
    set_override(conn, "r1", "i1", 170000)  # user says: that was onion, not garlic
    result = compute_nutrition(conn, "r1", llm=llm)
    item = result["breakdown"][0]
    assert item["fdc_id"] == 170000 and item["overridden"]
    # onion has no "clove" portion -> model's weight estimate
    assert item["grams"] == 9 and item["confidence"] == "low"


def test_publisher_nutrition_wins_and_breakdown_is_secondary(conn):
    add_recipe(conn, LINES)
    conn.execute(
        "INSERT INTO source_nutrition(recipe_id, data, created_at) VALUES (?,?,?)",
        ("r1", json.dumps({"calories": "500 kcal", "fatContent": "20 g"}), db.now()),
    )
    result = compute_nutrition(conn, "r1", llm=FakeLLM())
    assert result["source"] == "publisher"
    assert result["per_serving"]["kcal"] == 500 and result["per_serving"]["fat_g"] == 20
    assert result["confidence"] is None
    assert result["computed_per_serving"]["kcal"] > 0
    save_nutrition(conn, "r1", result)
    row = conn.execute("SELECT * FROM nutrition WHERE recipe_id = 'r1'").fetchone()
    assert row["source"] == "publisher"
    stored = json.loads(row["breakdown"])
    assert stored["version"] == nutrition.current_version() and len(stored["lines"]) == 5


def test_missing_servings_falls_back_and_flags(conn):
    add_recipe(conn, ["2 cups all-purpose flour"], servings=None)
    result = compute_nutrition(conn, "r1", llm=FakeLLM())
    assert result["servings"] == 4 and result["servings_assumed"] is True
    assert result["per_serving"]["kcal"] == round(910 / 4)


def test_servings_from_yield_text(conn):
    add_recipe(conn, ["2 cups all-purpose flour"], servings=None, yield_text="Makes 10 cookies")
    result = compute_nutrition(conn, "r1", llm=FakeLLM())
    assert result["servings"] == 10 and result["servings_assumed"] is False


def test_confidence_badge_thresholds():
    assert badge(95) == "high"
    assert badge(90) == "high"
    assert badge(89.9) == "medium"
    assert badge(70) == "medium"
    assert badge(69.9) == "low"
    assert badge(None) == "low"


def test_badge_low_when_most_mass_unmatched(conn):
    add_recipe(conn, ["1 tbsp gochujang", "3 cloves garlic, minced"])
    result = compute_nutrition(conn, "r1", llm=FakeLLM(pick=0, quality="none"))
    # 9 g matched of 9 + 17
    assert result["confidence"] == "low"


def test_backfill_skips_up_to_date(conn):
    add_recipe(conn, ["2 cups all-purpose flour"], recipe_id="a")
    add_recipe(conn, ["3 cloves garlic, minced"], recipe_id="b")
    stats = nutrition.backfill(conn, llm=FakeLLM())
    assert stats == {"computed": 2, "skipped": 0, "failed": 0}
    stats = nutrition.backfill(conn, llm=FakeLLM())
    assert stats == {"computed": 0, "skipped": 2, "failed": 0}
    stats = nutrition.backfill(conn, llm=FakeLLM(), recipe_id="a", force=True)
    assert stats["computed"] == 1


def test_llm_schemas_are_strict():
    for schema in (ParseResponse, MatchResponse):
        js = schema.model_json_schema()
        assert js["additionalProperties"] is False


# --- curated aliases vs the real FDC data ------------------------------------------------


def _fdc_db(tmp_path_factory) -> Path | None:
    fdc_dir = ROOT / "data" / "fdc"
    if not list(fdc_dir.glob("FoodData_Central_sr_legacy_food_csv_*.zip")):
        return None
    from cookt.enrich.fdc_load import load

    path = tmp_path_factory.mktemp("fdc") / "fdc.db"
    connection = db.connect(path)
    load(connection, fdc_dir)
    connection.close()
    return path


def test_alias_ids_exist_in_fdc_data(tmp_path_factory):
    path = _fdc_db(tmp_path_factory)
    if path is None:
        pytest.skip("FDC zips not downloaded (see docs/fdc.md)")
    connection = db.connect(path)
    data = json.loads(fdc.ALIASES_PATH.read_text())
    ids = set(data["aliases"].values())
    assert len(data["aliases"]) >= 150
    rows = {
        r["fdc_id"]: r["description"]
        for r in connection.execute(
            f"SELECT fdc_id, description FROM fdc_food WHERE kcal IS NOT NULL AND fdc_id IN"
            f" ({','.join('?' * len(ids))})",
            list(ids),
        )
    }
    missing = ids - set(rows)
    assert not missing, f"alias ids not in FDC data: {sorted(missing)}"
    for fdc_id, description in data["foods"].items():
        assert rows[int(fdc_id)] == description


def test_refuses_to_compute_without_fdc_data(tmp_path):
    empty = db.connect(tmp_path / "empty.db")
    db.init(empty)
    add_recipe(empty, ["2 cups all-purpose flour"])
    with pytest.raises(RuntimeError, match="FDC data not loaded"):
        compute_nutrition(empty, "r1", llm=FakeLLM())
    assert empty.execute("SELECT COUNT(*) FROM ingredient_parse_cache").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("text", "model", "expected"),
    [
        ("3/4 cup sugar", (0.5, "cup"), 0.75),  # model misread the fraction: text wins
        ("1 1/3 cups flour", (1.3, "cup"), 4 / 3),
        ("2-3 cloves garlic", (2, "clove"), 2.5),
        ("1½ teaspoons salt", (1, "tsp"), 1.5),
        ("2 large eggs", (3, None), 2),
        ("1 (14.5 oz) can tomatoes", (14.5, "oz"), 14.5),  # package size: model wins
        ("1 cup (120 g) flour", (120, "g"), 120),  # weight preferred: model wins
    ],
)
def test_reconcile_quantity(text, model, expected):
    parse = {"quantity": model[0], "unit": model[1], "food": "x"}
    assert nutrition.reconcile_quantity(text, parse)["quantity"] == pytest.approx(expected)
