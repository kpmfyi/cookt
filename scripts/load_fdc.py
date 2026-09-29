"""Load USDA FoodData Central (Foundation + SR Legacy) into a cookt SQLite DB.

    uv run python scripts/load_fdc.py [--db data/cookt.db] [--fdc-dir data/fdc]

Idempotent: replaces fdc_food / fdc_portion / fdc_food_fts wholesale. Creates the
DB (via db.init) if it does not exist yet, so it works before or after the
migration rebuilds data/cookt.db. Download the zips first (URLs in docs/fdc.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cookt import db  # noqa: E402
from cookt.enrich.fdc_load import load  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "cookt.db")
    parser.add_argument("--fdc-dir", type=Path, default=ROOT / "data" / "fdc")
    args = parser.parse_args()
    started = time.monotonic()
    conn = db.connect(args.db)
    db.init(conn)
    counts = load(conn, args.fdc_dir)
    conn.close()
    print(
        f"loaded {counts['foods']} foods ({counts['foods_without_kcal']} without kcal), "
        f"{counts['portions']} portions into {args.db} in {time.monotonic() - started:.1f}s"
    )


if __name__ == "__main__":
    main()
