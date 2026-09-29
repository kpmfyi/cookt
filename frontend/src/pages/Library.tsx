import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { FacetPanel } from "../components/FacetBar";
import { Icon } from "../components/Icon";
import { DishMarks, Emoji } from "../components/Marks";
import { PersonPicker } from "../components/PersonPicker";
import { api } from "../lib/api";
import { searchIds, useCatalog } from "../lib/catalog";
import { formatMinutes } from "../lib/durations";
import { applyLibrary, emptyQuery, type Facet, libraryStore, rrf, type Sort } from "../lib/library";
import { linkProps } from "../lib/router";
import { useStore } from "../lib/store";
import type { Recipe } from "../lib/types";
import styles from "./Library.module.css";

const PAGE = 24;

const SORTS: [Sort, string][] = [
  ["relevance", "Best match"],
  ["az", "A to Z"],
  ["recent", "Newest first"],
  ["last-cooked", "Recently cooked"],
  ["never-cooked", "Never cooked"],
];

const MONTH: Intl.DateTimeFormatOptions = { month: "long", year: "numeric" };
const DAY: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };

/** The heading a row sits under, so the order is legible: a letter, or the month it was added / cooked. */
function groupOf(recipe: Recipe, sort: Sort): string | null {
  if (sort === "az" || sort === "never-cooked") {
    const first = recipe.title.replace(/^[^\p{L}\p{N}]+/u, "").charAt(0).toUpperCase();
    return /\p{L}/u.test(first) ? first : "#";
  }
  if (sort === "recent") return `Added ${new Date(recipe.created_at).toLocaleDateString(undefined, MONTH)}`;
  if (sort === "last-cooked") {
    return recipe.cooked ? `Cooked ${new Date(`${recipe.cooked.last.slice(0, 10)}T12:00`).toLocaleDateString(undefined, MONTH)}` : "Not cooked yet";
  }
  return null;
}

/** The fact that explains the row's position under a date sort. */
function sortFact(recipe: Recipe, sort: Sort): string | null {
  if (sort === "recent") return `added ${new Date(recipe.created_at).toLocaleDateString(undefined, DAY)}`;
  if (sort === "last-cooked" && recipe.cooked) {
    return `cooked ${new Date(`${recipe.cooked.last.slice(0, 10)}T12:00`).toLocaleDateString(undefined, DAY)}${recipe.cooked.count > 1 ? `, ${recipe.cooked.count}×` : ""}`;
  }
  return null;
}

