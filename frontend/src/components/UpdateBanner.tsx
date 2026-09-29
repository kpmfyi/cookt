import { useEffect, useState } from "react";
import styles from "./UpdateBanner.module.css";

let updateRequested = false;

/** "Update available" prompt from the service worker, plus an offline notice. */
export function UpdateBanner() {
  const [waiting, setWaiting] = useState<ServiceWorker | null>(null);
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    const sw = navigator.serviceWorker;
    if (sw) {
      void sw.getRegistration().then((registration) => {
        if (!registration) return;
        if (registration.waiting && sw.controller) setWaiting(registration.waiting);
        registration.addEventListener("updatefound", () => {
          const installing = registration.installing;
          installing?.addEventListener("statechange", () => {
            if (installing.state === "installed" && sw.controller) setWaiting(installing);
          });
        });
      });
      // Reload only when the user asked for the update; the first install (clients.claim)
      // also fires controllerchange and must not reload the page.
      sw.addEventListener("controllerchange", () => {
        if (updateRequested) window.location.reload();
      });
    }
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  if (waiting) {
    return (
      <div className={styles.banner} role="status">
        🆕 A new version of cookt is ready.
        <button
          type="button"
          onClick={() => {
            updateRequested = true;
            waiting.postMessage({ type: "skip-waiting" });
          }}
        >
          Update
        </button>
      </div>
    );
  }
  if (!online) {
    return <div className={`${styles.banner} ${styles.offline}`} role="status">📴 Offline — reading from this device</div>;
  }
  return null;
}
