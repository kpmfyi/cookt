import { beforeEach, describe, expect, it } from "vitest";
import { closeTab, openTab, pickColor, recipeRoute, tabStore } from "./tabs";
import { startTimer, timerStore } from "./timers";

describe("recipe tabs", () => {
  beforeEach(() => {
    tabStore.set([]);
    timerStore.set([]);
  });

  it("gives each open recipe its own colour, lowest free first", () => {
    expect(openTab("a")).toBe(0);
    expect(openTab("b")).toBe(1);
    expect(openTab("a")).toBe(0); // already open: same colour
    closeTab("a");
    expect(openTab("c")).toBe(0); // freed colour is reused
  });

  it("remembers read vs cook without duplicating the tab", () => {
    openTab("a", "read");
    openTab("a", "cook");
    expect(tabStore.get()).toEqual([{ id: "a", mode: "cook", color: 0 }]);
  });

  it("keeps a closed recipe's colour while its timers run, and reuses it on reopen", () => {
    const color = openTab("a");
    startTimer({ label: "Step 1 · 5 minutes", seconds: 300, recipeId: "a", recipeTitle: "A", color });
    closeTab("a");
    expect(openTab("b")).toBe(1); // colour 0 still belongs to a's timer
    expect(openTab("a")).toBe(0);
  });

  it("falls back to the least-used colour when every colour is taken", () => {
    const tabs = [0, 1, 2, 3, 4, 5].map((color) => ({ id: `r${color}`, mode: "read" as const, color }));
    const timers = [{ recipeId: "r0", color: 0 }, { recipeId: "x", color: 0 }];
    expect(pickColor("new", tabs, timers)).toBe(1);
  });

  it("drops the oldest tab without timers past eight", () => {
    const busy = openTab("r0");
    startTimer({ label: "t", seconds: 60, recipeId: "r0", recipeTitle: "R0", color: busy });
    for (let i = 1; i <= 8; i += 1) openTab(`r${i}`);
    const ids = tabStore.get().map((tab) => tab.id);
    expect(ids).toHaveLength(8);
    expect(ids).toContain("r0");
    expect(ids).not.toContain("r1");
  });

  it("parses recipe routes", () => {
    expect(recipeRoute("/r/pho/cook")).toEqual({ slug: "pho", mode: "cook" });
    expect(recipeRoute("/r/pho")).toEqual({ slug: "pho", mode: "read" });
    expect(recipeRoute("/r/pho/edit")).toEqual({ slug: "pho", mode: "edit" });
    expect(recipeRoute("/plan")).toBeNull();
  });
});
