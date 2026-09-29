"""Acceptance eval §7 #9: computed vs publisher nutrition on 15 real recipes.

    uv run python scripts/eval_nutrition.py [--n 15] [--refetch]

Selection (fixed before any computation, no per-URL tuning): source URLs of the
migrated catalog (scratch Postgres ``recipe_artifacts.canonical_url``), sorted by
site then URL, taken round-robin across sites (at most 2 per site); the first N
pages whose JSON-LD Recipe has ``nutrition.calories``, a numeric yield, and
ingredients are used. Pages are fetched sequentially, >= 2.5 s apart, and cached
in ``data/eval/nutrition_pages/`` so re-runs don't refetch.

Nutrition is computed WITHOUT the publisher block through the real pipeline
(local model + local FDC) in a separate DB, ``data/eval/nutrition_eval.db``
(never ``data/cookt.db``). Writes ``docs/evals/nutrition.md`` and ``.json``.
Pass = computed kcal/serving within ±20 % of the publisher's.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cookt import db  # noqa: E402
from cookt.enrich import nutrition  # noqa: E402
from cookt.enrich.fdc_load import is_loaded, load  # noqa: E402

EVAL_DIR = ROOT / "data" / "eval"
PAGES = EVAL_DIR / "nutrition_pages"
EVAL_DB = EVAL_DIR / "nutrition_eval.db"
OUT_MD = ROOT / "docs" / "evals" / "nutrition.md"
OUT_JSON = ROOT / "docs" / "evals" / "nutrition.json"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
PG = "host=127.0.0.1 port=15499 user=scratch password=scratch dbname=src"
TOLERANCE = 20.0
PER_SITE = 2


def catalog_urls() -> list[str]:
    import psycopg

    with psycopg.connect(PG) as conn:
        rows = conn.execute(
            "SELECT DISTINCT canonical_url, source_site FROM recipe_artifacts"
            " WHERE canonical_url LIKE 'http%' AND archived_at IS NULL"
        ).fetchall()
    by_site: dict[str, list[str]] = defaultdict(list)
    for url, site in sorted(rows, key=lambda r: (r[1] or "", r[0])):
        by_site[site or ""].append(url)
    ordered: list[str] = []
    for round_index in range(PER_SITE):
        for site in sorted(by_site):
            if round_index < len(by_site[site]):
                ordered.append(by_site[site][round_index])
    return ordered


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def recipe_jsonld(page: str) -> dict | None:
    for match in re.finditer(
        r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, flags=re.S | re.I
    ):
        try:
            data = json.loads(match.group(1).strip(), strict=False)
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Recipe" in kinds and node.get("recipeIngredient"):
                return node
    return None


def yield_number(value) -> float | None:
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, int | float) and item > 0:
            return float(item)
        match = re.search(r"\d+(?:\.\d+)?", str(item or ""))
        if match and float(match.group(0)) > 0:
            return float(match.group(0))
    return None


def fetch(url: str, refetch: bool, last_fetch: list[float]) -> str | None:
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = PAGES / f"{key}.html"
    failed = path.with_suffix(".failed")
    if path.exists() and not refetch:
        return path.read_text(errors="replace")
    if failed.exists() and not refetch:
        print(f"  (cached failure) {failed.read_text().strip()}")
        return None
    wait = 2.5 - (time.monotonic() - last_fetch[0])
    if wait > 0:
        time.sleep(wait)
    last_fetch[0] = time.monotonic()
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
            timeout=25,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        print(f"  fetch failed: {exc}")
        return None
    PAGES.mkdir(parents=True, exist_ok=True)
    if response.status_code != 200:
        print(f"  HTTP {response.status_code}")
        failed.write_text(f"HTTP {response.status_code}")
        return None
    path.write_text(response.text)
    return response.text


def select(n: int, refetch: bool) -> tuple[list[dict], list[dict]]:
    chosen: list[dict] = []
    skipped: list[dict] = []
    last_fetch = [0.0]
    for url in catalog_urls():
        if len(chosen) >= n:
            break
        print(f"- {url}")
        page = fetch(url, refetch, last_fetch)
        if page is None:
            skipped.append({"url": url, "reason": "fetch failed"})
            continue
        node = recipe_jsonld(page)
        if node is None:
            skipped.append({"url": url, "reason": "no JSON-LD Recipe"})
            continue
        block = node.get("nutrition") if isinstance(node.get("nutrition"), dict) else None
        servings = yield_number(node.get("recipeYield"))
        publisher = nutrition.parse_publisher_nutrition(block or {}, servings)
        if publisher is None:
            skipped.append({"url": url, "reason": "no nutrition.calories"})
            continue
        if servings is None:
            skipped.append({"url": url, "reason": "no numeric recipeYield"})
            continue
        ingredients = [
            html.unescape(re.sub(r"<[^>]+>", "", str(line))).strip()
            for line in node["recipeIngredient"]
        ]
        ingredients = [line for line in ingredients if line]
        chosen.append(
            {
                "url": url,
                "title": html.unescape(str(node.get("name") or url))[:200],
                "servings": servings,
                "ingredients": ingredients,
                "publisher": publisher,
                "publisher_block": block,
            }
        )
    return chosen, skipped


def insert_recipe(conn, item: dict) -> str:
    recipe_id = "eval-" + hashlib.sha256(item["url"].encode()).hexdigest()[:12]
    document = {
        "schema_version": 2,
        "title": item["title"],
        "ingredient_sections": [
            {
                "id": "ing",
                "ingredients": [
                    {"id": f"i{n}", "source_text": text[:500]}
                    for n, text in enumerate(item["ingredients"], 1)
                ],
            }
        ],
        "instruction_sections": [{"id": "ins", "steps": [{"id": "s1", "text": "n/a"}]}],
    }
    now = db.now()
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    conn.execute(
        "INSERT INTO recipes(id, slug, title, document, source_url, servings, origin,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            recipe_id,
            recipe_id,
            item["title"],
            json.dumps(document),
            item["url"],
            item["servings"],
            "eval",
            now,
            now,
        ),
    )
    return recipe_id


def line_summary(line: dict) -> str:
    if line["excluded"]:
        return f"✗ {line['source_text']} — excluded: {line['reason']}"
    return (
        f"✓ {line['source_text']} → {line['food']} [{line['fdc_id']}], {line['grams']:g} g, "
        f"{line['kcal']} kcal ({line['confidence']}; {line['grams_method']})"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--refetch", action="store_true")
    parser.add_argument(
        "--tag", default="", help="write docs/evals/nutrition-<tag>.{md,json} (supplementary runs)"
    )
    args = parser.parse_args()
    out_md, out_json = OUT_MD, OUT_JSON
    if args.tag:
        out_md = OUT_MD.with_name(f"nutrition-{args.tag}.md")
        out_json = OUT_JSON.with_name(f"nutrition-{args.tag}.json")

    chosen, skipped = select(args.n, args.refetch)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(EVAL_DB)
    db.init(conn)
    if not is_loaded(conn):
        load(conn, ROOT / "data" / "fdc")

    results = []
    for item in chosen:
        recipe_id = insert_recipe(conn, item)
        started = time.monotonic()
        computed = nutrition.compute_nutrition(conn, recipe_id)
        elapsed = time.monotonic() - started
        pub_kcal = item["publisher"]["kcal"]
        comp_kcal = computed["per_serving"]["kcal"]
        diff = 100.0 * (comp_kcal - pub_kcal) / pub_kcal
        passed = abs(diff) <= TOLERANCE
        print(
            f"{'PASS' if passed else 'FAIL'} {diff:+6.1f}%  pub {pub_kcal:>5} comp {comp_kcal:>5}"
            f"  mass {computed['mass_matched_pct']}% {computed['confidence']}  "
            f"{item['title'][:50]} ({elapsed:.0f}s)"
        )
        results.append(
            {
                "url": item["url"],
                "title": item["title"],
                "servings": item["servings"],
                "publisher": item["publisher"],
                "computed": computed["per_serving"],
                "kcal_diff_pct": round(diff, 1),
                "pass": passed,
                "mass_matched_pct": computed["mass_matched_pct"],
                "confidence": computed["confidence"],
                "breakdown": computed["breakdown"],
            }
        )
    passes = sum(1 for r in results if r["pass"])
    report = {
        "generated_at": db.now(),
        "version": nutrition.current_version(),
        "tolerance_pct": TOLERANCE,
        "passed": passes,
        "total": len(results),
        "results": results,
        "skipped_candidates": skipped,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    out_md.write_text(render_md(report, out_json.name))
    print(f"\n{passes}/{len(results)} within ±{TOLERANCE:g}%  -> {out_md}")


def render_md(report: dict, json_name: str = "nutrition.json") -> str:
    out = [
        "# Nutrition eval (spec §7 #9)",
        "",
        f"Generated {report['generated_at']} by `scripts/eval_nutrition.py`"
        f" (pipeline `{report['version']}`).",
        "",
        f"**Result: {report['passed']}/{report['total']} recipes within ±"
        f"{report['tolerance_pct']:g}% of the publisher's kcal/serving** (target ≥ 12/15).",
        "",
        "Method: catalog source URLs (scratch Postgres `recipe_artifacts`), round-robin by site"
        f" (≤ {PER_SITE}/site), first {report['total']} with JSON-LD `nutrition.calories` and a"
        " numeric `recipeYield`. Ingredients + servings come from the JSON-LD; nutrition is"
        " computed without the publisher block (local model parse + local USDA FDC) in"
        " `data/eval/nutrition_eval.db`. Per-line detail for every recipe is in"
        f" [`{json_name}`]({json_name}).",
        "",
        "| # | Recipe | Servings | Publisher kcal | Computed kcal | Diff | Pass | Mass matched |"
        " Badge |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, r in enumerate(report["results"], 1):
        out.append(
            f"| {n} | [{r['title'][:60]}]({r['url']}) | {r['servings']:g} |"
            f" {r['publisher']['kcal']} | {r['computed']['kcal']} | {r['kcal_diff_pct']:+.1f}% |"
            f" {'✅' if r['pass'] else '❌'} | {r['mass_matched_pct']}% | {r['confidence']} |"
        )
    out += ["", "## Macros (publisher → computed, per serving)", ""]
    out.append("| # | Protein g | Carbs g | Fat g | Fiber g | Sodium mg |")
    out.append("|---|---|---|---|---|---|")
    for n, r in enumerate(report["results"], 1):
        cells = []
        for key in ("protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg"):
            cells.append(f"{r['publisher'].get(key)} → {r['computed'].get(key)}")
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    out += ["", "## Per-ingredient breakdown", ""]
    for n, r in enumerate(report["results"], 1):
        out.append(
            f"### {n}. {r['title'][:80]} ({'pass' if r['pass'] else 'FAIL'},"
            f" {r['kcal_diff_pct']:+.1f}%)"
        )
        out.append("")
        for line in r["breakdown"]:
            out.append(f"- {line_summary(line)}")
        out.append("")
    if report["skipped_candidates"]:
        out += ["## Candidates skipped during selection", ""]
        for s in report["skipped_candidates"]:
            out.append(f"- {s['url']} — {s['reason']}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
