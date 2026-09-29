"""Pure, deterministic normalization helpers for the A (recipe-table) + B (cookt2) migration.

Nothing in here touches a database, the filesystem, or a model. Everything is unit-tested in
backend/tests/test_migrate.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..document import (
    Ingredient,
    IngredientSection,
    InstructionSection,
    InstructionStep,
    RecipeDocumentV2,
)

# --------------------------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------------------------

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "yclid",
    "igshid",
    "igsh",
    "ref",
    "ref_src",
    "ref_url",
    "referrer",
    "_ga",
    "_gl",
    "si",
    "s_cid",
    "cmpid",
    "ncid",
    "smid",
    "smtyp",
    "sr_share",
    "share",
    "spm",
    "trk",
    "rss",
    "mbid",
}
TRACKING_PREFIXES = ("utm_", "mc_", "hsa_", "pk_", "trk_")


def canonical_url(url: str | None) -> str | None:
    """Canonical form used for A<->B matching and `recipes.canonical_url`.

    lowercase host, strip `www.`, force https, drop fragment, default port and tracking
    params (utm_*, fbclid, gclid, mc_*, ref, ...), sort the remaining query, drop the
    trailing slash. Non-http(s) values (e.g. `urn:recipe-table:html:...`) return None.
    """

    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    path = re.sub(r"/{2,}", "/", parts.path or "")
    path = path.rstrip("/")
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def site_from_url(url: str | None) -> str | None:
    canon = canonical_url(url)
    if not canon:
        return None
    return urlsplit(canon).hostname


# --------------------------------------------------------------------------------------------
# Text folding
# --------------------------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lowercase ASCII fold: 'Crème Fraîche’s' -> "creme fraiche's"."""

    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


# --------------------------------------------------------------------------------------------
# Times ("Prep: 30 mins", "Cook Time: 1 hr 15 min", "Ready in about 20 minutes")
# --------------------------------------------------------------------------------------------

_NUM = r"\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+|\d*[½¼¾⅓⅔]"
_UNIT = r"days?|d|hours?|hrs?|h|minutes?|mins?|m"
_DURATION_PART = rf"(?P<n>{_NUM})\s*(?P<u>{_UNIT})\b\.?"
_DURATION_EXPR = re.compile(
    rf"^(?:(?:{_NUM})\s*(?:{_UNIT})\b\.?(?:\s*(?:,|and|\+)?\s*))+$", re.IGNORECASE
)
_TIME_LINE = re.compile(
    r"^\s*(?P<label>prep(?:aration)?|cook(?:ing)?|total|ready in|active|inactive)"
    r"(?:\s+time)?\s*[:\-–]?\s*(?:about|approx\.?|approximately|~)?\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_UNICODE_FRACTIONS = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4)}
_UNICODE_FRACTIONS.update({"⅓": Fraction(1, 3), "⅔": Fraction(2, 3)})
_LABEL_KIND = {
    "prep": "prep",
    "preparation": "prep",
    "cook": "cook",
    "cooking": "cook",
    "total": "total",
    "ready in": "total",
    "active": None,
    "inactive": None,
}


def _parse_number(text: str) -> Fraction:
    text = text.strip()
    if text and text[-1] in _UNICODE_FRACTIONS:
        whole = text[:-1].strip()
        return Fraction(int(whole or "0")) + _UNICODE_FRACTIONS[text[-1]]
    if " " in text:
        whole, frac = text.split(None, 1)
        return Fraction(int(whole)) + Fraction(frac)
    return Fraction(text)


def parse_duration_minutes(text: str) -> int | None:
    """'1 hr 15 min' -> 75, '1 1/2 hours' -> 90, '10mins' -> 10, '20' -> 20 (bare = minutes).

    The whole string must be a duration; prose ('400 degrees for 20 minutes') returns None.
    """

    value = text.strip().rstrip(".").strip()
    value = re.sub(r"\s+total time$", "", value, flags=re.IGNORECASE)
    if re.fullmatch(r"\d+", value):
        return int(value)
    if not _DURATION_EXPR.match(value):
        return None
    total = Fraction(0)
    for match in re.finditer(_DURATION_PART, value, re.IGNORECASE):
        number = _parse_number(match.group("n"))
        unit = match.group("u").lower()
        if unit.startswith("d"):
            total += number * 1440
        elif unit.startswith("h"):
            total += number * 60
        else:
            total += number
    minutes = round(total)
    return minutes if minutes > 0 else None


@dataclass
class TimeParse:
    prep: int | None = None
    cook: int | None = None
    total: int | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def parse_times(notes: list[str]) -> TimeParse:
    """Extract prep/cook/total minutes from A's `document.notes` lines (first hit per kind)."""

    result = TimeParse()
    for note in notes:
        match = _TIME_LINE.match(note)
        if not match:
            continue
        kind = _LABEL_KIND.get(match.group("label").lower())
        if kind is None:
            continue
        rest = match.group("rest")
        minutes = parse_duration_minutes(rest)
        if minutes is None:
            # Only flag lines that look like "Label: <something with a number>".
            if re.search(r"\d", rest) and len(note) <= 80:
                result.flags.append(f"unparsed time note: {note!r}")
            continue
        if re.fullmatch(r"\d+", rest.strip()):
            result.flags.append(f"assumed minutes for bare number: {note!r}")
        if re.search(r"total time$", rest.strip(), re.IGNORECASE) and kind != "total":
            result.flags.append(
                f"'{kind}' label with 'total time' wording kept as {kind}: {note!r}"
            )
        if getattr(result, kind) is None:
            setattr(result, kind, minutes)
            result.evidence[kind] = note
    return result


# --------------------------------------------------------------------------------------------
# Yield -> servings
# --------------------------------------------------------------------------------------------

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "a dozen": 12,
    "dozen": 12,
}
_SERVING_WORDS = r"servings?|serves|people|persons?|portions?|bowls?|plates?|meal prep containers?"
_ITEM_WORDS = (
    r"cookies?|muffins?|scones?|scuffins?|rolls?|buns?|donuts?|doughnuts?|holes|tostadas?|"
    r"pastries|slices?|bars?|balls?|bites?|pancakes?|waffles?|crumpets?|bagels?|"
    r"hors d'oeuvres|tikki|sandwich(?:es)?|tacos?|burgers?|patties|fritters?|"
    r"eggs|cups? ?cakes|cupcakes?|biscuits?|pieces|kolaches|empanadas|dumplings|skewers|wings"
)
# A volume / whole-dish yield is not a servings count.
_VOLUME_WORDS = (
    r"cups?|tablespoons?|tbsp|teaspoons?|tsp|quarts?|pints?|ounces?|oz|liters?|litres?|ml|"
    r"loaf|loaves|pies?|strombolis?|tarts?|tartes?|cakes?|pans?|jars?|batch(?:es)?|pounds?|lbs?|grams?|g"
)


