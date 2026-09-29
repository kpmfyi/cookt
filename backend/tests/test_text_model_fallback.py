"""Ported from recipe-table tests/test_text_model_fallback.py.

recipe-table exercised these through the scan API + worker; cookt calls the extraction
pipeline directly. The model fallback is always on (no ``use_llm`` opt-in).
"""

from __future__ import annotations

import pytest
from cookt.extraction.model import ModelUnavailable
from cookt.extraction.pipeline import ExtractionError, extract_from_text


class FakeCompletionClient:
    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt, schema, name):
        self.calls.append((prompt, name))
        if self.error is not None:
            raise self.error
        if self.payload is None:
            raise AssertionError("The local model should not have been called")
        return schema.model_validate(self.payload)


def test_structured_paste_stays_deterministic_without_calling_model():
    model = FakeCompletionClient()
    extraction = extract_from_text(
        """Ginger Noodles

Ingredients
8 oz noodles
2 tbsp soy sauce
1 tbsp grated ginger

Method
Cook the noodles until tender.
Toss with soy sauce and ginger.
""",
        client=model,
    )

    assert model.calls == []
    assert extraction.method == "text"
    assert extraction.document.title == "Ginger Noodles"
    assert extraction.coverage is None


def test_ambiguous_paste_automatically_calls_model():
    source = """Family Tomato Soup
Serves 4
Use the heavy saucepan.
2 tablespoons olive oil
4 cups crushed tomatoes
Heat the oil, add the tomatoes, and simmer for 20 minutes.
"""
    model = FakeCompletionClient(
        payload={
            "title": "Family Tomato Soup",
            "yield_text": "Serves 4",
            "prep_notes": ["Use the heavy saucepan."],
            "ingredients": [
                {"source_text": "2 tablespoons olive oil"},
                {"source_text": "4 cups crushed tomatoes"},
            ],
            "directions": [
                {
                    "section": None,
                    "text": "Heat the oil, add the tomatoes, and simmer for 20 minutes.",
                }
            ],
            "author": None,
        }
    )

    extraction = extract_from_text(source, client=model)

    assert len(model.calls) == 1
    assert source.strip() in model.calls[0][0]
    assert model.calls[0][1] == "conventional_recipe"
    assert extraction.method == "llm"
    assert extraction.document.title == "Family Tomato Soup"
    assert extraction.document.yield_text == "Serves 4"
    assert extraction.coverage is not None and extraction.coverage > 0.9


def test_incomplete_model_parse_fails_closed():
    source = """Weeknight Soup
Ingredients
1 onion
2 cups stock
1 tsp salt
Directions
1. Chop the onion.
Simmer the stock for 20 minutes.
3. Season with salt.
"""
    model = FakeCompletionClient(
        payload={
            "title": "Weeknight Soup",
            "yield_text": None,
            "prep_notes": [],
            "ingredients": [{"source_text": "1 onion"}],
            "directions": [
                {"section": None, "text": "1. Chop the onion."},
            ],
            "author": None,
        }
    )

    with pytest.raises(ExtractionError) as caught:
        extract_from_text(source, client=model)

    assert len(model.calls) == 2  # one constrained repair retry
    assert caught.value.code == "invalid_model_output"


def test_model_unavailability_fails_closed():
    model = FakeCompletionClient(error=ModelUnavailable("offline"))

    with pytest.raises(ExtractionError) as caught:
        extract_from_text(
            "A stew recipe copied from a notebook without recognizable section headings.",
            client=model,
        )

    assert len(model.calls) == 1
    assert caught.value.code == "inference_unavailable"


def test_semantic_clipboard_html_rescues_flattened_text_without_model():
    model = FakeCompletionClient()
    extraction = extract_from_text(
        "Fast Soup Ingredients 2 cups stock Directions Simmer for 10 minutes.",
        html="""<article>
          <h1>Fast Soup</h1>
          <h2>Ingredients</h2><ul><li>2 cups stock</li></ul>
          <h2>Directions</h2><ol><li>Simmer for 10 minutes.</li></ol>
        </article>""",
        client=model,
    )

    assert model.calls == []
    assert extraction.method == "text"
    assert extraction.document.title == "Fast Soup"


def test_invented_ingredient_is_rejected():
    source = """Grandma's Cocoa
1 cup milk
2 tbsp cocoa
Warm the milk and whisk in the cocoa.
"""
    model = FakeCompletionClient(
        payload={
            "title": "Grandma's Cocoa",
            "yield_text": None,
            "prep_notes": [],
            "ingredients": [
                {"source_text": "1 cup milk"},
                {"source_text": "2 tbsp cocoa"},
                {"source_text": "1 tsp vanilla"},
            ],
            "directions": [{"section": None, "text": "Warm the milk and whisk in the cocoa."}],
            "author": None,
        }
    )
    with pytest.raises(ExtractionError) as caught:
        extract_from_text(source, client=model)
    assert caught.value.code == "invalid_model_output"
    assert len(model.calls) == 2
