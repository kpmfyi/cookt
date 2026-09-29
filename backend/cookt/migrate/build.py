"""Steps 4-6 + orchestration: resolve matched pairs, normalize, and write the new cookt DB.

`run()` never writes to a source. It builds into a temp DB + a staging image directory,
verifies them, and only then atomically replaces `--out` and `<out dir>/images`.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db
from ..config import ROOT, settings
from ..document import RecipeDocumentV2
from . import extract as X
from . import images as IMG
from . import normalize as N
from .match import MatchResult, match

NS = uuid.UUID("5b0c2a52-7d1e-4a55-9a3c-6c6f6f6b7432")
MIGRATION_ORIGINS = ("migrated_a", "migrated_b", "merged", "migrated_b_generated")
DEMO_HOUSEHOLD = "Tailnet Demo Kitchen"
HOUSEHOLD = "Home Kitchen"
PRESERVE_TABLES = ("ingredient_parse_cache", "store_section_cache")  # + every fdc_* table
_FTS_SHADOW = ("_data", "_idx", "_content", "_docsize", "_config")


def did(*parts: str) -> str:
    return str(uuid.uuid5(NS, ":".join(parts)))


@dataclass
class Paths:
    data: Path

    @property
    def sources(self) -> Path:
        return self.data / "sources"

    @property
    def work(self) -> Path:
        return self.data / "migration"

    @property
    def archive(self) -> Path:
        return self.data / "archive" / "ai-images"

    @property
    def report_json(self) -> Path:
        return self.work / "report.json"

    @property
    def review_md(self) -> Path:
        return self.work / "review-candidates.md"

    @property
    def report_md(self) -> Path:
        return ROOT / "docs" / "migration-report.md"


class MigrationError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Safety: refuse to clobber real user data
# --------------------------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row is not None


def user_data_reasons(path: Path) -> list[str]:
    """Why replacing `path` would lose non-migration data (empty list = safe)."""

    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "recipes"):
            return []
        reasons: list[str] = []
        meta = (
            dict(conn.execute("SELECT key, value FROM meta").fetchall())
            if _table_exists(conn, "meta")
            else {}
        )
        migrated_at = meta.get("migration_completed_at")
        recipe_count = conn.execute("SELECT count(*) FROM recipes").fetchone()[0]
        if recipe_count and not migrated_at:
            reasons.append(f"{recipe_count} recipes but no migration marker in meta")
            return reasons
        marks = ",".join("?" * len(MIGRATION_ORIGINS))

        def count(sql: str, *params: Any) -> int:
            table = sql.split(" FROM ", 1)[1].split()[0]
            if not _table_exists(conn, table):
                return 0
            return conn.execute(sql, params).fetchone()[0]

        checks = [
            (
                "recipes not created by the migration",
                f"SELECT count(*) FROM recipes WHERE origin NOT IN ({marks})",
                MIGRATION_ORIGINS,
            ),
            (
                "recipes edited after migration",
                "SELECT count(*) FROM recipes WHERE updated_at > ?",
                (migrated_at,),
            ),
            (
                "inbox rows other than the migration re-scrape rows",
                "SELECT count(*) FROM inbox WHERE NOT (kind = 'rescrape' AND "
                "coalesce(json_extract(input, '$.migration'), 0) = 1)",
                (),
            ),
            (
                "cook_log entries newer than the migration",
                "SELECT count(*) FROM cook_log WHERE created_at > ?",
                (migrated_at,),
            ),
            (
                "favorites newer than the migration",
                "SELECT count(*) FROM favorites WHERE created_at > ?",
                (migrated_at,),
            ),
            (
                "next_time_notes newer than the migration",
                "SELECT count(*) FROM next_time_notes WHERE created_at > ?",
                (migrated_at,),
            ),
            (
                "revisions newer than the migration",
                "SELECT count(*) FROM revisions WHERE created_at > ?",
                (migrated_at,),
            ),
            (
                "plan_entries newer than the migration",
                "SELECT count(*) FROM plan_entries WHERE created_at > ?",
                (migrated_at,),
            ),
            (
                "shopping_items changed after the migration",
                "SELECT count(*) FROM shopping_items WHERE updated_at > ?",
                (migrated_at,),
            ),
            (
                "pantry_staples newer than the migration",
                "SELECT count(*) FROM pantry_staples WHERE created_at > ?",
                (migrated_at,),
            ),
            ("people", "SELECT count(*) FROM people", ()),
            ("manual/auto tags", "SELECT count(*) FROM recipe_tags WHERE source != 'migrated'", ()),
            ("rejected tags", "SELECT count(*) FROM rejected_tags", ()),
            ("enrichment changes feed rows", "SELECT count(*) FROM changes", ()),
            ("computed nutrition rows", "SELECT count(*) FROM nutrition", ()),
            ("nutrition overrides", "SELECT count(*) FROM nutrition_overrides", ()),
            ("embeddings", "SELECT count(*) FROM embeddings", ()),
            ("recipe feature profiles", "SELECT count(*) FROM recipe_features", ()),
            ("jobs", "SELECT count(*) FROM jobs", ()),
        ]
        for label, sql, params in checks:
            try:
                n = count(sql, *params)
            except sqlite3.Error as exc:
                reasons.append(f"could not check {label}: {exc}")
                continue
            if n:
                reasons.append(f"{n} {label}")
        return reasons
    finally:
        conn.close()


def processes_holding(path: Path) -> list[str]:
    """PIDs (other than us) with `path` (or its -wal/-shm) open, from /proc."""

    targets = {str(path.resolve()), f"{path.resolve()}-wal", f"{path.resolve()}-shm"}
    holders: list[str] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return holders
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    if os.readlink(fd) in targets:
                        cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
                        holders.append(f"{entry.name} {cmd.strip()[:80]}")
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return holders


# --------------------------------------------------------------------------------------------
# Resolve
# --------------------------------------------------------------------------------------------


@dataclass
class Plan:
    recipes: list[dict[str, Any]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    inbox: list[dict[str, Any]] = field(default_factory=list)
    archive: list[dict[str, Any]] = field(default_factory=list)
    suppressions: list[dict[str, Any]] = field(default_factory=list)
    tag_mappings: Counter = field(default_factory=Counter)
    prefixes: dict[str, dict[str, Any]] = field(default_factory=dict)
    flags: list[dict[str, Any]] = field(default_factory=list)
    time_stats: Counter = field(default_factory=Counter)
    serving_stats: Counter = field(default_factory=Counter)
    image_stats: Counter = field(default_factory=Counter)
    other: dict[str, Any] = field(default_factory=dict)
    planning: dict[str, Any] = field(default_factory=dict)


def _a_tags(rec: dict[str, Any], plan: Plan) -> list[tuple[str, str, str]]:
    tags: list[tuple[str, str, str]] = []
    for cls in rec["classifications"]:
        for raw in cls.get("tags") or []:
            kind, value = N.map_free_tag(str(raw))
            tags.append((kind, value, f"recipe-table classification tag '{raw}'"))
            plan.tag_mappings[f"A tag '{raw}' -> {kind}:{value}"] += 1
        for key, values in (cls.get("facets") or {}).items():
            for raw in values or []:
                if key == "cuisines":
                    kind, value = "cuisine", N.CUISINES.get(str(raw).casefold(), str(raw).title())
                else:
                    kind, value = "personal", f"{key.rstrip('s')}: {str(raw).casefold()}"
                tags.append((kind, value, f"recipe-table facet {key}={raw}"))
                plan.tag_mappings[f"A facet {key} '{raw}' -> {kind}:{value}"] += 1
        plan.other.setdefault("a_desire_values", Counter())[cls.get("desire") or ""] += 1
    return tags


def _b_tags(rec: dict[str, Any], plan: Plan, b_title_by_id: dict[str, str]) -> list:
    tags: list[tuple[str, str, str]] = []
    for tag in rec["tags"]:
        kind, value = N.map_b_tag(tag["category"], tag["name"])
        tags.append((kind, value, f"cookt2 tag {tag['category']}:{tag['name']}"))
        plan.tag_mappings[f"B {tag['category']}:{tag['name']} -> {kind}:{value}"] += 1
    for name in rec["collections"]:
        value = name.strip().casefold()
        tags.append(("personal", value, f"cookt2 collection '{name}'"))
        plan.tag_mappings[f"B collection '{name}' -> personal:{value}"] += 1
    if rec["origin"] == "generated":
        tags.append(("personal", "ai-generated", "cookt2 origin 'generated' (LLM-generated)"))
    elif rec["source_recipe_id"]:
        parent = b_title_by_id.get(rec["source_recipe_id"], rec["source_recipe_id"])
        tags.append(
            (
                "personal",
                "ai-generated",
                f"cookt2 source_recipe_id set (generated companion of '{parent}')",
            )
        )
    return tags


def _title_tags(title: str, plan: Plan, recipe_id: str, site: str | None, author: str | None):
    info = N.analyze_title(title)
    for key in info.prefixes:
        entry = plan.prefixes.setdefault(
            key,
            {
                "count": 0,
                "credit": None,
                "guess": False,
                "examples": [],
                "sites": Counter(),
                "recipe_ids": set(),
            },
        )
        if recipe_id not in entry["recipe_ids"]:
            entry["recipe_ids"].add(recipe_id)
            entry["count"] += 1
            entry["sites"][site or "?"] += 1
        if len(entry["examples"]) < 4 and title not in entry["examples"]:
            entry["examples"].append(title)
        if info.credit and entry["credit"] is None:
            entry["credit"] = info.credit
            entry["guess"] = info.credit_guess
    return info


def _yield_and_times(
    doc: dict[str, Any], b: dict[str, Any] | None, plan: Plan, title: str, rid: str
) -> dict[str, Any]:
    out: dict[str, Any] = {"prep": None, "cook": None, "total": None, "servings": None}
    times = N.parse_times(doc.get("notes") or [])
    for kind in ("prep", "cook", "total"):
        value = getattr(times, kind)
        if value is not None:
            out[kind] = value
            plan.time_stats[f"{kind} from notes"] += 1
    for flag in times.flags:
        plan.flags.append({"recipe_id": rid, "title": title, "kind": "time", "flag": flag})
    parsed = N.parse_yield(doc.get("yield_text"))
    if parsed.servings is not None:
        out["servings"] = parsed.servings
        plan.serving_stats["from yield_text"] += 1
    if parsed.flag:
        plan.flags.append(
            {
                "recipe_id": rid,
                "title": title,
                "kind": "yield",
                "flag": f"{parsed.flag}: {doc.get('yield_text')!r}",
            }
        )
    if b is not None:
        for kind, column in (("prep", "prep_time"), ("cook", "cook_time"), ("total", "total_time")):
            if out[kind] is None and b.get(column):
                out[kind] = int(b[column])
                plan.time_stats[f"{kind} from cookt2 column"] += 1
        if out["servings"] is None and b.get("servings"):
            out["servings"] = float(b["servings"])
            plan.serving_stats["from cookt2 servings"] += 1
    if out["servings"] is None:
        plan.serving_stats["none"] += 1
    return out


def resolve(
    a_records: list[dict[str, Any]],
    b_records: list[dict[str, Any]],
    planning: dict[str, Any],
    result: MatchResult,
    run_at: str,
) -> Plan:
    plan = Plan()
    b_by = {r["source_id"]: r for r in b_records}
    b_title_by_id = {r["source_id"]: r["title"] for r in b_records}
    pair_by_a = {p.a: p for p in result.pairs}
    pair_by_b = {p.b: p for p in result.pairs}

    def add_archive(source: str, rec: dict[str, Any], image: dict[str, Any]) -> None:
        plan.archive.append(
            {
                "source": source,
                "source_recipe_id": rec["source_id"],
                "title": rec["title"],
                "image_id": image.get("id"),
                "file": image["file"],
                "thumb_file": image.get("thumb_file"),
                "sha256": image["sha256"],
                "media_type": image.get("media_type"),
                "reason": image.get("ai_reason"),
            }
        )

    # ---- A: classify ----
    for rec in sorted(a_records, key=lambda r: r["source_id"]):
        aid = rec["source_id"]
        if rec["household"] != HOUSEHOLD:
            plan.ledger.append(
                {
                    "source": "A",
                    "source_id": aid,
                    "title": rec["title"],
                    "outcome": "dropped",
                    "recipe_id": None,
                    "reason": f"demo recipe (household '{rec['household']}'), not household data",
                }
            )
            for image in rec["images"]:
                if image["ai_generated"]:
                    add_archive("A", rec, image)
            continue
        if rec["legacy_v1"]:
            steps = [
                s["text"] for sec in rec["document"]["instruction_sections"] for s in sec["steps"]
            ]
            plan.inbox.append(
                {
                    "id": did("inbox", "rescrape", "A", aid),
                    "kind": "rescrape",
                    "status": "queued",
                    "input": {
                        "migration": True,
                        "url": rec["source_url"],
                        "title": rec["title"],
                        "source_site": rec["source_site"],
                        "source_author": rec["source_author"],
                        "old_ids": {"recipe-table": aid},
                        "reason": "recipe-table kept only a legacy v1 document (ingredients + "
                        "3 short action labels, original directions not retained); re-scrape "
                        "the source instead of migrating an empty recipe",
                        "favorite": bool(rec["favorites"]),
                        "previous_document": rec["document_raw"],
                        "previous_action_labels": steps,
                        "intermediate_images": [i["file"] for i in rec["images"]],
                    },
                    "created_at": rec["created_at"],
                    "updated_at": run_at,
                }
            )
            plan.ledger.append(
                {
                    "source": "A",
                    "source_id": aid,
                    "title": rec["title"],
                    "outcome": "inbox",
                    "recipe_id": None,
                    "reason": "empty 'Modern Proper carnitas' (legacy v1 document, directions "
                    "not retained) -> inbox re-scrape",
                }
            )
            for image in rec["images"]:
                if image["ai_generated"]:
                    add_archive("A", rec, image)
            continue

    dropped_or_inbox_a = {row["source_id"] for row in plan.ledger if row["source"] == "A"}

    # ---- Build recipes: A-origin (incl. merged) ----
    def base_recipe(rid: str) -> dict[str, Any]:
        return {
            "id": rid,
            "tags": {},
            "aliases": [],
            "favorites": [],
            "cook_log": [],
            "next_time_notes": [],
            "revisions": [],
            "images": [],
            "flags": [],
            "sources": {},
        }

    def add_tags(recipe: dict[str, Any], tags: list[tuple[str, str, str]]) -> None:
        for kind, value, evidence in tags:
            key = (kind, value)
            if key in recipe["tags"]:
                if evidence not in recipe["tags"][key]:
                    recipe["tags"][key] += f"; {evidence}"
            else:
                recipe["tags"][key] = evidence

    for rec in sorted(a_records, key=lambda r: r["source_id"]):
        aid = rec["source_id"]
        if aid in dropped_or_inbox_a:
            continue
        pair = pair_by_a.get(aid)
        b = b_by[pair.b] if pair else None
        recipe = base_recipe(aid)
        recipe["sources"] = {"A": aid, **({"B": b["source_id"]} if b else {})}
        recipe["origin"] = "merged" if b else "migrated_a"
        doc = dict(rec["document"])
        personal = doc.get("personal_notes")
        if personal:
            recipe["next_time_notes"].append(
                {
                    "id": did("next-time", "A-personal-notes", aid),
                    "text": personal,
                    "created_at": rec["updated_at"],
                    "evidence": "recipe-table document.personal_notes",
                }
            )
        doc["personal_notes"] = None
        recipe["document"] = doc
        recipe["title"] = doc["title"]
        recipe["version"] = max(int(rec["edit_version"] or 1), 1)
        site = rec["source_site"]
        if site in ("HTML export", "Demo kitchen"):
            site = None
        source_url = rec["source_url"] or (b["source_url"] if b else None)
        recipe["source_url"] = source_url
        recipe["canonical_url"] = N.canonical_url(source_url)
        recipe["source_site"] = (
            site or (b["source_site"] if b else None) or N.site_from_url(source_url)
        )
        recipe["source_author"] = rec["source_author"]
        recipe["description"] = (b["description"] or None) if b else None
        stamps = [rec["created_at"]] + ([b["created_at"]] if b else [])
        recipe["created_at"] = min(s for s in stamps if s)
        recipe["updated_at"] = max(
            s for s in [rec["updated_at"]] + ([b["updated_at"]] if b else []) if s
        )
        recipe["archived_at"] = rec["archived_at"]
        # Times / servings.
        recipe.update(
            {
                f"{k}_minutes" if k != "servings" else k: v
                for k, v in _yield_and_times(doc, b, plan, recipe["title"], aid).items()
            }
        )
        # Credit + title tags.
        info = _title_tags(rec["title"], plan, aid, recipe["source_site"], rec["source_author"])
        credit, guess = info.credit, info.credit_guess
        title_tags = list(info.tags)
        if b and b["title"] != rec["title"]:
            b_info = _title_tags(b["title"], plan, aid, recipe["source_site"], None)
            if credit is None and b_info.credit:
                credit, guess = b_info.credit, b_info.credit_guess
            title_tags += b_info.tags
        if credit is None and rec["source_author"]:
            credit = rec["source_author"]
        if b:
            credit = _b_source_name(b, recipe, credit)
        recipe["credit"] = credit
        if guess:
            recipe["flags"].append(f"credit '{credit}' is a guess from the title prefix")
        add_tags(recipe, title_tags)
        add_tags(recipe, _a_tags(rec, plan))
        for cls in rec["classifications"]:
            if (cls.get("note") or "").strip():
                recipe["next_time_notes"].append(
                    {
                        "id": did("next-time", "A-classification-note", aid, cls["user_id"]),
                        "text": cls["note"],
                        "created_at": cls["updated_at"],
                        "evidence": "recipe-table classification note",
                    }
                )
        if b:
            add_tags(recipe, _b_tags(b, plan, b_title_by_id))
        # Favorites.
        fav_times = [f["created_at"] for f in rec["favorites"]]
        if b and b["is_favorite"]:
            fav_times.append(b["updated_at"])
        if fav_times:
            recipe["favorites"].append({"created_at": min(fav_times)})
        # Cook log.
        for event in rec["cook_events"]:
            recipe["cook_log"].append(
                {
                    "id": event["id"],
                    "cooked_on": event["cooked_at"][:10],
                    "make_again": None if event["make_again"] is None else int(event["make_again"]),
                    "note": (event["note"] or "").strip() or None,
                    "created_at": event["created_at"],
                }
            )
        if b:
            _b_feedback(b, recipe, plan)
        # Revisions.
        for rev in rec["revisions"]:
            recipe["revisions"].append(
                {
                    "id": rev["id"],
                    "version": rev["recipe_version"],
                    "snapshot": {
                        "title": (rev["document"] or {}).get("title"),
                        "document": rev["document"],
                        "source_site": rev["source_site"],
                        "source_author": rev["source_author"],
                        "migrated_from": {
                            "system": "recipe-table",
                            "revision_id": rev["id"],
                            "reason": rev["reason"],
                            "recipe_version": rev["recipe_version"],
                            "scraped_at": rev["scraped_at"],
                        },
                    },
                    "reason": "migrated from recipe-table",
                    "created_at": rev["created_at"],
                }
            )
        # Images.
        primary_kept = False
        for image in sorted(rec["images"], key=lambda i: (i["role"] != "primary", i["position"])):
            if image["ai_generated"]:
                add_archive("A", rec, image)
                plan.image_stats["A AI-generated dropped"] += 1
                continue
            if image["role"] == "primary":
                if primary_kept:
                    plan.image_stats["A extra primary skipped"] += 1
                    continue
                primary_kept = True
                recipe["images"].append({**image, "source": "A", "new_role": "primary"})
                plan.image_stats["A primary kept"] += 1
            else:
                recipe["images"].append({**image, "source": "A", "new_role": "step"})
                plan.image_stats["A step kept"] += 1
        if b:
            for image in b["images"]:
                if not image.get("exists"):
                    plan.image_stats["B image file missing"] += 1
                    continue
                if image["ai_generated"]:
                    add_archive("B", b, image)
                    plan.image_stats["B AI-generated dropped"] += 1
                elif not primary_kept:
                    recipe["images"].append(
                        {
                            **image,
                            "id": did("image", "B", b["source_id"]),
                            "source": "B",
                            "new_role": "primary",
                        }
                    )
                    primary_kept = True
                    plan.image_stats["B primary kept (A had none)"] += 1
                else:
                    plan.image_stats["B real photo skipped (A primary preferred)"] += 1
        if not primary_kept:
            plan.image_stats["recipes with typographic fallback"] += 1
        # Aliases.
        recipe["aliases"].append((aid, "recipe-table"))
        if b:
            recipe["aliases"].append((b["source_id"], "cookt2"))
            if b["slug"]:
                recipe["aliases"].append((b["slug"], "slug"))
        plan.recipes.append(recipe)
        plan.ledger.append(
            {
                "source": "A",
                "source_id": aid,
                "title": rec["title"],
                "outcome": "merged" if b else "migrated",
                "recipe_id": aid,
                "reason": (
                    f"merged with cookt2 {b['source_id']} by {pair.rule}"
                    f" (title {pair.title_sim:.2f}, ingredients {pair.jaccard:.2f})"
                    if b
                    else "recipe-table only (no cookt2 match)"
                ),
            }
        )
        if b:
            plan.ledger.append(
                {
                    "source": "B",
                    "source_id": b["source_id"],
                    "title": b["title"],
                    "outcome": "merged",
                    "recipe_id": aid,
                    "reason": f"merged into recipe-table {aid} by {pair.rule}; A document kept"
                    + (f" ({pair.note})" if pair.note else ""),
                }
            )

    # ---- B-only ----
    for b in sorted(b_records, key=lambda r: r["source_id"]):
        bid = b["source_id"]
        if bid in pair_by_b:
            continue
        if b["document"] is None:
            outcome, reason = (
                "dropped",
                (
                    "empty cookt2 row: no ingredients"
                    + (" and no instructions" if not b["instructions"] else "")
                    + ", no source URL, no recipe-table counterpart"
                ),
            )
            if b["source_url"]:
                outcome = "inbox"
                reason = "cookt2 row without ingredients -> inbox re-scrape"
                plan.inbox.append(
                    {
                        "id": did("inbox", "rescrape", "B", bid),
                        "kind": "rescrape",
                        "status": "queued",
                        "input": {
                            "migration": True,
                            "url": b["source_url"],
                            "title": b["title"],
                            "old_ids": {"cookt2": bid},
                            "reason": reason,
                        },
                        "created_at": b["created_at"],
                        "updated_at": run_at,
                    }
                )
            plan.ledger.append(
                {
                    "source": "B",
                    "source_id": bid,
                    "title": b["title"],
                    "outcome": outcome,
                    "recipe_id": None,
                    "reason": reason,
                }
            )
            for image in b["images"]:
                if image.get("exists") and image["ai_generated"]:
                    add_archive("B", b, image)
            continue
        recipe = base_recipe(bid)
        recipe["sources"] = {"B": bid}
        generated = b["origin"] == "generated"
        recipe["origin"] = "migrated_b_generated" if generated else "migrated_b"
        doc = dict(b["document"])
        recipe["document"] = doc
        recipe["title"] = doc["title"]
        recipe["flags"].extend(b["document_flags"])
        recipe["version"] = 1 + len(b["recipe_edits"])
        recipe["source_url"] = b["source_url"]
        recipe["canonical_url"] = b["canonical_url"]
        recipe["source_site"] = b["source_site"] or None
        recipe["source_author"] = None
        recipe["description"] = b["description"] or None
        recipe["created_at"] = b["created_at"]
        recipe["updated_at"] = b["updated_at"] or b["created_at"]
        recipe["archived_at"] = b["updated_at"] if b["is_archived"] else None
        recipe["prep_minutes"] = b["prep_time"] or None
        recipe["cook_minutes"] = b["cook_time"] or None
        recipe["total_minutes"] = b["total_time"] or None
        for kind, column in (("prep", "prep_time"), ("cook", "cook_time"), ("total", "total_time")):
            if b[column]:
                plan.time_stats[f"{kind} from cookt2 column"] += 1
        recipe["servings"] = float(b["servings"]) if b["servings"] else None
        plan.serving_stats["from cookt2 servings" if b["servings"] else "none"] += 1
        info = _title_tags(b["title"], plan, bid, recipe["source_site"], None)
        recipe["credit"] = _b_source_name(b, recipe, info.credit)
        if info.credit_guess:
            recipe["flags"].append(f"credit '{info.credit}' is a guess from the title prefix")
        add_tags(recipe, list(info.tags))
        add_tags(recipe, _b_tags(b, plan, b_title_by_id))
        if b["is_favorite"]:
            recipe["favorites"].append({"created_at": b["updated_at"]})
        _b_feedback(b, recipe, plan)
        for edit in b["recipe_edits"]:
            before = (
                json.loads(edit["before"]) if isinstance(edit["before"], str) else edit["before"]
            )
            after = json.loads(edit["after"]) if isinstance(edit["after"], str) else edit["after"]
            diff = json.loads(edit["diff"]) if isinstance(edit["diff"], str) else edit["diff"]
            recipe["revisions"].append(
                {
                    "id": edit["id"],
                    "version": 1,
                    "snapshot": {
                        "title": (before or {}).get("title"),
                        "cookt2_recipe": before,
                        "migrated_from": {
                            "system": "cookt2",
                            "recipe_edit_id": edit["id"],
                            "diff": diff,
                        },
                    },
                    "reason": "migrated from cookt2",
                    "created_at": X.iso(edit["edited_at"]),
                }
            )
            if "notes" in (diff or {}).get("changed_fields", []) and (after or {}).get("notes"):
                recipe["next_time_notes"].append(
                    {
                        "id": did("next-time", "B-edit-notes", edit["id"]),
                        "text": after["notes"],
                        "created_at": X.iso(edit["edited_at"]),
                        "evidence": "cookt2 household edit of the notes field",
                    }
                )
        for image in b["images"]:
            if not image.get("exists"):
                plan.image_stats["B image file missing"] += 1
                continue
            if image["ai_generated"]:
                add_archive("B", b, image)
                plan.image_stats["B AI-generated dropped"] += 1
            else:
                recipe["images"].append(
                    {**image, "id": did("image", "B", bid), "source": "B", "new_role": "primary"}
                )
                plan.image_stats["B primary kept"] += 1
        if not recipe["images"]:
            plan.image_stats["recipes with typographic fallback"] += 1
        recipe["aliases"].append((bid, "cookt2"))
        if b["slug"]:
            recipe["aliases"].append((b["slug"], "slug"))
        plan.recipes.append(recipe)
        plan.ledger.append(
            {
                "source": "B",
                "source_id": bid,
                "title": b["title"],
                "outcome": "migrated",
                "recipe_id": bid,
                "reason": (
                    "cookt2 generated recipe saved by the household (not a recipe-table edition)"
                    if generated
                    else "cookt2 only (no recipe-table match)"
                ),
            }
        )

    # ---- Diet guard + slugs ----
    for recipe in plan.recipes:
        texts = N.document_ingredient_texts(recipe["document"])
        kept, suppressed = N.diet_guard(list(recipe["tags"].keys()), texts)
        for entry in suppressed:
            plan.suppressions.append({"recipe_id": recipe["id"], "title": recipe["title"], **entry})
        recipe["tags"] = {key: recipe["tags"][key] for key in kept}
    taken: set[str] = set()  # new slugs are also registered as aliases (system 'slug')
    for recipe in sorted(plan.recipes, key=lambda r: (r["created_at"], r["id"])):
        base = N.slugify(recipe["title"])
        slug, n = base, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        recipe["slug"] = slug
        recipe["aliases"].append((slug, "slug"))
    alias_seen: dict[str, str] = {}
    for recipe in plan.recipes:
        kept_aliases = []
        for alias, system in recipe["aliases"]:
            if system == "slug" and (alias in taken and alias != recipe["slug"]):
                plan.flags.append(
                    {
                        "recipe_id": recipe["id"],
                        "title": recipe["title"],
                        "kind": "alias",
                        "flag": f"old cookt2 slug '{alias}' is another recipe's new slug; "
                        "alias skipped",
                    }
                )
                continue
            if alias in alias_seen:
                plan.flags.append(
                    {
                        "recipe_id": recipe["id"],
                        "title": recipe["title"],
                        "kind": "alias",
                        "flag": f"duplicate alias '{alias}' skipped",
                    }
                )
                continue
            alias_seen[alias] = recipe["id"]
            kept_aliases.append((alias, system))
        recipe["aliases"] = kept_aliases
    for recipe in plan.recipes:
        for flag in recipe["flags"]:
            plan.flags.append(
                {
                    "recipe_id": recipe["id"],
                    "title": recipe["title"],
                    "kind": "recipe",
                    "flag": flag,
                }
            )

    plan.planning = _planning(planning, plan, b_by)
    return plan


_SCAN_SOURCE = ("scanned from cookbook", "cookbook scan")


def _b_source_name(b: dict[str, Any], recipe: dict[str, Any], credit: str | None) -> str | None:
    """cookt2 `source_name` that is not a domain: a scan marker -> personal tag, else a credit."""

    name = (b.get("source_name") or "").strip()
    if not name or X._domain_like(name):
        return credit
    if name.casefold() in _SCAN_SOURCE:
        recipe["tags"].setdefault(("personal", "cookbook scan"), f"cookt2 source_name '{name}'")
        return credit
    return credit or name


def _b_feedback(b: dict[str, Any], recipe: dict[str, Any], plan: Plan) -> None:
    for fb in b["feedback"]:
        tags = json.loads(fb["tags"]) if isinstance(fb["tags"], str) else (fb["tags"] or [])
        if fb["kind"] != "cooked":
            plan.other.setdefault("b_feedback_skipped", []).append(
                {"id": fb["id"], "kind": fb["kind"], "recipe": b["title"]}
            )
            continue
        parts = []
        if fb["rating"] is not None:
            parts.append(f"Rating {fb['rating']}/5")
        if tags:
            parts.append(", ".join(str(t).replace("_", " ") for t in tags))
        if (fb["text"] or "").strip():
            parts.append(fb["text"].strip())
        recipe["cook_log"].append(
            {
                "id": fb["id"],
                "cooked_on": X.iso(fb["created_at"])[:10],
                "make_again": 1 if "would_make_again" in tags else None,
                "note": " · ".join(parts) or None,
                "created_at": X.iso(fb["created_at"]),
            }
        )
        plan.other.setdefault("b_feedback_to_cook_log", 0)
        plan.other["b_feedback_to_cook_log"] += 1


def _planning(planning: dict[str, Any], plan: Plan, b_by: dict[str, Any]) -> dict[str, Any]:
    new_id_for_b: dict[str, str | None] = {}
    title_for_new: dict[str, str] = {r["id"]: r["title"] for r in plan.recipes}
    for row in plan.ledger:
        if row["source"] == "B":
            new_id_for_b[row["source_id"]] = row["recipe_id"]
    stats: Counter = Counter()
    # Shopping: unchecked items only, dedupe by folded name.
    items: dict[str, dict[str, Any]] = {}
    for item in planning["shopping_items"]:
        if item["is_checked"]:
            stats["shopping checked skipped"] += 1
            continue
        name = (item["ingredient_name"] or "").strip()
        if not name:
            stats["shopping empty skipped"] += 1
            continue
        key = " ".join(N.fold(name).split())
        rid = new_id_for_b.get(item["recipe_id"]) if item["recipe_id"] else None
        text = " ".join(p for p in (item["quantity"], item["unit"], name) if p)
        source = {
            "recipe_id": rid,
            "title": title_for_new.get(rid) if rid else None,
            "text": text,
            "list": item["list_name"],
        }
        if key in items:
            items[key]["sources"].append(source)
            stats["shopping duplicates merged"] += 1
            continue
        items[key] = {
            "id": did("shopping", key),
            "name": name,
            "quantity": item["quantity"] or None,
            "unit": item["unit"] or None,
            "section": item["category"] or None,
            "sources": [source],
            "position": len(items),
            "created_at": X.iso(item["list_created_at"]),
        }
    stats["shopping migrated"] = len(items)
    # Meal plans -> plan entries.
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
    entries = []
    for mp in sorted(
        planning["meal_plans"], key=lambda m: (m["date"], order.get(m["meal_type"], 9))
    ):
        rid = new_id_for_b.get(mp["recipe_id"])
        if not rid:
            stats["meal plans skipped (recipe not migrated)"] += 1
            continue
        same_day = sum(1 for e in entries if e["day"] == mp["date"])
        note = mp["meal_type"] + (f" · {mp['notes']}" if mp["notes"] else "")
        entries.append(
            {
                "id": mp["id"],
                "day": mp["date"],
                "recipe_id": rid,
                "servings": float(mp["servings"]) if mp["servings"] else None,
                "position": same_day,
                "note": note,
                "created_at": X.iso(mp["created_at"]),
            }
        )
    stats["plan entries migrated"] = len(entries)
    # Pantry -> staples (names only).
    staples: dict[str, dict[str, Any]] = {}
    for item in planning["pantry_items"]:
        name = (item["name"] or "").strip()
        key = " ".join(N.fold(name).split())
        if not name or key in staples:
            continue
        staples[key] = {"name": name, "created_at": X.iso(item["created_at"])}
    stats["pantry staples migrated"] = len(staples)
    stats["pantry items in source"] = len(planning["pantry_items"])
    stats["shopping items in source"] = len(planning["shopping_items"])
    stats["meal plans in source"] = len(planning["meal_plans"])
    stats["collections -> personal tags"] = len(planning["collections"])
    return {
        "shopping": list(items.values()),
        "plan_entries": entries,
        "pantry": list(staples.values()),
        "stats": dict(stats),
    }


# --------------------------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------------------------


def write_db(
    conn: sqlite3.Connection, plan: Plan, images_root: Path, work: Path, run_at: str
) -> None:
    conn.execute("BEGIN")
    for recipe in plan.recipes:
        document = RecipeDocumentV2.model_validate(recipe["document"]).model_dump(mode="json")
        primary_id = None
        image_rows = []
        for image in recipe["images"]:
            original = work / image["file"]
            ext = original.suffix.lstrip(".").lower() or "bin"
            if image["new_role"] == "primary":
                derived = IMG.derive(original, images_root, recipe["id"], "", ext)
                primary_id = image["id"]
            else:
                derived = IMG.derive(
                    original, images_root, f"{recipe['id']}/steps/{image['id']}", "", ext
                )
            image_rows.append(
                (
                    image["id"],
                    recipe["id"],
                    image["new_role"],
                    image.get("step_id") if image["new_role"] == "step" else None,
                    image.get("position") or 0,
                    derived.path,
                    derived.thumb_path,
                    "image/webp",
                    derived.width,
                    derived.height,
                    image["sha256"],
                    image.get("source_url"),
                    image.get("created_at") or recipe["created_at"],
                )
            )
            plan.image_stats[f"original format {image.get('media_type')}"] += 1
        conn.execute(
            """INSERT INTO recipes (id, slug, title, document, source_url, canonical_url,
                 source_site, source_author, credit, description, prep_minutes, cook_minutes,
                 total_minutes, servings, image_id, origin, version, created_at, updated_at,
                 archived_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                recipe["id"],
                recipe["slug"],
                recipe["title"],
                db.dumps(document),
                recipe["source_url"],
                recipe["canonical_url"],
                recipe["source_site"],
                recipe["source_author"],
                recipe["credit"],
                recipe["description"],
                recipe["prep_minutes"],
                recipe["cook_minutes"],
                recipe["total_minutes"],
                recipe["servings"],
                primary_id,
                recipe["origin"],
                recipe["version"],
                recipe["created_at"],
                recipe["updated_at"],
                recipe["archived_at"],
            ),
        )
        conn.executemany(
            """INSERT INTO images (id, recipe_id, role, step_id, position, path, thumb_path,
                 media_type, width, height, sha256, source_url, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            image_rows,
        )
        conn.executemany(
            "INSERT INTO recipe_aliases (alias, recipe_id, system) VALUES (?,?,?)",
            [(alias, recipe["id"], system) for alias, system in recipe["aliases"]],
        )
        conn.executemany(
            """INSERT INTO recipe_tags (recipe_id, kind, value, source, confidence, evidence,
                 created_at) VALUES (?,?,?,'migrated',NULL,?,?)""",
            [
                (recipe["id"], kind, value, evidence, run_at)
                for (kind, value), evidence in sorted(recipe["tags"].items())
            ],
        )
        conn.executemany(
            "INSERT INTO favorites (recipe_id, person_id, created_at) VALUES (?, '', ?)",
            [(recipe["id"], fav["created_at"]) for fav in recipe["favorites"]],
        )
        conn.executemany(
            """INSERT INTO cook_log (id, recipe_id, person_id, cooked_on, make_again, note,
                 created_at) VALUES (?,?,NULL,?,?,?,?)""",
            [
                (e["id"], recipe["id"], e["cooked_on"], e["make_again"], e["note"], e["created_at"])
                for e in recipe["cook_log"]
            ],
        )
        conn.executemany(
            """INSERT INTO next_time_notes (id, recipe_id, person_id, text, created_at,
                 resolved_at) VALUES (?,?,NULL,?,?,NULL)""",
            [
                (n["id"], recipe["id"], n["text"], n["created_at"])
                for n in recipe["next_time_notes"]
            ],
        )
        conn.executemany(
            """INSERT INTO revisions (id, recipe_id, version, snapshot, reason, person_id,
                 created_at) VALUES (?,?,?,?,?,NULL,?)""",
            [
                (
                    r["id"],
                    recipe["id"],
                    r["version"],
                    db.dumps(r["snapshot"]),
                    r["reason"],
                    r["created_at"],
                )
                for r in recipe["revisions"]
            ],
        )
    conn.executemany(
        """INSERT INTO inbox (id, kind, status, input, draft, error, duplicate_of, recipe_id,
             person_id, created_at, updated_at)
           VALUES (?,?,?,?,NULL,NULL,NULL,NULL,NULL,?,?)""",
        [
            (
                i["id"],
                i["kind"],
                i["status"],
                db.dumps(i["input"]),
                i["created_at"],
                i["updated_at"],
            )
            for i in plan.inbox
        ],
    )
    conn.executemany(
        """INSERT INTO migration_ledger (source, source_id, title, outcome, recipe_id, reason)
           VALUES (?,?,?,?,?,?)""",
        [
            (r["source"], r["source_id"], r["title"], r["outcome"], r["recipe_id"], r["reason"])
            for r in plan.ledger
        ],
    )
    planning = plan.planning
    conn.executemany(
        """INSERT INTO shopping_items (id, name, quantity, unit, section, sources, checked,
             position, created_at, updated_at) VALUES (?,?,?,?,?,?,0,?,?,?)""",
        [
            (
                s["id"],
                s["name"],
                s["quantity"],
                s["unit"],
                s["section"],
                db.dumps(s["sources"]),
                s["position"],
                s["created_at"],
                run_at,
            )
            for s in planning["shopping"]
        ],
    )
    conn.executemany(
        """INSERT INTO plan_entries (id, day, recipe_id, servings, position, note, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (
                e["id"],
                e["day"],
                e["recipe_id"],
                e["servings"],
                e["position"],
                e["note"],
                e["created_at"],
            )
            for e in planning["plan_entries"]
        ],
    )
    conn.executemany(
        "INSERT INTO pantry_staples (name, created_at) VALUES (?, ?)",
        [(p["name"], p["created_at"]) for p in planning["pantry"]],
    )
    conn.execute("COMMIT")


