// Ported from recipe-table frontend/src/lib/fractions.ts.
const vulgar: Record<string, [number, number]> = {
  "¼": [1, 4], "½": [1, 2], "¾": [3, 4], "⅓": [1, 3], "⅔": [2, 3], "⅕": [1, 5], "⅖": [2, 5],
  "⅗": [3, 5], "⅘": [4, 5], "⅙": [1, 6], "⅚": [5, 6], "⅛": [1, 8], "⅜": [3, 8], "⅝": [5, 8],
  "⅞": [7, 8],
};

const reverse = new Map(Object.entries(vulgar).map(([symbol, [n, d]]) => [`${n}/${d}`, symbol]));

export function parseQuantity(raw: string): number | null {
  const text = raw.trim();
  const last = text.at(-1);
  if (last && vulgar[last]) {
    const whole = Number(text.slice(0, -1).trim() || 0);
    const [n, d] = vulgar[last];
    return Number.isFinite(whole) ? whole + n / d : null;
  }
  const mixed = text.match(/^(\d+)\s+(\d+)\/(\d+)$/);
  if (mixed) {
    const denominator = Number(mixed[3]);
    return denominator ? Number(mixed[1]) + Number(mixed[2]) / denominator : null;
  }
  const fraction = text.match(/^(\d+)\/(\d+)$/);
  if (fraction) {
    const denominator = Number(fraction[2]);
    return denominator ? Number(fraction[1]) / denominator : null;
  }
  if (!text) return null;
  const numeric = Number(text);
  return Number.isFinite(numeric) ? numeric : null;
}

function gcd(a: number, b: number): number {
  return b ? gcd(b, a % b) : a;
}

const NICE_DENOMINATORS = [2, 3, 4, 8];

export function formatQuantity(value: number): string {
  if (!Number.isFinite(value) || value < 0) return String(value);
  if (Number.isInteger(value)) return String(value);
  if (value >= 10) return String(Math.round(value));
  const whole = Math.floor(value);
  let bestN = 0;
  let bestD = 1;
  let error = Number.POSITIVE_INFINITY;
  for (const d of NICE_DENOMINATORS) {
    const n = Math.round((value - whole) * d);
    const current = Math.abs(value - whole - n / d);
    if (current < error - 1e-9) {
      bestN = n;
      bestD = d;
      error = current;
    }
  }
  if (error > 0.04) return String(Math.round(value * 100) / 100);
  const divisor = gcd(bestN, bestD) || 1;
  bestN /= divisor;
  bestD /= divisor;
  if (bestN === 0) return String(whole);
  if (bestN === bestD) return String(whole + 1);
  const fraction = reverse.get(`${bestN}/${bestD}`) ?? `${bestN}/${bestD}`;
  return whole ? `${whole}${reverse.has(`${bestN}/${bestD}`) ? "" : " "}${fraction}` : fraction;
}

export const QUANTITY_PREFIX =
  /^(?:(\d+\s+\d+\/\d+)|(\d+\/\d+)|((?:\d+\s*)?[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])|(\d+(?:\.\d+)?))/;

export function scaleText(text: string, factor: number): string {
  if (factor === 1) return text;
  const match = text.match(QUANTITY_PREFIX);
  if (!match) return text;
  const parsed = parseQuantity(match[0]);
  if (parsed === null) return text;
  const rest = text.slice(match[0].length);
  const range = rest.match(/^(\s*(?:–|—|-|to)\s*)(?=\d|[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])/);
  if (range) {
    const upper = rest.slice(range[0].length).match(QUANTITY_PREFIX);
    const value = upper && parseQuantity(upper[0]);
    if (upper && value !== null && value !== undefined) {
      return `${formatQuantity(parsed * factor)}${range[0]}${formatQuantity(value * factor)}${rest.slice(range[0].length + upper[0].length)}`;
    }
  }
  return `${formatQuantity(parsed * factor)}${rest}`;
}
