// Per-device preferences: who's cooking, theme, unit mode.
import { createStore, readLocal, useStore, writeLocal } from "./store";
import type { UnitMode } from "./units";

export type Theme = "system" | "light" | "dark";

export const personStore = createStore<string>(readLocal("cookt:person", ""));
export const themeStore = createStore<Theme>(readLocal("cookt:theme", "system"));
export const unitStore = createStore<UnitMode>(readLocal("cookt:units", "original"));
/** Show the "-free" diet marks (gluten-free, dairy-free) on dishes. Off by default: they are absences, not features. */
export const restrictionStore = createStore<boolean>(readLocal("cookt:restrictions", false));

personStore.subscribe(() => writeLocal("cookt:person", personStore.get()));
unitStore.subscribe(() => writeLocal("cookt:units", unitStore.get()));
restrictionStore.subscribe(() => writeLocal("cookt:restrictions", restrictionStore.get()));
themeStore.subscribe(() => {
  const theme = themeStore.get();
  writeLocal("cookt:theme", theme);
  applyTheme(theme);
});

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.dataset.theme = theme;
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#000000" : "#ffffff");
}

export const usePerson = () => useStore(personStore);
export const useTheme = () => useStore(themeStore);
export const useUnits = () => useStore(unitStore);
export const useRestrictions = () => useStore(restrictionStore);
