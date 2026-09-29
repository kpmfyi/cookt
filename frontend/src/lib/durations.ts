// Ported from recipe-table frontend/src/lib/durations.ts: tappable timers parsed from step text.
import { parseQuantity } from "./fractions";

const FRACTIONS = "¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞";
const AMOUNT = `(?:\\d+\\s+\\d+/\\d+|\\d+/\\d+|(?:\\d+\\s*)?[${FRACTIONS}]|\\d+(?:\\.\\d+)?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty)`;
const DURATION = new RegExp(
  `\\b(${AMOUNT})(?:\\s*(?:-|–|—|to)\\s*(${AMOUNT}))?\\s*[-–]?\\s*` +
    `(seconds?|secs?|minutes?|mins?|hours?|hrs?)\\b`,
  "gi",
);
const WORDS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, ten: 10, fifteen: 15, twenty: 20, thirty: 30,
};

export interface DurationSpan {
  start: number;
  end: number;
  text: string;
  seconds: number;
}

function amount(raw: string): number | null {
  const word = WORDS[raw.toLowerCase()];
  return word ?? parseQuantity(raw);
}

export function durationSpans(text: string): DurationSpan[] {
  const matches: DurationSpan[] = [];
  for (const match of text.matchAll(DURATION)) {
    const quantity = amount(match[2] || match[1]);
    if (quantity === null || quantity <= 0 || match.index === undefined) continue;
    const unit = match[3].toLowerCase();
    const multiplier = unit.startsWith("hour") || unit.startsWith("hr")
      ? 3_600
      : unit.startsWith("min")
        ? 60
        : 1;
    matches.push({
      start: match.index,
      end: match.index + match[0].length,
      text: match[0],
      seconds: Math.round(quantity * multiplier),
    });
  }
  return matches;
}

export function formatRemaining(totalSeconds: number): string {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function formatMinutes(minutes: number | null | undefined): string | null {
  if (!minutes || minutes <= 0) return null;
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} hr ${m} min` : `${h} hr`;
}
