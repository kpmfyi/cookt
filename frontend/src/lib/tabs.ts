// Open recipes: a row of tabs for cooking several recipes at once. Each open recipe owns a colour
// (--recipe-1 … --recipe-6) that its timers wear too, so a timer says which recipe it belongs to.
// The recipe on screen that isn't kept shows as a "preview" tab; cook mode, a timer or the keep
// button keeps it.
import { useEffect } from "react";
import { catalogStore, findRecipe, useCatalog } from "./catalog";
import { navigate, useLocation } from "./router";
import { createStore, readLocal, useStore, writeLocal } from "./store";
import { timerStore, type Timer } from "./timers";

export type TabMode = "read" | "cook";

export interface RecipeTab {
  id: string; // recipe id
  mode: TabMode; // where the tab reopens: the recipe page or cook mode
  color: number; // 0-based index into the recipe palette
}

export const RECIPE_COLORS = 6;
const MAX_TABS = 8;

export const tabStore = createStore<RecipeTab[]>(readLocal("cookt:tabs", []));
tabStore.subscribe(() => writeLocal("cookt:tabs", tabStore.get()));

export const useTabs = () => useStore(tabStore);

/** CSS custom property that tints a tab or timer in its recipe's colour. */
export function recipeColor(color: number | undefined): React.CSSProperties | undefined {
  return color === undefined ? undefined : ({ "--recipe": `var(--recipe-${(color % RECIPE_COLORS) + 1})` } as React.CSSProperties);
}

/** A recipe's colour: the one its timers already wear, else the colour fewest other recipes hold. */
export function pickColor(recipeId: string, tabs: RecipeTab[], timers: Pick<Timer, "recipeId" | "color">[]): number {
  const own = tabs.find((tab) => tab.id === recipeId)?.color ?? timers.find((t) => t.recipeId === recipeId && t.color !== undefined)?.color;
  if (own !== undefined) return own;
  const holders = Array.from({ length: RECIPE_COLORS }, () => new Set<string>());
  tabs.forEach((tab) => holders[tab.color % RECIPE_COLORS].add(tab.id));
  timers.forEach((t) => t.color !== undefined && holders[t.color % RECIPE_COLORS].add(t.recipeId));
  return holders.reduce((best, set, color) => (set.size < holders[best].size ? color : best), 0);
}

/** Keep a recipe open (or update where its tab reopens). Returns the recipe's colour. */
export function openTab(recipeId: string, mode?: TabMode): number {
  const tabs = tabStore.get();
  const existing = tabs.find((tab) => tab.id === recipeId);
  if (existing) {
    if (mode && mode !== existing.mode) tabStore.set(tabs.map((tab) => (tab.id === recipeId ? { ...tab, mode } : tab)));
    return existing.color;
  }
  const timers = timerStore.get();
  const tab: RecipeTab = { id: recipeId, mode: mode ?? "read", color: pickColor(recipeId, tabs, timers) };
  let next = [...tabs, tab];
  if (next.length > MAX_TABS) {
    // Drop the oldest tab that has no timers; a recipe with a timer stays reachable.
    const busy = new Set(timers.map((t) => t.recipeId));
    const drop = next.find((t) => t.id !== recipeId && !busy.has(t.id));
    if (drop) next = next.filter((t) => t !== drop);
  }
  tabStore.set(next);
  return tab.color;
}

export function setTabMode(recipeId: string, mode: TabMode): void {
  if (tabStore.get().some((tab) => tab.id === recipeId && tab.mode !== mode)) openTab(recipeId, mode);
}

/** Close a tab. Its timers keep running (and keep the colour); tapping one reopens the recipe. */
export function closeTab(recipeId: string): void {
  tabStore.set((tabs) => tabs.filter((tab) => tab.id !== recipeId));
}

export function tabPath(slug: string, mode: TabMode): string {
  return `/r/${slug}${mode === "cook" ? "/cook" : ""}`;
}

/** Switch to a recipe, reopening its tab if it was closed. */
export function showRecipe(recipeId: string, fallback: TabMode = "read"): void {
  const recipe = catalogStore.get().byId.get(recipeId);
  if (!recipe) return;
  const mode = tabStore.get().find((tab) => tab.id === recipeId)?.mode ?? fallback;
  openTab(recipeId, mode);
  navigate(tabPath(recipe.slug, mode));
}

export function recipeRoute(path: string): { slug: string; mode: TabMode | "edit" } | null {
  const match = path.match(/^\/r\/([^/]+)(\/cook|\/edit)?\/?$/);
  if (!match) return null;
  return { slug: decodeURIComponent(match[1]), mode: match[2] === "/cook" ? "cook" : match[2] === "/edit" ? "edit" : "read" };
}

/** The recipe on screen (recipe page, cook mode or editor), if any. */
export function useCurrentRecipe(): { id: string; mode: TabMode | "edit" } | null {
  const { path } = useLocation();
  const state = useCatalog();
  const route = recipeRoute(path);
  const recipe = route ? findRecipe(state, route.slug) : undefined;
  return recipe && route ? { id: recipe.id, mode: route.mode } : null;
}

/** Keep tabs in step with the route: cook mode keeps its recipe open; a tab remembers read vs cook. */
export function useTabTracking(): void {
  const current = useCurrentRecipe();
  const id = current?.id;
  const mode = current?.mode;
  useEffect(() => {
    if (!id) return;
    if (mode === "cook") openTab(id, "cook");
    else if (mode === "read") setTabMode(id, "read");
  }, [id, mode]);
}
