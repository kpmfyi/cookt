import { useCallback, useEffect, useMemo, useState } from "react";
import { Emoji } from "../components/Marks";
import { Icon } from "../components/Icon";
import { api, post } from "../lib/api";
import { refreshCatalog } from "../lib/catalog";
import { diffWords } from "../lib/diff";
import { linkProps } from "../lib/router";
import type { RecipeDocument } from "../lib/types";
import styles from "./Review.module.css";

type Status = "pending" | "nochange" | "error" | "unreviewed" | "approved" | "rejected" | "kept" | "deleted";

interface Edit {
  key: string;
  kind: "title" | "heading" | "ingredient" | "step" | "note";
  op: "edit" | "remove" | "heading";
  before: string;
  after: string;
  reason: string;
  numbers_changed: boolean;
}

interface Item {
  id: string;
  slug: string;
  title: string;
  credit?: string | null;
  source_url?: string | null;
  document: RecipeDocument;
  status: Status;
  score?: number | null;
  summary?: string | null;
  edits: Edit[];
  flags: string[];
  error?: string | null;
  stale: boolean;
}

type Filter = "todo" | "clean" | "decided" | "all";
const OPEN: Status[] = ["pending", "nochange", "error", "unreviewed"];
const isOpen = (item: Item) => OPEN.includes(item.status);

function inFilter(item: Item, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "decided") return !isOpen(item);
  if (!isOpen(item)) return false;
  const needsDecision = item.status === "pending" || item.flags.includes("empty");
  return filter === "todo" ? needsDecision : !needsDecision;
}

const GROUPS: { kind: Edit["kind"]; label: string }[] = [
  { kind: "title", label: "Title" },
  { kind: "note", label: "Notes" },
  { kind: "heading", label: "Section headings" },
  { kind: "ingredient", label: "Ingredients" },
  { kind: "step", label: "Method" },
];

const DECIDED_LABEL: Partial<Record<Status, string>> = {
  approved: "✅ Approved",
  rejected: "↩️ Rejected — text left as it was",
  kept: "👍 Kept as is",
  deleted: "🗑️ Deleted from the catalog",
};

function Diff({ edit }: { edit: Edit }) {
  if (edit.op === "remove") return <del className={styles.del}>{edit.before}</del>;
  if (edit.op === "heading") {
    return (
      <>
        <del className={styles.del}>{edit.before}</del> <span className={styles.arrow}>→ heading</span>{" "}
        <ins className={styles.ins}>{edit.after}</ins>
      </>
    );
  }
  if (!edit.before) return <ins className={styles.ins}>{edit.after}</ins>;
  return (
    <>
      {diffWords(edit.before, edit.after).map((part, i) =>
        part.type === "same" ? <span key={i}>{part.text}</span> : part.type === "del" ? <del key={i} className={styles.del}>{part.text}</del> : <ins key={i} className={styles.ins}>{part.text}</ins>,
      )}
    </>
  );
}

function FullRecipe({ document }: { document: RecipeDocument }) {
  return (
    <details className={styles.full}>
      <summary>Full recipe as it stands</summary>
      {document.notes.length > 0 && (
        <ul>{document.notes.map((note, i) => <li key={i}>{note}</li>)}</ul>
      )}
      {document.ingredient_sections.map((section) => (
        <section key={section.id}>
          {section.heading && <h4>{section.heading}</h4>}
          <ul>{section.ingredients.map((item) => <li key={item.id}>{item.source_text}</li>)}</ul>
        </section>
      ))}
      {document.instruction_sections.map((section) => (
        <section key={section.id}>
          {section.heading && <h4>{section.heading}</h4>}
          <ol>{section.steps.map((step) => <li key={step.id}>{step.text}</li>)}</ol>
        </section>
      ))}
    </details>
  );
}

