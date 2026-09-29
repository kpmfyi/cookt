import type { ReactNode } from "react";
import { linkProps } from "../lib/router";
import { Emoji } from "./Marks";
import { PersonPicker } from "./PersonPicker";
import { RecipeTabs } from "./RecipeTabs";
import styles from "./Shell.module.css";
import { TimerStrip } from "./TimerStrip";

export interface NavItem { to: string; label: string; icon: string; badge?: number }

export function Shell({ path, nav, children }: { path: string; nav: NavItem[]; children: ReactNode }) {
  const current = (to: string) => (to === "/" ? path === "/" || path.startsWith("/r/") : path.startsWith(to));
  return (
    <div className={styles.shell}>
      <nav className={styles.tabbar} aria-label="Main">
        <a className={styles.wordmark} {...linkProps("/")}>cookt</a>
        {nav.map((item) => (
          <a key={item.to} className={styles.tab} aria-current={current(item.to) ? "page" : undefined} {...linkProps(item.to)}>
            <Emoji char={item.icon} size={22} />
            <span>
              {item.label}
              {item.badge ? <span className={styles.badge}>{item.badge}</span> : null}
            </span>
          </a>
        ))}
        <span className={styles.person}><PersonPicker variant="bar" /></span>
      </nav>
      <RecipeTabs />
      <main className={styles.main}>{children}</main>
      <TimerStrip hidden={path.endsWith("/cook")} />
    </div>
  );
}
