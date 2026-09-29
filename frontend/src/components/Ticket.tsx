import type { HTMLAttributes, ReactNode } from "react";
import styles from "./Ticket.module.css";

interface TicketProps extends HTMLAttributes<HTMLElement> {
  as?: "div" | "section" | "article" | "aside" | "li" | "header";
  perforated?: boolean;
  children: ReactNode;
}

/** A card surface. (`perforated` is kept for API compatibility; false = flat, bordered card.) */
export function Ticket({ as: Tag = "div", perforated = true, className, children, ...rest }: TicketProps) {
  return (
    <Tag className={[styles.ticket, perforated ? "" : styles.plain, className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </Tag>
  );
}
