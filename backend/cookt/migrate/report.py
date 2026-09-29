"""Migration report: data/migration/report.json + docs/migration-report.md (human-readable)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .match import JACCARD_MATCH, TITLE_MATCH, MatchResult

JUDGMENT_CALLS = (
    "The empty 'Modern Proper carnitas' is recognised as the only recipe-table row whose stored "
    "document is still schema v1 (ingredients + 3 short action labels, directions never "
    "retained). It becomes an `inbox` row (kind `rescrape`, status `queued`) carrying the URL, "
    "old id, favorite flag, the old document and its intermediate image files.",
    "cookt2's 2 `generated` recipes are household-saved recipes, not recipe-table editions: "
    "migrated with origin `migrated_b_generated` and personal tag `ai-generated`. The 7 "
    "`migrated_v1` rows with `source_recipe_id` set (companions generated from another recipe "
    "in cookt v1) keep origin `migrated_b` but also get the `ai-generated` personal tag.",
    "All 19 cookt2 images are AI-generated: every file carries Stable Diffusion `parameters` or "
    "ComfyUI `prompt` PNG metadata, and `image_status` is only ever set by cookt2's "
    "`generate_image` pipeline. All are archived, none used. recipe-table's 28 'Generated "
    "locally' primaries likewise. Affected recipes use the typographic fallback.",
    "Rule 2 (title + ingredients) is read as: normalized titles near-identical (similarity "
    ">= 0.85) AND ingredient Jaccard >= 0.7, and never across two different source sites. "
    "An extra rule merges a cookt2 row with no ingredients and no steps into the recipe-table "
    "recipe with the identical normalized title (Sock It To Me Cake); otherwise that empty row "
    "could not be migrated at all.",
    "cookt2 'Elote Pasta Salad' has no ingredients, no steps, no URL and no recipe-table "
    "counterpart: dropped (only an LLM-written description existed).",
    "cookt2 rows without instructions (none remain after matching) would get a placeholder step "
    "and `legacy_source=true`, the same convention recipe-table used for legacy recipes.",
    "cookt2 ingredient `source_text` is rebuilt as '<quantity> <unit> <name>, <notes>' (the "
    "cookt2 UI showed notes after an em dash; the comma form matches the original recipe "
    "lines). cookt2 `yield_text` is left empty; its integer `servings` goes to "
    "`recipes.servings`.",
    "Diet guard is strict: an optional or alternative meat ingredient ('vegetable broth or "
    "chicken broth', 'CHICKEN optional') and Worcestershire (anchovy) suppress 'vegetarian'. "
    "Alternatives explicitly marked '(... if not vegan)' and plant-based look-alikes "
    "('vegan parmesan', 'vegan \"chicken\" broth', 'peanut butter', 'coconut milk') do not.",
    "Yields: a leading count wins for ranges ('Serves 4-6' -> 4) and for item counts "
    "('Makes 24 cookies' -> 24, '12 donuts and 12 holes' -> 12). Volume or whole-dish yields "
    "(cups, tablespoons, loaf, pie, strombolis) and serving-size-only yields ('Servings "
    "(2-tbsp servings)') stay NULL and are flagged; cookt2's servings then fill the gap for "
    "merged recipes.",
    "Times: a note line must be entirely a duration after its label; prose ('Cook: 400 degrees "
    "for 20 minutes') is flagged, not guessed. Bare numbers ('Prep: 20') are read as minutes and "
    "flagged. cookt2's prep/cook/total columns only fill gaps. total is not derived from prep + "
    "cook.",
    "Credits: 'Kenji' prefix/suffix/'(Kenji)' -> J. Kenji López-Alt (seriouseats.com / NYT, "
    "one row has that source_author). 'Gordon …' -> Gordon Ramsay. 'Minimalist Baker - …' -> "
    "Minimalist Baker (minimalistbaker.com). '… by Chef Frank Proto' -> Frank Proto. "
    "'TDDC'/'TDCC' -> The Defined Dish (Alex Snodgrass): a guess ('TDCC' taken as a typo). "
    "Those two rows are HTML exports with no URL or author, so it is flagged. 'IP' gives no "
    "credit, only `equipment:instant pot`. Otherwise credit = recipe-table source_author, then a "
    "non-domain cookt2 source_name (e.g. 'The Pioneer Woman', 'Jimmy Watts'). 'Scanned from "
    "cookbook' becomes personal tag `cookbook scan`.",
    "Household provenance in titles ('(aaron rec)', '(from bob)', '(marion cabin weekend "
    "recipe)') becomes personal tags ('from aaron', 'from bob', 'marion cabin weekend'). "
    "Titles are unchanged.",
    "Tag vocabulary: cookt2 cooking methods other than Instant Pot/Slow Cooker become "
    "`method: <x>` and difficulty becomes `difficulty: <x>` (recipe-table already used the "
    "'method: bake' style); prep_style values and Dinner/Lunch are plain personal tags. "
    "recipe-table `desire` values (only neutral/unset) carry no signal and are not migrated.",
    "Favorites from both systems collapse to one household favorite (person_id ''). "
    "cookt2 feedback of kind 'cooked' becomes cook_log (rating/tags/text in the note, make_again "
    "only when tagged would_make_again). A cookt2 household edit of the notes field ('Family "
    "verdict: use less thyme next time.') is also copied into next_time_notes.",
    "Shopping: only unchecked items, deduped by case-folded name (first quantity/unit kept, "
    "every original line kept in `sources`). Meal plans become plan_entries with the meal type "
    "in `note`. Pantry items become staples (names only).",
    "Every recipe's new slug is also written to recipe_aliases (system 'slug'); cookt2 had no "
    "slugs (all NULL).",
    "Preserved across re-runs: all `fdc_*` tables (USDA) plus `ingredient_parse_cache` and "
    "`store_section_cache` (model caches that do not reference recipes).",
)


def build_report(
    plan, result: MatchResult, a_meta: dict, b_meta: dict, manifest: list, run_at: str
) -> dict[str, Any]:
    ledger = plan.ledger
    outcomes = {
        src: dict(Counter(r["outcome"] for r in ledger if r["source"] == src)) for src in "AB"
    }
    reasons: dict[str, Counter] = {}
    for row in ledger:
        if row["outcome"] in ("dropped", "inbox"):
            reasons.setdefault(f"{row['source']} {row['outcome']}", Counter())[
                f"{row['title']}: {row['reason']}"
            ] += 1
    origins = Counter(r["origin"] for r in plan.recipes)
    tags = Counter(kind for r in plan.recipes for (kind, _v) in r["tags"])
    tag_values = Counter(f"{k}:{v}" for r in plan.recipes for (k, v) in r["tags"])
    return {
        "run_at": run_at,
        "sources": {"A": a_meta, "B": b_meta},
        "outcomes": outcomes,
        "drop_and_inbox_details": {k: dict(v) for k, v in reasons.items()},
        "recipes": {"total": len(plan.recipes), "by_origin": dict(origins)},
        "matching": {
            "rules": {
                "canonical_url": "lowercase host, strip www., https, no fragment, tracking "
                "params stripped, no trailing slash",
                "title_ingredients": f"normalized title similarity >= {TITLE_MATCH} and "
                f"ingredient-set Jaccard >= {JACCARD_MATCH}, never across different sites",
                "title_exact_empty_b": "cookt2 row with no ingredients/steps and an identical "
                "normalized title",
            },
            "pairs_by_rule": dict(Counter(p.rule for p in result.pairs)),
            "pairs_with_notes": [
                {"a": p.a, "b": p.b, "rule": p.rule, "note": p.note} for p in result.pairs if p.note
            ],
            "non_url_pairs": [
                {
                    "a": p.a,
                    "b": p.b,
                    "rule": p.rule,
                    "title_sim": round(p.title_sim, 3),
                    "jaccard": round(p.jaccard, 3),
                }
                for p in result.pairs
                if p.rule != "canonical_url"
            ],
            "review_candidates": len(result.review),
            "within_a_duplicates": Counter(d["kind"] for d in result.dup_a),
            "within_b_duplicates": Counter(d["kind"] for d in result.dup_b),
        },
        "tags": {
            "rows_by_kind": dict(tags),
            "top_values": dict(tag_values.most_common(60)),
            "mappings": dict(sorted(plan.tag_mappings.items())),
            "diet_suppressions": plan.suppressions,
            "a_desire_values_ignored": dict(plan.other.get("a_desire_values", {})),
        },
        "owner_prefixes": {
            key: {
                **{k: v for k, v in value.items() if k != "recipe_ids"},
                "sites": dict(value["sites"]),
            }
            for key, value in plan.prefixes.items()
        },
        "normalization": {
            "times": dict(plan.time_stats),
            "servings": dict(plan.serving_stats),
            "flags": plan.flags,
        },
        "images": {
            "stats": dict(plan.image_stats),
            "ai_archived": dict(Counter(m["source"] for m in manifest)),
            "orphan_a_images_not_migrated": a_meta.get("orphan_images", []),
        },
        "history": {
            "favorites": sum(len(r["favorites"]) for r in plan.recipes),
            "cook_log": sum(len(r["cook_log"]) for r in plan.recipes),
            "revisions": sum(len(r["revisions"]) for r in plan.recipes),
            "next_time_notes": sum(len(r["next_time_notes"]) for r in plan.recipes),
            "b_feedback_to_cook_log": plan.other.get("b_feedback_to_cook_log", 0),
            "b_feedback_skipped": plan.other.get("b_feedback_skipped", []),
            "aliases": Counter(s for r in plan.recipes for (_a, s) in r["aliases"]),
        },
        "planning": plan.planning["stats"],
        "inbox": [
            {
                "id": i["id"],
                "kind": i["kind"],
                "status": i["status"],
                "url": i["input"].get("url"),
                "title": i["input"].get("title"),
            }
            for i in plan.inbox
        ],
    }


def write(report: dict[str, Any], paths) -> None:
    paths.report_json.parent.mkdir(parents=True, exist_ok=True)
    paths.report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True, default=_default)
    )
    paths.report_md.parent.mkdir(parents=True, exist_ok=True)
    paths.report_md.write_text(render_markdown(json.loads(paths.report_json.read_text())))


def _default(value: Any) -> Any:
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return out


def render_markdown(r: dict[str, Any]) -> str:
    o = r["outcomes"]
    a, b = o.get("A", {}), o.get("B", {})
    lines = [
        "# Migration report",
        "",
        f"Generated by `uv run --group dev python -m cookt.migrate run` at {r['run_at']} "
        "(re-render: `... report`). Machine-readable: `data/migration/report.json`; review "
        "candidates: `data/migration/review-candidates.md`.",
        "",
        "## Counts",
        "",
    ]
    lines += _table(
        ["source", "rows", "migrated", "merged", "dropped", "inbox"],
        [
            [
                "A recipe-table (review DB)",
                r["sources"]["A"]["rows"],
                a.get("migrated", 0),
                a.get("merged", 0),
                a.get("dropped", 0),
                a.get("inbox", 0),
            ],
            [
                "B cookt2",
                r["sources"]["B"]["rows"],
                b.get("migrated", 0),
                b.get("merged", 0),
                b.get("dropped", 0),
                b.get("inbox", 0),
            ],
        ],
    )
    lines += [
        "",
        f"Recipes in the new DB: **{r['recipes']['total']}** "
        f"({', '.join(f'{k} {v}' for k, v in sorted(r['recipes']['by_origin'].items()))}).",
    ]
    lines += ["", "Dropped / inbox, with reasons:", ""]
    for group, items in sorted(r["drop_and_inbox_details"].items()):
        for text, n in items.items():
            lines.append(f"- **{group}**: {text}" + (f" (x{n})" if n > 1 else ""))
    lines += [
        "",
        "Not recipes, not migrated (recipe-table): "
        + ", ".join(f"`{k}` {v}" for k, v in r["sources"]["A"]["out_of_scope_tables"].items())
        + '. AI "editions" = `recipe_refinements`, paintings = `recipe_image_rerolls`.',
        "",
        "Not migrated (cookt2 internals): "
        + ", ".join(f"`{k}` {v}" for k, v in r["sources"]["B"]["out_of_scope_tables"].items())
        + ".",
    ]
    orphans = r["images"].get("orphan_a_images_not_migrated") or []
    if orphans:
        lines.append(
            f"\nrecipe-table image rows with no recipe (scan uploads), not migrated: {len(orphans)}"
        )

    m = r["matching"]
    lines += ["", "## A <-> B matching", ""]
    lines += _table(
        ["rule", "pairs", "definition"],
        [[k, m["pairs_by_rule"].get(k, 0), v] for k, v in m["rules"].items()],
    )
    lines += [
        "",
        f"Review candidates (not merged, operator decides): **{m['review_candidates']}** in "
        "`data/migration/review-candidates.md`. Within-source possible duplicates (reported, "
        f"never merged): A {dict(m['within_a_duplicates'])}, B {dict(m['within_b_duplicates'])}.",
    ]
    if m["pairs_with_notes"]:
        lines.append("")
        for p in m["pairs_with_notes"]:
            lines.append(f"- pair A `{p['a'][:8]}` / B `{p['b'][:8]}` ({p['rule']}): {p['note']}")

    t = r["tags"]
    lines += [
        "",
        "## Tags",
        "",
        "Rows by kind (all `source='migrated'`): "
        + ", ".join(f"{k} {v}" for k, v in sorted(t["rows_by_kind"].items()))
        + ".",
        "",
    ]
    lines += ["Mappings applied (source tag -> kind:value, recipe count):", ""]
    lines += _table(["mapping", "n"], [[k, v] for k, v in t["mappings"].items()])
    lines += ["", f"Diet guard suppressions: **{len(t['diet_suppressions'])}**", ""]
    if t["diet_suppressions"]:
        lines += _table(
            ["recipe", "tag removed", "reason", "ingredient"],
            [[s["title"], s["tag"], s["reason"], s["ingredient"]] for s in t["diet_suppressions"]],
        )
    if t.get("a_desire_values_ignored"):
        lines += [
            "",
            f"recipe-table `desire` values (not migrated, no signal): "
            f"{t['a_desire_values_ignored']}",
        ]

    lines += [
        "",
        "## Owner title prefixes",
        "",
        "Titles are kept as-is; prefixes only derive a `credit` and tags.",
    ]
    lines += [""] + _table(
        ["prefix", "recipes seen", "credit", "guess?", "examples"],
        [
            [
                k,
                v["count"],
                v["credit"] or "-",
                "yes" if v["guess"] else "no",
                "; ".join(v["examples"][:3]),
            ]
            for k, v in sorted(r["owner_prefixes"].items())
        ],
    )

    n = r["normalization"]
    lines += [
        "",
        "## Times and servings",
        "",
        "Times: " + ", ".join(f"{k} {v}" for k, v in sorted(n["times"].items())) + ".",
        "Servings: " + ", ".join(f"{k} {v}" for k, v in sorted(n["servings"].items())) + ".",
        "",
        f"Flags ({len(n['flags'])}):",
        "",
    ]
    lines += _table(
        ["kind", "recipe", "flag"], [[f["kind"], f["title"], f["flag"]] for f in n["flags"]]
    )

    im = r["images"]
    lines += [
        "",
        "## Images",
        "",
        "Display = 4:3 center crop, max 1600 px wide, WebP q82; thumb = 4:3, 480 px, "
        "WebP; original bytes kept as `original.<ext>`. AVIF decoded natively (Pillow "
        "AVIF support present).",
        "",
    ]
    lines += _table(["stat", "n"], [[k, v] for k, v in sorted(im["stats"].items())])
    lines += [
        "",
        f"AI-generated images exported to `data/archive/ai-images/` (with "
        f"`manifest.json`): {im['ai_archived']}.",
    ]

    h = r["history"]
    lines += ["", "## History and planning", ""]
    lines += _table(
        ["item", "n"],
        [
            ["favorites (household, person_id '')", h["favorites"]],
            ["cook_log", h["cook_log"]],
            ["  of which from cookt2 feedback", h["b_feedback_to_cook_log"]],
            ["revisions", h["revisions"]],
            ["next_time_notes", h["next_time_notes"]],
            *[[f"aliases ({k})", v] for k, v in sorted(h["aliases"].items())],
            *[[k, v] for k, v in sorted(r["planning"].items())],
        ],
    )
    if r.get("preserved_tables"):
        lines += [
            "",
            "Preserved from the previous DB: "
            + ", ".join(f"`{k}` {v}" for k, v in r["preserved_tables"].items()),
        ]

    lines += ["", "## Judgment calls (made without the operator)", ""]
    lines += [f"- {text}" for text in JUDGMENT_CALLS]

    v = r.get("verification") or {}
    lines += ["", "## Verification", "", f"Result: **{'PASS' if v.get('ok') else 'FAIL'}**", ""]
    lines += _table(
        ["check", "ok", "detail"],
        [
            [
                c["name"],
                "yes" if c["ok"] else "**NO**",
                json.dumps(c["detail"], ensure_ascii=False)
                if not isinstance(c["detail"], str)
                else c["detail"],
            ]
            for c in v.get("checks", [])
        ],
    )
    return "\n".join(lines) + "\n"
