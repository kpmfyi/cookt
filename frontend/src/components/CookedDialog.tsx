import { useEffect, useRef, useState } from "react";
import { post } from "../lib/api";
import { refreshCatalog, useCatalog } from "../lib/catalog";
import { usePerson } from "../lib/prefs";
import { Ticket } from "./Ticket";
import styles from "./CookedDialog.module.css";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "Cooked it": date, who, make-again, a note, and an optional note for next time. */
export function CookedDialog({ recipeId, open, onClose, onSaved }: { recipeId: string; open: boolean; onClose: () => void; onSaved?: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const { catalog } = useCatalog();
  const person = usePerson();
  const [who, setWho] = useState(person);
  const [day, setDay] = useState(today());
  const [again, setAgain] = useState<boolean | null>(null);
  const [note, setNote] = useState("");
  const [nextTime, setNextTime] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setWho(person);
      setDay(today());
      setAgain(null);
      setNote("");
      setNextTime("");
      setError("");
      dialog.showModal();
    } else if (!open && dialog.open) dialog.close();
  }, [open, person]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await post(`/api/recipes/${recipeId}/cooked`, {
        person_id: who || null,
        cooked_on: day,
        make_again: again,
        note: note.trim() || null,
        next_time: nextTime.trim() || null,
      });
      refreshCatalog();
      onSaved?.();
      onClose();
    } catch (err) {
      setError(navigator.onLine ? (err as Error).message : "You're offline. Try again when you're back online.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className={styles.dialog} onClose={onClose} aria-labelledby="cooked-title">
      <Ticket className={styles.card}>
        <form onSubmit={save}>
          <h2 id="cooked-title">Cooked it</h2>
          <label className={styles.field}>
            When
            <input type="date" value={day} max={today()} onChange={(e) => setDay(e.target.value)} required />
          </label>
          <label className={styles.field}>
            Who cooked
            <select value={who} onChange={(e) => setWho(e.target.value)}>
              <option value="">The household</option>
              {catalog?.people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <div className={styles.field} role="group" aria-label="Make again?">
            Make again?
            <div className={styles.choices}>
              {([[true, "👍 Yes"], [false, "👎 No"], [null, "🤷 Not sure"]] as const).map(([value, label]) => (
                <button key={label} type="button" aria-pressed={again === value} onClick={() => setAgain(value)}>{label}</button>
              ))}
            </div>
          </div>
          <label className={styles.field}>
            How did it go?
            <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" />
          </label>
          <label className={styles.field}>
            For next time
            <textarea value={nextTime} onChange={(e) => setNextTime(e.target.value)} placeholder="Shown at the top of the method next time" />
          </label>
          {error && <p className={styles.error} role="alert">{error}</p>}
          <div className={styles.actions}>
            <button type="button" className={styles.secondary} onClick={onClose}>Cancel</button>
            <button type="submit" className={styles.primary} disabled={busy}>{busy ? "Saving…" : "Log it"}</button>
          </div>
        </form>
      </Ticket>
    </dialog>
  );
}
