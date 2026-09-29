import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../components/Icon";
import { api, post, put } from "../lib/api";
import { refreshCatalog } from "../lib/catalog";
import { ingredientBlocks, stepBlocks, toIngredientSections, toInstructionSections } from "../lib/editing";
import { usePerson } from "../lib/prefs";
import { linkProps, navigate, useLocation } from "../lib/router";
import type { RecipeDocument } from "../lib/types";
import styles from "./Inbox.module.css";

type Mode = "url" | "paste" | "photos" | "handwritten" | "social" | "paprika" | "epub";
const MODES: [Mode, string][] = [
  ["url", "Link"],
  ["paste", "Paste text"],
  ["photos", "Cookbook pages"],
  ["handwritten", "Handwritten card"],
  ["social", "Instagram / TikTok / YouTube"],
  ["paprika", "Paprika export"],
  ["epub", "Cookbook EPUB"],
];

interface Draft {
  document: RecipeDocument;
  method: string;
  source_url?: string | null;
  source_site?: string | null;
  image_urls?: string[];
  nutrition?: Record<string, unknown> | null;
  coverage?: number | null;
  uncertain_lines?: string[];
  transcript?: string | null;
  warnings?: string[];
  upload_images?: string[];
  total_minutes?: number | null;
  yield_text?: string | null;
}
interface Item {
  id: string;
  kind: string;
  status: string;
  input: { url?: string; text?: string; caption?: string; images?: string[]; filename?: string; title?: string };
  draft: Draft | null;
  error?: string | null;
  duplicates: { recipe_id: string; title: string; reason: string }[];
  created_at: string;
}
const WORKING = new Set(["queued", "uploading", "fetching", "extracting"]);
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", uploading: "Uploading", fetching: "Fetching", extracting: "Extracting",
  ready: "Ready to save", failed: "Failed", saved: "Saved",
};

