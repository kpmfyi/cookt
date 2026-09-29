# USDA FoodData Central data

Nutrition (spec §4.5) is computed against a **local** copy of USDA FoodData Central (public
domain, CC0). Only the two generic-food datasets are used; Branded and Survey (FNDDS) foods are
not loaded.

| Dataset | Release | URL | sha256 |
|---|---|---|---|
| Foundation Foods (CSV) | 2026-04-30 | https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2026-04-30.zip | `70457ee9d9342f43bda2010318c85f04210c689fdeb9cd2da4c513b0e8dbc655` |
| SR Legacy (CSV) | 2018-04 (final release) | https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip | `b80817294b8850530aaedf2e515c02593b1824f763a0ff356e5c2081643e6fd0` |

Links taken from https://fdc.nal.usda.gov/download-datasets on 2026-09-26.

## Load

```bash
mkdir -p data/fdc && cd data/fdc
curl -LO https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2026-04-30.zip
curl -LO https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip
sha256sum -c <<'EOF'
70457ee9d9342f43bda2010318c85f04210c689fdeb9cd2da4c513b0e8dbc655  FoodData_Central_foundation_food_csv_2026-04-30.zip
b80817294b8850530aaedf2e515c02593b1824f763a0ff356e5c2081643e6fd0  FoodData_Central_sr_legacy_food_csv_2018-04.zip
EOF
cd ../..
uv run python scripts/load_fdc.py            # into data/cookt.db (created if missing)
uv run python scripts/load_fdc.py --db other.db
```

The loader reads the zips directly (no extraction needed), replaces `fdc_food`, `fdc_portion`
and `fdc_food_fts` in one transaction (idempotent, ~1 s), and records what it loaded under
`meta.fdc_loaded`. Re-run it after the migration rebuilds `data/cookt.db`.

Loaded (2026-09-26): **8,262 foods** (7,793 SR Legacy + 469 Foundation; 91 Foundation foods have
no energy value and are never offered as matches) and **14,636 portions**.

## What is loaded

- `fdc_food`: only `data_type` `foundation_food` and `sr_legacy_food` (Foundation's per-sample
  `sub_sample_food` / `market_acquisition` / ... records are skipped). Per 100 g:
  - kcal: nutrient 1008 (Energy), falling back to 2048 then 2047 (Atwater specific / general
    energy, which is what most Foundation foods carry);
  - protein 1003; carbs 1005 (by difference, fallback 1050 by summation); fat 1004 (fallback
    1085); fiber 1079; sodium 1093 (mg).
- `fdc_portion`: `food_portion.csv` joined to `measure_unit.csv`. Foundation portions carry real
  units (cup, tablespoon, ...); SR Legacy portions all use unit 9999 `undetermined` and put the
  unit in `modifier` ("cup, chopped", "tbsp", "large", "clove"). `cookt.enrich.fdc.grams_for`
  parses both shapes.
- `fdc_food_fts`: FTS5 (`porter unicode61`) over description + category, rowid = fdc_id.

## Curated aliases

`backend/cookt/enrich/fdc_aliases.json` maps ~320 common recipe ingredient names (217 distinct
foods, SR Legacy raw/plain forms preferred) straight to an fdc_id, skipping the model pick.
`backend/tests/test_nutrition.py::test_alias_ids_exist_in_fdc_data` checks every id and its
recorded description against the loaded data whenever the zips are present.
