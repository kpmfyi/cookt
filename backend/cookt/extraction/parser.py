"""Deterministic recipe parsing (ported from recipe-table ``extraction/parser.py``).

Covers Schema.org JSON-LD (incl. ``@graph``, ``mainEntity`` and ``HowToSection``),
microdata, Paprika HTML/ZIP exports and native ``.paprikarecipes`` archives, clipboard
HTML to text, and the conservative plain-text title/ingredients/directions parser.

Changes from recipe-table: publisher nutrition, image URLs, page URL and publisher name
are surfaced on ``SourceRecipeMetadata``; JSON-LD text is HTML-unescaped; string
instructions are split on their line/paragraph breaks; overlong JSON-LD steps are split
at sentence boundaries instead of failing the whole parse.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import html as html_lib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field

from ..document import Ingredient, MeasurementVariant
from ..images import ImageRole, detect_image_media_type


class ConventionalDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section: str | None = Field(default=None, max_length=160)
    text: str = Field(min_length=1, max_length=2_000)


class ConventionalRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=240)
    yield_text: str | None = Field(default=None, max_length=160)
    prep_notes: list[str] = Field(default_factory=list, max_length=30)
    ingredients: list[Ingredient] = Field(min_length=1, max_length=250)
    directions: list[ConventionalDirection] = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=255)


class SourceRecipeMetadata(BaseModel):
    """Useful publisher-supplied Schema.org fields kept separate from recipe text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: str | None = Field(default=None, max_length=2_000)
    prep_time: str | None = Field(default=None, max_length=80)
    cook_time: str | None = Field(default=None, max_length=80)
    total_time: str | None = Field(default=None, max_length=80)
    categories: list[str] = Field(default_factory=list, max_length=20)
    cuisines: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    diets: list[str] = Field(default_factory=list, max_length=20)
    date_published: str | None = Field(default=None, max_length=80)
    date_modified: str | None = Field(default=None, max_length=80)
    rating_value: float | None = Field(default=None, ge=0, le=5)
    rating_count: int | None = Field(default=None, ge=0)
    # Publisher-supplied Schema.org NutritionInformation, kept verbatim.
    nutrition: dict[str, Any] | None = None
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    url: str | None = Field(default=None, max_length=4_096)
    publisher: str | None = Field(default=None, max_length=255)


@dataclass(frozen=True, slots=True)
class ParseResult:
    recipe: ConventionalRecipe
    parser_path: str
    source_metadata: SourceRecipeMetadata = field(default_factory=SourceRecipeMetadata)


@dataclass(frozen=True, slots=True)
class ImportedRecipe:
    index: int
    recipe: ConventionalRecipe
    source_url: str | None
    content_hash: str
    images: tuple[ImportedImage, ...] = ()
    source_metadata: SourceRecipeMetadata = field(default_factory=SourceRecipeMetadata)


@dataclass(frozen=True, slots=True)
class ImportedImage:
    path: str
    role: ImageRole
    step_index: int | None = None
    caption: str | None = None
    source_url: str | None = None
    data: bytes | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class ImportIssue:
    index: int
    title: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RecipeExport:
    total: int
    recipes: list[ImportedRecipe]
    issues: list[ImportIssue]


UNITS = (
    r"cups?|c|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|pounds?|lbs?|lb|"
    r"grams?|g|kilograms?|kg|milliliters?|ml|liters?|l|cloves?|cans?|packages?|"
    r"pinches?|pieces?|sticks?|bunch(?:es)?"
)
INGREDIENT_RE = re.compile(
    rf"^(?P<quantity>(?:\d+\s+)?\d+/\d+|(?:\d+)?[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+(?:\.\d+)?)?"
    rf"\s*(?P<unit>{UNITS})?\b\s*(?P<name>.*)$",
    re.IGNORECASE,
)
DUAL_RE = re.compile(
    r"^(?P<us_qty>(?:\d+\s+)?\d+/\d+|(?:\d+)?[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+(?:\.\d+)?)"
    r"\s+(?P<us_unit>cups?|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|pounds?|lbs?|lb)"
    r"\s*/\s*(?P<metric_qty>\d+(?:\.\d+)?)\s+"
    r"(?P<metric_unit>grams?|g|kilograms?|kg|milliliters?|ml|liters?|l)\s+"
    r"(?P<name>.+)$",
    re.IGNORECASE,
)

PLAIN_INGREDIENT_HEADINGS = {
    "ingredient",
    "ingredient checklist",
    "ingredients",
    "what you’ll need",
    "what you need",
    "what you'll need",
    "you will need",
}
PLAIN_DIRECTION_HEADINGS = {
    "direction",
    "directions",
    "instruction",
    "instructions",
    "method",
    "how to make",
    "how to make it",
    "preparation",
    "preparation method",
    "procedure",
    "steps",
}
PLAIN_NOTE_HEADINGS = {
    "chef's note",
    "chef’s note",
    "chef's notes",
    "chef’s notes",
    "cook's note",
    "cook’s note",
    "make ahead",
    "make-ahead and storage",
    "note",
    "notes",
    "recipe notes",
    "recipe tips",
    "storage",
    "tips",
    "tips from our bakers",
    "variations",
}
PLAIN_EQUIPMENT_HEADINGS = {
    "equipment",
    "special equipment",
    "you’ll also need",
    "you'll also need",
}
PLAIN_STOP_HEADINGS = {
    "categories",
    "comments",
    "explore more",
    "more recipes",
    "nutrition",
    "nutrition facts",
    "nutrition info",
    "nutrition information",
    "nutritional information",
    "ratings",
    "read more",
    "related recipes",
    "reviews",
    "tools you may need",
    "you may also like",
}
PLAIN_UI_NOISE = {
    "add to shopping list",
    "deselect all",
    "jump to recipe",
    "print",
    "print recipe",
    "rate",
    "rate this recipe",
    "save",
    "save recipe",
    "select all",
    "share",
}
PLAIN_UI_NOISE_PREFIXES = (
    "ask ai",
    "cook mode",
    "keep screen awake",
    "recipe video",
    "watch how to make",
)
PLAIN_TIME_LABELS = {
    "active",
    "active time",
    "additional",
    "additional time",
    "bake",
    "bake time",
    "chill",
    "chill time",
    "chilling time",
    "cooling time",
    "cook",
    "cook time",
    "hands-on time",
    "inactive",
    "inactive time",
    "marinating time",
    "prep",
    "prep time",
    "proofing time",
    "ready in",
    "resting time",
    "rise time",
    "total",
    "total time",
}
PLAIN_YIELD_LABELS = {"makes", "serves", "serving", "servings", "yield"}
PLAIN_SPLIT_METADATA_LABELS = {
    *PLAIN_TIME_LABELS,
    *PLAIN_YIELD_LABELS,
    "author",
    "difficulty",
    "level",
}
PLAIN_METADATA_RE = re.compile(
    r"^(?P<label>yield|servings?|serves|makes|author|"
    r"(?:(?:active|additional|bake|chill(?:ing)?|cool(?:ing)?|cook|inactive|marinating|"
    r"prep|proof(?:ing)?|rest(?:ing)?|rise|total)(?:\s+time)?)|ready in|"
    r"(?:[a-z][a-z -]*\s+time))"
    r"\s*[:\-]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
PLAIN_INLINE_YIELD_RE = re.compile(
    r"^(?P<label>yield|servings?|serves|makes)\s+(?P<value>.+)$",
    re.IGNORECASE,
)
PLAIN_BYLINE_RE = re.compile(
    r"^by\s+(?P<author>.+?)(?:\s+(?:updated|published)(?:\s+on)?\b.*)?$",
    re.IGNORECASE,
)
PLAIN_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*•▪◦☐☑]|\[\s?[xX]?\])\s*")
PLAIN_STEP_PREFIX_RE = re.compile(r"^\s*(?:(?:step\s+)?\d{1,3}[.)]|[-*•▪◦])\s*", re.I)
PLAIN_STEP_ONLY_RE = re.compile(r"^step\s+\d{1,3}\s*:?$", re.IGNORECASE)
PLAIN_PAGE_NOISE_RE = re.compile(
    r"^(?:https?://\S+(?:\s+page\s+\d+(?:\s+of\s+\d+)?)?|"
    r"page\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+.+\|.+)$",
    re.IGNORECASE,
)
PLAIN_AUX_METADATA_RE = re.compile(
    r"^(?:category|course|cuisine|diet|difficulty|keywords?|level|rating)\s*[:\-]",
    re.IGNORECASE,
)
PLAIN_AMOUNT_ONLY_RE = re.compile(r"^(?:(?:\d+\s+)?\d+/\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])$")


