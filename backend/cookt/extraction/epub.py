"""Cookbook EPUB import: find every recipe in a book the household owns.

Deterministic (no model): the book's spine is flattened into a sequence of text blocks
(headings, paragraphs, list items, images), recipes are found by their shape (a title, a run
of ingredient lines, then method steps), and each becomes an `Extraction` for the inbox.
Publisher CSS classes ("ingredient", "method", "yield", ...) are used when present; text
patterns (quantities, units, numbered steps) otherwise.

    uv run python -m cookt.extraction.epub BOOK.epub [--show 5]   # dry run, prints what it finds
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag

from ..document import IngredientSection, InstructionSection, InstructionStep, RecipeDocumentV2
from .parser import parse_ingredient

MAX_TEXT_FILE = 8 * 1024 * 1024  # one XHTML chapter
MAX_TEXT_TOTAL = 120 * 1024 * 1024  # all chapters together (zip-bomb guard)
MAX_IMAGE = 12 * 1024 * 1024
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "dt", "dd", "div", "td", "blockquote"}
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_FRACTION = r"[½¼¾⅓⅔⅛⅜⅝⅞]"
_QTY = rf"(?:\d+(?:[.,]\d+)?(?:\s*[-–/]\s*\d+)?(?:\s+\d/\d)?|\d/\d|{_FRACTION}|\d+{_FRACTION})"
_UNITS = (
    r"(?:cups?|c\.|tablespoons?|tbsp\.?|teaspoons?|tsp\.?|ounces?|oz\.?|pounds?|lbs?\.?|grams?|g|"
    r"kg|kilograms?|ml|milliliters?|liters?|l|quarts?|qt\.?|pints?|cloves?|heads?|bunch(?:es)?|"
    r"sprigs?|stalks?|slices?|pieces?|cans?|sticks?|pinch(?:es)?|dash(?:es)?|large|medium|small|"
    r"whole|inch|-inch)"
)
# After the amount: a unit, a parenthesis, or a lowercase word ("2 garlic cloves"). Not a capital:
# "1.1 How to Stir-Fry" in a table of contents is not an ingredient.
QTY_START = re.compile(rf"^\(?\s*(?:{_QTY})\s*(?:\(|(?i:{_UNITS})\b|[a-z])")
WORD_QTY_START = re.compile(
    r"^(?:one|two|three|four|five|six|eight|ten|twelve|a|an|half|a few|a handful|a pinch|"
    r"pinch|dash|juice of|zest of)\b\s+\S",
    re.I,
)
# Qty-less ingredient lines that still show up in lists ("Kosher salt", "Oil, for frying").
BARE_INGREDIENT = re.compile(
    r"^(?:kosher salt|salt|sea salt|freshly ground|ground black pepper|black pepper|pepper|"
    r"(?:vegetable|canola|peanut|olive|neutral) oil|extra-virgin olive oil|sugar|water|ice|"
    r"cooking spray|nonstick|flaky|lemon wedges|lime wedges|steamed rice|cooked rice)\b",
    re.I,
)
YIELD = re.compile(
    r"^(?:serves|makes|yield|yields|for)\s+(?:about\s+)?\d|^(?:serves|makes|yield)s?\b", re.I
)
SUBHEAD = re.compile(r"^(?:for (?:the )?|to (?:finish|serve|garnish)|garnish|topping)", re.I)
# "1." "2)" "Step 3:" or a bare "1 For the sauce: ..." (number then a capitalized word).
STEP_NUMBER = re.compile(r"^\s*(?i:step\s*)?(\d{1,2})(?:\s*[.):]\s*|\s+(?=[A-Z]))")
LETTERED = re.compile(r"^[a-h][.)]\s+\S")  # "a. Dried chiles" sub-items inside a step
# Label/value pairs some books use ("Yield" / "Serves 4", "Total Time" / "40 minutes").
LABEL = re.compile(
    r"^(yield|serves|makes|active time|total time|prep time|cook time|time)\s*:?$", re.I
)
NOTE_LABEL = re.compile(r"^notes?\s*:?$", re.I)
SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "de",
    "la",
    "con",
    "y",
    "e",
}
NOTE = re.compile(r"^(?:notes?|tips?|variations?|make ahead|to store|storage)\b\s*[:.—-]?", re.I)
NOT_TITLE = re.compile(
    r"^(?:\d+(?:\.\d+)+\s|\d+\s+[A-Z]{3}|\d+°|experiment\b|sidebar\b|"  # "4.3 Crispy…", "136°F"
    r"(?:volume |weight |metric )?(?:equivalen|conversion)|"
    r"ingredients?|directions?|method|instructions?|preparation|notes?|yield|serves|makes|"
    r"active time|total time|prep time|cook time|equipment|"
    r"contents|index|acknowledg|copyright)\b",
    re.I,
)
NAV_FILE = re.compile(r"(?:^|/)(?:toc|nav|contents?|index|copyright)[^/]*$", re.I)
SENTENCE_END = re.compile(r"[.!?][\"”’)]?\s+[A-Z]")


@dataclass
class Block:
    kind: str  # heading | text | image
    text: str
    level: int = 0  # heading level (1-6); 0 for text
    classes: str = ""
    src: str | None = None  # image path inside the zip
    glyphs: bool = False  # the ebook drew some letters as inline pictures (e.g. Vietnamese Ộ)
    cell: bool = False  # inside a table cell (reference tables, yield/time grids)

    @property
    def cls(self) -> str:
        return self.classes.lower()


@dataclass
class Book:
    title: str | None
    author: str | None
    blocks: list[Block]
    zip: zipfile.ZipFile


@dataclass
class FoundRecipe:
    title: str
    yield_text: str | None
    headnote: str | None
    ingredient_sections: list[tuple[str | None, list[str]]]
    step_sections: list[tuple[str | None, list[str]]]
    notes: list[str]
    image: str | None
    warnings: list[str] = field(default_factory=list)
    total_minutes: int | None = None


class EpubError(ValueError):
    pass


# --- reading ------------------------------------------------------------------------------


def _read(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with zf.open(name) as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise EpubError(f"{name} is too large")
    return data


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("­", "")).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def open_book(path: Path) -> Book:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise EpubError("not an EPUB (zip) file") from exc
    names = set(zf.namelist())
    if "META-INF/container.xml" not in names:
        raise EpubError("not an EPUB: META-INF/container.xml is missing")
    try:
        container = ElementTree.fromstring(_read(zf, "META-INF/container.xml", 1 << 20))
        rootfile = next((el for el in container.iter() if _local(el.tag) == "rootfile"), None)
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path or opf_path not in names:
            raise EpubError("not an EPUB: package document not found")
        opf = ElementTree.fromstring(_read(zf, opf_path, 8 << 20))
    except ElementTree.ParseError as exc:
        raise EpubError("not an EPUB: unreadable package document") from exc
    base = posixpath.dirname(opf_path)

    def first(tag: str) -> str | None:
        el = next(
            (el for el in opf.iter() if _local(el.tag) == tag and (el.text or "").strip()), None
        )
        return _norm(el.text) if el is not None else None

    manifest = {
        el.get("id"): el
        for el in opf.iter()
        if _local(el.tag) == "item" and el.get("id") and el.get("href")
    }
    spine = [el.get("idref") for el in opf.iter() if _local(el.tag) == "itemref"]
    blocks: list[Block] = []
    total = 0
    for idref in spine:
        item = manifest.get(idref)
        if item is None or "html" not in (item.get("media-type") or ""):
            continue
        # Navigation, contents and index pages list titles, not recipes.
        if "nav" in (item.get("properties") or "") or NAV_FILE.search(item.get("href") or ""):
            continue
        name = posixpath.normpath(posixpath.join(base, unquote(item.get("href"))))
        if name not in names:
            continue
        data = _read(zf, name, MAX_TEXT_FILE)
        total += len(data)
        if total > MAX_TEXT_TOTAL:
            raise EpubError("book text is too large")
        blocks += _blocks(data, posixpath.dirname(name))
    return Book(
        title=first("title"),
        author=first("creator"),
        blocks=blocks,
        zip=zf,
    )


def _blocks(xhtml: bytes, base: str) -> list[Block]:
    soup = BeautifulSoup(xhtml, "html.parser")
    body = soup.body or soup
    out: list[Block] = []

    def classes(el: Tag) -> str:
        own = " ".join(el.get("class") or [])
        parent = el.parent if isinstance(el.parent, Tag) else None
        up = " ".join(parent.get("class") or []) if parent is not None else ""
        ordered = (
            " ol-step" if el.name == "li" and parent is not None and parent.name == "ol" else ""
        )
        return f"{own} {up} {el.get('epub:type', '')}{ordered}".strip()

    for el in body.find_all(True):
        if el.name in ("img", "image"):
            src = el.get("src") or el.get("xlink:href") or el.get("href")
            holder = el.find_parent(list(BLOCK_TAGS))
            if holder is not None and _norm(holder.get_text(" ")):
                continue  # an inline glyph inside a line of text, not a photo
            if src:
                out.append(
                    Block("image", "", src=posixpath.normpath(posixpath.join(base, unquote(src))))
                )
            continue
        if el.name not in BLOCK_TAGS:
            continue
        # Leaf blocks only: a <div> wrapping paragraphs is not itself a line.
        if any(child.name in BLOCK_TAGS for child in el.find_all(True)):
            continue
        text = _norm(el.get_text(" "))
        if not text:
            continue
        glyphs = el.find(["img", "image"]) is not None
        cell = el.name == "td" or el.find_parent("td") is not None
        if el.name[0] == "h" and el.name[1:].isdigit():
            out.append(
                Block("heading", text, level=int(el.name[1]), classes=classes(el), glyphs=glyphs)
            )
        else:
            out.append(Block("text", text, classes=classes(el), glyphs=glyphs, cell=cell))
    return out


# --- recipe detection ---------------------------------------------------------------------


ING_CLASS = re.compile(r"\bing(?:r\w*|-?list\w*|-?item\w*|-?line\w*)\b|\bingredient\b")
ING_HEAD_CLASS = re.compile(r"\bing-?(?:h|hb|ts|head\w*|sub\w*)\b")
METHOD_KEYS = ("meth", "step", "direct", "instr", "proc", "number")


def _ing_class(block: Block) -> bool:
    return bool(ING_CLASS.search(block.cls)) and not ING_HEAD_CLASS.search(block.cls)


def _is_label(text: str) -> bool:
    return bool(LABEL.match(text) or NOT_TITLE.match(text) and len(text.split()) <= 2)


def _minutes(text: str) -> int | None:
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", text, re.I)
    mins = re.search(r"(\d+)\s*(?:minutes?|mins?)\b", text, re.I)
    if not hours and not mins:
        return None
    return round(float(hours.group(1)) * 60 if hours else 0) + (int(mins.group(1)) if mins else 0)


def _title_case(text: str) -> str:
    """ALL-CAPS book titles read as shouting in a recipe list."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.85 * len(letters):
        return text  # already mixed case (tolerates a stray "î" in "CRÈME FRAîCHE")

    def cap(part: str) -> str:
        # Capitalize the first letter, past any leading "(" or quote: "(pad" -> "(Pad".
        return re.sub(r"^([^\w]*)(\w)", lambda m: m.group(1) + m.group(2).upper(), part)

    out = []
    for i, word in enumerate(text.lower().split()):
        after_break = i == 0 or out[-1].endswith((":", "(")) or word.startswith(("(", "“", '"'))
        if word in SMALL_WORDS and not after_break:
            out.append(word)
        else:
            parts = re.split(r"([-–/])", word)
            out.append(
                "".join(p if k and p in SMALL_WORDS else cap(p) for k, p in enumerate(parts))
            )
    return " ".join(out)


