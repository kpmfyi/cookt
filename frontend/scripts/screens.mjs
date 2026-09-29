// Screenshot the key screens at phone / iPad portrait / iPad landscape in light and dark.
// Usage: node scripts/screens.mjs [outDir] [--engine webkit|chromium] [--only name]
import { chromium, webkit } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.COOKT_URL ?? "http://127.0.0.1:8088";
const args = process.argv.slice(2);
const out = args.find((a) => !a.startsWith("--")) ?? "../docs/screens";
const engineName = args.includes("--engine") ? args[args.indexOf("--engine") + 1] : "chromium";
const only = args.includes("--only") ? args[args.indexOf("--only") + 1] : null;
mkdirSync(out, { recursive: true });

const ALL_SIZES = {
  phone: [390, 844],
  "phone-landscape": [844, 390],
  "ipad-portrait": [820, 1180],
  "ipad-landscape": [1180, 820],
  desktop: [1440, 900],
};
const SIZES = process.env.SIZES ? Object.fromEntries(Object.entries(ALL_SIZES).filter(([k]) => process.env.SIZES.split(",").includes(k))) : ALL_SIZES;
const browser = await (engineName === "webkit" ? webkit : chromium).launch();

const probe = await (await browser.newContext()).newPage();
const catalog = await (await probe.request.get(`${BASE}/api/catalog`)).json();
const pick =
  process.env.COOKT_SLUG ??
  catalog.recipes
    .filter((r) => r.document.ingredient_sections.flatMap((s) => s.ingredients).length >= 8)
    .filter((r) => r.document.instruction_sections.flatMap((s) => s.steps).length >= 4)
    .sort((a, b) => a.slug.localeCompare(b.slug))[0]?.slug;

const SHOTS = [
  { name: "library", path: "/" },
  { name: "recipe", path: `/r/${pick}` },
  { name: "recipe-method", path: `/r/${pick}`, action: async (p) => p.getByRole("tab", { name: "Method" }).click({ timeout: 1500 }).catch(() => {}) },
  { name: "cook", path: `/r/${pick}/cook`, action: async (p) => { await p.keyboard.press("ArrowRight"); await p.waitForTimeout(400); } },
  { name: "search", path: "/", action: async (p) => { await p.locator('input[type="search"]').fill("chicken"); await p.waitForTimeout(500); } },
  { name: "inbox", path: "/inbox" },
  { name: "plan", path: "/plan" },
  { name: "shop", path: "/shop" },
  { name: "changes", path: "/changes" },
];
const ONLY_SET = process.env.SHOTS ? new Set(process.env.SHOTS.split(",")) : null;

for (const [size, [width, height]] of Object.entries(SIZES)) {
  for (const theme of (process.env.THEMES ?? "light,dark").split(",")) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, colorScheme: theme });
    const page = await context.newPage();
    for (const shot of SHOTS) {
      if ((only && shot.name !== only) || (ONLY_SET && !ONLY_SET.has(shot.name))) continue;
      await page.goto(`${BASE}${shot.path}`);
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => document.fonts.ready);
      if (shot.action) await shot.action(page);
      await page.waitForTimeout(250);
      const file = `${out}/${shot.name}-${size}-${theme}.png`;
      await page.screenshot({ path: file });
      console.log(file);
    }
    await context.close();
  }
}
await browser.close();
