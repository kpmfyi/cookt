"""Pure-function tests for the A (recipe-table) + B (cookt2) migration."""

from __future__ import annotations

import pytest
from cookt.document import RecipeDocumentV2
from cookt.migrate import images as IMG
from cookt.migrate import normalize as N
from cookt.migrate.match import info_for, match, within_source_duplicates

# ---------------------------------------------------------------------------- canonical URL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "http://www.SeriousEats.com/foo-recipe/?utm_source=x&utm_medium=y#recipe",
            "https://seriouseats.com/foo-recipe",
        ),
        (
            "https://cooking.nytimes.com/recipes/123-x?smid=ck-recipe-iOS-share",
            "https://cooking.nytimes.com/recipes/123-x",
        ),
        (
            "https://example.com/r?fbclid=1&gclid=2&mc_cid=3&mc_eid=4&ref=home&id=7",
            "https://example.com/r?id=7",
        ),
        ("https://example.com/r?b=2&a=1", "https://example.com/r?a=1&b=2"),
        ("https://example.com:443/a//b/", "https://example.com/a/b"),
        (
            "https://easyindiancookbook.com/pumpkin-chickpea-curry/#recipe",
            "https://easyindiancookbook.com/pumpkin-chickpea-curry",
        ),
        ("urn:recipe-table:html:abc", None),
        ("", None),
        (None, None),
    ],
)
def test_canonical_url(raw, expected):
    assert N.canonical_url(raw) == expected


def test_canonical_url_equates_variants():
    a = N.canonical_url("https://www.themediterraneandish.com/greek-salad/")
    b = N.canonical_url("http://themediterraneandish.com/greek-salad?utm_campaign=z")
    assert a == b


# ---------------------------------------------------------------------------- times


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("30 mins", 30),
        ("1 hr 15 min", 75),
        ("1 hour 30 minutes", 90),
        ("2 hrs 5 mins", 125),
        ("1 1/2 hours", 90),
        ("1½ hours", 90),
        ("10mins", 10),
        ("2 h", 120),
        ("1 day 1 hour 45 minutes", 1545),
        ("20", 20),
        ("45 minutes total time", 45),
        ("400 degrees for 20 minutes", None),
        ("this sauce only requires 10 minutes of simmering", None),
        ("0 mins", None),
    ],
)
def test_parse_duration_minutes(text, minutes):
    assert N.parse_duration_minutes(text) == minutes


def test_parse_times_from_notes():
    parsed = N.parse_times(
        [
            "Prep: 30 mins",
            "Cook Time: 1 hr 15 min",
            "Total: 45 minutes",
            "Cook's Tip for Rice: rinse it well for 10 minutes.",
            "Prep: 5 mins",  # second prep line is ignored
        ]
    )
    assert (parsed.prep, parsed.cook, parsed.total) == (30, 75, 45)
    assert parsed.evidence["cook"] == "Cook Time: 1 hr 15 min"


def test_parse_times_ready_in_and_flags():
    parsed = N.parse_times(
        ["Ready in about 20 minutes", "Cook: 400 degrees for 20 minutes", "Prep: 10"]
    )
    assert parsed.total == 20
    assert parsed.cook is None
    assert parsed.prep == 10
    assert any("unparsed" in f for f in parsed.flags)
    assert any("assumed minutes" in f for f in parsed.flags)


def test_parse_times_ignores_prose():
    parsed = N.parse_times(["Cook the curry paste and stir frequently for a few minutes."])
    assert (parsed.prep, parsed.cook, parsed.total) == (None, None, None)
    assert parsed.flags == []


# ---------------------------------------------------------------------------- yield