@dataclass
class YieldParse:
    servings: float | None
    flag: str | None = None


def _first_number(text: str) -> int | None:
    """Earliest number in the text, digits or words ('two 12 inch strombolis' -> 2)."""

    hits: list[tuple[int, int]] = []
    match = re.search(r"\d+", text)
    if match:
        hits.append((match.start(), int(match.group())))
    for word, value in _WORD_NUMBERS.items():
        found = re.search(rf"\b{word}\b", text)
        if found:
            hits.append((found.start(), value))
    return min(hits)[1] if hits else None


def parse_yield(yield_text: str | None) -> YieldParse:
    """'Serves 4-6' -> 4, 'Makes 24 cookies' -> 24, '1 (9-inch) pie' -> None + flag."""

    if yield_text is None or not yield_text.strip():
        return YieldParse(None)
    text = fold(yield_text).strip()
    text = re.sub(r"\s+", " ", text)
    # "Servings (2-tbsp servings)" / "Servings (~1 cup servings)": the number is a serving size.
    paren_size = re.fullmatch(r"servings?\s*\((?:~|about )?[^)]*\)", text)
    if paren_size:
        return YieldParse(None, "serving size only, no count")
    has_serving = re.search(rf"\b(?:{_SERVING_WORDS})\b", text) is not None
    has_item = re.search(rf"\b(?:{_ITEM_WORDS})\b", text) is not None
    has_volume = re.search(rf"\d\s*(?:{_VOLUME_WORDS})\b|\b(?:{_VOLUME_WORDS})\b", text) is not None
    # "Serving: 1bar" -> per-serving size, not a count.
    if re.fullmatch(r"serving:? ?\d+ ?[a-z]+", text):
        return YieldParse(None, "serving size only, no count")
    text = re.sub(r"^(?:yields?|makes|servings?|serves)\s*[:\-]?\s*", "", text).strip()
    bare = re.fullmatch(r"(?:about )?(\d+)\s*\+?", text)
    if bare:
        return YieldParse(float(int(bare.group(1))))
    span = re.fullmatch(r"(?:about )?(\d+)\s*(?:-|to)\s*\d+", text)
    if span:
        return YieldParse(float(int(span.group(1))))
    number = _first_number(text)
    if number is None or number <= 0:
        return YieldParse(None, "no number")
    # Number followed by a parenthetical ("4 (makes 3 to 4 cups)"): leading count wins.
    lead_paren = re.fullmatch(r"(\d+)\s*\(.*\)", text)
    if lead_paren:
        return YieldParse(float(int(lead_paren.group(1))), "leading count assumed to be servings")
    if has_serving:
        # "Serves – 6 tablespoons" is a volume, not a head count.
        if has_volume and not re.search(
            r"\d+\s*(?:-|to)?\s*\d*\s*(?:" + _SERVING_WORDS + ")", text
        ):
            if not re.search(r"(?:servings?|serves)\s*[:\-]?\s*(?:about )?\d+\s*$", text):
                return YieldParse(None, "volume yield, not servings")
        return YieldParse(float(number))
    if has_item:
        return YieldParse(float(number))
    if has_volume:
        return YieldParse(None, "volume/whole-dish yield, not servings")
    # Plain range "4-6" / "4 to 6".
    if re.fullmatch(r"(?:about )?\d+\s*(?:-|to)\s*\d+", text):
        return YieldParse(float(number))
    return YieldParse(None, "unrecognized yield")


