from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from cookt.extraction.model import (
    FallbackIngredient,
    FallbackRecipe,
    ModelError,
    conventional_to_document,
    extract_conventional,
    normalize_imported_title,
)
from cookt.extraction.parser import (
    ConventionalDirection,
    ConventionalRecipe,
    clipboard_html_to_text,
    clipboard_text_overlaps,
    parse_html_recipe_export,
    parse_ingredient,
    parse_plain_text_recipe,
    parse_structured_recipe,
    parse_zip_recipe_export,
    sanitized_main_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "schema_recipe.html"
EXPORT_FIXTURE = Path(__file__).parent / "fixtures" / "paprika_export.html"


def test_json_ld_and_how_to_sections_are_parsed():
    html = FIXTURE.read_text(encoding="utf-8")
    result = parse_structured_recipe(html, "https://recipes.example.test/pie")
    assert result is not None
    assert result.parser_path == "json_ld"
    assert result.recipe.title == "Summer Berry Pie"
    assert result.recipe.author == "Ada Baker"
    assert result.recipe.yield_text == "8 slices"
    assert result.source_metadata.prep_time == "PT25M"
    assert result.source_metadata.cook_time == "PT1H5M"
    assert [item.section for item in result.recipe.directions] == [
        "Crust",
        "Crust",
        "Filling",
        "Filling",
    ]
    butter = result.recipe.ingredients[1]
    assert butter.us and butter.metric
    assert butter.metric.quantity == "115"


def test_conventional_v2_conversion_preserves_full_directions_without_model_mapping():
    parsed = parse_structured_recipe(
        FIXTURE.read_text(encoding="utf-8"),
        "https://recipes.example.test/pie",
    )
    assert parsed is not None
    document = conventional_to_document(parsed.recipe)

    assert document.schema_version == 2
    assert [section.heading for section in document.instruction_sections] == [
        "Crust",
        "Filling",
    ]
    assert [step.text for section in document.instruction_sections for step in section.steps] == [
        "Cut the butter into the flour.",
        "Chill for 30 minutes.",
        "Mix berries and sugar.",
        "Bake at 375°F for 65 minutes.",
    ]
    assert [
        ingredient.source_text
        for section in document.ingredient_sections
        for ingredient in section.ingredients
    ] == [
        "2 cups flour",
        "1/2 cup / 115 g butter, cold",
        "4 cups berries",
        "3/4 cup sugar",
    ]


def test_sanitizer_removes_navigation_and_scripts():
    text = sanitized_main_text(
        "<html><body><nav>secret nav</nav><main>Recipe body</main>"
        "<script>steal()</script></body></html>",
        "https://example.test/recipe",
    )
    assert "Recipe body" in text
    assert "secret nav" not in text
    assert "steal" not in text


def test_plain_text_recipe_uses_familiar_headings_without_inference():
    parsed = parse_plain_text_recipe(
        """Weeknight Tomato Pasta
Serves: 4

Ingredients
- 2 tbsp olive oil
- 1 onion, diced
- 14 oz canned tomatoes

Directions
1. Heat the oil over medium heat.
2. Cook the onion until soft.
3. Add tomatoes and simmer for 20 minutes.
"""
    )

    assert parsed is not None
    assert parsed.parser_path == "plain_text"
    assert parsed.recipe.title == "Weeknight Tomato Pasta"
    assert parsed.recipe.yield_text == "Serves 4"
    assert [item.source_text for item in parsed.recipe.ingredients] == [
        "2 tbsp olive oil",
        "1 onion, diced",
        "14 oz canned tomatoes",
    ]
    assert parsed.recipe.directions[0].text == "Heat the oil over medium heat."


def test_plain_text_recipe_accepts_three_unlabeled_blocks():
    parsed = parse_plain_text_recipe(
        """Simple Beans

2 cans black beans
1 tsp cumin

Drain the beans.
Simmer with cumin for 10 minutes.
"""
    )

    assert parsed is not None
    assert parsed.recipe.title == "Simple Beans"
    assert len(parsed.recipe.ingredients) == 2
    assert len(parsed.recipe.directions) == 2


def test_plain_text_recipe_separates_page_chrome_equipment_notes_and_nutrition():
    parsed = parse_plain_text_recipe(
        """Serious Eats
Charred Salsa Verde
Smoky, spicy, sweet, bright, and complex, this is the one salsa to rule them all.

By J. Kenji López-Alt  Updated on December 19, 2025
Prep Time: 10 mins
Cook Time: 15 mins
Cooling Time : 30 mins
Total Time: 55 mins
Servings: 24 servings
Yield: 3 cups
Ingredients
1 1/2 pounds tomatillos, husks removed, split in half (680 g; about 10 medium)

1 medium white onion, peeled and split in half (about 6 ounces; 170 g)

2 to 4 Serrano or jalapeño chiles, split in half

10 to 15 sprigs cilantro, tough lower stems discarded

1 tablespoon vegetable oil

Kosher salt

Directions
Adjust oven rack to 4 inches below broiler and preheat broiler to high.

Transfer vegetables and their juice to a blender. Add half of cilantro.

Heat oil in a medium saucepan over high heat until shimmering.

Finely chop remaining cilantro and stir into salsa.

Special Equipment
Rimmed baking sheet, blender, food processor, or immersion blender

Notes
If your tomatillos are exceptionally tart, add a bit of sugar to balance the flavors.

Read More
Basic Salsa Verde Recipe

Nutrition Facts
calories 18
"""
    )

    assert parsed is not None
    recipe = parsed.recipe
    assert recipe.title == "Charred Salsa Verde"
    assert recipe.author == "J. Kenji López-Alt"
    assert recipe.yield_text == "24 servings · 3 cups"
    assert recipe.prep_notes == [
        "Prep Time: 10 mins",
        "Cook Time: 15 mins",
        "Cooling Time: 30 mins",
        "Total Time: 55 mins",
        "Equipment: Rimmed baking sheet, blender, food processor, or immersion blender",
        "Note: If your tomatillos are exceptionally tart, add a bit of sugar to balance "
        "the flavors.",
    ]
    assert len(recipe.directions) == 4
    assert all("Equipment" not in item.text for item in recipe.directions)
    assert all("Nutrition" not in item.text for item in recipe.directions)


def test_clipboard_html_preserves_semantic_blocks_and_drops_hidden_controls():
    plain = """Fast Soup

Ingredients
2 cups stock
1 tsp salt

Directions
Simmer for 10 minutes.
"""
    semantic = clipboard_html_to_text(
        """<article>
        <h1>Fast Soup</h1>
        <button>Save recipe</button>
        <h2>Ingredients</h2>
        <ul><li><span>2 cups</span> stock</li><li>1 tsp salt</li></ul>
        <h2>Directions</h2>
        <ol><li>Simmer for 10 minutes.</li></ol>
        <p aria-hidden="true">Advertisement instructions</p>
        </article>"""
    )

    assert "Save recipe" not in semantic
    assert "Advertisement" not in semantic
    assert clipboard_text_overlaps(plain, semantic)
    parsed = parse_plain_text_recipe(semantic)
    assert parsed is not None
    assert parsed.recipe.title == "Fast Soup"
    assert [item.source_text for item in parsed.recipe.ingredients] == [
        "2 cups stock",
        "1 tsp salt",
    ]
    assert [item.text for item in parsed.recipe.directions] == ["Simmer for 10 minutes."]


def test_paprika_html_export_parses_multiple_recipes_without_images_or_boilerplate():
    result = parse_html_recipe_export(EXPORT_FIXTURE.read_bytes())

    assert result.total == 2
    assert not result.issues
    assert [item.recipe.title for item in result.recipes] == [
        "Lemon Rice",
        "Quick Beans",
    ]
    rice = result.recipes[0]
    assert rice.source_url == "https://recipes.example.test/lemon-rice"
    assert rice.recipe.yield_text == "4 bowls"
    assert rice.recipe.prep_notes == [
        "Prep: 10 mins",
        "Note: Finish with lemon zest.",
    ]
    assert [step.text for step in rice.recipe.directions] == [
        "Simmer the rice in stock until tender."
    ]
    assert [ingredient.id for ingredient in rice.recipe.ingredients] == ["i001", "i002"]
    assert len(rice.content_hash) == 64


def test_html_export_preserves_long_personal_notes_and_ignores_domain_as_author():
    long_note = "Remember this adjustment next time. " * 30
    payload = (
        EXPORT_FIXTURE.read_text(encoding="utf-8")
        .replace(
            "Finish with lemon zest.",
            long_note,
        )
        .replace(
            "Example Kitchen</span>",
            "recipes.example.test</span>",
            1,
        )
    )

    result = parse_html_recipe_export(payload.encode())

    recipe = result.recipes[0].recipe
    assert recipe.author is None
    assert recipe.prep_notes[-1] == f"Note: {long_note.strip()}"


def test_html_export_preserves_incomplete_entries_as_flagged_review_drafts():
    payload = (
        EXPORT_FIXTURE.read_text(encoding="utf-8")
        .replace(
            "</body>",
            '<div itemscope itemtype="http://schema.org/Recipe">'
            '<h1 itemprop="name">Missing directions</h1>'
            '<p itemprop="recipeIngredient">1 cup flour</p></div></body>',
        )
        .encode()
    )

    result = parse_html_recipe_export(payload)

    assert result.total == 3
    assert len(result.recipes) == 3
    assert not result.issues
    incomplete = result.recipes[-1].recipe
    assert incomplete.title == "Missing directions"
    assert incomplete.directions[0].text == ("Directions were not included in the original export.")
    assert incomplete.prep_notes == [
        "Import warning: The original export did not include directions."
    ]


def test_zip_cookbook_export_ignores_images_and_macos_metadata():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("Recipes/family.html", EXPORT_FIXTURE.read_bytes())
        archive.writestr("Recipes/Images/photo.jpg", b"not parsed")
        archive.writestr("__MACOSX/Recipes/._family.html", b"metadata")

    result = parse_zip_recipe_export(buffer.getvalue())

    assert result.total == 2
    assert not result.issues
    assert [item.recipe.title for item in result.recipes] == [
        "Lemon Rice",
        "Quick Beans",
    ]


def test_export_section_labels_become_cooking_view_headings():
    payload = b"""<div itemscope itemtype="http://schema.org/Recipe">
      <h1 itemprop="name">Dinner</h1>
      <p itemprop="recipeIngredient">For the sauce:</p>
      <p itemprop="recipeIngredient">2 cups tomatoes</p>
      <p itemprop="recipeIngredient">For the pasta:</p>
      <p itemprop="recipeIngredient">8 oz spaghetti</p>
      <div itemprop="recipeInstructions">
        <p>Make the sauce:</p><p>Simmer the tomatoes.</p>
        <p>Finish the pasta:</p><p>Toss with spaghetti.</p>
      </div>
    </div>"""

    result = parse_html_recipe_export(payload)
    document = conventional_to_document(result.recipes[0].recipe)

    assert [section.heading for section in document.ingredient_sections] == [
        "For the sauce",
        "For the pasta",
    ]
    assert [section.heading for section in document.instruction_sections] == [
        "Make the sauce",
        "Finish the pasta",
    ]


def test_document_normalization_promotes_component_labels_and_removes_export_chrome():
    recipe = ConventionalRecipe(
        title="Layer cake",
        prep_notes=["Note: Note: Chill before slicing."],
        ingredients=[
            parse_ingredient("CAKE", "i001"),
            parse_ingredient("2 cups flour", "i002"),
            parse_ingredient("Ingredients Save Recipe", "i006"),
            parse_ingredient("Frosting", "i003"),
            parse_ingredient("1 cup butter", "i004"),
            parse_ingredient("Kosher salt", "i005"),
        ],
        directions=[
            ConventionalDirection(text="Gather the ingredients."),
            ConventionalDirection(text="How to Make Layer cake"),
            ConventionalDirection(text="Mix the batter."),
            ConventionalDirection(text="4 Likes"),
            ConventionalDirection(text="Bake until set."),
        ],
    )

    document = conventional_to_document(recipe)

    assert document.notes == ["Chill before slicing."]
    assert [section.heading for section in document.ingredient_sections] == [
        "CAKE",
        "Frosting",
    ]
    assert [
        item.source_text for section in document.ingredient_sections for item in section.ingredients
    ] == ["2 cups flour", "1 cup butter", "Kosher salt"]
    assert [step.text for section in document.instruction_sections for step in section.steps] == [
        "Mix the batter.",
        "Bake until set.",
    ]


def test_import_title_normalization_removes_source_and_forum_chrome():
    assert (
        normalize_imported_title(
            "Minimalist Baker - Black Bean Buddha Bowl with Gingery Lemon Tahini Sauce",
            "minimalistbaker.com",
        )
        == "Black Bean Buddha Bowl with Gingery Lemon Tahini Sauce"
    )
    assert (
        normalize_imported_title(
            "Minimalist Baker Hummus",
            "minimalistbaker.com",
        )
        == "Hummus"
    )
    assert (
        normalize_imported_title(
            "Another Go with J Kenji Alt's Same Day NY Style Pizza Recipe - "
            "New York Style - Pizza Making Forum",
            "pizzamaking.com",
        )
        == "Same Day NY Style Pizza"
    )


def test_import_title_normalization_drops_media_and_section_labels_only():
    assert (
        normalize_imported_title(
            "Instant Pot Brisket Recipe Flavor Profile",
            "lemonblossoms.com",
        )
        == "Instant Pot Brisket"
    )
    assert (
        normalize_imported_title(
            "Philly Cheesesteak Recipe (VIDEO)",
            "natashaskitchen.com",
        )
        == "Philly Cheesesteak Recipe"
    )
    assert (
        normalize_imported_title(
            "Pastéis De Nata | Portuguese Custard Tarts",
            "leitesculinaria.com",
        )
        == "Pastéis De Nata | Portuguese Custard Tarts"
    )
    assert (
        normalize_imported_title(
            "Mandarin Orange Spinach Salad with Chicken and Lemon Honey Ginger Dressing",
            "cookingclassy.com",
        )
        == "Mandarin Orange Spinach Salad with Chicken and Lemon Honey Ginger Dressing"
    )


def test_document_normalization_splits_long_steps_only_at_sentence_boundaries():
    sentences = [f"Sentence {index} keeps 2 cups at 375°F for 10 minutes." for index in range(20)]
    source = " ".join(sentences)
    recipe = ConventionalRecipe(
        title="Long method",
        ingredients=[parse_ingredient("2 cups flour", "i001")],
        directions=[ConventionalDirection(text=source)],
    )

    document = conventional_to_document(recipe)
    steps = [step.text for section in document.instruction_sections for step in section.steps]

    assert len(steps) > 1
    assert " ".join(steps) == source
    assert all(len(step) <= 800 for step in steps)


class RepairingExtractionClient:
    def __init__(self):
        self.calls = 0

    def complete(self, _prompt, _schema, _name):
        self.calls += 1
        if self.calls == 1:
            raise ModelError("invalid first result")
        return FallbackRecipe(
            title="Soup",
            ingredients=[FallbackIngredient(source_text="2 cups stock")],
            directions=[ConventionalDirection(text="Simmer for 20 minutes.")],
        )


def test_fallback_extraction_attempts_one_constrained_repair():
    client = RepairingExtractionClient()
    recipe = extract_conventional(
        "Soup\n2 cups stock\nSimmer for 20 minutes.",
        client,
    )
    assert client.calls == 2
    assert recipe.ingredients[0].id == "i001"
    assert recipe.directions[0].text == "Simmer for 20 minutes."
