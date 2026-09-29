// Library filtering: search ranking + multi-select facets (OR within a group, AND across groups).
import { createStore, readLocal, writeLocal } from "./store";
import type { Recipe } from "./types";

export const FACETS = ["course", "cuisine", "protein", "diet"] as const;
export type Facet = (typeof FACETS)[number];
export type Sort = "relevance" | "recent" | "az" | "last-cooked" | "never-cooked";

export interface LibraryQuery {
  q: string;
  selected: Record<Facet, string[]>;
  favorites: boolean;
  sort: Sort;
}

export const emptyQuery: LibraryQuery = {
  q: "",
  selected: { course: [], cuisine: [], protein: [], diet: [] },
  favorites: false,
  sort: "az",
};

export const libraryStore = createStore<LibraryQuery>(readLocal("cookt:library:v2", emptyQuery));
libraryStore.subscribe(() => writeLocal("cookt:library:v2", { ...libraryStore.get(), q: "" }));

/** Titles sort by their first letter, ignoring quotes and other leading punctuation. */
const sortKey = (title: string) => title.replace(/^[^\p{L}\p{N}]+/u, "");

const COURSE_ORDER = ["main", "side", "soup", "salad", "sauce/condiment", "bread", "dessert", "breakfast", "snack", "drink"];
const DIET_ORDER = ["vegetarian", "vegan", "gluten-free", "dairy-free"];

function matchesFacet(recipe: Recipe, facet: Facet, values: string[]): boolean {
  if (values.length === 0) return true;
  const have = recipe.tags[facet] ?? [];
  return values.some((value) => have.includes(value));
}

export interface FacetCount { value: string; count: number; selected: boolean }

export interface LibraryResult {
  recipes: Recipe[];
  counts: Record<Facet, FacetCount[]>;
  total: number;
}

export function applyLibrary(all: Recipe[], query: LibraryQuery, rankedIds: string[] | null): LibraryResult {
  let pool = all;
  if (rankedIds) {
    const byId = new Map(all.map((recipe) => [recipe.id, recipe]));
    pool = rankedIds.map((id) => byId.get(id)).filter((recipe): recipe is Recipe => Boolean(recipe));
  }
  if (query.favorites) pool = pool.filter((recipe) => recipe.favorites.length > 0);
  if (query.sort === "never-cooked") pool = pool.filter((recipe) => !recipe.cooked);

  const counts = {} as Record<Facet, FacetCount[]>;
  for (const facet of FACETS) {
    // Counts for a group ignore that group's own selection so options stay reachable.
    const base = pool.filter((recipe) =>
      FACETS.every((other) => other === facet || matchesFacet(recipe, other, query.selected[other])),
    );
    const tally = new Map<string, number>();
    for (const recipe of base) for (const value of recipe.tags[facet] ?? []) tally.set(value, (tally.get(value) ?? 0) + 1);
    for (const value of query.selected[facet]) if (!tally.has(value)) tally.set(value, 0);
    const order = facet === "course" ? COURSE_ORDER : facet === "diet" ? DIET_ORDER : null;
    counts[facet] = [...tally.entries()]
      .map(([value, count]) => ({ value, count, selected: query.selected[facet].includes(value) }))
      .sort((a, b) =>
        order
          ? (order.indexOf(a.value) + 1 || 99) - (order.indexOf(b.value) + 1 || 99)
          : b.count - a.count || a.value.localeCompare(b.value),
      );
  }

  const byTitle = (a: Recipe, b: Recipe) => sortKey(a.title).localeCompare(sortKey(b.title), undefined, { sensitivity: "base" });
  let recipes = pool.filter((recipe) => FACETS.every((facet) => matchesFacet(recipe, facet, query.selected[facet])));
  const keepRanking = rankedIds !== null && query.sort === "relevance";
  if (!keepRanking) {
    const compare: Record<Sort, (a: Recipe, b: Recipe) => number> = {
      relevance: (a, b) => b.created_at.localeCompare(a.created_at),
      recent: (a, b) => b.created_at.localeCompare(a.created_at),
      az: byTitle,
      "never-cooked": byTitle,
      "last-cooked": (a, b) => (b.cooked?.last ?? "").localeCompare(a.cooked?.last ?? "") || byTitle(a, b),
    };
    recipes = [...recipes].sort(compare[query.sort]);
  }
  return { recipes, counts, total: all.length };
}

/** Reciprocal-rank fusion of lexical and semantic rankings. */
export function rrf(lists: string[][], k = 60): string[] {
  const scores = new Map<string, number>();
  for (const list of lists) list.forEach((id, rank) => scores.set(id, (scores.get(id) ?? 0) + 1 / (k + rank + 1)));
  return [...scores.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
}