def _is_ingredient(block: Block) -> bool:
    if block.kind != "text" or len(block.text) > (320 if _ing_class(block) else 180):
        return False
    if block.cell and not _ing_class(block):
        return False  # "1 cup | 240 ml" rows of a conversion table
    if _is_label(block.text):
        return False
    if _ing_class(block) and not SUBHEAD.match(block.text):
        return True
    if any(
        key in block.cls for key in ("meth", "step", "direct", "instr", "proc", "headnote", "intro")
    ):
        return False
    if SENTENCE_END.search(block.text):
        return False  # two sentences read like method or prose
    return bool(QTY_START.match(block.text) or WORD_QTY_START.match(block.text))


def _is_weak_ingredient(block: Block) -> bool:
    """Qty-less lines that belong in a list only when surrounded by ingredients."""
    return (
        block.kind == "text"
        and len(block.text) <= 90
        and not SENTENCE_END.search(block.text)
        and not _is_label(block.text)
        and bool(BARE_INGREDIENT.match(block.text) or _ing_class(block))
    )


def _is_subhead(block: Block) -> bool:
    text = block.text.rstrip(":")
    if len(text) > 60 or SENTENCE_END.search(text) or text.endswith(".") or _is_label(text):
        return False
    return (
        bool(SUBHEAD.match(text))
        or bool(ING_HEAD_CLASS.search(block.cls))
        or (block.kind == "heading" and block.level >= 3)
        or ("head" in block.cls and len(text.split()) <= 6)
        or (text.isupper() and len(text.split()) <= 5)
    )


