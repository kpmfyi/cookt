import { useEffect, useRef, useState } from "react";
import { put } from "../lib/api";
import { patchRecipe, refreshCatalog } from "../lib/catalog";
import { usePerson } from "../lib/prefs";
import type { Recipe } from "../lib/types";
import { Emoji, EMOJI_TAG, heroEmoji } from "./Marks";
import styles from "./HeroPicker.module.css";

const HERO_CHOICES = [
  "🍽️", "🍛", "🥘", "🍲", "🍜", "🍝", "🍕", "🌮", "🌯", "🥙", "🍔", "🌭", "🥪", "🍗", "🥩", "🍖", "🥓", "🍤", "🐟", "🦀",
  "🍳", "🥞", "🧇", "🥐", "🥖", "🍞", "🥯", "🧀", "🥗", "🥬", "🥦", "🍅", "🌽", "🥕", "🍄", "🫘", "🍚", "🍙", "🍣", "🥟",
  "🍰", "🎂", "🧁", "🍪", "🍩", "🍫", "🍨", "🥧", "🍮", "🍯", "🍎", "🍓", "🍋", "🥤", "🍹", "☕", "🍵", "🧃", "🥛", "🍷",
];

/** "Change emoji": pick the emoji that stands for this recipe (kept as a personal tag, so every device sees it). */
export function HeroPicker({ recipe }: { recipe: Recipe }) {
  const hero = heroEmoji(recipe.tags);
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const person = usePerson();
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  async function choose(emoji: string | null) {
    const personal = (recipe.tags.personal ?? []).filter((t) => !t.startsWith(EMOJI_TAG));
    if (emoji) personal.push(`${EMOJI_TAG}${emoji}`);
    patchRecipe(recipe.id, { tags: { ...recipe.tags, personal } });
    setOpen(false);
    setCustom("");
    await put(`/api/recipes/${recipe.id}`, { personal_tags: personal, person_id: person || null }).catch(() => undefined);
    refreshCatalog();
  }

  return (
    <div className={styles.heroWrap} ref={ref}>
      <button type="button" className={styles.heroButton} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Emoji char={hero} size={20} /> Change emoji
      </button>
      {open && (
        <div className={styles.heroMenu} role="dialog" aria-label="Pick an emoji">
          <div className={styles.heroGrid}>
            {HERO_CHOICES.map((choice) => (
              <button key={choice} type="button" aria-pressed={choice === hero} onClick={() => choose(choice)}>
                <Emoji char={choice} size={26} />
              </button>
            ))}
          </div>
          <form
            className={styles.heroCustom}
            onSubmit={(event) => {
              event.preventDefault();
              const first = [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(custom.trim())][0]?.segment;
              if (first) void choose(first);
            }}
          >
            <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Any emoji" aria-label="Any emoji" />
            <button type="submit">Use</button>
            <button type="button" onClick={() => choose(null)}>Reset</button>
          </form>
        </div>
      )}
    </div>
  );
}

