"""USDA FoodData Central lookups: food search and quantity -> grams.

Deterministic, no model calls. ``grams_for`` converts a parsed ingredient
quantity to grams using standard mass units or the food's FDC portion data and
returns ``None`` rather than guessing when no portion supports the conversion.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).with_name("fdc_aliases.json")

MASS_G = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "oz": 28.3495,
    "lb": 453.592,
}
VOLUME_ML = {
    "ml": 1.0,
    "cl": 10.0,
    "dl": 100.0,
    "l": 1000.0,
    "tsp": 4.92892,
    "tbsp": 14.7868,
    "fl oz": 29.5735,
    "cup": 236.588,
    "pint": 473.176,
    "quart": 946.353,
    "gallon": 3785.41,
    "pinch": 4.92892 / 16,
    "dash": 4.92892 / 8,
}
UNIT_SYNONYMS = {
    "g": "g",
    "gr": "g",
    "gram": "g",
    "grams": "g",
    "gm": "g",
    "gms": "g",
    "kg": "kg",
    "kgs": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "mg": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "wt oz": "oz",
    "oz wt": "oz",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "#": "lb",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "cc": "ml",
    "cl": "cl",
    "dl": "dl",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "lt": "l",
    "tsp": "tsp",
    "t": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tsps": "tsp",
    "tspn": "tsp",
    "tbsp": "tbsp",
    "tbs": "tbsp",
    "tbl": "tbsp",
    "tbsps": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tblsp": "tbsp",
    "tbspn": "tbsp",
    "fl oz": "fl oz",
    "fl. oz": "fl oz",
    "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    "floz": "fl oz",
    "cup": "cup",
    "cups": "cup",
    "c": "cup",
    "pint": "pint",
    "pints": "pint",
    "pt": "pint",
    "quart": "quart",
    "quarts": "quart",
    "qt": "quart",
    "gallon": "gallon",
    "gallons": "gallon",
    "gal": "gallon",
    "pinch": "pinch",
    "pinches": "pinch",
    "dash": "dash",
    "dashes": "dash",
    "smidgen": "pinch",
}
# FDC portion unit / modifier words that name a volume.
PORTION_VOLUME = {
    "cup": "cup",
    "cups": "cup",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "fl oz": "fl oz",
    "milliliter": "ml",
    "ml": "ml",
    "liter": "l",
    "quart": "quart",
    "pint": "pint",
    "gallon": "gallon",
}
# Count-style units: matched against FDC portion modifiers.
COUNT_WORDS = {
    "whole",
    "each",
    "large",
    "medium",
    "small",
    "extra large",
    "jumbo",
    "clove",
    "slice",
    "stick",
    "can",
    "piece",
    "head",
    "bunch",
    "stalk",
    "sprig",
    "leaf",
    "ear",
    "fillet",
    "breast",
    "thigh",
    "drumstick",
    "wing",
    "link",
    "strip",
    "wedge",
    "fruit",
    "bulb",
    "package",
    "packet",
    "envelope",
    "container",
    "jar",
    "bottle",
    "sheet",
    "tortilla",
    "bun",
    "roll",
    "bagel",
    "muffin",
    "patty",
    "pepper",
    "rib",
    "spear",
    "stem",
}
COUNT_SYNONYMS = {
    "cloves": "clove",
    "slices": "slice",
    "sticks": "stick",
    "cans": "can",
    "pieces": "piece",
    "pc": "piece",
    "pcs": "piece",
    "heads": "head",
    "bunches": "bunch",
    "stalks": "stalk",
    "sprigs": "sprig",
    "leaves": "leaf",
    "ears": "ear",
    "fillets": "fillet",
    "breasts": "breast",
    "thighs": "thigh",
    "drumsticks": "drumstick",
    "wings": "wing",
    "links": "link",
    "strips": "strip",
    "wedges": "wedge",
    "fruits": "fruit",
    "bulbs": "bulb",
    "packages": "package",
    "pkg": "package",
    "packets": "packet",
    "envelopes": "envelope",
    "containers": "container",
    "jars": "jar",
    "bottles": "bottle",
    "sheets": "sheet",
    "tortillas": "tortilla",
    "buns": "bun",
    "rolls": "roll",
    "bagels": "bagel",
    "ribs": "rib",
    "spears": "spear",
    "stems": "stem",
    "lg": "large",
    "med": "medium",
    "sm": "small",
    "xl": "extra large",
    "ea": "each",
    "item": "each",
    "items": "each",
    "egg": "whole",
    "eggs": "whole",
}
# Preference order when the recipe gives a bare count ("2 onions", "3 eggs").
BARE_COUNT_PREFERENCE = ["medium", "whole", "large", "each", "fruit", "small", "extra large"]
STOPWORDS = {
    "a",
    "an",
    "and",
    "or",
    "of",
    "the",
    "to",
    "for",
    "with",
    "in",
    "into",
    "on",
    "at",
    "fresh",
    "freshly",
    "chopped",
    "minced",
    "diced",
    "sliced",
    "large",
    "small",
    "medium",
    "about",
    "plus",
    "more",
    "taste",
    "divided",
    "finely",
    "roughly",
    "coarsely",
    "thinly",
    "cut",
    "pieces",
    "piece",
    "optional",
    "peeled",
    "trimmed",
    "packed",
    "softened",
    "melted",
    "room",
    "temperature",
    "cold",
    "warm",
    "hot",
    "cup",
    "cups",
    "tbsp",
    "tsp",
    "g",
    "oz",
    "lb",
    "ml",
    "good",
    "quality",
    "best",
    "your",
    "favorite",
    "such",
    "as",
    "like",
    "note",
}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = re.sub(r"\s+", " ", unit.strip().casefold().rstrip("."))
    text = text.replace("fl. oz", "fl oz").replace("fluid oz", "fl oz")
    if not text:
        return None
    if text in UNIT_SYNONYMS:
        return UNIT_SYNONYMS[text]
    if text in COUNT_WORDS:
        return text
    if text in COUNT_SYNONYMS:
        return COUNT_SYNONYMS[text]
    if text.endswith("es") and text[:-2] in COUNT_WORDS:
        return text[:-2]
    if text.endswith("s") and text[:-1] in COUNT_WORDS:
        return text[:-1]
    return text


def unit_kind(unit: str | None) -> str:
    """'mass' | 'volume' | 'count' (a bare count has unit None)."""
    norm = normalize_unit(unit)
    if norm in MASS_G:
        return "mass"
    if norm in VOLUME_ML:
        return "volume"
    return "count"


@dataclass(frozen=True, slots=True)
class Portion:
    amount: float
    unit: str | None  # normalized volume unit, 'mass:<unit>', or None for count portions
    label: str  # human-readable, e.g. "1 cup, chopped"
    words: frozenset[str]
    grams: float

    @property
    def ml(self) -> float | None:
        if self.unit in VOLUME_ML:
            return self.amount * VOLUME_ML[self.unit]
        return None


def _singular_word(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("oes", "ches", "shes", "sses")) and len(word) > 4:
        return word[:-2]
    if word in {"leaves", "halves", "loaves"}:
        return word[:-3] + "f"
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _fold(text: str) -> str:
    """casefold + strip accents ("jalapeño" -> "jalapeno")."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _words(text: str) -> frozenset[str]:
    """Lower-cased, singularized words (parenthetical asides like '(2-1/2" dia)' dropped)."""
    text = re.sub(r"\(.*?\)", " ", _fold(text))
    return frozenset(_singular_word(w) for w in re.findall(r"[a-z]+", text) if len(w) > 1)