def _is_title(block: Block) -> bool:
    text = block.text
    if NOT_TITLE.match(text) or YIELD.match(text) or SUBHEAD.match(text):
        return False
    if len(text) > 110 or text.endswith((".", ":")):
        return False
    if block.kind == "heading":
        return True
    if block.kind == "text" and text.isupper() and 1 <= len(text.split()) <= 14:
        return True  # many books set recipe titles as an all-caps paragraph
    return any(
        key in block.cls
        for key in ("title", "rt", "rh", "recipe-name", "recipename", "head", "_hd", "hd_")
    )


def _class_stem(block: Block) -> str:
    first = (block.classes.split() or [""])[0].lower()
    return re.sub(r"[\d_-]*[a-z]?$", "", first) if len(first) >= 5 else ""


def _list_member(prev: Block, block: Block, following: list[Block]) -> bool:
    """A short line that is an ingredient by position: it carries the same publisher class as
    the ingredient before it, or sits between two ingredient lines."""
    if block.kind != "text" or len(block.text) > 320:
        return False
    if _is_label(block.text) or _is_title(block) or YIELD.match(block.text):
        return False
    same_style = bool(block.classes) and block.classes == prev.classes and _is_ingredient(prev)
    between = (
        _is_ingredient(prev)
        and any(_is_ingredient(b) for b in following)
        and not SENTENCE_END.search(block.text)
    )
    return same_style or between


