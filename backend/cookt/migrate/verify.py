"""Step 8: verification. Every check is recorded; any failure makes the migration fail loudly."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..document import RecipeDocumentV2
from . import extract as X

# Exact expected counts (spec §5 step 8). Changing the match rules or merging a review
# candidate must come with an explicit update here.
EXPECTED: dict[str, Any] = {
    "A": {"total": 262, "migrated": 1, "merged": 259, "dropped": 1, "inbox": 1},
    "B": {"total": 331, "migrated": 71, "merged": 259, "dropped": 1, "inbox": 0},
    "recipes": 331,
    "ai_images_archived": {"A": 28, "B": 19},
}
CHECKSUM_SQL = (
    "select md5(string_agg(id::text||document::text, ',' order by id)) from recipe_artifacts"
)
LIVE_A_CMD = [
    "podman",
    "exec",
    "recipe-table-review-postgres",
    "psql",
    "-U",
    "recipe_table",
    "-d",
    "recipe_table_review",
    "-tAc",
    CHECKSUM_SQL,
]


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = "") -> None:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})


def _hash_list(path: Path, base: Path) -> list[tuple[str, Path]]:
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        file = Path(name) if name.startswith("/") else (base / name)
        entries.append((digest, file))
    return entries


def check_hash_file(path: Path, base: Path) -> tuple[bool, str]:
    entries = _hash_list(path, base)
    bad = []
    for digest, file in entries:
        if not file.is_file():
            bad.append(f"missing {file}")
        elif X.sha256_file(file) != digest:
            bad.append(f"changed {file}")
    return (
        not bad,
        f"{len(entries) - len(bad)}/{len(entries)} unchanged" + (f"; {bad[:5]}" if bad else ""),
    )


def a_checksums() -> tuple[str | None, str | None, str]:
    scratch = live = None
    note = ""
    try:
        conn = X._pg_connect(X.a_dsn())
        try:
            scratch = X.pg_rows(conn, CHECKSUM_SQL)[0]["md5"]
        finally:
            conn.rollback()
            conn.close()
    except Exception as exc:  # noqa: BLE001
        note += f"scratch: {exc}; "
    try:
        proc = subprocess.run(LIVE_A_CMD, capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode == 0:
            live = proc.stdout.strip()
        else:
            note += f"live: {proc.stderr.strip()[:200]}"
    except Exception as exc:  # noqa: BLE001
        note += f"live: {exc}"
    return scratch, live, note


def a_source_ids() -> set[str]:
    conn = X._pg_connect(X.a_dsn())
    try:
        return {r["id"] for r in X.pg_rows(conn, "SELECT id::text AS id FROM recipe_artifacts")}
    finally:
        conn.rollback()
        conn.close()


def verify(
    db_path: Path,
    images_root: Path,
    paths: Any,
    archive_manifest: list[dict[str, Any]] | None = None,
    check_sources: bool = True,
) -> dict[str, Any]:
    checks = Checks()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        _verify_db(conn, checks, images_root, paths, archive_manifest, check_sources)
    finally:
        conn.close()
    ok = all(item["ok"] for item in checks.items)
    return {"ok": ok, "checks": checks.items}


def _verify_db(conn, checks: Checks, images_root: Path, paths, manifest, check_sources) -> None:
    ledger = [dict(r) for r in conn.execute("SELECT * FROM migration_ledger")]
    outcomes = {src: Counter(r["outcome"] for r in ledger if r["source"] == src) for src in "AB"}

    # Coverage against the sources themselves.
    if check_sources:
        a_ids = a_source_ids()
        b_conn = sqlite3.connect(f"file:{paths.sources / 'cookt2.db'}?mode=ro", uri=True)
        try:
            b_ids = {r[0] for r in b_conn.execute("SELECT id FROM recipes")}
        finally:
            b_conn.close()
        checks.add(
            "A source row count",
            len(a_ids) == EXPECTED["A"]["total"],
            f"{len(a_ids)} (expected {EXPECTED['A']['total']})",
        )
        checks.add(
            "B source row count",
            len(b_ids) == EXPECTED["B"]["total"],
            f"{len(b_ids)} (expected {EXPECTED['B']['total']})",
        )
        for src, ids in (("A", a_ids), ("B", b_ids)):
            got = {r["source_id"] for r in ledger if r["source"] == src}
            missing, extra = ids - got, got - ids
            checks.add(
                f"every {src} recipe has a ledger row",
                not missing and not extra,
                f"missing {sorted(missing)[:5]}, extra {sorted(extra)[:5]}"
                if missing or extra
                else f"{len(got)} rows",
            )

    for src in "AB":
        exp = EXPECTED[src]
        got = outcomes[src]
        total = sum(got.values())
        detail = (
            f"{exp['total']} = migrated {got['migrated']} + merged {got['merged']} + "
            f"dropped {got['dropped']} + inbox {got['inbox']}"
        )
        checks.add(f"{src} ledger arithmetic", total == exp["total"], detail)
        exact = all(got[k] == exp[k] for k in ("migrated", "merged", "dropped", "inbox"))
        checks.add(
            f"{src} exact expected outcome counts",
            exact,
            {
                "expected": {k: exp[k] for k in ("migrated", "merged", "dropped", "inbox")},
                "got": dict(got),
            },
        )
    bad_outcome = [
        r for r in ledger if r["outcome"] not in ("migrated", "merged", "dropped", "inbox")
    ]
    no_reason = [r for r in ledger if not (r["reason"] or "").strip()]
    checks.add(
        "ledger outcomes valid and every row has a reason",
        not bad_outcome and not no_reason,
        f"{len(bad_outcome)} bad, {len(no_reason)} without reason",
    )
    checks.add(
        "merged counts agree (A merged == B merged)",
        outcomes["A"]["merged"] == outcomes["B"]["merged"],
        f"A {outcomes['A']['merged']}, B {outcomes['B']['merged']}",
    )

    recipes = [dict(r) for r in conn.execute("SELECT * FROM recipes")]
    recipe_ids = {r["id"] for r in recipes}
    expected_recipes = (
        outcomes["A"]["migrated"] + outcomes["A"]["merged"] + outcomes["B"]["migrated"]
    )
    checks.add(
        "recipe count",
        len(recipes) == expected_recipes == EXPECTED["recipes"],
        f"{len(recipes)} recipes; ledger implies {expected_recipes}; expected "
        f"{EXPECTED['recipes']}",
    )
    dangling = [
        r
        for r in ledger
        if r["outcome"] in ("migrated", "merged") and r["recipe_id"] not in recipe_ids
    ]
    checks.add("ledger recipe_ids exist", not dangling, f"{len(dangling)} dangling")
    ledger_targets = {r["recipe_id"] for r in ledger if r["recipe_id"]}
    orphans = recipe_ids - ledger_targets
    checks.add(
        "every recipe traces to a source ledger row", not orphans, f"{len(orphans)} without ledger"
    )
    bad_origin = [
        r["id"]
        for r in recipes
        if r["origin"] not in ("migrated_a", "migrated_b", "merged", "migrated_b_generated")
    ]
    checks.add("recipe origins", not bad_origin, f"{len(bad_origin)} unexpected")

    # Aliases.
    aliases = {r["alias"]: r["recipe_id"] for r in conn.execute("SELECT * FROM recipe_aliases")}
    missing_alias = [
        r
        for r in ledger
        if r["outcome"] in ("migrated", "merged") and aliases.get(r["source_id"]) != r["recipe_id"]
    ]
    checks.add(
        "every migrated/merged source id resolves via recipe_aliases",
        not missing_alias,
        f"{len(missing_alias)} missing",
    )

    # Inbox rows for inbox outcomes.
    inbox = [dict(r) for r in conn.execute("SELECT * FROM inbox")]
    inbox_ids = set()
    for row in inbox:
        data = json.loads(row["input"])
        inbox_ids.update(data.get("old_ids", {}).values())
    missing_inbox = [
        r["source_id"]
        for r in ledger
        if r["outcome"] == "inbox" and r["source_id"] not in inbox_ids
    ]
    checks.add(
        "inbox rows for every inbox outcome",
        not missing_inbox,
        f"{len(inbox)} inbox rows; missing {missing_inbox}",
    )

    # Documents.
    invalid, title_mismatch, personal = [], [], []
    step_ids_by_recipe: dict[str, set[str]] = {}
    for r in recipes:
        try:
            doc = RecipeDocumentV2.model_validate_json(r["document"])
        except ValidationError as exc:
            invalid.append(f"{r['id']}: {str(exc).splitlines()[0]}")
            continue
        if doc.title != r["title"]:
            title_mismatch.append(r["id"])
        if doc.personal_notes:
            personal.append(r["id"])
        step_ids_by_recipe[r["id"]] = {s.id for sec in doc.instruction_sections for s in sec.steps}
    checks.add(
        "every document validates as RecipeDocumentV2",
        not invalid,
        f"{len(recipes) - len(invalid)}/{len(recipes)} valid"
        + (f"; {invalid[:3]}" if invalid else ""),
    )
    checks.add(
        "document title == recipes.title", not title_mismatch, f"{len(title_mismatch)} mismatches"
    )
    checks.add("personal_notes moved out of documents", not personal, f"{len(personal)} still set")

    # Images.
    images = [dict(r) for r in conn.execute("SELECT * FROM images")]
    missing_files, sha_bad = [], []
    for img in images:
        for rel in (img["path"], img["thumb_path"]):
            if not rel or not (images_root / rel).is_file():
                missing_files.append(rel)
        folder = (images_root / img["path"]).parent
        originals = sorted(folder.glob("original.*"))
        if not originals:
            missing_files.append(f"{folder}/original.*")
        elif X.sha256_file(originals[0]) != img["sha256"]:
            sha_bad.append(img["id"])
    checks.add(
        "every image path/thumb/original resolves to a file",
        not missing_files,
        f"{len(images)} images; missing {missing_files[:5]}",
    )
    checks.add("image sha256 == original bytes", not sha_bad, f"{len(sha_bad)} mismatches")
    by_id = {img["id"]: img for img in images}
    bad_primary = [
        r["id"]
        for r in recipes
        if r["image_id"]
        and (
            r["image_id"] not in by_id
            or by_id[r["image_id"]]["recipe_id"] != r["id"]
            or by_id[r["image_id"]]["role"] != "primary"
        )
    ]
    checks.add(
        "recipes.image_id points at the recipe's primary image",
        not bad_primary,
        f"{len(bad_primary)} bad",
    )
    bad_steps = [
        img["id"]
        for img in images
        if img["role"] == "step"
        and img["step_id"] not in step_ids_by_recipe.get(img["recipe_id"], set())
    ]
    checks.add(
        "step images reference existing step ids",
        not bad_steps,
        f"{sum(1 for i in images if i['role'] == 'step')} step images; {len(bad_steps)} unresolved",
    )

    # AI images: exported, and none of them in the app.
    if manifest is None and (paths.archive / "manifest.json").is_file():
        manifest = json.loads((paths.archive / "manifest.json").read_text())
    manifest = manifest or []
    ai_sha = {m["sha256"] for m in manifest}
    leaked = [img["id"] for img in images if img["sha256"] in ai_sha]
    checks.add("no AI-generated image is used by the app", not leaked, f"{len(leaked)} leaked")
    per_source = Counter(m["source"] for m in manifest)
    missing_archive = [
        m["archived_file"] for m in manifest if not (paths.archive / m["archived_file"]).is_file()
    ]
    checks.add(
        "AI images exported to data/archive/ai-images",
        not missing_archive and dict(per_source) == EXPECTED["ai_images_archived"],
        {
            "exported": dict(per_source),
            "expected": EXPECTED["ai_images_archived"],
            "missing": missing_archive[:5],
        },
    )

    if check_sources:
        ok, detail = check_hash_file(paths.sources / "source-originals.sha256", Path("/"))
        checks.add("original cookt2 files unchanged (source-originals.sha256)", ok, detail)
        ok, detail = check_hash_file(paths.sources / "MANIFEST.sha256", paths.sources)
        checks.add("source snapshots unchanged (MANIFEST.sha256)", ok, detail)
        scratch, live, note = a_checksums()
        checks.add(
            "live recipe-table DB content == scratch snapshot (md5 of id||document)",
            bool(scratch and live and scratch == live),
            f"live {live}, scratch {scratch}" + (f"; {note}" if note else ""),
        )
