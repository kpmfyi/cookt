"""Cookbook EPUB import: recipe detection on synthetic books in two publisher styles."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from cookt.extraction import epub


def _png() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(out, "PNG")
    return out.getvalue()


PNG = _png()

# Style A: everything is a <p> with publisher classes (common in trade cookbooks).
CLASSED = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1 class="ct">Chapter 3: Wok Basics</h1>
<p class="tx">The wok is the most versatile pan in the kitchen. It heats fast. It tosses well.</p>
<p class="fig"><img src="../images/beef.png" alt=""/></p>
<p class="rt">Stir-Fried Beef with Broccoli</p>
<p class="ry">Serves 4</p>
<p class="hn">A takeout classic that is better at home. The trick is velveting the beef.</p>
<p class="ingh">For the Beef</p>
<p class="ingr">12 ounces (340g) flank steak, thinly sliced</p>
<p class="ingr">1 tablespoon (15ml) soy sauce</p>
<p class="ingr">1 teaspoon cornstarch</p>
<p class="ingh">For the Sauce</p>
<p class="ingr">2 tablespoons (30ml) oyster sauce</p>
<p class="ingr">1/2 cup (120ml) chicken stock</p>
<p class="ingr">Kosher salt</p>
<p class="meth">1. Combine the beef, soy sauce and cornstarch in a bowl and toss to coat.</p>
<p class="meth">2. Heat the wok over high heat until smoking. Add the beef and stir-fry
until browned, about 1 minute.</p>
<p class="meth">3. Add the sauce and toss until glossy. Season with salt and serve.</p>
<p class="note">Note: Skirt steak works too.</p>
<p class="tx">Why velveting works is a long story, and this paragraph is an essay, not a step.</p>
<p class="tx">More essay text that goes on about proteins. It has several sentences. Really.</p>
<p class="rt">Garlic Fried Rice</p>
<p class="ry">Makes 2 servings</p>
<p class="ingr">3 cups cooked rice, preferably day-old</p>
<p class="ingr">2 tablespoons vegetable oil</p>
<p class="ingr">4 garlic cloves, minced</p>
<p class="meth">1. Heat the oil in a wok over medium heat. Add the garlic and cook until golden.</p>
<p class="meth">2. Add the rice and stir-fry until hot, about 3 minutes.</p>
<h1 class="ct">Chapter 4: Noodles</h1>
</body></html>"""

