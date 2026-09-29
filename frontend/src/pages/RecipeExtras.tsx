import { useEffect, useState } from "react";
import { HeroPicker } from "../components/HeroPicker";
import { DishMarks, Emoji, Mark } from "../components/Marks";
import { useCatalog } from "../lib/catalog";
import { api, del, post } from "../lib/api";
import { refreshCatalog } from "../lib/catalog";
import { linkProps } from "../lib/router";
import type { Recipe } from "../lib/types";
import styles from "./Recipe.module.css";
import extra from "./RecipeExtras.module.css";

export interface CookEntry { id: string; cooked_on: string; person?: string | null; make_again?: number | null; note?: string | null }
export interface Revision { id: string; version: number; reason: string; created_at: string }
export interface BreakdownLine {
  ingredient_id: string;
  source_text: string;
  food?: string | null;
  fdc_id?: number | null;
  grams?: number | null;
  kcal?: number | null;
  confidence?: string | null;
  excluded?: boolean;
  reason?: string | null;
}
export interface RecipeDetail {
  id: string;
  cook_log: CookEntry[];
  revisions: Revision[];
  tags: { kind: string; value: string; source: string; confidence?: number | null; evidence?: string | null }[];
  nutrition: {
    source: string;
    confidence?: string | null;
    per_serving: Record<string, number | null>;
    breakdown?: { lines?: BreakdownLine[]; mass_matched_pct?: number | null; servings_assumed?: boolean } | null;
    servings?: number | null;
  } | null;
  aliases: { alias: string; system: string }[];
}

interface Pairing { id: string; slug: string; title: string; course?: string; reason: string }
interface Pairings { build_a_meal: Pairing[]; more_like_this: Pairing[] }

/* key, emoji, spoken label, unit */
const NUTRIENTS: [string, string, string, string][] = [
  ["kcal", "🔥", "calories", " kcal"],
  ["protein_g", "💪", "protein", "g"],
  ["carbs_g", "🌾", "carbs", "g"],
  ["fat_g", "🧈", "fat", "g"],
  ["fiber_g", "🥦", "fiber", "g"],
  ["sodium_mg", "🧂", "sodium", "mg"],
];

