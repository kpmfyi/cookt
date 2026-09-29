"""Per-serving nutrition (spec §4.5).

1. Publisher JSON-LD ``nutrition`` wins when present (``source='publisher'``,
   shown as "from source").
2. Otherwise it is computed: the local model parses every ingredient line of a
   recipe in ONE call (quantity, unit, generic food name); each food is matched
   to a local USDA FoodData Central entry (curated alias table first, then FTS
   candidates from which the model picks, also one call per recipe); grams come
   from standard units or FDC portion data (``fdc.grams_for``); the nutrients are
   summed deterministically and divided by servings.
3. Every line keeps its breakdown (food, fdc_id, grams, nutrients, confidence,
   method). Lines with no amount ("salt to taste", "oil for frying"), optional
   lines, water, and lines with no reasonable FDC match are excluded, never
   guessed. User overrides (``nutrition_overrides``) replace the match or exclude
   the line, and persist.
4. Overall confidence badge from the share of the recipe's estimated mass whose
   food AND grams both come from FDC data: >= 90% high, >= 70% medium, else low.

Model parses and picks are cached per ingredient ``source_text`` in
``ingredient_parse_cache`` (with prompt versions), so recomputing after an
override or an alias update costs no model calls.

CLI (resumable; the ``nutrition`` table is the checkpoint)::

    uv run python -m cookt.enrich.nutrition backfill [--db PATH] [--limit N] [--recipe ID]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .. import db
from ..config import settings
from ..document import load_document
from ..fractions import parse_quantity
from ..llm import untrusted
from . import fdc

log = logging.getLogger("cookt.enrich.nutrition")

PARSE_VERSION = "nutrition-parse-v2"
MATCH_VERSION = "nutrition-match-v1"
ALGO_VERSION = "nutrition-v1"

HIGH_MASS_PCT = 90.0
MEDIUM_MASS_PCT = 70.0
DEFAULT_SERVINGS = 4.0
MAX_CANDIDATES = 8
PARSE_CHUNK = 60
NUTRIENT_KEYS = ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg")

# The model client: (prompt, schema, name) -> validated schema instance. Injected in tests.
LLMFn = Callable[[str, type[BaseModel], str], BaseModel]


def _default_llm(prompt: str, schema: type[BaseModel], name: str) -> BaseModel:
    from ..llm import complete_json

    return complete_json(prompt, schema, name)


def current_version() -> str:
    alias_hash = hashlib.sha256(fdc.ALIASES_PATH.read_bytes()).hexdigest()[:8]
    return f"{ALGO_VERSION}/{PARSE_VERSION}/{MATCH_VERSION}/aliases-{alias_hash}"


# Version string the enrichment orchestrator (cookt.enrich.run_step) records per step.
PROMPT_VERSION = current_version()


# --- publisher nutrition -----------------------------------------------------------------

_PUBLISHER_FIELDS = {
    "kcal": ("calories", "kcal"),
    "protein_g": ("proteinContent", "g"),
    "carbs_g": ("carbohydrateContent", "g"),
    "fat_g": ("fatContent", "g"),
    "fiber_g": ("fiberContent", "g"),
    "sodium_mg": ("sodiumContent", "mg"),
}
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)*)")


def _publisher_value(raw: Any, target: str) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("@value")
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).strip().casefold()
    match = _NUMBER.search(text)
    if not match:
        return None
    number = match.group(1)
    # "1,250" (thousands) vs "12,5" (decimal comma)
    if re.fullmatch(r"\d{1,3}(,\d{3})+", number):
        number = number.replace(",", "")
    else:
        number = number.replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        return None
    unit = text[match.end() :].strip()
    if target == "kcal":
        if unit.startswith("kj") or "kilojoule" in unit:
            value /= 4.184
        return value
    if target == "g":
        if unit.startswith("mg"):
            return value / 1000
        return value
    if target == "mg":
        if unit.startswith("mg") or not unit:
            return value
        if unit.startswith("g"):
            return value * 1000
        return value
    return value


def parse_publisher_nutrition(block: dict, servings: float | None = None) -> dict | None:
    """Parse a JSON-LD NutritionInformation block into per-serving numbers.

    schema.org nutrition values are per serving already; ``servings`` is recorded
    alongside. Returns None when the block carries no calories.
    """
    if not isinstance(block, dict):
        return None
    result: dict[str, Any] = {}
    for key, (field, target) in _PUBLISHER_FIELDS.items():
        value = _publisher_value(block.get(field), target)
        result[key] = round(value, 1) if value is not None else None
    if result["kcal"] is None or result["kcal"] <= 0:
        return None
    result["kcal"] = round(result["kcal"])
    if result["sodium_mg"] is not None:
        result["sodium_mg"] = round(result["sodium_mg"])
    if block.get("servingSize"):
        result["serving_size"] = str(block["servingSize"])[:80]
    result["servings"] = servings
    return result


# --- model schemas + prompts -------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParsedLine(_Strict):
    i: int
    quantity: float | None
    unit: str | None
    food: str
    approx_grams: float | None
    exclude: bool
    exclude_reason: str | None


class ParseResponse(_Strict):
    lines: list[ParsedLine]


class MatchChoice(_Strict):
    i: int
    choice: int
    quality: Literal["exact", "close", "approximate", "none"]


class MatchResponse(_Strict):
    choices: list[MatchChoice]


PARSE_PROMPT = """You convert recipe ingredient lines into data for a nutrition calculator.
For EVERY numbered line return one entry with:
- i: the line number.
- quantity: the amount as a decimal number (1 1/2 -> 1.5, ½ -> 0.5, a range 2-3 -> 2.5,
  "a pinch" -> 1). null when no amount is given.