/** Copy-edit proposals from the local model: approve all or some lines, reject, or delete the recipe. */
export function Review() {
  const [items, setItems] = useState<Map<string, Item>>(new Map());
  const [filter, setFilter] = useState<Filter>("todo");
  const [sort, setSort] = useState<"score" | "title">("score");
  const [order, setOrder] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const data = await api<{ items: Item[] }>("/api/review");
    const map = new Map(data.items.map((item) => [item.id, item]));
    setItems(map);
    setLoaded(true);
    return map;
  }, []);
  useEffect(() => {
    load().catch((err: Error) => setMessage(err.message));
  }, [load]);

  // The queue is a snapshot, so a decided recipe stays put and can be revisited or undone.
  const rebuild = useCallback(
    (source: Map<string, Item>, keepId?: string) => {
      const list = [...source.values()].filter((item) => inFilter(item, filter));
      list.sort((a, b) =>
        sort === "title" ? a.title.localeCompare(b.title) : (a.score ?? 6) - (b.score ?? 6) || b.edits.length - a.edits.length || a.title.localeCompare(b.title),
      );
      const ids = list.map((item) => item.id);
      setOrder(ids);
      setIndex(Math.max(0, keepId ? ids.indexOf(keepId) : 0));
    },
    [filter, sort],
  );
  useEffect(() => {
    if (loaded) rebuild(items);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, filter, sort]);

  const current = order[index] ? items.get(order[index]) : undefined;
  useEffect(() => setOff(new Set()), [current?.id]);

  const counts = useMemo(() => {
    const all = [...items.values()];
    return {
      todo: all.filter((i) => inFilter(i, "todo")).length,
      clean: all.filter((i) => inFilter(i, "clean")).length,
      decided: all.filter((i) => inFilter(i, "decided")).length,
      all: all.length,
      waiting: all.filter((i) => i.status === "unreviewed").length,
    };
  }, [items]);

  const advance = useCallback(() => {
    setIndex((i) => {
      for (let k = i + 1; k < order.length; k++) if (isOpen(items.get(order[k])!)) return k;
      return Math.min(i + 1, order.length - 1);
    });
  }, [order, items]);

  async function act(action: "approve" | "reject" | "keep" | "delete" | "undo") {
    if (!current || busy) return;
    const item = current;
    const accepted = item.edits.filter((e) => !off.has(e.key)).map((e) => e.key);
    setBusy(true);
    setMessage("");
    try {
      await post(`/api/review/${item.id}/${action}`, action === "approve" ? { accepted } : {});
      const next: Status =
        action === "undo" ? (item.edits.length ? "pending" : "nochange") : action === "approve" ? (accepted.length ? "approved" : "rejected") : action === "reject" ? "rejected" : action === "keep" ? "kept" : "deleted";
      setItems((prev) => new Map(prev).set(item.id, { ...item, status: next, stale: false }));
      if (action === "undo") {
        await load();
      } else {
        if (action === "delete") setMessage(`Deleted “${item.title}”. Press U or go back to undo.`);
        advance();
      }
      refreshCatalog();
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select")) return;
      const key = event.key.toLowerCase();
      if (!current) return;
      const open = isOpen(current);
      if (key === "j" || key === "arrowright") setIndex((i) => Math.min(i + 1, order.length - 1));
      else if (key === "k" || key === "arrowleft") setIndex((i) => Math.max(i - 1, 0));
      else if (open && (key === "a" || key === "enter")) void act(current.edits.length ? "approve" : "keep");
      else if (open && key === "r" && current.edits.length) void act("reject");
      else if (open && key === "d") void act("delete");
      else if (!open && key === "u") void act("undo");
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const toggle = (key: string) =>
    setOff((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const open = current ? isOpen(current) : false;
  const acceptedCount = current ? current.edits.filter((e) => !off.has(e.key)).length : 0;

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1>Review</h1>
        <p>
          Copy edits toward the house style (clear, exact, no brands or filler), proposed by the local model. Nothing changes until you approve it; everything can be undone.
          {counts.waiting > 0 && <> The model is still reading {counts.waiting} recipes. <button type="button" className={styles.linkish} onClick={() => load().then((map) => rebuild(map, current?.id))}>Refresh</button></>}
        </p>
        <div className={styles.controls}>
          <div className={styles.segments} role="tablist" aria-label="Show">
            {([
              ["todo", "To review", counts.todo],
              ["clean", "No edits", counts.clean],
              ["decided", "Decided", counts.decided],
              ["all", "All", counts.all],
            ] as const).map(([value, label, n]) => (
              <button key={value} type="button" role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>
                {label} <small>{n}</small>
              </button>
            ))}
          </div>
          <label className={styles.sort}>
            Sort
            <select value={sort} onChange={(e) => setSort(e.target.value as "score" | "title")}>
              <option value="score">Weakest first</option>
              <option value="title">A–Z</option>
            </select>
          </label>
        </div>
      </header>

      <div className={styles.layout}>
        <nav className={styles.queue} aria-label="Queue">
          <ol>
            {order.map((id, k) => {
              const item = items.get(id)!;
              return (
                <li key={id}>
                  <button type="button" aria-current={k === index} className={isOpen(item) ? undefined : styles.done} onClick={() => setIndex(k)}>
                    <span className={styles.qscore}>{item.status === "deleted" ? "🗑️" : !isOpen(item) ? "✓" : item.score ?? "·"}</span>
                    <span className={styles.qtitle}>{item.title}</span>
                    {item.edits.length > 0 && <small>{item.edits.length}</small>}
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <main className={styles.main}>
          {!loaded && !message && <p className={styles.muted}>Loading…</p>}
          {loaded && !current && <p className={styles.empty}><Emoji char="🎉" size={28} /> Nothing here.</p>}
          {current && (
            <>
              <div className={styles.bar}>
                <div className={styles.position}>
                  <button type="button" className={styles.nav} onClick={() => setIndex((i) => Math.max(i - 1, 0))} disabled={index === 0} aria-label="Previous (K)"><Icon name="back" size={18} /></button>
                  <span>{index + 1} / {order.length}</span>
                  <button type="button" className={styles.nav} onClick={() => setIndex((i) => Math.min(i + 1, order.length - 1))} disabled={index >= order.length - 1} aria-label="Next (J)"><Icon name="next" size={18} /></button>
                </div>
                <div className={styles.actions}>
                  {open ? (
                    <>
                      <button type="button" className={styles.delete} disabled={busy} onClick={() => act("delete")}><Icon name="trash" size={17} /> Delete recipe <kbd>D</kbd></button>
                      {current.edits.length > 0 && <button type="button" className={styles.secondary} disabled={busy} onClick={() => act("reject")}>Reject <kbd>R</kbd></button>}
                      <button type="button" className={styles.primary} disabled={busy} onClick={() => act(current.edits.length ? "approve" : "keep")}>
                        {current.edits.length ? <>Approve {acceptedCount < current.edits.length ? `${acceptedCount} of ${current.edits.length}` : "all"}</> : "Keep"} <kbd>A</kbd>
                      </button>
                    </>
                  ) : (
                    <>
                      <span className={styles.decided}>{DECIDED_LABEL[current.status]}</span>
                      <button type="button" className={styles.secondary} disabled={busy} onClick={() => act("undo")}><Icon name="undo" size={16} /> Undo <kbd>U</kbd></button>
                    </>
                  )}
                </div>
                {message && <p className={styles.message} role="status">{message}</p>}
              </div>

              <article className={styles.card}>
                <header className={styles.recipeHead}>
                  <h2>{current.status === "deleted" ? current.title : <a {...linkProps(`/r/${current.slug}`)}>{current.title}</a>}</h2>
                  <p className={styles.meta}>
                    {current.credit && <span>{current.credit}</span>}
                    {current.source_url && <a href={current.source_url} target="_blank" rel="noreferrer">source <Icon name="external" size={13} /></a>}
                    {current.score != null && <span className={styles.score} title="Quality of the original text, 1–5">{"★".repeat(current.score)}{"☆".repeat(5 - current.score)}</span>}
                  </p>
                  {current.summary && <p className={styles.summary}>{current.summary}</p>}
                  {current.flags.includes("empty") && <p className={styles.flag}>⚠️ Nothing to cook from: the import lost the ingredients or the method.</p>}
                  {current.flags.includes("exemplar") && <p className={styles.flag}>🏅 Already meets the standard.</p>}
                  {current.status === "unreviewed" && <p className={styles.flag}>⏳ The model hasn't read this one yet. You can still keep or delete it.</p>}
                  {current.status === "error" && <p className={styles.flag}>⚠️ The model failed on this recipe: {current.error}</p>}
                  {current.stale && open && <p className={styles.flag}>⚠️ Edited since the proposal; lines changed since then won't apply.</p>}
                </header>

                {current.edits.length > 0 && (
                  <div className={styles.toggles}>
                    <button type="button" className={styles.linkish} onClick={() => setOff(new Set())}>Select all</button>
                    <button type="button" className={styles.linkish} onClick={() => setOff(new Set(current.edits.map((e) => e.key)))}>Select none</button>
                  </div>
                )}

                {GROUPS.map(({ kind, label }) => {
                  const edits = current.edits.filter((e) => e.kind === kind);
                  if (!edits.length) return null;
                  return (
                    <section key={kind} className={styles.group}>
                      <h3>{label}</h3>
                      <ul>
                        {edits.map((edit) => (
                          <li key={edit.key} className={off.has(edit.key) ? styles.skipped : undefined}>
                            <label>
                              <input type="checkbox" checked={!off.has(edit.key)} disabled={!open} onChange={() => toggle(edit.key)} />
                              <span className={styles.line}><Diff edit={edit} /></span>
                            </label>
                            <span className={styles.reason}>
                              {edit.numbers_changed && <span className={styles.numbers} title="A number changed: check amounts, times and temperatures">🔢 number changed</span>}
                              {edit.reason}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </section>
                  );
                })}

                <FullRecipe document={current.document} />
              </article>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
