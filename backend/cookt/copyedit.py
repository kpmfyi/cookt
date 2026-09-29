"""Copy-edit proposals: bring recipe text up to the house style (docs/style-guide.md).

The local model proposes line-level edits (rewrite, remove, or turn a line into a section heading)
plus a 1-5 quality score. Nothing is applied automatically: every proposal waits on the Review
page, where a person approves all or some of its edits, rejects it, or deletes the recipe.
Approval goes through ``recipes.update_recipe`` (so the previous text is a revision) and can be
undone; deletion archives the recipe and can be undone too.

    uv run python -m cookt.copyedit run [--limit N] [--recipe SLUG] [--force]
    uv run python -m cookt.copyedit status
"""

from __future__ import annotations

import argparse
import copy
import logging
import re
import sqlite3
import sys
from typing import Any, Literal

from pydantic import BaseModel

from . import db, llm, recipes
from .config import settings
from .document import RecipeDocumentV2
from .extraction.parser import parse_ingredient

log = logging.getLogger("cookt.copyedit")

PROMPT_VERSION = "copyedit-v3"
UNDECIDED = ("pending", "nochange", "error")

EMPTY_MARKERS = (
    "were not included in the original export",
    "original directions were not retained",
)
IMPORT_WARNING = re.compile(r"^import warning:", re.I)
TIME_NOTE = re.compile(r"^(prep|cook|total)(?: time)?\s*:", re.I)
TIME_FIELD = {"prep": "prep_minutes", "cook": "cook_minutes", "total": "total_minutes"}


# --- model contract -----------------------------------------------------------------------


class LineEdit(BaseModel):
    id: str
    action: Literal["rewrite", "remove", "make_heading"]
    text: str
    reason: str


class NoteEdit(BaseModel):
    index: int
    action: Literal["rewrite", "remove"]
    text: str
    reason: str


class HeadingEdit(BaseModel):
    id: str
    text: str
    reason: str


class ModelProposal(BaseModel):
    score: int
    summary: str
    title: str
    title_reason: str
    headings: list[HeadingEdit]
    ingredients: list[LineEdit]
    steps: list[LineEdit]
    notes: list[NoteEdit]


