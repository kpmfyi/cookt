import { durationSpans } from "../lib/durations";
import { openTab } from "../lib/tabs";
import { startTimer } from "../lib/timers";
import { convertTemps, type UnitMode } from "../lib/units";
import styles from "./StepText.module.css";

type Piece = { start: number; end: number; node: (key: number) => React.ReactNode };

/** Step text with tappable durations: "simmer 20 minutes" starts a 20:00 timer. */
export function StepText({ text, units, recipeId, recipeTitle, stepLabel }: { text: string; units: UnitMode; recipeId: string; recipeTitle: string; stepLabel: string }) {
  const shown = convertTemps(text, units);
  const pieces: Piece[] = durationSpans(shown).map((span) => ({
    start: span.start,
    end: span.end,
    node: (key) => (
      <button
        key={key}
        type="button"
        className={styles.timer}
        aria-label={`Start a ${span.text} timer`}
        onClick={() => {
          // A timer keeps its recipe open in a tab and wears the tab's colour.
          const mode = window.location.pathname.endsWith("/cook") ? "cook" : "read";
          const color = openTab(recipeId, mode);
          startTimer({ label: `${stepLabel} · ${span.text}`, seconds: span.seconds, recipeId, recipeTitle, color, mode });
        }}
      >
        {span.text}
      </button>
    ),
  }));
  if (pieces.length === 0) return <>{shown}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  pieces.forEach((piece, i) => {
    parts.push(shown.slice(cursor, piece.start));
    parts.push(piece.node(i));
    cursor = piece.end;
  });
  parts.push(shown.slice(cursor));
  return <>{parts}</>;
}
