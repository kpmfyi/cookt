// Minimal stroke icon set (24px grid, currentColor).
const PATHS: Record<string, string> = {
  search: "M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15ZM16 16l5 5",
  heart: "M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20Z",
  back: "M15 5l-7 7 7 7",
  next: "M9 5l7 7-7 7",
  close: "M6 6l12 12M18 6 6 18",
  check: "M5 12.5l4.5 4.5L19 7.5",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  clock: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18ZM12 7v5l3 2",
  timer: "M12 21a8 8 0 1 1 0-16 8 8 0 0 1 0 16ZM12 9v4M9.5 2.5h5M18.5 6.5l1.5-1.5",
  play: "M8 5.5v13l10-6.5-10-6.5Z",
  pause: "M6.5 5h4v14h-4zM13.5 5h4v14h-4z",
  book: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15ZM4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  cart: "M3 4h2.5l2.2 10.5h10.6L20.5 7H7M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2ZM18 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  inbox: "M4 13l2.5-8h11L20 13v6H4v-6ZM4 13h5l1 2h4l1-2h5",
  flame: "M12 21c-3.6 0-6-2.4-6-5.8 0-3.6 3-5.7 3.6-9.2 2.5 1.5 3.4 3.6 3.4 5.3 1-.6 1.6-1.6 1.8-2.8 1.8 1.6 3.2 3.8 3.2 6.7 0 3.4-2.4 5.8-6 5.8Z",
  edit: "M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM12 1.5v2M12 20.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1.5 12h2M20.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 21a7.5 7.5 0 0 1 15 0",
  sort: "M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5",
  filter: "M4 6h16M7 12h10M10 18h4",
  history: "M3.5 12a8.5 8.5 0 1 0 2.5-6M3 4v4h4M12 8v4.5l3 1.5",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  undo: "M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
  link: "M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1",
  camera: "M4 7h3.5L9 4.5h6L16.5 7H20v12H4V7ZM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  store: "M4 10h16v10H4zM3 10l2-6h14l2 6M9 20v-5h6v5",
};

export function Icon({ name, size = 22, filled = false, label }: { name: keyof typeof PATHS | string; size?: number; filled?: boolean; label?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke={filled ? "none" : "currentColor"}
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
