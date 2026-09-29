"""Tests for the high-level import API in ``cookt.extraction.pipeline``."""

from __future__ import annotations

import base64
import gzip
import io
import json
import zipfile
from pathlib import Path

import cookt.extraction.pipeline as pipeline
import httpx
import pytest
from cookt.extraction.parser import duration_minutes
from cookt.extraction.pipeline import (
    ExtractionError,
    canonicalize_url,
    extract_from_images,
    extract_from_paprika,
    extract_from_paprika_detailed,
    extract_from_text,
    extract_from_url,
)
from cookt.extraction.vision import PageTranscript, PhotoRecipe
from cookt.url_safety import FetchError, FetchResult
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
GRAPH_FIXTURE = FIXTURES / "jsonld_graph_recipe.html"
EXPORT_FIXTURE = FIXTURES / "paprika_export.html"


# --- canonicalize_url ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "HTTP://WWW.SeriousEats.com/best-chili/?utm_source=nl&utm_medium=email#comments",
            "https://seriouseats.com/best-chili",
        ),
        (
            "https://cooking.nytimes.com/recipes/1017256-pie?smid=ck-recipe-iOS-share&b=2&a=1",
            "https://cooking.nytimes.com/recipes/1017256-pie?a=1&b=2",
        ),
        (
            "https://example.com/r/?fbclid=abc&gclid=x&mc_cid=1&mc_eid=2&ref=pin&srsltid=q",
            "https://example.com/r",
        ),
        ("example.com/pie/", "https://example.com/pie"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com:443//a//b/", "https://example.com/a/b"),
        ("https://www.example.com:8443/pie", "https://example.com:8443/pie"),
    ],
)
def test_canonicalize_url(raw: str, expected: str):
    assert canonicalize_url(raw) == expected


def test_canonicalize_url_rejects_non_http():
    with pytest.raises(ExtractionError):
        canonicalize_url("ftp://example.com/pie")


def test_duration_minutes_handles_iso_and_text():
    assert duration_minutes("PT1H5M") == 65
    assert duration_minutes("P0DT0H40M") == 40
    assert duration_minutes("PT90M") == 90
    assert duration_minutes("PT1H30M0S") == 90
    assert duration_minutes("P1D") == 1_440
    assert duration_minutes("1 hr 20 mins") == 80
    assert duration_minutes("10 mins") == 10
    assert duration_minutes("PT0S") is None
    assert duration_minutes("") is None
    assert duration_minutes("overnight") is None


# --- extract_from_url ---------------------------------------------------------------------


def _fake_fetch(html: str, url: str = "https://www.kitchen.example.test/brown-butter-cornbread/"):
    def fetch(requested: str, **kwargs):
        assert kwargs.get("allow_restricted") is True
        return FetchResult(
            html=html,
            canonical_url=url,
            content_hash="0" * 64,
            source_site="kitchen.example.test",
            restricted="password" in html,
            final_url=url,
        )

    return fetch


