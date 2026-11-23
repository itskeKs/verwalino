import { Link } from "react-router-dom";
import { formatDateTime } from "../../lib/format";
import type { DeclarationImport, DeclarationImportMode } from "../../api/types";
import { DeclarationImportProgress } from "./DeclarationImportProgress";

/** What the import is for: a new property or an amendment to the property whose page opened it. */
export function DeclarationImportTarget({ mode, propertyId }: { mode: DeclarationImportMode; propertyId?: string | null }) {
  if (mode === "EXISTING_PROPERTY") {
    return <p><strong>Nachtrag für das aktuelle Objekt einlesen</strong><br />Objekt-ID: {propertyId}. Das Ziel ist über diese Seite festgelegt.</p>;
  }
  return <p><strong>Neues Objekt aus einer Teilungserklärung anlegen</strong><br />Die erkannten Angaben werden später vor der Übernahme fachlich geprüft.</p>;
}

interface StatusProps {
  item: DeclarationImport;
  documentTitles: string[];
  /** When the page received `item`; the running step's time counts on from it. */
  receivedAt: number;
  /** The German text for the import's failure code, when it has one. */
  failureMessage?: string;
  retryable: boolean;
  retrying: boolean;
  onRetry: () => void;
}

/** The page of an import that was started: what it is, which step it is in, and how it ended. */
export function DeclarationImportStatus({ item, documentTitles, receivedAt, failureMessage, retryable, retrying, onRetry }: StatusProps) {
  const retryButton = retryable && (
    <button type="button" className="btn btn--primary" disabled={retrying} onClick={onRetry}>{retrying ? "Wird erneut gestartet…" : "Erneut versuchen"}</button>
  );
  const ended = ["NEEDS_REVIEW", "APPLIED", "CANCELLED", "FAILED"].includes(item.status);
  return <>
    <section className="card declaration-import-section" aria-labelledby="declaration-summary-heading">
      <h2 id="declaration-summary-heading">Import</h2>
      <DeclarationImportTarget mode={item.mode} propertyId={item.target_property_id} />
      {documentTitles.length > 0 && <>
        <p className="mt-3">Dokumente:</p>
        <ul className="list-disc pl-5 min-w-0">{documentTitles.map((title, index) => <li key={index} className="break-words">{title}</li>)}</ul>
      </>}
      {item.started_at && <p className="mt-3">Gestartet: {formatDateTime(item.started_at)}</p>}
    </section>

    {item.steps.length > 0 && <section className="card declaration-import-section" aria-labelledby="declaration-processing-heading">
      <h2 id="declaration-processing-heading">Verarbeitung</h2>
      <DeclarationImportProgress steps={item.steps} receivedAt={receivedAt} />
      {!["NEEDS_REVIEW", "APPLYING", "APPLIED"].includes(item.status) && (
        <p className="empty-hint">Sie können diese Seite schließen; der serverseitige Importstatus bleibt erhalten.</p>
      )}
    </section>}

    {ended && <section className="card declaration-import-section" aria-labelledby="declaration-result-heading">
      <h2 id="declaration-result-heading">Ergebnis</h2>
      {item.status === "NEEDS_REVIEW" && <>
        <p><strong>Bereit für die fachliche Prüfung</strong><br />Die erkannten Angaben liegen als Entwurf zur Prüfung vor.</p>
        <Link className="btn btn--primary" to={`/teilungserklaerung/import/${item.id}/review`}>Zur fachlichen Prüfung</Link>
      </>}
      {item.status === "APPLIED" && <p><strong>Übernommen</strong><br />Der freigegebene Entwurf wurde in das Objekt übernommen.</p>}
      {item.status === "CANCELLED" && <><p><strong>Abgebrochen</strong><br />{failureMessage ?? "Der Import wurde abgebrochen."}</p>{retryButton}</>}
      {item.status === "FAILED" && <>
        <p className="error-text" role="alert"><strong>Verarbeitung fehlgeschlagen.</strong><br />{failureMessage ?? "Der Import kann nicht fortgesetzt werden."}</p>
        {retryButton}
      </>}
    </section>}
  </>;
}