# --------------------------------------------------------------------------------------------
# Owner title prefixes / attributions
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefixRule:
    key: str
    pattern: re.Pattern[str]
    credit: str | None
    tags: tuple[tuple[str, str], ...]
    basis: str
    guess: bool = False


KENJI = "J. Kenji López-Alt"
PREFIX_RULES: tuple[PrefixRule, ...] = (
    PrefixRule(
        "Kenji",
        re.compile(r"^kenji(?:'s)?\s+|\s+kenji$|\((?:kenji|kenji style)\)", re.IGNORECASE),
        KENJI,
        (),
        "source_site seriouseats.com / cooking.nytimes.com (Kenji's column); one row has "
        "source_author 'J. Kenji López-Alt'",
    ),
    PrefixRule(
        "IP",
        re.compile(r"^ip\s+", re.IGNORECASE),
        None,
        (("equipment", "instant pot"),),
        "IP = Instant Pot (every IP recipe is a pressure-cooker recipe, e.g. twosleevers.com)",
    ),
    PrefixRule(
        "TDDC",
        re.compile(r"^td[dc]c\s+", re.IGNORECASE),
        "The Defined Dish (Alex Snodgrass)",
        (),
        "HTML-export recipes; 'TDDC' read as The Defined Dish Cookbook ('TDCC' taken as a typo)",
        guess=True,
    ),
    PrefixRule(
        "Gordon",
        re.compile(r"^gordon(?:'s)?(?:\s+ramsay(?:'s)?)?\s+", re.IGNORECASE),
        "Gordon Ramsay",
        (),
        "titles 'Gordon Ramsay …' are explicit; bare 'Gordon …' taken to mean the same",
    ),
    PrefixRule(
        "Minimalist Baker",
        re.compile(r"^minimalist baker\s*(?:-\s*)?", re.IGNORECASE),
        "Minimalist Baker",
        (),
        "source_site minimalistbaker.com",
    ),
    PrefixRule(
        "by Chef",
        re.compile(r"\s+by chef\s+(?P<name>[a-z][a-z .'-]+)$", re.IGNORECASE),
        None,  # filled from the captured name
        (),
        "title says 'by Chef <name>'",
    ),
)
# Household provenance parentheticals: "(aaron rec)", "(from bob)", "(... weekend recipe)".
ATTRIBUTION_PAREN = re.compile(
    r"\((?P<inner>(?:from\s+(?!scratch\b|the\b|a\b)[a-z]+)|(?:[a-z]+\s+rec)|"
    r"(?:[a-z ]+\s+weekend recipe))\)",
    re.IGNORECASE,
)


@dataclass
class TitleInfo:
    prefixes: list[str] = field(default_factory=list)
    credit: str | None = None
    credit_guess: bool = False
    tags: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, value, evidence)


