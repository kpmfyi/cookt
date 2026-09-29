"""Spec §7 #8: tagging agreement on the hand-labelled 40-recipe sample.

Scores the tags actually applied (auto + migrated + manual) against
docs/evals/tagging-labels.json. A facet agrees when any applied value is in the
acceptable set (an empty facet agrees only when "" is acceptable). Also counts
false vegetarian / vegan tags. Writes docs/evals/tagging.md + .json.

    uv run python scripts/eval_tagging.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
labels = json.loads((ROOT / "docs/evals/tagging-labels.json").read_text())
labels.pop("_about")
conn = sqlite3.connect(f"file:{ROOT / 'data/cookt.db'}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

rows, totals = [], {"course": 0, "cuisine": 0, "protein": 0}
false_veg, false_vegan, tagged = [], [], 0
for prefix, label in labels.items():
    rid = conn.execute("SELECT id FROM recipes WHERE id LIKE ?", (prefix + "%",)).fetchone()["id"]
    tags: dict[str, list[str]] = {}
    for t in conn.execute("SELECT kind, value FROM recipe_tags WHERE recipe_id = ?", (rid,)):
        tags.setdefault(t["kind"], []).append(t["value"])
    done = conn.execute(
        "SELECT 1 FROM enrichment_state WHERE recipe_id = ? AND step = 'tags' AND error IS NULL",
        (rid,),
    ).fetchone()
    tagged += bool(done)
    row = {"id": prefix, "title": label["t"], "auto_tagged": bool(done)}
    for facet in totals:
        got = tags.get(facet, [])
        ok = any(v in label[facet] for v in got) if got else "" in label[facet]
        totals[facet] += ok
        row[facet] = {"applied": got, "acceptable": label[facet], "agree": ok}
    diets = set(tags.get("diet", []))
    if "vegetarian" in diets and label["veg"] is False:
        false_veg.append(label["t"])
    if "vegan" in diets and label["vegan"] is False:
        false_vegan.append(label["t"])
    row["diet"] = sorted(diets)
    rows.append(row)

n = len(labels)
summary = {
    "n": n,
    "auto_tagged": tagged,
    "agreement": {k: round(v / n, 3) for k, v in totals.items()},
    "false_vegetarian": false_veg,
    "false_vegan": false_vegan,
    "pass": all(v / n >= 0.9 for v in totals.values()) and not false_veg and not false_vegan,
}
(ROOT / "docs/evals/tagging.json").write_text(
    json.dumps({"summary": summary, "rows": rows}, indent=1)
)
lines = [
    "# Tagging eval (spec §7 #8)",
    "",
    f"Sample: {n} recipes (seeded random draw), hand-labelled by the implementing agent before looking at model output "
    "(`docs/evals/tagging-labels.json`). {tagged}/{n} had been auto-tagged when scored.".replace(
        "{tagged}", str(tagged)
    ).replace("{n}", str(n)),
    "",
    "| facet | agreement | target |",
    "|---|---|---|",
    *[f"| {k} | {round(v / n * 100)}% ({v}/{n}) | ≥ 90% |" for k, v in totals.items()],
    f"| false vegetarian | {len(false_veg)} | 0 |",
    f"| false vegan | {len(false_vegan)} | 0 |",
    "",
    f"**Result: {'PASS' if summary['pass'] else 'FAIL'}**",
    "",
    "## Disagreements",
    "",
]
for row in rows:
    for facet in totals:
        if not row[facet]["agree"]:
            lines.append(
                f"- {row['title']}: {facet} applied {row[facet]['applied'] or '—'}, acceptable {row[facet]['acceptable']}"
            )
(ROOT / "docs/evals/tagging.md").write_text("\n".join(lines) + "\n")
print(json.dumps(summary, indent=1))