def _parse_portion(
    amount: float | None,
    unit: str | None,
    modifier: str | None,
    description: str | None,
    grams: float,
) -> Portion:
    amount = amount if amount and amount > 0 else 1.0
    unit_name = (unit or "").strip().casefold()
    mod = (modifier or "").strip()
    desc = (description or "").strip()
    label_parts = [
        p for p in [unit_name if unit_name not in {"", "undetermined"} else "", mod, desc] if p
    ]
    label = f"{amount:g} " + ", ".join(label_parts) if label_parts else f"{amount:g} portion"
    text = " ".join(label_parts).casefold()
    norm: str | None = None
    if unit_name in PORTION_VOLUME:
        norm = PORTION_VOLUME[unit_name]
    elif unit_name in {"oz", "lb", "g"}:
        norm = "mass:" + unit_name
    else:
        # SR Legacy: unit 'undetermined', unit lives at the start of the modifier text.
        head = re.split(r"[,(]", mod.casefold() or desc.casefold(), maxsplit=1)[0].strip()
        head = head.replace("fl. oz", "fl oz")
        if head in PORTION_VOLUME:
            norm = PORTION_VOLUME[head]
        elif head in {"oz", "lb", "g"}:
            norm = "mass:" + head
        elif re.match(r"^cups?\b", head):
            norm = "cup"
        elif re.match(r"^(tbsp|tablespoons?)\b", head):
            norm = "tbsp"
        elif re.match(r"^(tsp|teaspoons?)\b", head):
            norm = "tsp"
        elif re.match(r"^fl oz\b", head):
            norm = "fl oz"
    return Portion(amount, norm, label, _words(text), grams)


