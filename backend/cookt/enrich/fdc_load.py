"""Load USDA FoodData Central (Foundation + SR Legacy) CSV zips into SQLite.

Reads the zips in ``data/fdc/`` directly (no extraction needed) and replaces the
contents of ``fdc_food``, ``fdc_portion`` and ``fdc_food_fts`` in one
transaction, so it is idempotent and safe to re-run against a freshly migrated
DB. See ``docs/fdc.md`` for the dataset versions and checksums.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from collections.abc import Iterator
from pathlib import Path

from .. import db

# Nutrient ids (FDC nutrient.id). The first id found wins, in list order.
NUTRIENTS: dict[str, list[int]] = {
    # Energy (kcal); Foundation foods often only carry the Atwater energies.
    "kcal": [1008, 2048, 2047],
    "protein_g": [1003],
    # Carbohydrate by difference; by summation as a fallback (Foundation).
    "carbs_g": [1005, 1050],
    # Total lipid; NLEA total fat as a fallback (Foundation).
    "fat_g": [1004, 1085],
    "fiber_g": [1079],
    "sodium_mg": [1093],
}
WANTED_NUTRIENTS = {nid for ids in NUTRIENTS.values() for nid in ids}
# Only the aggregate food records, not Foundation's per-sample sub-records.
WANTED_DATA_TYPES = {"foundation_food", "sr_legacy_food"}


def _find_zips(fdc_dir: Path) -> list[Path]:
    zips = sorted(fdc_dir.glob("FoodData_Central_foundation_food_csv_*.zip"))[-1:]
    zips += sorted(fdc_dir.glob("FoodData_Central_sr_legacy_food_csv_*.zip"))[-1:]
    if len(zips) != 2:
        raise FileNotFoundError(
            f"expected the Foundation and SR Legacy CSV zips in {fdc_dir} (see docs/fdc.md)"
        )
    return zips


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    member = next((item for item in archive.namelist() if item.rsplit("/", 1)[-1] == name), None)
    if member is None:
        return iter(())
    handle = io.TextIOWrapper(archive.open(member), encoding="utf-8", newline="")
    return csv.DictReader(handle)


def _float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load(conn: sqlite3.Connection, fdc_dir: Path) -> dict[str, int]:
    """Replace the FDC tables with the contents of the zips in ``fdc_dir``."""
    db.init(conn)
    foods: list[tuple] = []
    portions: list[tuple] = []
    for zip_path in _find_zips(fdc_dir):
        with zipfile.ZipFile(zip_path) as archive:
            categories = {
                row["id"]: row["description"] for row in _rows(archive, "food_category.csv")
            }
            units = {row["id"]: row["name"] for row in _rows(archive, "measure_unit.csv")}
            food_meta: dict[int, tuple[str, str, str | None]] = {}
            for row in _rows(archive, "food.csv"):
                if row["data_type"] not in WANTED_DATA_TYPES:
                    continue
                food_meta[int(row["fdc_id"])] = (
                    row["data_type"],
                    row["description"].strip(),
                    categories.get(row.get("food_category_id") or ""),
                )
            values: dict[int, dict[int, float]] = {}
            for row in _rows(archive, "food_nutrient.csv"):
                if not (row.get("nutrient_id") or "").isdigit():
                    continue
                nutrient_id = int(row["nutrient_id"])
                if nutrient_id not in WANTED_NUTRIENTS:
                    continue
                fdc_id = int(row["fdc_id"])
                if fdc_id not in food_meta:
                    continue
                amount = _float(row["amount"])
                if amount is not None:
                    values.setdefault(fdc_id, {})[nutrient_id] = amount
            for fdc_id, (data_type, description, category) in food_meta.items():
                found = values.get(fdc_id, {})
                per100 = []
                for ids in NUTRIENTS.values():
                    per100.append(next((found[i] for i in ids if i in found), None))
                foods.append((fdc_id, data_type, description, category, *per100))
            for row in _rows(archive, "food_portion.csv"):
                if not (row.get("fdc_id") or "").isdigit():
                    continue
                fdc_id = int(row["fdc_id"])
                grams = _float(row["gram_weight"])
                if fdc_id not in food_meta or not grams or grams <= 0:
                    continue
                portions.append(
                    (
                        fdc_id,
                        _float(row["amount"]),
                        units.get(row["measure_unit_id"] or "", None),
                        (row.get("modifier") or "").strip() or None,
                        (row.get("portion_description") or "").strip() or None,
                        grams,
                    )
                )
    with db.tx(conn):
        conn.execute("DELETE FROM fdc_food")
        conn.execute("DELETE FROM fdc_portion")
        conn.execute("DELETE FROM fdc_food_fts")
        conn.executemany(
            "INSERT INTO fdc_food(fdc_id, data_type, description, category, kcal, protein_g,"
            " carbs_g, fat_g, fiber_g, sodium_mg) VALUES (?,?,?,?,?,?,?,?,?,?)",
            foods,
        )
        conn.executemany(
            "INSERT INTO fdc_portion(fdc_id, amount, unit, modifier, description, gram_weight)"
            " VALUES (?,?,?,?,?,?)",
            portions,
        )
        conn.execute(
            "INSERT INTO fdc_food_fts(rowid, description, category)"
            " SELECT fdc_id, description, coalesce(category, '') FROM fdc_food"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('fdc_loaded', ?)",
            (db.dumps({"at": db.now(), "zips": [p.name for p in _find_zips(fdc_dir)]}),),
        )
    return {
        "foods": len(foods),
        "portions": len(portions),
        "foods_without_kcal": sum(1 for food in foods if food[4] is None),
    }


def is_loaded(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM fdc_food LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False