def export_archive(plan: Plan, work: Path, archive: Path) -> list[dict[str, Any]]:
    if archive.exists():
        shutil.rmtree(archive)
    archive.mkdir(parents=True)
    manifest = []
    for entry in plan.archive:
        src = work / entry["file"]
        dest = archive / entry["source"] / entry["source_recipe_id"] / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        row = {**entry, "archived_file": str(dest.relative_to(archive))}
        if entry.get("thumb_file"):
            tsrc = work / entry["thumb_file"]
            tdest = dest.parent / tsrc.name
            shutil.copyfile(tsrc, tdest)
            row["archived_thumb"] = str(tdest.relative_to(archive))
        manifest.append(row)
    (archive / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True)
    )
    return manifest


def preserve_tables(old_db: Path, new_conn: sqlite3.Connection) -> dict[str, int]:
    """Copy fdc_* (USDA) tables and model caches from the DB being replaced."""

    copied: dict[str, int] = {}
    if not old_db.exists():
        return copied
    new_conn.execute("ATTACH DATABASE ? AS old", (str(old_db),))  # SELECT only
    try:
        rows = new_conn.execute(
            "SELECT name, type, sql FROM old.sqlite_master WHERE type = 'table'"
        ).fetchall()
        for name, _type, sql in rows:
            if not (name.startswith("fdc_") or name in PRESERVE_TABLES):
                continue
            if any(name.endswith(suffix) for suffix in _FTS_SHADOW) and name.startswith(
                "fdc_food_fts"
            ):
                continue
            virtual = (sql or "").upper().startswith("CREATE VIRTUAL TABLE")
            if not _table_exists(new_conn, name):
                new_conn.execute(sql)
            cols = [r[1] for r in new_conn.execute(f"PRAGMA old.table_info({name})").fetchall()]
            col_list = ", ".join(f'"{c}"' for c in cols)
            if virtual:
                col_list = "rowid, " + col_list
            new_conn.execute(f'DELETE FROM main."{name}"')
            new_conn.execute(
                f'INSERT INTO main."{name}" ({col_list}) SELECT {col_list} FROM old."{name}"'
            )
            copied[name] = new_conn.execute(f'SELECT count(*) FROM main."{name}"').fetchone()[0]
    finally:
        new_conn.execute("DETACH DATABASE old")
    return copied