- unit: one of g, kg, oz, lb, ml, l, tsp, tbsp, cup, fl oz, pint, quart, pinch, dash; or a count
  word such as clove, slice, stick, can, package, large, medium, small, bunch, sprig, head, stalk,
  piece, fillet; null for a bare count ("2 eggs", "1 onion").
  * A package size in the line wins: "1 (14.5 oz) can diced tomatoes" -> 14.5 oz;
    "2 (15-ounce) cans chickpeas" -> 30 oz.
  * When a line gives both a volume and a weight ("1 cup (120 g) flour"), return the weight.
  * When a line gives the weight of each item ("2 pork chops, about 7 oz each"), return the
    total weight (14 oz). A total weight in the line ("4 chicken breasts (2 lb)") also wins.
  * When a line offers alternatives ("butter or oil"), describe the first.
- food: a short generic USDA-style food name: singular, no brand, no quantity, no preparation
  steps (chopped, minced, divided, at room temperature), but KEEP words that change nutrition:
  raw vs cooked/canned/dried/frozen, fat level (whole, skim, lean %), unsalted, boneless
  skinless, sweetened, and which part of a fruit (juice, zest). Examples: "unsalted butter",
  "boneless skinless chicken thigh", "canned black beans", "lemon juice", "yellow onion",
  "all-purpose flour", "extra-virgin olive oil", "kosher salt", "cooked white rice".
  "juice of 1 lemon" -> quantity 1, unit null, food "lemon juice".
- approx_grams: your best estimate of the weight in grams of the whole line as written
  (statistics only). null when there is no amount.
- exclude: true when the line must not count toward nutrition: no amount or "to taste"
  ("salt and pepper to taste", "oil for frying", "cooking spray", "cilantro for garnish"),
  explicitly optional, plain water or ice, or not a food at all (a heading, equipment).
  exclude_reason: a few words why (e.g. "to taste", "no amount", "optional", "water",
  "not a food"); null when exclude is false.