def _clean_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or ""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_ingredient(source_text: str, ingredient_id: str) -> Ingredient:
    source = _clean_text(source_text)[:500]
    dual = DUAL_RE.match(source)
    if dual:
        values = dual.groupdict()
        name = values["name"].strip()[:240]
        us_display = f"{values['us_qty']} {values['us_unit']} {name}"[:240]
        metric_display = f"{values['metric_qty']} {values['metric_unit']} {name}"[:240]
        return Ingredient(
            id=ingredient_id,
            source_text=source,
            quantity=values["us_qty"],
            unit=values["us_unit"],
            name=name,
            us=MeasurementVariant(
                display_text=us_display,
                quantity=values["us_qty"],
                unit=values["us_unit"],
            ),
            metric=MeasurementVariant(
                display_text=metric_display,
                quantity=values["metric_qty"],
                unit=values["metric_unit"],
            ),
        )
    match = INGREDIENT_RE.match(source)
    if not match:
        return Ingredient(id=ingredient_id, source_text=source, name=source[:240])
    values = match.groupdict()
    return Ingredient(
        id=ingredient_id,
        source_text=source,
        quantity=values.get("quantity") or None,
        unit=values.get("unit") or None,
        name=(values.get("name") or source).strip()[:240],
    )


def _plain_heading(value: str) -> str:
    plain = re.sub(r"^#{1,6}\s*", "", value.strip())
    return re.sub(r"\s+", " ", plain.rstrip(":")).casefold()


def _plain_is_heading(value: str, headings: set[str]) -> bool:
    heading = _plain_heading(value)
    return any(
        heading == candidate
        or heading.startswith(f"{candidate}:")
        or heading.startswith(f"{candidate} (")
        for candidate in headings
    )


def _plain_noise(value: str) -> bool:
    heading = _plain_heading(value)
    return (
        heading in PLAIN_UI_NOISE
        or _plain_is_heading(value, PLAIN_STOP_HEADINGS)
        or bool(PLAIN_PAGE_NOISE_RE.fullmatch(value.strip()))
        or bool(PLAIN_AUX_METADATA_RE.match(value.strip()))
        or bool(re.fullmatch(r"level\s+(?:easy|intermediate|advanced)", heading))
        or heading.startswith(
            ("fact checked by ", "published on ", "reviewed by ", "tested by ", "updated on ")
        )
        or heading.startswith(PLAIN_UI_NOISE_PREFIXES)
    )


def _plain_title(
    lines: list[str], ingredient_index: int
) -> tuple[str, str | None, list[str], str | None]:
    title_candidates: list[str] = []
    yield_parts: list[str] = []
    notes: list[str] = []
    author: str | None = None
    preamble = lines[:ingredient_index]

    def record_metadata(label: str, value: str) -> None:
        nonlocal author
        normalized_label = _plain_heading(label)
        value = value.strip()
        if normalized_label == "yield":
            yield_parts.append(value)
        elif normalized_label in {"serving", "servings"}:
            yield_parts.append(value if "serving" in value.casefold() else f"{value} servings")
        elif normalized_label in {"serves", "makes"}:
            yield_parts.append(f"{normalized_label.title()} {value}")
        elif normalized_label == "author":
            author = value[:255]
        elif normalized_label in PLAIN_TIME_LABELS:
            notes.append(f"{normalized_label.title()}: {value}")

    index = 0
    while index < len(preamble):
        line = preamble[index]
        match = PLAIN_METADATA_RE.match(line)
        if match is None:
            match = PLAIN_INLINE_YIELD_RE.match(line)
        if match:
            record_metadata(match.group("label"), match.group("value"))
            index += 1
            continue
        heading = _plain_heading(line)
        if heading in PLAIN_SPLIT_METADATA_LABELS and index + 1 < len(preamble):
            value = preamble[index + 1]
            if len(value.split()) <= 8 and not _plain_noise(value):
                record_metadata(heading, value)
                index += 2
                continue
        if heading in {"difficulty", "level"}:
            index += 1
            continue
        if byline := PLAIN_BYLINE_RE.match(line):
            author = byline.group("author").strip()[:255]
            index += 1
            continue
        candidate = re.sub(r"^title\s*:\s*", "", line, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"^#{1,6}\s*", "", candidate)
        words = candidate.split()
        if (
            candidate
            and not _plain_noise(candidate)
            and _plain_heading(candidate) not in {"recipe", "recipe card"}
            and not re.match(r"^https?://", candidate, re.IGNORECASE)
            and not (len(words) > 5 and candidate.endswith((".", "!", "?")))
            and len(words) <= 8
        ):
            title_candidates.append(candidate)
        index += 1
    yield_text = " · ".join(dict.fromkeys(yield_parts))[:160] or None
    return (
        (title_candidates[-1] if title_candidates else "Untitled recipe")[:240],
        yield_text,
        notes[:30],
        author,
    )


