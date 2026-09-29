// Cook timers: several at once, persisted by absolute end time so they survive
// navigation, reloads and screen lock. A chime plays on finish; a system
// notification is shown when permission was granted.
import { useEffect, useState } from "react";
import { createStore, readLocal, useStore, writeLocal } from "./store";

export interface Timer {
  id: string;
  label: string;
  recipeId: string;
  recipeTitle: string;
  seconds: number;
  endsAt: number | null; // running when set
  remaining: number; // seconds left while paused
  done: boolean;
  color?: number; // the recipe's tab colour (lib/tabs)
  mode?: "read" | "cook"; // where it was started, for reopening the recipe from the timer
}

export const timerStore = createStore<Timer[]>(readLocal("cookt:timers", []));
timerStore.subscribe(() => writeLocal("cookt:timers", timerStore.get()));

export const useTimers = () => useStore(timerStore);

export function remainingOf(timer: Timer, now = Date.now()): number {
  return timer.endsAt ? Math.max(0, (timer.endsAt - now) / 1000) : timer.remaining;
}

export function startTimer(input: { label: string; seconds: number; recipeId: string; recipeTitle: string; color?: number; mode?: "read" | "cook" }): void {
  requestNotifyPermission();
  unlockAudio();
  const id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  timerStore.set((timers) => [
    ...timers,
    { ...input, id, endsAt: Date.now() + input.seconds * 1000, remaining: input.seconds, done: false },
  ]);
}

export function toggleTimer(id: string): void {
  timerStore.set((timers) =>
    timers.map((timer) => {
      if (timer.id !== id || timer.done) return timer;
      return timer.endsAt
        ? { ...timer, endsAt: null, remaining: remainingOf(timer) }
        : { ...timer, endsAt: Date.now() + timer.remaining * 1000 };
    }),
  );
}

export function addTime(id: string, seconds: number): void {
  timerStore.set((timers) =>
    timers.map((timer) => {
      if (timer.id !== id) return timer;
      if (timer.done) return { ...timer, done: false, endsAt: Date.now() + seconds * 1000, remaining: seconds };
      return timer.endsAt ? { ...timer, endsAt: timer.endsAt + seconds * 1000 } : { ...timer, remaining: timer.remaining + seconds };
    }),
  );
}

export function dismissTimer(id: string): void {
  timerStore.set((timers) => timers.filter((timer) => timer.id !== id));
}

// --- finishing ------------------------------------------------------------------------

let audio: AudioContext | null = null;

export function unlockAudio(): void {
  try {
    audio ??= new AudioContext();
    if (audio.state === "suspended") void audio.resume();
  } catch {
    audio = null;
  }
}

export function chime(): void {
  if (!audio) return;
  const start = audio.currentTime;
  // Three short bell-like tones (a kitchen pass bell), repeated twice.
  [0, 0.5].forEach((offset) => {
    [880, 1318.5].forEach((frequency, i) => {
      const osc = audio!.createOscillator();
      const gain = audio!.createGain();
      osc.type = "sine";
      osc.frequency.value = frequency;
      const t = start + offset + i * 0.12;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.35, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 1.1);
      osc.connect(gain).connect(audio!.destination);
      osc.start(t);
      osc.stop(t + 1.2);
    });
  });
}

function requestNotifyPermission(): void {
  if ("Notification" in window && Notification.permission === "default") {
    void Notification.requestPermission().catch(() => undefined);
  }
}

async function notify(timer: Timer): Promise<void> {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const body = `${timer.label} — ${timer.recipeTitle}`;
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (registration) await registration.showNotification("Timer done", { body, tag: timer.id });
    else new Notification("Timer done", { body, tag: timer.id });
  } catch {
    /* best effort */
  }
}

function tick(): void {
  const now = Date.now();
  const finished = timerStore.get().filter((timer) => !timer.done && timer.endsAt && timer.endsAt <= now);
  if (finished.length === 0) return;
  timerStore.set((timers) =>
    timers.map((timer) => (finished.some((f) => f.id === timer.id) ? { ...timer, done: true, endsAt: null, remaining: 0 } : timer)),
  );
  chime();
  if (document.visibilityState !== "visible") finished.forEach((timer) => void notify(timer));
  navigator.vibrate?.([200, 100, 200]);
}

setInterval(tick, 500);
document.addEventListener("visibilitychange", tick);

/** Re-render several times a second while any timer runs. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const handle = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(handle);
  }, [active]);
  return now;
}