Ingredient lines:
{lines}"""

MATCH_PROMPT = """Match each recipe ingredient to the USDA FoodData Central entry that best
represents its nutrition as it goes into the dish.
Rules:
- Prefer raw/uncooked forms unless the line says cooked, canned, dried or frozen.
- Prefer the plain/regular variety over low-fat, sweetened, fortified or branded ones unless the
  line asks for them.
- choice is the candidate number, or 0 when none is a reasonable nutritional stand-in. Do not
  pick a different food just because words overlap ("rice vinegar" is not rice, "coconut
  milk" is not cow's milk, "cream of tartar" is not cream).
- quality: "exact" (same food), "close" (same food, different variety or form, nutrition within
  about 10%), "approximate" (a reasonable stand-in), "none" (choice 0).

{items}"""


# --- cache -------------------------------------------------------------------------------


def _cache_get(conn: sqlite3.Connection, source_text: str) -> dict | None:
    row = conn.execute(
        "SELECT parsed, prompt_version FROM ingredient_parse_cache WHERE source_text = ?",
        (source_text,),
    ).fetchone()
    if not row or row["prompt_version"] != PARSE_VERSION:
        return None
    return db.loads(row["parsed"])


def _cache_put(conn: sqlite3.Connection, source_text: str, entry: dict) -> None:
    conn.execute(
        "INSERT INTO ingredient_parse_cache(source_text, parsed, model, prompt_version, created_at)"
        " VALUES (?,?,?,?,?) ON CONFLICT(source_text) DO UPDATE SET parsed = excluded.parsed,"
        " model = excluded.model, prompt_version = excluded.prompt_version,"
        " created_at = excluded.created_at",
        (source_text, db.dumps(entry), settings.text_model, PARSE_VERSION, db.now()),
    )


# --- pipeline ----------------------------------------------------------------------------


def _ingredients(document_json: str) -> list[dict]:
    document, _ = load_document(json.loads(document_json))
    lines = []
    for section in document.ingredient_sections:
        for item in section.ingredients:
            lines.append({"id": item.id, "source_text": item.source_text})
    return lines


def _parse_lines(conn: sqlite3.Connection, texts: list[str], llm: LLMFn) -> dict[str, dict]:
    """source_text -> cache entry ({'parse': {...}, 'match': {...}?}); model call for misses."""
    entries: dict[str, dict] = {}
    missing: list[str] = []
    for text in dict.fromkeys(texts):
        cached = _cache_get(conn, text)
        if cached and "parse" in cached:
            entries[text] = cached
        else:
            missing.append(text)
    for start in range(0, len(missing), PARSE_CHUNK):
        chunk = missing[start : start + PARSE_CHUNK]
        numbered = "\n".join(f"{n}. {text}" for n, text in enumerate(chunk, 1))
        response = llm(PARSE_PROMPT.format(lines=untrusted(numbered)), ParseResponse, "parse")
        by_index = {line.i: line for line in response.lines}  # type: ignore[attr-defined]
        for n, text in enumerate(chunk, 1):
            line = by_index.get(n)
            if line is None:
                log.warning("model skipped ingredient line %r", text)
                continue
            parse = line.model_dump(exclude={"i"})
            entries[text] = {"parse": parse}
            _cache_put(conn, text, entries[text])
    return entries


def _candidates(
    conn: sqlite3.Connection, parse: dict, source_text: str
) -> list[tuple[dict, tuple[float, str] | None]]:
    """FTS candidates, preferring those whose grams can be derived from FDC data."""
    found = fdc.search_foods(conn, parse["food"], limit=24)
    scored = [
        (food, fdc.grams_for(conn, food["fdc_id"], parse["quantity"], parse["unit"], source_text))
        for food in found
    ]
    convertible = [item for item in scored if item[1] is not None]
    rest = [item for item in scored if item[1] is None]
    return (convertible + rest)[:MAX_CANDIDATES]


def _match_lines(
    conn: sqlite3.Connection,
    todo: list[tuple[str, dict]],
    entries: dict[str, dict],
    llm: LLMFn,
) -> None:
    """One model call choosing FDC entries for every line in ``todo`` (text, parse)."""
    items: list[str] = []
    options: dict[int, list[dict]] = {}
    for n, (text, parse) in enumerate(todo, 1):
        cands = _candidates(conn, parse, text)
        if not cands:
            entries[text]["match"] = {
                "version": MATCH_VERSION,
                "food": parse["food"],
                "fdc_id": None,
                "quality": "none",
            }
            _cache_put(conn, text, entries[text])
            continue
        options[n] = [food for food, _ in cands]
        listing = "\n".join(
            f"   {k}. {food['description']} [{food['category'] or 'n/a'}]"
            for k, (food, _) in enumerate(cands, 1)
        )
        amount = " ".join(str(v) for v in (parse["quantity"], parse["unit"]) if v is not None)
        items.append(
            f"Line {n}: {untrusted(text)}\n   food: {parse['food']}; amount: {amount or 'n/a'}\n"
            f"   candidates:\n{listing}"
        )
    if not items:
        return
    response = llm(MATCH_PROMPT.format(items="\n\n".join(items)), MatchResponse, "match")
    chosen = {c.i: c for c in response.choices}  # type: ignore[attr-defined]
    for n, (text, parse) in enumerate(todo, 1):
        if n not in options:
            continue
        pick = chosen.get(n)
        fdc_id = None
        quality = "none"
        if pick and 1 <= pick.choice <= len(options[n]) and pick.quality != "none":
            fdc_id = options[n][pick.choice - 1]["fdc_id"]
            quality = pick.quality
        entries[text]["match"] = {
            "version": MATCH_VERSION,
            "food": parse["food"],
            "fdc_id": fdc_id,
            "quality": quality,
        }
        _cache_put(conn, text, entries[text])


_QTY = r"(?:\d+\s+\d+/\d+|\d+\s*[¼½¾⅓⅔⅛⅜⅝⅞⅕⅖⅗⅘⅙⅚]|\d+/\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞⅕⅖⅗⅘⅙⅚])"
_LEADING_QTY = re.compile(
    rf"^\s*(?P<a>{_QTY})(?:\s*(?:-|–|to)\s*(?P<b>{_QTY}))?\s*(?P<rest>.*)$", re.S
)


def _leading_quantity(text: str) -> tuple[float, str] | None:
    """Deterministic leading quantity ("1 1/3 cups ...", "2-3 cloves") and the next word."""
    match = _LEADING_QTY.match(text)
    if not match:
        return None
    a = parse_quantity(re.sub(r"\s+", " ", match.group("a")).replace(" ", " ", 1))
    if a is None:
        a = parse_quantity(re.sub(r"\s+", "", match.group("a")))
    if a is None:
        return None
    value = float(a)
    if match.group("b"):
        b = parse_quantity(re.sub(r"\s+", " ", match.group("b")))
        if b is not None and float(b) > value:
            value = (value + float(b)) / 2
    rest = match.group("rest").strip()
    word = re.match(r"([A-Za-z.]+(?:\s+oz\b)?)", rest)
    return value, (word.group(1) if word else "")


def reconcile_quantity(source_text: str, parse: dict) -> dict:
    """Trust the text over the model for plain leading quantities.

    Only applies when the unit written after the number is the unit the model returned
    (or both are a bare count), so package sizes ("1 (14 oz) can" -> 14 oz) and weights
    preferred over volumes ("1 cup (120 g)") keep the model's reading.
    """
    lead = _leading_quantity(source_text)
    if lead is None or parse.get("quantity") is None:
        return parse
    value, word = lead
    model_unit = fdc.normalize_unit(parse.get("unit"))
    text_unit = fdc.normalize_unit(word) if word else None
    text_is_unit = (
        text_unit in fdc.MASS_G or text_unit in fdc.VOLUME_ML or (text_unit in fdc.COUNT_WORDS)
    )
    bare = (None, "whole", "each")
    sizes = {"large", "medium", "small", "extra large", "jumbo"}
    same = (
        (model_unit == text_unit and text_is_unit)
        or (model_unit in bare and not text_is_unit)
        or (model_unit in bare and text_unit in sizes)
        or (model_unit in sizes and text_unit in sizes)
    )
    if same and abs(value - float(parse["quantity"])) > 1e-6:
        return {**parse, "quantity": value, "quantity_source": "text"}
    return parse


def _nutrients(food: dict, grams: float) -> dict[str, float]:
    factor = grams / 100.0
    return {key: (food.get(key) or 0.0) * factor for key in NUTRIENT_KEYS}


def _round_nutrients(values: dict[str, float]) -> dict[str, float]:
    out = {}
    for key in NUTRIENT_KEYS:
        value = values.get(key) or 0.0
        out[key] = round(value) if key in {"kcal", "sodium_mg"} else round(value, 1)
    return out


def _servings(conn: sqlite3.Connection, recipe: sqlite3.Row) -> tuple[float, bool]:
    servings = recipe["servings"]
    if servings and servings > 0:
        return float(servings), False
    try:
        yield_text = json.loads(recipe["document"]).get("yield_text") or ""
    except (ValueError, AttributeError):
        yield_text = ""
    match = re.search(r"(\d+(?:\.\d+)?)", yield_text)
    if match and 0 < float(match.group(1)) <= 100:
        return float(match.group(1)), False
    return DEFAULT_SERVINGS, True


def badge(mass_matched_pct: float | None) -> str:
    if mass_matched_pct is None:
        return "low"
    if mass_matched_pct >= HIGH_MASS_PCT:
        return "high"
    if mass_matched_pct >= MEDIUM_MASS_PCT:
        return "medium"
    return "low"


def compute_lines(
    conn: sqlite3.Connection,
    lines: list[dict],
    *,
    overrides: dict[str, dict] | None = None,
    llm: LLMFn | None = None,
) -> list[dict]:
    """Breakdown entries for ingredient lines [{id, source_text}] (no DB writes besides cache)."""
    llm = llm or _default_llm
    overrides = overrides or {}
    entries = _parse_lines(conn, [line["source_text"] for line in lines], llm)

    # Resolve foods: alias table, then the cached model pick, then one batched model call.
    todo: list[tuple[str, dict]] = []
    queued: set[str] = set()
    for line in lines:
        entry = entries.get(line["source_text"])
        if not entry or entry["parse"]["exclude"]:
            continue
        parse = entry["parse"]
        if not parse.get("food") or fdc.lookup_alias(parse["food"]) is not None:
            continue
        match = entry.get("match")
        if match and match.get("version") == MATCH_VERSION and match.get("food") == parse["food"]:
            if match["fdc_id"] is None or fdc.get_food(conn, match["fdc_id"]):
                continue
        if line["source_text"] not in queued and line["id"] not in overrides:
            todo.append((line["source_text"], parse))
            queued.add(line["source_text"])
    if todo:
        _match_lines(conn, todo, entries, llm)

    breakdown = []
    for line in lines:
        text = line["source_text"]
        entry = entries.get(text)
        item: dict[str, Any] = {
            "ingredient_id": line["id"],
            "source_text": text,
            "parsed": None,
            "food": None,
            "fdc_id": None,
            "grams": None,
            **{key: None for key in NUTRIENT_KEYS},
            "confidence": None,
            "excluded": True,
            "reason": None,
            "match": None,
            "grams_method": None,
            "overridden": False,
            "approx_grams": None,
        }
        breakdown.append(item)
        override = overrides.get(line["id"])
        if entry is None:
            item["reason"] = "could not parse"
            if override is None or override.get("fdc_id") is None:
                if override is not None:
                    item["reason"] = "excluded by user"
                    item["overridden"] = True
                continue
            parse = {
                "quantity": None,
                "unit": None,
                "food": text,
                "approx_grams": None,
                "exclude": False,
                "exclude_reason": None,
            }
        else:
            parse = reconcile_quantity(text, entry["parse"])
        item["parsed"] = {k: parse.get(k) for k in ("quantity", "unit", "food")}
        item["approx_grams"] = parse.get("approx_grams")

        if override is not None:
            item["overridden"] = True
            if override.get("fdc_id") is None:
                item["reason"] = "excluded by user"
                continue
            food = fdc.get_food(conn, int(override["fdc_id"]))
            if food is None:
                item["reason"] = f"override food {override['fdc_id']} not in FDC data"
                continue
            match_how, match_conf = "user override", "high"
        else:
            if parse["exclude"]:
                item["reason"] = parse.get("exclude_reason") or "excluded"
                continue
            fdc_id = fdc.lookup_alias(parse["food"]) if parse.get("food") else None
            match_how, match_conf = "alias", "high"
            if fdc_id is None:
                match = (entry or {}).get("match") or {}
                fdc_id = match.get("fdc_id")
                quality = match.get("quality", "none")
                match_how = f"model pick ({quality})"
                match_conf = {"exact": "high", "close": "medium", "approximate": "low"}.get(
                    quality, "low"
                )
            food = fdc.get_food(conn, fdc_id) if fdc_id is not None else None
            if food is None:
                item["reason"] = "no FDC match"
                continue

        item["food"] = food["description"]
        item["fdc_id"] = food["fdc_id"]
        item["match"] = match_how
        grams: float | None = None
        grams_conf = "high"
        if override is not None and override.get("grams"):
            grams, item["grams_method"] = float(override["grams"]), "user override"
        else:
            converted = fdc.grams_for(
                conn, food["fdc_id"], parse.get("quantity"), parse.get("unit"), text
            )
            if converted is not None:
                grams, item["grams_method"] = converted
                bare_count = parse.get("unit") is None or fdc.normalize_unit(parse["unit"]) in {
                    "whole",
                    "each",
                }
                if bare_count and not re.search(r"\b(large|medium|small|jumbo)\b", text.casefold()):
                    grams_conf = "medium"  # size not stated, FDC default portion used
            elif parse.get("approx_grams"):
                grams = float(parse["approx_grams"])
                item["grams_method"] = "model estimate (no FDC portion for this unit)"
                grams_conf = "low"
        if grams is None or grams <= 0:
            item["reason"] = "no amount that converts to grams"
            continue
        order = {"high": 2, "medium": 1, "low": 0}
        item["confidence"] = min(match_conf, grams_conf, key=lambda c: order[c])
        item["grams"] = round(grams, 1)
        item.update(_round_nutrients(_nutrients(food, grams)))
        item["excluded"] = False
        # unrounded values for exact totals
        item["_exact"] = _nutrients(food, grams)
        item["_fdc_grams"] = item["grams_method"] != (
            "model estimate (no FDC portion for this unit)"
        )
    return breakdown


def summarize(breakdown: list[dict], servings: float) -> dict:
    totals = {key: 0.0 for key in NUTRIENT_KEYS}
    matched_mass = 0.0
    total_mass = 0.0
    for item in breakdown:
        if item["excluded"]:
            # Rule-excluded lines (to taste, optional, water, user) are not part of the mass.
            if item["reason"] in {"no FDC match", "no amount that converts to grams"}:
                total_mass += float(item.get("approx_grams") or 0)
            continue
        for key in NUTRIENT_KEYS:
            totals[key] += item["_exact"][key]
        total_mass += item["grams"]
        if item["_fdc_grams"] and item["confidence"] in {"high", "medium"}:
            matched_mass += item["grams"]
    pct = round(100.0 * matched_mass / total_mass, 1) if total_mass > 0 else None
    per_serving = _round_nutrients({k: v / servings for k, v in totals.items()})
    return {
        "per_serving": per_serving,
        "totals": _round_nutrients(totals),
        "mass_matched_pct": pct,
        "confidence": badge(pct),
    }


def _clean(breakdown: list[dict]) -> list[dict]:
    return [{k: v for k, v in item.items() if not k.startswith("_")} for item in breakdown]


def compute_nutrition(
    conn: sqlite3.Connection,
    recipe_id: str,
    *,
    llm: LLMFn | None = None,
    secondary: bool = True,
) -> dict:
    """Compute the nutrition result for a recipe (does not write the nutrition row).

    Returns ``{recipe_id, source, per_serving, confidence, servings, servings_assumed,
    mass_matched_pct, breakdown, totals, computed_per_serving, computed_confidence,
    version}``. ``source`` is 'publisher' when a publisher block with calories exists
    (``confidence`` is then None), else 'computed'. With ``secondary`` the per-line
    breakdown is also computed for publisher recipes (model calls are cached).
    """
    recipe = conn.execute(
        "SELECT id, document, servings FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if recipe is None:
        raise KeyError(f"recipe {recipe_id} not found")
    servings, servings_assumed = _servings(conn, recipe)
    publisher = None
    row = conn.execute(
        "SELECT data FROM source_nutrition WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()
    if row is not None:
        try:
            publisher = parse_publisher_nutrition(db.loads(row["data"]), servings)
        except (ValueError, TypeError):
            publisher = None
    overrides = {
        r["ingredient_id"]: {"fdc_id": r["fdc_id"], "grams": r["grams"]}
        for r in conn.execute(
            "SELECT ingredient_id, fdc_id, grams FROM nutrition_overrides WHERE recipe_id = ?",
            (recipe_id,),
        )
    }
    result: dict[str, Any] = {
        "recipe_id": recipe_id,
        "servings": servings,
        "servings_assumed": servings_assumed,
        "version": current_version(),
        "breakdown": [],
        "totals": None,
        "mass_matched_pct": None,
        "computed_per_serving": None,
        "computed_confidence": None,
    }
    if publisher is None or secondary:
        if not fdc_loaded(conn):
            # Never cache "no match" results against an empty food table.
            raise RuntimeError("FDC data not loaded: run `uv run python scripts/load_fdc.py`")
        breakdown = compute_lines(
            conn, _ingredients(recipe["document"]), overrides=overrides, llm=llm
        )
        summary = summarize(breakdown, servings)
        result.update(
            breakdown=_clean(breakdown),
            totals=summary["totals"],
            mass_matched_pct=summary["mass_matched_pct"],
            computed_per_serving=summary["per_serving"],
            computed_confidence=summary["confidence"],
        )
    if publisher is not None:
        result["source"] = "publisher"
        result["per_serving"] = {key: publisher.get(key) for key in NUTRIENT_KEYS}
        result["confidence"] = None
    else:
        result["source"] = "computed"
        result["per_serving"] = result["computed_per_serving"]
        result["confidence"] = result["computed_confidence"]
    return result


def save_nutrition(conn: sqlite3.Connection, recipe_id: str, result: dict) -> None:
    """Upsert the ``nutrition`` row. ``breakdown`` column holds a JSON object:
    {version, mass_matched_pct, servings_assumed, totals, computed_per_serving,
    computed_confidence, lines: [...]}."""
    breakdown = {
        "version": result["version"],
        "mass_matched_pct": result["mass_matched_pct"],
        "servings_assumed": result["servings_assumed"],
        "totals": result["totals"],
        "computed_per_serving": result["computed_per_serving"],
        "computed_confidence": result["computed_confidence"],
        "lines": result["breakdown"],
    }
    conn.execute(
        "INSERT INTO nutrition(recipe_id, source, per_serving, confidence, breakdown, servings,"
        " computed_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET"
        " source = excluded.source, per_serving = excluded.per_serving,"
        " confidence = excluded.confidence, breakdown = excluded.breakdown,"
        " servings = excluded.servings, computed_at = excluded.computed_at",
        (
            recipe_id,
            result["source"],
            db.dumps(result["per_serving"]),
            result["confidence"],
            db.dumps(breakdown),
            result["servings"],
            db.now(),
        ),
    )


def set_override(
    conn: sqlite3.Connection,
    recipe_id: str,
    ingredient_id: str,
    fdc_id: int | None,
    grams: float | None = None,
) -> None:
    """Persist a user override (fdc_id None = exclude the line). Recompute afterwards."""
    if fdc_id is not None and fdc.get_food(conn, fdc_id) is None:
        raise ValueError(f"unknown fdc_id {fdc_id}")
    conn.execute(
        "INSERT INTO nutrition_overrides(recipe_id, ingredient_id, fdc_id, grams, created_at)"
        " VALUES (?,?,?,?,?) ON CONFLICT(recipe_id, ingredient_id) DO UPDATE SET"
        " fdc_id = excluded.fdc_id, grams = excluded.grams, created_at = excluded.created_at",
        (recipe_id, ingredient_id, fdc_id, grams, db.now()),
    )


def clear_override(conn: sqlite3.Connection, recipe_id: str, ingredient_id: str) -> None:
    conn.execute(
        "DELETE FROM nutrition_overrides WHERE recipe_id = ? AND ingredient_id = ?",
        (recipe_id, ingredient_id),
    )


def refresh(conn: sqlite3.Connection, recipe_id: str, *, llm: LLMFn | None = None) -> dict:
    """compute + save; the call the API makes after an override or a document edit."""
    result = compute_nutrition(conn, recipe_id, llm=llm)
    save_nutrition(conn, recipe_id, result)
    return result


# --- CLI ---------------------------------------------------------------------------------


def backfill(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    recipe_id: str | None = None,
    force: bool = False,
    llm: LLMFn | None = None,
) -> dict[str, int]:
    if not fdc_loaded(conn):
        raise SystemExit("FDC data not loaded: run `uv run python scripts/load_fdc.py` first")
    version = current_version()
    if recipe_id:
        ids = [recipe_id]
    else:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM recipes WHERE archived_at IS NULL ORDER BY created_at, id"
            )
        ]
    done = {
        r["recipe_id"]: r["breakdown"]
        for r in conn.execute("SELECT recipe_id, breakdown FROM nutrition")
    }
    stats = {"computed": 0, "skipped": 0, "failed": 0}
    for rid in ids:
        if not force and rid in done:
            try:
                if (db.loads(done[rid]) or {}).get("version") == version:
                    stats["skipped"] += 1
                    continue
            except (ValueError, AttributeError):
                pass
        if limit is not None and stats["computed"] + stats["failed"] >= limit:
            break
        try:
            result = refresh(conn, rid, llm=llm)
        except Exception as exc:  # keep going; the next run retries this recipe
            log.warning("nutrition failed for %s: %s", rid, exc)
            stats["failed"] += 1
            continue
        stats["computed"] += 1
        log.info(
            "%s: %s %s kcal/serving (%s, %s%% mass matched)",
            rid,
            result["source"],
            (result["per_serving"] or {}).get("kcal"),
            result["confidence"],
            result["mass_matched_pct"],
        )
    return stats


def fdc_loaded(conn: sqlite3.Connection) -> bool:
    from .fdc_load import is_loaded

    return is_loaded(conn)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m cookt.enrich.nutrition")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("backfill", help="compute nutrition for recipes (resumable)")
    run.add_argument("--db", type=Path, default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--recipe", default=None)
    run.add_argument("--force", action="store_true", help="recompute even if up to date")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.connect(args.db)
    db.init(conn)
    stats = backfill(conn, limit=args.limit, recipe_id=args.recipe, force=args.force)
    print(json.dumps(stats))


if __name__ == "__main__":
    main(sys.argv[1:])
