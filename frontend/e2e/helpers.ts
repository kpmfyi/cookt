import type { Page } from "@playwright/test";

export const PHONE = { width: 390, height: 844 };
export const IPAD_PORTRAIT = { width: 820, height: 1180 };
export const IPAD_LANDSCAPE = { width: 1180, height: 820 };

/** A recipe with a photo, several ingredients and several steps (stable across runs). */
export async function pickRecipe(page: Page, opts: { photo?: boolean; minSteps?: number } = {}): Promise<{ slug: string; title: string }> {
  const response = await page.request.get("/api/catalog");
  const catalog = await response.json();
  const candidates = catalog.recipes
    .filter((r: any) => (opts.photo ?? true ? r.image : true))
    .filter((r: any) => r.document.instruction_sections.flatMap((s: any) => s.steps).length >= (opts.minSteps ?? 4))
    .filter((r: any) => r.document.ingredient_sections.flatMap((s: any) => s.ingredients).length >= 8)
    .sort((a: any, b: any) => a.slug.localeCompare(b.slug));
  const pick = candidates[Math.floor(candidates.length / 3)] ?? catalog.recipes[0];
  return { slug: pick.slug, title: pick.title };
}

export function luminance(rgb: string): number {
  const [r, g, b] = rgb.match(/[\d.]+/g)!.slice(0, 3).map(Number).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

/** Resolve a CSS color (incl. color-mix) to rgb() in the page. */
export async function resolvedColor(page: Page, selector: string, prop: "color" | "background-color"): Promise<string> {
  return page.$eval(selector, (el, p) => {
    let node: Element | null = el;
    while (node) {
      const value = getComputedStyle(node).getPropertyValue(p);
      if (p === "color" || (value && value !== "rgba(0, 0, 0, 0)" && value !== "transparent")) {
        const canvas = document.createElement("canvas").getContext("2d")!;
        canvas.fillStyle = value;
        canvas.fillRect(0, 0, 1, 1);
        const [r, g, b] = canvas.getImageData(0, 0, 1, 1).data;
        return `rgb(${r}, ${g}, ${b})`;
      }
      node = node.parentElement;
    }
    return "rgb(255, 255, 255)";
  }, prop);
}
