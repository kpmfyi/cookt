import { useState } from "react";
import { Ticket } from "../components/Ticket";
import { put } from "../lib/api";
import { findRecipe, refreshCatalog, useCatalog } from "../lib/catalog";
import { ingredientBlocks, newId, stepBlocks, toIngredientSections, toInstructionSections, type IngredientBlock, type StepBlock } from "../lib/editing";
import { usePerson } from "../lib/prefs";
import { linkProps, navigate } from "../lib/router";
import type { Recipe, TagKind } from "../lib/types";
import styles from "./Edit.module.css";

export const TAXONOMY: Record<Exclude<TagKind, "personal" | "equipment">, string[]> = {
  course: ["main", "side", "soup", "salad", "sauce/condiment", "bread", "dessert", "breakfast", "snack", "drink"],
  protein: ["chicken", "pork", "beef", "lamb", "fish", "shellfish", "tofu/tempeh", "beans/legumes", "eggs", "none"],
  diet: ["vegetarian", "vegan", "gluten-free", "dairy-free"],
  cuisine: [],
};

export function Edit({ slug }: { slug: string }) {
  const state = useCatalog();
  const recipe = findRecipe(state, slug);
  if (state.status === "loading") return null;
  if (!recipe) return <p style={{ padding: 24 }}>Recipe not found.</p>;
  return <Editor key={recipe.id} recipe={recipe} />;
}

const num = (value: string) => (value.trim() === "" ? null : Number(value));

