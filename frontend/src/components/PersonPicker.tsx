import { useEffect, useRef, useState } from "react";
import { post } from "../lib/api";
import { refreshCatalog, useCatalog } from "../lib/catalog";
import { personStore, usePerson } from "../lib/prefs";
import { linkProps } from "../lib/router";
import type { Person } from "../lib/types";
import { Icon } from "./Icon";
import { Emoji } from "./Marks";
import styles from "./PersonPicker.module.css";

/** "Who's cooking": names only, no auth. Tags cook-log entries and favorites. Also the way to Settings. */
export function PersonPicker({ variant }: { variant: "bar" | "inline" | "compact" }) {
  const { catalog } = useCatalog();
  const personId = usePerson();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const people = catalog?.people ?? [];
  const current = people.find((person) => person.id === personId);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    const person = await post<Person>("/api/people", { name: name.trim() });
    personStore.set(person.id);
    setName("");
    setOpen(false);
    refreshCatalog();
  }

  return (
    <div className={styles.wrap} ref={ref}>
      <button
        type="button"
        className={`${styles.button} ${styles[variant]}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Emoji char="🧑‍🍳" size={18} />
        <span className={variant === "compact" ? "visually-hidden" : undefined}>{current ? current.name : "Who's cooking?"}</span>
        {variant === "compact" && current && <span aria-hidden="true" className={styles.initial}>{current.name.slice(0, 1)}</span>}
      </button>
      {open && (
        <div className={styles.menu} role="menu">
          <h2>Who's cooking</h2>
          {people.map((person) => (
            <button
              key={person.id}
              type="button"
              role="menuitemradio"
              aria-checked={person.id === personId}
              className={styles.option}
              onClick={() => {
                personStore.set(person.id);
                setOpen(false);
              }}
            >
              {person.name}
              {person.id === personId && <Icon name="check" size={18} />}
            </button>
          ))}
          <button
            type="button"
            role="menuitemradio"
            aria-checked={!personId}
            className={styles.option}
            onClick={() => {
              personStore.set("");
              setOpen(false);
            }}
          >
            Just the household
          </button>
          <form className={styles.add} onSubmit={add}>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Add a name" aria-label="Add a name" maxLength={40} />
            <button type="submit">Add</button>
          </form>
          {variant !== "inline" && (
            <div className={styles.footer}>
              <MenuLink to="/more" onNavigate={() => setOpen(false)}>Settings</MenuLink>
              <MenuLink to="/changes" onNavigate={() => setOpen(false)}>Automatic changes</MenuLink>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MenuLink({ to, onNavigate, children }: { to: string; onNavigate: () => void; children: React.ReactNode }) {
  const link = linkProps(to);
  return (
    <a
      role="menuitem"
      className={styles.option}
      href={link.href}
      onClick={(event) => {
        link.onClick(event);
        onNavigate();
      }}
    >
      {children}
    </a>
  );
}
