import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { isStandalone, persistStorage } from "./lib/device";
import "./lib/push";
import { applyTheme, themeStore } from "./lib/prefs";

applyTheme(themeStore.get());

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").then((registration) => {
      setInterval(() => void registration.update(), 30 * 60 * 1000);
      // A home-screen app resumes instead of reloading: check for a new version on every return.
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") void registration.update().catch(() => undefined);
      });
    });
    if (isStandalone()) void persistStorage();
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
