import { useEffect, useRef } from "react";
import { useCatalog } from "../lib/catalog";
import { formatRemaining } from "../lib/durations";
import { linkProps, navigate } from "../lib/router";
import { closeTab, openTab, recipeColor, tabPath, useCurrentRecipe, useTabs, type RecipeTab } from "../lib/tabs";
import { remainingOf, useNow, useTimers } from "../lib/timers";
import type { Recipe } from "../lib/types";
import { Icon } from "./Icon";
import { Emoji, heroEmoji } from "./Marks";
import styles from "./RecipeTabs.module.css";

/**
 * The row of open recipes. Shows whenever there is an open recipe other than the one on screen.
 * `page` sits sticky under the top navigation (and sets html[data-tabs] so sticky page bars move
 * down); `cook` sits in cook mode's own layout.
 */
export function RecipeTabs({ variant = "page" }: { variant?: "page" | "cook" }) {
  const tabs = useTabs();
  const state = useCatalog();
  const current = useCurrentRecipe();
  const timers = useTimers();
  const now = useNow(timers.some((timer) => timer.endsAt));
  const rowRef = useRef<HTMLElement>(null);

  const open = tabs.flatMap((tab) => {
    const recipe = state.byId.get(tab.id);
    return recipe ? [{ tab, recipe }] : [];
  });
  const shown = open.some(({ tab }) => tab.id !== current?.id);
  const previewRecipe = current && !open.some(({ tab }) => tab.id === current.id) ? state.byId.get(current.id) : undefined;

  useEffect(() => {
    if (variant !== "page") return;
    document.documentElement.toggleAttribute("data-tabs", shown);
    return () => document.documentElement.removeAttribute("data-tabs");
  }, [variant, shown]);

  // Scroll the row (only the row) so the recipe on screen is in view.
  useEffect(() => {
    const row = rowRef.current;
    const tab = row?.querySelector<HTMLElement>("li[data-current]");
    if (!row || !tab) return;
    const left = tab.getBoundingClientRect().left - row.getBoundingClientRect().left + row.scrollLeft;
    if (left < row.scrollLeft || left + tab.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollTo({ left: Math.max(0, left - 24), behavior: "smooth" });
    }
  }, [current?.id, shown, tabs.length]);

  if (!shown) return null;

  function close(tab: RecipeTab) {
    const index = open.findIndex((o) => o.tab.id === tab.id);
    closeTab(tab.id);
    if (tab.id !== current?.id) return;
    const neighbour = open[index + 1] ?? open[index - 1];
    if (neighbour) navigate(tabPath(neighbour.recipe.slug, neighbour.tab.mode));
  }

  return (
    <nav ref={rowRef} className={`${styles.row} ${variant === "cook" ? styles.cook : ""}`} aria-label="Open recipes">
      <ul className={styles.list}>
        {open.map(({ tab, recipe }) => {
          const mine = timers.filter((timer) => timer.recipeId === recipe.id);
          const ringing = mine.some((timer) => timer.done);
          const running = mine.filter((timer) => timer.endsAt).map((timer) => remainingOf(timer, now));
          const isCurrent = tab.id === current?.id;
          return (
            <li key={tab.id} className={styles.tab} style={recipeColor(tab.color)} data-current={isCurrent || undefined} data-ringing={ringing || undefined}>
              <a className={styles.link} aria-current={isCurrent ? "page" : undefined} {...linkProps(tabPath(recipe.slug, tab.mode))}>
                <TabFace recipe={recipe} ringing={ringing} />
                {!ringing && running.length > 0 && <span className={styles.time}>{formatRemaining(Math.min(...running))}</span>}
              </a>
              <button type="button" className={styles.action} onClick={() => close(tab)} aria-label={`Close ${recipe.title}`}>
                <Icon name="close" size={16} />
              </button>
            </li>
          );
        })}
        {previewRecipe && current && current.mode !== "edit" && (
          <li className={`${styles.tab} ${styles.preview}`} data-current>
            <span className={styles.link} aria-current="page">
              <TabFace recipe={previewRecipe} ringing={false} />
            </span>
            <button type="button" className={styles.action} onClick={() => openTab(previewRecipe.id, current.mode === "cook" ? "cook" : "read")} aria-label={`Keep ${previewRecipe.title} open`}>
              <Icon name="plus" size={16} />
            </button>
          </li>
        )}
      </ul>
    </nav>
  );
}

function TabFace({ recipe, ringing }: { recipe: Recipe; ringing: boolean }) {
  return (
    <>
      <span className={styles.dot} aria-hidden="true" />
      <Emoji char={ringing ? "🔔" : heroEmoji(recipe.tags)} size={16} animate={ringing ? "pulse" : undefined} />
      <span className={styles.title}>{recipe.title}</span>
    </>
  );
}
