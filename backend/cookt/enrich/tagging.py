"""Auto-tagging and derived metadata (spec §4.4): auto-apply, recorded, undoable.

For each recipe the local text model returns, under a strict JSON schema:
- cuisine / course / protein / diet tags, each with a confidence and the evidence
  (the ingredient or step that supports it);
- structured prep/cook/total minutes and numeric servings (only used to fill gaps);
- a cleaned ingredient name per line (stored beside the document; display text is
  never touched).

Everything applied is written to the `changes` feed with before/after, model and
prompt version, and can be reverted in one tap. Reverted tags go to
`rejected_tags` and are never re-suggested for that recipe. A deterministic diet
guard runs after the model: no vegetarian/vegan when meat or fish is present, no
vegan with dairy/eggs/honey, no gluten-free with wheat, no dairy-free with dairy.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import db, llm
from ..config import settings

log = logging.getLogger("cookt.tagging")

PROMPT_VERSION = "tags-v5"  # v2: supersede cookt2 tags; v3: eggs rule, supersede older auto tags;
# v4: specific cuisines (no "Asian"/"European"), "American" only for American cooking;
# v5: a vague or empty cuisine gets a second, enum-constrained pick
MIN_CONFIDENCE = 0.6
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
BASE_CUISINES = (
    "American",
    "Southern",
    "Cajun",
    "Mexican",
    "Tex-Mex",
    "Latin American",
    "Caribbean",
    "Italian",
    "French",
    "Spanish",
    "Greek",
    "Mediterranean",
    "Middle Eastern",
    "Moroccan",
    "Indian",
    "Thai",
    "Vietnamese",
    "Chinese",
    "Japanese",
    "Korean",
    "Filipino",
    "British",
    "German",
    "Eastern European",
    "Persian",
    "Turkish",
    "African",
    "Hawaiian",
    "Taiwanese",
    "Indonesian",
    "Malaysian",
    "Peruvian",
    "Portuguese",
    "Irish",
    "Lebanese",
    "Cuban",
)
# Umbrella labels that say too little to pair dishes by: never assigned.
GENERIC_CUISINES = {"asian", "european", "international", "western", "global", "world"}
# Allowed, but only when nothing more specific fits: a second pick is asked for first.
VAGUE_CUISINES = GENERIC_CUISINES | {"mediterranean", "latin american"}

MEAT = re.compile(
    r"\b(chicken|beef|pork|bacon|ham|prosciutto|pancetta|sausage|chorizo|salami|pepperoni|lamb|"
    r"veal|turkey|duck|goose|venison|bison|steak|brisket|ribs?|carnitas|mince|ground (?:beef|pork|"
    r"turkey|lamb|chicken)|guanciale|lard|gelatin|anchov(?:y|ies)|fish|salmon|tuna|cod|halibut|"
    r"tilapia|trout|sardines?|mackerel|shrimp|prawns?|crab|lobster|scallops?|clams?|mussels?|"
    r"oysters?|squid|calamari|octopus|fish sauce|oyster sauce|worcestershire|bone broth|"
    r"(?:chicken|beef|pork|fish) (?:stock|broth|bouillon|base)|dashi|bonito)\b",
    re.I,
)
ANIMAL = re.compile(
    r"\b(butter|milk|cream|cheese|parmesan|parmigiano|mozzarella|cheddar|feta|ricotta|yogh?urt|"
    r"ghee|buttermilk|crème fraîche|creme fraiche|sour cream|eggs?|egg yolks?|egg whites?|honey|"
    r"mayonnaise|mayo|whey|custard)\b",
    re.I,
)
DAIRY = re.compile(
    r"\b(butter|milk|cream|cheese|parmesan|parmigiano|mozzarella|cheddar|feta|ricotta|yogh?urt|"
    r"ghee|buttermilk|crème fraîche|creme fraiche|sour cream|whey|custard)\b",
    re.I,
)
NOT_DAIRY = re.compile(
    r"\b(coconut|oat|almond|soy|cashew|rice|peanut|nut) (milk|cream|butter|yogh?urt)\b|"
    r"\bbutter(nut|milk biscuits)\b|\bcocoa butter\b|\bpeanut butter\b",
    re.I,
)
WHEAT = re.compile(
    r"\b((?<!rice )(?<!almond )(?<!coconut )(?<!corn )(?<!chickpea )(?<!oat )flour|"
    r"bread|breadcrumbs?|"
    r"panko|pasta|spaghetti|linguine|fettuccine|penne|macaroni|lasagna|orzo|couscous|noodles|"
    r"tortillas?|pita|naan|croutons?|soy sauce|beer|barley|farro|bulgur|seitan|pie crust|"
    r"puff pastry|crackers?|biscuits?|buns?)\b",
    re.I,
)
GF_OK = re.compile(r"\b(gluten[- ]free|tamari|corn tortillas?|rice noodles|rice flour)\b", re.I)


class TagEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=40)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(
        max_length=200, description="the ingredient line or step that supports it"
    )


class CleanName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=64)
    name: str = Field(max_length=80, description="plain food name, lowercase, no quantity/prep")


class TagResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cuisine: list[TagEvidence] = Field(max_length=3)
    course: list[TagEvidence] = Field(max_length=3)
    protein: list[TagEvidence] = Field(max_length=3)
    diet: list[TagEvidence] = Field(max_length=4)
    prep_minutes: int | None = Field(ge=0, le=10000)
    cook_minutes: int | None = Field(ge=0, le=10000)
    total_minutes: int | None = Field(ge=0, le=20000)
    servings: float | None = Field(ge=0, le=500)
    ingredient_names: list[CleanName] = Field(max_length=250)


def _lines(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in document.get("ingredient_sections", []) for i in s.get("ingredients", [])]


def _prompt(row: sqlite3.Row, document: dict[str, Any], cuisines: list[str]) -> str:
    ingredients = "\n".join(f"[{i['id']}] {i['source_text']}" for i in _lines(document))
    steps = "\n".join(
        f"{n}. {step['text']}"
        for n, step in enumerate(
            (st for s in document.get("instruction_sections", []) for st in s.get("steps", [])), 1
        )
    )
    notes = "\n".join(document.get("notes", []))[:1500]
    recipe = (
        f"TITLE: {row['title']}\nYIELD: {document.get('yield_text') or ''}\n"
        f"SOURCE: {row['source_site'] or ''}\nNOTES:\n{notes}\n\nINGREDIENTS:\n{ingredients}\n\n"
        f"STEPS:\n{steps[:6000]}"
    )
    return (
        "Classify this household recipe. Use ONLY these controlled values:\n"
        f"- course (1-2): {', '.join(COURSES)}. 'main' = the centre of a meal; "
        "a hearty soup served as dinner is 'soup'. Condiments, dressings, salsas, "
        "spice mixes = 'sauce/condiment'.\n"
        f"- protein (1-2): {', '.join(PROTEINS)}. The main protein(s) the dish is built around; "
        "'none' if no meaningful protein (e.g. most desserts, breads, vegetable sides). "
        "'eggs' only when eggs are the point of the dish (omelette, frittata, shakshuka, egg "
        "salad) — eggs used as a binder or in batter/dough (cakes, cookies, doughnuts, "
        "muffins, breading) are NOT a protein. Bacon or cheese used as a garnish is not either.\n"
        "- diet (0-4): vegetarian, vegan, gluten-free, dairy-free — only when EVERY ingredient "
        "complies (stock, fish sauce, anchovy, gelatin, honey, butter, flour, soy sauce count!). "
        "When unsure leave it out.\n"
        f"- cuisine (0-2): the specific cuisine the dish comes from, preferably one of: "
        f"{', '.join(cuisines)}. Be specific: Chinese, Korean, Japanese or Thai, never 'Asian'; "
        "Italian, French or British, never 'European'; 'Mediterranean' only for a dish that truly "
        "belongs to no single Mediterranean cuisine. 'American' only for American cooking "
        "(burgers, meatloaf, chocolate chip cookies, Southern food, American barbecue), not as a "
        "default for Western home food: roast chicken with herbs is French or American by "
        "method; lemon-caper pork is Italian. A second cuisine only for a real fusion dish "
        "(Korean tacos: Korean + Mexican). Leave empty only for dishes with no cuisine at all "
        "(plain rice, hard-boiled eggs). A new cuisine name is allowed if none of these fit.\n"
        "For every tag give a confidence 0-1 and the evidence: quote the ingredient or step.\n"
        "Also: prep/cook/total minutes if the recipe states or clearly implies them (else null); "
        "numeric servings from the yield (else null); and for EVERY ingredient line id a short "
        "clean food name (e.g. '(8g) kosher salt, plus more' -> 'kosher salt').\n\n"
        + llm.untrusted(recipe)
    )


def diet_guard(document: dict[str, Any], diets: set[str]) -> dict[str, str]:
    """Return {diet: reason} for diet tags that must be suppressed."""
    text = "\n".join(i.get("source_text", "") for i in _lines(document))
    lowered = NOT_DAIRY.sub(" ", text)
    out: dict[str, str] = {}
    meat = MEAT.search(text)
    if meat:
        for diet in ("vegetarian", "vegan"):
            if diet in diets:
                out[diet] = f"contains {meat.group(0)}"
    animal = ANIMAL.search(lowered)
    if "vegan" in diets and "vegan" not in out and animal:
        out["vegan"] = f"contains {animal.group(0)}"
    dairy = DAIRY.search(lowered)
    if "dairy-free" in diets and dairy:
        out["dairy-free"] = f"contains {dairy.group(0)}"
    if "gluten-free" in diets:
        wheat = WHEAT.search(GF_OK.sub(" ", text))
        if wheat:
            out["gluten-free"] = f"contains {wheat.group(0)}"
    return out


def _record(conn, recipe_id, field, before, after, evidence, model, note=None) -> None:
    conn.execute(
        "INSERT INTO changes(id, recipe_id, field, before, after, evidence, model, prompt_version,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            db.new_id(),
            recipe_id,
            field,
            db.dumps(before),
            db.dumps(after),
            evidence or note,
            model,
            PROMPT_VERSION,
            db.now(),
        ),
    )


def known_cuisines(conn: sqlite3.Connection) -> list[str]:
    seen = {
        r["value"]
        for r in conn.execute("SELECT DISTINCT value FROM recipe_tags WHERE kind='cuisine'")
    }
    return sorted((set(BASE_CUISINES) | seen) - {c for c in seen if c.lower() in GENERIC_CUISINES})


def _pick_model(cuisines: list[str]) -> type[BaseModel]:
    specific = sorted(c for c in cuisines if c.lower() not in GENERIC_CUISINES)
    Choice = Literal[tuple([*specific, "none"])]  # type: ignore[valid-type]

    class CuisinePick(BaseModel):
        model_config = ConfigDict(extra="forbid")
        cuisine: Choice  # type: ignore[valid-type]
        second: Choice  # type: ignore[valid-type]
        confidence: float = Field(ge=0, le=1)
        evidence: str = Field(max_length=200)

    return CuisinePick


def _pick_cuisine(
    row: sqlite3.Row, document: dict[str, Any], cuisines: list[str]
) -> list[TagEvidence]:
    """A second, enum-constrained call when the first answer was vague ("Asian") or empty."""
    ingredients = "\n".join(i["source_text"] for i in _lines(document))
    pick = llm.complete_json(
        "Which cuisine does this dish come from? Pick the most specific option: the cuisine a "
        "cook from that tradition would recognise it as (sesame-soy tofu is Chinese or Japanese, "
        "shawarma is Middle Eastern, shortbread is British). 'Mediterranean' and 'Latin American' "
        "only when no single cuisine fits. 'second' is for a real fusion dish, else 'none'. "
        "'none' as the cuisine only for dishes with no cuisine at all "
        "(plain rice, boiled eggs).\n\n"
        + llm.untrusted(f"TITLE: {row['title']}\nINGREDIENTS:\n{ingredients[:3000]}"),
        _pick_model(cuisines),
        "cuisine_pick",
        max_tokens=400,
    )
    out = []
    for value in (pick.cuisine, pick.second):
        if value != "none" and value not in [t.value for t in out]:
            out.append(
                TagEvidence(value=value, confidence=pick.confidence, evidence=pick.evidence[:200])
            )
    return out


def classify(conn: sqlite3.Connection, recipe_id: str) -> TagResult:
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    document = db.loads(row["document"])
    cuisines = known_cuisines(conn)
    result = llm.complete_json(
        _prompt(row, document, cuisines), TagResult, "recipe_tags", max_tokens=6000
    )
    specific = [
        c
        for c in result.cuisine
        if c.confidence >= MIN_CONFIDENCE and c.value.strip().lower() not in VAGUE_CUISINES
    ]
    if not specific:
        picked = _pick_cuisine(row, document, cuisines)
        if picked:
            result.cuisine = picked
    return result


def _normalize(kind: str, value: str, cuisines: list[str]) -> str | None:
    v = value.strip()
    if kind == "course":
        v = v.lower()
        v = {
            "sauce": "sauce/condiment",
            "condiment": "sauce/condiment",
            "dressing": "sauce/condiment",
            "appetizer": "snack",
            "beverage": "drink",
            "entree": "main",
            "main course": "main",
            "side dish": "side",
        }.get(v, v)
        return v if v in COURSES else None
    if kind == "protein":
        v = v.lower()
        v = {
            "tofu": "tofu/tempeh",
            "tempeh": "tofu/tempeh",
            "beans": "beans/legumes",
            "legumes": "beans/legumes",
            "lentils": "beans/legumes",
            "chickpeas": "beans/legumes",
            "egg": "eggs",
            "seafood": "shellfish",
            "shrimp": "shellfish",
            "salmon": "fish",
            "turkey": "chicken",
        }.get(v, v)
        return v if v in PROTEINS else None
    if kind == "diet":
        v = v.lower().replace(" ", "-")
        return v if v in DIETS else None
    if kind == "cuisine":
        if v.lower() in GENERIC_CUISINES:
            return None
        if v.lower() in {
            "none",
            "n/a",
            "na",
            "unknown",
            "generic",
            "international",
            "fusion",
            "other",
            "",
        }:
            return None
        for c in cuisines:
            if c.lower() == v.lower():
                return c
        return v.title() if 2 < len(v) <= 30 else None
    return None


def apply(
    conn: sqlite3.Connection, recipe_id: str, result: TagResult, model: str | None = None
) -> list[str]:
    """Apply a classification. Returns human-readable change summaries."""
    model = model or settings.text_model
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    document = db.loads(row["document"])
    cuisines = known_cuisines(conn)
    rejected = {
        (r["kind"], r["value"])
        for r in conn.execute(
            "SELECT kind, value FROM rejected_tags WHERE recipe_id = ?", (recipe_id,)
        )
    }
    existing = {
        (r["kind"], r["value"])
        for r in conn.execute(
            "SELECT kind, value FROM recipe_tags WHERE recipe_id = ?", (recipe_id,)
        )
    }
    summaries: list[str] = []
    proposed: dict[tuple[str, str], TagEvidence] = {}
    limits = {"cuisine": 2, "course": 2, "protein": 2, "diet": 4}
    for kind in ("cuisine", "course", "protein", "diet"):
        items = sorted(getattr(result, kind), key=lambda t: -t.confidence)
        count = 0
        for item in items:
            value = _normalize(kind, item.value, cuisines)
            if value is None or item.confidence < MIN_CONFIDENCE or count >= limits[kind]:
                continue
            proposed[(kind, value)] = item
            count += 1
    # "none" protein only if the model gave no real protein
    if any(k == "protein" and v != "none" for k, v in proposed) and ("protein", "none") in proposed:
        proposed.pop(("protein", "none"))
    # deterministic diet guard, applied to the union of existing + proposed diet tags
    diets = {v for k, v in proposed if k == "diet"} | {v for k, v in existing if k == "diet"}
    suppressed = diet_guard(document, diets)
    with db.tx(conn):
        for (kind, value), item in proposed.items():
            if (kind, value) in existing or (kind, value) in rejected:
                continue
            if kind == "diet" and value in suppressed:
                continue
            conn.execute(
                "INSERT INTO recipe_tags(recipe_id, kind, value, source, confidence, evidence, "
                "created_at) VALUES (?, ?, ?, 'auto', ?, ?, ?)",
                (recipe_id, kind, value, round(item.confidence, 2), item.evidence[:200], db.now()),
            )
            _record(conn, recipe_id, f"tag:{kind}", None, value, item.evidence, model)
            summaries.append(f"+{kind}:{value}")
        # Machine tags (earlier auto tags, and cookt2's LLM tags that came over as 'migrated'
        # with evidence "cookt2 tag ...") are guesses, not household choices: when this
        # classifier has an opinion on a facet, unconfirmed machine tags in it are removed
        # (logged, revertible). recipe-table tags (human) and manual tags are never touched.
        proposed_kinds = {k for k, _ in proposed}
        for row_tag in conn.execute(
            "SELECT kind, value FROM recipe_tags WHERE recipe_id = ? AND kind IN "
            "('course', 'cuisine', 'protein', 'diet') AND (source = 'auto' OR "
            "(source = 'migrated' AND evidence LIKE 'cookt2 tag %'))",
            (recipe_id,),
        ).fetchall():
            kind, value = row_tag["kind"], row_tag["value"]
            generic = kind == "cuisine" and value.lower() in GENERIC_CUISINES
            if (kind, value) in proposed or (
                kind != "diet" and kind not in proposed_kinds and not generic
            ):
                continue  # (a machine-made "Asian" goes even with nothing to replace it)
            conn.execute(
                "DELETE FROM recipe_tags WHERE recipe_id = ? AND kind = ? AND value = ?",
                (recipe_id, kind, value),
            )
            _record(
                conn,
                recipe_id,
                f"tag:{kind}",
                value,
                None,
                "superseded: cookt2 auto tag not confirmed by the classifier",
                model,
            )
            summaries.append(f"-{kind}:{value} (cookt2)")
        for diet, reason in suppressed.items():
            if ("diet", diet) in existing:
                conn.execute(
                    "DELETE FROM recipe_tags WHERE recipe_id = ? AND kind = 'diet' AND value = ?",
                    (recipe_id, diet),
                )
                _record(conn, recipe_id, "tag:diet", diet, None, f"diet guard: {reason}", "rule")
                summaries.append(f"-diet:{diet} ({reason})")
        # derived metadata: only fill gaps, never overwrite
        for field in ("prep_minutes", "cook_minutes", "total_minutes", "servings"):
            new = getattr(result, field)
            if new in (None, 0) or row[field] is not None:
                continue
            conn.execute(f"UPDATE recipes SET {field} = ? WHERE id = ?", (new, recipe_id))
            _record(conn, recipe_id, field, None, new, "derived from recipe text", model)
            summaries.append(f"{field}={new}")
        # cleaned ingredient names (side table; display text untouched)
        ids = {i["id"] for i in _lines(document)}
        names = {
            n.id: n.name.strip().lower()
            for n in result.ingredient_names
            if n.id in ids and n.name.strip()
        }
        if names:
            before = {
                r["ingredient_id"]: r["clean_name"]
                for r in conn.execute(
                    "SELECT ingredient_id, clean_name FROM ingredient_names WHERE recipe_id = ?",
                    (recipe_id,),
                )
            }
            if before != names:
                conn.execute("DELETE FROM ingredient_names WHERE recipe_id = ?", (recipe_id,))
                conn.executemany(
                    "INSERT INTO ingredient_names(recipe_id, ingredient_id, clean_name) "
                    "VALUES (?, ?, ?)",
                    [(recipe_id, k, v) for k, v in names.items()],
                )
                _record(
                    conn,
                    recipe_id,
                    "ingredient_names",
                    before or None,
                    names,
                    f"{len(names)} cleaned names",
                    model,
                )
                summaries.append(f"ingredient_names x{len(names)}")
    return summaries


def revert(conn: sqlite3.Connection, change_id: str) -> dict[str, Any]:
    change = conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
    if change is None:
        raise LookupError(change_id)
    if change["reverted_at"]:
        return {"ok": True, "already": True}
    recipe_id, field = change["recipe_id"], change["field"]
    before, after = db.loads(change["before"]), db.loads(change["after"])
    with db.tx(conn):
        if field.startswith("tag:"):
            kind = field.split(":", 1)[1]
            if after is not None:  # an added tag: remove it and never suggest it again
                conn.execute(
                    "DELETE FROM recipe_tags WHERE recipe_id = ? AND kind = ? AND value = ?",
                    (recipe_id, kind, after),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO rejected_tags(recipe_id, kind, value, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (recipe_id, kind, after, db.now()),
                )
            elif before is not None:  # a removed tag: put it back as manual
                conn.execute(
                    "INSERT OR IGNORE INTO recipe_tags(recipe_id, kind, value, source, created_at) "
                    "VALUES (?, ?, ?, 'manual', ?)",
                    (recipe_id, kind, before, db.now()),
                )
        elif field in ("prep_minutes", "cook_minutes", "total_minutes", "servings"):
            conn.execute(
                f"UPDATE recipes SET {field} = ? WHERE id = ? AND {field} IS ?",
                (before, recipe_id, after),
            )
        elif field == "ingredient_names":
            conn.execute("DELETE FROM ingredient_names WHERE recipe_id = ?", (recipe_id,))
            for key, value in (before or {}).items():
                conn.execute(
                    "INSERT INTO ingredient_names(recipe_id, ingredient_id, clean_name) "
                    "VALUES (?, ?, ?)",
                    (recipe_id, key, value),
                )
        conn.execute("UPDATE changes SET reverted_at = ? WHERE id = ?", (db.now(), change_id))
        conn.execute("UPDATE recipes SET updated_at = ? WHERE id = ?", (db.now(), recipe_id))
    return {"ok": True}


def tag_recipe(conn: sqlite3.Connection, recipe_id: str) -> list[str]:
    result = classify(conn, recipe_id)
    summaries = apply(conn, recipe_id, result)
    log.info("tagged %s: %s", recipe_id, ", ".join(summaries) or "no changes")
    return summaries