@pytest.mark.parametrize(
    ("text", "servings", "flagged"),
    [
        ("Serves 4-6", 4, False),
        ("Makes 24 cookies", 24, False),
        ("1 (9-inch) pie", None, True),
        ("one 9-inch pie", None, True),
        ("4 servings", 4, False),
        ("Servings: 6", 6, False),
        ("Serves – 4 people", 4, False),
        ("6 to 8 servings", 6, False),
        ("Yields: 8 - 10", 8, False),
        ("8", 8, False),
        ("8 +", 8, False),
        ("12 donuts and 12 holes", 12, False),
        ("Servings (2-tbsp servings)", None, True),
        ("Serves – 4 tablespoons", None, True),
        ("Serving: 1bar", None, True),
        ("about 2 cups", None, True),
        ("2 loaves", None, True),
        ("two 12 inch strombolis", None, True),
        ("4 (makes 7 to 8 cups)", 4, True),
        ("Makes a dozen muffins", 12, False),
        (None, None, False),
    ],
)
def test_parse_yield(text, servings, flagged):
    parsed = N.parse_yield(text)
    assert parsed.servings == servings
    assert (parsed.flag is not None) == flagged


# ---------------------------------------------------------------------------- prefixes


def test_prefix_kenji_credit_and_title_untouched():
    title = "Kenji Mapo Tofu"
    info = N.analyze_title(title)
    assert info.prefixes == ["Kenji"]
    assert info.credit == "J. Kenji López-Alt"
    assert info.tags == []
    assert title == "Kenji Mapo Tofu"


@pytest.mark.parametrize(
    "title",
    [
        "Kenji’s Weeknight Chili",
        "Ragu Bolognese Kenji",
        "Same Day NY Style Pizza Recipe (Kenji)",
        "Same-Day New York-Style Pizza (Kenji style)",
    ],
)
def test_prefix_kenji_variants(title):
    assert N.analyze_title(title).credit == "J. Kenji López-Alt"


def test_prefix_ip_equipment_tag_no_credit():
    info = N.analyze_title("IP Pot Roast")
    assert info.prefixes == ["IP"]
    assert info.credit is None
    assert ("equipment", "instant pot") in [(k, v) for k, v, _ in info.tags]


def test_prefix_ip_is_word_only():
    assert N.analyze_title("Ipswich Clam Chowder").prefixes == []


def test_prefix_tddc_is_flagged_guess():
    for title in ("TDDC butter chicken", "TDCC Garlic Naan"):
        info = N.analyze_title(title)
        assert info.credit == "The Defined Dish (Alex Snodgrass)"
        assert info.credit_guess


def test_prefix_gordon_minimalist_chef():
    assert N.analyze_title("Gordon Ramsay Scrambled Eggs").credit == "Gordon Ramsay"
    assert N.analyze_title("Gordon's Pork Chops with Peppers").credit == "Gordon Ramsay"
    assert N.analyze_title("Minimalist Baker - Vegan Tzatziki").credit == "Minimalist Baker"
    info = N.analyze_title("“Best Pancakes You Will Ever Make” by Chef Frank Proto")
    assert info.credit == "Frank Proto"


def test_title_attributions_become_personal_tags():
    tags = lambda t: [(k, v) for k, v, _ in N.analyze_title(t).tags]  # noqa: E731
    assert tags("Adult Mac and Cheese (aaron rec)") == [("personal", "from aaron")]
    assert tags("Beef Short Rib Ragu (from bob)") == [("personal", "from bob")]
    assert tags("Texas Kolaches (marion cabin weekend recipe)") == [
        ("personal", "marion cabin weekend")
    ]
    assert tags("Cinnamon Rolls (From Scratch)") == []


def test_title_equipment_keywords():
    kinds = [(k, v) for k, v, _ in N.analyze_title("Instant Pot Beef Brisket").tags]
    assert kinds == [("equipment", "instant pot")]


# ---------------------------------------------------------------------------- matching helpers


def test_normalize_title_strips_prefixes_and_noise():
    assert N.normalize_title("Minimalist Baker - Vegan Tzatziki") == "vegan tzatziki"
    assert N.normalize_title("Kenji Crème Fraîche") == "creme fraiche"
    assert N.normalize_title("Philly Cheesesteak Recipe (VIDEO)") == "philly cheesesteak"
    assert N.normalize_title("Same Day NY Style Pizza") == N.normalize_title(
        "Same Day New York Style Pizza"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(8g) kosher salt, plus more…", "kosher salt"),
        ("2 cups chopped onions", "onion"),
        (", Yukon Gold potatoes, peeled and sliced", "yukon gold potato"),
        ("3 tablespoons (45g) unsalted butter, divided", "butter"),
        ("+ 2 Tablespoons all-purpose flour", "all purpose flour"),
        ("freshly ground black pepper", "black pepper"),
        ("Tomatoes", "tomato"),
    ],
)
def test_normalize_ingredient_name(raw, expected):
    assert N.normalize_ingredient_name(raw) == expected