def _ingredient_runs(blocks: list[Block]) -> list[tuple[int, int]]:
    """[start, end) spans of ingredient lists (with any subheads and bare lines inside)."""
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(blocks):
        if not _is_ingredient(blocks[i]):
            i += 1
            continue
        start = i
        # A subhead or bare line just before the first ingredient belongs to the list.
        while start > 0 and (
            _is_weak_ingredient(blocks[start - 1])
            or (_is_subhead(blocks[start - 1]) and SUBHEAD.match(blocks[start - 1].text))
        ):
            start -= 1
        end = i
        strong = 0
        while end < len(blocks):
            block = blocks[end]
            if _is_ingredient(block):
                strong += 1
                end += 1
                continue
            if block.kind == "image":
                end += 1
                continue
            lookahead = blocks[end + 1 : end + 3]
            if (_is_weak_ingredient(block) or _is_subhead(block)) and any(
                _is_ingredient(b) or _is_weak_ingredient(b) for b in lookahead
            ):
                end += 1
                continue
            if _is_weak_ingredient(block):  # trailing "Kosher salt"
                end += 1
                continue
            if _list_member(blocks[end - 1], block, lookahead[:1]):
                end += (
                    1  # "Large pinch of red pepper flakes": styled and placed like its neighbours
                )
                continue
            break
        if strong >= 3 or (strong >= 2 and any(_ing_class(b) for b in blocks[i:end])):
            runs.append((start, end))
        i = end
    return runs


