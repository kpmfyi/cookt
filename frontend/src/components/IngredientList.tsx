import { amountAndRest, displayLine, type UnitMode } from "../lib/units";
import type { IngredientSection } from "../lib/types";
import { foodEmoji } from "../lib/foodEmoji";
import { Emoji } from "./Marks";
import styles from "./IngredientList.module.css";

interface Props {
  sections: IngredientSection[];
  factor: number;
  units: UnitMode;
  checked: Set<string>;
  onToggle: (id: string) => void;
  big?: boolean;
}

/** The mise card: quantities in a fixed tabular column so amounts align like a prep list. */
export function IngredientList({ sections, factor, units, checked, onToggle, big }: Props) {
  return (
    <div className={big ? styles.big : undefined}>
      {sections.map((section) => (
        <section key={section.id} className={styles.section} aria-label={section.heading ?? "Ingredients"}>
          {section.heading && <h3 className={styles.heading}>{section.heading}</h3>}
          <ul className={`${styles.list} ${section.ingredients.some((item) => amountAndRest(displayLine(item, factor, units))[0]) ? "" : styles.noAmounts}`}>
            {section.ingredients.map((item) => {
              const [amount, rest] = amountAndRest(displayLine(item, factor, units));
              const done = checked.has(item.id);
              const emoji = foodEmoji(rest || item.source_text);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={done}
                    className={`${styles.item} ${done ? styles.checked : ""}`}
                    onClick={() => onToggle(item.id)}
                  >
                    <span className={styles.amount}>{amount}</span>
                    <span className={styles.text}>
                      {done ? <Emoji char="✅" size={17} animate="pop" className={styles.food} /> : emoji ? <Emoji char={emoji} size={17} className={styles.food} /> : <span className={styles.foodBlank} aria-hidden="true" />}
                      {rest}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