function formatDay(day: string): string {
  const d = new Date(`${day}T12:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() === new Date().getFullYear() ? undefined : "2-digit" });
}

/**
 * Everything below the recipe itself, in reading order: plan/shop, nutrition, cook log,
 * pairings, about. Plain ruled sections with small headings so none of it competes with
 * the ingredients and method.
 */
export function RecipeExtras({ recipe, detail, reload }: { recipe: Recipe; detail: RecipeDetail | null; reload: () => void }) {
  const [pairings, setPairings] = useState<Pairings | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    api<Pairings>(`/api/recipes/${recipe.id}/pairings`).then(setPairings).catch(() => setPairings(null));
  }, [recipe.id]);

  async function restore(revision: Revision) {
    if (!confirm(`Restore the version from ${new Date(revision.created_at).toLocaleString()}? The current version stays in history.`)) return;
    await post(`/api/recipes/${recipe.id}/revisions/${revision.id}/restore`, {});
    refreshCatalog();
    reload();
  }

  async function removeCook(entry: CookEntry) {
    if (!confirm("Remove this cook-log entry?")) return;
    await del(`/api/cooklog/${entry.id}`);
    refreshCatalog();
    reload();
  }

  const nutrition = detail?.nutrition;
  const personalTags = (recipe.tags.personal ?? []).filter((t) => !t.startsWith("emoji:"));
  const nutrientBits = nutrition
    ? NUTRIENTS.filter(([key]) => nutrition.per_serving[key] != null).map(([key, emoji, label, unit]) => (
        <span key={key} title={label}><Emoji char={emoji} label={label} size={18} /> <b>{Math.round(nutrition.per_serving[key] as number)}{unit}</b></span>
      ))
    : [];

  return (
    <div className={styles.more}>
      <section className={styles.block} aria-labelledby="plan-h">
        <h2 id="plan-h">Plan</h2>
        <PlanShopRow recipeId={recipe.id} />
      </section>

      {nutrition && nutrientBits.length > 0 && (
        <section className={styles.block} aria-labelledby="nut-h">
          <h2 id="nut-h">Per serving</h2>
          <p className={extra.nutrients}>{nutrientBits}</p>
          <p className={styles.muted}>
            {nutrition.servings ? `${nutrition.servings} servings` : "Servings unknown"}
            {nutrition.breakdown?.servings_assumed ? " (assumed)" : ""}
            {nutrition.source === "publisher" ? " · from the source" : ` · ${nutrition.confidence ?? "low"} confidence`}
          </p>
          {(nutrition.breakdown?.lines?.length ?? 0) > 0 && (
            <details className={extra.details}>
              <summary>Per-ingredient breakdown</summary>
              <NutritionBreakdown recipeId={recipe.id} lines={nutrition.breakdown!.lines!} onChanged={reload} />
            </details>
          )}
        </section>
      )}

      {detail && detail.cook_log.length > 0 && (
        <section className={styles.block} aria-labelledby="log-h">
          <h2 id="log-h">Cooked {detail.cook_log.length === 1 ? "once" : `${detail.cook_log.length} times`}</h2>
          <ul className={styles.log}>
            {detail.cook_log.map((entry) => (
              <li key={entry.id}>
                <time dateTime={entry.cooked_on}>{formatDay(entry.cooked_on)}</time>
                <span>
                  {entry.person ?? "Household"}
                  {entry.make_again === 1 && <> <Emoji char="👍" label="make again" size={15} /></>}
                  {entry.make_again === 0 && <> <Emoji char="👎" label="wouldn't make again" size={15} /></>}
                  {entry.note && <><br /><span className={styles.muted}>{entry.note}</span></>}
                </span>
                <button type="button" className={styles.small} onClick={() => removeCook(entry)} aria-label="Remove entry">remove</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {pairings && pairings.build_a_meal.length > 0 && (
        <section className={styles.block} aria-labelledby="pair-h">
          <h2 id="pair-h">Goes with</h2>
          <ul className={extra.links}>
            {pairings.build_a_meal.map((p) => (
              <li key={p.id}>
                <a {...linkProps(`/r/${p.slug}`)}>{p.title}</a>
                <PairMarks id={p.id} course={p.course} />
                <PairActions recipeId={p.id} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {pairings && pairings.more_like_this.length > 0 && (
        <section className={styles.block} aria-labelledby="like-h">
          <h2 id="like-h">Similar</h2>
          <ul className={extra.links}>
            {pairings.more_like_this.map((p) => (
              <li key={p.id}><a {...linkProps(`/r/${p.slug}`)}>{p.title}</a><PairMarks id={p.id} /></li>
            ))}
          </ul>
        </section>
      )}

      <section className={styles.block} aria-labelledby="about-h">
        <h2 id="about-h">About</h2>
        {recipe.description && <p className={extra.description}>{recipe.description}</p>}
        {personalTags.length > 0 && <p className={extra.tags}>🏷️ {personalTags.join(" · ")}</p>}
        {recipe.source_url && (
          <p className={extra.source}>
            <a href={recipe.source_url} target="_blank" rel="noreferrer">{recipe.source_site || new URL(recipe.source_url).hostname}</a>
            {recipe.source_author && <span className={styles.muted}> · {recipe.source_author}</span>}
          </p>
        )}
        <p className={styles.quietRow}>
          <a className={styles.quietButton} {...linkProps(`/r/${recipe.slug}/edit`)}>Edit</a>
          <button
            type="button"
            className={styles.quietButton}
            onClick={() => post(`/api/recipes/${recipe.id}/enrich`, {}).then(() => alert("Queued: tags, pairing profile, embedding and nutrition will refresh in a minute or two."))}
          >
            Re-run tagging
          </button>
          <button type="button" className={styles.quietButton} aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
            History{detail ? ` (${detail.revisions.length})` : ""}
          </button>
          <HeroPicker recipe={recipe} />
        </p>
        {showHistory && detail && (
          detail.revisions.length === 0 ? (
            <p className={styles.muted}>No earlier versions.</p>
          ) : (
            <ul className={styles.log}>
              {detail.revisions.map((revision) => (
                <li key={revision.id}>
                  <time dateTime={revision.created_at}>v{revision.version}</time>
                  <span>{revision.reason} · {new Date(revision.created_at).toLocaleDateString()}</span>
                  <button type="button" className={styles.small} onClick={() => restore(revision)}>restore</button>
                </li>
              ))}
            </ul>
          )
        )}
      </section>
    </div>
  );
}

/** Cuisine, course and diet of a linked recipe (from the offline catalog), so the list scans without words. */
function PairMarks({ id, course }: { id: string; course?: string }) {
  const { byId } = useCatalog();
  const recipe = byId.get(id);
  if (recipe) return <DishMarks tags={recipe.tags} size={18} className={extra.marks} />;
  return course ? <Mark kind="course" value={course} size={18} className={extra.marks} /> : null;
}

function PairActions({ recipeId }: { recipeId: string }) {
  const [done, setDone] = useState<string | null>(null);
  async function addToPlan() {
    const day = new Date();
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    await post("/api/plan", { day: iso, recipe_id: recipeId }).then(() => setDone("Planned today")).catch(() => setDone("Couldn't add"));
  }
  async function addToList() {
    await post("/api/shopping/from-recipes", { recipe_ids: [recipeId] }).then(() => setDone("Added to list")).catch(() => setDone("Couldn't add"));
  }
  if (done) return <span className={extra.done}>{done}</span>;
  return (
    <span className={extra.pairActions}>
      <button type="button" onClick={addToPlan} aria-label="Add to plan"><Emoji char="📅" size={17} /></button>
      <button type="button" onClick={addToList} aria-label="Add to shopping list"><Emoji char="🛒" size={17} /></button>
    </span>
  );
}

function NutritionBreakdown({ recipeId, lines, onChanged }: { recipeId: string; lines: BreakdownLine[]; onChanged: () => void }) {
  async function exclude(line: BreakdownLine) {
    await post(`/api/recipes/${recipeId}/nutrition/override`, { ingredient_id: line.ingredient_id, fdc_id: null });
    onChanged();
  }
  async function change(line: BreakdownLine) {
    const query = prompt(`Match “${line.source_text}” to which food? (e.g. “butter, unsalted”)`, line.food ?? "");
    if (!query) return;
    const { results } = await api<{ results: { fdc_id: number; description: string }[] }>(`/api/fdc/search?q=${encodeURIComponent(query)}`);
    if (!results.length) return alert("No USDA food matched that.");
    const pick = results[0];
    const grams = prompt(`Grams of “${pick.description}” in this line?`, line.grams ? String(Math.round(line.grams)) : "");
    await post(`/api/recipes/${recipeId}/nutrition/override`, {
      ingredient_id: line.ingredient_id,
      fdc_id: pick.fdc_id,
      grams: grams ? Number(grams) : null,
    });
    onChanged();
  }
  return (
    <ul className={extra.breakdown}>
      {lines.map((line) => (
        <li key={line.ingredient_id} data-excluded={line.excluded || undefined}>
          <span className={extra.lineText}>{line.source_text}</span>
          <span className={extra.lineMatch}>
            {line.excluded ? <em>excluded{line.reason ? ` — ${line.reason}` : ""}</em> : line.food}
          </span>
          <span className={extra.lineNums}>
            {line.grams != null && !line.excluded && <span>{Math.round(line.grams)} g</span>}
            {line.kcal != null && !line.excluded && <span>{Math.round(line.kcal)} kcal</span>}
            <span className={extra.rowActions}>
              <button type="button" onClick={() => change(line)}>change</button>
              {!line.excluded && <button type="button" onClick={() => exclude(line)}>exclude</button>}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function PlanShopRow({ recipeId }: { recipeId: string }) {
  const [message, setMessage] = useState("");
  const [day, setDay] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
  async function plan() {
    await post("/api/plan", { day, recipe_id: recipeId })
      .then(() => setMessage(`Planned for ${new Date(`${day}T12:00`).toLocaleDateString(undefined, { weekday: "long" })}`))
      .catch(() => setMessage("Couldn't reach the server"));
  }
  async function shop() {
    await post<{ added: number; merged: number }>("/api/shopping/from-recipes", { recipe_ids: [recipeId] })
      .then((r) => setMessage(`Shopping list: ${r.added} added, ${r.merged} merged`))
      .catch(() => setMessage("Couldn't reach the server"));
  }
  return (
    <div className={extra.planRow}>
      <span className={extra.planGroup}>
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Plan for day" className={extra.dateInput} />
        <button type="button" className={styles.secondaryButton} onClick={plan}>Add to plan</button>
      </span>
      <button type="button" className={styles.secondaryButton} onClick={shop}>Add to shopping list</button>
      {message && <span className={extra.done} role="status">{message}</span>}
    </div>
  );
}