# --------------------------------------------------------------------------------------------
# Review report
# --------------------------------------------------------------------------------------------


def write_review(path: Path, result: MatchResult) -> None:
    lines = [
        "# Migration review candidates",
        "",
        "Generated by `python -m cookt.migrate run`. **Nothing here was merged.** Each pair stays "
        "two separate recipes until the operator decides. Auto-merge rules: canonical URL, or "
        "normalized title similarity >= 0.85 and ingredient Jaccard >= 0.7 (never across two "
        "different source sites), or an empty cookt2 shell with an identical title.",
        "",
        f"## A <-> B candidates ({len(result.review)})",
        "",
        "| recipe-table (A) | cookt2 (B) | title sim | ingredient Jaccard | A merged elsewhere | "
        "B merged elsewhere | why |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in result.review:
        lines.append(
            f"| {r['a_title']} `{r['a'][:8]}` | {r['b_title']} `{r['b'][:8]}` | {r['title_sim']} "
            f"| {r['jaccard']} | {'yes' if r['a_matched'] else 'no'} | "
            f"{'yes' if r['b_matched'] else 'no'} | {r['why']} |"
        )
    for label, dups in (("recipe-table (A)", result.dup_a), ("cookt2 (B)", result.dup_b)):
        lines += [
            "",
            f"## Possible duplicates within {label} ({len(dups)})",
            "",
            "| kind | first | second | title sim | ingredient Jaccard |",
            "|---|---|---|---|---|",
        ]
        for d in dups:
            lines.append(
                f"| {d['kind']} | {d['x_title']} `{d['x'][:8]}` | {d['y_title']} `{d['y'][:8]}` "
                f"| {d['title_sim']} | {d['jaccard']} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------


def run(out: Path | None = None, force: bool = False, data_dir: Path | None = None) -> dict:
    from . import report as R
    from . import verify as V

    started = time.monotonic()
    paths = Paths(data_dir or settings.data_dir)
    out = (out or settings.db_path).resolve()
    images_root = out.parent / "images"
    run_at = db.now()

    holders = processes_holding(out) if out.exists() else []
    if holders:
        raise MigrationError(
            f"{out} is open by other processes (stop them first, e.g. scripts/demo.sh stop): "
            + "; ".join(holders)
        )
    reasons = user_data_reasons(out)
    if reasons and not force:
        raise MigrationError(
            f"refusing to replace {out}: it holds data the migration did not create:\n  - "
            + "\n  - ".join(reasons)
            + "\nre-run with --force to replace it anyway (the old DB and images are kept as "
            "*.replaced-<timestamp>)."
        )

    X.reset_intermediate(paths.work)
    a = X.extract_a(X.a_dsn(), paths.work)
    b = X.extract_b(
        paths.sources / "cookt2.db", paths.sources / "cookt2-images" / "recipes", paths.work
    )
    eligible_a = [r for r in a["records"] if r["household"] == HOUSEHOLD and not r["legacy_v1"]]
    result = match(eligible_a, b["records"])
    write_review(paths.review_md, result)
    plan = resolve(a["records"], b["records"], b["planning"], result, run_at)
    manifest = export_archive(plan, paths.work, paths.archive)

    tmp_db = out.parent / f".{out.name}.migrate-{os.getpid()}"
    staging = out.parent / f".images.migrate-{os.getpid()}"
    for leftover in (tmp_db, Path(f"{tmp_db}-wal"), Path(f"{tmp_db}-shm")):
        leftover.unlink(missing_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        conn = db.connect(tmp_db)
        db.init(conn)
        write_db(conn, plan, staging, paths.work, run_at)
        conn.close()

        report = R.build_report(plan, result, a["meta"], b["meta"], manifest, run_at)
        verification = V.verify(tmp_db, staging, paths, archive_manifest=manifest)
        report["verification"] = verification
        if not verification["ok"]:
            R.write(report, paths)
            failed = [c for c in verification["checks"] if not c["ok"]]
            raise MigrationError(
                "verification failed; nothing replaced:\n  - "
                + "\n  - ".join(f"{c['name']}: {c['detail']}" for c in failed)
            )

        conn = db.connect(tmp_db)
        report["preserved_tables"] = preserve_tables(out, conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('migration_completed_at', ?)",
            (db.now(),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('migration_run_at', ?)", (run_at,)
        )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()

        # Swap in: DB first, then images.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if out.exists() and force and reasons:
            shutil.copy2(out, out.parent / f"{out.name}.replaced-{stamp}")
        for suffix in ("-wal", "-shm"):
            Path(f"{out}{suffix}").unlink(missing_ok=True)
        os.replace(tmp_db, out)
        old_images = out.parent / f".images.old-{os.getpid()}"
        if images_root.exists():
            os.replace(images_root, old_images)
        os.replace(staging, images_root)
        if old_images.exists():
            if force and reasons:
                os.replace(old_images, out.parent / f"images.replaced-{stamp}")
            else:
                shutil.rmtree(old_images)
    finally:
        for leftover in (tmp_db, Path(f"{tmp_db}-wal"), Path(f"{tmp_db}-shm")):
            leftover.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)

    post = V.verify(out, images_root, paths, archive_manifest=manifest, check_sources=False)
    report["verification_after_swap"] = {
        "ok": post["ok"],
        "failed": [c for c in post["checks"] if not c["ok"]],
    }
    report["duration_seconds"] = round(time.monotonic() - started, 1)
    report["output"] = {"db": str(out), "images": str(images_root)}
    R.write(report, paths)
    if not post["ok"]:
        raise MigrationError("post-swap verification failed: " + json.dumps(post["checks"]))
    return report
