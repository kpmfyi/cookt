import { useEffect, useState } from "react";
import { Icon } from "../components/Icon";
import { DishMarks, Emoji } from "../components/Marks";
import { foodEmoji } from "../lib/foodEmoji";
import { useCatalog } from "../lib/catalog";
import { Ticket } from "../components/Ticket";
import { api, del, post } from "../lib/api";
import { linkProps } from "../lib/router";
import { queue, shopStore, sync, useShopping } from "../lib/shopping";
import styles from "./Shopping.module.css";

const SECTION_ORDER = ["produce", "meat & seafood", "dairy & eggs", "bakery", "pantry", "spices & baking", "canned & jarred", "frozen", "international", "drinks", "other"];
const SECTION_EMOJI: Record<string, string> = {
  produce: "🥬", "meat & seafood": "🥩", "dairy & eggs": "🥚", bakery: "🥖", pantry: "🥫", "spices & baking": "🧂",
  "canned & jarred": "🫙", frozen: "🧊", international: "🌍", drinks: "🥤", other: "📦",
};

export function Shopping() {
  const [tab, setTab] = useState<"list" | "pantry">("list");
  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1>{tab === "list" ? "Shopping" : "Pantry"}</h1>
        <div className={styles.tabs} role="group" aria-label="View">
          <button type="button" aria-pressed={tab === "list"} onClick={() => setTab("list")}>List</button>
          <button type="button" aria-pressed={tab === "pantry"} onClick={() => setTab("pantry")}>Staples &amp; what can I make</button>
        </div>
      </header>
      {tab === "list" ? <ShoppingList /> : <Pantry />}
    </div>
  );
}

function ShoppingList() {
  const { items, pending, error } = useShopping();
  const [name, setName] = useState("");
  useEffect(() => void sync(), []);

  const groups = SECTION_ORDER.map((section) => ({
    section,
    items: items.filter((item) => (item.section || "other") === section).sort((a, b) => Number(a.checked) - Number(b.checked)),
  })).filter((group) => group.items.length);
  const checkedCount = items.filter((i) => i.checked).length;

  function add(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    queue({ id: crypto.randomUUID(), op: "add", name: name.trim().toLowerCase() });
    setName("");
  }

  async function clearChecked() {
    const checked = items.filter((i) => i.checked);
    for (const item of checked) queue({ id: item.id, op: "delete" });
  }

  return (
    <Ticket className={styles.sheet} as="section" aria-label="Shopping list">
      <form className={styles.add} onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Add an item" aria-label="Add an item" />
        <button type="submit" className={styles.primary}>Add</button>
      </form>
      <p className={styles.status} aria-live="polite">
        {items.length - checkedCount} to get
        {pending.length > 0 ? ` · ${pending.length} change${pending.length === 1 ? "" : "s"} waiting to sync` : error === "offline" ? " · offline" : ""}
      </p>
      {groups.length === 0 && <p className={styles.muted} style={{ marginTop: 12 }}>The list is empty. Add recipes from the week board or a recipe's Plan section.</p>}
      {groups.map((group) => (
        <section key={group.section} className={styles.section}>
          <h2><Emoji char={SECTION_EMOJI[group.section] ?? "📦"} size={18} /> {group.section}</h2>
          <ul className={styles.items}>
            {group.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={item.checked}
                  className={`${styles.row} ${item.checked ? styles.checked : ""}`}
                  onClick={() => queue({ id: item.id, op: item.checked ? "uncheck" : "check" })}
                >
                  <span className={styles.amount}>{[item.quantity, item.unit].filter(Boolean).join(" ")}</span>
                  <span className={styles.name}>
                    {item.checked ? <Emoji char="✅" size={17} animate="pop" className={styles.food} /> : foodEmoji(item.name) ? <Emoji char={foodEmoji(item.name)!} size={17} className={styles.food} /> : null}
                    {item.name}
                    {item.sources.length > 0 && <span className={styles.from}>{[...new Set(item.sources.map((s) => s.title))].join(" · ")}</span>}
                  </span>
                  <span />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {checkedCount > 0 && (
        <div className={styles.footer}>
          <button type="button" className={styles.secondary} onClick={clearChecked}>Clear {checkedCount} checked</button>
        </div>
      )}
      {shopStore.get().syncedAt && <p className={styles.muted} style={{ marginTop: 8 }}>Works offline; check-offs sync when you're back online.</p>}
    </Ticket>
  );
}

function Pantry() {
  const [staples, setStaples] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [make, setMake] = useState<{ id: string; slug: string; title: string; to_buy: string[]; percent_to_buy: number }[]>([]);
  const [error, setError] = useState("");
  const { byId } = useCatalog();
  const load = () => {
    api<{ staples: string[] }>("/api/pantry").then((r) => setStaples(r.staples)).catch(() => setError("Needs a connection."));
    api<{ results: typeof make }>("/api/what-can-i-make?limit=25").then((r) => setMake(r.results)).catch(() => undefined);
  };
  useEffect(load, []);
  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    await post("/api/pantry", { name: name.trim() });
    setName("");
    load();
  }
  return (
    <>
      <Ticket className={styles.sheet} as="section" aria-label="Pantry staples">
        <h2><Emoji char="🏠" size={18} /> Always have</h2>
        <p className={styles.muted}>Staples are left off shopping lists and count as “on hand” below.</p>
        <div className={styles.staples}>
          {staples.map((staple) => (
            <span key={staple} className={styles.staple}>
              {foodEmoji(staple) && <Emoji char={foodEmoji(staple)!} size={15} className={styles.food} />}
              {staple}
              <button type="button" aria-label={`Remove ${staple}`} onClick={() => del(`/api/pantry/${encodeURIComponent(staple)}`).then(load)}><Icon name="close" size={16} /></button>
            </span>
          ))}
        </div>
        <form className={styles.add} onSubmit={add}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. olive oil" aria-label="Add a staple" />
          <button type="submit" className={styles.primary}>Add</button>
        </form>
        {error && <p className={styles.muted}>{error}</p>}
      </Ticket>
      <Ticket className={styles.sheet} as="section" aria-label="What can I make">
        <h2>What can I make</h2>
        <p className={styles.muted}>Ranked by how much of each recipe you'd need to buy beyond your staples.</p>
        <ul className={styles.make}>
          {make.map((r) => (
            <li key={r.id}>
              <a {...linkProps(`/r/${r.slug}`)}>{r.title}</a>
              {byId.get(r.id) && <DishMarks tags={byId.get(r.id)!.tags} size={17} className={styles.marks} />}
              <p className={styles.muted}>{r.percent_to_buy}% to buy{r.to_buy.length ? `: ${r.to_buy.slice(0, 6).join(", ")}${r.to_buy.length > 6 ? "…" : ""}` : " — nothing!"}</p>
            </li>
          ))}
        </ul>
      </Ticket>
    </>
  );
}
