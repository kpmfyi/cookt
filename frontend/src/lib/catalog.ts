// The whole catalog lives on the client: browse, search, read and cook work offline.
import MiniSearch, { type SearchResult } from "minisearch";
import { api } from "./api";
import { createStore, useStore } from "./store";
import type { Catalog, Recipe, TagKind } from "./types";

export interface CatalogState {
  status: "loading" | "ready" | "error";
  catalog: Catalog | null;
  byId: Map<string, Recipe>;
  error?: string;
}

export const catalogStore = createStore<CatalogState>({ status: "loading", catalog: null, byId: new Map() });
let index: MiniSearch<SearchDoc> | null = null;

interface SearchDoc {
  id: string;
  title: string;
  tags: string;
  ingredients: string;
  source: string;
  notes: string;
}

function ingredientNames(recipe: Recipe): string {
  return recipe.document.ingredient_sections
    .flatMap((section) => section.ingredients.map((item) => item.name || item.source_text))
    .join(" · ");
}

function toDoc(recipe: Recipe): SearchDoc {
  return {
    id: recipe.id,
    title: recipe.title,
    tags: Object.values(recipe.tags).flat().join(" · "),
    ingredients: ingredientNames(recipe),
    source: [recipe.source_site, recipe.source_author, recipe.credit].filter(Boolean).join(" · "),
    notes: [
      recipe.description,
      ...recipe.document.notes,
      ...recipe.next_time.map((note) => note.text),
    ].filter(Boolean).join(" "),
  };
}

export const SEARCH_OPTIONS = {
  boost: { title: 6, tags: 3, ingredients: 2, source: 1.5, notes: 1 },
  prefix: (term: string) => term.length >= 2,
  // Typo tolerance: 1 edit from 5 chars, 2 edits from 8 ("carnitsa" → carnitas).
  fuzzy: (term: string) => (term.length >= 8 ? 2 : term.length >= 5 ? 1 : 0),
  weights: { fuzzy: 0.3, prefix: 0.7 },
};

export function buildIndex(recipes: Recipe[]): MiniSearch<SearchDoc> {
  const mini = new MiniSearch<SearchDoc>({
    fields: ["title", "tags", "ingredients", "source", "notes"],
    storeFields: [],
    searchOptions: SEARCH_OPTIONS,
    processTerm: (term) => term.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase(),
  });
  mini.addAll(recipes.map(toDoc));
  return mini;
}

export function searchIds(query: string): string[] {
  if (!index || !query.trim()) return [];
  let results: SearchResult[] = index.search(query, { combineWith: "AND" });
  if (results.length === 0) results = index.search(query, { combineWith: "OR" });
  // Drop the weak tail (fuzzy hits on unrelated words) relative to the best match.
  const floor = (results[0]?.score ?? 0) * 0.15;
  return results.filter((result) => result.score >= floor).map((result) => String(result.id));
}

export function setCatalog(catalog: Catalog): void {
  const byId = new Map(catalog.recipes.map((recipe) => [recipe.id, recipe]));
  index = buildIndex(catalog.recipes);
  catalogStore.set({ status: "ready", catalog, byId });
}

let inflight: Promise<void> | null = null;

export function loadCatalog(): Promise<void> {
  inflight ??= api<Catalog>("/api/catalog")
    .then(setCatalog)
    .catch((error: Error) => {
      if (!catalogStore.get().catalog) catalogStore.set({ status: "error", catalog: null, byId: new Map(), error: error.message });
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Patch one recipe locally after a write, then refresh from the server in the background. */
export function patchRecipe(id: string, patch: Partial<Recipe>): void {
  const state = catalogStore.get();
  if (!state.catalog) return;
  const recipes = state.catalog.recipes.map((recipe) => (recipe.id === id ? { ...recipe, ...patch } : recipe));
  setCatalog({ ...state.catalog, recipes });
}

export function refreshCatalog(): void {
  void loadCatalog();
}

export const useCatalog = () => useStore(catalogStore);

export function findRecipe(state: CatalogState, key: string): Recipe | undefined {
  return state.byId.get(key) ?? state.catalog?.recipes.find((recipe) => recipe.slug === key);
}

export function tagValues(recipe: Recipe, kind: TagKind): string[] {
  return recipe.tags[kind] ?? [];
}

export function primaryCourse(recipe: Recipe): string {
  return tagValues(recipe, "course")[0] ?? "none";
}

// Exposed for the latency acceptance test (e2e/acceptance.spec.ts).
(window as unknown as { __cooktSearch: typeof searchIds }).__cooktSearch = searchIds;