def test_jaccard():
    assert N.jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert N.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert N.jaccard(set(), set()) == 0.0
    left = N.ingredient_set(["(8g) kosher salt", "2 cups onions", "1 lb beef"])
    right = N.ingredient_set(["kosher salt, to taste", "onion", "beef"])
    assert N.jaccard(left, right) == 1.0


def test_title_similarity():
    assert N.title_similarity("beef stroganoff", "beef stroganoff") == 1.0
    assert N.title_similarity("beef stroganoff", "lemon bars") < 0.5
    assert N.title_similarity("", "x") == 0.0


def _a(source_id, title, url, ingredients):
    return {
        "source": "A",
        "source_id": source_id,
        "title": title,
        "canonical_url": N.canonical_url(url),
        "document": {
            "ingredient_sections": [
                {"ingredients": [{"name": n, "source_text": n} for n in ingredients]}
            ]
        },
    }


def _b(source_id, title, url, ingredients, steps=1):
    return {
        "source": "B",
        "source_id": source_id,
        "title": title,
        "canonical_url": N.canonical_url(url),
        "ingredients": [{"name": n} for n in ingredients],
        "instructions": [{"step_number": i, "text": "x"} for i in range(steps)],
    }


def test_match_rules():
    ing = ["kosher salt", "onion", "beef chuck", "garlic", "tomato paste"]
    a = [
        _a("a1", "Beef Stew", "https://www.x.com/stew/?utm_source=z", ing),
        _a("a2", "Kenji Chili", None, ing),
        _a("a3", "Red Velvet Crinkle Cookies", "https://delish.com/rv", ["flour", "sugar"]),
        _a("a4", "Sock It To Me Cake", None, ["flour"]),
    ]
    b = [
        _b("b1", "Beef Stew (Best)", "http://x.com/stew", ["salt"]),
        _b("b2", "Chili", None, ing),
        _b("b3", "Red Velvet Crinkle Cookies", "https://acozykitchen.com/rv", ["flour", "sugar"]),
        _b("b4", "Sock It To Me Cake", None, [], steps=0),
    ]
    result = match(a, b)
    rules = {(p.a, p.b): p.rule for p in result.pairs}
    assert rules == {
        ("a1", "b1"): "canonical_url",
        ("a2", "b2"): "title_ingredients",
        ("a4", "b4"): "title_exact_empty_b",
    }
    # Same title + same ingredients but different sites -> review only, never merged.
    assert any(r["a"] == "a3" and r["b"] == "b3" for r in result.review)


def test_match_jaccard_band_goes_to_review():
    a = [_a("a1", "Lemon Bars", None, ["lemon", "sugar", "butter", "flour", "egg"])]
    b = [_b("b1", "Lemon Bars", None, ["lemon", "sugar", "butter", "salt", "cream", "zest"])]
    result = match(a, b)
    assert result.pairs == []
    assert len(result.review) == 1


def test_within_source_duplicates():
    infos = [
        info_for(_b("b1", "Maine Italian Sub", None, ["ham", "salami", "roll"])),
        info_for(_b("b2", "Maine Italian Sub", None, ["ham", "salami", "roll"])),
        info_for(_b("b3", "Lemon Bars", None, ["lemon"])),
    ]
    dups = within_source_duplicates(infos)
    assert [(d["x"], d["y"], d["kind"]) for d in dups] == [
        ("b1", "b2", "title + ingredients (>= 0.7)")
    ]


# ---------------------------------------------------------------------------- tags


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("side dish", ("course", "side")),
        ("course: dessert", ("course", "dessert")),
        ("protein: beans", ("protein", "beans/legumes")),
        ("protein: chicken", ("protein", "chicken")),
        ("fish", ("protein", "fish")),
        ("vegetarian", ("diet", "vegetarian")),
        ("mexican", ("cuisine", "Mexican")),
        ("equipment: oven", ("equipment", "oven")),
        ("method: bake", ("personal", "method: bake")),
        ("Comfort Food", ("personal", "comfort food")),
    ],
)
def test_map_free_tag(tag, expected):
    assert N.map_free_tag(tag) == expected


