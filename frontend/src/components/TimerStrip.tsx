import { useCatalog } from "../lib/catalog";
import { formatRemaining } from "../lib/durations";
import { recipeColor, showRecipe, useCurrentRecipe } from "../lib/tabs";
import { addTime, dismissTimer, remainingOf, toggleTimer, useNow, useTimers } from "../lib/timers";
import { Icon } from "./Icon";
import { Emoji, heroEmoji } from "./Marks";
import styles from "./TimerStrip.module.css";

/**
 * Persistent strip of concurrent timers, each in its recipe's colour. Global by default; cook mode
 * renders it inline. Tapping a timer from another recipe switches to that recipe.
 */
export function TimerStrip({ inline = false, hidden = false }: { inline?: boolean; hidden?: boolean }) {
  const timers = useTimers();
  const state = useCatalog();
  const current = useCurrentRecipe();
  const now = useNow(timers.some((timer) => timer.endsAt));
  if (hidden || timers.length === 0) return null;
  return (
    <div className={[styles.strip, inline ? styles.inline : "", inline ? styles.big : ""].join(" ")} role="region" aria-label="Timers">
      {timers.map((timer) => {
        const left = remainingOf(timer, now);
        const recipe = state.byId.get(timer.recipeId);
        const elsewhere = Boolean(recipe) && timer.recipeId !== current?.id;
        const face = (
          <>
            <Emoji char={timer.done ? "🔔" : recipe ? heroEmoji(recipe.tags) : "⏱️"} size={16} animate={timer.done ? "pulse" : undefined} />
            <span className={styles.label}>{timer.label}</span>
            <span className={styles.time}>{timer.done ? "Done" : formatRemaining(left)}</span>
          </>
        );
        return (
          <div
            key={timer.id}
            className={[styles.timer, timer.endsAt ? styles.running : "", timer.done ? styles.done : ""].join(" ")}
            style={recipeColor(timer.color)}
            title={`${timer.label} · ${timer.recipeTitle}`}
            aria-live={timer.done ? "assertive" : "off"}
          >
            {elsewhere ? (
              <button type="button" className={styles.face} onClick={() => showRecipe(timer.recipeId, timer.mode)} aria-label={`${timer.label}, ${timer.recipeTitle}: go to recipe`}>
                {face}
              </button>
            ) : (
              <span className={styles.face}>{face}</span>
            )}
            {!timer.done && (
              <button type="button" className={styles.icon} onClick={() => toggleTimer(timer.id)} aria-label={timer.endsAt ? "Pause timer" : "Resume timer"}>
                <Icon name={timer.endsAt ? "pause" : "play"} size={18} filled />
              </button>
            )}
            <button type="button" className={styles.icon} onClick={() => addTime(timer.id, 60)} aria-label="Add one minute">+1</button>
            <button type="button" className={styles.icon} onClick={() => dismissTimer(timer.id)} aria-label="Dismiss timer">
              <Icon name="close" size={18} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