RULES = """\
You are the copy editor for a household recipe collection. The house standard is J. Kenji
López-Alt's writing: it addresses a competent home cook, gives exact amounts, one clear action per
sentence, a sensory doneness cue followed by an approximate time, and nothing that isn't needed to
cook the dish.

Title
- Name the dish plainly. Drop owner prefixes ("Kenji", "IP", "TDDC"), version suffixes ("II",
  "III"), hype words ("The Best", "Amazing", "Easy", "Perfect") and emoji. Keep words describing
  the dish or method. Title case.

Ingredients
- Line order: quantity, unit, ingredient, then a comma and the prep ("1 medium shallot, thinly
  sliced"). Spell out units (teaspoon, tablespoon, cup, ounce, pound; not tsp./tbsp./c./oz./lbs.).
- NEVER change a quantity or unit. Keep metric equivalents already present. Do not convert units.
- Replace brand and trademark names with generic ones, unless the brand changes the measurement
  (Diamond Crystal vs Morton kosher salt).
- Lowercase common nouns; capitalize proper names (Parmigiano-Reggiano, Dijon, Shaoxing).
- "Optional" goes last: ", optional".
- Keep fractions as written (½ and 1/2 are both fine). Ingredient lines start lowercase unless
  they begin with a number or a proper name.
- Remove exact or near duplicate lines (action "remove"). When a whole block of the list repeats,
  keep the first block and remove every line of the later copies.
- A line that is really a section name (e.g. "Smashed Cukes", "For the sauce:") gets action
  "make_heading" with the clean heading text, never "remove": the lines after it become that
  section.
- Keep "(see note)" only if a matching note exists.

Method
- Imperative, present tense. Name the vessel, heat level and a doneness cue, then "about N
  minutes". Temperatures as 375°F.
- Remove filler: step labels ("Get the oven ready:"), anecdotes and first person ("I usually"),
  cheerleading ("Enjoy!"), justifications nobody needs, references to photos, videos, links, FAQs
  or "the post".
- Steps must name ingredients the way the ingredient list does. If a step mentions an ingredient
  that is not in the list, or calls it something else, fix the step when the intent is clear.
- Repair broken imports: a sentence split across two steps (merge into the first, remove the
  second), "Step 1" prefixes, stray fragments.
- Fix technique only when it is plainly wrong from a cooking or chemistry standpoint, and say why
  in the reason, starting with "Technique:". Never change what the dish is. Never invent times,
  temperatures or amounts the recipe does not imply.

Notes
- Keep notes that help cook the dish: substitutions, sourcing, make-ahead, storage, equipment.
- Remove anecdotes, marketing, nutrition disclaimers, orphan fragments ("Before you start:"),
  notes repeating the ingredient list or method, and references to links/videos.
- Leave "Prep:/Cook:/Total:" time lines and "Import warning:" lines alone.

Section headings
- Heading edits only rename sections that already exist (ids H1, H2, ...). A section that is the
  only one of its kind (the only ingredient section, or the only method section) needs no
  heading. To start a new section, use "make_heading" on a line.
- Short noun phrases: "Dressing", "Chicken", "Smashed Cucumbers". Not "FOR DRESSING" or "For the
  Chicken".

Output
- Only list lines you change. An already clean line is not listed. Do not make changes that
  merely add or drop an article, swap synonyms, or reorder words without making the line clearer
  or more correct. A recipe that already meets the standard returns empty lists and an empty
  title.
- "rewrite": text is the full replacement line. "remove": text is "". "make_heading": text is the
  heading. Heading edits rename a section heading ("" removes it).
- reason: at most 8 words, no deliberation ("brand name", "duplicate line", "filler",
  "abbreviated unit", "split sentence", "Technique: garlic burns in a 5-minute high-heat sauté").
- score (of the ORIGINAL text): 5 = already meets the standard; 4 = minor polish; 3 = several
  fixes; 2 = hard to cook from as written; 1 = broken or mostly missing.
- summary: one short sentence on the main problems (or "Meets the standard.").
- Do not edit anything to match your personal taste; if in doubt, leave it.
"""

EXEMPLAR = """\
Roasted Carrots with Harissa and Crème Fraîche (J. Kenji López-Alt)
Ingredients
- 2 pounds (900g) medium carrots, ends trimmed, quartered lengthwise and cut into 3-inch segments
- Kosher salt
- 1/4 cup (60ml) crème fraîche
- 2 tablespoons (30ml) harissa paste
- 1/2 teaspoon ground cumin
- 1/4 cup (60ml) extra-virgin olive oil
- 2 tablespoons finely chopped fresh cilantro leaves
Method
1. Adjust oven rack to center position and preheat oven to 375°F. Place carrots in a large pot,
   cover with water, and season heavily with salt. Bring to a boil over high heat, then reduce to a
   simmer and cook until barely tender, about 5 minutes. Drain and let dry for 5 minutes.
2. Combine harissa, cumin and olive oil in a large bowl. Transfer half to a small bowl and set
   aside. Add carrots to the remaining mixture and toss to coat. Spread on a foil-lined rimmed
   baking sheet and roast until caramelized, about 40 minutes, turning once or twice.
3. Spread crème fraîche on a serving platter, top with carrots, drizzle with the reserved harissa
   mixture and serve immediately."""