@pytest.mark.parametrize(
    ("category", "name", "expected"),
    [
        ("meal_type", "Dessert", ("course", "dessert")),
        ("meal_type", "Side Dish", ("course", "side")),
        ("meal_type", "Appetizer", ("course", "snack")),
        ("meal_type", "Dinner", ("personal", "dinner")),
        ("cooking_method", "Instant Pot", ("equipment", "instant pot")),
        ("cooking_method", "Slow Cooker", ("equipment", "slow cooker")),
        ("cooking_method", "Bake", ("personal", "method: bake")),
        ("prep_style", "Weeknight", ("personal", "weeknight")),
        ("difficulty", "Medium", ("personal", "difficulty: medium")),
        ("diet", "Keto", ("personal", "keto")),
        ("diet", "Gluten-Free", ("diet", "gluten-free")),
        ("cuisine", "Middle Eastern", ("cuisine", "Middle Eastern")),
    ],
)
def test_map_b_tag(category, name, expected):
    assert N.map_b_tag(category, name) == expected


# ---------------------------------------------------------------------------- diet guard


def test_diet_guard_meat_suppresses_vegetarian_and_vegan():
    tags = [("diet", "vegetarian"), ("diet", "vegan"), ("cuisine", "Thai")]
    kept, log = N.diet_guard(tags, ["1 tbsp fish sauce", "2 cups rice"])
    assert kept == [("cuisine", "Thai")]
    assert {entry["tag"] for entry in log} == {"vegetarian", "vegan"}
    assert log[0]["ingredient"] == "1 tbsp fish sauce"


def test_diet_guard_dairy_only_blocks_vegan():
    kept, log = N.diet_guard([("diet", "vegetarian"), ("diet", "vegan")], ["1 cup whole milk"])
    assert kept == [("diet", "vegetarian")]
    assert log == [{"tag": "vegan", "reason": "dairy/egg/honey", "ingredient": "1 cup whole milk"}]


@pytest.mark.parametrize(
    "line",
    [
        "1/4 cup vegan parmesan cheese (plus more for serving)",
        "3 cups vegan “chicken” broth or vegetable broth",
        "1 medium jalapeño, seeds and ribs removed",
        "2 tbsp peanut butter",
        "1 can coconut milk",
        "1 eggplant, cubed",
        "1 butternut squash",
        "2 tbsp maple syrup (or sub agave – or honey if not vegan)",
        "4 ounces vegan cream cheese",
        "8 oz oyster mushrooms",
    ],
)
def test_diet_guard_plant_lookalikes_are_not_animal(line):
    kept, log = N.diet_guard([("diet", "vegan")], [line])
    assert kept == [("diet", "vegan")], log


@pytest.mark.parametrize(
    "line",
    [
        "4 tablespoons worcestershire sauce",
        "2 cups vegetable broth or chicken broth",
        "CHICKEN optional",
        "2 anchovies",
        "1 tbsp gelatin",
        "4 slices bacon",
    ],
)
def test_diet_guard_strict_meat(line):
    kept, _ = N.diet_guard([("diet", "vegetarian")], [line])
    assert kept == []


@pytest.mark.parametrize("line", ["2 eggs", "1 tbsp honey", "2 tbsp butter", "1/2 cup feta"])
def test_diet_guard_vegan_blockers(line):
    kept, _ = N.diet_guard([("diet", "vegan"), ("diet", "vegetarian")], [line])
    assert kept == [("diet", "vegetarian")]


# ---------------------------------------------------------------------------- B -> document