def analyze_title(title: str) -> TitleInfo:
    """Owner prefixes / attributions in a title. The title itself is never changed."""

    info = TitleInfo()
    folded = fold(title).replace('"', "").strip()
    for rule in PREFIX_RULES:
        match = rule.pattern.search(folded)
        if not match:
            continue
        info.prefixes.append(rule.key)
        credit = rule.credit
        if rule.key == "by Chef":
            # Take the display-cased name from the original title.
            original = re.search(r"by chef\s+(.+)$", title, re.IGNORECASE)
            credit = original.group(1).strip().strip('"”') if original else None
        if credit and info.credit is None:
            info.credit = credit
            info.credit_guess = rule.guess
        for kind, value in rule.tags:
            info.tags.append((kind, value, f"title prefix '{rule.key}'"))
    for match in ATTRIBUTION_PAREN.finditer(folded):
        inner = match.group("inner").strip()
        if inner.endswith(" rec"):
            value = f"from {inner[:-4].strip()}"
        elif inner.endswith("weekend recipe"):
            value = inner[: -len(" recipe")].strip()
        else:
            value = inner
        info.prefixes.append(f"({inner})")
        info.tags.append(("personal", value, f"title attribution '({inner})'"))
    if re.search(r"\binstant pot\b", folded) and not any(t[1] == "instant pot" for t in info.tags):
        info.tags.append(("equipment", "instant pot", "title mentions 'Instant Pot'"))
    if re.search(r"\bslow cooker\b", folded):
        info.tags.append(("equipment", "slow cooker", "title mentions 'Slow Cooker'"))
    if re.search(r"\bsous vide\b", folded):
        info.tags.append(("equipment", "sous vide", "title mentions 'Sous Vide'"))
    return info


# --------------------------------------------------------------------------------------------
# Title / ingredient normalization for matching
# --------------------------------------------------------------------------------------------

_TITLE_STRIP_PREFIX = re.compile(
    r"^(?:kenji(?:'s)?|ip|td[dc]c|minimalist baker\s*-?|gordon(?:'s)?(?:\s+ramsay(?:'s)?)?)\s+"
)
_TITLE_NOISE = {"recipe", "video", "the", "a", "an", "and", "with", "w", "of"}


