// History-API router: a path store + link helper. Routes are matched in App.tsx.
import { createStore, useStore } from "./store";

const location = createStore(window.location.pathname + window.location.search);

window.addEventListener("popstate", () => location.set(window.location.pathname + window.location.search));

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === location.get()) return;
  if (options.replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  location.set(to);
  if (!options.replace) window.scrollTo(0, 0);
}

export function useLocation(): { path: string; query: URLSearchParams } {
  const full = useStore(location);
  const [path, search = ""] = full.split("?");
  return { path, query: new URLSearchParams(search) };
}

export function linkProps(to: string) {
  return {
    href: to,
    onClick(event: React.MouseEvent) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      navigate(to);
    },
  };
}