def _plain_blocks(text: str) -> list[list[str]]:
    normalized = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\f", "\n\n")
        .replace("\u00a0", " ")
        .replace("\u00ad", "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    return [
        [re.sub(r"\s+", " ", line).strip() for line in block.split("\n") if line.strip()]
        for block in re.split(r"\n\s*\n", normalized.strip())
        if block.strip()
    ]


def clipboard_html_to_text(html: str, max_chars: int = 50_000) -> str:
    """Keep semantic block boundaries from a browser paste without rendering the HTML."""

    soup = BeautifulSoup(html, "html.parser")
    for element in soup(
        [
            "audio",
            "button",
            "canvas",
            "embed",
            "iframe",
            "img",
            "input",
            "noscript",
            "object",
            "picture",
            "script",
            "select",
            "style",
            "svg",
            "template",
            "textarea",
            "video",
        ]
    ):
        element.decompose()
    for element in soup.find_all(
        lambda tag: (
            isinstance(tag, Tag)
            and (tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).casefold() == "true")
        )
    ):
        element.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(
        [
            "address",
            "article",
            "aside",
            "blockquote",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "li",
            "main",
            "p",
            "section",
            "table",
            "tr",
        ]
    ):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in soup.get_text(" ").splitlines()
        if line.strip()
    ][:2_000]
    return unicodedata.normalize("NFC", "\n\n".join(lines))[:max_chars]


def clipboard_text_overlaps(plain_text: str, semantic_text: str) -> bool:
    """Reject clipboard HTML whose visible words do not match the user's plain paste."""

    plain_words = set(re.findall(r"[\w¼-¾]+", plain_text.casefold()))
    semantic_words = set(re.findall(r"[\w¼-¾]+", semantic_text.casefold()))
    if not plain_words or not semantic_words:
        return False
    return len(plain_words & semantic_words) / len(plain_words) >= 0.55


def parse_plain_text_recipe(text: str) -> ParseResult | None:
    """Parse the common title / ingredients / directions shape without inference.

    The parser stays deliberately conservative. Ambiguous pasted prose is handed to the
    opt-in local model instead of silently producing a misleading recipe.
    """

    blocks = _plain_blocks(text)
    lines = [line for block in blocks for line in block]
    if len(lines) < 3:
        return None

    ingredient_heading_count = sum(
        _plain_heading(line) in PLAIN_INGREDIENT_HEADINGS for line in lines
    )
    direction_heading_count = sum(
        _plain_heading(line) in PLAIN_DIRECTION_HEADINGS for line in lines
    )
    if ingredient_heading_count > 1 and direction_heading_count > 1:
        return None

    ingredient_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _plain_heading(line) in PLAIN_INGREDIENT_HEADINGS
        ),
        -1,
    )
    direction_index = next(
        (
            index
            for index, line in enumerate(lines)
            if index > ingredient_index and _plain_heading(line) in PLAIN_DIRECTION_HEADINGS
        ),
        -1,
    )

    if ingredient_index < 0 or direction_index < 0:
        # A compact personal note often uses three blank-line-separated blocks without
        # headings: title, ingredients, then directions.
        if len(blocks) < 3 or len(blocks[1]) < 1 or len(blocks[2]) < 1:
            return None
        preamble = blocks[0]
        ingredient_lines = blocks[1]
        direction_lines = [line for block in blocks[2:] for line in block]
        title, yield_text, notes, author = _plain_title([*preamble, "Ingredients"], len(preamble))
    else:
        title, yield_text, notes, author = _plain_title(lines, ingredient_index)
        ingredient_lines = lines[ingredient_index + 1 : direction_index]
        direction_lines = lines[direction_index + 1 :]

    if title == "Untitled recipe":
        return None

    clean_ingredients: list[str] = []
    equipment: list[str] = []
    skip_ingredient_tail = False
    ingredient_mode = "ingredients"
    pending_amount: str | None = None
    for line in ingredient_lines:
        heading = _plain_heading(line)
        if _plain_is_heading(line, PLAIN_STOP_HEADINGS):
            skip_ingredient_tail = True
            continue
        if _plain_noise(line) or heading in PLAIN_INGREDIENT_HEADINGS:
            continue
        if skip_ingredient_tail:
            continue
        if _plain_is_heading(line, PLAIN_EQUIPMENT_HEADINGS):
            ingredient_mode = "equipment"
            continue
        value = PLAIN_LIST_PREFIX_RE.sub("", line).strip()
        if not value or _plain_heading(value) in PLAIN_NOTE_HEADINGS:
            continue
        if ingredient_mode == "equipment":
            equipment.append(value)
            continue
        # Short labels inside ingredient lists are useful visually but the conventional
        # intermediate format has no ingredient-section field. Do not turn them into food.
        if value.endswith(":") and len(value.split()) <= 5:
            continue
        if PLAIN_AMOUNT_ONLY_RE.fullmatch(value):
            if pending_amount is not None:
                return None
            pending_amount = value
            continue
        if pending_amount:
            value = f"{pending_amount} {value}"
            pending_amount = None
        clean_ingredients.append(value[:500])

    if pending_amount:
        return None

    directions: list[ConventionalDirection] = []
    direction_section: str | None = None
    post_section: str | None = None
    recipe_notes: list[str] = []
    saw_numbered_step = False
    saw_unnumbered_step = False
    for line in direction_lines:
        heading = _plain_heading(line)
        if _plain_is_heading(line, PLAIN_STOP_HEADINGS):
            break
        if _plain_is_heading(line, PLAIN_EQUIPMENT_HEADINGS):
            post_section = "equipment"
            continue
        if _plain_is_heading(line, PLAIN_NOTE_HEADINGS):
            post_section = "notes"
            continue
        if (
            _plain_noise(line)
            or heading in PLAIN_DIRECTION_HEADINGS
            or PLAIN_STEP_ONLY_RE.fullmatch(line.strip())
        ):
            continue
        if PLAIN_BYLINE_RE.match(line) or PLAIN_METADATA_RE.match(line):
            return None
        if post_section:
            value = PLAIN_LIST_PREFIX_RE.sub("", line).strip()
            if not value:
                continue
            if post_section == "equipment":
                equipment.append(value)
            else:
                recipe_notes.append(value)
            continue
        if line.endswith(":") and len(line.split()) <= 6:
            direction_section = line.rstrip(":").strip()[:160]
            continue
        value = PLAIN_STEP_PREFIX_RE.sub("", line).strip()
        if value:
            numbered = bool(re.match(r"^\s*(?:(?:step\s+)?\d{1,3}[.)])\s+", line, re.IGNORECASE))
            saw_numbered_step = saw_numbered_step or numbered
            saw_unnumbered_step = saw_unnumbered_step or not numbered
            directions.append(ConventionalDirection(section=direction_section, text=value[:2_000]))

    if equipment:
        notes.append(f"Equipment: {' '.join(equipment)}")
    notes.extend(f"Note: {value}" for value in recipe_notes)

    if not clean_ingredients or not directions or (saw_numbered_step and saw_unnumbered_step):
        return None
    try:
        recipe = ConventionalRecipe(
            title=title,
            yield_text=yield_text,
            prep_notes=notes[:30],
            ingredients=[
                parse_ingredient(value, f"i{index:03d}")
                for index, value in enumerate(clean_ingredients[:250], 1)
            ],
            directions=directions[:500],
            author=author,
        )
    except ValueError:
        return None
    return ParseResult(recipe=recipe, parser_path="plain_text")


