// Home-screen app helpers: install state, persistent storage, app badge, offline photos.
import type { Recipe } from "./types";

export const isStandalone = (): boolean =>
  matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;

/** iPadOS Safari reports itself as a Mac; a touch screen gives it away. */
export const isIOS = (): boolean =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

/** Ask the browser not to evict the offline cache under storage pressure (granted to installed apps). */
export async function persistStorage(): Promise<boolean> {
  try {
    if (!navigator.storage?.persist) return false;
    return (await navigator.storage.persisted()) || (await navigator.storage.persist());
  } catch {
    return false;
  }
}

export async function storageUsage(): Promise<{ used: number; persisted: boolean } | null> {
  try {
    if (!navigator.storage?.estimate) return null;
    const { usage = 0 } = await navigator.storage.estimate();
    return { used: usage, persisted: (await navigator.storage.persisted?.()) ?? false };
  } catch {
    return null;
  }
}

/** Home-screen icon badge (iPadOS 16.4+ once notifications are allowed; a no-op elsewhere). */
export function setBadge(count: number): void {
  const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
  try {
    const done = count > 0 ? nav.setAppBadge?.(count) : nav.clearAppBadge?.();
    void done?.catch(() => undefined);
  } catch {
    // unsupported
  }
}

export function photoUrls(recipes: Recipe[]): string[] {
  const urls = new Set<string>();
  for (const recipe of recipes) {
    if (recipe.image) urls.add(recipe.image.thumb).add(recipe.image.src);
    for (const image of recipe.step_images) urls.add(image.src);
  }
  return [...urls];
}

/** Fetch every photo once; the service worker keeps them (cache-first on /images). */
export async function savePhotos(urls: string[], onProgress: (done: number) => void): Promise<number> {
  let done = 0;
  let failed = 0;
  const queue = [...urls];
  const worker = async () => {
    for (let url = queue.shift(); url; url = queue.shift()) {
      try {
        const response = await fetch(url);
        if (!response.ok) failed += 1;
        await response.arrayBuffer();
      } catch {
        failed += 1;
      }
      onProgress(++done);
    }
  };
  await Promise.all(Array.from({ length: 4 }, worker));
  return failed;
}
