// Spec §7 acceptance criteria that can be checked in a browser: 2, 3, 4, 5, 10.
import { expect, test } from "@playwright/test";
import { IPAD_LANDSCAPE, PHONE, contrast, pickRecipe, resolvedColor } from "./helpers";

test.describe("§7 acceptance", () => {
  test("#3 phone library shows ≥4 recipes above the fold", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await page.goto("/");
    await page.waitForSelector("main ul li a h3");
    const visible = await page.$$eval("main ul li", (items) =>
      items.filter((li) => {
        const r = li.getBoundingClientRect();
        const tabbar = document.querySelector("nav")?.getBoundingClientRect();
        const bottom = tabbar && tabbar.top > 0 && tabbar.top < window.innerHeight ? tabbar.top : window.innerHeight;
        return r.top >= 0 && r.bottom <= bottom;
      }).length,
    );
    console.log(`phone library: ${visible} recipes fully visible`);
    expect(visible).toBeGreaterThanOrEqual(4);
  });

  test("#4 phone recipe view shows the first ingredient above the fold", async ({ page }) => {
    await page.setViewportSize(PHONE);
    const { slug } = await pickRecipe(page);
    const detail = page.waitForResponse((r) => /\/api\/recipes\/[^/]+$/.test(new URL(r.url()).pathname));
    await page.goto(`/r/${slug}`);
    expect((await detail).status()).toBe(200); // cook log / history / nutrition panels load
    const first = page.locator('[aria-labelledby="ing-h"] [role="checkbox"]').first();
    await expect(first).toBeVisible();
    const box = (await first.boundingBox())!;
    console.log(`first ingredient bottom at ${Math.round(box.y + box.height)}px of ${PHONE.height}`);
    expect(box.y + box.height).toBeLessThanOrEqual(PHONE.height - 58); // above the tab bar
  });

  test("#5 iPad landscape cook mode: ingredients + current step visible, ≥28px, ≥7:1", async ({ page }) => {
    await page.setViewportSize(IPAD_LANDSCAPE);
    const { slug } = await pickRecipe(page);
    await page.goto(`/r/${slug}/cook`);
    const ingredients = page.locator('aside[aria-label="Ingredients"]');
    const current = page.locator('section[aria-current="step"]');
    await expect(ingredients).toBeInViewport();
    await expect(current).toBeInViewport({ ratio: 0.9 });
    const text = current.locator("p").first();
    const size = await text.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size).toBeGreaterThanOrEqual(28);
    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => (document.documentElement.dataset.theme = t), theme);
      const fg = await resolvedColor(page, 'section[aria-current="step"] p', "color");
      const bg = await resolvedColor(page, 'section[aria-current="step"]', "background-color");
      const ratio = contrast(fg, bg);
      console.log(`cook step ${theme}: ${size}px, contrast ${ratio.toFixed(2)}:1 (${fg} on ${bg})`);
      expect(ratio).toBeGreaterThanOrEqual(7);
    }
  });

  test("#2 search: keystroke → results < 50 ms, typo tolerant ('carnitsa' finds carnitas)", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("main ul li a h3");
    const input = page.locator('input[type="search"]');
    await input.click();
    const queries = ["c", "ca", "car", "carn", "carni", "carnit", "carnits", "carnitsa"];
    const runs: number[][] = [];
    for (let run = 0; run < 3; run += 1) {
      const timings: number[] = [];
      for (const query of ["", ...queries]) {
        const ms = await page.evaluate(async (q) => {
          const el = document.querySelector<HTMLInputElement>('input[type="search"]')!;
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
          const start = performance.now();
          setter.call(el, q);
          el.dispatchEvent(new Event("input", { bubbles: true }));
          await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));
          return performance.now() - start;
        }, query);
        if (query) timings.push(ms);
      }
      runs.push(timings);
    }
    const medians = queries.map((_, i) => runs.map((r) => r[i]).sort((a, b) => a - b)[1]);
    const worst = Math.max(...runs.flat());
    console.log(`keystroke → rendered results, median of 3 (ms): ${medians.map((t) => t.toFixed(1)).join(", ")}; worst single ${worst.toFixed(1)}`);
    await expect(page.locator("main ul li h3").first()).toContainText(/carnitas/i);
    expect(Math.max(...medians)).toBeLessThan(50);
  });

  test("#10 offline after one online visit: library, search, recipe, cook", async ({ page, context, browserName }) => {
    test.skip(browserName === "webkit", "Playwright WebKit service workers are unreliable on Linux; checked in Chromium");
    await page.goto("/");
    await page.waitForSelector("main ul li a h3");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload(); // now controlled by the SW; catalog cached
    await page.waitForSelector("main ul li a h3");
    const { slug, title } = await pickRecipe(page);
    await page.goto(`/r/${slug}`); // warms the recipe detail + its photo
    await page.waitForSelector("h1");
    await context.setOffline(true);
    await page.goto("/");
    await expect(page.locator("main ul li h3").first()).toBeVisible();
    await page.locator('input[type="search"]').fill(title.split(" ")[0]);
    await expect(page.locator("main ul li h3").first()).toBeVisible();
    await page.goto(`/r/${slug}`);
    await expect(page.locator("h1")).toContainText(title.slice(0, 10));
    await page.goto(`/r/${slug}/cook`);
    await expect(page.locator('section[aria-current="step"]')).toBeVisible();
    await context.setOffline(false);
  });
});