def portions(conn: sqlite3.Connection, fdc_id: int) -> list[Portion]:
    rows = conn.execute(
        "SELECT amount, unit, modifier, description, gram_weight FROM fdc_portion"
        " WHERE fdc_id = ? ORDER BY rowid",
        (fdc_id,),
    ).fetchall()
    return [_parse_portion(r[0], r[1], r[2], r[3], r[4]) for r in rows]


# Words that describe a measure rather than a form of the food; ignored when comparing a
# portion's descriptors with the ingredient text.
NEUTRAL_PORTION_WORDS = {
    "cup",
    "tbsp",
    "tsp",
    "tablespoon",
    "teaspoon",
    "fl",
    "oz",
    "lb",
    "ml",
    "fluid",
    "dia",
    "approx",
    "about",
    "yield",
    "level",
    "heaping",
    "measure",
    "unpacked",
    "loosely",
}
AVOID_PORTION_WORDS = {"nlea", "serving", "prepared", "reconstituted", "whipped", "yield"}
SIZE_WORDS = ("extra large", "jumbo", "large", "small", "medium")


def _score_volume_portion(portion: Portion, text_words: frozenset[str]) -> float:
    descriptors = portion.words - NEUTRAL_PORTION_WORDS
    score = 2.0 * len(descriptors & text_words)  # "1 cup, chopped" for "chopped onion"
    score -= 0.5 * len(descriptors - text_words)  # "1 cup, whipped" for "heavy cream"
    score -= 1.5 * len(AVOID_PORTION_WORDS & portion.words - text_words)
    # prefer bigger reference measures (a cup is measured more precisely than a tsp)
    score += 0.1 * {"cup": 3, "tbsp": 2, "fl oz": 2, "tsp": 1}.get(portion.unit or "", 0)
    return score