def test_url_import_surfaces_nutrition_times_and_publisher_metadata(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_recipe_page", _fake_fetch(GRAPH_FIXTURE.read_text()))

    extraction = extract_from_url(
        "https://www.kitchen.example.test/brown-butter-cornbread/?utm_source=pin"
    )

    assert extraction.method == "jsonld"
    assert extraction.document.title == "Brown Butter Skillet Cornbread"
    assert extraction.canonical_url == "https://kitchen.example.test/brown-butter-cornbread"
    assert extraction.source_site == "kitchen.example.test"
    assert extraction.source_author == "Rosa Miller"
    assert extraction.nutrition == {
        "@type": "NutritionInformation",
        "servingSize": "1 wedge",
        "calories": "310 kcal",
        "fatContent": "14 g",
        "proteinContent": "6 g",
        "sodiumContent": "420 mg",
    }
    assert (extraction.prep_minutes, extraction.cook_minutes, extraction.total_minutes) == (
        15,
        25,
        40,
    )
    assert extraction.yield_text == "8"
    assert extraction.image_urls == [
        "https://cdn.kitchen.example.test/cornbread-1x1.jpg",
        "https://cdn.kitchen.example.test/cornbread-16x9.jpg",
        "https://cdn.kitchen.example.test/og/cornbread.jpg",
    ]
    assert extraction.hints["cuisine"] == ["American", "Southern"]
    assert extraction.hints["category"] == ["Side Dish", "Bread"]
    assert extraction.hints["keywords"] == ["cornbread", "skillet", "brown butter"]
    assert extraction.hints["site_name"] == "Example Kitchen"
    assert extraction.hints["diets"] == ["VegetarianDiet"]
    assert extraction.hints["description"].endswith("brown butter & honey.")
    assert extraction.coverage is None
    ingredients = [
        item.source_text
        for section in extraction.document.ingredient_sections
        for item in section.ingredients
    ]
    assert len(ingredients) == 6
    steps = [
        step.text for section in extraction.document.instruction_sections for step in section.steps
    ]
    assert steps == [
        "Heat the oven to 425°F with a 10-inch cast-iron skillet inside.",
        "Melt the butter in a saucepan and cook until nutty & brown, about 5 minutes.",
        "Whisk the cornmeal, flour, honey, buttermilk, eggs and most of the butter.",
        "Pour into the hot skillet and bake 20 to 25 minutes, until golden.",
    ]
    assert [section.heading for section in extraction.document.instruction_sections] == [
        "Brown the butter",
        "Bake",
    ]


def test_url_import_string_instructions_split_on_breaks(monkeypatch):
    html = """<script type="application/ld+json">{"@type": "Recipe", "name": "Toast",
      "recipeIngredient": ["1 slice bread", "1 tsp butter"],
      "recipeInstructions": "Toast the bread.\\nSpread with butter.\\n\\nEat warm."}</script>"""
    monkeypatch.setattr(pipeline, "fetch_recipe_page", _fake_fetch(html))
    extraction = extract_from_url("https://kitchen.example.test/toast")
    steps = [s.text for sec in extraction.document.instruction_sections for s in sec.steps]
    assert steps == ["Toast the bread.", "Spread with butter.", "Eat warm."]


class FakeCompletionClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[str] = []

    def complete(self, prompt, schema, name):
        self.calls.append(prompt)
        return schema.model_validate(self.payload)


def test_url_import_falls_back_to_model_when_no_structured_data(monkeypatch):
    body = (
        "<html><body><nav>Home</nav><main><h1>Lentil Soup</h1>"
        + "<p>My family has loved this soup for years and years.</p>" * 5
        + "<h2>Ingredients</h2><ul><li>1 cup lentils</li><li>4 cups stock</li></ul>"
        + "<h2>Instructions</h2><ol><li>Simmer the lentils in stock for 25 minutes.</li></ol>"
        + "</main></body></html>"
    )
    monkeypatch.setattr(pipeline, "fetch_recipe_page", _fake_fetch(body))
    model = FakeCompletionClient(
        {
            "title": "Lentil Soup",
            "yield_text": None,
            "prep_notes": [],
            "ingredients": [{"source_text": "1 cup lentils"}, {"source_text": "4 cups stock"}],
            "directions": [
                {"section": None, "text": "Simmer the lentils in stock for 25 minutes."}
            ],
            "author": None,
        }
    )

    extraction = extract_from_url("https://kitchen.example.test/lentil-soup", client=model)

    assert len(model.calls) == 1
    assert "Home" not in model.calls[0]
    assert extraction.method == "llm"
    assert extraction.coverage == pytest.approx(1.0)
    assert any("local model" in warning for warning in extraction.warnings)


def test_url_import_restricted_page_without_structured_data_is_rejected(monkeypatch):
    body = "<html><body><input type='password'><p>Subscribe to continue</p></body></html>"
    monkeypatch.setattr(pipeline, "fetch_recipe_page", _fake_fetch(body))
    with pytest.raises(ExtractionError) as caught:
        extract_from_url("https://kitchen.example.test/paywalled")
    assert caught.value.code == "restricted_page"


def test_url_import_restricted_page_with_json_ld_still_imports(monkeypatch):
    html = GRAPH_FIXTURE.read_text().replace("<body>", "<body><form><input type='password'></form>")
    monkeypatch.setattr(pipeline, "fetch_recipe_page", _fake_fetch(html))
    extraction = extract_from_url("https://kitchen.example.test/brown-butter-cornbread")
    assert extraction.method == "jsonld"
    assert any("paywalled" in warning for warning in extraction.warnings)


def test_url_import_maps_fetch_errors(monkeypatch):
    def failing(*_args, **_kwargs):
        raise FetchError("browser_challenge", "This site requires browser verification")

    monkeypatch.setattr(pipeline, "fetch_recipe_page", failing)
    with pytest.raises(ExtractionError) as caught:
        extract_from_url("https://kitchen.example.test/x")
    assert caught.value.code == "browser_challenge"


def test_text_import_with_clipboard_json_ld_uses_structured_data():
    extraction = extract_from_text(
        "Brown Butter Skillet Cornbread ...",
        html=GRAPH_FIXTURE.read_text(),
        source_url="https://www.kitchen.example.test/brown-butter-cornbread/",
    )
    assert extraction.method == "jsonld"
    assert extraction.nutrition and extraction.nutrition["calories"] == "310 kcal"
    assert extraction.canonical_url == "https://kitchen.example.test/brown-butter-cornbread"


def test_text_import_parses_times_from_plain_text_notes():
    extraction = extract_from_text(
        """Quick Rice
Prep Time: 5 mins
Cook Time: 1 hr 5 mins

Ingredients
1 cup rice
2 cups water

Directions
1. Simmer covered until tender.
"""
    )
    assert extraction.method == "text"
    assert (extraction.prep_minutes, extraction.cook_minutes) == (5, 65)


def test_empty_text_is_rejected():
    with pytest.raises(ExtractionError) as caught:
        extract_from_text("   ")
    assert caught.value.code == "empty_input"


# --- Paprika ------------------------------------------------------------------------------


def _png(width: int = 4, height: int = 4) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _paprika_entry(name: str, **extra) -> bytes:
    data = {
        "name": name,
        "ingredients": "For the dough:\n2 cups flour\n1 cup water\n\n1 tsp salt",
        "directions": "Mix everything.\n\nKnead for 10 minutes.\nBake at 450°F for 30 minutes.",
        "notes": "Great with soup.",
        "servings": "1 loaf",
        "source": "Grandma",
        "source_url": "https://www.bread.example.test/loaf/?utm_source=app",
        "prep_time": "15 mins",
        "cook_time": "30 mins",
        "total_time": "1 hr 45 mins",
        "categories": ["Bread"],
        "photo_data": base64.b64encode(_png()).decode(),
        "image_url": "https://cdn.bread.example.test/loaf.jpg",
        **extra,
    }
    return gzip.compress(json.dumps(data).encode())


def test_native_paprika_archive_imports_recipes_and_photos():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Loaf.paprikarecipe", _paprika_entry("Country Loaf"))
        archive.writestr("Broken.paprikarecipe", b"not gzip at all")

    extractions, issues = extract_from_paprika_detailed(buffer.getvalue(), "My.paprikarecipes")

    assert len(extractions) == 1 and len(issues) == 1
    loaf = extractions[0]
    assert loaf.method == "paprika"
    assert loaf.document.title == "Country Loaf"
    assert loaf.canonical_url == "https://bread.example.test/loaf"
    assert loaf.source_site == "bread.example.test"
    assert loaf.source_author == "Grandma"
    assert (loaf.prep_minutes, loaf.cook_minutes, loaf.total_minutes) == (15, 30, 105)
    assert loaf.yield_text == "1 loaf"
    assert loaf.image_urls == ["https://cdn.bread.example.test/loaf.jpg"]
    assert loaf.image_data and loaf.image_data[0][1] == "image/png"
    assert [s.heading for s in loaf.document.ingredient_sections] == ["For the dough"]
    assert len(loaf.document.ingredient_sections[0].ingredients) == 3
    steps = [s.text for sec in loaf.document.instruction_sections for s in sec.steps]
    assert steps == [
        "Mix everything.",
        "Knead for 10 minutes.",
        "Bake at 450°F for 30 minutes.",
    ]
    assert "Great with soup." in loaf.document.notes


def test_paprika_html_export_and_zip():
    html_results = extract_from_paprika(EXPORT_FIXTURE.read_bytes(), "export.html")
    assert [item.document.title for item in html_results] == ["Lemon Rice", "Quick Beans"]
    assert html_results[0].prep_minutes == 10
    assert html_results[0].canonical_url == "https://recipes.example.test/lemon-rice"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Recipes/family.html", EXPORT_FIXTURE.read_bytes())
    zip_results = extract_from_paprika(buffer.getvalue(), "Recipes.zip")
    assert len(zip_results) == 2


def test_paprika_garbage_raises_extraction_error():
    with pytest.raises(ExtractionError):
        extract_from_paprika(b"<html><body>nothing here</body></html>", "export.html")
    with pytest.raises(ExtractionError):
        extract_from_paprika(b"PK\x03\x04garbage", "broken.zip")


# --- photos -------------------------------------------------------------------------------


def _line(text: str, confidence: str = "high", recipe: bool = True) -> dict:
    return {"text": text, "confidence": confidence, "recipe_content": recipe}


PAGE_ONE = {
    "lines": [
        _line("142", recipe=False),
        _line("Aunt May's Oat Cookies"),
        _line("Makes 24"),
        _line("1 cup butter, softened"),
        _line("1 cup brown sugar"),
        _line("2 eggs"),
        _line("1 1/2 cups flour", "low"),
    ]
}
PAGE_TWO = {
    "lines": [
        _line("3 cups rolled oats"),
        _line("Cream the butter and sugar, then beat in"),
        _line("the eggs."),
        _line("Stir in flour and oats. Bake at 350 for 10 min.", "medium"),
        _line("143", recipe=False),
    ]
}
GOOD_RECIPE = {
    "title": "Aunt May's Oat Cookies",
    "yield_text": "Makes 24",
    "prep_notes": [],
    "ingredients": [
        {"text": "1 cup butter, softened", "confidence": "high"},
        {"text": "1 cup brown sugar", "confidence": "high"},
        {"text": "2 eggs", "confidence": "high"},
        {"text": "1 1/2 cups flour", "confidence": "high"},
        {"text": "3 cups rolled oats", "confidence": "high"},
    ],
    "directions": [
        {
            "section": None,
            "text": "Cream the butter and sugar, then beat in the eggs.",
            "confidence": "high",
        },
        {
            "section": None,
            "text": "Stir in flour and oats. Bake at 350 for 10 min.",
            "confidence": "high",
        },
    ],
    "author": None,
}


class FakeVisionClient:
    def __init__(self, pages: list[dict], recipes: list[dict]):
        self.pages = list(pages)
        self.recipes = list(recipes)
        self.calls: list[dict] = []

    def complete(self, prompt, schema, name, *, model=None, images=None, max_tokens=8000):
        self.calls.append({"name": name, "model": model, "images": images, "prompt": prompt})
        if schema is PageTranscript:
            return schema.model_validate(self.pages.pop(0))
        assert schema is PhotoRecipe
        return schema.model_validate(
            self.recipes.pop(0) if len(self.recipes) > 1 else self.recipes[0]
        )


def test_photo_extraction_spans_pages_and_flags_uncertain_lines():
    client = FakeVisionClient([PAGE_ONE, PAGE_TWO], [GOOD_RECIPE])
    photos = [(_png(), "image/png"), (_png(), "image/png")]

    extraction = extract_from_images(photos, client=client)

    assert [call["name"] for call in client.calls] == [
        "page_transcript",
        "page_transcript",
        "photo_recipe",
    ]
    assert all(call["images"] for call in client.calls[:2])
    assert client.calls[0]["model"] == pipeline.settings.vision_model
    assert "page 1 of 2" in client.calls[0]["prompt"]
    assert "142" not in client.calls[2]["prompt"]  # page furniture never reaches extraction
    assert extraction.method == "vision"
    assert extraction.document.title == "Aunt May's Oat Cookies"
    assert extraction.coverage == pytest.approx(1.0)
    assert extraction.uncertain_lines == ["1 1/2 cups flour"]
    assert "--- page 2 ---" in extraction.transcript
    assert extraction.transcript.count("\n") >= 10


def test_handwritten_photo_also_flags_medium_confidence_lines():
    client = FakeVisionClient([PAGE_ONE, PAGE_TWO], [GOOD_RECIPE])
    extraction = extract_from_images(
        [(_png(), "image/png"), (_png(), "image/png")], handwritten=True, client=client
    )
    assert "handwriting" in client.calls[0]["prompt"].casefold() or "handwritten" in (
        client.calls[0]["prompt"].casefold()
    )
    assert extraction.uncertain_lines == [
        "1 1/2 cups flour",
        "Stir in flour and oats. Bake at 350 for 10 min.",
    ]


def test_photo_extraction_rejects_low_coverage():
    dropped = {**GOOD_RECIPE, "ingredients": GOOD_RECIPE["ingredients"][:2]}
    dropped["directions"] = GOOD_RECIPE["directions"][:1]
    client = FakeVisionClient([PAGE_ONE, PAGE_TWO], [dropped])

    with pytest.raises(ExtractionError) as caught:
        extract_from_images([(_png(), "image/png"), (_png(), "image/png")], client=client)

    assert caught.value.code == "invalid_model_output"
    assert "covers only" in str(caught.value)
    assert [call["name"] for call in client.calls].count("photo_recipe") == 2


def test_photo_extraction_rejects_invented_lines():
    invented = {
        **GOOD_RECIPE,
        "ingredients": [
            *GOOD_RECIPE["ingredients"],
            {"text": "1 tsp vanilla", "confidence": "high"},
        ],
    }
    client = FakeVisionClient([PAGE_ONE, PAGE_TWO], [invented])
    with pytest.raises(ExtractionError) as caught:
        extract_from_images([(_png(), "image/png"), (_png(), "image/png")], client=client)
    assert "not copied from the transcript" in str(caught.value)


def test_photo_extraction_repairs_once_then_succeeds():
    invented = {
        **GOOD_RECIPE,
        "ingredients": [
            *GOOD_RECIPE["ingredients"],
            {"text": "1 tsp vanilla", "confidence": "high"},
        ],
    }
    client = FakeVisionClient([PAGE_ONE, PAGE_TWO], [invented, GOOD_RECIPE])
    extraction = extract_from_images([(_png(), "image/png"), (_png(), "image/png")], client=client)
    assert "invalid for this reason" in client.calls[-1]["prompt"]
    assert len(extraction.document.ingredient_sections[0].ingredients) == 5


def test_large_photos_are_downscaled_to_jpeg():
    client = FakeVisionClient(
        [{"lines": [_line(line) for line in ["Toast", "1 slice bread", "Toast the bread well."]]}],
        [
            {
                "title": "Toast",
                "yield_text": None,
                "prep_notes": [],
                "ingredients": [{"text": "1 slice bread", "confidence": "high"}],
                "directions": [
                    {"section": None, "text": "Toast the bread well.", "confidence": "high"}
                ],
                "author": None,
            }
        ],
    )
    big = _png(3200, 2400)
    extract_from_images([(big, "image/png")], client=client)
    sent, media_type = client.calls[0]["images"][0]
    assert media_type == "image/jpeg"
    with Image.open(io.BytesIO(sent)) as image:
        assert max(image.size) == 1600


def test_unsupported_photo_is_rejected():
    with pytest.raises(ExtractionError) as caught:
        extract_from_images([(b"%PDF-1.7 not an image", "application/pdf")])
    assert caught.value.code == "unsupported_image"


# --- social helpers -----------------------------------------------------------------------


def test_transcribe_audio_reports_whisper_unavailable(monkeypatch):
    monkeypatch.setattr(pipeline, "resolve_public_addresses", lambda *_a, **_k: {"93.184.216.34"})

    def down(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(pipeline.httpx, "get", down)
    with pytest.raises(ExtractionError, match="whisper unavailable"):
        pipeline.transcribe_audio_url("https://www.tiktok.com/@cook/video/123")


def test_social_helpers_only_accept_social_hosts():
    with pytest.raises(ExtractionError):
        pipeline.transcribe_audio_url("https://kitchen.example.test/recipe")
    with pytest.raises(ExtractionError):
        pipeline.fetch_social_caption("http://127.0.0.1/admin")


# --- opt-in live tests --------------------------------------------------------------------


@pytest.mark.local_model
def test_live_local_model_extracts_complete_conventional_recipe():
    source = """
Recipe: Weeknight tomato soup
Serves 4

Ingredients
- 2 tablespoons olive oil
- 1 onion, diced
- 3 cloves garlic, minced
- 28 ounces canned tomatoes
- 2 cups vegetable stock

Directions
1. Heat the oil over medium heat. Cook the onion for 6 to 8 minutes, until soft.
2. Add garlic and cook for 30 seconds.
3. Add tomatoes and stock. Simmer uncovered for 20 minutes, then blend until smooth.
"""
    # Headings present, but "Recipe:" prefix and bullets make the plain parser defer.
    extraction = extract_from_text(source)
    ingredients = [i for s in extraction.document.ingredient_sections for i in s.ingredients]
    assert len(ingredients) == 5
    directions = [st.text for s in extraction.document.instruction_sections for st in s.steps]
    assert len(directions) == 3
    assert any("6" in step and "8" in step for step in directions)


@pytest.mark.local_model
def test_live_vision_model_reads_a_rendered_recipe_card():
    from PIL import ImageDraw, ImageFont

    lines = [
        "Lemon Yogurt Cake",
        "Serves 8",
        "1 1/2 cups flour",
        "2 tsp baking powder",
        "1 cup plain yogurt",
        "3 eggs",
        "1 cup sugar",
        "Zest of 2 lemons",
        "1. Heat the oven to 350F and grease a loaf pan.",
        "2. Whisk everything together until smooth.",
        "3. Bake 50 minutes, until a skewer comes out clean.",
    ]
    image = Image.new("RGB", (1400, 1100), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default(size=40)
    for index, text in enumerate(lines):
        draw.text((60, 50 + index * 90), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)

    extraction = extract_from_images([(buffer.getvalue(), "image/jpeg")])

    ingredients = [i for s in extraction.document.ingredient_sections for i in s.ingredients]
    steps = [st.text for s in extraction.document.instruction_sections for st in s.steps]
    assert extraction.method == "vision"
    assert len(ingredients) == 6
    assert len(steps) == 3
    assert extraction.coverage and extraction.coverage >= 0.88
    assert "Lemon Yogurt Cake" in (extraction.transcript or "")


@pytest.mark.network
def test_live_url_import_serious_eats():
    extraction = extract_from_url(
        "https://www.seriouseats.com/the-food-lab-best-chocolate-chip-cookie-recipe"
    )
    assert extraction.method in {"jsonld", "microdata"}
    assert len(extraction.document.ingredient_sections[0].ingredients) >= 8
