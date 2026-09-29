// Scaling + unit modes (original / US / metric) for ingredient lines and step temperatures.
import { formatQuantity, parseQuantity, QUANTITY_PREFIX, scaleText } from "./fractions";
import type { Ingredient } from "./types";

export type UnitMode = "original" | "us" | "metric";

type Kind = "volume" | "mass";
interface UnitDef { kind: Kind; base: number; label: string }

// base: ml for volume, g for mass
const UNITS: Record<string, UnitDef> = {
  tsp: { kind: "volume", base: 4.929, label: "tsp" },
  teaspoon: { kind: "volume", base: 4.929, label: "tsp" },
  teaspoons: { kind: "volume", base: 4.929, label: "tsp" },
  t: { kind: "volume", base: 4.929, label: "tsp" },
  tbsp: { kind: "volume", base: 14.787, label: "tbsp" },
  tbs: { kind: "volume", base: 14.787, label: "tbsp" },
  tablespoon: { kind: "volume", base: 14.787, label: "tbsp" },
  tablespoons: { kind: "volume", base: 14.787, label: "tbsp" },
  cup: { kind: "volume", base: 236.59, label: "cup" },
  cups: { kind: "volume", base: 236.59, label: "cups" },
  c: { kind: "volume", base: 236.59, label: "cup" },
  "fl oz": { kind: "volume", base: 29.574, label: "fl oz" },
  pint: { kind: "volume", base: 473.18, label: "pint" },
  pints: { kind: "volume", base: 473.18, label: "pints" },
  quart: { kind: "volume", base: 946.35, label: "quart" },
  quarts: { kind: "volume", base: 946.35, label: "quarts" },
  qt: { kind: "volume", base: 946.35, label: "qt" },
  gallon: { kind: "volume", base: 3785.4, label: "gallon" },
  ml: { kind: "volume", base: 1, label: "ml" },
  milliliter: { kind: "volume", base: 1, label: "ml" },
  milliliters: { kind: "volume", base: 1, label: "ml" },
  l: { kind: "volume", base: 1000, label: "l" },
  liter: { kind: "volume", base: 1000, label: "l" },
  liters: { kind: "volume", base: 1000, label: "l" },
  litre: { kind: "volume", base: 1000, label: "l" },
  oz: { kind: "mass", base: 28.35, label: "oz" },
  ounce: { kind: "mass", base: 28.35, label: "oz" },
  ounces: { kind: "mass", base: 28.35, label: "oz" },
  lb: { kind: "mass", base: 453.59, label: "lb" },
  lbs: { kind: "mass", base: 453.59, label: "lb" },
  pound: { kind: "mass", base: 453.59, label: "lb" },
  pounds: { kind: "mass", base: 453.59, label: "lb" },
  g: { kind: "mass", base: 1, label: "g" },
  gram: { kind: "mass", base: 1, label: "g" },
  grams: { kind: "mass", base: 1, label: "g" },
  kg: { kind: "mass", base: 1000, label: "kg" },
  kilogram: { kind: "mass", base: 1000, label: "kg" },
  kilograms: { kind: "mass", base: 1000, label: "kg" },
};
const METRIC = new Set(["ml", "l", "g", "kg"]);
const UNIT_RE = /^\s*(fl\.?\s?oz|[a-zA-Z]+)\.?(?=[\s,)]|$)/;

function round(value: number, step: number): number {
  return Math.max(step, Math.round(value / step) * step);
}

function toMetric(base: number, kind: Kind): string {
  if (kind === "mass") {
    if (base >= 1000) return `${Math.round(base / 100) / 10} kg`;
    return `${base < 10 ? Math.round(base * 2) / 2 : round(base, 5)} g`;
  }
  if (base >= 1000) return `${Math.round(base / 100) / 10} l`;
  return `${base < 15 ? Math.round(base * 2) / 2 : round(base, 5)} ml`;
}

