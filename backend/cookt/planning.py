"""Light planning: pantry staples, "what can I make", shopping-list merging."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import db

ALWAYS_HAVE = {"water", "ice", "ice water", "boiling water", "hot water", "cold water"}
_PAREN = re.compile(r"\([^)]*\)")
_PREP = re.compile(
    r"^(?:(?:finely|roughly|coarsely|thinly|freshly|lightly|very|well|packed|loosely|firmly)\s+)*"
    r"(?:(?:chopped|diced|minced|sliced|grated|shredded|crumbled|cubed|peeled|trimmed|melted|"
    r"softened|beaten|toasted|cooked|drained|rinsed|halved|quartered)\s+)+",
    re.IGNORECASE,
)
_LEADING = re.compile(
    r"^[\d\s/½¼¾⅓⅔⅛.,-]*(?:(?:heaping|scant|large|small|medium|whole|about|approx\.?)\s+)*"
    r"(?:(?:cups?|c\.|tablespoons?|tbsps?|teaspoons?|tsps?|ounces?|oz|pounds?|lbs?|grams?|g|kg|ml|"
    r"liters?|l|cloves?|cans?|pinch(?:es)?|dash(?:es)?|sticks?|bunch(?:es)?|sprigs?|slices?|"
    r"pieces?|packages?|pkgs?|jars?|bottles?|heads?|stalks?|quarts?|pints?)\.?\s+)?(?:of\s+)?",
    re.IGNORECASE,
)


def ingredient_key(item: dict[str, Any]) -> str:
    """A short, lowercase food name for matching ('2 cups kosher salt, divided' -> 'kosher salt')."""
    raw = item.get("clean_name") or item.get("name") or item.get("source_text") or ""
    text = _PAREN.sub("", raw).lower()
    text = _LEADING.sub("", text.strip())
    text = re.split(r",| for | to taste| plus | or |;", text)[0]
    text = _PREP.sub("", text.strip())
    return re.sub(r"\s+", " ", text).strip(" .-*")


def staples(conn: sqlite3.Connection) -> list[str]:
    return [
        r["name"].lower() for r in conn.execute("SELECT name FROM pantry_staples ORDER BY name")
    ]


def is_staple(key: str, staple_names: list[str]) -> bool:
    if not key or key in ALWAYS_HAVE:
        return True
    words = set(key.split())
    for staple in staple_names:
        if key == staple or staple in key and set(staple.split()) <= words:
            return True
    return False


def what_can_i_make(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    staple_names = staples(conn)
    ranked = []
    for row in conn.execute(
        "SELECT id, slug, title, document FROM recipes WHERE archived_at IS NULL"
    ):
        document = db.loads(row["document"])
        items = [
            i for s in document.get("ingredient_sections", []) for i in s.get("ingredients", [])
        ]
        if not items:
            continue
        keys = [ingredient_key(i) for i in items]
        to_buy = [k for k in dict.fromkeys(keys) if not is_staple(k, staple_names)]
        share = len(to_buy) / max(1, len(set(keys)))
        ranked.append(
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "to_buy": to_buy,
                "to_buy_count": len(to_buy),
                "percent_to_buy": round(share * 100),
            }
        )
    ranked.sort(key=lambda r: (r["percent_to_buy"], r["to_buy_count"], r["title"]))
    return ranked[:limit]


# --- shopping list ---------------------------------------------------------------------

SECTIONS = (
    "produce",
    "meat & seafood",
    "dairy & eggs",
    "bakery",
    "pantry",
    "spices & baking",
    "canned & jarred",
    "frozen",
    "international",
    "drinks",
    "other",
)
_SECTION_WORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "meat & seafood",
        (
            "chicken",
            "beef",
            "pork",
            "bacon",
            "sausage",
            "lamb",
            "turkey",
            "shrimp",
            "salmon",
            "fish",
            "steak",
            "ground",
            "chorizo",
            "ham",
            "prosciutto",
            "cod",
        ),
    ),
    (
        "spices & baking",
        (
            "black pepper",
            "peppercorn",
            "pepper flakes",
            "cayenne",
            "chili powder",
            "kosher salt",
            "sea salt",
            "salt",
        ),
    ),
    (
        "dairy & eggs",
        (
            "milk",
            "butter",
            "cream",
            "cheese",
            "yogurt",
            "egg",
            "parmesan",
            "mozzarella",
            "feta",
            "ricotta",
            "sour cream",
            "buttermilk",
            "creme fraiche",
            "half-and-half",
        ),
    ),
    (
        "produce",
        (
            "onion",
            "garlic",
            "shallot",
            "tomato",
            "potato",
            "carrot",
            "celery",
            "lemon",
            "lime",
            "orange",
            "apple",
            "banana",
            "berries",
            "lettuce",
            "spinach",
            "kale",
            "cabbage",
            "pepper",
            "jalapeño",
            "jalapeno",
            "chile",
            "cilantro",
            "parsley",
            "basil",
            "mint",
            "thyme",
            "rosemary",
            "dill",
            "scallion",
            "green onion",
            "ginger",
            "avocado",
            "cucumber",
            "zucchini",
            "squash",
            "mushroom",
            "broccoli",
            "cauliflower",
            "corn",
            "peas",
            "beans, green",
            "green beans",
            "herbs",
            "leek",
            "fennel",
            "eggplant",
        ),
    ),
    ("bakery", ("bread", "bun", "tortilla", "pita", "naan", "baguette", "roll")),
    (
        "spices & baking",
        (
            "salt",
            "pepper",
            "cumin",
            "paprika",
            "cinnamon",
            "oregano",
            "chili powder",
            "flour",
            "sugar",
            "baking soda",
            "baking powder",
            "yeast",
            "vanilla",
            "cocoa",
            "chocolate",
            "nutmeg",
            "cayenne",
            "turmeric",
            "coriander",
            "bay leaves",
            "cornstarch",
            "extract",
            "spice",
        ),
    ),
    (
        "canned & jarred",
        (
            "canned",
            "can ",
            "tomato paste",
            "broth",
            "stock",
            "coconut milk",
            "salsa",
            "beans",
            "chickpeas",
            "tomato sauce",
            "diced tomatoes",
        ),
    ),
    (
        "pantry",
        (
            "oil",
            "vinegar",
            "rice",
            "pasta",
            "noodles",
            "soy sauce",
            "honey",
            "maple",
            "mustard",
            "ketchup",
            "mayonnaise",
            "peanut butter",
            "oats",
            "lentils",
            "nuts",
            "almonds",
            "walnuts",
            "breadcrumbs",
            "panko",
            "sesame",
            "sriracha",
            "fish sauce",
        ),
    ),
    ("frozen", ("frozen",)),
    ("drinks", ("wine", "beer", "juice", "coffee", "tea", "sparkling")),
]

_VOLUME_ML = {
    "tsp": 4.929,
    "teaspoon": 4.929,
    "teaspoons": 4.929,
    "tbsp": 14.787,
    "tablespoon": 14.787,
    "tablespoons": 14.787,
    "tbs": 14.787,
    "cup": 236.59,
    "cups": 236.59,
    "c": 236.59,
    "ml": 1.0,
    "milliliter": 1.0,
    "milliliters": 1.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "pint": 473.18,
    "pints": 473.18,
    "quart": 946.35,
    "quarts": 946.35,
    "fl oz": 29.574,
    "gallon": 3785.4,
}
_MASS_G = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.59,
    "lbs": 453.59,
    "pound": 453.59,
    "pounds": 453.59,
}
_QTY = re.compile(
    r"^\s*(\d+\s+\d+/\d+|\d+/\d+|(?:\d+\s*)?[½¼¾⅓⅔⅛⅜⅝⅞]|\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*"
    r"(\d+\s+\d+/\d+|\d+/\d+|(?:\d+\s*)?[½¼¾⅓⅔⅛⅜⅝⅞]|\d+(?:\.\d+)?))?\s*"
)
_UNIT = re.compile(r"^(fl\.?\s?oz|[a-zA-Z]+)\.?\s+")
_COUNT_UNITS = {
    "clove": "clove",
    "cloves": "clove",
    "can": "can",
    "cans": "can",
    "stick": "stick",
    "sticks": "stick",
    "bunch": "bunch",
    "bunches": "bunch",
    "sprig": "sprig",
    "sprigs": "sprig",
    "slice": "slice",
    "slices": "slice",
    "piece": "piece",
    "pieces": "piece",
    "package": "package",
    "packages": "package",
    "jar": "jar",
    "jars": "jar",
    "head": "head",
    "heads": "head",
    "stalk": "stalk",
    "stalks": "stalk",
    "pinch": "pinch",
    "dash": "dash",
    "handful": "handful",
    "sheet": "sheet",
    "sheets": "sheet",
    "loaf": "loaf",
    "bottle": "bottle",
}
_VULGAR = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}


def _num(text: str) -> float | None:
    text = text.strip()
    try:
        if text and text[-1] in _VULGAR:
            return float(text[:-1] or 0) + _VULGAR[text[-1]]
        if " " in text:
            whole, frac = text.split()
            n, d = frac.split("/")
            return float(whole) + float(n) / float(d)
        if "/" in text:
            n, d = text.split("/")
            return float(n) / float(d)
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def parse_amount(source_text: str) -> tuple[float | None, str | None, str]:
    """'1 1/2 cups flour, sifted' -> (1.5, 'cup', 'flour, sifted')."""
    match = _QTY.match(source_text)
    if not match:
        return None, None, source_text
    qty = _num(match.group(2) or match.group(1))
    rest = source_text[match.end() :]
    unit_match = _UNIT.match(rest)
    if unit_match:
        unit = unit_match.group(1).lower().replace(".", "")
        unit = "fl oz" if unit.replace(" ", "") == "floz" else unit
        if unit in _VOLUME_ML or unit in _MASS_G:
            return qty, unit, rest[unit_match.end() :]
        if unit in _COUNT_UNITS:
            return qty, _COUNT_UNITS[unit], rest[unit_match.end() :]
    return qty, None, rest


def _family(unit: str | None) -> tuple[str, float]:
    if unit in _VOLUME_ML:
        return "volume", _VOLUME_ML[unit]
    if unit in _MASS_G:
        return "mass", _MASS_G[unit]
    return f"count:{unit or ''}", 1.0


def _display(family: str, base: float, hint_unit: str | None) -> tuple[str, str | None]:
    def nice(v: float) -> str:
        for denom in (2, 3, 4, 8):
            if abs(v * denom - round(v * denom)) < 0.02:
                whole, rem = divmod(round(v * denom), denom)
                if rem == 0:
                    return str(whole)
                frac = {
                    (1, 2): "½",
                    (1, 3): "⅓",
                    (2, 3): "⅔",
                    (1, 4): "¼",
                    (3, 4): "¾",
                    (1, 8): "⅛",
                    (3, 8): "⅜",
                    (5, 8): "⅝",
                    (7, 8): "⅞",
                }
                from math import gcd

                g = gcd(rem, denom)
                sym = frac.get((rem // g, denom // g), f"{rem // g}/{denom // g}")
                return f"{whole}{sym}" if whole else sym
        return f"{v:.2f}".rstrip("0").rstrip(".")

    if family == "volume":
        if hint_unit in ("ml", "l", "milliliter", "milliliters", "liter", "liters"):
            return (nice(base / 1000), "l") if base >= 1000 else (str(round(base)), "ml")
        if base >= 236.59 / 4 * 0.99:
            cups = base / 236.59
            return nice(cups), "cup" if cups <= 1 else "cups"
        if base >= 14.787 * 0.99:
            return nice(base / 14.787), "tbsp"
        return nice(base / 4.929), "tsp"
    if family == "mass":
        if hint_unit in ("g", "gram", "grams", "kg"):
            return (nice(base / 1000), "kg") if base >= 1000 else (str(round(base)), "g")
        if base >= 453.59 * 0.99:
            return nice(base / 453.59), "lb"
        return nice(base / 28.35), "oz"
    unit = family.split(":", 1)[1] or None
    if unit and base > 1 and not unit.endswith("s"):
        unit = unit + ("es" if unit.endswith(("ch", "sh")) else "s")
    return nice(base), unit


def section_for(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT section FROM store_section_cache WHERE name = ?", (name,)).fetchone()
    if row:
        return row["section"]
    for section, words in _SECTION_WORDS:
        if any(w in name for w in words):
            return section
    return "other"


def recipe_lines(conn: sqlite3.Connection, recipe_id: str, factor: float) -> list[dict[str, Any]]:
    row = conn.execute("SELECT title, document FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        return []
    clean = {
        r["ingredient_id"]: r["clean_name"]
        for r in conn.execute(
            "SELECT ingredient_id, clean_name FROM ingredient_names WHERE recipe_id = ?",
            (recipe_id,),
        )
    }
    lines = []
    for section in db.loads(row["document"]).get("ingredient_sections", []):
        for item in section.get("ingredients", []):
            qty, unit, _rest = parse_amount(item.get("source_text", ""))
            key = clean.get(item["id"]) or ingredient_key(item)
            if not key:
                continue
            lines.append(
                {
                    "key": key,
                    "qty": qty * factor if qty is not None else None,
                    "unit": unit,
                    "text": item.get("source_text"),
                    "recipe_id": recipe_id,
                    "title": row["title"],
                }
            )
    return lines


def add_to_shopping(conn: sqlite3.Connection, entries: list[tuple[str, float]]) -> dict[str, Any]:
    """Merge the ingredients of (recipe_id, factor) entries into the shopping list.

    Lines merge with existing unchecked items of the same food when units are compatible
    (volume with volume, mass with mass, same count unit). Pantry staples are skipped.
    """
    staple_names = staples(conn)
    added, merged, skipped = 0, 0, []
    stamp = db.now()
    with db.tx(conn):
        for recipe_id, factor in entries:
            for line in recipe_lines(conn, recipe_id, factor):
                if is_staple(line["key"], staple_names):
                    skipped.append(line["key"])
                    continue
                family, per = _family(line["unit"])
                source = {
                    "recipe_id": line["recipe_id"],
                    "title": line["title"],
                    "text": line["text"],
                }
                candidates = conn.execute(
                    "SELECT * FROM shopping_items WHERE name = ? AND checked = 0", (line["key"],)
                ).fetchall()
                target = None
                for cand in candidates:
                    meta = db.loads(cand["sources"])
                    cand_family = meta[0].get("_family") if meta else None
                    if cand_family == family:
                        target = cand
                        break
                if target is not None:
                    meta = db.loads(target["sources"])
                    base = (meta[0].get("_base") or 0) + ((line["qty"] or 0) * per)
                    unknown = meta[0].get("_unknown", False) or line["qty"] is None
                    meta[0].update(_base=base, _unknown=unknown)
                    meta.append(source)
                    qty, unit = (
                        _display(family, base, meta[0].get("_hint")) if base else (None, None)
                    )
                    conn.execute(
                        "UPDATE shopping_items SET quantity = ?, unit = ?, sources = ?, updated_at = ?"
                        " WHERE id = ?",
                        (
                            (qty + "+" if unknown and qty else qty),
                            unit,
                            db.dumps(meta),
                            stamp,
                            target["id"],
                        ),
                    )
                    merged += 1
                else:
                    base = (line["qty"] or 0) * per
                    qty, unit = _display(family, base, line["unit"]) if base else (None, None)
                    meta = [
                        {
                            **source,
                            "_family": family,
                            "_base": base,
                            "_hint": line["unit"],
                            "_unknown": line["qty"] is None,
                        }
                    ]
                    position = conn.execute(
                        "SELECT COALESCE(MAX(position), 0) + 1 FROM shopping_items"
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO shopping_items(id, name, quantity, unit, section, sources, checked,"
                        " position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                        (
                            db.new_id(),
                            line["key"],
                            qty,
                            unit,
                            section_for(conn, line["key"]),
                            db.dumps(meta),
                            position,
                            stamp,
                            stamp,
                        ),
                    )
                    added += 1
    return {"added": added, "merged": merged, "skipped_staples": sorted(set(skipped))}


def shopping_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    order = {s: i for i, s in enumerate(SECTIONS)}
    items = []
    for r in conn.execute("SELECT * FROM shopping_items ORDER BY position, name"):
        sources = [
            {k: v for k, v in s.items() if not k.startswith("_")} for s in db.loads(r["sources"])
        ]
        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "quantity": r["quantity"],
                "unit": r["unit"],
                "section": r["section"] or "other",
                "checked": bool(r["checked"]),
                "sources": [s for s in sources if s.get("recipe_id")],
                "updated_at": r["updated_at"],
            }
        )
    items.sort(key=lambda i: (i["checked"], order.get(i["section"], 99)))
    return items
