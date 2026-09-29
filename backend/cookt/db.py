"""SQLite storage: one file, WAL, stdlib driver.

The recipe document (RecipeDocumentV2, lossless) lives as JSON on the row;
everything derived (times, servings, tags, nutrition, embeddings, features)
sits in side tables so it can be recomputed or reverted without touching the
document text.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  document TEXT NOT NULL,              -- RecipeDocumentV2 JSON, lossless display text
  source_url TEXT,
  canonical_url TEXT,
  source_site TEXT,
  source_author TEXT,
  credit TEXT,                         -- author/credit derived from owner title prefixes etc.
  description TEXT,
  prep_minutes INTEGER,
  cook_minutes INTEGER,
  total_minutes INTEGER,
  servings REAL,
  image_id TEXT,
  -- origin: migrated_a / migrated_b / merged / url / paste / photo / ...
  origin TEXT NOT NULL DEFAULT 'manual',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);
CREATE INDEX IF NOT EXISTS recipes_canonical_url ON recipes(canonical_url);

-- Old ids from recipe-table (A) and cookt2 (B) so old links and assistant refs resolve.
CREATE TABLE IF NOT EXISTS recipe_aliases (
  alias TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  system TEXT NOT NULL                 -- 'recipe-table' | 'cookt2' | 'slug'
);

CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  recipe_id TEXT REFERENCES recipes(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'primary', -- primary | step | import
  step_id TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,                  -- relative to data/images
  thumb_path TEXT,
  media_type TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  sha256 TEXT NOT NULL,
  source_url TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS images_recipe ON images(recipe_id);

CREATE TABLE IF NOT EXISTS revisions (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  snapshot TEXT NOT NULL,              -- JSON of the editable recipe fields before the change
  reason TEXT NOT NULL,
  person_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS revisions_recipe ON revisions(recipe_id, version);

-- Tags: controlled facets (cuisine/course/protein/diet), equipment, and free-form personal tags.
CREATE TABLE IF NOT EXISTS recipe_tags (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                  -- cuisine | course | protein | diet | equipment | personal
  value TEXT NOT NULL,
  source TEXT NOT NULL,                -- manual | migrated | auto
  confidence REAL,
  evidence TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, kind, value)
);
CREATE INDEX IF NOT EXISTS recipe_tags_kind ON recipe_tags(kind, value);

-- A reverted auto tag is never re-suggested for that recipe.
CREATE TABLE IF NOT EXISTS rejected_tags (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, kind, value)
);

-- The Changes feed: every automatic change, revertible.
CREATE TABLE IF NOT EXISTS changes (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  field TEXT NOT NULL,                 -- e.g. tag:cuisine, prep_minutes, servings
  before TEXT,                         -- JSON
  after TEXT,                          -- JSON
  evidence TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  reverted_at TEXT
);
CREATE INDEX IF NOT EXISTS changes_recipe ON changes(recipe_id);
CREATE INDEX IF NOT EXISTS changes_created ON changes(created_at);

CREATE TABLE IF NOT EXISTS favorites (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL DEFAULT '',  -- '' = household (migrated, no person)
  created_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, person_id)
);

CREATE TABLE IF NOT EXISTS cook_log (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  person_id TEXT,
  cooked_on TEXT NOT NULL,             -- YYYY-MM-DD
  make_again INTEGER,                  -- 1 / 0 / NULL
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cook_log_recipe ON cook_log(recipe_id, cooked_on);

-- "For next time" notes, shown at the top of the method and on the first cook ticket.
CREATE TABLE IF NOT EXISTS next_time_notes (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  person_id TEXT,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

-- Import inbox: everything imported lands here first.
CREATE TABLE IF NOT EXISTS inbox (
  id TEXT PRIMARY KEY,
  -- kind: url | paste | photos | handwritten | social | paprika | share | proposal | rescrape
  kind TEXT NOT NULL,
  -- status: queued | fetching | extracting | ready | failed | saved | dismissed
  status TEXT NOT NULL,
  input TEXT NOT NULL,                 -- JSON
  draft TEXT,                 -- JSON: {document, meta, uncertain_lines, source_nutrition, ...}
  error TEXT,
  duplicate_of TEXT,                   -- JSON list of {recipe_id, reason, score}
  recipe_id TEXT,                      -- set once saved
  person_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,                -- queued | running | done | failed
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS embeddings (
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL
);

-- LLM feature profile used by deterministic pairing (computed at enrichment time only).
CREATE TABLE IF NOT EXISTS recipe_features (
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  features TEXT NOT NULL,     -- JSON {richness, acidity, texture, weight, dish_family, ...}
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nutrition (
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  source TEXT NOT NULL,                -- publisher | computed
  per_serving TEXT NOT NULL,           -- JSON {kcal, protein_g, carbs_g, fat_g, fiber_g, sodium_mg}
  confidence TEXT,                     -- high | medium | low
  breakdown TEXT,                      -- JSON per ingredient line
  servings REAL,
  computed_at TEXT NOT NULL
);

-- Publisher JSON-LD nutrition block kept verbatim.
CREATE TABLE IF NOT EXISTS source_nutrition (
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nutrition_overrides (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  ingredient_id TEXT NOT NULL,
  fdc_id INTEGER,                      -- NULL = exclude this line
  grams REAL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, ingredient_id)
);

-- LLM ingredient parse cache: source line -> quantity/unit/food + chosen FDC match.
CREATE TABLE IF NOT EXISTS ingredient_parse_cache (
  source_text TEXT PRIMARY KEY,
  parsed TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL
);

-- USDA FoodData Central (Foundation + SR Legacy), loaded by scripts/load_fdc.py.
CREATE TABLE IF NOT EXISTS fdc_food (
  fdc_id INTEGER PRIMARY KEY,
  data_type TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT,
  kcal REAL, protein_g REAL, carbs_g REAL, fat_g REAL, fiber_g REAL, sodium_mg REAL  -- per 100 g
);
CREATE TABLE IF NOT EXISTS fdc_portion (
  fdc_id INTEGER NOT NULL,
  amount REAL,
  unit TEXT,                           -- measure unit name, e.g. cup, tbsp, undetermined
  modifier TEXT,
  description TEXT,
  gram_weight REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS fdc_portion_food ON fdc_portion(fdc_id);
-- Full-text index over FDC descriptions (rowid = fdc_id), rebuilt by scripts/load_fdc.py.
CREATE VIRTUAL TABLE IF NOT EXISTS fdc_food_fts USING fts5(
  description, category, tokenize = 'porter unicode61'
);

-- Planning.
CREATE TABLE IF NOT EXISTS plan_entries (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,                   -- YYYY-MM-DD
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  servings REAL,
  position INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plan_day ON plan_entries(day);

CREATE TABLE IF NOT EXISTS shopping_items (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  quantity TEXT,
  unit TEXT,
  section TEXT,
  sources TEXT NOT NULL DEFAULT '[]',  -- JSON [{recipe_id, title, text}]
  checked INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS store_section_cache (
  name TEXT PRIMARY KEY,
  section TEXT NOT NULL,
  model TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pantry_staples (
  name TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- Migration accounting: every source recipe, what happened to it and why.
CREATE TABLE IF NOT EXISTS migration_ledger (
  source TEXT NOT NULL,                -- A | B
  source_id TEXT NOT NULL,
  title TEXT,
  outcome TEXT NOT NULL,               -- migrated | merged | dropped | inbox
  recipe_id TEXT,
  reason TEXT,
  PRIMARY KEY (source, source_id)
);

-- Cleaned ingredient names (derived; the document's display text is never changed).
CREATE TABLE IF NOT EXISTS ingredient_names (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  ingredient_id TEXT NOT NULL,
  clean_name TEXT NOT NULL,
  PRIMARY KEY (recipe_id, ingredient_id)
);

-- Copy-edit proposals (cookt.copyedit): model-suggested text edits awaiting a person's decision.
CREATE TABLE IF NOT EXISTS copyedit_proposals (
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  base_version INTEGER NOT NULL,       -- recipe version the edits were made against
  -- status: pending | nochange | error | approved | rejected | kept | deleted
  status TEXT NOT NULL,
  score INTEGER,                       -- 1-5 quality of the original text
  summary TEXT,
  -- edits: JSON [{key, kind, op, before, after, reason, numbers_changed}]
  edits TEXT NOT NULL DEFAULT '[]',
  flags TEXT NOT NULL DEFAULT '[]',    -- JSON e.g. ["empty"], ["exemplar"]
  error TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decision TEXT                        -- JSON {accepted, revision_id}
);

-- Web Push (cookt.push): one row per device that allowed notifications.
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint TEXT PRIMARY KEY,           -- the push service URL for this device
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  user_agent TEXT,
  created_at TEXT NOT NULL
);

-- Cook-timer alerts waiting to be pushed; the page replaces its set whenever timers change.
CREATE TABLE IF NOT EXISTS push_timers (
  endpoint TEXT NOT NULL REFERENCES push_subscriptions(endpoint) ON DELETE CASCADE,
  timer_id TEXT NOT NULL,
  ends_at REAL NOT NULL,               -- unix seconds
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at TEXT,
  PRIMARY KEY (endpoint, timer_id)
);

-- Enrichment checkpoints: which step ran for a recipe at which prompt/model version.
CREATE TABLE IF NOT EXISTS enrichment_state (
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  step TEXT NOT NULL,                  -- tags | features | embedding | nutrition
  version TEXT NOT NULL,
  error TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, step)
);
"""

_local = threading.local()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def get_conn() -> sqlite3.Connection:
    """One connection per thread for the app process."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | bytes | None) -> Any:
    return None if value is None else json.loads(value)