function toUS(base: number, kind: Kind): string {
  if (kind === "mass") {
    const oz = base / 28.35;
    if (oz >= 16) return `${formatQuantity(Math.round((oz / 16) * 4) / 4)} lb`;
    return `${formatQuantity(Math.round(oz * 4) / 4)} oz`;
  }
  const tsp = base / 4.929;
  if (tsp < 3) return `${formatQuantity(Math.round(tsp * 4) / 4)} tsp`;
  const tbsp = tsp / 3;
  if (tbsp < 4) return `${formatQuantity(Math.round(tbsp * 2) / 2)} tbsp`;
  const cups = tbsp / 16;
  const shown = formatQuantity(Math.round(cups * 4) / 4);
  return `${shown} ${cups > 1.1 ? "cups" : "cup"}`;
}

/** Split "1 1/2 cups flour" into quantity (number), unit def and the remaining text. */
export function splitLine(text: string): { qty: number; qtyHigh?: number; unit?: UnitDef; unitRaw?: string; rest: string } | null {
  const match = text.match(QUANTITY_PREFIX);
  if (!match) return null;
  const qty = parseQuantity(match[0]);
  if (qty === null) return null;
  let rest = text.slice(match[0].length);
  let qtyHigh: number | undefined;
  const range = rest.match(/^\s*(?:–|—|-|to)\s*/);
  if (range) {
    const upper = rest.slice(range[0].length).match(QUANTITY_PREFIX);
    const value = upper && parseQuantity(upper[0]);
    if (upper && value !== null && value !== undefined) {
      qtyHigh = value;
      rest = rest.slice(range[0].length + upper[0].length);
    }
  }
  const unitMatch = rest.match(UNIT_RE);
  if (unitMatch) {
    const key = unitMatch[1].toLowerCase().replace(/\.|\s/g, (c) => (c === "." ? "" : " ")).replace(/^fl ?oz$/, "fl oz");
    const unit = UNITS[key] ?? (unitMatch[1] === "T" ? UNITS.tbsp : undefined);
    if (unit && !(key === "c" && !/^\s*c\.?\s/.test(rest)) && !(key === "t" && unitMatch[1] !== "t")) {
      return { qty, qtyHigh, unit, unitRaw: unitMatch[0], rest: rest.slice(unitMatch[0].length) };
    }
  }
  return { qty, qtyHigh, rest };
}

export function displayLine(item: Ingredient, factor: number, mode: UnitMode): string {
  const text = item.source_text;
  if (mode !== "original") {
    const variant = mode === "metric" ? item.metric : item.us;
    if (variant?.display_text && factor === 1) return variant.display_text;
    const parts = splitLine(text);
    if (parts?.unit) {
      const isMetric = METRIC.has(parts.unit.label);
      if ((mode === "metric" && !isMetric) || (mode === "us" && isMetric)) {
        const convert = mode === "metric" ? toMetric : toUS;
        const low = convert(parts.qty * factor * parts.unit.base, parts.unit.kind);
        const high = parts.qtyHigh !== undefined
          ? convert(parts.qtyHigh * factor * parts.unit.base, parts.unit.kind)
          : null;
        const amount = high ? `${low.split(" ")[0]}–${high}` : low;
        return `${amount}${parts.rest.startsWith(" ") || !parts.rest ? "" : " "}${parts.rest}`;
      }
    }
  }
  return scaleText(text, factor);
}

/** Split a display line into [amount, rest] so amounts align in a tabular column. */
export function amountAndRest(line: string): [string, string] {
  const parts = splitLine(line);
  if (!parts) return ["", line];
  const consumed = line.length - parts.rest.length;
  return [line.slice(0, consumed).trim(), parts.rest.replace(/^\s+/, "")];
}

const TEMP = /(\d{2,3})\s*°\s*([FC])\b|(\d{2,3})\s*degrees\s*(F|C|Fahrenheit|Celsius)\b/gi;

export function convertTemps(text: string, mode: UnitMode): string {
  if (mode === "original") return text;
  return text.replace(TEMP, (whole, a, unitA, b, unitB) => {
    const value = Number(a ?? b);
    const unit = String(unitA ?? unitB)[0].toUpperCase();
    if (mode === "metric" && unit === "F") return `${Math.round(((value - 32) * 5) / 9 / 5) * 5}°C`;
    if (mode === "us" && unit === "C") return `${Math.round(((value * 9) / 5 + 32) / 5) * 5}°F`;
    return whole;
  });
}
