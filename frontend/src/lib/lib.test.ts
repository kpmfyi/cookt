import { describe, expect, it } from "vitest";
import { diffWords } from "./diff";
import { durationSpans, formatRemaining } from "./durations";
import { ingredientBlocks, stepBlocks, toIngredientSections, toInstructionSections } from "./editing";
import { formatQuantity, parseQuantity, scaleText } from "./fractions";
import { applyLibrary, emptyQuery, rrf } from "./library";
import type { Recipe, RecipeDocument } from "./types";
import { amountAndRest, convertTemps, displayLine } from "./units";

describe("fractions", () => {
  it("parses and formats", () => {
    expect(parseQuantity("1 1/2")).toBe(1.5);
    expect(parseQuantity("1½")).toBe(1.5);
    expect(formatQuantity(0.75)).toBe("¾");
    expect(formatQuantity(2.5)).toBe("2½");
    expect(scaleText("1 ½ cups flour", 2)).toBe("3 cups flour");
    expect(scaleText("2-3 cloves garlic", 2)).toBe("4-6 cloves garlic");
  });
});

describe("durations", () => {
  it("finds tappable timers in step text", () => {
    const spans = durationSpans("Simmer 20 minutes, then rest 1 hour. Bake 10-12 mins.");
    expect(spans.map((s) => s.seconds)).toEqual([1200, 3600, 720]);
    expect(formatRemaining(3725)).toBe("1:02:05");
  });
});

describe("units", () => {
  const item = (source_text: string) => ({ id: "x", source_text });
  it("converts US volume and mass to metric", () => {
    expect(displayLine(item("1 cup milk"), 1, "metric")).toBe("235 ml milk");
    expect(displayLine(item("1 lb ground beef"), 1, "metric")).toBe("455 g ground beef");
    expect(displayLine(item("2 tbsp butter"), 2, "metric")).toBe("60 ml butter");
  });
  it("converts metric to US and keeps unconvertible lines", () => {
    expect(displayLine(item("500 g flour"), 1, "us")).toBe("1 lb flour");
    expect(displayLine(item("3 eggs"), 2, "us")).toBe("6 eggs");
    expect(displayLine(item("salt to taste"), 2, "metric")).toBe("salt to taste");
  });
  it("splits amount into its own column", () => {
    expect(amountAndRest("1 ½ cups flour, sifted")).toEqual(["1 ½ cups", "flour, sifted"]);
    expect(amountAndRest("Salt")).toEqual(["", "Salt"]);
  });
  it("converts oven temperatures", () => {
    expect(convertTemps("Heat oven to 425°F.", "metric")).toBe("Heat oven to 220°C.");
    expect(convertTemps("Bake at 180°C", "us")).toBe("Bake at 355°F");
  });
});

const doc: RecipeDocument = {
  schema_version: 2,
  title: "T",
  notes: [],
  ingredient_sections: [{ id: "ing", ingredients: [{ id: "a", source_text: "1 egg" }, { id: "b", source_text: "salt" }] }],
  instruction_sections: [{ id: "ins", steps: [{ id: "s1", text: "Beat." }, { id: "s2", text: "Cook." }] }],
};

describe("editing", () => {
  it("round-trips and keeps ids for unchanged lines", () => {
    const ing = ingredientBlocks(doc);
    ing[0].text = "1 egg\n2 tbsp milk\nsalt";
    const sections = toIngredientSections(ing, doc);
    expect(sections[0].ingredients.map((i) => i.id).filter((id) => id === "a" || id === "b")).toEqual(["a", "b"]);
    expect(sections[0].ingredients).toHaveLength(3);
    const steps = stepBlocks(doc);
    steps[0].text = "Beat.\n\nCook\nuntil set.";
    const out = toInstructionSections(steps, doc);
    expect(out[0].steps[0].id).toBe("s1");
    expect(out[0].steps[1].text).toBe("Cook until set.");
  });
});

function recipe(id: string, tags: Recipe["tags"], extra: Partial<Recipe> = {}): Recipe {
  return {
    id, slug: id, title: id, image: null, step_images: [], tags, favorites: [], cooked: null, next_time: [],
    nutrition: null, created_at: `2026-01-0${id.length}`, updated_at: "", version: 1, document: doc, ...extra,
  } as Recipe;
}

describe("library facets", () => {
  const all = [
    recipe("a", { course: ["main"], cuisine: ["Mexican"] }),
    recipe("bb", { course: ["main"], cuisine: ["Italian"] }, { favorites: [""] }),
    recipe("ccc", { course: ["side"], cuisine: ["Mexican"] }, { cooked: { count: 2, last: "2026-09-01" } }),
  ];
  it("ORs within a group, ANDs across groups, and counts ignore the group's own selection", () => {
    const q = { ...emptyQuery, selected: { ...emptyQuery.selected, course: ["main"], cuisine: [] } };
    const r = applyLibrary(all, q, null);
    expect(r.recipes.map((x) => x.id).sort()).toEqual(["a", "bb"]);
    expect(r.counts.course.find((c) => c.value === "side")?.count).toBe(1);
    expect(r.counts.cuisine.find((c) => c.value === "Mexican")?.count).toBe(1);
    const both = applyLibrary(all, { ...q, selected: { ...q.selected, cuisine: ["Mexican", "Italian"] } }, null);
    expect(both.recipes).toHaveLength(2);
  });
  it("favorites, never-cooked and ranking", () => {
    expect(applyLibrary(all, { ...emptyQuery, favorites: true }, null).recipes.map((r) => r.id)).toEqual(["bb"]);
    expect(applyLibrary(all, { ...emptyQuery, sort: "never-cooked" }, null).recipes.map((r) => r.id)).not.toContain("ccc");
    expect(applyLibrary(all, { ...emptyQuery, sort: "relevance" }, ["ccc", "a"]).recipes.map((r) => r.id)).toEqual(["ccc", "a"]);
  });
  it("RRF merges lexical and semantic rankings", () => {
    expect(rrf([["a", "b", "c"], ["c", "a"]])[0]).toBe("a");
  });
});

describe("diff", () => {
  it("marks word-level insertions and deletions", () => {
    const parts = diffWords("2 tbsp. Kerrygold butter", "2 tablespoons unsalted butter");
    expect(parts.filter((p) => p.type === "del").map((p) => p.text).join("")).toContain("Kerrygold");
    expect(parts.filter((p) => p.type === "ins").map((p) => p.text).join("")).toContain("unsalted");
    expect(parts[0]).toEqual({ type: "same", text: "2 " });
    expect(parts.filter((p) => p.type !== "ins").map((p) => p.text).join("")).toBe("2 tbsp. Kerrygold butter");
    expect(parts.filter((p) => p.type !== "del").map((p) => p.text).join("")).toBe("2 tablespoons unsalted butter");
  });
  it("returns one same part for identical text", () => {
    expect(diffWords("Cook until golden.", "Cook until golden.")).toEqual([{ type: "same", text: "Cook until golden." }]);
  });
});