def _type_contains(value: Any, expected: str) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(str(item).rsplit("/", 1)[-1] == expected for item in values if item)


def _walk_json_ld(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _walk_json_ld(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _walk_json_ld(value["@graph"])
        for key in ("mainEntity", "mainEntityOfPage"):
            if isinstance(value.get(key), dict | list):
                yield from _walk_json_ld(value[key])


def _load_json_ld(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    # Some publishers emit raw control characters inside JSON-LD strings.
    try:
        return json.loads(re.sub(r"[\x00-\x1f]+", " ", raw or ""))
    except (json.JSONDecodeError, TypeError):
        return None


def _find_recipe_json_ld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        data = _load_json_ld(script.string or script.get_text())
        if data is None:
            continue
        for item in _walk_json_ld(data):
            if _type_contains(item.get("@type"), "Recipe"):
                return item
    return None


_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _json_text(value: Any) -> str:
    """Clean a JSON-LD string: decode HTML entities and drop inline markup."""

    text = _clean_text(value)
    if "&" in text:
        text = html_lib.unescape(html_lib.unescape(text))
    if _HTML_TAG.search(text):
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return _clean_text(text)


def _author_name(value: Any) -> str | None:
    if isinstance(value, list):
        names = [name for item in value if (name := _author_name(item))]
        return ", ".join(names) or None
    if isinstance(value, dict):
        return _json_text(value.get("name")) or None
    return _json_text(value) or None


def _metadata_list(value: Any, *, comma_separated: bool = False) -> list[str]:
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("name") or item.get("@id")
        cleaned = _json_text(item)
        if not cleaned:
            continue
        candidates = re.split(r"\s*,\s*", cleaned) if comma_separated else [cleaned]
        for candidate in candidates:
            if "schema.org/" in candidate:
                candidate = candidate.rstrip("/").rsplit("/", 1)[-1]
            if candidate:
                output.append(candidate[:120])
    return list(dict.fromkeys(output))


def _metadata_text(value: Any) -> str:
    return _json_text(value)


def _number(value: Any, cast):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _rating_value(value: Any) -> float | None:
    rating = _number(value, float)
    return rating if rating is not None and 0 <= rating <= 5 else None


def _rating_count(value: Any) -> int | None:
    count = _number(value, int)
    return count if count is not None and count >= 0 else None


def _json_ld_nutrition(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, dict)), None)
    if not isinstance(value, dict):
        return None
    nutrition = {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {}) and isinstance(item, str | int | float | bool | dict)
    }
    return nutrition if any(not key.startswith("@") for key in nutrition) else None


def _json_ld_image_urls(value: Any) -> list[str]:
    urls: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
            if isinstance(item, list):
                urls.extend(_json_ld_image_urls(item))
                continue
        if isinstance(item, str) and item.strip().startswith(("http://", "https://", "//", "/")):
            urls.append(item.strip()[:4_096])
    return list(dict.fromkeys(urls))[:20]


def _json_ld_url(data: dict[str, Any]) -> str | None:
    for key in ("url", "mainEntityOfPage", "@id"):
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value.strip()[:4_096]
    return None


def _json_ld_publisher(data: dict[str, Any]) -> str | None:
    publisher = data.get("publisher")
    if isinstance(publisher, list):
        publisher = next(iter(publisher), None)
    if isinstance(publisher, dict):
        return _json_text(publisher.get("name"))[:255] or None
    if isinstance(publisher, str):
        return _json_text(publisher)[:255] or None
    return None


def _source_metadata_from_json_ld(data: dict[str, Any]) -> SourceRecipeMetadata:
    rating = data.get("aggregateRating")
    rating = rating if isinstance(rating, dict) else {}
    return SourceRecipeMetadata(
        description=_metadata_text(data.get("description"))[:2_000] or None,
        prep_time=_clean_text(data.get("prepTime"))[:80] or None,
        cook_time=_clean_text(data.get("cookTime"))[:80] or None,
        total_time=_clean_text(data.get("totalTime"))[:80] or None,
        categories=_metadata_list(data.get("recipeCategory"), comma_separated=True)[:20],
        cuisines=_metadata_list(data.get("recipeCuisine"), comma_separated=True)[:20],
        keywords=_metadata_list(data.get("keywords"), comma_separated=True)[:40],
        diets=_metadata_list(data.get("suitableForDiet"))[:20],
        date_published=_clean_text(data.get("datePublished"))[:80] or None,
        date_modified=_clean_text(data.get("dateModified"))[:80] or None,
        rating_value=_rating_value(rating.get("ratingValue")),
        rating_count=_rating_count(rating.get("ratingCount") or rating.get("reviewCount")),
        nutrition=_json_ld_nutrition(data.get("nutrition")),
        image_urls=_json_ld_image_urls(data.get("image")),
        url=_json_ld_url(data),
        publisher=_json_ld_publisher(data),
    )


_ISO_DURATION = re.compile(
    r"^-?P(?:(?P<years>\d+(?:[.,]\d+)?)Y)?(?:(?P<months>\d+(?:[.,]\d+)?)M)?"
    r"(?:(?P<weeks>\d+(?:[.,]\d+)?)W)?(?:(?P<days>\d+(?:[.,]\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:[.,]\d+)?)H)?(?:(?P<minutes>\d+(?:[.,]\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:[.,]\d+)?)S)?)?$",
    re.IGNORECASE,
)
_TEXT_DURATION = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>days?|d|hours?|hrs?|h|minutes?|mins?|m)\b",
    re.IGNORECASE,
)


