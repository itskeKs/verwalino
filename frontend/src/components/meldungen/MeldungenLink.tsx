import { Bell } from "lucide-react";
import { Link } from "react-router-dom";
import { useMeldungen } from "./useMeldungen";

/** The entry of the application frame: reachable from every page, with the number of Meldungen once the server has said it. */
export function MeldungenLink() {
  const { items } = useMeldungen();
  const count = items?.length ?? null;
  return (
    <Link
      to="/meldungen"
      className="topbar-meldungen"
      aria-label={count === null ? "Meldungen" : `Meldungen, ${count} offen`}
    >
      <Bell aria-hidden="true" />
      <span>Meldungen</span>
      {count !== null && <span className={`topbar-count${count > 0 ? " topbar-count--open" : ""}`}>{count}</span>}
    </Link>
  );
}
