// Open-recipe tabs and per-recipe timer colours.
import { expect, test, type Page } from "@playwright/test";
import { IPAD_LANDSCAPE, PHONE } from "./helpers";

/** Two recipes whose steps have a tappable duration ("simmer 20 minutes"). */
async function twoTimedRecipes(page: Page): Promise<{ slug: string; title: string }[]> {
  const catalog = await (await page.request.get("/api/catalog")).json();
  const timed = catalog.recipes
    .filter((r: any) => r.document.instruction_sections.some((s: any) => s.steps.some((st: any) => /\b\d+\s*(minutes?|mins?)\b/i.test(st.text))))
    .sort((a: any, b: any) => a.slug.localeCompare(b.slug));
  return [timed[0], timed[Math.floor(timed.length / 2)]].map((r: any) => ({ slug: r.slug, title: r.title }));
}

const tabRow = (page: Page) => page.getByRole("navigation", { name: "Open recipes" });

async function startFirstTimer(page: Page) {
  await page.getByRole("button", { name: /^Start a .* timer$/ }).first().click();
}

test.describe("open-recipe tabs", () => {
  test("keep two recipes open, colour their timers, and switch from a timer", async ({ page }) => {
    await page.setViewportSize(IPAD_LANDSCAPE);
    const [a, b] = await twoTimedRecipes(page);

    // A: a timer keeps it open.
    await page.goto(`/r/${a.slug}`);
    await startFirstTimer(page);
    await expect(page.getByRole("button", { name: "Close this recipe's tab" })).toHaveAttribute("aria-pressed", "true");

    // B: shows as a preview next to A until kept.
    await page.goto(`/r/${b.slug}`);
    await expect(tabRow(page)).toBeVisible();
    await expect(tabRow(page).getByRole("link")).toHaveCount(1); // A (B is the preview)
    await tabRow(page).getByRole("button", { name: `Keep ${b.title} open` }).click();
    await expect(tabRow(page).getByRole("link")).toHaveCount(2);
    await startFirstTimer(page);

    // Timers wear their recipe's colour, and the two differ.
    const timers = page.getByRole("region", { name: "Timers" }).locator("> div");
    await expect(timers).toHaveCount(2);
    const tones = await timers.evaluateAll((els) => els.map((el) => getComputedStyle(el).borderTopColor));
    expect(tones[0]).not.toBe(tones[1]);
    const tabTones = await tabRow(page).locator("li").evaluateAll((els) => els.map((el) => getComputedStyle(el.querySelector("span")!).backgroundColor));
    expect(tabTones).toEqual(tones);

    // Tapping A's timer (not on screen) switches to A.
    await page.getByRole("button", { name: new RegExp(`${escape(a.title)}: go to recipe`) }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${a.slug}$`));
    // A's own timer is not a link while A is on screen.
    await expect(page.getByRole("button", { name: new RegExp(`${escape(a.title)}: go to recipe`) })).toHaveCount(0);

    // Tabs navigate; cook mode is remembered per tab.
    await tabRow(page).getByRole("link", { name: b.title }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${b.slug}$`));
    await page.goto(`/r/${b.slug}/cook`);
    await expect(tabRow(page)).toBeVisible();
    await tabRow(page).getByRole("link", { name: a.title }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${a.slug}$`));
    await tabRow(page).getByRole("link", { name: b.title }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${b.slug}/cook$`));

    // Closing the current tab moves to the neighbour; the timer survives and reopens B.
    await tabRow(page).getByRole("button", { name: `Close ${b.title}` }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${a.slug}$`));
    await expect(timers).toHaveCount(2);
    await page.getByRole("button", { name: new RegExp(`${escape(b.title)}: go to recipe`) }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${b.slug}$`)); // where its timer was started
    const reopened = await page.getByRole("region", { name: "Timers" }).locator("> div").evaluateAll((els) => els.map((el) => getComputedStyle(el).borderTopColor));
    expect(reopened).toEqual(tones); // B got its colour back
  });

  test("phone: the tab row sits above the recipe bar and survives scrolling", async ({ page }) => {
    await page.setViewportSize(PHONE);
    const [a, b] = await twoTimedRecipes(page);
    await page.goto(`/r/${a.slug}`);
    await page.getByRole("button", { name: "Keep open in a tab" }).click();
    await page.goto(`/r/${b.slug}`);
    await expect(tabRow(page)).toBeVisible();
    await page.mouse.wheel(0, 1500);
    await page.waitForTimeout(300);
    const row = (await tabRow(page).boundingBox())!;
    const bar = (await page.getByRole("link", { name: "Back to library" }).boundingBox())!;
    expect(row.y).toBeLessThanOrEqual(1);
    expect(bar.y).toBeGreaterThanOrEqual(row.y + row.height - 1);
  });
});

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
