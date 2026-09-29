import { useCallback, useEffect, useState } from "react";
import { Icon } from "../components/Icon";
import { DishMarks } from "../components/Marks";
import { useCatalog } from "../lib/catalog";
import { RecipePicker } from "../components/RecipePicker";
import { Ticket } from "../components/Ticket";
import { api, del, post } from "../lib/api";
import { linkProps } from "../lib/router";
import styles from "./Plan.module.css";

interface Entry { id: string; day: string; recipe_id: string; title: string; slug: string; servings: number | null; recipe_servings: number | null; note?: string | null }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function startOfWeek(d: Date): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7)); // Monday
  return copy;
}
const addDays = (d: Date, n: number) => {
  const c = new Date(d);
  c.setDate(c.getDate() + n);
  return c;
};

/** Week board: add or drag recipes onto days, servings per entry, then build the shopping list. */
export function Plan() {
  const [start, setStart] = useState(() => startOfWeek(new Date()));
  const [entries, setEntries] = useState<Entry[]>([]);
  const [picker, setPicker] = useState<string | null>(null);
  const [dropDay, setDropDay] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const { byId } = useCatalog();
  const today = iso(new Date());

  const load = useCallback(() => {
    api<{ entries: Entry[] }>(`/api/plan?start=${iso(start)}&days=7`)
      .then((r) => {
        setEntries(r.entries);
        setError("");
      })
      .catch(() => setError("The week board needs a connection."));
  }, [start]);
  useEffect(load, [load]);

  async function add(day: string, recipeId: string) {
    await post("/api/plan", { day, recipe_id: recipeId });
    load();
  }
  async function move(entry: Entry, day: string) {
    await api(`/api/plan/${entry.id}`, { method: "PATCH", json: { day } });
    load();
  }
  async function setServings(entry: Entry, servings: number) {
    setEntries((prev) => prev.map((e) => (e.id === entry.id ? { ...e, servings } : e)));
    await api(`/api/plan/${entry.id}`, { method: "PATCH", json: { servings } });
  }
  async function remove(entry: Entry) {
    await del(`/api/plan/${entry.id}`);
    load();
  }
  async function toShopping() {
    const r = await post<{ added: number; merged: number; recipes: number; skipped_staples: string[] }>("/api/shopping/from-plan", { start: iso(start), days: 7 });
    setNotice(`Added ${r.added} items (${r.merged} merged) from ${r.recipes} planned recipes${r.skipped_staples.length ? `; skipped staples: ${r.skipped_staples.slice(0, 6).join(", ")}` : ""}.`);
  }

  const fmt = (d: Date, opts: Intl.DateTimeFormatOptions) => d.toLocaleDateString(undefined, opts);
  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1>Week</h1>
        <div className={styles.weekNav}>
          <button type="button" className={styles.iconButton} onClick={() => setStart(addDays(start, -7))} aria-label="Previous week"><Icon name="back" /></button>
          <span className={styles.range}>{fmt(days[0], { month: "short", day: "numeric" })} – {fmt(days[6], { month: "short", day: "numeric" })}</span>
          <button type="button" className={styles.iconButton} onClick={() => setStart(addDays(start, 7))} aria-label="Next week"><Icon name="next" /></button>
        </div>
        <button type="button" className={styles.primary} onClick={toShopping} disabled={!entries.length}>
          <Icon name="cart" size={18} /> Shopping list from this week
        </button>
      </header>
      {notice && <p className={styles.notice} role="status">{notice} <a {...linkProps("/shop")}>Open list</a></p>}
      {error && <p className={styles.notice} role="alert">{error}</p>}
      <div className={styles.board}>
        {days.map((d) => {
          const day = iso(d);
          const mine = entries.filter((e) => e.day === day);
          return (
            <Ticket
              key={day}
              as="section"
              className={`${styles.day} ${day === today ? styles.today : ""} ${day < today ? styles.past : ""} ${dropDay === day ? styles.dropping : ""}`}
              aria-label={fmt(d, { weekday: "long" })}
              onDragOver={(e) => {
                e.preventDefault();
                setDropDay(day);
              }}
              onDragLeave={() => setDropDay(null)}
              onDrop={(e) => {
                e.preventDefault();
                setDropDay(null);
                const entry = entries.find((x) => x.id === e.dataTransfer.getData("text/plain"));
                if (entry && entry.day !== day) void move(entry, day);
              }}
            >
              <div className={styles.dayHead}>
                <span className={styles.dayName}>{fmt(d, { weekday: "short" })}</span>
                <span className={styles.date}>{fmt(d, { month: "short", day: "numeric" })}</span>
              </div>
              <ul className={styles.entries}>
                {mine.map((entry) => {
                  const servings = entry.servings ?? entry.recipe_servings ?? 4;
                  return (
                    <li key={entry.id} className={styles.entry} draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", entry.id)}>
                      <a {...linkProps(`/r/${entry.slug}`)}>{entry.title}</a>
                      {byId.get(entry.recipe_id) && <DishMarks tags={byId.get(entry.recipe_id)!.tags} size={17} className={styles.marks} />}
                      <div className={styles.entryTools}>
                        <button type="button" onClick={() => setServings(entry, Math.max(1, servings - 1))} aria-label="Fewer servings">−</button>
                        <span className={styles.servings}>{servings} serv.</span>
                        <button type="button" onClick={() => setServings(entry, servings + 1)} aria-label="More servings">+</button>
                        <select aria-label="Move to day" value={day} onChange={(e) => move(entry, e.target.value)}>
                          {days.map((o) => <option key={iso(o)} value={iso(o)}>{fmt(o, { weekday: "short" })}</option>)}
                        </select>
                        <button type="button" onClick={() => remove(entry)} aria-label="Remove">✕</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <button type="button" className={styles.add} onClick={() => setPicker(day)}>+ Add a recipe</button>
            </Ticket>
          );
        })}
      </div>
      <RecipePicker
        open={picker !== null}
        title={picker ? `Add to ${new Date(`${picker}T12:00`).toLocaleDateString(undefined, { weekday: "long" })}` : ""}
        onClose={() => setPicker(null)}
        onPick={(recipe) => {
          const day = picker!;
          setPicker(null);
          void add(day, recipe.id);
        }}
      />
    </div>
  );
}