def grams_for(
    conn: sqlite3.Connection,
    fdc_id: int,
    quantity: float | None,
    unit: str | None,
    food_text: str,
) -> tuple[float, str] | None:
    """Convert ``quantity unit`` of an FDC food to grams.

    Returns ``(grams, method)`` where method explains the conversion, or None
    when the unit cannot be converted from standard units or the food's portions.
    """
    norm = normalize_unit(unit)
    if quantity is None:
        if norm in {"pinch", "dash"}:
            quantity = 1.0
        else:
            return None
    if quantity <= 0:
        return None
    if norm in MASS_G:
        grams = quantity * MASS_G[norm]
        return grams, f"{quantity:g} {norm} = {grams:.1f} g (standard mass)"
    food_portions = portions(conn, fdc_id)
    text_words = _words(food_text)
    if norm in VOLUME_ML:
        ml = quantity * VOLUME_ML[norm]
        volume_portions = [p for p in food_portions if p.ml]
        if not volume_portions:
            return None
        best = max(volume_portions, key=lambda p: _score_volume_portion(p, text_words))
        grams = ml * best.grams / best.ml  # type: ignore[operator]
        how = "portion" if best.unit == norm else "scaled portion"
        return grams, f"{quantity:g} {norm} via {how} '{best.label}' = {best.grams:g} g"
    # Count units (unit None = bare count).
    count_portions = [
        p for p in food_portions if p.unit is None and not ({"serving", "nlea", "cubic"} & p.words)
    ]
    if not count_portions:
        return None
    wanted = norm if norm and norm not in {"whole", "each"} else None
    if wanted:
        wanted_words = _words(wanted)
        matches = [p for p in count_portions if wanted_words <= p.words]
        if not matches:
            # e.g. "1 can" or "2 large" with no such portion: fail rather than guess.
            return None
        # "2 medium" with "medium (2-1/2" dia)" and "medium, chopped": prefer overlap with
        # the ingredient text, then the plainest portion.
        chosen = max(matches, key=lambda p: (len(p.words & text_words), -len(p.words)))
    else:
        lowered = food_text.casefold()
        size = next((s for s in SIZE_WORDS if re.search(rf"\b{s}\b", lowered)), None)
        # US recipes mean large eggs unless they say otherwise.
        default_size = ["large"] if re.search(r"\beggs?\b", lowered) else []
        order = ([size] if size else []) + default_size + BARE_COUNT_PREFERENCE
        chosen = None
        for word in order:
            matches = [p for p in count_portions if _words(word) <= p.words]
            if matches:
                chosen = min(matches, key=lambda p: len(p.words))
                break
        if chosen is None:
            # A portion named after the food itself ("1 tortilla", "1 thigh", "1 pepper" for
            # "Peppers, jalapeno, raw"): match the ingredient text and the FDC description.
            row = conn.execute(
                "SELECT description FROM fdc_food WHERE fdc_id = ?", (fdc_id,)
            ).fetchone()
            named = text_words | (_words(row[0]) if row else frozenset())
            matches = [
                p
                for p in count_portions
                if (p.words & named)
                and not ({"cup", "oz", "lb", "tbsp", "tsp", "package", "container"} & p.words)
            ]
            if matches:
                chosen = max(matches, key=lambda p: (len(p.words & text_words), -len(p.words)))
        if chosen is None:
            return None
    # SR Legacy lists some pieces as a fraction of the anatomical whole ("0.5 breast",
    # "0.5 fillet"); recipes count those pieces as sold, so such a portion is one piece.
    per_piece = chosen.grams if chosen.amount < 1 else chosen.grams / chosen.amount
    grams = quantity * per_piece
    return grams, f"{quantity:g} × '{chosen.label}' ({per_piece:g} g each)"


# --- search ------------------------------------------------------------------------------


SPECIAL_VARIANT_WORDS = {
    "nonfat",
    "free",
    "low",
    "reduced",
    "substitute",
    "imitation",
    "light",
    "lite",
    "diet",
    "dietetic",
    "sweetened",
    "unsweetened",
    "fortified",
    "dehydrated",
    "frozen",
    "canned",
    "cooked",
    "dried",
    "dry",
    "smoked",
    "cured",
    "breaded",
    "fried",
    "mix",
    "prepared",
}


# Diet/marketing labels that don't change which generic food a line is ("gluten-free rolled
# oats" -> rolled oats). Nutrition differences for these are within FDC's own variance.
DIET_LABELS = re.compile(
    r"\b(?:(?:gluten|dairy|grain|nut|soy)[- ]free|organic|vegan|non[- ]gmo|all[- ]natural)\b"
)


def strip_labels(text: str) -> str:
    return re.sub(r"\s+", " ", DIET_LABELS.sub(" ", _fold(text))).strip()


def _fts_terms(query: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z]+", strip_labels(query)) if len(w) > 1]
    return [w for w in words if w not in STOPWORDS]