def test_b_to_document_groups_and_ids():
    ingredients = [
        {
            "name": "garlic",
            "quantity": "3",
            "unit": "cloves",
            "group_name": None,
            "notes": "minced",
            "order_index": 1,
        },
        {
            "name": "(45g) unsalted butter, divided",
            "quantity": "3",
            "unit": "tablespoons",
            "group_name": None,
            "notes": None,
            "order_index": 0,
        },
        {
            "name": "powdered sugar",
            "quantity": "1",
            "unit": "cup",
            "group_name": "Glaze",
            "notes": None,
            "order_index": 2,
        },
        {
            "name": "salt",
            "quantity": None,
            "unit": None,
            "group_name": None,
            "notes": "to taste",
            "order_index": 3,
        },
    ]
    instructions = [
        {"step_number": 2, "text": "Glaze it."},
        {"step_number": 1, "text": "Melt the butter."},
        {"step_number": 3, "text": "   "},
    ]
    doc, flags = N.b_to_document(
        "Test Rolls", ingredients, instructions, "Keeps 3 days.\n\nFreeze up to a month."
    )
    assert isinstance(doc, RecipeDocumentV2)
    assert [s.id for s in doc.ingredient_sections] == ["ingredients_1", "ingredients_2"]
    assert doc.ingredient_sections[0].heading is None
    assert doc.ingredient_sections[1].heading == "Glaze"
    first = doc.ingredient_sections[0].ingredients
    assert [i.id for i in first] == ["i_1", "i_2", "i_4"]
    assert first[0].source_text == "3 tablespoons (45g) unsalted butter, divided"
    assert first[0].name == "(45g) unsalted butter, divided"
    assert first[1].source_text == "3 cloves garlic, minced"
    assert (first[1].quantity, first[1].unit, first[1].name, first[1].note) == (
        "3",
        "cloves",
        "garlic",
        "minced",
    )
    assert first[2].source_text == "salt, to taste"
    steps = doc.instruction_sections[0].steps
    assert [(s.id, s.text) for s in steps] == [
        ("step_1", "Melt the butter."),
        ("step_2", "Glaze it."),
    ]
    assert doc.notes == ["Keeps 3 days.", "Freeze up to a month."]
    assert doc.personal_notes is None and doc.yield_text is None
    assert not doc.legacy_source
    assert flags == []


def test_b_to_document_missing_steps_placeholder():
    doc, flags = N.b_to_document(
        "TDDC butter chicken",
        [
            {
                "name": "chicken",
                "quantity": "2",
                "unit": "lb",
                "group_name": None,
                "notes": None,
                "order_index": 0,
            }
        ],
        [],
        None,
    )
    assert doc.legacy_source
    assert doc.instruction_sections[0].steps[0].text == N.MISSING_DIRECTIONS
    assert flags


def test_b_to_document_requires_ingredients():
    with pytest.raises(ValueError):
        N.b_to_document("Elote Pasta Salad", [], [], None)


def test_b_to_document_long_fields_stay_in_source_text():
    long_name = "x" * 300
    doc, flags = N.b_to_document(
        "Long",
        [
            {
                "name": long_name,
                "quantity": "1",
                "unit": None,
                "group_name": None,
                "notes": None,
                "order_index": 0,
            }
        ],
        [{"step_number": 1, "text": "Go."}],
        None,
    )
    item = doc.ingredient_sections[0].ingredients[0]
    assert item.name is None and item.source_text.endswith(long_name)
    assert flags


# ---------------------------------------------------------------------------- misc


def test_slugify():
    assert N.slugify("Kenji Crème Fraîche") == "kenji-creme-fraiche"
    assert N.slugify("“Best Pancakes” by Chef Frank Proto") == "best-pancakes-by-chef-frank-proto"
    assert N.slugify("!!!") == "recipe"


@pytest.mark.parametrize(
    ("size", "box"),
    [
        ((1600, 1200), (0, 0, 1600, 1200)),
        ((2000, 1000), (333, 0, 1666, 1000)),
        ((1000, 1000), (0, 125, 1000, 875)),
    ],
)
def test_crop_box_4x3(size, box):
    assert IMG.crop_box_4x3(*size) == box


def test_derive_writes_4x3_webp(tmp_path):
    from PIL import Image

    src = tmp_path / "in.png"
    Image.new("RGB", (3000, 1000), "red").save(src)
    derived = IMG.derive(src, tmp_path / "images", "r1", "", "png")
    assert (derived.width, derived.height) == (1333, 1000)
    with Image.open(tmp_path / "images" / derived.thumb_path) as thumb:
        assert thumb.size == (480, 360) and thumb.format == "WEBP"
    assert (tmp_path / "images" / "r1" / "original.png").read_bytes() == src.read_bytes()