function Editor({ recipe }: { recipe: Recipe }) {
  const state = useCatalog();
  const person = usePerson();
  const doc = recipe.document;
  const [title, setTitle] = useState(recipe.title);
  const [meta, setMeta] = useState({
    credit: recipe.credit ?? "",
    source_url: recipe.source_url ?? "",
    source_site: recipe.source_site ?? "",
    source_author: recipe.source_author ?? "",
    description: recipe.description ?? "",
    prep_minutes: recipe.prep_minutes?.toString() ?? "",
    cook_minutes: recipe.cook_minutes?.toString() ?? "",
    total_minutes: recipe.total_minutes?.toString() ?? "",
    servings: recipe.servings?.toString() ?? "",
    yield_text: doc.yield_text ?? "",
  });
  const [ingredients, setIngredients] = useState<IngredientBlock[]>(ingredientBlocks(doc));
  const [steps, setSteps] = useState<StepBlock[]>(stepBlocks(doc));
  const [notes, setNotes] = useState(doc.notes.join("\n\n"));
  const [tags, setTags] = useState<Record<string, string[]>>({
    course: recipe.tags.course ?? [],
    protein: recipe.tags.protein ?? [],
    diet: recipe.tags.diet ?? [],
    cuisine: recipe.tags.cuisine ?? [],
    equipment: recipe.tags.equipment ?? [],
  });
  const hiddenTags = (recipe.tags.personal ?? []).filter((t) => t.startsWith("emoji:"));
  const [personal, setPersonal] = useState((recipe.tags.personal ?? []).filter((t) => !t.startsWith("emoji:")).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const cuisines = [...new Set([...(state.catalog?.recipes.flatMap((r) => r.tags.cuisine ?? []) ?? []), ...tags.cuisine])].sort();
  const field = (key: keyof typeof meta) => ({
    value: meta[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setMeta({ ...meta, [key]: e.target.value }),
  });
  const toggleTag = (kind: string, value: string) =>
    setTags({ ...tags, [kind]: tags[kind].includes(value) ? tags[kind].filter((v) => v !== value) : [...tags[kind], value] });

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const ingredient_sections = toIngredientSections(ingredients, doc);
    const instruction_sections = toInstructionSections(steps, doc);
    if (!title.trim()) return setError("A title is required.");
    if (!ingredient_sections.length) return setError("Add at least one ingredient.");
    if (!instruction_sections.length) return setError("Add at least one step.");
    setBusy(true);
    try {
      const saved = await put<{ slug: string }>(`/api/recipes/${recipe.id}`, {
        document: {
          ...doc,
          title: title.trim(),
          yield_text: meta.yield_text.trim() || null,
          notes: notes.split(/\n\s*\n/).map((n) => n.trim()).filter(Boolean),
          ingredient_sections,
          instruction_sections,
        },
        credit: meta.credit.trim() || null,
        source_url: meta.source_url.trim() || null,
        source_site: meta.source_site.trim() || null,
        source_author: meta.source_author.trim() || null,
        description: meta.description.trim() || null,
        prep_minutes: num(meta.prep_minutes),
        cook_minutes: num(meta.cook_minutes),
        total_minutes: num(meta.total_minutes),
        servings: num(meta.servings),
        personal_tags: [...hiddenTags, ...personal.split(",").map((t) => t.trim()).filter(Boolean)],
        course_tags: tags.course,
        protein_tags: tags.protein,
        diet_tags: tags.diet,
        cuisine_tags: tags.cuisine,
        equipment_tags: tags.equipment,
        person_id: person || null,
        expected_version: recipe.version,
      });
      refreshCatalog();
      navigate(`/r/${saved.slug}`, { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={styles.page} onSubmit={save}>
      <Ticket className={styles.sheet}>
        <div className={styles.head}>
          <h1>Edit recipe</h1>
        </div>
        <div className={styles.grid}>
          <label className={`${styles.field} ${styles.wide}`}>
            Title
            <input className={styles.titleInput} value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={240} />
          </label>
          <label className={styles.field}>Credit<input {...field("credit")} /></label>
          <label className={styles.field}>Source site<input {...field("source_site")} /></label>
          <label className={styles.field}>Author<input {...field("source_author")} /></label>
          <label className={`${styles.field} ${styles.wide}`}>Source URL<input type="url" {...field("source_url")} /></label>
          <label className={styles.field}>Prep (min)<input inputMode="numeric" {...field("prep_minutes")} /></label>
          <label className={styles.field}>Cook (min)<input inputMode="numeric" {...field("cook_minutes")} /></label>
          <label className={styles.field}>Total (min)<input inputMode="numeric" {...field("total_minutes")} /></label>
          <label className={styles.field}>Servings<input inputMode="decimal" {...field("servings")} /></label>
          <label className={`${styles.field} ${styles.wide}`}>Yield as written<input {...field("yield_text")} /></label>
          <label className={`${styles.field} ${styles.wide}`}>Description<textarea {...field("description")} /></label>
        </div>
      </Ticket>

      <Ticket className={styles.sheet}>
        <h2 className={styles.section}>Ingredients</h2>
        {ingredients.map((block, i) => (
          <div key={block.id} className={styles.block}>
            <div className={styles.blockHead}>
              <label className={styles.field}>
                Section heading <span className={styles.hint}>(optional)</span>
                <input value={block.heading} onChange={(e) => setIngredients(ingredients.map((b, j) => (j === i ? { ...b, heading: e.target.value } : b)))} />
              </label>
              {ingredients.length > 1 && (
                <button type="button" className={styles.link} onClick={() => setIngredients(ingredients.filter((_, j) => j !== i))}>Remove</button>
              )}
            </div>
            <label className={styles.field}>
              One ingredient per line
              <textarea rows={Math.max(5, block.text.split("\n").length + 1)} value={block.text} onChange={(e) => setIngredients(ingredients.map((b, j) => (j === i ? { ...b, text: e.target.value } : b)))} />
            </label>
          </div>
        ))}
        <button type="button" className={styles.link} onClick={() => setIngredients([...ingredients, { id: newId("ingredients"), heading: "", text: "" }])}>+ Add ingredient section</button>
      </Ticket>

      <Ticket className={styles.sheet}>
        <h2 className={styles.section}>Method</h2>
        {steps.map((block, i) => (
          <div key={block.id} className={styles.block}>
            <div className={styles.blockHead}>
              <label className={styles.field}>
                Section heading <span className={styles.hint}>(optional)</span>
                <input value={block.heading} onChange={(e) => setSteps(steps.map((b, j) => (j === i ? { ...b, heading: e.target.value } : b)))} />
              </label>
              {steps.length > 1 && (
                <button type="button" className={styles.link} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>Remove</button>
              )}
            </div>
            <label className={styles.field}>
              Steps, separated by a blank line
              <textarea rows={Math.max(8, block.text.split("\n").length + 2)} value={block.text} onChange={(e) => setSteps(steps.map((b, j) => (j === i ? { ...b, text: e.target.value } : b)))} />
            </label>
          </div>
        ))}
        <button type="button" className={styles.link} onClick={() => setSteps([...steps, { id: newId("instructions"), heading: "", text: "" }])}>+ Add method section</button>
        <label className={styles.field} style={{ marginTop: 16 }}>
          Notes <span className={styles.hint}>(paragraphs separated by a blank line)</span>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </Ticket>

      <Ticket className={styles.sheet}>
        <h2 className={styles.section}>Tags</h2>
        {(["course", "protein", "diet"] as const).map((kind) => (
          <div key={kind} className={styles.chips} role="group" aria-label={kind}>
            <span className={styles.chipLabel}>{kind}</span>
            {TAXONOMY[kind].map((value) => (
              <button key={value} type="button" className={styles.chip} aria-pressed={tags[kind].includes(value)} onClick={() => toggleTag(kind, value)}>{value}</button>
            ))}
          </div>
        ))}
        <div className={styles.chips} role="group" aria-label="cuisine">
          <span className={styles.chipLabel}>cuisine</span>
          {cuisines.map((value) => (
            <button key={value} type="button" className={styles.chip} aria-pressed={tags.cuisine.includes(value)} onClick={() => toggleTag("cuisine", value)}>{value}</button>
          ))}
          <button type="button" className={styles.link} onClick={() => {
            const value = prompt("New cuisine")?.trim();
            if (value) setTags({ ...tags, cuisine: [...tags.cuisine, value] });
          }}>+ cuisine</button>
        </div>
        <label className={styles.field}>
          Personal tags <span className={styles.hint}>(comma separated)</span>
          <input value={personal} onChange={(e) => setPersonal(e.target.value)} placeholder="weeknight, for guests" />
        </label>
      </Ticket>

      <div className={styles.actions}>
        {error && <span className={styles.error} role="alert">{error}</span>}
        <a className={styles.secondary} {...linkProps(`/r/${recipe.slug}`)}>Cancel</a>
        <button type="submit" className={styles.primary} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}
