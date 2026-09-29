#!/usr/bin/env python3
"""URL import acceptance eval.

Acceptance: "20 real URLs from the top sites import with correct ingredient and step
counts". Ground truth is the curated recipe-table corpus, restored read-only into a
scratch Postgres (default ``postgresql://scratch:scratch@127.0.0.1:15499/src``, table
``recipe_artifacts``). Each selected URL is re-imported with
``cookt.extraction.pipeline.extract_from_url`` and its ingredient-line and step counts
are compared with the curated document.

Two verdicts per URL:
- exact:    ingredient count and step count both equal the curated document.
- tolerant: ingredient count within +/-1, and step count within max(1, 25%) or the step
            text matches (token F1 >= 0.9) - curated steps were sometimes split/merged by
            hand, so the same text in a different number of steps still counts.

Polite: sequential, at least ``--delay`` seconds between fetches, overall time budget.

    uv run python scripts/eval_urls.py            # pick 20, run, write docs/evals/url-import.*
    uv run python scripts/eval_urls.py --limit 3  # quick smoke run (does not overwrite)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cookt.extraction.pipeline import ExtractionError, extract_from_url  # noqa: E402

DEFAULT_DSN = "postgresql://scratch:scratch@127.0.0.1:15499/src"
# Target spread across the most-imported sites in the corpus (recipe-table has no Smitten
# Kitchen or Budget Bytes rows). Order matters only for the report.
SITE_QUOTAS = {
    "seriouseats.com": 3,
    "cooking.nytimes.com": 2,
    "sallysbakingaddiction.com": 2,
    "bonappetit.com": 2,
    "minimalistbaker.com": 2,
    "allrecipes.com": 2,
    "themediterraneandish.com": 1,
    "thespruceeats.com": 1,
    "delish.com": 1,
    "food52.com": 1,
    "foodnetwork.com": 1,
    "natashaskitchen.com": 1,
    "pinchofyum.com": 1,
}


def load_ground_truth(dsn: str) -> list[dict[str, Any]]:
    import psycopg

    query = """
        SELECT canonical_url, source_site, document
        FROM recipe_artifacts
        WHERE source_site = ANY(%s) AND archived_at IS NULL AND state = 'confirmed'
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SET default_transaction_read_only = on")
        cur.execute(query, (list(SITE_QUOTAS),))
        rows = cur.fetchall()
    return [{"url": url, "site": site, "document": document} for url, site, document in rows]