export function Inbox() {
  const [items, setItems] = useState<Item[]>([]);
  const [recent, setRecent] = useState<{ id: string; title: string; slug: string }[]>([]);
  const [offline, setOffline] = useState(false);
  const { query } = useLocation();
  const highlight = query.get("item");

  const load = useCallback(async () => {
    try {
      const data = await api<{ items: Item[]; recently_saved: { id: string; title: string; slug: string }[] }>("/api/inbox");
      setItems(data.items);
      setRecent(data.recently_saved);
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);
  useEffect(() => void load(), [load]);
  const busy = items.some((item) => WORKING.has(item.status));
  useEffect(() => {
    if (!busy) return;
    const handle = setInterval(load, 2500);
    return () => clearInterval(handle);
  }, [busy, load]);

  return (
    <div className={styles.page}>
      <header className={styles.head}><h1>Inbox</h1></header>
      <ImportPanel onQueued={load} />
      {offline && <p className={styles.notice}>Imports need a connection to the kitchen server.</p>}
      <div className={styles.list}>
        {items.map((item) => (
          <InboxItem key={item.id} item={item} onChange={load} highlight={item.id === highlight} />
        ))}
      </div>
      {!offline && items.length === 0 && (
        <p className={styles.notice}>Nothing waiting. Share a link from Safari (Share → cookt), paste a recipe, or photograph a cookbook page.</p>
      )}
      {recent.length > 0 && (
        <p className={styles.recent}>
          Recently saved:{" "}
          {recent.map((r, i) => (
            <span key={r.id}>{i > 0 && " · "}<a {...linkProps(`/r/${r.slug}`)}>{r.title}</a></span>
          ))}
        </p>
      )}
    </div>
  );
}

function ImportPanel({ onQueued }: { onQueued: () => void }) {
  const [mode, setMode] = useState<Mode>("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [html, setHtml] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [transcribe, setTranscribe] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const person = usePerson();
  const fileRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setUrl("");
    setText("");
    setHtml(null);
    setFiles([]);
  };

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "epub") {
        const form = new FormData();
        form.append("file", files[0]);
        if (person) form.append("person_id", person);
        const result = await api<{ book: string | null; count: number; already_imported: number; skipped: number }>("/api/import/epub", { method: "POST", body: form });
        const parts = [`${result.count} recipes from ${result.book ?? files[0].name} are in the inbox`];
        if (result.already_imported) parts.push(`${result.already_imported} were already imported`);
        if (result.skipped) parts.push(`${result.skipped} ingredient lists had no method and were left out`);
        setNotice(`${parts.join("; ")}.`);
      } else if (mode === "url") await post("/api/import/url", { url, person_id: person || null });
      else if (mode === "paste") await post("/api/import/text", { text, html, person_id: person || null });
      else {
        const form = new FormData();
        if (mode === "paprika") {
          form.append("file", files[0]);
        } else {
          files.forEach((file) => form.append("images", file));
          if (mode === "handwritten") form.append("handwritten", "true");
          if (mode === "social") {
            if (url) form.append("url", url);
            if (text) form.append("caption", text);
            if (transcribe) form.append("transcribe", "true");
          }
        }
        if (person) form.append("person_id", person);
        const endpoint = mode === "paprika" ? "/api/import/paprika" : mode === "social" ? "/api/import/social" : "/api/import/photos";
        await api(endpoint, { method: "POST", body: form });
      }
      reset();
      onQueued();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onPaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    const richHtml = event.clipboardData.getData("text/html");
    if (richHtml) setHtml(richHtml);
  }

  const needsFiles = mode === "photos" || mode === "handwritten" || mode === "paprika" || mode === "epub";
  const singleFile = mode === "paprika" || mode === "epub";
  const canSubmit =
    !busy &&
    ((mode === "url" && url.trim().length > 6) ||
      (mode === "paste" && text.trim().length > 20) ||
      (needsFiles && files.length > 0) ||
      (mode === "social" && (url.trim() || text.trim() || files.length > 0)));

  return (
    <section className={styles.sheet} aria-label="Import a recipe">
      <div className={styles.modes} role="group" aria-label="Import from">
        {MODES.map(([key, label]) => (
          <button key={key} type="button" aria-pressed={mode === key} onClick={() => { setMode(key); setFiles([]); setError(""); }}>{label}</button>
        ))}
      </div>
      <form className={styles.form} onSubmit={submit}>
        {(mode === "url" || mode === "social") && (
          <div className={styles.row}>
            <input type="url" inputMode="url" placeholder={mode === "url" ? "https://…" : "Link to the post or video"} value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Recipe link" />
            {mode === "url" && <button type="submit" className={styles.primary} disabled={!canSubmit}>Import</button>}
          </div>
        )}
        {(mode === "paste" || mode === "social") && (
          <textarea
            placeholder={mode === "paste" ? "Paste the recipe (text or copied web page)" : "Caption or description text (optional)"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onPaste={onPaste}
            aria-label="Recipe text"
          />
        )}
        {(needsFiles || mode === "social") && (
          <label className={styles.drop}>
            <Icon name="camera" size={28} />
            <span>
              {mode === "paprika"
                ? "Choose a Paprika export (.paprikarecipes, .zip or .html)"
                : mode === "epub"
                  ? "Choose a cookbook you own (.epub). Every recipe in it lands in the inbox."
                : mode === "social"
                  ? "Optional: screenshots of the recipe"
                  : mode === "handwritten"
                    ? "Photograph the card (front and back if needed)"
                    : "Photograph each page, in order — a recipe can span pages"}
            </span>
            <input
              ref={fileRef}
              type="file"
              accept={mode === "paprika" ? ".paprikarecipes,.zip,.html,.htm" : mode === "epub" ? ".epub,application/epub+zip" : "image/*"}
              multiple={!singleFile}
              onChange={(e) => setFiles(singleFile ? Array.from(e.target.files ?? []).slice(0, 1) : [...files, ...Array.from(e.target.files ?? [])])}
            />
          </label>
        )}
        {files.length > 0 && !singleFile && (
          <div className={styles.previews}>
            {files.map((file, i) => <img key={i} src={URL.createObjectURL(file)} alt={`Page ${i + 1}`} />)}
          </div>
        )}
        {files.length > 0 && singleFile && <p className={styles.hint}>{files[0].name}{mode === "epub" && ` · ${(files[0].size / 1048576).toFixed(0)} MB`}</p>}
        {mode === "social" && (
          <label className={styles.hint}>
            <input type="checkbox" checked={transcribe} onChange={(e) => setTranscribe(e.target.checked)} /> Also transcribe the video's audio (slow; local Whisper)
          </label>
        )}
        {mode !== "url" && (
          <div className={styles.row}>
            <button type="submit" className={styles.primary} disabled={!canSubmit}>{busy ? (mode === "epub" ? "Reading the book…" : "Sending…") : "Import"}</button>
            {mode === "paste" && html && <span className={styles.hint}>Formatting from the copied page will help.</span>}
          </div>
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        {notice && <p className={styles.hint} role="status">{notice}</p>}
      </form>
    </section>
  );
}

function stepFlags(status: string) {
  const order = ["fetching", "extracting", "ready"];
  const at = order.indexOf(status);
  return order.map((name, i) => ({ name, on: at >= i || status === "ready" }));
}

function InboxItem({ item, onChange, highlight }: { item: Item; onChange: () => void; highlight: boolean }) {
  const person = usePerson();
  const [editing, setEditing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (highlight) ref.current?.scrollIntoView({ block: "start" });
  }, [highlight]);

  const draft = item.draft;
  const doc = draft?.document;
  const uncertain = new Set(draft?.uncertain_lines ?? []);
  const needsConfirm = item.kind === "handwritten" && uncertain.size > 0;
  const title = doc?.title ?? item.input.title ?? item.input.url ?? item.input.filename ?? `${item.kind} import`;

  async function save(mergeInto?: string) {
    setBusy(true);
    setError("");
    try {
      const saved = await post<{ slug: string }>(`/api/inbox/${item.id}/save`, { merge_into: mergeInto ?? null, person_id: person || null });
      refreshCatalog();
      navigate(`/r/${saved.slug}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const isUncertain = (text: string) => [...uncertain].some((line) => line && (text.includes(line) || line.includes(text)));

  return (
    <article className={styles.item} aria-label={title}>
      <section ref={ref}>
        <div className={styles.itemHead}>
          <h2>{title}</h2>
          <span className={`${styles.status} ${WORKING.has(item.status) ? styles.working : ""}`} data-state={item.status}>
            {STATUS_LABEL[item.status] ?? item.status}
          </span>
          {WORKING.has(item.status) && (
            <span className={styles.steps} aria-hidden="true">
              {stepFlags(item.status).map((s) => <span key={s.name} data-on={s.on}>{s.name}</span>)}
            </span>
          )}
        </div>
        {item.status === "failed" && (
          <>
            <p className={styles.error}>{item.error}</p>
            {/verification|blocked|403|429|forbidden/i.test(item.error ?? "") && (
              <p className={styles.hint}>
                This site blocks automatic fetching. Open the page, select all and copy, then use <b>Paste text</b> — the copied page's formatting is used too.
              </p>
            )}
            <div className={styles.actions}>
              <button type="button" className={styles.primary} onClick={() => post(`/api/inbox/${item.id}/retry`, {}).then(onChange)}>Retry</button>
              <button type="button" className={styles.danger} onClick={() => post(`/api/inbox/${item.id}/dismiss`, {}).then(onChange)}>Dismiss</button>
            </div>
          </>
        )}
        {draft && doc && (
          <>
            <p className={styles.meta}>
              via {draft.method}
              {draft.source_site ? ` · ${draft.source_site}` : ""}
              {draft.coverage != null ? ` · ${Math.round(draft.coverage * 100)}% text coverage` : ""}
              {draft.nutrition ? " · publisher nutrition kept" : ""}
              {draft.warnings?.length ? ` · ${draft.warnings.join("; ")}` : ""}
            </p>
            {item.duplicates.length > 0 && (
              <div className={styles.dupes}>
                <b>Looks like something you already have</b>
                <ul>
                  {item.duplicates.map((d) => (
                    <li key={d.recipe_id}>
                      <a {...linkProps(`/r/${d.recipe_id}`)}>{d.title}</a> <span className={styles.hint}>({d.reason})</span>
                      <button type="button" className={styles.secondary} disabled={busy} onClick={() => save(d.recipe_id)}>Merge into this one</button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className={styles.side}>
              <div className={styles.pane}>
                <h3>Source</h3>
                <div className={styles.source}>
                  {item.input.url && <p><a href={item.input.url} target="_blank" rel="noreferrer">{item.input.url}</a></p>}
                  {(item.input.images ?? []).map((name) => <img key={name} src={`/api/inbox-uploads/${item.id}/${name}`} alt="" />)}
                  {draft.transcript ?? item.input.text ?? item.input.caption ?? null}
                </div>
              </div>
              <div className={styles.pane}>
                <h3>Recipe</h3>
                {editing ? (
                  <DraftEditor itemId={item.id} doc={doc} onDone={() => { setEditing(false); onChange(); }} />
                ) : (
                  <>
                    <h4 className={styles.draftTitle}>{doc.title}</h4>
                    {(doc.yield_text || draft.total_minutes) && <p className={styles.meta}>{doc.yield_text ? `Yield: ${doc.yield_text}` : ""}{doc.yield_text && draft.total_minutes ? " · " : ""}{draft.total_minutes ? `${draft.total_minutes} min total` : ""}</p>}
                    {doc.ingredient_sections.map((section) => (
                      <div key={section.id}>
                        {section.heading && <b>{section.heading}</b>}
                        <ul className={styles.draftList}>
                          {section.ingredients.map((ing) => (
                            <li key={ing.id} className={isUncertain(ing.source_text) ? styles.uncertain : undefined}>{ing.source_text}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                    {doc.instruction_sections.map((section) => (
                      <div key={section.id}>
                        {section.heading && <b>{section.heading}</b>}
                        <ol className={styles.draftList}>
                          {section.steps.map((step) => (
                            <li key={step.id} className={isUncertain(step.text) ? styles.uncertain : undefined}>{step.text}</li>
                          ))}
                        </ol>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
            {uncertain.size > 0 && (
              <div className={styles.confirm}>
                <b>{uncertain.size} line{uncertain.size === 1 ? "" : "s"} the model wasn't sure about</b> (highlighted). Check them against the photo{needsConfirm ? " before saving" : ""}.
                <ul>{[...uncertain].map((line) => <li key={line}>{line}</li>)}</ul>
                {needsConfirm && (
                  <label>
                    <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I checked the highlighted lines
                  </label>
                )}
              </div>
            )}
            {error && <p className={styles.error} role="alert">{error}</p>}
            {!editing && (
              <div className={styles.actions}>
                <button type="button" className={styles.primary} disabled={busy || (needsConfirm && !confirmed)} onClick={() => save()}>
                  <Icon name="check" size={18} /> Save to recipes
                </button>
                <button type="button" className={styles.secondary} onClick={() => setEditing(true)}><Icon name="edit" size={18} /> Edit first</button>
                <button type="button" className={styles.danger} onClick={() => post(`/api/inbox/${item.id}/dismiss`, {}).then(onChange)}>Dismiss</button>
              </div>
            )}
          </>
        )}
        {WORKING.has(item.status) && (
          <p className={styles.meta}>
            {item.kind === "photos" || item.kind === "handwritten"
              ? "Reading the pages with the local vision model — this can take a minute or two."
              : "Working on it. You can leave this page; it keeps going."}
          </p>
        )}
      </section>
    </article>
  );
}

function DraftEditor({ itemId, doc, onDone }: { itemId: string; doc: RecipeDocument; onDone: () => void }) {
  const [title, setTitle] = useState(doc.title);
  const [ingredients, setIngredients] = useState(ingredientBlocks(doc));
  const [steps, setSteps] = useState(stepBlocks(doc));
  const [error, setError] = useState("");
  async function save() {
    try {
      await put(`/api/inbox/${itemId}/draft`, {
        document: { ...doc, title, ingredient_sections: toIngredientSections(ingredients, doc), instruction_sections: toInstructionSections(steps, doc) },
      });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <div className={styles.form}>
      <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" />
      {ingredients.map((block, i) => (
        <textarea key={block.id} className={styles.editArea} value={block.text} aria-label="Ingredients, one per line"
          onChange={(e) => setIngredients(ingredients.map((b, j) => (j === i ? { ...b, text: e.target.value } : b)))} />
      ))}
      {steps.map((block, i) => (
        <textarea key={block.id} className={styles.editArea} value={block.text} aria-label="Steps, blank line between"
          onChange={(e) => setSteps(steps.map((b, j) => (j === i ? { ...b, text: e.target.value } : b)))} />
      ))}
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.actions}>
        <button type="button" className={styles.primary} onClick={save}>Done editing</button>
        <button type="button" className={styles.secondary} onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
