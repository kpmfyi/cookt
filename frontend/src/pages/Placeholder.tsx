import { Ticket } from "../components/Ticket";

export function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div style={{ padding: "var(--s-4) var(--s-3)", maxWidth: 720, margin: "0 auto" }}>
      <Ticket style={{ padding: "var(--s-5)" }}>
        <h1 style={{ fontSize: "var(--fs-3)", marginBottom: "var(--s-2)" }}>{title}</h1>
        <p>{note}</p>
      </Ticket>
    </div>
  );
}