def duration_minutes(value: Any) -> int | None:
    """ISO 8601 (``PT1H5M``, ``P0DT0H20M``) or plain text (``1 hr 20 mins``) to minutes."""

    text = _clean_text(value)
    if not text:
        return None
    iso = _ISO_DURATION.match(text.replace(" ", ""))
    if iso and any(iso.groupdict().values()):
        parts = {
            key: float(item.replace(",", ".")) if item else 0.0
            for key, item in iso.groupdict().items()
        }
        total = (
            parts["years"] * 525_600
            + parts["months"] * 43_200
            + parts["weeks"] * 10_080
            + parts["days"] * 1_440
            + parts["hours"] * 60
            + parts["minutes"]
            + parts["seconds"] / 60
        )
        return round(total) if 0 < total < 525_600 else None
    total = 0.0
    matched = False
    for match in _TEXT_DURATION.finditer(text):
        matched = True
        number = float(match.group("value").replace(",", "."))
        unit = match.group("unit").casefold()
        if unit.startswith("d"):
            total += number * 1_440
        elif unit.startswith("h"):
            total += number * 60
        else:
            total += number
    return round(total) if matched and 0 < total < 525_600 else None


def _duration_label(value: str) -> str:
    match = re.fullmatch(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?", value or "")
    if not match:
        return _clean_text(value)
    hours, minutes = (int(part or 0) for part in match.groups())
    pieces = []
    if hours:
        pieces.append(f"{hours} hr")
    if minutes:
        pieces.append(f"{minutes} min")
    return " ".join(pieces)


def _string_instruction_values(value: str) -> list[str]:
    """Split one string of instructions on the publisher's own paragraph/line breaks."""

    raw = html_lib.unescape(value) if "&" in value else value
    if _HTML_TAG.search(raw):
        soup = BeautifulSoup(raw, "html.parser")
        blocks = [node for node in soup.find_all(["li", "p"]) if isinstance(node, Tag)]
        if blocks:
            return [text for node in blocks if (text := _clean_text(node.get_text(" ")))]
        for br in soup.find_all("br"):
            br.replace_with("\n")
        raw = soup.get_text()
    return [text for line in re.split(r"\n+", raw) if (text := _clean_text(line))]


def _direction_items(text: str, section: str | None) -> list[ConventionalDirection]:
    return [
        ConventionalDirection(section=section, text=chunk)
        for chunk in _bounded_instruction_values(text)
    ]


def _directions(value: Any, section: str | None = None) -> list[ConventionalDirection]:
    if isinstance(value, str):
        output: list[ConventionalDirection] = []
        for text in _string_instruction_values(value):
            output.extend(_direction_items(text, section))
        return output
    if isinstance(value, list):
        output: list[ConventionalDirection] = []
        for item in value:
            output.extend(_directions(item, section))
        return output
    if not isinstance(value, dict):
        return []
    if _type_contains(value.get("@type"), "HowToSection") or (
        not value.get("text") and isinstance(value.get("itemListElement"), list)
    ):
        heading = _json_text(value.get("name"))[:160] or section
        nested = value.get("itemListElement") or value.get("steps") or []
        return _directions(nested, heading)
    text = _json_text(value.get("text") or value.get("name"))
    return _direction_items(text, section) if text else []


def _json_ld_to_recipe(data: dict) -> ConventionalRecipe:
    raw_ingredients = data.get("recipeIngredient") or data.get("ingredients") or []
    if isinstance(raw_ingredients, str):
        raw_ingredients = [raw_ingredients]
    ingredient_values = [text for item in raw_ingredients if (text := _json_text(item))]
    ingredients = [
        parse_ingredient(text, f"i{index:03d}")
        for index, text in enumerate(ingredient_values[:250], 1)
    ]
    raw_yield = data.get("recipeYield")
    if isinstance(raw_yield, list):
        raw_yield = next((_json_text(item) for item in raw_yield if _json_text(item)), None)
    notes = []
    for label, key in (("Prep", "prepTime"), ("Cook", "cookTime"), ("Total", "totalTime")):
        duration = _duration_label(_clean_text(data.get(key)))
        if duration:
            notes.append(f"{label}: {duration}")
    return ConventionalRecipe(
        title=_json_text(data.get("name"))[:240] or "Untitled recipe",
        yield_text=_json_text(raw_yield)[:160] or None,
        prep_notes=notes,
        ingredients=ingredients,
        directions=_directions(data.get("recipeInstructions") or data.get("instructions"))[:500],
        author=(_author_name(data.get("author")) or "")[:255] or None,
    )


def _property_value(element: Tag) -> str:
    return _clean_text(
        element.get("content")
        or element.get("datetime")
        or element.get("value")
        or element.get_text(" ", strip=True)
    )


def _property_nodes(root: Tag | BeautifulSoup, prop: str) -> list[Tag]:
    pattern = re.compile(rf"(?:^|\s){re.escape(prop)}(?:\s|$)", re.IGNORECASE)
    return [item for item in root.find_all(attrs={"itemprop": pattern}) if isinstance(item, Tag)]


def _instruction_values(root: Tag) -> list[str]:
    step_nodes = _property_nodes(root, "step")
    if step_nodes:
        values = [_property_value(node) for node in step_nodes]
    else:
        values = []
        for container in _property_nodes(root, "recipeInstructions"):
            blocks = [
                child
                for child in container.find_all(["p", "li"], recursive=True)
                if isinstance(child, Tag)
            ]
            if blocks:
                values.extend(_property_value(block) for block in blocks)
            else:
                values.append(_property_value(container))
    ignored = {"instruction checklist", "instructions checklist", "instructions", "directions"}
    return [value for value in values if value and value.casefold().rstrip(":") not in ignored]


def _bounded_instruction_values(value: str, max_length: int = 2_000) -> list[str]:
    """Split an overlong exported direction without dropping its wording."""

    if len(value) <= max_length:
        return [value]
    sentences = re.split(r"(?<=[.!?])\s+(?=(?:\d{1,3}[.)]\s*)?[A-Z])", value)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        while len(sentence) > max_length:
            split_at = sentence.rfind(" ", 0, max_length + 1)
            if split_at < max_length // 2:
                split_at = max_length
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:split_at].strip())
            sentence = sentence[split_at:].strip()
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_length:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _comment_values(root: Tag) -> list[str]:
    values: list[str] = []
    for container in _property_nodes(root, "comment"):
        blocks = [
            child
            for child in container.find_all(["p", "li"], recursive=True)
            if isinstance(child, Tag)
        ]
        candidates = blocks or [container]
        values.extend(_property_value(item) for item in candidates)
    return [value[:10_000] for value in values if value][:20]


