import { useEffect, useState } from "react";
import { Icon } from "../components/Icon";
import { api } from "../lib/api";
import { PersonPicker } from "../components/PersonPicker";
import { Ticket } from "../components/Ticket";
import { useCatalog } from "../lib/catalog";
import { isIOS, isStandalone, persistStorage, photoUrls, savePhotos, storageUsage } from "../lib/device";
import { disablePush, enablePush, testPush, usePushState } from "../lib/push";
import { restrictionStore, themeStore, type Theme, useRestrictions, useTheme } from "../lib/prefs";
import { linkProps } from "../lib/router";
import styles from "./Settings.module.css";

// The HTTPS address to install from, when the page is opened over plain http (set at build time).
const SECURE_URL: string = import.meta.env.VITE_SECURE_URL ?? "";

export function Settings() {
  const theme = useTheme();
  const restrictions = useRestrictions();
  const { catalog } = useCatalog();
  const [status, setStatus] = useState<{ total: number; done: Record<string, number> } | null>(null);
  useEffect(() => {
    api<{ total: number; done: Record<string, number> }>("/api/enrichment/status").then(setStatus).catch(() => undefined);
  }, []);
  return (
    <div className={styles.page}>
      <Ticket className={styles.sheet}>
        <h2>Kitchen</h2>
        <ul className={styles.links}>
          <li><a {...linkProps("/inbox")}><Icon name="inbox" /> Import inbox</a></li>
          <li><a {...linkProps("/changes")}><Icon name="spark" /> Changes feed <small>automatic tags &amp; cleanup, undoable</small></a></li>
          <li><a {...linkProps("/review")}><Icon name="edit" /> Review copy edits <small>approve, reject or delete recipes</small></a></li>
          <li><a {...linkProps("/plan")}><Icon name="calendar" /> Week board</a></li>
          <li><a {...linkProps("/shop")}><Icon name="cart" /> Shopping list &amp; pantry staples</a></li>
        </ul>
      </Ticket>
      {status && (
        <Ticket className={styles.sheet}>
          <h2>Enrichment</h2>
          <p className={styles.muted}>Local models tag recipes, work out nutrition and pairing profiles in the background.</p>
          <ul className={styles.links}>
            {Object.entries(status.done).map(([step, done]) => (
              <li key={step}><a {...linkProps("/changes")}>{step}<small>{done} / {status.total}</small></a></li>
            ))}
          </ul>
        </Ticket>
      )}
      <Ticket className={styles.sheet}>
        <h2>This device</h2>
        <div className={styles.row}>
          <PersonPicker variant="inline" />
        </div>
        <div className={styles.row} style={{ marginTop: 12 }}>
          <div className={styles.segmented} role="group" aria-label="Theme">
            {(["system", "light", "dark"] as Theme[]).map((value) => (
              <button key={value} type="button" aria-pressed={theme === value} onClick={() => themeStore.set(value)}>
                {value === "system" ? "🌗 Auto" : value === "light" ? "☀️ Light" : "🌙 Dark"}
              </button>
            ))}
          </div>
        </div>
        <label className={styles.toggle}>
          <input type="checkbox" checked={restrictions} onChange={(e) => restrictionStore.set(e.target.checked)} />
          Show 🌾 gluten-free and 🥛 dairy-free marks on dishes
        </label>
        {!window.isSecureContext && (
          <p className={styles.muted}>
            <b>Offline needs the secure address.</b> Browsers only allow offline storage over HTTPS. Open{" "}
            {SECURE_URL ? <a href={SECURE_URL}>{SECURE_URL.replace(/\/$/, "")}</a> : "the app's HTTPS address"} once
            (and add that one to your home screen) to cook without a connection.
          </p>
        )}
      </Ticket>
      <HomeScreen recipeCount={catalog?.recipes.length ?? 0} photos={catalog ? photoUrls(catalog.recipes) : []} />
    </div>
  );
}

const megabytes = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;

function HomeScreen({ recipeCount, photos }: { recipeCount: number; photos: string[] }) {
  const standalone = isStandalone();
  const push = usePushState();
  const [saved, setSaved] = useState<number | null>(null);
  const [failed, setFailed] = useState(0);
  const [usage, setUsage] = useState<{ used: number; persisted: boolean } | null>(null);
  const [tested, setTested] = useState<string>("");
  const refreshUsage = () => void storageUsage().then(setUsage);
  useEffect(refreshUsage, []);
  const saving = saved !== null && saved < photos.length;

  return (
    <Ticket className={styles.sheet}>
      <h2>Home screen app</h2>
      <div className={styles.item}>
        {standalone ? (
          <b>✅ Installed. cookt opens full screen from the home screen.</b>
        ) : (
          <>
            <b>📲 Add cookt to your home screen</b>
            {isIOS() ? (
              <ol className={styles.steps}>
                <li>Open this page in Safari at the secure address above.</li>
                <li>Tap Share <span aria-hidden>(the square with an arrow)</span>, then <b>Add to Home Screen</b>.</li>
                <li>Open cookt from the new icon. It runs full screen, works offline and can alert you when a timer ends.</li>
              </ol>
            ) : (
              <p>Use your browser&rsquo;s install or &ldquo;Add to Home Screen&rdquo; menu item.</p>
            )}
          </>
        )}
      </div>

      <div className={styles.item}>
        <div className={styles.row}>
          <b>⏱️ Timer alerts when the screen is locked</b>
          {push === "on" ? (
            <span className={styles.row}>
              <button
                type="button"
                className={styles.button}
                onClick={() => {
                  setTested("sending…");
                  void testPush().then((ok) => setTested(ok ? "Sent. It should arrive in a few seconds." : "The push service refused it."));
                }}
              >
                Test
              </button>
              <button type="button" className={styles.button} onClick={() => void disablePush()}>Turn off</button>
            </span>
          ) : push === "off" ? (
            <button type="button" className={styles.button} onClick={() => void enablePush()}>Turn on</button>
          ) : null}
        </div>
        <p>
          {push === "on" && (tested || "On for this device. Finished timers come through as notifications, even with cookt closed.")}
          {push === "off" && "The iPad pauses cookt when it locks, so the server sends each timer's alert instead. Starting a timer also offers this."}
          {push === "denied" && "Notifications are blocked for cookt. Allow them in Settings → Notifications → cookt."}
          {push === "needs-install" && "Available once cookt is added to the home screen (iPadOS 16.4 or later)."}
          {push === "insecure" && "Needs the secure address above."}
          {push === "unsupported" && "This browser can't receive push notifications."}
        </p>
      </div>

      <div className={styles.item}>
        <div className={styles.row}>
          <b>📴 Offline</b>
          <button
            type="button"
            className={styles.button}
            disabled={saving || photos.length === 0 || !window.isSecureContext}
            onClick={() => {
              setSaved(0);
              void persistStorage();
              void savePhotos(photos, setSaved).then((bad) => {
                setFailed(bad);
                refreshUsage();
              });
            }}
          >
            {saving ? `Saving ${saved} / ${photos.length}` : "Save all photos"}
          </button>
        </div>
        <p>
          {recipeCount} recipes are kept on this device. Photos are kept once viewed; save them all now to cook with no
          connection.
          {saved === photos.length && photos.length > 0 && ` Saved ${photos.length - failed} of ${photos.length} photos.`}
          {usage && ` Using ${megabytes(usage.used)}${usage.persisted ? ", protected from automatic clean-up" : ""}.`}
        </p>
      </div>
    </Ticket>
  );
}