def _clean_step(text: str) -> str:
    return STEP_NUMBER.sub("", text, count=1).strip()


def find_recipes(blocks: list[Block]) -> tuple[list[FoundRecipe], int]:
    """Recipes found in reading order, and how many ingredient lists had no usable method."""
    runs = _ingredient_runs(blocks)
    # Title for each run: the nearest title-like block before it, after the previous run.
    titles: list[int | None] = []
    floor = 0
    for start, end in runs:
        title_at = None
        for j in range(start - 1, max(floor, start - 40) - 1, -1):
            if _is_title(blocks[j]):
                title_at = j
                break
        titles.append(title_at)
        floor = end
    found: list[FoundRecipe] = []
    skipped = 0
    for index, (start, end) in enumerate(runs):
        title_at = titles[index]
        if title_at is None:
            skipped += 1
            continue
        next_start = runs[index + 1][0] if index + 1 < len(runs) else len(blocks)
        next_title = titles[index + 1] if index + 1 < len(runs) else None
        stop = min(next_start, next_title if next_title is not None else len(blocks))
        recipe = _assemble(blocks, title_at, start, end, stop)
        if recipe is None:
            skipped += 1
        else:
            found.append(recipe)
    return found, skipped


class _Meta:
    """Yield, total time and notes, from label/value pairs ("Yield" / "Serves 4"), NOTE blocks
    and "Serves 4" lines, wherever they sit around the ingredient list."""

    def __init__(self) -> None:
        self.yield_text: str | None = None
        self.total_minutes: int | None = None
        self.notes: list[str] = []
        self.label: str | None = None
        self.note_mark = 0

    def take(self, block: Block) -> bool:
        text = block.text
        if self.label == "note":
            # A note runs one paragraph, or more while the publisher keeps marking them as notes.
            if len(self.notes) == self.note_mark or "note" in block.cls:
                self.notes.append(text)
                return True
            self.label = None
        elif self.label:
            if self.label in ("yield", "serves", "makes"):
                value = text if self.label == "yield" else f"{self.label.title()} {text}"
                self.yield_text = self.yield_text or value
            elif self.label == "total time":
                self.total_minutes = self.total_minutes or _minutes(text)
            self.label = None
            return True
        if NOTE_LABEL.match(text):
            self.label, self.note_mark = "note", len(self.notes)
        elif LABEL.match(text):
            self.label = LABEL.match(text).group(1).lower()
        elif YIELD.match(text) and len(text) < 80:
            self.yield_text = self.yield_text or text
        elif "note" in block.cls and block.kind == "text" and len(text) > 12:
            self.notes.append(text)
        else:
            return False
        return True