def _export_direction_values(root: Tag) -> list[ConventionalDirection]:
    directions: list[ConventionalDirection] = []
    section: str | None = None
    for value in _instruction_values(root):
        if len(value) <= 160 and value.endswith(":"):
            section = value.rstrip(":").strip()
            continue
        directions.extend(
            ConventionalDirection(section=section, text=chunk)
            for chunk in _bounded_instruction_values(value)
        )
    return directions


def _microdata_to_recipe(
    soup: BeautifulSoup,
    *,
    allow_incomplete: bool = False,
) -> ConventionalRecipe | None:
    root = soup.find(
        attrs={"itemtype": re.compile(r"(?:https?://)?schema\.org/Recipe", re.IGNORECASE)}
    )
    if root is None:
        root = soup.find(attrs={"typeof": re.compile(r"(?:schema:)?Recipe", re.IGNORECASE)})
    if not isinstance(root, Tag):
        return None

    def first(prop: str) -> str:
        item = root.find(attrs={"itemprop": prop}) or root.find(attrs={"property": prop})
        return _property_value(item) if isinstance(item, Tag) else ""

    ingredient_nodes = root.find_all(
        attrs={"itemprop": re.compile(r"^(recipeIngredient|ingredients)$")}
    )
    ingredients = [
        parse_ingredient(value, f"i{index:03d}")
        for index, node in enumerate(ingredient_nodes, 1)
        if (value := _property_value(node))
    ]
    directions = _export_direction_values(root)
    if (not ingredients or not directions) and not allow_incomplete:
        return None
    notes = []
    for label, prop in (("Prep", "prepTime"), ("Cook", "cookTime"), ("Total", "totalTime")):
        if value := first(prop):
            notes.append(f"{label}: {_duration_label(value)}")
    notes.extend(f"Note: {value}" for value in _comment_values(root))
    if not ingredients:
        notes.append("Import warning: The original export did not include ingredients.")
        ingredients = [
            parse_ingredient("Ingredients were not included in the original export.", "i001")
        ]
    if not directions:
        notes.append("Import warning: The original export did not include directions.")
        directions = [
            ConventionalDirection(text="Directions were not included in the original export.")
        ]
    yield_text = re.sub(r"^yield\s*:\s*", "", first("recipeYield"), flags=re.IGNORECASE)
    return ConventionalRecipe(
        title=first("name") or "Untitled recipe",
        yield_text=yield_text or None,
        prep_notes=notes,
        ingredients=ingredients,
        directions=directions,
        author=first("author") or None,
    )


def _microdata_root(soup: BeautifulSoup) -> Tag | None:
    root = soup.find(
        attrs={"itemtype": re.compile(r"(?:https?://)?schema\.org/Recipe", re.IGNORECASE)}
    )
    if root is None:
        root = soup.find(attrs={"typeof": re.compile(r"(?:schema:)?Recipe", re.IGNORECASE)})
    return root if isinstance(root, Tag) else None


def _microdata_nutrition(root: Tag) -> dict[str, Any] | None:
    node = root.find(attrs={"itemprop": re.compile(r"(?:^|\s)nutrition(?:\s|$)")})
    if not isinstance(node, Tag):
        return None
    nutrition: dict[str, Any] = {"@type": "NutritionInformation"}
    for child in node.find_all(attrs={"itemprop": True}):
        if not isinstance(child, Tag):
            continue
        key = str(child.get("itemprop")).split()[0]
        if value := _property_value(child):
            nutrition.setdefault(key, value[:200])
    return nutrition if len(nutrition) > 1 else None


def _microdata_image_urls(root: Tag) -> list[str]:
    urls: list[str] = []
    for node in _property_nodes(root, "image"):
        value = _clean_text(node.get("src") or node.get("content") or node.get("href"))
        if value.startswith(("http://", "https://", "//", "/")):
            urls.append(value[:4_096])
    return list(dict.fromkeys(urls))[:20]


def _source_metadata_from_microdata(root: Tag) -> SourceRecipeMetadata:
    def first(prop: str) -> str:
        item = root.find(attrs={"itemprop": prop}) or root.find(attrs={"property": prop})
        return _property_value(item) if isinstance(item, Tag) else ""

    return SourceRecipeMetadata(
        description=first("description")[:2_000] or None,
        prep_time=first("prepTime")[:80] or None,
        cook_time=first("cookTime")[:80] or None,
        total_time=first("totalTime")[:80] or None,
        categories=_metadata_list(first("recipeCategory"), comma_separated=True)[:20],
        cuisines=_metadata_list(first("recipeCuisine"), comma_separated=True)[:20],
        keywords=_metadata_list(first("keywords"), comma_separated=True)[:40],
        diets=_metadata_list(first("suitableForDiet"))[:20],
        date_published=first("datePublished")[:80] or None,
        date_modified=first("dateModified")[:80] or None,
        rating_value=_rating_value(first("ratingValue")),
        rating_count=_rating_count(first("ratingCount") or first("reviewCount")),
        nutrition=_microdata_nutrition(root),
        image_urls=_microdata_image_urls(root),
        url=_source_url(root),
    )


def parse_structured_recipe(html: str, source_url: str) -> ParseResult | None:
    del source_url  # Kept in the interface for fixture parity and future base URL handling.
    soup = BeautifulSoup(html, "html.parser")
    if data := _find_recipe_json_ld(soup):
        try:
            recipe = _json_ld_to_recipe(data)
        except ValueError:
            recipe = None
        if recipe and recipe.ingredients and recipe.directions:
            return ParseResult(
                recipe=recipe,
                parser_path="json_ld",
                source_metadata=_source_metadata_from_json_ld(data),
            )
    if recipe := _microdata_to_recipe(soup):
        root = _microdata_root(soup)
        return ParseResult(
            recipe=recipe,
            parser_path="microdata",
            source_metadata=(
                _source_metadata_from_microdata(root)
                if root is not None
                else SourceRecipeMetadata()
            ),
        )
    return None


def _recipe_roots(soup: BeautifulSoup) -> list[Tag]:
    recipe_type = re.compile(r"(?:https?://)?schema\.org/Recipe(?:\s|$)", re.IGNORECASE)
    candidates = [
        item for item in soup.find_all(attrs={"itemtype": recipe_type}) if isinstance(item, Tag)
    ]
    roots: list[Tag] = []
    candidate_ids = {id(item) for item in candidates}
    for candidate in candidates:
        parent = candidate.find_parent(
            lambda tag: isinstance(tag, Tag) and id(tag) in candidate_ids
        )
        if parent is None:
            roots.append(candidate)
    return roots