# Style B: semantic headings with <ul>/<ol>, no classes.
PLAIN = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h2>Perfect Roast Chicken</h2>
<p>Serves 4</p>
<p>Spatchcocking gives you juicy breast meat and crisp skin. Here's how.</p>
<h3>Ingredients</h3>
<ul>
<li>1 whole chicken, 4 to 5 pounds</li>
<li>2 tablespoons olive oil</li>
<li>Kosher salt and freshly ground black pepper</li>
<li>1 lemon, halved</li>
</ul>
<h3>Directions</h3>
<ol>
<li>Adjust oven rack to middle position and preheat oven to 450°F.</li>
<li>Spatchcock the chicken and rub with oil. Season with salt and pepper.</li>
<li>Roast until the breast registers 150°F, about 45 minutes. Rest 10 minutes.</li>
</ol>
<h2>Pan Sauce Basics</h2>
<p>This is an essay about pan sauces with no ingredient list at all.</p>
</body></html>"""


# Style C: the patterns of two real trade cookbooks (all-caps paragraph titles, label/value
# yield tables, NOTE blocks, bare-numbered steps with lettered sub-items, two-line titles).
TRADE = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p class="chap_hd">KUNG PAO SHRIMP</p>
<table><tr><td><p class="old_y">Yield</p></td><td><p class="old_ypara">Serves 4</p></td></tr>
<tr><td><p class="old_y">Total Time</p></td>
<td><p class="old_ypara">40 minutes</p></td></tr></table>
<p class="recipe_rn">NOTE</p>
<p class="notep">12.5 grams is about 1 tablespoon Diamond Crystal kosher salt.</p>
<p class="noindent_paraf">You can swap the shrimp for chicken. It works just as well.</p>
<p class="ing-h">INGREDIENTS</p>
<p class="ing-ts">For the Shrimp:</p>
<p class="ing-list">1 pound (450 g) large shrimp, peeled</p>
<p class="ing-list">A cup or so of ice cubes</p>
<p class="ing-list">Large pinch of red pepper flakes (optional)</p>
<p class="ing-list">2 fresh lemongrass stalks, bottom 3 to 5 inches only (1½ ounces/40 g),
tough outer leaves removed and discarded, the rest thinly sliced crosswise and then
bruised well with the flat side of a knife to release their oils</p>
<p class="ing-ts">For the Sauce:</p>
<p class="ing-list">4 teaspoons (20 ml) honey</p>
<p class="ing-list">1 tablespoon (15 ml) Chinkiang vinegar</p>
<p class="dir">DIRECTIONS</p>
<p class="numberg"><span class="crlg">1</span> For the Shrimp: Brine the shrimp for 15 minutes.</p>
<p class="numberg"><span class="crlg">2</span> BEFORE YOU STIR-FRY, GET YOUR BOWLS READY:</p>
<ul><li class="salp">a. Shrimp</li><li class="salp">b. Sauce</li></ul>
<p class="numberg"><span class="crlg">3</span> Stir-fry the shrimp and add the sauce. Serve.</p>
<p class="h2">HOW TO BUY SHRIMP</p>
<p class="indent_para">An essay that is not part of the method at all. It has sentences.</p>
<p class="recipe_rt1a">SIMPLE RED-WINE</p>
<p class="recipe_rt1">PAN SAUCE</p>
<p class="recipe_y">SERVES 4</p>
<p class="recipe_i">1 medium shallot, finely minced</p>
<p class="recipe_i">1 cup red wine</p>
<p class="recipe_i">4 tablespoons unsalted butter</p>
<p class="recipe_rsteps">1. Cook the shallot in the pan drippings until softened.</p>
<p class="recipe_rsteps1">2. Add the wine, reduce by half, and whisk in the butter.</p>
</body></html>"""


def build_epub(tmp: Path, chapters: list[str], *, title="Test Cookbook", author="A. Cook") -> Path:
    path = tmp / "book.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        items = "".join(
            f'<item id="c{i}" href="text/ch{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(chapters))
        )
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata>"
            f'<manifest>{items}<item id="img" href="images/beef.png" media-type="image/png"/>'
            f"</manifest><spine>{spine}</spine></package>",
        )
        for i, chapter in enumerate(chapters):
            zf.writestr(f"OEBPS/text/ch{i}.xhtml", chapter)
        zf.writestr("OEBPS/images/beef.png", PNG)
    return path


def test_classed_book(tmp_path):
    extractions, stats = epub.extract_from_epub(build_epub(tmp_path, [CLASSED]))
    assert stats["book"] == "Test Cookbook" and stats["author"] == "A. Cook"
    assert [e.document.title for e in extractions] == [
        "Stir-Fried Beef with Broccoli",
        "Garlic Fried Rice",
    ]
    beef = extractions[0]
    doc = beef.document
    assert doc.yield_text == "Serves 4"
    assert [s.heading for s in doc.ingredient_sections] == ["For the Beef", "For the Sauce"]
    assert [i.source_text for i in doc.ingredient_sections[1].ingredients] == [
        "2 tablespoons (30ml) oyster sauce",
        "1/2 cup (120ml) chicken stock",
        "Kosher salt",
    ]
    steps = [s.text for s in doc.instruction_sections[0].steps]
    assert len(steps) == 3 and steps[0].startswith("Combine the beef")  # number stripped
    assert not any("essay" in s for s in steps)  # stops at the prose after the method
    assert doc.notes == ["Note: Skirt steak works too."]
    assert beef.hints["description"].startswith("A takeout classic")
    assert beef.image_data and beef.image_data[0][1] == "image/png"
    assert beef.source_site == "Test Cookbook" and beef.method == "epub"
    rice = extractions[1].document
    assert len(rice.instruction_sections[0].steps) == 2  # the chapter heading ends it


