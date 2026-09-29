"""Resumable enrichment backfill.

    uv run python -m cookt.enrich backfill [--steps tags,features,embedding,nutrition]
                                           [--limit N] [--recipe ID] [--force]
    uv run python -m cookt.enrich status

Checkpoints are the `enrichment_state` rows (one per recipe × step at a prompt
version), so a killed run resumes where it stopped and a prompt-version bump
re-runs everything. Model calls serialize on data/llm.lock (see cookt.llm).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .. import db
from . import STEP_VERSIONS, run_step

log = logging.getLogger("cookt.enrich")


def pending(conn, step: str, force: bool, recipe: str | None) -> list[str]:
    version = STEP_VERSIONS[step]
    if recipe:
        from ..recipes import resolve_id

        return [resolve_id(conn, recipe)]  # an id, slug or old alias
    rows = conn.execute(
        "SELECT r.id FROM recipes r LEFT JOIN enrichment_state e "
        "ON e.recipe_id = r.id AND e.step = ?"
        " WHERE r.archived_at IS NULL"
        " AND (? OR e.version IS NULL OR e.version != ? OR e.error IS NOT NULL)"
        " ORDER BY r.created_at DESC",
        (step, int(force), version),
    ).fetchall()
    return [r["id"] for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(prog="cookt.enrich")
    sub = parser.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill")
    bf.add_argument("--steps", default="tags,features,embedding,nutrition")
    bf.add_argument("--limit", type=int, default=0)
    bf.add_argument("--recipe")
    bf.add_argument("--force", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = db.connect()
    db.init(conn)
    if args.cmd == "status":
        total = conn.execute("SELECT COUNT(*) FROM recipes WHERE archived_at IS NULL").fetchone()[0]
        for step, version in STEP_VERSIONS.items():
            done = conn.execute(
                "SELECT COUNT(*) FROM enrichment_state e JOIN recipes r ON r.id = e.recipe_id "
                "WHERE e.step = ? AND e.version = ? AND e.error IS NULL "
                "AND r.archived_at IS NULL",
                (step, version),
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM enrichment_state WHERE step = ? AND error IS NOT NULL",
                (step,),
            ).fetchone()[0]
            print(f"{step:10s} {done}/{total} done, {errors} errors ({version})")
        return 0
    for step in [s.strip() for s in args.steps.split(",") if s.strip()]:
        ids = pending(conn, step, args.force, args.recipe)
        if args.limit:
            ids = ids[: args.limit]
        log.info("step %s: %d recipes to process", step, len(ids))
        started = time.monotonic()
        for n, rid in enumerate(ids, 1):
            ok = run_step(conn, step, rid)
            if n % 10 == 0 or n == len(ids):
                rate = (time.monotonic() - started) / n
                log.info(
                    "step %s: %d/%d (%.1fs/recipe)%s",
                    step,
                    n,
                    len(ids),
                    rate,
                    "" if ok else " [last failed]",
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