def _source_url(root: Tag) -> str | None:
    for node in _property_nodes(root, "url"):
        candidate = _clean_text(node.get("href") or node.get("content"))
        if candidate:
            return candidate
    return None


def _imported_image(
    node: Tag,
    role: ImageRole,
    step_index: int | None = None,
) -> ImportedImage | None:
    path = _clean_text(node.get("src") or node.get("content") or node.get("href"))
    if not path or path.casefold().startswith(("data:", "blob:")):
        return None
    link = node.find_parent("a", href=True)
    linked_url = _clean_text(link.get("href")) if isinstance(link, Tag) else ""
    source_url = linked_url if linked_url.startswith(("http://", "https://")) else None
    if path.startswith(("http://", "https://")):
        source_url = path
    caption = _clean_text(node.get("alt") or node.get("title"))[:500] or None
    return ImportedImage(
        path=path,
        role=role,
        step_index=step_index,
        caption=caption,
        source_url=source_url,
    )


def _embedded_recipe_images(root: Tag) -> tuple[ImportedImage, ...]:
    images: list[ImportedImage] = []
    primary = root.find(attrs={"itemprop": re.compile(r"(?:^|\s)image(?:\s|$)")})
    if isinstance(primary, Tag) and (image := _imported_image(primary, "primary")):
        images.append(image)

    step_index = 0
    for container in _property_nodes(root, "recipeInstructions"):
        blocks = [
            child
            for child in container.find_all(["p", "li"], recursive=True)
            if isinstance(child, Tag)
        ]
        for block in blocks:
            if not _property_value(block):
                continue
            step_index += 1
            for node in block.find_all("img"):
                if image := _imported_image(node, "step", step_index):
                    images.append(image)
    return tuple(images)


