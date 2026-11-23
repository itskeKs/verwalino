import { Link } from "react-router-dom";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { useMeldungen } from "../components/meldungen/useMeldungen";
import { formatDateTime } from "../lib/format";
import type { MeldungState } from "../api/types";

const TONES: Record<MeldungState, BadgeTone> = {
  WAITING: "default",
  RUNNING: "primary",
  REVIEW: "warning",
  FAILED: "destructive",
};

/** The Meldungen of the signed-in user: one row for each, leading to the page of its subject. */
export function MeldungenPage() {
  const { items, failed, reload } = useMeldungen();
  return (
    <div className="max-w-3xl">
      <div className="page-heading">
        <h1>Meldungen</h1>
      </div>
      {failed && (
        <p className="error-text mb-3" role="alert">
          Die Meldungen konnten nicht geladen werden. <button type="button" className="btn btn--small" onClick={reload}>Erneut laden</button>
        </p>
      )}
      {items === null && !failed && <p role="status">Meldungen werden geladen…</p>}
      {items?.length === 0 && <p className="empty-hint">Keine Meldungen vorhanden.</p>}
      {items && items.length > 0 && (
        <ul aria-label="Meldungen" className="grid gap-3 list-none p-0 m-0">
          {items.map((item) => (
            <li key={`${item.kind}:${item.id}`}>
              <Link to={item.target} className="card meldung-row">
                <span className="flex flex-wrap items-start justify-between gap-2">
                  <span className="font-semibold break-words min-w-0">{item.title}</span>
                  <Badge tone={TONES[item.state]}>{item.state_label}</Badge>
                </span>
                <span className="text-sm break-words">{item.subject}</span>
                {item.at && <span className="text-sm text-muted-foreground">Gestartet: {formatDateTime(item.at)}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
