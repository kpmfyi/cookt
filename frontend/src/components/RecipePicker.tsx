import { useEffect, useMemo, useRef, useState } from "react";
import { searchIds, useCatalog } from "../lib/catalog";
import type { Recipe } from "../lib/types";
import styles from "./RecipePicker.module.css";

/** Modal recipe search (uses the same offline index as the library). */
export function RecipePicker({ open, title, onPick, onClose }: { open: boolean; title: string; onPick: (recipe: Recipe) => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const { catalog, byId } = useCatalog();
  const [q, setQ] = useState("");
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setQ("");
      dialog.showModal();
    } else if (!open && dialog.open) dialog.close();
  }, [open]);
  const results = useMemo(() => {
    if (!catalog) return [];
    if (!q.trim()) return catalog.recipes.slice(0, 40);
    return searchIds(q).slice(0, 40).map((id) => byId.get(id)).filter((r): r is Recipe => Boolean(r));
  }, [q, catalog, byId]);
  return (
    <dialog ref={ref} className={styles.dialog} onClose={onClose}>
      <div className={styles.card}>
        <h2>{title}</h2>
        <input type="search" autoFocus placeholder="Search recipes" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search recipes" />
        <ul className={styles.results}>
          {results.map((recipe) => (
            <li key={recipe.id}>
              <button type="button" onClick={() => onPick(recipe)}>
                <b>{recipe.title}</b>
              </button>
            </li>
          ))}
        </ul>
        <button type="button" className={styles.close} onClick={onClose}>Close</button>
      </div>
    </dialog>
  );
}