def test_plain_book(tmp_path):
    extractions, stats = epub.extract_from_epub(build_epub(tmp_path, [PLAIN]))
    assert [e.document.title for e in extractions] == ["Perfect Roast Chicken"]
    doc = extractions[0].document
    assert [i.source_text for i in doc.ingredient_sections[0].ingredients][2] == (
        "Kosher salt and freshly ground black pepper"
    )
    assert len(doc.instruction_sections[0].steps) == 3
    assert doc.yield_text == "Serves 4"
    assert stats["skipped"] == 0


def test_not_an_epub(tmp_path):
    bad = tmp_path / "x.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(epub.EpubError):
        epub.extract_from_epub(bad)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COOKT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COOKT_RUN_WORKER", "0")
    monkeypatch.setenv("COOKT_LLM_LOCK", str(tmp_path / "llm.lock"))
    import cookt.config

    importlib.reload(cookt.config)
    for name in [
        "cookt.db",
        "cookt.search",
        "cookt.recipes",
        "cookt.planning",
        "cookt.jobs",
        "cookt.imports",
        "cookt.enrich.tagging",
        "cookt.copyedit",
        "cookt.api.intelligence",
        "cookt.api.planning",
        "cookt.api.imports",
        "cookt.api.review",
        "cookt.mcp_server",
        "cookt.pipeline_jobs",
        "cookt.app",
    ]:
        importlib.reload(importlib.import_module(name))
    from cookt import db
    from cookt.app import app
    from fastapi.testclient import TestClient

    db.init(db.connect())
    with TestClient(app) as test_client:
        yield test_client


def test_upload_to_inbox_and_save(client, tmp_path):
    book = build_epub(tmp_path, [CLASSED, PLAIN], title="The Wok", author="J. Kenji López-Alt")

    def upload():
        with open(book, "rb") as handle:
            return client.post(
                "/api/import/epub", files={"file": ("the-wok.epub", handle, "application/epub+zip")}
            )

    first = upload().json()
    assert first["count"] == 3 and first["book"] == "The Wok"
    assert upload().json() == {**first, "ids": [], "count": 0, "already_imported": 3}

    items = client.get("/api/inbox").json()["items"]
    assert {i["status"] for i in items} == {"ready"} and {i["kind"] for i in items} == {"epub"}
    beef = next(i for i in items if i["draft"]["document"]["title"].startswith("Stir-Fried Beef"))
    saved = client.post(f"/api/inbox/{beef['id']}/save", json={}).json()
    recipe = client.get(f"/api/recipes/{saved['recipe_id']}").json()
    assert recipe["source_site"] == "The Wok"
    assert recipe["source_author"] == "J. Kenji López-Alt"
    assert recipe["image"] is not None  # the EPUB's photo came along


def test_upload_rejects_non_epub(client):
    response = client.post(
        "/api/import/epub", files={"file": ("x.epub", b"nope", "application/epub+zip")}
    )
    assert response.status_code == 422


def test_trade_book_patterns(tmp_path):
    extractions, _ = epub.extract_from_epub(build_epub(tmp_path, [TRADE]))
    assert [e.document.title for e in extractions] == [
        "Kung Pao Shrimp",
        "Simple Red-Wine Pan Sauce",
    ]
    shrimp = extractions[0]
    doc = shrimp.document
    assert doc.yield_text == "Serves 4" and shrimp.total_minutes == 40
    assert doc.notes == ["12.5 grams is about 1 tablespoon Diamond Crystal kosher salt."]
    assert shrimp.hints["description"].startswith("You can swap")
    assert [s.heading for s in doc.ingredient_sections] == ["For the Shrimp", "For the Sauce"]
    shrimp_lines = [i.source_text for i in doc.ingredient_sections[0].ingredients]
    assert "Large pinch of red pepper flakes (optional)" in shrimp_lines
    assert shrimp_lines[-1].startswith("2 fresh lemongrass stalks")  # long line kept, not a step
    steps = [s.text for s in doc.instruction_sections[0].steps]
    assert len(steps) == 3
    assert steps[0].startswith("For the Shrimp: Brine")  # bare "1" stripped
    assert steps[1].endswith("a. Shrimp b. Sauce")  # lettered sub-items folded in
    assert not any("essay" in step for step in steps)  # the all-caps essay heading ends it
    sauce = extractions[1].document
    assert sauce.yield_text == "SERVES 4" and len(sauce.instruction_sections[0].steps) == 2
