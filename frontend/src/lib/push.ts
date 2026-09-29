// Timer alerts through Web Push (backend cookt.push): the page is suspended when the iPad locks,
// so the server sends "timer done" at each running timer's end time.
import { isStandalone, setBadge } from "./device";
import { createStore, useStore } from "./store";
import { remainingOf, timerStore, type Timer } from "./timers";

export type PushState = "unsupported" | "insecure" | "needs-install" | "off" | "on" | "denied";

export const pushStore = createStore<PushState>("off");
export const usePushState = () => useStore(pushStore);

const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function subscription(): Promise<PushSubscription | null> {
  if (!supported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  return (await registration?.pushManager.getSubscription()) ?? null;
}

export async function refreshPushState(standalone: boolean): Promise<void> {
  if (!window.isSecureContext) return pushStore.set("insecure");
  // iPadOS only offers Web Push to apps opened from the Home Screen.
  if (!supported()) return pushStore.set(standalone ? "unsupported" : "needs-install");
  if (Notification.permission === "denied") return pushStore.set("denied");
  pushStore.set((await subscription()) ? "on" : "off");
}

function toKey(base64url: string): Uint8Array<ArrayBuffer> {
  const raw = atob(base64url.replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function post(path: string, body: unknown): Promise<Response> {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** Must run from a tap: asks for notification permission, subscribes, registers with the server. */
export async function enablePush(): Promise<PushState> {
  if (!supported()) return pushStore.get();
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    pushStore.set(permission === "denied" ? "denied" : "off");
    return pushStore.get();
  }
  const registration = await navigator.serviceWorker.ready;
  let sub = await registration.pushManager.getSubscription();
  if (!sub) {
    const { key } = (await (await fetch("/api/push/key")).json()) as { key: string };
    sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: toKey(key) });
  }
  const response = await post("/api/push/subscribe", sub.toJSON());
  pushStore.set(response.ok ? "on" : "off");
  if (response.ok) void syncTimers();
  return pushStore.get();
}

export async function disablePush(): Promise<void> {
  const sub = await subscription();
  if (sub) {
    await post("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => undefined);
    await sub.unsubscribe().catch(() => undefined);
  }
  pushStore.set("off");
}

export async function testPush(): Promise<boolean> {
  const sub = await subscription();
  if (!sub) return false;
  const response = await post("/api/push/test", { endpoint: sub.endpoint });
  return response.ok && ((await response.json()) as { ok: boolean }).ok;
}

const running = (timers: Timer[]) => timers.filter((t) => t.endsAt && !t.done && remainingOf(t) > 0);

let last = "";
async function syncTimers(): Promise<void> {
  const sub = await subscription();
  if (!sub) {
    // Starting a timer asks for notification permission; once granted, subscribe quietly.
    if (supported() && isStandalone() && Notification.permission === "granted" && running(timerStore.get()).length) {
      await enablePush().catch(() => undefined);
    }
    return;
  }
  const timers = running(timerStore.get()).map((t) => ({
    id: t.id,
    label: t.label,
    recipe_title: t.recipeTitle,
    ends_at: t.endsAt,
  }));
  const key = JSON.stringify(timers);
  if (key === last) return;
  const response = await post("/api/push/timers", { endpoint: sub.endpoint, timers }).catch(() => null);
  if (response?.ok) last = key;
  else if (response?.status === 404) {
    // the server forgot this device (e.g. the push service expired it): register again
    const again = await post("/api/push/subscribe", sub.toJSON()).catch(() => null);
    if (again?.ok) void syncTimers();
  }
}

let pending: ReturnType<typeof setTimeout> | undefined;
timerStore.subscribe(() => {
  clearTimeout(pending);
  pending = setTimeout(() => void syncTimers(), 300);
});
window.addEventListener("online", () => {
  last = "";
  void syncTimers();
});

// Home-screen badge: finished timers still waiting to be dismissed (the service worker sets a
// dot when a push arrives while the app is closed; this corrects it on return).
const showBadge = () => setBadge(timerStore.get().filter((t) => t.done).length);
timerStore.subscribe(showBadge);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") showBadge();
});
showBadge();
void refreshPushState(isStandalone());
