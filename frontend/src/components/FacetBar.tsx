import { useState } from "react";
import { FACETS, type Facet, type FacetCount } from "../lib/library";
import { Flag, Mark } from "./Marks";
import styles from "./FacetBar.module.css";

const LABELS: Record<Facet, string> = { course: "Course", cuisine: "Cuisine", protein: "Protein", diet: "Diet" };

/** Filter panel (opened from the library's Filter button). Multi-select; counts update live. */
const SHORT = 8; // long tails (cuisines) collapse behind "+N more"

export function FacetPanel({ id, counts, onToggle }: { id?: string; counts: Record<Facet, FacetCount[]>; onToggle: (facet: Facet, value: string) => void }) {
  const [expanded, setExpanded] = useState<Facet | null>(null);
  return (
    <div id={id} className={styles.panel} role="group" aria-label="Filters">
      {FACETS.map((facet) =>
        counts[facet].length === 0 ? null : (
          <div key={facet} className={styles.group} role="group" aria-label={LABELS[facet]}>
            <span className={styles.label}>{LABELS[facet]}</span>
            <div className={styles.chips}>
              {counts[facet].map((option, i) => (
                <button
                  key={option.value}
                  type="button"
                  className={`${styles.chip} ${i >= SHORT && !option.selected && expanded !== facet ? styles.tail : ""}`}
                  aria-pressed={option.selected}
                  disabled={!option.selected && option.count === 0}
                  onClick={() => onToggle(facet, option.value)}
                >
                  {facet === "cuisine" ? <Flag cuisine={option.value} size={18} /> : <Mark kind={facet} value={option.value} size={19} label={false} />}
                  {option.value}
                  <span className={styles.count}>{option.count}</span>
                </button>
              ))}
              {counts[facet].length > SHORT && (
                <button type="button" className={styles.more} onClick={() => setExpanded(expanded === facet ? null : facet)}>
                  {expanded === facet ? "fewer" : `+${counts[facet].length - SHORT} more`}
                </button>
              )}
            </div>
          </div>
        ),
      )}
    </div>
  );
}