WORKED_EXAMPLE = """\
Example input:
TITLE: The BEST Garlic Green Beans!!
NOTES:
[0] My kids beg for these every week, you're going to love them!
[1] Cook: 10 mins
INGREDIENTS
## ingredient section H1: (no heading)
[I1] 1 lb. green beans, trimmed
[I2] 2 tbsp. Kerrygold butter
[I3] 4 cloves garlic minced
[I4] 1 lb. green beans, trimmed
METHOD
## method section H2: (no heading)
[S1] Let's get cooking! Melt the butter in a skillet over high heat and add the garlic, \
cook 5 minutes.
[S2] Add the beans and cook until crisp-tender, about 6 minutes. Season with salt and enjoy!

Example output:
{"score":2,"summary":"Brand name, duplicate line, filler, and garlic that burns before \
the beans go in.",
 "title":"Garlic Green Beans","title_reason":"hype words",
 "headings":[],
 "ingredients":[
  {"id":"I1","action":"rewrite","text":"1 pound green beans, trimmed","reason":"abbreviated unit"},
  {"id":"I2","action":"rewrite","text":"2 tablespoons unsalted butter","reason":"brand name"},
  {"id":"I3","action":"rewrite","text":"4 garlic cloves, minced","reason":"prep after a comma"},
  {"id":"I4","action":"remove","text":"","reason":"duplicate line"}],
 "steps":[
  {"id":"S1","action":"rewrite","text":"Melt the butter in a large skillet over medium-high heat. \
Add the green beans and cook, tossing occasionally, until crisp-tender and lightly blistered, \
about 6 minutes.","reason":"Technique: minced garlic burns in 5 minutes over high heat; \
add it at the end"},
  {"id":"S2","action":"rewrite","text":"Add the garlic and cook, tossing, \
until fragrant, about 30 seconds. \
Season with salt and serve.","reason":"filler; garlic added last"}],
 "notes":[{"index":0,"action":"remove","text":"","reason":"anecdote"}]}
(Salt appears in S2 but not in the ingredient list; that is acceptable for "season with salt".)"""


# --- helpers ------------------------------------------------------------------------------

_FRACTIONS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅕": "1/5",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
    "⅙": "1/6",
    "⅚": "5/6",
}
_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "half": "1/2",
}


def numbers(text: str) -> list[str]:
    """Numeric tokens in a line, normalized so '1 ½' == '1 1/2' and 'six' == '6'."""
    t = text.lower()
    for char, value in _FRACTIONS.items():
        t = t.replace(char, f" {value}")
    t = re.sub(r"\b(" + "|".join(_WORDS) + r")\b", lambda m: _WORDS[m.group(1)], t)
    return sorted(re.findall(r"\d+(?:[./]\d+)?", t))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _trivial(before: str, after: str) -> bool:
    """Same line up to fraction glyphs, spacing, case of the first letter and a final period."""

    def key(text: str) -> str:
        for char, value in _FRACTIONS.items():
            text = text.replace(char, f" {value}")
        text = _norm(text).rstrip(".")
        return text[:1].lower() + text[1:]

    return key(before) == key(after)


def _reason(text: str) -> str:
    text = _norm(text)
    if len(text) > 80:  # the model sometimes deliberates in the reason; keep its first clause
        text = re.split(r"(?<=[.;?!])\s|\s\(", text)[0][:80].rstrip(".;")
    return text


def is_empty_recipe(document: dict[str, Any]) -> bool:
    lines = [
        *(i["source_text"] for s in document["ingredient_sections"] for i in s["ingredients"]),
        *(st["text"] for s in document["instruction_sections"] for st in s["steps"]),
    ]
    return any(marker in line.lower() for line in lines for marker in EMPTY_MARKERS)