def search_foods(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """FTS5 search over FDC descriptions. Prefers AND-matches, then OR-matches;
    within each, short generic descriptions and SR Legacy (which carries portions)."""
    terms = _fts_terms(query)
    if not terms:
        return []
    results: list[dict] = []
    seen: set[int] = set()
    wants_cooked = bool(
        {"cooked", "roasted", "boiled", "baked", "fried", "grilled", "canned", "dried", "toasted"}
        & set(terms)
    )
    for joiner in (" AND ", " OR "):
        match = joiner.join(f'"{t}"' for t in terms)
        try:
            rows = conn.execute(
                "SELECT f.fdc_id, f.description, f.category, f.data_type, f.kcal, f.protein_g,"
                " f.carbs_g, f.fat_g, f.fiber_g, f.sodium_mg, bm25(fdc_food_fts, 5.0, 0.5) AS score"
                " FROM fdc_food_fts JOIN fdc_food f ON f.fdc_id = fdc_food_fts.rowid"
                " WHERE fdc_food_fts MATCH ? AND f.kcal IS NOT NULL"
                " ORDER BY score LIMIT 200",
                (match,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        scored = []
        for row in rows:
            if row["fdc_id"] in seen:
                continue
            desc = row["description"].casefold()
            desc_words = set(re.findall(r"[a-z]+", desc))
            score = row["score"]  # more negative is better
            score += 0.04 * len(desc)  # generic beats specific
            first = re.split(r"[,(]", desc, maxsplit=1)[0]
            if any(t in first or first.startswith(t[:4]) for t in terms):
                score -= 1.5  # head noun matches ("Onions, raw" for "onion")
            if "raw" in desc_words and not wants_cooked:
                score -= 1.0
            if row["data_type"] == "sr_legacy_food":
                score -= 0.3
            if {"babyfood", "infant", "formula"} & desc_words:
                score += 5
            special = SPECIAL_VARIANT_WORDS & desc_words - set(terms)
            score += 1.5 * len(special)  # "nonfat", "substitute" only when asked for
            if row["category"] in {"Restaurant Foods", "Fast Foods", "Baby Foods"}:
                score += 4
            if re.search(r"\b[A-Z][A-Z'&]{2,}\b", row["description"]):
                score += 2  # brand names are upper-case in SR Legacy
            scored.append((score, row))
        scored.sort(key=lambda item: item[0])
        for _, row in scored:
            seen.add(row["fdc_id"])
            results.append(dict(row) | {"score": None})
            if len(results) >= limit:
                return results
    return results


def get_food(conn: sqlite3.Connection, fdc_id: int) -> dict | None:
    row = conn.execute(
        "SELECT fdc_id, description, category, data_type, kcal, protein_g, carbs_g, fat_g,"
        " fiber_g, sodium_mg FROM fdc_food WHERE fdc_id = ?",
        (fdc_id,),
    ).fetchone()
    return dict(row) if row else None


# --- aliases -----------------------------------------------------------------------------


# Words that never change which alias applies ("fresh basil" = "basil").
ALIAS_FILLER = {
    "fresh",
    "freshly",
    "organic",
    "ripe",
    "good",
    "quality",
    "store",
    "bought",
    "homemade",
    "deli",
    "sliced",
    "plain",
    "regular",
    "pure",
    "real",
    "large",
    "medium",
    "small",
}


@lru_cache(maxsize=1)
def aliases() -> dict[str, int]:
    """Curated common-ingredient name -> fdc_id map, keyed by ``alias_key``."""
    data = json.loads(ALIASES_PATH.read_text())
    return {alias_key(key): int(value) for key, value in data["aliases"].items()}


@lru_cache(maxsize=1)
def _aliases_unordered() -> dict[str, int]:
    return {" ".join(sorted(key.split())): value for key, value in aliases().items()}


def alias_key(name: str) -> str:
    text = _fold(name).strip().replace("-", " ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9% ]+", " ", text)
    words = [_singular_word(w) for w in text.split()]
    return " ".join(words)


def lookup_alias(name: str) -> int | None:
    """Exact alias, then word-order-insensitive, then without filler words."""
    if not name:
        return None
    table = aliases()
    key = alias_key(strip_labels(name) or name)
    if key in table:
        return table[key]
    unordered = _aliases_unordered()
    sorted_key = " ".join(sorted(key.split()))
    if sorted_key in unordered:
        return unordered[sorted_key]
    trimmed = [w for w in key.split() if w not in ALIAS_FILLER]
    if trimmed and len(trimmed) < len(key.split()):
        return unordered.get(" ".join(sorted(trimmed)))
    return None
