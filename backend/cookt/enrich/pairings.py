"""Deterministic pairings over precomputed features (spec §4.6).

- Goes with (build a meal): from a main (or soup/salad), dishes of a complementary course that
  share the anchor's cuisine or a neighbouring one, or plain dishes with no cuisine signature
  (a green salad, rice, vanilla ice cream) that can't clash. Clashing cuisines are excluded,
  not just ranked down. Contrast rules and embeddings order what's left. Every suggestion
  carries a one-line reason.
- Similar (more like this): same course, same or very adjacent cuisine, nearest by embedding
  with a same-dish-family boost.

Cuisine comes from the recipe's cuisine tags, widened by signature ingredients (soy sauce and
gochujang read East Asian; garam masala South Asian) when the tags are missing or only say
"American"/"European". No model is called here. Feature profiles (richness, acidity, texture,
weight, dish family, flavours) are produced by `cookt.enrich.features` at enrichment time.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import db, search

COMPLEMENTS: dict[str, dict[str, float]] = {
    "main": {
        "side": 1.0,
        "salad": 0.9,
        "bread": 0.7,
        "sauce/condiment": 0.5,
        "dessert": 0.5,
        "drink": 0.4,
        "soup": 0.3,
    },
    "soup": {"bread": 1.0, "salad": 0.9, "side": 0.5, "dessert": 0.4, "drink": 0.3},
    "salad": {"bread": 0.8, "soup": 0.8, "main": 0.6, "drink": 0.3},
    "breakfast": {"drink": 0.6, "side": 0.5, "bread": 0.6},
    "side": {"main": 0.8, "sauce/condiment": 0.5},
    "sauce/condiment": {"main": 0.9, "side": 0.6},
    "bread": {"soup": 0.9, "salad": 0.7, "main": 0.6},
    "dessert": {"drink": 0.6},
    "snack": {"sauce/condiment": 0.6, "drink": 0.5},
    "drink": {"snack": 0.6, "dessert": 0.5},
}
MEAL_COURSES = {"side", "salad", "bread", "sauce/condiment", "soup", "main", "dessert", "drink"}
COURSE_CAP = {"side": 2, "salad": 2}  # every other course: one slot
# Courses that can be "plain enough to go with anything"; a main or soup always has a cuisine.
NEUTRAL_COURSES = {"side", "salad", "bread", "dessert", "drink"}

# Cuisine tags -> region. A tag may sit in two regions (Tex-Mex, Hawaiian).
REGIONS: dict[str, set[str]] = {
    "east_asian": {"Chinese", "Japanese", "Korean", "Taiwanese", "Hawaiian"},
    "southeast_asian": {"Thai", "Vietnamese", "Filipino", "Indonesian", "Malaysian"},
    "south_asian": {"Indian", "Pakistani", "Nepali", "Sri Lankan"},
    "middle_eastern": {
        "Middle Eastern",
        "Moroccan",
        "Egyptian",
        "Turkish",
        "Persian",
        "Lebanese",
        "North African",
    },
    "greek": {"Greek", "Mediterranean"},
    "italian": {"Italian"},
    "iberian": {"Spanish", "Portuguese"},
    "french": {"French"},
    "northern_european": {
        "British",
        "Irish",
        "European",
        "Swedish",
        "Russian",
        "German",
        "Scandinavian",
    },
    "american": {"American", "Southern", "Cajun", "BBQ", "Hawaiian", "Tex-Mex"},
    "latin": {"Mexican", "Tex-Mex", "Latin American", "Peruvian", "Southwestern", "Cuban"},
}
# Umbrella tags spread over several regions.
GENERIC_TAGS: dict[str, dict[str, float]] = {
    "Asian": {"east_asian": 0.85, "southeast_asian": 0.85},
    "Mediterranean": {"greek": 1.0, "italian": 0.75, "iberian": 0.75, "middle_eastern": 0.75},
    "European": {"northern_european": 1.0, "french": 0.7, "italian": 0.6},
}
# Tags too broad to name a cuisine on their own (ingredient markers may refine them).
CATCH_ALL = {"American", "European"}
SAME_REGION = 0.85
# Neighbouring regions: dishes from these can share a table without clashing.
ADJACENT: dict[frozenset[str], float] = {
    frozenset(pair): weight
    for pair, weight in (
        (("east_asian", "southeast_asian"), 0.6),
        (("southeast_asian", "south_asian"), 0.4),
        (("south_asian", "middle_eastern"), 0.45),
        (("middle_eastern", "greek"), 0.7),
        (("greek", "italian"), 0.6),
        (("greek", "iberian"), 0.6),
        (("italian", "iberian"), 0.5),
        (("italian", "french"), 0.55),
        (("iberian", "french"), 0.5),
        (("french", "northern_european"), 0.6),
        (("northern_european", "american"), 0.65),
        (("american", "italian"), 0.45),
        (("american", "latin"), 0.4),
        (("iberian", "latin"), 0.45),
    )
}
PAIR_MIN = 0.55  # goes-with: a cuisine this close or closer (else it must be neutral)
SIMILAR_MIN = 0.6  # similar: same or very adjacent cuisine
NEUTRAL_COMPAT = 0.5

# Signature ingredients: a dish containing these has a cuisine, whatever its tags say.
MARKERS: dict[str, tuple[str, ...]] = {
    "east_asian": (
        "soy sauce",
        "tamari",
        "miso",
        "gochujang",
        "gochugaru",
        "kimchi",
        "mirin",
        "sake",
        "rice vinegar",
        "sesame oil",
        "hoisin",
        "oyster sauce",
        "doubanjiang",
        "chili bean",
        "sichuan",
        "five-spice",
        "five spice",
        "nori",
        "dashi",
        "bok choy",
        "shaoxing",
        "xiaoxing",
        "furikake",
        "ponzu",
        "wasabi",
        "black bean sauce",
    ),
    "southeast_asian": (
        "fish sauce",
        "lemongrass",
        "galangal",
        "curry paste",
        "kaffir",
        "makrut",
        "sambal",
        "thai basil",
    ),
    "south_asian": (
        "garam masala",
        "curry powder",
        "ghee",
        "paneer",
        "masala",
        "tandoori",
        "chaat",
        "fenugreek",
        "curry leaves",
        "mustard seeds",
        "asafoetida",
    ),
    "middle_eastern": (
        "tahini",
        "sumac",
        "za'atar",
        "zaatar",
        "harissa",
        "pomegranate molasses",
        "baharat",
        "ras el hanout",
        "preserved lemon",
        "hummus",
    ),
    "greek": ("feta", "kalamata", "tzatziki", "halloumi"),
    "italian": (
        "parmesan",
        "parmigiano",
        "pecorino",
        "mozzarella",
        "ricotta",
        "pesto",
        "prosciutto",
        "pancetta",
        "marinara",
        "mascarpone",
    ),
    "latin": (
        "tortilla",
        "chipotle",
        "adobo",
        "cotija",
        "queso fresco",
        "salsa",
        "masa",
        "poblano",
        "ancho",
        "guajillo",
        "achiote",
        "epazote",
        "tomatillo",
        "taco seasoning",
        "enchilada",
    ),
}
_MARKER_RE = {
    region: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")
    for region, words in MARKERS.items()
}
# One signature ingredient can be a borrowed accent (Kenji's stroganoff has soy and fish sauce);
# two distinct ones name the cuisine.
MARKER_MIN = 2
# Components you cook with, not dishes you serve alongside.
COMPONENT_RE = re.compile(
    r"\b(?:paste|seasoning|spice (?:blend|mix)|rub|stock|broth|dough|pastry|crust)\b", re.I
)
# Sweet bakes the flavour profile sometimes misses.
SWEET_RE = re.compile(
    r"\b(?:streusel|banana bread|zucchini bread|pumpkin bread|muffins?|cinnamon rolls?|scones?|"
    r"coffee cake|glaze[d]?|frosting|doughnuts?|donuts?|sweet rolls?)\b",
    re.I,
)

RICH_WORDS = (
    "cream",
    "butter",
    "cheese",
    "bacon",
    "pork belly",
    "short rib",
    "braise",
    "coconut milk",
)
BRIGHT_WORDS = ("lemon", "lime", "vinegar", "pickle", "yogurt", "herb", "citrus", "salsa")


@dataclass
class Profile:
    id: str
    slug: str
    title: str
    courses: list[str]
    cuisines: list[str]
    proteins: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    favorite: bool = False
    make_again: int = 0
    regions: dict[str, float] = field(default_factory=dict)  # region -> weight
    markers: dict[str, int] = field(default_factory=dict)  # region -> signature ingredients seen


def _level(features: dict[str, Any], key: str) -> int:
    value = features.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    return {"low": 0, "light": 0, "medium": 1, "moderate": 1, "high": 2, "heavy": 2}.get(
        str(value).lower(), 1
    )


def _heuristic_features(document: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        i.get("source_text", "").lower()
        for s in document.get("ingredient_sections", [])
        for i in s.get("ingredients", [])
    )
    return {
        "richness": 2 if sum(w in text for w in RICH_WORDS) >= 2 else 1,
        "acidity": 2 if sum(w in text for w in BRIGHT_WORDS) >= 2 else 1,
        "texture": [],
        "weight": "medium",
        "dish_family": None,
    }


def _ingredient_text(document: dict[str, Any]) -> str:
    return " ".join(
        i.get("source_text", "").lower()
        for s in document.get("ingredient_sections", [])
        for i in s.get("ingredients", [])
    )


def _regions(cuisines: list[str], markers: dict[str, int]) -> dict[str, float]:
    """Region weights for a recipe: its cuisine tags, refined by signature ingredients."""
    out: dict[str, float] = {}
    for tag in cuisines:
        spread = GENERIC_TAGS.get(tag) or {r: 1.0 for r, tags in REGIONS.items() if tag in tags}
        for region, weight in spread.items():
            out[region] = max(out.get(region, 0.0), weight)
    # Tags missing or catch-all ("American" on a kimchi slaw): let the ingredients speak.
    if not set(cuisines) - CATCH_ALL:
        for region, count in markers.items():
            if count >= MARKER_MIN:
                out[region] = max(out.get(region, 0.0), 1.0)
    return out


def _profiles(conn: sqlite3.Connection) -> dict[str, Profile]:
    tags: dict[str, dict[str, list[str]]] = {}
    for row in conn.execute("SELECT recipe_id, kind, value FROM recipe_tags"):
        tags.setdefault(row["recipe_id"], {}).setdefault(row["kind"], []).append(row["value"])
    features = {
        row["recipe_id"]: db.loads(row["features"])
        for row in conn.execute("SELECT recipe_id, features FROM recipe_features")
    }
    favorites = {r["recipe_id"] for r in conn.execute("SELECT DISTINCT recipe_id FROM favorites")}
    make_again: dict[str, int] = {}
    for row in conn.execute(
        "SELECT recipe_id, SUM(CASE make_again WHEN 1 THEN 1 WHEN 0 THEN -1 ELSE 0 END) AS s "
        "FROM cook_log GROUP BY recipe_id"
    ):
        make_again[row["recipe_id"]] = row["s"] or 0
    out = {}
    for row in conn.execute(
        "SELECT id, slug, title, document FROM recipes WHERE archived_at IS NULL"
    ):
        t = tags.get(row["id"], {})
        document = db.loads(row["document"])
        text = f"{row['title'].lower()} {_ingredient_text(document)}"
        markers = {
            region: len(set(pattern.findall(text)))
            for region, pattern in _MARKER_RE.items()
            if pattern.search(text)
        }
        cuisines = t.get("cuisine", [])
        out[row["id"]] = Profile(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            courses=t.get("course", []),
            cuisines=cuisines,
            proteins=t.get("protein", []),
            features=features.get(row["id"]) or _heuristic_features(document),
            favorite=row["id"] in favorites,
            make_again=make_again.get(row["id"], 0),
            regions=_regions(cuisines, markers),
            markers=markers,
        )
    return out


def _cuisine_affinity(a: Profile, b: Profile) -> tuple[float, str | None]:
    """1.0 same cuisine, ~0.85 same region, lower for neighbours, 0 for a clash."""
    shared = sorted(set(a.cuisines) & set(b.cuisines))
    if shared:
        return 1.0, shared[0]
    best = 0.0
    for ra, wa in a.regions.items():
        for rb, wb in b.regions.items():
            link = SAME_REGION if ra == rb else ADJACENT.get(frozenset((ra, rb)), 0.0)
            best = max(best, wa * wb * link)
    return best, None


def _neutral(p: Profile) -> bool:
    """Plain enough to sit beside any cuisine: no signature ingredients, no strong heat/smoke,
    and no cuisine beyond the catch-all tags."""
    flavors = {str(f).lower() for f in p.features.get("flavors") or []}
    courses = set(p.courses) & NEUTRAL_COURSES
    # A plain side is also a light one: egg salad or loaded potatoes aren't "plain".
    light = _level(p.features, "richness") <= 1 or courses <= {"dessert", "drink"}
    return (
        bool(courses)
        and light
        and not p.markers
        and not set(p.cuisines) - CATCH_ALL
        and not flavors & {"spicy", "smoky"}
    )


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False))


def _contrast(anchor: Profile, cand: Profile) -> tuple[float, str | None]:
    af, cf = anchor.features, cand.features
    a_rich, c_rich = _level(af, "richness"), _level(cf, "richness")
    a_acid, c_acid = _level(af, "acidity"), _level(cf, "acidity")
    a_tex = {str(t).lower() for t in af.get("texture") or []}
    c_tex = {str(t).lower() for t in cf.get("texture") or []}
    a_weight, c_weight = _level(af, "weight"), _level(cf, "weight")
    family = str(af.get("dish_family") or "").replace("_", " ").strip()
    if not family:
        family = anchor.courses[0] if anchor.courses else "dish"
    # The model sometimes names a cuisine as the family ("Mexican"): read it as "Mexican dish".
    cuisines = {tag for tags in REGIONS.values() for tag in tags} | set(GENERIC_TAGS)
    if family in cuisines or family in ("main", "side"):
        anchor_kind = f"{family} dish"
    else:
        anchor_kind = family.lower()
    if a_rich >= 2 and c_acid >= 2:
        return 1.0, f"bright and acidic against a rich {anchor_kind}"
    if a_tex & {"creamy", "soft", "tender", "saucy", "stewy"} and c_tex & {
        "crisp",
        "crunchy",
        "crispy",
    }:
        return 0.8, f"crisp and crunchy next to a soft, saucy {anchor_kind}"
    if a_weight >= 2 and c_weight == 0:
        return 0.7, f"light enough to sit beside a hearty {anchor_kind}"
    if a_acid >= 2 and c_rich >= 2:
        return 0.6, "a richer counterpoint to bright flavours"
    if a_tex & {"crisp", "crunchy", "crispy"} and c_tex & {"creamy", "soft"}:
        return 0.6, f"creamy texture against a crisp {anchor_kind}"
    return 0.0, None


MEATY = {"chicken", "pork", "beef", "lamb", "fish", "shellfish"}
SEASONING_WORDS = ("seasoning", "spice blend", "masala", " rub", "spice mix")


def _cap(course: str) -> str:
    return course[0].upper() + course[1:]


def suggest(conn: sqlite3.Connection, recipe_id: str, limit: int = 6) -> dict[str, Any]:
    profiles = _profiles(conn)
    anchor = profiles.get(recipe_id)
    if anchor is None:
        return {"build_a_meal": [], "more_like_this": []}
    vectors = search.recipe_vectors(conn)
    anchor_vec = vectors.get(recipe_id)
    anchor_courses = anchor.courses or ["main"]
    anchor_has_cuisine = bool(anchor.regions)
    savory_anchor = not {"main", "soup", "salad", "side"}.isdisjoint(anchor_courses)
    anchor_textures = {str(t).lower() for t in anchor.features.get("texture") or []}
    wanted: dict[str, float] = {}
    for course in anchor_courses:
        for other, weight in COMPLEMENTS.get(course, {}).items():
            wanted[other] = max(wanted.get(other, 0.0), weight)

    meal: list[tuple[float, Profile, str, str]] = []
    for cand in profiles.values():
        if cand.id == recipe_id or not cand.courses:
            continue
        course_scores = [(wanted[c], c) for c in cand.courses if c in wanted and c in MEAL_COURSES]
        if not course_scores:
            continue
        complement, course = max(course_scores)
        affinity, shared = _cuisine_affinity(anchor, cand)
        neutral = _neutral(cand)
        if not anchor_has_cuisine:
            compat = max(affinity, NEUTRAL_COMPAT if neutral or not cand.regions else 0.3)
        elif affinity >= PAIR_MIN:
            compat = affinity
        elif neutral:
            compat = NEUTRAL_COMPAT
        else:
            continue  # a different cuisine that would fight the dish
        # Sweet non-desserts (banana bread, fruit salads) don't sit beside a savory dish.
        cand_flavors = {str(f).lower() for f in cand.features.get("flavors") or []}
        # Sweet as the point of the dish, not a savory dish with a sweet note.
        sweet = bool(SWEET_RE.search(cand.title)) or (
            "sweet" in cand_flavors and not cand_flavors & {"savory", "umami", "salty"}
        )
        if sweet and savory_anchor and course not in ("dessert", "drink"):
            continue
        if course in ("sauce/condiment", "side", "bread") and COMPONENT_RE.search(cand.title):
            continue
        # A braise or curry already brings its sauce.
        if course == "sauce/condiment" and anchor_textures & {"saucy", "brothy", "stewy"}:
            continue
        # Texture and acid contrast is a savory-plate idea; it says nothing about dessert.
        contrast, contrast_reason = (
            (0.0, None) if course in ("dessert", "drink") else _contrast(anchor, cand)
        )
        similarity = _cosine(anchor_vec, vectors.get(cand.id))
        score = compat * 3 + complement * 2 + contrast * 0.8 + similarity * 0.5
        # A side for a meat main shouldn't itself be a meat dish (e.g. a chicken salad).
        if (
            course in ("side", "salad")
            and set(anchor.proteins) & MEATY
            and set(cand.proteins) & MEATY
        ):
            score -= 2.5
        # Masala blends and the like are pantry components, not something served alongside.
        if course == "sauce/condiment" and any(w in cand.title.lower() for w in SEASONING_WORDS):
            score -= 3
        score += 0.4 if cand.favorite else 0
        score += 0.3 * max(-2, min(2, cand.make_again))
        if shared:
            table = f"same {shared} table"
        elif affinity >= PAIR_MIN:
            table = f"a neighbouring {(cand.cuisines or ['kitchen'])[0]} dish that won't clash"
        else:
            table = f"a plain {course} that won't clash"
        reason = f"{contrast_reason}; {table}" if contrast_reason else _cap(table)
        meal.append((score, cand, course, _cap(reason)))
    meal.sort(key=lambda item: (-item[0], item[1].title))
    picked: list[dict[str, Any]] = []
    per_course: dict[str, int] = {}
    for score, cand, course, reason in meal:
        if per_course.get(course, 0) >= COURSE_CAP.get(course, 1):
            continue
        per_course[course] = per_course.get(course, 0) + 1
        picked.append(
            {
                "id": cand.id,
                "slug": cand.slug,
                "title": cand.title,
                "course": course,
                "reason": reason,
                "score": round(score, 3),
            }
        )
        if len(picked) >= limit:
            break

    similar: list[tuple[float, Profile, str]] = []
    family = anchor.features.get("dish_family")
    for cand in profiles.values():
        if cand.id == recipe_id:
            continue
        # Same kind of dish: a main for a main, a side for a side.
        if anchor.courses and not set(anchor.courses) & set(cand.courses):
            continue
        # A spice blend is "similar" to another spice blend, not to a dip.
        if bool(COMPONENT_RE.search(cand.title)) != bool(COMPONENT_RE.search(anchor.title)):
            continue
        affinity, shared = _cuisine_affinity(anchor, cand)
        if anchor_has_cuisine and cand.regions and affinity < SIMILAR_MIN:
            continue
        if anchor_has_cuisine and not cand.regions and not _neutral(cand) and cand.markers:
            continue
        sim = _cosine(anchor_vec, vectors.get(cand.id))
        if not anchor_vec:
            # No embeddings yet: fall back to shared courses/cuisines.
            sim = 0.3 * len(set(anchor.courses) & set(cand.courses)) + 0.3 * affinity
        same_family = bool(family) and cand.features.get("dish_family") == family
        score = sim + 0.25 * affinity + (0.08 if same_family else 0)
        course = next((c for c in anchor.courses if c in cand.courses), None)
        if same_family:
            reason = f"Another {str(family).replace('_', ' ')}"
        elif shared and course:
            reason = f"Another {shared} {course}"
        elif shared:
            reason = f"Similar {shared} flavours"
        else:
            reason = "Close in ingredients and method"
        similar.append((score, cand, reason))
    similar.sort(key=lambda item: (-item[0], item[1].title))
    more = [
        {"id": c.id, "slug": c.slug, "title": c.title, "reason": r, "score": round(s, 3)}
        for s, c, r in similar[:limit]
        if s > 0
    ]
    return {"build_a_meal": picked, "more_like_this": more}