def render_for_model(row: sqlite3.Row, document: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Recipe text with short line ids (I1, S1, H1). Returns the text and alias -> real id."""
    aliases: dict[str, str] = {}
    out = [f"TITLE: {document['title']}"]
    credit = row["credit"] or row["source_author"] or row["source_site"]
    if credit:
        out.append(f"CREDIT: {credit}")
    if document.get("yield_text"):
        out.append(f"YIELD: {document['yield_text']}")
    if document.get("notes"):
        out.append("NOTES:")
        out += [f"[{i}] {note}" for i, note in enumerate(document["notes"])]
    counters = {"I": 0, "S": 0, "H": 0}

    def alias(prefix: str, real: str) -> str:
        counters[prefix] += 1
        key = f"{prefix}{counters[prefix]}"
        aliases[key] = real
        return key

    out.append("INGREDIENTS")
    for section in document["ingredient_sections"]:
        out.append(
            f"## ingredient section {alias('H', section['id'])}: "
            f"{section.get('heading') or '(no heading)'}"
        )
        out += [f"[{alias('I', i['id'])}] {i['source_text']}" for i in section["ingredients"]]
    out.append("METHOD")
    for section in document["instruction_sections"]:
        out.append(
            f"## method section {alias('H', section['id'])}: "
            f"{section.get('heading') or '(no heading)'}"
        )
        out += [f"[{alias('S', s['id'])}] {s['text']}" for s in section["steps"]]
    return "\n".join(out), aliases


def build_prompt(recipe_text: str) -> str:
    return (
        f"{RULES}\nThe target voice (read-only example of a recipe that meets the standard):\n"
        f"{EXEMPLAR}\n\n{WORKED_EXAMPLE}\n\nNow copy-edit this recipe. Line ids are in brackets.\n"
        f"{llm.untrusted(recipe_text)}"
    )


def _lookup(document: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    """real id -> (kind, section id, current text) for ingredients, steps and section headings."""
    found: dict[str, tuple[str, str, str]] = {}
    for section in document["ingredient_sections"]:
        found[section["id"]] = ("heading", section["id"], section.get("heading") or "")
        for item in section["ingredients"]:
            found[item["id"]] = ("ingredient", section["id"], item["source_text"])
    for section in document["instruction_sections"]:
        found[section["id"]] = ("heading", section["id"], section.get("heading") or "")
        for step in section["steps"]:
            found[step["id"]] = ("step", section["id"], step["text"])
    return found


def normalize_proposal(
    row: sqlite3.Row, document: dict[str, Any], proposal: ModelProposal, aliases: dict[str, str]
) -> list[dict[str, Any]]:
    """Model output -> validated edit items (unknown ids, no-ops and protected lines dropped)."""
    lines = _lookup(document)
    edits: list[dict[str, Any]] = []

    def add(key: str, kind: str, op: str, before: str, after: str, reason: str) -> None:
        after = _norm(after)
        if re.search(r"already clean|no change", reason, re.I):
            return
        if op == "edit" and not after:
            op = "remove"
        if op == "edit" and _trivial(before, after):
            return
        edits.append(
            {
                "key": key,
                "kind": kind,
                "op": op,
                "before": before,
                "after": after if op != "remove" else "",
                "reason": _reason(reason),
                "numbers_changed": op == "edit" and numbers(before) != numbers(after),
            }
        )

    title = _norm(proposal.title)
    if title and title != _norm(document["title"]):
        add("title", "title", "edit", document["title"], title, proposal.title_reason)

    notes = document.get("notes") or []
    for edit in proposal.notes:
        if not 0 <= edit.index < len(notes):
            continue
        note = notes[edit.index]
        if IMPORT_WARNING.match(note) or TIME_NOTE.match(note):
            continue
        add(
            f"note:{edit.index}",
            "note",
            "remove" if edit.action == "remove" else "edit",
            note,
            edit.text,
            edit.reason,
        )
    for index, note in enumerate(notes):
        match = TIME_NOTE.match(note)
        if match and row[TIME_FIELD[match.group(1).lower()]] is not None:
            add(f"note:{index}", "note", "remove", note, "", "repeats the recipe's time fields")

    splits = {  # sections the model also splits with make_heading
        lines[aliases[e.id]][1]
        for e in [*proposal.ingredients, *proposal.steps]
        if e.action == "make_heading" and aliases.get(e.id) in lines
    }
    sole = {
        group[0]["id"]
        for group in (document["ingredient_sections"], document["instruction_sections"])
        if len(group) == 1
    }
    for edit in proposal.headings:
        real = aliases.get(edit.id)
        if real is None or lines.get(real, ("",))[0] != "heading":
            continue
        if _norm(edit.text) == lines[real][2]:
            continue
        if real in sole and real not in splits and not lines[real][2]:
            continue  # a heading on the only section of its kind adds nothing
        add(f"heading:{real}", "heading", "edit", lines[real][2], edit.text, edit.reason)
        if not _norm(edit.text) and edits and edits[-1]["key"] == f"heading:{real}":
            edits[-1]["op"] = "edit"  # an emptied heading is a heading removal, not a line removal

    for kind, items in (("ingredient", proposal.ingredients), ("step", proposal.steps)):
        for edit in items:
            real = aliases.get(edit.id)
            if real is None or lines.get(real, ("",))[0] != kind:
                continue
            before = lines[real][2]
            op = {"rewrite": "edit", "remove": "remove", "make_heading": "heading"}[edit.action]
            if op == "remove" and re.search(r"heading|section name", edit.reason, re.I):
                # A section name imported as a line: keep the grouping it carried.
                op, edit.text = (
                    "heading",
                    re.sub(r"^for (the )?|:$", "", before.strip(), flags=re.I),
                )
            if op == "heading" and not _norm(edit.text):
                continue
            prefix = "ing" if kind == "ingredient" else "step"
            add(f"{prefix}:{real}", kind, op, before, edit.text, edit.reason)
    # One edit per key; the last one wins.
    return list({edit["key"]: edit for edit in edits}.values())


# --- applying -----------------------------------------------------------------------------


def _split_sections(
    sections: list[dict[str, Any]], items_key: str, edits: dict[str, dict], prefix: str, make_item
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in sections:
        heading_edit = edits.get(f"heading:{section['id']}")
        current = {
            "id": section["id"],
            "heading": (heading_edit["after"] or None) if heading_edit else section.get("heading"),
            items_key: [],
        }
        splits = 0
        for item in section[items_key]:
            edit = edits.get(f"{prefix}:{item['id']}")
            if edit is None:
                current[items_key].append(item)
            elif edit["op"] == "remove":
                continue
            elif edit["op"] == "heading":
                if current[items_key]:
                    out.append(current)
                    splits += 1
                    current = {
                        "id": f"{section['id']}_{splits}",
                        "heading": edit["after"],
                        items_key: [],
                    }
                else:
                    current["heading"] = edit["after"]
            else:
                current[items_key].append(make_item(item, edit["after"]))
        out.append(current)
    return [section for section in out if section[items_key]]


def apply_edits(
    document: dict[str, Any], edits: list[dict[str, Any]], accepted: set[str]
) -> dict[str, Any]:
    chosen = {edit["key"]: edit for edit in edits if edit["key"] in accepted}
    doc = copy.deepcopy(document)
    if "title" in chosen:
        doc["title"] = chosen["title"]["after"]
    notes = []
    for index, note in enumerate(doc.get("notes") or []):
        edit = chosen.get(f"note:{index}")
        if edit is None:
            notes.append(note)
        elif edit["op"] != "remove":
            notes.append(edit["after"])
    doc["notes"] = notes
    doc["ingredient_sections"] = _split_sections(
        doc["ingredient_sections"],
        "ingredients",
        chosen,
        "ing",
        lambda item, text: parse_ingredient(text, item["id"]).model_dump(mode="json"),
    )
    doc["instruction_sections"] = _split_sections(
        doc["instruction_sections"],
        "steps",
        chosen,
        "step",
        lambda item, text: {**item, "text": text},
    )
    return RecipeDocumentV2.model_validate(doc).model_dump(mode="json")


def _current_text(document: dict[str, Any], key: str) -> str | None:
    if key == "title":
        return document["title"]
    kind, _, target = key.partition(":")
    if kind == "note":
        notes = document.get("notes") or []
        return notes[int(target)] if int(target) < len(notes) else None
    found = _lookup(document).get(target)
    return found[2] if found else None


# --- generating ---------------------------------------------------------------------------


def _save(conn: sqlite3.Connection, recipe_id: str, version: int, **fields: Any) -> None:
    values = {
        "status": "pending",
        "score": None,
        "summary": None,
        "edits": [],
        "flags": [],
        "error": None,
        "model": None,
        **fields,
    }
    conn.execute(
        "INSERT INTO copyedit_proposals(recipe_id, base_version, status, score, summary, edits, "
        "flags, error, model, prompt_version, created_at, decided_at, decision) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL) "
        "ON CONFLICT(recipe_id) DO UPDATE SET base_version = excluded.base_version, "
        "status = excluded.status, score = excluded.score, summary = excluded.summary, "
        "edits = excluded.edits, flags = excluded.flags, error = excluded.error, "
        "model = excluded.model, prompt_version = excluded.prompt_version, "
        "created_at = excluded.created_at, decided_at = NULL, decision = NULL "
        "WHERE copyedit_proposals.status IN ('pending', 'nochange', 'error')",
        (
            recipe_id,
            version,
            values["status"],
            values["score"],
            values["summary"],
            db.dumps(values["edits"]),
            db.dumps(values["flags"]),
            values["error"],
            values["model"],
            PROMPT_VERSION,
            db.now(),
        ),
    )


def propose(conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        raise recipes.NotFound(recipe_id)
    document = db.loads(row["document"])
    if is_empty_recipe(document):
        _save(
            conn,
            recipe_id,
            row["version"],
            status="nochange",
            score=1,
            summary="Ingredients or directions never came across in the import.",
            flags=["empty"],
        )
        return {"status": "nochange", "edits": 0}
    text, aliases = render_for_model(row, document)
    model = settings.copyedit_model
    try:
        proposal = llm.complete_json(build_prompt(text), ModelProposal, "copyedit", model=model)
        edits = normalize_proposal(row, document, proposal, aliases)
        try:
            apply_edits(document, edits, {e["key"] for e in edits})
        except ValueError:
            # e.g. every ingredient removed: keep the rewrites, drop the structural edits.
            edits = [e for e in edits if e["op"] == "edit"]
            apply_edits(document, edits, {e["key"] for e in edits})
    except (llm.ModelError, ValueError) as exc:
        _save(conn, recipe_id, row["version"], status="error", error=str(exc)[:500], model=model)
        return {"status": "error", "error": str(exc)}
    score = max(1, min(5, proposal.score))
    flags = ["exemplar"] if score == 5 and not edits else []
    status = "pending" if edits else "nochange"
    _save(
        conn,
        recipe_id,
        row["version"],
        status=status,
        score=score,
        summary=_norm(proposal.summary)[:300],
        edits=edits,
        flags=flags,
        model=model,
    )
    return {"status": status, "edits": len(edits)}


# --- decisions ----------------------------------------------------------------------------


def _proposal(conn: sqlite3.Connection, recipe_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM copyedit_proposals WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()


def _decide(
    conn: sqlite3.Connection, recipe_id: str, status: str, decision: dict[str, Any]
) -> None:
    if _proposal(conn, recipe_id) is None:
        row = conn.execute("SELECT version FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        _save(conn, recipe_id, row["version"], status="nochange")
    conn.execute(
        "UPDATE copyedit_proposals SET status = ?, decided_at = ?, decision = ? "
        "WHERE recipe_id = ?",
        (status, db.now(), db.dumps(decision), recipe_id),
    )


def approve(
    conn: sqlite3.Connection,
    recipe_id: str,
    accepted: list[str] | None,
    person_id: str | None = None,
) -> dict[str, Any]:
    prop = _proposal(conn, recipe_id)
    if prop is None or prop["status"] not in UNDECIDED:
        raise ValueError("nothing to approve")
    edits = db.loads(prop["edits"])
    keys = {e["key"] for e in edits} if accepted is None else set(accepted)
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    document = db.loads(row["document"])
    for edit in edits:
        if edit["key"] in keys and _current_text(document, edit["key"]) != edit["before"]:
            raise ValueError("the recipe changed since this proposal was made; re-run it")
    if not keys:
        return reject(conn, recipe_id)
    new_document = apply_edits(document, edits, keys)
    recipes.update_recipe(
        conn,
        recipe_id,
        {"document": new_document},
        reason="copy edit",
        person_id=person_id,
        expected_version=row["version"],
    )
    revision = conn.execute(
        "SELECT id FROM revisions WHERE recipe_id = ? AND version = ? AND reason = 'copy edit' "
        "ORDER BY created_at DESC LIMIT 1",
        (recipe_id, row["version"]),
    ).fetchone()
    _decide(conn, recipe_id, "approved", {"accepted": sorted(keys), "revision_id": revision["id"]})
    return {"ok": True, "applied": len(keys)}


def reject(conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
    _decide(conn, recipe_id, "rejected", {})
    return {"ok": True}


def keep(conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
    """Mark a recipe with no proposal (or a no-change verdict) as reviewed and kept."""
    _decide(conn, recipe_id, "kept", {})
    return {"ok": True}


def delete(conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
    """Archive the recipe: gone from the catalog, search, planning and MCP; undoable."""
    with db.tx(conn):
        conn.execute(
            "UPDATE recipes SET archived_at = ?, updated_at = ? "
            "WHERE id = ? AND archived_at IS NULL",
            (db.now(), db.now(), recipe_id),
        )
        _decide(conn, recipe_id, "deleted", {})
    return {"ok": True}


def undo(conn: sqlite3.Connection, recipe_id: str, person_id: str | None = None) -> dict[str, Any]:
    prop = _proposal(conn, recipe_id)
    if prop is None or prop["status"] in UNDECIDED:
        raise ValueError("nothing to undo")
    decision = db.loads(prop["decision"]) or {}
    if prop["status"] == "deleted":
        conn.execute(
            "UPDATE recipes SET archived_at = NULL, updated_at = ? WHERE id = ?",
            (db.now(), recipe_id),
        )
    elif prop["status"] == "approved" and decision.get("revision_id"):
        recipes.restore_revision(conn, recipe_id, decision["revision_id"], person_id)
    version = conn.execute("SELECT version FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0]
    edits = db.loads(prop["edits"])
    conn.execute(
        "UPDATE copyedit_proposals SET status = ?, base_version = ?, decided_at = NULL, "
        "decision = NULL WHERE recipe_id = ?",
        ("pending" if edits else "nochange", version, recipe_id),
    )
    return {"ok": True}


def review_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every live recipe (plus ones deleted from the Review page) with its proposal, if any."""
    proposals = {r["recipe_id"]: r for r in conn.execute("SELECT * FROM copyedit_proposals")}
    items = []
    for row in conn.execute(
        "SELECT id, slug, title, credit, source_site, source_url, document, version, archived_at "
        "FROM recipes ORDER BY title COLLATE NOCASE"
    ):
        prop = proposals.get(row["id"])
        if row["archived_at"] and (prop is None or prop["status"] != "deleted"):
            continue
        items.append(
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "credit": row["credit"] or row["source_site"],
                "source_url": row["source_url"],
                "document": db.loads(row["document"]),
                "status": prop["status"] if prop else "unreviewed",
                "score": prop["score"] if prop else None,
                "summary": prop["summary"] if prop else None,
                "edits": [e for e in db.loads(prop["edits"]) if e["before"] or e["after"]]
                if prop
                else [],
                "flags": db.loads(prop["flags"]) if prop else [],
                "error": prop["error"] if prop else None,
                "stale": bool(prop)
                and prop["status"] in UNDECIDED
                and prop["base_version"] != row["version"],
                "decided_at": prop["decided_at"] if prop else None,
            }
        )
    return items