def parse_html_recipe_export(
    payload: bytes,
    *,
    max_recipes: int = 100,
) -> RecipeExport:
    """Parse a Paprika-style HTML export into sanitized conventional recipes."""

    soup = BeautifulSoup(payload, "html.parser")
    roots = _recipe_roots(soup)
    if not roots:
        raise ValueError("No Schema.org Recipe entries were found in this HTML export")
    if len(roots) > max_recipes:
        raise ValueError(
            f"This export contains {len(roots)} recipes; the limit is {max_recipes} per batch"
        )

    imported: list[ImportedRecipe] = []
    issues: list[ImportIssue] = []
    for index, root in enumerate(roots, 1):
        name_node = next(iter(_property_nodes(root, "name")), None)
        title = _property_value(name_node) if name_node else None
        try:
            fragment = BeautifulSoup(str(root), "html.parser")
            recipe = _microdata_to_recipe(fragment, allow_incomplete=True)
            if recipe is None:
                raise ValueError("Recipe must include ingredients and directions")
            source_url = _source_url(root)
            if recipe.author and source_url:
                source_host = (urlsplit(source_url).hostname or "").removeprefix("www.")
                author_host = recipe.author.casefold().removeprefix("www.")
                if author_host == source_host.casefold():
                    recipe = recipe.model_copy(update={"author": None})
            serialized = json.dumps(
                recipe.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            imported.append(
                ImportedRecipe(
                    index=index,
                    recipe=recipe,
                    source_url=source_url,
                    content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                    images=_embedded_recipe_images(root),
                    source_metadata=_source_metadata_from_microdata(root),
                )
            )
        except ValueError as exc:
            issues.append(
                ImportIssue(
                    index=index,
                    title=title,
                    message=str(exc).splitlines()[0][:300],
                )
            )
    return RecipeExport(total=len(roots), recipes=imported, issues=issues)


def parse_zip_recipe_export(
    payload: bytes,
    *,
    max_recipes: int = 500,
    max_entries: int = 2_000,
    max_html_bytes: int = 50 * 1024 * 1024,
    max_image_bytes: int = 5 * 1024 * 1024,
) -> RecipeExport:
    """Parse HTML recipe records from a bounded cookbook ZIP without extracting it."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("The cookbook ZIP is not valid") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise ValueError(f"This ZIP contains too many files; the limit is {max_entries}")
        html_infos = [
            info
            for info in infos
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and not PurePosixPath(info.filename).name.startswith("._")
            and PurePosixPath(info.filename).suffix.casefold() in {".html", ".htm"}
        ]
        if not html_infos:
            raise ValueError("No HTML recipe files were found in this cookbook ZIP")
        if any(info.flag_bits & 0x1 for info in html_infos):
            raise ValueError("Encrypted cookbook ZIP files are not supported")
        useful_infos = [
            info
            for info in infos
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and not PurePosixPath(info.filename).name.startswith("._")
        ]
        if sum(info.file_size for info in useful_infos) > max_html_bytes:
            raise ValueError("The uncompressed cookbook ZIP exceeds the import limit")
        info_by_path = {info.filename: info for info in useful_infos}

        def image_from_archive(recipe_info: zipfile.ZipInfo, image: ImportedImage) -> ImportedImage:
            if image.path.startswith(("http://", "https://")):
                return image
            parts: list[str] = []
            candidate = PurePosixPath(recipe_info.filename).parent / image.path
            for part in candidate.parts:
                if part in {"", "."}:
                    continue
                if part == "..":
                    if not parts:
                        return image
                    parts.pop()
                    continue
                parts.append(part)
            member = info_by_path.get("/".join(parts))
            if member is None or member.flag_bits & 0x1 or member.file_size > max_image_bytes:
                return image
            try:
                data = archive.read(member)
            except (zipfile.BadZipFile, RuntimeError):
                return image
            media_type = detect_image_media_type(data)
            if media_type is None:
                return image
            return ImportedImage(
                path=image.path,
                role=image.role,
                step_index=image.step_index,
                caption=image.caption,
                source_url=image.source_url,
                data=data,
                media_type=media_type,
            )

        imported: list[ImportedRecipe] = []
        issues: list[ImportIssue] = []
        total = 0
        for info in html_infos:
            if total >= max_recipes:
                raise ValueError(
                    f"This export contains more than {max_recipes} recipes; "
                    "split it into smaller batches"
                )
            try:
                document = archive.read(info)
                parsed = parse_html_recipe_export(
                    document,
                    max_recipes=max_recipes - total,
                )
            except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
                total += 1
                issues.append(
                    ImportIssue(
                        index=total,
                        title=PurePosixPath(info.filename).stem[:240] or None,
                        message=str(exc).splitlines()[0][:300],
                    )
                )
                continue
            for item in parsed.recipes:
                imported.append(
                    ImportedRecipe(
                        index=total + item.index,
                        recipe=item.recipe,
                        source_url=item.source_url,
                        content_hash=item.content_hash,
                        images=tuple(image_from_archive(info, image) for image in item.images),
                        source_metadata=item.source_metadata,
                    )
                )
            for issue in parsed.issues:
                issues.append(
                    ImportIssue(
                        index=total + issue.index,
                        title=issue.title,
                        message=issue.message,
                    )
                )
            total += parsed.total
        return RecipeExport(total=total, recipes=imported, issues=issues)


def sanitized_main_text(html: str, source_url: str, max_chars: int = 40_000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(
        ["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "img", "aside"]
    ):
        element.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True))
    site = (urlsplit(source_url).hostname or "").removeprefix("www.")
    return f"Source site: {site}\n\n{text[:max_chars]}"


def _paprika_lines(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [text for line in value.replace("\r", "\n").split("\n") if (text := _clean_text(line))]


def _paprika_recipe(data: dict[str, Any]) -> tuple[ConventionalRecipe, SourceRecipeMetadata]:
    ingredient_lines = _paprika_lines(data.get("ingredients"))
    directions: list[ConventionalDirection] = []
    section: str | None = None
    for line in _paprika_lines(data.get("directions")):
        if len(line) <= 160 and line.endswith(":"):
            section = line.rstrip(":").strip() or None
            continue
        value = PLAIN_STEP_PREFIX_RE.sub("", line).strip() or line
        directions.extend(
            ConventionalDirection(section=section, text=chunk)
            for chunk in _bounded_instruction_values(value)
        )
    notes: list[str] = []
    for label, key in (("Prep", "prep_time"), ("Cook", "cook_time"), ("Total", "total_time")):
        if value := _clean_text(data.get(key)):
            notes.append(f"{label}: {value}"[:500])
    notes.extend(f"Note: {line}" for line in _paprika_lines(data.get("notes")))
    if not ingredient_lines:
        notes.append("Import warning: The original export did not include ingredients.")
        ingredient_lines = ["Ingredients were not included in the original export."]
    if not directions:
        notes.append("Import warning: The original export did not include directions.")
        directions = [
            ConventionalDirection(text="Directions were not included in the original export.")
        ]
    source_url = _clean_text(data.get("source_url")) or None
    author = _clean_text(data.get("source")) or None
    if author and source_url:
        host = (urlsplit(source_url).hostname or "").removeprefix("www.").casefold()
        if author.casefold().removeprefix("www.") == host:
            author = None
    recipe = ConventionalRecipe(
        title=_clean_text(data.get("name"))[:240] or "Untitled recipe",
        yield_text=_clean_text(data.get("servings"))[:160] or None,
        prep_notes=notes[:30],
        ingredients=[
            parse_ingredient(value, f"i{index:03d}")
            for index, value in enumerate(ingredient_lines[:250], 1)
        ],
        directions=directions[:500],
        author=author[:255] if author else None,
    )
    categories = data.get("categories") if isinstance(data.get("categories"), list) else []
    image_url = _clean_text(data.get("image_url"))
    metadata = SourceRecipeMetadata(
        description=_clean_text(data.get("description"))[:2_000] or None,
        prep_time=_clean_text(data.get("prep_time"))[:80] or None,
        cook_time=_clean_text(data.get("cook_time"))[:80] or None,
        total_time=_clean_text(data.get("total_time"))[:80] or None,
        categories=_metadata_list(categories)[:20],
        rating_value=_rating_value(data.get("rating")) or None,
        image_urls=[image_url] if image_url.startswith(("http://", "https://")) else [],
        url=source_url,
    )
    return recipe, metadata


def _paprika_image(data: dict[str, Any]) -> tuple[ImportedImage, ...]:
    raw = data.get("photo_data")
    if not isinstance(raw, str) or not raw:
        return ()
    try:
        payload = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return ()
    media_type = detect_image_media_type(payload)
    if media_type is None:
        return ()
    return (
        ImportedImage(
            path=_clean_text(data.get("photo")) or "photo",
            role="primary",
            data=payload,
            media_type=media_type,
        ),
    )


def parse_paprika_archive(
    payload: bytes,
    *,
    max_recipes: int = 500,
    max_uncompressed_bytes: int = 50 * 1024 * 1024,
) -> RecipeExport:
    """Parse Paprika's native ``.paprikarecipes`` (ZIP of gzipped JSON) or a single
    ``.paprikarecipe`` (gzipped JSON)."""

    blobs: list[tuple[str, bytes]] = []
    if payload[:2] == b"\x1f\x8b":
        blobs.append(("recipe", payload))
    else:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise ValueError("The Paprika archive is not valid") from exc
        with archive:
            infos = [
                info
                for info in archive.infolist()
                if not info.is_dir() and not info.filename.startswith("__MACOSX/")
            ]
            if len(infos) > max_recipes:
                raise ValueError(
                    f"This export contains {len(infos)} recipes; the limit is {max_recipes}"
                )
            if sum(info.file_size for info in infos) > max_uncompressed_bytes:
                raise ValueError("The uncompressed Paprika archive exceeds the import limit")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise ValueError("Encrypted Paprika archives are not supported")
                blobs.append((PurePosixPath(info.filename).stem, archive.read(info)))
    if not blobs:
        raise ValueError("No recipes were found in this Paprika archive")

    imported: list[ImportedRecipe] = []
    issues: list[ImportIssue] = []
    budget = max_uncompressed_bytes
    for index, (name, blob) in enumerate(blobs, 1):
        try:
            if blob[:2] == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(blob)) as handle:
                    blob = handle.read(budget + 1)
                if len(blob) > budget:
                    raise ValueError("The uncompressed Paprika archive exceeds the import limit")
            budget -= len(blob)
            data = json.loads(blob.decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                raise ValueError("Paprika recipe entry is not a JSON object")
            recipe, metadata = _paprika_recipe(data)
            serialized = json.dumps(
                recipe.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            imported.append(
                ImportedRecipe(
                    index=index,
                    recipe=recipe,
                    source_url=metadata.url,
                    content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                    images=_paprika_image(data),
                    source_metadata=metadata,
                )
            )
        except (ValueError, OSError, EOFError, gzip.BadGzipFile) as exc:
            issues.append(
                ImportIssue(index=index, title=name[:240] or None, message=str(exc)[:300])
            )
    return RecipeExport(total=len(blobs), recipes=imported, issues=issues)
