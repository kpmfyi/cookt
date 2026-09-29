// Shopping list with offline check-off: the list is kept on the device, changes are
// applied locally at once and queued; the queue replays to /api/shopping/sync when online.
import { api, post } from "./api";
import { createStore, readLocal, useStore, writeLocal } from "./store";

export interface ShopItem {
  id: string;
  name: string;
  quantity?: string | null;
  unit?: string | null;
  section: string;
  checked: boolean;
  sources: { recipe_id: string; title: string; text: string }[];
}
interface Op { id: string; op: "check" | "uncheck" | "delete" | "add"; at: string; name?: string }
interface State { items: ShopItem[]; pending: Op[]; syncedAt: string | null; error?: string }

export const shopStore = createStore<State>(readLocal("cookt:shopping", { items: [], pending: [], syncedAt: null }));
shopStore.subscribe(() => writeLocal("cookt:shopping", shopStore.get()));
export const useShopping = () => useStore(shopStore);

function applyOps(items: ShopItem[], ops: Op[]): ShopItem[] {
  let out = items;
  for (const op of ops) {
    if (op.op === "delete") out = out.filter((item) => item.id !== op.id);
    else if (op.op === "add" && op.name && !out.some((item) => item.id === op.id)) {
      out = [...out, { id: op.id, name: op.name, section: "other", checked: false, sources: [] }];
    } else out = out.map((item) => (item.id === op.id ? { ...item, checked: op.op === "check" } : item));
  }
  return out;
}

export function queue(op: Omit<Op, "at">): void {
  const full = { ...op, at: new Date().toISOString() } as Op;
  shopStore.set((state) => ({ ...state, items: applyOps(state.items, [full]), pending: [...state.pending, full] }));
  void sync();
}

let syncing = false;
export async function sync(): Promise<void> {
  if (syncing) return;
  syncing = true;
  try {
    const { pending } = shopStore.get();
    const result = pending.length
      ? await post<{ items: ShopItem[] }>("/api/shopping/sync", { ops: pending })
      : await api<{ items: ShopItem[] }>("/api/shopping");
    shopStore.set((state) => {
      const rest = state.pending.slice(pending.length); // ops queued while syncing
      return { items: applyOps(result.items, rest), pending: rest, syncedAt: new Date().toISOString() };
    });
  } catch {
    shopStore.set((state) => ({ ...state, error: "offline" }));
  } finally {
    syncing = false;
  }
}

window.addEventListener("online", () => void sync());
