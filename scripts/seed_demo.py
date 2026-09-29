"""Seed an empty data dir with a small demo household: recipes, tags, plan, shopping list.

Used for the README screenshots and for trying the app without importing anything. The recipes
are written for this demo. Nothing calls a model.

    uv run python scripts/seed_demo.py /tmp/cookt-demo
    COOKT_DATA_DIR=/tmp/cookt-demo COOKT_PORT=8197 COOKT_RUN_WORKER=0 uv run python -m cookt

Refuses to touch a database that already has recipes in it.

README images (docs/images): with the demo running, capture them with
    cd frontend && COOKT_URL=http://127.0.0.1:8197 COOKT_SLUG=chana-masala \\
        SIZES=phone,ipad-landscape node scripts/screens.mjs /tmp/cookt-shots
then save the chosen shots as WebP, 1600 px wide for iPad and 600 px for phone.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit("usage: seed_demo.py DATA_DIR  (a new or empty directory, never your real data/)")
DATA = Path(sys.argv[1]).expanduser().resolve()
os.environ["COOKT_DATA_DIR"] = str(DATA)
os.environ["COOKT_RUN_WORKER"] = "0"

from cookt import db, planning, recipes  # noqa: E402  (config reads the env at import)
from cookt.document import RecipeDocumentV2  # noqa: E402

# (title, cuisine, course, protein, diets, servings, prep, cook, description,
#  ingredient sections [(heading, [lines])], step sections [(heading, [steps])])
RECIPES = [
    (
        "Lemon Chicken Orzo Soup",
        "Greek",
        "soup",
        "chicken",
        [],
        6,
        15,
        35,
        "Bright, brothy and done in under an hour. The egg-lemon finish makes it silky.",
        [
            (
                None,
                [
                    "2 tablespoons olive oil",
                    "1 yellow onion, diced",
                    "2 carrots, diced",
                    "2 celery stalks, diced",
                    "3 garlic cloves, minced",
                    "8 cups chicken broth",
                    "1 1/2 pounds boneless, skinless chicken thighs",
                    "3/4 cup orzo",
                    "2 large eggs",
                    "1/3 cup fresh lemon juice",
                    "2 tablespoons chopped fresh dill",
                    "Kosher salt and black pepper",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Heat the oil in a large pot over medium heat. Add the onion, carrots and "
                        "celery and cook, stirring now and then, until soft, about 8 minutes. Stir "
                        "in the garlic for 30 seconds."
                    ),
                    (
                        "Add the broth and chicken, bring to a boil, then lower to a gentle "
                        "simmer. Cook until the chicken is cooked through, about 20 minutes. Lift "
                        "out the chicken and shred it with two forks."
                    ),
                    (
                        "Stir the orzo into the simmering broth and cook until tender, about 9 "
                        "minutes."
                    ),
                    (
                        "Whisk the eggs and lemon juice in a bowl. Slowly whisk in two ladles of "
                        "hot broth, then stir the mixture back into the pot off the heat."
                    ),
                    "Return the chicken, stir in the dill, and season with salt and pepper.",
                ],
            ),
        ],
    ),
    (
        "Black Bean Tacos with Pickled Onions",
        "Mexican",
        "main",
        "beans/legumes",
        ["vegetarian"],
        4,
        20,
        15,
        "Smoky beans, sharp onions, soft tortillas. A weeknight regular.",
        [
            (
                "Pickled onions",
                [
                    "1 red onion, thinly sliced",
                    "1/2 cup apple cider vinegar",
                    "1 teaspoon sugar",
                    "1 teaspoon kosher salt",
                ],
            ),
            (
                "Tacos",
                [
                    "1 tablespoon olive oil",
                    "2 cans (15 ounces each) black beans, drained",
                    "1 teaspoon ground cumin",
                    "1 teaspoon smoked paprika",
                    "12 corn tortillas",
                    "1 avocado, sliced",
                    "1/2 cup crumbled cotija cheese",
                    "1 lime, cut into wedges",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Pack the onion into a jar. Warm the vinegar, sugar and salt until "
                        "dissolved, pour over the onion and let stand at least 15 minutes."
                    ),
                    (
                        "Warm the oil in a skillet over medium heat. Add the beans, cumin and "
                        "paprika and cook, mashing about half the beans, until thick, about 6 "
                        "minutes."
                    ),
                    "Char the tortillas one at a time in a dry pan, about 30 seconds a side.",
                    (
                        "Fill the tortillas with beans, avocado, cotija and pickled onions. Serve "
                        "with lime."
                    ),
                ],
            ),
        ],
    ),
    (
        "Miso Butter Salmon",
        "Japanese",
        "main",
        "fish",
        ["gluten-free"],
        4,
        10,
        12,
        "A glaze you can stir together while the oven heats.",
        [
            (
                None,
                [
                    "4 salmon fillets (6 ounces each)",
                    "2 tablespoons white miso",
                    "2 tablespoons unsalted butter, softened",
                    "1 tablespoon honey",
                    "1 teaspoon rice vinegar",
                    "1 teaspoon grated fresh ginger",
                    "2 scallions, thinly sliced",
                    "1 teaspoon toasted sesame seeds",
                ],
            ),
        ],
        [
            (
                None,
                [
                    "Heat the oven to 425°F and line a sheet pan with parchment.",
                    "Mash the miso, butter, honey, vinegar and ginger into a smooth paste.",
                    "Set the salmon on the pan and spread the glaze over the tops.",
                    "Roast until the salmon flakes at the thickest part, 10 to 12 minutes.",
                    "Scatter with scallions and sesame seeds before serving.",
                ],
            ),
        ],
    ),
    (
        "Chana Masala",
        "Indian",
        "main",
        "beans/legumes",
        ["vegetarian", "vegan", "gluten-free", "dairy-free"],
        4,
        15,
        30,
        "Pantry chickpeas in a deeply spiced tomato gravy.",
        [
            (
                None,
                [
                    "3 tablespoons neutral oil",
                    "1 teaspoon cumin seeds",
                    "1 large onion, finely chopped",
                    "4 garlic cloves, minced",
                    "1 tablespoon grated fresh ginger",
                    "1 green chile, minced",
                    "2 teaspoons garam masala",
                    "1 teaspoon ground coriander",
                    "1/2 teaspoon ground turmeric",
                    "1 can (14 ounces) crushed tomatoes",
                    "2 cans (15 ounces each) chickpeas, drained",
                    "1 cup water",
                    "1 tablespoon lemon juice",
                    "Chopped cilantro, for serving",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Heat the oil in a heavy pot over medium heat. Add the cumin seeds and let "
                        "them sizzle for 30 seconds."
                    ),
                    "Add the onion and cook until deep golden, about 12 minutes.",
                    (
                        "Stir in the garlic, ginger and chile for 1 minute, then the garam masala, "
                        "coriander and turmeric for 30 seconds more."
                    ),
                    (
                        "Add the tomatoes and cook until the oil separates at the edges, about 8 "
                        "minutes."
                    ),
                    (
                        "Add the chickpeas and water, mash a few chickpeas against the pot, and "
                        "simmer until thick, about 15 minutes."
                    ),
                    "Stir in the lemon juice, season with salt, and top with cilantro.",
                ],
            ),
        ],
    ),
    (
        "Thai Basil Chicken",
        "Thai",
        "main",
        "chicken",
        ["dairy-free"],
        4,
        10,
        10,
        "Pad kra pao: fast, fiery, and best with a crispy fried egg on top.",
        [
            (
                None,
                [
                    "2 tablespoons neutral oil",
                    "5 garlic cloves, chopped",
                    "3 Thai chiles, sliced",
                    "1 pound ground chicken",
                    "1 tablespoon oyster sauce",
                    "1 tablespoon soy sauce",
                    "2 teaspoons fish sauce",
                    "1 teaspoon sugar",
                    "2 cups Thai basil leaves",
                    "Steamed jasmine rice, for serving",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Heat the oil in a wok over high heat. Add the garlic and chiles and stir "
                        "for 20 seconds."
                    ),
                    (
                        "Add the chicken and cook, breaking it up, until no longer pink, about 4 "
                        "minutes."
                    ),
                    "Add the oyster sauce, soy sauce, fish sauce and sugar and toss for 1 minute.",
                    "Turn off the heat, fold in the basil until wilted, and serve over rice.",
                ],
            ),
        ],
    ),
    (
        "Buttermilk Pancakes",
        "American",
        "breakfast",
        "eggs",
        ["vegetarian"],
        4,
        10,
        20,
        "Tall and tender. Let the batter rest while the griddle heats.",
        [
            (
                None,
                [
                    "2 cups all-purpose flour",
                    "2 tablespoons sugar",
                    "1 1/2 teaspoons baking powder",
                    "1/2 teaspoon baking soda",
                    "1/2 teaspoon kosher salt",
                    "2 cups buttermilk",
                    "2 large eggs",
                    "4 tablespoons unsalted butter, melted",
                    "Maple syrup, for serving",
                ],
            ),
        ],
        [
            (
                None,
                [
                    "Whisk the flour, sugar, baking powder, baking soda and salt in a large bowl.",
                    (
                        "Whisk the buttermilk, eggs and melted butter in another bowl, then fold "
                        "into the dry ingredients until just combined. Rest for 10 minutes."
                    ),
                    (
                        "Heat a buttered griddle over medium-low heat. Pour 1/3 cup batter per "
                        "pancake and cook until bubbles set on top, about 2 minutes."
                    ),
                    "Flip and cook until golden, about 1 minute more. Serve with maple syrup.",
                ],
            ),
        ],
    ),
    (
        "Brown Butter Chocolate Chip Cookies",
        "American",
        "dessert",
        "none",
        ["vegetarian"],
        24,
        20,
        12,
        "Browning the butter takes five minutes and makes them taste like toffee.",
        [
            (
                None,
                [
                    "1 cup unsalted butter",
                    "1 cup packed dark brown sugar",
                    "1/2 cup granulated sugar",
                    "2 large eggs",
                    "2 teaspoons vanilla extract",
                    "2 1/4 cups all-purpose flour",
                    "1 teaspoon baking soda",
                    "1 teaspoon kosher salt",
                    "2 cups chopped dark chocolate",
                    "Flaky salt, for sprinkling",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Melt the butter in a light-colored pan over medium heat, swirling, until "
                        "it smells nutty and the solids turn brown, about 5 minutes. Cool for 15 "
                        "minutes."
                    ),
                    "Whisk in both sugars, then the eggs and vanilla until glossy.",
                    (
                        "Stir in the flour, baking soda and salt, then the chocolate. Chill the "
                        "dough for 30 minutes."
                    ),
                    "Heat the oven to 375°F. Scoop 2-tablespoon balls onto lined sheets.",
                    (
                        "Bake until the edges are set and the centers look underdone, 10 to 12 "
                        "minutes. Sprinkle with flaky salt."
                    ),
                ],
            ),
        ],
    ),
    (
        "Shakshuka",
        "Middle Eastern",
        "breakfast",
        "eggs",
        ["vegetarian", "gluten-free"],
        4,
        10,
        25,
        "Eggs poached in a spiced pepper and tomato sauce. Bring bread for dipping.",
        [
            (
                None,
                [
                    "3 tablespoons olive oil",
                    "1 onion, sliced",
                    "2 red bell peppers, sliced",
                    "3 garlic cloves, sliced",
                    "1 teaspoon ground cumin",
                    "1 teaspoon sweet paprika",
                    "1 can (28 ounces) whole tomatoes",
                    "6 large eggs",
                    "1/2 cup crumbled feta",
                    "Chopped parsley, for serving",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Heat the oil in a large skillet over medium heat. Cook the onion and "
                        "peppers until very soft, about 12 minutes."
                    ),
                    "Add the garlic, cumin and paprika and cook for 1 minute.",
                    (
                        "Crush in the tomatoes with their juice and simmer until thickened, about "
                        "10 minutes."
                    ),
                    (
                        "Make six wells, crack an egg into each, cover, and cook until the whites "
                        "set, about 6 minutes."
                    ),
                    "Top with feta and parsley and serve from the pan.",
                ],
            ),
        ],
    ),
    (
        "Crispy Smashed Potatoes",
        "American",
        "side",
        "none",
        ["vegetarian", "vegan", "gluten-free", "dairy-free"],
        4,
        10,
        50,
        "Boil, smash, roast. The craggy edges are the point.",
        [
            (
                None,
                [
                    "2 pounds baby potatoes",
                    "1/4 cup olive oil",
                    "1 teaspoon kosher salt",
                    "1/2 teaspoon black pepper",
                    "2 tablespoons chopped fresh rosemary",
                    "Flaky salt, for serving",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Boil the potatoes in well-salted water until tender, about 20 minutes. "
                        "Drain and let them steam dry for 5 minutes."
                    ),
                    (
                        "Heat the oven to 450°F. Oil a sheet pan and flatten each potato with the "
                        "bottom of a glass."
                    ),
                    (
                        "Drizzle with the rest of the oil, season, and roast until deeply browned, "
                        "about 25 minutes."
                    ),
                    "Toss with rosemary and flaky salt.",
                ],
            ),
        ],
    ),
    (
        "Beef and Broccoli",
        "Chinese",
        "main",
        "beef",
        ["dairy-free"],
        4,
        15,
        12,
        "Better than takeout, and faster than delivery.",
        [
            (
                None,
                [
                    "1 pound flank steak, thinly sliced against the grain",
                    "1 tablespoon cornstarch",
                    "1/3 cup soy sauce",
                    "2 tablespoons oyster sauce",
                    "1 tablespoon brown sugar",
                    "1/2 cup water",
                    "2 tablespoons neutral oil",
                    "4 cups broccoli florets",
                    "3 garlic cloves, minced",
                    "1 tablespoon grated fresh ginger",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Toss the steak with the cornstarch. Stir the soy sauce, oyster sauce, "
                        "sugar and water together."
                    ),
                    (
                        "Heat 1 tablespoon oil in a wok over high heat and sear the beef in one "
                        "layer, about 2 minutes. Transfer to a plate."
                    ),
                    (
                        "Add the rest of the oil and the broccoli with a splash of water; cover "
                        "and steam until bright green, about 3 minutes."
                    ),
                    (
                        "Add the garlic and ginger for 30 seconds, return the beef, pour in the "
                        "sauce and toss until glossy, about 2 minutes."
                    ),
                ],
            ),
        ],
    ),
    (
        "Greek Salad",
        "Greek",
        "salad",
        "none",
        ["vegetarian", "gluten-free"],
        4,
        15,
        0,
        "No lettuce. Just good tomatoes, cucumber and a slab of feta.",
        [
            (
                None,
                [
                    "4 ripe tomatoes, cut into wedges",
                    "1 English cucumber, sliced",
                    "1/2 red onion, thinly sliced",
                    "1 green bell pepper, sliced",
                    "1/2 cup Kalamata olives",
                    "7 ounces feta, in one piece",
                    "1/4 cup olive oil",
                    "1 tablespoon red wine vinegar",
                    "1 teaspoon dried oregano",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Arrange the tomatoes, cucumber, onion, pepper and olives in a wide bowl "
                        "and season with salt."
                    ),
                    "Set the feta on top, drizzle with oil and vinegar, and sprinkle with oregano.",
                ],
            ),
        ],
    ),
    (
        "Overnight Focaccia",
        "Italian",
        "bread",
        "none",
        ["vegetarian", "vegan", "dairy-free"],
        12,
        20,
        25,
        "Almost no kneading: time does the work.",
        [
            (
                None,
                [
                    "4 cups bread flour",
                    "2 teaspoons kosher salt",
                    "1 teaspoon instant yeast",
                    "2 cups lukewarm water",
                    "6 tablespoons olive oil, divided",
                    "Flaky salt, for the top",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Mix the flour, salt, yeast and water into a shaggy dough. Cover and "
                        "refrigerate overnight, 12 to 24 hours."
                    ),
                    (
                        "Pour 4 tablespoons oil into a 9x13-inch pan. Tip in the dough, turn to "
                        "coat, and let rise until puffy, about 3 hours."
                    ),
                    (
                        "Heat the oven to 450°F. Drizzle with the rest of the oil, dimple all over "
                        "with your fingers, and sprinkle with flaky salt."
                    ),
                    "Bake until deep golden, about 25 minutes. Cool for 10 minutes before cutting.",
                ],
            ),
        ],
    ),
    (
        "Kimchi Fried Rice",
        "Korean",
        "main",
        "eggs",
        [],
        2,
        10,
        10,
        "The best use for day-old rice.",
        [
            (
                None,
                [
                    "2 tablespoons neutral oil",
                    "1 cup chopped kimchi, plus 2 tablespoons juice",
                    "1 tablespoon gochujang",
                    "3 cups cooked rice, cold",
                    "1 teaspoon toasted sesame oil",
                    "2 large eggs",
                    "2 scallions, sliced",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Heat the oil in a skillet over medium-high heat. Fry the kimchi until it "
                        "starts to caramelize, about 3 minutes."
                    ),
                    (
                        "Stir in the gochujang and kimchi juice, then the rice. Press it into the "
                        "pan and cook without stirring until crisp underneath, about 4 minutes."
                    ),
                    "Drizzle with sesame oil. Fry the eggs in a separate pan.",
                    "Top each bowl with an egg and scallions.",
                ],
            ),
        ],
    ),
    (
        "Salsa Verde",
        "Mexican",
        "sauce/condiment",
        "none",
        ["vegetarian", "vegan", "gluten-free", "dairy-free"],
        8,
        10,
        10,
        "Broiled tomatillos make it smoky.",
        [
            (
                None,
                [
                    "1 pound tomatillos, husked",
                    "1/2 white onion",
                    "2 jalapeños",
                    "2 garlic cloves",
                    "1 cup cilantro",
                    "1 lime, juiced",
                    "1 teaspoon kosher salt",
                ],
            ),
        ],
        [
            (
                None,
                [
                    (
                        "Broil the tomatillos, onion, jalapeños and garlic until blistered, about "
                        "8 minutes, turning once."
                    ),
                    "Blend with the cilantro, lime juice and salt until mostly smooth.",
                ],
            ),
        ],
    ),
    (
        "Sheet-Pan Gnocchi with Sausage and Peppers",
        "Italian",
        "main",
        "pork",
        [],
        4,
        10,
        25,
        "Shelf-stable gnocchi roast crisp, no boiling required.",
        [
            (
                None,
                [
                    "1 pound shelf-stable potato gnocchi",
                    "1 pound Italian sausage, in 1-inch pieces",
                    "2 bell peppers, sliced",
                    "1 red onion, in wedges",
                    "2 tablespoons olive oil",
                    "1 teaspoon dried oregano",
                    "1/2 cup grated Parmesan",
                ],
            ),
        ],
        [
            (
                None,
                [
                    "Heat the oven to 450°F.",
                    (
                        "Toss everything except the Parmesan on a sheet pan and spread out in one "
                        "layer."
                    ),
                    (
                        "Roast, stirring once, until the gnocchi are crisp and the sausage is "
                        "browned, about 25 minutes."
                    ),
                    "Shower with Parmesan and serve.",
                ],
            ),
        ],
    ),
    (
        "Banana Bread",
        "American",
        "bread",
        "eggs",
        ["vegetarian"],
        10,
        15,
        60,
        "For the bananas nobody got around to eating.",
        [
            (
                None,
                [
                    "3 very ripe bananas",
                    "1/2 cup unsalted butter, melted",
                    "3/4 cup brown sugar",
                    "1 large egg",
                    "1 teaspoon vanilla extract",
                    "1 teaspoon baking soda",
                    "1/2 teaspoon kosher salt",
                    "1 1/2 cups all-purpose flour",
                    "1/2 cup chopped walnuts",
                ],
            ),
        ],
        [
            (
                None,
                [
                    "Heat the oven to 350°F and butter a 9x5-inch loaf pan.",
                    "Mash the bananas, then stir in the butter, sugar, egg and vanilla.",
                    "Sprinkle in the baking soda and salt, then fold in the flour and walnuts.",
                    (
                        "Bake until a skewer comes out clean, about 60 minutes. Cool for 15 "
                        "minutes before turning out."
                    ),
                ],
            ),
        ],
    ),
]

FAVORITES = [
    "Chana Masala",
    "Lemon Chicken Orzo Soup",
    "Brown Butter Chocolate Chip Cookies",
    "Thai Basil Chicken",
    "Overnight Focaccia",
]
PEOPLE = ["Sam", "Riley"]
# Tags the demo presents as model-suggested (shown on the Changes page with their evidence).
AUTO_TAGS = {
    "Kimchi Fried Rice": [
        ("cuisine", "Korean", "Kimchi and gochujang are Korean staples."),
        ("protein", "eggs", "Two fried eggs are the only protein."),
    ],
    "Shakshuka": [
        ("diet", "vegetarian", "No meat or fish in any ingredient."),
        ("course", "breakfast", "Eggs baked in sauce, served from the pan: a brunch dish."),
    ],
    "Miso Butter Salmon": [("protein", "fish", "4 salmon fillets.")],
}
NEXT_TIME = {"Chana Masala": "Double the ginger and let the onions go even darker."}
PLAN = [
    "Lemon Chicken Orzo Soup",
    "Black Bean Tacos with Pickled Onions",
    "Miso Butter Salmon",
    "Chana Masala",
    "Sheet-Pan Gnocchi with Sausage and Peppers",
]
PANTRY = ["kosher salt", "black pepper", "olive oil", "neutral oil"]


def document(recipe: tuple) -> dict:
    title, *_rest, ingredient_sections, step_sections = recipe
    doc = {
        "title": title,
        "ingredient_sections": [
            {
                "id": f"ingredients_{n}",
                "heading": heading,
                "ingredients": [
                    {"id": f"i{n}{k:02d}", "source_text": line} for k, line in enumerate(lines, 1)
                ],
            }
            for n, (heading, lines) in enumerate(ingredient_sections, 1)
        ],
        "instruction_sections": [
            {
                "id": f"instructions_{n}",
                "heading": heading,
                "steps": [{"id": f"s{n}{k:02d}", "text": text} for k, text in enumerate(steps, 1)],
            }
            for n, (heading, steps) in enumerate(step_sections, 1)
        ],
    }
    return RecipeDocumentV2.model_validate(doc).model_dump(mode="json")


def main() -> None:
    conn = db.connect()
    db.init(conn)
    if conn.execute("SELECT count(*) FROM recipes").fetchone()[0]:
        sys.exit(f"{DATA} already has recipes; seed only a new, empty data dir")
    now = datetime.now(UTC)

    def ago(**delta) -> str:
        return (now - timedelta(**delta)).isoformat(timespec="seconds")

    people = {}
    for name in PEOPLE:
        people[name] = db.new_id()
        conn.execute(
            "INSERT INTO people(id, name, created_at) VALUES (?, ?, ?)", (people[name], name, ago())
        )

    ids: dict[str, str] = {}
    with db.tx(conn):
        for n, recipe in enumerate(RECIPES):
            title, cuisine, course, protein, diets, servings, prep, cook, description = recipe[:9]
            rid = ids[title] = db.new_id()
            stamp = ago(days=40 - 2 * n)
            conn.execute(
                "INSERT INTO recipes(id, slug, title, document, description, prep_minutes, "
                "cook_minutes, total_minutes, servings, origin, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo', ?, ?)",
                (
                    rid,
                    recipes.unique_slug(conn, title),
                    title,
                    db.dumps(document(recipe)),
                    description,
                    prep,
                    cook or None,
                    prep + cook,
                    servings,
                    stamp,
                    stamp,
                ),
            )
            auto = {(kind, value): evidence for kind, value, evidence in AUTO_TAGS.get(title, [])}
            tags = [("cuisine", cuisine), ("course", course), ("protein", protein)]
            tags += [("diet", diet) for diet in diets]
            for kind, value in tags:
                evidence = auto.get((kind, value))
                conn.execute(
                    "INSERT INTO recipe_tags(recipe_id, kind, value, source, confidence, evidence,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rid,
                        kind,
                        value,
                        "auto" if evidence else "manual",
                        0.9 if evidence else None,
                        evidence,
                        stamp,
                    ),
                )
                if evidence:
                    conn.execute(
                        "INSERT INTO changes(id, recipe_id, field, before, after, evidence, model,"
                        " prompt_version, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                        (
                            db.new_id(),
                            rid,
                            f"tag:{kind}",
                            db.dumps(value),
                            evidence,
                            "local text model",
                            "demo",
                            ago(hours=2 + n),
                        ),
                    )
        for title in FAVORITES:
            conn.execute(
                "INSERT INTO favorites(recipe_id, person_id, created_at) VALUES (?, '', ?)",
                (ids[title], ago(days=3)),
            )
        for title, text in NEXT_TIME.items():
            conn.execute(
                "INSERT INTO next_time_notes(id, recipe_id, person_id, text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (db.new_id(), ids[title], people["Sam"], text, ago(days=6)),
            )
        for days, title, person in [
            (2, "Chana Masala", "Sam"),
            (5, "Thai Basil Chicken", "Riley"),
            (9, "Chana Masala", "Riley"),
            (12, "Buttermilk Pancakes", "Sam"),
        ]:
            conn.execute(
                "INSERT INTO cook_log(id, recipe_id, person_id, cooked_on, make_again, created_at)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (
                    db.new_id(),
                    ids[title],
                    people[person],
                    (date.today() - timedelta(days=days)).isoformat(),
                    ago(days=days),
                ),
            )
        monday = date.today() - timedelta(days=date.today().weekday())
        for offset, title in enumerate(PLAN):
            conn.execute(
                "INSERT INTO plan_entries(id, day, recipe_id, servings, position, created_at) "
                "VALUES (?, ?, ?, NULL, 0, ?)",
                (db.new_id(), (monday + timedelta(days=offset)).isoformat(), ids[title], ago()),
            )
        for name in PANTRY:
            conn.execute(
                "INSERT INTO pantry_staples(name, created_at) VALUES (?, ?)", (name, ago())
            )
    planning.add_to_shopping(conn, [(ids[title], 1.0) for title in PLAN[:3]])
    first = conn.execute("SELECT id FROM shopping_items ORDER BY position LIMIT 3").fetchall()
    for row in first:
        conn.execute("UPDATE shopping_items SET checked = 1 WHERE id = ?", (row["id"],))
    conn.execute("DELETE FROM jobs")  # nothing for a worker to do: no model calls
    count = conn.execute("SELECT count(*) FROM recipes").fetchone()[0]
    print(f"seeded {count} recipes into {DATA / 'cookt.db'}")


if __name__ == "__main__":
    main()
