"""Step 3: match A<->B (canonical URL, then title + ingredient Jaccard) and find duplicates.

Only rule 1 / rule 2 (and the empty-shell rule) auto-merge. Everything else that looks
plausible is written to the review report and stays a separate recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from . import normalize as N

TITLE_MATCH = 0.85  # rule 2: normalized titles (near-)identical ...
JACCARD_MATCH = 0.7  # ... and ingredient-set Jaccard >= 0.7
REVIEW_JACCARD_MIN = 0.4
REVIEW_TITLE_MIN = 0.75


@dataclass
class Info:
    source: str
    source_id: str
    title: str
    norm_title: str
    ingredients: set[str]
    canonical_url: str | None
    host: str | None
    empty: bool


@dataclass
class Pair:
    a: str
    b: str
    rule: str
    title_sim: float
    jaccard: float
    note: str = ""


@dataclass
class MatchResult:
    pairs: list[Pair] = field(default_factory=list)
    review: list[dict[str, Any]] = field(default_factory=list)
    dup_a: list[dict[str, Any]] = field(default_factory=list)
    dup_b: list[dict[str, Any]] = field(default_factory=list)


def info_for(record: dict[str, Any]) -> Info:
    names: list[str] = []
    if record["source"] == "A":
        names = N.document_ingredient_names(record["document"])
    else:
        names = [i["name"] for i in record["ingredients"] if i.get("name")]
    canon = record.get("canonical_url")
    return Info(
        source=record["source"],
        source_id=record["source_id"],
        title=record["title"],
        norm_title=N.normalize_title(record["title"]),
        ingredients=N.ingredient_set(names),
        canonical_url=canon,
        host=N.site_from_url(canon) if canon else None,
        empty=(
            record["source"] == "B" and not record["ingredients"] and not record["instructions"]
        ),
    )


def _scores(x: Info, y: Info) -> tuple[float, float]:
    return N.title_similarity(x.norm_title, y.norm_title), N.jaccard(x.ingredients, y.ingredients)


def _hosts_conflict(x: Info, y: Info) -> bool:
    return bool(x.host and y.host and x.host != y.host)


def match(a_records: list[dict[str, Any]], b_records: list[dict[str, Any]]) -> MatchResult:
    """a_records: A recipes eligible for matching (demo/inbox already excluded)."""

    result = MatchResult()
    a_info = {r["source_id"]: info_for(r) for r in a_records}
    b_info = {r["source_id"]: info_for(r) for r in b_records}
    matched_a: set[str] = set()
    matched_b: set[str] = set()

    # Rule 1: canonical URL.
    by_url: dict[str, tuple[list[str], list[str]]] = {}
    for key, info in a_info.items():
        if info.canonical_url:
            by_url.setdefault(info.canonical_url, ([], []))[0].append(key)
    for key, info in b_info.items():
        if info.canonical_url:
            by_url.setdefault(info.canonical_url, ([], []))[1].append(key)
    for url in sorted(by_url):
        a_ids, b_ids = by_url[url]
        if not a_ids or not b_ids:
            continue
        options = []
        for a_id in a_ids:
            for b_id in b_ids:
                title, jac = _scores(a_info[a_id], b_info[b_id])
                options.append((-(title + jac), a_id, b_id, title, jac))
        for _, a_id, b_id, title, jac in sorted(options):
            if a_id in matched_a or b_id in matched_b:
                continue
            note = ""
            if jac < 0.3 and not b_info[b_id].empty:
                note = "same URL but low ingredient overlap"
            result.pairs.append(Pair(a_id, b_id, "canonical_url", title, jac, note))
            matched_a.add(a_id)
            matched_b.add(b_id)

    # Rule 2: normalized title + ingredient Jaccard >= 0.7 (never across two different sites).
    scored: dict[tuple[str, str], tuple[float, float]] = {}
    for a_id, ai in a_info.items():
        for b_id, bi in b_info.items():
            scored[(a_id, b_id)] = _scores(ai, bi)
    options = []
    for (a_id, b_id), (title, jac) in scored.items():
        if a_id in matched_a or b_id in matched_b:
            continue
        if _hosts_conflict(a_info[a_id], b_info[b_id]):
            continue
        if title >= TITLE_MATCH and jac >= JACCARD_MATCH:
            options.append((-(title + jac), a_id, b_id, title, jac))
    for _, a_id, b_id, title, jac in sorted(options):
        if a_id in matched_a or b_id in matched_b:
            continue
        result.pairs.append(Pair(a_id, b_id, "title_ingredients", title, jac))
        matched_a.add(a_id)
        matched_b.add(b_id)

    # Rule 2b: B is an empty shell (no ingredients, no steps) with the exact same normalized
    # title as an unmatched A recipe -> the same recipe; B contributes only ids/tags/flags.
    for b_id, bi in sorted(b_info.items()):
        if b_id in matched_b or not bi.empty:
            continue
        hits = [
            a_id
            for a_id, ai in sorted(a_info.items())
            if a_id not in matched_a
            and ai.norm_title == bi.norm_title
            and not _hosts_conflict(ai, bi)
        ]
        if len(hits) == 1:
            result.pairs.append(
                Pair(
                    hits[0],
                    b_id,
                    "title_exact_empty_b",
                    1.0,
                    0.0,
                    "B row has no ingredients and no instructions",
                )
            )
            matched_a.add(hits[0])
            matched_b.add(b_id)

    # Review candidates: plausible but not auto-merged. At least one side unmatched.
    for (a_id, b_id), (title, jac) in sorted(scored.items()):
        if a_id in matched_a and b_id in matched_b:
            continue
        plausible = title >= REVIEW_TITLE_MIN or (jac >= REVIEW_JACCARD_MIN and title >= 0.5)
        if not plausible:
            continue
        reasons = []
        if title >= REVIEW_TITLE_MIN:
            reasons.append("similar title")
        if jac >= REVIEW_JACCARD_MIN:
            reasons.append("ingredient overlap")
        if _hosts_conflict(a_info[a_id], b_info[b_id]):
            reasons.append("different source sites")
        result.review.append(
            {
                "a": a_id,
                "b": b_id,
                "a_title": a_info[a_id].title,
                "b_title": b_info[b_id].title,
                "a_url": a_info[a_id].canonical_url,
                "b_url": b_info[b_id].canonical_url,
                "title_sim": round(title, 3),
                "jaccard": round(jac, 3),
                "a_matched": a_id in matched_a,
                "b_matched": b_id in matched_b,
                "why": ", ".join(reasons),
            }
        )
    result.review.sort(key=lambda r: (-(r["title_sim"] + r["jaccard"]), r["a_title"]))
    result.dup_a = within_source_duplicates(list(a_info.values()))
    result.dup_b = within_source_duplicates(list(b_info.values()))
    return result


def within_source_duplicates(infos: list[Info]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for x, y in combinations(sorted(infos, key=lambda i: i.source_id), 2):
        same_url = bool(x.canonical_url and x.canonical_url == y.canonical_url)
        title, jac = _scores(x, y)
        if same_url:
            kind = "same canonical URL"
        elif title >= TITLE_MATCH and jac >= JACCARD_MATCH:
            kind = "title + ingredients (>= 0.7)"
        elif title >= TITLE_MATCH and (x.empty or y.empty):
            kind = "same title, one side empty"
        elif title >= REVIEW_TITLE_MIN or (jac >= REVIEW_JACCARD_MIN and title >= 0.5):
            kind = "possible"
        else:
            continue
        found.append(
            {
                "x": x.source_id,
                "y": y.source_id,
                "x_title": x.title,
                "y_title": y.title,
                "x_url": x.canonical_url,
                "y_url": y.canonical_url,
                "title_sim": round(title, 3),
                "jaccard": round(jac, 3),
                "kind": kind,
            }
        )
    order = {
        "same canonical URL": 0,
        "title + ingredients (>= 0.7)": 1,
        "same title, one side empty": 2,
        "possible": 3,
    }
    found.sort(key=lambda d: (order[d["kind"]], -(d["title_sim"] + d["jaccard"])))
    return found
