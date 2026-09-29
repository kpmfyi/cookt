import { lazy, Suspense, useEffect, useState } from "react";
import { Shell, type NavItem } from "./components/Shell";
import { UpdateBanner } from "./components/UpdateBanner";
import { loadCatalog } from "./lib/catalog";
import { useLocation } from "./lib/router";
import { useTabTracking } from "./lib/tabs";
import { Cook } from "./pages/Cook";
import { Library } from "./pages/Library";
import { Recipe } from "./pages/Recipe";

const Edit = lazy(() => import("./pages/Edit").then((m) => ({ default: m.Edit })));
const Inbox = lazy(() => import("./pages/Inbox").then((m) => ({ default: m.Inbox })));
const Changes = lazy(() => import("./pages/Changes").then((m) => ({ default: m.Changes })));
const Plan = lazy(() => import("./pages/Plan").then((m) => ({ default: m.Plan })));
const Shopping = lazy(() => import("./pages/Shopping").then((m) => ({ default: m.Shopping })));
const Review = lazy(() => import("./pages/Review").then((m) => ({ default: m.Review })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));

const NAV: NavItem[] = [
  { to: "/", label: "Recipes", icon: "📖" },
  { to: "/plan", label: "Plan", icon: "📅" },
  { to: "/shop", label: "Shop", icon: "🛒" },
  { to: "/inbox", label: "Inbox", icon: "📥" },
];

function route(path: string) {
  const recipe = path.match(/^\/r\/([^/]+)(\/cook|\/edit)?\/?$/);
  if (recipe) {
    const slug = decodeURIComponent(recipe[1]);
    if (recipe[2] === "/cook") return { page: <Cook slug={slug} />, chrome: false };
    if (recipe[2] === "/edit") return { page: <Edit slug={slug} />, chrome: true };
    return { page: <Recipe slug={slug} />, chrome: true };
  }
  if (path.startsWith("/inbox")) return { page: <Inbox />, chrome: true };
  if (path.startsWith("/changes")) return { page: <Changes />, chrome: true };
  if (path.startsWith("/review")) return { page: <Review />, chrome: true };
  if (path.startsWith("/plan")) return { page: <Plan />, chrome: true };
  if (path.startsWith("/shop")) return { page: <Shopping />, chrome: true };
  if (path.startsWith("/more")) return { page: <Settings />, chrome: true };
  return { page: <Library />, chrome: true };
}

function useInboxCount(path: string): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    fetch("/api/inbox")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items: { status: string }[] }) => setCount(d.items.filter((i) => i.status === "ready").length))
      .catch(() => undefined);
  }, [path]);
  return count;
}

export function App() {
  const { path } = useLocation();
  const inboxCount = useInboxCount(path);
  useTabTracking();
  useEffect(() => {
    void loadCatalog();
    const refresh = () => {
      if (document.visibilityState === "visible") void loadCatalog();
    };
    document.addEventListener("visibilitychange", refresh);
    navigator.serviceWorker?.addEventListener("message", (event) => {
      if (event.data?.type === "catalog-updated") void loadCatalog();
    });
    return () => document.removeEventListener("visibilitychange", refresh);
  }, []);
  const { page, chrome } = route(path);
  const content = <Suspense fallback={null}>{page}</Suspense>;
  return (
    <>
      {chrome ? <Shell path={path} nav={NAV.map((item) => (item.to === "/inbox" ? { ...item, badge: inboxCount } : item))}>{content}</Shell> : content}
      <UpdateBanner />
    </>
  );
}
