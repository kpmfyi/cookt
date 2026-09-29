// Per-recipe cook progress (current step + checked ingredients), persisted on device.
import { readLocal, writeLocal } from "./store";

export interface CookProgress {
  step: number;
  checked: string[];
  factor: number;
  updatedAt: number;
}

const key = (recipeId: string) => `cookt:progress:${recipeId}`;

export function loadProgress(recipeId: string): CookProgress {
  const saved = readLocal<CookProgress | null>(key(recipeId), null);
  // Progress older than 18 hours is a new cook.
  if (!saved || Date.now() - saved.updatedAt > 18 * 3600 * 1000) return { step: 0, checked: [], factor: 1, updatedAt: Date.now() };
  return saved;
}

export function saveProgress(recipeId: string, progress: Omit<CookProgress, "updatedAt">): void {
  writeLocal(key(recipeId), { ...progress, updatedAt: Date.now() });
}

export function clearProgress(recipeId: string): void {
  writeLocal(key(recipeId), undefined);
}
