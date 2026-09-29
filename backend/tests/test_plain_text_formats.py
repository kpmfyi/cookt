from cookt.extraction.parser import ParseResult, parse_plain_text_recipe


def _parse(text: str) -> ParseResult:
    parsed = parse_plain_text_recipe(text)
    assert parsed is not None
    return parsed


def _ingredient_texts(parsed: ParseResult) -> list[str]:
    return [ingredient.source_text for ingredient in parsed.recipe.ingredients]


def _direction_texts(parsed: ParseResult) -> list[str]:
    return [direction.text for direction in parsed.recipe.directions]


def test_serious_eats_sibling_sections_stay_out_of_directions() -> None:
    parsed = _parse(
        """Serious Eats
Charred Green Beans
A compact synthetic recipe for parser testing.

By Test Cook  Updated on January 2, 2026
Prep Time: 5 mins
Cook Time: 8 mins
Total Time: 13 mins
Servings: 2 servings
Yield: 1 bowl

Ingredients
8 ounces green beans
1 tablespoon neutral oil

Directions
1. Broil the beans until browned.
2. Toss with the oil and serve.

Special Equipment
Rimmed baking sheet

Notes
Dry the beans before broiling.

Read More
Another Green Bean Recipe

Nutrition Facts
Calories 90
"""
    )

    assert parsed.recipe.title == "Charred Green Beans"
    assert parsed.recipe.yield_text == "2 servings · 1 bowl"
    assert _direction_texts(parsed) == [
        "Broil the beans until browned.",
        "Toss with the oil and serve.",
    ]
    assert "Equipment: Rimmed baking sheet" in parsed.recipe.prep_notes
    assert "Note: Dry the beans before broiling." in parsed.recipe.prep_notes


def test_allrecipes_chefs_notes_are_a_sibling_of_directions() -> None:
    parsed = _parse(
        """Allrecipes
Skillet Chickpeas
Prep Time: 4 mins
Cook Time: 6 mins
Servings: 2

Ingredients
1 can chickpeas
1 teaspoon olive oil

Directions
1. Warm the chickpeas in the oil.

Chef's Notes
Drain the chickpeas well for better browning.

Nutrition Facts (per serving)
Calories 120
"""
    )

    assert parsed.recipe.title == "Skillet Chickpeas"
    assert _direction_texts(parsed) == ["Warm the chickpeas in the oil."]
    assert any(
        "Drain the chickpeas well for better browning." in note for note in parsed.recipe.prep_notes
    )


def test_food_network_inactive_time_and_cooks_note_are_metadata() -> None:
    parsed = _parse(
        """Food Network
Chilled Cucumber Soup
Inactive: 20 min
Yield: 2 bowls

Ingredients
2 cucumbers
1 cup plain yogurt

Directions
1. Blend the cucumbers and yogurt until smooth.

Cook’s Note
Chill before serving.

Tools You May Need
Blender
"""
    )

    assert parsed.recipe.title == "Chilled Cucumber Soup"
    assert parsed.recipe.yield_text == "2 bowls"
    assert _direction_texts(parsed) == ["Blend the cucumbers and yogurt until smooth."]
    assert any(note == "Inactive: 20 min" for note in parsed.recipe.prep_notes)
    assert any("Chill before serving." in note for note in parsed.recipe.prep_notes)


def test_good_food_nutrition_between_ingredients_and_method_is_not_food() -> None:
    parsed = _parse(
        """Good Food
Minted Peas

Ingredients
200g frozen peas
1 tablespoon chopped mint

Nutrition: per serving
kcal 90
fat 2g
protein 5g

Method
step 1
Simmer the peas, then stir in the mint.
"""
    )

    assert parsed.recipe.title == "Minted Peas"
    assert _ingredient_texts(parsed) == [
        "200g frozen peas",
        "1 tablespoon chopped mint",
    ]
    assert _direction_texts(parsed) == ["Simmer the peas, then stir in the mint."]


def test_king_arthur_baker_tips_follow_instructions_as_notes() -> None:
    parsed = _parse(
        """King Arthur Baking
Quick Skillet Bread

Ingredients
2 cups bread flour
1 cup warm water

Instructions
1. Stir the flour and water into a dough.

Tips from our Bakers
Cover leftovers before storing.

Baker's Resources
Ingredient weight chart
"""
    )

    assert parsed.recipe.title == "Quick Skillet Bread"
    assert _direction_texts(parsed) == ["Stir the flour and water into a dough."]
    assert any("Cover leftovers before storing." in note for note in parsed.recipe.prep_notes)


def test_pdf_page_headers_and_footers_are_removed_inside_recipe() -> None:
    parsed = _parse(
        """2/3/2026 Tiny Tomato Soup | Example Recipes
https://recipes.example.test/tiny-tomato-soup Page 1 of 2
Tiny Tomato Soup

Ingredients
2 cups chopped tomatoes
1 teaspoon olive oil

Directions
1. Simmer the tomatoes in the oil.

2/3/2026 Tiny Tomato Soup | Example Recipes
https://recipes.example.test/tiny-tomato-soup Page 2 of 2

2. Blend until smooth.
"""
    )

    assert parsed.recipe.title == "Tiny Tomato Soup"
    assert _direction_texts(parsed) == [
        "Simmer the tomatoes in the oil.",
        "Blend until smooth.",
    ]