# --- CLI ----------------------------------------------------------------------------------


def _todo(conn: sqlite3.Connection, force: bool, key: str | None) -> list[str]:
    if key:
        return [recipes.resolve_id(conn, key)]
    rows = conn.execute(
        "SELECT r.id FROM recipes r LEFT JOIN copyedit_proposals p ON p.recipe_id = r.id "
        "WHERE r.archived_at IS NULL AND (p.recipe_id IS NULL OR p.status = 'error' OR "
        "(p.status IN ('pending', 'nochange') AND "
        "(? OR p.prompt_version != ? OR p.base_version != r.version))) "
        "ORDER BY r.title COLLATE NOCASE",
        (force, PROMPT_VERSION),
    ).fetchall()
    return [r["id"] for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cookt.copyedit")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate proposals (resumable)")
    run.add_argument("--limit", type=int)
    run.add_argument("--recipe")
    run.add_argument("--force", action="store_true", help="redo undecided proposals too")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.connect()
    db.init(conn)
    if args.command == "status":
        for row in conn.execute(
            "SELECT status, COUNT(*) n FROM copyedit_proposals GROUP BY status ORDER BY status"
        ):
            print(f"{row['status']:>10}  {row['n']}")
        live = conn.execute("SELECT COUNT(*) FROM recipes WHERE archived_at IS NULL").fetchone()[0]
        print(f"{'live':>10}  {live}")
        return 0
    todo = _todo(conn, args.force, args.recipe)[: args.limit]
    for n, recipe_id in enumerate(todo, 1):
        title = conn.execute("SELECT title FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0]
        result = propose(conn, recipe_id)
        log.info("[%d/%d] %s: %s", n, len(todo), title, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
