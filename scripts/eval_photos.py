"""Synthetic photo-import check for spec §7 #7 (NOT the real acceptance test).

No photographs of cookbook pages or handwritten cards exist on this machine, so this renders
real catalog recipes as degraded "phone photos": 5 cookbook pages (one recipe split over two
pages) in a serif face, and 3 cards in a script face (Z003) with jittered lines and blue ink.
Each goes through `extract_from_images` (local vision model, OCR transcript + coverage check).
Ingredient-line accuracy = share of ground-truth lines matched (normalized text similarity ≥ 0.9)
by an extracted line. Writes docs/evals/photo-import-synthetic.{md,json}; images in data/eval/photos.

    uv run python scripts/eval_photos.py
"""

from __future__ import annotations

import io
import json
import random
import re
import sqlite3
import textwrap
import time
from difflib import SequenceMatcher
from pathlib import Path

from cookt.extraction import pipeline
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/eval/photos"
SERIF = "/usr/share/fonts/urw-base35/C059-Roman.otf"
SERIF_BOLD = "/usr/share/fonts/urw-base35/C059-Bold.otf"
SCRIPT = "/usr/share/fonts/urw-base35/Z003-MediumItalic.otf"
rng = random.Random(7)


def norm(text: str) -> str:
    text = text.lower().replace("½", "1/2").replace("¼", "1/4").replace("¾", "3/4")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()


