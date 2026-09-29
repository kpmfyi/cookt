import { useCallback, useEffect, useState } from "react";
import { CookedDialog } from "../components/CookedDialog";
import { Icon } from "../components/Icon";
import { IngredientList } from "../components/IngredientList";
import { DishMarks, Emoji } from "../components/Marks";
import { ScaleControl } from "../components/ScaleControl";
import { StepText } from "../components/StepText";
import { api, del, post } from "../lib/api";
import { findRecipe, patchRecipe, refreshCatalog, useCatalog } from "../lib/catalog";
import { formatMinutes } from "../lib/durations";
import { formatQuantity } from "../lib/fractions";
import { usePerson, useUnits } from "../lib/prefs";
import { loadProgress, saveProgress } from "../lib/progress";
import { linkProps, navigate } from "../lib/router";
import { closeTab, openTab, useTabs } from "../lib/tabs";
import type { Recipe as RecipeT } from "../lib/types";
import { RecipeExtras, type RecipeDetail } from "./RecipeExtras";
import styles from "./Recipe.module.css";

export function useRecipeDetail(id: string | undefined) {
  const [detail, setDetail] = useState<RecipeDetail | null>(null);
  const reload = useCallback(() => {
    if (!id) return;
    api<RecipeDetail>(`/api/recipes/${id}`).then(setDetail).catch(() => undefined);
  }, [id]);
  useEffect(reload, [reload]);
  return { detail, reload };
}

export function Recipe({ slug }: { slug: string }) {
  const state = useCatalog();
  const recipe = findRecipe(state, slug);
  if (state.status === "loading") return null;
  if (!recipe) return <MissingRecipe slug={slug} />;
  return <RecipeView key={recipe.id} recipe={recipe} />;
}

function MissingRecipe({ slug }: { slug: string }) {
  const [resolved, setResolved] = useState<string | null>(null);
  useEffect(() => {
    // Old recipe-table / cookt2 ids resolve through the alias table.
    api<{ slug: string }>(`/api/recipes/${slug}`)
      .then((r) => (r.slug !== slug ? navigate(`/r/${r.slug}`, { replace: true }) : setResolved("missing")))
      .catch(() => setResolved("missing"));
  }, [slug]);
  if (!resolved) return null;
  return (
    <div className={styles.missing}>
      <h1>Recipe not found</h1>
      <p><a {...linkProps("/")}>Back to the library</a></p>
    </div>
  );
}

const PLAIN_YIELD = /^\s*(serves|servings?|yield)?\s*:?\s*\d+(\s*(-|to)\s*\d+)?\s*(servings?(\(s\))?|people|portions)?\s*$/i;