def select_recipes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic pick: per site, order by sha256(url) and take the quota."""

    picked: list[dict[str, Any]] = []
    for site, quota in SITE_QUOTAS.items():
        candidates = [
            row
            for row in rows
            if row["site"] == site and "/amp" not in row["url"] and not row["url"].endswith(".amp")
        ]
        candidates.sort(key=lambda row: hashlib.sha256(row["url"].encode()).hexdigest())
        picked.extend(candidates[:quota])
    return picked


def _ingredients(document: dict[str, Any]) -> list[str]:
    return [
        item.get("source_text", "")
        for section in document.get("ingredient_sections", [])
        for item in section.get("ingredients", [])
    ]


def _steps(document: dict[str, Any]) -> list[str]:
    return [
        step.get("text", "")
        for section in document.get("instruction_sections", [])
        for step in section.get("steps", [])
    ]


def _tokens(values: list[str]) -> Counter:
    text = unicodedata.normalize("NFKC", " ".join(values)).casefold()
    return Counter(re.sub(r"[^\w]+", " ", text).split())


def token_f1(expected: list[str], actual: list[str]) -> float:
    left, right = _tokens(expected), _tokens(actual)
    if not left and not right:
        return 1.0
    overlap = sum((left & right).values())
    if not overlap:
        return 0.0
    precision = overlap / sum(right.values())
    recall = overlap / sum(left.values())
    return round(2 * precision * recall / (precision + recall), 3)


def compare(curated: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    ci, ei = _ingredients(curated), _ingredients(extracted)
    cs, es = _steps(curated), _steps(extracted)
    ingredient_f1 = token_f1(ci, ei)
    step_f1 = token_f1(cs, es)
    step_slack = max(1, round(0.25 * len(cs)))
    ingredients_ok = abs(len(ci) - len(ei)) <= 1
    steps_ok = abs(len(cs) - len(es)) <= step_slack or step_f1 >= 0.9
    return {
        "curated_ingredients": len(ci),
        "extracted_ingredients": len(ei),
        "curated_steps": len(cs),
        "extracted_steps": len(es),
        "ingredient_text_f1": ingredient_f1,
        "step_text_f1": step_f1,
        "exact": len(ci) == len(ei) and len(cs) == len(es),
        "tolerant": ingredients_ok and steps_ok,
    }


def run(
    selected: list[dict[str, Any]], delay: float, budget_seconds: float
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    last_fetch = 0.0
    for index, row in enumerate(selected, 1):
        result: dict[str, Any] = {"url": row["url"], "site": row["site"]}
        if time.monotonic() - started > budget_seconds:
            result.update(status="skipped", error="time budget exhausted")
            results.append(result)
            continue
        wait = delay - (time.monotonic() - last_fetch)
        if wait > 0:
            time.sleep(wait)
        last_fetch = time.monotonic()
        t0 = time.monotonic()
        try:
            extraction = extract_from_url(row["url"])
        except ExtractionError as exc:
            result.update(status="failed", error_code=exc.code, error=str(exc)[:300])
        except Exception as exc:  # the eval records every failure instead of stopping
            result.update(status="failed", error_code="crash", error=repr(exc)[:300])
        else:
            extracted = extraction.document.model_dump(mode="json")
            result.update(
                status="ok",
                method=extraction.method,
                title=extraction.document.title,
                curated_title=row["document"].get("title"),
                coverage=extraction.coverage,
                has_nutrition=extraction.nutrition is not None,
                total_minutes=extraction.total_minutes,
                warnings=extraction.warnings,
                **compare(row["document"], extracted),
            )
        result["seconds"] = round(time.monotonic() - t0, 1)
        verdict = (
            "exact"
            if result.get("exact")
            else "tolerant"
            if result.get("tolerant")
            else result.get("error_code") or "mismatch"
        )
        print(f"[{index:2}/{len(selected)}] {verdict:<18} {result['seconds']:>6}s {row['url']}")
        results.append(result)
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [item for item in results if item["status"] == "ok"]
    return {
        "total": len(results),
        "imported": len(ok),
        "exact": sum(bool(item.get("exact")) for item in ok),
        "tolerant": sum(bool(item.get("tolerant")) for item in ok),
        "failed": sum(item["status"] == "failed" for item in results),
        "skipped": sum(item["status"] == "skipped" for item in results),
        "methods": dict(Counter(item.get("method") for item in ok)),
        "with_nutrition": sum(bool(item.get("has_nutrition")) for item in ok),
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    totals = payload["totals"]
    lines = [
        "# URL import eval",
        "",
        f"_Run {payload['run_at']} with `scripts/eval_urls.py`; ground truth = curated "
        "recipe-table documents (scratch restore, read-only)._",
        "",
        "Acceptance: 20 real URLs from the top sites import with correct ingredient and "
        "step counts.",
        "",
        "- **exact**: ingredient-line and step counts both equal the curated document.",
        "- **tolerant**: ingredients within +/-1, steps within max(1, 25%) or same step "
        "text (token F1 >= 0.9) split differently.",
        "",
        "## Totals",
        "",
        f"- URLs: {totals['total']} (imported {totals['imported']}, failed "
        f"{totals['failed']}, skipped {totals['skipped']})",
        f"- Exact: **{totals['exact']}/{totals['total']}**",
        f"- Within tolerance: **{totals['tolerant']}/{totals['total']}**",
        f"- Methods: {', '.join(f'{k}={v}' for k, v in totals['methods'].items()) or 'none'}",
        f"- Publisher nutrition surfaced: {totals['with_nutrition']}/{totals['imported']}",
        f"- Wall time: {payload['wall_seconds']:.0f}s",
        "",
        "## Per URL",
        "",
        "| # | Site | Result | Method | Ingredients (curated/got) | Steps (curated/got) | "
        "Ingr F1 | Step F1 | Time | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(payload["results"], 1):
        link = f"[{item['site']}]({item['url']})"
        if item["status"] == "ok":
            verdict = "exact" if item["exact"] else "tolerant" if item["tolerant"] else "MISMATCH"
            notes = "; ".join(w for w in item.get("warnings", []) if "structured" in w)[:80]
            lines.append(
                f"| {index} | {link} | {verdict} | {item['method']} | "
                f"{item['curated_ingredients']}/{item['extracted_ingredients']} | "
                f"{item['curated_steps']}/{item['extracted_steps']} | "
                f"{item['ingredient_text_f1']:.2f} | {item['step_text_f1']:.2f} | "
                f"{item['seconds']}s | {notes} |"
            )
        else:
            reason = f"{item.get('error_code', item['status'])}: {item.get('error', '')}"
            reason = reason.replace("|", "/")[:140]
            lines.append(
                f"| {index} | {link} | {item['status'].upper()} | - | - | - | - | - | "
                f"{item.get('seconds', '-')}s | {reason} |"
            )
    failures = Counter(
        item.get("error_code", item["status"])
        for item in payload["results"]
        if item["status"] != "ok"
    )
    if failures:
        explain = {
            "browser_challenge": "Cloudflare bot challenge (`cf-mitigated: challenge`); the "
            "importer reports it and never tries to bypass it. Paste/share the page instead.",
            "fetch_status": "the site's WAF refused the request (HTTP 403/429).",
            "restricted_page": "paywall/login gate with no structured recipe data.",
        }
        lines += ["", "## Failures by cause", ""]
        for code, count in failures.most_common():
            sites = sorted(
                {
                    item["site"]
                    for item in payload["results"]
                    if item.get("error_code", item["status"]) == code
                }
            )
            lines.append(
                f"- `{code}` x{count} ({', '.join(sites)}): {explain.get(code, '')}".rstrip(": ")
            )
    mismatches = [
        item for item in payload["results"] if item["status"] == "ok" and not item["exact"]
    ]
    if mismatches:
        lines += ["", "## Count differences", ""]
        for item in mismatches:
            lines.append(
                f"- {item['url']}: ingredients {item['curated_ingredients']} -> "
                f"{item['extracted_ingredients']}, steps {item['curated_steps']} -> "
                f"{item['extracted_steps']} (step text F1 {item['step_text_f1']:.2f})"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--limit", type=int, default=None, help="run only the first N picks")
    parser.add_argument("--delay", type=float, default=2.5, help="seconds between fetches")
    parser.add_argument("--budget-minutes", type=float, default=30)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs" / "evals")
    parser.add_argument(
        "--from-json", action="store_true", help="only re-render the markdown from the JSON"
    )
    args = parser.parse_args()

    if args.from_json:
        payload = json.loads((args.out_dir / "url-import.json").read_text(encoding="utf-8"))
        write_markdown(args.out_dir / "url-import.md", payload)
        return 0

    selected = select_recipes(load_ground_truth(args.dsn))
    if args.limit:
        selected = selected[: args.limit]
    print(f"selected {len(selected)} recipes across {len({r['site'] for r in selected})} sites")
    started = time.monotonic()
    results = run(selected, args.delay, args.budget_minutes * 60)
    payload = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "wall_seconds": round(time.monotonic() - started, 1),
        "criteria": {
            "exact": "ingredient and step counts equal curated",
            "tolerant": "ingredients +/-1 and (steps within max(1,25%) or step text F1>=0.9)",
        },
        "totals": summarize(results),
        "results": results,
    }
    print(json.dumps(payload["totals"], indent=2))
    if args.limit:
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "url-import.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_markdown(args.out_dir / "url-import.md", payload)
    print(f"wrote {args.out_dir / 'url-import.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
