import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { CookedDialog } from "../components/CookedDialog";
import { Icon } from "../components/Icon";
import { Emoji, heroEmoji } from "../components/Marks";
import { IngredientList } from "../components/IngredientList";
import { RecipeTabs } from "../components/RecipeTabs";
import { StepText } from "../components/StepText";
import { Ticket } from "../components/Ticket";
import { TimerStrip } from "../components/TimerStrip";
import { findRecipe, useCatalog } from "../lib/catalog";
import { formatQuantity } from "../lib/fractions";
import { useUnits } from "../lib/prefs";
import { clearProgress, loadProgress, saveProgress } from "../lib/progress";
import { linkProps, navigate } from "../lib/router";
import type { Recipe } from "../lib/types";
import styles from "./Cook.module.css";

interface WakeLockSentinelLike { release: () => Promise<void>; released?: boolean }

/** Keep the screen on while cooking; re-acquire when the page becomes visible again. */
function useWakeLock(): boolean {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    let sentinel: WakeLockSentinelLike | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (type: "screen") => Promise<WakeLockSentinelLike> } };
    const acquire = async () => {
      if (!nav.wakeLock || document.visibilityState !== "visible") return;
      try {
        sentinel = await nav.wakeLock.request("screen");
        setHeld(true);
      } catch {
        setHeld(false);
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void acquire();
      else setHeld(false);
    };
    void acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      void sentinel?.release().catch(() => undefined);
    };
  }, []);
  return held;
}

export function Cook({ slug }: { slug: string }) {
  const state = useCatalog();
  const recipe = findRecipe(state, slug);
  if (state.status === "loading") return null;
  if (!recipe) {
    navigate(`/r/${slug}`, { replace: true });
    return null;
  }
  return <CookRail key={recipe.id} recipe={recipe} />;
}

interface FlatStep { id: string; text: string; number: number; section?: string | null }