function RecipeView({ recipe }: { recipe: RecipeT }) {
  const units = useUnits();
  const person = usePerson();
  const [tab, setTab] = useState<"ingredients" | "method">("ingredients");
  const [progress, setProgress] = useState(() => loadProgress(recipe.id));
  const [cookedOpen, setCookedOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const { detail, reload } = useRecipeDetail(recipe.id);
  const checked = new Set(progress.checked);
  const favorite = recipe.favorites.includes(person) || (!person && recipe.favorites.length > 0);
  const kept = useTabs().some((t) => t.id === recipe.id);

  useEffect(() => {
    document.title = `${recipe.title} · cookt`;
    const onScroll = () => setScrolled(window.scrollY > 120);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.title = "cookt";
    };
  }, [recipe.title]);

  const update = (patch: Partial<typeof progress>) => {
    const next = { ...progress, ...patch };
    setProgress(next);
    saveProgress(recipe.id, next);
  };
  const toggle = (id: string) =>
    update({ checked: checked.has(id) ? progress.checked.filter((x) => x !== id) : [...progress.checked, id] });

  async function toggleFavorite() {
    const on = !favorite;
    patchRecipe(recipe.id, {
      favorites: on ? [...recipe.favorites, person] : recipe.favorites.filter((p) => p !== person && (person || p !== "")),
    });
    try {
      if (!on && !person) {
        await Promise.all(recipe.favorites.map((p) => post(`/api/recipes/${recipe.id}/favorite`, { person_id: p, on: false })));
      } else {
        await post(`/api/recipes/${recipe.id}/favorite`, { person_id: person, on });
      }
    } finally {
      refreshCatalog();
    }
  }

  async function resolveNote(id: string) {
    patchRecipe(recipe.id, { next_time: recipe.next_time.filter((n) => n.id !== id) });
    await del(`/api/notes/${id}`).catch(() => undefined);
    refreshCatalog();
  }

  const doc = recipe.document;
  const total = recipe.total_minutes ?? ((recipe.prep_minutes ?? 0) + (recipe.cook_minutes ?? 0) || null);
  const facts: React.ReactNode[] = [];
  if (total) facts.push(<span key="time"><Emoji char="⏱️" label="Time" size={16} /> {formatMinutes(total)}</span>);
  if (recipe.servings) facts.push(<span key="serv"><Emoji char="👥" label="Servings" size={16} /> {formatQuantity(recipe.servings)}</span>);
  else if (doc.yield_text) facts.push(<span key="yield"><Emoji char="👥" label="Yield" size={16} /> {doc.yield_text}</span>);
  if (recipe.source_site || recipe.credit) {
    const name = recipe.credit ?? recipe.source_site;
    facts.push(
      <span key="src">
        <Emoji char="🔗" label="Source" size={16} /> {recipe.source_url ? <a href={recipe.source_url} target="_blank" rel="noreferrer">{name}</a> : name}
      </span>,
    );
  }
  let stepNumber = 0;

  return (
    <article className={styles.page}>
      <div className={styles.topbar}>
        <a className={styles.iconButton} {...linkProps("/")} aria-label="Back to library"><Icon name="back" /></a>
        <span className={styles.topTitle} data-shown={scrolled}>{recipe.title}</span>
        <button
          type="button"
          className={`${styles.iconButton} ${styles.keep}`}
          aria-pressed={kept}
          aria-label={kept ? "Close this recipe's tab" : "Keep open in a tab"}
          title={kept ? "Open in a tab" : "Keep open in a tab"}
          onClick={() => (kept ? closeTab(recipe.id) : openTab(recipe.id, "read"))}
        >
          <Emoji char="📌" size={20} />
        </button>
        <button type="button" className={styles.iconButton} aria-pressed={favorite} aria-label={favorite ? "Remove favorite" : "Add favorite"} onClick={toggleFavorite}>
          <Emoji key={String(favorite)} char={favorite ? "❤️" : "🤍"} size={22} animate="pop" />
        </button>
        <a className={styles.cook} {...linkProps(`/r/${recipe.slug}/cook`)}><Emoji char="🔥" size={18} /> Cook</a>
      </div>

      <div className={styles.layout}>
        <header className={styles.head}>
          <h1>{recipe.title}</h1>
          <p className={styles.facts}>
            <DishMarks tags={recipe.tags} cuisineName courseName size={22} className={styles.marks} />
            {facts}
          </p>
        </header>

        <div className={styles.tabs} role="tablist" aria-label="Recipe sections">
          <button type="button" role="tab" aria-selected={tab === "ingredients"} onClick={() => setTab("ingredients")}>Ingredients</button>
          <button type="button" role="tab" aria-selected={tab === "method"} onClick={() => setTab("method")}>Method</button>
        </div>

        <div className={styles.sheet}>
          <section className={`${styles.panel} ${styles.ingredientsPanel} ${tab !== "ingredients" ? styles.hiddenPhone : ""}`} aria-labelledby="ing-h">
            <div className={styles.panelHead}>
              <h2 id="ing-h">Ingredients</h2>
              {doc.yield_text && !PLAIN_YIELD.test(doc.yield_text) && <span className={styles.yield}>{doc.yield_text}</span>}
            </div>
            <ScaleControl factor={progress.factor} servings={recipe.servings} onFactor={(factor) => update({ factor })} />
            <div className={styles.ingredientBody}>
              <IngredientList sections={doc.ingredient_sections} factor={progress.factor} units={units} checked={checked} onToggle={toggle} />
            </div>
            {progress.checked.length > 0 && (
              <button type="button" className={styles.small} onClick={() => update({ checked: [] })}>Clear checks</button>
            )}
          </section>

          <section className={`${styles.panel} ${styles.methodPanel} ${tab !== "method" ? styles.hiddenPhone : ""}`} aria-labelledby="method-h">
            <div className={styles.panelHead}><h2 id="method-h">Method</h2></div>
            {recipe.next_time.length > 0 && (
              <aside className={styles.nextTime} aria-label="For next time">
                <h3>For next time</h3>
                <ul>
                  {recipe.next_time.map((note) => (
                    <li key={note.id}>
                      <span>{note.text}</span>
                      <button type="button" className={styles.small} onClick={() => resolveNote(note.id)}>Done</button>
                    </li>
                  ))}
                </ul>
              </aside>
            )}
            {doc.instruction_sections.map((section) => (
              <section key={section.id} className={styles.stepSection}>
                {section.heading && <h3 className={styles.stepHeading}>{section.heading}</h3>}
                <ol className={styles.steps}>
                  {section.steps.map((step) => {
                    stepNumber += 1;
                    return (
                      <li key={step.id} className={styles.step}>
                        <span className={styles.num} aria-hidden="true">{stepNumber}</span>
                        <div className={styles.stepText}>
                          <StepText text={step.text} units={units} recipeId={recipe.id} recipeTitle={recipe.title} stepLabel={`Step ${stepNumber}`} />
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
            {doc.notes.length > 0 && (
              <div className={styles.notes}>
                {doc.notes.map((note, i) => <p key={i}>{note}</p>)}
              </div>
            )}
            <div className={styles.buttonRow}>
              <button type="button" className={styles.primaryButton} onClick={() => setCookedOpen(true)}>
                <Icon name="check" size={18} /> Cooked it
              </button>
              <a className={styles.secondaryButton} {...linkProps(`/r/${recipe.slug}/cook`)}>Cook mode</a>
            </div>
          </section>
        </div>

        <RecipeExtras recipe={recipe} detail={detail} reload={reload} />
      </div>
      <CookedDialog recipeId={recipe.id} open={cookedOpen} onClose={() => setCookedOpen(false)} onSaved={reload} />
    </article>
  );
}