def _assemble(
    blocks: list[Block], title_at: int, start: int, end: int, stop: int
) -> FoundRecipe | None:
    # Titles set on two lines ("SIMPLE RED-WINE" / "PAN SAUCE") come as sibling blocks whose
    # publisher classes share a stem (recipe_rt1a / recipe_rt1).
    first = title_at
    while (
        first > 0
        and first > title_at - 2
        and _is_title(blocks[first - 1])
        and _class_stem(blocks[first - 1]) == _class_stem(blocks[title_at]) != ""
    ):
        first -= 1
    title = _title_case(" ".join(b.text for b in blocks[first : title_at + 1]))
    title = re.sub(r"(\w)- (\w)", r"\1-\2", title)  # a line break inside "Herb-Rubbed"
    meta = _Meta()
    headnote: list[str] = []
    image = None
    for block in blocks[title_at + 1 : start]:
        if block.kind == "image":
            image = image or block.src
        elif not meta.take(block) and block.kind == "text" and not _is_label(block.text):
            headnote.append(block.text)
    # A photo placed just before the title belongs to it.
    for block in blocks[max(0, title_at - 2) : title_at]:
        if block.kind == "image" and image is None:
            image = block.src

    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for block in blocks[start:end]:
        if block.kind == "image":
            image = image or block.src
        elif _is_label(block.text):
            continue  # "INGREDIENTS"
        elif _is_subhead(block) and not _is_ingredient(block):
            sections.append((block.text.rstrip(":"), []))
        else:
            sections[-1][1].append(block.text)  # the run finder already vetted every line
    sections = [s for s in sections if s[1]]

    steps: list[tuple[str | None, list[str]]] = [(None, [])]
    numbered = False
    after_note = False
    pending: list[str] = []  # unnumbered paragraphs before the first numbered step
    for block in blocks[end:stop]:
        if block.kind == "image":
            image = image or block.src
            continue
        text = block.text
        started = any(s for _, s in steps) or bool(pending)
        if started and ((block.kind == "heading" and block.level <= 2) or _is_title(block)):
            break  # a chapter heading or the next essay's all-caps heading ends the method
        if not started and meta.take(block):
            continue  # yield/time table, NOTE, "DIRECTIONS"
        if _is_label(text) and not started:
            continue
        if NOTE.match(text) and len(text) > 12:
            meta.notes.append(text)
            after_note = True
            continue
        if LETTERED.match(text) and steps[-1][1]:
            steps[-1][1][-1] = f"{steps[-1][1][-1]} {text}"  # "a. Dried chiles" belongs to its step
            continue
        if _is_subhead(block) and not STEP_NUMBER.match(text):
            if started and block.kind == "heading" and not SUBHEAD.match(text):
                break  # an unrelated heading after the method
            steps.append((text.rstrip(":"), []))
            continue
        is_numbered = bool(STEP_NUMBER.match(text)) or any(key in block.cls for key in METHOD_KEYS)
        if numbered and not is_numbered:
            break  # prose after a numbered method is the next essay, not a step
        if meta.notes and after_note:
            meta.notes[-1] = f"{meta.notes[-1]} {text}"  # a note's continuation paragraph
            continue
        if is_numbered and not numbered and pending:
            headnote += pending  # intro prose that sat between the ingredients and the method
            pending = []
        numbered = numbered or is_numbered
        if not numbered:
            pending.append(text)
            continue
        steps[-1][1].append(_clean_step(text))
        after_note = False
    if pending:  # an unnumbered method: the paragraphs were the steps after all
        steps[-1][1].extend(_clean_step(t) for t in pending)
    steps = [(h, [s for s in lines if s]) for h, lines in steps]
    steps = [s for s in steps if s[1]]
    if not sections or not steps:
        return None
    warnings = []
    used = blocks[title_at:stop]
    if any(b.glyphs for b in used):
        warnings.append(
            "Some letters are pictures in the ebook (often accented letters); check the spelling."
        )
    if sum(len(lines) for _, lines in steps) > 25:
        warnings.append("Long method: check where this recipe ends.")
    return FoundRecipe(
        title=title,
        yield_text=meta.yield_text,
        headnote=" ".join(headnote)[:2000] or None,
        ingredient_sections=sections,
        step_sections=steps,
        notes=meta.notes,
        image=image,
        warnings=warnings,
        total_minutes=meta.total_minutes,
    )


# --- building documents -------------------------------------------------------------------