function CookRail({ recipe }: { recipe: Recipe }) {
  const units = useUnits();
  const wake = useWakeLock();
  const [progress, setProgress] = useState(() => loadProgress(recipe.id));
  const [miseOpen, setMiseOpen] = useState(false);
  const [cookedOpen, setCookedOpen] = useState(false);
  const [drag, setDrag] = useState(0);
  const [dragging, setDragging] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState({ wrap: 0, card: 0, gap: 12 });

  const steps: FlatStep[] = useMemo(() => {
    let n = 0;
    return recipe.document.instruction_sections.flatMap((section) =>
      section.steps.map((step) => ({ id: step.id, text: step.text, number: ++n, section: section.heading })),
    );
  }, [recipe]);
  const lastIndex = steps.length; // index == steps.length is the "finish" ticket
  const index = Math.min(progress.step, lastIndex);
  const checked = new Set(progress.checked);
  const totalIngredients = recipe.document.ingredient_sections.reduce((n, s) => n + s.ingredients.length, 0);

  const update = useCallback(
    (patch: Partial<typeof progress>) => {
      setProgress((prev) => {
        const next = { ...prev, ...patch };
        saveProgress(recipe.id, next);
        return next;
      });
    },
    [recipe.id],
  );
  const go = useCallback((to: number) => update({ step: Math.max(0, Math.min(lastIndex, to)) }), [update, lastIndex]);

  useEffect(() => {
    document.title = `Cooking · ${recipe.title}`;
    return () => {
      document.title = "cookt";
    };
  }, [recipe.title]);

  // Arrow keys, Page Up/Down and Space work from a keyboard or a foot pedal.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (cookedOpen || (event.target as HTMLElement).closest("input, textarea, select, dialog")) return;
      if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(event.key)) {
        if (event.key === "Enter" && (event.target as HTMLElement).tagName === "BUTTON") return;
        event.preventDefault();
        go(index + 1);
      } else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace"].includes(event.key)) {
        event.preventDefault();
        go(index - 1);
      } else if (event.key === "Escape") {
        navigate(`/r/${recipe.slug}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, index, cookedOpen, recipe.slug]);

  useLayoutEffect(() => {
    const measure = () => {
      const wrap = wrapRef.current;
      const card = railRef.current?.firstElementChild as HTMLElement | null;
      if (!wrap || !card) return;
      const gap = parseFloat(getComputedStyle(railRef.current!).columnGap) || 12;
      setGeometry({ wrap: wrap.clientWidth, card: card.offsetWidth, gap });
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (wrapRef.current) observer.observe(wrapRef.current);
    return () => observer.disconnect();
  }, []);

  // Swipe: horizontal drag moves the rail; vertical stays with the ticket's own scroll.
  const start = useRef<{ x: number; y: number; id: number; locked: boolean | null } | null>(null);
  const onPointerDown = (event: React.PointerEvent) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    start.current = { x: event.clientX, y: event.clientY, id: event.pointerId, locked: null };
  };
  const onPointerMove = (event: React.PointerEvent) => {
    const s = start.current;
    if (!s || s.id !== event.pointerId) return;
    const dx = event.clientX - s.x;
    const dy = event.clientY - s.y;
    if (s.locked === null && Math.hypot(dx, dy) > 8) s.locked = Math.abs(dx) > Math.abs(dy);
    if (s.locked) {
      setDragging(true);
      setDrag(dx);
    }
  };
  const justDragged = useRef(false);
  const onPointerUp = (event: React.PointerEvent) => {
    const s = start.current;
    start.current = null;
    if (!s || !s.locked) return;
    justDragged.current = true;
    setTimeout(() => (justDragged.current = false), 50);
    const dx = event.clientX - s.x;
    setDragging(false);
    setDrag(0);
    const threshold = Math.min(90, geometry.card * 0.18);
    if (dx < -threshold) go(index + 1);
    else if (dx > threshold) go(index - 1);
  };

  // Previous ticket peeks by a sliver; most spare width previews the next ticket.
  const offset = geometry.wrap > 0 ? Math.min(36, (geometry.wrap - geometry.card) * 0.15) : 0;
  const translate = offset - index * (geometry.card + geometry.gap) + drag;

  function finish() {
    setCookedOpen(true);
  }

  return (
    <div className={styles.cook}>
      <header className={styles.top}>
        <a className={styles.exit} {...linkProps(`/r/${recipe.slug}`)} aria-label="Leave cook mode"><Icon name="close" /></a>
        <Emoji char={heroEmoji(recipe.tags)} size={26} className={styles.hero} />
        <div className={styles.titleBlock}>
          <h1 className={styles.title}>{recipe.title}</h1>
          <p className={styles.counter}>
            {index < lastIndex ? <>Step <b>{index + 1}</b> of <b>{steps.length}</b></> : <>All <b>{steps.length}</b> steps done</>}
            {progress.factor !== 1 && <> · <b>{formatQuantity(progress.factor)}×</b></>}
            {wake && <span className={styles.wake}> · screen stays on</span>}
          </p>
        </div>
        <button type="button" className={styles.mise} aria-expanded={miseOpen} onClick={() => setMiseOpen(!miseOpen)}>
          <Icon name="book" size={18} /> {checked.size}/{totalIngredients}
        </button>
      </header>

      <div className={styles.tabs}><RecipeTabs variant="cook" /></div>

      <div className={styles.body}>
        <aside className={`${styles.ingredients} ${miseOpen ? styles.ingredientsOpen : ""}`} aria-label="Ingredients">
          <Ticket className={styles.ingredientTicket}>
            <div className={styles.ingredientHead}>
              <h2>Mise</h2>
              <span>{checked.size} of {totalIngredients} ready</span>
            </div>
            <div className={styles.ingredientScroll}>
              <IngredientList
                sections={recipe.document.ingredient_sections}
                factor={progress.factor}
                units={units}
                checked={checked}
                onToggle={(id) => update({ checked: checked.has(id) ? progress.checked.filter((x) => x !== id) : [...progress.checked, id] })}
              />
            </div>
          </Ticket>
        </aside>

        <div
          ref={wrapRef}
          className={styles.railWrap}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <div
            ref={railRef}
            className={`${styles.rail} ${dragging ? styles.dragging : ""}`}
            style={{ transform: `translate3d(${translate}px, 0, 0)` }}
            aria-live="polite"
          >
            {steps.map((step, i) => {
              return (
                <Ticket
                  key={step.id}
                  as="section"
                  className={`${styles.ticket} ${i === index ? styles.current : ""} ${i < index ? styles.doneTicket : ""}`}
                  aria-current={i === index ? "step" : undefined}
                  aria-hidden={Math.abs(i - index) > 1 ? true : undefined}
                  aria-label={`Step ${step.number}`}
                  onClick={i !== index ? () => !justDragged.current && go(i) : undefined}
                >
                  <div className={styles.ticketInner}>
                    {i === 0 && recipe.next_time.length > 0 && (
                      <div className={styles.nextTime}>
                        <b>For next time</b>
                        {recipe.next_time.map((note) => <p key={note.id}>{note.text}</p>)}
                      </div>
                    )}
                    <div className={styles.stepHead}>
                      <span className={styles.num}>{step.number}</span>
                      <span className={styles.stepMeta}>{step.section ?? `of ${steps.length}`}</span>
                    </div>
                    <p className={styles.text}>
                      <StepText text={step.text} units={units} recipeId={recipe.id} recipeTitle={recipe.title} stepLabel={`Step ${step.number}`} />
                    </p>
                  </div>
                </Ticket>
              );
            })}
            <Ticket
              as="section"
              className={`${styles.ticket} ${index === lastIndex ? styles.current : ""}`}
              aria-label="Finish"
              onClick={index !== lastIndex ? () => go(lastIndex) : undefined}
            >
              <div className={`${styles.ticketInner} ${styles.finish}`}>
                <Emoji char="🛎️" size={64} animate="float" />
                <h2>Service.</h2>
                <p>That's every ticket on the rail.</p>
                <div>
                  <button type="button" className={`${styles.nav} ${styles.primary}`} onClick={finish}>
                    <Icon name="check" /> Done — log this cook
                  </button>
                </div>
              </div>
            </Ticket>
          </div>
        </div>
      </div>

      <footer className={styles.bottom}>
        <button type="button" className={styles.nav} onClick={() => go(index - 1)} disabled={index === 0} aria-label="Previous step">
          <Icon name="back" /> <span className={styles.label}>Back</span>
        </button>
        <div className={styles.timers}><TimerStrip inline /></div>
        {index < lastIndex ? (
          <button type="button" className={`${styles.nav} ${styles.primary}`} onClick={() => go(index + 1)} aria-label="Next step">
            <span className={styles.label}>{index === lastIndex - 1 ? "Finish" : "Next"}</span> <Icon name="next" />
          </button>
        ) : (
          <button type="button" className={`${styles.nav} ${styles.primary}`} onClick={finish}>
            <Icon name="check" /> <span className={styles.label}>Done</span>
          </button>
        )}
      </footer>

      <CookedDialog
        recipeId={recipe.id}
        open={cookedOpen}
        onClose={() => setCookedOpen(false)}
        onSaved={() => {
          clearProgress(recipe.id);
          navigate(`/r/${recipe.slug}`, { replace: true });
        }}
      />
    </div>
  );
}