function useSemantic(query: string): { ids: string[]; related: Set<string> } | null {
  const [result, setResult] = useState<{ q: string; ids: string[]; related: Set<string> } | null>(null);
  useEffect(() => {
    const q = query.trim();
    if (q.length < 3 || !navigator.onLine) return;
    const controller = new AbortController();
    const handle = setTimeout(() => {
      api<{ results: { id: string; score: number }[] }>(`/api/search/semantic?q=${encodeURIComponent(q)}&limit=40`, {
        signal: controller.signal,
      })
        .then(({ results }) => {
          const good = results.filter((item) => item.score >= 0.42);
          setResult({ q, ids: good.map((item) => item.id), related: new Set(good.map((item) => item.id)) });
        })
        .catch(() => undefined);
    }, 280);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [query]);
  return result && result.q === query.trim() ? result : null;
}

/** The row's words: how long. What kind of dish it is comes from the marks. */
function metaBits(recipe: Recipe): string[] {
  return [formatMinutes(recipe.total_minutes ?? ((recipe.prep_minutes ?? 0) + (recipe.cook_minutes ?? 0) || null))].filter(
    (bit): bit is string => Boolean(bit),
  );
}

export function Library() {
  const state = useCatalog();
  const query = useStore(libraryStore);
  const [text, setText] = useState(query.q);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const deferred = useDeferredValue(text);
  const inputRef = useRef<HTMLInputElement>(null);
  const recipes = state.catalog?.recipes ?? [];

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const semantic = useSemantic(deferred);
  const lexical = useMemo(() => (deferred.trim() ? searchIds(deferred) : null), [deferred, state.catalog]);
  const ranked = useMemo(() => {
    if (!lexical) return null;
    if (!semantic) return lexical;
    return rrf([lexical, semantic.ids]);
  }, [lexical, semantic]);

  const effective = { ...query, sort: deferred.trim() ? query.sort : query.sort === "relevance" ? "recent" : query.sort } as typeof query;
  const result = useMemo(() => applyLibrary(recipes, effective, ranked), [recipes, ranked, effective.selected, effective.favorites, effective.sort]);
  const lexicalSet = useMemo(() => new Set(lexical ?? []), [lexical]);

  const setQuery = (patch: Partial<typeof query>) => libraryStore.set({ ...libraryStore.get(), ...patch });
  const toggle = (facet: Facet, value: string) => {
    const current = query.selected[facet];
    setQuery({
      selected: {
        ...query.selected,
        [facet]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
      },
    });
  };
  const onText = (value: string) => {
    setText(value);
    if (value.trim() && !text.trim()) setQuery({ sort: "relevance" });
  };
  // Windowed rendering: keystrokes only re-render what's on screen; more rows load on scroll.
  const [shown, setShown] = useState(PAGE);
  const sentinel = useRef<HTMLLIElement>(null);
  useEffect(() => setShown(PAGE), [ranked, query.selected, query.favorites, query.sort]);
  useEffect(() => {
    const node = sentinel.current;
    if (!node) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setShown((n) => n + PAGE);
    }, { rootMargin: "800px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [shown, result.recipes.length]);

  const selectedValues = Object.values(query.selected).flat();
  const filtersActive = query.favorites || selectedValues.length > 0;

  if (state.status === "loading") return <p className={styles.skeleton}><Emoji char="🍳" size={40} animate="pulse" /><br />Loading the catalog…</p>;
  if (state.status === "error") {
    return (
      <div className={styles.empty}>
        <Emoji char="📡" size={56} />
        <h2>Can't reach the kitchen</h2>
        <p>{state.error}. Open cookt once while online so it can keep a copy for offline use.</p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <div className={styles.searchRow}>
          <label className={styles.search}>
            <span className="visually-hidden">Search recipes</span>
            <span className={styles.searchIcon}><Emoji char="🔍" size={18} /></span>
            <input
              ref={inputRef}
              type="search"
              inputMode="search"
              enterKeyHint="search"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              placeholder={`Search ${recipes.length} recipes`}
              value={text}
              onChange={(e) => onText(e.target.value)}
            />
            {text && (
              <button type="button" className={styles.clear} onClick={() => onText("")} aria-label="Clear search">
                <Icon name="close" size={18} />
              </button>
            )}
          </label>
          <span className={styles.phoneOnly}><PersonPicker variant="compact" /></span>
        </div>
        <div className={styles.tools}>
          <button
            type="button"
            className={styles.tool}
            aria-pressed={query.favorites}
            onClick={() => setQuery({ favorites: !query.favorites })}
          >
            <Emoji char={query.favorites ? "❤️" : "🤍"} size={16} /> Favorites
          </button>
          <button
            type="button"
            className={styles.tool}
            aria-pressed={selectedValues.length > 0}
            aria-expanded={filtersOpen}
            aria-controls="library-filters"
            onClick={() => setFiltersOpen(!filtersOpen)}
          >
            <Icon name="filter" size={16} /> Filter{selectedValues.length ? ` · ${selectedValues.length}` : ""}
          </button>
          <label className={`${styles.tool} ${styles.sort}`}>
            <Icon name="sort" size={16} />
            <span>{SORTS.find(([key]) => key === effective.sort)?.[1]}</span>
            <select
              aria-label="Sort"
              value={effective.sort}
              onChange={(e) => setQuery({ sort: e.target.value as Sort })}
            >
              {SORTS.filter(([key]) => key !== "relevance" || text.trim()).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </label>
          {(filtersActive || text.trim()) && (
            <span className={styles.status} aria-live="polite">
              {result.recipes.length} of {result.total}
              {filtersActive && (
                <button type="button" onClick={() => { libraryStore.set({ ...emptyQuery, sort: query.sort }); setFiltersOpen(false); }}>Clear</button>
              )}
            </span>
          )}
        </div>
        {filtersOpen && <FacetPanel id="library-filters" counts={result.counts} onToggle={toggle} />}
      </header>

      {result.recipes.length === 0 ? (
        <div className={styles.empty}>
          <Emoji char="🫙" size={56} animate="float" />
          <h2>Nothing here</h2>
          <p>No recipe matches{text.trim() ? ` “${text.trim()}”` : ""} with these filters.</p>
        </div>
      ) : (
        <ul className={styles.list}>
          {result.recipes.slice(0, shown).map((recipe, i, shownList) => {
            const group = groupOf(recipe, effective.sort);
            const heading = group && (i === 0 || groupOf(shownList[i - 1], effective.sort) !== group) ? group : null;
            const fact = sortFact(recipe, effective.sort);
            return (
            <li key={recipe.id} className={styles.item} data-heading={heading ?? undefined}>
              {heading && <h2 className={`${styles.group} ${heading.length === 1 ? styles.letter : ""}`}>{heading}</h2>}
              <a className={styles.link} {...linkProps(`/r/${recipe.slug}`)}>
                <h3 className={styles.title}>{recipe.title}</h3>
                <p className={styles.meta}>
                  <DishMarks tags={recipe.tags} className={styles.marks} />
                  {semantic?.related.has(recipe.id) && !lexicalSet.has(recipe.id) && <span className={styles.related}>related</span>}
                  {fact && <span className={styles.fact}>{fact}</span>}
                  {metaBits(recipe).map((bit) => <span key={bit}>{bit}</span>)}
                </p>
                {recipe.favorites.length > 0 && (
                  <span className={styles.fav}><Emoji char="❤️" label="Favorite" size={15} /></span>
                )}
              </a>
            </li>
            );
          })}
          {shown < result.recipes.length && <li ref={sentinel} aria-hidden="true" style={{ height: 1 }} />}
        </ul>
      )}
    </div>
  );
}