def to_document(recipe: FoundRecipe) -> RecipeDocumentV2:
    n = 0
    ingredient_sections = []
    for s, (heading, lines) in enumerate(recipe.ingredient_sections, 1):
        items = []
        for line in lines:
            n += 1
            items.append(parse_ingredient(line, f"i{n:03d}"))
        ingredient_sections.append(
            IngredientSection(id=f"ingredients_{s}", heading=heading, ingredients=items)
        )
    k = 0
    instruction_sections = []
    for s, (heading, lines) in enumerate(recipe.step_sections, 1):
        steps = []
        for line in lines:
            k += 1
            steps.append(InstructionStep(id=f"step_{k}", text=line[:4000]))
        instruction_sections.append(
            InstructionSection(id=f"instructions_{s}", heading=heading, steps=steps)
        )
    return RecipeDocumentV2(
        title=recipe.title[:240],
        yield_text=(recipe.yield_text or None) and recipe.yield_text[:160],
        notes=[note[:4000] for note in recipe.notes][:100],
        ingredient_sections=ingredient_sections,
        instruction_sections=instruction_sections,
    )


def image_bytes(book: Book, src: str | None) -> tuple[bytes, str] | None:
    if not src:
        return None
    media = IMAGE_TYPES.get(posixpath.splitext(src)[1].lower())
    if media is None or src not in book.zip.namelist():
        return None
    try:
        return _read(book.zip, src, MAX_IMAGE), media
    except EpubError:
        return None


def extract_from_epub(path: Path) -> tuple[list[Any], dict[str, Any]]:
    """Every recipe in the book as an `Extraction`, plus stats for the response."""
    from .pipeline import Extraction

    book = open_book(path)
    found, skipped = find_recipes(book.blocks)
    extractions = []
    invalid = 0
    for recipe in found:
        try:
            document = to_document(recipe)
        except ValueError:
            invalid += 1
            continue
        image = image_bytes(book, recipe.image)
        extractions.append(
            Extraction(
                document=document,
                method="epub",
                source_site=book.title,
                source_author=book.author,
                yield_text=document.yield_text,
                total_minutes=recipe.total_minutes,
                hints={"description": recipe.headnote} if recipe.headnote else {},
                warnings=recipe.warnings,
                image_data=[image] if image else [],
            )
        )
    stats = {
        "book": book.title,
        "author": book.author,
        "found": len(extractions),
        "skipped": skipped + invalid,
        "blocks": len(book.blocks),
    }
    return extractions, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cookt.extraction.epub", description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--show", type=int, default=3, help="print the first N recipes in full")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="file the recipes into the inbox (as the upload does)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="with --import: first dismiss this book's unsaved inbox items (after parser fixes)",
    )
    args = parser.parse_args(argv)
    extractions, stats = extract_from_epub(args.path)
    print(stats)
    if args.do_import:
        import hashlib

        from .. import db, imports

        conn = db.connect()
        db.init(conn)
        if args.replace:
            dismissed = conn.execute(
                "UPDATE inbox SET status = 'dismissed', updated_at = ? WHERE kind = 'epub' "
                "AND status NOT IN ('saved', 'dismissed') AND json_extract(input, '$.book') = ?",
                (db.now(), stats["book"]),
            ).rowcount
            print(f"dismissed {dismissed} earlier inbox items from {stats['book']}")
        sha = hashlib.sha256(args.path.read_bytes()).hexdigest()
        result = imports.file_epub(conn, extractions, stats, args.path.name, sha)
        print({k: v for k, v in result.items() if k != "ids"})
        return 0
    for n, extraction in enumerate(extractions):
        doc = extraction.document
        lines = sum(len(s.ingredients) for s in doc.ingredient_sections)
        steps = sum(len(s.steps) for s in doc.instruction_sections)
        flag = f"  ⚠ {'; '.join(extraction.warnings)}" if extraction.warnings else ""
        print(
            f"{n + 1:4d}. {doc.title}  [{lines} ingr, {steps} steps"
            f"{', photo' if extraction.image_data else ''}]{flag}"
        )
        if n < args.show:
            for section in doc.ingredient_sections:
                if section.heading:
                    print(f"        ## {section.heading}")
                for item in section.ingredients:
                    print(f"        - {item.source_text}")
            for section in doc.instruction_sections:
                if section.heading:
                    print(f"        ## {section.heading}")
                for step in section.steps:
                    print(f"        > {step.text[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
