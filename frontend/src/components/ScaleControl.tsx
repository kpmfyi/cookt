import { formatQuantity } from "../lib/fractions";
import { unitStore, useUnits } from "../lib/prefs";
import type { UnitMode } from "../lib/units";
import { Icon } from "./Icon";
import styles from "./ScaleControl.module.css";

const FACTORS = [0.5, 0.75, 1, 1.5, 2, 3, 4];

/** Scaling (½×–4×, or by servings) and US / metric / original units. */
export function ScaleControl({ factor, servings, onFactor }: { factor: number; servings?: number | null; onFactor: (factor: number) => void }) {
  const units = useUnits();
  const byServings = Boolean(servings && servings > 0 && servings < 100);
  let options = FACTORS;
  if (byServings) {
    const base = servings as number;
    const counts = new Set<number>();
    for (let n = Math.max(1, Math.ceil(base * 0.5)); n <= Math.floor(base * 4); n += n < 12 ? 1 : 2) counts.add(n);
    counts.add(base);
    options = [...counts].sort((a, b) => a - b).map((n) => n / base);
  }
  const index = options.findIndex((option) => Math.abs(option - factor) < 1e-6);
  const current = index === -1 ? options.indexOf(1) : index;
  const step = (delta: number) => onFactor(options[Math.min(options.length - 1, Math.max(0, current + delta))]);
  return (
    <div className={styles.row}>
      <div className={styles.stepper} role="group" aria-label="Scale">
        <button type="button" onClick={() => step(-1)} disabled={current <= 0} aria-label="Fewer">
          <Icon name="minus" size={18} />
        </button>
        <span className={styles.value} aria-live="polite">
          {byServings ? (
            <><b>{formatQuantity(Math.round((servings as number) * factor * 100) / 100)}</b> servings</>
          ) : (
            <><b>{formatQuantity(factor)}×</b> recipe</>
          )}
        </span>
        <button type="button" onClick={() => step(1)} disabled={current >= options.length - 1} aria-label="More">
          <Icon name="plus" size={18} />
        </button>
      </div>
      <div className={styles.segmented} role="group" aria-label="Units">
        {(["original", "us", "metric"] as UnitMode[]).map((mode) => (
          <button key={mode} type="button" aria-pressed={units === mode} onClick={() => unitStore.set(mode)}>
            {mode === "original" ? "As written" : mode === "us" ? "US" : "Metric"}
          </button>
        ))}
      </div>
    </div>
  );
}