def normalize_title(title: str) -> str:
    text = fold(title).replace('"', " ")
    text = _TITLE_STRIP_PREFIX.sub("", text)
    text = re.sub(r"\s+kenji$", "", text)
    text = re.sub(r"\s+by chef\s+.+$", "", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"\bny\b", "new york", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [w for w in text.split() if w not in _TITLE_NOISE]
    return " ".join(words)


def title_similarity(a: str, b: str) -> float:
    """Similarity of two *normalized* titles in [0, 1] (max of char ratio and token Jaccard)."""

    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return max(ratio, jaccard(set(a.split()), set(b.split())))


_UNITS = (
    "cups?|c|tablespoons?|tbsps?|tbs|tbl|tsps?|teaspoons?|ounces?|oz|pounds?|lbs?|lb|grams?|g|"
    "kilograms?|kg|milliliters?|ml|liters?|l|quarts?|qt|pints?|pinch(?:es)?|dash(?:es)?|"
    "cloves?|cans?|packages?|pkg|sticks?|slices?|bunch(?:es)?|sprigs?|handfuls?|heads?|"
    "stalks?|pieces?|inch(?:es)?|jars?|bottles?|containers?|envelopes?|boxes?|bags?|drops?|"
    "sheets?|fillets?|leaves"
)
_PREP_WORDS = {
    "chopped",
    "minced",
    "diced",
    "sliced",
    "fresh",
    "freshly",
    "finely",
    "thinly",
    "roughly",
    "coarsely",
    "grated",
    "shredded",
    "peeled",
    "crushed",
    "ground",
    "divided",
    "optional",
    "softened",
    "melted",
    "packed",
    "large",
    "small",
    "medium",
    "cold",
    "warm",
    "room",
    "temperature",
    "taste",
    "plus",
    "more",
    "serving",
    "garnish",
    "about",
    "of",
    "for",
    "to",
    "and",
    "or",
    "the",
    "a",
    "extra",
    "cut",
    "into",
    "halved",
    "quartered",
    "drained",
    "rinsed",
    "trimmed",
    "whole",
    "lightly",
    "beaten",
    "cubed",
    "juiced",
    "zested",
    "needed",
    "if",
    "as",
    "such",
    "good",
    "quality",
    "store",
    "bought",
    "homemade",
    "unsalted",
    "salted",
}


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_ingredient_name(text: str) -> str:
    """'(8g) kosher salt, plus more…' -> 'kosher salt'; '2 cups chopped onions' -> 'onion'."""

    value = fold(text)
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", value)
    value = value.strip(" ,;:*-")
    value = re.split(r",|;| - |\*", value, maxsplit=1)[0]
    value = re.sub(r"^\s*\+\s*", " ", value)
    value = re.sub(r"[\d½¼¾⅓⅔⅛⅜⅝⅞/.\-–x×]+", " ", value)
    value = re.sub(rf"\b(?:{_UNITS})\b\.?", " ", value)
    value = re.sub(r"[^a-z ]+", " ", value)
    words = [_singular(w) for w in value.split() if w not in _PREP_WORDS and len(w) > 1]
    return " ".join(words)


def ingredient_set(names: list[str]) -> set[str]:
    return {n for n in (normalize_ingredient_name(name) for name in names) if n}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------------------------

COURSES = (
    "main",
    "side",
    "soup",
    "salad",
    "sauce/condiment",
    "bread",
    "dessert",
    "breakfast",
    "snack",
    "drink",
)
PROTEINS = (
    "chicken",
    "pork",
    "beef",
    "lamb",
    "fish",
    "shellfish",
    "tofu/tempeh",
    "beans/legumes",
    "eggs",
    "none",
)
DIETS = ("vegetarian", "vegan", "gluten-free", "dairy-free")
CUISINES = {
    "american": "American",
    "asian": "Asian",
    "chinese": "Chinese",
    "french": "French",
    "greek": "Greek",
    "indian": "Indian",
    "italian": "Italian",
    "japanese": "Japanese",
    "korean": "Korean",
    "mediterranean": "Mediterranean",
    "mexican": "Mexican",
    "middle eastern": "Middle Eastern",
    "thai": "Thai",
    "vietnamese": "Vietnamese",
    "spanish": "Spanish",
    "portuguese": "Portuguese",
    "moroccan": "Moroccan",
    "tex-mex": "Tex-Mex",
    "cajun": "Cajun",
    "southern": "Southern",
    "british": "British",
    "irish": "Irish",
    "german": "German",
    "caribbean": "Caribbean",
    "hawaiian": "Hawaiian",
    "filipino": "Filipino",
    "egyptian": "Egyptian",
    "swedish": "Swedish",
}
_COURSE_SYNONYMS = {
    "main": "main",
    "mains": "main",
    "main course": "main",
    "main dish": "main",
    "entree": "main",
    "side": "side",
    "sides": "side",
    "side dish": "side",
    "soup": "soup",
    "soups": "soup",
    "salad": "salad",
    "salads": "salad",
    "sauce": "sauce/condiment",
    "sauces": "sauce/condiment",
    "condiment": "sauce/condiment",
    "condiments": "sauce/condiment",
    "sauce/condiment": "sauce/condiment",
    "bread": "bread",
    "breads": "bread",
    "dessert": "dessert",
    "desserts": "dessert",
    "breakfast": "breakfast",
    "snack": "snack",
    "snacks": "snack",
    "appetizer": "snack",
    "appetizers": "snack",
    "drink": "drink",
    "drinks": "drink",
    "beverage": "drink",
    "beverages": "drink",
}
_PROTEIN_SYNONYMS = {
    "chicken": "chicken",
    "pork": "pork",
    "beef": "beef",
    "lamb": "lamb",
    "fish": "fish",
    "seafood": "shellfish",
    "shellfish": "shellfish",
    "shrimp": "shellfish",
    "tofu": "tofu/tempeh",
    "tempeh": "tofu/tempeh",
    "tofu/tempeh": "tofu/tempeh",
    "beans": "beans/legumes",
    "bean": "beans/legumes",
    "legumes": "beans/legumes",
    "lentils": "beans/legumes",
    "beans/legumes": "beans/legumes",
    "egg": "eggs",
    "eggs": "eggs",
    "none": "none",
}
_DIET_SYNONYMS = {
    "vegetarian": "vegetarian",
    "vegan": "vegan",
    "gluten-free": "gluten-free",
    "gluten free": "gluten-free",
    "gf": "gluten-free",
    "dairy-free": "dairy-free",
    "dairy free": "dairy-free",
}


def map_free_tag(tag: str) -> tuple[str, str]:
    """Map a free-form household tag (A classifications) to (kind, value).

    Controlled kinds when the tag matches the taxonomy, e.g. 'side dish' -> ('course', 'side'),
    'protein: chicken' -> ('protein', 'chicken'), 'equipment: oven' -> ('equipment', 'oven');
    anything else becomes ('personal', <lowercased tag>).
    """

    raw = re.sub(r"\s+", " ", tag.strip())
    low = raw.casefold()
    prefix, _, rest = low.partition(":")
    rest = rest.strip()
    if rest:
        if prefix == "course" and rest in _COURSE_SYNONYMS:
            return "course", _COURSE_SYNONYMS[rest]
        if prefix == "protein" and rest in _PROTEIN_SYNONYMS:
            return "protein", _PROTEIN_SYNONYMS[rest]
        if prefix == "diet" and rest in _DIET_SYNONYMS:
            return "diet", _DIET_SYNONYMS[rest]
        if prefix == "cuisine" and rest in CUISINES:
            return "cuisine", CUISINES[rest]
        if prefix == "equipment":
            return "equipment", rest
        return "personal", f"{prefix}: {rest}"
    if low in _COURSE_SYNONYMS:
        return "course", _COURSE_SYNONYMS[low]
    if low in _DIET_SYNONYMS:
        return "diet", _DIET_SYNONYMS[low]
    if low in CUISINES:
        return "cuisine", CUISINES[low]
    if low in _PROTEIN_SYNONYMS and low != "none":
        return "protein", _PROTEIN_SYNONYMS[low]
    return "personal", low


_B_MEAL_TYPE = {
    "dessert": ("course", "dessert"),
    "side dish": ("course", "side"),
    "soup": ("course", "soup"),
    "salad": ("course", "salad"),
    "breakfast": ("course", "breakfast"),
    "snack": ("course", "snack"),
    "appetizer": ("course", "snack"),
}
_B_EQUIPMENT = {"instant pot": "instant pot", "slow cooker": "slow cooker"}


def map_b_tag(category: str | None, name: str) -> tuple[str, str]:
    """cookt2 tag (category, name) -> (kind, value)."""

    cat = (category or "").casefold()
    low = name.strip().casefold()
    if cat == "cuisine":
        return "cuisine", CUISINES.get(low, name.strip())
    if cat == "meal_type":
        return _B_MEAL_TYPE.get(low, ("personal", low))
    if cat == "cooking_method":
        if low in _B_EQUIPMENT:
            return "equipment", _B_EQUIPMENT[low]
        return "personal", f"method: {low}"
    if cat == "diet":
        if low in _DIET_SYNONYMS:
            return "diet", _DIET_SYNONYMS[low]
        return "personal", low
    if cat == "difficulty":
        return "personal", f"difficulty: {low}"
    if cat == "prep_style":
        return "personal", low
    return map_free_tag(name)


# --------------------------------------------------------------------------------------------
# Diet guard
# --------------------------------------------------------------------------------------------

# Phrases removed before keyword search (plant-based look-alikes).
_NOT_ANIMAL = re.compile(
    r"\b(?:vegan|vegetarian|plant[- ]based|meatless|dairy[- ]free|non[- ]?dairy|egg[- ]free|"
    r"imitation|no[- ]chicken|faux)(?:\s+(?!and\b|or\b|with\b|plus\b)[a-z]+){1,3}|"
    r"\b(?:vegetable|veggie|mushroom)\s+[a-z]+(?:\s+(?:broth|stock|sauce|bouillon|base))?|"
    r"\b(?:peanut|almond|cashew|nut|seed|sunflower|apple|cocoa|shea|soy|coconut|oat|rice|"
    r"hemp|pea|macadamia|hazelnut|pistachio|sesame)\s+(?:butter|milk|cream|yogurt|yoghurt|"
    r"cheese|creamer)|"
    r"\bbutter\s*(?:beans?|lettuce|nut squash)|\bbutternut\b|\bbuttercup squash\b|"
    r"\bcream of tartar\b|\bflax\s*eggs?\b|\bchia\s*eggs?\b|\beggplants?\b|"
    r"\boyster mushrooms?\b|\bchicken[- ]of[- ]the[- ]woods\b|\bnutritional yeast\b|"
    r"\bcoconut\s+(?:cream|milk)\b|\bhoneydew\b",
    re.IGNORECASE,
)
_MEAT_FISH = re.compile(
    r"\b(?:chicken|beef|pork|lamb|veal|mutton|goat meat|turkey|duck|goose|venison|bison|rabbit|"
    r"bacon|pancetta|prosciutto|guanciale|ham|hams|sausages?|chorizo|pepperoni|salami|"
    r"mortadella|capicola|bresaola|speck|kielbasa|bratwursts?|hot ?dogs?|frankfurters?|spam|"
    r"meat|meats|meatballs?|steaks?|brisket|short ribs?|spare ?ribs|baby back ribs|ribeye|"
    r"sirloin|tenderloin|"
    r"carnitas|barbacoa|lard|suet|tallow|gelatin|gelatine|bone broth|drippings|"
    r"fish|anchov(?:y|ies)|sardines?|tuna|salmon|cod|halibut|tilapia|trout|mackerel|bass|"
    r"snapper|haddock|pollock|swordfish|mahi|bonito|katsuobushi|dashi|bacalhau|bacalao|"
    r"shrimp|prawns?|crab|lobster|crawfish|crayfish|mussels?|clams?|scallops?|oysters?|"
    r"squid|calamari|octopus|shellfish|seafood|caviar|roe|fish sauce|oyster sauce|"
    r"worcestershire)\b",
    re.IGNORECASE,
)
_DAIRY_EGG_HONEY = re.compile(
    r"\b(?:milk|buttermilk|butter|cream|creme fraiche|sour cream|half[- ]and[- ]half|cheese|"
    r"cheddar|parmesan|parmigiano|pecorino|mozzarella|feta|ricotta|mascarpone|gruyere|"
    r"provolone|goat cheese|cream cheese|paneer|queso|cotija|burrata|halloumi|yogurt|yoghurt|"
    r"ghee|whey|casein|curd|kefir|egg|eggs|yolks?|egg whites?|mayonnaise|mayo|aioli|honey|"
    r"custard|ice cream|milk chocolate|condensed milk|evaporated milk)\b",
    re.IGNORECASE,
)


_NON_VEG_ALTERNATIVE = re.compile(
    r"\([^)]*\b(?:if not vegan|if not vegetarian|non[- ]vegan|non[- ]vegetarian|"
    r"if you'?re not vegan)\b[^)]*\)",
    re.IGNORECASE,
)


def _clean_for_diet(text: str) -> str:
    """Fold, drop quotes, drop '(... if not vegan)' alternatives and plant-based look-alikes."""

    value = fold(text).replace('"', "")
    value = _NON_VEG_ALTERNATIVE.sub(" ", value)
    return _NOT_ANIMAL.sub(" ", value)


def animal_evidence(ingredient_texts: list[str]) -> tuple[str | None, str | None]:
    """(first meat/fish/shellfish line, first dairy/egg/honey line) or None for each."""

    meat = dairy = None
    for line in ingredient_texts:
        cleaned = _clean_for_diet(line)
        if meat is None and _MEAT_FISH.search(cleaned):
            meat = line
        if dairy is None and _DAIRY_EGG_HONEY.search(cleaned):
            dairy = line
        if meat and dairy:
            break
    return meat, dairy


def diet_guard(
    tags: list[tuple[str, str]], ingredient_texts: list[str]
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """Drop 'vegetarian'/'vegan' when meat/fish/shellfish is present, 'vegan' with dairy/egg/honey.

    Returns (kept tags, suppression log entries).
    """

    meat, dairy = animal_evidence(ingredient_texts)
    kept: list[tuple[str, str]] = []
    suppressed: list[dict[str, str]] = []
    for kind, value in tags:
        if kind == "diet" and value in {"vegetarian", "vegan"} and meat:
            suppressed.append({"tag": value, "reason": "meat/fish/shellfish", "ingredient": meat})
            continue
        if kind == "diet" and value == "vegan" and dairy:
            suppressed.append({"tag": value, "reason": "dairy/egg/honey", "ingredient": dairy})
            continue
        kept.append((kind, value))
    return kept, suppressed


# --------------------------------------------------------------------------------------------
# B (cookt2 relational rows) -> RecipeDocumentV2
# --------------------------------------------------------------------------------------------

MISSING_DIRECTIONS = "Original directions were not retained for this recipe."


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def b_ingredient_source_text(
    quantity: str | None, unit: str | None, name: str | None, notes: str | None
) -> str:
    """Reconstruct the display line: '3 tablespoons (45g) unsalted butter, divided, softened'."""

    base = " ".join(part for part in (_clean(quantity), _clean(unit), _clean(name)) if part)
    note = _clean(notes)
    if note:
        base = f"{base}, {note}" if base else note
    return base


def notes_to_paragraphs(notes: str | None) -> list[str]:
    if not notes:
        return []
    return [line.strip() for line in re.split(r"\n+", notes) if line.strip()]


def b_to_document(
    title: str,
    ingredients: list[dict[str, Any]],
    instructions: list[dict[str, Any]],
    notes: str | None,
) -> tuple[RecipeDocumentV2, list[str]]:
    """Convert cookt2 rows to a validated RecipeDocumentV2.

    `ingredients`: dicts with name, quantity, unit, group_name, notes, order_index.
    `instructions`: dicts with step_number, text. Returns (document, flags).
    Raises ValueError when there are no ingredients (nothing to cook from).
    """

    flags: list[str] = []
    ordered = sorted(ingredients, key=lambda row: row.get("order_index") or 0)
    sections: list[tuple[str | None, list[Ingredient]]] = []
    index_by_group: dict[str | None, int] = {}
    for number, row in enumerate(ordered, 1):
        quantity = _clean(row.get("quantity"))
        unit = _clean(row.get("unit"))
        name = _clean(row.get("name"))
        note = _clean(row.get("notes"))
        source_text = b_ingredient_source_text(quantity, unit, name, note)
        if not source_text:
            flags.append(f"empty ingredient row #{number} skipped")
            continue
        if len(source_text) > 500:
            flags.append(f"ingredient #{number} longer than 500 chars truncated")
            source_text = source_text[:500]
        if quantity and len(quantity) > 40:
            flags.append(f"ingredient #{number} quantity too long, kept only in source_text")
            quantity = None
        if unit and len(unit) > 40:
            unit = None
        if name and len(name) > 240:
            flags.append(f"ingredient #{number} name too long, kept only in source_text")
            name = None
        if note and len(note) > 240:
            note = None
        group = _clean(row.get("group_name"))
        if group not in index_by_group:
            index_by_group[group] = len(sections)
            sections.append((group, []))
        sections[index_by_group[group]][1].append(
            Ingredient(
                id=f"i_{number}",
                source_text=source_text,
                quantity=quantity,
                unit=unit,
                name=name,
                note=note,
            )
        )
    if not sections:
        raise ValueError("no ingredients")
    ingredient_sections = [
        IngredientSection(
            id=f"ingredients_{i}", heading=(group[:160] if group else None), ingredients=items
        )
        for i, (group, items) in enumerate(sections, 1)
    ]
    steps: list[InstructionStep] = []
    for row in sorted(instructions, key=lambda r: r.get("step_number") or 0):
        text = _clean(row.get("text"))
        if not text:
            continue
        if len(text) > 4000:
            flags.append("instruction longer than 4000 chars truncated")
            text = text[:4000]
        steps.append(InstructionStep(id=f"step_{len(steps) + 1}", text=text))
    legacy = False
    if not steps:
        steps = [InstructionStep(id="step_missing_1", text=MISSING_DIRECTIONS)]
        legacy = True
        flags.append("no instructions in source: placeholder step, legacy_source=true")
    paragraphs = notes_to_paragraphs(notes)
    if len(paragraphs) > 100:
        paragraphs = paragraphs[:99] + ["\n".join(paragraphs[99:])]
    document = RecipeDocumentV2(
        title=title.strip()[:240],
        yield_text=None,
        notes=paragraphs,
        personal_notes=None,
        legacy_source=legacy,
        ingredient_sections=ingredient_sections,
        instruction_sections=[InstructionSection(id="instructions_1", heading=None, steps=steps)],
    )
    return document, flags


def document_ingredient_texts(document: dict[str, Any]) -> list[str]:
    """All ingredient display lines (source_text + name) of a v2 document dict."""

    lines: list[str] = []
    for section in document.get("ingredient_sections", []):
        for item in section.get("ingredients", []):
            lines.append(item.get("source_text") or item.get("name") or "")
    return [line for line in lines if line]


def document_ingredient_names(document: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for section in document.get("ingredient_sections", []):
        for item in section.get("ingredients", []):
            names.append(item.get("name") or item.get("source_text") or "")
    return [n for n in names if n]


# --------------------------------------------------------------------------------------------
# Slugs
# --------------------------------------------------------------------------------------------


def slugify(title: str, max_len: int = 80) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", fold(title)).strip("-")
    text = text[:max_len].rstrip("-")
    return text or "recipe"