def photo_finish(img: Image.Image) -> bytes:
    """Rotate slightly, warm the paper, add blur + noise, JPEG like a phone photo."""
    img = img.rotate(
        rng.uniform(-2.0, 2.0), resample=Image.BICUBIC, expand=True, fillcolor=(120, 110, 95)
    )
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    noise = Image.effect_noise(img.size, 18).convert("L")
    img = Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.06)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def cookbook_pages(title: str, ingredients: list[str], steps: list[str], pages: int) -> list[bytes]:
    W, H, margin = 1240, 1754, 110
    body = ImageFont.truetype(SERIF, 30)
    head = ImageFont.truetype(SERIF_BOLD, 54)
    sub = ImageFont.truetype(SERIF_BOLD, 34)
    blocks: list[tuple[str, ImageFont.FreeTypeFont, int]] = [
        (title, head, 90),
        ("Ingredients", sub, 60),
    ]
    blocks += [(line, body, 44) for line in ingredients]
    blocks += [("", body, 30), ("Method", sub, 60)]
    for n, step in enumerate(steps, 1):
        for i, part in enumerate(textwrap.wrap(f"{n}. {step}", 62)):
            blocks.append((("   " if i else "") + part, body, 42))
        blocks.append(("", body, 16))
    per_page = -(-len(blocks) // pages)
    images = []
    for p in range(pages):
        img = Image.new("RGB", (W, H), (246, 240, 226))
        draw = ImageDraw.Draw(img)
        y = margin
        for text, font, step in blocks[p * per_page : (p + 1) * per_page]:
            draw.text((margin, y), text, font=font, fill=(30, 28, 26))
            y += step
        draw.text((W // 2 - 20, H - 80), str(112 + p), font=body, fill=(90, 85, 80))
        images.append(photo_finish(img))
    return images


def card(title: str, ingredients: list[str], steps: list[str]) -> bytes:
    lines = (
        [title, ""] + ingredients + [""] + [part for s in steps for part in textwrap.wrap(s, 46)]
    )
    font = ImageFont.truetype(SCRIPT, 34)
    H = max(900, 120 + 46 * len(lines))
    img = Image.new("RGB", (1300, H), (250, 247, 236))
    draw = ImageDraw.Draw(img)
    for y in range(110, H, 46):
        draw.line((60, y + 38, 1240, y + 38), fill=(170, 200, 225), width=2)
    y = 70
    for text in lines:
        draw.text(
            (80 + rng.randint(-6, 6), y + rng.randint(-3, 3)), text, font=font, fill=(28, 45, 120)
        )
        y += 46
    return photo_finish(img)


def pick(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, title, document FROM recipes ORDER BY id").fetchall()
    rng.shuffle(rows)
    out = []
    for r in rows:
        d = json.loads(r["document"])
        ings = [i["source_text"] for s in d["ingredient_sections"] for i in s["ingredients"]]
        steps = [st["text"] for s in d["instruction_sections"] for st in s["steps"]]
        if 5 <= len(ings) <= 14 and 3 <= len(steps) <= 8 and all(len(i) < 70 for i in ings):
            out.append({"id": r["id"], "title": r["title"], "ingredients": ings, "steps": steps})
    return out


def score(truth: list[str], got: list[str]) -> tuple[float, list[str]]:
    missing = []
    matched = 0
    pool = [norm(g) for g in got]
    for line in truth:
        best = max((SequenceMatcher(None, norm(line), g).ratio() for g in pool), default=0)
        if best >= 0.9:
            matched += 1
        else:
            missing.append(line)
    return matched / len(truth), missing


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{ROOT / 'data/cookt.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    candidates = pick(conn)
    long_one = max(candidates[:30], key=lambda c: len(c["ingredients"]) + 3 * len(c["steps"]))
    pages_set = [long_one] + [c for c in candidates if c is not long_one][:4]
    cards_set = sorted(candidates[10:40], key=lambda c: len(c["ingredients"]))[:3]
    cases = [("page", c, 2 if c is long_one else 1) for c in pages_set]
    cases += [("card", c, 1) for c in cards_set]
    results = []
    for kind, case, pages in cases:
        images = (
            cookbook_pages(case["title"], case["ingredients"], case["steps"], pages)
            if kind == "page"
            else [card(case["title"], case["ingredients"], case["steps"])]
        )
        for i, data in enumerate(images):
            (OUT / f"{kind}-{case['id'][:8]}-{i + 1}.jpg").write_bytes(data)
        started = time.monotonic()
        row = {
            "kind": kind,
            "title": case["title"],
            "pages": pages,
            "truth_lines": len(case["ingredients"]),
        }
        try:
            ex = pipeline.extract_from_images(
                [(d, "image/jpeg") for d in images], handwritten=kind == "card"
            )
            got = [i.source_text for s in ex.document.ingredient_sections for i in s.ingredients]
            acc, missing = score(case["ingredients"], got)
            precision, extra = score(got, case["ingredients"])  # extracted lines that are real
            row.update(
                accuracy=round(acc, 3),
                precision=round(precision, 3),
                extra=extra,
                extracted_lines=len(got),
                missing=missing,
                coverage=ex.coverage,
                uncertain=ex.uncertain_lines,
                steps=sum(len(s.steps) for s in ex.document.instruction_sections),
                truth_steps=len(case["steps"]),
            )
        except Exception as exc:  # noqa: BLE001
            row.update(accuracy=0.0, error=str(exc)[:300])
        row["seconds"] = round(time.monotonic() - started, 1)
        results.append(row)
        print(json.dumps({k: row[k] for k in row if k not in ("missing", "uncertain")}), flush=True)
    ok = sum(r["accuracy"] >= 0.95 and r.get("precision", 0) >= 0.95 for r in results)
    (ROOT / "docs/evals/photo-import-synthetic.json").write_text(json.dumps(results, indent=1))
    lines = [
        "# Photo import — synthetic check (not the real §7 #7 test)",
        "",
        "Real cookbook-page and handwritten-card photos were not available, so real catalog recipes were "
        "rendered as degraded phone-style photos (serif pages incl. one 2-page recipe; script-font cards). "
        "This exercises the vision pipeline, the OCR-transcript coverage check and uncertain-line flags; it "
        "says little about real handwriting. Images: `data/eval/photos/`.",
        "",
        f"**{ok}/{len(results)} imports reached ≥ 95 % ingredient-line recall and precision.** "
        "(Recall = source lines found; precision = extracted lines that are real list lines.)",
        "",
        "| kind | recipe | pages | recall | precision | lines (truth→got) | steps (truth→got) | coverage | uncertain | s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['kind']} | {r['title']} | {r['pages']} | {round(r['accuracy'] * 100)}% | "
            f"{round(r.get('precision', 0) * 100)}% | "
            f"{r['truth_lines']}→{r.get('extracted_lines', '—')} | {r.get('truth_steps', '—')}→{r.get('steps', '—')} | "
            f"{r.get('coverage') if r.get('coverage') is not None else '—'} | {len(r.get('uncertain') or [])} | {r['seconds']} |"
        )
    for r in results:
        if r.get("error") or r.get("missing"):
            lines.append(
                f"\n- **{r['title']}**: {r.get('error') or 'missed: ' + '; '.join(r['missing'])}"
            )
    (ROOT / "docs/evals/photo-import-synthetic.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
