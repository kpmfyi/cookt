import { useCallback, useEffect, useState } from "react";
import { Icon } from "../components/Icon";
import { Ticket } from "../components/Ticket";
import { api, post } from "../lib/api";
import { refreshCatalog } from "../lib/catalog";
import { linkProps } from "../lib/router";
import styles from "./Feed.module.css";

interface Change {
  id: string;
  recipe_id: string;
  title: string;
  slug: string;
  field: string;
  before: unknown;
  after: unknown;
  evidence?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  created_at: string;
  reverted_at?: string | null;
}

function label(field: string): string {
  if (field.startsWith("tag:")) return field.slice(4);
  return { prep_minutes: "prep", cook_minutes: "cook", total_minutes: "total time", servings: "servings", ingredient_names: "ingredient names" }[field] ?? field;
}

function show(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return `${Object.keys(value as object).length} names`;
  return String(value);
}

/** Every automatic change (tags, derived metadata), newest first, each one-tap revertible. */
export function Changes() {
  const [items, setItems] = useState<Change[]>([]);
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (offset = 0) => {
    try {
      const page = await api<{ total: number; items: Change[] }>(`/api/changes?limit=150&offset=${offset}`);
      setTotal(page.total);
      setItems((prev) => (offset ? [...prev, ...page.items] : page.items));
      setDone(page.items.length < 150);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function revert(change: Change) {
    await post(`/api/changes/${change.id}/revert`, {});
    setItems((prev) => prev.map((c) => (c.id === change.id ? { ...c, reverted_at: new Date().toISOString() } : c)));
    refreshCatalog();
  }

  const groups: { key: string; title: string; slug: string; changes: Change[] }[] = [];
  for (const change of items) {
    const last = groups.at(-1);
    if (last && last.key === change.recipe_id) last.changes.push(change);
    else groups.push({ key: change.recipe_id, title: change.title, slug: change.slug, changes: [change] });
  }

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1>Changes</h1>
        <p>{total} automatic changes · tags &amp; derived details only — recipe text is never rewritten</p>
      </header>
      {error && <Ticket className={styles.empty}>{error}</Ticket>}
      {!error && items.length === 0 && <Ticket className={styles.empty}>No automatic changes yet.</Ticket>}
      {groups.map((group, gi) => (
        <Ticket key={`${group.key}-${gi}`} className={styles.sheet} as="section">
          <div className={styles.group}>
            <h2><a {...linkProps(`/r/${group.slug}`)}>{group.title}</a></h2>
            <ul className={styles.list}>
              {group.changes.map((change) => (
                <li key={change.id} className={`${styles.row} ${change.reverted_at ? styles.reverted : ""}`}>
                  <span className={styles.what}>
                    <span className={styles.field}>{label(change.field)}</span>
                    {change.after == null ? (
                      <span className={`${styles.value} ${styles.removed}`}>{show(change.before)}</span>
                    ) : (
                      <span className={styles.value}>
                        {change.before != null && typeof change.before !== "object" && <span className={styles.removed}>{show(change.before)}</span>} {show(change.after)}
                      </span>
                    )}
                  </span>
                  <button type="button" className={styles.revert} disabled={Boolean(change.reverted_at)} onClick={() => revert(change)}>
                    {change.reverted_at ? "Reverted" : <><Icon name="undo" size={16} /> Revert</>}
                  </button>
                  {change.evidence && <span className={styles.evidence}>“{change.evidence}” · {change.model} · {change.prompt_version}</span>}
                </li>
              ))}
            </ul>
          </div>
        </Ticket>
      ))}
      {!done && items.length > 0 && (
        <button type="button" className={styles.more} onClick={() => load(items.length)}>Older changes</button>
      )}
    </div>
  );
}
